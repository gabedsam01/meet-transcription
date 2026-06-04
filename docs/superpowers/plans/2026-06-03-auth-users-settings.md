# Auth Users Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-admin web UI into an admin-controlled multiuser app on PostgreSQL (consumed via repository Protocols + in-memory fakes), with per-user Google OAuth, encrypted per-user Deepgram keys, Drive-settings-by-URL, roles and admin user management — with NO SQLite anywhere.

**Architecture:** This branch defines the persistence **contract** (`Protocol`s + storage-agnostic dataclasses in `app/web/repositories.py`) and wires it via dependency injection. Production builds Postgres-backed repos from `postgres-core` through `build_repositories`; tests inject dict-backed fakes (`tests/fakes.py`). Crypto (Fernet) lives in application stores (`TokenStore`, `DeepgramKeyStore`) so repos persist ciphertext. The web run-once only **enqueues** a pending job; real processing is the `postgres-worker` branch.

**Tech Stack:** FastAPI, Starlette SessionMiddleware, Jinja2, `cryptography` (Fernet), `bcrypt` (password hashing), `requests`, pytest + `TestClient`.

**Branch/worktree:** `feat/auth-users-settings` at `…/meet-transcription-auth`. Commit after every task. No push.

**Spec:** `docs/superpowers/specs/2026-06-03-auth-users-settings-design.md`.

