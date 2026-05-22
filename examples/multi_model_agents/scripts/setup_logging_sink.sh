#!/usr/bin/env bash
# Setup BigQuery logging sink for wrangler agent traces and eval results.
#
# Routes agent trace data from Cloud Logging to BigQuery for:
# - Historical eval score analysis (SQL-queryable)
# - Trend tracking and regression detection
# - Dashboard building (Looker, Data Studio)
#
# Usage:
#   bash examples/multi_model_agents/scripts/setup_logging_sink.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "${EXAMPLE_DIR}/.env" ]; then
    set -a
    source "${EXAMPLE_DIR}/.env"
    set +a
fi

PROJECT_ID="${GCP_PROJECT_ID:-hybrid-vertex}"
DATASET="${DATASET_NAME:-gepa_wrangler_logs}"
SINK="${SINK_NAME:-gepa-agent-traces}"

BLUE='\033[0;34m'
GREEN='\033[0;32m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
info() { echo -e "${BLUE}  $1${NC}"; }

echo "=============================================="
echo " Wrangler — BigQuery Logging Sink Setup"
echo "=============================================="
echo "  Project:  $PROJECT_ID"
echo "  Dataset:  $DATASET"
echo "  Sink:     $SINK"
echo ""

# --- Step 1: Enable APIs ---
info "Enabling APIs..."
gcloud services enable \
    logging.googleapis.com \
    bigquery.googleapis.com \
    --project="$PROJECT_ID" --quiet
ok "APIs enabled"

# --- Step 2: Create BigQuery dataset ---
info "Creating BigQuery dataset '$DATASET'..."
bq mk --dataset \
    --project_id="$PROJECT_ID" \
    --description="Wrangler agent traces and eval results" \
    "$DATASET" \
    2>/dev/null && ok "Dataset created" || ok "Dataset already exists"

# --- Step 3: Create logging sink ---
info "Creating logging sink '$SINK'..."
gcloud logging sinks create "$SINK" \
    "bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${DATASET}" \
    --project="$PROJECT_ID" \
    --log-filter='resource.type="aiplatform.googleapis.com/ReasoningEngine"' \
    --description="Sink wrangler agent traces to BigQuery" \
    2>/dev/null && ok "Sink created" || ok "Sink already exists"

# --- Step 4: Grant sink writer access ---
info "Granting BigQuery access to sink writer..."
WRITER_IDENTITY=$(gcloud logging sinks describe "$SINK" \
    --project="$PROJECT_ID" \
    --format="value(writerIdentity)" 2>/dev/null)

if [ -n "$WRITER_IDENTITY" ]; then
    bq add-iam-policy-binding \
        --member="$WRITER_IDENTITY" \
        --role="roles/bigquery.dataEditor" \
        "$PROJECT_ID:$DATASET" \
        2>/dev/null || true
    ok "Writer identity granted: $WRITER_IDENTITY"
else
    echo "  Warning: Could not get writer identity"
fi

echo ""
echo "=============================================="
echo " Logging Sink Setup Complete"
echo "=============================================="
echo "  Agent traces → BigQuery: ${PROJECT_ID}.${DATASET}"
echo ""
echo "  Query eval results:"
echo "    SELECT * FROM \`${PROJECT_ID}.${DATASET}.online_eval_results\`"
echo "    WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)"
echo ""
echo "  Note: Results will appear after online evaluators run"
echo "  and traffic flows through the deployed agents."
