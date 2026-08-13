# Resume Tailoring Pipeline

An agentic pipeline that tailors a resume to a job description using the
Google Drive API, the Google Docs API, and Claude. It edits native Google
Docs **in place** (via `documents.batchUpdate`), so formatting is never lost
to a download/rebuild cycle.

This is a monorepo: the pipeline described below lives entirely in
`backend/`, in preparation for a `frontend/` app to sit in front of it
(currently an empty placeholder). **Every command in this README that
mentions `main.py`, `pytest`, `.env`, or `.venv` assumes you're running it
from inside `backend/`.**

## How it works

1. Claude reads the job description and picks the best-fitting base resume
   (DS / ML / SE) out of the three in your working folder.
2. It finds or creates `[Company]/[RoleName]_[MonthYear]/` under your
   working folder.
3. It makes a server-side **copy** of the chosen base resume into that
   folder (a native Doc-to-Doc copy, so formatting is untouched) and never
   edits the original.
4. It gets the JD's structured requirements record -- company/role
   identification plus required tools/technologies and action phrases --
   either extracted fresh from raw JD text, or read directly from a
   pre-extracted `job_descriptions/*.json` file (see below), which skips
   that extraction call entirely. It scores the untouched resume against
   this record as a baseline.
5. It loops: propose edits -> fact-check each edit -> apply only the ones
   that pass, add coverage without dropping any, and stay within the
   one-page length budget -> re-score against the JD -> repeat until the
   score clears your threshold or it hits `MAX_ITER`. If an entire round's
   edits get applied but the resulting score doesn't beat the score before
   that round, those edits are reverted and the loop stops (the
   score-increase gate below). If a round's edits are all *rejected*
   instead (nothing applied), the loop doesn't give up -- it tries again
   with the rejected edits and their rejection reasons fed back in, so the
   next round proposes something genuinely different rather than the same
   non-additive rewrite. It only stops early on an empty round if Claude
   itself proposes zero edits (nothing left it thinks is worth trying).
6. It writes a changelog Doc into the same folder listing every edit
   applied, every edit rejected (and why), and any gaps it couldn't close
   honestly.

**Four gates are enforced in code, not just prompted:**
- **Fact-check gate.** An edit is only applied if a separate Claude call
  confirms no underlying fact or action changed, and if the original text
  matched exactly once in the document (so it can't accidentally rewrite
  the wrong occurrence).
- **Coverage gate.** An edit is only applied if it doesn't drop a
  tool/action the resume currently covers, and it adds coverage of at
  least one it doesn't already cover -- otherwise it's rejected as a
  no-op that wouldn't move the score. Tool coverage is checked with a
  deterministic, case-insensitive substring match against the whole
  resume (no API call); action-phrase coverage reuses the same
  fact-check call's own before/after read of the bullet, so this adds no
  extra Claude calls.
- **Score-increase gate.** Each iteration's post-edit coverage score is
  compared against the score before that iteration. If it didn't strictly
  increase, that iteration's edits are reverted in the Doc and the loop
  stops -- a holistic backstop on top of the per-edit coverage gate.
- **One-page length gate.** Before an edit is applied, the pipeline checks
  whether it would push the resume's total character count past
  `MAX_LENGTH_GROWTH` over the original base resume's length. If so, it's
  rejected before it ever touches the Doc.

There is no code path that applies an edit without passing the fact-check,
coverage, and length gates, and no code path that leaves a net-negative
iteration's edits in place.

## Job description format

`backend/job_descriptions/*.json` files hold the structured extraction Claude
produces from a JD -- `company_name`, `role_title`, `role_summary`, `tools`
(each tagged `required`/`preferred`), and `action_phrases` -- rather than
free JD text. `/fetch-jd <url>` produces this directly. `main.py --jd-file`
accepts either a `.json` file (skips the extraction call at pipeline run
time) or a `.txt`/raw-text file (extracted at run time, for JD text pasted
or piped in directly).

## Project structure

```
resume-agent-project/
  README.md, CLAUDE.md         # repo-level docs
  .claude/commands/              # slash commands, cd into backend/ as needed
    fetch-jd.md                  # /fetch-jd <url>
    test-tailor.md                # /test-tailor [jd-file]
    initialize-project.md
  frontend/                      # reserved for the future app -- empty for now
  backend/
    main.py                     # CLI entrypoint
    requirements.txt
    .env.example                  # copy to .env and fill in
    job_descriptions/              # saved JD extractions (*.json) and raw JD text (*.txt)
    resume_tailor/
      config.py                   # env loading, defaults
      auth.py                     # Google OAuth (Drive + Docs scopes)
      drive_client.py              # find/create folders, copy files
      docs_client.py               # read paragraphs, in-place text replace
      jd_fetcher.py                 # fetch a JD URL, strip boilerplate, render a summary
      claude_client.py             # all Claude API calls
      prompts.py                   # system prompts for each Claude call
      pipeline.py                   # the orchestration loop + code-enforced gates
```

## Project initialization

