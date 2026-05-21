"""Poll Hermes health and optional local Docker stats into JSONL."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _fetch_json(url: str, timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # nosec B310
            raw = response.read()
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}, None
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return None, f"HTTP {exc.code}: {body[:300]}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _percent(value: str) -> float | None:
    raw = str(value or "").strip().rstrip("%")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _docker_stats(container: str) -> dict[str, Any] | None:
    if not container:
        return None
    try:
        proc = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        raw = json.loads(proc.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return None
    return {
        "name": raw.get("Name") or container,
        "cpu_percent": _percent(raw.get("CPUPerc", "")),
        "mem_percent": _percent(raw.get("MemPerc", "")),
        "mem_usage": raw.get("MemUsage"),
        "net_io": raw.get("NetIO"),
        "block_io": raw.get("BlockIO"),
        "pids": raw.get("PIDs"),
    }


def collect_sample(base_url: str, timeout: float, container: str) -> dict[str, Any]:
    health, error = _fetch_json(_url(base_url, "/health?deep=1"), timeout)
    return {
        "ts": time.time(),
        "base_url": base_url,
        "health": health,
        "health_error": error,
        "container": _docker_stats(container),
    }


def monitor(base_url: str, output: Path, interval: float, timeout: float, container: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        while True:
            sample = collect_sample(base_url, timeout, container)
            handle.write(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://172.234.237.195:8787")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--interval", default=5.0, type=float)
    parser.add_argument("--timeout", default=10.0, type=float)
    parser.add_argument("--container", default="hermes-webui")
    args = parser.parse_args()
    monitor(args.base_url, args.output, args.interval, args.timeout, args.container)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
