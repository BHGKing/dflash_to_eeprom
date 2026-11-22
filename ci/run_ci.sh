#!/bin/sh
# Regression check. Golden D-Flash/EEPROM pairs live in ci/input.
cd "$(dirname "$0")/.."
exec python3 -m pytest -q
