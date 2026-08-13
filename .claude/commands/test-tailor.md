Run the resume tailoring pipeline end-to-end against a job description file, for testing the tailoring/scoring/edit logic itself (not the JD-fetch step).

Default JD file: `job_descriptions/Braze_Forward-Deployed_Data_Scientist.json`. If $ARGUMENTS is non-empty, treat it as a path to a different JD file (.json pre-extracted, or .txt raw text) and use that instead.

Run (from the repo root), cd into backend/ first since main.py and job_descriptions/ live there:
cd backend && python3 main.py --jd-file $ARGUMENTS

(if $ARGUMENTS is empty, run: cd backend && python3 main.py --jd-file job_descriptions/Braze_Forward-Deployed_Data_Scientist.json)

This is a real, live run: it copies an actual Google Doc, calls Claude repeatedly, and writes to Drive. Let the pipeline's own per-iteration stdout output (baseline score, edits proposed/applied/rejected, score per iteration) stream to the user as it runs — that output is the point of this command, since it shows how the resume changes iteration by iteration.

When the run finishes:
1. Summarize the final result block (company, role, resume tag, final score, iterations run, edits applied/rejected, gaps).
2. Read the changelog doc's applied/rejected edits back (e.g. via the Google Drive MCP `read_file_content`/`download_file_content` tools on the changelog doc ID printed by the pipeline), and show the user a few concrete before/after examples of applied edits so they can judge tailoring quality directly, not just the score number.
3. Note the resume and changelog Google Doc links from the pipeline output so the user can open them directly.
