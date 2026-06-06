from __future__ import annotations

from dataclasses import replace

from app.transcription.provider_config import ModelSettings
from app.web.repositories import (
    DriveSettings,
    ExtensionToken,
    GoogleToken,
    Job,
    RepositoryBundle,
    User,
)

_FIXED_TS = "2026-06-03T00:00:00+00:00"


class InMemoryUsersRepository:
    def __init__(self) -> None:
        self._users: dict[int, User] = {}
        self._hashes: dict[int, str] = {}
        self._seq = 0

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def get_by_email(self, email: str) -> User | None:
        for user in self._users.values():
            if user.email == email:
                return user
        return None

    def get_by_id(self, user_id: int) -> User | None:
        return self._users.get(user_id)

    def get_password_hash(self, user_id: int) -> str | None:
        return self._hashes.get(user_id)

    def list_all(self) -> list[User]:
        return [self._users[key] for key in sorted(self._users)]

    def create(self, *, email: str, password_hash: str, role: str, name: str | None = None) -> User:
        if self.get_by_email(email) is not None:
            raise ValueError(f"email already exists: {email}")
        user_id = self._next_id()
        user = User(id=user_id, email=email, name=name, role=role, is_active=True)
        self._users[user_id] = user
        self._hashes[user_id] = password_hash
        return user

    def set_active(self, user_id: int, active: bool) -> None:
        self._users[user_id] = replace(self._users[user_id], is_active=active)

    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        self._hashes[user_id] = password_hash

    def set_google_identity(self, user_id: int, google_email: str | None, google_name: str | None) -> None:
        self._users[user_id] = replace(
            self._users[user_id], google_email=google_email, google_name=google_name
        )

    def ensure_admin(self, *, email: str, password_hash: str) -> User:
        existing = self.get_by_email(email)
        if existing is not None:
            self._users[existing.id] = replace(existing, role="admin", is_active=True)
            self._hashes[existing.id] = password_hash
            return self._users[existing.id]
        return self.create(email=email, password_hash=password_hash, role="admin")


class InMemoryGoogleTokensRepository:
    def __init__(self) -> None:
        self._tokens: dict[int, GoogleToken] = {}

    def get_for_user(self, user_id: int) -> GoogleToken | None:
        return self._tokens.get(user_id)

    def save_for_user(self, user_id: int, token: GoogleToken) -> None:
        self._tokens[user_id] = token


class InMemoryDeepgramCredentialsRepository:
    def __init__(self) -> None:
        self._keys: dict[int, str] = {}

    def get_encrypted_for_user(self, user_id: int) -> str | None:
        return self._keys.get(user_id)

    def save_for_user(self, user_id: int, api_key_encrypted: str) -> None:
        self._keys[user_id] = api_key_encrypted


class InMemoryProviderCredentialsRepository:
    """Mirrors the real adapter, including the legacy ``deepgram_credentials``
    read-fallback, so tests exercise the same backward-compatible behaviour."""

    def __init__(self, legacy_deepgram=None) -> None:
        self._creds: dict[tuple[int, str], str] = {}
        self._legacy_deepgram = legacy_deepgram

    def get_encrypted(self, user_id: int, provider: str) -> str | None:
        value = self._creds.get((user_id, provider))
        if value is None and provider == "deepgram" and self._legacy_deepgram is not None:
            return self._legacy_deepgram.get_encrypted_for_user(user_id)
        return value

    def save(self, user_id: int, provider: str, encrypted_api_key: str) -> None:
        self._creds[(user_id, provider)] = encrypted_api_key

    def list_encrypted(self, user_id: int) -> dict[str, str]:
        creds = {
            provider: value
            for (uid, provider), value in self._creds.items()
            if uid == user_id
        }
        if "deepgram" not in creds and self._legacy_deepgram is not None:
            legacy = self._legacy_deepgram.get_encrypted_for_user(user_id)
            if legacy is not None:
                creds["deepgram"] = legacy
        return creds


class InMemoryUserModelSettingsRepository:
    def __init__(self) -> None:
        self._by_user: dict[int, ModelSettings] = {}

    def get_for_user(self, user_id: int) -> ModelSettings | None:
        return self._by_user.get(user_id)

    def save_for_user(self, user_id: int, settings: ModelSettings) -> None:
        self._by_user[user_id] = settings


