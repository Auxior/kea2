import hashlib
import mimetypes
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
import urllib3
from minio import Minio

from .storage import write_json


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
                timeout=urllib3.Timeout(connect=5, read=15), retries=False
            ),
        )
        self.bucket = cfg["bucket"]
        self.prefix = cfg["prefix"].strip("/")
        self.base = (
            cfg.get("publicBaseUrl") or f"{cfg['endpoint'].rstrip('/')}/{self.bucket}"
        ).rstrip("/")
        self.sent = {}

    def upload(self, directory: Path, run_id):
        deadline = time.monotonic() + 90
        paths = sorted(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and not path.name.endswith(".tmp")
            and path.name != "report-generation.log"
        )
        entry = directory / "index.html"
        paths = [p for p in paths if p != entry] + [entry]
        for path in paths:
            if time.monotonic() >= deadline:
                raise TimeoutError("本次过程报告上传超过 90 秒，下一周期继续同步")
            relative = path.relative_to(directory).as_posix()
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if relative != "index.html" and self.sent.get(relative) == digest:
                continue
            key = f"{self.prefix}/runs/{run_id}/{relative}"
            from io import BytesIO

            self.client.put_object(
                self.bucket,
                key,
                BytesIO(data),
                len(data),
                content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.sent[relative] = digest
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
