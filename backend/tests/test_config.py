from resume_tailor.config import Settings


def test_settings_loads_required_and_defaults(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

    settings = Settings()

    assert settings.anthropic_api_key == "sk-ant-test-key"
    assert settings.google_client_id == "test-client-id"
    assert settings.claude_model == "claude-sonnet-4-6"
    assert settings.max_action_passes == 5
    assert settings.score_threshold == 0.85


def test_settings_raises_when_required_var_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import pytest

    with pytest.raises(EnvironmentError):
        Settings()
