# Kea2 Android 测试工作台

基于官方 [Kea2](https://github.com/ecnusse/Kea2) 1.2.4 的独立本机 Web 工具，当前工具版本 **0.1.3**。支持 Android 设备选择、随机探索参数配置、启动与停止、实时日志、本地 HTML 报告，以及向测试平台持续同步结果。Windows 和 macOS 均使用 Python 安装，在浏览器中操作；无需安装其他工作台或部署测试平台即可完成本地测试。

独立分发形式为 Python 源码包（`.tar.gz`）和通用 wheel（`.whl`）。需预装 Python 3.11+；源码包的双击入口自动准备工具依赖及缺失的 ADB，也支持手动安装。请按所用系统的完整指南操作：

| 文档 | 内容 |
|---|---|
| [Windows 使用指南](docs/windows.md) | PowerShell 环境要求、安装、启动、连接设备、测试、报告和上报 |
| [macOS 使用指南](docs/macos.md) | Terminal 环境要求、安装、启动、连接设备、测试、报告和上报 |
| [测试与平台上报说明](docs/usage.md) | 参数范围、状态、报告文件、上报门槛、配置与故障排查 |
| [独立工具发布说明](docs/release.md) | 构建、分发内容、离线安装、升级、验证与许可范围 |
| [版本记录](CHANGELOG.md) | 本次分发的功能和兼容性变更 |

## 启动概览

推荐完整解压源码包后双击根目录中的启动脚本：Windows 使用 `start-windows.bat`，macOS 使用 `start-macos.command`。需预装 Python 3.11+；脚本自动创建/检查 `.venv`，安装或更新 Python 依赖，查找 ADB（缺失时下载官方 Platform-Tools 到本工具 `tools/`），初始化缺失的 Kea2 配置，启动服务并在就绪后打开浏览器。优先使用端口 4320，被占用时自动选择可用端口并打开实际地址；重复双击复用已有服务。关闭原始启动终端会退出服务并释放端口。首次安装需要联网，不会安装系统级 Python 或手机驱动。

临时报告域名兼容：双击及命令行启动都会检查系统 hosts，确保 `192.168.55.199 aitest_engine_dev.ugreencloud.com` 为唯一有效映射。正确则跳过；缺失、重复或 IP 冲突时修正并刷新 DNS，保留其他域名。需要修改时，Windows 显示 UAC 授权，macOS 在 Terminal 提示 sudo 密码；仅配置助手提权，失败时停止本次启动。映射在工作台退出后保留，后续按通知移除此兼容措施。具体权限与故障排查见两系统指南。

下面是等价的手动安装和启动方式；双击启动无需逐条执行这些命令。

解压源码包并进入其根目录（包含 `pyproject.toml` 和 `configs/`），安装到本机虚拟环境。以下安装使用普通安装，不需要开发模式或测试依赖。

Windows / PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple .
.\.venv\Scripts\python.exe -m kea_workbench --root .
```

macOS / Terminal：

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install --index-url https://pypi.org/simple .
./.venv/bin/python -m kea_workbench --root .
```

安装命令显式使用官方 PyPI，覆盖本机默认镜像；源码隔离构建也使用该源下载 `setuptools>=77`。安装时需要联网访问该包源，受限网络可替换为组织提供的可用源，或按发布说明准备离线依赖。已有 0.1.0 源码包遇到镜像下载错误时，也可使用上述指定源的安装命令重试，无需重建虚拟环境。

默认地址为 [http://127.0.0.1:4320/](http://127.0.0.1:4320/)。端口被占用时，以终端打印的实际地址为准；双击入口会自动打开该地址。首次试运行建议关闭“同步到测试平台”，设置 1 分钟，连接已授权 USB 调试的设备，并确认被测 App 已安装。默认时长为 600 分钟，即 10 小时。

`kea2-web` 与 `python -m kea_workbench` 是同一启动入口；可以通过 `--host`、`--port`、`--root` 设置监听地址、首选端口和工作目录，首选端口被占用时也会自动改用可用端口。工作目录必须有官方 `configs/`；只安装 wheel 时请先按系统指南执行 `kea2 init`。设置和历史记录保存在工作目录内，重启时使用同一 `--root`。

## 使用边界

- 本机服务为单进程，默认只监听 `127.0.0.1`，没有用户登录或访问控制。一个工作目录只启动一个服务，不配置多个 Uvicorn worker。
- 同一设备只能运行一个测试；不要与其他 Monkey / Fastbot 或设备控制工具同时操作。不同设备可分别建立运行记录。
- 测试由服务进程持有，刷新或关闭浏览器页面仍会继续。服务随原始启动终端关闭而退出并释放端口；为保留完整报告，建议先点击“停止测试”，等待最终上报后再按 Ctrl+C 或关闭终端。
- 默认执行随机探索，页面不提供业务断言脚本上传或编辑。运行进程的当前目录是独立的 `artifacts/<运行编号>/`，不会自动发现源码目录里的单元测试。
- 平台上报可关闭。开启时使用默认 MinIO 凭据或自定义凭据，并需配置可访问的报告地址和平台已登记的服务/能力，满足实际探索满 300 秒等门槛；详细规则见[上报说明](docs/usage.md#平台上报)。

## 来源与许可

`configs/` 和 `quicktest.py` 来源于官方 `kea2 init`。保留第三方许可原文 [THIRD_PARTY_LICENSE.txt](THIRD_PARTY_LICENSE.txt)。Kea2 SDK 许可限定内部使用，分发范围包括许可允许的关联方和承包方；本工具按该范围准备内部独立分发包。对外分发前需取得相应许可。
