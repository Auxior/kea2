# 独立工具发布说明

当前版本为 **0.1.1**，发布范围为 Kea2 许可允许的内部使用。产物是 Python 应用包，依赖 Python、ADB 和官方 Kea2 SDK；Windows 和 macOS 均无需依赖原开发目录或其他专项工具。工具不是捆绑 Python/ADB 的免安装桌面程序。

## 发布内容

| 文件 | 用途 |
|---|---|
| `kea2_workbench-0.1.1.tar.gz` | 源码分发包：应用、网页资源、默认设置、官方 `configs/`、样例、两系统指南、测试与许可 |
| `kea2_workbench-0.1.1-py3-none-any.whl` | 应用安装包：应用、网页资源、内置默认设置及许可；工作目录需另行执行 `kea2 init` |
| `SHA256SUMS.txt` | 两个分发包的 SHA-256 校验值，用于核对传输完整性 |

源码包推荐用于首次部署，解压后可以直接以其根目录作为工作目录。wheel 适合把应用安装到虚拟环境后另建数据目录。`py3-none-any` 仅指工作台自身使用通用 Python 包，不表示依赖已离线捆绑或所有平台均完成真机验收。

分发包不包含 `.venv/`、`data/`、`artifacts/`、本机 MinIO 密钥、个人设备报告或其他工程。示例文件含内部开发环境的地址与登记 ID，不包含访问密钥；分发对象应为获准使用该环境的人员。

## 构建

在源码根目录使用独立的构建虚拟环境。下列命令不会提交 Git、打 tag 或向公共包源上传。

macOS：

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install --index-url https://pypi.org/simple --upgrade pip
./.venv/bin/python -m pip install --index-url https://pypi.org/simple build 'setuptools>=77'
./.venv/bin/python -m build --no-isolation
```

Windows / PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --upgrade pip
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple build 'setuptools>=77'
.\.venv\Scripts\python.exe -m build --no-isolation
```

构建依赖先通过显式指定的包源装入构建虚拟环境，再用 `build --no-isolation` 使用这些依赖，避免构建时重新访问本机默认镜像。此选项用于维护者打包；用户源码安装仍使用默认隔离构建和显式的 `--index-url`。

构建输出位于 `dist/`。版本由 `pyproject.toml` 的 `project.version` 决定；修改版本时同步更新 `CHANGELOG.md` 和文档内文件名。顶层与包内 `settings.example.json` 必须保持一致，内置版本用于独立 wheel 启动。

生成校验文件（源码根目录，两种系统使用对应虚拟环境 Python 执行此脚本）：

```python
from hashlib import sha256
from pathlib import Path

packages = sorted(Path("dist").glob("kea2_workbench-0.1.1*"))
lines = [f"{sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in packages
         if p.suffix in {".whl", ".gz"}]
Path("dist/SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
```

检查源码包包含 `docs/`、`configs/`、`settings.example.json`、`THIRD_PARTY_LICENSE.txt` 和测试；检查 wheel 包含 `kea_workbench/static/` 和 `kea_workbench/settings.example.json`。必须在源码目录外的空工作目录安装一次 wheel，初始化并启动，避免本机源码或设置文件掩盖缺失资源。

## 内部分发与安装

将源码包、wheel 和校验文件交付到组织内约定的工具分发位置。用户按 [Windows](windows.md) 或 [macOS](macos.md) 指南选择一种安装方式，安装后用 `python -m kea_workbench` 或 `kea2-web` 启动。当前仓库没有配置发布远端；本地构建产物不等于已经上传至制品库或创建公开 Release。

不向公开 PyPI/GitHub 上传包含 Kea2 SDK、官方配置或 SDK 派生内容的分发包，除非已取得相应许可。第三方原文见 [THIRD_PARTY_LICENSE.txt](../THIRD_PARTY_LICENSE.txt)，其中明确限定自身内部使用及允许的关联方/承包方范围。

## 离线安装

