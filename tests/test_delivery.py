import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from fastapi.testclient import TestClient

from kea_workbench import delivery, engine
from kea_workbench.models import PROJECTS, RunConfig
from kea_workbench.server import create_app
from kea_workbench.storage import write_json


@pytest.fixture
def settings():
    result = json.loads((Path(__file__).parents[1] / "settings.example.json").read_text(encoding="utf-8"))
    result["minio"].update(accessKey="unit", secretKey="unit", endpoint="http://storage.example")
    return result


def wait_for(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("delivery condition not reached")
        time.sleep(.005)


def stored_run(root, settings, status="completed"):
    record = dict(
        id="run-retry", configuration=RunConfig(serial="unit").model_dump(),
        project=PROJECTS["com.ugreen.iot"], device={"model": "Unit", "serial": "unit"},
        settings=settings, status=status, statusLabel="已完成", startedAt="2026-10-20T11:01:28Z",
        endedAt="2026-10-20T21:01:42Z", updatedAt="2026-10-21T01:14:16Z",
        qualifiedAt="2026-10-20T11:06:38Z", elapsedSeconds=36000.1, steps=32710,
        findings=0, recentLogs=[], error="",
        delivery=dict(status="failed", revision=557, taskId="existing-task", error="旧超时"),
    )
    directory = root / "artifacts" / record["id"]
    write_json(directory / "run.json", record)
    (directory / "console.log").write_text("Test log", encoding="utf-8")
    return record, directory


def test_large_file_budget_resumes_across_publishers_and_never_uploads_cache(tmp_path, settings, monkeypatch):
    clock = [0]
    sent = []

    class Storage:
        def fput_object(self, bucket, key, path, content_type, progress):
            with open(path, "rb") as source:
                content = source.read()
            sent.append((key, hashlib.sha256(content).hexdigest()))
            progress.update(len(content))
            clock[0] += 95  # One successful file may take longer than a slice.

    monkeypatch.setattr(delivery, "Minio", lambda *args, **kw: Storage())
    monkeypatch.setattr(delivery, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    for name in ["a.bin", "b.bin", "index.html"]:
        (tmp_path / name).write_text(name, encoding="utf-8")
    progress = []
    for _ in range(2):
        with pytest.raises(delivery.UploadDeferred):
            delivery.Publisher(settings).upload(tmp_path, "run", progress=progress.append)
    url = delivery.Publisher(settings).upload(tmp_path, "run", progress=progress.append)
    assert url.endswith("/run/index.html")
    assert [key.rsplit("/", 1)[-1] for key, _ in sent] == ["a.bin", "b.bin", "index.html"]
    assert progress[-1]["uploadedFiles"] == progress[-1]["totalFiles"] == 3
    assert progress[-1]["uploadedBytes"] == progress[-1]["totalBytes"]
    (tmp_path / "b.bin").write_text("changed", encoding="utf-8")
    with pytest.raises(delivery.UploadDeferred):
        delivery.Publisher(settings).upload(tmp_path, "run")
    assert sent[-1][0].endswith("/b.bin")
    assert all(".upload-state" not in key for key, _ in sent)
    # A different destination must not reuse confirmations from another run.
    with pytest.raises(delivery.UploadDeferred):
        delivery.Publisher(settings).upload(tmp_path, "other-run")
    assert sent[-1][0].endswith("/other-run/a.bin")


def test_network_failure_keeps_only_successful_file_confirmations(tmp_path, settings, monkeypatch):
    sent = []
    failing = [True]

    class Storage:
        def fput_object(self, bucket, key, path, **kwargs):
            sent.append(Path(path).name)
            if Path(path).name == "b.bin" and failing[0]:
                raise TimeoutError("storage read timeout")

    monkeypatch.setattr(delivery, "Minio", lambda *args, **kw: Storage())
    for name in ["a.bin", "b.bin", "index.html"]:
        (tmp_path / name).write_bytes(b"payload")
    with pytest.raises(TimeoutError):
        delivery.Publisher(settings).upload(tmp_path, "run")
    failing[0] = False
    delivery.Publisher(settings).upload(tmp_path, "run")
    assert sent == ["a.bin", "b.bin", "b.bin", "index.html"]


@pytest.mark.parametrize("failure", [delivery.UploadDeferred("slice exhausted"), TimeoutError("network timeout")])
def test_final_report_automatically_retries_and_confirms_final_revision(tmp_path, settings, monkeypatch, failure):
    record, directory = stored_run(tmp_path, settings)
    attempts, bodies = [], []

    class Publisher:
        def __init__(self, settings):
            pass

        def upload(self, snapshot, run_id, progress=None):
            attempts.append((snapshot / "run.json").read_bytes())
            if len(attempts) == 1:
                raise failure
            return "http://reports.example/run/index.html"

    monkeypatch.setattr(engine, "Publisher", Publisher)
    monkeypatch.setattr(engine, "SYNC_INTERVAL", .03)
    monkeypatch.setattr(engine, "send_report", lambda directory, body, settings: bodies.append(body) or "existing-task")
    manager = engine.Manager(tmp_path, settings)
    try:
        result = manager.retry(record["id"])
        assert result["delivery"]["status"] in {"queued", "preparing", "uploading"}
        runtime = manager.runs[record["id"]]
        wait_for(lambda: runtime.view()["delivery"]["status"] == "uploaded")
        assert attempts[0] == attempts[1]  # Continue the same immutable snapshot.
        assert len(bodies) == 1
        assert bodies[0]["externalFields"]["custom"]["sync"] == dict(
            revision=558, qualified=True, final=True, phase="completed", heartbeatSeconds=180,
        )
        assert runtime.view()["delivery"]["final"] is True
        assert not runtime.view()["delivery"]["busy"]
        assert not runtime.view()["delivery"]["canRetry"]
        assert '"status": "retrying"' in (directory / "delivery.log").read_text(encoding="utf-8")
    finally:
        manager.close()


def test_retry_api_accepts_immediately_deduplicates_and_wakes_sleeping_worker(tmp_path, settings, monkeypatch):
    record, _ = stored_run(tmp_path, settings)
    entered, release = threading.Event(), threading.Event()
    attempts, requests = [], []

    class Publisher:
        def __init__(self, settings):
            pass

        def upload(self, directory, run_id, progress=None):
            attempts.append(run_id)
            entered.set()
            assert release.wait(5)
            return "http://reports.example/index.html"

    def report(directory, body, settings):
        requests.append(body)
        if len(requests) == 1:
            raise TimeoutError("platform timeout")
        return "existing-task"

    monkeypatch.setattr(engine, "Publisher", Publisher)
    monkeypatch.setattr(engine, "send_report", report)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        runtime = app.state.manager.runs[record["id"]]
        try:
            response = client.post(f"/api/runs/{record['id']}/retry")
            assert response.status_code == 202
            assert response.json()["delivery"]["busy"] is True
            assert response.json()["delivery"]["error"] == ""
            assert entered.wait(3)
            worker = runtime.delivery_thread
            again = client.post(f"/api/runs/{record['id']}/retry")
            assert again.status_code == 202 and again.json()["delivery"]["busy"]
            assert attempts == [record["id"]]
            release.set()
            wait_for(lambda: runtime.view()["delivery"]["status"] == "retrying")
            waiting = runtime.view()["delivery"]
            assert waiting["canRetry"] and waiting["nextRetryAt"]
            response = client.post(f"/api/runs/{record['id']}/retry")
            assert response.status_code == 202
            wait_for(lambda: runtime.view()["delivery"]["status"] == "uploaded")
            assert runtime.delivery_thread is worker
            assert len(attempts) == len(requests) == 2
            # An uncertain acknowledgement retries the same revision and body.
            assert requests[0] == requests[1]
            assert runtime.view()["delivery"]["taskId"] == "existing-task"
        finally:
            release.set()


@pytest.mark.parametrize("run_status,delivery_status", [
    ("completed", "uploading"), ("running", "retrying"), ("completed", "uploaded"),
])
def test_restart_exposes_interrupted_delivery_as_retryable(tmp_path, settings, run_status, delivery_status):
    record, directory = stored_run(tmp_path, settings, status=run_status)
    record["delivery"].update(
        status=delivery_status, final=False, nextRetryAt="2026-10-21T01:15:00Z",
    )
    write_json(directory / "run.json", record)
    manager = engine.Manager(tmp_path, settings)
    result = manager.runs[record["id"]].view()
    assert result["delivery"]["status"] == "failed"
    assert result["delivery"]["canRetry"] and not result["delivery"]["busy"]
    assert result["delivery"]["nextRetryAt"] is None
    assert "可立即重试" in result["delivery"]["message"]


def test_restart_preserves_confirmed_final_delivery(tmp_path, settings):
    record, directory = stored_run(tmp_path, settings)
    record["delivery"].update(status="uploaded", final=True, error="")
    write_json(directory / "run.json", record)
    result = engine.Manager(tmp_path, settings).runs[record["id"]].view()
    assert result["delivery"]["status"] == "uploaded"
    assert result["delivery"]["final"] is True and not result["delivery"]["canRetry"]


def test_running_snapshot_survives_defer_and_final_snapshot_supersedes_it(tmp_path, settings, monkeypatch):
    record, directory = stored_run(tmp_path, settings, status="running")
    snapshots, bodies = [], []
    manager = engine.Manager(tmp_path, settings)
    runtime = manager.runs[record["id"]]
    # Restore a live runtime; Manager correctly treats a run loaded from disk as interrupted.
    runtime.record.update(status="running", error="")
    runtime.finished.clear()

    class Publisher:
        def __init__(self, settings):
            pass

        def upload(self, snapshot, run_id, progress=None):
            snapshots.append((snapshot / "console.log").read_bytes())
            if len(snapshots) == 1:
                (directory / "console.log").write_text("New live log", encoding="utf-8")
                raise delivery.UploadDeferred("next slice")
            return "http://reports.example/run/index.html"

    def report(directory, body, settings):
        bodies.append(body)
        if len(bodies) == 1:
            with runtime.lock:
                runtime.record.update(status="completed", statusLabel="已完成")
            runtime.finished.set()
            runtime.sync.set()
        return "existing-task"

    monkeypatch.setattr(engine, "Publisher", Publisher)
    monkeypatch.setattr(engine, "send_report", report)
    monkeypatch.setattr(engine, "SYNC_INTERVAL", .03)
    try:
        manager.retry(record["id"])
        wait_for(lambda: runtime.view()["delivery"].get("final") is True)
        assert snapshots == [b"Test log", b"Test log", b"New live log"]
        assert [body["externalFields"]["custom"]["sync"]["final"] for body in bodies] == [False, True]
        assert [body["externalFields"]["custom"]["sync"]["revision"] for body in bodies] == [558, 559]
    finally:
        manager.close()


def test_shutdown_interrupts_scheduled_retry_without_waiting_for_next_cycle(tmp_path, settings, monkeypatch):
    record, _ = stored_run(tmp_path, settings)

    class Publisher:
        def __init__(self, settings):
            pass

        def upload(self, *args, **kwargs):
            raise TimeoutError("offline")

    monkeypatch.setattr(engine, "Publisher", Publisher)
    manager = engine.Manager(tmp_path, settings)
    runtime = manager.runs[record["id"]]
    manager.retry(record["id"])
    wait_for(lambda: runtime.view()["delivery"]["status"] == "retrying")
    started = time.monotonic()
    manager.close()
    assert time.monotonic() - started < 1
    assert not runtime.delivery_thread.is_alive()
    assert runtime.view()["delivery"]["status"] == "failed"
    assert runtime.view()["delivery"]["nextRetryAt"] is None


def test_permanent_platform_error_waits_for_manual_retry(tmp_path, settings, monkeypatch):
    record, _ = stored_run(tmp_path, settings)
    calls = []

    class Publisher:
        def __init__(self, settings):
            pass

        def upload(self, *args, **kwargs):
            return "http://reports.example/index.html"

    def report(*args):
        calls.append(1)
        if len(calls) == 1:
            response = httpx.Response(422, request=httpx.Request("POST", "http://platform.example"))
            response.raise_for_status()
        return "existing-task"

    monkeypatch.setattr(engine, "Publisher", Publisher)
    monkeypatch.setattr(engine, "send_report", report)
    monkeypatch.setattr(engine, "SYNC_INTERVAL", .01)
    manager = engine.Manager(tmp_path, settings)
    runtime = manager.runs[record["id"]]
    try:
        manager.retry(record["id"])
        wait_for(lambda: runtime.view()["delivery"]["status"] == "failed")
        assert runtime.view()["delivery"]["nextRetryAt"] is None
        time.sleep(.06)
        assert len(calls) == 1
        manager.retry(record["id"])
        wait_for(lambda: runtime.view()["delivery"]["status"] == "uploaded")
        assert len(calls) == 2
    finally:
        manager.close()
