"""Summarize Hermes WebUI load-test runs."""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class Thresholds:
    chat_start_success_rate: float = 0.99
    sse_completion_rate: float = 0.95
    first_token_p95_ms: float = 15_000.0
    stream_total_p95_ms: float = 60_000.0
    max_cpu_percent: float = 85.0
    max_mem_percent: float = 80.0
    require_health_ok: bool = True


@dataclass(frozen=True)
class RunResult:
    concurrency: int
    passed: bool
    metrics: dict[str, Any]
    reasons: list[str]
    stats_path: str
    health_path: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        raw = str(value).strip().replace(",", "")
        if not raw:
            return default
        return float(raw)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    return int(_to_float(value, float(default)))


def _load_stats(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_health(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _find_row(rows: Iterable[dict[str, str]], name: str) -> dict[str, str] | None:
    for row in rows:
        if (row.get("Name") or "").strip() == name:
            return row
    for row in rows:
        if name in (row.get("Name") or ""):
            return row
    return None


def _success_rate(row: dict[str, str] | None) -> float:
    if row is None:
        return 0.0
    requests = _to_int(row.get("Request Count"))
    failures = _to_int(row.get("Failure Count"))
    if requests <= 0:
        return 0.0
    return round(max(0.0, (requests - failures) / requests), 4)


def _p95(row: dict[str, str] | None) -> float:
    if row is None:
        return 0.0
    for key in ("95%", "95", "95.0%"):
        if key in row:
            return _to_float(row.get(key))
    return 0.0


def _health_status_ok(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    for row in rows:
        if row.get("health_error"):
            return False
        health = row.get("health")
        if not isinstance(health, dict) or health.get("status") != "ok":
            return False
    return True


def _max_active_streams(rows: list[dict[str, Any]]) -> int:
    values = []
    for row in rows:
        health = row.get("health")
        if isinstance(health, dict):
            values.append(_to_int(health.get("active_streams")))
    return max(values, default=0)


def _max_container_metric(rows: list[dict[str, Any]], key: str) -> float | None:
    values = []
    for row in rows:
        container = row.get("container")
        if not isinstance(container, dict):
            continue
        value = container.get(key)
        if value is not None:
            values.append(_to_float(value))
    if not values:
        return None
    return max(values)


def evaluate_run(
    concurrency: int,
    stats_path: Path | str,
    health_path: Path | str,
    thresholds: Thresholds | None = None,
) -> RunResult:
    thresholds = thresholds or Thresholds()
    stats_path = Path(stats_path)
    health_path = Path(health_path)
    stats = _load_stats(stats_path)
    health = _load_health(health_path)

    chat_start = _find_row(stats, "/api/chat/start")
    stream_total = _find_row(stats, "/api/chat/stream total")
    first_token = _find_row(stats, "/api/chat/stream first_token")
    max_cpu = _max_container_metric(health, "cpu_percent")
    max_mem = _max_container_metric(health, "mem_percent")
    metrics: dict[str, Any] = {
        "chat_start_success_rate": _success_rate(chat_start),
        "sse_completion_rate": _success_rate(stream_total),
        "first_token_p95_ms": _p95(first_token),
        "stream_total_p95_ms": _p95(stream_total),
        "health_ok": _health_status_ok(health),
        "health_samples": len(health),
        "max_active_streams": _max_active_streams(health),
        "max_cpu_percent": max_cpu,
        "max_mem_percent": max_mem,
    }

    reasons: list[str] = []
    if metrics["chat_start_success_rate"] < thresholds.chat_start_success_rate:
        reasons.append(
            f"chat_start_success_rate {metrics['chat_start_success_rate']:.3f} "
            f"< {thresholds.chat_start_success_rate:.3f}"
        )
    if metrics["sse_completion_rate"] < thresholds.sse_completion_rate:
        reasons.append(
            f"sse_completion_rate {metrics['sse_completion_rate']:.3f} "
            f"< {thresholds.sse_completion_rate:.3f}"
        )
    if metrics["first_token_p95_ms"] <= 0 or metrics["first_token_p95_ms"] > thresholds.first_token_p95_ms:
        reasons.append(
            f"first_token_p95_ms {metrics['first_token_p95_ms']:.1f} "
            f"> {thresholds.first_token_p95_ms:.1f}"
        )
    if metrics["stream_total_p95_ms"] <= 0 or metrics["stream_total_p95_ms"] > thresholds.stream_total_p95_ms:
        reasons.append(
            f"stream_total_p95_ms {metrics['stream_total_p95_ms']:.1f} "
            f"> {thresholds.stream_total_p95_ms:.1f}"
        )
    if thresholds.require_health_ok and not metrics["health_ok"]:
        reasons.append("health was not ok for every monitor sample")
    if max_cpu is not None and max_cpu > thresholds.max_cpu_percent:
        reasons.append(f"max_cpu_percent {max_cpu:.1f} > {thresholds.max_cpu_percent:.1f}")
    if max_mem is not None and max_mem > thresholds.max_mem_percent:
        reasons.append(f"max_mem_percent {max_mem:.1f} > {thresholds.max_mem_percent:.1f}")

    return RunResult(
        concurrency=concurrency,
        passed=not reasons,
        metrics=metrics,
        reasons=reasons,
        stats_path=str(stats_path),
        health_path=str(health_path),
    )


def summarize_runs(runs: Iterable[RunResult]) -> dict[str, Any]:
    ordered = sorted(runs, key=lambda run: run.concurrency)
    passing = [run.concurrency for run in ordered if run.passed]
    return {
        "max_stable_concurrent_runs": max(passing) if passing else 0,
        "runs": [run.as_dict() for run in ordered],
    }


def _discover_runs(results_dir: Path, thresholds: Thresholds) -> list[RunResult]:
    runs: list[RunResult] = []
    for stats_path in sorted(results_dir.glob("run_*_stats.csv")):
        match = re.search(r"run_(\d+)", stats_path.name)
        if not match:
            continue
        concurrency = int(match.group(1))
        health_path = stats_path.with_name(stats_path.name.removesuffix("_stats.csv") + "_health.jsonl")
        runs.append(evaluate_run(concurrency, stats_path, health_path, thresholds))
    return runs


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Hermes WebUI Load Test Summary",
        "",
        f"MaxStableConcurrentRuns: {summary['max_stable_concurrent_runs']}",
        "",
        "| concurrency | passed | start success | sse completion | first token p95 ms | stream p95 ms | health | max active streams | cpu max | mem max | reasons |",
        "|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---|",
    ]
    for run in summary["runs"]:
        metrics = run["metrics"]
        reasons = "; ".join(run["reasons"]) if run["reasons"] else ""
        lines.append(
            "| {concurrency} | {passed} | {start:.3f} | {sse:.3f} | {first:.1f} | "
            "{total:.1f} | {health} | {active} | {cpu} | {mem} | {reasons} |".format(
                concurrency=run["concurrency"],
                passed="yes" if run["passed"] else "no",
                start=metrics["chat_start_success_rate"],
                sse=metrics["sse_completion_rate"],
                first=metrics["first_token_p95_ms"],
                total=metrics["stream_total_p95_ms"],
                health="ok" if metrics["health_ok"] else "bad",
                active=metrics["max_active_streams"],
                cpu="" if metrics["max_cpu_percent"] is None else f"{metrics['max_cpu_percent']:.1f}",
                mem="" if metrics["max_mem_percent"] is None else f"{metrics['max_mem_percent']:.1f}",
                reasons=reasons.replace("|", "/"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def _write_outputs(summary: dict[str, Any], output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_md.write_text(_render_markdown(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default="loadtests/results", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--start-success-rate", default=0.99, type=float)
    parser.add_argument("--sse-completion-rate", default=0.95, type=float)
    parser.add_argument("--first-token-p95-ms", default=15_000.0, type=float)
    parser.add_argument("--stream-total-p95-ms", default=60_000.0, type=float)
    parser.add_argument("--max-cpu-percent", default=85.0, type=float)
    parser.add_argument("--max-mem-percent", default=80.0, type=float)
    parser.add_argument("--allow-missing-health", action="store_true")
    args = parser.parse_args()

    thresholds = Thresholds(
        chat_start_success_rate=args.start_success_rate,
        sse_completion_rate=args.sse_completion_rate,
        first_token_p95_ms=args.first_token_p95_ms,
        stream_total_p95_ms=args.stream_total_p95_ms,
        max_cpu_percent=args.max_cpu_percent,
        max_mem_percent=args.max_mem_percent,
        require_health_ok=not args.allow_missing_health,
    )
    runs = _discover_runs(args.results_dir, thresholds)
    summary = summarize_runs(runs)
    output_json = args.output_json or args.results_dir / "summary.json"
    output_md = args.output_md or args.results_dir / "summary.md"
    _write_outputs(summary, output_json, output_md)
    print(_render_markdown(summary))
    return 0 if runs else 2


if __name__ == "__main__":
    raise SystemExit(main())
