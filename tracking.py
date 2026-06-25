"""Email open/click tracking — pixel + link wrapping."""
import secrets, base64
from db import get_connection
from activity import log_activity


# Minimal 1x1 transparent GIF
PIXEL_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


def create_token(contact_id: int, email_subject: str = "") -> str:
    token = secrets.token_urlsafe(24)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO email_tracking (token, contact_id, email_subject, created_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (token, contact_id, email_subject)
        )
    return token


def record_open(token: str) -> None:
    contact_id = subject = None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, contact_id, email_subject, opened_at FROM email_tracking WHERE token=?",
            (token,)
        ).fetchone()
        if not row:
            return
        if not row["opened_at"]:
            conn.execute(
                "UPDATE email_tracking SET opened_at=datetime('now'), open_count=open_count+1 WHERE token=?",
                (token,)
            )
        else:
            conn.execute(
                "UPDATE email_tracking SET open_count=open_count+1 WHERE token=?",
                (token,)
            )
        contact_id = row["contact_id"]
        subject    = row["email_subject"]
    log_activity(
        contact_id=contact_id,
        activity_type="email",
        summary=f"Opened email: {subject or '(no subject)'}"
    )


def record_click(token: str, url: str) -> None:
    contact_id = subject = None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, contact_id, email_subject FROM email_tracking WHERE token=?",
            (token,)
        ).fetchone()
        if not row:
            return
        conn.execute(
            "UPDATE email_tracking SET click_count=click_count+1 WHERE token=?",
            (token,)
        )
        contact_id = row["contact_id"]
        subject    = row["email_subject"]
    log_activity(
        contact_id=contact_id,
        activity_type="email",
        summary=f"Clicked link in email: {subject or '(no subject)'} → {url[:100]}"
    )


def inject_tracking(html_body: str, token: str, base_url: str) -> str:
    """Add tracking pixel and wrap links in an HTML email body."""
    import re

    def wrap_link(m):
        original_url = m.group(1)
        tracked = f"{base_url}/track/click/{token}?url={original_url}"
        return f'href="{tracked}"'

    html_body = re.sub(r'href="([^"]+)"', wrap_link, html_body)
    pixel = f'<img src="{base_url}/track/open/{token}" width="1" height="1" style="display:none">'
    return html_body + "\n" + pixel


def get_tracking_stats(contact_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM email_tracking WHERE contact_id=? ORDER BY created_at DESC LIMIT 20",
            (contact_id,)
        ).fetchall()
