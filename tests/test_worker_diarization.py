from datetime import datetime, timezone

from app.core.models import JobStatus
from app.diarization.config import DiarizationConfig
from app.diarization.provider import DiarizationProbes, SpeakerTurn
from app.recordings import (
    RecordingMetadata,
    new_recording_id,
    recording_path,
    source_file_id_for,
    write_metadata,
)
from app.transcription.config import TranscriptionConfig
from app.transcription.local_validation import ValidationProbes
from app.transcription.provider import TranscriptionResult
from app.worker.processor import JobProcessor
from tests.support import make_worker_container


def _local_cfg():
    return TranscriptionConfig.from_env(
        {"LOCAL_TRANSCRIPTION_ENABLED": "true", "LOCAL_TRANSCRIPTION_ENGINE": "faster-whisper"}
    )


def _tx_probes():
    return ValidationProbes(
        module_available=lambda name: True, path_exists=lambda p: True,
        is_executable=lambda p: True,
    )


def _ok_runner(cmd):
    class _R:
        returncode = 0
        stdout = ""
        stderr = ""
    return _R()


class _SegLocalProvider:
    """Returns two segments with explicit timings so speakers can be assigned."""

    def transcribe(self, source_path, *, original_name, file_id):
        return TranscriptionResult(
            text="Olá. Tudo bem.",
            payload={
                "provider": "local", "engine": "faster-whisper", "model": "small",
                "language": "pt", "text": "Olá. Tudo bem.",
                "segments": [
                    {"start": 0.0, "end": 2.0, "speaker": None, "text": "Olá."},
                    {"start": 2.0, "end": 4.0, "speaker": None, "text": "Tudo bem."},
                ],
                "words": [], "utterances": [], "raw": {},
            },
        )


class _FakeDiar:
    def __init__(self, turns):
        self.turns = turns
        self.calls = []

    def diarize(self, audio_path, *, min_speakers=None, max_speakers=None):
        self.calls.append(str(audio_path))
        return self.turns


def _now():
    return datetime.now(timezone.utc)


def _seed_upload(recdir):
    recdir.mkdir(parents=True, exist_ok=True)
    rid = new_recording_id()
    media = recording_path(recdir, rid, ".webm")
    media.write_bytes(b"webm")
    write_metadata(recdir, RecordingMetadata(recording_id=rid, filename=media.name))
    return rid


def _container(tmp_path, recdir, **over):
    return make_worker_container(
        tmp_path,
        transcription_config=_local_cfg(), transcription_probes=_tx_probes(),
        build_local_provider=lambda cfg: _SegLocalProvider(),
        recordings_dir=recdir, audio_runner=_ok_runner, **over,
    )


def _claim_upload(repos, rid):
    repos.jobs.create_job(7, source_file_id_for(rid), "Weekly", _now())
    return repos.jobs.claim_next_pending_job("w1", _now())


def test_diarization_disabled_leaves_speakers_null(tmp_path):
    recdir = tmp_path / "recordings"
    rid = _seed_upload(recdir)
    container = _container(tmp_path, recdir)  # no diarization_config -> disabled
    job = _claim_upload(container.repositories, rid)

    JobProcessor(container).process(job)

    transcript = container.repositories.transcripts.get_by_job(job.id)
    assert all(seg["speaker"] is None for seg in transcript.json_payload["segments"])


def test_diarization_assigns_speakers_and_rerenders_text(tmp_path):
    recdir = tmp_path / "recordings"
    rid = _seed_upload(recdir)
    turns = [SpeakerTurn(0.0, 2.0, "SPEAKER_00"), SpeakerTurn(2.0, 4.0, "SPEAKER_01")]
    diar = _FakeDiar(turns)
    container = _container(
        tmp_path, recdir,
        diarization_config=DiarizationConfig.from_env({
            "DIARIZATION_ENABLED": "true", "DIARIZATION_ENGINE": "pyannote",
            "DIARIZATION_AUTH_TOKEN": "hf-secret-token",
        }),
        diarization_probes=DiarizationProbes(module_available=lambda n: True),
        build_diarization_provider=lambda cfg: diar,
    )
    job = _claim_upload(container.repositories, rid)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.COMPLETED.value
    assert diar.calls, "diarization provider was invoked"
    transcript = container.repositories.transcripts.get_by_job(job.id)
    speakers = [seg["speaker"] for seg in transcript.json_payload["segments"]]
    assert speakers == ["SPEAKER_00", "SPEAKER_01"]
    # Text was re-rendered so speaker labels appear in the .txt download.
    assert "SPEAKER_00" in transcript.text
    # The auth token must never leak into the stored transcript.
    assert "hf-secret-token" not in transcript.text


def test_diarization_required_but_invalid_fails_job(tmp_path):
    recdir = tmp_path / "recordings"
    rid = _seed_upload(recdir)
    container = _container(
        tmp_path, recdir,
        diarization_config=DiarizationConfig.from_env({
            "DIARIZATION_ENABLED": "true", "DIARIZATION_ENGINE": "pyannote",
            "DIARIZATION_REQUIRED": "true",  # no auth token -> invalid
        }),
        diarization_probes=DiarizationProbes(module_available=lambda n: True),
    )
    job = _claim_upload(container.repositories, rid)

    JobProcessor(container).process(job)

    done = container.repositories.jobs.get_job(job.id)
    assert done.status == JobStatus.FAILED.value
    assert "Diariza" in done.error_message  # friendly, secret-free
    assert container.repositories.transcripts.get_by_job(job.id) is None
