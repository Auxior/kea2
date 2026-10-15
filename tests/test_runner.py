import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kea_workbench import engine, runner
from kea_workbench.models import RunConfig
from kea_workbench.storage import write_json


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_test_commands_use_compatible_runner(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(engine, "sys", SimpleNamespace(platform=platform, executable=sys.executable))
    args = engine.command(RunConfig(serial="device"), tmp_path, "test")
    assert args[:5] == [sys.executable, "-u", "-m", "kea_workbench.runner", "run"]


@pytest.fixture
def sdk_configs(tmp_path, monkeypatch):
    import kea2.version_manager as versions

    assets = tmp_path / "sdk/assets"
    source = assets / "fastbot_configs"
    source.mkdir(parents=True)
    (source / "__pycache__").mkdir()
    (source / "__pycache__/config.cpython-314.pyc").write_bytes(b"bytecode")
    (source / "other-directory").mkdir()
    (source / "config.pyc").write_bytes(b"bytecode")
    (source / "config.pyo").write_bytes(b"bytecode")
    (source / "existing.config").write_text("SDK default", encoding="utf-8")
    (source / "new.config").write_text("new default", encoding="utf-8")
    write_json(assets / "config_version.json", {"compatibility infos": []})
    destination = tmp_path / "project/configs"
    destination.mkdir(parents=True)
    (destination / "existing.config").write_text("user customization", encoding="utf-8")
    write_json(destination / "version.json", {"version": versions.get_cur_version()})
    working = destination.parent / "artifacts/run"
    working.mkdir(parents=True)
    monkeypatch.chdir(working)
    monkeypatch.setattr(versions, "__file__", str(assets.parent / "version_manager.py"))
    return source, destination


@pytest.mark.parametrize("existing_cache", [False, True])
def test_real_sdk_cli_sync_skips_caches_and_preserves_user_configs(
    sdk_configs, monkeypatch, existing_cache
):
    import kea2.cli as cli
    from kea2.version_manager import ConfigVersionSanitizer

    source, destination = sdk_configs
    if existing_cache:
        (destination / "__pycache__").mkdir()
        (destination / "__pycache__/user.pyc").write_bytes(b"user cache")
    expected = {path.relative_to(destination): path.read_bytes()
                for path in destination.rglob("*") if path.is_file()}
    expected[Path("new.config")] = b"new default"
    checks, runs = [], []
    original_check = ConfigVersionSanitizer.check_config_compatibility
    original_copy = ConfigVersionSanitizer._copy_new_configs

    def check(sanitizer):
        checks.append(sanitizer.config_version)
        return original_check(sanitizer)

    monkeypatch.setattr(ConfigVersionSanitizer, "check_config_compatibility", check)
    monkeypatch.setattr(cli, "run", lambda args: runs.append(args.serial))
    args = engine.command(RunConfig(serial="device"), destination.parent / "artifacts/run", "test")
    monkeypatch.setattr(sys, "argv", ["runner", *args[4:]])
    for _ in range(2):
        with pytest.raises(SystemExit) as exited:
            runner.main()
        assert exited.value.code == 0
        assert ConfigVersionSanitizer._copy_new_configs is original_copy
        actual = {path.relative_to(destination): path.read_bytes()
                  for path in destination.rglob("*") if path.is_file()}
        assert actual == expected
        assert not (destination / "other-directory").exists()
        assert (destination / "__pycache__").exists() == existing_cache
        # Subsequent starts must preserve edits to a previously copied default.
        (destination / "new.config").write_text("user edit", encoding="utf-8")
        expected[Path("new.config")] = b"user edit"
    assert len(checks) == 2
    assert runs == ["device", "device"]
    assert (source / "__pycache__/config.cpython-314.pyc").read_bytes() == b"bytecode"


def test_config_copy_errors_are_not_hidden_and_patch_is_restored(sdk_configs, monkeypatch):
    import kea2.cli as cli
    from kea2.version_manager import ConfigVersionSanitizer, check_config_compatibility

    original = ConfigVersionSanitizer._copy_new_configs

    def fail_copy(*args):
        raise PermissionError("config is not writable")

    monkeypatch.setattr(runner.shutil, "copy2", fail_copy)
    monkeypatch.setattr(cli, "main", check_config_compatibility)
    with pytest.raises(PermissionError, match="not writable"):
        runner.main()
    assert ConfigVersionSanitizer._copy_new_configs is original
