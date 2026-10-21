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
from pathlib import Path

from .delivery import Publisher, eligible, payload, send_report
from .models import ACTIVE, LABELS, PROJECTS, RunConfig
from .reports import generate_native, make_snapshot, raw_directory, render_report
from .storage import now, write_json


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
        self.device_log_offset = 0

    def save(self):
        self.record["updatedAt"] = now()
        self.record["statusLabel"] = LABELS[self.record["status"]]
        write_json(self.directory / "run.json", self.record)

    def view(self):
        with self.lock:
            return copy.deepcopy(self.record)

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
                        status="failed", error="服务中断，最终状态尚未同步，请补报"
                    )
                else:
                    run["delivery"]["status"] = (
                        "skipped" if run["configuration"]["platformEnabled"] else "disabled"
                    )
                write_json(path, run)
                render_report(path.parent, run)
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
                    runtime.record["error"] = (
                        f"Kea2 异常结束，退出码 {process.returncode}；请查看完整日志"
                    )
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
        while True:
            runtime.sync.wait(timeout=15)
            runtime.sync.clear()
            final = runtime.finished.is_set()
            run = runtime.view()
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
                    render_report(runtime.directory, runtime.record)
                snapshot = runtime.directory / ".platform-snapshot"
                make_snapshot(runtime.directory, snapshot, run)
                publisher = publisher or Publisher(runtime.settings)
                url = publisher.upload(snapshot, run["id"])
                body = payload(run, runtime.settings["platform"], url)
                # Freeze each revision before a request; a failed revision is
                # superseded only by the next immutable snapshot.
                with runtime.lock:
                    runtime.record["delivery"].update(
                        status="pending",
                        revision=body["externalFields"]["custom"]["sync"]["revision"],
                        reportUrl=url,
                    )
                    runtime.save()
                task_id = send_report(runtime.directory, body, runtime.settings["platform"])
                with runtime.lock:
                    runtime.record["delivery"].update(
                        status="uploaded", taskId=task_id, updatedAt=now(), error=""
                    )
                    runtime.save()
            except Exception as error:
                with runtime.lock:
                    runtime.record["delivery"].update(
                        status="failed", error=str(error), updatedAt=now()
                    )
                    runtime.save()
            if final:
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
            if (
                not runtime.finished.is_set()
                or runtime.delivery_thread
                and runtime.delivery_thread.is_alive()
            ):
                raise ValueError("执行或上报正在进行，请稍后重试")
            if not eligible(runtime.view()):
                raise ValueError("该运行未达到上报门槛")
            runtime.sync.set()
            runtime.delivery_thread = threading.Thread(
                target=self.deliver, args=(runtime,), daemon=True
            )
            runtime.delivery_thread.start()
        return runtime.view()

    def close(self):
        for run_id, runtime in list(self.runs.items()):
            self.stop_run(run_id)
        for runtime in self.runs.values():
            if runtime.thread:
                runtime.thread.join(timeout=100)
            if runtime.delivery_thread:
                runtime.delivery_thread.join(timeout=60)
