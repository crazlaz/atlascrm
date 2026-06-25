from db import get_connection


def get_field_defs(entity: str) -> list:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM custom_field_defs WHERE entity=? ORDER BY label", (entity,)
        ).fetchall()


def create_field_def(entity: str, label: str, field_type: str = "text", options: str = None):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO custom_field_defs (entity, label, field_type, options) VALUES (?,?,?,?)",
            (entity, label, field_type, options),
        )


def delete_field_def(fid: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM custom_field_defs WHERE id=?", (fid,))


def get_values(entity: str, entity_id: int) -> dict:
    defs = get_field_defs(entity)
    if not defs:
        return {}
    field_ids = [d["id"] for d in defs]
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT field_id, value FROM custom_field_values WHERE entity_id=? AND field_id IN ({','.join('?'*len(field_ids))})",
            (entity_id, *field_ids),
        ).fetchall()
    result = {d["label"]: None for d in defs}
    id_to_label = {d["id"]: d["label"] for d in defs}
    for r in rows:
        result[id_to_label[r["field_id"]]] = r["value"]
    return result


def set_values(entity: str, entity_id: int, data: dict):
    defs = {d["label"]: d for d in get_field_defs(entity)}
    with get_connection() as conn:
        for label, value in data.items():
            if label not in defs:
                continue
            fid = defs[label]["id"]
            conn.execute(
                """INSERT INTO custom_field_values (field_id, entity_id, value) VALUES (?,?,?)
                   ON CONFLICT(field_id, entity_id) DO UPDATE SET value=excluded.value""",
                (fid, entity_id, value or None),
            )


def get_fields_with_values(entity: str, entity_id: int) -> list:
    defs = get_field_defs(entity)
    if not defs:
        return []
    values = get_values(entity, entity_id)
    return [{"id": d["id"], "label": d["label"], "field_type": d["field_type"],
             "options": d["options"], "value": values.get(d["label"])} for d in defs]
