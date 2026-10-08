#!/usr/bin/env bash
# Bash entry point; the same stdlib-only bootstrap also runs on native Windows.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# 检查 Python 命令。预期：/usr/bin/python3，或用户指定的 Python 可执行文件。
# PYTHON 可包含空格，例如 PYTHON='/c/Program Files/Python311/python.exe'。
if [[ -n "${PYTHON:-}" ]]; then
    python_cmd="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    python_cmd=python3
elif command -v python >/dev/null 2>&1; then
    python_cmd=python
else
    printf '%s\n' '错误：找不到 Python。请安装 Python 3.11+ 并将其加入 PATH。' >&2
    exit 1
fi

# 共享检查的预期输出（实现见 src/bootstrap.py，每一步均含输出注释）：
# Python：Python 3.11.9；pip：pip 24.0 from ...；内存：31.43 GiB，建议 7B。
# Ollama：ollama version is ...；服务：Ollama is running；模型：success。
# embedding：embedding 离线加载验证通过；总结：各组件 OK / 模型大小 / 磁盘估算。
export PYTHONUTF8=1
if ! "$python_cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    printf '%s\n' '错误：需要 Python 3.11+。请升级 Python，或用 PYTHON 指定正确路径。' >&2
    exit 1
fi
exec "$python_cmd" -m src.bootstrap "$@"
