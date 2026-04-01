#!/usr/bin/env bash
set -euo pipefail

PORT=18083
BASE_URL="http://127.0.0.1:${PORT}"

TMP_DIR="$(mktemp -d)"
SERVER_LOG="${TMP_DIR}/server.log"
MOVE_LOG="${TMP_DIR}/move.log"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "${SERVER_PID}" 2>/dev/null; then
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

python - <<'PY' >"${SERVER_LOG}" 2>&1 &
import asyncio

import uvicorn

from api.routes import create_app, set_app_state
from local_ui.arbitration import ArbitrationController


class _FakeResponseStatus:
    OK = "ok"


class _FakeResponse:
    def __init__(self):
        self.status = _FakeResponseStatus.OK
        self.message = "ok"


class _FakeSerialManager:
    async def send_command_async(self, command):
        # Hold movement command briefly so REMOTE state is observable.
        if command.get("kind") == "motor_velocity":
            await asyncio.sleep(0.2)
        return _FakeResponse()


class _FakeImageProcessor:
    def render_remote_active_notice(self, text: str, font_size: int = 32):
        return b"\xff" * 15000


class _FakeCommandBuilder:
    @staticmethod
    def motor_velocity(left_speed: int, right_speed: int, duration_ms: int):
        return {
            "kind": "motor_velocity",
            "left_speed": left_speed,
            "right_speed": right_speed,
            "duration_ms": duration_ms,
        }

    @staticmethod
    def display_image(payload: bytes):
        return {"kind": "display_image", "payload": payload}


# Patch esp_serial imports used by routes/arbitration.
import sys
import types

fake_pkg = types.ModuleType("esp_serial")
fake_commands = types.ModuleType("esp_serial.commands")
fake_protocol = types.ModuleType("esp_serial.protocol")
fake_commands.CommandBuilder = _FakeCommandBuilder
fake_protocol.ResponseStatus = _FakeResponseStatus

sys.modules["esp_serial"] = fake_pkg
sys.modules["esp_serial.commands"] = fake_commands
sys.modules["esp_serial.protocol"] = fake_protocol

serial = _FakeSerialManager()
image_processor = _FakeImageProcessor()
arbitration = ArbitrationController(
    cooldown_seconds=0.8,
    serial_manager=serial,
    image_processor=image_processor,
)

set_app_state(serial_manager=serial, image_processor=image_processor, arbitration=arbitration)
app = create_app()
uvicorn.run(app, host="127.0.0.1", port=18083, log_level="warning")
PY
SERVER_PID=$!

for _ in $(seq 1 60); do
  if curl -sS "${BASE_URL}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

if ! curl -sS "${BASE_URL}/health" >/dev/null 2>&1; then
  echo "❌ server failed to start; log at ${SERVER_LOG}"
  exit 1
fi

get_state() {
  curl -sS "${BASE_URL}/api/arbitration/status" | python -c 'import json,sys; print(json.load(sys.stdin)["state"])'
}

wait_for_state() {
  local expected="$1"
  local timeout_s="$2"
  local start
  start="$(python - <<'PY'
import time
print(time.monotonic())
PY
)"

  while true; do
    local current
    current="$(get_state)"
    if [[ "${current}" == "${expected}" ]]; then
      python - <<PY
import time
print(f"{time.monotonic() - float(${start}):.3f}")
PY
      return 0
    fi

    local elapsed
    elapsed="$(python - <<PY
import time
print(time.monotonic() - float(${start}))
PY
)"
    if python - <<PY
import sys
sys.exit(0 if float(${elapsed}) > float(${timeout_s}) else 1)
PY
    then
      return 1
    fi

    sleep 0.05
  done
}

initial_state="$(get_state)"
if [[ "${initial_state}" != "local" ]]; then
  echo "❌ expected initial state local, got ${initial_state}"
  exit 1
fi

curl -sS -X POST "${BASE_URL}/api/movement/move" \
  -H "Content-Type: application/json" \
  -d '{"left_speed":120,"right_speed":120,"duration_ms":80}' \
  >"${MOVE_LOG}" 2>&1 &
MOVE_PID=$!

remote_t="$(wait_for_state remote 2.0 || true)"
if [[ -z "${remote_t}" ]]; then
  echo "❌ did not observe remote state"
  exit 1
fi

wait "${MOVE_PID}"

cooldown_t="$(wait_for_state cooldown 2.0 || true)"
if [[ -z "${cooldown_t}" ]]; then
  echo "❌ did not observe cooldown state"
  exit 1
fi

local_t="$(wait_for_state local 3.0 || true)"
if [[ -z "${local_t}" ]]; then
  echo "❌ did not return to local state"
  exit 1
fi

echo "✅ state: local → remote (${remote_t}s) → cooldown (${cooldown_t}s) → local (${local_t}s)"
