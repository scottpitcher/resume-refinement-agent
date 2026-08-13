from unittest.mock import MagicMock, patch

import pytest

from resume_tailor.pipeline import (
    ChangelogEntry,
    ResumeTailoringPipeline,
    RunResult,
    _find_tools_paragraph,
    _parse_tools_from_line,
    _tool_already_in_line,
)


def test_changelog_entry_fields():
    entry = ChangelogEntry(
        original_text="Built a thing", new_text="Built a scalable thing",
        verdict="approved", reasoning="Matches JD requirement for scale.",
    )
    assert entry.verdict == "approved"


def test_run_result_defaults_to_empty_lists():
    result = RunResult(
        company="Acme", role_slug="acme-swe", resume_tag="SE",
        resume_file_id="file123", resume_file_name="Resume - SE.docx",
        folder_id="folder123", final_score=0.9, action_passes_run=2,
    )
    assert result.applied_edits == []
    assert result.rejected_edits == []
    assert result.gaps == []
    assert result.already_covered_tools == []
    assert result.tools_confirmed == []
    assert result.tools_declined == []
    assert result.baseline_score == 0.0
    assert result.changelog_doc_id is None


@patch("resume_tailor.pipeline.ClaudeClient")
@patch("resume_tailor.pipeline.DocsClient")
@patch("resume_tailor.pipeline.DriveClient")
def test_get_working_folder_raises_when_not_found(mock_drive_cls, mock_docs_cls, mock_claude_cls):
    mock_drive = MagicMock()
    mock_drive.find_folder.return_value = None
    mock_drive_cls.return_value = mock_drive

    pipeline = ResumeTailoringPipeline()

    with pytest.raises(FileNotFoundError):
        pipeline._get_working_folder()


# ------------------------------------------------------------------
# Tools-line parsing/matching helpers
# ------------------------------------------------------------------

def test_find_tools_paragraph_matches_label():
    paragraphs = ["Scott Pitcher", "SKILLS", "Tools: Python, SQL", "Languages: Python"]
    assert _find_tools_paragraph(paragraphs) == "Tools: Python, SQL"


def test_find_tools_paragraph_is_case_insensitive():
    paragraphs = ["tools: Python, SQL"]
    assert _find_tools_paragraph(paragraphs) == "tools: Python, SQL"


def test_find_tools_paragraph_returns_none_when_absent():
    paragraphs = ["Scott Pitcher", "SKILLS", "Languages: Python"]
    assert _find_tools_paragraph(paragraphs) is None


def test_find_tools_paragraph_returns_none_when_ambiguous():
    paragraphs = ["Tools: Python, SQL", "Tools: R, Bash"]
    assert _find_tools_paragraph(paragraphs) is None


def test_parse_tools_from_line_splits_and_strips():
    assert _parse_tools_from_line("Tools: Python,  SQL ,R") == ["Python", "SQL", "R"]


def test_tool_already_in_line_is_exact_not_substring():
    # "SQ" is a substring of "SQL" but must NOT count as an exact-token match.
    assert _tool_already_in_line("SQ", ["SQL"]) is False
    assert _tool_already_in_line("SQL", ["SQL"]) is True
    # "R" being a substring of "Docker" must not false-positive either.
    assert _tool_already_in_line("R", ["Docker", "NoSQL"]) is False
    assert _tool_already_in_line("r", ["Docker", "R"]) is True  # case-insensitive exact match


# ------------------------------------------------------------------
# _run_tools_phase
# ------------------------------------------------------------------

