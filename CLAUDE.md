# CLAUDE.md

## Project Overview
Resume Agent automates tailoring resumes to specific job postings using Claude, pulling and writing to Google Drive and Google Docs. It's structured as a monorepo (`backend/` + `frontend/`) in preparation for a full-stack app; today only `backend/` has code.

## Repo layout
```
resume-agent-project/
  README.md, CLAUDE.md          # repo-level docs, stay at root
  .claude/                       # slash commands (repo-level, cd into backend/ as needed)
  backend/                       # Python pipeline -- see below
  frontend/                      # reserved for the future app, currently empty
```

## Architecture (backend/)
- `backend/main.py` — CLI entry point, orchestrates the pipeline
- `backend/resume_tailor/pipeline.py` — core pipeline logic connecting all steps; also where the fact-check, coverage, score-increase, and one-page length gates are enforced in code
- `backend/resume_tailor/claude_client.py` — wraps Claude API calls for resume tailoring/generation
- `backend/resume_tailor/drive_client.py` — Google Drive API interactions (fetching/saving files)
- `backend/resume_tailor/docs_client.py` — Google Docs API interactions (reading/writing doc content)
- `backend/resume_tailor/jd_fetcher.py` — fetches a JD from a URL, strips boilerplate, and renders a structured JD record back to readable text (`render_jd_summary`)
- `backend/resume_tailor/auth.py` — handles Google OAuth and any Claude API auth
- `backend/resume_tailor/config.py` — loads environment variables and app configuration
- `backend/resume_tailor/prompts.py` — prompt templates used with Claude
- `backend/job_descriptions/` — saved JD extractions (`*.json`) and raw JD text (`*.txt`)

## Job description format
`backend/job_descriptions/*.json` files hold Claude's structured extraction of a JD —
`company_name`, `role_title`, `role_summary`, `tools` (each tagged
`required`/`preferred`), `action_phrases` — not free text. `main.py --jd-file`
accepts this `.json` form (skips the extraction call at pipeline run time) or
a `.txt`/raw-text file (extracted at run time). Keep this schema in sync
across `prompts.EXTRACT_JD_SYSTEM`, `jd_fetcher.render_jd_summary`, and any
pipeline code that reads `jd_requirements` fields.

## Environment Setup
- All commands below run from inside `backend/`, not the repo root
- Requires a `backend/.env` file (see `backend/.env.example`) with API keys and Google OAuth credentials
- Install dependencies: `pip install -r requirements.txt` (from within `backend/`, ideally into `backend/.venv`)
- Google API auth likely requires a `credentials.json` from Google Cloud Console (OAuth client) — never commit this

## Conventions
- Keep API clients (`claude_client.py`, `drive_client.py`, `docs_client.py`) as thin wrappers — business logic belongs in `pipeline.py`
- All config/secrets loaded through `config.py`, never hardcoded or read directly from `os.environ` elsewhere
- Prompts live in `prompts.py`, not inline in other files
- The pipeline's edit-apply loop enforces its gates (fact-check, coverage, score-increase, one-page length) in code, never by prompt instruction alone — an edit that fails a gate must never reach `docs_client.replace_text`, regardless of what a prompt says. Preserve this when touching `pipeline.py`.
- Reuse existing Claude calls for new judgment data where possible instead of adding a new call (e.g. `fact_check_edit` also reports action-phrase coverage deltas) — this project is deliberately cost-conscious about API calls.

## Common Commands
- Run the pipeline: `cd backend && python main.py --jd-file job_descriptions/some_role.json`
- Test the tailoring loop against a saved JD: `/test-tailor` slash command (see `.claude/commands/test-tailor.md`)
- Fetch + extract a JD from a URL: `/fetch-jd <url>` slash command
- Run tests: `cd backend && pytest tests/`

## Notes for Claude Code
- Before modifying `auth.py` or `config.py`, confirm what env vars are already expected downstream
- When adding new Claude prompts, follow the existing structure/naming in `prompts.py`
- Do not commit `.env`, `credentials.json`, or any generated OAuth token files
- Do not run `main.py` or the `/test-tailor` command yourself unless explicitly asked — it makes live Claude API calls and writes real files to the user's Google Drive
- Backend commands (pytest, py_compile, pip, python main.py) must run from inside `backend/`, not the repo root — the package, venv, and .env all live there now

## Project Initialization
When asked to "initialize project," do the following, one file at a time, confirming with me before moving to the next:

1. Create a `backend/.env.example` listing all environment variables referenced across `claude_client.py`, `drive_client.py`, `docs_client.py`, `auth.py`, and `config.py`, with placeholder values and short comments.
2. Create a `.gitignore` at the repo root covering `backend/.env`, `__pycache__`, `backend/.venv`, `*.pyc`, `credentials.json`, OAuth token files, and standard Python/IDE junk.
3. Check `backend/resume_tailor/config.py` for `python-dotenv` usage loading from `.env`. If missing, add it.
4. Create a `setup.py` or `pyproject.toml` inside `backend/` so the package is pip installable locally.
5. Add a `backend/tests/` folder scaffolded to match the `resume_tailor` module structure.