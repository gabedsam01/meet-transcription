import pytest

from app.config import Settings, parse_bool


def test_parse_bool_accepts_expected_values():
    assert parse_bool("true") is True
    assert parse_bool("1") is True
    assert parse_bool("yes") is True
    assert parse_bool("false") is False
    assert parse_bool("0") is False
    assert parse_bool("no") is False


def test_parse_bool_rejects_invalid_value():
    with pytest.raises(ValueError, match="Invalid boolean"):
        parse_bool("maybe")


def test_settings_from_env_parses_required_values(tmp_path):
    env = {
        "DEEPGRAM_API_KEY": "dg-key",
        "GOOGLE_AUTH_MODE": "service_account",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "/app/secrets/service-account.json",
        "SOURCE_DRIVE_FOLDER_ID": "source",
        "DESTINATION_DRIVE_FOLDER_ID": "destination",
        "POLL_INTERVAL_SECONDS": "300",
        "TMP_DIR": str(tmp_path / "tmp"),
        "STATE_FILE": str(tmp_path / "data" / "processed_files.json"),
        "DEEPGRAM_MODEL": "nova-3",
        "DEEPGRAM_LANGUAGE": "pt-BR",
        "DEEPGRAM_SMART_FORMAT": "true",
        "DEEPGRAM_PUNCTUATE": "true",
        "DEEPGRAM_DIARIZE": "true",
        "DEEPGRAM_UTTERANCES": "true",
    }

    settings = Settings.from_env(env)

    assert settings.deepgram_api_key == "dg-key"
    assert settings.poll_interval_seconds == 300
    assert settings.deepgram_smart_format is True


def test_settings_from_env_parses_oauth_mode_and_failure_limits(tmp_path):
    env = {
        "DEEPGRAM_API_KEY": "dg-key",
        "GOOGLE_AUTH_MODE": "oauth",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "/app/secrets/service-account.json",
        "GOOGLE_OAUTH_CLIENT_SECRETS_FILE": "/app/secrets/oauth-client.json",
        "GOOGLE_OAUTH_TOKEN_FILE": "/app/secrets/token.json",
        "SOURCE_DRIVE_FOLDER_ID": "source",
        "DESTINATION_DRIVE_FOLDER_ID": "destination",
        "POLL_INTERVAL_SECONDS": "300",
        "TMP_DIR": str(tmp_path / "tmp"),
        "STATE_FILE": str(tmp_path / "data" / "processed_files.json"),
        "MAX_PROCESSING_ATTEMPTS": "2",
        "FAILED_RETRY_AFTER_SECONDS": "86400",
        "DEEPGRAM_MODEL": "nova-3",
        "DEEPGRAM_LANGUAGE": "pt-BR",
        "DEEPGRAM_SMART_FORMAT": "true",
        "DEEPGRAM_PUNCTUATE": "true",
        "DEEPGRAM_DIARIZE": "true",
        "DEEPGRAM_UTTERANCES": "true",
    }

    settings = Settings.from_env(env)

    assert settings.google_auth_mode == "oauth"
    assert settings.google_oauth_client_secrets_file.name == "oauth-client.json"
    assert settings.google_oauth_token_file.name == "token.json"
    assert settings.max_processing_attempts == 2
    assert settings.failed_retry_after_seconds == 86400
