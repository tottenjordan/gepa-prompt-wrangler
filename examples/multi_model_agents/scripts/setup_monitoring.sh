#!/usr/bin/env bash
# Setup online evaluators + logging sink for all deployed wrangler agents.
#
# Combines:
#   1. BigQuery logging sink (eval result persistence)
#   2. Online evaluators (automatic trace scoring every 10 min)
#   3. Verification that everything is active
#
# Prerequisites:
#   - Agents deployed (engine IDs in .env)
#   - PROJECT_NUMBER set in .env
#
# Usage:
#   bash examples/multi_model_agents/scripts/setup_monitoring.sh
#   bash examples/multi_model_agents/scripts/setup_monitoring.sh --skip-sink
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXAMPLE_DIR/../.." && pwd)"

if [ -f "${EXAMPLE_DIR}/.env" ]; then
    set -a
    source "${EXAMPLE_DIR}/.env"
    set +a
fi

SKIP_SINK=false
for arg in "$@"; do
    case "$arg" in
        --skip-sink) SKIP_SINK=true ;;
    esac
done

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
info() { echo -e "${BLUE}  $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $1${NC}"; }

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           Wrangler — Monitoring Setup                       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check prerequisites
echo "--- Prerequisites ---"
AGENTS_FOUND=0
for agent in LITE FLASH PRO SONNET OPUS; do
    VAR="${agent}_ENGINE_ID"
    VAL="${!VAR:-}"
    if [ -n "$VAL" ]; then
        ok "$agent: $VAL"
        AGENTS_FOUND=$((AGENTS_FOUND + 1))
    else
        warn "$agent: not deployed (${VAR} not set)"
    fi
done

if [ "$AGENTS_FOUND" -eq 0 ]; then
    echo ""
    echo "  No agents deployed. Run deploy_agents.py first."
    exit 1
fi

echo ""
echo "  Found $AGENTS_FOUND deployed agent(s)"
echo "  PROJECT_NUMBER: ${PROJECT_NUMBER:-NOT SET}"
echo ""

if [ -z "${PROJECT_NUMBER:-}" ]; then
    echo "  Error: PROJECT_NUMBER not set in .env"
    exit 1
fi

# --- Step 1: BigQuery Logging Sink ---
if ! $SKIP_SINK; then
    echo "--- Step 1: BigQuery Logging Sink ---"
    bash "${SCRIPT_DIR}/setup_logging_sink.sh"
    echo ""
else
    echo "--- Step 1: Skipping logging sink (--skip-sink) ---"
    echo ""
fi

# --- Step 2: Online Evaluators ---
echo "--- Step 2: Create Online Evaluators ---"
cd "$REPO_ROOT"
uv run python -m wrangler.online_evaluators create
echo ""

# --- Step 3: Verify ---
echo "--- Step 3: Verify Setup ---"
uv run python -m wrangler.online_evaluators verify
echo ""

# --- Step 4: Run Quick Monitor ---
echo "--- Step 4: Quick Health Check (first agent) ---"
FIRST_ENGINE=""
for agent in LITE FLASH PRO SONNET OPUS; do
    VAR="${agent}_ENGINE_ID"
    VAL="${!VAR:-}"
    if [ -n "$VAL" ]; then
        FIRST_ENGINE="$VAL"
        info "Running monitor against ${agent} ($VAL)..."
        uv run python -m wrangler.online_monitors "$VAL" --cases 3
        break
    fi
done

echo ""
echo -e "${GREEN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           Monitoring Setup Complete                         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo "  Online evaluators: scoring traces every 10 min"
echo "  Logging sink:      ${DATASET_NAME:-gepa_wrangler_logs} (BigQuery)"
echo "  Monitor results:   outputs/monitors/"
echo ""
echo "  Next steps:"
echo "    - Generate traffic to populate eval data"
echo "    - Check Observability tab in Agent Engine console"
echo "    - Query results: SELECT * FROM \`${GCP_PROJECT_ID}.${DATASET_NAME:-gepa_wrangler_logs}.online_eval_results\`"
