"""
crmpy CLI
  python cli.py contacts add
  python cli.py contacts list
  python cli.py contacts search <query>
  python cli.py contacts show <id>
  python cli.py contacts tag <id> <tag>

  python cli.py deals add <contact_id>
  python cli.py deals list <contact_id>
  python cli.py deals move <deal_id> <stage>
  python cli.py deals pipeline

  python cli.py notes add
  python cli.py notes list <contact_id|deal_id>

  python cli.py activity feed
  python cli.py activity recent

  python cli.py reminders add
  python cli.py reminders due
  python cli.py reminders upcoming
  python cli.py reminders done <id>
"""

import sys
from db import init_db, get_connection
import contacts as C
import deals as D
import notes as N
import activity as A
import reminders as R


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(row):
    return dict(row) if row else None


def _print_rows(rows):
    if not rows:
        print("  (none)")
        return
    for r in rows:
        print(" ", dict(r))


def _input(prompt, required=True):
    val = input(f"  {prompt}: ").strip()
    if required and not val:
        print(f"  '{prompt}' is required.")
        sys.exit(1)
    return val or None


# ── contacts ─────────────────────────────────────────────────────────────────

def cmd_contacts(args):
    sub = args[0] if args else "list"

    if sub == "add":
        first = _input("First name")
        last  = _input("Last name")
        email = _input("Email", required=False)
        phone = _input("Phone", required=False)
        co    = _input("Company", required=False)
        cid   = C.add_contact(first, last, email, phone, co)
        print(f"  Created contact #{cid}")

    elif sub == "list":
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM contacts ORDER BY last_name, first_name"
            ).fetchall()
        _print_rows(rows)

    elif sub == "search":
        query = args[1] if len(args) > 1 else _input("Search query")
        _print_rows(C.search_contacts(query))

    elif sub == "show":
        cid = int(args[1]) if len(args) > 1 else int(_input("Contact ID"))
        row = C.get_contact(cid)
        if not row:
            print("  Not found.")
            return
        print(" ", dict(row))
        print("  --- deals ---")
        _print_rows(D.get_deals_for_contact(cid))
        print("  --- notes ---")
        _print_rows(N.get_notes(contact_id=cid))

    elif sub == "tag":
        cid  = int(args[1]) if len(args) > 1 else int(_input("Contact ID"))
        tag  = args[2] if len(args) > 2 else _input("Tag name")
        C.tag_contact(cid, tag)
        print(f"  Tagged #{cid} with '{tag}'")

    elif sub == "by-tag":
        tag = args[1] if len(args) > 1 else _input("Tag name")
        _print_rows(C.get_contacts_by_tag(tag))

    else:
        print(f"  Unknown sub-command: {sub}")


# ── deals ─────────────────────────────────────────────────────────────────────

def cmd_deals(args):
    sub = args[0] if args else "pipeline"

    if sub == "add":
        cid   = int(args[1]) if len(args) > 1 else int(_input("Contact ID"))
        title = _input("Deal title")
        value = float(_input("Value (0)") or 0)
        stage = _input("Stage (lead)") or "lead"
        did   = D.add_deal(cid, title, value, stage)
        print(f"  Created deal #{did}")

    elif sub == "list":
        cid = int(args[1]) if len(args) > 1 else int(_input("Contact ID"))
        _print_rows(D.get_deals_for_contact(cid))

    elif sub == "move":
        did   = int(args[1]) if len(args) > 1 else int(_input("Deal ID"))
        stage = args[2] if len(args) > 2 else _input("New stage")
        D.move_stage(did, stage)
        print(f"  Deal #{did} moved to '{stage}'")

    elif sub == "pipeline":
        rows = D.pipeline_summary()
        print(f"  {'Stage':<12} {'Count':>6} {'Total Value':>14}")
        print("  " + "-" * 35)
        for r in rows:
            print(f"  {r['stage']:<12} {r['count']:>6} {r['total_value']:>14,.2f}")

    else:
        print(f"  Unknown sub-command: {sub}")


# ── notes ─────────────────────────────────────────────────────────────────────

def cmd_notes(args):
    sub = args[0] if args else "list"

    if sub == "add":
        body = _input("Note body")
        cid  = _input("Contact ID (leave blank for deal)", required=False)
        did  = _input("Deal ID", required=False) if not cid else None
        nid  = N.add_note(body, contact_id=int(cid) if cid else None,
                          deal_id=int(did) if did else None)
        print(f"  Note #{nid} saved")

    elif sub == "list":
        cid = _input("Contact ID (blank for deal)", required=False)
        did = _input("Deal ID", required=False) if not cid else None
        _print_rows(N.get_notes(
            contact_id=int(cid) if cid else None,
            deal_id=int(did) if did else None,
        ))

    else:
        print(f"  Unknown sub-command: {sub}")


# ── activity ──────────────────────────────────────────────────────────────────

def cmd_activity(args):
    sub = args[0] if args else "recent"

    if sub == "feed":
        cid = _input("Contact ID (blank for deal)", required=False)
        did = _input("Deal ID", required=False) if not cid else None
        _print_rows(A.get_activity_feed(
            contact_id=int(cid) if cid else None,
            deal_id=int(did) if did else None,
        ))

    elif sub == "recent":
        _print_rows(A.get_recent_activity())

    elif sub == "log":
        atype   = _input("Type (call/email/meeting/task/note/stage_change)")
        summary = _input("Summary")
        cid     = _input("Contact ID (blank for deal)", required=False)
        did     = _input("Deal ID", required=False) if not cid else None
        A.log_activity(atype, summary,
                       contact_id=int(cid) if cid else None,
                       deal_id=int(did) if did else None)
        print("  Activity logged")

    else:
        print(f"  Unknown sub-command: {sub}")


# ── reminders ─────────────────────────────────────────────────────────────────

def cmd_reminders(args):
    sub = args[0] if args else "upcoming"

    if sub == "add":
        due  = _input("Due (YYYY-MM-DD HH:MM)")
        body = _input("Reminder text")
        cid  = _input("Contact ID (blank for deal)", required=False)
        did  = _input("Deal ID", required=False) if not cid else None
        rid  = R.add_reminder(due, body,
                              contact_id=int(cid) if cid else None,
                              deal_id=int(did) if did else None)
        print(f"  Reminder #{rid} set for {due}")

    elif sub == "due":
        _print_rows(R.get_due_reminders())

    elif sub == "upcoming":
        days = int(args[1]) if len(args) > 1 else 7
        _print_rows(R.get_upcoming_reminders(days))

    elif sub == "done":
        rid = int(args[1]) if len(args) > 1 else int(_input("Reminder ID"))
        R.complete_reminder(rid)
        print(f"  Reminder #{rid} marked done")

    else:
        print(f"  Unknown sub-command: {sub}")


# ── dispatch ──────────────────────────────────────────────────────────────────

COMMANDS = {
    "contacts":  cmd_contacts,
    "deals":     cmd_deals,
    "notes":     cmd_notes,
    "activity":  cmd_activity,
    "reminders": cmd_reminders,
}


def main():
    init_db()
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        print("Usage: python cli.py <contacts|deals|notes|activity|reminders> [sub] [args]")
        sys.exit(0)
    COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    main()
