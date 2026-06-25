"""Job cost tracking — actual material and subcontractor costs vs quoted."""
from db import get_connection


def add_cost(job_id, description, amount, cost_type='material', vendor='', notes=''):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO job_costs (job_id, description, amount, cost_type, vendor, notes) "
            "VALUES (?,?,?,?,?,?)",
            (job_id, description, float(amount), cost_type, vendor, notes)
        )


def get_costs(job_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM job_costs WHERE job_id=? ORDER BY created_at",
            (job_id,)
        ).fetchall()


def delete_cost(cid):
    with get_connection() as conn:
        conn.execute("DELETE FROM job_costs WHERE id=?", (cid,))


def profitability(job_id):
    """Return revenue, costs, labor cost, and net profit for a job."""
    import invoices as INV
    import crew as CR

    costs    = get_costs(job_id)
    material = sum(c['amount'] for c in costs if c['cost_type'] == 'material')
    sub      = sum(c['amount'] for c in costs if c['cost_type'] == 'subcontractor')
    other    = sum(c['amount'] for c in costs if c['cost_type'] == 'other')

    # labor cost from logged hours × pay rate
    hours = CR.get_job_hours(job_id)
    labor = sum(h['hours'] * (h['pay_rate'] or 0) for h in hours)

    # revenue from invoices
    all_inv = INV.get_all_invoices_for_job(job_id)
    revenue = sum(INV.totals(i['id'])['paid'] for i in all_inv)

    total_cost = material + sub + other + labor
    profit     = revenue - total_cost
    margin     = (profit / revenue * 100) if revenue > 0 else 0

    return {
        'revenue':       revenue,
        'material_cost': material,
        'labor_cost':    labor,
        'sub_cost':      sub,
        'other_cost':    other,
        'total_cost':    total_cost,
        'profit':        profit,
        'margin':        round(margin, 1),
    }


def all_jobs_profitability():
    """Summary profit report across all completed/paid jobs."""
    with get_connection() as conn:
        jobs = conn.execute(
            "SELECT j.id, j.title, j.status, c.first_name||' '||c.last_name AS contact_name "
            "FROM jobs j JOIN contacts c ON c.id=j.contact_id "
            "WHERE j.status IN ('completed','invoiced','paid') ORDER BY j.updated_at DESC"
        ).fetchall()
    return [{**dict(j), **profitability(j['id'])} for j in jobs]
