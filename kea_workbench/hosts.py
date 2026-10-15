"""Temporary report-platform hosts mapping; remove when internal DNS is ready."""

import argparse
import codecs
from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import sys

REPORT_HOST = "aitest_engine_dev.ugreencloud.com"
REPORT_IP = "192.168.55.199"


def hosts_path():
    if sys.platform == "win32":
        return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/drivers/etc/hosts"
    if sys.platform == "darwin":
        return Path("/etc/hosts")
    raise RuntimeError("临时 hosts 映射仅支持 Windows 和 macOS。")


def is_report_host(value):
    return value.lower().rstrip(".") == REPORT_HOST


def updated_hosts(data):
    """Keep unrelated entries, aliases, comments, encoding and line endings."""
    encoding, prefix = "utf-8", b""
    for bom, codec in (
        (codecs.BOM_UTF8, "utf-8"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ):
        if data.startswith(bom):
            prefix, encoding = bom, codec
            data = data[len(bom):]
            break
    text = data.decode(encoding, errors="surrogateescape")
    lines = text.splitlines(keepends=True)
    matches = []
    for line in lines:
        fields = line.partition("#")[0].split()
        if len(fields) >= 2:
            matches.extend(fields[0] for host in fields[1:] if is_report_host(host))
    if matches == [REPORT_IP]:
        return prefix + data

    newline = next(iter(re.findall(r"\r\n|\n|\r", text)), "\r\n" if os.name == "nt" else "\n")
    result = []
    for line in lines:
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        body, marker, comment = content.partition("#")
        fields = body.split()
        if len(fields) < 2 or not any(is_report_host(host) for host in fields[1:]):
            result.append(line)
            continue
        aliases = [host for host in fields[1:] if not is_report_host(host)]
        if aliases:
            result.append(f"{fields[0]}\t{' '.join(aliases)}" +
                          (f"  #{comment}" if marker else "") + ending)
        elif marker:
            result.append(f"#{comment}{ending}")
    content = "".join(result)
    if content and not content.endswith(("\n", "\r")):
        content += newline
    content += f"{REPORT_IP} {REPORT_HOST} # Kea2 workbench temporary report mapping{newline}"
    return prefix + content.encode(encoding, errors="surrogateescape")


@contextmanager
def locked_hosts(path):
    # Cooperating launchers must re-read inside the lock, including after UAC/sudo.
    with path.open("r+b") as handle:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield handle
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield handle
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def apply_mapping(path):
    with locked_hosts(path) as handle:
        original = handle.read()
        updated = updated_hosts(original)
        if original == updated:
            return False
        handle.seek(0)
        handle.write(updated)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
        return True


def flush_dns():
    if sys.platform == "win32":
        commands = [["ipconfig.exe", "/flushdns"]]
    elif sys.platform == "darwin":
        commands = [["/usr/bin/dscacheutil", "-flushcache"],
                    ["/usr/bin/killall", "-HUP", "mDNSResponder"]]
    else:
        raise RuntimeError("临时 DNS 刷新仅支持 Windows 和 macOS。")
    for command in commands:
        subprocess.run(command, check=True)


def is_admin():
    if sys.platform == "win32":
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return os.geteuid() == 0


def elevate_update():
    helper = str(Path(__file__).resolve())
    if sys.platform == "darwin":
        print("需要管理员权限修改 /etc/hosts 并刷新 DNS；请在 sudo 提示时输入本机管理员密码（输入不回显）。",
              flush=True)
        subprocess.run(["/usr/bin/sudo", "--", sys.executable, helper, "--apply"], check=True)
    elif sys.platform == "win32":
        print("需要管理员权限修改系统 hosts 并刷新 DNS；请在即将弹出的 UAC 窗口中允许。仅配置助手提权。",
              flush=True)

        def literal(value):
            return "'" + value.replace("'", "''") + "'"

        arguments = subprocess.list2cmdline([helper, "--apply"])
        script = (
            "$ErrorActionPreference='Stop'; "
            "$process = Start-Process -FilePath " + literal(sys.executable) +
            " -ArgumentList " + literal(arguments) +
            " -Verb RunAs -Wait -PassThru; exit $process.ExitCode"
        )
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       check=True)
    else:
        raise RuntimeError("临时 hosts 映射仅支持 Windows 和 macOS。")


def apply_and_flush():
    apply_mapping(hosts_path())
    # Always flush in the helper: another launcher may have completed the write.
    flush_dns()


def ensure_report_hosts():
    if sys.platform not in {"darwin", "win32"}:
        return
    path = hosts_path()
    try:
        current = path.read_bytes()
        if updated_hosts(current) == current:
            print(f"报告域名 hosts 映射正确，跳过：{REPORT_IP} {REPORT_HOST}", flush=True)
            return
        print(f"临时配置报告域名：{REPORT_IP} {REPORT_HOST}；保留其他域名映射。", flush=True)
        if is_admin():
            apply_and_flush()
        else:
            elevate_update()
        current = path.read_bytes()
        if updated_hosts(current) != current:
            raise RuntimeError("更新后校验未通过，目标域名仍存在缺失或冲突。")
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        raise RuntimeError(
            f"报告域名配置或 DNS 刷新未完成（{path}）：{error}。"
            "请允许管理员授权，并检查 hosts 文件权限及 DNS 刷新命令后重新启动；"
            "本次不启动或打开工作台。"
        ) from error
    print("报告域名映射已更新并刷新 DNS 缓存。", flush=True)


def main():
    parser = argparse.ArgumentParser(description="临时配置 Kea2 报告平台 hosts 映射")
    parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.apply:
        apply_and_flush()
    else:
        ensure_report_hosts()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"hosts 配置失败：{error}", file=sys.stderr, flush=True)
        sys.exit(1)
