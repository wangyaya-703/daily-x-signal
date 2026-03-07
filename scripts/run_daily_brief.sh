#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

python3.11 -m pip install -e . >/dev/null
daily-x-signal generate "$@"
