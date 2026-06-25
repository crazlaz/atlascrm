import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from db import get_connection


def get_smtp_config() -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM smtp_config WHERE id=1").fetchone()
    return dict(row) if row else None


def save_smtp_config(host, port, username, password, use_tls, from_addr, imap_host=""):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO smtp_config (id, host, port, username, password, use_tls, from_addr, imap_host)
            VALUES (1,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                host=excluded.host, port=excluded.port,
                username=excluded.username, password=excluded.password,
                use_tls=excluded.use_tls, from_addr=excluded.from_addr,
                imap_host=excluded.imap_host
        """, (host, int(port), username, password, int(use_tls), from_addr, imap_host))


def send_email(to: str, subject: str, body: str) -> str | None:
    """Returns None on success, error string on failure."""
    cfg = get_smtp_config()
    if not cfg or not cfg["host"]:
        return "SMTP not configured. Go to Settings → Email."
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = cfg["from_addr"] or cfg["username"]
        msg["To"]      = to
        msg.attach(MIMEText(body, "plain"))

        if cfg["use_tls"]:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)

        server.login(cfg["username"], cfg["password"])
        server.sendmail(msg["From"], [to], msg.as_string())
        server.quit()
        return None
    except Exception as e:
        return str(e)


def get_templates() -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM email_templates ORDER BY name"
        ).fetchall()


def save_template(name: str, subject: str, body: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO email_templates (name, subject, body) VALUES (?,?,?)
            ON CONFLICT(name) DO UPDATE SET subject=excluded.subject, body=excluded.body
        """, (name, subject, body))


def delete_template(tid: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM email_templates WHERE id=?", (tid,))
