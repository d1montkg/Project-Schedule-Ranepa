import logging
import socket
import sys
import time
import db
import crypto
import first_try as ft

from pathlib import Path


LOG_PATH = Path(__file__).parent / "data" / "sync_all.log"
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8"
)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
log = logging.getLogger(__name__)


def internet_ok(host="my.ranepa.ru", port=443, timeout=5):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def wait_for_internet(attempts=5, pause=60):
    for attempt in range(1, attempts + 1):
        if internet_ok():
            return True
        log.warning("Нет связи с ЛК, попытка %s из %s", attempt, attempts)
        if attempt != attempts:
            time.sleep(pause)

    return False


def main():
    db.init_db()

    if not wait_for_internet():
        log.error("Интернета нет, синхронизация отменена")
        sys.exit(1)

    for user in db.get_all_users():
        try:
            password = crypto.decrypt(user["password"])
            status, message = ft.run_sync(user["login"], password, user["calendar_id"])

            error = None if status == "ok" else message
            log.info("user %s: %s", user["telegram_id"], message)
            db.save_sync_result(user["telegram_id"], status, error)

        except Exception as e:
            log.exception("Сбой у пользователя %s", user["telegram_id"])
            db.save_sync_result(user["telegram_id"], "error", type(e).__name__ )
    

if __name__ == "__main__":
    main()
