"""Run Kea2 with configuration-sync and Windows interrupt compatibility fixes."""

import shutil
import signal
import sys


def copy_new_configs(sanitizer):
    """Kea2 1.2.4 also tries to copy directories created by pip's byte compilation."""
    from kea2.version_manager import logger

    source = sanitizer.kea2_assets_path / "fastbot_configs"
    destination = sanitizer.user_config_path
    existing = {path.name for path in destination.iterdir()}
    for path in sorted(source.iterdir()):
        if (
            path.name in existing
            or not path.is_file()
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        logger.info(f"Copying new config file: {path.name}")
        shutil.copy2(path, destination / path.name)


def main():
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    from kea2.cli import main as kea_main
    from kea2.version_manager import ConfigVersionSanitizer

    # Preserve upstream version checks; replace only its broken file-copy step.
    original = ConfigVersionSanitizer._copy_new_configs
    ConfigVersionSanitizer._copy_new_configs = copy_new_configs
    try:
        kea_main()
    finally:
        ConfigVersionSanitizer._copy_new_configs = original


if __name__ == "__main__":
    main()
