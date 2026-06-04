from app.queue.memory_queue import InMemoryTranscriptionQueue


def test_enqueue_dedupes_same_job_id():
    q = InMemoryTranscriptionQueue()
    assert q.enqueue(5) is True
    assert q.enqueue(5) is False  # already queued -> not added twice
    assert q.queued_job_ids() == {5}


def test_dequeue_is_fifo_and_clears_queued_marker():
    q = InMemoryTranscriptionQueue()
    q.enqueue(1)
    q.enqueue(2)
    assert q.dequeue(0) == 1
    assert q.dequeue(0) == 2
    assert q.dequeue(0) is None
    assert q.queued_job_ids() == set()
    # Once dequeued the id can be enqueued again (e.g. a reprocess).
    assert q.enqueue(1) is True


def test_requeue_puts_job_back_for_retry():
    q = InMemoryTranscriptionQueue()
    q.enqueue(1)
    assert q.dequeue(0) == 1
    q.requeue(1)
    assert q.queued_job_ids() == {1}
    assert q.dequeue(0) == 1


def test_ensure_queued_is_idempotent():
    q = InMemoryTranscriptionQueue()
    assert q.ensure_queued(9) is True
    assert q.ensure_queued(9) is False  # already in the list -> not duplicated
    assert q.queued_job_ids() == {9}


def test_global_lock_is_mutually_exclusive():
    q = InMemoryTranscriptionQueue()
    token = q.acquire_global_lock(60)
    assert token is not None
    assert q.acquire_global_lock(60) is None  # second acquire blocked while held
    q.release_global_lock(token)
    assert q.acquire_global_lock(60) is not None  # freed


def test_release_with_wrong_token_does_not_unlock():
    q = InMemoryTranscriptionQueue()
    token = q.acquire_global_lock(60)
    q.release_global_lock("not-the-token")
    assert q.acquire_global_lock(60) is None  # still held
    q.release_global_lock(token)
    assert q.acquire_global_lock(60) is not None
