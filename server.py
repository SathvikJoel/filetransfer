"""
File Transfer Service — FastAPI Server
Identical on every machine in the network.
"""

import asyncio
import json
import logging
import os
import subprocess
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("server.log", mode="a"),
    ],
)
logger = logging.getLogger("filetransfer")

# ---------------------------------------------------------------------------
# Load machine identity
# ---------------------------------------------------------------------------
MACHINES_FILE = Path(__file__).parent / "machines.json"
MACHINE_NAME = "unknown"
if MACHINES_FILE.exists():
    with open(MACHINES_FILE) as f:
        _cfg = json.load(f)
    MACHINE_NAME = _cfg.get("this_machine", "unknown")

# ---------------------------------------------------------------------------
# In-memory transfer registry
# ---------------------------------------------------------------------------
transfers: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _human_size(n_bytes: int) -> str:
    """Return a human-readable size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n_bytes) < 1024:
            return f"{n_bytes:.1f} {unit}" if unit != "B" else f"{n_bytes} B"
        n_bytes /= 1024  # type: ignore[assignment]
    return f"{n_bytes:.1f} PB"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _monitor_transfer(transfer_id: str, proc: asyncio.subprocess.Process) -> None:
    """Background task that watches a croc send subprocess and updates status."""
    info = transfers[transfer_id]
    try:
        assert proc.stdout is not None
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode(errors="replace").strip()
            logger.info("croc [%s]: %s", transfer_id[:8], line)
            if "Sending" in line or "sending" in line:
                info["status"] = "sending"

        retcode = await proc.wait()
        if retcode == 0:
            info["status"] = "completed"
            logger.info("Transfer %s completed.", transfer_id[:8])
        else:
            stderr_out = ""
            if proc.stderr:
                stderr_out = (await proc.stderr.read()).decode(errors="replace")
            info["status"] = "failed"
            info["error"] = stderr_out or f"croc exited with code {retcode}"
            logger.error(
                "Transfer %s failed (rc=%d): %s",
                transfer_id[:8],
                retcode,
                info["error"],
            )
    except Exception:
        info["status"] = "failed"
        info["error"] = traceback.format_exc()
        logger.error("Transfer %s monitor error:\n%s", transfer_id[:8], info["error"])


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="File Transfer Service", version="1.0.0")


@app.on_event("startup")
async def _startup() -> None:
    logger.info("Server starting — machine=%s", MACHINE_NAME)


# ---- GET /health ----------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "machine": MACHINE_NAME, "timestamp": _now_iso()}


# ---- GET /check-file ------------------------------------------------------
@app.get("/check-file")
async def check_file(path: str = Query(..., description="Absolute path to the file")):
    logger.info("check-file: %s", path)
    p = Path(path)
    if p.exists() and p.is_file():
        stat = p.stat()
        logger.info("check-file: FOUND %s (%d bytes)", path, stat.st_size)
        return {
            "exists": True,
            "path": str(p),
            "size_bytes": stat.st_size,
            "size_human": _human_size(stat.st_size),
            "last_modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
        }
    logger.info("check-file: NOT FOUND %s", path)
    return {"exists": False, "path": str(p)}


# ---- GET /list-files -------------------------------------------------------
@app.get("/list-files")
async def list_files(
    path: str = Query(..., description="Absolute path to a directory"),
    pattern: str = Query("*", description="Glob pattern filter"),
):
    logger.info("list-files: dir=%s pattern=%s", path, pattern)
    d = Path(path)
    if not d.exists() or not d.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

    files = []
    for f in sorted(d.rglob(pattern)):
        if f.is_file():
            stat = f.stat()
            files.append(
                {
                    "path": str(f),
                    "size_bytes": stat.st_size,
                    "size_human": _human_size(stat.st_size),
                    "last_modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
    logger.info("list-files: %d files found under %s", len(files), path)
    return {"directory": str(d), "files": files, "total_files": len(files)}


# ---- POST /send-file -------------------------------------------------------
class SendFileRequest(BaseModel):
    path: str


@app.post("/send-file")
async def send_file(req: SendFileRequest):
    logger.info("send-file: %s", req.path)
    p = Path(req.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {req.path}")

    is_dir = p.is_dir()
    if is_dir:
        # Calculate total size of directory
        total_bytes = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    else:
        total_bytes = p.stat().st_size
    transfer_id = str(uuid.uuid4())

    logger.info("send-file: transfer_id=%s", transfer_id[:8])

    # Launch croc send — let croc auto-generate the code phrase
    # croc writes its output to stderr, so merge stderr into stdout
    cmd = ["croc", "send", str(p)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    # Poll until croc is ready and parse the auto-generated code (up to 15 seconds)
    ready = False
    croc_code = ""
    deadline = asyncio.get_event_loop().time() + 15
    collected_lines: list[str] = []
    assert proc.stdout is not None

    while asyncio.get_event_loop().time() < deadline:
        try:
            line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=0.5)
        except asyncio.TimeoutError:
            # Check if process died
            if proc.returncode is not None:
                break
            continue

        if not line_bytes:
            break
        line = line_bytes.decode(errors="replace").strip()
        collected_lines.append(line)
        logger.info("croc startup [%s]: %s", transfer_id[:8], line)

        # croc v10+ prints "Code is: <code>" when ready
        if "code is:" in line.lower():
            # Extract the code from e.g. "Code is: 1234-word-word-word"
            parts = line.split(":", 1)
            if len(parts) == 2:
                croc_code = parts[1].strip()
            ready = True
            break

    if not ready or not croc_code:
        proc.kill()
        detail = f"croc failed to become ready. output: {collected_lines}"
        logger.error("send-file: %s", detail)
        raise HTTPException(status_code=500, detail=detail)

    logger.info(
        "send-file: croc ready for transfer %s, code=%s", transfer_id[:8], croc_code
    )

    transfers[transfer_id] = {
        "transfer_id": transfer_id,
        "croc_code": croc_code,
        "path": str(p),
        "status": "ready",
        "size_bytes": total_bytes,
        "size_human": _human_size(total_bytes),
        "started_at": _now_iso(),
        "error": None,
    }

    # Start background monitor
    asyncio.create_task(_monitor_transfer(transfer_id, proc))

    return {
        "transfer_id": transfer_id,
        "croc_code": croc_code,
        "path": str(p),
        "status": "ready",
        "size_bytes": total_bytes,
        "size_human": _human_size(total_bytes),
    }


# ---- GET /transfer-status/{transfer_id} -----------------------------------
@app.get("/transfer-status/{transfer_id}")
async def transfer_status(transfer_id: str):
    info = transfers.get(transfer_id)
    if not info:
        raise HTTPException(
            status_code=404, detail=f"Transfer not found: {transfer_id}"
        )
    return {
        "transfer_id": info["transfer_id"],
        "croc_code": info["croc_code"],
        "status": info["status"],
        "path": info["path"],
        "started_at": info["started_at"],
        "error": info["error"],
    }
