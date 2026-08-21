#!/usr/bin/env bash
# =============================================================================
# Multi-Model Agents — One-Shot Deployment (thin wrapper)
# =============================================================================
# Orchestrates the example's canonical scripts instead of reimplementing them:
#   1. Enable required GCP APIs
#   2. Create the staging bucket
#   3. Bootstrap .env (from .env.example) with project settings
#   4. Deploy MCP servers              -> deploy_mcp_servers.sh
#   5. Register them in Agent Registry -> register_agent_registry.sh
#   6. Run the wrangler pipeline       -> wrangler run manifest.yaml
#
# Observability (logging sink / monitoring / App Hub) and GEAP-tour governance
# (Model Armor, Agent Gateway, org policies) are printed as next steps rather
# than run here — they require deployed-agent engine IDs and/or features that
# live in the GEAP tour, not this example.
#
# Usage:
#   bash scripts/deploy_all.sh
#   GCP_PROJECT_ID=my-project GCP_REGION=us-central1 bash scripts/deploy_all.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXAMPLE_DIR/../.." && pwd)"

PROJECT_ID="${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set — see .env.example}"
REGION="${GCP_REGION:-us-central1}"
PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || echo "unknown")
STAGING_BUCKET="${GCP_STAGING_BUCKET:-${PROJECT_ID}-geap-staging}"
ENV_FILE="${EXAMPLE_DIR}/.env"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step() { echo -e "\n${BLUE}━━━ [$1] $2 ━━━${NC}"; }
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; }

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║        Multi-Model Agents — One-Shot Deployment              ║"
echo "║  Project: ${PROJECT_ID} (${PROJECT_NUM})"
echo "║  Region:  ${REGION}"
echo "║  Bucket:  ${STAGING_BUCKET}"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ─── Step 1: Enable APIs ────────────────────────────────────────────
step "1/6" "Enabling required APIs"
gcloud services enable \
    run.googleapis.com \
    aiplatform.googleapis.com \
    logging.googleapis.com \
    bigquery.googleapis.com \
    cloudtrace.googleapis.com \
    monitoring.googleapis.com \
    --project="$PROJECT_ID" --quiet
ok "APIs enabled"

# ─── Step 2: Create staging bucket ──────────────────────────────────
step "2/6" "Creating staging bucket"
gcloud storage buckets create "gs://${STAGING_BUCKET}" \
    --project="$PROJECT_ID" --location="$REGION" \
    --uniform-bucket-level-access 2>/dev/null && ok "Bucket created" || ok "Bucket already exists"

# ─── Step 3: Bootstrap .env ─────────────────────────────────────────
step "3/6" "Bootstrapping .env"
if [ ! -f "$ENV_FILE" ]; then
    cp "${EXAMPLE_DIR}/.env.example" "$ENV_FILE"
    ok "Created .env from .env.example"
else
    ok ".env already exists (updating project settings)"
fi
set_env() {  # set_env KEY VALUE — replace existing line or append
    local key=$1 value=$2
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}
set_env GCP_PROJECT_ID "$PROJECT_ID"
set_env GCP_REGION "$REGION"
set_env GCP_STAGING_BUCKET "$STAGING_BUCKET"
set_env PROJECT_NUMBER "$PROJECT_NUM"
ok "Project settings written to $ENV_FILE"

# ─── Step 4: Deploy MCP servers (delegate) ──────────────────────────
step "4/6" "Deploying MCP servers to Cloud Run"
bash "${SCRIPT_DIR}/deploy_mcp_servers.sh"

# ─── Step 5: Register MCP servers in Agent Registry (delegate) ──────
step "5/6" "Registering MCP servers in Agent Registry"
bash "${SCRIPT_DIR}/register_agent_registry.sh"

# ─── Step 6: Run the wrangler pipeline ──────────────────────────────
step "6/6" "Running the wrangler pipeline (deploy → eval → optimize → redeploy → eval → report)"
warn "This is the full pipeline and includes GEPA optimization — it can run for hours."
warn "For a quick validation instead, Ctrl-C now and run:"
warn "  uv run wrangler run examples/multi_model_agents/manifest.yaml --dry-run"
# Export .env so wrangler (run from repo root) sees MCP vars regardless of
# where python-dotenv searches.
set -a && source "$ENV_FILE" && set +a
cd "$REPO_ROOT"
uv run wrangler run "examples/multi_model_agents/manifest.yaml"

# ─── Done + next steps ──────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                 Deployment Complete!                         ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Next steps (optional):"
echo "  • Logging sink:    bash ${SCRIPT_DIR}/setup_logging_sink.sh"
echo "  • Online monitors: bash ${SCRIPT_DIR}/setup_monitoring.sh   # needs *_ENGINE_ID set"
echo "  • App Hub:         bash ${SCRIPT_DIR}/setup_apphub.sh        # needs *_ENGINE_ID set"
echo "  • Governance (Model Armor / Agent Gateway / org policies): see the GEAP tour"
echo ""
echo "Re-run a single pair:"
echo "  uv run wrangler run examples/multi_model_agents/manifest.yaml --pair flash-gemini-3.5"
