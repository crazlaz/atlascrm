"""Invoice creation, payment tracking, and status management."""
from db import get_connection
from activity import log_activity
import estimates as EST


def _next_number():
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO company_settings (id, next_invoice_number) VALUES (1, 1)"
        )
        row = conn.execute(
            "SELECT next_invoice_number FROM company_settings WHERE id=1"
        ).fetchone()
        n = row['next_invoice_number']
        conn.execute(
            "UPDATE company_settings SET next_invoice_number = next_invoice_number + 1 WHERE id=1"
        )
    return f"INV-{n:04d}"


def create_invoice(contact_id, job_id=None, estimate_id=None,
                   due_date=None, tax_rate=0, discount=0,
                   deposit_required=0, notes='', terms=''):
    num = _next_number()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO invoices (contact_id, job_id, estimate_id, invoice_number, "
            "due_date, tax_rate, discount, deposit_required, notes, terms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (contact_id, job_id or None, estimate_id or None, num,
             due_date or None, float(tax_rate), float(discount),
             float(deposit_required), notes, terms)
        )
        iid = cur.lastrowid
    log_activity(contact_id=contact_id, activity_type='note',
                 summary=f'Invoice {num} created')
    return iid, num


def invoice_from_estimate(eid, due_date=None):
    """Convert an approved estimate into an invoice, copying all line items."""
    est   = EST.get_estimate(eid)
    items = EST.get_items(eid)
    iid, num = create_invoice(
        contact_id=est['contact_id'],
        job_id=est['job_id'],
        estimate_id=eid,
        due_date=due_date,
        tax_rate=est['tax_rate'],
        discount=est['discount'],
        notes=est['notes'],
        terms=est['terms']
    )
    with get_connection() as conn:
        for item in items:
            conn.execute(
                "INSERT INTO invoice_items (invoice_id, description, quantity, unit, unit_price, sort_order) "
                "VALUES (?,?,?,?,?,?)",
                (iid, item['description'], item['quantity'], item['unit'],
                 item['unit_price'], item['sort_order'])
            )
    EST.update_status(eid, 'approved')
    return iid, num


def get_invoice(iid):
    with get_connection() as conn:
        return conn.execute(
            "SELECT inv.*, c.first_name||' '||c.last_name AS contact_name, "
            "c.email AS contact_email, c.phone AS contact_phone, "
            "c.company AS contact_company "
            "FROM invoices inv JOIN contacts c ON c.id=inv.contact_id WHERE inv.id=?",
            (iid,)
        ).fetchone()


def get_items(iid):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY sort_order, id",
            (iid,)
        ).fetchall()


def add_item(iid, description, quantity, unit, unit_price, sort_order=0):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO invoice_items (invoice_id, description, quantity, unit, unit_price, sort_order) "
            "VALUES (?,?,?,?,?,?)",
            (iid, description, float(quantity), unit, float(unit_price), int(sort_order))
        )


def remove_item(item_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM invoice_items WHERE id=?", (item_id,))


def get_payments(iid):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM payments WHERE invoice_id=? ORDER BY paid_at",
            (iid,)
        ).fetchall()


def add_payment(iid, amount, method='check', reference='', notes='', paid_at=None):
    contact_id = inv_num = None
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO payments (invoice_id, amount, method, reference, notes, paid_at) "
            "VALUES (?,?,?,?,?,COALESCE(?,datetime('now')))",
            (iid, float(amount), method, reference, notes, paid_at or None)
        )
        inv = conn.execute(
            "SELECT contact_id, invoice_number FROM invoices WHERE id=?", (iid,)
        ).fetchone()
        if inv:
            contact_id = inv['contact_id']
            inv_num    = inv['invoice_number']
    if contact_id:
        log_activity(contact_id=contact_id, activity_type='note',
                     summary=f'Payment of ${amount:,.2f} received on {inv_num} via {method}')
    _refresh_status(iid)


def _refresh_status(iid):
    t = totals(iid)
    paid = t['paid']
    total = t['total']
    with get_connection() as conn:
        if paid <= 0:
            status = 'sent'
        elif paid < total:
            status = 'partial'
        else:
            status = 'paid'
            # auto-move linked job to paid
            inv = conn.execute("SELECT job_id, contact_id FROM invoices WHERE id=?", (iid,)).fetchone()
            if inv and inv['job_id']:
                conn.execute(
                    "UPDATE jobs SET status='paid', updated_at=datetime('now') WHERE id=?",
                    (inv['job_id'],)
                )
        conn.execute(
            "UPDATE invoices SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, iid)
        )


def update_status(iid, status):
    with get_connection() as conn:
        conn.execute(
            "UPDATE invoices SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, iid)
        )


def get_all_invoices(status=None):
    with get_connection() as conn:
        where = "WHERE inv.status=?" if status else ""
        params = [status] if status else []
        return conn.execute(
            f"SELECT inv.*, c.first_name||' '||c.last_name AS contact_name "
            f"FROM invoices inv JOIN contacts c ON c.id=inv.contact_id "
            f"{where} ORDER BY inv.created_at DESC", params
        ).fetchall()


def get_invoices_for_contact(contact_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM invoices WHERE contact_id=? ORDER BY created_at DESC",
            (contact_id,)
        ).fetchall()


def get_all_invoices_for_job(job_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM invoices WHERE job_id=? ORDER BY created_at DESC", (job_id,)
        ).fetchall()


def delete_invoice(iid):
    with get_connection() as conn:
        conn.execute("DELETE FROM invoices WHERE id=?", (iid,))


def totals(iid):
    items    = get_items(iid)
    inv      = get_invoice(iid)
    payments = get_payments(iid)
    subtotal = sum(i['quantity'] * i['unit_price'] for i in items)
    discount = inv['discount'] or 0
    after_discount = subtotal - discount
    tax      = round(after_discount * (inv['tax_rate'] or 0) / 100, 2)
    total    = round(after_discount + tax, 2)
    paid     = sum(p['amount'] for p in payments)
    balance  = round(total - paid, 2)
    return {
        'subtotal': subtotal, 'discount': discount,
        'after_discount': after_discount, 'tax': tax,
        'total': total, 'paid': paid, 'balance': balance
    }


def get_company_settings():
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM company_settings WHERE id=1").fetchone()
        return dict(row) if row else {}


def save_company_settings(**kwargs):
    allowed = {'name','address','city','state','zip','phone','email',
               'website','tax_rate','invoice_terms','estimate_terms'}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    cols = ','.join(fields.keys())
    placeholders = ','.join('?' * len(fields))
    updates = ','.join(f"{k}=excluded.{k}" for k in fields)
    with get_connection() as conn:
        conn.execute(
            f"INSERT INTO company_settings (id,{cols}) VALUES (1,{placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            list(fields.values())
        )
