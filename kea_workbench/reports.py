import html
import shutil
import subprocess
import sys
from pathlib import Path

from .models import LABELS
from .storage import write_json


def raw_directory(directory: Path):
    return next(iter(sorted((directory / "raw").glob("res_*"))), None)


def generate_native(directory: Path):
    raw = raw_directory(directory)
    if raw is None:
        return False
    with (directory / "report-generation.log").open("a") as log:
        result = subprocess.run(
            [sys.executable, "-m", "kea2.cli", "report", "-p", str(raw)],
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
    return result.returncode == 0 and (raw / "bug_report.html").is_file()


def render_report(directory: Path, run: dict):
    def escape(value):
        return html.escape(str(value))

    cfg = run["configuration"]
    native = raw_directory(directory)
    native_link = (
        f"raw/{native.name}/bug_report.html"
        if native and (native / "bug_report.html").is_file()
        else ""
    )
    banner = (
        "测试运行中，数据持续更新"
        if run["status"] in {"preparing", "running", "stopping", "finalizing"}
        else LABELS[run["status"]]
    )
    cards = [
        ("成功探索时长", f"{run['elapsedSeconds']:.1f} 秒"),
        ("已发送步数", run["steps"]),
        ("Crash / ANR", run["findings"]),
        ("测试设备", run["device"].get("model", cfg["serial"])),
    ]
    metrics = "".join(
        f"<div><span>{escape(title)}</span><strong>{escape(value)}</strong></div>"
        for title, value in cards
    )
    details = "".join(
        f"<dt>{escape(title)}</dt><dd>{escape(value)}</dd>"
        for title, value in [
            ("App 包名", cfg["package"]),
            ("关联项目", (run.get("project") or {}).get("name", "未关联")),
            ("计划时长", f"{cfg['runningMinutes']} 分钟"),
            ("最大步数", cfg["maxStep"]),
            ("操作间隔", f"{cfg['throttle']} 毫秒"),
            (
                "截图",
                f"{'开启' if cfg['takeScreenshots'] else '关闭'} · 失败前 {cfg['preFailureScreenshots']} / 后 {cfg['postFailureScreenshots']}",
            ),
            ("开始时间", run["startedAt"]),
            ("结束时间", run.get("endedAt") or "尚未结束"),
            ("运行编号", run["id"]),
        ]
    )
    native_view = (
        f'<section><h2>Kea2 原生测试报告</h2><a href="{native_link}" target="_blank">独立打开原生报告 ↗</a><iframe src="{native_link}" title="Kea2 原生报告"></iframe></section>'
        if native_link
        else "<p>原生报告将在 Kea2 完成数据同步后生成。原始执行数据始终保留。</p>"
    )
    content = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kea2 · {escape(cfg["package"])} 测试报告</title>
<style>body{{margin:0;background:#f7f8fa;color:#202a39;font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{max-width:1200px;margin:auto;padding:32px}}section{{background:white;border:1px solid #e7e9ee;border-radius:14px;padding:24px;margin:20px 0}}h1{{font-size:28px}}h2{{font-size:18px}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}.metrics div{{padding:16px;background:#f2f4f7;border-radius:10px}}span{{display:block;color:#536174}}strong{{font-size:22px}}dl{{display:grid;grid-template-columns:160px 1fr;gap:10px}}dd{{margin:0;overflow-wrap:anywhere}}iframe{{width:100%;height:1100px;border:0;margin-top:20px}}a{{color:#167957}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f7;padding:20px}}@media(max-width:650px){{main{{padding:16px}}.metrics{{grid-template-columns:repeat(2,1fr)}}dl{{grid-template-columns:100px 1fr}}}}</style>
<main><small>KEA2 / ANDROID TESTING</small><h1>Android 随机探索报告</h1><p role="status">{escape(banner)} · 更新于 {escape(run["updatedAt"])}</p><div class="metrics">{metrics}</div><section><h2>本次执行</h2><dl>{details}</dl><p>{escape(run.get("error") or "")}</p></section>{native_view}<section><h2>最近日志</h2><pre>{escape(chr(10).join(run.get("recentLogs", [])[-80:]))}</pre><a href="console.log">完整执行日志</a> · <a href="run.json">执行数据 JSON</a></section></main></html>"""
    temporary = directory / "index.html.tmp"
    temporary.write_text(content)
    temporary.replace(directory / "index.html")


def make_snapshot(source: Path, destination: Path, run: dict):
    destination.mkdir(parents=True, exist_ok=True)
    raw = raw_directory(source)
    if raw:
        shutil.copytree(raw, destination / "raw" / raw.name, dirs_exist_ok=True)
    shutil.copyfile(source / "console.log", destination / "console.log")
    write_json(
        destination / "run.json",
        {key: value for key, value in run.items() if key != "settings"},
    )
    if raw and run["status"] == "running":
        try:
            generate_native(destination)
        except (subprocess.SubprocessError, OSError):
            pass
    render_report(destination, run)
