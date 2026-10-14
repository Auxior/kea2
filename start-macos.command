#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/Library/Android/sdk/platform-tools:$PATH"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

for candidate in "$PWD/.venv/bin/python" python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
        "$candidate" -u launch_workbench.py
        launch_status=$?
        if [ "$launch_status" -ne 0 ]; then
            read -r -p "启动失败。按回车关闭窗口……" _
        fi
        exit "$launch_status"
    fi
done

printf '%s\n' '未找到 Python 3.11+。请从 https://www.python.org/downloads/macos/ 安装后重新双击。'
read -r -p "按回车关闭窗口……" _
exit 1
