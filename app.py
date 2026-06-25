import io, os, tempfile, csv
from flask import (Flask, render_template, request, redirect,
                   url_for, flash, send_file, Response, jsonify, session)
from flask_login import login_required, login_user, logout_user, current_user
from db import init_db, get_connection
from translations import get_translator, LANGUAGES
import contacts as C
import deals as D
import notes as N
import activity as A
import reminders as R
import spark as SP
from auth import login_manager, create_user, check_login, user_count
import backup as BK
import email_service as EM
import attachments as AT
import custom_fields as CF
import webhooks as WH
import drip as DRIP
import imap_sync as IMAP
import ai_assistant as AI
import tracking as TRK
import jobs as JB
import estimates as EST
import invoices as INV
import service_catalog as SC
import crew as CR
import job_costs as JC
import recurring as REC
import automations as AUTO

app = Flask(__name__)
app.secret_key = "crm-dev-secret-change-me"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit
login_manager.init_app(app)

PER_PAGE   = 25
STAGE_PROB = {"lead": 0.10, "qualified": 0.30, "proposal": 0.60, "won": 1.0, "lost": 0.0}


@app.context_processor
def inject_lang():
    lang = session.get('lang', 'en')
    return dict(t=get_translator(lang), lang=lang, languages=LANGUAGES)


@app.route('/set-lang/<lang>')
def set_lang(lang):
    if lang in LANGUAGES:
        session['lang'] = lang
    return redirect(request.referrer or url_for('dashboard'))


# ── one-time startup init ─────────────────────────────────────────────────────
from db import migrate_db
init_db()
migrate_db()

# ── auto-backup (start once) ──────────────────────────────────────────────────
BK.start_auto_backup(interval_hours=24)
DRIP.start_drip_scheduler(interval_minutes=30)
IMAP.start_imap_sync(interval_minutes=15)
SC.seed_defaults()
REC.start_recurring_scheduler(interval_hours=24)
AUTO.start_automation_scheduler(interval_hours=24)


# ── auth ──────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    setup_mode = user_count() == 0
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if setup_mode:
            create_user(username, password)
        user = check_login(username, password)
        if user:
            login_user(user)
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html", setup=setup_mode)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    with get_connection() as conn:
        stats = {
            "contacts":       conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
            "open_deals":     conn.execute("SELECT COUNT(*) FROM deals WHERE stage NOT IN ('won','lost')").fetchone()[0],
            "pipeline_value": conn.execute("SELECT COALESCE(SUM(value),0) FROM deals WHERE stage NOT IN ('won','lost')").fetchone()[0],
            "won_value":      conn.execute("SELECT COALESCE(SUM(value),0) FROM deals WHERE stage='won' AND strftime('%Y-%m',closed_at)=strftime('%Y-%m','now')").fetchone()[0],
        }
    return render_template("dashboard.html", stats=stats,
                           pipeline=D.pipeline_summary(),
                           reminders=R.get_upcoming_reminders(7),
                           activity=A.get_recent_activity(10))


# ── search ────────────────────────────────────────────────────────────────────

@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    like = f"%{q}%"
    contacts_r, deals_r, notes_r = [], [], []
    if q:
        with get_connection() as conn:
            contacts_r = conn.execute(
                "SELECT * FROM contacts WHERE first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR company LIKE ? ORDER BY last_name LIMIT 50",
                (like, like, like, like)).fetchall()
            deals_r = conn.execute(
                "SELECT d.*,c.first_name||' '||c.last_name AS contact_name FROM deals d JOIN contacts c ON c.id=d.contact_id WHERE d.title LIKE ? OR c.first_name LIKE ? OR c.last_name LIKE ? ORDER BY d.updated_at DESC LIMIT 50",
                (like, like, like)).fetchall()
            notes_r = conn.execute(
                "SELECT n.*,c.first_name||' '||c.last_name AS contact_name FROM notes n LEFT JOIN contacts c ON c.id=n.contact_id WHERE n.body LIKE ? ORDER BY n.created_at DESC LIMIT 50",
                (like,)).fetchall()
    return render_template("search.html", q=q, contacts=contacts_r, deals=deals_r, notes=notes_r)


# ── contacts ──────────────────────────────────────────────────────────────────

