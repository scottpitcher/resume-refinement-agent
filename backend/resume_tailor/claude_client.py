"""
Wrapper around the Anthropic API for every judgment call the pipeline needs:
picking a resume, extracting JD requirements, proposing edits, fact-checking
each edit, and scoring coverage.

The fact-check call is always made separately from the edit-proposal call
(different call, different prompt) so the model isn't grading its own
homework in the same breath it wrote it.
"""

import json
import re
import time
from typing import Any, Dict, List

import anthropic

from . import prompts
from .config import settings


def _extract_json(text: str) -> Dict[str, Any]:
    """Strips markdown fences and any surrounding prose, then parses the first JSON
    object/array found. Some models (Haiku especially) add a preamble or trailing
    remark even when told "JSON only", so this doesn't assume the whole string is
    valid JSON -- it finds the first '{' or '[' and decodes from there, ignoring
    anything after the JSON value ends. Raises if no JSON value is found at all."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    candidates = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
    if not candidates:
        raise json.JSONDecodeError("No JSON object/array found in response", cleaned, 0)

    obj, _ = json.JSONDecoder().raw_decode(cleaned, min(candidates))
    return obj


class ClaudeClient:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.claude_model

    def _call(self, system: str, user_content: str, max_tokens: int = 2000) -> Dict[str, Any]:
        attempts = 3
        for attempt in range(1, attempts + 1):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
            text = "".join(block.text for block in response.content if block.type == "text")
            try:
                return _extract_json(text)
            except json.JSONDecodeError as e:
                if attempt == attempts:
                    snippet = text[:500] + ("..." if len(text) > 500 else "")
                    raise ValueError(
                        f"Model ({self.model}) did not return parseable JSON after {attempts} attempts. "
                        f"Last raw response: {snippet!r}"
                    ) from e
                time.sleep(attempt)

    def pick_resume(self, jd_text: str, resume_options: List[Dict[str, str]]) -> Dict[str, Any]:
        """resume_options: [{"tag": "DS", "excerpt": "..."}, ...]"""
        options_str = "\n\n".join(
            f"--- {opt['tag']} ---\n{opt['excerpt']}" for opt in resume_options
        )
        user_content = f"JOB DESCRIPTION:\n{jd_text}\n\nRESUME OPTIONS:\n{options_str}"
        return self._call(prompts.PICK_RESUME_SYSTEM, user_content)

    def get_role_slug(self, jd_text: str) -> Dict[str, Any]:
        return self._call(prompts.ROLE_SLUG_SYSTEM, f"JOB DESCRIPTION:\n{jd_text}")

    def extract_jd_requirements(self, jd_text: str) -> Dict[str, Any]:
        return self._call(prompts.EXTRACT_JD_SYSTEM, f"JOB DESCRIPTION:\n{jd_text}", max_tokens=2000)

    def propose_edits(
        self,
        jd_requirements: Dict[str, Any],
        current_paragraphs: List[str],
        original_facts: List[str],
        gap_summary: str,
        num_edits: int,
        already_tried: List[Dict[str, str]] = (),
    ) -> Dict[str, Any]:
        already_tried_block = ""
        if already_tried:
            already_tried_block = (
                "\n\nEDITS ALREADY TRIED AND REJECTED THIS RUN (do not repeat these or propose "
                "trivial variations of them -- find a different bullet, or an honestly-supportable "
                "angle you haven't tried, or say so in gaps if nothing else is left):\n"
                + "\n".join(
                    f"- TRIED: {t['original_text']}\n  REJECTED BECAUSE: {t['reason']}"
                    for t in already_tried
                )
            )
        user_content = (
            f"JD REQUIREMENTS:\n{json.dumps(jd_requirements, indent=2)}\n\n"
            f"CURRENT RESUME (paragraphs/bullets, in order):\n"
            + "\n".join(f"[{i}] {p}" for i, p in enumerate(current_paragraphs))
            + f"\n\nORIGINAL ATOMIC FACT RECORD (ground truth, never contradict this):\n"
            + "\n".join(f"- {f}" for f in original_facts)
            + f"\n\nCURRENT GAP ANALYSIS:\n{gap_summary}"
            + already_tried_block
            + f"\n\nPropose up to {num_edits} edits this round, highest-yield first."
        )
        # Each edit carries four verbose string fields (original/new text, based_on_fact,
        # reasoning) -- a flat cap was getting cut off mid-response for the full num_edits.
        max_tokens = 1000 + 800 * num_edits
        return self._call(prompts.PROPOSE_EDITS_SYSTEM, user_content, max_tokens=max_tokens)

    def fact_check_edit(self, original_text: str, new_text: str, action_phrases: List[str]) -> Dict[str, Any]:
        user_content = (
            f"ORIGINAL:\n{original_text}\n\nPROPOSED:\n{new_text}\n\n"
            f"JD ACTION PHRASES:\n" + "\n".join(f"- {p}" for p in action_phrases)
        )
        return self._call(prompts.FACT_CHECK_SYSTEM, user_content, max_tokens=600)

    def score_coverage(self, jd_requirements: Dict[str, Any], resume_paragraphs: List[str]) -> Dict[str, Any]:
        user_content = (
            f"JD REQUIREMENTS:\n{json.dumps(jd_requirements, indent=2)}\n\n"
            f"CURRENT RESUME:\n" + "\n".join(resume_paragraphs)
        )
        return self._call(prompts.SCORE_SYSTEM, user_content, max_tokens=1500)