class InMemoryDriveSettingsRepository:
    def __init__(self) -> None:
        self._settings: dict[int, DriveSettings] = {}

    def get_for_user(self, user_id: int) -> DriveSettings | None:
        return self._settings.get(user_id)

    def save_for_user(self, user_id: int, settings: DriveSettings) -> None:
        self._settings[user_id] = settings


class InMemoryExtensionTokensRepository:
    """In-memory adapter for the per-user extension-token contract.

    Mirrors the Postgres adapter closely enough for web-layer tests:
    - ``create_for_user`` returns the persisted masked view (no raw token);
    - ``find_by_hash`` returns the row for the auth helper to apply the
      revoked/active policy;
    - ``touch`` is best-effort and never fatal.
    """

    def __init__(self) -> None:
        self._tokens: dict[int, ExtensionToken] = {}
        self._by_hash: dict[str, int] = {}
        self._seq = 0

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def list_for_user(self, user_id: int) -> list[ExtensionToken]:
        return sorted(
            (t for t in self._tokens.values() if t.user_id == user_id),
            key=lambda t: t.id,
            reverse=True,
        )

    def get_for_user(self, token_id: int, user_id: int) -> ExtensionToken | None:
        token = self._tokens.get(token_id)
        if token is None or token.user_id != user_id:
            return None
        return token

    def find_by_hash(self, token_hash: str) -> ExtensionToken | None:
        token_id = self._by_hash.get(token_hash)
        if token_id is None:
            return None
        return self._tokens.get(token_id)

    def create_for_user(
        self,
        user_id: int,
        *,
        name: str,
        token_hash: str,
        token_prefix: str,
    ) -> ExtensionToken:
        token = ExtensionToken(
            id=self._next_id(),
            user_id=user_id,
            name=(name or "Token").strip() or "Token",
            masked=token_prefix,
            created_at=_FIXED_TS,
            last_used_at=None,
            revoked_at=None,
        )
        self._tokens[token.id] = token
        self._by_hash[token_hash] = token.id
        return token

    def revoke_for_user(self, token_id: int, user_id: int) -> bool:
        token = self.get_for_user(token_id, user_id)
        if token is None or token.revoked_at is not None:
            return False
        self._tokens[token_id] = replace(token, revoked_at=_FIXED_TS)
        return True

    def touch(self, token_id: int) -> None:
        token = self._tokens.get(token_id)
        if token is not None:
            self._tokens[token_id] = replace(token, last_used_at=_FIXED_TS)


class InMemoryTranscriptionJobsRepository:
    def __init__(self) -> None:
        self._jobs: dict[int, Job] = {}
        self._seq = 0

    def create_job(
        self,
        *,
        user_id: int,
        status: str = "pending",
        source_file_id: str | None = None,
        source_file_name: str | None = None,
    ) -> Job:
        self._seq += 1
        job = Job(
            id=self._seq,
            user_id=user_id,
            status=status,
            source_file_id=source_file_id,
            source_file_name=source_file_name,
            created_at=_FIXED_TS,
            updated_at=_FIXED_TS,
        )
        self._jobs[job.id] = job
        return job

    def list_jobs_for_user(self, user_id: int, limit: int | None = None) -> list[Job]:
        jobs = sorted(
            (j for j in self._jobs.values() if j.user_id == user_id),
            key=lambda j: j.id,
            reverse=True,
        )
        return jobs[:limit] if limit else jobs

    def find_active_for_user(self, user_id: int) -> Job | None:
        for job in sorted(self._jobs.values(), key=lambda j: j.id, reverse=True):
            if job.user_id == user_id and job.status in ("pending", "processing"):
                return job
        return None


def build_fake_repositories() -> RepositoryBundle:
    deepgram_credentials = InMemoryDeepgramCredentialsRepository()
    return RepositoryBundle(
        users=InMemoryUsersRepository(),
        google_tokens=InMemoryGoogleTokensRepository(),
        deepgram_credentials=deepgram_credentials,
        drive_settings=InMemoryDriveSettingsRepository(),
        jobs=InMemoryTranscriptionJobsRepository(),
        # Faithful to the Postgres adapter: provider_credentials reads fall back to
        # the legacy deepgram_credentials row when no new row exists.
        provider_credentials=InMemoryProviderCredentialsRepository(
            legacy_deepgram=deepgram_credentials
        ),
        model_settings=InMemoryUserModelSettingsRepository(),
        extension_tokens=InMemoryExtensionTokensRepository(),
    )
