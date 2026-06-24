#!/usr/bin/env bash
# Deploy MCP servers to Cloud Run with "wrangler-" prefix
# Creates separate resources from the GEAP tour deployment
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "${EXAMPLE_DIR}/.env" ]; then
    set -a
    source "${EXAMPLE_DIR}/.env"
    set +a
fi

PROJECT_ID="${GCP_PROJECT_ID:-hybrid-vertex}"
REGION="${GCP_REGION:-us-central1}"
STAGING_BUCKET="${GCP_STAGING_BUCKET:-jts-wrangler-staging}"

BLUE='\033[0;34m'
GREEN='\033[0;32m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
info() { echo -e "${BLUE}  $1${NC}"; }

echo "=============================================="
echo " Wrangler — Deploy MCP Servers"
echo "=============================================="
echo "  Project:  $PROJECT_ID"
echo "  Region:   $REGION"
echo "  Bucket:   $STAGING_BUCKET"
echo ""

# --- Step 1: Create staging bucket ---
info "Creating staging bucket..."
gsutil mb -p "$PROJECT_ID" -l "$REGION" "gs://${STAGING_BUCKET}" 2>/dev/null && ok "Bucket created" || ok "Bucket already exists"

# --- Step 2: Deploy MCP servers ---
deploy_mcp() {
    local service_name=$1 source_dir=$2 port=$3
    info "Deploying $service_name from $source_dir (port $port)..."
    gcloud run deploy "$service_name" \
        --source "${EXAMPLE_DIR}/mcp_servers/${source_dir}" \
        --region "$REGION" \
        --project "$PROJECT_ID" \
        --port "$port" \
        --min-instances 3 \
        --concurrency 250 \
        --session-affinity \
        --allow-unauthenticated \
        --quiet 2>&1 | tail -2
}

deploy_mcp "wrangler-search-mcp" "search" 8001 &
PID1=$!
deploy_mcp "wrangler-booking-mcp" "booking" 8002 &
PID2=$!
deploy_mcp "wrangler-expense-mcp" "expense" 8003 &
PID3=$!

wait $PID1 && ok "wrangler-search-mcp deployed" || echo "  search-mcp failed"
wait $PID2 && ok "wrangler-booking-mcp deployed" || echo "  booking-mcp failed"
wait $PID3 && ok "wrangler-expense-mcp deployed" || echo "  expense-mcp failed"

# --- Step 3: Get URLs and update .env ---
echo ""
info "Fetching service URLs..."

SEARCH_URL=$(gcloud run services describe wrangler-search-mcp --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)" 2>/dev/null)
BOOKING_URL=$(gcloud run services describe wrangler-booking-mcp --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)" 2>/dev/null)
EXPENSE_URL=$(gcloud run services describe wrangler-expense-mcp --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)" 2>/dev/null)

echo "  Search:  ${SEARCH_URL}/mcp"
echo "  Booking: ${BOOKING_URL}/mcp"
echo "  Expense: ${EXPENSE_URL}/mcp"

# Update .env with actual URLs
ENV_FILE="${EXAMPLE_DIR}/.env"
if [ -f "$ENV_FILE" ]; then
    sed -i "s|SEARCH_MCP_URL=.*|SEARCH_MCP_URL=${SEARCH_URL}/mcp|" "$ENV_FILE"
    sed -i "s|BOOKING_MCP_URL=.*|BOOKING_MCP_URL=${BOOKING_URL}/mcp|" "$ENV_FILE"
    sed -i "s|EXPENSE_MCP_URL=.*|EXPENSE_MCP_URL=${EXPENSE_URL}/mcp|" "$ENV_FILE"
    ok ".env updated with service URLs"
fi

echo ""
echo "=============================================="
echo " MCP Servers Deployed"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  1. Register servers in Agent Registry (optional)"
echo "  2. Deploy agents: wrangler deploy examples/multi_model_agents/manifest.yaml"
echo "  3. Run experiment: wrangler run examples/multi_model_agents/manifest.yaml"