If you're setting this repo up fresh for version control and local
development, run the `/initialize-project` slash command in Claude Code. It
scaffolds everything that isn't pipeline logic itself and isn't a secret
you have to supply by hand: `.env.example`, `.gitignore`, a
`setup.py`/`pyproject.toml` for local pip installs, a `tests/` folder
matching the `resume_tailor` module structure, and a `.venv` with
dependencies installed from `requirements.txt` (via `uv` if available). It
confirms each file with you before moving to the next.

That covers steps 3 (env file scaffold) and 4 (dependency install) below —
the setup steps that remain are the ones that need your own credentials or
Drive account.

## What you need to do to make it work

### 1. Google Cloud setup (Drive + Docs API access)

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and
   create a new project (or use an existing one).
2. Enable two APIs for that project:
   - **Google Drive API**
   - **Google Docs API**
   (APIs & Services > Library > search each by name > Enable.)
3. Configure the OAuth consent screen (APIs & Services > OAuth consent
   screen). "External" is fine for personal use; you'll need to add your
   own Google account as a test user if the app isn't verified.
4. Create credentials: APIs & Services > Credentials > Create Credentials >
   OAuth client ID > Application type **Desktop app**.
5. Copy the generated **Client ID** and **Client Secret** into your `.env`
   file (see below). You do not need to download the JSON file; the app
   builds its own client config from these two values.

### 2. Anthropic API key

