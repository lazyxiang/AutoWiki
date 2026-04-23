from worker.main import WorkerSettings


def test_worker_limits_concurrent_jobs_to_two():
    assert WorkerSettings.max_jobs == 2
