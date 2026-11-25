import re

MAX_REMINDERS = 5
MAX_MINUTES = 40320
DEFAULT_MINUTES = [5, 15, 30]

PRESETS = {
    "p1": ("5, 15 и 30 мин", [5, 15, 30]),
    "p2": ("10 мин", [10]),
    "p3": ("30 и 60 мин", [30, 60]),
    "p4": ("1 день", [1440]),
}


class ReminderError(Exception):
    pass


def parse_minutes(text):
    parts = [p for p in re.split(r"[,;\s]+", text.strip()) if p]
    if not parts:
        raise ReminderError("Не вижу чисел. Пример: 10, 30, 60")

    values = set()
    for part in parts:
        if not (part.isascii() and part.isdigit()):
            raise ReminderError(
                f"«{part[:20]}» не похоже на число минут. Пример: 10, 30, 60"
            )
        value = int(part)
        if value < 1 or value > MAX_MINUTES:
            raise ReminderError(
                f"{value}: допустимо от 1 до {MAX_MINUTES} минут (это 4 недели)"
            )
        values.add(value)

    if len(values) > MAX_REMINDERS:
        raise ReminderError(
            f"Можно не больше {MAX_REMINDERS} напоминаний, а у тебя {len(values)}"
        )
    return sorted(values)


def human(minutes):
    if minutes % 1440 == 0:
        return f"{minutes // 1440} дн"
    if minutes % 60 == 0:
        return f"{minutes // 60} ч"
    return f"{minutes} мин"


def describe(minutes):
    items = [human(m) for m in minutes]
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " и " + items[-1]


def settings_key(enabled, minutes):
    if not enabled or not minutes:
        return "off"
    return ",".join(str(m) for m in minutes)


def build_body(enabled, minutes):
    if not enabled or not minutes:
        return {"useDefault": False, "overrides": []}
    return {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": m} for m in minutes],
    }


def user_settings(user):
    enabled = bool(user["reminders_enabled"])
    raw = user["reminder_minutes"] or ""
    minutes = [int(x) for x in raw.split(",") if x.strip().isdigit()]
    return enabled, minutes or list(DEFAULT_MINUTES)
