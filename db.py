import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "crm.db"


@contextmanager
def get_connection():
    """
    Yields an open, auto-committing connection and CLOSES it on exit.
    All callers already use `with get_connection() as conn:` — this makes
    that pattern actually close the connection so locks are released immediately.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS contacts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name  TEXT NOT NULL,
                last_name   TEXT NOT NULL,
                email       TEXT UNIQUE,
                phone       TEXT,
                company     TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS deals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                title        TEXT NOT NULL,
                value        REAL DEFAULT 0,
                stage        TEXT NOT NULL DEFAULT 'lead'
                                 CHECK(stage IN ('lead','qualified','proposal','won','lost')),
                closed_at    TEXT,
                created_at   TEXT DEFAULT (datetime('now')),
                updated_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id  INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
                deal_id     INTEGER REFERENCES deals(id) ON DELETE CASCADE,
                body        TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                CHECK (contact_id IS NOT NULL OR deal_id IS NOT NULL)
            );

            CREATE TABLE IF NOT EXISTS tags (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS contact_tags (
                contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (contact_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS activities (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                type        TEXT NOT NULL
                                CHECK(type IN ('call','email','meeting','task','note','stage_change')),
                summary     TEXT NOT NULL,
                contact_id  INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
                deal_id     INTEGER REFERENCES deals(id) ON DELETE CASCADE,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id  INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
                deal_id     INTEGER REFERENCES deals(id) ON DELETE CASCADE,
                due_at      TEXT NOT NULL,
                body        TEXT NOT NULL,
                done        INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_deals_contact      ON deals(contact_id);
            CREATE INDEX IF NOT EXISTS idx_deals_stage        ON deals(stage);
            CREATE INDEX IF NOT EXISTS idx_notes_contact      ON notes(contact_id);
            CREATE INDEX IF NOT EXISTS idx_notes_deal         ON notes(deal_id);
            CREATE INDEX IF NOT EXISTS idx_contact_tags       ON contact_tags(contact_id);
            CREATE INDEX IF NOT EXISTS idx_activities_contact ON activities(contact_id);
            CREATE INDEX IF NOT EXISTS idx_activities_deal    ON activities(deal_id);
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                created_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS attachments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id  INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
                deal_id     INTEGER REFERENCES deals(id)    ON DELETE CASCADE,
                filename    TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                size_bytes  INTEGER,
                uploaded_by INTEGER REFERENCES users(id),
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS custom_field_defs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entity      TEXT NOT NULL CHECK(entity IN ('contact','deal')),
                label       TEXT NOT NULL,
                field_type  TEXT NOT NULL DEFAULT 'text'
                                CHECK(field_type IN ('text','number','date','boolean','select')),
                options     TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(entity, label)
            );

            CREATE TABLE IF NOT EXISTS custom_field_values (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                field_id   INTEGER NOT NULL REFERENCES custom_field_defs(id) ON DELETE CASCADE,
                entity_id  INTEGER NOT NULL,
                value      TEXT,
                UNIQUE(field_id, entity_id)
            );

            CREATE TABLE IF NOT EXISTS email_templates (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                subject    TEXT NOT NULL,
                body       TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS webhooks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                url        TEXT NOT NULL,
                event      TEXT NOT NULL CHECK(event IN ('deal_won','stage_change','contact_created')),
                active     INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS smtp_config (
                id        INTEGER PRIMARY KEY CHECK(id=1),
                host      TEXT,
                port      INTEGER DEFAULT 587,
                username  TEXT,
                password  TEXT,
                use_tls   INTEGER DEFAULT 1,
                from_addr TEXT,
                imap_host TEXT
            );

            CREATE TABLE IF NOT EXISTS drip_sequences (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS drip_steps (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id  INTEGER NOT NULL REFERENCES drip_sequences(id) ON DELETE CASCADE,
                step_number  INTEGER NOT NULL,
                delay_days   INTEGER NOT NULL DEFAULT 1,
                subject      TEXT NOT NULL,
                body         TEXT NOT NULL,
                UNIQUE(sequence_id, step_number)
            );

            CREATE TABLE IF NOT EXISTS drip_enrollments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                sequence_id  INTEGER NOT NULL REFERENCES drip_sequences(id) ON DELETE CASCADE,
                status       TEXT NOT NULL DEFAULT 'active'
                                 CHECK(status IN ('active','completed','cancelled')),
                current_step INTEGER NOT NULL DEFAULT 0,
                enrolled_at  TEXT DEFAULT (datetime('now')),
                last_sent_at TEXT
            );

            CREATE TABLE IF NOT EXISTS email_tracking (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                token         TEXT NOT NULL UNIQUE,
                contact_id    INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                email_subject TEXT,
                opened_at     TEXT,
                open_count    INTEGER NOT NULL DEFAULT 0,
                click_count   INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            );

            -- ── Software/Web: Jobs ─────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                title        TEXT NOT NULL,
                job_type     TEXT NOT NULL DEFAULT 'other'
                                 CHECK(job_type IN ('website_build','web_app','crm_setup',
                                                    'ecommerce','integration','migration',
                                                    'maintenance','support','consulting','other')),
                status       TEXT NOT NULL DEFAULT 'estimate'
                                 CHECK(status IN ('estimate','scheduled','in_progress',
                                                  'completed','invoiced','paid','cancelled')),
                address      TEXT,
                city         TEXT,
                state        TEXT,
                zip          TEXT,
                start_date   TEXT,
                end_date     TEXT,
                description  TEXT,
                internal_notes TEXT,
                created_at   TEXT DEFAULT (datetime('now')),
                updated_at   TEXT DEFAULT (datetime('now'))
            );

            -- ── Software/Web: Service Catalog ────────────────────────────
            CREATE TABLE IF NOT EXISTS service_catalog (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL DEFAULT 'general',
                name        TEXT NOT NULL,
                description TEXT,
                unit        TEXT NOT NULL DEFAULT 'each',
                unit_price  REAL NOT NULL DEFAULT 0,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Software/Web: Estimates ──────────────────────────────────
            CREATE TABLE IF NOT EXISTS estimates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
                contact_id      INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                estimate_number TEXT NOT NULL UNIQUE,
                status          TEXT NOT NULL DEFAULT 'draft'
                                    CHECK(status IN ('draft','sent','approved','rejected','expired')),
                valid_until     TEXT,
                tax_rate        REAL NOT NULL DEFAULT 0,
                discount        REAL NOT NULL DEFAULT 0,
                notes           TEXT,
                terms           TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS estimate_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                estimate_id  INTEGER NOT NULL REFERENCES estimates(id) ON DELETE CASCADE,
                catalog_id   INTEGER REFERENCES service_catalog(id) ON DELETE SET NULL,
                description  TEXT NOT NULL,
                quantity     REAL NOT NULL DEFAULT 1,
                unit         TEXT NOT NULL DEFAULT 'each',
                unit_price   REAL NOT NULL DEFAULT 0,
                sort_order   INTEGER NOT NULL DEFAULT 0
            );

            -- ── Software/Web: Invoices ───────────────────────────────────
            CREATE TABLE IF NOT EXISTS invoices (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id         INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
                estimate_id    INTEGER REFERENCES estimates(id) ON DELETE SET NULL,
                contact_id     INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                invoice_number TEXT NOT NULL UNIQUE,
                status         TEXT NOT NULL DEFAULT 'draft'
                                   CHECK(status IN ('draft','sent','partial','paid','overdue','void')),
                due_date       TEXT,
                tax_rate       REAL NOT NULL DEFAULT 0,
                discount       REAL NOT NULL DEFAULT 0,
                deposit_required REAL NOT NULL DEFAULT 0,
                notes          TEXT,
                terms          TEXT,
                created_at     TEXT DEFAULT (datetime('now')),
                updated_at     TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS invoice_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id   INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                description  TEXT NOT NULL,
                quantity     REAL NOT NULL DEFAULT 1,
                unit         TEXT NOT NULL DEFAULT 'each',
                unit_price   REAL NOT NULL DEFAULT 0,
                sort_order   INTEGER NOT NULL DEFAULT 0
            );

            -- ── Software/Web: Payments ───────────────────────────────────
            CREATE TABLE IF NOT EXISTS payments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                amount      REAL NOT NULL,
                method      TEXT NOT NULL DEFAULT 'check'
                                CHECK(method IN ('cash','check','card','zelle','venmo','bank_transfer','other')),
                reference   TEXT,
                paid_at     TEXT NOT NULL DEFAULT (datetime('now')),
                notes       TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Software/Web: Job Photos ─────────────────────────────────
            CREATE TABLE IF NOT EXISTS job_photos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                filename    TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                photo_type  TEXT NOT NULL DEFAULT 'progress'
                                CHECK(photo_type IN ('before','progress','after')),
                caption     TEXT,
                size_bytes  INTEGER,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Software/Web: Job Schedule ──────────────────────────────
            CREATE TABLE IF NOT EXISTS job_schedule (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                scheduled_date TEXT NOT NULL,
                crew_notes   TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            -- ── Software/Web: Company Settings ──────────────────────────
            CREATE TABLE IF NOT EXISTS company_settings (
                id           INTEGER PRIMARY KEY CHECK(id=1),
                name         TEXT,
                address      TEXT,
                city         TEXT,
                state        TEXT,
                zip          TEXT,
                phone        TEXT,
                email        TEXT,
                website      TEXT,
                logo_path    TEXT,
                tax_rate     REAL DEFAULT 0,
                invoice_terms TEXT DEFAULT 'Payment due within 30 days.',
                estimate_terms TEXT DEFAULT 'This estimate is valid for 30 days.',
                next_invoice_number INTEGER DEFAULT 1,
                next_estimate_number INTEGER DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_attachments_contact    ON attachments(contact_id);
            CREATE INDEX IF NOT EXISTS idx_attachments_deal       ON attachments(deal_id);
            CREATE INDEX IF NOT EXISTS idx_cfv_field              ON custom_field_values(field_id, entity_id);
            CREATE INDEX IF NOT EXISTS idx_reminders_due          ON reminders(due_at) WHERE done = 0;
            CREATE INDEX IF NOT EXISTS idx_drip_steps_seq         ON drip_steps(sequence_id);
            CREATE INDEX IF NOT EXISTS idx_drip_enroll_contact    ON drip_enrollments(contact_id);
            CREATE INDEX IF NOT EXISTS idx_email_tracking_contact ON email_tracking(contact_id);
            CREATE INDEX IF NOT EXISTS idx_email_tracking_token   ON email_tracking(token);
            -- ── Crew ──────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS crew_members (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                role       TEXT,
                phone      TEXT,
                email      TEXT,
                pay_rate   REAL NOT NULL DEFAULT 0,
                active     INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS crew_assignments (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id         INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                member_id      INTEGER NOT NULL REFERENCES crew_members(id) ON DELETE CASCADE,
                scheduled_date TEXT,
                notes          TEXT,
                UNIQUE(job_id, member_id)
            );

            CREATE TABLE IF NOT EXISTS crew_hours (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                member_id  INTEGER NOT NULL REFERENCES crew_members(id) ON DELETE CASCADE,
                hours      REAL NOT NULL,
                work_date  TEXT NOT NULL DEFAULT (date('now')),
                notes      TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            -- ── Job Costs ──────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS job_costs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                description TEXT NOT NULL,
                amount      REAL NOT NULL,
                cost_type   TEXT NOT NULL DEFAULT 'material'
                                CHECK(cost_type IN ('material','subcontractor','equipment','other')),
                vendor      TEXT,
                notes       TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Recurring Contracts ────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS recurring_contracts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                job_id      INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
                name        TEXT NOT NULL,
                amount      REAL NOT NULL DEFAULT 0,
                frequency   TEXT NOT NULL DEFAULT 'monthly'
                                CHECK(frequency IN ('weekly','biweekly','monthly','quarterly','yearly')),
                next_date   TEXT NOT NULL,
                last_billed TEXT,
                active      INTEGER NOT NULL DEFAULT 1,
                notes       TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_contact           ON jobs(contact_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status            ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_start_date        ON jobs(start_date);
            CREATE INDEX IF NOT EXISTS idx_estimates_contact      ON estimates(contact_id);
            CREATE INDEX IF NOT EXISTS idx_estimates_job          ON estimates(job_id);
            CREATE INDEX IF NOT EXISTS idx_estimate_items         ON estimate_items(estimate_id);
            CREATE INDEX IF NOT EXISTS idx_invoices_contact       ON invoices(contact_id);
            CREATE INDEX IF NOT EXISTS idx_invoices_job           ON invoices(job_id);
            CREATE INDEX IF NOT EXISTS idx_invoice_items          ON invoice_items(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_payments_invoice       ON payments(invoice_id);
            CREATE INDEX IF NOT EXISTS idx_job_photos             ON job_photos(job_id);
            CREATE INDEX IF NOT EXISTS idx_job_schedule           ON job_schedule(scheduled_date);
        """)


def _add_column_if_missing(conn, table, column, definition):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_db():
    """Safe column additions for schema upgrades."""
    with get_connection() as conn:
        _add_column_if_missing(conn, "estimates", "approval_token", "TEXT")
        _add_column_if_missing(conn, "estimates", "signed_by",      "TEXT")
        _add_column_if_missing(conn, "estimates", "signed_at",      "TEXT")
        _add_column_if_missing(conn, "smtp_config", "imap_host",    "TEXT")


if __name__ == "__main__":
    init_db()
    migrate_db()
    print(f"Database ready at {DB_PATH}")
