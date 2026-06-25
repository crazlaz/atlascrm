from db import get_connection

TYPES = ("call", "email", "meeting", "task", "note", "stage_change")


def log_activity(type=None, summary=None, contact_id=None, deal_id=None, activity_type=None):
    if activity_type is not None:
        type = activity_type
    if type not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    if not contact_id and not deal_id:
        raise ValueError("provide contact_id or deal_id")
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO activities (type, summary, contact_id, deal_id)
               VALUES (?, ?, ?, ?)""",
            (type, summary, contact_id, deal_id),
        )
        return cur.lastrowid


def get_activity_feed(contact_id=None, deal_id=None, limit=50):
    conditions = []
    params = []
    if contact_id:
        conditions.append("contact_id = ?")
        params.append(contact_id)
    if deal_id:
        conditions.append("deal_id = ?")
        params.append(deal_id)
    where = f"WHERE {' OR '.join(conditions)}" if conditions else ""
    with get_connection() as conn:
        return conn.execute(
            f"""SELECT a.*, c.first_name || ' ' || c.last_name AS contact_name
                FROM activities a
                LEFT JOIN contacts c ON c.id = a.contact_id
                {where}
                ORDER BY a.created_at DESC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()


def get_recent_activity(limit=20):
    with get_connection() as conn:
        return conn.execute(
            """SELECT a.*, c.first_name || ' ' || c.last_name AS contact_name
               FROM activities a
               LEFT JOIN contacts c ON c.id = a.contact_id
               ORDER BY a.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
