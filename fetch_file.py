#!/usr/bin/env python3
"""
fetch_file.py — Agent CLI for the File Transfer Service.

Usage examples:
    # Search all machines for a file and fetch it
    python fetch_file.py --file /path/to/file --destination /local/path/

    # Fetch from a specific machine
    python fetch_file.py --file /path/to/file --from machine2 --destination /local/path/

    # List files on all machines under a directory
    python fetch_file.py --list /some/directory --pattern "*.csv"
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MACHINES_FILE = Path(__file__).parent / "machines.json"
REQUEST_TIMEOUT = 30.0   # seconds for HTTP requests
MAX_RETRIES = 3


def load_machines() -> tuple[list[dict], str]:
    """Return (machines_list, this_machine_name)."""
    if not MACHINES_FILE.exists():
        print(f"❌ machines.json not found at {MACHINES_FILE}")
        sys.exit(1)
    with open(MACHINES_FILE) as f:
        cfg = json.load(f)
    return cfg["machines"], cfg.get("this_machine", "")


def remote_machines(machines: list[dict], this_machine: str) -> list[dict]:
    """Filter out this machine from the list."""
    return [m for m in machines if m["name"] != this_machine]


# ---------------------------------------------------------------------------
# List mode
# ---------------------------------------------------------------------------
async def list_files_on_all(directory: str, pattern: str) -> None:
    machines, this = load_machines()
    remotes = remote_machines(machines, this)

    if not remotes:
        print("No remote machines configured.")
        return

    print(f"Listing files under {directory} (pattern={pattern}) on {len(remotes)} machine(s)...\n")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, verify=False) as client:
        tasks = []
        for m in remotes:
            tasks.append(_list_on_machine(client, m, directory, pattern))
        await asyncio.gather(*tasks)


async def _list_on_machine(client: httpx.AsyncClient, machine: dict, directory: str, pattern: str) -> None:
    name, host = machine["name"], machine["host"]
    try:
        resp = await client.get(f"{host}/list-files", params={"path": directory, "pattern": pattern})
        if resp.status_code == 200:
            data = resp.json()
            print(f"── {name} ({host}) — {data['total_files']} file(s) ──")
            for f in data["files"]:
                print(f"  {f['path']}  ({f['size_human']})")
            print()
        else:
            print(f"── {name} — error {resp.status_code}: {resp.text}\n")
    except Exception as e:
        print(f"── {name} — unreachable: {e}\n")


# ---------------------------------------------------------------------------
# Fetch mode
# ---------------------------------------------------------------------------
async def fetch_file(file_path: str, destination: str, from_machine: str | None) -> None:
    machines, this = load_machines()
    remotes = remote_machines(machines, this)

    if not remotes:
        print("No remote machines configured.")
        return

    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)

    # Step 1: Find the file
    source_machine: dict | None = None

    if from_machine:
        # Go directly to the specified machine
        source_machine = next((m for m in machines if m["name"] == from_machine), None)
        if not source_machine:
            print(f"❌ Machine '{from_machine}' not found in machines.json")
            sys.exit(1)
        print(f"[1/4] Checking {from_machine} for {file_path}...")
        exists = await _check_file(source_machine, file_path)
        if not exists:
            print(f"❌ File not found on {from_machine}")
            sys.exit(1)
    else:
        print(f"[1/4] Searching for {file_path} across {len(remotes)} machine(s)...")
        source_machine = await _search_all_machines(remotes, file_path)
        if not source_machine:
            print(f"❌ File not found on any machine.")
            sys.exit(1)

    print(f"[2/4] Found on {source_machine['name']}. Requesting send...")

    # Retry loop
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            send_data = await _request_send(source_machine, file_path)
            croc_code = send_data["croc_code"]
            size_human = send_data.get("size_human", "unknown size")
            print(f"[3/4] Croc ready (code: {croc_code}). Receiving {size_human}...")

            start_time = time.time()
            success = _run_croc_receive(croc_code, dest)
            elapsed = time.time() - start_time

            if success:
                print(f"[4/4] ✅ Transfer complete. {size_human} received in {elapsed:.1f}s → {dest}")
                return
            else:
                print(f"  ⚠️  Attempt {attempt}/{MAX_RETRIES} failed.")
        except Exception as e:
            print(f"  ⚠️  Attempt {attempt}/{MAX_RETRIES} error: {e}")

    print(f"❌ All {MAX_RETRIES} attempts failed.")
    sys.exit(1)


async def _check_file(machine: dict, file_path: str) -> bool:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, verify=False) as client:
        try:
            resp = await client.get(f"{machine['host']}/check-file", params={"path": file_path})
            if resp.status_code == 200:
                return resp.json().get("exists", False)
        except Exception:
            pass
    return False


async def _search_all_machines(machines: list[dict], file_path: str) -> dict | None:
    """Check all machines in parallel, return the first one that has the file."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, verify=False) as client:
        tasks = {m["name"]: _check_file_with_client(client, m, file_path) for m in machines}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for machine, result in zip(machines, results):
            if result is True:
                return machine
    return None


async def _check_file_with_client(client: httpx.AsyncClient, machine: dict, file_path: str) -> bool:
    try:
        resp = await client.get(f"{machine['host']}/check-file", params={"path": file_path})
        if resp.status_code == 200:
            return resp.json().get("exists", False)
    except Exception:
        pass
    return False


async def _request_send(machine: dict, file_path: str) -> dict:
    """Call POST /send-file and return the response data."""
    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        resp = await client.post(f"{machine['host']}/send-file", json={"path": file_path})
        if resp.status_code != 200:
            raise RuntimeError(f"send-file failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        if data.get("status") != "ready":
            raise RuntimeError(f"send-file returned status={data.get('status')}")
        return data


def _run_croc_receive(croc_code: str, dest: Path) -> bool:
    """Run croc on the local machine to receive the file."""
    cmd = ["croc", "--yes", croc_code]
    try:
        result = subprocess.run(cmd, cwd=str(dest), timeout=3600, capture_output=True, text=True)
        if result.returncode == 0:
            return True
        print(f"  croc stderr: {result.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        print("  croc timed out (1 hour).")
        return False
    except FileNotFoundError:
        print("  ❌ croc is not installed. Install it: curl https://getcroc.schollz.com | bash")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="File Transfer Agent — fetch files from remote machines via croc",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", type=str, help="Absolute path of the file to fetch")
    group.add_argument("--list", type=str, metavar="DIR", help="List files under a directory on all machines")

    parser.add_argument("--destination", type=str, help="Local destination directory (required with --file)")
    parser.add_argument("--from", dest="from_machine", type=str, help="Fetch from a specific machine name")
    parser.add_argument("--pattern", type=str, default="*", help="Glob pattern for --list mode (default: *)")

    args = parser.parse_args()

    if args.file:
        if not args.destination:
            parser.error("--destination is required when using --file")
        asyncio.run(fetch_file(args.file, args.destination, args.from_machine))
    elif args.list:
        asyncio.run(list_files_on_all(args.list, args.pattern))


if __name__ == "__main__":
    main()
