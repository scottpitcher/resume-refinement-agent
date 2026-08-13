"""
Handles Google OAuth2 for Drive + Docs API access.

First run opens a browser for consent and caches a refresh token locally
(token.json, gitignored). Subsequent runs reuse it silently.
"""

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import SCOPES, settings


def _client_config() -> dict:
    """Builds an in-memory client config so we don't require a credentials.json file."""
    return {
        "installed": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


def get_credentials() -> Credentials:
    creds = None
    token_path = settings.google_token_path

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_config(_client_config(), SCOPES)
            creds = flow.run_local_server(port=8765)

        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


def get_drive_service():
    return build("drive", "v3", credentials=get_credentials())


def get_docs_service():
    return build("docs", "v1", credentials=get_credentials())
