import uuid
from pathlib import Path
from db import get_connection

UPLOAD_DIR = Path(__file__).parent / "uploads"


def save_attachment(file, contact_id=None, deal_id=None, user_id=None) -> int:
    UPLOAD_DIR.mkdir(exist_ok=True)
    ext         = Path(file.filename).suffix
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest        = UPLOAD_DIR / stored_name
    file.save(str(dest))
    size = dest.stat().st_size
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO attachments
               (contact_id, deal_id, filename, stored_name, size_bytes, uploaded_by)
               VALUES (?,?,?,?,?,?)""",
            (contact_id, deal_id, file.filename, stored_name, size, user_id),
        )
        return cur.lastrowid


def get_attachments(contact_id=None, deal_id=None) -> list:
    if contact_id:
        col, val = "contact_id", contact_id
    else:
        col, val = "deal_id", deal_id
    with get_connection() as conn:
        return conn.execute(
            f"SELECT * FROM attachments WHERE {col}=? ORDER BY created_at DESC", (val,)
        ).fetchall()


def get_attachment(aid: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM attachments WHERE id=?", (aid,)
        ).fetchone()


def delete_attachment(aid: int):
    att = get_attachment(aid)
    if att:
        p = UPLOAD_DIR / att["stored_name"]
        p.unlink(missing_ok=True)
        with get_connection() as conn:
            conn.execute("DELETE FROM attachments WHERE id=?", (aid,))