源码包和 wheel 均不包含依赖。离线部署须在联网环境为每一种目标系统、CPU 架构和 Python 版本准备依赖 wheel；Windows 与 macOS 的依赖包不可直接互用。

1. 在与目标机匹配的联网环境执行：

   ```sh
   python -m pip download --index-url https://pypi.org/simple --only-binary=:all: --dest wheelhouse ./kea2_workbench-0.1.1-py3-none-any.whl
   ```

2. 将工作台 wheel 和 `wheelhouse/` 一并传到离线机，以目标虚拟环境解释器执行：

   ```sh
   python -m pip install --no-index --find-links=wheelhouse ./kea2_workbench-0.1.1-py3-none-any.whl
   ```

3. 初始化数据目录并按系统指南启动。ADB 也需另行准备；离线平台上报仅在目标机可访问内部平台/MinIO 时可用。

若某依赖没有匹配目标的二进制 wheel，下载命令会失败，需要在目标兼容的构建环境先生成该依赖的 wheel。`requirements.lock` 是开发环境全量依赖版本快照，可用于复核，安装入口仍以 `pyproject.toml` 为准；不承诺该快照在每个 OS/Python 组合中均可安装。

## 升级与数据保留

先停止测试并等待最终报告与上报，再退出服务。备份工作目录中的 `configs/`、`data/` 和 `artifacts/`，在原虚拟环境安装新 wheel：

```sh
python -m pip install --index-url https://pypi.org/simple --upgrade ./kea2_workbench-NEW_VERSION-py3-none-any.whl
```

请将 `python` 换成实际虚拟环境解释器路径，将文件名换成新版本；继续指定原工作目录的 `--root`，才能看到历史记录。使用源码安装时，将新源码解压到独立目录再安装，避免覆盖原设置与运行材料。不要跨系统复制 `.venv`，需在目标机重新创建环境。当前没有历史数据迁移或自动恢复未完成测试的机制。

## 发布验收

安装测试依赖并运行隔离测试：

macOS：

```sh
./.venv/bin/python -m pip install --index-url https://pypi.org/simple '.[test]'
./.venv/bin/python -m pytest -q
```

Windows：

```powershell
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple '.[test]'
.\.venv\Scripts\python.exe -m pytest -q
```

自动测试覆盖参数、上报资格、回执、报告、服务与后台停止/同步，并模拟 Windows 子进程组和停止信号分支；不连接真实手机、平台或 MinIO。当前验证环境为 macOS，Windows 真机验收尚未在本次工作环境执行。

0.1.1 已在 macOS / Python 3.14 的全新虚拟环境中验证源码包完整安装（含隔离构建和全部运行依赖）、`pip check`、命令入口、HTTP 页面/API 及正常退出。验证时将默认包源设为清华镜像，安装命令显式指定官方 PyPI，确认构建子进程也能成功获取依赖。

维护者应在拟分发的 Windows 与 macOS 环境各执行以下验收并保留结果：

| 验收项 | 预期 |
|---|---|
| 干净环境安装并启动源码包/wheel | 无开发机路径依赖，页面和设置正常，wheel 工作目录 `init` 后可执行 |
| `adb devices -l` 与 App 查询 | 状态 `device`，指定包名返回安装路径 |
| 关闭上报执行短测试 | 有探索日志，正常结束，可查看本地完整报告和日志 |
| 运行期间点击停止 | Kea2 收尾、最终状态已取消，报告可查看，设备无残留控制进程 |
| 刷新/关闭浏览器 | 任务继续；重新打开可查看状态 |
| 开启上报并正式探索达标 | 使用对应项目/身份，收到有效平台回执，浏览器能访问报告资源 |
| 完成/取消与最终上报 | 同一平台任务得到最终状态，报告材料和质量结果一致 |
| 未达门槛、断网和服务中断 | 未达门槛不可补报；失败保留本地材料；已达标的终态可恢复补报 |

未完成某系统的真机与网络验收时，应在内部分发记录中注明，不能用模拟测试宣称所有设备或平台环境已验证。
