import crypto
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).parent / "data" / "bot.db"


class UserNotFound(Exception):
    """Пользователя с таким telegram_id нет в БД."""


def get_connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                login TEXT,
                password TEXT,
                calendar_id TEXT
            )
        """)


def save_user(telegram_id, login, password, calendar_id):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, login, password, calendar_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                login = excluded.login,
                password = excluded.password,
                calendar_id = excluded.calendar_id
            """,
            (telegram_id, login, crypto.encrypt(password), calendar_id),
        )


def get_user(telegram_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()


def get_password(telegram_id):
    user = get_user(telegram_id)
    if user is None:
        raise UserNotFound(f"Пользователь {telegram_id} не найден")
    
    return crypto.decrypt(user["password"])
