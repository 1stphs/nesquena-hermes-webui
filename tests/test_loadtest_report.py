import json
from pathlib import Path


def _write_stats(path: Path, rows: list[dict[str, object]]) -> None:
    headers = [
        "Type",
        "Name",
        "Request Count",
        "Failure Count",
        "95%",
    ]
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(str(row.get(header, "")) for header in headers))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_health(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_evaluate_run_passes_when_thresholds_are_met(tmp_path):
    from loadtests.report import evaluate_run

    stats_path = tmp_path / "run_020_stats.csv"
    health_path = tmp_path / "run_020_health.jsonl"
    _write_stats(
        stats_path,
        [
            {
                "Type": "POST",
                "Name": "/api/chat/start",
                "Request Count": 20,
                "Failure Count": 0,
                "95%": 400,
            },
            {
                "Type": "SSE",
                "Name": "/api/chat/stream total",
                "Request Count": 20,
                "Failure Count": 1,
                "95%": 42000,
            },
            {
                "Type": "SSE",
                "Name": "/api/chat/stream first_token",
                "Request Count": 20,
                "Failure Count": 0,
                "95%": 2400,
            },
        ],
    )
    _write_health(
        health_path,
        [
            {
                "health": {"status": "ok", "active_streams": 18},
                "container": {"cpu_percent": 71.5, "mem_percent": 52.0},
            },
            {
                "health": {"status": "ok", "active_streams": 20},
                "container": {"cpu_percent": 78.1, "mem_percent": 54.2},
            },
        ],
    )

    result = evaluate_run(20, stats_path, health_path)

    assert result.passed is True
    assert result.metrics["chat_start_success_rate"] == 1.0
    assert result.metrics["sse_completion_rate"] == 0.95
    assert result.metrics["first_token_p95_ms"] == 2400.0
    assert result.metrics["stream_total_p95_ms"] == 42000.0
    assert result.metrics["max_active_streams"] == 20


def test_summarize_runs_reports_largest_passing_concurrency(tmp_path):
    from loadtests.report import evaluate_run, summarize_runs

    good_stats = tmp_path / "run_020_stats.csv"
    good_health = tmp_path / "run_020_health.jsonl"
    bad_stats = tmp_path / "run_030_stats.csv"
    bad_health = tmp_path / "run_030_health.jsonl"
    _write_stats(
        good_stats,
        [
            {"Type": "POST", "Name": "/api/chat/start", "Request Count": 20, "Failure Count": 0, "95%": 500},
            {"Type": "SSE", "Name": "/api/chat/stream total", "Request Count": 20, "Failure Count": 0, "95%": 30000},
            {"Type": "SSE", "Name": "/api/chat/stream first_token", "Request Count": 20, "Failure Count": 0, "95%": 2000},
        ],
    )
    _write_health(good_health, [{"health": {"status": "ok"}, "container": {"cpu_percent": 40, "mem_percent": 30}}])
    _write_stats(
        bad_stats,
        [
            {"Type": "POST", "Name": "/api/chat/start", "Request Count": 30, "Failure Count": 2, "95%": 500},
            {"Type": "SSE", "Name": "/api/chat/stream total", "Request Count": 28, "Failure Count": 5, "95%": 70000},
            {"Type": "SSE", "Name": "/api/chat/stream first_token", "Request Count": 28, "Failure Count": 0, "95%": 17000},
        ],
    )
    _write_health(bad_health, [{"health": {"status": "degraded"}, "container": {"cpu_percent": 92, "mem_percent": 83}}])

    summary = summarize_runs(
        [
            evaluate_run(20, good_stats, good_health),
            evaluate_run(30, bad_stats, bad_health),
        ]
    )

    assert summary["max_stable_concurrent_runs"] == 20
    assert summary["runs"][0]["passed"] is True
    assert summary["runs"][1]["passed"] is False
