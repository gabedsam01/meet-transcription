from app.version import APP_NAME, APP_VERSION, get_version_info


def test_defaults_are_safe_and_secret_free():
    info = get_version_info({})
    assert info["app"] == APP_NAME
    assert info["version"] == APP_VERSION
    assert info["commit"] == "unknown"
    assert info["build_time"] is None


def test_env_overrides_take_effect():
    info = get_version_info({
        "APP_VERSION": "9.9.9",
        "GIT_COMMIT": "abc123",
        "BUILD_TIME": "2026-06-05T00:00:00Z",
    })
    assert info["version"] == "9.9.9"
    assert info["commit"] == "abc123"
    assert info["build_time"] == "2026-06-05T00:00:00Z"


def test_git_sha_is_a_commit_fallback():
    info = get_version_info({"GIT_SHA": "deadbeef"})
    assert info["commit"] == "deadbeef"
