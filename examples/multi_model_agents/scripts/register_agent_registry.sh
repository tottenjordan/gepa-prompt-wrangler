#!/usr/bin/env bash
# Register wrangler MCP servers in Agent Registry for tool discovery.
#
# Agent Registry lets deployed agents discover MCP tools by resource name
# instead of hardcoded URLs. This script registers each wrangler MCP server
# with its tool specs, then updates .env with the resource names.
#
# Prerequisites:
#   - MCP servers deployed to Cloud Run (run deploy_mcp_servers.sh first)
#   - MCP URLs in .env
#
# Usage:
#   bash examples/multi_model_agents/scripts/register_agent_registry.sh
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

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
info() { echo -e "${BLUE}  $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $1${NC}"; }

echo "=============================================="
echo " Wrangler — Agent Registry MCP Registration"
echo "=============================================="
echo "  Project:  $PROJECT_ID"
echo "  Region:   $REGION"
echo ""

# --- Tool Specs ---
SEARCH_TOOLSPEC='{"tools":[{"name":"search_flights","description":"Search for available flights by origin, destination, and date","inputSchema":{"type":"object","properties":{"origin":{"type":"string","description":"Origin airport code"},"destination":{"type":"string","description":"Destination airport code"},"date":{"type":"string","description":"Travel date (YYYY-MM-DD)"}},"required":["origin","destination"]},"annotations":{"readOnlyHint":true}},{"name":"search_hotels","description":"Search for available hotels by city and optional max price","inputSchema":{"type":"object","properties":{"city":{"type":"string","description":"City name"},"max_price":{"type":"number","description":"Maximum price per night"}},"required":["city"]},"annotations":{"readOnlyHint":true}}]}'

BOOKING_TOOLSPEC='{"tools":[{"name":"book_flight","description":"Book a flight for a passenger","inputSchema":{"type":"object","properties":{"flight_id":{"type":"string"},"passenger_name":{"type":"string"}},"required":["flight_id","passenger_name"]}},{"name":"book_hotel","description":"Book a hotel for a guest","inputSchema":{"type":"object","properties":{"hotel_id":{"type":"string"},"guest_name":{"type":"string"},"checkin":{"type":"string"},"checkout":{"type":"string"}},"required":["hotel_id","guest_name"]}},{"name":"cancel_booking","description":"Cancel an existing booking","inputSchema":{"type":"object","properties":{"booking_id":{"type":"string"}},"required":["booking_id"]}},{"name":"get_booking_details","description":"Get details of a booking by ID","inputSchema":{"type":"object","properties":{"booking_id":{"type":"string"}},"required":["booking_id"]},"annotations":{"readOnlyHint":true}},{"name":"list_all_bookings","description":"List all bookings","inputSchema":{"type":"object","properties":{}},"annotations":{"readOnlyHint":true}}]}'

EXPENSE_TOOLSPEC='{"tools":[{"name":"check_expense_policy","description":"Check if an expense amount is within corporate policy for a category","inputSchema":{"type":"object","properties":{"category":{"type":"string"},"amount":{"type":"number"}},"required":["category","amount"]},"annotations":{"readOnlyHint":true}},{"name":"submit_expense","description":"Submit an expense report","inputSchema":{"type":"object","properties":{"user_id":{"type":"string"},"category":{"type":"string"},"amount":{"type":"number"},"description":{"type":"string"}},"required":["user_id","category","amount","description"]}},{"name":"get_user_expenses","description":"Get all expenses for a user","inputSchema":{"type":"object","properties":{"user_id":{"type":"string"}},"required":["user_id"]},"annotations":{"readOnlyHint":true}}]}'

# --- Register each MCP server ---
register_mcp() {
    local name=$1 url=$2 toolspec=$3

    info "Registering $name..."
    echo "$toolspec" > "/tmp/${name}-toolspec.json"

    if gcloud alpha agent-registry services describe "$name" \
        --project="$PROJECT_ID" --location="$REGION" &>/dev/null; then
        ok "$name already registered"
        return
    fi

    gcloud alpha agent-registry services create "$name" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --display-name="$name" \
        --mcp-server-spec-type=tool-spec \
        --mcp-server-spec-content="/tmp/${name}-toolspec.json" \
        --interfaces=url="${url}",protocolBinding=JSONRPC \
        2>&1 | grep -E "(Created|error)" || true
    ok "$name registered"
}

# Get Cloud Run URLs (strip /mcp suffix for display)
SEARCH_URL="${SEARCH_MCP_URL:-}"
BOOKING_URL="${BOOKING_MCP_URL:-}"
EXPENSE_URL="${EXPENSE_MCP_URL:-}"

if [ -z "$SEARCH_URL" ] || [ -z "$BOOKING_URL" ] || [ -z "$EXPENSE_URL" ]; then
    echo "  Error: MCP URLs not set in .env. Deploy MCP servers first."
    exit 1
fi

echo "--- Step 1: Register MCP Servers ---"
register_mcp "wrangler-search-mcp" "$SEARCH_URL" "$SEARCH_TOOLSPEC"
register_mcp "wrangler-booking-mcp" "$BOOKING_URL" "$BOOKING_TOOLSPEC"
register_mcp "wrangler-expense-mcp" "$EXPENSE_URL" "$EXPENSE_TOOLSPEC"
echo ""

# --- Look up resource names ---
echo "--- Step 2: Look up resource names ---"
TOKEN=$(gcloud auth print-access-token 2>/dev/null || gcloud auth application-default print-access-token 2>/dev/null)
API="https://agentregistry.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${REGION}/services"

lookup_server() {
    local display_name=$1
    curl -s -H "Authorization: Bearer $TOKEN" "$API" \
        | python3 -c "import json,sys; [print(s['name']) for s in json.load(sys.stdin).get('services',[]) if s.get('displayName')=='${display_name}']" 2>/dev/null | head -1
}

SEARCH_SERVER=$(lookup_server "wrangler-search-mcp")
BOOKING_SERVER=$(lookup_server "wrangler-booking-mcp")
EXPENSE_SERVER=$(lookup_server "wrangler-expense-mcp")

ok "search:  ${SEARCH_SERVER:-NOT FOUND}"
ok "booking: ${BOOKING_SERVER:-NOT FOUND}"
ok "expense: ${EXPENSE_SERVER:-NOT FOUND}"
echo ""

# --- Update .env ---
echo "--- Step 3: Update .env ---"
ENV_FILE="${EXAMPLE_DIR}/.env"

update_env() {
    local key=$1 value=$2
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

if [ -n "$SEARCH_SERVER" ]; then
    update_env "SEARCH_MCP_SERVER" "$SEARCH_SERVER"
    ok ".env: SEARCH_MCP_SERVER=$SEARCH_SERVER"
fi
if [ -n "$BOOKING_SERVER" ]; then
    update_env "BOOKING_MCP_SERVER" "$BOOKING_SERVER"
    ok ".env: BOOKING_MCP_SERVER=$BOOKING_SERVER"
fi
if [ -n "$EXPENSE_SERVER" ]; then
    update_env "EXPENSE_MCP_SERVER" "$EXPENSE_SERVER"
    ok ".env: EXPENSE_MCP_SERVER=$EXPENSE_SERVER"
fi

echo ""
echo "=============================================="
echo " Agent Registry Registration Complete"
echo "=============================================="
echo "  Agents can now discover MCP tools by resource name."
echo "  View: https://console.cloud.google.com/agent-platform/agent-registry?project=$PROJECT_ID"
