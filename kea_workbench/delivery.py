import hashlib
import json
import mimetypes
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
import urllib3
from minio import Minio

from .storage import write_json

UPLOAD_SLICE_SECONDS = 90


class UploadDeferred(Exception):
    """Yield between files while retaining confirmed uploads for the next pass."""


def retryable(error):
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500 or error.response.status_code in {408, 429}
    return not isinstance(error, (ValueError, FileNotFoundError, PermissionError))


class UploadProgress:
    def __init__(self, callback, total_files, total_bytes):
        self.callback = callback
        self.lock = threading.RLock()
        self.value = dict(
            totalFiles=total_files, uploadedFiles=0, totalBytes=total_bytes,
            uploadedBytes=0, currentFile="", currentFileBytes=0, currentFileTotal=0,
        )

    def emit(self, **fields):
        with self.lock:
            self.value.update(fields)
            if self.callback:
                self.callback(dict(self.value))

    def set_meta(self, object_name=None, total_length=None):
        # MinIO calls this hook before transferring a (possibly multipart) file.
        pass

    def update(self, length):
        with self.lock:
            self.emit(currentFileBytes=min(
                self.value["currentFileTotal"], self.value["currentFileBytes"] + length,
            ))

    def complete(self, size):
        self.emit(
            uploadedFiles=self.value["uploadedFiles"] + 1,
            uploadedBytes=self.value["uploadedBytes"] + size,
            currentFile="", currentFileBytes=0, currentFileTotal=0,
        )


def eligible(run):
    return (
        run["configuration"]["platformEnabled"]
        and run["configuration"]["runningMinutes"] >= 5
        and (
            run.get("qualifiedAt") or (run["elapsedSeconds"] >= 300 and not run.get("gateBlocked"))
        )
    )


def payload(run, settings, report_url):
    final = run["status"] not in {"preparing", "running", "stopping", "finalizing"}
    status = {
        "completed": "succeeded",
        "cancelled": "canceled",
        "interrupted": "failed",
    }.get(run["status"], "running")
    summary = {
        "total": 1,
        "passed": int(final and status == "succeeded" and not run["findings"]),
        "failed": int(final and (status != "succeeded" or bool(run["findings"]))),
        "steps": run["steps"],
        "crashesAndAnrs": run["findings"],
    }
    return {
        "projectId": run["project"]["id"],
        "serviceId": settings["serviceId"],
        "capabilityId": settings["capabilityId"],
        "externalFields": {
            "taskName": "Kea2 Android 随机测试",
            "externalTaskId": run["id"],
            "status": status,
            "message": f"{run['statusLabel']}；探索 {run['elapsedSeconds']:.1f} 秒，{run['steps']} 步，Crash / ANR {run['findings']}",
            "resultSummary": summary,
            "artifacts": [{"artifactType": "report", "name": "测试报告", "url": report_url}],
            "custom": {
                "schemaVersion": 1,
                "sync": {
                    "revision": run["delivery"]["revision"] + 1,
                    "qualified": True,
                    "final": final,
                    "phase": run["status"],
                    "heartbeatSeconds": 180,
                },
                "app": {
                    "id": run["configuration"]["package"],
                    "version": run["device"].get("appVersion"),
                },
                "device": run["device"],
                "configuration": run["configuration"],
                "execution": {
                    "startedAt": run["startedAt"],
                    "endedAt": run.get("endedAt"),
                    "elapsedSeconds": run["elapsedSeconds"],
                    "exitCode": run.get("exitCode"),
                },
                "progress": {"steps": run["steps"], "updatedAt": run["updatedAt"]},
            },
        },
    }


class Publisher:
    def __init__(self, settings):
        cfg = settings["minio"]
        endpoint = urlsplit(cfg["endpoint"])
        self.client = Minio(
            endpoint.netloc,
            access_key=cfg["accessKey"],
            secret_key=cfg["secretKey"],
            secure=endpoint.scheme == "https",
            region="us-east-1",
            http_client=urllib3.PoolManager(
                # urllib3 also uses the connect timeout while sending upload data.
                timeout=urllib3.Timeout(connect=30, read=60), retries=False
            ),
        )
        self.bucket = cfg["bucket"]
        self.prefix = cfg["prefix"].strip("/")
        self.destination = [cfg["endpoint"].rstrip("/"), self.bucket, self.prefix]
        self.base = (
            cfg.get("publicBaseUrl") or f"{cfg['endpoint'].rstrip('/')}/{self.bucket}"
        ).rstrip("/")
        self.sent = {}

    def upload(self, directory: Path, run_id, progress=None):
        deadline = time.monotonic() + UPLOAD_SLICE_SECONDS
        state_file = directory / ".upload-state.json"
        target = [*self.destination, run_id]
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            files = state.get("files") if isinstance(state, dict) else None
            self.sent = files if (
                isinstance(files, dict) and state.get("target") == target
                and all(isinstance(v, str) for v in files.values())
            ) else {}
        except (OSError, ValueError, KeyError, TypeError):
            self.sent = {}
        paths = sorted(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path != state_file
            and not path.name.endswith(".tmp")
            and path.name != "report-generation.log"
        )
        entry = directory / "index.html"
        paths = [p for p in paths if p != entry] + [entry]
        tracker = UploadProgress(progress, len(paths), sum(p.stat().st_size for p in paths))
        tracker.emit()
        sent_this_pass = 0
        for path in paths:
            relative = path.relative_to(directory).as_posix()
            size = path.stat().st_size
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if relative != "index.html" and self.sent.get(relative) == digest:
                tracker.complete(size)
                continue
            tracker.emit(currentFile=relative, currentFileBytes=0, currentFileTotal=size)
            # Check only files that still need sending. A large file can finish
            # after this cooperative budget; confirmed files survive retries.
            if sent_this_pass and time.monotonic() >= deadline:
                raise UploadDeferred("本轮上传时间预算已用完，已保存进度，将自动继续")
            key = f"{self.prefix}/runs/{run_id}/{relative}"
            self.client.fput_object(
                self.bucket,
                key,
                str(path),
                content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                progress=tracker,
            )
            self.sent[relative] = digest
            sent_this_pass += 1
            write_json(state_file, {"target": target, "files": self.sent})
            tracker.complete(size)
        return f"{self.base}/{quote(self.prefix)}/runs/{quote(run_id)}/index.html"


def send_report(directory, body, settings):
    write_json(
        directory / "external-report-request.json",
        {"endpoint": settings["endpoint"], "payload": body},
    )
    for attempt in range(3):
        try:
            response = httpx.post(
                settings["endpoint"], json=body, timeout=15, follow_redirects=False
            )
            response.raise_for_status()
            result = response.json()
            task = result.get("task", {})
            fields = body["externalFields"]
            expected = {
                "serviceId": body["serviceId"],
                "capabilityId": body["capabilityId"],
                "externalTaskId": fields["externalTaskId"],
                "taskName": fields["taskName"],
                "status": fields["status"],
                "resultSummary": fields["resultSummary"],
            }
            if (
                result.get("success") is False
                or not task.get("taskId")
                or any(task.get(k) != v for k, v in expected.items())
                or task.get("requestPayload", {}).get("projectId") != body["projectId"]
                or task.get("externalFields", {}).get("custom", {}).get("sync")
                != fields["custom"]["sync"]
            ):
                raise ValueError("平台回执未确认当前项目、执行数据与版本")
            write_json(directory / "external-report-response.json", result)
            return task["taskId"]
        except Exception as error:
            if (
                isinstance(error, httpx.HTTPStatusError)
                and error.response.status_code < 500
                and error.response.status_code not in {408, 429}
            ):
                raise
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
