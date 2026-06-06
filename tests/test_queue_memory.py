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


def test_health_is_always_true_in_process():
    assert InMemoryTranscriptionQueue().health() is True


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


# --- provider concurrency slots ---------------------------------------------


def test_cloud_semaphore_allows_up_to_capacity_then_blocks():
    q = InMemoryTranscriptionQueue(cloud_concurrency=2)
    t1 = q.acquire_provider_slot("cloud", 60)
    t2 = q.acquire_provider_slot("cloud", 60)
    assert t1 and t2 and t1 != t2
    assert q.acquire_provider_slot("cloud", 60) is None  # 3rd over capacity
    q.release_provider_slot("cloud", t1)
    assert q.acquire_provider_slot("cloud", 60) is not None  # a slot freed


def test_local_lock_serializes_to_one():
    q = InMemoryTranscriptionQueue()
    t1 = q.acquire_provider_slot("local", 60)
    assert t1 is not None
    assert q.acquire_provider_slot("local", 60) is None  # 2nd local waits
    q.release_provider_slot("local", t1)
    assert q.acquire_provider_slot("local", 60) is not None


def test_release_provider_slot_with_wrong_token_keeps_local_lock():
    q = InMemoryTranscriptionQueue()
    t1 = q.acquire_provider_slot("local", 60)
    q.release_provider_slot("local", "wrong")
    assert q.acquire_provider_slot("local", 60) is None  # still held
    q.release_provider_slot("local", t1)
    assert q.acquire_provider_slot("local", 60) is not None


def test_processing_and_dead_sets_feed_queue_stats():
    q = InMemoryTranscriptionQueue()
    q.enqueue(1)
    q.enqueue(2)
    q.mark_processing(10)
    q.mark_processing(11)
    q.clear_processing(10)
    q.mark_dead(20)
    q.mark_dead(21)
    q.remove_dead(21)

    stats = q.queue_stats()
    assert stats["queued"] == 2
    assert stats["processing"] == 1
    assert stats["dead"] == 1
    assert q.dead_job_ids() == {20}
