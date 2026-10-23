import os
import requests
import json
import datetime as dt
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials # токен для запросов
from google_auth_oauthlib.flow import InstalledAppFlow # OAuth2 для авторизации через браузер
from google.auth.transport.requests import Request # для обновления истекшего токена
from googleapiclient.discovery import build # создание объекта service для работы с гугл календарем
from googleapiclient.errors import HttpError # для обработки ошибок http
from google.auth.exceptions import RefreshError

from dotenv import load_dotenv


load_dotenv()
files = {
    "login": (None, os.environ["RANEPA_LOGIN"]),
    "password": (None, os.environ["RANEPA_PASSWORD"]),
    "remember_me": (None, True)
}

headers = {
    "Accept": "application/json, text/plain, */*",
    "User-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
}

moscow_tz = ZoneInfo("Europe/Moscow")
SCOPES = ["https://www.googleapis.com/auth/calendar"]
SCHEDULE_CALENDAR_ID = os.environ["SCHEDULE_CALENDAR_ID"]
SCHEDULE_PATH = "data/schedule.json"
TOKEN_PATH = "secrets/token.json"
CREDENTIALS_PATH = "secrets/credentials.json"

def get_schedule(start, end):
    login_url = "https://my.ranepa.ru/lk/n-api/auth/login"
    r = requests.post(login_url, headers=headers, files=files)
    preview = r.json()
    access_token = preview["access_token"]

    
    date_list = []
    day = start
    while day < end:
        current_day = day.date()
        date_list.append(f"{current_day}T00:00:00.000")
        day += dt.timedelta(days=1)


    url = "https://my.ranepa.ru/lk/n-api/schedule?uid_org=7923e704-99aa-11e5-bed4-c48508aa74e4&type=group&filter[]=398d38b8-1b98-11f0-9f95-00620bc81861&filter[]=8957051a-1b99-11f0-9f95-00620bc81861&filter[]=971e9ee3-1f81-11f0-9f96-00620bc81861&filter[]=606e8975-1f82-11f0-9f96-00620bc81861&filter[]=8dfd5228-1f82-11f0-9f96-00620bc81861&filter[]=2526ca7c-9ae1-11f0-9fa0-00620bc81861&filter[]=79fea6eb-b56d-11f0-9fa2-00620bc81861&filter[]=1c55dbe9-b718-11f0-9fa2-00620bc81861&filter[]=6bfce9e7-b718-11f0-9fa2-00620bc81861&filter[]=252931bd-c5f7-11f0-9fa2-00620bc81861&filter[]=50d92106-cecd-11f0-9fa2-00620bc81861&filter[]=7368ed65-d4e4-11f0-9fa3-00620bc81861&filter[]=031dbd30-d4f8-11f0-9fa3-00620bc81861&filter[]=041383ac-34e6-11f1-9fab-00620bc81861&filter[]=9542dc2c-3fd7-11f1-9fab-00620bc81861&filter[]=d8a7d792-5828-11f1-9fac-00620bc81861&filter[]=d8a7d7ab-5828-11f1-9fac-00620bc81861&filter[]=d8a7d7d7-5828-11f1-9fac-00620bc81861&filter[]=c57342e5-7e3c-11f1-9fac-00620bc81861&filter[]=2b0e6a5a-7f99-11f1-9fac-00620bc81861&filter[]=2b0e6ab5-7f99-11f1-9fac-00620bc81861&filter[]=aa496774-817b-11f1-9fac-00620bc81861&filter[]=aa496776-817b-11f1-9fac-00620bc81861&filter[]=d3eae5db-912f-11f1-9fac-00620bc81861&filter[]=9015f5ba-9a40-11f1-9faf-00620bc81861"
    headers_token = {
        "Authorization": "Bearer " + access_token,
        "Accept": "application/json, text/plain, */*",
        "User-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
        "Version": "1.7"
    }

    params = {
        "dates[]": date_list,
        "branch_type": "main"
    }

    # получаем schedule.json
    r = requests.get(url, headers=headers_token, params=params)
    schedule = r.json()
    with open(SCHEDULE_PATH, "w", encoding="utf8") as file:
        json.dump(schedule, file, indent=4, ensure_ascii=False)

    with open(SCHEDULE_PATH, encoding="utf8") as file:
        schedule = json.load(file)

