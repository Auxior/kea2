# Windows 使用指南

适用版本：Kea2 Android 测试工作台 0.1.3 / Kea2 1.2.4。命令均在 **PowerShell** 中执行。示例路径 `C:\Tools\kea2-workbench` 表示解压后的源码根目录，请替换为实际路径。

## 1. 环境要求

| 项目 | 要求与检查 |
|---|---|
| 系统 | 推荐 Windows 10 / 11 64 位；需有可写工作目录和正常的控制台会话 |
| Python | 3.11 或更高版本，推荐使用 64 位 Python；`py -3 --version` 必须满足要求 |
| ADB | 双击脚本可自动下载缺失的 ADB；手动启动需安装官方 [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools)，将包含 `adb.exe` 的目录加入 PATH，确认 `adb version` 能执行 |
| 设备驱动 | 根据手机厂商安装 Windows USB/ADB 驱动，设备管理器能识别 Android 调试设备 |
| Android 设备 | 已开启开发者选项、USB 调试并授权本机；被测 App 已安装，已完成测试需要的登录与权限授予 |
| 浏览器 | 当前版本的 Edge、Chrome 或 Firefox |
| 网络和空间 | 安装需要访问 Python 包源；上报需要访问测试平台与 MinIO；电脑与手机为日志和截图预留可用空间 |

本工具不需要 Android Studio 完整环境；Platform-Tools 提供 ADB 即可。工具不会替你安装被测 APK。默认仅在本机浏览器访问。

如果 `py` 不存在，可用已安装的 `python` 替代，但先执行 `python --version` 确认版本。多个 Python 共存时，请指定符合要求的版本（例如 `py -3.11`）。

## 2. 安装和配置

### 推荐：双击自动准备和启动

完整解压 0.1.3 源码包后，双击根目录的 `start-windows.bat`。先安装 Python 3.11+（包含 Python Launcher 或加入 PATH）。脚本自动使用自身所在目录，不受资源管理器当前目录影响，也支持含空格和中文的路径。

启动过程会检查 Python 版本，在本工具目录创建或复用 `.venv`，检查应用版本、直接依赖和依赖完整性；需要安装时通过官方 PyPI 更新。ADB 优先使用本工具 `tools/platform-tools`，其次查找 PATH、`ANDROID_HOME` / `ANDROID_SDK_ROOT` 和常见 SDK 目录；缺失时下载官方 Windows Platform-Tools 到 `tools/`，不会更改系统 PATH。如果 `configs/` 缺失，也会自动初始化。服务就绪后打开默认浏览器，默认地址仍为 `http://127.0.0.1:4320/`。

临时兼容报告平台域名：每次启动（包括重复双击、`python -m kea_workbench` 和 `kea2-web`）检查 `%SystemRoot%\System32\drivers\etc\hosts` 中的 `192.168.55.199 aitest_engine_dev.ugreencloud.com`。已有唯一正确映射时跳过，不请求权限；缺失则添加，其他 IP 或重复映射则合并为这一条，保留其他域名、同一行的别名和注释。需要修改时明确提示，通过 PowerShell 弹出 UAC，请允许授权或按系统要求提供管理员凭据；仅 hosts 配置助手以管理员权限运行。更新后执行 `ipconfig /flushdns` 刷新 DNS，再校验 hosts，工作台仍以原用户权限运行。拒绝授权、权限受组织策略限制或刷新失败会中止本次启动并保留错误。该系统映射在退出后保留，后续收到移除通知时再撤销临时兼容措施。

首次准备需要访问官方 PyPI 和（ADB 缺失时）Google 下载服务。已安装且版本一致的依赖会复用，后续启动不需要重新联网下载。Python 和手机厂商 USB 驱动需预先安装；手机尚未连接也可以打开工作台。失败时窗口保留错误和安装日志，按提示排查后重试。跨系统复制的 `.venv` 无法使用时，先将其重命名，再重新双击；不要只复制脚本。

