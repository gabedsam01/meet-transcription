"""RedisTranscriptionQueue against an in-process fake redis client.

No real Redis is required (and none is installed in CI). The fake implements
exactly the subset of the redis-py API the queue uses, so these tests pin the
Redis key layout and command choices without a server.
"""

from app.queue.redis_queue import RedisTranscriptionQueue


class _FakeScript:
    """Mirrors redis-py's Script callable for the two Lua scripts the queue uses.

    The fake exists to pin the queue's exact Redis usage without a server, so it
    is allowed to know these scripts; it runs the Python equivalent atomically
    (single-threaded test) against the fake's own zset/kv storage.
    """

    def __init__(self, fake, src):
        self._fake = fake
        self._src = src

    def __call__(self, keys=None, args=None, client=None):
        return self._fake._run_script(self._src, keys or [], args or [])


class FakeRedis:
    def __init__(self):
        self.sets: dict[str, set] = {}
        self.lists: dict[str, list] = {}
        self.kv: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def sadd(self, key, member):
        bucket = self.sets.setdefault(key, set())
        member = str(member)
        if member in bucket:
            return 0
        bucket.add(member)
        return 1

    def srem(self, key, member):
        self.sets.get(key, set()).discard(str(member))
        return 1

    def scard(self, key):
        return len(self.sets.get(key, set()))

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def llen(self, key):
        return len(self.lists.get(key, []))

    def zrem(self, key, member):
        self.zsets.get(key, {}).pop(str(member), None)
        return 1

    def register_script(self, src):
        return _FakeScript(self, src)

    def _run_script(self, src, keys, args):
        if "ACQUIRE_CLOUD_SLOT" in src:
            sem_key = keys[0]
            now, expiry, cap, token = float(args[0]), float(args[1]), int(args[2]), str(args[3])
            z = self.zsets.setdefault(sem_key, {})
            for member in [m for m, score in z.items() if score <= now]:
                del z[member]
            if len(z) < cap:
                z[token] = expiry
                return token
            return None
        if "RELEASE_IF_OWNER" in src:
            lock_key = keys[0]
            token = str(args[0])
            if self.kv.get(lock_key) == token:
                self.kv.pop(lock_key, None)
                return 1
            return 0
        raise AssertionError(f"unknown script: {src!r}")

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, str(value))

    def lpos(self, key, value):
        bucket = self.lists.get(key, [])
        value = str(value)
        return bucket.index(value) if value in bucket else None

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(str(value))

    def brpop(self, key, timeout=0):
        bucket = self.lists.get(key, [])
        if not bucket:
            return None
        return (key, bucket.pop())  # pop from the tail = FIFO

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, key):
        self.kv.pop(key, None)
        return 1

    def ping(self):
        return True


def test_enqueue_dedupes_via_set_and_uses_transcription_keys():
    r = FakeRedis()
    q = RedisTranscriptionQueue(r, queue_name="t")
    assert q.enqueue(7) is True
    assert q.enqueue(7) is False
    assert r.lists["t:queue"] == ["7"]
    assert r.sets["t:queued"] == {"7"}


def test_dequeue_pops_fifo_and_clears_set():
    r = FakeRedis()
    q = RedisTranscriptionQueue(r, queue_name="t")
    q.enqueue(1)
    q.enqueue(2)
    assert q.dequeue(0) == 1
    assert q.dequeue(0) == 2
    assert q.dequeue(0) is None
    assert r.sets["t:queued"] == set()


def test_ensure_queued_reheals_an_orphaned_set_entry():
    # Simulate the BRPOP-then-SREM crash: the id is left in the dedupe set but not
    # in the list. ensure_queued (used by reconciliation) must re-push it anyway so
    # the pending Postgres job is never stuck forever.
    r = FakeRedis()
    q = RedisTranscriptionQueue(r, queue_name="t")
    r.sets["t:queued"] = {"5"}  # orphan: in set, absent from list
    assert q.ensure_queued(5) is True
    assert r.lists["t:queue"] == ["5"]
    assert q.ensure_queued(5) is False  # now really queued -> no duplicate


def test_global_lock_uses_set_nx_ex():
    r = FakeRedis()
    q = RedisTranscriptionQueue(r, queue_name="t")
    token = q.acquire_global_lock(120)
    assert token is not None
    assert r.kv["t:global_lock"] == token
    assert q.acquire_global_lock(120) is None  # nx blocks a second holder
    q.release_global_lock(token)
    assert q.acquire_global_lock(120) is not None


def test_release_with_wrong_token_keeps_lock():
    r = FakeRedis()
    q = RedisTranscriptionQueue(r, queue_name="t")
    token = q.acquire_global_lock(120)
    q.release_global_lock("wrong-token")
    assert r.kv.get("t:global_lock") == token  # not released by a foreign token


def test_health_pings_and_degrades_gracefully():
    assert RedisTranscriptionQueue(FakeRedis(), queue_name="t").health() is True

    class DownRedis(FakeRedis):
        def ping(self):
            raise RuntimeError("connection refused")

    assert RedisTranscriptionQueue(DownRedis(), queue_name="t").health() is False


# --- provider concurrency slots ---------------------------------------------


def _clocked_queue(cloud_concurrency=5):
    clock = {"t": 0.0}
    r = FakeRedis()
    q = RedisTranscriptionQueue(
        r, queue_name="t", cloud_concurrency=cloud_concurrency,
        time_fn=lambda: clock["t"],
    )
    return q, r, clock


def test_cloud_semaphore_allows_five_blocks_sixth():
    q, _r, _clock = _clocked_queue(cloud_concurrency=5)
    tokens = [q.acquire_provider_slot("cloud", 3600) for _ in range(5)]
    assert all(tokens) and len(set(tokens)) == 5
    assert q.acquire_provider_slot("cloud", 3600) is None  # 6th over capacity
    q.release_provider_slot("cloud", tokens[0])
    assert q.acquire_provider_slot("cloud", 3600) is not None  # a slot freed


def test_cloud_semaphore_reclaims_expired_slots():
    q, _r, clock = _clocked_queue(cloud_concurrency=5)
    for _ in range(5):
        q.acquire_provider_slot("cloud", 10)  # expire at t=10
    clock["t"] = 5.0
    assert q.acquire_provider_slot("cloud", 10) is None  # none expired yet
    clock["t"] = 20.0
    # All five slots have expired (score 10 <= now 20) -> a new acquire reclaims one.
    assert q.acquire_provider_slot("cloud", 10) is not None


def test_local_lock_serializes_and_is_token_safe():
    q, r, _clock = _clocked_queue()
    t1 = q.acquire_provider_slot("local", 3600)
    assert t1 is not None
    assert r.kv["lock:local"] == t1
    assert q.acquire_provider_slot("local", 3600) is None  # 2nd local waits
    q.release_provider_slot("local", "wrong-token")
    assert r.kv.get("lock:local") == t1  # foreign release is a no-op
    q.release_provider_slot("local", t1)
    assert q.acquire_provider_slot("local", 3600) is not None


def test_processing_dead_sets_and_stats():
    q, _r, _clock = _clocked_queue()
    q.enqueue(1)
    q.enqueue(2)
    q.mark_processing(10)
    q.mark_dead(20)
    q.mark_dead(21)
    q.remove_dead(21)
    stats = q.queue_stats()
    assert stats == {"queued": 2, "processing": 1, "dead": 1}
    assert q.dead_job_ids() == {20}
