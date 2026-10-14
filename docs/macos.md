# macOS 使用指南

适用版本：Kea2 Android 测试工作台 0.1.1 / Kea2 1.2.4。命令在 **Terminal / zsh** 中执行。示例路径 `$HOME/Tools/kea2-workbench` 表示解压后的源码根目录，请替换为实际路径。

## 1. 环境要求

| 项目 | 要求与检查 |
|---|---|
| 系统 | 推荐使用受所选 Python 支持的 macOS；Intel 和 Apple Silicon 应安装对应架构的 Python/依赖，工作目录可写 |
| Python | Python 3.11 或更高版本，`python3 --version` 必须满足要求，不依赖系统自带 Python |
| ADB | 官方 [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) 的 macOS 版，包含 `adb` 的目录加入 PATH；`adb version` 能执行 |
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

### 使用源码分发包

解压维护者提供的 `kea2_workbench-0.1.1.tar.gz`，进入包含 `pyproject.toml`、`settings.example.json` 和 `configs/` 的目录：

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
./.venv/bin/python -m pip install --index-url https://pypi.org/simple ./kea2_workbench-0.1.1-py3-none-any.whl
./.venv/bin/python -m kea2.cli init
```

`init` 会生成 `configs/` 和 `quicktest.py`。后续使用这个目录作为 `--root`；wheel 只包含应用与默认连接配置，不含工作目录的官方配置。

只做本地测试时，关闭“同步到测试平台”即可。需要上报时，在网页右上角“设置”中配置平台与 MinIO。保存位置为 `<工作目录>/data/settings.json`，只影响之后创建的任务。字段说明见[连接设置](usage.md#连接设置)。

## 3. 启动与退出

打开 Terminal，进入安装时的工作目录：

```sh
cd "$HOME/Tools/kea2-workbench"
./.venv/bin/python -m kea_workbench --host 127.0.0.1 --port 4320 --root .
```

wheel 用户改为 `cd "$HOME/Kea2Data"`。在浏览器打开 [http://127.0.0.1:4320/](http://127.0.0.1:4320/)，命令不会自动打开网页。同一虚拟环境的等价入口为：

```sh
./.venv/bin/kea2-web --root .
```

从其他目录启动时，指定完整的解释器和工作目录路径：

```sh
"$HOME/Tools/kea2-workbench/.venv/bin/python" -m kea_workbench --root "$HOME/Tools/kea2-workbench" --port 4321
```

对应网页为 [http://127.0.0.1:4321/](http://127.0.0.1:4321/)。更改端口可解决占用问题；一个工作目录只运行一个服务。

保持 Terminal 打开，长时间测试期间保持电脑唤醒及 USB 可用。可按需要在系统设置中调整睡眠策略，或用系统 `caffeinate` 命令包装启动：

```sh
caffeinate -i ./.venv/bin/python -m kea_workbench --root .
```

结束时先在网页点击“停止测试”，等待报告与最终上报完成，再在 Terminal 按一次 Ctrl+C。macOS 停止向独立会话中的 Kea2 子进程发送 SIGINT，触发上游清理；超过 90 秒后强制结束并记录设备清理未确认。强制关闭终端或杀进程可能丢失收尾数据。

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
2. 首次验证建议关闭“同步到测试平台”，设置 **1 分钟**。其他默认参数为 99999 步、300 毫秒间隔、25 步采集周期，截图开启（失败前 20 / 后 5）；正式默认时长 600 分钟为 10 小时。
3. 点击“开始测试”。从“准备中”进入“运行中”后检查成功探索时长、发送步数、Crash / ANR 及日志。“完整日志”可查看最近 160 行之外的输出。
4. 达到时长或最大步数任一限制即结束；提前结束点击“停止测试”，等待同步和报告生成。执行状态和质量结果分别判断，不能把“已完成”直接理解为没有缺陷。
5. 在“运行记录”点击“查看过程”或“报告”回看。页面刷新或关闭后测试继续由服务持有。

失败前截图为 0 表示每步采集，此时失败后也需设为 0。默认 20/5 模式在没有失败时不一定生成失败截图。完整参数及状态见[测试参数](usage.md#测试参数)。

## 6. 报告查看

选中运行后点击“查看测试报告”，也可在“运行记录”点击“报告”。结束后可查看；已取得上报资格的活动任务也显示入口，内容随报告生成与同步更新。若提示原生报告尚未生成，可先查看完整日志和现有执行信息。

工作台报告位于 `<工作目录>/artifacts/<运行编号>/index.html`；原生报告为 `raw/res_<运行编号>/bug_report.html`。配置和实际命令在 `run.json`，全部终端输出在 `console.log`。

优先通过 HTTP 页面查看，转移时复制整个运行目录，保留原生报告的图片、日志及相对路径。工作台发送步数/探索时长与原生报告设备日志统计的事件数/时长可能不同。详见[报告与数据](usage.md#报告与数据)。

## 7. 平台上报流程

1. 连接可访问内部平台和 MinIO 的网络，进入网页“设置”，填写平台接口、前端地址、服务/能力 ID、MinIO 地址、已创建的桶、前缀和凭据；报告 URL 需要能被使用者的浏览器访问。
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
| 电脑睡眠或拔线后异常终止 | 保持唤醒/连接，查看日志与设备残留进程后新建任务；不会自动续跑旧任务 |
| 原生报告缺失 | 查看 `console.log`，如果生成过补充报告，还可查 `report-generation.log`；清理未完成时数据可能不完整 |

自动化测试不能替代真实手机验收；发布时的检查项见[发布验收](release.md#发布验收)。
