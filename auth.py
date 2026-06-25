import hashlib, os, sqlite3
from flask_login import LoginManager, UserMixin
from db import get_connection, DB_PATH
#hi claude hehe
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message_category = "error"


class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username


@login_manager.user_loader
def load_user(user_id):
    with get_connection() as conn:
        row = conn.execute("SELECT id, username FROM users WHERE id=?", (user_id,)).fetchone()
    return User(row["id"], row["username"]) if row else None


def _hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode()).hexdigest()


def create_user(username: str, password: str):
    salt = os.urandom(16).hex()
    h    = _hash(password, salt)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, salt) VALUES (?,?,?)",
            (username, h, salt),
        )


def check_login(username: str, password: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, salt FROM users WHERE username=?",
            (username,),
        ).fetchone()
    if not row:
        return None
    if _hash(password, row["salt"]) == row["password_hash"]:
        return User(row["id"], row["username"])
    return None


def user_count() -> int:
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
