"""Recurring contracts — auto-generate invoices on a schedule."""
import threading, time
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from db import get_connection
import invoices as INV
from activity import log_activity


def create_contract(contact_id, job_id=None, name='', amount=0,
                    frequency='monthly', next_date=None, notes=''):
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO recurring_contracts "
            "(contact_id, job_id, name, amount, frequency, next_date, notes) "
            "VALUES (?,?,?,?,?,?,?)",
            (contact_id, job_id or None, name, float(amount),
             frequency, next_date or str(date.today()), notes)
        )
        return cur.lastrowid


def get_contracts(contact_id=None):
    with get_connection() as conn:
        if contact_id:
            return conn.execute(
                "SELECT rc.*, c.first_name||' '||c.last_name AS contact_name "
                "FROM recurring_contracts rc JOIN contacts c ON c.id=rc.contact_id "
                "WHERE rc.contact_id=? AND rc.active=1 ORDER BY rc.next_date",
                (contact_id,)
            ).fetchall()
        return conn.execute(
            "SELECT rc.*, c.first_name||' '||c.last_name AS contact_name "
            "FROM recurring_contracts rc JOIN contacts c ON c.id=rc.contact_id "
            "WHERE rc.active=1 ORDER BY rc.next_date"
        ).fetchall()


def get_contract(rid):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM recurring_contracts WHERE id=?", (rid,)
        ).fetchone()


def pause_contract(rid):
    with get_connection() as conn:
        conn.execute("UPDATE recurring_contracts SET active=0 WHERE id=?", (rid,))


def resume_contract(rid):
    with get_connection() as conn:
        conn.execute("UPDATE recurring_contracts SET active=1 WHERE id=?", (rid,))


def delete_contract(rid):
    with get_connection() as conn:
        conn.execute("DELETE FROM recurring_contracts WHERE id=?", (rid,))


def _advance_date(current: str, frequency: str) -> str:
    d = date.fromisoformat(current)
    if frequency == 'weekly':
        d += relativedelta(weeks=1)
    elif frequency == 'biweekly':
        d += relativedelta(weeks=2)
    elif frequency == 'monthly':
        d += relativedelta(months=1)
    elif frequency == 'quarterly':
        d += relativedelta(months=3)
    elif frequency == 'yearly':
        d += relativedelta(years=1)
    return str(d)


def run_due_contracts():
    today = str(date.today())
    with get_connection() as conn:
        due = conn.execute(
            "SELECT * FROM recurring_contracts WHERE active=1 AND next_date<=?",
            (today,)
        ).fetchall()

    for rc in due:
        from datetime import timedelta
        due_dt = str(date.today() + timedelta(days=30))

        iid, num = INV.create_invoice(
            contact_id=rc['contact_id'],
            job_id=rc['job_id'],
            due_date=due_dt,
            notes=f"Recurring: {rc['name']}"
        )
        # add single line item for the contract amount
        INV.add_item(iid, rc['name'], 1, 'month', rc['amount'])
        log_activity(contact_id=rc['contact_id'], activity_type='note',
                     summary=f"Auto-invoice {num} generated for recurring contract: {rc['name']}")

        next_d = _advance_date(rc['next_date'], rc['frequency'])
        with get_connection() as conn:
            conn.execute(
                "UPDATE recurring_contracts SET next_date=?, last_billed=? WHERE id=?",
                (next_d, today, rc['id'])
            )


def _worker(interval_hours=24):
    time.sleep(300)
    while True:
        try:
            run_due_contracts()
        except Exception as e:
            print(f"[recurring] error: {e}")
        time.sleep(interval_hours * 3600)


def start_recurring_scheduler(interval_hours=24):
    t = threading.Thread(target=_worker, args=(interval_hours,), daemon=True)
    t.start()
