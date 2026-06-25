"""Estimate creation and management."""
from db import get_connection
from activity import log_activity


def _next_number():
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO company_settings (id, next_estimate_number) VALUES (1, 1)"
        )
        row = conn.execute(
            "SELECT next_estimate_number FROM company_settings WHERE id=1"
        ).fetchone()
        n = row['next_estimate_number']
        conn.execute(
            "UPDATE company_settings SET next_estimate_number = next_estimate_number + 1 WHERE id=1"
        )
    return f"EST-{n:04d}"


def create_estimate(contact_id, job_id=None, valid_until=None,
                    tax_rate=0, discount=0, notes='', terms=''):
    num = _next_number()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO estimates (contact_id, job_id, estimate_number, valid_until, "
            "tax_rate, discount, notes, terms) VALUES (?,?,?,?,?,?,?,?)",
            (contact_id, job_id or None, num, valid_until or None,
             float(tax_rate), float(discount), notes, terms)
        )
        eid = cur.lastrowid
    log_activity(contact_id=contact_id, activity_type='note',
                 summary=f'Estimate {num} created')
    return eid, num


def get_estimate(eid):
    with get_connection() as conn:
        return conn.execute(
            "SELECT e.*, c.first_name||' '||c.last_name AS contact_name, "
            "c.email AS contact_email, c.phone AS contact_phone, c.company AS contact_company "
            "FROM estimates e JOIN contacts c ON c.id=e.contact_id WHERE e.id=?", (eid,)
        ).fetchone()


def get_items(eid):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM estimate_items WHERE estimate_id=? ORDER BY sort_order, id",
            (eid,)
        ).fetchall()


def add_item(eid, description, quantity, unit, unit_price, catalog_id=None, sort_order=0):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO estimate_items (estimate_id, catalog_id, description, "
            "quantity, unit, unit_price, sort_order) VALUES (?,?,?,?,?,?,?)",
            (eid, catalog_id or None, description, float(quantity), unit,
             float(unit_price), int(sort_order))
        )


def remove_item(item_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM estimate_items WHERE id=?", (item_id,))


def update_status(eid, status):
    contact_id = est_num = None
    with get_connection() as conn:
        conn.execute(
            "UPDATE estimates SET status=?, updated_at=datetime('now') WHERE id=?",
            (status, eid)
        )
        est = conn.execute(
            "SELECT contact_id, estimate_number FROM estimates WHERE id=?", (eid,)
        ).fetchone()
        if est:
            contact_id = est['contact_id']
            est_num    = est['estimate_number']
    if contact_id:
        log_activity(contact_id=contact_id, activity_type='note',
                     summary=f'Estimate {est_num} marked {status}')


def get_all_estimates(status=None):
    with get_connection() as conn:
        where = "WHERE e.status=?" if status else ""
        params = [status] if status else []
        return conn.execute(
            f"SELECT e.*, c.first_name||' '||c.last_name AS contact_name "
            f"FROM estimates e JOIN contacts c ON c.id=e.contact_id "
            f"{where} ORDER BY e.created_at DESC", params
        ).fetchall()


def get_estimates_for_contact(contact_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM estimates WHERE contact_id=? ORDER BY created_at DESC",
            (contact_id,)
        ).fetchall()


def get_all_estimates_for_job(job_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM estimates WHERE job_id=? ORDER BY created_at DESC", (job_id,)
        ).fetchall()


def delete_estimate(eid):
    with get_connection() as conn:
        conn.execute("DELETE FROM estimates WHERE id=?", (eid,))


def totals(eid):
    items = get_items(eid)
    est   = get_estimate(eid)
    subtotal = sum(i['quantity'] * i['unit_price'] for i in items)
    discount = est['discount'] or 0
    after_discount = subtotal - discount
    tax  = round(after_discount * (est['tax_rate'] or 0) / 100, 2)
    total = round(after_discount + tax, 2)
    return {'subtotal': subtotal, 'discount': discount,
            'after_discount': after_discount, 'tax': tax, 'total': total}
