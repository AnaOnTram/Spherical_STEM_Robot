#!/usr/bin/env bash
set -euo pipefail

echo "[S04] Verifying STEM launch + return lifecycle"
pytest tests/local_ui/test_stem_launch_dispatch.py -q
pytest tests/local_ui/test_stem_return_integration.py -q
echo "[S04] PASS: STEM launch and return integration checks passed"