@app.route("/contacts")
@login_required
def contacts_list():
    q    = request.args.get("q", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    rows = C.search_contacts(q) if q else _all_contacts()
    total_pages = max(1, (len(rows) + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    return render_template("contacts.html", contacts=rows[(page-1)*PER_PAGE:page*PER_PAGE],
                           q=q, page=page, total_pages=total_pages)


@app.route("/contacts/new", methods=["GET", "POST"])
@login_required
def contact_new():
    if request.method == "POST":
        f = request.form
        cid = C.add_contact(f["first_name"], f["last_name"],
                            f.get("email") or None, f.get("phone") or None,
                            f.get("company") or None)
        WH.fire("contact_created", {"contact_id": cid})
        flash("Contact created.", "success")
        return redirect(url_for("contact_detail", cid=cid))
    return render_template("contact_form.html", contact=None)


@app.route("/contacts/bulk", methods=["POST"])
@login_required
def contacts_bulk():
    action = request.form.get("action")
    ids    = request.form.getlist("ids")
    if not ids:
        flash("No contacts selected.", "error")
        return redirect(url_for("contacts_list"))
    if action == "delete":
        for cid in ids: C.delete_contact(int(cid))
        flash(f"Deleted {len(ids)} contact(s).", "success")
    elif action == "tag":
        tag = request.form.get("tag_value", "").strip()
        if not tag:
            flash("Enter a tag name.", "error")
            return redirect(url_for("contacts_list"))
        for cid in ids: C.tag_contact(int(cid), tag)
        flash(f"Tagged {len(ids)} contact(s) with '{tag}'.", "success")
    elif action == "export":
        with get_connection() as conn:
            rows = conn.execute(f"SELECT * FROM contacts WHERE id IN ({','.join('?'*len(ids))})", ids).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id","first_name","last_name","email","phone","company","created_at"])
        for r in rows: w.writerow([r["id"],r["first_name"],r["last_name"],r["email"],r["phone"],r["company"],r["created_at"]])
        buf.seek(0)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition":"attachment;filename=contacts_export.csv"})
    return redirect(url_for("contacts_list"))


@app.route("/contacts/merge", methods=["GET", "POST"])
@login_required
def contacts_merge():
    if request.method == "POST":
        src, tgt = int(request.form["source_id"]), int(request.form["target_id"])
        if src == tgt:
            flash("Source and target must be different.", "error")
        else:
            with get_connection() as conn:
                for tbl, col in [("deals","contact_id"),("notes","contact_id"),
                                  ("activities","contact_id"),("reminders","contact_id")]:
                    conn.execute(f"UPDATE {tbl} SET {col}=? WHERE {col}=?", (tgt, src))
                conn.execute("INSERT OR IGNORE INTO contact_tags (contact_id,tag_id) SELECT ?,tag_id FROM contact_tags WHERE contact_id=?", (tgt, src))
                conn.execute("DELETE FROM contacts WHERE id=?", (src,))
            flash("Contacts merged.", "success")
            return redirect(url_for("contact_detail", cid=tgt))
    return render_template("merge.html", contacts=_all_contacts(),
                           source=request.args.get("source"), target=request.args.get("target"))


@app.route("/contacts/<int:cid>")
@login_required
def contact_detail(cid):
    contact = C.get_contact(cid)
    if not contact:
        flash("Contact not found.", "error")
        return redirect(url_for("contacts_list"))
    with get_connection() as conn:
        tags = conn.execute("SELECT t.name FROM tags t JOIN contact_tags ct ON ct.tag_id=t.id WHERE ct.contact_id=?", (cid,)).fetchall()
    return render_template("contact_detail.html",
        contact=contact, tags=tags,
        deals=D.get_deals_for_contact(cid),
        notes=N.get_notes(contact_id=cid),
        activity=A.get_activity_feed(contact_id=cid),
        attachments=AT.get_attachments(contact_id=cid),
        custom_fields=CF.get_fields_with_values("contact", cid),
        email_templates=EM.get_templates(),
        drip_sequences=DRIP.get_sequences(),
        drip_enrollments=DRIP.get_enrollments(cid),
        tracking_stats=TRK.get_tracking_stats(cid))


@app.route("/contacts/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def contact_edit(cid):
    contact = C.get_contact(cid)
    if request.method == "POST":
        f = request.form
        C.update_contact(cid, first_name=f["first_name"], last_name=f["last_name"],
                         email=f.get("email") or None, phone=f.get("phone") or None,
                         company=f.get("company") or None)
        flash("Contact updated.", "success")
        return redirect(url_for("contact_detail", cid=cid))
    return render_template("contact_form.html", contact=contact)


@app.route("/contacts/<int:cid>/delete", methods=["POST"])
@login_required
def contact_delete(cid):
    C.delete_contact(cid)
    flash("Contact deleted.", "success")
    return redirect(url_for("contacts_list"))


@app.route("/contacts/<int:cid>/tag", methods=["POST"])
@login_required
def contact_tag(cid):
    tag = request.form.get("tag", "").strip()
    if tag: C.tag_contact(cid, tag)
    return redirect(url_for("contact_detail", cid=cid))


@app.route("/contacts/<int:cid>/notes", methods=["POST"])
@login_required
def contact_add_note(cid):
    body = request.form.get("body", "").strip()
    if body: N.add_note(body, contact_id=cid)
    return redirect(url_for("contact_detail", cid=cid))


@app.route("/contacts/<int:cid>/activity", methods=["POST"])
@login_required
def contact_log_activity(cid):
    atype   = request.form["type"]
    summary = request.form.get("summary", "").strip() or request.form.get("outcome", "Call logged")
    body    = request.form.get("body", "").strip() or None
    A.log_activity(atype, summary, contact_id=cid)
    if body:
        N.add_note(f"[{atype}] {body}", contact_id=cid)
    return redirect(url_for("contact_detail", cid=cid))


@app.route("/contacts/<int:cid>/reminders", methods=["POST"])
@login_required
def contact_add_reminder(cid):
    R.add_reminder(request.form["due_at"].replace("T"," "), request.form["body"], contact_id=cid)
    flash("Reminder set.", "success")
    return redirect(url_for("contact_detail", cid=cid))


@app.route("/contacts/<int:cid>/send-email", methods=["POST"])
@login_required
def contact_send_email(cid):
    contact = C.get_contact(cid)
    f       = request.form
    to      = f.get("to", "").strip()
    subject = f.get("subject", "").strip()
    body    = f.get("body", "").strip()
    err = EM.send_email(to, subject, body)
    if err:
        flash(f"Email error: {err}", "error")
    else:
        A.log_activity("email", f"Sent: {subject}", contact_id=cid)
        flash(f"Email sent to {to}.", "success")
    return redirect(url_for("contact_detail", cid=cid))


@app.route("/contacts/<int:cid>/attachments", methods=["POST"])
@login_required
def contact_upload(cid):
    f = request.files.get("file")
    if f and f.filename:
        AT.save_attachment(f, contact_id=cid, user_id=current_user.id)
        flash("File uploaded.", "success")
    return redirect(url_for("contact_detail", cid=cid))


@app.route("/contacts/<int:cid>/custom-fields", methods=["POST"])
@login_required
def contact_custom_fields(cid):
    CF.set_values("contact", cid, dict(request.form))
    flash("Fields saved.", "success")
    return redirect(url_for("contact_detail", cid=cid))


# ── deals ─────────────────────────────────────────────────────────────────────

@app.route("/deals")
@login_required
def deals_list():
    page = max(1, int(request.args.get("page", 1)))
    with get_connection() as conn:
        all_deals = conn.execute(
            "SELECT d.*,c.first_name||' '||c.last_name AS contact_name,"
            "CAST(julianday('now')-julianday(d.updated_at) AS INTEGER) AS age_days "
            "FROM deals d JOIN contacts c ON c.id=d.contact_id ORDER BY d.updated_at DESC"
        ).fetchall()
    total_pages = max(1, (len(all_deals) + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    return render_template("deals.html", deals=all_deals[(page-1)*PER_PAGE:page*PER_PAGE],
                           page=page, total_pages=total_pages)


@app.route("/deals/new", methods=["GET", "POST"])
@login_required
def deal_new():
    if request.method == "POST":
        f   = request.form
        did = D.add_deal(int(f["contact_id"]), f["title"],
                         float(f.get("value") or 0), f.get("stage","lead"))
        flash("Deal created.", "success")
        return redirect(url_for("deal_detail", did=did))
    return render_template("deal_form.html", deal=None, contacts=_all_contacts(),
                           preselect=request.args.get("contact_id",""))


@app.route("/deals/<int:did>")
@login_required
def deal_detail(did):
    with get_connection() as conn:
        deal = conn.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
    if not deal:
        flash("Deal not found.", "error")
        return redirect(url_for("deals_list"))
    return render_template("deal_detail.html",
        deal=deal,
        contact=C.get_contact(deal["contact_id"]),
        notes=N.get_notes(deal_id=did),
        activity=A.get_activity_feed(deal_id=did),
        attachments=AT.get_attachments(deal_id=did),
        custom_fields=CF.get_fields_with_values("deal", did),
        email_templates=EM.get_templates())


@app.route("/deals/<int:did>/edit", methods=["GET", "POST"])
@login_required
def deal_edit(did):
    with get_connection() as conn:
        deal = conn.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
    if request.method == "POST":
        f = request.form
        with get_connection() as conn:
            conn.execute(
                "UPDATE deals SET contact_id=?,title=?,value=?,stage=?,updated_at=datetime('now') WHERE id=?",
                (int(f["contact_id"]), f["title"], float(f.get("value") or 0), f["stage"], did))
        flash("Deal updated.", "success")
        return redirect(url_for("deal_detail", did=did))
    return render_template("deal_form.html", deal=deal, contacts=_all_contacts(), preselect="")


@app.route("/deals/<int:did>/stage", methods=["POST"])
@login_required
def deal_move_stage(did):
    stage = request.form["stage"]
    D.move_stage(did, stage)
    # AJAX drag-and-drop returns 200 with no redirect
    if request.headers.get("X-Requested-With") == "fetch":
        return "", 200
    flash("Stage updated.", "success")
    return redirect(request.referrer or url_for("deals_list"))


@app.route("/deals/<int:did>/notes", methods=["POST"])
@login_required
def deal_add_note(did):
    body = request.form.get("body","").strip()
    if body: N.add_note(body, deal_id=did)
    return redirect(url_for("deal_detail", did=did))


@app.route("/deals/<int:did>/attachments", methods=["POST"])
@login_required
def deal_upload(did):
    f = request.files.get("file")
    if f and f.filename:
        AT.save_attachment(f, deal_id=did, user_id=current_user.id)
        flash("File uploaded.", "success")
    return redirect(url_for("deal_detail", did=did))


@app.route("/deals/<int:did>/custom-fields", methods=["POST"])
@login_required
def deal_custom_fields(did):
    CF.set_values("deal", did, dict(request.form))
    flash("Fields saved.", "success")
    return redirect(url_for("deal_detail", did=did))


# ── attachments ───────────────────────────────────────────────────────────────

@app.route("/attachments/<int:aid>/download")
@login_required
def attachment_download(aid):
    att  = AT.get_attachment(aid)
    if not att:
        flash("File not found.", "error")
        return redirect(url_for("dashboard"))
    path = AT.UPLOAD_DIR / att["stored_name"]
    if not path.exists():
        flash("File missing from disk.", "error")
        return redirect(url_for("dashboard"))
    return send_file(str(path), as_attachment=True, download_name=att["filename"])


@app.route("/attachments/<int:aid>/delete", methods=["POST"])
@login_required
def attachment_delete(aid):
    att = AT.get_attachment(aid)
    AT.delete_attachment(aid)
    if att and att["contact_id"]:
        return redirect(url_for("contact_detail", cid=att["contact_id"]))
    if att and att["deal_id"]:
        return redirect(url_for("deal_detail", did=att["deal_id"]))
    return redirect(url_for("dashboard"))


# ── reminders ─────────────────────────────────────────────────────────────────

@app.route("/reminders")
@login_required
def reminders_list():
    return render_template("reminders.html",
        due=R.get_due_reminders(), upcoming=R.get_upcoming_reminders(30),
        contacts=_all_contacts())


@app.route("/reminders/new", methods=["POST"])
@login_required
def reminder_new():
    f   = request.form
    cid = int(f["contact_id"]) if f.get("contact_id") else None
    R.add_reminder(f["due_at"].replace("T"," "), f["body"], contact_id=cid)
    flash("Reminder set.", "success")
    return redirect(url_for("reminders_list"))


@app.route("/reminders/<int:rid>/done", methods=["POST"])
@login_required
def reminder_done(rid):
    R.complete_reminder(rid)
    return redirect(request.referrer or url_for("reminders_list"))


# ── analytics ─────────────────────────────────────────────────────────────────

@app.route("/analytics")
@login_required
def analytics():
    return render_template("analytics.html",
        d=SP.pipeline_analytics(), scores=SP.contact_scores())


# ── forecast ─────────────────────────────────────────────────────────────────

@app.route("/forecast")
@login_required
def forecast():
    with get_connection() as conn:
        raw = conn.execute(
            "SELECT d.*,c.first_name||' '||c.last_name AS contact_name,"
            "CAST(julianday('now')-julianday(d.updated_at) AS INTEGER) AS age_days "
            "FROM deals d JOIN contacts c ON c.id=d.contact_id "
            "WHERE d.stage NOT IN ('won','lost') ORDER BY d.value DESC"
        ).fetchall()
    deals = [{**dict(r), "prob": STAGE_PROB.get(r["stage"],0),
              "weighted": r["value"]*STAGE_PROB.get(r["stage"],0)} for r in raw]
    stage_map = {}
    for d in deals:
        s = d["stage"]
        if s not in stage_map:
            stage_map[s] = {"stage":s,"prob":d["prob"],"count":0,"raw":0,"weighted":0}
        stage_map[s]["count"]    += 1
        stage_map[s]["raw"]      += d["value"]
        stage_map[s]["weighted"] += d["weighted"]
    return render_template("forecast.html", deals=deals,
        by_stage=sorted(stage_map.values(), key=lambda r:r["prob"]),
        total_weighted=sum(d["weighted"] for d in deals),
        total_best=sum(d["value"] for d in deals))


# ── churn ─────────────────────────────────────────────────────────────────────

@app.route("/churn")
@login_required
def churn():
    with get_connection() as conn:
        at_risk     = conn.execute("SELECT c.*,MAX(a.created_at) AS last_activity,CAST(julianday('now')-julianday(MAX(a.created_at)) AS INTEGER) AS days_silent,COUNT(DISTINCT d.id) AS open_deals FROM contacts c LEFT JOIN activities a ON a.contact_id=c.id LEFT JOIN deals d ON d.contact_id=c.id AND d.stage NOT IN ('won','lost') GROUP BY c.id HAVING days_silent>=60 OR last_activity IS NULL ORDER BY days_silent DESC").fetchall()
        stale_deals = conn.execute("SELECT d.*,c.first_name||' '||c.last_name AS contact_name,CAST(julianday('now')-julianday(d.updated_at) AS INTEGER) AS days_in_stage FROM deals d JOIN contacts c ON c.id=d.contact_id WHERE d.stage NOT IN ('won','lost') AND days_in_stage>=30 ORDER BY days_in_stage DESC").fetchall()
        recently_won = conn.execute("SELECT d.*,c.first_name||' '||c.last_name AS contact_name FROM deals d JOIN contacts c ON c.id=d.contact_id WHERE d.stage='won' AND d.closed_at>=datetime('now','-30 days') ORDER BY d.closed_at DESC").fetchall()
    return render_template("churn.html", at_risk=at_risk,
                           stale_deals=stale_deals, recently_won=recently_won)


# ── audit log ────────────────────────────────────────────────────────────────

@app.route("/audit")
@login_required
def audit_log():
    page           = max(1, int(request.args.get("page", 1)))
    filter_type    = request.args.get("type", "")
    filter_contact = request.args.get("contact", "")
    filter_from    = request.args.get("from", "")
    filter_to      = request.args.get("to", "")

    wheres, params = [], []
    if filter_type:
        wheres.append("a.type=?"); params.append(filter_type)
    if filter_contact:
        like = f"%{filter_contact}%"
        wheres.append("(c.first_name LIKE ? OR c.last_name LIKE ?)")
        params += [like, like]
    if filter_from:
        wheres.append("a.created_at>=?"); params.append(filter_from)
    if filter_to:
        wheres.append("a.created_at<=?"); params.append(filter_to + " 23:59:59")

    where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
    with get_connection() as conn:
        all_rows = conn.execute(
            f"SELECT a.*,c.first_name||' '||c.last_name AS contact_name "
            f"FROM activities a LEFT JOIN contacts c ON c.id=a.contact_id "
            f"{where_sql} ORDER BY a.created_at DESC",
            params).fetchall()

    total_pages = max(1, (len(all_rows) + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    return render_template("audit.html",
        rows=all_rows[(page-1)*PER_PAGE:page*PER_PAGE],
        page=page, total_pages=total_pages,
        filter_type=filter_type, filter_contact=filter_contact,
        filter_from=filter_from, filter_to=filter_to)


# ── import / export ───────────────────────────────────────────────────────────

@app.route("/import", methods=["GET", "POST"])
@login_required
def import_contacts():
    result = None
    if request.method == "POST":
        f = request.files.get("file")
        if f and f.filename.endswith(".csv"):
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
            f.save(tmp.name)
            result = SP.import_contacts_csv(tmp.name)
            os.unlink(tmp.name)
        else:
            flash("Please upload a .csv file.", "error")
    return render_template("import.html", result=result)


@app.route("/import/leads", methods=["POST"])
@login_required
def import_leads():
    import csv as _csv, io
    f = request.files.get("file")
    if not f or not f.filename.endswith(".csv"):
        flash("Please upload a .csv file.", "error")
        return redirect(url_for("import_contacts"))

    stream  = io.StringIO(f.stream.read().decode("utf-8", errors="replace"))
    reader  = _csv.DictReader(stream)
    inserted = skipped = 0

    for row in reader:
        company_name = row.get("Company Name", "").strip()
        if not company_name:
            continue

        parts = company_name.split()
        first = parts[0]
        last  = " ".join(parts[1:]) if len(parts) > 1 else "—"

        email    = row.get("Email", "").strip() or None
        phone    = row.get("Phone", "").strip() or None
        website  = row.get("Website", "").strip()
        linkedin = row.get("LinkedIn", "").strip()
        facebook = row.get("Facebook", "").strip()
        address  = row.get("Address", "").strip()
        rating   = row.get("Google Rating", "").strip()
        reviews  = row.get("# Reviews", "").strip()
        category = row.get("Category", "").strip()

        try:
            cid = C.add_contact(first, last, email, phone, company_name)

            note_parts = []
            if website:  note_parts.append(f"Website: {website}")
            if linkedin: note_parts.append(f"LinkedIn: {linkedin}")
            if facebook: note_parts.append(f"Facebook: {facebook}")
            if address:  note_parts.append(f"Address: {address}")
            if rating:   note_parts.append(f"Google Rating: {rating} ⭐ ({reviews} reviews)")
            if note_parts:
                N.add_note("\n".join(note_parts), contact_id=cid)

            if category:
                C.tag_contact(cid, category)

            D.add_deal(cid, f"Contractor Outreach — {company_name}", 0, "lead")
            A.log_activity("note", f"Imported as lead — {category or 'Contractor'}", contact_id=cid)
            inserted += 1
        except Exception:
            skipped += 1

    flash(f"Import complete: {inserted} leads added, {skipped} skipped (duplicates).", "success")
    return redirect(url_for("import_contacts"))


@app.route("/export/contacts")
@login_required
def export_contacts():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    SP.export_contacts_csv(tmp.name)
    return send_file(tmp.name, as_attachment=True, download_name="contacts.csv", mimetype="text/csv")


@app.route("/export/deals")
@login_required
def export_deals():
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    SP.export_deals_csv(tmp.name)
    return send_file(tmp.name, as_attachment=True, download_name="deals.csv", mimetype="text/csv")


# ── settings ──────────────────────────────────────────────────────────────────

@app.route("/settings")
@login_required
def settings():
    return render_template("settings.html",
        smtp=EM.get_smtp_config() or {},
        email_templates=EM.get_templates(),
        custom_fields={"contact": CF.get_field_defs("contact"),
                       "deal":    CF.get_field_defs("deal")},
        webhooks=WH.get_webhooks(),
        backups=BK.list_backups())


@app.route("/settings/smtp", methods=["POST"])
@login_required
def settings_smtp():
    f = request.form
    EM.save_smtp_config(f.get("host",""), f.get("port",587), f.get("username",""),
                        f.get("password",""), 1 if f.get("use_tls") else 0,
                        f.get("from_addr",""), f.get("imap_host",""))
    flash("SMTP settings saved.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/smtp-test")
@login_required
def settings_smtp_test():
    cfg = EM.get_smtp_config()
    if not cfg or not cfg.get("username"):
        flash("Configure SMTP first.", "error")
        return redirect(url_for("settings"))
    err = EM.send_email(cfg["username"], "AtlasCRM test email", "Your SMTP is configured correctly!")
    flash("Test email sent!" if not err else f"Error: {err}",
          "success" if not err else "error")
    return redirect(url_for("settings"))


@app.route("/settings/email-templates", methods=["POST"])
@login_required
def settings_save_template():
    f = request.form
    EM.save_template(f["name"], f["subject"], f.get("body",""))
    flash("Template saved.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/email-templates/<int:tid>/delete", methods=["POST"])
@login_required
def settings_delete_template(tid):
    EM.delete_template(tid)
    flash("Template deleted.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/custom-fields", methods=["POST"])
@login_required
def settings_add_field():
    f = request.form
    CF.create_field_def(f["entity"], f["label"], f.get("field_type","text"), f.get("options") or None)
    flash("Custom field added.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/custom-fields/<int:fid>/delete", methods=["POST"])
@login_required
def settings_delete_field(fid):
    CF.delete_field_def(fid)
    flash("Custom field deleted.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/webhooks", methods=["POST"])
@login_required
def settings_add_webhook():
    WH.add_webhook(request.form["url"], request.form["event"])
    flash("Webhook added.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/webhooks/<int:wid>/toggle", methods=["POST"])
@login_required
def settings_toggle_webhook(wid):
    WH.toggle_webhook(wid)
    return redirect(url_for("settings"))


@app.route("/settings/webhooks/<int:wid>/delete", methods=["POST"])
@login_required
def settings_delete_webhook(wid):
    WH.delete_webhook(wid)
    flash("Webhook deleted.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/backup", methods=["POST"])
@login_required
def settings_backup():
    dest = BK.create_backup()
    flash(f"Backup created: {dest.name}", "success")
    return redirect(url_for("settings"))


@app.route("/settings/backup/<name>/download")
@login_required
def settings_backup_download(name):
    path = BK.BACKUP_DIR / name
    if not path.exists():
        flash("Backup not found.", "error")
        return redirect(url_for("settings"))
    return send_file(str(path), as_attachment=True, download_name=name,
                     mimetype="application/octet-stream")


@app.route("/settings/password", methods=["POST"])
@login_required
def settings_password():
    if not check_login(current_user.username, request.form["current_password"]):
        flash("Current password is incorrect.", "error")
    else:
        import hashlib, os as _os
        salt = _os.urandom(16).hex()
        h    = hashlib.sha256((salt + request.form["new_password"]).encode()).hexdigest()
        with get_connection() as conn:
            conn.execute("UPDATE users SET password_hash=?,salt=? WHERE id=?",
                         (h, salt, current_user.id))
        flash("Password updated.", "success")
    return redirect(url_for("settings"))


@app.route("/settings/purge-activity", methods=["POST"])
@login_required
def settings_purge_activity():
    with get_connection() as conn:
        conn.execute("DELETE FROM activities WHERE created_at<datetime('now','-90 days')")
    flash("Old activity logs purged.", "success")
    return redirect(url_for("settings"))


# ── drip sequences ────────────────────────────────────────────────────────────

@app.route("/drip")
@login_required
def drip_list():
    return render_template("drip.html", sequences=DRIP.get_sequences())


@app.route("/drip/new", methods=["POST"])
@login_required
def drip_new():
    try:
        sid = DRIP.create_sequence(request.form["name"], request.form.get("description",""))
        flash("Sequence created.", "success")
        return redirect(url_for("drip_detail", sid=sid))
    except Exception:
        flash("A sequence with that name already exists.", "error")
        return redirect(url_for("drip_list"))


@app.route("/drip/<int:sid>")
@login_required
def drip_detail(sid):
    seq, steps = DRIP.get_sequence(sid)
    if not seq:
        flash("Sequence not found.", "error")
        return redirect(url_for("drip_list"))
    return render_template("drip_detail.html", seq=seq, steps=steps)


@app.route("/drip/<int:sid>/steps", methods=["POST"])
@login_required
def drip_add_step(sid):
    f = request.form
    DRIP.add_step(sid, int(f["step_number"]), int(f["delay_days"]),
                  f["subject"], f["body"])
    flash("Step added.", "success")
    return redirect(url_for("drip_detail", sid=sid))


@app.route("/drip/<int:sid>/steps/<int:step_id>/delete", methods=["POST"])
@login_required
def drip_delete_step(sid, step_id):
    DRIP.delete_step(step_id)
    flash("Step deleted.", "success")
    return redirect(url_for("drip_detail", sid=sid))


@app.route("/drip/<int:sid>/delete", methods=["POST"])
@login_required
def drip_delete(sid):
    DRIP.delete_sequence(sid)
    flash("Sequence deleted.", "success")
    return redirect(url_for("drip_list"))


@app.route("/contacts/<int:cid>/drip/enroll", methods=["POST"])
@login_required
def drip_enroll(cid):
    sid = int(request.form["sequence_id"])
    ok  = DRIP.enroll_contact(cid, sid)
    flash("Enrolled in sequence." if ok else "Already enrolled in this sequence.", "success" if ok else "error")
    return redirect(url_for("contact_detail", cid=cid))


@app.route("/drip/enrollments/<int:eid>/cancel", methods=["POST"])
@login_required
def drip_unenroll(eid):
    DRIP.unenroll_contact(eid)
    flash("Removed from sequence.", "success")
    return redirect(request.referrer or url_for("contacts_list"))


# ── AI assistant ──────────────────────────────────────────────────────────────

@app.route("/contacts/<int:cid>/ai", methods=["GET","POST"])
@login_required
def contact_ai(cid):
    contact = C.get_contact(cid)
    if not contact:
        flash("Contact not found.", "error")
        return redirect(url_for("contacts_list"))
    result = None
    action = request.form.get("action","") if request.method == "POST" else ""
    instruction = request.form.get("instruction","")
    try:
        if action == "draft_email":
            result = AI.draft_email(cid, instruction)
        elif action == "summarize":
            result = AI.summarize_contact(cid)
        elif action == "ask":
            result = AI.ask_anything(cid, instruction)
    except Exception as e:
        flash(f"AI error: {e}", "error")
    return render_template("ai_assistant.html", contact=contact, result=result,
                           action=action, instruction=instruction)


# ── lead capture (public, no login required) ──────────────────────────────────

ALLOWED_ORIGINS = [
    "http://localhost:5002",
    "http://127.0.0.1:5002",
]

def _cors_origin():
    origin = request.headers.get("Origin", "*")
    return origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]

@app.route("/api/leads", methods=["POST", "OPTIONS"])
def api_leads():
    origin = _cors_origin()

    if request.method == "OPTIONS":
        resp = Response("", 204)
        resp.headers["Access-Control-Allow-Origin"]  = origin
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    data       = request.get_json(force=True, silent=True) or {}
    name_parts = data.get("name","").split()
    first      = (data.get("first_name") or (name_parts[0] if name_parts else "")).strip()
    last       = (data.get("last_name")  or (" ".join(name_parts[1:]) or "—")).strip()
    email   = data.get("email","").strip() or None
    phone   = data.get("phone","").strip() or None
    company = data.get("company","").strip() or None
    service = data.get("service","").strip() or None
    message = data.get("message","").strip() or None

    if not first:
        r = jsonify({"error": "first_name required"})
        r.headers["Access-Control-Allow-Origin"] = origin
        return r, 400

    try:
        cid = C.add_contact(first, last, email, phone, company)

        note_parts = []
        if service:
            note_parts.append(f"Service requested: {service}")
        if message:
            note_parts.append(f"Message: {message}")
        if note_parts:
            N.add_note("\n".join(note_parts), contact_id=cid)

        summary = f"Lead from website — {service or 'General inquiry'}"
        A.log_activity("note", summary, contact_id=cid)
        WH.fire("contact_created", {"contact_id": cid, "source": "website"})
        resp = jsonify({"ok": True, "contact_id": cid})
    except Exception as e:
        resp = jsonify({"error": str(e)})
        resp.headers["Access-Control-Allow-Origin"] = origin
        return resp, 400

    resp.headers["Access-Control-Allow-Origin"] = origin
    return resp


# ── email tracking (public, no login required) ────────────────────────────────

@app.route("/track/open/<token>")
def track_open(token):
    TRK.record_open(token)
    return Response(TRK.PIXEL_GIF, mimetype="image/gif",
                    headers={"Cache-Control": "no-store, no-cache"})


@app.route("/track/click/<token>")
def track_click(token):
    url = request.args.get("url", "/")
    TRK.record_click(token, url)
    return redirect(url)


# ── Jobs ─────────────────────────────────────────────────────────────────────

@app.route("/jobs")
@login_required
def jobs_list():
    status   = request.args.get("status","")
    job_type = request.args.get("type","")
    all_jobs = JB.get_all_jobs(status or None, job_type or None)
    summary  = JB.get_pipeline_summary()
    return render_template("jobs.html", jobs=all_jobs, summary=summary,
                           filter_status=status, filter_type=job_type,
                           job_types=JB.JOB_TYPES, statuses=JB.STATUS_LABELS)


@app.route("/jobs/new", methods=["GET","POST"])
@login_required
def job_new():
    if request.method == "POST":
        f = request.form
        jid = JB.create_job(
            contact_id=int(f["contact_id"]),
            title=f["title"],
            job_type=f.get("job_type","other"),
            address=f.get("address",""),
            city=f.get("city",""),
            state=f.get("state",""),
            zip_=f.get("zip",""),
            start_date=f.get("start_date") or None,
            end_date=f.get("end_date") or None,
            description=f.get("description",""),
            status=f.get("status","estimate")
        )
        flash("Job created.", "success")
        return redirect(url_for("job_detail", jid=jid))
    return render_template("job_form.html", job=None, contacts=_all_contacts(),
                           job_types=JB.JOB_TYPES, statuses=JB.STATUS_LABELS,
                           preselect=request.args.get("contact_id",""))


@app.route("/jobs/<int:jid>")
@login_required
def job_detail(jid):
    job = JB.get_job(jid)
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("jobs_list"))
    photos    = JB.get_photos(jid)
    estimates = EST.get_all_estimates_for_job(jid)
    invoices  = INV.get_all_invoices_for_job(jid)
    crew      = CR.get_job_crew(jid)
    all_crew  = CR.get_members()
    costs     = JC.get_costs(jid)
    return render_template("job_detail.html",
        job=job, photos=photos, estimates=estimates, invoices=invoices,
        crew=crew, all_crew=all_crew, costs=costs,
        job_types=JB.JOB_TYPES, statuses=JB.STATUS_LABELS,
        activity=A.get_activity_feed(contact_id=job["contact_id"]))


@app.route("/jobs/<int:jid>/edit", methods=["GET","POST"])
@login_required
def job_edit(jid):
    job = JB.get_job(jid)
    if request.method == "POST":
        f = request.form
        JB.update_job(jid, title=f["title"], job_type=f["job_type"],
                      address=f.get("address"), city=f.get("city"),
                      state=f.get("state"), zip=f.get("zip"),
                      start_date=f.get("start_date"), end_date=f.get("end_date"),
                      description=f.get("description"),
                      internal_notes=f.get("internal_notes"), status=f["status"])
        flash("Job updated.", "success")
        return redirect(url_for("job_detail", jid=jid))
    return render_template("job_form.html", job=job, contacts=_all_contacts(),
                           job_types=JB.JOB_TYPES, statuses=JB.STATUS_LABELS, preselect="")


@app.route("/jobs/<int:jid>/status", methods=["POST"])
@login_required
def job_move_status(jid):
    JB.move_status(jid, request.form["status"])
    flash("Status updated.", "success")
    return redirect(request.referrer or url_for("job_detail", jid=jid))


@app.route("/jobs/<int:jid>/delete", methods=["POST"])
@login_required
def job_delete(jid):
    JB.delete_job(jid)
    flash("Job deleted.", "success")
    return redirect(url_for("jobs_list"))


@app.route("/jobs/<int:jid>/photos", methods=["POST"])
@login_required
def job_upload_photo(jid):
    f = request.files.get("photo")
    if f and f.filename:
        JB.save_photo(jid, f,
                      photo_type=request.form.get("photo_type","progress"),
                      caption=request.form.get("caption",""))
        flash("Photo uploaded.", "success")
    return redirect(url_for("job_detail", jid=jid))


@app.route("/jobs/photos/<int:pid>/delete", methods=["POST"])
@login_required
def job_delete_photo(pid):
    with get_connection() as conn:
        row = conn.execute("SELECT job_id FROM job_photos WHERE id=?", (pid,)).fetchone()
    JB.delete_photo(pid)
    return redirect(url_for("job_detail", jid=row["job_id"]) if row else url_for("jobs_list"))


@app.route("/jobs/photos/<int:pid>/view")
@login_required
def job_view_photo(pid):
    with get_connection() as conn:
        row = conn.execute("SELECT stored_name, filename FROM job_photos WHERE id=?", (pid,)).fetchone()
    if not row:
        flash("Photo not found.", "error")
        return redirect(url_for("jobs_list"))
    photo_path = JB.PHOTO_DIR / row["stored_name"]
    if not photo_path.exists():
        flash("Photo missing from disk.", "error")
        return redirect(url_for("jobs_list"))
    return send_file(str(photo_path), mimetype="image/jpeg", download_name=row["filename"])


@app.route("/jobs/<int:jid>/schedule", methods=["POST"])
@login_required
def job_schedule(jid):
    JB.schedule_job(jid, request.form["date"], request.form.get("crew_notes",""))
    flash("Job scheduled.", "success")
    return redirect(url_for("job_detail", jid=jid))


# ── Estimates ─────────────────────────────────────────────────────────────────

@app.route("/estimates")
@login_required
def estimates_list():
    status = request.args.get("status","")
    return render_template("estimates.html",
        estimates=EST.get_all_estimates(status or None),
        filter_status=status)


@app.route("/estimates/new", methods=["GET","POST"])
@login_required
def estimate_new():
    if request.method == "POST":
        f  = request.form
        co = INV.get_company_settings()
        job_id = int(f["job_id"]) if f.get("job_id","").strip() else None
        if job_id is not None:
            with get_connection() as conn:
                if not conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone():
                    flash(f"Job #{job_id} not found. Leave the Job field blank or pick a valid job.", "error")
                    return render_template("estimate_form.html", estimate=None,
                                           contacts=_all_contacts(), co=co,
                                           preselect_contact=f.get("contact_id",""),
                                           preselect_job=f.get("job_id",""))
        eid, num = EST.create_estimate(
            contact_id=int(f["contact_id"]),
            job_id=job_id,
            valid_until=f.get("valid_until") or None,
            tax_rate=float(f.get("tax_rate") or co.get("tax_rate") or 0),
            discount=float(f.get("discount") or 0),
            notes=f.get("notes",""),
            terms=f.get("terms","") or co.get("estimate_terms","")
        )
        flash(f"Estimate {num} created.", "success")
        return redirect(url_for("estimate_detail", eid=eid))
    co = INV.get_company_settings()
    return render_template("estimate_form.html", estimate=None,
                           contacts=_all_contacts(), co=co,
                           preselect_contact=request.args.get("contact_id",""),
                           preselect_job=request.args.get("job_id",""))


@app.route("/estimates/<int:eid>")
@login_required
def estimate_detail(eid):
    est   = EST.get_estimate(eid)
    if not est:
        flash("Estimate not found.", "error")
        return redirect(url_for("estimates_list"))
    items = EST.get_items(eid)
    t     = EST.totals(eid)
    co    = INV.get_company_settings()
    catalog = SC.get_by_category()
    return render_template("estimate_detail.html",
        est=est, items=items, totals=t, co=co, catalog=catalog)


@app.route("/estimates/<int:eid>/items", methods=["POST"])
@login_required
def estimate_add_item(eid):
    f = request.form
    EST.add_item(eid,
        description=f["description"],
        quantity=float(f.get("quantity",1)),
        unit=f.get("unit","each"),
        unit_price=float(f.get("unit_price",0)),
        catalog_id=int(f["catalog_id"]) if f.get("catalog_id") else None,
        sort_order=int(f.get("sort_order",0))
    )
    return redirect(url_for("estimate_detail", eid=eid))


@app.route("/estimates/<int:eid>/items/<int:iid>/delete", methods=["POST"])
@login_required
def estimate_remove_item(eid, iid):
    EST.remove_item(iid)
    return redirect(url_for("estimate_detail", eid=eid))


@app.route("/estimates/<int:eid>/status", methods=["POST"])
@login_required
def estimate_status(eid):
    EST.update_status(eid, request.form["status"])
    flash("Estimate updated.", "success")
    return redirect(url_for("estimate_detail", eid=eid))


@app.route("/estimates/<int:eid>/convert", methods=["POST"])
@login_required
def estimate_convert(eid):
    due = request.form.get("due_date") or None
    iid, num = INV.invoice_from_estimate(eid, due_date=due)
    flash(f"Invoice {num} created from estimate.", "success")
    return redirect(url_for("invoice_detail", iid=iid))


@app.route("/estimates/<int:eid>/delete", methods=["POST"])
@login_required
def estimate_delete(eid):
    EST.delete_estimate(eid)
    flash("Estimate deleted.", "success")
    return redirect(url_for("estimates_list"))


@app.route("/estimates/<int:eid>/print")
@login_required
def estimate_print(eid):
    est   = EST.get_estimate(eid)
    items = EST.get_items(eid)
    t     = EST.totals(eid)
    co    = INV.get_company_settings()
    return render_template("estimate_print.html", est=est, items=items, totals=t, co=co)


# ── Invoices ──────────────────────────────────────────────────────────────────

@app.route("/invoices")
@login_required
def invoices_list():
    status = request.args.get("status","")
    all_inv = INV.get_all_invoices(status or None)
    # compute totals per invoice for display
    inv_totals = {i["id"]: INV.totals(i["id"]) for i in all_inv}
    return render_template("invoices.html", invoices=all_inv,
                           inv_totals=inv_totals, filter_status=status)


@app.route("/invoices/new", methods=["GET","POST"])
@login_required
def invoice_new():
    if request.method == "POST":
        f  = request.form
        co = INV.get_company_settings()
        job_id = int(f["job_id"]) if f.get("job_id","").strip() else None
        if job_id is not None:
            with get_connection() as conn:
                if not conn.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone():
                    flash(f"Job #{job_id} not found. Leave the Job field blank or pick a valid job.", "error")
                    return render_template("invoice_form.html", invoice=None,
                                           contacts=_all_contacts(), co=co,
                                           preselect_contact=f.get("contact_id",""),
                                           preselect_job=f.get("job_id",""))
        iid, num = INV.create_invoice(
            contact_id=int(f["contact_id"]),
            job_id=job_id,
            due_date=f.get("due_date") or None,
            tax_rate=float(f.get("tax_rate") or co.get("tax_rate") or 0),
            discount=float(f.get("discount") or 0),
            deposit_required=float(f.get("deposit_required") or 0),
            notes=f.get("notes",""),
            terms=f.get("terms","") or co.get("invoice_terms","")
        )
        flash(f"Invoice {num} created.", "success")
        return redirect(url_for("invoice_detail", iid=iid))
    co = INV.get_company_settings()
    return render_template("invoice_form.html", invoice=None,
                           contacts=_all_contacts(), co=co,
                           preselect_contact=request.args.get("contact_id",""),
                           preselect_job=request.args.get("job_id",""))


@app.route("/invoices/<int:iid>")
@login_required
def invoice_detail(iid):
    inv = INV.get_invoice(iid)
    if not inv:
        flash("Invoice not found.", "error")
        return redirect(url_for("invoices_list"))
    items    = INV.get_items(iid)
    payments = INV.get_payments(iid)
    t        = INV.totals(iid)
    co       = INV.get_company_settings()
    catalog  = SC.get_by_category()
    return render_template("invoice_detail.html",
        inv=inv, items=items, payments=payments, totals=t, co=co, catalog=catalog)


@app.route("/invoices/<int:iid>/items", methods=["POST"])
@login_required
def invoice_add_item(iid):
    f = request.form
    INV.add_item(iid,
        description=f["description"],
        quantity=float(f.get("quantity",1)),
        unit=f.get("unit","each"),
        unit_price=float(f.get("unit_price",0)),
        sort_order=int(f.get("sort_order",0))
    )
    return redirect(url_for("invoice_detail", iid=iid))


@app.route("/invoices/<int:iid>/items/<int:item_id>/delete", methods=["POST"])
@login_required
def invoice_remove_item(iid, item_id):
    INV.remove_item(item_id)
    return redirect(url_for("invoice_detail", iid=iid))


@app.route("/invoices/<int:iid>/payment", methods=["POST"])
@login_required
def invoice_add_payment(iid):
    f = request.form
    INV.add_payment(iid,
        amount=float(f["amount"]),
        method=f.get("method","check"),
        reference=f.get("reference",""),
        notes=f.get("notes",""),
        paid_at=f.get("paid_at") or None
    )
    flash("Payment recorded.", "success")
    return redirect(url_for("invoice_detail", iid=iid))


@app.route("/invoices/<int:iid>/status", methods=["POST"])
@login_required
def invoice_status(iid):
    INV.update_status(iid, request.form["status"])
    flash("Invoice updated.", "success")
    return redirect(url_for("invoice_detail", iid=iid))


@app.route("/invoices/<int:iid>/delete", methods=["POST"])
@login_required
def invoice_delete(iid):
    INV.delete_invoice(iid)
    flash("Invoice deleted.", "success")
    return redirect(url_for("invoices_list"))


@app.route("/invoices/<int:iid>/print")
@login_required
def invoice_print(iid):
    inv      = INV.get_invoice(iid)
    items    = INV.get_items(iid)
    payments = INV.get_payments(iid)
    t        = INV.totals(iid)
    co       = INV.get_company_settings()
    return render_template("invoice_print.html",
        inv=inv, items=items, payments=payments, totals=t, co=co)


# ── Schedule / Calendar ───────────────────────────────────────────────────────

@app.route("/schedule")
@login_required
def schedule_view():
    from datetime import datetime
    now   = datetime.now()
    month = int(request.args.get("month", now.month))
    year  = int(request.args.get("year",  now.year))
    scheduled = JB.get_schedule(month=month, year=year)
    # group by date
    by_date = {}
    for s in scheduled:
        by_date.setdefault(s["scheduled_date"], []).append(s)
    import calendar
    cal = calendar.monthcalendar(year, month)
    return render_template("schedule.html",
        cal=cal, by_date=by_date, month=month, year=year,
        month_name=calendar.month_name[month])


# ── Service Catalog ───────────────────────────────────────────────────────────

@app.route("/settings/catalog")
@login_required
def catalog_list():
    return render_template("catalog.html", by_category=SC.get_by_category(active_only=False))


@app.route("/settings/catalog/new", methods=["POST"])
@login_required
def catalog_new():
    f = request.form
    SC.create_item(f["category"], f["name"], f.get("description",""),
                   f["unit"], float(f.get("unit_price",0)))
    flash("Service added.", "success")
    return redirect(url_for("catalog_list"))


@app.route("/settings/catalog/<int:iid>/delete", methods=["POST"])
@login_required
def catalog_delete(iid):
    SC.delete_item(iid)
    flash("Service deleted.", "success")
    return redirect(url_for("catalog_list"))


# ── Company Settings ──────────────────────────────────────────────────────────

@app.route("/settings/company", methods=["GET","POST"])
@login_required
def settings_company():
    if request.method == "POST":
        f = request.form
        INV.save_company_settings(
            name=f.get("name",""), address=f.get("address",""),
            city=f.get("city",""), state=f.get("state",""),
            zip=f.get("zip",""), phone=f.get("phone",""),
            email=f.get("email",""), website=f.get("website",""),
            tax_rate=float(f.get("tax_rate") or 0),
            invoice_terms=f.get("invoice_terms",""),
            estimate_terms=f.get("estimate_terms","")
        )
        flash("Company settings saved.", "success")
        return redirect(url_for("settings_company"))
    return render_template("company_settings.html", co=INV.get_company_settings())


# ── Client Approval Portal (public — no login) ────────────────────────────────

@app.route("/approve/<token>")
def client_approve_view(token):
    est = AUTO.get_estimate_by_token(token)
    if not est:
        return render_template("approval_invalid.html"), 404
    import estimates as EST
    items  = EST.get_items(est['id'])
    totals = EST.totals(est['id'])
    co     = INV.get_company_settings()
    return render_template("approval_portal.html",
        est=est, items=items, totals=totals, co=co, token=token)


@app.route("/approve/<token>/sign", methods=["POST"])
def client_approve_sign(token):
    action = request.form.get("action")
    name   = request.form.get("signer_name","").strip()
    reason = request.form.get("reason","").strip()
    if action == "approve" and name:
        AUTO.client_approve(token, name)
        return render_template("approval_done.html", approved=True, name=name)
    elif action == "reject":
        AUTO.client_reject(token, reason)
        return render_template("approval_done.html", approved=False, name=name)
    return redirect(url_for("client_approve_view", token=token))


@app.route("/estimates/<int:eid>/send-approval", methods=["POST"])
@login_required
def estimate_send_approval(eid):
    est = EST.get_estimate(eid)
    if not est:
        flash("Estimate not found.", "error")
        return redirect(url_for("estimates_list"))
    token    = AUTO.create_approval_token(eid)
    base_url = request.host_url.rstrip("/")
    link     = f"{base_url}/approve/{token}"
    co       = INV.get_company_settings()
    subject  = f"Please review your estimate — {est['estimate_number']}"
    body = (
        f"Hi {est['contact_name'].split()[0]},\n\n"
        f"Your estimate {est['estimate_number']} from {co.get('name','us')} is ready for review.\n\n"
        f"Click the link below to view, approve, or decline:\n{link}\n\n"
        f"Questions? Reply to this email or call {co.get('phone','')}.\n\n"
        f"Thank you,\n{co.get('name','Your Team')}"
    )
    err = EM.send_email(est['contact_email'], subject, body)
    if err:
        flash(f"Could not send: {err}", "error")
    else:
        EST.update_status(eid, 'sent')
        flash(f"Approval link sent to {est['contact_email']}.", "success")
    return redirect(url_for("estimate_detail", eid=eid))


# ── Crew Management ───────────────────────────────────────────────────────────

@app.route("/crew")
@login_required
def crew_list():
    return render_template("crew.html", members=CR.get_members(active_only=False))


@app.route("/crew/new", methods=["POST"])
@login_required
def crew_new():
    f = request.form
    CR.create_member(f["name"], f.get("role",""), f.get("phone",""),
                     f.get("email",""), float(f.get("pay_rate") or 0))
    flash("Crew member added.", "success")
    return redirect(url_for("crew_list"))


@app.route("/crew/<int:mid>/edit", methods=["POST"])
@login_required
def crew_edit(mid):
    f = request.form
    CR.update_member(mid, f["name"], f.get("role",""), f.get("phone",""),
                     f.get("email",""), float(f.get("pay_rate") or 0),
                     int(f.get("active", 1)))
    flash("Updated.", "success")
    return redirect(url_for("crew_list"))


@app.route("/crew/<int:mid>/delete", methods=["POST"])
@login_required
def crew_delete(mid):
    CR.delete_member(mid)
    flash("Removed.", "success")
    return redirect(url_for("crew_list"))


@app.route("/jobs/<int:jid>/crew/assign", methods=["POST"])
@login_required
def job_assign_crew(jid):
    f = request.form
    CR.assign_to_job(jid, int(f["member_id"]),
                     f.get("scheduled_date") or None, f.get("notes",""))
    flash("Crew member assigned.", "success")
    return redirect(url_for("job_detail", jid=jid))


@app.route("/jobs/<int:jid>/crew/<int:aid>/remove", methods=["POST"])
@login_required
def job_remove_crew(jid, aid):
    CR.unassign(aid)
    return redirect(url_for("job_detail", jid=jid))


@app.route("/jobs/<int:jid>/hours", methods=["POST"])
@login_required
def job_log_hours(jid):
    f = request.form
    CR.log_hours(jid, int(f["member_id"]), float(f["hours"]),
                 f.get("work_date") or None, f.get("notes",""))
    flash("Hours logged.", "success")
    return redirect(url_for("job_detail", jid=jid))


# ── Job Costs ─────────────────────────────────────────────────────────────────

@app.route("/jobs/<int:jid>/costs", methods=["POST"])
@login_required
def job_add_cost(jid):
    f = request.form
    JC.add_cost(jid, f["description"], float(f["amount"]),
                f.get("cost_type","material"), f.get("vendor",""), f.get("notes",""))
    flash("Cost logged.", "success")
    return redirect(url_for("job_detail", jid=jid))


@app.route("/jobs/<int:jid>/costs/<int:cid>/delete", methods=["POST"])
@login_required
def job_delete_cost(jid, cid):
    JC.delete_cost(cid)
    return redirect(url_for("job_detail", jid=jid))


# ── Profitability Report ──────────────────────────────────────────────────────

@app.route("/reports/profitability")
@login_required
def report_profitability():
    jobs = JC.all_jobs_profitability()
    total_revenue   = sum(j["revenue"]       for j in jobs)
    total_costs     = sum(j["total_cost"]    for j in jobs)
    total_materials = sum(j["material_cost"] for j in jobs)
    total_labor     = sum(j["labor_cost"]    for j in jobs)
    total_other     = sum(j["other_cost"]    for j in jobs)
    gross_profit    = total_revenue - total_costs
    avg_margin      = (gross_profit / total_revenue * 100) if total_revenue else 0
    summary = dict(total_revenue=total_revenue, total_costs=total_costs,
                   total_materials=total_materials, total_labor=total_labor,
                   total_other=total_other, gross_profit=gross_profit, avg_margin=avg_margin)
    return render_template("report_profitability.html", jobs=jobs, summary=summary)


# ── Recurring Contracts ───────────────────────────────────────────────────────

@app.route("/recurring")
@login_required
def recurring_list():
    return render_template("recurring.html", contracts=REC.get_contracts(), contacts=_all_contacts())


@app.route("/recurring/new", methods=["POST"])
@login_required
def recurring_new():
    f = request.form
    REC.create_contract(
        contact_id=int(f["contact_id"]),
        job_id=int(f["job_id"]) if f.get("job_id") else None,
        name=f["name"],
        amount=float(f.get("amount") or 0),
        frequency=f.get("frequency","monthly"),
        next_date=f.get("next_date") or None,
        notes=f.get("notes","")
    )
    flash("Recurring contract created.", "success")
    return redirect(url_for("recurring_list"))


@app.route("/recurring/<int:rid>/pause", methods=["POST"])
@login_required
def recurring_pause(rid):
    REC.pause_contract(rid)
    flash("Contract paused.", "success")
    return redirect(url_for("recurring_list"))


@app.route("/recurring/<int:rid>/resume", methods=["POST"])
@login_required
def recurring_resume(rid):
    REC.resume_contract(rid)
    flash("Contract resumed.", "success")
    return redirect(url_for("recurring_list"))


@app.route("/recurring/<int:rid>/delete", methods=["POST"])
@login_required
def recurring_delete(rid):
    REC.delete_contract(rid)
    flash("Contract deleted.", "success")
    return redirect(url_for("recurring_list"))


# ── Review Requests ───────────────────────────────────────────────────────────

@app.route("/jobs/<int:jid>/send-review", methods=["POST"])
@login_required
def job_send_review(jid):
    job        = JB.get_job(jid)
    review_url = request.form.get("review_url","")
    ok = AUTO.send_review_request(job["contact_id"], job["title"], review_url)
    flash("Review request sent!" if ok else "Could not send — check SMTP settings.", "success" if ok else "error")
    return redirect(url_for("job_detail", jid=jid))


# ── helpers ───────────────────────────────────────────────────────────────────

def _all_contacts():
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM contacts ORDER BY last_name, first_name"
        ).fetchall()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
