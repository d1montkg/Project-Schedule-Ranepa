import crypto
import sqlite3
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
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
                calendar_id TEXT,
                last_sync_at TEXT,
                last_status TEXT,
                last_error TEXT,
                notified INTEGER NOT NULL DEFAULT 0,
                admin_notified INTEGER NOT NULL DEFAULT 0
            )
        """)
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(users)")]
        if "admin_notified" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN admin_notified INTEGER NOT NULL DEFAULT 0"
            )


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


def get_all_users():
    with get_connection() as conn:
        return conn.execute(
        """
        SELECT *
        FROM users
        """
        ).fetchall()


def get_password(telegram_id):
    user = get_user(telegram_id)
    if user is None:
        raise UserNotFound(f"Пользователь {telegram_id} не найден")
    
    return crypto.decrypt(user["password"])


def save_sync_result(telegram_id, status, error):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE users
            SET
                last_sync_at = ?,
                last_status = ?,
                last_error = ?,
                notified = CASE WHEN ? = 'ok' THEN 0
                ELSE notified END,
                admin_notified = CASE WHEN ? = 'ok' THEN 0
                ELSE admin_notified END
            WHERE telegram_id = ?
            """,
            (
                dt.datetime.now(tz=MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                status,
                error,
                status,
                status,
                telegram_id,
            ),
        )


def get_users_to_notify():
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT
                telegram_id,
                last_status,
                last_error
            FROM users
            WHERE last_status = 'user_error' AND notified = 0
            """
        ).fetchall()


def mark_notified(telegram_id):
    with get_connection() as conn:
        return conn.execute(
        """
        UPDATE users
        SET notified = 1
        WHERE telegram_id = ?
        """,
        (telegram_id,)
        )


def get_users_for_admin():
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT telegram_id, login, last_status, last_error
            FROM users
            WHERE last_status IN ('error', 'partial', 'user_error')
              AND admin_notified = 0
            """
        ).fetchall()


def mark_admin_notified(telegram_ids):
    if not telegram_ids:
        return
    placeholders = ",".join("?" * len(telegram_ids))
    with get_connection() as conn:
        conn.execute(
            f"UPDATE users SET admin_notified = 1 WHERE telegram_id IN ({placeholders})",
            telegram_ids,
        )


