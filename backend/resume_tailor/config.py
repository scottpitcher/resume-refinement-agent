"""
Configuration and environment loading for the resume tailoring pipeline.

All secrets come from environment variables (see .env.example). This file
only defines defaults and reads env vars -- it never hardcodes credentials.
"""

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _get_env(name: str, default: str = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise EnvironmentError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


@dataclass
class Settings:
    # --- Anthropic ---
    anthropic_api_key: str = field(default_factory=lambda: _get_env("ANTHROPIC_API_KEY", required=True))
    claude_model: str = field(default_factory=lambda: _get_env("CLAUDE_MODEL", "claude-sonnet-4-6"))

    # --- Google OAuth (Drive + Docs) ---
    google_client_id: str = field(default_factory=lambda: _get_env("GOOGLE_CLIENT_ID", required=True))
    google_client_secret: str = field(default_factory=lambda: _get_env("GOOGLE_CLIENT_SECRET", required=True))
    google_token_path: str = field(default_factory=lambda: _get_env("GOOGLE_TOKEN_PATH", "token.json"))
    google_redirect_uri: str = field(default_factory=lambda: _get_env("GOOGLE_REDIRECT_URI", "http://localhost:8765/"))

    # --- Pipeline behavior ---
    working_folder_name: str = field(default_factory=lambda: _get_env("WORKING_FOLDER_NAME", "2026 Recruiting"))
    max_action_passes: int = field(default_factory=lambda: int(_get_env("MAX_ACTION_PASSES", "5")))
    score_threshold: float = field(default_factory=lambda: float(_get_env("SCORE_THRESHOLD", "0.85")))
    max_length_growth: float = field(default_factory=lambda: float(_get_env("MAX_LENGTH_GROWTH", "0.05")))

    # --- Base resume identification ---
    # These are substrings matched (case-insensitive) against file names in the
    # working folder to identify the three base resume versions. Adjust in .env
    # if your file names differ.
    base_resume_tags: List[str] = field(default_factory=lambda: ["DS", "ML", "SE"])


SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

settings = Settings()
