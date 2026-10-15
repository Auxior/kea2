import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def resolve_settings(settings):
    effective = copy.deepcopy(settings)
    defaults = json.loads(
        (Path(__file__).parent / "settings.example.json").read_text(encoding="utf-8")
    )["minio"]
    minio = effective.setdefault("minio", {})
    for field in ("accessKey", "secretKey"):
        value = minio.get(field)
        if value is None or isinstance(value, str) and not value.strip():
            minio[field] = defaults[field]
    return effective


def load_settings(root: Path):
    source = root / "data/settings.json"
    if not source.exists():
        source = root / "settings.example.json"
    if not source.exists():
        source = Path(__file__).parent / "settings.example.json"
    return resolve_settings(json.loads(source.read_text(encoding="utf-8-sig")))
