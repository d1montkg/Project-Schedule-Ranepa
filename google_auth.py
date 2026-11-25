import logging
import os
import json

from urllib.parse import parse_qs, urlparse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from pathlib import Path
from google_auth_oauthlib.flow import Flow

os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

logger = logging.getLogger(__name__)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
SCOPES = [CALENDAR_SCOPE]
CLIENT_SECRET_PATH = Path(__file__).parent / "secrets" / "client_secret.json"
REDIRECT_URI = "http://localhost"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class AuthError(Exception):
    pass


def start_auth():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET_PATH, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    url, state = flow.authorization_url(
        access_type="offline", prompt="consent"
    )
    return flow, url, state


def finish_auth(flow, state, pasted_url):
    pasted_url = pasted_url.strip()
    params = parse_qs(urlparse(pasted_url).query)

    if "error" in params:
        raise AuthError(
            "Доступ не разрешён. Открой ссылку ещё раз, нажми «Разрешить» "
            "и пришли новый адрес."
        )
    if params.get("state", [None])[0] != state or "code" not in params:
        raise AuthError(
            "Этот адрес не подходит. Нужен полный адрес из строки браузера, "
            "который появился после нажатия «Разрешить» по ссылке из "
            "последнего сообщения."
        )

    try:
        flow.fetch_token(authorization_response=pasted_url)
    except Exception as e:
        logger.warning("Обмен кода Google не удался: %s", type(e).__name__)
        raise AuthError(
            "Google не принял адрес: код одноразовый и быстро истекает. "
            "Начни заново командой /start."
        ) from e

    credentials = flow.credentials
    if CALENDAR_SCOPE not in (credentials.scopes or []):
        raise AuthError(
            "Не выдан доступ к календарю. Начни заново командой /start "
            "и оставь галочку на календаре."
        )
    if not credentials.refresh_token:
        raise AuthError(
            "Google не выдал постоянный доступ. Начни заново командой /start."
        )

    return credentials.refresh_token


def build_calendar_service(refresh_token):
    with open(CLIENT_SECRET_PATH, encoding="utf-8") as f:
        client = json.load(f)["installed"]

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=credentials)