import os
import time
import datetime as dt
from zoneinfo import ZoneInfo

import httplib2
import requests
from dotenv import load_dotenv
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


load_dotenv()

headers = {
    "Accept": "application/json, text/plain, */*",
    "User-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
}

moscow_tz = ZoneInfo("Europe/Moscow")
SCOPES = ["https://www.googleapis.com/auth/calendar"]
SERVICE_ACCOUNT_PATH = "secrets/service_account.json"
TOTALLY_TRIES = 2
NETWORK_ERRORS = (OSError, httplib2.HttpLib2Error)


class ScheduleError(Exception):
    pass


class NetworkError(Exception):
    pass


class GoogleHttpError(Exception):
    pass


class GoogleAuthError(Exception):
    pass


class BadCredentials(Exception):
    pass


def run_sync(login, password, calendar_id):
    now = dt.datetime.now(tz=moscow_tz)
    start, end = get_window(now)

    try:
        schedule = get_schedule(start, end, login, password)
        service = get_calendar_service()
        exist_events = read_events(service, calendar_id, start, end)
    except (BadCredentials, GoogleAuthError) as e:
        return "user_error", f"Синхронизация не выполнена: {e}"
    except (ScheduleError, NetworkError, GoogleHttpError) as e:
        return "error", f"Синхронизация не выполнена: {e}"

    errors = sync_schedule(service, calendar_id, exist_events, schedule, now)
    if errors:
        status = "partial"
        message = f"Готово с ошибками, не обработано пар: {len(errors)}"
        return status, message

    status = "ok"
    message = "Готово, расписание обновлено"
    return status, message


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
        print(f"сайт вернул не JSON: {content_type}")
        raise ScheduleError(
            "сайт РАНХиГС сейчас просит пройти проверку и не отдаёт расписание. "
            "Календарь не изменён, попробуйте позже"
        )

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
            raise BadCredentials(
                "Неверный логин или пароль от личного кабинета"
            ) from e
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


def parse_schedule(schedule):
    events = []
    for certain_day in schedule["days"]:
        for lesson in certain_day["discs"]:
            start_time = make_time(lesson["start_date"])
            end_time = make_time(lesson["end_date"])

            event_body = {
                "summary": lesson["disc"],
                "location": lesson["room"],
                "description": f'{lesson["lecturer"]}, \n{lesson["type"]}',

                "start": {
                    "dateTime": start_time.isoformat(),
                    "timeZone": "Europe/Moscow"
                },

                "end": {
                    "dateTime": end_time.isoformat(),
                    "timeZone": "Europe/Moscow"
                },

                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 5},
                        {"method": "popup", "minutes": 15},
                        {"method": "popup", "minutes": 30},
                    ]
                },

                "extendedProperties": {
                    "private": {
                        "uid_disc": lesson["uid_disc"],
                        "uid_status": lesson["uid_status"],
                        "uid_room": lesson["uid_room"],
                        "uid_type": lesson["uid_type"],
                        "uid_lecturer": lesson["uid_lecturer"],
                    }
                }
            }

            events.append(event_body)

    return events


def get_calendar_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_PATH, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds)


def read_events(service, calendar_id, start, end):
    time_min_iso = start.isoformat()
    time_max_iso = end.isoformat()

    try:
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min_iso,
            timeMax=time_max_iso,
            singleEvents=True,
            orderBy="startTime",
            timeZone="Europe/Moscow",
        ).execute()
    except HttpError as e:
        if e.resp.status in (403, 404):
            raise GoogleAuthError(
                "нет доступа к календарю: проверь ID и то, что ты дал доступ "
                "адресу бота с правом изменять мероприятия"
            ) from e
        raise GoogleHttpError(f"Не удалось прочитать календарь: {e}") from e
    except RefreshError as e:
        raise GoogleHttpError("не удалось войти в Google под аккаунтом бота") from e
    except (TransportError,) + NETWORK_ERRORS as e:
        raise NetworkError(f"Нет связи с Google: {e}") from e

    return events_result.get("items", [])


def create_events(service, calendar_id, event_body):
    request = service.events().insert(
        calendarId=calendar_id,
        body=event_body
    )
    return methods_with_calendar(request)


def sync_schedule(service, calendar_id, exist_events, schedule, now):
    events_in_calendar = {}
    for event in exist_events:
        key = get_event_key(event)
        events_in_calendar[key] = event

    events = parse_schedule(schedule)
    errors_events = []
    schedule_keys = set()
    for event in events:
        check_event = get_event_key(event)
        schedule_keys.add(check_event)

        if check_event not in events_in_calendar:
            if create_events(service, calendar_id, event) is not None:
                errors_events.append(check_event)

        else:  # событие уже есть в календаре: проверяем, не изменилось ли оно
            private_par = events_in_calendar[check_event]["extendedProperties"]["private"]
            event_par = event["extendedProperties"]["private"]

            if (
                private_par["uid_status"] == event_par["uid_status"]
                and private_par["uid_room"] == event_par["uid_room"]
                and private_par["uid_type"] == event_par["uid_type"]
                and private_par["uid_lecturer"] == event_par["uid_lecturer"]
            ):
                pass

            else:  # поменялась аудитория, преподаватель или тип
                eventId = events_in_calendar[check_event]["id"]
                request = service.events().patch(
                    calendarId=calendar_id,
                    eventId=eventId,
                    body=event
                )
                if methods_with_calendar(request) is not None:
                    errors_events.append(check_event)

    for key, event in events_in_calendar.items():
        event_start = dt.datetime.fromisoformat(event["start"]["dateTime"])
        if key not in schedule_keys and event_start > now:
            eventId = event["id"]
            request = service.events().delete(
                calendarId=calendar_id,
                eventId=eventId
            )
            if methods_with_calendar(request) is not None:
                errors_events.append(key)

    return errors_events


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


def main():
    status, message = run_sync(
        os.environ["RANEPA_LOGIN"],
        os.environ["RANEPA_PASSWORD"],
        os.environ["SCHEDULE_CALENDAR_ID"],
    )
    print(message)


if __name__ == "__main__":
    main()