同一目录重复双击只打开已有页面，不重复启动服务；即使首次启动自动换过端口，也会找回实际地址。已有窗口仍在准备时会提示返回该窗口。优先使用端口 4320；被其他应用占用时，由系统分配可用端口，终端地址和浏览器访问地址同步更新。若需指定首选端口或包源，可在 PowerShell 设置当前会话变量后运行：

```powershell
$env:KEA2_PORT = '4321'
$env:KEA2_PIP_INDEX_URL = 'https://pypi.org/simple'
.\start-windows.bat
```

关闭原始启动窗口会退出服务并释放端口；启动器被强制结束时，服务也会通过控制管道检测到父进程退出，清理阻塞时最多等待约 5 秒后结束。为保留完整报告，建议先在网页停止测试、等待最终上报，再按一次 Ctrl+C 或关闭窗口。重复双击打开的窗口不持有服务，关闭它不影响原始启动窗口。浏览器未能自动打开时，可手动打开终端打印的实际 URL。

以下是手动安装方式；使用源码包双击启动时无需逐条执行。

### 使用源码分发包

解压维护者提供的 `kea2_workbench-0.1.3.tar.gz`，进入包含 `pyproject.toml`、`settings.example.json` 和 `configs/` 的目录：

```powershell
Set-Location 'C:\Tools\kea2-workbench'
py -3 --version
adb version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --upgrade pip
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple .
.\.venv\Scripts\python.exe -m kea_workbench --help
.\.venv\Scripts\python.exe -m kea2.cli version
```

最后一条应输出 Kea2 `1.2.4`。使用完整的虚拟环境解释器路径，不需要激活环境或修改 PowerShell 执行策略。源码包已提供 `configs/`，无需再次初始化。