def parse_schedule_for_month(): # возвращает список событий events
    with open(SCHEDULE_PATH, encoding="utf8") as file:
        schedule = json.load(file)

        events = []
        all_days = schedule["days"]
        for certain_day in all_days:
            schedule_certain_day = certain_day["discs"]

            for lesson in schedule_certain_day:
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
                            {"method": "popup", "minutes": 15},
                            {"method": "popup", "minutes": 30},
                            {"method": "popup", "minutes": 60},
                        ]
                    },

                    "extendedProperties":{
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

def get_calendar_service(): #доступ к календарю, объект service
    creds = None
    token_path = TOKEN_PATH
    credentials_path = CREDENTIALS_PATH

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.valid:
        pass

    else:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                creds = None
        else:
            creds = None

        if creds is None:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as token:
            token.write(creds.to_json())

    service = build("calendar", "v3", credentials=creds)
    return service

def read_events(service, start, end):
    time_min_iso = start.isoformat()
    time_max_iso = end.isoformat()

    events_result = service.events().list(
        calendarId=SCHEDULE_CALENDAR_ID,
        timeMin=time_min_iso,
        timeMax=time_max_iso,
        singleEvents=True,
        orderBy="startTime",
        timeZone="Europe/Moscow",
    ).execute()
    
    events = events_result.get("items", [])

    return events
        
def create_events(service, event_body):
    try:
        service.events().insert(
            calendarId=SCHEDULE_CALENDAR_ID,
            body=event_body
        ).execute()

    except HttpError as error:
        return error

def sync_schedule(service, exist_events, now):
    events_in_calendar = {}
    for event in exist_events:
        key = get_event_key(event)
        events_in_calendar[key] = event


    events = parse_schedule_for_month()
    errors_events = []
    schedule_keys = set()
    for event in events:
        check_event = get_event_key(event)
        schedule_keys.add(check_event)

        if check_event not in events_in_calendar:
            if create_events(service, event) is not None:
                errors_events.append(check_event)

        else: # если событие есть в календаре, но ничего не изменилось
            private_par = events_in_calendar[check_event]["extendedProperties"]["private"]
            event_par = event["extendedProperties"]["private"]
            
            uid_status_e = private_par["uid_status"]
            uid_room_e = private_par["uid_room"]
            uid_type_e = private_par["uid_type"]
            uid_lecturer_e = private_par["uid_lecturer"]
            
            uid_status = event_par["uid_status"]
            uid_room = event_par["uid_room"]
            uid_type = event_par["uid_type"]
            uid_lecturer = event_par["uid_lecturer"]

            if (
                uid_status_e == uid_status
                and uid_room_e == uid_room
                and uid_type_e == uid_type
                and uid_lecturer_e == uid_lecturer
            ):
                pass

            else: # если поменялась аудитория, преподаватель или тп
                try:
                    eventId = events_in_calendar[check_event]["id"]
                    service.events().patch(
                        calendarId=SCHEDULE_CALENDAR_ID,
                        eventId=eventId,
                        body=event
                    ).execute()
                except HttpError:
                    errors_events.append(check_event)

    for key, event in events_in_calendar.items():
        try:
            event_start = dt.datetime.fromisoformat(event["start"]["dateTime"])
            if key not in schedule_keys and event_start > now:
                eventId = event["id"]
                service.events().delete(
                    calendarId=SCHEDULE_CALENDAR_ID,
                    eventId=eventId
                ).execute()
        except HttpError:
            errors_events.append(key)

    return errors_events
        
def get_event_key(event):
    start = event["start"]["dateTime"]
    uid_disc = event["extendedProperties"]["private"]["uid_disc"]

    return f"{start}_{uid_disc}"

def make_time(date):
    date = dt.datetime.fromisoformat(date).replace(tzinfo=moscow_tz)
    return date

def get_window(now):
    start = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(28)
    return (start, end)

def main():
    now = dt.datetime.now(tz=moscow_tz)
    start, end = get_window(now)
    get_schedule(start, end) # получаем расписание в виде файла schedule.json
    service = get_calendar_service() # получаем для взаимодействия между python и Google Calendar API
    exist_events = read_events(service, start, end) # получаем уже существующие мероприятия
    errors = sync_schedule(service, exist_events, now)
    print(errors)
    
if __name__ == "__main__":
    main()
