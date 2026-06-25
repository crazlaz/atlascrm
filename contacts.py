from db import get_connection


def add_contact(first_name, last_name, email=None, phone=None, company=None):
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO contacts (first_name, last_name, email, phone, company)
               VALUES (?, ?, ?, ?, ?)""",
            (first_name, last_name, email, phone, company),
        )
        return cur.lastrowid


def get_contact(contact_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()


def search_contacts(query):
    like = f"%{query}%"
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM contacts
               WHERE first_name LIKE ? OR last_name LIKE ?
                  OR email LIKE ? OR company LIKE ?
               ORDER BY last_name, first_name""",
            (like, like, like, like),
        ).fetchall()


def update_contact(contact_id, **fields):
    allowed = {"first_name", "last_name", "email", "phone", "company"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    set_clause += ", updated_at = datetime('now')"
    with get_connection() as conn:
        conn.execute(
            f"UPDATE contacts SET {set_clause} WHERE id = ?",
            (*updates.values(), contact_id),
        )


def delete_contact(contact_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))


def tag_contact(contact_id, tag_name):
    with get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
        tag_id = conn.execute(
            "SELECT id FROM tags WHERE name = ?", (tag_name,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO contact_tags (contact_id, tag_id) VALUES (?, ?)",
            (contact_id, tag_id),
        )


def get_contacts_by_tag(tag_name):
    with get_connection() as conn:
        return conn.execute(
            """SELECT c.* FROM contacts c
               JOIN contact_tags ct ON ct.contact_id = c.id
               JOIN tags t ON t.id = ct.tag_id
               WHERE t.name = ?
               ORDER BY c.last_name, c.first_name""",
            (tag_name,),
        ).fetchall()
