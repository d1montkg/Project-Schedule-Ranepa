import json
import logging
import time
import datetime as dt
import google_auth
import httplib2
import requests
import reminders

from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError


load_dotenv()

log = logging.getLogger(__name__)

headers = {
    "Accept": "application/json, text/plain, */*",
    "User-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
}

moscow_tz = ZoneInfo("Europe/Moscow")
TOTALLY_TRIES = 2
NETWORK_ERRORS = (OSError, httplib2.HttpLib2Error)
CALENDAR_TITLE = "Расписание РАНХиГС"
BATCH_SIZE = 50
PAGE_SIZE = 250


class SyncError(Exception):
    user_text = "Не удалось синхронизировать. Календарь не изменён."

    def __init__(self, detail=""):
        super().__init__(detail)
        self.detail = detail


class ScheduleError(SyncError):
    user_text = (
        "Сайт РАНХиГС сейчас не отдаёт расписание, возможно, просит пройти "
        "проверку. Календарь не изменён, при следующей синхронизации попробую снова."
    )


class NetworkError(SyncError):
    user_text = (
        "Не удалось связаться с сайтом или Google. Обычно это временно, "
        "при следующей синхронизации попробую снова."
    )


class GoogleHttpError(SyncError):
    user_text = (
        "Сбой на стороне бота или Google. Календарь не изменён, "
        "я уже знаю о проблеме."
    )


class GoogleAuthError(SyncError):
    user_text = "Не удалось войти в Google. Подключи заново: /start"


class GoogleAccessRevoked(GoogleAuthError):
    user_text = "Доступ к Google отозван или истёк. Подключи заново: /start"


class CalendarUnavailable(GoogleAuthError):
    user_text = "Календарь недоступен, возможно, он удалён. Подключи заново: /start"


class BadCredentials(SyncError):
    user_text = "Неверный логин или пароль от ЛК. Пришли их заново: /start"


def run_sync(login, password, google_token, calendar_id,
             reminders_enabled, reminder_minutes):
    if not google_token or not calendar_id:
        return (
            "user_error",
            "Нужно заново подключить Google Calendar: пройди /start",
            "нет google_token или calendar_id",
        )

    now = dt.datetime.now(tz=moscow_tz)
    start, end = get_window(now)

    try:
        schedule = get_schedule(start, end, login, password)
        service = google_auth.build_calendar_service(google_token)
        exist_events = read_events(service, calendar_id, start, end)
    except (BadCredentials, GoogleAuthError) as e:
        return (
            "user_error",
            f"Синхронизация не выполнена. {e.user_text}",
            f"{type(e).__name__}: {e.detail}",
        )
    except (ScheduleError, NetworkError, GoogleHttpError) as e:
        return (
            "error",
            f"Синхронизация не выполнена. {e.user_text}",
            f"{type(e).__name__}: {e.detail}",
        )

    errors = sync_schedule(
        service, calendar_id, exist_events, schedule, now,
        reminders_enabled, reminder_minutes,
    )
    if errors:
        return (
            "partial",
            f"Готово с ошибками, не обработано пар: {len(errors)}",
            f"не обработано: {errors[:5]}",
        )

    return "ok", "Готово, расписание обновлено", None


def describe_error(e):
    if isinstance(e, HttpError):
        status = getattr(e.resp, "status", "?")
        reason = ""
        try:
            err = json.loads(e.content.decode("utf-8")).get("error", {})
            reason = (err.get("errors") or [{}])[0].get("reason", "")
            message = err.get("message", "")
        except Exception:
            message = str(e)
        return f"{status} {reason}: {message}"

    return f"{type(e).__name__}: {e}"


def methods_with_calendar(request):
    for attempt in range(TOTALLY_TRIES + 1):
        is_last = attempt == TOTALLY_TRIES
        try:
            request.execute()
            return None

        except HttpError as e:
            is_temporary = e.resp.status in (500, 503, 429)
            error = e

        except NETWORK_ERRORS as e:
            is_temporary = True
            error = e

        if is_temporary and not is_last:
            time.sleep(3)
        else:
            return error


def check_json(r):
    content_type = r.headers.get("Content-Type", "")
    if "json" not in content_type:
        raise ScheduleError(f"ответ не JSON, Content-Type: {content_type}")

    return r.json()


