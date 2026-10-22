import copy
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .delivery import Publisher, UploadDeferred, eligible, payload, retryable, send_report
from .models import ACTIVE, LABELS, PROJECTS, RunConfig
from .reports import generate_native, make_snapshot, raw_directory, render_report
from .storage import now, write_json

SYNC_INTERVAL = 15
DELIVERY_BUSY = {"queued", "preparing", "uploading", "reporting", "pending"}


def process_options():
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def interrupt_process(process):
    process.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)


def adb(serial, *args):
    return subprocess.check_output(
        ["adb", "-s", serial, *args], stdin=subprocess.DEVNULL,
        encoding="utf-8", errors="replace", timeout=15
    ).strip()


def devices():
    output = subprocess.check_output(
        ["adb", "devices", "-l"], stdin=subprocess.DEVNULL,
        encoding="utf-8", errors="replace", timeout=10
    )
    found = []
    for line in output.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 2:
            continue
        info = dict(part.split(":", 1) for part in fields[2:] if ":" in part)
        found.append(
            {
                "serial": fields[0],
                "state": fields[1],
                "model": info.get("model", fields[0]).replace("_", " "),
            }
        )
    return found


def failure_reason(runtime, code):
    """Kea2 reports a Fastbot abort with its generic error code; name the actual failure."""
    try:
        with (runtime.directory / "console.log").open("rb") as stream:
            stream.seek(max(stream.seek(0, os.SEEK_END) - 262144, 0))
            tail = stream.read().decode("utf-8", errors="replace")
    except OSError:
        tail = ""
    if "Fastbot Aborted." not in tail:
        return f"Kea2 异常结束，退出码 {code}；请查看完整日志"
    serial = runtime.record["configuration"]["serial"]
    try:
        online = any(d["serial"] == serial and d["state"] == "device" for d in devices())
    except (OSError, subprocess.SubprocessError):
        online = None
    if online is False:
        return (
            f"测试设备连接已断开，Fastbot 无法继续，测试提前结束（退出码 {code}）；"
            "已保留中断前的数据，请检查 USB / 无线调试连接后重新执行"
        )
    return (
        f"设备端 Fastbot 进程意外退出，测试提前结束（退出码 {code}）；已保留中断前的数据。"
        f"常见原因为设备内存不足或系统清理后台进程，请查看 fastbot_{runtime.record['id']}.log 末尾"
    )


def command(config: RunConfig, directory: Path, run_id):
    args = [
        sys.executable,
        "-u",
        "-m",
        "kea_workbench.runner",
        "run",
        "-s",
        config.serial,
        "-p",
        config.package,
        "-o",
        str(directory / "raw"),
        "--log-stamp",
        run_id,
        "--running-minutes",
        str(config.runningMinutes),
        "--max-step",
        str(config.maxStep),
        "--throttle",
        str(config.throttle),
        "--profile-period",
        str(config.profilePeriod),
    ]
    if config.takeScreenshots:
        args += [
            "--take-screenshots",
            "--pre-failure-screenshots",
            str(config.preFailureScreenshots),
            "--post-failure-screenshots",
            str(config.postFailureScreenshots),
        ]
    return args


class Runtime:
    def __init__(self, directory, record, settings):
        self.directory, self.record, self.settings = directory, record, settings
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.sync = threading.Event()
        self.finished = threading.Event()
        self.process = None
        self.thread = None
        self.delivery_thread = None
        self.delivery_busy = False
        self.device_log_offset = 0

    def save(self):
        self.record["updatedAt"] = now()
        self.record["statusLabel"] = LABELS[self.record["status"]]
        write_json(self.directory / "run.json", self.record)

    def view(self):
        with self.lock:
            record = copy.deepcopy(self.record)
            record["delivery"]["busy"] = self.delivery_busy
            record["delivery"]["canRetry"] = (
                eligible(record) and not self.delivery_busy
                and record["delivery"]["status"] in {"failed", "retrying"}
            )
            return record

    def delivery_state(self, **fields):
        with self.lock:
            self.record["delivery"].update(fields, updatedAt=now())
            self.save()
            with (self.directory / "delivery.log").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"at": now(), **fields}, ensure_ascii=False) + "\n")

    def log(self, line):
        line = re.sub(r"\x1b\[[0-9;]*m", "", line).rstrip()
        with self.lock:
            logs = self.record["recentLogs"]
            logs.append(line)
            del logs[:-160]
            elapsed = re.search(r"\[New Iteration\] Elapsed: ([\d.]+)s", line)
            if elapsed:
                self.record["elapsedSeconds"] = max(
                    self.record["elapsedSeconds"], float(elapsed[1])
                )
                if self.record["status"] == "preparing":
                    self.record["status"] = "running"
            step = re.search(r"Sending monkeyEvent\s+\(?(\d+)", line)
            if step:
                self.record["steps"] = max(self.record["steps"], int(step[1]))
            if "Generating bug report" in line and self.record["status"] in ACTIVE:
                self.record["status"] = "finalizing"
            self.save()

    def read_findings(self):
        raw = raw_directory(self.directory)
        path = raw / f"fastbot_{self.record['id']}.log" if raw else None
        if path is None or not path.exists():
            return
        with path.open(encoding="utf-8", errors="replace") as stream:
            stream.seek(self.device_log_offset)
            lines = stream.readlines()
            self.device_log_offset = stream.tell()
        count = sum(bool(re.search(r"^// (?:CRASH:|NOT RESPONDING:)", line)) for line in lines)
        if count:
            with self.lock:
                self.record["findings"] += count
                if not self.record.get("qualifiedAt"):
                    self.record["gateBlocked"] = True
                self.save()

    def qualify(self):
        with self.lock:
            if not self.record.get("qualifiedAt") and eligible(self.record):
                self.record["qualifiedAt"] = now()
                self.record["delivery"]["status"] = "pending"
                self.save()
                self.sync.set()


