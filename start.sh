#!/usr/bin/env bash
# start.sh — Launch the File Transfer Service (uvicorn + cloudflared tunnel).
# Run setup_machine.sh first to install prerequisites and create the conda env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_NAME="file-transfer"
PORT=8000
SERVER_LOG="$SCRIPT_DIR/server.log"
CF_LOG="$SCRIPT_DIR/cloudflare.log"
TUNNEL_URL_FILE="$SCRIPT_DIR/tunnel_url.txt"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Colour

# ── Pre-flight checks ───────────────────────────────────────────────────────
echo "🔍 Pre-flight checks..."

if ! command -v conda &>/dev/null; then
    echo -e "${RED}❌ conda is not installed. Install Miniconda first:${NC}"
    echo "   https://docs.conda.io/en/latest/miniconda.html"
    exit 1
fi

if ! command -v cloudflared &>/dev/null; then
    echo -e "${RED}❌ cloudflared is not installed. Install it:${NC}"
    echo "   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    echo "   chmod +x cloudflared-linux-amd64"
    echo "   sudo mv cloudflared-linux-amd64 /usr/local/bin/cloudflared"
    exit 1
fi

if ! command -v croc &>/dev/null; then
    echo -e "${YELLOW}⚠️  croc is not installed. File transfers will not work.${NC}"
    echo "   Install: curl https://getcroc.schollz.com | bash"
fi

# ── Kill anything already on port $PORT ─────────────────────────────────────
if lsof -i :$PORT -t &>/dev/null; then
    echo -e "${YELLOW}⚠️  Port $PORT is in use. Killing existing process...${NC}"
    kill $(lsof -i :$PORT -t) 2>/dev/null || true
    sleep 1
fi

# ── Kill any existing cloudflared tunnel processes ──────────────────────────
if pgrep -f "cloudflared tunnel" &>/dev/null; then
    echo -e "${YELLOW}⚠️  Existing cloudflared tunnel found. Killing...${NC}"
    pkill -f "cloudflared tunnel" 2>/dev/null || true
    sleep 1
fi

# ── Activate conda environment ──────────────────────────────────────────────
# Temporarily disable nounset (-u) because conda's Qt activation scripts
# reference unset variables (QT_XCB_GL_INTEGRATION etc.).
set +u
eval "$(conda shell.bash hook)"
if ! conda env list | grep -qw "$ENV_NAME"; then
    echo -e "${RED}❌ Conda environment '$ENV_NAME' not found. Run setup_machine.sh first.${NC}"
    set -u
    exit 1
fi
conda activate "$ENV_NAME"
set -u
echo "📦 Activated conda environment: $ENV_NAME"

# ── Trap to clean up on exit ─────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    [[ -n "${UVICORN_PID:-}" ]] && kill "$UVICORN_PID" 2>/dev/null && echo "   Stopped uvicorn ($UVICORN_PID)"
    [[ -n "${CF_PID:-}" ]] && kill "$CF_PID" 2>/dev/null && echo "   Stopped cloudflared ($CF_PID)"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Start uvicorn (background, so we can also run cloudflared) ──────────────
echo "🚀 Starting uvicorn on 127.0.0.1:$PORT..."
uvicorn server:app --host 127.0.0.1 --port $PORT 2>&1 | tee -a "$SERVER_LOG" &
UVICORN_PID=$!

# Give uvicorn a moment to bind the port
sleep 2
if ! kill -0 $UVICORN_PID 2>/dev/null; then
    echo -e "${RED}❌ uvicorn failed to start. Check output above.${NC}"
    exit 1
fi

# ── Start Cloudflare tunnel (background) ────────────────────────────────────
echo "🌐 Starting Cloudflare tunnel..."
: > "$CF_LOG"
cloudflared tunnel --url http://localhost:$PORT 2>&1 | tee -a "$CF_LOG" &
CF_PID=$!

# ── Wait for the tunnel URL ────────────────────────────────────────────────
echo "⏳ Waiting for Cloudflare tunnel URL (up to 30s)..."
TUNNEL_URL=""
for i in $(seq 1 60); do
    TUNNEL_URL=$(grep -oP 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1 || true)
    if [[ -n "$TUNNEL_URL" ]]; then
        break
    fi
    sleep 0.5
done

if [[ -z "$TUNNEL_URL" ]]; then
    echo -e "${RED}❌ Timed out waiting for Cloudflare tunnel URL.${NC}"
    echo "   Check output above for errors."
    exit 1
fi

# ── Print summary ───────────────────────────────────────────────────────────
echo "$TUNNEL_URL" > "$TUNNEL_URL_FILE"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}✅ Server running.${NC}"
echo -e "  ${GREEN}🌐 Cloudflare URL: ${TUNNEL_URL}${NC}"
echo -e "  ${YELLOW}👉 Copy this URL into machines.json on all machines.${NC}"
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "  URL also saved to: $TUNNEL_URL_FILE"
echo "  Press Ctrl+C to stop both services."
echo ""

# ── Stay in foreground — wait for both processes ────────────────────────────
wait
echo ""
