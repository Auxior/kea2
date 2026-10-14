import io
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

import launch_workbench as launcher


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


def test_wrong_service_port_is_rejected_before_installation(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "healthy", lambda *args: False)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        monkeypatch.setenv("KEA2_PORT", str(listener.getsockname()[1]))
        with pytest.raises(RuntimeError, match="其他服务占用"):
            launcher.main()


def test_real_service_is_ready_before_browser_and_duplicate_launch_reuses_it(tmp_path, monkeypatch):
    repository = Path(__file__).resolve().parents[1]
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "configs").mkdir()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "prepare_adb", lambda *args: None)

    def python(root, env):
        env["PYTHONPATH"] = str(repository)
        return Path(sys.executable)

    monkeypatch.setattr(launcher, "prepare_python", python)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    monkeypatch.setenv("KEA2_PORT", str(port))
    original_popen = subprocess.Popen
    processes = []

    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    opened = []

    def browser(url):
        assert launcher.healthy(url, tmp_path)
        opened.append(url)
        count = len(processes)
        monkeypatch.setattr(launcher, "open_page", opened.append)
        assert launcher.main() == 0
        assert len(processes) == count
        processes[-1].send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)

    monkeypatch.setattr(launcher, "open_page", browser)
    try:
        assert launcher.main() == 0
        assert len(opened) == 2
    finally:
        for process in processes:
            if process.poll() is None:
                launcher.stop_service(process)
