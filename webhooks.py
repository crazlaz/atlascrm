import json, threading, urllib.request, urllib.error
from db import get_connection


def get_webhooks(event: str = None) -> list:
    with get_connection() as conn:
        if event:
            return conn.execute(
                "SELECT * FROM webhooks WHERE event=? AND active=1", (event,)
            ).fetchall()
        return conn.execute("SELECT * FROM webhooks ORDER BY event").fetchall()


def add_webhook(url: str, event: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO webhooks (url, event) VALUES (?,?)", (url, event)
        )


def delete_webhook(wid: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM webhooks WHERE id=?", (wid,))


def toggle_webhook(wid: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE webhooks SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=?",
            (wid,),
        )


def fire(event: str, payload: dict):
    hooks = get_webhooks(event)
    if not hooks:
        return

    def _send(url, data):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    for hook in hooks:
        threading.Thread(target=_send, args=(hook["url"], {"event": event, **payload}),
                         daemon=True).start()
