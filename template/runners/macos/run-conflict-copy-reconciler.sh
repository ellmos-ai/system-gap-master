#!/bin/sh
set -eu

: "${SYSTEM_GAP_RECONCILER_CONFIG:?set SYSTEM_GAP_RECONCILER_CONFIG}"

PYTHON_BIN="${SYSTEM_GAP_PYTHON:-python3}"
RUN_MODE="${SYSTEM_GAP_RECONCILER_MODE:-observer}"

case "$RUN_MODE" in
  observer)
    : "${SYSTEM_GAP_RECONCILER_PLAN_OUTPUT:?set SYSTEM_GAP_RECONCILER_PLAN_OUTPUT}"
    exec "$PYTHON_BIN" -m system_gap_master.conflict_copy_reconciler plan \
      --config "$SYSTEM_GAP_RECONCILER_CONFIG" \
      --output "$SYSTEM_GAP_RECONCILER_PLAN_OUTPUT"
    ;;
  mutating-owner)
    exec "$PYTHON_BIN" -m system_gap_master.conflict_copy_reconciler reconcile \
      --config "$SYSTEM_GAP_RECONCILER_CONFIG"
    ;;
  *)
    echo "SYSTEM_GAP_RECONCILER_MODE must be observer or mutating-owner" >&2
    exit 64
    ;;
esac
