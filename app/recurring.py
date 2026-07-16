"""Auto-generation of expense instances from recurring rules."""
import calendar
import sqlite3
from datetime import date
from typing import Callable


def instance_date(rule_day: int, year: int, month: int) -> date:
    """The rule's date within a month, clamped to the month's last day (e.g. day 31 in Feb)."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(rule_day, last_day))


def _generate(
    conn: sqlite3.Connection,
    rules: list[sqlite3.Row],
    insert: Callable[[sqlite3.Row, str], int],
    today: date | None,
) -> int:
    """Shared generation loop: walk each rule's due months and insert instances.

    Idempotent via each table's unique (recurring_id, month) index. Also backfills
    months between a rule's start_date (or this month, if none) and today that
    were missed while the app wasn't running.
    """
    today = today or date.today()
    created = 0
    for rule in rules:
        start = date.fromisoformat(rule["start_date"]) if rule["start_date"] else today.replace(day=1)
        end = date.fromisoformat(rule["end_date"]) if rule["end_date"] else None
        year, month = start.year, start.month
        while (year, month) <= (today.year, today.month):
            due = instance_date(rule["day_of_month"], year, month)
            in_window = due >= start and (end is None or due <= end)
            if in_window and due <= today:
                created += insert(rule, due.isoformat())
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    conn.commit()
    return created


def generate_due_instances(conn: sqlite3.Connection, today: date | None = None) -> int:
    """Create expense rows for active rules whose day_of_month has arrived this month."""
    rules = conn.execute("SELECT * FROM recurring_expenses WHERE active = 1").fetchall()

    def insert(rule: sqlite3.Row, due_iso: str) -> int:
        return conn.execute(
            """
            INSERT OR IGNORE INTO expenses
                (date, description, amount, category_id, split_type,
                 is_recurring_instance, recurring_id)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (due_iso, rule["description"], rule["amount"],
             rule["category_id"], rule["split_type"], rule["id"]),
        ).rowcount

    return _generate(conn, rules, insert, today)


def generate_due_wife_instances(conn: sqlite3.Connection, today: date | None = None) -> int:
    """Same generation for the isolated "Her Spending" tables (no split concept)."""
    rules = conn.execute("SELECT * FROM wife_recurring_expenses WHERE active = 1").fetchall()

    def insert(rule: sqlite3.Row, due_iso: str) -> int:
        return conn.execute(
            """
            INSERT OR IGNORE INTO wife_expenses
                (date, description, amount, category_id, is_recurring_instance, recurring_id)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (due_iso, rule["description"], rule["amount"], rule["category_id"], rule["id"]),
        ).rowcount

    return _generate(conn, rules, insert, today)
