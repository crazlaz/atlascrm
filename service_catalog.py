"""Service catalog — reusable line items for estimates and invoices."""
from db import get_connection

DEFAULT_SERVICES = [
    ('Pavers',        'Paver installation',          'sq ft',  18.00),
    ('Pavers',        'Paver base prep & compaction', 'sq ft',  6.00),
    ('Retaining Wall','Retaining wall block',         'sq ft',  45.00),
    ('Retaining Wall','Retaining wall cap',           'linear ft', 12.00),
    ('Driveway',      'Concrete driveway',            'sq ft',  10.00),
    ('Driveway',      'Asphalt driveway',             'sq ft',  7.00),
    ('Steps',         'Concrete steps',               'each',   350.00),
    ('Steps',         'Bluestone steps',              'each',   500.00),
    ('Drainage',      'French drain installation',    'linear ft', 30.00),
    ('Drainage',      'Catch basin',                  'each',   400.00),
    ('Lighting',      'Landscape light fixture',      'each',   150.00),
    ('Lighting',      'Transformer / wiring',         'each',   250.00),
    ('Lawn Care',     'Lawn cleanup / leaf removal',  'hour',   65.00),
    ('Lawn Care',     'Mulch installation',           'cubic yard', 85.00),
    ('General',       'Equipment / machine time',     'hour',   120.00),
    ('General',       'Labor',                        'hour',   55.00),
    ('General',       'Delivery / hauling',           'each',   200.00),
    ('General',       'Permit fee',                   'each',   0.00),
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
