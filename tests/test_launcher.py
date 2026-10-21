import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import zipfile

import pytest

import launch_workbench as launcher
from kea_workbench.__main__ import bind_listener


@pytest.mark.parametrize("payload,expected", [
    ({"application": "kea2-workbench", "root": str(Path("/work"))}, True),
    ({"application": "kea2-workbench", "root": str(Path("/other"))}, False),
    ({"application": "other", "root": str(Path("/work"))}, False),
    ([], False),
])
def test_readiness_rejects_other_services_and_directories(monkeypatch, payload, expected):
    response = lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(launcher, "build_opener", lambda *args: SimpleNamespace(open=response))
    assert launcher.healthy("http://127.0.0.1:4320/", Path("/work")) is expected


def test_readiness_accepts_redirected_python_pid_and_rejects_stale_launch(tmp_path, monkeypatch):
    state = tmp_path / "ready.json"
    state.write_text(json.dumps({
        "root": str(tmp_path), "port": 4320, "pid": 222, "launchId": "current",
    }), encoding="utf-8")
    process = SimpleNamespace(pid=111, poll=lambda: None)
    monkeypatch.setattr(launcher, "healthy", lambda url, root: True)
    assert launcher.wait_ready(process, tmp_path, state, "current", timeout=1) == (
        "http://127.0.0.1:4320/"
    )
    assert launcher.saved_address(state, tmp_path, "previous") is None
    assert launcher.saved_address(state, tmp_path / "other", "current") is None
    # A healthy service plus an old state file must not satisfy a fresh launch.
    with pytest.raises(RuntimeError, match="超时"):
        launcher.wait_ready(process, tmp_path, state, "previous", timeout=0.01)
    assert launcher.saved_address(state, tmp_path) == "http://127.0.0.1:4320/"
    legacy = json.loads(state.read_text(encoding="utf-8"))
    del legacy["launchId"]
    state.write_text(json.dumps(legacy), encoding="utf-8")
    assert launcher.saved_address(state, tmp_path, "current") is None
    assert launcher.saved_address(state, tmp_path) == "http://127.0.0.1:4320/"


@pytest.mark.parametrize("ready", [True, False])
def test_dependency_setup_reuses_environment_or_installs_from_explicit_index(
    tmp_path, monkeypatch, ready,
):
    (tmp_path / ".venv").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion="0.1.2"\ndependencies=["httpx==0.28.1"]\n', encoding="utf-8",
    )
    calls = []

    # The first call checks the interpreter, the second checks packages, and
    # (when ready) the third checks transitive dependencies.
    answers = iter([0, int(not ready), 0])
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=next(answers)))
    monkeypatch.setattr(launcher, "run_checked", lambda command, **kwargs: calls.append(command))
    monkeypatch.delenv("KEA2_PIP_INDEX_URL", raising=False)
    launcher.prepare_python(tmp_path, {})
    if ready:
        assert not calls
    else:
        assert calls[0][1:] == ["-m", "pip", "install", "--index-url", "https://pypi.org/simple", "."]
        assert calls[-1][1:] == ["-m", "pip", "check"]


def make_adb_archive(name, malicious=False):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("platform-tools/" + name, b"fake ADB")
        if malicious:
            archive.writestr("../outside", b"invalid")
    content.seek(0)
    return content


@pytest.mark.parametrize("malicious", [False, True])
def test_adb_download_stays_in_local_tools_directory(tmp_path, monkeypatch, malicious):
    name = "adb.exe" if sys.platform == "win32" else "adb"
    archive = make_adb_archive(name, malicious)
    monkeypatch.setattr(launcher, "urlopen", lambda *a, **k: archive)
    if malicious:
        with pytest.raises(RuntimeError, match="不合法路径"):
            launcher.download_adb(tmp_path)
        assert not (tmp_path.parent / "outside").exists()
    else:
        directory = launcher.download_adb(tmp_path)
        executable = directory / name
        assert executable.read_bytes() == b"fake ADB"
        if sys.platform != "win32":
            assert executable.stat().st_mode & 0o111


def test_launch_lock_prevents_duplicate_preparation_and_releases_on_close(tmp_path):
    first = launcher.acquire_lock(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="已有窗口"):
            launcher.acquire_lock(tmp_path)
    finally:
        first.close()
    launcher.acquire_lock(tmp_path).close()


def test_missing_adb_uses_virtual_environment_certificates_and_local_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(launcher.shutil, "which", lambda *a, **k: None)
    python = tmp_path / ".venv/python"
    queries = []
    monkeypatch.setattr(
        launcher.subprocess, "check_output",
        lambda command, **kwargs: queries.append(command) or "/venv/certifi/cacert.pem\n",
    )
    directory = tmp_path / "tools/platform-tools"
    downloads = []
    monkeypatch.setattr(
        launcher, "download_adb",
        lambda root, certificates: downloads.append((root, certificates)) or directory,
    )
    commands = []
    monkeypatch.setattr(launcher, "run_checked", lambda command, **kwargs: commands.append(command))
    env = {"PATH": "original"}
    launcher.prepare_adb(tmp_path, env, python)
    assert queries[0][0] == str(python)
    assert downloads == [(tmp_path, "/venv/certifi/cacert.pem")]
    assert env["PATH"].startswith(str(directory))
    assert commands[0][-1] == "version"


