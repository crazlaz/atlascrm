"""Claude AI assistant — draft emails, summarize contact history."""
from db import get_connection

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def _call(prompt: str, max_tokens: int = 1024) -> str:
    try:
        client = _get_client()
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        text_parts = [b.text for b in response.content if b.type == "text"]
        return "\n".join(text_parts).strip()
    except Exception as e:
        return f"AI unavailable: {e}"


def _build_contact_context(contact_id: int) -> str:
    with get_connection() as conn:
        contact = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if not contact:
            return ""
        notes = conn.execute(
            "SELECT body, created_at FROM notes WHERE contact_id=? ORDER BY created_at DESC LIMIT 10",
            (contact_id,)
        ).fetchall()
        activities = conn.execute(
            "SELECT type, summary, created_at FROM activities "
            "WHERE contact_id=? ORDER BY created_at DESC LIMIT 20",
            (contact_id,)
        ).fetchall()

    lines = [
        f"Contact: {contact['first_name']} {contact['last_name']}",
        f"Email: {contact['email'] or 'unknown'}",
        f"Company: {contact['company'] or 'unknown'}",
        f"Phone: {contact['phone'] or 'unknown'}",
    ]
    if notes:
        lines.append("\nRecent notes:")
        for n in notes:
            lines.append(f"  [{n['created_at'][:10]}] {n['body'][:200]}")
    if activities:
        lines.append("\nRecent activity:")
        for a in activities:
            lines.append(f"  [{a['created_at'][:10]}] {a['type']}: {a['summary']}")
    return "\n".join(lines)


def draft_email(contact_id: int, instruction: str = "") -> str:
    ctx = _build_contact_context(contact_id)
    if not ctx:
        return "Contact not found."
    prompt = (
        f"You are a helpful CRM assistant for a hardscaping/lawn company. "
        f"Based on this contact's history, draft a professional follow-up email.\n\n"
        f"{ctx}\n\n"
        f"Additional instruction: {instruction or 'Write a warm, professional follow-up email.'}\n\n"
        f"Return ONLY the email body — no subject line, no explanation."
    )
    return _call(prompt, max_tokens=1024)


def summarize_contact(contact_id: int) -> str:
    ctx = _build_contact_context(contact_id)
    if not ctx:
        return "Contact not found."
    prompt = (
        f"You are a CRM assistant. Summarize this contact's last 30 days of activity "
        f"in 3-5 bullet points. Focus on: stage of relationship, key topics discussed, "
        f"any action items or follow-ups needed.\n\n{ctx}"
    )
    return _call(prompt, max_tokens=512)


def ask_anything(contact_id: int, question: str) -> str:
    ctx = _build_contact_context(contact_id)
    prompt = (
        f"You are a CRM assistant. Answer the user's question about this contact.\n\n"
        f"{ctx}\n\nQuestion: {question}"
    )
    return _call(prompt, max_tokens=1024)
