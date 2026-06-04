from app.transcription.deepgram_provider import DeepgramProvider


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def transcribe(self, path):
        self.calls.append(path)
        return self.response


def test_deepgram_provider_keeps_legacy_txt_and_normalizes():
    raw = {
        "results": {
            "utterances": [
                {"start": 1.0, "end": 2.0, "speaker": 0, "transcript": "Ola mundo."}
            ]
        }
    }
    provider = DeepgramProvider(_FakeClient(raw), model="nova-3", language="pt-BR")

    result = provider.transcribe("x.mp4", original_name="meet.mp4", file_id="id-9")

    # Legacy .txt formatting preserved (header + utterance line).
    assert "TRANSCRIÇÃO DA REUNIÃO" in result.text
    assert "Ola mundo." in result.text
    assert "meet.mp4" in result.text
    # Normalized payload stored in transcript_json; raw deepgram response retained.
    assert result.payload["provider"] == "deepgram"
    assert result.payload["engine"] == "deepgram"
    assert result.payload["raw"] == raw
    assert result.payload["text"] == "Ola mundo."