def test_listener_reserves_preferred_port_and_falls_back_without_a_race():
    first = bind_listener("127.0.0.1", 0)
    port = first.getsockname()[1]
    try:
        second = bind_listener("127.0.0.1", port)
        try:
            assert second.getsockname()[1] != port
            with socket.socket() as connection:
                assert connection.connect_ex(("127.0.0.1", port)) == 0
        finally:
            second.close()
    finally:
        first.close()
    restarted = bind_listener("127.0.0.1", port)
    try:
        assert restarted.getsockname()[1] == port
    finally:
        restarted.close()


@pytest.mark.parametrize("occupied", [False, True])
@pytest.mark.parametrize("redirected", [False, True])
def test_real_service_is_ready_before_browser_and_duplicate_launch_reuses_it(
    tmp_path, monkeypatch, occupied, redirected,
):
    repository = Path(__file__).resolve().parents[1]
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "configs").mkdir()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "prepare_adb", lambda *args: None)

    def python(root, env):
        env["PYTHONPATH"] = str(repository)
        return Path(sys.executable)

    monkeypatch.setattr(launcher, "prepare_python", python)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    if not occupied:
        listener.close()
    monkeypatch.setenv("KEA2_PORT", str(port))
    original_popen = subprocess.Popen
    processes = []

    def popen(*args, **kwargs):
        if redirected:
            args = ([
                sys.executable, "-c",
                "import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))",
                *args[0],
            ], *args[1:])
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    opened = []

    def browser(url):
        assert launcher.healthy(url, tmp_path)
        state = json.loads((tmp_path / "data/launcher.json").read_text(encoding="utf-8"))
        assert state["launchId"]
        if redirected:
            assert state["pid"] != processes[-1].pid
        if occupied:
            assert url != f"http://127.0.0.1:{port}/"
        else:
            assert url == f"http://127.0.0.1:{port}/"
        assert launcher.saved_address(tmp_path / "data/launcher.json", tmp_path) == url
        opened.append(url)
        count = len(processes)
        monkeypatch.setattr(launcher, "open_page", opened.append)
        assert launcher.main() == 0
        assert len(processes) == count
        processes[-1].stdin.write(b"s")
        processes[-1].stdin.flush()

    monkeypatch.setattr(launcher, "open_page", browser)
    try:
        assert launcher.main() == 0
        assert len(opened) == 2
        # Restart immediately after HTTP connections close; TIME_WAIT sockets
        # must not be mistaken for a listening application.
        monkeypatch.setattr(launcher, "open_page", browser)
        assert launcher.main() == 0
        assert len(processes) == 2
        assert len(opened) == 4
    finally:
        listener.close()
        for process in processes:
            if process.poll() is None:
                launcher.stop_service(process)


def wait_for(test, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if test():
            return
        time.sleep(0.05)
    raise AssertionError("等待测试条件超时")


@pytest.mark.parametrize("stop_task", [False, True])
def test_task_progress_stop_and_native_report_with_launcher_watchdog_active(tmp_path, stop_task):
    repository = Path(__file__).resolve().parents[1]
    # Exercise the real subprocess boundaries without a device or Kea2 run.
    sdk = tmp_path / "sdk/kea2"
    sdk.mkdir(parents=True)
    (sdk / "__init__.py").touch()
    (sdk / "cli.py").write_text("""import sys
from pathlib import Path
assert sys.stdin.read() == ''
(Path(sys.argv[-1]) / 'bug_report.html').write_text('Native report', encoding='utf-8')
print('Native report generated', flush=True)
""", encoding="utf-8")
    adb_worker = """import sys
assert sys.stdin.read() == ''
if sys.argv[1:3] == ['devices', '-l']:
    print('List of devices attached\\nsmoke device model:Smoke')
elif sys.argv[4:6] == ['pm', 'path']:
    print('package:smoke')
"""
    worker = """import signal, sys, time
from pathlib import Path
if sys.platform == 'win32':
    signal.signal(signal.SIGBREAK, signal.default_int_handler)
assert sys.stdin.read() == ''
Path('raw/res_smoke').mkdir(parents=True)
print('[New Iteration] Elapsed: 1.0s', flush=True)
print('Sending monkeyEvent (125 / 99999)', flush=True)
try:
    while STOP_TASK:
        time.sleep(.1)
except KeyboardInterrupt:
    pass
print('Cleanup complete', flush=True)
""".replace("STOP_TASK", repr(stop_task))
    driver = """import sys, threading, time
from kea_workbench import engine
from kea_workbench.models import RunConfig
import kea_workbench.__main__ as cli
check_output = engine.subprocess.check_output
engine.subprocess.check_output = lambda command, **kwargs: check_output(
    [sys.executable, '-c', ADB_WORKER, *command[1:]], **kwargs)
engine.command = lambda *args: [sys.executable, '-u', '-c', WORKER]
original = cli.create_app
def app(root):
    application = original(root)
    def start():
        time.sleep(.2)
        application.state.manager.start(RunConfig(serial='smoke', platformEnabled=False))
    threading.Thread(target=start, daemon=True).start()
    return application
cli.create_app = app
sys.argv = ['server', '--root', sys.argv[1], '--port', '0', '--watch-parent']
cli.main()
""".replace("ADB_WORKER", repr(adb_worker)).replace("WORKER", repr(worker))
    with (tmp_path / "service.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", driver, str(tmp_path)], cwd=repository,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join([str(repository), str(sdk.parent)]),
                "PYTHONUTF8": "1",
            },
            stdin=subprocess.PIPE, stdout=log, stderr=log,
        )
        try:
            def progressed():
                logs = list((tmp_path / "artifacts").glob("*/console.log"))
                return logs and "Sending monkeyEvent (125" in logs[0].read_text(encoding="utf-8")

            # No input on the launcher's control pipe is needed to start a task.
            wait_for(progressed)
            if not stop_task:
                wait_for(lambda: list((tmp_path / "artifacts").glob("*/raw/*/bug_report.html")))
            assert process.poll() is None
            process.stdin.write(b"s")
            process.stdin.flush()
            assert process.wait(timeout=15) == 0
        finally:
            process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    directory = next((tmp_path / "artifacts").iterdir())
    record = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == ("cancelled" if stop_task else "completed")
    assert record["steps"] == 125
    assert "Cleanup complete" in record["recentLogs"]
    assert (directory / "index.html").is_file()
    assert (directory / "raw/res_smoke/bug_report.html").read_text(encoding="utf-8") == (
        "Native report"
    )
    assert "Native report generated" in (directory / "report-generation.log").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("termination", ["kill", "hangup", "terminal"])