class Manager:
    def __init__(self, root: Path, settings):
        self.root, self.settings = root, settings
        self.runs = {}
        self.lock = threading.RLock()
        self.closing = threading.Event()
        for path in sorted((root / "artifacts").glob("*/run.json")):
            run = json.loads(path.read_text(encoding="utf-8"))
            if run["status"] in ACTIVE:
                run.update(
                    status="interrupted",
                    statusLabel=LABELS["interrupted"],
                    endedAt=now(),
                    error="工作台服务中断，请检查设备后重新执行",
                )
                if eligible(run):
                    run["delivery"].update(
                        status="failed", error="服务中断，最终状态尚未同步，请补报",
                        message="上次上报已中断，可立即重试", nextRetryAt=None,
                    )
                else:
                    run["delivery"]["status"] = (
                        "skipped" if run["configuration"]["platformEnabled"] else "disabled"
                    )
                write_json(path, run)
                render_report(path.parent, run)
            elif eligible(run) and (
                run["delivery"]["status"] in DELIVERY_BUSY | {"retrying"}
                or (run["delivery"]["status"] == "uploaded" and run["delivery"].get("final") is False)
            ):
                run["delivery"].update(
                    status="failed", error="上次上报未完成，请重试上报",
                    message="上次上报已中断，可立即重试", nextRetryAt=None,
                )
                write_json(path, run)
            runtime = Runtime(path.parent, run, run.get("settings", settings))
            runtime.finished.set()
            self.runs[run["id"]] = runtime

    def list(self):
        with self.lock:
            return sorted(
                (r.view() for r in self.runs.values()),
                key=lambda run: run["startedAt"],
                reverse=True,
            )

    def start(self, config):
        with self.lock:
            if any(
                r.view()["configuration"]["serial"] == config.serial
                and r.view()["status"] in ACTIVE
                for r in self.runs.values()
            ):
                raise ValueError("该设备已有正在执行的任务")
            device = next(
                (d for d in devices() if d["serial"] == config.serial and d["state"] == "device"),
                None,
            )
            if not device:
                raise ValueError("设备不在线或尚未授权 USB 调试")
            if not adb(config.serial, "shell", "pm", "path", config.package).startswith("package:"):
                raise ValueError("设备上未安装指定 App")
            if "com.android.commands.monkey" in adb(config.serial, "shell", "ps", "-A"):
                raise ValueError("该设备已有 Monkey / Fastbot 进程，请先结束原任务")
            if config.platformEnabled and (
                not self.settings["minio"]["accessKey"] or not self.settings["minio"]["secretKey"]
            ):
                raise ValueError("请先在设置中配置 MinIO 连接与访问凭据")
            device["osVersion"] = adb(config.serial, "shell", "getprop", "ro.build.version.release")
            package_info = adb(config.serial, "shell", "dumpsys", "package", config.package)
            version = re.search(r"versionName=(\S+)", package_info)
            device["appVersion"] = version[1] if version else ""
            run_id = "kea2-" + uuid.uuid4().hex
            directory = self.root / "artifacts" / run_id
            directory.mkdir(parents=True)
            (directory / "console.log").touch()
            record = {
                "id": run_id,
                "configuration": config.model_dump(),
                "project": PROJECTS.get(config.package),
                "device": device,
                "settings": copy.deepcopy(self.settings),
                "status": "preparing",
                "startedAt": now(),
                "elapsedSeconds": 0.0,
                "steps": 0,
                "findings": 0,
                "recentLogs": [],
                "error": "",
                "delivery": {
                    "status": "waiting" if config.platformEnabled else "disabled",
                    "revision": 0,
                },
                "command": command(config, directory, run_id),
            }
            runtime = Runtime(directory, record, record["settings"])
            runtime.save()
            self.runs[run_id] = runtime
            runtime.delivery_thread = threading.Thread(
                target=self.deliver, args=(runtime,), daemon=True
            )
            runtime.delivery_thread.start()
            runtime.thread = threading.Thread(
                target=self.execute, args=(runtime, config), daemon=True
            )
            runtime.thread.start()
            return runtime.view()

    def execute(self, runtime, config):
        def read_output(process):
            with (runtime.directory / "console.log").open("a", buffering=1, encoding="utf-8") as log:
                for line in process.stdout:
                    log.write(line)
                    runtime.log(line)

        try:
            process = subprocess.Popen(
                runtime.record["command"],
                cwd=runtime.directory,
                # The service's stdin belongs exclusively to the launcher
                # watchdog; test processes must not inherit that control pipe.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                **process_options(),
            )
            runtime.process = process
            with runtime.lock:
                runtime.record["pid"] = process.pid
                runtime.save()
            reader = threading.Thread(target=read_output, args=(process,), daemon=True)
            reader.start()
            interrupted_at = None
            forced = False
            while process.poll() is None:
                runtime.read_findings()
                runtime.qualify()
                if runtime.stop.is_set() and interrupted_at is None:
                    interrupt_process(process)
                    interrupted_at = time.monotonic()
                if interrupted_at and time.monotonic() - interrupted_at > 90:
                    process.kill()
                    forced = True
                    runtime.log("停止超过 90 秒，执行进程已强制结束；设备清理未确认")
                time.sleep(0.5)
            reader.join(timeout=5)
            runtime.read_findings()
            runtime.qualify()
            reason = failure_reason(runtime, process.returncode)
            with runtime.lock:
                runtime.record["exitCode"] = process.returncode
                if process.returncode in {1, 2, 3}:
                    runtime.record["findings"] = max(runtime.record["findings"], 1)
                no_execution = (
                    runtime.record["steps"] == 0 and runtime.record["elapsedSeconds"] == 0
                )
                runtime.record["status"] = (
                    "interrupted"
                    if forced
                    or (
                        not runtime.stop.is_set()
                        and (process.returncode not in {0, 1, 2, 3} or no_execution)
                    )
                    else "cancelled"
                    if runtime.stop.is_set()
                    else "completed"
                )
                if runtime.record["status"] == "interrupted":
                    runtime.record["error"] = reason
                runtime.record["endedAt"] = now()
                runtime.save()
            native = raw_directory(runtime.directory)
            if native and not (native / "bug_report.html").exists():
                try:
                    generate_native(runtime.directory)
                except Exception as error:
                    runtime.log(f"原生报告生成失败：{error}")
        except Exception as error:
            with runtime.lock:
                runtime.record.update(status="interrupted", error=str(error), endedAt=now())
                runtime.save()
        finally:
            with runtime.lock:
                render_report(runtime.directory, runtime.record)
            runtime.finished.set()
            runtime.sync.set()

    def deliver(self, runtime):
        publisher = None
        delay = SYNC_INTERVAL
        failures = 0
        pending_run = None
        while True:
            runtime.sync.wait(timeout=delay)
            with runtime.lock:
                runtime.sync.clear()
                final = runtime.finished.is_set()
                if (
                    self.closing.is_set() and final and not runtime.delivery_busy
                    and runtime.record["delivery"]["status"] in {"retrying", "failed"}
                ):
                    return
                run = runtime.view()
                if final and pending_run and pending_run["status"] in ACTIVE:
                    pending_run = None
                if not final and run["status"] not in ACTIVE and pending_run is None:
                    continue
            if not eligible(run):
                if final:
                    with runtime.lock:
                        runtime.record["delivery"]["status"] = (
                            "skipped" if run["configuration"]["platformEnabled"] else "disabled"
                        )
                        runtime.save()
                    return
                continue
            try:
                with runtime.lock:
                    runtime.delivery_busy = True
                    runtime.delivery_state(
                        status="preparing", error="", nextRetryAt=None,
                        message="正在准备最终报告" if final else "正在准备过程报告",
                        attemptStartedAt=now(), progress=None,
                    )
                    if pending_run is None:
                        run = runtime.view()
                        render_report(runtime.directory, runtime.record)
                snapshot = runtime.directory / ".platform-snapshot"
                if pending_run is None:
                    make_snapshot(runtime.directory, snapshot, run)
                    pending_run = run
                else:
                    run = pending_run
                final = run["status"] not in ACTIVE
                publisher = publisher or Publisher(runtime.settings)
                runtime.delivery_state(status="uploading", message="正在上传报告文件")
                last_progress = [0.0, None]

                def progress(value):
                    stamp = time.monotonic()
                    signature = (value["currentFile"], value["uploadedFiles"])
                    if stamp - last_progress[0] >= 1 or signature != last_progress[1]:
                        with runtime.lock:
                            runtime.record["delivery"]["progress"] = value
                            runtime.save()
                        last_progress[:] = [stamp, signature]

                url = publisher.upload(snapshot, run["id"], progress=progress)
                body = payload(run, runtime.settings["platform"], url)
                # Freeze each revision before a request; a failed revision is
                # superseded only by the next immutable snapshot.
                with runtime.lock:
                    runtime.delivery_state(
                        status="reporting", message="文件已上传，正在等待平台确认",
                        revision=body["externalFields"]["custom"]["sync"]["revision"],
                        reportUrl=url,
                    )
                task_id = send_report(runtime.directory, body, runtime.settings["platform"])
                with runtime.lock:
                    runtime.delivery_state(
                        status="uploaded", taskId=task_id, error="", final=final,
                        lastSyncedAt=now(), nextRetryAt=None,
                        message="最终报告已同步" if final else "过程报告已同步，等待下一周期",
                    )
                    runtime.delivery_busy = False
                failures = 0
                delay = SYNC_INTERVAL
                pending_run = None
                if final:
                    return
                if runtime.finished.is_set():
                    runtime.sync.set()
            except Exception as error:
                deferred = isinstance(error, UploadDeferred)
                failures = 0 if deferred else failures + 1
                delay = min(SYNC_INTERVAL * (2 ** min(failures, 4)), 180) if retryable(error) else None
                with runtime.lock:
                    runtime.delivery_busy = False
                    closing = self.closing.is_set()
                    runtime.delivery_state(
                        status="failed" if closing or delay is None else "retrying",
                        error="" if deferred and not closing else str(error),
                        nextRetryAt=None if closing or delay is None else (
                            datetime.now(timezone.utc) + timedelta(seconds=delay)
                        ).isoformat(),
                        message=(
                            "服务关闭前上报未完成，可在重启后重试" if closing else
                            "上报失败，请检查错误后手动重试" if delay is None else
                            f"已保存上传进度，{delay} 秒后自动继续" if deferred else
                            f"上报失败，{delay} 秒后自动重试，也可立即重试"
                        ),
                    )
                if closing:
                    return

    def stop_run(self, run_id):
        runtime = self.runs[run_id]
        with runtime.lock:
            if runtime.record["status"] in ACTIVE:
                runtime.record["status"] = "stopping"
                runtime.stop.set()
                runtime.save()
        return runtime.view()

    def retry(self, run_id):
        runtime = self.runs[run_id]
        with runtime.lock:
            if self.closing.is_set():
                raise ValueError("工作台正在关闭，请重启后重试")
            if not eligible(runtime.view()):
                raise ValueError("该运行未达到上报门槛")
            if runtime.delivery_busy or (
                runtime.finished.is_set()
                and runtime.record["delivery"].get("final") is True
                and runtime.record["delivery"]["status"] == "uploaded"
            ):
                return runtime.view()
            runtime.delivery_busy = True
            runtime.delivery_state(
                status="queued", error="", nextRetryAt=None,
                message="重试已接受，正在排队上报",
            )
            runtime.sync.set()
            if not runtime.delivery_thread or not runtime.delivery_thread.is_alive():
                runtime.delivery_thread = threading.Thread(
                    target=self.deliver, args=(runtime,), daemon=True
                )
                runtime.delivery_thread.start()
        return runtime.view()

    def close(self):
        self.closing.set()
        for runtime in self.runs.values():
            with runtime.lock:
                if runtime.finished.is_set() and not runtime.delivery_busy:
                    if runtime.record["delivery"]["status"] == "retrying":
                        runtime.delivery_state(
                            status="failed", nextRetryAt=None,
                            message="服务已关闭，重启后可重试上报",
                        )
                    runtime.sync.set()
        for run_id, runtime in list(self.runs.items()):
            self.stop_run(run_id)
        for runtime in self.runs.values():
            if runtime.thread:
                runtime.thread.join(timeout=100)
            if runtime.delivery_thread:
                runtime.delivery_thread.join(timeout=60)
