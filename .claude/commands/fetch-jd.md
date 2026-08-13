Fetch a job posting from the URL provided as an argument and extract it into a structured local .json file using the existing JD fetcher.

Run (from the repo root): cd backend && python3 main.py --jd-url $ARGUMENTS

This command only saves the file and prints its path to its own stdout — that output is NOT shown to the user automatically. You (the assistant) must:

1. Use the Read tool to open the saved .json file (the path printed by the command is relative to `backend/`, e.g. `job_descriptions/<name>.json` -> actual path `backend/job_descriptions/<name>.json`). It contains `company_name`, `role_title`, `role_summary`, `tools` (each with a `required`/`preferred` importance), and `action_phrases`.
2. Summarize it readably in your chat response — company, role, a couple sentences from the summary, and the tools/action phrases list — so the user can review it without opening the file themselves.
3. Finish your chat response with the exact shell command to run the tailoring pipeline against this file next, formatted so it can be copy-pasted directly, e.g.:

Next step: cd backend && python3 main.py --jd-file job_descriptions/<name>.json

Do not run that command yourself, just print it and stop.
