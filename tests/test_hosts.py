import codecs
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import launch_workbench as launcher
from kea_workbench import hosts


HOST = hosts.REPORT_HOST
IP = hosts.REPORT_IP


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("encoding,bom", [
    ("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
    ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE),
])
def test_missing_mapping_keeps_original_bytes_and_encoding(newline, encoding, bom):
    original = bom + (f"# 注释{newline}127.0.0.1 localhost{newline}"
                      f"# 10.0.0.1 {HOST}{newline}").encode(encoding)
    result = hosts.updated_hosts(original)
    assert result.startswith(original)
    text = result[len(bom):].decode(encoding)
    assert text.endswith(f"{IP} {HOST} # Kea2 workbench temporary report mapping{newline}")
    assert hosts.updated_hosts(result) == result


@pytest.mark.parametrize("original", [
    f"{IP}\t{HOST} # keep original\n",
    f"{IP} alias.local {HOST.upper()}.\r\n",
    f"# comment\n{IP} {HOST}",
])
def test_single_correct_mapping_is_unchanged(original):
    assert hosts.updated_hosts(original.encode()) == original.encode()


def test_conflicting_duplicates_keep_other_aliases_comments_and_ansi_bytes():
    original = (
        b"# ANSI comment: \xe9\r\n127.0.0.1 localhost\r\n" +
        f"10.0.0.1 alias.local {HOST.upper()}. # shared entry\r\n"
        f"::1 {HOST} ipv6.local\r\n"
        f"{IP} {HOST} {HOST} # duplicate\r\n"
        f"# 10.0.0.2 {HOST}\r\n".encode()
    )
    result = hosts.updated_hosts(original)
    assert result.startswith(b"# ANSI comment: \xe9\r\n127.0.0.1 localhost\r\n")
    assert b"10.0.0.1\talias.local  # shared entry\r\n" in result
    assert b"::1\tipv6.local\r\n" in result
    assert b"# duplicate\r\n" in result
    assert f"# 10.0.0.2 {HOST}\r\n".encode() in result
    entries = [line.partition(b"#")[0].split() for line in result.splitlines()]
    assert [fields[0] for fields in entries if HOST.encode() in fields[1:]] == [IP.encode()]
    assert hosts.updated_hosts(result) == result


@pytest.mark.parametrize("original", [b"", b"127.0.0.1 localhost"])
def test_empty_file_or_missing_final_newline_is_supported(original):
    result = hosts.updated_hosts(original)
    assert result.startswith(original)
    entries = [line.split() for line in result.splitlines()]
    assert any(fields[:2] == [IP.encode(), HOST.encode()] for fields in entries)


def test_file_update_is_idempotent_and_preserves_file_permissions(tmp_path):
    path = tmp_path / "hosts"
    path.write_bytes(f"127.0.0.1 localhost\n10.0.0.1 {HOST}\n".encode())
    mode = path.stat().st_mode
    assert hosts.apply_mapping(path)
    final = path.read_bytes()
    assert not hosts.apply_mapping(path)
    assert path.read_bytes() == final
    assert path.stat().st_mode == mode


@pytest.fixture
def system_hosts(tmp_path, monkeypatch):
    path = tmp_path / "hosts"
    path.write_bytes(b"127.0.0.1 localhost\n")
    monkeypatch.setattr(hosts, "hosts_path", lambda: path)
    monkeypatch.setattr(hosts, "sys", SimpleNamespace(platform="darwin", executable=sys.executable))
    return path


def test_correct_mapping_does_not_write_elevate_or_flush(system_hosts, monkeypatch, capsys):
    original = f"{IP} {HOST}\n".encode()
    system_hosts.write_bytes(original)

    def unexpected():
        pytest.fail("正确映射不应请求权限或刷新 DNS")

    monkeypatch.setattr(hosts, "is_admin", unexpected)
    monkeypatch.setattr(hosts, "apply_and_flush", unexpected)
    monkeypatch.setattr(hosts, "elevate_update", unexpected)
    hosts.ensure_report_hosts()
    assert system_hosts.read_bytes() == original
    assert "跳过" in capsys.readouterr().out


def test_admin_update_and_dns_flush_complete_before_success(system_hosts, monkeypatch, capsys):
    monkeypatch.setattr(hosts, "is_admin", lambda: True)
    flushed = []
    monkeypatch.setattr(hosts, "flush_dns", lambda: flushed.append(system_hosts.read_bytes()))
    hosts.ensure_report_hosts()
    assert flushed == [system_hosts.read_bytes()]
    assert hosts.updated_hosts(flushed[0]) == flushed[0]
    assert "已更新并刷新" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["denied", "not-written", "dns"])
