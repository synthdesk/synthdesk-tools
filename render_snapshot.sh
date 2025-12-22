#!/bin/sh
set -e

EVENT_SPINE="$1"
ROUTER_INTENTS="$2"

OUT_DIR="$(dirname "$0")"
OUT_FILE="$OUT_DIR/index.html"
TMP_FILE="$OUT_DIR/index.html.tmp"

python3 synthdesk-tools/snapshot/synthdesk_snapshot.py \
  "$EVENT_SPINE" \
  "$ROUTER_INTENTS" \
  html \
  > "$TMP_FILE"

mv "$TMP_FILE" "$OUT_FILE"
