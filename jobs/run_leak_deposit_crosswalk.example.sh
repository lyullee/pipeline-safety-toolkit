#!/usr/bin/env bash
set -euo pipefail

# Run only after the reviewed transfer plan has staged both source archives.
# Usage: bash jobs/run_leak_deposit_crosswalk.example.sh RAW_ROOT OUTPUT_JSON
RAW_ROOT="${1:?provide the staged raw-data root}"
OUTPUT_JSON="${2:?provide a persistent result path}"
python3 scripts/compare_leak_dataset_deposits.py \
  --raw-root "$RAW_ROOT" \
  --output "$OUTPUT_JSON" \
  --timeout-s 3600