> **Note on hashing:** spec said `passlib[bcrypt]`; this plan uses the `bcrypt` library directly (within the user's stated "bcrypt/passlib"). It avoids the passlib+bcrypt≥4.1 `__about__` warning and needs no version pin.

> **Validation note:** the web app cannot fully boot without `postgres-core` (by design — `build_repositories` raises `RepositoryBackendUnavailable`). All tests use fakes; `docker compose config`/`build` don't run the app, so the required validations pass.

---

## Task 1: Repository contract (Protocols + dataclasses + DI entrypoint)

**Files:**
- Create: `app/web/repositories.py`
- Test: `tests/test_repositories.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repositories.py
import pytest

from app.web.repositories import (
    DriveSettings,
    GoogleToken,
    Job,
    RepositoryBackendUnavailable,
    User,
    build_repositories,
)


def test_domain_dataclasses_are_constructible():
    user = User(id=1, email="a@b.com", name="A", role="admin", is_active=True)
    assert user.google_email is None
    token = GoogleToken(
        access_token="x", refresh_token=None, token_uri="u", client_id="c",
        client_secret=None, scopes="s", expiry=None,
    )
    assert token.access_token == "x"
    ds = DriveSettings(
        source_drive_folder_url="url", source_drive_folder_id="id",
        destination_drive_folder_url=None, destination_drive_folder_id=None,
        save_copy_to_drive=False,
    )
    assert ds.source_drive_folder_id == "id"
    job = Job(id=1, user_id=1, status="pending")
    assert job.attempts == 0 and job.source_file_id is None


def test_build_repositories_raises_when_postgres_backend_absent():
    class S:
        database_url = "postgresql://nope"

    with pytest.raises(RepositoryBackendUnavailable):
        build_repositories(S())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repositories.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.web.repositories'`

- [ ] **Step 3: Write the implementation**

```python
# app/web/repositories.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str | None
    role: str  # "admin" | "user"
    is_active: bool
    google_email: str | None = None
    google_name: str | None = None


@dataclass(frozen=True)
class GoogleToken:
    """Sensitive fields hold ciphertext at the repository boundary."""
    access_token: str
    refresh_token: str | None
    token_uri: str
    client_id: str
    client_secret: str | None
    scopes: str
    expiry: str | None


@dataclass(frozen=True)
class DriveSettings:
    source_drive_folder_url: str
    source_drive_folder_id: str
    destination_drive_folder_url: str | None
    destination_drive_folder_id: str | None
    save_copy_to_drive: bool


@dataclass(frozen=True)
class Job:
    id: int
    user_id: int
    status: str
    source_file_id: str | None = None
    source_file_name: str | None = None
    transcript_drive_file_id: str | None = None
    error_message: str | None = None
    attempts: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    processed_at: str | None = None


class UsersRepository(Protocol):
    def get_by_email(self, email: str) -> User | None: ...
    def get_by_id(self, user_id: int) -> User | None: ...
    def get_password_hash(self, user_id: int) -> str | None: ...
    def list_all(self) -> list[User]: ...
    def create(self, *, email: str, password_hash: str, role: str, name: str | None = None) -> User: ...
    def set_active(self, user_id: int, active: bool) -> None: ...
    def set_password_hash(self, user_id: int, password_hash: str) -> None: ...
    def set_google_identity(self, user_id: int, google_email: str | None, google_name: str | None) -> None: ...
    def ensure_admin(self, *, email: str, password_hash: str) -> User: ...


class GoogleTokensRepository(Protocol):
    def get_for_user(self, user_id: int) -> GoogleToken | None: ...
    def save_for_user(self, user_id: int, token: GoogleToken) -> None: ...


class DeepgramCredentialsRepository(Protocol):
    def get_encrypted_for_user(self, user_id: int) -> str | None: ...
    def save_for_user(self, user_id: int, api_key_encrypted: str) -> None: ...


class DriveSettingsRepository(Protocol):
    def get_for_user(self, user_id: int) -> DriveSettings | None: ...
    def save_for_user(self, user_id: int, settings: DriveSettings) -> None: ...


class TranscriptionJobsRepository(Protocol):
    # Minimal subset compatible with postgres-worker's JobRepository naming.
    def create_job(self, *, user_id: int, status: str = "pending",
                   source_file_id: str | None = None,
                   source_file_name: str | None = None) -> Job: ...
    def list_jobs_for_user(self, user_id: int, limit: int | None = None) -> list[Job]: ...
    def find_active_for_user(self, user_id: int) -> Job | None: ...


@dataclass(frozen=True)
class RepositoryBundle:
    users: UsersRepository
    google_tokens: GoogleTokensRepository
    deepgram_credentials: DeepgramCredentialsRepository
    drive_settings: DriveSettingsRepository
    jobs: TranscriptionJobsRepository


class RepositoryBackendUnavailable(RuntimeError):
    pass


def build_repositories(settings) -> RepositoryBundle:
    """Build the production Postgres-backed bundle provided by postgres-core.

    Integration point: postgres-core must expose
    ``app/db/postgres.py::build_repositories(database_url) -> RepositoryBundle``
    satisfying the Protocols above. Until then this raises a clear error.
    """
    try:
        from app.db.postgres import build_repositories as build_pg
    except ImportError as exc:  # postgres-core not integrated yet
        raise RepositoryBackendUnavailable(
            "Camada PostgreSQL (postgres-core) indisponível: integre a branch "
            "postgres-core (app.db.postgres.build_repositories) para rodar o app web."
        ) from exc
    return build_pg(settings.database_url)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_repositories.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/repositories.py tests/test_repositories.py
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "add repository contract: Protocols, domain dataclasses, DI entrypoint"
```

---

## Task 2: In-memory fakes for tests

**Files:**
- Create: `tests/fakes.py`
- Test: `tests/test_fakes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fakes.py
from app.web.repositories import DriveSettings, GoogleToken
from tests.fakes import build_fake_repositories


def test_users_create_get_and_ensure_admin():
    repos = build_fake_repositories()
    repos.users.ensure_admin(email="admin", password_hash="h1")
    admin = repos.users.get_by_email("admin")
    assert admin.role == "admin" and admin.is_active
    assert repos.users.get_password_hash(admin.id) == "h1"

    # idempotent: keeps a single admin, updates hash
    repos.users.ensure_admin(email="admin", password_hash="h2")
    assert len(repos.users.list_all()) == 1
    assert repos.users.get_password_hash(admin.id) == "h2"

    u = repos.users.create(email="u@x.com", password_hash="ph", role="user")
    assert u.role == "user"
    repos.users.set_active(u.id, False)
    assert repos.users.get_by_id(u.id).is_active is False
    repos.users.set_google_identity(u.id, "g@x.com", "G")
    assert repos.users.get_by_id(u.id).google_email == "g@x.com"


def test_jobs_create_list_and_active():
    repos = build_fake_repositories()
    assert repos.jobs.find_active_for_user(1) is None
    j = repos.jobs.create_job(user_id=1, status="pending")
    assert j.status == "pending"
    assert repos.jobs.find_active_for_user(1).id == j.id
    assert [x.id for x in repos.jobs.list_jobs_for_user(1)] == [j.id]
    assert repos.jobs.list_jobs_for_user(2) == []


def test_token_and_deepgram_and_drive_roundtrip():
    repos = build_fake_repositories()
    repos.google_tokens.save_for_user(1, GoogleToken("a", "r", "u", "c", "s", "sc", None))
    assert repos.google_tokens.get_for_user(1).access_token == "a"
    repos.deepgram_credentials.save_for_user(1, "cipher")
    assert repos.deepgram_credentials.get_encrypted_for_user(1) == "cipher"
    ds = DriveSettings("url", "id", None, None, True)
    repos.drive_settings.save_for_user(1, ds)
    assert repos.drive_settings.get_for_user(1).save_copy_to_drive is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fakes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tests.fakes'`

- [ ] **Step 3: Write the implementation**

```python
# tests/fakes.py
from __future__ import annotations

from dataclasses import replace

from app.web.repositories import (
    DriveSettings,
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


class InMemoryDriveSettingsRepository:
    def __init__(self) -> None:
        self._settings: dict[int, DriveSettings] = {}

    def get_for_user(self, user_id: int) -> DriveSettings | None:
        return self._settings.get(user_id)

    def save_for_user(self, user_id: int, settings: DriveSettings) -> None:
        self._settings[user_id] = settings


class InMemoryTranscriptionJobsRepository:
    def __init__(self) -> None:
        self._jobs: dict[int, Job] = {}
        self._seq = 0

    def create_job(self, *, user_id: int, status: str = "pending",
                   source_file_id: str | None = None,
                   source_file_name: str | None = None) -> Job:
        self._seq += 1
        job = Job(
            id=self._seq, user_id=user_id, status=status,
            source_file_id=source_file_id, source_file_name=source_file_name,
            created_at=_FIXED_TS, updated_at=_FIXED_TS,
        )
        self._jobs[job.id] = job
        return job

    def list_jobs_for_user(self, user_id: int, limit: int | None = None) -> list[Job]:
        jobs = sorted(
            (j for j in self._jobs.values() if j.user_id == user_id),
            key=lambda j: j.id, reverse=True,
        )
        return jobs[:limit] if limit else jobs

    def find_active_for_user(self, user_id: int) -> Job | None:
        for job in sorted(self._jobs.values(), key=lambda j: j.id, reverse=True):
            if job.user_id == user_id and job.status in ("pending", "processing"):
                return job
        return None


def build_fake_repositories() -> RepositoryBundle:
    return RepositoryBundle(
        users=InMemoryUsersRepository(),
        google_tokens=InMemoryGoogleTokensRepository(),
        deepgram_credentials=InMemoryDeepgramCredentialsRepository(),
        drive_settings=InMemoryDriveSettingsRepository(),
        jobs=InMemoryTranscriptionJobsRepository(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fakes.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add tests/fakes.py tests/test_fakes.py
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "add in-memory repository fakes for tests"
```

---

## Task 3: Password hashing (bcrypt)

**Files:**
- Create: `app/web/passwords.py`
- Modify: `requirements.txt` (add `bcrypt>=4,<5`)
- Test: `tests/test_passwords.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_passwords.py
from app.web.passwords import hash_password, verify_password


def test_hash_is_not_plaintext_and_verifies():
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"
    assert verify_password("s3cret", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_verify_handles_missing_or_garbage_hash():
    assert verify_password("x", None) is False
    assert verify_password("x", "") is False
    assert verify_password("x", "not-a-bcrypt-hash") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_passwords.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.web.passwords'`

- [ ] **Step 3: Write the implementation**

Add to `requirements.txt` (after the `cryptography` line):
```
bcrypt>=4,<5
```

```python
# app/web/passwords.py
from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install 'bcrypt>=4,<5' && python -m pytest tests/test_passwords.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/passwords.py tests/test_passwords.py requirements.txt
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "add bcrypt password hashing"
```

---

## Task 4: Drive folder ID extraction

**Files:**
- Create: `app/web/drive_links.py`
- Test: `tests/test_drive_links.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_drive_links.py
import pytest

from app.web.drive_links import extract_google_drive_folder_id

VALID_ID = "1A2b3C4d5E6f7G8h9I0jKlMnOpQ"


@pytest.mark.parametrize("value", [
    f"https://drive.google.com/drive/folders/{VALID_ID}",
    f"https://drive.google.com/drive/folders/{VALID_ID}?usp=sharing",
    f"https://drive.google.com/drive/u/0/folders/{VALID_ID}",
    f"  {VALID_ID}  ",
])
def test_extracts_id_from_supported_forms(value):
    assert extract_google_drive_folder_id(value) == VALID_ID


@pytest.mark.parametrize("value", ["", "   ", "https://drive.google.com/drive/folders/", "short", "https://example.com/x"])
def test_rejects_invalid(value):
    with pytest.raises(ValueError):
        extract_google_drive_folder_id(value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_links.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.web.drive_links'`

- [ ] **Step 3: Write the implementation**

```python
# app/web/drive_links.py
from __future__ import annotations

import re
from urllib.parse import urlparse

_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def extract_google_drive_folder_id(value: str) -> str:
    """Extract a Drive folder ID from a full URL, a URL with querystring, or a raw ID."""
    if not value or not value.strip():
        raise ValueError("Drive folder value is required")
    text = value.strip()
    if "drive.google.com" in text or "/folders/" in text:
        match = _FOLDER_RE.search(urlparse(text).path)
        if not match:
            raise ValueError(f"Could not extract a Drive folder ID from URL: {value!r}")
        candidate = match.group(1)
    else:
        candidate = text
    if not _ID_RE.match(candidate):
        raise ValueError(f"Not a valid Drive folder ID: {candidate!r}")
    return candidate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_drive_links.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/drive_links.py tests/test_drive_links.py
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "add Google Drive folder ID extraction helper"
```

---

## Task 5: Deepgram key store + best-effort verification

**Files:**
- Create: `app/web/deepgram_key.py`
- Test: `tests/test_deepgram_key.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deepgram_key.py
from app.web.deepgram_key import DeepgramKeyStore, verify_deepgram_key
from app.web.security import fernet_from_secret
from tests.fakes import InMemoryDeepgramCredentialsRepository


def _store():
    return DeepgramKeyStore(
        InMemoryDeepgramCredentialsRepository(), fernet_from_secret("a-long-secret-for-tests")
    )


def test_store_encrypts_and_roundtrips_and_masks():
    store = _store()
    assert store.has_key(1) is False
    store.save_for_user(1, "dg-supersecretkey")
    assert store.has_key(1) is True
    # stored value is ciphertext, not the plaintext key
    assert store._repo.get_encrypted_for_user(1) != "dg-supersecretkey"
    assert store.get_key(1) == "dg-supersecretkey"
    assert store.masked(1).endswith("rkey")
    assert "dg-supersecretkey" not in store.masked(1)


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _Session:
    def __init__(self, status=None, exc=None):
        self._status = status
        self._exc = exc
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers, timeout))
        if self._exc:
            raise self._exc
        return _Resp(self._status)


def test_verify_returns_valid_invalid_unverifiable():
    assert verify_deepgram_key("k", session=_Session(status=200)) == "valid"
    assert verify_deepgram_key("k", session=_Session(status=401)) == "invalid"
    assert verify_deepgram_key("k", session=_Session(status=403)) == "invalid"
    assert verify_deepgram_key("k", session=_Session(status=500)) == "unverifiable"
    assert verify_deepgram_key("k", session=_Session(exc=TimeoutError())) == "unverifiable"


def test_verify_sends_token_header_not_logged():
    session = _Session(status=200)
    verify_deepgram_key("secret-key", session=session)
    _, headers, _ = session.calls[0]
    assert headers["Authorization"] == "Token secret-key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deepgram_key.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.web.deepgram_key'`

- [ ] **Step 3: Write the implementation**

```python
# app/web/deepgram_key.py
from __future__ import annotations

import logging
from typing import Literal

import requests

from app.web.repositories import DeepgramCredentialsRepository
from app.web.security import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

DEEPGRAM_PROJECTS_URL = "https://api.deepgram.com/v1/projects"


class DeepgramKeyStore:
    def __init__(self, repository: DeepgramCredentialsRepository, fernet) -> None:
        self._repo = repository
        self._fernet = fernet

    def save_for_user(self, user_id: int, api_key: str) -> None:
        self._repo.save_for_user(user_id, encrypt_value(self._fernet, api_key))

    def get_key(self, user_id: int) -> str | None:
        encrypted = self._repo.get_encrypted_for_user(user_id)
        return decrypt_value(self._fernet, encrypted) if encrypted else None

    def has_key(self, user_id: int) -> bool:
        return self._repo.get_encrypted_for_user(user_id) is not None

    def masked(self, user_id: int) -> str | None:
        key = self.get_key(user_id)
        if not key:
            return None
        return f"…{key[-4:]}" if len(key) >= 4 else "…"


def verify_deepgram_key(
    api_key: str, *, session=None, timeout: int = 5
) -> Literal["valid", "invalid", "unverifiable"]:
    """Best-effort live check. Never raises; never logs the key."""
    http = session or requests
    try:
        response = http.get(
            DEEPGRAM_PROJECTS_URL,
            headers={"Authorization": f"Token {api_key}"},
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - network/timeout must degrade to "unverifiable"
        logger.warning("Deepgram key verification could not reach the API")
        return "unverifiable"
    if response.status_code == 200:
        return "valid"
    if response.status_code in (401, 403):
        return "invalid"
    logger.warning("Deepgram key verification got unexpected status %s", response.status_code)
    return "unverifiable"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_deepgram_key.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/deepgram_key.py tests/test_deepgram_key.py
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "add encrypted Deepgram key store and best-effort verification"
```

---

## Task 6: Refactor TokenStore onto the repository

**Files:**
- Modify: `app/web/token_store.py` (full rewrite)
- Test: `tests/test_token_store.py` (full rewrite)

- [ ] **Step 1: Rewrite the test**

```python
# tests/test_token_store.py
from app.web.repositories import GoogleToken
from app.web.security import decrypt_value, encrypt_value, fernet_from_secret
from app.web.token_store import TokenStore
from tests.fakes import InMemoryGoogleTokensRepository


def test_encrypt_value_does_not_store_plaintext():
    fernet = fernet_from_secret("a-long-secret-for-tests")
    encrypted = encrypt_value(fernet, "secret-token")
    assert encrypted != "secret-token"
    assert decrypt_value(fernet, encrypted) == "secret-token"


def test_token_store_encrypts_at_rest_and_decrypts_on_read():
    repo = InMemoryGoogleTokensRepository()
    fernet = fernet_from_secret("a-long-secret-for-tests")
    store = TokenStore(repo, fernet)

    store.save_for_user(1, {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": "https://www.googleapis.com/auth/drive",
        "expiry": "2026-06-03T10:00:00Z",
    })

    stored = repo.get_for_user(1)
    assert isinstance(stored, GoogleToken)
    assert stored.access_token != "access-secret"
    assert stored.client_secret != "client-secret"

    loaded = store.get_for_user(1)
    assert loaded["access_token"] == "access-secret"
    assert loaded["refresh_token"] == "refresh-secret"
    assert loaded["client_secret"] == "client-secret"
    assert store.get_for_user(2) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_token_store.py -v`
Expected: FAIL (old `TokenStore(db_path, fernet)` signature / `from app import db` import error)

- [ ] **Step 3: Rewrite the implementation**

```python
# app/web/token_store.py
from __future__ import annotations

from cryptography.fernet import Fernet

from app.web.repositories import GoogleToken, GoogleTokensRepository
from app.web.security import decrypt_value, encrypt_value


class TokenStore:
    def __init__(self, repository: GoogleTokensRepository, fernet: Fernet) -> None:
        self._repo = repository
        self._fernet = fernet

    def save_for_user(self, user_id: int, token_data: dict) -> None:
        token = GoogleToken(
            access_token=encrypt_value(self._fernet, token_data["access_token"]),
            refresh_token=encrypt_value(self._fernet, token_data.get("refresh_token")),
            token_uri=token_data["token_uri"],
            client_id=token_data["client_id"],
            client_secret=encrypt_value(self._fernet, token_data.get("client_secret")),
            scopes=token_data["scopes"],
            expiry=token_data.get("expiry"),
        )
        self._repo.save_for_user(user_id, token)

    def get_for_user(self, user_id: int) -> dict | None:
        token = self._repo.get_for_user(user_id)
        if token is None:
            return None
        return {
            "access_token": decrypt_value(self._fernet, token.access_token),
            "refresh_token": decrypt_value(self._fernet, token.refresh_token),
            "token_uri": token.token_uri,
            "client_id": token.client_id,
            "client_secret": decrypt_value(self._fernet, token.client_secret),
            "scopes": token.scopes,
            "expiry": token.expiry,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_token_store.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/token_store.py tests/test_token_store.py
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "refactor TokenStore onto GoogleTokensRepository"
```

---

## Task 7: Refactor WebSettings (database_url; drop deepgram_api_key)

**Files:**
- Modify: `app/web/config.py`
- Test: `tests/test_web_config.py` (rewrite)

- [ ] **Step 1: Rewrite the test**

```python
# tests/test_web_config.py
import pytest

from app.web.config import WebSettings


def _env(tmp_path, **overrides):
    env = {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "SESSION_COOKIE_SECURE": "false",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": "postgresql+psycopg://app:app@db:5432/meet",
        "TMP_DIR": str(tmp_path / "tmp"),
    }
    env.update(overrides)
    return env


def test_web_settings_parses_required_values(tmp_path):
    settings = WebSettings.from_env(_env(tmp_path))
    assert settings.admin_username == "admin"
    assert settings.session_cookie_secure is False
    assert settings.database_url.startswith("postgresql")
    assert settings.tmp_dir.name == "tmp"
    assert not hasattr(settings, "deepgram_api_key")


def test_web_settings_requires_app_secret_key(tmp_path):
    env = _env(tmp_path)
    del env["APP_SECRET_KEY"]
    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        WebSettings.from_env(env)


def test_web_settings_requires_database_url(tmp_path):
    env = _env(tmp_path)
    del env["DATABASE_URL"]
    with pytest.raises(ValueError, match="DATABASE_URL"):
        WebSettings.from_env(env)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web_config.py -v`
Expected: FAIL (current settings still expose `deepgram_api_key`/`database_path`)

- [ ] **Step 3: Rewrite the implementation**

Replace the `WebSettings` dataclass body and `from_env` in `app/web/config.py`:

```python
# app/web/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.config import parse_bool


@dataclass(frozen=True)
class WebSettings:
    admin_username: str
    admin_password: str
    app_secret_key: str
    session_cookie_secure: bool
    google_web_client_id: str
    google_web_client_secret: str
    google_redirect_uri: str
    database_url: str
    tmp_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "WebSettings":
        values = env or os.environ
        settings = cls(
            admin_username=_required(values, "ADMIN_USERNAME"),
            admin_password=_required(values, "ADMIN_PASSWORD"),
            app_secret_key=_required(values, "APP_SECRET_KEY"),
            session_cookie_secure=parse_bool(values.get("SESSION_COOKIE_SECURE", "false")),
            google_web_client_id=_required(values, "GOOGLE_WEB_CLIENT_ID"),
            google_web_client_secret=_required(values, "GOOGLE_WEB_CLIENT_SECRET"),
            google_redirect_uri=_required(values, "GOOGLE_REDIRECT_URI"),
            database_url=_required(values, "DATABASE_URL"),
            tmp_dir=Path(values.get("TMP_DIR", "/app/tmp")),
        )
        settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        return settings


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_web_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/config.py tests/test_web_config.py
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "refactor WebSettings to PostgreSQL database_url, drop env deepgram key"
```

---

## Task 8: Refactor services to enqueue-only (repos + Deepgram gate)

**Files:**
- Modify: `app/web/services.py` (full rewrite — removes all heavy processing; that work belongs to postgres-worker)
- Test: `tests/test_web_services.py` (full rewrite)

- [ ] **Step 1: Rewrite the test**

```python
# tests/test_web_services.py
from app.web import services
from app.web.deepgram_key import DeepgramKeyStore
from app.web.repositories import DriveSettings, GoogleToken
from app.web.security import fernet_from_secret
from tests.fakes import build_fake_repositories


def _deepgram(repos):
    return DeepgramKeyStore(repos.deepgram_credentials, fernet_from_secret("a-long-secret-for-tests"))


def _connect_google(repos, user_id):
    repos.google_tokens.save_for_user(user_id, GoogleToken("a", "r", "u", "c", "s", "sc", None))


def _set_source(repos, user_id):
    repos.drive_settings.save_for_user(
        user_id, DriveSettings("url", "source-id", None, None, False)
    )


def test_enqueue_reports_missing_settings():
    repos = build_fake_repositories()
    result = services.enqueue_run_once_job(repos, _deepgram(repos), 1)
    assert result.status == "missing_settings"
    assert repos.jobs.list_jobs_for_user(1) == []


def test_enqueue_reports_google_not_connected():
    repos = build_fake_repositories()
    _set_source(repos, 1)
    result = services.enqueue_run_once_job(repos, _deepgram(repos), 1)
    assert result.status == "not_connected"


def test_enqueue_blocks_without_deepgram_key():
    repos = build_fake_repositories()
    _set_source(repos, 1)
    _connect_google(repos, 1)
    result = services.enqueue_run_once_job(repos, _deepgram(repos), 1)
    assert result.status == "no_deepgram_key"
    assert repos.jobs.list_jobs_for_user(1) == []


def test_enqueue_creates_pending_job_when_ready():
    repos = build_fake_repositories()
    _set_source(repos, 1)
    _connect_google(repos, 1)
    deepgram = _deepgram(repos)
    deepgram.save_for_user(1, "dg-key")
    result = services.enqueue_run_once_job(repos, deepgram, 1)
    assert result.status == "created"
    assert result.job.status == "pending"
    assert [j.status for j in repos.jobs.list_jobs_for_user(1)] == ["pending"]


def test_enqueue_blocks_when_active_job_exists():
    repos = build_fake_repositories()
    _set_source(repos, 1)
    _connect_google(repos, 1)
    deepgram = _deepgram(repos)
    deepgram.save_for_user(1, "dg-key")
    repos.jobs.create_job(user_id=1, status="processing")
    result = services.enqueue_run_once_job(repos, deepgram, 1)
    assert result.status == "already_running"
    assert len(repos.jobs.list_jobs_for_user(1)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web_services.py -v`
Expected: FAIL (old services imports `from app import db`, signature mismatch)

- [ ] **Step 3: Rewrite the implementation**

```python
# app/web/services.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.web.deepgram_key import DeepgramKeyStore
from app.web.repositories import RepositoryBundle


@dataclass(frozen=True)
class EnqueueResult:
    """status in: missing_settings | not_connected | no_deepgram_key | already_running | created."""
    status: str
    job: Any | None = None


def enqueue_run_once_job(
    repositories: RepositoryBundle, deepgram_store: DeepgramKeyStore, user_id: int
) -> EnqueueResult:
    """Validate preconditions and enqueue a pending job.

    This branch only enqueues. Real download/transcribe/persist is owned by the
    postgres-worker branch, which consumes pending jobs from the same repository.
    """
    drive_settings = repositories.drive_settings.get_for_user(user_id)
    if drive_settings is None or not drive_settings.source_drive_folder_id:
        return EnqueueResult("missing_settings")
    if repositories.google_tokens.get_for_user(user_id) is None:
        return EnqueueResult("not_connected")
    if not deepgram_store.has_key(user_id):
        return EnqueueResult("no_deepgram_key")
    if repositories.jobs.find_active_for_user(user_id) is not None:
        return EnqueueResult("already_running")
    job = repositories.jobs.create_job(user_id=user_id, status="pending")
    logging.info("Run once job enqueued job_id=%s user_id=%s", job.id, user_id)
    return EnqueueResult("created", job)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_web_services.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/services.py tests/test_web_services.py
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "refactor run-once to enqueue-only with Deepgram-key gate"
```

---

## Task 9: Rewrite main.py (DI, DB-backed auth, roles, new routes)

**Files:**
- Modify: `app/web/main.py` (full rewrite)
- Test: covered by Task 12 (`tests/test_web_routes.py`, `tests/test_admin.py`). After this task, run the existing suite to ensure imports resolve.

- [ ] **Step 1: Rewrite `app/web/main.py`**

```python
# app/web/main.py
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.logger import setup_logging
from app.web import services
from app.web.config import WebSettings
from app.web.deepgram_key import DeepgramKeyStore, verify_deepgram_key
from app.web.drive_links import extract_google_drive_folder_id
from app.web.passwords import hash_password, verify_password
from app.web.repositories import DriveSettings, RepositoryBundle, build_repositories
from app.web.security import fernet_from_secret
from app.web.token_store import TokenStore

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

RUN_ONCE_MESSAGES = {
    "missing_settings": "Configure a pasta de origem em Drive Settings primeiro.",
    "not_connected": "Conecte o Google antes de rodar uma transcrição.",
    "no_deepgram_key": "Configure sua Deepgram API Key antes de iniciar uma transcrição.",
    "already_running": "Já existe um job em execução.",
    "created": "Job enfileirado; o worker fará o processamento.",
}
DEEPGRAM_TEST_MESSAGES = {
    "valid": "Deepgram API Key válida.",
    "invalid": "Deepgram API Key inválida.",
    "unverifiable": "Não foi possível verificar agora.",
}


def create_app(settings: WebSettings | None = None,
               repositories: RepositoryBundle | None = None) -> FastAPI:
    setup_logging()
    web_settings = settings or WebSettings.from_env()
    repos = repositories or build_repositories(web_settings)
    fernet = fernet_from_secret(web_settings.app_secret_key)
    token_store = TokenStore(repos.google_tokens, fernet)
    deepgram_store = DeepgramKeyStore(repos.deepgram_credentials, fernet)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repos.users.ensure_admin(
            email=web_settings.admin_username,
            password_hash=hash_password(web_settings.admin_password),
        )
        yield

    app = FastAPI(title="Meet Transcription", lifespan=lifespan)
    app.state.settings = web_settings
    app.state.repositories = repos
    app.state.token_store = token_store
    app.state.deepgram_store = deepgram_store
    app.add_middleware(
        SessionMiddleware,
        secret_key=web_settings.app_secret_key,
        https_only=web_settings.session_cookie_secure,
        same_site="lax",
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html")

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        user = repos.users.get_by_email(username.strip())
        pw_hash = repos.users.get_password_hash(user.id) if user else None
        if user is None or not user.is_active or not verify_password(password, pw_hash):
            return templates.TemplateResponse(
                request, "login.html", {"error": "Invalid email or password"}, status_code=401
            )
        request.session["user_id"] = user.id
        request.session["user_email"] = user.email
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, user=Depends(require_user)):
        return templates.TemplateResponse(request, "dashboard.html", {
            "user": user,
            "settings": repos.drive_settings.get_for_user(user.id),
            "google_connected": repos.google_tokens.get_for_user(user.id) is not None,
            "deepgram_configured": deepgram_store.has_key(user.id),
            "jobs": repos.jobs.list_jobs_for_user(user.id, limit=5),
        })

    @app.get("/settings")
    def settings_redirect():
        return RedirectResponse("/settings/drive", status_code=303)

    @app.get("/settings/drive", response_class=HTMLResponse)
    def drive_settings_page(request: Request, user=Depends(require_user)):
        return templates.TemplateResponse(request, "settings_drive.html", {
            "user": user,
            "settings": repos.drive_settings.get_for_user(user.id),
            "message": _pop_flash(request),
        })

    @app.post("/settings/drive")
    def save_drive_settings(
        request: Request,
        user=Depends(require_user),
        source_drive_folder_url: str = Form(...),
        destination_drive_folder_url: str = Form(""),
        save_copy_to_drive: bool = Form(False),
    ):
        try:
            source_id = extract_google_drive_folder_id(source_drive_folder_url)
            dest_url = destination_drive_folder_url.strip() or None
            dest_id = extract_google_drive_folder_id(dest_url) if dest_url else None
        except ValueError as exc:
            return templates.TemplateResponse(request, "settings_drive.html", {
                "user": user,
                "settings": repos.drive_settings.get_for_user(user.id),
                "error": str(exc),
            }, status_code=400)
        repos.drive_settings.save_for_user(user.id, DriveSettings(
            source_drive_folder_url=source_drive_folder_url.strip(),
            source_drive_folder_id=source_id,
            destination_drive_folder_url=dest_url,
            destination_drive_folder_id=dest_id,
            save_copy_to_drive=bool(save_copy_to_drive),
        ))
        _set_flash(request, "Drive settings salvos.")
        return RedirectResponse("/settings/drive", status_code=303)

    @app.get("/settings/deepgram", response_class=HTMLResponse)
    def deepgram_page(request: Request, user=Depends(require_user)):
        return templates.TemplateResponse(request, "settings_deepgram.html", {
            "user": user,
            "configured": deepgram_store.has_key(user.id),
            "masked": deepgram_store.masked(user.id),
            "message": _pop_flash(request),
        })

    @app.post("/settings/deepgram")
    def save_deepgram(request: Request, user=Depends(require_user),
                      deepgram_api_key: str = Form(...)):
        key = deepgram_api_key.strip()
        if not key:
            _set_flash(request, "Deepgram API Key não pode ser vazia.")
        else:
            deepgram_store.save_for_user(user.id, key)
            _set_flash(request, "Deepgram API Key salva.")
        return RedirectResponse("/settings/deepgram", status_code=303)

    @app.post("/settings/deepgram/test")
    def test_deepgram(request: Request, user=Depends(require_user)):
        key = deepgram_store.get_key(user.id)
        if not key:
            _set_flash(request, "Configure sua Deepgram API Key antes de iniciar uma transcrição.")
        else:
            _set_flash(request, DEEPGRAM_TEST_MESSAGES[verify_deepgram_key(key)])
        return RedirectResponse("/settings/deepgram", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request, user=Depends(require_user)):
        return templates.TemplateResponse(request, "jobs.html", {
            "user": user,
            "jobs": repos.jobs.list_jobs_for_user(user.id),
            "message": _pop_flash(request),
        })

    @app.post("/jobs/run-once")
    def run_once(request: Request, user=Depends(require_user)):
        result = services.enqueue_run_once_job(repos, deepgram_store, user.id)
        _set_flash(request, RUN_ONCE_MESSAGES[result.status])
        return RedirectResponse("/jobs", status_code=303)

    @app.get("/admin/users", response_class=HTMLResponse)
    def admin_users(request: Request, admin=Depends(require_admin)):
        return templates.TemplateResponse(request, "admin_users.html", {
            "user": admin,
            "users": repos.users.list_all(),
            "message": _pop_flash(request),
        })

    @app.post("/admin/users")
    def admin_create_user(request: Request, admin=Depends(require_admin),
                          email: str = Form(...), password: str = Form(...),
                          role: str = Form("user")):
        email = email.strip()
        if not email or not password:
            _set_flash(request, "Email e senha são obrigatórios.")
        elif repos.users.get_by_email(email) is not None:
            _set_flash(request, f"Usuário já existe: {email}")
        else:
            repos.users.create(
                email=email, password_hash=hash_password(password),
                role="admin" if role == "admin" else "user",
            )
            _set_flash(request, f"Usuário criado: {email}")
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}/disable")
    def admin_disable_user(request: Request, user_id: int, admin=Depends(require_admin)):
        repos.users.set_active(user_id, False)
        _set_flash(request, "Usuário desativado.")
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}/enable")
    def admin_enable_user(request: Request, user_id: int, admin=Depends(require_admin)):
        repos.users.set_active(user_id, True)
        _set_flash(request, "Usuário ativado.")
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/admin/users/{user_id}/reset-password")
    def admin_reset_password(request: Request, user_id: int, admin=Depends(require_admin),
                             new_password: str = Form(...)):
        if not new_password.strip():
            _set_flash(request, "Nova senha não pode ser vazia.")
        else:
            repos.users.set_password_hash(user_id, hash_password(new_password))
            _set_flash(request, "Senha redefinida.")
        return RedirectResponse("/admin/users", status_code=303)

    @app.get("/connect-google")
    def connect_google(request: Request, user=Depends(require_user)):
        state = secrets.token_urlsafe(32)
        request.session["oauth_state"] = state
        params = {
            "client_id": web_settings.google_web_client_id,
            "redirect_uri": web_settings.google_redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/drive",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        return RedirectResponse(url, status_code=303)

    @app.get("/oauth/google/callback")
    def oauth_callback(request: Request, code: str, state: str, user=Depends(require_user)):
        expected_state = request.session.get("oauth_state")
        if not expected_state or state != expected_state:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        token_data = exchange_google_code(web_settings, code)
        token_store.save_for_user(user.id, token_data)
        profile = fetch_google_userinfo(token_data["access_token"])
        if profile:
            repos.users.set_google_identity(user.id, profile.get("email"), profile.get("name"))
        request.session.pop("oauth_state", None)
        return RedirectResponse("/", status_code=303)

    return app


def _set_flash(request: Request, message: str) -> None:
    request.session["flash"] = message


def _pop_flash(request: Request) -> str | None:
    return request.session.pop("flash", None)


def require_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user = request.app.state.repositories.users.get_by_id(int(user_id))
    if user is None or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(request: Request, user=Depends(require_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def exchange_google_code(settings: WebSettings, code: str) -> dict:
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.google_web_client_id,
            "client_secret": settings.google_web_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    expires_in = int(payload.get("expires_in", 3600))
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token"),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": settings.google_web_client_id,
        "client_secret": settings.google_web_client_secret,
        "scopes": payload.get("scope", "https://www.googleapis.com/auth/drive"),
        "expiry": expiry.replace(microsecond=0).isoformat(),
    }


def fetch_google_userinfo(access_token: str) -> dict | None:
    """Best-effort fetch of the connected Google account's email/name."""
    try:
        response = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:  # noqa: BLE001 - identity is optional, never block the callback
        logging.warning("Could not fetch Google userinfo")
    return None


class LazyApp:
    def __init__(self) -> None:
        self._app: FastAPI | None = None

    async def __call__(self, scope, receive, send) -> None:
        if self._app is None:
            self._app = create_app()
        await self._app(scope, receive, send)


app = LazyApp()
```

- [ ] **Step 2: Verify imports resolve (templates added in Task 10–11; route tests in Task 12)**

Run: `python -c "import app.web.main"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/main.py
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "rewrite web app: DI repos, DB-backed login, roles, admin and settings routes"
```

---

## Task 10: Templates — nav, dashboard, jobs, login (attribute access)

**Files:**
- Modify: `app/web/templates/base.html`, `dashboard.html`, `jobs.html`, `login.html`
- Modify: `app/web/static/styles.css` (one checkbox rule)

- [ ] **Step 1: Update `base.html` nav block** (replace the `<nav>…</nav>` inside `{% if user %}`):

```html
      <nav>
        <a href="/">Dashboard</a>
        <a href="/jobs">Jobs</a>
        <a href="/settings/drive">Drive Settings</a>
        <a href="/settings/deepgram">Deepgram</a>
        {% if user.role == "admin" %}<a href="/admin/users">Admin Users</a>{% endif %}
        <form action="/logout" method="post"><button type="submit">Logout</button></form>
      </nav>
```

- [ ] **Step 2: Replace `dashboard.html`**

```html
{% extends "base.html" %}
{% block content %}
<section class="hero">
  <div>
    <p class="eyebrow">Dashboard</p>
    <h1>Meeting transcription control panel</h1>
    <p>Connect Google Drive, configure folders, save your Deepgram key, and enqueue a run.</p>
  </div>
  <a class="button" href="/connect-google">Connect Google</a>
</section>
<section class="grid">
  <article class="card"><h2>Google</h2><p>{{ "Connected" if google_connected else "Not connected" }}</p></article>
  <article class="card"><h2>Deepgram</h2><p>{{ "Configured" if deepgram_configured else "Not configured" }}</p></article>
  <article class="card"><h2>Source Folder</h2><p>{{ settings.source_drive_folder_id if settings else "Not configured" }}</p></article>
</section>
<section class="card">
  <h2>Recent jobs</h2>
  {% if jobs %}
  <ul class="list">
    {% for job in jobs %}<li>{{ job.source_file_name or "Manual run" }} — {{ job.status }}</li>{% endfor %}
  </ul>
  {% else %}<p>No jobs yet.</p>{% endif %}
</section>
{% endblock %}
```

- [ ] **Step 3: Replace the `<tbody>` rows in `jobs.html`** (switch `job['x']` → `job.x`):

```html
    {% for job in jobs %}
      <tr>
        <td>{{ job.source_file_name or '-' }}</td>
        <td>{{ job.source_file_id or '-' }}</td>
        <td>{{ job.status }}</td>
        <td>{{ job.attempts }}</td>
        <td>{{ job.transcript_drive_file_id or '-' }}</td>
        <td>{{ job.error_message or '' }}</td>
        <td>{{ job.created_at or '-' }}</td>
        <td>{{ job.updated_at or '-' }}</td>
        <td>{{ job.processed_at or '-' }}</td>
      </tr>
    {% endfor %}
```

- [ ] **Step 4: Update `login.html`** — relabel the username field to Email (keep `name="username"` for compatibility), update help text:

```html
  <p>Entre com seu email e senha.</p>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <form method="post" action="/login" class="form-stack">
    <label>Email<input name="username" type="text" autocomplete="username" required></label>
    <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">Enter dashboard</button>
  </form>
```

- [ ] **Step 5: Add a checkbox style** to `app/web/static/styles.css` (append):

```css
label.checkbox { grid-auto-flow: column; justify-content: start; align-items: center; gap: .5rem; color: var(--ink); }
label.checkbox input { width: auto; }
```

- [ ] **Step 6: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/templates/base.html app/web/templates/dashboard.html app/web/templates/jobs.html app/web/templates/login.html app/web/static/styles.css
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "update nav and templates for multiuser, attribute-access domain objects"
```

---

## Task 11: New templates — drive settings, deepgram, admin users

**Files:**
- Create: `app/web/templates/settings_drive.html`, `settings_deepgram.html`, `admin_users.html`

- [ ] **Step 1: Create `settings_drive.html`**

```html
{% extends "base.html" %}
{% block content %}
<section class="card">
  <h1>Drive Settings</h1>
  <p class="hint">A pasta de origem é obrigatória. O destino é opcional (backup). Cole a URL da pasta do Google Drive.</p>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  {% if message %}<div class="notice">{{ message }}</div>{% endif %}
  <form method="post" action="/settings/drive" class="form-stack">
    <label>Source Drive Folder URL
      <input name="source_drive_folder_url" value="{{ settings.source_drive_folder_url if settings else '' }}" required></label>
    <label>Destination Drive Folder URL (opcional)
      <input name="destination_drive_folder_url" value="{{ settings.destination_drive_folder_url if settings and settings.destination_drive_folder_url else '' }}"></label>
    <label class="checkbox">
      <input type="checkbox" name="save_copy_to_drive" value="true" {{ "checked" if settings and settings.save_copy_to_drive else "" }}>
      Salvar cópia da transcrição no Drive (backup)
    </label>
    <button type="submit">Save Drive settings</button>
  </form>
  {% if settings %}
  <p class="hint">Source folder ID: {{ settings.source_drive_folder_id }}{% if settings.destination_drive_folder_id %} · Destination folder ID: {{ settings.destination_drive_folder_id }}{% endif %}</p>
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 2: Create `settings_deepgram.html`**

```html
{% extends "base.html" %}
{% block content %}
<section class="card">
  <h1>Deepgram</h1>
  <p class="hint">Sua Deepgram API Key fica criptografada e nunca é exibida novamente.</p>
  {% if message %}<div class="notice">{{ message }}</div>{% endif %}
  <p>Status: {% if configured %}<strong>Configured</strong> ({{ masked }}){% else %}<strong>Not configured</strong>{% endif %}</p>
  <form method="post" action="/settings/deepgram" class="form-stack">
    <label>Deepgram API Key<input name="deepgram_api_key" type="password" autocomplete="off" required></label>
    <button type="submit">Save key</button>
  </form>
  {% if configured %}
  <form method="post" action="/settings/deepgram/test"><button type="submit">Test key</button></form>
  {% endif %}
</section>
{% endblock %}
```

- [ ] **Step 3: Create `admin_users.html`**

```html
{% extends "base.html" %}
{% block content %}
<section class="card">
  <h1>Admin · Users</h1>
  {% if message %}<div class="notice">{{ message }}</div>{% endif %}
  <form method="post" action="/admin/users" class="form-stack">
    <label>Email<input name="email" type="email" required></label>
    <label>Password<input name="password" type="password" required></label>
    <label>Role
      <select name="role"><option value="user">user</option><option value="admin">admin</option></select>
    </label>
    <button type="submit">Create user</button>
  </form>
</section>
<section class="card">
  <h2>Users</h2>
  <div class="table-scroll">
  <table>
    <thead><tr><th>ID</th><th>Email</th><th>Role</th><th>Active</th><th>Google</th><th>Actions</th></tr></thead>
    <tbody>
    {% for u in users %}
      <tr>
        <td>{{ u.id }}</td>
        <td>{{ u.email }}</td>
        <td>{{ u.role }}</td>
        <td>{{ "yes" if u.is_active else "no" }}</td>
        <td>{{ u.google_email or "-" }}</td>
        <td class="row-between">
          {% if u.is_active %}
          <form method="post" action="/admin/users/{{ u.id }}/disable"><button type="submit">Disable</button></form>
          {% else %}
          <form method="post" action="/admin/users/{{ u.id }}/enable"><button type="submit">Enable</button></form>
          {% endif %}
          <form method="post" action="/admin/users/{{ u.id }}/reset-password" class="row-between">
            <input name="new_password" type="password" placeholder="new password" required>
            <button type="submit">Reset</button>
          </form>
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</section>
{% endblock %}
```

- [ ] **Step 4: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add app/web/templates/settings_drive.html app/web/templates/settings_deepgram.html app/web/templates/admin_users.html
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "add drive settings, deepgram and admin users templates"
```

---

## Task 12: Route tests (auth, settings, admin, oauth) with fakes

**Files:**
- Rewrite: `tests/test_web_routes.py`
- Create: `tests/test_admin.py`

- [ ] **Step 1: Rewrite `tests/test_web_routes.py`**

```python
# tests/test_web_routes.py
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.web.config import WebSettings
from app.web.deepgram_key import DeepgramKeyStore
from app.web.main import create_app
from app.web.repositories import DriveSettings, GoogleToken
from app.web.security import fernet_from_secret
from tests.fakes import build_fake_repositories


def _settings(tmp_path) -> WebSettings:
    return WebSettings.from_env({
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "SESSION_COOKIE_SECURE": "false",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
    })


def _client(tmp_path, repos=None):
    repos = repos or build_fake_repositories()
    return TestClient(create_app(_settings(tmp_path), repositories=repos)), repos


def _login(client, username="admin", password="secret"):
    response = client.post("/login", data={"username": username, "password": password},
                           follow_redirects=False)
    assert response.status_code in {302, 303}, response.text
    return response


def test_health_ok(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        assert client.get("/health").json() == {"status": "ok"}


def test_dashboard_redirects_when_anonymous(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code in {302, 303, 307}
        assert r.headers["location"].startswith("/login")


def test_bootstrap_admin_login_sets_httponly_cookie(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        r = _login(client)
        assert r.headers["location"] == "/"
        assert "httponly" in r.headers["set-cookie"].lower()
        admin = repos.users.get_by_email("admin")
        assert admin.role == "admin"


def test_disabled_user_cannot_login(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        client.get("/health")  # trigger lifespan/bootstrap
        admin = repos.users.get_by_email("admin")
        repos.users.set_active(admin.id, False)
        r = client.post("/login", data={"username": "admin", "password": "secret"},
                        follow_redirects=False)
        assert r.status_code == 401


def test_wrong_password_rejected(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        r = client.post("/login", data={"username": "admin", "password": "nope"},
                        follow_redirects=False)
        assert r.status_code == 401


def test_settings_drive_save_extracts_folder_ids(tmp_path):
    client, repos = _client(tmp_path)
    folder = "1A2b3C4d5E6f7G8h9I0jKl"
    with client:
        _login(client)
        r = client.post("/settings/drive", data={
            "source_drive_folder_url": f"https://drive.google.com/drive/folders/{folder}?usp=sharing",
            "destination_drive_folder_url": "",
            "save_copy_to_drive": "true",
        }, follow_redirects=False)
        assert r.status_code == 303
        admin = repos.users.get_by_email("admin")
        saved = repos.drive_settings.get_for_user(admin.id)
        assert saved.source_drive_folder_id == folder
        assert saved.destination_drive_folder_id is None
        assert saved.save_copy_to_drive is True


def test_settings_drive_rejects_bad_url(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        _login(client)
        r = client.post("/settings/drive", data={
            "source_drive_folder_url": "not a url", "destination_drive_folder_url": "",
        }, follow_redirects=False)
        assert r.status_code == 400


def test_deepgram_save_encrypts_and_masks(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client)
        client.post("/settings/deepgram", data={"deepgram_api_key": "dg-mysecretkey"},
                    follow_redirects=False)
        admin = repos.users.get_by_email("admin")
        assert repos.deepgram_credentials.get_encrypted_for_user(admin.id) != "dg-mysecretkey"
        page = client.get("/settings/deepgram").text
        assert "Configured" in page
        assert "dg-mysecretkey" not in page


def test_run_once_blocks_without_deepgram_key(tmp_path):
    client, repos = _client(tmp_path)
    folder = "1A2b3C4d5E6f7G8h9I0jKl"
    with client:
        _login(client)
        admin = repos.users.get_by_email("admin")
        repos.drive_settings.save_for_user(admin.id, DriveSettings(
            "url", folder, None, None, False))
        repos.google_tokens.save_for_user(admin.id, GoogleToken("a", "r", "u", "c", "s", "sc", None))
        client.post("/jobs/run-once", follow_redirects=False)
        assert repos.jobs.list_jobs_for_user(admin.id) == []
        page = client.get("/jobs").text
        assert "Configure sua Deepgram API Key antes de iniciar uma transcrição." in page


def test_run_once_enqueues_pending_when_ready(tmp_path):
    client, repos = _client(tmp_path)
    folder = "1A2b3C4d5E6f7G8h9I0jKl"
    with client:
        _login(client)
        admin = repos.users.get_by_email("admin")
        repos.drive_settings.save_for_user(admin.id, DriveSettings("url", folder, None, None, False))
        repos.google_tokens.save_for_user(admin.id, GoogleToken("a", "r", "u", "c", "s", "sc", None))
        DeepgramKeyStore(repos.deepgram_credentials,
                         fernet_from_secret("a-long-secret-for-tests")).save_for_user(admin.id, "dg-key")
        client.post("/jobs/run-once", follow_redirects=False)
        jobs = repos.jobs.list_jobs_for_user(admin.id)
        assert len(jobs) == 1 and jobs[0].status == "pending"


def test_connect_google_redirects_with_state(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        _login(client)
        r = client.get("/connect-google", follow_redirects=False)
        assert "accounts.google.com" in r.headers["location"]
        assert "state=" in r.headers["location"]


def test_oauth_callback_saves_token_and_identity(tmp_path, monkeypatch):
    client, repos = _client(tmp_path)
    monkeypatch.setattr("app.web.main.exchange_google_code", lambda s, code: {
        "access_token": "access-token", "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token", "client_id": "client-id",
        "client_secret": "client-secret", "scopes": "https://www.googleapis.com/auth/drive",
        "expiry": "2026-06-03T00:00:00+00:00",
    })
    monkeypatch.setattr("app.web.main.fetch_google_userinfo",
                        lambda token: {"email": "me@gmail.com", "name": "Me"})
    with client:
        _login(client)
        connect = client.get("/connect-google", follow_redirects=False)
        state = parse_qs(urlparse(connect.headers["location"]).query)["state"][0]
        r = client.get(f"/oauth/google/callback?code=abc&state={state}", follow_redirects=False)
        assert r.status_code in {302, 303}
        admin = repos.users.get_by_email("admin")
        assert repos.google_tokens.get_for_user(admin.id) is not None
        assert admin and repos.users.get_by_id(admin.id).google_email == "me@gmail.com"


def test_oauth_callback_rejects_bad_state(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        _login(client)
        client.get("/connect-google", follow_redirects=False)
        r = client.get("/oauth/google/callback?code=abc&state=wrong")
        assert r.status_code == 400
```

- [ ] **Step 2: Create `tests/test_admin.py`**

```python
# tests/test_admin.py
from fastapi.testclient import TestClient

from app.web.config import WebSettings
from app.web.main import create_app
from app.web.passwords import hash_password
from tests.fakes import build_fake_repositories


def _settings(tmp_path) -> WebSettings:
    return WebSettings.from_env({
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "secret",
        "APP_SECRET_KEY": "a-long-secret-for-tests",
        "SESSION_COOKIE_SECURE": "false",
        "GOOGLE_WEB_CLIENT_ID": "client-id",
        "GOOGLE_WEB_CLIENT_SECRET": "client-secret",
        "GOOGLE_REDIRECT_URI": "http://localhost:8000/oauth/google/callback",
        "DATABASE_URL": "postgresql://test",
        "TMP_DIR": str(tmp_path / "tmp"),
    })


def _client(tmp_path, repos=None):
    repos = repos or build_fake_repositories()
    return TestClient(create_app(_settings(tmp_path), repositories=repos)), repos


def _login(client, username, password):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)


def test_admin_can_create_and_list_user(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client, "admin", "secret")
        client.post("/admin/users",
                    data={"email": "u@x.com", "password": "pw", "role": "user"},
                    follow_redirects=False)
        page = client.get("/admin/users").text
        assert "u@x.com" in page
        assert repos.users.get_by_email("u@x.com").role == "user"


def test_created_user_can_login(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client, "admin", "secret")
        client.post("/admin/users",
                    data={"email": "u@x.com", "password": "pw", "role": "user"},
                    follow_redirects=False)
        client.post("/logout", follow_redirects=False)
        r = _login(client, "u@x.com", "pw")
        assert r.status_code in {302, 303}
        assert r.headers["location"] == "/"


def test_non_admin_blocked_from_admin_routes(tmp_path):
    client, repos = _client(tmp_path)
    # Pre-seed a normal user before lifespan (bootstrap only adds the admin).
    repos.users.create(email="u@x.com", password_hash=hash_password("pw"), role="user")
    with client:
        _login(client, "u@x.com", "pw")
        assert client.get("/admin/users", follow_redirects=False).status_code == 403
        assert client.post("/admin/users",
                           data={"email": "z@x.com", "password": "p", "role": "user"},
                           follow_redirects=False).status_code == 403


def test_admin_disable_enable_and_reset_password(tmp_path):
    client, repos = _client(tmp_path)
    with client:
        _login(client, "admin", "secret")
        client.post("/admin/users",
                    data={"email": "u@x.com", "password": "pw", "role": "user"},
                    follow_redirects=False)
        uid = repos.users.get_by_email("u@x.com").id

        client.post(f"/admin/users/{uid}/disable", follow_redirects=False)
        assert repos.users.get_by_id(uid).is_active is False
        client.post(f"/admin/users/{uid}/enable", follow_redirects=False)
        assert repos.users.get_by_id(uid).is_active is True

        old_hash = repos.users.get_password_hash(uid)
        client.post(f"/admin/users/{uid}/reset-password",
                    data={"new_password": "newpw"}, follow_redirects=False)
        assert repos.users.get_password_hash(uid) != old_hash
```

- [ ] **Step 3: Run the route tests**

Run: `python -m pytest tests/test_web_routes.py tests/test_admin.py -v`
Expected: PASS (all)

- [ ] **Step 4: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add tests/test_web_routes.py tests/test_admin.py
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "add route tests for auth, settings, admin and oauth with fakes"
```

---

## Task 13: Remove SQLite layer

**Files:**
- Delete: `app/db.py`, `tests/test_db.py`

- [ ] **Step 1: Confirm nothing imports `app.db` anymore**

Run: `grep -rn "from app import db\|import app.db\|from app.db" app tests`
Expected: no matches.

- [ ] **Step 2: Delete the files**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth rm app/db.py tests/test_db.py
```

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS, no `app.db` import errors.

- [ ] **Step 4: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "remove SQLite db layer and its tests (PostgreSQL is the source of truth)"
```

---

## Task 14: Update .env.example and docker-compose docs

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml` (drop unused `./data` mount on web; no SQLite)

- [ ] **Step 1: Edit `.env.example`** — replace the top `DEEPGRAM_API_KEY` comment context and the `DATABASE_URL` block:

Change the `DATABASE_URL` lines to:
```
# Web UI mode: PostgreSQL DSN consumed by the postgres-core repository layer.
DATABASE_URL=postgresql+psycopg://app:app@db:5432/meet
```
And add this note under the `DEEPGRAM_API_KEY` line:
```
# Note: DEEPGRAM_API_KEY is used by the CLI worker only. The Web UI uses
# per-user Deepgram keys saved (encrypted) in the app, not this env var.
```

- [ ] **Step 2: Edit `docker-compose.yml`** — the `web` service no longer needs the `./data` SQLite volume. Replace the `web` service `volumes:` with only tmp and secrets:

```yaml
    volumes:
      - ./tmp:/app/tmp
      - ./secrets:/app/secrets:ro
```

- [ ] **Step 3: Validate compose**

Run: `docker compose config >/dev/null && echo OK`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth add .env.example docker-compose.yml
git -C /home/gabedsam01/Documentos/meet-transcription-auth commit -m "document PostgreSQL DSN and per-user Deepgram key in env/compose"
```

---

## Task 15: Full validation

- [ ] **Step 1: Run the test suite**

Run: `python -m pytest -v`
Expected: all green; no SQLite, no `app.db`.

- [ ] **Step 2: Compile all**

Run: `python -m compileall app scripts`
Expected: exit 0, no errors.

- [ ] **Step 3: Compose config + build**

Run: `docker compose config >/dev/null && docker compose build`
Expected: config OK; build succeeds (installs `bcrypt`).

- [ ] **Step 4: Grep guard — no secrets logged, no sqlite**

Run: `grep -rn "sqlite\|app.db" app || echo "no sqlite refs"`
Expected: `no sqlite refs`.

- [ ] **Step 5: Final commit (if any docs/cleanup pending)**

```bash
git -C /home/gabedsam01/Documentos/meet-transcription-auth status
```

---

## Self-Review (completed during planning)

- **Spec coverage:** Auth/roles/bootstrap (T3, T9, T12/T_admin), admin UI routes (T9, T11, T12), Google OAuth per user + identity (T9, T12), Deepgram per-user encrypted + test + gate (T5, T8, T9, T12), Drive settings by URL + extraction (T4, T9, T11, T12), route permissions (T9, T12), nav (T10), security/no-logging (T5, T9), no-SQLite/contract (T1, T2, T13), tests (T1–T12), validation (T15), integration points (T1 docstring, spec). All covered.
- **Placeholder scan:** none — every code step has full code.
- **Type consistency:** `User`/`GoogleToken`/`DriveSettings`/`Job` fields and repo method names (`create_job`, `list_jobs_for_user`, `find_active_for_user`, `get_password_hash`, `ensure_admin`, `set_google_identity`, `get_encrypted_for_user`) are used identically across tasks. `EnqueueResult.status` values match `RUN_ONCE_MESSAGES` keys.
