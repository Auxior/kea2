"""Prepare a source distribution and run its local workbench using only the stdlib."""

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener, urlopen
import webbrowser
import zipfile

ROOT = Path(__file__).resolve().parent
DEFAULT_INDEX = "https://pypi.org/simple"


def say(message):
    print(message, flush=True)


def run_checked(command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def healthy(url, root):
    # A proxy configured for the internal platform must not intercept localhost.
    try:
        with build_opener(ProxyHandler({})).open(url + "api/health", timeout=1) as response:
            data = json.load(response)
        return (
            isinstance(data, dict)
            and data.get("application") == "kea2-workbench"
            and data.get("root") == str(root)
        )
    except (OSError, ValueError, URLError):
        return False


def open_page(url):
    say(f"Web 操作页面：{url}")
    try:
        if not webbrowser.open(url):
            say("浏览器未能自动打开，请复制上面的地址手动访问。")
    except Exception:
        say("浏览器未能自动打开，请复制上面的地址手动访问。")


def acquire_lock(root):
    directory = root / "data"
    directory.mkdir(exist_ok=True)
    path = directory / ".launcher.lock"
    handle = path.open("a+b")
    try:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("同一工作目录的启动窗口正在准备或运行，请返回已有窗口。") from None
    return handle


def prepare_python(root, env):
    directory = root / ".venv"
    python = directory / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not directory.exists():
        say("正在创建本工具的 Python 虚拟环境……")
        run_checked([sys.executable, "-m", "venv", str(directory)], env=env)
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; sys.exit(sys.version_info < (3, 11))"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode:
            raise OSError("版本不满足要求")
    except OSError:
        raise RuntimeError("已有 .venv 无法使用。请关闭工作台，将 .venv 重命名后重新双击启动。") from None

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    probe = """import json, sys
from importlib.metadata import version
from packaging.requirements import Requirement
project = json.loads(sys.argv[1])
assert version('kea2-workbench') == project['version']
for spec in project['dependencies']:
    requirement = Requirement(spec)
    if requirement.marker is None or requirement.marker.evaluate():
        assert version(requirement.name) in requirement.specifier
import fastapi, uvicorn, minio, httpx, kea2
"""
    ready = subprocess.run(
        [str(python), "-c", probe, json.dumps(project)], cwd=directory, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if ready:
        ready = subprocess.run(
            [str(python), "-m", "pip", "check"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    if not ready:
        index = os.environ.get("KEA2_PIP_INDEX_URL", DEFAULT_INDEX)
        say("正在安装或更新工作台依赖（首次安装需要联网）……")
        run_checked(
            [str(python), "-m", "pip", "install", "--index-url", index, "."],
            cwd=root, env=env,
        )
        run_checked([str(python), "-m", "pip", "check"], env=env)
    else:
        say("Python 依赖检查通过，复用已有环境。")
    return python


def download_adb(root, ca_bundle=None):
    platform = {"darwin": "darwin", "win32": "windows"}.get(sys.platform)
    if not platform:
        raise RuntimeError("自动下载 ADB 仅支持 Windows 和 macOS，请手动安装 Platform-Tools。")
    tools = root / "tools"
    tools.mkdir(exist_ok=True)
    url = f"https://dl.google.com/android/repository/platform-tools-latest-{platform}.zip"
    say("未找到 ADB，正在下载官方 Platform-Tools 到本工具的 tools/ 目录……")
    context = ssl.create_default_context()
    if ca_bundle:
        # python.org macOS installations may lack the optional system CA setup.
        # Add the certificate bundle already installed with the app dependencies.
        context.load_verify_locations(cafile=ca_bundle)
    with tempfile.TemporaryDirectory(prefix=".adb-", dir=tools) as temporary:
        temporary = Path(temporary)
        archive = temporary / "platform-tools.zip"
        with urlopen(url, timeout=30, context=context) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                target = (temporary / member.filename).resolve()
                if not target.is_relative_to(temporary.resolve() / "platform-tools"):
                    raise RuntimeError("Platform-Tools 压缩包包含不合法路径，已停止解压。")
            package.extractall(temporary)
        source = temporary / "platform-tools"
        executable = source / ("adb.exe" if sys.platform == "win32" else "adb")
        if not executable.is_file():
            raise RuntimeError("下载的 Platform-Tools 缺少 ADB。")
        if sys.platform != "win32":
            executable.chmod(0o755)
        destination = tools / "platform-tools"
        destination.mkdir(exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination


def prepare_adb(root, env, python):
    executable = "adb.exe" if sys.platform == "win32" else "adb"
    candidates = [root / "tools/platform-tools"]
    found = shutil.which("adb", path=env.get("PATH"))
    if found:
        candidates.append(Path(found).parent)
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if env.get(variable):
            candidates.append(Path(env[variable]) / "platform-tools")
    candidates.extend([
        Path.home() / "Library/Android/sdk/platform-tools",
        Path(env.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        / "Android/Sdk/platform-tools",
    ])
    directory = next((path for path in candidates if (path / executable).is_file()), None)
    if directory is None:
        certificates = subprocess.check_output(
            [str(python), "-c", "import certifi; print(certifi.where())"],
            env=env, encoding="utf-8", timeout=15,
        ).strip()
        directory = download_adb(root, certificates)
    env["PATH"] = str(directory) + os.pathsep + env.get("PATH", "")
    run_checked([str(directory / executable), "version"], env=env)
    say("ADB 检查通过；手机可以在工作台打开后连接。")


def wait_ready(process, url, root, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("工作台服务已退出，请查看上方启动日志。")
        if healthy(url, root):
            return
        time.sleep(0.2)
    raise RuntimeError("等待工作台启动超时，请查看上方日志。")


def stop_service(process):
    if process.poll() is not None:
        return
    say("正在退出工作台，等待测试清理、报告和最终同步；请保留此窗口……")
    process.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
    while True:
        try:
            process.wait()
            return
        except KeyboardInterrupt:
            say("仍在等待工作台正常退出，请勿关闭窗口。")


def main():
    if sys.version_info < (3, 11):
        raise RuntimeError("请先安装 Python 3.11 或更高版本。")
    root = ROOT
    if not (root / "pyproject.toml").is_file():
        raise RuntimeError("请完整解压源码分发包，不要只移动启动脚本。")
    port = int(os.environ.get("KEA2_PORT", "4320"))
    if not 1 <= port <= 65535:
        raise RuntimeError("KEA2_PORT 必须为 1–65535 的端口号。")
    url = f"http://127.0.0.1:{port}/"
    if healthy(url, root):
        say("本工作目录的工作台已运行，打开已有页面。")
        open_page(url)
        return 0
    lock = acquire_lock(root)
    try:
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError(f"端口 {port} 已被其他服务占用，请设置 KEA2_PORT 后重试。") from None
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        python = prepare_python(root, env)
        prepare_adb(root, env, python)
        if not (root / "configs").is_dir():
            say("正在初始化官方 Kea2 配置……")
            run_checked([str(python), "-m", "kea2.cli", "init"], cwd=root, env=env)
        say("正在启动工作台。退出前请先在页面停止测试，再在此窗口按 Ctrl+C。")
        options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                   if sys.platform == "win32" else {"start_new_session": True})
        process = subprocess.Popen(
            [str(python), "-m", "kea_workbench", "--root", str(root),
             "--host", "127.0.0.1", "--port", str(port)], cwd=root, env=env, **options,
        )
        try:
            wait_ready(process, url, root)
            open_page(url)
            return process.wait()
        finally:
            stop_service(process)
    finally:
        lock.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("工作台已停止。")
        sys.exit(0)
    except Exception as error:
        say(f"启动失败：{error}")
        say("请检查 Python 版本、网络或代理；保留本窗口日志以便排查。")
        sys.exit(1)
