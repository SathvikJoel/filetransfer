#!/usr/bin/env bash
# setup_machine.sh — Install all prerequisites on a fresh Ubuntu machine.
# Run this ONCE before using start.sh.
#
# Usage:  chmod +x setup_machine.sh && ./setup_machine.sh
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "══════════════════════════════════════════════════════════"
echo "  File Transfer Service — Machine Setup"
echo "══════════════════════════════════════════════════════════"
echo ""

# ── 1. Install Miniconda (if conda not present) ────────────────────────────
if command -v conda &>/dev/null; then
    echo -e "${GREEN}✅ conda is already installed.${NC}"
else
    echo "📦 Installing Miniconda..."
    MINICONDA_INSTALLER="/tmp/miniconda_installer.sh"
    wget -q "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh" \
         -O "$MINICONDA_INSTALLER"
    bash "$MINICONDA_INSTALLER" -b -p "$HOME/miniconda3"
    rm -f "$MINICONDA_INSTALLER"

    # Initialise conda for the current shell
    eval "$("$HOME/miniconda3/bin/conda" shell.bash hook)"
    "$HOME/miniconda3/bin/conda" init bash
    echo -e "${GREEN}✅ Miniconda installed to ~/miniconda3${NC}"
    echo -e "${YELLOW}   ⚠️  Run 'source ~/.bashrc' or open a new terminal for conda to work.${NC}"
fi

# ── 2. Create conda environment and install Python dependencies ───────────
ENV_NAME="file-transfer"
if command -v conda &>/dev/null; then
    if ! conda env list | grep -qw "$ENV_NAME"; then
        echo "📦 Creating conda environment '$ENV_NAME' (Python 3.10)..."
        conda create -n "$ENV_NAME" python=3.10 -y -q
    else
        echo -e "${GREEN}✅ Conda environment '$ENV_NAME' already exists.${NC}"
    fi
    echo "📦 Installing Python dependencies into '$ENV_NAME'..."
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    conda run -n "$ENV_NAME" pip install -q -r "$SCRIPT_DIR/requirements.txt"
    echo -e "${GREEN}✅ Python dependencies installed.${NC}"
else
    echo -e "${YELLOW}⚠️  conda not available yet — Python env will be created after you source ~/.bashrc and re-run this script.${NC}"
fi

# ── 3. Install cloudflared ────────────────────────────────────────────────
if command -v cloudflared &>/dev/null; then
    echo -e "${GREEN}✅ cloudflared is already installed.${NC}"
else
    echo "📦 Installing cloudflared..."
    wget -q "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" \
         -O /tmp/cloudflared-linux-amd64
    chmod +x /tmp/cloudflared-linux-amd64
    sudo mv /tmp/cloudflared-linux-amd64 /usr/local/bin/cloudflared
    echo -e "${GREEN}✅ cloudflared installed.${NC}"
fi

# ── 4. Install tmux (if not present) ─────────────────────────────────────
if command -v tmux &>/dev/null; then
    echo -e "${GREEN}✅ tmux is already installed.${NC}"
else
    echo "📦 Installing tmux..."
    sudo apt-get update -qq && sudo apt-get install -y -qq tmux
    echo -e "${GREEN}✅ tmux installed.${NC}"
fi

# ── 5. Summary ────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════"
echo -e "  ${GREEN}All prerequisites installed!${NC}"
echo ""
echo "  Next steps:"
echo "    1. Open a new terminal (or run: source ~/.bashrc)"
echo "    2. Edit machines.json — set 'this_machine' to this machine's name"
echo "    3. Run:  chmod +x start.sh && ./start.sh"
echo "    4. Copy the Cloudflare URL into machines.json on ALL machines"
echo "══════════════════════════════════════════════════════════"
echo ""
