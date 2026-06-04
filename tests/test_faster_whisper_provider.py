from app.transcription.config import TranscriptionConfig
from app.transcription.faster_whisper_provider import FasterWhisperProvider


def _cfg(**over):
    env = {
        "LOCAL_TRANSCRIPTION_ENABLED": "true",
        "LOCAL_TRANSCRIPTION_ENGINE": "faster-whisper",
        "LOCAL_TRANSCRIPTION_MODEL": "small",
        "LOCAL_TRANSCRIPTION_COMPUTE_TYPE": "int8",
    }
    env.update(over)
    return TranscriptionConfig.from_env(env)


class _Seg:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class _Info:
    language = "pt"
    language_probability = 0.99


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return (iter([_Seg(0.0, 2.0, "Olá"), _Seg(2.0, 4.0, "mundo")]), _Info())


def test_provider_normalizes_segments_and_text(tmp_path):
    fake = _FakeModel()
    provider = FasterWhisperProvider(_cfg(LOCAL_TRANSCRIPTION_LANGUAGE="auto"), model_factory=lambda: fake)

    result = provider.transcribe(tmp_path / "x.mp4", original_name="x.mp4", file_id="id1")

    assert result.payload["provider"] == "local"
    assert result.payload["engine"] == "faster-whisper"
    assert result.payload["model"] == "small"
    assert result.payload["text"] == "Olá mundo"
    assert result.payload["language"] == "pt"  # detected language from info
    assert result.payload["segments"][0]["speaker"] is None
    # The rendered .txt lists one segment per line ("[00:00:00] Olá" / "[00:00:02] mundo").
    assert "Olá" in result.text and "mundo" in result.text


def test_auto_language_passes_none_to_model(tmp_path):
    fake = _FakeModel()
    provider = FasterWhisperProvider(_cfg(LOCAL_TRANSCRIPTION_LANGUAGE="auto"), model_factory=lambda: fake)
    provider.transcribe(tmp_path / "x.mp4", original_name="x", file_id="i")
    assert fake.calls[0][1]["language"] is None


def test_explicit_language_is_forwarded(tmp_path):
    fake = _FakeModel()
    provider = FasterWhisperProvider(_cfg(LOCAL_TRANSCRIPTION_LANGUAGE="pt"), model_factory=lambda: fake)
    provider.transcribe(tmp_path / "x.mp4", original_name="x", file_id="i")
    assert fake.calls[0][1]["language"] == "pt"


def test_model_is_built_once_and_cached(tmp_path):
    built = []

    def factory():
        model = _FakeModel()
        built.append(model)
        return model

    provider = FasterWhisperProvider(_cfg(), model_factory=factory)
    provider.transcribe(tmp_path / "a.mp4", original_name="a", file_id="i")
    provider.transcribe(tmp_path / "b.mp4", original_name="b", file_id="j")
    assert len(built) == 1  # heavy model loaded a single time
