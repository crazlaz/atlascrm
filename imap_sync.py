"""IMAP sync — pull email replies into contact activity timelines."""
import imaplib, email, threading, time
from email.header import decode_header
from db import get_connection
import email_service as EM
from activity import log_activity


def _decode_str(val):
    if val is None:
        return ""
    parts = decode_header(val)
    result = []
    for b, enc in parts:
        if isinstance(b, bytes):
            result.append(b.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(b)
    return " ".join(result)


def _find_contact_by_email(conn, from_addr: str):
    from_addr = from_addr.lower().strip()
    # extract bare address from "Name <addr>"
    if "<" in from_addr:
        from_addr = from_addr.split("<")[-1].rstrip(">").strip()
    return conn.execute(
        "SELECT id, first_name, last_name FROM contacts WHERE lower(email)=?",
        (from_addr,)
    ).fetchone()


def sync_once():
    cfg = EM.get_smtp_config()
    if not cfg:
        return
    imap_host = cfg.get("imap_host") or ""
    imap_user = cfg.get("username") or ""
    imap_pass = cfg.get("password") or ""
    if not imap_host or not imap_user or not imap_pass:
        return

    mail = None
    try:
        mail = imaplib.IMAP4_SSL(imap_host)
        mail.login(imap_user, imap_pass)
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        ids = data[0].split()

        activities = []
        with get_connection() as conn:
            for uid in ids:
                _, msg_data = mail.fetch(uid, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                from_hdr = _decode_str(msg.get("From", ""))
                subject  = _decode_str(msg.get("Subject", "(no subject)"))

                contact = _find_contact_by_email(conn, from_hdr)
                if not contact:
                    continue

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode("utf-8", errors="replace")[:500]
                            break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="replace")[:500]

                summary = f'Email reply -- "{subject}"'
                if body:
                    summary += f": {body[:120]}…"

                activities.append((contact["id"], summary))

        for contact_id, summary in activities:
            log_activity(
                contact_id=contact_id,
                activity_type="email",
                summary=summary
            )
    except Exception as e:
        print(f"[imap_sync] error: {e}")
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


def _worker(interval_minutes: int = 15):
    while True:
        try:
            sync_once()
        except Exception as e:
            print(f"[imap_sync worker] {e}")
        time.sleep(interval_minutes * 60)


def start_imap_sync(interval_minutes: int = 15):
    t = threading.Thread(target=_worker, args=(interval_minutes,), daemon=True)
    t.start()
