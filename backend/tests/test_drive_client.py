from unittest.mock import MagicMock, patch

from resume_tailor.drive_client import DriveClient, _escape


def test_escape_single_quotes():
    assert _escape("O'Brien") == "O\\'Brien"


def test_escape_no_quotes_unchanged():
    assert _escape("plain name") == "plain name"


@patch("resume_tailor.drive_client.get_drive_service")
def test_find_folder_returns_first_match(mock_get_service):
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    mock_service.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "abc123", "name": "2026 Recruiting"}]
    }

    client = DriveClient()
    result = client.find_folder("2026 Recruiting")

    assert result == {"id": "abc123", "name": "2026 Recruiting"}


@patch("resume_tailor.drive_client.get_drive_service")
def test_find_folder_returns_none_when_missing(mock_get_service):
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service
    mock_service.files.return_value.list.return_value.execute.return_value = {"files": []}

    client = DriveClient()
    result = client.find_folder("Nonexistent")

    assert result is None
