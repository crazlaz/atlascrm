"""Crew member management and job assignment."""
from db import get_connection


def create_member(name, role='', phone='', email='', pay_rate=0.0):
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO crew_members (name, role, phone, email, pay_rate) VALUES (?,?,?,?,?)",
            (name, role, phone or None, email or None, float(pay_rate))
        )
        return cur.lastrowid


def get_members(active_only=True):
    with get_connection() as conn:
        where = "WHERE active=1" if active_only else ""
        return conn.execute(
            f"SELECT * FROM crew_members {where} ORDER BY name"
        ).fetchall()


def get_member(mid):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM crew_members WHERE id=?", (mid,)).fetchone()


def update_member(mid, name, role='', phone='', email='', pay_rate=0.0, active=1):
    with get_connection() as conn:
        conn.execute(
            "UPDATE crew_members SET name=?,role=?,phone=?,email=?,pay_rate=?,active=? WHERE id=?",
            (name, role, phone or None, email or None, float(pay_rate), int(active), mid)
        )


def delete_member(mid):
    with get_connection() as conn:
        conn.execute("DELETE FROM crew_members WHERE id=?", (mid,))


def assign_to_job(job_id, member_id, scheduled_date=None, notes=''):
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM crew_assignments WHERE job_id=? AND member_id=?",
            (job_id, member_id)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO crew_assignments (job_id, member_id, scheduled_date, notes) VALUES (?,?,?,?)",
                (job_id, member_id, scheduled_date or None, notes)
            )


def unassign(assignment_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM crew_assignments WHERE id=?", (assignment_id,))


def log_hours(job_id, member_id, hours, work_date=None, notes=''):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO crew_hours (job_id, member_id, hours, work_date, notes) "
            "VALUES (?,?,?,COALESCE(?,date('now')),?)",
            (job_id, member_id, float(hours), work_date or None, notes)
        )


def get_job_crew(job_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT ca.*, cm.name, cm.role, cm.phone, cm.pay_rate, "
            "COALESCE((SELECT SUM(h.hours) FROM crew_hours h WHERE h.job_id=ca.job_id AND h.member_id=ca.member_id),0) AS total_hours "
            "FROM crew_assignments ca JOIN crew_members cm ON cm.id=ca.member_id "
            "WHERE ca.job_id=? ORDER BY cm.name",
            (job_id,)
        ).fetchall()


def get_job_hours(job_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT ch.*, cm.name, cm.pay_rate FROM crew_hours ch "
            "JOIN crew_members cm ON cm.id=ch.member_id "
            "WHERE ch.job_id=? ORDER BY ch.work_date DESC",
            (job_id,)
        ).fetchall()
