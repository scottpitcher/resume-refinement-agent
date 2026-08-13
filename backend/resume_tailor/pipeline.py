"""
The orchestration loop. This is intentionally "agentic within a scaffold":
Claude decides *which* edits to make and *when* it's done (via scoring), but
the fact-check, coverage, and length gates are enforced here in code -- an
edit that fails any of them is never applied, full stop, regardless of what
any prompt says.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from .claude_client import ClaudeClient
from .config import settings
from .docs_client import DocsClient
from .drive_client import DOC_MIME, FOLDER_MIME, DriveClient
from .jd_fetcher import render_jd_summary


def _tools_covered(text: str, tools: List[Dict[str, Any]]) -> set:
    """Deterministic, case-insensitive substring match -- no API call."""
    lowered = text.lower()
    return {t["name"] for t in tools if t.get("name", "").lower() in lowered}


@dataclass
class ChangelogEntry:
    original_text: str
    new_text: str
    verdict: str
    reasoning: str


@dataclass
class RunResult:
    company: str
    role_slug: str
    resume_tag: str
    resume_file_id: str
    resume_file_name: str
    folder_id: str
    final_score: float
    iterations_run: int
    applied_edits: List[ChangelogEntry] = field(default_factory=list)
    rejected_edits: List[ChangelogEntry] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    changelog_doc_id: Optional[str] = None


class ResumeTailoringPipeline:
    def __init__(self):
        self.drive = DriveClient()
        self.docs = DocsClient()
        self.claude = ClaudeClient()

    # ------------------------------------------------------------------
    # Setup: locate working folder, base resumes, existing company folders
    # ------------------------------------------------------------------

    def _get_working_folder(self) -> dict:
        folder = self.drive.find_folder(settings.working_folder_name)
        if not folder:
            raise FileNotFoundError(
                f"Could not find a folder named '{settings.working_folder_name}' in Drive. "
                f"Check WORKING_FOLDER_NAME in .env matches the real folder name exactly."
            )
        return folder

    def _find_base_resumes(self, working_folder_id: str) -> Dict[str, dict]:
        """Returns {tag: file_metadata} for each base resume tag found directly in the working folder."""
        children = self.drive.list_children(working_folder_id)
        found = {}
        for tag in settings.base_resume_tags:
            for child in children:
                if child["mimeType"] != DOC_MIME:
                    continue
                if f"({tag})" in child["name"] or f"({tag.upper()})" in child["name"].upper():
                    found[tag] = child
                    break
        missing = [t for t in settings.base_resume_tags if t not in found]
        if missing:
            raise FileNotFoundError(
                f"Could not find base resumes for tags {missing} directly inside "
                f"'{settings.working_folder_name}'. Expected file names containing e.g. '(DS)', '(ML)', '(SE)'."
            )
        return found

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, jd_input: Union[str, Dict[str, Any]]) -> RunResult:
        """jd_input is either raw JD text (extracted at run time) or an already-extracted
        JD requirements dict (e.g. loaded from a job_descriptions/*.json file), in which
        case the extraction call is skipped entirely."""
        if isinstance(jd_input, dict):
            jd_requirements: Optional[Dict[str, Any]] = jd_input
            jd_text = render_jd_summary(jd_input)
        else:
            jd_requirements = None
            jd_text = jd_input

        working_folder = self._get_working_folder()
        base_resumes = self._find_base_resumes(working_folder["id"])

        # Step 1: pick the best base resume
        resume_options = []
        for tag, meta in base_resumes.items():
            paragraphs = self.docs.get_paragraphs(meta["id"])
            excerpt = "\n".join(paragraphs[:15])  # enough to judge fit without blowing up tokens
            resume_options.append({"tag": tag, "excerpt": excerpt})

        pick = self.claude.pick_resume(jd_text, resume_options)
        chosen_tag = pick["chosen_tag"]
        chosen_resume = base_resumes[chosen_tag]
        print(f"[1/8] Picked base resume: {chosen_tag} -- {pick['reasoning']}")

        # Step 2: derive company + role naming
        naming = self.claude.get_role_slug(jd_text)
        company_name = naming["company_name"]
        role_slug = naming["role_slug"]
        date_suffix = datetime.datetime.now().strftime("%B%Y")
        role_folder_name = f"{role_slug}_{date_suffix}"
        print(f"[2/8] Target location: {company_name}/{role_folder_name}")

        # Step 3: find/create company + role folders
        company_folder = self.drive.find_or_create_folder(company_name, working_folder["id"])
        role_folder = self.drive.find_or_create_folder(role_folder_name, company_folder["id"])
        print(f"[3/8] Folder ready: {company_folder['id']}/{role_folder['id']}")

        # Step 4: copy the base resume into the role folder (native Doc copy -- formatting preserved)
        new_name = f"Scott Pitcher Resume ({company_name} - {role_slug})"
        copy = self.drive.copy_file(chosen_resume["id"], new_name, role_folder["id"])
        resume_id = copy["id"]
        print(f"[4/8] Copied resume: {copy['name']} ({resume_id})")

        # Step 5: build the immutable atomic fact record from the COPY, before any edits
        original_facts = self.docs.get_paragraphs(resume_id)

        # Step 6: extract JD requirements (skipped if the caller already supplied them)
        if jd_requirements is None:
            jd_requirements = self.claude.extract_jd_requirements(jd_text)
            print(f"[5/8] Extracted {len(jd_requirements.get('tools', []))} tools, "
                  f"{len(jd_requirements.get('action_phrases', []))} action phrases from JD")
        else:
            print(f"[5/8] Using pre-extracted JD requirements: {len(jd_requirements.get('tools', []))} tools, "
                  f"{len(jd_requirements.get('action_phrases', []))} action phrases")

        # Step 7: iterative edit loop
        applied: List[ChangelogEntry] = []
        rejected: List[ChangelogEntry] = []
        gaps: List[str] = []

        # Baseline: score and length of the resume before any edits. Edits are only
        # kept if the iteration they belong to actually raises coverage above this
        # running baseline, and are never applied if they'd blow the one-page budget.
        last_score_result = self.claude.score_coverage(jd_requirements, original_facts)
        baseline_score = last_score_result.get("score", 0.0)
        print(f"[6/8] Baseline coverage score: {baseline_score:.2f}")

        current_length = sum(len(p) for p in original_facts)
        max_length = current_length * (1 + settings.max_length_growth)
        current_full_text = "\n".join(original_facts)
        jd_tools = jd_requirements.get("tools", [])
        jd_action_phrases = jd_requirements.get("action_phrases", [])

        iteration = 0
        already_tried: List[Dict[str, str]] = []

        for iteration in range(1, settings.max_iter + 1):
            current_paragraphs = self.docs.get_paragraphs(resume_id)
            score_before = last_score_result.get("score", 0.0)

            gap_summary = (
                f"Tools still missing: {last_score_result.get('tools_missing', [])}. "
                f"Actions still missing: {last_score_result.get('actions_missing', [])}."
            )

            proposal = self.claude.propose_edits(
                jd_requirements=jd_requirements,
                current_paragraphs=current_paragraphs,
                original_facts=original_facts,
                gap_summary=gap_summary,
                num_edits=settings.max_edits_per_iteration,
                already_tried=already_tried,
            )
            gaps.extend(g for g in proposal.get("gaps", []) if g not in gaps)

            edits = proposal.get("edits", [])
            if not edits:
                print(f"[6/8] Iteration {iteration}: no further edits proposed, stopping.")
                break

            any_applied = False
            iteration_applied: List[ChangelogEntry] = []
            for edit in edits:
                original_text = edit["original_text"]
                new_text = edit["new_text"]

                check = self.claude.fact_check_edit(original_text, new_text, jd_action_phrases)
                entry = ChangelogEntry(
                    original_text=original_text,
                    new_text=new_text,
                    verdict=check["verdict"],
                    reasoning=check["reasoning"],
                )

                if check["verdict"] != "pass":
                    rejected.append(entry)
                    already_tried.append({"original_text": original_text, "reason": entry.reasoning})
                    continue

                occurrences = self.docs.count_occurrences(resume_id, original_text)
                if occurrences != 1:
                    entry.verdict = "reject"
                    entry.reasoning += (
                        f" [Skipped: original text matched {occurrences} times in the document, "
                        f"not applied to avoid an ambiguous or unintended replacement.]"
                    )
                    rejected.append(entry)
                    already_tried.append({"original_text": original_text, "reason": entry.reasoning})
                    continue

                projected_length = current_length - len(original_text) + len(new_text)
                if projected_length > max_length:
                    entry.verdict = "reject"
                    entry.reasoning += (
                        f" [Skipped: would grow the resume past the one-page budget "
                        f"({projected_length} chars > {max_length:.0f} cap).]"
                    )
                    rejected.append(entry)
                    already_tried.append({"original_text": original_text, "reason": entry.reasoning})
                    continue

                # Deterministic coverage gate: only apply an edit if it doesn't drop any
                # tool/action the resume currently covers, and it adds at least one it
                # doesn't already cover. Tools are checked against the whole resume text
                # (cheap substring match, no API call); action phrases use the fact-check
                # call's own before/after read of this bullet, so no extra call is needed.
                projected_full_text = current_full_text.replace(original_text, new_text, 1)
                tools_before = _tools_covered(current_full_text, jd_tools)
                tools_after = _tools_covered(projected_full_text, jd_tools)
                tools_lost = tools_before - tools_after
                tools_gained = tools_after - tools_before

                actions_before = set(check.get("action_phrases_covered_before", []))
                actions_after = set(check.get("action_phrases_covered_after", []))
                actions_lost = actions_before - actions_after
                actions_gained = actions_after - actions_before

                if tools_lost or actions_lost:
                    entry.verdict = "reject"
                    entry.reasoning += (
                        f" [Skipped: would drop coverage of tools={sorted(tools_lost)} "
                        f"actions={sorted(actions_lost)} that are currently matched.]"
                    )
                    rejected.append(entry)
                    already_tried.append({"original_text": original_text, "reason": entry.reasoning})
                    continue

                if not (tools_gained or actions_gained):
                    entry.verdict = "reject"
                    entry.reasoning += (
                        " [Skipped: doesn't add coverage of any currently-missing tool or "
                        "action phrase, so it wouldn't increase the score.]"
                    )
                    rejected.append(entry)
                    already_tried.append({"original_text": original_text, "reason": entry.reasoning})
                    continue

                self.docs.replace_text(resume_id, original_text, new_text)
                current_length = projected_length
                current_full_text = projected_full_text
                applied.append(entry)
                iteration_applied.append(entry)
                any_applied = True

            print(f"[6/8] Iteration {iteration}: {sum(1 for e in edits)} proposed, "
                  f"{len(applied)} applied so far, {len(rejected)} rejected so far.")

            if not any_applied:
                print(f"       No edits applied this round ({len(edits)} proposed, all rejected); "
                      f"trying again with {len(already_tried)} known-bad edits ruled out.")
                # Skip re-scoring: the document didn't change, so the score can't have
                # moved. already_tried (fed back into propose_edits above) is what
                # steers the next attempt toward edits it hasn't already tried and had
                # rejected -- MAX_ITER is still the hard cap on how many rounds this can take.
                continue

            # Re-score against the now-updated document
            updated_paragraphs = self.docs.get_paragraphs(resume_id)
            last_score_result = self.claude.score_coverage(jd_requirements, updated_paragraphs)
            score = last_score_result.get("score", 0.0)
            print(f"[7/8] Coverage score after iteration {iteration}: {score:.2f}")

            if score <= score_before:
                print(f"       Score did not increase ({score:.2f} <= {score_before:.2f}), "
                      f"reverting this iteration's edits and stopping.")
                for entry in reversed(iteration_applied):
                    self.docs.replace_text(resume_id, entry.new_text, entry.original_text)
                    current_length = current_length - len(entry.new_text) + len(entry.original_text)
                    current_full_text = current_full_text.replace(entry.new_text, entry.original_text, 1)
                    applied.remove(entry)
                    entry.verdict = "reject"
                    entry.reasoning += " [Reverted: iteration did not increase coverage score.]"
                    rejected.append(entry)
                last_score_result["score"] = score_before
                break

            if score >= settings.score_threshold:
                print(f"       Threshold {settings.score_threshold} reached, stopping.")
                break

        final_score = last_score_result.get("score", 0.0) if last_score_result else 0.0
        if last_score_result:
            gaps.extend(g for g in last_score_result.get("actions_missing", []) if g not in gaps)
            gaps.extend(
                f"Tool not found in resume: {t}"
                for t in last_score_result.get("tools_missing", [])
                if f"Tool not found in resume: {t}" not in gaps
            )

        result = RunResult(
            company=company_name,
            role_slug=role_slug,
            resume_tag=chosen_tag,
            resume_file_id=resume_id,
            resume_file_name=new_name,
            folder_id=role_folder["id"],
            final_score=final_score,
            iterations_run=iteration,
            applied_edits=applied,
            rejected_edits=rejected,
            gaps=gaps,
        )

        # Step 8: write a changelog doc alongside the resume
        result.changelog_doc_id = self._write_changelog(result, role_folder["id"])
        print(f"[8/8] Changelog written: {result.changelog_doc_id}")

        return result

    # ------------------------------------------------------------------
    # Changelog
    # ------------------------------------------------------------------

    def _write_changelog(self, result: RunResult, folder_id: str) -> str:
        doc = self.docs.create_document(f"Changelog - {result.resume_file_name}")
        doc_id = doc["documentId"]

        lines = [
            f"Changelog for {result.resume_file_name}",
            f"Base resume version used: {result.resume_tag}",
            f"Final coverage score: {result.final_score:.2f} (threshold: {settings.score_threshold})",
            f"Iterations run: {result.iterations_run} / {settings.max_iter}",
            "",
            "APPLIED EDITS:",
        ]
        if result.applied_edits:
            for e in result.applied_edits:
                lines.append(f"- ORIGINAL: {e.original_text}")
                lines.append(f"  NEW:      {e.new_text}")
                lines.append(f"  Reason:   {e.reasoning}")
                lines.append("")
        else:
            lines.append("(none)")
            lines.append("")

        lines.append("REJECTED EDITS (fact-check failed or ambiguous match):")
        if result.rejected_edits:
            for e in result.rejected_edits:
                lines.append(f"- ORIGINAL: {e.original_text}")
                lines.append(f"  PROPOSED: {e.new_text}")
                lines.append(f"  Reason rejected: {e.reasoning}")
                lines.append("")
        else:
            lines.append("(none)")
            lines.append("")

        lines.append("GAPS THAT COULD NOT BE HONESTLY CLOSED:")
        if result.gaps:
            for g in result.gaps:
                lines.append(f"- {g}")
        else:
            lines.append("(none identified)")

        self.docs.append_text(doc_id, "\n".join(lines))

        # Move the changelog doc into the role folder
        drive_service = self.drive.service
        file_meta = drive_service.files().get(fileId=doc_id, fields="parents").execute()
        prev_parents = ",".join(file_meta.get("parents", []))
        drive_service.files().update(
            fileId=doc_id, addParents=folder_id, removeParents=prev_parents, fields="id, parents"
        ).execute()

        return doc_id
