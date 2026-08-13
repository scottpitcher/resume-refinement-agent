from resume_tailor import auth


def test_client_config_shape():
    config = auth._client_config()

    assert "installed" in config
    assert "client_id" in config["installed"]
    assert "client_secret" in config["installed"]
