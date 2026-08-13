"""
Fetches a job posting from a URL and reduces it to clean text.

This module only handles HTTP fetching and HTML-to-text stripping. Figuring
out the actual structured content (company, role, tools, action phrases,
etc.) is delegated to Claude via ClaudeClient.extract_jd_requirements, since
job boards don't agree on section headings and hardcoding them here would
be brittle.
"""

import re

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_BOT_BLOCK_MARKERS = (
    "captcha",
    "are you a human",
    "access denied",
    "attention required",
    "cloudflare",
    "please verify you are a human",
    "request unsuccessful",
)

# Tags whose content is essentially never part of the actual job posting body.
_STRIP_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "svg", "iframe", "form")

_BOILERPLATE_CLASS_HINTS = (
    "cookie",
    "consent",
    "gdpr",
    "banner",
    "nav",
    "footer",
    "header",
    "menu",
    "breadcrumb",
    "social-share",
    "share-buttons",
)


class JDFetchError(Exception):
    """Raised when a job posting URL can't be fetched or looks blocked."""


def fetch_page_text(url: str, timeout: int = 15) -> str:
    """Fetches `url` and returns cleaned, boilerplate-stripped page text.

    Raises JDFetchError for 404s, other HTTP errors, network failures, or
    pages that look like a bot-detection/challenge page rather than real
    content.
    """
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.exceptions.Timeout:
        raise JDFetchError(f"Timed out fetching {url}.")
    except requests.exceptions.ConnectionError as e:
        raise JDFetchError(f"Could not connect to {url}: {e}")
    except requests.exceptions.RequestException as e:
        raise JDFetchError(f"Failed to fetch {url}: {e}")

    if response.status_code == 404:
        raise JDFetchError(f"Job posting not found (404) at {url}. It may have been taken down.")
    if response.status_code in (403, 429, 503):
        raise JDFetchError(
            f"Request to {url} was blocked or rate-limited (HTTP {response.status_code}). "
            f"This site may be actively blocking automated requests -- try pasting the JD "
            f"text manually instead."
        )
    if not response.ok:
        raise JDFetchError(f"Failed to fetch {url}: HTTP {response.status_code}.")

    text = _extract_visible_text(response.text)

    lowered = text.lower()
    if len(text.strip()) < 200 or any(marker in lowered for marker in _BOT_BLOCK_MARKERS):
        raise JDFetchError(
            f"The page at {url} doesn't look like a real job posting (too short or looks like "
            f"a bot-detection/challenge page). Try pasting the JD text manually instead."
        )

    return text


def _extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    for el in soup.find_all(True):
        if el.attrs is None:
            continue  # already decomposed via an ancestor removal above
        classes = " ".join(el.get("class", [])).lower()
        el_id = (el.get("id") or "").lower()
        haystack = f"{classes} {el_id}"
        if any(hint in haystack for hint in _BOILERPLATE_CLASS_HINTS):
            el.decompose()

    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    text = main.get_text(separator="\n")

    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def slugify_filename(company_name: str, role_title: str) -> str:
    """Builds a filesystem-safe base filename (no extension) from company + role."""
    combined = f"{company_name}_{role_title}"
    combined = re.sub(r"[^\w\s-]", "", combined)
    combined = re.sub(r"\s+", "_", combined.strip())
    return combined or "job_description"


def render_jd_summary(jd_requirements: dict) -> str:
    """Renders an extracted JD requirements record into plain, human-readable text.

    Used to give pick_resume/get_role_slug enough narrative context to work with,
    since job_descriptions/ files now store the structured record, not free text.
    """
    lines = []

    company = jd_requirements.get("company_name", "").strip()
    role = jd_requirements.get("role_title", "").strip()
    lines.append(f"{role or 'Role'} at {company or 'Unknown Company'}")
    lines.append("=" * 60)
    lines.append("")

    summary = jd_requirements.get("role_summary", "").strip()
    if summary:
        lines.append("ROLE SUMMARY")
        lines.append("-" * 60)
        lines.append(summary)
        lines.append("")

    tools = jd_requirements.get("tools", [])
    if tools:
        lines.append("TOOLS/TECHNOLOGIES")
        lines.append("-" * 60)
        for t in tools:
            lines.append(f"- {t.get('name', '')} ({t.get('importance', '')})")
        lines.append("")

    actions = jd_requirements.get("action_phrases", [])
    if actions:
        lines.append("ACTION PHRASES")
        lines.append("-" * 60)
        for a in actions:
            lines.append(f"- {a}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"
