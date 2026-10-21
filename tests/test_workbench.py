import copy
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from kea_workbench import engine
from kea_workbench.delivery import eligible, payload, send_report
from kea_workbench.engine import Manager, Runtime, command
from kea_workbench.models import PROJECTS, RunConfig
from kea_workbench.reports import render_report
from kea_workbench.server import create_app
from kea_workbench.storage import load_settings, write_json


@pytest.fixture
def settings():
    return json.loads((Path(__file__).parents[1] / "settings.example.json").read_text(encoding="utf-8"))


@pytest.fixture
def record(settings):
    return {
        "id": "run-unit",
        "configuration": RunConfig(serial="device").model_dump(),
        "project": PROJECTS["com.ugreen.iot"],
        "device": {"model": "Unit Android", "serial": "device"},
        "settings": settings,
        "status": "running",
        "statusLabel": "运行中",
        "elapsedSeconds": 300,
        "steps": 15,
        "findings": 0,
        "startedAt": "2026-10-13T10:00:00+00:00",
        "updatedAt": "2026-10-13T10:05:00+00:00",
        "delivery": {"revision": 1},
        "recentLogs": [],
    }


def test_command_preserves_parameters_and_screenshot_switch(tmp_path):
    cfg = RunConfig(
        serial="4A101FDAP000B4",
        runningMinutes=5,
        maxStep=21,
        throttle=456,
        preFailureScreenshots=20,
        postFailureScreenshots=5,
    )
    args = command(cfg, tmp_path, "run-unit")
    for flag, value in [
        ("-s", cfg.serial),
        ("-p", cfg.package),
        ("--running-minutes", "5"),
        ("--max-step", "21"),
        ("--throttle", "456"),
        ("--pre-failure-screenshots", "20"),
        ("--post-failure-screenshots", "5"),
    ]:
        assert args[args.index(flag) + 1] == value
    assert "--take-screenshots" in args
    cfg.takeScreenshots = False
    assert "--take-screenshots" not in command(cfg, tmp_path, "run-unit")


@pytest.mark.parametrize(
    "fields",
    [
        {"runningMinutes": 0},
        {"maxStep": 0},
        {"throttle": -1},
        {"postFailureScreenshots": 21},
        {"package": "com.unknown.app"},
        {"serial": "device; echo injected"},
    ],
)
def test_invalid_inputs_are_rejected(fields):
    with pytest.raises(ValidationError):
        RunConfig(serial="device", **fields) if "serial" not in fields else RunConfig(**fields)


def test_unknown_app_can_run_without_platform():
    assert RunConfig(serial="device", package="com.unknown.app", platformEnabled=False)


