from unittest.mock import MagicMock, patch

import pytest

from resume_tailor.pipeline import ChangelogEntry, ResumeTailoringPipeline, RunResult


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
        folder_id="folder123", final_score=0.9, iterations_run=2,
    )
    assert result.applied_edits == []
    assert result.rejected_edits == []
    assert result.gaps == []
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
