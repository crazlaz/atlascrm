"""Drip sequence automation — schedule email chains per contact."""
import threading, time
from datetime import datetime, timedelta
from db import get_connection
import email_service as EM


def create_sequence(name: str, description: str = "") -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO drip_sequences (name, description) VALUES (?, ?)",
            (name, description)
        )
        return cur.lastrowid


def add_step(sequence_id: int, step_number: int, delay_days: int,
             subject: str, body: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO drip_steps (sequence_id, step_number, delay_days, subject, body) "
            "VALUES (?, ?, ?, ?, ?)",
            (sequence_id, step_number, delay_days, subject, body)
        )
        return cur.lastrowid


def get_sequences():
    with get_connection() as conn:
        return conn.execute(
            "SELECT s.*, COUNT(st.id) as step_count, COUNT(e.id) as enrollment_count "
            "FROM drip_sequences s "
            "LEFT JOIN drip_steps st ON st.sequence_id = s.id "
            "LEFT JOIN drip_enrollments e ON e.sequence_id = s.id AND e.status='active' "
            "WHERE s.active=1 GROUP BY s.id ORDER BY s.created_at DESC"
        ).fetchall()


def get_sequence(sid: int):
    with get_connection() as conn:
        seq = conn.execute("SELECT * FROM drip_sequences WHERE id=?", (sid,)).fetchone()
        steps = conn.execute(
            "SELECT * FROM drip_steps WHERE sequence_id=? ORDER BY step_number",
            (sid,)
        ).fetchall()
        return seq, steps


def delete_sequence(sid: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM drip_sequences WHERE id=?", (sid,))


def delete_step(step_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM drip_steps WHERE id=?", (step_id,))


def enroll_contact(contact_id: int, sequence_id: int) -> bool:
    """Enroll a contact in a sequence; returns False if already enrolled."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM drip_enrollments WHERE contact_id=? AND sequence_id=? AND status='active'",
            (contact_id, sequence_id)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO drip_enrollments (contact_id, sequence_id, enrolled_at, current_step) "
            "VALUES (?, ?, datetime('now'), 0)",
            (contact_id, sequence_id)
        )
        return True


def unenroll_contact(enrollment_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE drip_enrollments SET status='cancelled' WHERE id=?",
            (enrollment_id,)
        )


def get_enrollments(contact_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT e.*, s.name as sequence_name FROM drip_enrollments e "
            "JOIN drip_sequences s ON s.id = e.sequence_id "
            "WHERE e.contact_id=? ORDER BY e.enrolled_at DESC",
            (contact_id,)
        ).fetchall()


def _run_due_emails():
    """Find enrollments where the next step is due and send emails."""
    with get_connection() as conn:
        enrollments = conn.execute(
            "SELECT e.*, c.email, c.first_name, c.last_name "
            "FROM drip_enrollments e "
            "JOIN contacts c ON c.id = e.contact_id "
            "WHERE e.status='active'"
        ).fetchall()

        smtp = EM.get_smtp_config()
        if not smtp or not smtp["host"]:
            return

        for enr in enrollments:
            next_step_num = enr["current_step"] + 1
            # find the next step
            step = conn.execute(
                "SELECT * FROM drip_steps WHERE sequence_id=? AND step_number=?",
                (enr["sequence_id"], next_step_num)
            ).fetchone()
            if not step:
                # No more steps — mark complete
                conn.execute(
                    "UPDATE drip_enrollments SET status='completed' WHERE id=?",
                    (enr["id"],)
                )
                continue

            enrolled_dt = datetime.fromisoformat(enr["enrolled_at"])
            last_sent_dt = datetime.fromisoformat(enr["last_sent_at"]) if enr["last_sent_at"] else enrolled_dt
            due_at = last_sent_dt + timedelta(days=step["delay_days"])

            if datetime.now() >= due_at and enr["email"]:
                body = step["body"].replace("{{first_name}}", enr["first_name"] or "")
                body = body.replace("{{last_name}}", enr["last_name"] or "")
                ok = EM.send_email(
                    to=enr["email"],
                    subject=step["subject"],
                    body=body
                )
                if not ok:  # send_email returns None on success, error string on failure
                    conn.execute(
                        "UPDATE drip_enrollments SET current_step=?, last_sent_at=datetime('now') WHERE id=?",
                        (next_step_num, enr["id"])
                    )


def _drip_worker(interval_minutes: int = 30):
    while True:
        try:
            _run_due_emails()
        except Exception as e:
            print(f"[drip] error: {e}")
        time.sleep(interval_minutes * 60)


def start_drip_scheduler(interval_minutes: int = 30):
    t = threading.Thread(target=_drip_worker, args=(interval_minutes,), daemon=True)
    t.start()
