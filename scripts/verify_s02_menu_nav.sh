#!/usr/bin/env bash
set -euo pipefail

echo "[S02] Running integration pytest"
pytest tests/local_ui/test_menu_integration.py -v

echo "[S02] Running menu status API + gesture flow verification"
python - <<'PY'
import asyncio
import json
import threading
import time
import urllib.request

import uvicorn

from api.routes import create_app, set_app_state
from cv_engine.gesture_detector import Gesture
from local_ui.bootstrap import BASELINE_HOME_MENU_ENTRIES
from local_ui.menu_state import MenuStateMachine

PORT = 18080
URL = f"http://127.0.0.1:{PORT}/api/local-ui/menu/status"

menu = MenuStateMachine(
    menu_entries=BASELINE_HOME_MENU_ENTRIES,
    victory_hold_seconds=0.08,
    debounce_frames=1,
)
set_app_state(menu_state=menu)

app = create_app()
config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
server = uvicorn.Server(config)

thread = threading.Thread(target=server.run, daemon=True)
thread.start()


def get_status():
    with urllib.request.urlopen(URL, timeout=2) as resp:
        return json.loads(resp.read().decode("utf-8"))

# Wait for server readiness
ready = False
for _ in range(40):
    try:
        payload = get_status()
        if payload["state"] == "idle":
            ready = True
            break
    except Exception:
        time.sleep(0.1)

if not ready:
    raise SystemExit("menu status API failed to become ready")

# Navigate next (Thumb Up)
menu.handle_gesture(Gesture.THUMBS_UP, 0.95)
status = get_status()
if status["selected_index"] != 1:
    raise SystemExit(f"expected selected_index=1 after navigate, got {status['selected_index']}")

# Hold Victory until commit requested
menu.handle_gesture(Gesture.PEACE, 0.95)
deadline = time.monotonic() + 1.0
while time.monotonic() < deadline and not menu.consume_commit_requested():
    menu.handle_gesture(Gesture.PEACE, 0.95)
    time.sleep(0.01)

if menu.state.value != "locked":
    raise SystemExit(f"expected state=locked after hold commit, got {menu.state.value}")

asyncio.run(menu.commit_selection())

final_status = get_status()
if final_status["state"] != "navigating":
    raise SystemExit(f"expected state=navigating after commit handler, got {final_status['state']}")
if final_status["locked"]:
    raise SystemExit("expected locked=false after commit handler")

server.should_exit = True
thread.join(timeout=3)
print("menu flow verified")
PY

echo "[S02] Verification complete"