安装命令显式使用官方 PyPI，覆盖本机的默认镜像，并供隔离构建子进程下载 `setuptools>=77`。如果此前未指定源而出现 `No matching distribution found for setuptools>=77`，按上述命令重新执行安装即可，无需删除 `.venv`。不要降低构建依赖版本来绕过镜像故障；若官方源不可访问，替换为组织提供的可用包源，或使用[离线安装](release.md#离线安装)。

### 使用 wheel 分发包

wheel 只包含应用和默认连接配置，不含工作目录的官方配置。将 wheel 放到新建的工作目录中（下面使用 `C:\Tools\Kea2Data`），执行：

```powershell
New-Item -ItemType Directory -Force 'C:\Tools\Kea2Data'
Set-Location 'C:\Tools\Kea2Data'
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple .\kea2_workbench-0.1.3-py3-none-any.whl
.\.venv\Scripts\python.exe -m kea2.cli init
```

`init` 在当前目录生成 `configs/` 和 `quicktest.py`。将此目录作为后续 `--root`，使测试进程能找到上游配置。安装应用不等于初始化工作目录。

首次仅本地测试可直接关闭“同步到测试平台”。开启上报前，在网页右上角“设置”中填写平台及 MinIO 配置。保存结果位于 `<工作目录>\data\settings.json`，任务保存创建时的连接快照。字段说明见[连接设置](usage.md#连接设置)。

## 3. 启动与退出

每次打开 PowerShell，进入安装时的工作目录：

```powershell
Set-Location 'C:\Tools\kea2-workbench'
.\.venv\Scripts\python.exe -m kea_workbench --host 127.0.0.1 --port 4320 --root .
```

wheel 用户将上述目录替换为 `C:\Tools\Kea2Data`。默认打开 [http://127.0.0.1:4320/](http://127.0.0.1:4320/)，端口被占用时请使用终端打印的实际地址。手动启动命令不会自动打开浏览器。也可使用同一虚拟环境的入口：

```powershell
.\.venv\Scripts\kea2-web.exe --root .
```

从其他目录启动时，用完整路径指定解释器和工作目录，例如：

```powershell
& 'C:\Tools\kea2-workbench\.venv\Scripts\python.exe' -m kea_workbench --root 'C:\Tools\kea2-workbench' --port 4321
```

首选网页地址为 [http://127.0.0.1:4321/](http://127.0.0.1:4321/)。首选端口被占用时自动选择其他可用端口，请使用终端打印的实际地址；不要重复启动占用同一工作目录的服务。

运行期间保持电脑和 USB 连接可用。关闭原始启动终端会结束工作台服务并释放端口；如果正在测试，报告和设备清理可能未完成。建议先在网页停止测试，等运行变为终态并完成最终上报，再按一次 Ctrl+C 或关闭窗口。页面“停止测试”通过专用子进程组的 Ctrl+Break 转换为 Kea2 的中断清理流程；超出 90 秒会强制结束并记录清理未确认。

## 4. 连接设备

1. 用支持数据传输的 USB 线连接手机；手机开启“开发者选项 → USB 调试”。
2. 解锁手机，接受“允许 USB 调试”弹窗并确认本机 RSA 指纹。
3. 在 PowerShell 检查：

```powershell
adb devices -l
adb -s DEVICE_SERIAL shell pm path com.ugreen.iot
```

将 `DEVICE_SERIAL` 替换为上一条输出的序列号，包名替换为被测 App。第一条中设备状态应为 `device`，第二条应输出以 `package:` 开头的 APK 路径。`unauthorized` 需要手机授权；`offline` 需要检查连接；空列表优先检查 USB 线和 Windows 驱动。

4. 在网页“新建测试 → 测试设备”点击“刷新设备”，选择设备。只有一台可用设备时自动选中，多台时手动选择；非 `device` 状态不可选。

如果当前没有其他 ADB 测试，可尝试重新启动 ADB 服务：

```powershell
adb kill-server
adb start-server
adb devices -l
```

修改 PATH 后重新打开 PowerShell并重新启动工作台，运行中的服务不会自动继承新的 PATH。

## 5. 测试操作

1. 填写手机上实际安装的 App 包名。平台项目自动按包名关联，不提供手动项目 ID。
2. 首次验证关闭“同步到测试平台”，设置时长 **1 分钟**，其余参数可使用默认值。正常默认值为 600 分钟、99999 步、500 毫秒间隔、25 步采集一次、开启截图（失败前 20 / 后 5）。
3. 点击“开始测试”，确认状态从“准备中”进入“运行中”，观察成功探索时长、已发送步数、Crash / ANR 和最近日志。最近日志保留 160 行，可用“完整日志”查看全部输出。
4. 时间或最大步数任一到达限制就结束。需提前结束时点击“停止测试”，等待设备同步和报告生成。“已完成”只表示执行完成，质量结论需结合 Crash / ANR 和原生报告。
5. 点击历史记录的“查看过程”可切换运行；点击“报告”查看已有结果。刷新或关闭网页不会停止任务。

截图设为失败前 0 时，需要同时将失败后设为 0；表示每步截图，可能明显增加手机与电脑存储占用。完整范围和状态见[测试参数](usage.md#测试参数)。

## 6. 报告查看

网页选中运行后点击“查看测试报告”，或在“运行记录”点击“报告”。通常结束后可查看；已满足平台上报门槛的活动任务也显示报告入口，实际内容取决于报告生成和同步进度。

报告位于 `<工作目录>\artifacts\<运行编号>\index.html`，保留配置、状态、进度及最近日志，嵌入 `raw\res_<运行编号>\bug_report.html` 原生报告。`console.log` 为完整日志，`run.json` 为参数和真实命令。原生报告尚未生成时，工作台会显示提示并保留已有数据。

优先通过工作台的 HTTP 页面查看报告。需转移到其他电脑时，复制整个运行目录，保持 `raw` 内图片与文件的相对路径；仅复制 `index.html` 无法完整展示报告。报告的事件数和时长可能与工作台发送日志统计不同，详见[报告与数据](usage.md#报告与数据)。

## 7. 平台上报流程

1. 连接可访问内部测试平台与 MinIO 的网络，在网页“设置”中核对结果上报接口、平台页面地址、服务 ID、能力 ID、MinIO 地址、桶和前缀。Access Key / Secret Key 未配置或留空时自动使用默认值，也可填写自定义凭据。桶须已创建，报告地址需能被查看报告的浏览器访问。
2. 选择已关联项目的 App，开启“同步到测试平台”，设置时长至少 5 分钟，并确保最大步数不会在实际探索满 300 秒前耗尽。门槛不包含准备与收尾时间；达标前发现 Crash / ANR 的运行不会取得上报资格。
3. 达标后后台持续生成并上传报告快照，提交平台结果。等待约 15 秒是同步循环间隔，生成和上传耗时可能使实际间隔更长；观察“同步中 / 已上报 / 上报失败”。
4. 出现“平台记录”时可跳到平台任务中心。在线 HTML 报告地址是 `run.json` 中 `delivery.reportUrl`；平台列表和报告入口以部署的实际界面为准。
5. 结束后检查最终上报状态。上报失败可在任务结束且当前上报线程已退出后点击“重试上报”，重试使用该任务保存的连接配置，不连接设备。修改设置只影响新任务，不能用界面改写旧任务的连接快照。

未达门槛的任务保留本地报告，不能补报。完整门槛、状态映射和回执排查见[平台上报](usage.md#平台上报)。

## 8. 常见问题

| 现象 | Windows 检查方式 |
|---|---|
| 找不到 `py` / Python 版本过低 | 安装符合要求的 Python，使用已确认版本的解释器重新创建 `.venv` |
| 报告域名配置或 DNS 刷新失败 | 重新启动并允许 UAC 授权；检查管理员凭据、系统 hosts 权限及组织安全策略是否允许修改，保留窗口中的错误信息 |
| 找不到 `adb` | 将 Platform-Tools 加入 PATH，重新打开 PowerShell；`Get-Command adb` 检查实际路径 |
| 手机未显示或未授权 | 检查数据线、设备管理器的厂商驱动及手机授权弹窗 |
| 提示未初始化 Kea2 | 确认 `--root` 中有 `configs/`；对新的 wheel 工作目录执行 `python -m kea2.cli init` |
| 测试启动报 `IsADirectoryError`，路径含 `fastbot_configs\__pycache__` | Kea2 1.2.4 的配置同步误复制了缓存目录。安装包含此修复的工作台后，从 Web 页面重新创建测试；恢复步骤见下文 |
| 终端窗口关闭后任务异常 | 重新启动同一工作目录，检查设备残留 Monkey / Fastbot，达标且上报失败的记录可尝试补报 |
| 中文日志异常 | 本版本将子进程和保存的日志、JSON、HTML 设为 UTF-8；手工编辑设置时也保存为 UTF-8 |

遇到上述缓存目录错误时，关闭工作台，在包含本次修复的源码根目录执行以下命令，再启动工作台。即使工具版本号仍为 0.1.3，也应重新安装，让测试子进程使用修复后的入口：

```powershell
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --force-reinstall --no-deps .
.\start-windows.bat
```

旧版用户请先按安装说明解压修复后的源码并准备依赖；保留原工作目录的 `configs/`、`data/` 和 `artifacts/`，按原 `--root` 启动可继续查看历史记录。无需删除 SDK 缓存或重置配置；手工运行 `python -m kea2.cli run` 仍会触发上游缺陷，此兼容修复作用于工作台创建的测试任务。失败任务不会自动续跑，需要重新创建。

本仓库当前自动验证在 macOS 上执行；Windows 信号分支有模拟测试，真实 Windows 设备运行需按[发布验收](release.md#发布验收)核验。
