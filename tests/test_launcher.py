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

ISOLATED_SERVER = (
    "import kea_workbench.__main__ as cli; "
    "cli.ensure_report_hosts = lambda: None; cli.main()"
)


@pytest.fixture(autouse=True)
def isolate_system_hosts(monkeypatch):
    # Launcher integration tests must never change the developer's hosts file.
    monkeypatch.setattr(launcher, "ensure_report_hosts", lambda: None)


@pytest.mark.parametrize("payload,expected", [
    ({"application": "kea2-workbench", "root": "/work"}, True),
    ({"application": "kea2-workbench", "root": "/other"}, False),
    ({"application": "other", "root": "/work"}, False),
    ([], False),
])
def test_readiness_rejects_other_services_and_directories(monkeypatch, payload, expected):
    response = lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(launcher, "build_opener", lambda *args: SimpleNamespace(open=response))
    assert launcher.healthy("http://127.0.0.1:4320/", Path("/work")) is expected


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
def test_real_service_is_ready_before_browser_and_duplicate_launch_reuses_it(tmp_path, monkeypatch, occupied):
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
        command, *rest = args
        if command[1:3] == ["-m", "kea_workbench"]:
            command = [command[0], "-c", ISOLATED_SERVER, *command[3:]]
        process = original_popen(command, *rest, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    opened = []

    def browser(url):
        assert launcher.healthy(url, tmp_path)
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
launcher.ensure_report_hosts = lambda: None
launcher.open_page = lambda url: None
original_popen = launcher.subprocess.Popen
def isolated_popen(command, **kwargs):
    if command[1:3] == ['-m', 'kea_workbench']:
        code = 'import kea_workbench.__main__ as cli; cli.ensure_report_hosts = lambda: None; cli.main()'
        command = [command[0], '-c', code, *command[3:]]
    return original_popen(command, **kwargs)
launcher.subprocess.Popen = isolated_popen
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
cli.ensure_report_hosts = lambda: None
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
