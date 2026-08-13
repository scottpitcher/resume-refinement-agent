import pytest

from resume_tailor.claude_client import ClaudeClient, _extract_json


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_markdown_fences():
    text = '```json\n{"a": 1}\n```'
    assert _extract_json(text) == {"a": 1}


def test_extract_json_invalid_raises():
    with pytest.raises(Exception):
        _extract_json("not json")


def test_claude_client_reads_settings():
    client = ClaudeClient()
    assert client.model == "claude-sonnet-4-6"
