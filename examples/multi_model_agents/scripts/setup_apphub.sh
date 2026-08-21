#!/usr/bin/env bash
# Register wrangler agents and MCP servers in App Hub for topology visualization.
#
# App Hub powers the Topology tab in the Agent Engine console by tracking
# relationships between agents and MCP tool servers. Resources are auto-discovered
# but must be explicitly registered to an App Hub application.
#
# This script:
#   1. Creates a wrangler-specific App Hub application
#   2. Discovers and registers all 5 wrangler agents as workloads
#   3. Discovers and registers all 3 wrangler MCP servers as services
#   4. Verifies registration
#
# Usage:
#   bash examples/multi_model_agents/scripts/setup_apphub.sh
#   bash examples/multi_model_agents/scripts/setup_apphub.sh --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "${EXAMPLE_DIR}/.env" ]; then
    set -a
    source "${EXAMPLE_DIR}/.env"
    set +a
fi

PROJECT_ID="${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set — see .env.example}"
PROJECT_NUMBER="${PROJECT_NUMBER:?PROJECT_NUMBER must be set — see .env.example}"
REGION="${GCP_REGION:-us-central1}"
APP_NAME="${APPHUB_APP_NAME:-gepa-wrangler}"

DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
    esac
done

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${BLUE}  $1${NC}"; }
ok()    { echo -e "${GREEN}  ✓ $1${NC}"; }
warn()  { echo -e "${YELLOW}  ⚠ $1${NC}"; }

run_cmd() {
    if $DRY_RUN; then
        echo -e "${YELLOW}  [dry-run] $*${NC}"
    else
        "$@"
    fi
}

echo "=============================================="
echo " Wrangler — App Hub Registration"
echo "=============================================="
echo "  Project:      $PROJECT_ID"
echo "  Region:       $REGION"
echo "  Application:  $APP_NAME"
echo "  Agents:       lite, flash, pro, sonnet, opus"
echo "  MCP Servers:  wrangler-search-mcp, wrangler-booking-mcp, wrangler-expense-mcp"
echo ""

# --- Step 1: Create App Hub application ---
echo "--- Step 1: Create App Hub application ---"

existing=$(gcloud apphub applications describe "$APP_NAME" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --format="value(name)" 2>/dev/null || true)

if [ -n "$existing" ]; then
    ok "Application '$APP_NAME' already exists"
else
    info "Creating application '$APP_NAME'..."
    run_cmd gcloud apphub applications create "$APP_NAME" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --scope-type=REGIONAL \
        --display-name="GEPA Prompt Wrangler"
    ok "Application created"
fi
echo ""

# --- Step 2: Discover and register agents ---
echo "--- Step 2: Register agent workloads ---"

find_discovered_workload() {
    local engine_id="$1"
    gcloud apphub discovered-workloads list \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --format="value(name)" \
        --filter="workloadReference.uri~'reasoningEngines/${engine_id}'" 2>/dev/null || true
}

register_workload() {
    local workload_name="$1"
    local display_name="$2"
    local discovered_workload="$3"

    if [ -z "$discovered_workload" ]; then
        warn "Skipping '$display_name' — not discovered yet"
        return
    fi

    existing_workload=$(gcloud apphub applications workloads describe "$workload_name" \
        --application="$APP_NAME" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --format="value(name)" 2>/dev/null || true)

    if [ -n "$existing_workload" ]; then
        ok "'$display_name' already registered"
    else
        info "Registering '$display_name'..."
        run_cmd gcloud apphub applications workloads create "$workload_name" \
            --application="$APP_NAME" \
            --project="$PROJECT_ID" \
            --location="$REGION" \
            --discovered-workload="$discovered_workload" \
            --display-name="$display_name"
        ok "'$display_name' registered"
    fi
}

for agent in lite flash pro sonnet opus; do
    VAR="${agent^^}_ENGINE_ID"
    ENGINE_ID="${!VAR:-}"
    if [ -z "$ENGINE_ID" ]; then
        warn "$agent: no ENGINE_ID set, skipping"
        continue
    fi
    DISCOVERED=$(find_discovered_workload "$ENGINE_ID")
    if [ -n "$DISCOVERED" ]; then
        ok "Found $agent: $DISCOVERED"
    else
        warn "$agent ($ENGINE_ID) not found in discovered workloads"
    fi
    register_workload "wrangler-${agent}-agent" "Wrangler ${agent^} Agent" "${DISCOVERED:-}"
done
echo ""

# --- Step 3: Register MCP servers ---
echo "--- Step 3: Register MCP services ---"

find_discovered_service() {
    local service_pattern="$1"
    gcloud apphub discovered-services list \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --format="value(name)" \
        --filter="serviceReference.uri~'run.googleapis.com.*${service_pattern}'" 2>/dev/null | head -1 || true
}

register_service() {
    local service_name="$1"
    local display_name="$2"
    local discovered_service="$3"

    if [ -z "$discovered_service" ]; then
        warn "Skipping '$display_name' — not discovered yet"
        return
    fi

    existing_service=$(gcloud apphub applications services describe "$service_name" \
        --application="$APP_NAME" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --format="value(name)" 2>/dev/null || true)

    if [ -n "$existing_service" ]; then
        ok "'$display_name' already registered"
    else
        info "Registering '$display_name'..."
        run_cmd gcloud apphub applications services create "$service_name" \
            --application="$APP_NAME" \
            --project="$PROJECT_ID" \
            --location="$REGION" \
            --discovered-service="$discovered_service" \
            --display-name="$display_name"
        ok "'$display_name' registered"
    fi
}

for server in search booking expense; do
    DISCOVERED=$(find_discovered_service "wrangler-${server}-mcp")
    [ -n "$DISCOVERED" ] && ok "Found wrangler-${server}-mcp: $DISCOVERED" || warn "wrangler-${server}-mcp not found"
    register_service "wrangler-${server}-mcp" "Wrangler ${server^} MCP" "${DISCOVERED:-}"
done
echo ""

# --- Step 4: Verify ---
echo "--- Step 4: Verify registration ---"

echo "Workloads:"
gcloud apphub applications workloads list \
    --application="$APP_NAME" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --format="table(name.basename(),displayName,workloadReference.uri)" 2>/dev/null || true

echo ""
echo "Services:"
gcloud apphub applications services list \
    --application="$APP_NAME" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --format="table(name.basename(),displayName,serviceReference.uri)" 2>/dev/null || true

echo ""
ok "Topology data should appear in the Agent Engine console within a few minutes"

echo ""
echo "=============================================="
echo " Done"
echo "=============================================="
