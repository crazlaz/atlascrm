from db import get_connection


def add_reminder(due_at, body, contact_id=None, deal_id=None):
    """due_at: ISO datetime string e.g. '2026-06-15 09:00'"""
    if not contact_id and not deal_id:
        raise ValueError("provide contact_id or deal_id")
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO reminders (due_at, body, contact_id, deal_id)
               VALUES (?, ?, ?, ?)""",
            (due_at, body, contact_id, deal_id),
        )
        return cur.lastrowid


def get_due_reminders():
    with get_connection() as conn:
        return conn.execute(
            """SELECT r.*, c.first_name || ' ' || c.last_name AS contact_name
               FROM reminders r
               LEFT JOIN contacts c ON c.id = r.contact_id
               WHERE r.done = 0 AND r.due_at <= datetime('now')
               ORDER BY r.due_at""",
        ).fetchall()


def get_upcoming_reminders(days=7):
    with get_connection() as conn:
        return conn.execute(
            """SELECT r.*, c.first_name || ' ' || c.last_name AS contact_name
               FROM reminders r
               LEFT JOIN contacts c ON c.id = r.contact_id
               WHERE r.done = 0
                 AND r.due_at BETWEEN datetime('now') AND datetime('now', '+' || ? || ' days')
               ORDER BY r.due_at""",
            (str(days),),
        ).fetchall()


def complete_reminder(reminder_id):
    with get_connection() as conn:
        conn.execute(
            "UPDATE reminders SET done = 1 WHERE id = ?", (reminder_id,)
        )
