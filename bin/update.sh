#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"

if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
	. "$ROOT_DIR/.venv/bin/activate"
fi

python manage.py update --debug
python manage.py collectstatic --noinput --clear

