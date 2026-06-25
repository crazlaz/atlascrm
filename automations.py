"""Automation triggers — overdue reminders, review requests, approval tokens."""
import secrets, threading, time
from datetime import date
from db import get_connection
import email_service as EM
from activity import log_activity


# ── Client approval portal ────────────────────────────────────────────────────

def create_approval_token(estimate_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_connection() as conn:
        conn.execute(
            "UPDATE estimates SET approval_token=? WHERE id=?",
            (token, estimate_id)
        )
    return token


def get_estimate_by_token(token: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT e.*, c.first_name||' '||c.last_name AS contact_name, "
            "c.email AS contact_email "
            "FROM estimates e JOIN contacts c ON c.id=e.contact_id "
            "WHERE e.approval_token=?", (token,)
        ).fetchone()


def client_approve(token: str, signer_name: str) -> bool:
    contact_id = est_num = None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, contact_id, estimate_number FROM estimates WHERE approval_token=?",
            (token,)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE estimates SET status='approved', signed_by=?, signed_at=datetime('now') WHERE id=?",
            (signer_name, row['id'])
        )
        contact_id = row['contact_id']
        est_num    = row['estimate_number']
    log_activity(contact_id=contact_id, activity_type='note',
                 summary=f"Estimate {est_num} approved online by {signer_name}")
    return True


def client_reject(token: str, reason: str = '') -> bool:
    contact_id = est_num = None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, contact_id, estimate_number FROM estimates WHERE approval_token=?",
            (token,)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE estimates SET status='rejected' WHERE id=?", (row['id'],)
        )
        contact_id = row['contact_id']
        est_num    = row['estimate_number']
    if reason:
        log_activity(contact_id=contact_id, activity_type='note',
                     summary=f"Estimate {est_num} rejected: {reason}")
    return True


# ── Overdue invoice reminders ─────────────────────────────────────────────────

def send_overdue_reminders():
    today = str(date.today())
    with get_connection() as conn:
        overdue = conn.execute(
            "SELECT i.*, c.email, c.first_name "
            "FROM invoices i JOIN contacts c ON c.id=i.contact_id "
            "WHERE i.status IN ('sent','partial') AND i.due_date < ? AND c.email IS NOT NULL",
            (today,)
        ).fetchall()

    co = _get_company_name()
    for inv in overdue:
        from invoices import totals as inv_totals
        t = inv_totals(inv['id'])
        if t['balance'] <= 0:
            continue
        subject = f"Payment Reminder — {inv['invoice_number']} is overdue"
        body = (
            f"Hi {inv['first_name']},\n\n"
            f"This is a friendly reminder that invoice {inv['invoice_number']} "
            f"for ${t['balance']:,.2f} was due on {inv['due_date']}.\n\n"
            f"Please arrange payment at your earliest convenience.\n\n"
            f"Questions? Reply to this email or call us.\n\n"
            f"Thank you,\n{co}"
        )
        err = EM.send_email(inv['email'], subject, body)
        if not err:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE invoices SET status='overdue' WHERE id=?", (inv['id'],)
                )
            log_activity(contact_id=inv['contact_id'], activity_type='email',
                         summary=f"Overdue reminder sent for {inv['invoice_number']}")


# ── Review request ────────────────────────────────────────────────────────────

def send_review_request(contact_id: int, job_title: str, review_url: str = ''):
    with get_connection() as conn:
        contact = conn.execute(
            "SELECT first_name, email FROM contacts WHERE id=?", (contact_id,)
        ).fetchone()
    if not contact or not contact['email']:
        return False

    co  = _get_company_name()
    url = review_url or 'https://g.page/r/YOUR_GOOGLE_REVIEW_LINK'
    subject = f"How did we do? — {job_title}"
    body = (
        f"Hi {contact['first_name']},\n\n"
        f"Thank you for choosing {co}! We just completed your {job_title} "
        f"and we hope you love it.\n\n"
        f"If you have a moment, we'd really appreciate a Google review — "
        f"it helps us a lot:\n{url}\n\n"
        f"Thanks again for your business!\n\n{co}"
    )
    err = EM.send_email(contact['email'], subject, body)
    if not err:
        log_activity(contact_id=contact_id, activity_type='email',
                     summary=f"Review request sent for: {job_title}")
    return not err


# ── Background runner ─────────────────────────────────────────────────────────

def _get_company_name():
    with get_connection() as conn:
        row = conn.execute("SELECT name FROM company_settings WHERE id=1").fetchone()
    return row['name'] if row and row['name'] else 'Your Team'


def _automation_worker(interval_hours=24):
    time.sleep(300)
    while True:
        try:
            send_overdue_reminders()
        except Exception as e:
            print(f"[automations] overdue reminders error: {e}")
        time.sleep(interval_hours * 3600)


def start_automation_scheduler(interval_hours=24):
    t = threading.Thread(target=_automation_worker, args=(interval_hours,), daemon=True)
    t.start()