Get an API key from the [Anthropic Console](https://console.anthropic.com/)
and put it in `.env` as well.

### 3. Environment file

`/initialize-project` scaffolds `.env.example` for you; you still need to
create your own `.env` and fill in your actual secrets:

```
cp .env.example .env
```

Fill in:
- `ANTHROPIC_API_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

Leave the rest at their defaults unless you want to change behavior (see
Configuration below).

### 4. Dependencies

If you ran `/initialize-project`, dependencies are already installed into
`.venv` — just activate it:

```
source .venv/bin/activate       # Windows: .venv\Scripts\activate
```

Otherwise, install them yourself:

```
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 5. Confirm your Drive structure matches what the pipeline expects

```
2026 Recruiting/                          <- WORKING_FOLDER_NAME in .env
  Scott Pitcher Resume (DS)                <- native Google Doc
  Scott Pitcher Resume (ML)                <- native Google Doc
  Scott Pitcher Resume (SE)                <- native Google Doc
  [Company]/
    [RoleName]_[MonthYear]/
      ...
```

The base resumes must be **native Google Docs**, not uploaded `.docx`
files (uploaded Office files have a different MIME type and the Docs API
can't edit them in place the same way). If yours are uploaded `.docx`
files, open each one in Drive and use File > Save as Google Docs, then
delete the original `.docx`.

The pipeline matches base resumes by looking for `(DS)`, `(ML)`, `(SE)` in
the file name. If your naming differs, edit `base_resume_tags` in
`resume_tailor/config.py`.

### 6. First run: Google auth

The first time you run the pipeline, it will open a browser window asking
you to sign in and grant Drive + Docs access. After you approve, a
`token.json` file is cached locally so you won't have to re-auth on future
runs (it's gitignored, never commit it).

## How to use it

```
python main.py --jd-file job_descriptions/some_role.json   # pre-extracted, skips the extraction call
python main.py --jd-file path/to/job_description.txt       # raw text, extracted at run time
```

or paste it directly:

```
python main.py --jd "Full job description text goes here..."
```

or pipe it in:

```
cat job_description.txt | python main.py --jd-stdin
```

The script prints its progress step by step, and finishes with a summary
including direct links to the tailored resume and the changelog doc.

### Testing the tailoring logic itself

Run the `/test-tailor` slash command in Claude Code to run a live pipeline
pass against a JD already saved under `job_descriptions/` (defaults to the
Braze JD, or pass a different path as an argument) and see the score/edit
progression iteration by iteration, plus a few concrete before/after edit
examples pulled from the changelog once it finishes.

## Configuration

All in `.env`:

| Variable | Default | What it does |
|---|---|---|
| `WORKING_FOLDER_NAME` | `2026 Recruiting` | Top-level Drive folder name |
| `MAX_ITER` | `5` | Max edit/score loop iterations before stopping regardless of score |
| `SCORE_THRESHOLD` | `0.85` | Coverage score (0-1) at which the loop stops early |
| `MAX_EDITS_PER_ITERATION` | `4` | Cap on proposed edits per loop pass, keeps each round reviewable |
| `MAX_LENGTH_GROWTH` | `0.05` | Max fraction the tailored resume's total character count may grow past the original base resume's length, keeps it to one page |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Model used for all judgment calls. Cheaper models (e.g. Haiku) cut cost per run but may need a lower `SCORE_THRESHOLD` or more `MAX_ITER` to reach the same coverage |

## Things worth doing before you trust it on a real application

- **Run it once on a low-stakes JD first** and open the resulting Doc
  yourself. Confirm formatting held and skim every applied edit in the
  changelog.
- **Read the changelog's rejected edits section too.** Seeing what it
  *didn't* apply, and why, is often more informative than what it did.
- **Check the gaps list.** If it's flagging real, true gaps (missing
  tools/experience) instead of quietly working around them, that's the
  fact-check discipline working as intended.
- If you ever want to extend this (add a cover letter step, support more
  than three base resumes, plug in a different scoring rubric), the
  natural extension points are `prompts.py` (what Claude is asked to do)
  and `pipeline.py` (the loop structure itself).

## Known limitations

- Uses `replaceAllText`, which requires the original bullet text to match
  **exactly once** in the document. If it doesn't match uniquely, the
  pipeline skips that edit and logs why, rather than guessing which
  occurrence you meant.
- Works on native Google Docs only, not uploaded `.docx`/`.doc` files.
- No delete capability by design; it only ever adds copies and folders, it
  never removes anything from your Drive. Clean up unwanted files manually.
- OAuth scopes request full Drive access (`drive` scope, not
  `drive.file`), because the pipeline needs to search your whole Drive to
  find the working folder and existing company folders. If you'd rather
  scope this down, you could restructure to require the working folder ID
  as a direct input instead of searching for it by name.

## Obstacles

A running log of real problems hit while building/running this, and what
was done about each one.

- **JSON parsing broke on model preamble/trailing text.** `_extract_json`
  originally assumed the entire response was valid JSON once markdown
  fences were stripped. Switching `CLAUDE_MODEL` to a chattier model
  surfaced responses with prose before or after the JSON object (e.g. "Here
  are the edits:"), which failed with `Expecting value: line 1 column 1`.
  **Fixed:** `_extract_json` now finds the first `{`/`[` and decodes only
  that JSON value via `json.JSONDecoder().raw_decode`, ignoring any
  surrounding text.
- **`propose_edits` responses got truncated mid-JSON.** With a flat
  `max_tokens=3000`, a full batch of `MAX_EDITS_PER_ITERATION` edits (each
  carrying four verbose string fields) sometimes didn't fit, so the
  response was cut off mid-string and failed to parse -- `Unterminated
  string starting at: ...`. This looked identical to the preamble bug above
  until the raw-response diagnostic (see next item) made the real cause
  visible. **Fixed:** `max_tokens` for that call now scales with
  `num_edits` (`1000 + 800 * num_edits`) instead of a fixed cap.
- **JSON failures were undiagnosable from the traceback alone.** The
  original code re-raised a bare `json.JSONDecodeError` after 3 failed
  retries, with no visibility into what the model actually returned.
  **Fixed:** the final failure now raises a `ValueError` including a
  snippet of the raw response text, which is what made both bugs above
  possible to tell apart.
- **Stale venv after the backend/frontend reorg.** After splitting the repo
  into `backend/`/`frontend/`, the old root-level `.venv` was deleted and a
  fresh one created at `backend/.venv`. A terminal that still had the old
  venv "activated" (prompt showed `(.venv)`) silently fell back to a
  different `python3` once the old venv's files were gone, producing
  `ModuleNotFoundError: No module named 'anthropic'` even though the new
  venv had it installed. **Resolved:** `deactivate` then re-`source
  backend/.venv/bin/activate` from the new location.
- **Confusing traceback mid-move.** A pipeline run was kicked off against
  the pre-reorg file paths (`resume-agent-project/main.py`) moments before
  the move to `backend/` finished, producing a traceback pointing at files
  that no longer existed by the time it was read. Not an actual bug --
  just a timing artifact of running a command while files were being
  relocated.
- **Two pre-existing test failures unrelated to any of this work.**
  `test_claude_client_reads_settings` and
  `test_settings_loads_required_and_defaults` assert `CLAUDE_MODEL ==
  "claude-sonnet-4-6"`, but the local `.env` has since been changed (e.g.
  to test Haiku for cost, see Configuration above), so the assertion fails
  against whatever `.env` currently holds. Not fixed -- these tests assume
  a fixed default that no longer matches local config; they'd need to
  either read the expected value from `.env.example` or stop asserting a
  specific model string.
- **The loop gave up after a single all-rejected round.** With the
  coverage gate in place, a real run against the Braze JD had every
  proposed edit in iteration 1 rejected (3 for not adding coverage, 1 by
  the fact-check gate) -- and the loop treated "0 edits applied" the same
  as "nothing left to try," stopping immediately with the resume unchanged
  at its baseline score. The rejections were legitimate (the resume
  genuinely doesn't have TensorFlow/Keras/CatBoost/etc. experience to
  honestly surface), but the pipeline gave up without ever telling Claude
  what it had already tried, so it couldn't attempt something different.
  **Fixed:** rejected edits (from any gate) now accumulate into an
  `already_tried` list fed back into `propose_edits` on the next round,
  with each rejection's reason, and the loop keeps going instead of
  stopping when a round applies nothing -- still bounded by `MAX_ITER`. It
  only stops early on an empty round if Claude itself proposes zero edits.
