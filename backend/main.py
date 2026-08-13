#!/usr/bin/env python3
"""
CLI entrypoint for the resume tailoring pipeline.

Usage:
    python main.py --jd-file job_descriptions/some_role.json   # pre-extracted, skips extraction call
    python main.py --jd-file path/to/jd.txt                    # raw text, extracted at run time
    python main.py --jd "paste job description text directly"
    cat jd.txt | python main.py --jd-stdin
    python main.py --jd-url https://example.com/job/123
"""

import argparse
import json
import os
import sys

from resume_tailor.claude_client import ClaudeClient
from resume_tailor.interactions import default_terminal_tool_confirmation
from resume_tailor.jd_fetcher import JDFetchError, fetch_page_text, slugify_filename
from resume_tailor.pipeline import ResumeTailoringPipeline

JD_OUTPUT_DIR = "job_descriptions"


def parse_args():
    parser = argparse.ArgumentParser(description="Tailor a resume to a job description via Drive + Docs + Claude.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--jd-file", help="Path to a .json (pre-extracted) or .txt (raw text) job description file.")
    group.add_argument("--jd", help="Job description text, passed directly.")
    group.add_argument("--jd-stdin", action="store_true", help="Read job description text from stdin.")
    group.add_argument("--jd-url", help="URL of a job posting to fetch and save as a local .json file.")
    return parser.parse_args()


def fetch_jd_url(url: str) -> str:
    """Fetches and extracts a job posting into a structured .json file. Returns the file path."""
    try:
        page_text = fetch_page_text(url)
    except JDFetchError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    claude = ClaudeClient()
    jd_requirements = claude.extract_jd_requirements(page_text)

    os.makedirs(JD_OUTPUT_DIR, exist_ok=True)
    base_name = slugify_filename(
        jd_requirements.get("company_name", ""), jd_requirements.get("role_title", "")
    )
    file_path = os.path.join(JD_OUTPUT_DIR, f"{base_name}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(jd_requirements, f, indent=2)
        f.write("\n")

    return file_path


def main():
    args = parse_args()

    if args.jd_url:
        file_path = fetch_jd_url(args.jd_url)
        print(f"Saved job description to: {file_path}")
        print(f"Next step: python3 main.py --jd-file {file_path}")
        return

    if args.jd_file:
        with open(args.jd_file, "r", encoding="utf-8") as f:
            if args.jd_file.endswith(".json"):
                jd_input = json.load(f)
            else:
                jd_input = f.read()
    elif args.jd:
        jd_input = args.jd
    else:
        jd_input = sys.stdin.read()

    if not jd_input or (isinstance(jd_input, str) and not jd_input.strip()):
        print("Job description text is empty.", file=sys.stderr)
        sys.exit(1)

    pipeline = ResumeTailoringPipeline()
    result = pipeline.run(jd_input, confirm_tools=default_terminal_tool_confirmation)

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"Company:         {result.company}")
    print(f"Role folder:     {result.role_slug}")
    print(f"Resume version:  {result.resume_tag}")
    print(f"Resume file ID:  {result.resume_file_id}")
    print(f"Baseline score:  {result.baseline_score:.2f}")
    print(f"Final score:     {result.final_score:.2f}")
    print(f"Action passes:   {result.action_passes_run}")
    print(f"Tools confirmed: {len(result.tools_confirmed)}")
    print(f"Tools declined:  {len(result.tools_declined)}")
    print(f"Edits applied:   {len(result.applied_edits)}")
    print(f"Edits rejected:  {len(result.rejected_edits)}")
    print(f"Gaps flagged:    {len(result.gaps)}")
    print(f"Changelog doc:   {result.changelog_doc_id}")
    print(f"\nOpen the resume: https://docs.google.com/document/d/{result.resume_file_id}/edit")
    print(f"Open the changelog: https://docs.google.com/document/d/{result.changelog_doc_id}/edit")


if __name__ == "__main__":
    main()
