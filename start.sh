#!/bin/bash

export http_proxy=
export https_proxy=
export ALL_PROXY=

# 停止之前的 server 和 dispatch 进程
echo "Stopping previous server and dispatch processes..."
pkill -f "cairn serve" || true
pkill -f "cairn dispatch" || true

# 等待进程完全停止
sleep 1

# 启动 server（后台运行）
echo "Starting server on port 9999..."
uv run --project cairn cairn serve --port 9999 &

# 启动 dispatch（后台运行）
echo "Starting dispatch..."
uv run --project cairn cairn dispatch --config dispatch.local.pi.yaml &

# 等待后台进程
wait
