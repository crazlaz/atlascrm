"""Service catalog — reusable line items for estimates and invoices."""
from db import get_connection

DEFAULT_SERVICES = [
    ('Website',     'Custom website design & build',    'project', 3500.00),
    ('Website',     'Landing page',                      'each',    800.00),
    ('Website',     'E-commerce setup',                  'project', 4500.00),
    ('Website',     'Website redesign / refresh',        'project', 2200.00),
    ('CRM',         'CRM implementation & setup',        'project', 2500.00),
    ('CRM',         'Custom CRM integration',            'hour',     95.00),
    ('CRM',         'Data migration',                    'project', 1200.00),
    ('Hosting',     'Managed hosting',                   'month',    49.00),
    ('Hosting',     'Domain & SSL setup',                'each',     75.00),
    ('Maintenance', 'Monthly maintenance & updates',     'month',   150.00),
    ('Maintenance', 'Priority support retainer',         'month',   300.00),
    ('Marketing',   'SEO setup & optimization',          'project', 900.00),
    ('Marketing',   'Email marketing automation setup',  'project', 650.00),
    ('Design',      'UI / UX design',                    'hour',    110.00),
    ('Training',    'Staff onboarding & training',       'hour',     85.00),
    ('General',     'Custom development',                'hour',    125.00),
    ('General',     'Project management',                'hour',     75.00),
    ('General',     'Consulting / discovery call',       'hour',    150.00),
]


def seed_defaults():
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM service_catalog").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO service_catalog (category, name, unit, unit_price) VALUES (?,?,?,?)",
                DEFAULT_SERVICES
            )


def get_all(active_only=True):
    with get_connection() as conn:
        where = "WHERE active=1" if active_only else ""
        return conn.execute(
            f"SELECT * FROM service_catalog {where} ORDER BY category, name"
        ).fetchall()


def get_by_category(active_only=True):
    items = get_all(active_only)
    cats = {}
    for item in items:
        cats.setdefault(item['category'], []).append(item)
    return cats


def get_item(iid):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM service_catalog WHERE id=?", (iid,)).fetchone()


def create_item(category, name, description, unit, unit_price):
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO service_catalog (category, name, description, unit, unit_price) "
            "VALUES (?,?,?,?,?)",
            (category, name, description or '', unit, float(unit_price))
        )
        return cur.lastrowid


def update_item(iid, category, name, description, unit, unit_price, active=1):
    with get_connection() as conn:
        conn.execute(
            "UPDATE service_catalog SET category=?,name=?,description=?,unit=?,unit_price=?,active=? WHERE id=?",
            (category, name, description or '', unit, float(unit_price), int(active), iid)
        )


def delete_item(iid):
    with get_connection() as conn:
        conn.execute("DELETE FROM service_catalog WHERE id=?", (iid,))
