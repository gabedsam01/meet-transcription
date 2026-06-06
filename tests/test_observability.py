import json
import logging

from app.observability import (
    REDACTED,
    JsonLogFormatter,
    configure_logging,
    log_event,
    redact,
    resolve_log_format,
)


def test_redact_masks_sensitive_keys_only():
    clean = redact({
        "job_id": 1, "user_id": 2, "provider": "deepgram",
        "api_key": "sk-secret", "deepgram_key": "x", "token": "abc",
        "authorization": "Bearer y", "password": "p",
    })
    assert clean["job_id"] == 1
    assert clean["user_id"] == 2
    assert clean["provider"] == "deepgram"
    for sensitive in ("api_key", "deepgram_key", "token", "authorization", "password"):
        assert clean[sensitive] == REDACTED


def test_redact_keeps_empty_sensitive_values_unmasked():
    assert redact({"token": None})["token"] is None
    assert redact({"token": ""})["token"] == ""


def test_log_event_emits_one_record_and_never_leaks_secret():
    logger = logging.getLogger("test.events")
    logger.setLevel(logging.INFO)
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[assignment]
    logger.addHandler(handler)
    try:
        log_event(
            "transcription.completed", logger=logger,
            job_id=7, user_id=1, provider="deepgram", api_key="sk-leak",
        )
    finally:
        logger.removeHandler(handler)
    assert len(records) == 1
    rec = records[0]
    assert "event=transcription.completed" in rec.getMessage()
    assert "job_id=7" in rec.getMessage()
    assert "sk-leak" not in rec.getMessage()
    assert rec.event == "transcription.completed"
    assert rec.event_fields["api_key"] == REDACTED


def test_json_formatter_outputs_redacted_json_line():
    rec = logging.LogRecord("app.events", logging.INFO, __file__, 1, "msg", None, None)
    rec.event = "transcription.failed"
    rec.event_fields = {"job_id": 3, "secret": "nope", "error_code": "deepgram_key_required"}
    out = json.loads(JsonLogFormatter().format(rec))
    assert out["event"] == "transcription.failed"
    assert out["job_id"] == 3
    assert out["error_code"] == "deepgram_key_required"
    assert out["secret"] == REDACTED
    assert out["level"] == "INFO"
    assert "nope" not in json.dumps(out)


def test_resolve_log_format():
    assert resolve_log_format({"LOG_FORMAT": "json"}) == "json"
    assert resolve_log_format({"LOG_FORMAT": "TEXT"}) == "text"
    assert resolve_log_format({"LOG_FORMAT": "weird"}) == "text"
    assert resolve_log_format({}) == "text"


def test_redact_recurses_into_nested_mappings_and_lists():
    clean = redact({
        "job_id": 1,
        "nested": {"user_id": 2, "api_key": "sk-secret"},
        "items": [{"token": "abc"}, {"plain": "ok"}],
    })
    assert clean["job_id"] == 1
    assert clean["nested"]["user_id"] == 2
    assert clean["nested"]["api_key"] == REDACTED
    assert clean["items"][0]["token"] == REDACTED
    assert clean["items"][1]["plain"] == "ok"


def test_configure_logging_manages_a_single_stable_handler():
    # configure_logging owns exactly one root handler and reuses it across calls
    # (it never stacks duplicates), updating only the formatter. The test snapshots
    # and restores the global root logger so it leaks no state into other tests.
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_cached = getattr(configure_logging, "_handler", None)
    try:
        configure_logging(fmt="text")
        handler = configure_logging._handler
        configure_logging(fmt="json")
        assert configure_logging._handler is handler  # same handler reused
        assert isinstance(handler.formatter, JsonLogFormatter)  # formatter updated in place
        assert sum(1 for h in root.handlers if h is handler) == 1  # not duplicated
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)
        if saved_cached is not None:
            configure_logging._handler = saved_cached
        elif hasattr(configure_logging, "_handler"):
            delattr(configure_logging, "_handler")
