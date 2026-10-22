# macOS 使用指南

适用版本：Kea2 Android 测试工作台 0.1.6 / Kea2 1.2.4。命令在 **Terminal / zsh** 中执行。示例路径 `$HOME/Tools/kea2-workbench` 表示解压后的源码根目录，请替换为实际路径。

## 1. 环境要求

| 项目 | 要求与检查 |
|---|---|
| 系统 | 推荐使用受所选 Python 支持的 macOS；Intel 和 Apple Silicon 应安装对应架构的 Python/依赖，工作目录可写 |
| Python | Python 3.11 或更高版本，`python3 --version` 必须满足要求，不依赖系统自带 Python |
| ADB | 双击脚本可自动下载缺失的 ADB；手动启动需安装官方 [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) 的 macOS 版，包含 `adb` 的目录加入 PATH，确认 `adb version` 能执行 |
| Android 设备 | 开启开发者选项、USB 调试并授权本机；被测 App 已安装，登录与权限已准备好 |
| 浏览器 | 当前版本的 Safari、Chrome、Edge 或 Firefox |
| 网络和空间 | 安装需要访问 Python 包源；上报需要访问测试平台与 MinIO；电脑和手机预留日志、截图空间 |

可以从 [Python 官方网站](https://www.python.org/downloads/macos/) 安装 Python；如已使用 Homebrew，也可通过它管理 Python 和 `android-platform-tools`。macOS 通常无需安装手机厂商的 Windows USB 驱动。无需完整 Android Studio，也不需要其他工作台。

可检查当前解释器与 ADB 的路径：

```sh
python3 --version
command -v python3
adb version
command -v adb
```

如果 `python3` 版本不符合要求，使用新安装解释器的完整路径创建虚拟环境。

## 2. 安装和配置

### 推荐：双击自动准备和启动

完整解压 0.1.6 源码包后，双击根目录的 `start-macos.command`，在 Terminal 中自动启动。需先安装 Python 3.11+；脚本会查找已有虚拟环境和 PATH 中的 Python，并兼容 Homebrew 常用目录。脚本以自身所在目录作为工作目录，支持含空格和中文的路径。

启动过程自动创建或复用 `.venv`，核验应用版本、直接依赖和依赖完整性；需要安装时使用官方 PyPI。ADB 优先使用本工具 `tools/platform-tools`，其次查找 PATH、`ANDROID_HOME` / `ANDROID_SDK_ROOT` 和常见 SDK 目录；缺失时下载官方 macOS Platform-Tools 到本工具 `tools/`，不会更改系统 PATH。若 `configs/` 缺失会自动初始化。服务就绪后打开默认浏览器，默认地址 `http://127.0.0.1:4320/`。

首次准备需要访问官方 PyPI 和（ADB 缺失时）Google 下载服务，安装完成后依赖版本一致则直接复用。Python 本身需预装；无需先连接手机即可打开工作台。失败时 Terminal 保留错误和安装日志。若 `.command` 丢失可执行权限，在源码根目录执行一次：

```sh
chmod +x start-macos.command
```

若系统安全设置阻止打开，请对可信来源的脚本使用 Finder 中的“打开”或系统设置允许该脚本运行。跨系统复制的 `.venv` 无法使用时，先重命名该目录再重新启动；保持源码包文件完整，不要只移动脚本。

重复双击只打开本目录已有服务的页面，自动换过端口时也会找回实际地址；正在准备时提示返回已有窗口。优先使用端口 4320，被其他应用占用时由系统分配可用端口，终端地址和浏览器访问地址同步更新；必要时从 Terminal 指定首选端口和包源：

```sh
KEA2_PORT=4321 KEA2_PIP_INDEX_URL=https://pypi.org/simple ./start-macos.command
```

关闭原始启动 Terminal 会退出服务并释放端口；启动器被强制结束时，服务通过控制管道检测父进程退出，清理阻塞时最多等待约 5 秒后结束。为保留完整报告，建议先在网页停止测试并等待最终同步，再按一次 Ctrl+C 或关闭终端。重复双击打开的窗口不持有服务，关闭它不影响原始启动窗口。浏览器未能自动打开时，复制终端打印的实际 URL 手动访问。

以下是手动安装方式；使用源码包双击启动无需逐条执行。

### 使用源码分发包

解压维护者提供的 `kea2_workbench-0.1.6.tar.gz`，进入包含 `pyproject.toml`、`settings.example.json` 和 `configs/` 的目录：

```sh
cd "$HOME/Tools/kea2-workbench"
python3 -m venv .venv
./.venv/bin/python -m pip install --index-url https://pypi.org/simple --upgrade pip
./.venv/bin/python -m pip install --index-url https://pypi.org/simple .
./.venv/bin/python -m kea_workbench --help
./.venv/bin/python -m kea2.cli version
```

最后一条应输出 `1.2.4`。使用虚拟环境中解释器的完整路径，无需激活环境；源码包已包含 `configs/`，无需再次执行初始化。

安装命令显式使用官方 PyPI，覆盖本机配置的默认镜像，并将该源传递给隔离构建子进程。已有 0.1.0 源码包也可直接用这条命令重新安装，无需删除 `.venv`：

```sh
./.venv/bin/python -m pip install --index-url https://pypi.org/simple .
```

若出现 `No matching distribution found for setuptools>=77` 且日志显示清华等镜像，先按上述命令重试。`setuptools>=77` 用于构建元数据中的 `license-files`，应保留；仅提前安装 setuptools 不能阻止默认隔离构建再次访问旧镜像。若官方源也不可访问，使用组织提供的可用包源替换 `--index-url`，或按[离线安装](release.md#离线安装)准备 wheel 和依赖包。

### 使用 wheel 分发包

将 wheel 放入新的工作目录，例如 `$HOME/Kea2Data`：

```sh
mkdir -p "$HOME/Kea2Data"
cd "$HOME/Kea2Data"
python3 -m venv .venv
./.venv/bin/python -m pip install --index-url https://pypi.org/simple ./kea2_workbench-0.1.6-py3-none-any.whl
./.venv/bin/python -m kea2.cli init
```

`init` 会生成 `configs/` 和 `quicktest.py`。后续使用这个目录作为 `--root`；wheel 只包含应用与默认连接配置，不含工作目录的官方配置。

只做本地测试时，关闭“同步到测试平台”即可。需要上报时，在网页右上角“设置”中配置平台与 MinIO。保存位置为 `<工作目录>/data/settings.json`，只影响之后创建的任务。字段说明见[连接设置](usage.md#连接设置)。

默认平台为 `http://aitest_engine.ugreencloud.com`，结果接口为 `http://aitest_engine.ugreencloud.com/api/tasks/external-reports`。已有环境需在“设置”中核对并保存这两个地址；新建测试使用保存后的配置。

## 3. 启动与退出

打开 Terminal，进入安装时的工作目录：

```sh
cd "$HOME/Tools/kea2-workbench"
./.venv/bin/python -m kea_workbench --host 127.0.0.1 --port 4320 --root .
```

wheel 用户改为 `cd "$HOME/Kea2Data"`。默认在浏览器打开 [http://127.0.0.1:4320/](http://127.0.0.1:4320/)，端口被占用时请使用终端打印的实际地址。手动命令不会自动打开网页。同一虚拟环境的等价入口为：

```sh
./.venv/bin/kea2-web --root .
```

从其他目录启动时，指定完整的解释器和工作目录路径：

```sh
"$HOME/Tools/kea2-workbench/.venv/bin/python" -m kea_workbench --root "$HOME/Tools/kea2-workbench" --port 4321
```

首选网页为 [http://127.0.0.1:4321/](http://127.0.0.1:4321/)。首选端口被占用时自动选择其他可用端口，请使用终端打印的实际地址；一个工作目录只运行一个服务。

保持 Terminal 打开，长时间测试期间保持电脑唤醒及 USB 可用。可按需要在系统设置中调整睡眠策略，或用系统 `caffeinate` 命令包装启动：

```sh
caffeinate -i ./.venv/bin/python -m kea_workbench --root .
```

关闭原始启动 Terminal 会结束工作台服务并释放端口。为保留完整数据，建议先在网页点击“停止测试”，等待报告与最终上报完成，再按一次 Ctrl+C 或关闭窗口。页面“停止测试”向独立会话中的 Kea2 子进程发送 SIGINT，触发上游清理；超过 90 秒后强制结束并记录设备清理未确认。直接关闭终端或杀进程时，设备清理与报告可能未完成。

## 4. 连接设备

1. 用数据线连接 Android 手机，开启“开发者选项 → USB 调试”。解锁手机并接受本机 RSA 指纹授权。
2. 在 Terminal 检查：

```sh
adb devices -l
adb -s DEVICE_SERIAL shell pm path com.ugreen.iot
```

把 `DEVICE_SERIAL` 和包名替换为实际设备及 App。设备状态须为 `device`，App 查询应返回以 `package:` 开头的路径。`unauthorized` 需要手机授权，`offline` 需要检查连接，空列表先排查数据线、USB 扩展坞或接口。

3. 网页点击“刷新设备”，选择设备；只有一台可用设备时自动选中，多台时手动选择，未授权/离线设备不可选。

没有其他 ADB 任务时，可尝试：

```sh
adb kill-server
adb start-server
adb devices -l
```

修改 shell 的 PATH 后，从能执行 `adb version` 的新 Terminal 会话重新启动工作台。既有服务不会继承新的 PATH。

## 5. 测试操作

1. 填写实际安装的 App 包名，核对页面自动关联的项目。
2. 首次验证建议关闭“同步到测试平台”，设置 **1 分钟**。其他默认参数为 99999 步、500 毫秒间隔、25 步采集周期，截图开启（失败前 20 / 后 5）；正式默认时长 600 分钟为 10 小时。
3. 点击“开始测试”。从“准备中”进入“运行中”后检查成功探索时长、发送步数、Crash / ANR 及日志。“完整日志”可查看最近 160 行之外的输出。
4. 达到时长或最大步数任一限制即结束；提前结束点击“停止测试”，等待同步和报告生成。执行状态和质量结果分别判断，不能把“已完成”直接理解为没有缺陷。
5. 在“运行记录”点击“查看过程”或“报告”回看。页面刷新或关闭后测试继续由服务持有。

失败前截图为 0 表示每步采集，此时失败后也需设为 0。默认 20/5 模式在没有失败时不一定生成失败截图。完整参数及状态见[测试参数](usage.md#测试参数)。

## 6. 报告查看

选中运行后点击“查看测试报告”，也可在“运行记录”点击“报告”。结束后可查看；已取得上报资格的活动任务也显示入口，内容随报告生成与同步更新。若提示原生报告尚未生成，可先查看完整日志和现有执行信息。

工作台报告位于 `<工作目录>/artifacts/<运行编号>/index.html`；原生报告为 `raw/res_<运行编号>/bug_report.html`。配置和实际命令在 `run.json`，全部终端输出在 `console.log`。

优先通过 HTTP 页面查看，转移时复制整个运行目录，保留原生报告的图片、日志及相对路径。工作台发送步数/探索时长与原生报告设备日志统计的事件数/时长可能不同。详见[报告与数据](usage.md#报告与数据)。

## 7. 平台上报流程

1. 连接可访问内部平台和 MinIO 的网络，进入网页“设置”，核对平台接口、前端地址、服务/能力 ID、MinIO 地址、已创建的桶和前缀。Access Key / Secret Key 未配置或留空时自动使用默认值，也可填写自定义凭据；报告 URL 需要能被使用者的浏览器访问。
2. 选择已关联的 App 并开启“同步到测试平台”，配置时长至少 5 分钟。确保步数上限不会在实际探索满 300 秒前耗尽；准备和收尾不计时，达标前发现 Crash / ANR 会阻止资格取得。
3. 达标后通过后台线程持续生成快照、上传报告并提交平台记录，观察“同步中 / 已上报 / 上报失败”。同步循环通常等待 15 秒，生成和网络耗时会增加实际间隔。
4. 有平台回执后点击“平台记录”跳转任务中心；在线报告地址保存在 `run.json` 的 `delivery.reportUrl`。平台实际展示名称依部署版本而定。
5. 结束后确认最终上报。失败且任务已结束、当前上报线程退出时，可点击“重试上报”。补报使用该运行创建时的连接快照，无需重新连接手机；页面修改设置不会改变旧运行的快照。

未达门槛的历史结果不能补报。完整门槛、回执校验和状态映射见[平台上报](usage.md#平台上报)。

## 8. 常见问题

| 现象 | macOS 检查方式 |
|---|---|
| Python 版本不满足或依赖安装失败 | 确认解释器版本/架构，使用对应架构 Python 重新创建 `.venv`，检查包源网络 |
| 隔离构建找不到 `setuptools>=77` | 使用 `pip install --index-url https://pypi.org/simple .`；检查日志中的包源地址，参见上文安装说明 |
| `adb: command not found` | 用 `command -v adb` 检查 PATH，在能找到 ADB 的新 Terminal 会话重新启动服务 |
| 手机不可选 | 检查授权、数据线、扩展坞，确认 `adb devices -l` 为 `device` |
| Kea2 未初始化 | `--root` 中须有 `configs/`，新 wheel 工作目录先运行 `python -m kea2.cli init` |
| 测试启动报 `IsADirectoryError`，路径含 `fastbot_configs/__pycache__` | Kea2 1.2.4 的配置同步误复制了缓存目录。安装包含此修复的工作台后，从 Web 页面重新创建测试；恢复步骤见下文 |
| 电脑睡眠或拔线后异常终止 | 保持唤醒/连接，查看日志与设备残留进程后新建任务；不会自动续跑旧任务 |
| 原生报告缺失 | 查看 `console.log`，如果生成过补充报告，还可查 `report-generation.log`；清理未完成时数据可能不完整 |

遇到上述缓存目录错误时，关闭工作台，在包含本次修复的源码根目录执行以下命令，再启动工作台。安装 0.1.6 后，让测试子进程使用修复后的入口：

```sh
./.venv/bin/python -m pip install --index-url https://pypi.org/simple --force-reinstall --no-deps .
./start-macos.command
```

旧版用户请先按安装说明解压修复后的源码并准备依赖；保留原工作目录的 `configs/`、`data/` 和 `artifacts/`，按原 `--root` 启动可继续查看历史记录。无需删除 SDK 缓存或重置配置；手工运行 `python -m kea2.cli run` 仍会触发上游缺陷，此兼容修复作用于工作台创建的测试任务。失败任务不会自动续跑，需要重新创建。

自动化测试不能替代真实手机验收；发布时的检查项见[发布验收](release.md#发布验收)。
