"""Auto-generation of expense instances from recurring rules."""
import calendar
import sqlite3
from datetime import date


def instance_date(rule_day: int, year: int, month: int) -> date:
    """The rule's date within a month, clamped to the month's last day (e.g. day 31 in Feb)."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(rule_day, last_day))


def generate_due_instances(conn: sqlite3.Connection, today: date | None = None) -> int:
    """Create expense rows for active rules whose day_of_month has arrived this month.

    Idempotent via the unique (recurring_id, month) index. Also backfills months
    between a rule's start_date (or this month, if none) and today that were missed
    while the app wasn't running.
    """
    today = today or date.today()
    created = 0
    rules = conn.execute("SELECT * FROM recurring_expenses WHERE active = 1").fetchall()
    for rule in rules:
        start = date.fromisoformat(rule["start_date"]) if rule["start_date"] else today.replace(day=1)
        end = date.fromisoformat(rule["end_date"]) if rule["end_date"] else None
        year, month = start.year, start.month
        while (year, month) <= (today.year, today.month):
            due = instance_date(rule["day_of_month"], year, month)
            in_window = due >= start and (end is None or due <= end)
            if in_window and due <= today:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO expenses
                        (date, description, amount, category_id, split_type,
                         is_recurring_instance, recurring_id)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        due.isoformat(), rule["description"], rule["amount"],
                        rule["category_id"], rule["split_type"], rule["id"],
                    ),
                )
                created += cur.rowcount
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    conn.commit()
    return created
