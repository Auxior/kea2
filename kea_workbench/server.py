from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .engine import Manager, devices
from .models import PROJECTS, RunConfig
from .storage import load_settings, resolve_settings, write_json


def create_app(root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    manager = Manager(root, load_settings(root))

    @asynccontextmanager
    async def lifespan(app):
        yield
        import asyncio

        await asyncio.to_thread(manager.close)

    app = FastAPI(title="Kea2 测试工作台", lifespan=lifespan)
    app.state.manager = manager
    static = Path(__file__).parent / "static"

    @app.get("/api/health")
    def health():
        return {"application": "kea2-workbench", "root": str(root)}

    @app.get("/api/config")
    def configuration():
        return {
            "defaults": RunConfig(serial="4A101FDAP000B4").model_dump(),
            "projects": PROJECTS,
            "settings": manager.settings,
        }

    @app.put("/api/settings")
    def settings(body: dict):
        try:
            from urllib.parse import urlsplit

            for endpoint in [
                body["platform"]["endpoint"],
                body["platform"]["frontend"],
                body["minio"]["endpoint"],
            ]:
                assert urlsplit(endpoint).scheme in {"http", "https"} and urlsplit(endpoint).netloc
            for field in ["serviceId", "capabilityId"]:
                assert body["platform"][field]
            assert body["minio"]["bucket"] and body["minio"]["prefix"]
        except (KeyError, AssertionError):
            raise HTTPException(422, "平台与 MinIO 配置不完整或地址无效")
        body = resolve_settings(body)
        write_json(root / "data/settings.json", body)
        manager.settings = body
        return {"saved": True, "settings": body}

    @app.get("/api/devices")
    def list_devices():
        try:
            return {"devices": devices()}
        except Exception as error:
            raise HTTPException(503, str(error))

    @app.get("/api/runs")
    def list_runs():
        return {"runs": manager.list()}

    @app.post("/api/runs", status_code=201)
    def start(config: RunConfig):
        try:
            return manager.start(config)
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error))

    @app.get("/api/runs/{run_id}")
    def detail(run_id: str):
        if run_id not in manager.runs:
            raise HTTPException(404, "运行记录不存在")
        return manager.runs[run_id].view()

    @app.post("/api/runs/{run_id}/stop")
    def stop(run_id: str):
        detail(run_id)
        return manager.stop_run(run_id)

    @app.post("/api/runs/{run_id}/retry", status_code=202)
    def retry(run_id: str):
        detail(run_id)
        try:
            return manager.retry(run_id)
        except ValueError as error:
            raise HTTPException(409, str(error))

    @app.get("/reports/{run_id}/{resource:path}")
    def report(run_id: str, resource: str):
        detail(run_id)
        directory = manager.runs[run_id].directory.resolve()
        path = (directory / resource).resolve()
        if (
            not path.is_relative_to(directory)
            or not path.is_file()
            or any(p.startswith(".") for p in Path(resource).parts)
        ):
            raise HTTPException(404, "报告文件不存在")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