@patch("resume_tailor.pipeline.ClaudeClient")
@patch("resume_tailor.pipeline.DocsClient")
@patch("resume_tailor.pipeline.DriveClient")
def test_run_tools_phase_applies_single_replace_call_for_all_confirmed(
    mock_drive_cls, mock_docs_cls, mock_claude_cls
):
    mock_docs = MagicMock()
    mock_docs.get_paragraphs.return_value = ["Tools: Python, SQL"]
    mock_docs.count_occurrences.return_value = 1
    mock_docs_cls.return_value = mock_docs

    pipeline = ResumeTailoringPipeline()

    jd_tools = [
        {"name": "Python", "importance": "required", "context": ""},  # already present
        {"name": "TensorFlow", "importance": "required", "context": "deep learning"},
        {"name": "Keras", "importance": "preferred", "context": "deep learning"},
    ]

    def confirm_tools(missing):
        assert {t["name"] for t in missing} == {"TensorFlow", "Keras"}
        return ["TensorFlow"]  # decline Keras

    result = pipeline._run_tools_phase("resume123", jd_tools, confirm_tools)

    mock_docs.replace_text.assert_called_once_with(
        "resume123", "Tools: Python, SQL", "Tools: Python, SQL, TensorFlow"
    )
    assert result["already_present"] == ["Python"]
    assert result["confirmed"] == ["TensorFlow"]
    assert result["declined"] == ["Keras"]
    assert len(result["applied"]) == 1
    assert result["applied"][0].verdict == "user-confirmed"
    assert len(result["rejected"]) == 1
    assert result["rejected"][0].verdict == "user-declined"


@patch("resume_tailor.pipeline.ClaudeClient")
@patch("resume_tailor.pipeline.DocsClient")
@patch("resume_tailor.pipeline.DriveClient")
def test_run_tools_phase_skips_gracefully_when_no_tools_line(
    mock_drive_cls, mock_docs_cls, mock_claude_cls
):
    mock_docs = MagicMock()
    mock_docs.get_paragraphs.return_value = ["Scott Pitcher", "No skills section here"]
    mock_docs_cls.return_value = mock_docs

    pipeline = ResumeTailoringPipeline()
    result = pipeline._run_tools_phase(
        "resume123", [{"name": "Python", "importance": "required", "context": ""}],
        confirm_tools=lambda missing: [],
    )

    mock_docs.replace_text.assert_not_called()
    assert result["applied"] == []
    assert any("Tools:" in g for g in result["gaps"])


# ------------------------------------------------------------------
# _run_action_phrase_phase
# ------------------------------------------------------------------

def _make_pipeline_with_mocks():
    with patch("resume_tailor.pipeline.ClaudeClient"), \
         patch("resume_tailor.pipeline.DocsClient"), \
         patch("resume_tailor.pipeline.DriveClient"):
        pipeline = ResumeTailoringPipeline()
    pipeline.claude = MagicMock()
    pipeline.docs = MagicMock()
    return pipeline


def _passing_fact_check(phrase_after=None):
    return {
        "verdict": "pass",
        "reasoning": "ok",
        "action_phrases_covered_before": [],
        "action_phrases_covered_after": [phrase_after] if phrase_after else [],
    }


def test_run_action_phrase_phase_stops_on_clean_pass():
    pipeline = _make_pipeline_with_mocks()
    pipeline.docs.get_paragraphs.return_value = ["Some bullet."]
    pipeline.docs.count_occurrences.return_value = 1

    jd_requirements = {"tools": [], "action_phrases": ["Lead a migration"]}

    # Pass 1: proposes and applies an edit that covers the phrase. Pass 2: nothing left
    # to propose since the phrase is now marked covered -- propose_edits shouldn't even
    # be asked again for it, so returning empty edits here just guards against that.
    pipeline.claude.propose_edits.side_effect = [
        {"edits": [{"original_text": "Some bullet.", "new_text": "Led a migration."}], "gaps": []},
    ]
    pipeline.claude.fact_check_edit.return_value = _passing_fact_check(phrase_after="Lead a migration")

    result = pipeline._run_action_phrase_phase(
        "resume123", jd_requirements, ["Some bullet."], max_length=1000, current_length=20,
        covered_phrases=set(),
    )

    assert result["passes_run"] == 2  # pass 1 applies, pass 2 is clean (nothing left to attempt)
    assert len(result["applied"]) == 1
    pipeline.docs.replace_text.assert_called_once()


