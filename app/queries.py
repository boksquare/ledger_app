"""Read/aggregate queries shared by pages, API, and exports."""
import sqlite3


def month_expenses(conn: sqlite3.Connection, month: str, category_id: int | None = None) -> list[sqlite3.Row]:
    """All expenses in a YYYY-MM month, newest first, optionally filtered to one category."""
    sql = """
        SELECT e.*, c.name AS category_name
        FROM expenses e JOIN categories c ON c.id = e.category_id
        WHERE substr(e.date, 1, 7) = ?
    """
    params: list = [month]
    if category_id:
        sql += " AND e.category_id = ?"
        params.append(category_id)
    sql += " ORDER BY e.date DESC, e.id DESC"
    return conn.execute(sql, params).fetchall()


def month_summary(conn: sqlite3.Connection, month: str) -> dict:
    """Split totals for a month per spec §5: her_owed = 50% of 50_50 + 100% of 100_hers."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN split_type = '50_50' THEN amount END), 0) AS total_50_50,
            COALESCE(SUM(CASE WHEN split_type = '100_hers' THEN amount END), 0) AS total_100_hers,
            COALESCE(SUM(amount), 0) AS total_spending,
            COUNT(*) AS expense_count
        FROM expenses WHERE substr(date, 1, 7) = ?
        """,
        (month,),
    ).fetchone()
    half_share = round(row["total_50_50"] * 0.5, 2)
    return {
        "month": month,
        "half_share": half_share,
        "total_50_50": round(row["total_50_50"], 2),
        "total_100_hers": round(row["total_100_hers"], 2),
        "total_spending": round(row["total_spending"], 2),
        "her_owed": round(half_share + row["total_100_hers"], 2),
        "expense_count": row["expense_count"],
    }


def category_breakdown(conn: sqlite3.Connection, month: str) -> list[dict]:
    """Raw category totals for the month (both split types combined), for the pie chart."""
    rows = conn.execute(
        """
        SELECT c.id AS category_id, c.name, ROUND(SUM(e.amount), 2) AS total
        FROM expenses e JOIN categories c ON c.id = e.category_id
        WHERE substr(e.date, 1, 7) = ?
        GROUP BY c.id, c.name
        ORDER BY total DESC
        """,
        (month,),
    ).fetchall()
    return [dict(r) for r in rows]


def months_with_expenses(conn: sqlite3.Connection) -> list[str]:
    """Distinct YYYY-MM months that have any expenses, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT substr(date, 1, 7) AS month FROM expenses ORDER BY month DESC"
    ).fetchall()
    return [r["month"] for r in rows]


def distinct_descriptions(conn: sqlite3.Connection, limit: int = 200) -> list[str]:
    """Previously used descriptions for autocomplete, most frequent/recent first."""
    rows = conn.execute(
        """
        SELECT description, COUNT(*) AS uses, MAX(date) AS last_used
        FROM expenses
        GROUP BY description COLLATE NOCASE
        ORDER BY uses DESC, last_used DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [r["description"] for r in rows]


def active_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM categories WHERE active = 1 ORDER BY name"
    ).fetchall()


def get_or_create_category(conn: sqlite3.Connection, name: str) -> int:
    """Find a category by name (case-insensitive), reactivating or creating as needed."""
    name = name.strip()
    row = conn.execute("SELECT id, active FROM categories WHERE name = ?", (name,)).fetchone()
    if row:
        if not row["active"]:
            conn.execute("UPDATE categories SET active = 1 WHERE id = ?", (row["id"],))
        return row["id"]
    cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    return cur.lastrowid
