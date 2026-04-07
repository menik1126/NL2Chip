#!/usr/bin/env bash
# 后台运行 Sparkle Coding Agent，日志写入 agent/search.log
#
# Usage:
#   ./agent/run_nohup.sh -l 10
#   tail -f agent/search.log
#   kill $(cat agent/search.pid)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
nohup bash "$SCRIPT_DIR/run_agent.sh" "$@" > "$SCRIPT_DIR/search.log" 2>&1 &
echo $! > "$SCRIPT_DIR/search.pid"
echo "PID: $(cat "$SCRIPT_DIR/search.pid") — log: $SCRIPT_DIR/search.log"
