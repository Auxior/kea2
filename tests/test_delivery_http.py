import hashlib
import json
import shutil
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree import ElementTree

from kea_workbench.delivery import Publisher, payload, send_report
from kea_workbench.models import PROJECTS, RunConfig


@contextmanager
def local_storage_platform(root):
    """Local HTTP stand-in: stream S3 parts to disk and echo the platform contract."""
    state = SimpleNamespace(objects={}, uploads={}, puts=Counter(), reports=[])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body=b"", content_type="application/xml", status=200, **headers):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self):
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            key = unquote(url.path).lstrip("/")
            if "uploadId" in query:
                part = int(query["partNumber"][0])
                path = root / "parts" / query["uploadId"][0] / str(part)
                state.uploads[query["uploadId"][0]][1][part] = path
            else:
                path = root / "objects" / key
                state.objects[key] = path
            path.parent.mkdir(parents=True, exist_ok=True)
            remaining = int(self.headers["Content-Length"])
            digest = hashlib.sha256()
            with path.open("wb") as output:
                while remaining:
                    chunk = self.rfile.read(min(1048576, remaining))
                    if not chunk:
                        raise EOFError("incomplete upload")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            state.puts[key] += 1
            self.reply(ETag='"' + digest.hexdigest() + '"')

        def do_POST(self):
            url = urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if url.path == "/platform":
                request = json.loads(body)
                state.reports.append(request)
                fields = request["externalFields"]
                task = {
                    "taskId": "local-task",
                    **{key: request[key] for key in ["serviceId", "capabilityId"]},
                    **{key: fields[key] for key in ["externalTaskId", "taskName", "status", "resultSummary"]},
                    "requestPayload": {"projectId": request["projectId"]},
                    "externalFields": fields,
                }
                return self.reply(json.dumps({"success": True, "task": task}).encode(), "application/json")
            key = unquote(url.path).lstrip("/")
            bucket, name = key.split("/", 1)
            if "uploads" in parse_qs(url.query, keep_blank_values=True):
                upload_id = uuid.uuid4().hex
                state.uploads[upload_id] = (key, {})
                return self.reply((
                    '<InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                    f'<Bucket>{bucket}</Bucket><Key>{name}</Key><UploadId>{upload_id}</UploadId>'
                    '</InitiateMultipartUploadResult>'
                ).encode())
            upload_id = parse_qs(url.query)["uploadId"][0]
            _, parts = state.uploads[upload_id]
            path = root / "objects" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as output:
                for part in sorted(parts):
                    with parts[part].open("rb") as source:
                        shutil.copyfileobj(source, output)
            state.objects[key] = path
            # Validate that the SDK supplied a complete multipart commit.
            assert ElementTree.fromstring(body).tag.endswith("CompleteMultipartUpload")
            self.reply((
                '<CompleteMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f'<Location>http://localhost/{key}</Location><Bucket>{bucket}</Bucket>'
                f'<Key>{name}</Key><ETag>"complete"</ETag></CompleteMultipartUploadResult>'
            ).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    settings = {
        "minio": {"endpoint": endpoint, "bucket": "unit-bucket", "prefix": "kea2",
                  "accessKey": "unit", "secretKey": "unit"},
        "platform": {"endpoint": endpoint + "/platform", "serviceId": "unit-service",
                     "capabilityId": "unit-capability", "frontend": endpoint},
    }
    try:
        yield settings, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_real_http_multipart_streaming_cache_and_platform_acknowledgement(tmp_path):
    directory = tmp_path / "snapshot"
    directory.mkdir()
    large = directory / "large.log"
    large.write_bytes(b"x" * (6 * 1048576))
    (directory / "index.html").write_text("Report", encoding="utf-8")
    progress = []
    with local_storage_platform(tmp_path / "server") as (settings, state):
        url = Publisher(settings).upload(directory, "http-run", progress=progress.append)
        key = "unit-bucket/kea2/runs/http-run/large.log"
        assert state.puts[key] == 2
        assert state.objects[key].stat().st_size == large.stat().st_size
        with state.objects[key].open("rb") as source, large.open("rb") as expected:
            assert hashlib.file_digest(source, "sha256").digest() == hashlib.file_digest(expected, "sha256").digest()
        assert any(0 < item["currentFileBytes"] < large.stat().st_size for item in progress)
        Publisher(settings).upload(directory, "http-run")
        assert state.puts[key] == 2  # Recreating Publisher does not resend the large file.
        assert all(".upload-state" not in key for key in state.objects)
        run = dict(
            id="http-run", configuration=RunConfig(serial="unit").model_dump(),
            project=PROJECTS["com.ugreen.iot"], device={"model": "Unit"},
            status="completed", statusLabel="已完成", elapsedSeconds=36000, steps=32710,
            findings=0, startedAt="2026-10-20T11:00:00Z", endedAt="2026-10-20T21:00:00Z",
            updatedAt="2026-10-21T02:00:00Z", delivery={"revision": 557},
        )
        body = payload(run, settings["platform"], url)
        assert send_report(directory, body, settings["platform"]) == "local-task"
        assert state.reports[-1]["externalFields"]["custom"]["sync"]["final"] is True