def test_standalone_settings_fallback_and_local_utf8_override(tmp_path, settings):
    assert load_settings(tmp_path) == settings
    bundled = Path(engine.__file__).parent / "settings.example.json"
    assert json.loads(bundled.read_text(encoding="utf-8")) == settings
    settings["minio"]["prefix"] = "中文报告"
    write_json(tmp_path / "data/settings.json", settings)
    assert load_settings(tmp_path)["minio"]["prefix"] == "中文报告"
    assert "中文报告" in (tmp_path / "data/settings.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("source", ["data/settings.json", "settings.example.json"])
@pytest.mark.parametrize("value", [None, "", " \t\n"])
def test_unconfigured_minio_credentials_use_defaults(tmp_path, settings, source, value):
    configured = copy.deepcopy(settings)
    configured["minio"]["accessKey"] = value
    configured["minio"]["secretKey"] = value
    write_json(tmp_path / source, configured)
    effective = load_settings(tmp_path)
    for field in ("accessKey", "secretKey"):
        assert effective["minio"][field] == settings["minio"][field]


def test_missing_credentials_preserve_explicit_values_and_other_settings(tmp_path, settings):
    configured = copy.deepcopy(settings)
    configured["minio"].pop("secretKey")
    configured["minio"]["accessKey"] = "custom-access"
    configured["minio"]["prefix"] = "custom-prefix"
    write_json(tmp_path / "data/settings.json", configured)
    effective = load_settings(tmp_path)
    assert effective["minio"]["accessKey"] == "custom-access"
    assert effective["minio"]["secretKey"] == settings["minio"]["secretKey"]
    assert effective["minio"]["prefix"] == "custom-prefix"
    assert effective["platform"] == configured["platform"]


def test_settings_save_resolves_defaults_for_ui_runtime_and_restart(tmp_path, settings):
    configured = copy.deepcopy(settings)
    configured["minio"].pop("accessKey")
    configured["minio"]["secretKey"] = ""
    app = create_app(tmp_path)
    with TestClient(app) as client:
        response = client.put("/api/settings", json=configured)
        assert response.status_code == 200
        assert response.json()["settings"] == settings
        assert client.get("/api/config").json()["settings"] == settings
        assert app.state.manager.settings == settings
        assert load_settings(tmp_path) == settings

        configured["minio"].update(accessKey="custom-access", secretKey="custom-secret")
        response = client.put("/api/settings", json=configured)
        assert response.status_code == 200
        assert response.json()["settings"] == configured
        assert client.get("/api/config").json()["settings"] == configured
        assert load_settings(tmp_path) == configured


def test_windows_stop_uses_process_group_break_and_cleanup_handler(monkeypatch):
    from kea_workbench import runner

    signals = []
    monkeypatch.setattr(engine, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    monkeypatch.setattr(engine.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(engine.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    assert engine.process_options() == {"creationflags": 512}
    assert command(RunConfig(serial="device"), Path("run"), "id")[3] == "kea_workbench.runner"
    engine.interrupt_process(SimpleNamespace(send_signal=signals.append))
    assert signals == [1]

    import kea2.cli

    handlers = []
    monkeypatch.setattr(runner, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(runner.signal, "SIGBREAK", 21, raising=False)
    monkeypatch.setattr(runner.signal, "signal", lambda sig, handler: handlers.append((sig, handler)))
    monkeypatch.setattr(kea2.cli, "main", lambda: signals.append("kea2"))
    runner.main()
    assert signals[-1] == "kea2"
    assert handlers == [(21, runner.signal.default_int_handler)]
    with pytest.raises(KeyboardInterrupt):
        handlers[0][1](21, None)


def test_gate_excludes_preparation_and_latches_before_later_failure(tmp_path, record, settings):
    record["elapsedSeconds"] = 0
    runtime = Runtime(tmp_path, record, settings)
    runtime.log("Preparing device for 600 seconds")
    assert not eligible(runtime.view())
    runtime.log("[Property based testing] [New Iteration] Elapsed: 299.9s")
    runtime.qualify()
    assert not runtime.view().get("qualifiedAt")
    runtime.log("[Property based testing] [New Iteration] Elapsed: 300.0s")
    runtime.log("Sending monkeyEvent (123 / 99999)")
    runtime.qualify()
    assert runtime.view()["steps"] == 123
    assert runtime.view()["qualifiedAt"]
    runtime.record.update(status="interrupted", gateBlocked=True)
    assert eligible(runtime.view())
    runtime.record["configuration"]["runningMinutes"] = 4
    assert not eligible(runtime.view())


def test_failure_before_gate_never_qualifies(tmp_path, record, settings):
    record.update(elapsedSeconds=400, gateBlocked=True)
    assert not eligible(record)


def test_payload_has_project_stable_identity_and_actual_terminal_status(record, settings):
    first = payload(record, settings["platform"], "https://reports.example/runs/unit/index.html")
    record.update(status="cancelled", statusLabel="已取消", endedAt="2026-10-13T10:06:00+00:00")
    record["delivery"]["revision"] = 2
    final = payload(record, settings["platform"], "https://reports.example/runs/unit/index.html")
    assert first["projectId"] == "69684909ee73c95fe1643567"
    assert first["externalFields"]["status"] == "running"
    assert final["externalFields"]["status"] == "canceled"
    assert final["externalFields"]["custom"]["sync"]["final"]
    assert final["externalFields"]["custom"]["sync"]["revision"] == 3
    assert first["externalFields"]["externalTaskId"] == final["externalFields"]["externalTaskId"]
    assert first["externalFields"]["artifacts"] == final["externalFields"]["artifacts"]
    record["project"] = PROJECTS["com.ugreen.iot.connect"]
    assert (
        payload(record, settings["platform"], "https://reports.example")["projectId"]
        == "696eded40e4637c8b71e22a1"
    )


def test_platform_acknowledgement_verifies_project_and_revision(
    tmp_path, record, settings, monkeypatch
):
    body = payload(record, settings["platform"], "https://reports.example/index.html")
    task = {
        **{k: body[k] for k in ["serviceId", "capabilityId"]},
        **{
            k: body["externalFields"][k]
            for k in ["externalTaskId", "taskName", "status", "resultSummary"]
        },
        "taskId": "unit-task",
        "requestPayload": {"projectId": body["projectId"]},
        "externalFields": body["externalFields"],
    }
    monkeypatch.setattr("kea_workbench.delivery.time.sleep", lambda _: None)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200,
            json={"task": task},
            request=httpx.Request("POST", settings["platform"]["endpoint"]),
        ),
    )
    assert send_report(tmp_path, body, settings["platform"]) == "unit-task"
    task["requestPayload"]["projectId"] = "wrong"
    with pytest.raises(ValueError):
        send_report(tmp_path, body, settings["platform"])
    task["requestPayload"]["projectId"] = body["projectId"]
    task["externalFields"] = copy.deepcopy(body["externalFields"])
    task["externalFields"]["custom"]["sync"]["revision"] += 1
    with pytest.raises(ValueError):
        send_report(tmp_path, body, settings["platform"])


def test_report_retains_final_data_and_escapes_logs(tmp_path, record):
    record.update(status="interrupted", recentLogs=["<script>alert(1)</script>"])
    render_report(tmp_path, record)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "异常终止" in html and "300.0 秒" in html
    assert "&lt;script&gt;" in html and "<script>alert(1)</script>" not in html


def test_server_validates_requests_and_report_file_boundaries(tmp_path, settings):
    write_json(tmp_path / "settings.example.json", settings)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert (
            client.post("/api/runs", json={"serial": "device", "runningMinutes": -5}).status_code
            == 422
        )
        assert client.get("/api/runs/no-such-run").status_code == 404
        assert client.get("/api/config").json()["defaults"]["runningMinutes"] == 600


def wait_until(test, timeout=8):
    end = time.monotonic() + timeout
    while not test():
        if time.monotonic() > end:
            raise AssertionError("test condition not reached")
        time.sleep(0.02)


def test_restart_exposes_interrupted_report_and_sorts_by_start_time(tmp_path, record, settings):
    older = copy.deepcopy(record)
    older.update(id="z-older", qualifiedAt=record["updatedAt"])
    older["delivery"].update(status="uploaded", taskId="existing-task")
    newer = copy.deepcopy(record)
    newer.update(id="a-newer", startedAt="2026-10-13T11:00:00+00:00", elapsedSeconds=30)
    for run in [older, newer]:
        write_json(tmp_path / "artifacts" / run["id"] / "run.json", run)
    manager = Manager(tmp_path, settings)
    assert [r["id"] for r in manager.list()] == ["a-newer", "z-older"]
    recovered = manager.runs["z-older"].view()
    assert recovered["status"] == "interrupted"
    assert recovered["delivery"]["status"] == "failed"
    assert recovered["delivery"]["taskId"] == "existing-task"
    assert manager.runs["a-newer"].view()["delivery"]["status"] == "skipped"
    assert "异常终止" in (tmp_path / "artifacts/z-older/index.html").read_text(encoding="utf-8")


def test_background_upload_never_blocks_logs_and_cancellation_updates_same_task(
    tmp_path, settings, monkeypatch
):
    # Isolated child process and publisher; no real device or platform involved.
    settings["minio"].update(accessKey="unit", secretKey="unit")
    manager = Manager(tmp_path, settings)
    monkeypatch.setattr(
        engine,
        "devices",
        lambda: [{"serial": "unit", "state": "device", "model": "Unit"}],
    )
    monkeypatch.setattr(
        engine,
        "adb",
        lambda serial, *args: "package:unit" if args[1:3] == ("pm", "path") else "",
    )
    script = "import time, signal, sys\nif sys.platform == 'win32':\n signal.signal(signal.SIGBREAK, signal.default_int_handler)\nprint('[New Iteration] Elapsed: 300.0s', flush=True)\ntry:\n while True:\n  print('Sending monkeyEvent (125 / 99999)', flush=True)\n  time.sleep(.02)\nexcept KeyboardInterrupt:\n print('Finish sending monkey events.',flush=True)\n"
    monkeypatch.setattr(engine, "command", lambda *args: [sys.executable, "-u", "-c", script])
    entered, release = threading.Event(), threading.Event()
    requests = []

    class IsolatedPublisher:
        def __init__(self, settings):
            pass

        def upload(self, directory, run_id):
            entered.set()
            release.wait(5)
            return "https://reports.example/runs/" + run_id + "/index.html"

    monkeypatch.setattr(engine, "Publisher", IsolatedPublisher)
    monkeypatch.setattr(
        engine,
        "send_report",
        lambda directory, body, connection: requests.append(body) or "one-task",
    )
    created = manager.start(RunConfig(serial="unit", runningMinutes=6))
    runtime = manager.runs[created["id"]]
    try:
        assert entered.wait(5)
        wait_until(lambda: runtime.view()["steps"] == 125)
        with pytest.raises(ValueError, match="已有"):
            manager.start(RunConfig(serial="unit"))
        manager.stop_run(created["id"])
        wait_until(runtime.finished.is_set)
        release.set()
        runtime.delivery_thread.join(8)
        assert [p["externalFields"]["status"] for p in requests] == [
            "running",
            "canceled",
        ]
        assert [p["externalFields"]["custom"]["sync"]["revision"] for p in requests] == [1, 2]
        assert len({p["externalFields"]["externalTaskId"] for p in requests}) == 1
        assert (
            requests[0]["externalFields"]["artifacts"] == requests[1]["externalFields"]["artifacts"]
        )
        assert runtime.view()["delivery"]["taskId"] == "one-task"
        assert (runtime.directory / "index.html").is_file()
    finally:
        release.set()
        manager.close()