def test_run_action_phrase_phase_respects_pass_cap(monkeypatch):
    from resume_tailor.config import settings
    monkeypatch.setattr(settings, "max_action_passes", 2)

    pipeline = _make_pipeline_with_mocks()
    pipeline.docs.get_paragraphs.return_value = ["Some bullet."]
    pipeline.docs.count_occurrences.return_value = 1

    jd_requirements = {"tools": [], "action_phrases": ["Lead a migration"]}

    # Every pass proposes a *new* variant that gains a *different, unrelated* phrase --
    # so the target phrase never gets marked covered and the phrase is reattempted every
    # pass, meaning it never produces a clean pass on its own; the cap must still stop it.
    pipeline.claude.propose_edits.return_value = {
        "edits": [{"original_text": "Some bullet.", "new_text": "Reworded bullet."}], "gaps": [],
    }
    pipeline.claude.fact_check_edit.return_value = _passing_fact_check(phrase_after="Some other phrase")

    result = pipeline._run_action_phrase_phase(
        "resume123", jd_requirements, ["Some bullet."], max_length=10_000, current_length=20,
        covered_phrases=set(),
    )

    assert result["passes_run"] == 2


def test_action_phrase_coverage_gate_requires_target_phrase():
    pipeline = _make_pipeline_with_mocks()
    pipeline.docs.get_paragraphs.return_value = ["Some bullet."]
    pipeline.docs.count_occurrences.return_value = 1

    jd_requirements = {"tools": [], "action_phrases": ["Lead a migration"]}

    pipeline.claude.propose_edits.side_effect = [
        {"edits": [{"original_text": "Some bullet.", "new_text": "Reworded bullet."}], "gaps": []},
        {"edits": [], "gaps": []},
    ]
    # Covers an unrelated phrase, not the target -- must be rejected, not applied.
    pipeline.claude.fact_check_edit.return_value = _passing_fact_check(phrase_after="Some other phrase")

    result = pipeline._run_action_phrase_phase(
        "resume123", jd_requirements, ["Some bullet."], max_length=1000, current_length=20,
        covered_phrases=set(),
    )

    assert result["applied"] == []
    assert len(result["rejected"]) == 1
    assert "doesn't newly cover the target phrase" in result["rejected"][0].reasoning
    pipeline.docs.replace_text.assert_not_called()


def test_action_phrase_phase_does_not_abort_pass_on_single_rejection():
    pipeline = _make_pipeline_with_mocks()
    pipeline.docs.get_paragraphs.return_value = ["Bullet one.", "Bullet two."]
    pipeline.docs.count_occurrences.return_value = 1

    jd_requirements = {"tools": [], "action_phrases": ["Phrase A", "Phrase B"]}

    def propose_side_effect(**kwargs):
        gap_summary = kwargs["gap_summary"]
        if "Phrase A" in gap_summary:
            return {"edits": [{"original_text": "Bullet one.", "new_text": "Bad edit."}], "gaps": []}
        return {"edits": [{"original_text": "Bullet two.", "new_text": "Good edit for Phrase B."}], "gaps": []}

    pipeline.claude.propose_edits.side_effect = propose_side_effect

    def fact_check_side_effect(original_text, new_text, action_phrases):
        if original_text == "Bullet one.":
            return {"verdict": "reject", "reasoning": "fabricated", "action_phrases_covered_before": [], "action_phrases_covered_after": []}
        return _passing_fact_check(phrase_after="Phrase B")

    pipeline.claude.fact_check_edit.side_effect = fact_check_side_effect

    result = pipeline._run_action_phrase_phase(
        "resume123", jd_requirements, ["Bullet one.", "Bullet two."], max_length=1000, current_length=30,
        covered_phrases=set(),
    )

    # Phrase A's rejection must not stop Phrase B from being attempted in the same pass.
    assert len(result["rejected"]) == 1
    assert len(result["applied"]) == 1
    assert result["applied"][0].new_text == "Good edit for Phrase B."
