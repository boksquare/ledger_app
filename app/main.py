"""Monthly Expense & Bill-Split Tracker — FastAPI app."""
import calendar
import re
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, queries, recurring

BASE_DIR = Path(__file__).resolve().parent

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    with db_conn() as conn:
        recurring.generate_due_instances(conn)
    yield


app = FastAPI(title="Ledger", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["usd"] = lambda v: f"${v:,.2f}"
templates.env.filters["monthname"] = (
    lambda m: datetime.strptime(m, "%Y-%m").strftime("%B %Y")
)


@contextmanager
def db_conn():
    conn = db.get_db()
    try:
        yield conn
    finally:
        conn.close()


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def default_expense_date(view_month: str) -> date:
    """Today when adding to the current month; the same day-of-month (clamped)
    within the viewed month when adding to a past or future month."""
    today = date.today()
    if view_month == today.strftime("%Y-%m"):
        return today
    year, month = int(view_month[:4]), int(view_month[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(today.day, last_day))


def validate_month(month: str) -> str:
    if not MONTH_RE.match(month):
        raise HTTPException(400, "Month must be YYYY-MM")
    return month


def parse_amount(raw: str) -> float:
    try:
        amount = round(float(raw.replace("$", "").replace(",", "")), 2)
    except ValueError:
        raise HTTPException(400, "Amount must be a number")
    if amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    return amount


def validate_split(split_type: str) -> str:
    if split_type not in db.SPLIT_TYPES:
        raise HTTPException(400, "Invalid split type")
    return split_type


def dashboard_context(conn: sqlite3.Connection, month: str, category_id: int | None = None) -> dict:
    return {
        "month": month,
        "current_month": current_month(),
        "summary": queries.month_summary(conn, month),
        "expenses": queries.month_expenses(conn, month, category_id),
        "categories": queries.active_categories(conn),
        "descriptions": queries.distinct_descriptions(conn),
        "breakdown": queries.category_breakdown(conn, month),
        "filter_category_id": category_id,
        "today": date.today().isoformat(),
    }


def render_dashboard_content(request: Request, month: str, category_id: int | None = None) -> HTMLResponse:
    with db_conn() as conn:
        ctx = dashboard_context(conn, month, category_id)
    return templates.TemplateResponse(request, "partials/dashboard_content.html", ctx)


# ---------- Pages ----------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return month_view(request, current_month())


@app.get("/month/{month}", response_class=HTMLResponse)
def month_view(request: Request, month: str):
    validate_month(month)
    with db_conn() as conn:
        if month == current_month():
            recurring.generate_due_instances(conn)
        ctx = dashboard_context(conn, month)
        ctx["months"] = queries.months_with_expenses(conn)
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    with db_conn() as conn:
        months = queries.months_with_expenses(conn)
        summaries = [queries.month_summary(conn, m) for m in months]
    return templates.TemplateResponse(
        request, "history.html",
        {"summaries": summaries, "current_month": current_month()},
    )


@app.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request):
    with db_conn() as conn:
        cats = conn.execute("SELECT * FROM categories ORDER BY active DESC, name").fetchall()
    return templates.TemplateResponse(request, "categories.html", {"categories": cats})


@app.get("/recurring", response_class=HTMLResponse)
def recurring_page(request: Request):
    with db_conn() as conn:
        rules = conn.execute(
            """
            SELECT r.*, c.name AS category_name
            FROM recurring_expenses r JOIN categories c ON c.id = r.category_id
            ORDER BY r.active DESC, r.day_of_month
            """
        ).fetchall()
        cats = queries.active_categories(conn)
    return templates.TemplateResponse(
        request, "recurring.html", {"rules": rules, "categories": cats},
    )


# ---------- Expenses (HTMX partials) ----------

@app.get("/partials/expenses", response_class=HTMLResponse)
def expenses_partial(request: Request, month: str, category_id: int | None = None):
    validate_month(month)
    return render_dashboard_content(request, month, category_id)


@app.post("/expenses", response_class=HTMLResponse)
def create_expense(
    request: Request,
    description: str = Form(),
    amount: str = Form(),
    category_id: int = Form(),
    split_type: str = Form(),
    notes: str = Form(default=""),
    view_month: str = Form(),
):
    validate_month(view_month)
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO expenses (date, description, amount, category_id, split_type, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (default_expense_date(view_month).isoformat(), description.strip(),
             parse_amount(amount), category_id, validate_split(split_type),
             notes.strip() or None),
        )
        conn.commit()
    return render_dashboard_content(request, view_month)


INLINE_EDITABLE = {"description", "amount", "category_id", "split_type", "notes"}


@app.post("/expenses/{expense_id}/inline", response_class=HTMLResponse)
async def inline_update_expense(
    request: Request, expense_id: int, view_month: str, category_id: int | None = None,
):
    """Save a single field edited directly in the expense list (htmx change trigger)."""
    validate_month(view_month)
    form = await request.form()
    fields = {k: v for k, v in form.items() if k in INLINE_EDITABLE}
    if not fields:
        raise HTTPException(400, "No editable field submitted")
    field, value = next(iter(fields.items()))
    if field == "amount":
        value = parse_amount(str(value))
    elif field == "split_type":
        value = validate_split(str(value))
    elif field == "category_id":
        value = int(value)
    elif field == "description":
        value = str(value).strip()
        if not value:
            raise HTTPException(400, "Description cannot be empty")
    else:  # notes
        value = str(value).strip() or None
    with db_conn() as conn:
        cur = conn.execute(
            f"UPDATE expenses SET {field} = ? WHERE id = ?", (value, expense_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Expense not found")
        conn.commit()
    return render_dashboard_content(request, view_month, category_id)


@app.delete("/expenses/{expense_id}", response_class=HTMLResponse)
def delete_expense(request: Request, expense_id: int, view_month: str):
    validate_month(view_month)
    with db_conn() as conn:
        conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
    return render_dashboard_content(request, view_month)


# ---------- JSON API (chart + programmatic use) ----------

@app.get("/api/summary/{month}")
def api_summary(month: str):
    validate_month(month)
    with db_conn() as conn:
        return queries.month_summary(conn, month)


@app.get("/api/category-breakdown/{month}")
def api_breakdown(month: str):
    validate_month(month)
    with db_conn() as conn:
        return queries.category_breakdown(conn, month)


# ---------- Categories ----------

@app.post("/categories")
def create_category(name: str = Form()):
    if not name.strip():
        raise HTTPException(400, "Name is required")
    with db_conn() as conn:
        queries.get_or_create_category(conn, name)
        conn.commit()
    return RedirectResponse("/categories", status_code=303)


@app.post("/categories/{category_id}/rename")
def rename_category(category_id: int, name: str = Form()):
    if not name.strip():
        raise HTTPException(400, "Name is required")
    with db_conn() as conn:
        try:
            conn.execute("UPDATE categories SET name = ? WHERE id = ?", (name.strip(), category_id))
        except sqlite3.IntegrityError:
            raise HTTPException(400, "A category with that name already exists")
        conn.commit()
    return RedirectResponse("/categories", status_code=303)


@app.post("/categories/{category_id}/toggle")
def toggle_category(category_id: int):
    with db_conn() as conn:
        conn.execute("UPDATE categories SET active = 1 - active WHERE id = ?", (category_id,))
        conn.commit()
    return RedirectResponse("/categories", status_code=303)


# ---------- Recurring rules ----------

@app.post("/recurring")
def create_recurring(
    description: str = Form(),
    amount: str = Form(),
    category_id: int = Form(),
    split_type: str = Form(),
    day_of_month: int = Form(ge=1, le=31),
    start_date: str = Form(default=""),
    end_date: str = Form(default=""),
):
    with db_conn() as conn:
        cat_id = category_id
        conn.execute(
            """
            INSERT INTO recurring_expenses
                (description, amount, category_id, split_type, day_of_month, start_date, end_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (description.strip(), parse_amount(amount), cat_id, validate_split(split_type),
             day_of_month, start_date or None, end_date or None),
        )
        recurring.generate_due_instances(conn)
        conn.commit()
    return RedirectResponse("/recurring", status_code=303)


@app.post("/recurring/{rule_id}/update")
def update_recurring(
    rule_id: int,
    description: str = Form(),
    amount: str = Form(),
    category_id: int = Form(),
    split_type: str = Form(),
    day_of_month: int = Form(ge=1, le=31),
    start_date: str = Form(default=""),
    end_date: str = Form(default=""),
):
    with db_conn() as conn:
        cat_id = category_id
        cur = conn.execute(
            """
            UPDATE recurring_expenses SET description = ?, amount = ?, category_id = ?,
                split_type = ?, day_of_month = ?, start_date = ?, end_date = ?
            WHERE id = ?
            """,
            (description.strip(), parse_amount(amount), cat_id, validate_split(split_type),
             day_of_month, start_date or None, end_date or None, rule_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Rule not found")
        conn.commit()
    return RedirectResponse("/recurring", status_code=303)


@app.post("/recurring/{rule_id}/toggle")
def toggle_recurring(rule_id: int):
    with db_conn() as conn:
        conn.execute("UPDATE recurring_expenses SET active = 1 - active WHERE id = ?", (rule_id,))
        recurring.generate_due_instances(conn)
        conn.commit()
    return RedirectResponse("/recurring", status_code=303)


@app.post("/recurring/{rule_id}/delete")
def delete_recurring(rule_id: int):
    """Delete a rule; past generated instances are kept but unlinked."""
    with db_conn() as conn:
        conn.execute(
            "UPDATE expenses SET recurring_id = NULL WHERE recurring_id = ?", (rule_id,)
        )
        conn.execute("DELETE FROM recurring_expenses WHERE id = ?", (rule_id,))
        conn.commit()
    return RedirectResponse("/recurring", status_code=303)


from . import statements  # noqa: E402  (registers /import routes)
from . import export  # noqa: E402  (registers /export routes)

app.include_router(statements.router)
app.include_router(export.router)