def test_permission_cancel_write_or_dns_failure_stops_startup(system_hosts, monkeypatch, capsys, failure):
    monkeypatch.setattr(hosts, "is_admin", lambda: failure == "dns")

    def denied():
        raise subprocess.CalledProcessError(1, "elevated helper")

    monkeypatch.setattr(hosts, "elevate_update", denied if failure == "denied" else lambda: None)
    monkeypatch.setattr(hosts, "flush_dns", denied)
    with pytest.raises(RuntimeError, match="管理员授权"):
        hosts.ensure_report_hosts()
    assert "已更新并刷新" not in capsys.readouterr().out


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_elevation_is_limited_to_hosts_helper_and_rechecked(system_hosts, monkeypatch, capsys, platform):
    executable = r"C:\工具 O'Brien\Python\python.exe" if platform == "win32" else "/tmp/Python with spaces/python"
    monkeypatch.setattr(hosts, "sys", SimpleNamespace(platform=platform, executable=executable))
    monkeypatch.setattr(hosts, "is_admin", lambda: False)
    commands = []

    def elevated(command, **kwargs):
        commands.append(command)
        assert kwargs["check"]
        hosts.apply_mapping(system_hosts)

    monkeypatch.setattr(hosts.subprocess, "run", elevated)
    hosts.ensure_report_hosts()
    helper = str(Path(hosts.__file__).resolve())
    assert len(commands) == 1
    if platform == "darwin":
        assert commands[0] == ["/usr/bin/sudo", "--", executable, helper, "--apply"]
        assert "密码" in capsys.readouterr().out
    else:
        assert commands[0][:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
        assert "-Verb RunAs -Wait -PassThru" in commands[0][4]
        assert executable.replace("'", "''") in commands[0][4]
        assert "--apply" in commands[0][4] and "launch_workbench.py" not in commands[0][4]
        assert "UAC" in capsys.readouterr().out


@pytest.mark.parametrize("platform,expected", [
    ("win32", [["ipconfig.exe", "/flushdns"]]),
    ("darwin", [["/usr/bin/dscacheutil", "-flushcache"],
                ["/usr/bin/killall", "-HUP", "mDNSResponder"]]),
])
def test_platform_dns_commands_are_checked(monkeypatch, platform, expected):
    commands = []
    monkeypatch.setattr(hosts, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(hosts.subprocess, "run", lambda command, **kwargs: commands.append((command, kwargs)))
    hosts.flush_dns()
    assert commands == [(command, {"check": True}) for command in expected]


def test_launcher_checks_hosts_before_reusing_existing_service(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(launcher, "ensure_report_hosts", lambda: calls.append("hosts"))
    monkeypatch.setattr(launcher, "saved_address", lambda *args: "http://127.0.0.1:4320/")
    monkeypatch.setattr(launcher, "healthy", lambda *args: calls.append("health") or True)
    monkeypatch.setattr(launcher, "open_page", lambda *args: calls.append("browser"))
    assert launcher.main() == 0
    assert calls == ["hosts", "health", "browser"]


def test_launcher_does_not_start_or_open_page_if_hosts_setup_fails(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)

    def denied():
        raise RuntimeError("需要管理员授权")

    def unexpected(*args):
        pytest.fail("hosts 失败后不能继续启动")

    monkeypatch.setattr(launcher, "ensure_report_hosts", denied)
    monkeypatch.setattr(launcher, "prepare_python", unexpected)
    monkeypatch.setattr(launcher, "open_page", unexpected)
    with pytest.raises(RuntimeError, match="管理员授权"):
        launcher.main()


def test_manual_service_start_checks_hosts_before_binding_port(monkeypatch, capsys):
    import kea_workbench.__main__ as cli

    def denied():
        raise RuntimeError("需要管理员授权")

    def unexpected(*args):
        pytest.fail("hosts 失败后不能占用端口")

    monkeypatch.setattr(sys, "argv", ["kea2-web", "--port", "0"])
    monkeypatch.setattr(cli, "ensure_report_hosts", denied)
    monkeypatch.setattr(cli, "bind_listener", unexpected)
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 1
    assert "管理员授权" in capsys.readouterr().err