def test_closing_launcher_releases_service_port(tmp_path, termination):
    if termination != "kill" and os.name == "nt":
        pytest.skip("SIGHUP / PTY 仅适用于 POSIX；父进程终止检测跨平台运行")
    repository = Path(__file__).resolve().parents[1]
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "configs").mkdir()
    driver = """import os, sys
from pathlib import Path
if sys.argv[2] == 'terminal':
    import fcntl, termios
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
import launch_workbench as launcher
launcher.ROOT = Path(sys.argv[1])
def python(root, env):
    return Path(sys.executable)
launcher.prepare_python = python
launcher.prepare_adb = lambda *args: None
launcher.open_page = lambda url: None
launcher.main()
"""
    env = {**os.environ, "PYTHONPATH": str(repository)}
    with socket.socket() as temporary:
        temporary.bind(("127.0.0.1", 0))
        env["KEA2_PORT"] = str(temporary.getsockname()[1])
    master = slave = None
    options = {}
    if termination == "terminal":
        import pty

        master, slave = pty.openpty()
        options = {"stdin": slave, "stdout": slave, "stderr": slave, "start_new_session": True}
    else:
        options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    process = subprocess.Popen([sys.executable, "-c", driver, str(tmp_path), termination], cwd=repository, env=env, **options)
    state = tmp_path / "data/launcher.json"
    url = None
    try:
        def ready():
            nonlocal url
            url = launcher.saved_address(state, tmp_path)
            return url and launcher.healthy(url, tmp_path)

        wait_for(ready)
        if termination == "terminal":
            os.close(master)
            master = None
        elif termination == "hangup":
            process.send_signal(signal.SIGHUP)
        else:
            process.kill()
        process.wait(timeout=10)
        port = json.loads(state.read_text())["port"]

        def released():
            with socket.socket() as connection:
                connection.settimeout(0.2)
                return connection.connect_ex(("127.0.0.1", port)) != 0

        wait_for(released)
        restarted = bind_listener("127.0.0.1", port)
        try:
            assert restarted.getsockname()[1] == port
        finally:
            restarted.close()
    finally:
        if master is not None:
            os.close(master)
        if slave is not None:
            os.close(slave)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if url and launcher.healthy(url, tmp_path):
            state_data = json.loads(state.read_text())
            os.kill(state_data["pid"], signal.SIGTERM)


def test_parent_disappearance_exits_even_if_shutdown_cleanup_blocks(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    state = tmp_path / "ready.json"
    driver = """import sys, time
import kea_workbench.__main__ as cli
original = cli.create_app
def blocked(root):
    app = original(root)
    app.state.manager.close = lambda: time.sleep(30)
    return app
cli.create_app = blocked
sys.argv = ['kea2-web', '--root', sys.argv[1], '--port', '0', '--watch-parent', '--ready-file', sys.argv[2]]
cli.main()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", driver, str(tmp_path), str(state)],
        cwd=repository, env={**os.environ, "PYTHONPATH": str(repository)},
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_for(lambda: (url := launcher.saved_address(state, tmp_path)) and launcher.healthy(url, tmp_path))
        port = json.loads(state.read_text())["port"]
        process.stdin.close()
        process.wait(timeout=9)
        assert process.returncode == 0
        restarted = bind_listener("127.0.0.1", port)
        try:
            assert restarted.getsockname()[1] == port
        finally:
            restarted.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if not process.stdin.closed:
            process.stdin.close()
