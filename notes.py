from db import get_connection


def add_note(body, contact_id=None, deal_id=None):
    if not contact_id and not deal_id:
        raise ValueError("provide contact_id or deal_id")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO notes (body, contact_id, deal_id) VALUES (?, ?, ?)",
            (body, contact_id, deal_id),
        )
        return cur.lastrowid


def get_notes(contact_id=None, deal_id=None):
    if contact_id:
        col, val = "contact_id", contact_id
    elif deal_id:
        col, val = "deal_id", deal_id
    else:
        raise ValueError("provide contact_id or deal_id")
    with get_connection() as conn:
        return conn.execute(
            f"SELECT * FROM notes WHERE {col} = ? ORDER BY created_at DESC",
            (val,),
        ).fetchall()
