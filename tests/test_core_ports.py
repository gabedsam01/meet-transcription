from app.core.ports import (
    GoogleTokenRepository,
    JobRepository,
    Repositories,
    SettingsRepository,
    TranscriptRepository,
)


class _Stub:
    # Satisfies the method-name shape of every repository Protocol.
    def claim_next_pending_job(self, *a): ...
    def create_job(self, *a, **k): ...
    def get_job(self, *a): ...
    def mark_completed(self, *a, **k): ...
    def mark_failed(self, *a): ...
    def find_existing_job(self, *a): ...
    def reset_stale_processing_jobs(self, *a): ...
    def list_jobs_for_user(self, *a): ...
    def create(self, *a, **k): ...
    def get_by_job(self, *a): ...
    def get(self, *a): ...


def test_repositories_bundle_holds_four_repos():
    stub = _Stub()
    repos = Repositories(jobs=stub, transcripts=stub, settings=stub, google_tokens=stub)
    assert repos.jobs is stub
    assert repos.google_tokens is stub


def test_protocols_are_runtime_checkable():
    stub = _Stub()
    assert isinstance(stub, JobRepository)
    assert isinstance(stub, TranscriptRepository)
    assert isinstance(stub, SettingsRepository)
    assert isinstance(stub, GoogleTokenRepository)
