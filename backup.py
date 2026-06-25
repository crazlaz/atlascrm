import sqlite3, threading, time
from datetime import datetime
from pathlib import Path
from db import DB_PATH

_scheduler_started = False


def start_auto_backup(interval_hours: float = 24):
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        while True:
            time.sleep(interval_hours * 3600)
            try:
                create_backup()
            except Exception:
                pass

    t = threading.Thread(target=_loop, daemon=True)
    t.start()

BACKUP_DIR = Path(__file__).parent / "backups"


def create_backup() -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"crm_{ts}.db"
    # use SQLite online backup API for a consistent snapshot
    src  = sqlite3.connect(DB_PATH)
    dst  = sqlite3.connect(dest)
    src.backup(dst)
    src.close()
    dst.close()
    _prune(keep=10)
    return dest


def list_backups() -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    files = sorted(BACKUP_DIR.glob("crm_*.db"), reverse=True)
    return [{"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1),
             "path": str(f)} for f in files]


def _prune(keep: int = 10):
    files = sorted(BACKUP_DIR.glob("crm_*.db"), reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)