def get_group_filters(headers_token):
    groups_url = "https://my.ranepa.ru/lk/n-api/manual/student-groups"
    try:
        r = requests.get(groups_url, headers=headers_token, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        raise NetworkError(f"Не удалось получить список групп: {e}") from e

    data = check_json(r)

    items = data.get("items")
    if not items:
        raise ScheduleError("Сайт не вернул ни одного студента для этого аккаунта")
    
    group = items[0]
    uids = [g["uid_edu_group"] for g in group.get("edu_group", []) if g.get("uid_edu_group")]
    if not uids:
        raise ScheduleError("У студента нет учебных групп, расписание не построить")

    uid_org = group.get("uid_org")
    if not uid_org:
        raise ScheduleError("В ответе нет uid_org")
    
    return (uids, uid_org)


def get_schedule(start, end, login, password):
    login_url = "https://my.ranepa.ru/lk/n-api/auth/login"
    files = {
        "login": (None, login),
        "password": (None, password),
        "remember_me": (None, True),
    }

    try:
        r = requests.post(login_url, headers=headers, files=files, timeout=15)
        r.raise_for_status()

    except requests.RequestException as e:
        response = e.response
        if response is not None and response.status_code == 401:
            raise BadCredentials("401 при входе в ЛК") from e
        raise NetworkError(f"Не удалось войти на сайт: {e}") from e
    preview = check_json(r)

    url = "https://my.ranepa.ru/lk/n-api/schedule"
    access_token = preview["access_token"]
    headers_token = {
        "Authorization": "Bearer " + access_token,
        "Accept": "application/json, text/plain, */*",
        "User-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
        "Version": "1.7"
    }
    
    date_list = []
    day = start
    while day < end:
        current_day = day.date()
        date_list.append(f"{current_day}T00:00:00.000")
        day += dt.timedelta(days=1)

    filters, uid_org = get_group_filters(headers_token)

    params = {
        "uid_org": uid_org,
        "type": "group",
        "dates[]": date_list,
        "filter[]": filters,
        "branch_type": "main"
    }

    try:
        r = requests.get(url, headers=headers_token, params=params, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        raise NetworkError(f"Не удалось получить расписание: {e}") from e

    schedule = check_json(r)

    if "days" not in schedule:
        raise ScheduleError("В расписании нет поля days")

    lessons_count = sum(len(d.get("discs", [])) for d in schedule["days"])
    if lessons_count == 0:
        raise ScheduleError("Расписание пришло пустым")

    return schedule


def prop(value):
    if value is None:
        return ""
    return str(value)


def parse_schedule(schedule, reminders_enabled, reminder_minutes):
    reminders_body = reminders.build_body(reminders_enabled, reminder_minutes)
    reminders_key = reminders.settings_key(reminders_enabled, reminder_minutes)
    events = []
    for certain_day in schedule["days"]:
        for lesson in certain_day["discs"]:
            start_time = make_time(lesson["start_date"])
            end_time = make_time(lesson["end_date"])

            event_body = {
                "summary": lesson["disc"],
                "location": lesson["room"] or "",
                "description": f'{lesson["lecturer"]}, \n{lesson["type"]}',

                "start": {
                    "dateTime": start_time.isoformat(),
                    "timeZone": "Europe/Moscow"
                },

                "end": {
                    "dateTime": end_time.isoformat(),
                    "timeZone": "Europe/Moscow"
                },

                "reminders": reminders_body,

                "extendedProperties": {
                    "private": {
                        "uid_disc": prop(lesson["uid_disc"]),
                        "uid_status": prop(lesson["uid_status"]),
                        "uid_room": prop(lesson["uid_room"]),
                        "uid_type": prop(lesson["uid_type"]),
                        "uid_lecturer": prop(lesson["uid_lecturer"]),
                        "reminders": reminders_key,
                    }
                }
            }

            events.append(event_body)

    return events


def convert_refresh_error(e):
    details = e.args[1] if len(e.args) > 1 and isinstance(e.args[1], dict) else {}
    if details.get("error") == "invalid_grant":
        return GoogleAccessRevoked("invalid_grant")
    return GoogleHttpError(f"RefreshError: {details.get('error', 'unknown')}")


def calendar_exists(service, calendar_id):
    try:
        service.calendars().get(calendarId=calendar_id).execute()
    except HttpError as e:
        if e.resp.status in (403, 404):
            return False
        raise
    return True


def ensure_calendar(refresh_token, calendar_id):
    service = google_auth.build_calendar_service(refresh_token)
    try:
        if calendar_id and calendar_exists(service, calendar_id):
            return calendar_id
        created = service.calendars().insert(
            body={"summary": CALENDAR_TITLE, "timeZone": "Europe/Moscow"}
        ).execute()
    except RefreshError as e:
        raise convert_refresh_error(e) from e
    except HttpError as e:
        raise GoogleHttpError(f"Не удалось создать календарь: {e}") from e
    except (TransportError,) + NETWORK_ERRORS as e:
        raise NetworkError(f"Нет связи с Google: {e}") from e

    return created["id"]


def read_events(service, calendar_id, start, end):
    time_min_iso = start.isoformat()
    time_max_iso = end.isoformat()
    items = []
    page_token = None

    try:
        while True:
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min_iso,
                timeMax=time_max_iso,
                singleEvents=True,
                orderBy="startTime",
                timeZone="Europe/Moscow",
                maxResults=PAGE_SIZE,
                pageToken=page_token,
            ).execute()
            items.extend(events_result.get("items", []))
            page_token = events_result.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        if e.resp.status in (403, 404):
            raise CalendarUnavailable(
                f"HTTP {e.resp.status} при чтении календаря"
            ) from e
        raise GoogleHttpError(f"Не удалось прочитать календарь: {e}") from e
    except RefreshError as e:
        raise convert_refresh_error(e) from e
    except (TransportError,) + NETWORK_ERRORS as e:
        raise NetworkError(f"Нет связи с Google: {e}") from e

    return items


def make_callback(results, key):
    def callback(request_id, response, exception):
        results[key] = exception
    return callback


def execute_jobs(service, jobs):
    failed = []
    for start in range(0, len(jobs), BATCH_SIZE):
        chunk = jobs[start:start + BATCH_SIZE]
        results = {}

        batch = service.new_batch_http_request()
        for key, request in chunk:
            batch.add(request, callback=make_callback(results, key))

        try:
            batch.execute()
        except (HttpError,) + NETWORK_ERRORS:
            pass

        for key, request in chunk:
            if results.get(key, True) is not None:
                error = methods_with_calendar(request)
                if error is not None:
                    info = describe_error(error)
                    log.error("Пара %s не обработана: %s", key, info)
                    failed.append(f"{key}: {info}")

        if start + BATCH_SIZE < len(jobs):
            time.sleep(1)

    return failed


def sync_schedule(service, calendar_id, exist_events, schedule, now,
                  reminders_enabled, reminder_minutes):
    events_in_calendar = {}
    for event in exist_events:
        events_in_calendar[get_event_key(event)] = event

    events = parse_schedule(schedule, reminders_enabled, reminder_minutes)
    jobs = []
    schedule_keys = set()
    for event in events:
        key = get_event_key(event)
        schedule_keys.add(key)

        if key not in events_in_calendar:
            jobs.append((key, service.events().insert(
                calendarId=calendar_id, body=event
            )))
            continue

        if dt.datetime.fromisoformat(event["start"]["dateTime"]) <= now:
            continue

        current = events_in_calendar[key]
        private_par = current["extendedProperties"]["private"]
        event_par = event["extendedProperties"]["private"]
        if (
            private_par["uid_status"] == event_par["uid_status"]
            and private_par["uid_room"] == event_par["uid_room"]
            and private_par["uid_type"] == event_par["uid_type"]
            and private_par["uid_lecturer"] == event_par["uid_lecturer"]
            and private_par.get("reminders") == event_par["reminders"]
        ):
            continue

        jobs.append((key, service.events().patch(
            calendarId=calendar_id, eventId=current["id"], body=event
        )))

    for key, event in events_in_calendar.items():
        event_start = dt.datetime.fromisoformat(event["start"]["dateTime"])
        if key not in schedule_keys and event_start > now:
            jobs.append((key, service.events().delete(
                calendarId=calendar_id, eventId=event["id"]
            )))

    return execute_jobs(service, jobs)


def get_event_key(event):
    start = event["start"]["dateTime"]
    uid_disc = event["extendedProperties"]["private"]["uid_disc"]

    return f"{start}_{uid_disc}"


def make_time(date):
    return dt.datetime.fromisoformat(date).replace(tzinfo=moscow_tz)


def get_window(now):
    start = (now - dt.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + dt.timedelta(28)
    return (start, end)