#!/bin/sh
# Blackline 2 – Einrichtung für macOS/Linux
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m blackline2.setup_ki "$@"
echo "Fertig. Start mit ./blackline2.sh"
