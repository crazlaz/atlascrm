from db import get_connection
from activity import log_activity
import webhooks as WH

STAGES = ("lead", "qualified", "proposal", "won", "lost")


def add_deal(contact_id, title, value=0, stage="lead"):
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO deals (contact_id, title, value, stage)
               VALUES (?, ?, ?, ?)""",
            (contact_id, title, value, stage),
        )
        return cur.lastrowid


def move_stage(deal_id, stage):
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    closed_at = "datetime('now')" if stage in ("won", "lost") else "NULL"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT contact_id, stage FROM deals WHERE id = ?", (deal_id,)
        ).fetchone()
        conn.execute(
            f"""UPDATE deals
                SET stage = ?, closed_at = {closed_at}, updated_at = datetime('now')
                WHERE id = ?""",
            (stage, deal_id),
        )
    log_activity(
        "stage_change",
        f"Stage moved to '{stage}'",
        contact_id=row["contact_id"],
        deal_id=deal_id,
    )
    WH.fire("stage_change", {"deal_id": deal_id, "stage": stage})
    if stage == "won":
        WH.fire("deal_won", {"deal_id": deal_id})


def get_deals_for_contact(contact_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM deals WHERE contact_id = ? ORDER BY created_at DESC",
            (contact_id,),
        ).fetchall()


def pipeline_summary():
    with get_connection() as conn:
        return conn.execute(
            """SELECT stage,
                      COUNT(*)       AS count,
                      SUM(value)     AS total_value
               FROM deals
               GROUP BY stage
               ORDER BY CASE stage
                   WHEN 'lead'      THEN 1
                   WHEN 'qualified' THEN 2
                   WHEN 'proposal'  THEN 3
                   WHEN 'won'       THEN 4
                   WHEN 'lost'      THEN 5
               END"""
        ).fetchall()
