#!/usr/bin/env python3
"""
check_all.py — Health check helper for all machines in the network.

Usage:
    python check_all.py
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx

MACHINES_FILE = Path(__file__).parent / "machines.json"
REQUEST_TIMEOUT = 10.0


def load_machines() -> list[dict]:
    if not MACHINES_FILE.exists():
        print(f"❌ machines.json not found at {MACHINES_FILE}")
        sys.exit(1)
    with open(MACHINES_FILE) as f:
        cfg = json.load(f)
    return cfg["machines"]


async def check_machine(
    client: httpx.AsyncClient, machine: dict
) -> tuple[str, str, bool, str]:
    name, host = machine["name"], machine["host"]
    try:
        resp = await client.get(f"{host}/health")
        if resp.status_code == 200:
            data = resp.json()
            return name, host, True, data.get("machine", "?")
    except Exception:
        pass
    return name, host, False, ""


async def main() -> None:
    machines = load_machines()
    print(f"Checking {len(machines)} machine(s)...\n")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, verify=False) as client:
        tasks = [check_machine(client, m) for m in machines]
        results = await asyncio.gather(*tasks)

    for name, host, online, reported_name in results:
        if online:
            print(f"  {name} ({host})  ✅ online  (reports as: {reported_name})")
        else:
            print(f"  {name} ({host})  ❌ offline")

    print()


if __name__ == "__main__":
    asyncio.run(main())
