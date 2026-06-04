"""RedisTranscriptionQueue against an in-process fake redis client.

No real Redis is required (and none is installed in CI). The fake implements
exactly the subset of the redis-py API the queue uses, so these tests pin the
Redis key layout and command choices without a server.
"""

from app.queue.redis_queue import RedisTranscriptionQueue


class FakeRedis:
    def __init__(self):
        self.sets: dict[str, set] = {}
        self.lists: dict[str, list] = {}
        self.kv: dict[str, str] = {}

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
