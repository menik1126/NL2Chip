#!/usr/bin/env bash
# 启动 Sparkle Dashboard 后端
#
# Usage:
#   ./dashboard/start.sh              # 默认 8000 端口
#   ./dashboard/start.sh --port 8080  # 自定义端口

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 激活虚拟环境
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# 检查依赖
if ! python3 -c "import fastapi" &>/dev/null; then
    echo "安装 fastapi + uvicorn..."
    uv add "fastapi[standard]>=0.115"
fi

PORT=8000
prev=""
for arg in "$@"; do
    if [[ "$prev" == "--port" ]]; then PORT="$arg"; fi
    prev="$arg"
done

echo "╭──────────────────────────────────╮"
echo "│  Sparkle Dashboard               │"
echo "│  http://localhost:$PORT            │"
echo "│  API docs: /docs                 │"
echo "╰──────────────────────────────────╯"
echo ""

exec python3 -m uvicorn dashboard.api:app --host 0.0.0.0 --port "$PORT" --reload
