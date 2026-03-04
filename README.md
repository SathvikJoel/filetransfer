# File Transfer Service

A distributed file transfer service for cloud machines. Each machine runs an identical FastAPI server exposed via **Cloudflare Tunnel** (no ports to open). Files are transferred between machines using **croc**.

## Architecture

```
┌──────────────┐     Cloudflare Tunnel     ┌──────────────┐
│   machine1   │◄──── HTTPS (public) ────►│   machine2   │
│  server.py   │                           │  server.py   │
│  cloudflared │                           │  cloudflared │
└──────────────┘                           └──────────────┘
        ▲                                          ▲
        │           ┌──────────────┐               │
        └───────────│   machine3   │───────────────┘
                    │  server.py   │
                    │  cloudflared │
                    └──────────────┘

Transfer flow:
  1. Requester calls /check-file on remotes (parallel)
  2. Requester calls /send-file on the machine that has it
  3. That machine starts `croc send`, returns the code
  4. Requester runs `croc --yes <code>` locally to receive
```

## File Structure

```
├── server.py          # FastAPI server — identical on all machines
├── fetch_file.py      # Agent CLI — run on requesting machine
├── check_all.py       # Health check helper
├── machines.json      # Registry — edit per machine
├── start.sh           # One-command setup and launch
├── setup_machine.sh   # Install all prerequisites on a new machine
├── requirements.txt   # Python dependencies
└── README.md          # This file
```

---

## Prerequisites

### 1. Install Conda (Miniconda)

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p $HOME/miniconda3
rm /tmp/miniconda.sh
~/miniconda3/bin/conda init bash
source ~/.bashrc
```

### 2. Install croc

```bash
curl https://getcroc.schollz.com | bash
```

### 3. Install cloudflared

```bash
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared-linux-amd64
sudo mv cloudflared-linux-amd64 /usr/local/bin/cloudflared
```

### 4. Install tmux (optional but recommended)

```bash
sudo apt-get install -y tmux
```

**Or run the automated setup script which does all of the above:**

```bash
chmod +x setup_machine.sh
./setup_machine.sh
```

---

## Quick Start

### Step 1: Edit `machines.json`

Set `this_machine` to the current machine's name:

```json
{
  "machines": [
    { "name": "machine1", "host": "https://REPLACE-AFTER-START.trycloudflare.com" },
    { "name": "machine2", "host": "https://REPLACE-AFTER-START.trycloudflare.com" }
  ],
  "this_machine": "machine1"
}
```

### Step 2: Launch the server and tunnel

```bash
chmod +x start.sh
./start.sh
```

This will:
1. Create the `file-transfer` conda environment (Python 3.10)
2. Install FastAPI, uvicorn, httpx
3. Start the uvicorn server on `127.0.0.1:8000`
4. Start a Cloudflare quick tunnel
5. Print the public tunnel URL

Output will look like:
```
════════════════════════════════════════════════════════════════
  ✅ Server running.
  🌐 Cloudflare URL: https://random-words-here.trycloudflare.com
  👉 Copy this URL into machines.json on all machines.
════════════════════════════════════════════════════════════════
```

### Step 3: Update `machines.json` everywhere

Copy the Cloudflare URL printed by each machine into the `host` field of `machines.json` on **all** machines.

### Step 4 (recommended): Run inside tmux for persistence

```bash
tmux new -s services
./start.sh
# Detach: Ctrl+B then D
# Reattach later: tmux attach -t services
```

---

## Usage

### Check all machines are online

```bash
python check_all.py
```

Output:
```
  machine1 (https://abc.trycloudflare.com)  ✅ online
  machine2 (https://def.trycloudflare.com)  ✅ online
  machine3 (https://ghi.trycloudflare.com)  ❌ offline
```

### Fetch a file (auto-search all machines)

```bash
python fetch_file.py --file /path/to/file --destination /local/path/
```

Output:
```
[1/4] Searching for /path/to/file across 2 machines...
[2/4] Found on machine2. Requesting send...
[3/4] Croc ready (code: transfer-abc123). Receiving 100 MB...
[4/4] ✅ Transfer complete. 100 MB received in 12.3s → /local/path/
```

### Fetch from a specific machine

```bash
python fetch_file.py --file /path/to/file --from machine2 --destination /local/path/
```

### List files on all machines

```bash
python fetch_file.py --list /some/directory --pattern "*.csv"
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/check-file?path=/path` | Check if a file exists |
| GET | `/list-files?path=/dir&pattern=*` | List files recursively |
| POST | `/send-file` | Start croc send, return code |
| GET | `/transfer-status/{id}` | Poll transfer status |

---

## Adding a New Machine

1. Run `setup_machine.sh` on the new machine (or install prerequisites manually)
2. Copy the entire `filetransfer/` directory to the new machine
3. Edit `machines.json`:
   - Set `this_machine` to the new machine's name (e.g., `"machine4"`)
   - Add the new machine entry to the `machines` array (URL placeholder for now)
4. Run `./start.sh`
5. Copy the printed Cloudflare URL
6. Update `machines.json` on **every** machine:
   - Add/update the new machine's entry with the real URL

---

## Logs

- **Server log**: `server.log` (all API calls, transfers, errors)
- **Cloudflare log**: `cloudflare.log` (tunnel status)
- **Tunnel URL**: `tunnel_url.txt` (saved for reference)

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `cloudflared` not found | Run `setup_machine.sh` or install manually |
| `croc` not found | `curl https://getcroc.schollz.com \| bash` |
| Port 8000 in use | `start.sh` auto-kills the old process, or run `kill $(lsof -i :8000 -t)` |
| Tunnel URL not appearing | Check `cloudflare.log` — may be a network/firewall issue |
| Transfer fails | Retry is built-in (3 attempts). Check `server.log` on the sender |
| Machine shows offline | Verify tmux session is still running: `tmux attach -t services` |
