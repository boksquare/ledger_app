""""Her Spending" tab — her personal spending, fully isolated from the split tracker.

Writes only to the wife_* tables; shares the categories table with the main
tracker. Nothing here reads or affects expenses/split math.
"""
import sqlite3
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import queries, recurring

router = APIRouter(prefix="/her")


def _main():
    from . import main
    return main


def _validate_date(raw: str) -> str:
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        raise HTTPException(400, "Date must be YYYY-MM-DD")


def her_context(conn: sqlite3.Connection, month: str, category_id: int | None = None) -> dict:
    m = _main()
    return {
        "month": month,
        "current_month": m.current_month(),
        "total": queries.wife_month_total(conn, month),
        "expenses": queries.wife_month_expenses(conn, month, category_id),
        "categories": queries.active_categories(conn),
        "descriptions": queries.wife_distinct_descriptions(conn),
        "breakdown": queries.wife_category_breakdown(conn, month),
        "filter_category_id": category_id,
        "default_date": m.default_expense_date(month).isoformat(),
    }


def render_her_content(request: Request, month: str, category_id: int | None = None) -> HTMLResponse:
    m = _main()
    with m.db_conn() as conn:
        ctx = her_context(conn, month, category_id)
    return m.templates.TemplateResponse(request, "partials/her_content.html", ctx)


# ---------- Pages ----------

@router.get("", response_class=HTMLResponse)
def her_home(request: Request):
    return her_month_view(request, _main().current_month())


@router.get("/month/{month}", response_class=HTMLResponse)
def her_month_view(request: Request, month: str):
    m = _main()
    m.validate_month(month)
    with m.db_conn() as conn:
        if month == m.current_month():
            recurring.generate_due_wife_instances(conn)
        ctx = her_context(conn, month)
        ctx["months"] = queries.wife_months_with_expenses(conn)
        ctx["rules"] = conn.execute(
            """
            SELECT r.*, c.name AS category_name
            FROM wife_recurring_expenses r JOIN categories c ON c.id = r.category_id
            ORDER BY r.active DESC, r.day_of_month
            """
        ).fetchall()
    return m.templates.TemplateResponse(request, "her_spending.html", ctx)


# ---------- Expenses (HTMX partials) ----------

@router.get("/partials/expenses", response_class=HTMLResponse)
def her_expenses_partial(request: Request, month: str, category_id: int | None = None):
    _main().validate_month(month)
    return render_her_content(request, month, category_id)


@router.post("/expenses", response_class=HTMLResponse)
def create_her_expense(
    request: Request,
    expense_date: str = Form(alias="date"),
    description: str = Form(),
    amount: str = Form(),
    category_id: int = Form(),
    notes: str = Form(default=""),
    view_month: str = Form(),
):
    m = _main()
    m.validate_month(view_month)
    with m.db_conn() as conn:
        conn.execute(
            """
            INSERT INTO wife_expenses (date, description, amount, category_id, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (_validate_date(expense_date), description.strip(), m.parse_amount(amount),
             category_id, notes.strip() or None),
        )
        conn.commit()
    return render_her_content(request, view_month)


HER_INLINE_EDITABLE = {"date", "description", "amount", "category_id", "notes"}


@router.post("/expenses/{expense_id}/inline", response_class=HTMLResponse)
async def inline_update_her_expense(
    request: Request, expense_id: int, view_month: str, category_id: int | None = None,
):
    """Save a single field edited directly in the expense list (htmx change trigger)."""
    m = _main()
    m.validate_month(view_month)
    form = await request.form()
    fields = {k: v for k, v in form.items() if k in HER_INLINE_EDITABLE}
    if not fields:
        raise HTTPException(400, "No editable field submitted")
    field, value = next(iter(fields.items()))
    if field == "date":
        value = _validate_date(str(value))
    elif field == "amount":
        value = m.parse_amount(str(value))
    elif field == "category_id":
        value = int(value)
    elif field == "description":
        value = str(value).strip()
        if not value:
            raise HTTPException(400, "Description cannot be empty")
    else:  # notes
        value = str(value).strip() or None
    with m.db_conn() as conn:
        cur = conn.execute(
            f"UPDATE wife_expenses SET {field} = ? WHERE id = ?", (value, expense_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Expense not found")
        conn.commit()
    return render_her_content(request, view_month, category_id)


@router.delete("/expenses/{expense_id}", response_class=HTMLResponse)
def delete_her_expense(request: Request, expense_id: int, view_month: str):
    m = _main()
    m.validate_month(view_month)
    with m.db_conn() as conn:
        conn.execute("DELETE FROM wife_expenses WHERE id = ?", (expense_id,))
        conn.commit()
    return render_her_content(request, view_month)


# ---------- Recurring rules (managed inline on the tab) ----------

def _back(view_month: str) -> RedirectResponse:
    return RedirectResponse(f"/her/month/{view_month}", status_code=303)


@router.post("/recurring")
def create_her_recurring(
    description: str = Form(),
    amount: str = Form(),
    category_id: int = Form(),
    day_of_month: int = Form(ge=1, le=31),
    start_date: str = Form(default=""),
    end_date: str = Form(default=""),
    view_month: str = Form(),
):
    m = _main()
    m.validate_month(view_month)
    with m.db_conn() as conn:
        conn.execute(
            """
            INSERT INTO wife_recurring_expenses
                (description, amount, category_id, day_of_month, start_date, end_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (description.strip(), m.parse_amount(amount), category_id,
             day_of_month, start_date or None, end_date or None),
        )
        recurring.generate_due_wife_instances(conn)
        conn.commit()
    return _back(view_month)


@router.post("/recurring/{rule_id}/update")
def update_her_recurring(
    rule_id: int,
    description: str = Form(),
    amount: str = Form(),
    category_id: int = Form(),
    day_of_month: int = Form(ge=1, le=31),
    start_date: str = Form(default=""),
    end_date: str = Form(default=""),
    view_month: str = Form(),
):
    m = _main()
    m.validate_month(view_month)
    with m.db_conn() as conn:
        cur = conn.execute(
            """
            UPDATE wife_recurring_expenses SET description = ?, amount = ?, category_id = ?,
                day_of_month = ?, start_date = ?, end_date = ?
            WHERE id = ?
            """,
            (description.strip(), m.parse_amount(amount), category_id,
             day_of_month, start_date or None, end_date or None, rule_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Rule not found")
        conn.commit()
    return _back(view_month)


@router.post("/recurring/{rule_id}/toggle")
def toggle_her_recurring(rule_id: int, view_month: str = Form()):
    m = _main()
    m.validate_month(view_month)
    with m.db_conn() as conn:
        conn.execute(
            "UPDATE wife_recurring_expenses SET active = 1 - active WHERE id = ?", (rule_id,)
        )
        recurring.generate_due_wife_instances(conn)
        conn.commit()
    return _back(view_month)


@router.post("/recurring/{rule_id}/delete")
def delete_her_recurring(rule_id: int, view_month: str = Form()):
    """Delete a rule; past generated instances are kept but unlinked."""
    m = _main()
    m.validate_month(view_month)
    with m.db_conn() as conn:
        conn.execute(
            "UPDATE wife_expenses SET recurring_id = NULL WHERE recurring_id = ?", (rule_id,)
        )
        conn.execute("DELETE FROM wife_recurring_expenses WHERE id = ?", (rule_id,))
        conn.commit()
    return _back(view_month)
