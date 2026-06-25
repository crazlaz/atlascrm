"""Hardscaping job management."""
from db import get_connection
from activity import log_activity

JOB_TYPES = ['patio','retaining_wall','driveway','walkway','steps',
             'lawn_care','drainage','lighting','cleanup','other']

STATUS_ORDER = ['estimate','scheduled','in_progress','completed','invoiced','paid','cancelled']

STATUS_LABELS = {
    'estimate':    'Estimate',
    'scheduled':   'Scheduled',
    'in_progress': 'In Progress',
    'completed':   'Completed',
    'invoiced':    'Invoiced',
    'paid':        'Paid',
    'cancelled':   'Cancelled',
}


def create_job(contact_id, title, job_type='other', address='', city='', state='', zip_='',
               start_date=None, end_date=None, description='', status='estimate'):
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO jobs (contact_id, title, job_type, address, city, state, zip,
               start_date, end_date, description, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (contact_id, title, job_type, address, city, state, zip_,
             start_date or None, end_date or None, description, status)
        )
        jid = cur.lastrowid
    log_activity(contact_id=contact_id, activity_type='note',
                 summary=f'Job created: {title} ({job_type})')
    return jid


def get_job(jid):
    with get_connection() as conn:
        return conn.execute(
            "SELECT j.*, c.first_name||' '||c.last_name AS contact_name, "
            "c.email AS contact_email, c.phone AS contact_phone "
            "FROM jobs j JOIN contacts c ON c.id=j.contact_id WHERE j.id=?", (jid,)
        ).fetchone()


def get_jobs_for_contact(contact_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE contact_id=? ORDER BY created_at DESC", (contact_id,)
        ).fetchall()


def get_all_jobs(status=None, job_type=None):
    with get_connection() as conn:
        wheres, params = [], []
        if status:
            wheres.append("j.status=?"); params.append(status)
        if job_type:
            wheres.append("j.job_type=?"); params.append(job_type)
        where = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        return conn.execute(
            f"SELECT j.*, c.first_name||' '||c.last_name AS contact_name "
            f"FROM jobs j JOIN contacts c ON c.id=j.contact_id "
            f"{where} ORDER BY j.updated_at DESC", params
        ).fetchall()


def update_job(jid, **kwargs):
    allowed = {'title','job_type','status','address','city','state','zip',
               'start_date','end_date','description','internal_notes'}
    sets, params = [], []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k}=?"); params.append(v or None)
    if not sets:
        return
    params.append(jid)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE jobs SET {','.join(sets)}, updated_at=datetime('now') WHERE id=?", params
        )


def move_status(jid, new_status):
    job = get_job(jid)
    if not job:
        return
    with get_connection() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?",
            (new_status, jid)
        )
    log_activity(contact_id=job['contact_id'], activity_type='stage_change',
                 summary=f'Job "{job["title"]}" moved to {STATUS_LABELS.get(new_status, new_status)}')


def delete_job(jid):
    with get_connection() as conn:
        conn.execute("DELETE FROM jobs WHERE id=?", (jid,))


def get_pipeline_summary():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM jobs GROUP BY status"
        ).fetchall()
    return {r['status']: r['count'] for r in rows}


# ── Photos ────────────────────────────────────────────────────────────────────

import uuid, shutil
from pathlib import Path

PHOTO_DIR = Path(__file__).parent / 'uploads' / 'job_photos'
PHOTO_DIR.mkdir(parents=True, exist_ok=True)


def save_photo(jid, file_obj, photo_type='progress', caption=''):
    suffix = Path(file_obj.filename).suffix.lower()
    stored = uuid.uuid4().hex + suffix
    dest = PHOTO_DIR / stored
    file_obj.save(str(dest))
    size = dest.stat().st_size
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO job_photos (job_id, filename, stored_name, photo_type, caption, size_bytes) "
            "VALUES (?,?,?,?,?,?)",
            (jid, file_obj.filename, stored, photo_type, caption, size)
        )


def get_photos(jid):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM job_photos WHERE job_id=? ORDER BY photo_type, created_at",
            (jid,)
        ).fetchall()


def delete_photo(pid):
    with get_connection() as conn:
        row = conn.execute("SELECT stored_name FROM job_photos WHERE id=?", (pid,)).fetchone()
        if row:
            p = PHOTO_DIR / row['stored_name']
            if p.exists(): p.unlink()
        conn.execute("DELETE FROM job_photos WHERE id=?", (pid,))


# ── Schedule ──────────────────────────────────────────────────────────────────

def schedule_job(jid, date, crew_notes=''):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO job_schedule (job_id, scheduled_date, crew_notes) VALUES (?,?,?)",
            (jid, date, crew_notes)
        )


def get_schedule(month=None, year=None):
    with get_connection() as conn:
        if month and year:
            prefix = f"{year}-{month:02d}"
            return conn.execute(
                "SELECT s.*, j.title, j.job_type, j.status, "
                "c.first_name||' '||c.last_name AS contact_name "
                "FROM job_schedule s JOIN jobs j ON j.id=s.job_id "
                "JOIN contacts c ON c.id=j.contact_id "
                "WHERE s.scheduled_date LIKE ? ORDER BY s.scheduled_date",
                (f"{prefix}%",)
            ).fetchall()
        return conn.execute(
            "SELECT s.*, j.title, j.job_type, j.status, "
            "c.first_name||' '||c.last_name AS contact_name "
            "FROM job_schedule s JOIN jobs j ON j.id=s.job_id "
            "JOIN contacts c ON c.id=j.contact_id "
            "ORDER BY s.scheduled_date"
        ).fetchall()
