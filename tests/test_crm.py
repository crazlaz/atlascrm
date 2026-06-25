"""
Integration tests — run with:  python -m pytest tests/
Uses a temporary SQLite DB so production data is never touched.
"""
import os, sys, pytest

# point DB at a temp file before importing anything
import tempfile
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["CRM_DB"] = _tmp.name

# patch DB_PATH before any module loads it
import db as _db
_db.DB_PATH = _tmp.name
from pathlib import Path
_db.DB_PATH = Path(_tmp.name)

import contacts as C
import deals    as D
import notes    as N
import activity as A
import reminders as R
from db import init_db, get_connection
from auth import create_user, check_login, user_count


@pytest.fixture(autouse=True)
def fresh_db():
    """Wipe all rows before each test (leaf tables first to satisfy FK constraints)."""
    init_db()
    with get_connection() as conn:
        conn.executescript("""
            DELETE FROM reminders;
            DELETE FROM activities;
            DELETE FROM contact_tags;
            DELETE FROM notes;
            DELETE FROM deals;
            DELETE FROM tags;
            DELETE FROM users;
            DELETE FROM contacts;
            DELETE FROM sqlite_sequence;
        """)
    yield


# ── contacts ──────────────────────────────────────────────────────────────────

def test_add_and_get_contact():
    cid = C.add_contact("Alice", "Smith", email="alice@test.com", company="Acme")
    row = C.get_contact(cid)
    assert row["first_name"] == "Alice"
    assert row["email"]      == "alice@test.com"


def test_search_contacts():
    C.add_contact("Bob",   "Jones", email="bob@x.com")
    C.add_contact("Carol", "Lee",   email="carol@y.com", company="Lee Corp")
    assert len(C.search_contacts("bob"))      == 1
    assert len(C.search_contacts("lee"))      == 1
    assert len(C.search_contacts("notexist")) == 0


def test_update_contact():
    cid = C.add_contact("Dave", "Brown")
    C.update_contact(cid, company="NewCo")
    assert C.get_contact(cid)["company"] == "NewCo"


def test_delete_contact():
    cid = C.add_contact("Eve", "White")
    C.delete_contact(cid)
    assert C.get_contact(cid) is None


def test_tag_contact():
    cid = C.add_contact("Frank", "Black")
    C.tag_contact(cid, "vip")
    C.tag_contact(cid, "vip")   # duplicate — should not error
    with get_connection() as conn:
        tags = conn.execute(
            "SELECT t.name FROM tags t JOIN contact_tags ct ON ct.tag_id=t.id WHERE ct.contact_id=?",
            (cid,),
        ).fetchall()
    assert [t["name"] for t in tags] == ["vip"]


def test_contacts_by_tag():
    cid1 = C.add_contact("A", "A")
    cid2 = C.add_contact("B", "B")
    C.tag_contact(cid1, "hot")
    C.tag_contact(cid2, "cold")
    hot = C.get_contacts_by_tag("hot")
    assert len(hot) == 1
    assert hot[0]["id"] == cid1


# ── deals ─────────────────────────────────────────────────────────────────────

def test_add_deal():
    cid = C.add_contact("G", "H")
    did = D.add_deal(cid, "Widget sale", value=1000, stage="lead")
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
    assert row["title"] == "Widget sale"
    assert row["stage"] == "lead"


def test_move_stage():
    cid = C.add_contact("I", "J")
    did = D.add_deal(cid, "Big deal", value=5000)
    D.move_stage(did, "proposal")
    with get_connection() as conn:
        row = conn.execute("SELECT stage FROM deals WHERE id=?", (did,)).fetchone()
    assert row["stage"] == "proposal"


def test_move_stage_invalid():
    cid = C.add_contact("K", "L")
    did = D.add_deal(cid, "Deal")
    with pytest.raises(ValueError):
        D.move_stage(did, "bogus")


def test_won_sets_closed_at():
    cid = C.add_contact("M", "N")
    did = D.add_deal(cid, "Win me")
    D.move_stage(did, "won")
    with get_connection() as conn:
        row = conn.execute("SELECT closed_at FROM deals WHERE id=?", (did,)).fetchone()
    assert row["closed_at"] is not None


def test_pipeline_summary():
    cid = C.add_contact("O", "P")
    D.add_deal(cid, "D1", value=100, stage="lead")
    D.add_deal(cid, "D2", value=200, stage="lead")
    D.add_deal(cid, "D3", value=500, stage="won")
    summary = {r["stage"]: r for r in D.pipeline_summary()}
    assert summary["lead"]["count"] == 2
    assert summary["lead"]["total_value"] == 300
    assert summary["won"]["total_value"]  == 500


# ── notes ─────────────────────────────────────────────────────────────────────

def test_add_note_to_contact():
    cid = C.add_contact("Q", "R")
    nid = N.add_note("Hello", contact_id=cid)
    notes = N.get_notes(contact_id=cid)
    assert len(notes) == 1
    assert notes[0]["id"] == nid


def test_add_note_to_deal():
    cid = C.add_contact("S", "T")
    did = D.add_deal(cid, "Deal")
    N.add_note("Deal note", deal_id=did)
    notes = N.get_notes(deal_id=did)
    assert len(notes) == 1


def test_note_requires_contact_or_deal():
    with pytest.raises(ValueError):
        N.add_note("Orphan note")


# ── activity ──────────────────────────────────────────────────────────────────

def test_log_activity():
    cid = C.add_contact("U", "V")
    A.log_activity("call", "Called them", contact_id=cid)
    feed = A.get_activity_feed(contact_id=cid)
    assert len(feed) == 1
    assert feed[0]["type"] == "call"


def test_stage_change_auto_logs():
    cid  = C.add_contact("W", "X")
    did  = D.add_deal(cid, "Auto-log deal")
    D.move_stage(did, "qualified")
    feed = A.get_activity_feed(deal_id=did)
    types = [r["type"] for r in feed]
    assert "stage_change" in types


def test_invalid_activity_type():
    cid = C.add_contact("Y", "Z")
    with pytest.raises(ValueError):
        A.log_activity("smoke_signal", "???", contact_id=cid)


# ── reminders ────────────────────────────────────────────────────────────────

def test_add_and_complete_reminder():
    cid = C.add_contact("AA", "BB")
    rid = R.add_reminder("2099-01-01 09:00", "Future reminder", contact_id=cid)
    upcoming = R.get_upcoming_reminders(days=36500)
    assert any(r["id"] == rid for r in upcoming)
    R.complete_reminder(rid)
    upcoming2 = R.get_upcoming_reminders(days=36500)
    assert not any(r["id"] == rid for r in upcoming2)


def test_reminder_requires_contact_or_deal():
    with pytest.raises(ValueError):
        R.add_reminder("2099-01-01 09:00", "Orphan")


# ── auth ──────────────────────────────────────────────────────────────────────

def test_create_and_login():
    create_user("admin", "secret")
    assert user_count() == 1
    user = check_login("admin", "secret")
    assert user is not None
    assert user.username == "admin"


def test_wrong_password():
    create_user("admin2", "correct")
    assert check_login("admin2", "wrong") is None


def test_unknown_user():
    assert check_login("nobody", "pass") is None


# ── cascade delete ────────────────────────────────────────────────────────────

def test_cascade_delete_contact():
    cid = C.add_contact("CC", "DD")
    did = D.add_deal(cid, "Cascade deal")
    N.add_note("note", contact_id=cid)
    C.delete_contact(cid)
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM deals WHERE contact_id=?",    (cid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM notes WHERE contact_id=?",    (cid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM activities WHERE contact_id=?",(cid,)).fetchone()[0] == 0
