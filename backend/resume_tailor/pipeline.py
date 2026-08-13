"""
The orchestration loop. This is intentionally "agentic within a scaffold":
Claude decides *which* edits to make, but the fact-check, coverage, and
length gates are enforced here in code -- an edit that fails any of them is
never applied, full stop, regardless of what any prompt says.

Two sequential phases per run:
- Tools phase: deterministic, zero Claude calls. The user is asked which
  JD tools they're competent in; confirmed ones are appended to the
  resume's existing "Tools:" line.
- Action-phrase phase: Claude-driven, one phrase at a time, exhaustive.
  Each not-yet-covered phrase gets one targeted edit attempt per pass,
  repeated until a full pass applies nothing (a "clean pass") or a pass
  cap is hit.

score_coverage is reporting-only (baseline + final) -- it no longer
controls when the loop stops. The per-edit coverage gate already enforces
"only apply edits that add coverage" at a finer grain than a holistic
re-score ever did.
"""

import datetime
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from .claude_client import ClaudeClient
from .config import settings
from .docs_client import DocsClient
from .drive_client import DOC_MIME, FOLDER_MIME, DriveClient
from .interactions import default_terminal_tool_confirmation
from .jd_fetcher import render_jd_summary

ToolConfirmCallback = Callable[[List[Dict[str, Any]]], List[str]]

_TOOLS_LABEL_RE = re.compile(r"^\s*tools\s*:\s*", re.IGNORECASE)


def _tools_covered(text: str, tools: List[Dict[str, Any]]) -> set:
    """Deterministic, case-insensitive SUBSTRING match against free-text resume
    content -- no API call. Appropriate here because a tool can legitimately
    appear inside a longer phrase (e.g. "AWS Lambda" containing "AWS")."""
    lowered = text.lower()
    return {t["name"] for t in tools if t.get("name", "").lower() in lowered}


def _find_tools_paragraph(paragraphs: List[str]) -> Optional[str]:
    """Returns the exact paragraph text of the single "Tools: ..." line, or
    None if zero or more than one paragraph matches (ambiguous -> caller
    skips rather than guessing which one)."""
    matches = [p for p in paragraphs if _TOOLS_LABEL_RE.match(p)]
    return matches[0] if len(matches) == 1 else None


def _parse_tools_from_line(tools_line: str) -> List[str]:
    """Strips the "Tools:" label and splits the comma-separated tokens,
    trimming whitespace and dropping empties. Order preserved."""
    rest = _TOOLS_LABEL_RE.sub("", tools_line, count=1)
    return [tok.strip() for tok in rest.split(",") if tok.strip()]


