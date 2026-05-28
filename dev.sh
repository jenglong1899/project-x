#!/usr/bin/env bash
set -euo pipefail

# 一键启动前端（Vite）和后端（uv + uvicorn）
#
# 用法：
#   ./dev.sh
#
# 约定：
# - 前端目录：frontend/（npm run dev）
# - 后端目录：backend/（PYTHONPATH=. uv run python main.py）

ROOT_DIR="$(
  cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1
  pwd
)"

backend_pid=""
frontend_pid=""

cleanup() {
  set +e

  if [[ -n "${frontend_pid}" ]] && kill -0 "${frontend_pid}" >/dev/null 2>&1; then
    kill "${frontend_pid}" >/dev/null 2>&1
  fi
  if [[ -n "${backend_pid}" ]] && kill -0 "${backend_pid}" >/dev/null 2>&1; then
    kill "${backend_pid}" >/dev/null 2>&1
  fi

  if [[ -n "${frontend_pid}" ]]; then
    wait "${frontend_pid}" >/dev/null 2>&1
  fi
  if [[ -n "${backend_pid}" ]]; then
    wait "${backend_pid}" >/dev/null 2>&1
  fi
}

trap cleanup EXIT INT TERM

if ! command -v npm >/dev/null 2>&1; then
  echo "未找到 npm。请先安装 Node.js/npm。" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv。请先安装 uv（Python 包管理器）。" >&2
  exit 1
fi

echo "启动后端：backend/（PYTHONPATH=. uv run python main.py）"
(
  cd "${ROOT_DIR}/backend"
  exec env PYTHONPATH=. uv run python main.py
) &
backend_pid="$!"

wait_for_backend() {
  local backend_port="${PROJECT_X_PORT:-8000}"
  local url="http://127.0.0.1:${backend_port}/healthz"

  echo "等待后端就绪（/healthz）：${url}" >&2

  if command -v curl >/dev/null 2>&1; then
    for _ in $(seq 1 120); do
      if curl -fsS "${url}" >/dev/null 2>&1; then
        return 0
      fi
      sleep 0.2
    done
    echo "后端启动超时：${url}" >&2
    return 1
  fi

  if command -v wget >/dev/null 2>&1; then
    for _ in $(seq 1 120); do
      if wget -qO- "${url}" >/dev/null 2>&1; then
        return 0
      fi
      sleep 0.2
    done
    echo "后端启动超时：${url}" >&2
    return 1
  fi

  echo "未找到 curl/wget，跳过后端就绪等待（e2e 可能不稳定）" >&2
  return 0
}

(
  if [[ -z "${PROJECT_X_SKIP_BACKEND_WAIT:-}" ]]; then
    wait_for_backend
  fi
  echo "启动前端：frontend/（npm run dev）"
  cd "${ROOT_DIR}/frontend"
  # 在 Codespaces 里如果 Vite 只监听 localhost，端口转发/浏览器访问经常会出现 WebSocket 不稳定（例如 EPIPE）。
  # 监听 0.0.0.0 能显著降低这类问题，同时不影响本地开发体验（本地也能访问 localhost）。
  extra_args=()
  if [[ "${CODESPACES:-}" == "true" ]] || [[ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
    extra_args+=(--host 0.0.0.0)
  fi

  if [[ -n "${PROJECT_X_E2E_PORT:-}" ]]; then
    exec npm run dev -- "${extra_args[@]}" --port "${PROJECT_X_E2E_PORT}" --strictPort
  fi
  exec npm run dev -- "${extra_args[@]}"
) &
frontend_pid="$!"

wait_any_supported="false"
if help wait 2>/dev/null | grep -q -- "-n"; then
  wait_any_supported="true"
fi

set +e
if [[ "${wait_any_supported}" == "true" ]]; then
  wait -n "${backend_pid}" "${frontend_pid}"
else
  # 兼容旧 bash：轮询任一子进程结束。
  while true; do
    if ! kill -0 "${backend_pid}" >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "${frontend_pid}" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done
fi

exit_code="$?"
set -e

echo "检测到某个进程已退出（exit=${exit_code}），正在停止另一个进程..."
exit "${exit_code}"