def _tool_already_in_line(jd_tool_name: str, existing_tokens: List[str]) -> bool:
    """Exact, case-insensitive token match against the Tools: line's parsed
    tokens -- deliberately NOT a substring match. The Tools line is a short,
    atomic, comma-delimited vocabulary where substring matching produces real
    false positives (JD tool "R" would substring-match "Docker"; "SQL" would
    substring-match "NoSQL"). _tools_covered's substring match stays correct
    for the action-phrase phase's free-text whole-resume check; this helper
    is intentionally stricter and scoped only to the Tools line."""
    lowered_existing = {t.lower() for t in existing_tokens}
    return jd_tool_name.strip().lower() in lowered_existing


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
    action_passes_run: int
    baseline_score: float = 0.0
    applied_edits: List[ChangelogEntry] = field(default_factory=list)
    rejected_edits: List[ChangelogEntry] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    already_covered_tools: List[str] = field(default_factory=list)
    tools_confirmed: List[str] = field(default_factory=list)
    tools_declined: List[str] = field(default_factory=list)
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
    # Phase A: Tools (deterministic, zero Claude calls)
    # ------------------------------------------------------------------

    def _run_tools_phase(
        self,
        resume_id: str,
        jd_tools: List[Dict[str, Any]],
        confirm_tools: ToolConfirmCallback,
    ) -> Dict[str, Any]:
        """Returns a dict with: applied, rejected (ChangelogEntry lists),
        gaps, already_present (tool names), confirmed (tool names),
        declined (tool names)."""
        applied: List[ChangelogEntry] = []
        rejected: List[ChangelogEntry] = []
        gaps: List[str] = []

        paragraphs = self.docs.get_paragraphs(resume_id)
        tools_line = _find_tools_paragraph(paragraphs)
        if tools_line is None:
            gaps.append("Tools: line not found (or ambiguous) in resume -- tool coverage phase skipped.")
            return {
                "applied": applied, "rejected": rejected, "gaps": gaps,
                "already_present": [], "confirmed": [], "declined": [],
            }

        existing_tokens = _parse_tools_from_line(tools_line)
        already_present = [t for t in jd_tools if _tool_already_in_line(t["name"], existing_tokens)]
        missing = [t for t in jd_tools if not _tool_already_in_line(t["name"], existing_tokens)]

        if not missing:
            return {
                "applied": applied, "rejected": rejected, "gaps": gaps,
                "already_present": [t["name"] for t in already_present],
                "confirmed": [], "declined": [],
            }

        confirmed_names = confirm_tools(missing)
        confirmed_names = set(confirmed_names)
        confirmed = [t for t in missing if t["name"] in confirmed_names]
        declined = [t for t in missing if t["name"] not in confirmed_names]

        if confirmed:
            occurrences = self.docs.count_occurrences(resume_id, tools_line)
            if occurrences != 1:
                gaps.append(
                    f"Tools: line matched {occurrences} times in the document -- "
                    f"skipped updating it to avoid an ambiguous replacement."
                )
                declined = missing  # nothing could actually be applied
                confirmed = []
            else:
                new_tokens = existing_tokens + [t["name"] for t in confirmed]
                new_line = "Tools: " + ", ".join(new_tokens)
                self.docs.replace_text(resume_id, tools_line, new_line)
                for t in confirmed:
                    applied.append(ChangelogEntry(
                        original_text=tools_line,
                        new_text=new_line,
                        verdict="user-confirmed",
                        reasoning=(
                            f"User confirmed competency in '{t['name']}' "
                            f"(JD importance: {t.get('importance', '?')}); added to Tools line."
                        ),
                    ))

        for t in declined:
            rejected.append(ChangelogEntry(
                original_text=tools_line,
                new_text=tools_line,
                verdict="user-declined",
                reasoning=f"User did not confirm competency in '{t['name']}'; left out of Tools line.",
            ))

        return {
            "applied": applied, "rejected": rejected, "gaps": gaps,
            "already_present": [t["name"] for t in already_present],
            "confirmed": [t["name"] for t in confirmed],
            "declined": [t["name"] for t in declined],
        }

    # ------------------------------------------------------------------
    # Phase B: Action phrases (Claude-driven, exhaustive)
    # ------------------------------------------------------------------

    def _run_action_phrase_phase(
        self,
        resume_id: str,
        jd_requirements: Dict[str, Any],
        original_facts: List[str],
        max_length: float,
        current_length: int,
        covered_phrases: set,
    ) -> Dict[str, Any]:
        """Returns a dict with: applied, rejected (ChangelogEntry lists),
        gaps, passes_run, final_length."""
        jd_tools = jd_requirements.get("tools", [])
        jd_action_phrases = jd_requirements.get("action_phrases", [])

        applied: List[ChangelogEntry] = []
        rejected: List[ChangelogEntry] = []
        gaps: List[str] = []
        already_tried: List[Dict[str, str]] = []
        covered_phrases = set(covered_phrases)

        current_full_text = "\n".join(self.docs.get_paragraphs(resume_id))
        passes_run = 0

        for pass_num in range(1, settings.max_action_passes + 1):
            passes_run = pass_num
            any_applied_this_pass = False

            for phrase in jd_action_phrases:
                if phrase in covered_phrases:
                    continue

                current_paragraphs = self.docs.get_paragraphs(resume_id)
                gap_summary = (
                    f"Focus only on this JD action phrase and no other: '{phrase}'. "
                    f"Propose at most one edit that clearly and honestly surfaces it."
                )
                proposal = self.claude.propose_edits(
                    jd_requirements=jd_requirements,
                    current_paragraphs=current_paragraphs,
                    original_facts=original_facts,
                    gap_summary=gap_summary,
                    num_edits=1,
                    already_tried=already_tried,
                )
                for g in proposal.get("gaps", []):
                    if g not in gaps:
                        gaps.append(g)

                edits = proposal.get("edits", [])
                if not edits:
                    continue  # nothing honest for this phrase -- move on, don't stop the pass

                edit = edits[0]  # num_edits=1, but defensively only take the first
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

                projected_full_text = current_full_text.replace(original_text, new_text, 1)
                tools_before = _tools_covered(current_full_text, jd_tools)
                tools_after = _tools_covered(projected_full_text, jd_tools)
                tools_lost = tools_before - tools_after
                tools_gained = tools_after - tools_before

                actions_before = set(check.get("action_phrases_covered_before", []))
                actions_after = set(check.get("action_phrases_covered_after", []))
                actions_lost = actions_before - actions_after

                if tools_lost or actions_lost:
                    entry.verdict = "reject"
                    entry.reasoning += (
                        f" [Skipped: would drop coverage of tools={sorted(tools_lost)} "
                        f"actions={sorted(actions_lost)} that are currently matched.]"
                    )
                    rejected.append(entry)
                    already_tried.append({"original_text": original_text, "reason": entry.reasoning})
                    continue

                # Tightened coverage gate: must specifically newly-cover the TARGET phrase,
                # or gain a tool as a side effect -- an edit that happens to touch some other
                # phrase without honestly delivering the one it was asked for doesn't count,
                # since under per-phrase targeting we know exactly what "success" means here.
                phrase_gained = phrase in actions_after and phrase not in actions_before
                if not (phrase_gained or tools_gained):
                    entry.verdict = "reject"
                    entry.reasoning += (
                        f" [Skipped: doesn't newly cover the target phrase '{phrase}' "
                        f"and doesn't add any tool coverage.]"
                    )
                    rejected.append(entry)
                    already_tried.append({"original_text": original_text, "reason": entry.reasoning})
                    continue

                self.docs.replace_text(resume_id, original_text, new_text)
                current_length = projected_length
                current_full_text = projected_full_text
                applied.append(entry)
                any_applied_this_pass = True
                if phrase_gained:
                    covered_phrases.add(phrase)

            print(f"       Pass {pass_num}: {len(applied)} applied so far, {len(rejected)} rejected so far.")
            if not any_applied_this_pass:
                print("       Clean pass -- no edits applied for any remaining action phrase. Done.")
                break

        return {
            "applied": applied, "rejected": rejected, "gaps": gaps,
            "passes_run": passes_run, "final_length": current_length,
        }

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        jd_input: Union[str, Dict[str, Any]],
        confirm_tools: Optional[ToolConfirmCallback] = None,
    ) -> RunResult:
        """jd_input is either raw JD text (extracted at run time) or an already-extracted
        JD requirements dict (e.g. loaded from a job_descriptions/*.json file), in which
        case the extraction call is skipped entirely.

        confirm_tools is called with the list of JD tools not already found in the
        resume's Tools line, and must return the subset of names the user confirmed
        competency in. Defaults to a terminal prompt; a future web backend can pass a
        different implementation without touching this method."""
        confirm_tools = confirm_tools or default_terminal_tool_confirmation

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
        print(f"[1/9] Picked base resume: {chosen_tag} -- {pick['reasoning']}")

        # Step 2: derive company + role naming
        naming = self.claude.get_role_slug(jd_text)
        company_name = naming["company_name"]
        role_slug = naming["role_slug"]
        date_suffix = datetime.datetime.now().strftime("%B%Y")
        role_folder_name = f"{role_slug}_{date_suffix}"
        print(f"[2/9] Target location: {company_name}/{role_folder_name}")

        # Step 3: find/create company + role folders
        company_folder = self.drive.find_or_create_folder(company_name, working_folder["id"])
        role_folder = self.drive.find_or_create_folder(role_folder_name, company_folder["id"])
        print(f"[3/9] Folder ready: {company_folder['id']}/{role_folder['id']}")

        # Step 4: copy the base resume into the role folder (native Doc copy -- formatting preserved)
        new_name = f"Scott Pitcher Resume ({company_name} - {role_slug})"
        copy = self.drive.copy_file(chosen_resume["id"], new_name, role_folder["id"])
        resume_id = copy["id"]
        print(f"[4/9] Copied resume: {copy['name']} ({resume_id})")

        # Step 5: build the immutable atomic fact record from the COPY, before any edits
        original_facts = self.docs.get_paragraphs(resume_id)

        # Step 6: extract JD requirements (skipped if the caller already supplied them)
        if jd_requirements is None:
            jd_requirements = self.claude.extract_jd_requirements(jd_text)
            print(f"[5/9] Extracted {len(jd_requirements.get('tools', []))} tools, "
                  f"{len(jd_requirements.get('action_phrases', []))} action phrases from JD")
        else:
            print(f"[5/9] Using pre-extracted JD requirements: {len(jd_requirements.get('tools', []))} tools, "
                  f"{len(jd_requirements.get('action_phrases', []))} action phrases")

        # Step 7: baseline coverage score -- reporting only, never a stopping condition
        baseline_result = self.claude.score_coverage(jd_requirements, original_facts)
        baseline_score = baseline_result.get("score", 0.0)
        print(f"[6/9] Baseline coverage score: {baseline_score:.2f}")

        current_length = sum(len(p) for p in original_facts)
        max_length = current_length * (1 + settings.max_length_growth)

        # Step 8a: Phase A -- Tools (deterministic, zero Claude calls)
        tools_result = self._run_tools_phase(resume_id, jd_requirements.get("tools", []), confirm_tools)
        print(f"[7/9] Tools phase: {len(tools_result['confirmed'])} confirmed, "
              f"{len(tools_result['already_present'])} already present, "
              f"{len(tools_result['declined'])} declined.")

        current_paragraphs = self.docs.get_paragraphs(resume_id)
        current_length = sum(len(p) for p in current_paragraphs)

        # Step 8b: Phase B -- Action phrases (Claude-driven, exhaustive)
        covered_phrases = set(baseline_result.get("actions_reflected", []))
        action_result = self._run_action_phrase_phase(
            resume_id, jd_requirements, original_facts, max_length, current_length, covered_phrases,
        )
        print(f"[8/9] Action-phrase phase: {action_result['passes_run']} pass(es), "
              f"{len(action_result['applied'])} applied, {len(action_result['rejected'])} rejected.")

        # Step 9: final coverage score -- reporting only
        final_paragraphs = self.docs.get_paragraphs(resume_id)
        final_result = self.claude.score_coverage(jd_requirements, final_paragraphs)
        final_score = final_result.get("score", 0.0)
        print(f"[9/9] Final coverage score: {final_score:.2f} (baseline was {baseline_score:.2f})")

        applied = tools_result["applied"] + action_result["applied"]
        rejected = tools_result["rejected"] + action_result["rejected"]
        gaps = list(tools_result["gaps"])
        gaps.extend(g for g in action_result["gaps"] if g not in gaps)
        gaps.extend(f"Tool declined by user: {t}" for t in tools_result["declined"])
        gaps.extend(g for g in final_result.get("actions_missing", []) if g not in gaps)
        gaps.extend(
            f"Tool not found in resume: {t}"
            for t in final_result.get("tools_missing", [])
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
            baseline_score=baseline_score,
            action_passes_run=action_result["passes_run"],
            applied_edits=applied,
            rejected_edits=rejected,
            gaps=gaps,
            already_covered_tools=tools_result["already_present"],
            tools_confirmed=tools_result["confirmed"],
            tools_declined=tools_result["declined"],
        )

        result.changelog_doc_id = self._write_changelog(result, role_folder["id"])
        print(f"[9/9] Changelog written: {result.changelog_doc_id}")

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
            f"Baseline coverage score: {result.baseline_score:.2f}  |  "
            f"Final coverage score: {result.final_score:.2f} "
            f"(informational only, target was {settings.score_threshold})",
            f"Action-phrase passes run: {result.action_passes_run} / {settings.max_action_passes}",
            "",
            "TOOLS ALREADY PRESENT (no action needed):",
        ]
        if result.already_covered_tools:
            for t in result.already_covered_tools:
                lines.append(f"- {t}")
        else:
            lines.append("(none)")
        lines.append("")

        lines.append("APPLIED EDITS:")
        if result.applied_edits:
            for e in result.applied_edits:
                lines.append(f"- ORIGINAL: {e.original_text}")
                lines.append(f"  NEW:      {e.new_text}")
                lines.append(f"  Reason:   {e.reasoning}")
                lines.append("")
        else:
            lines.append("(none)")
            lines.append("")

        lines.append("REJECTED EDITS (fact-check/coverage/length failed, or user declined a tool):")
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
