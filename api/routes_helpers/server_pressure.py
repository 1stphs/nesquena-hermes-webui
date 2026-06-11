"""高成本会话入口的服务器压力检查。"""

from __future__ import annotations

from pathlib import Path


SERVER_MEMORY_PRESSURE_MESSAGE = "服务器压力过大"
SERVER_MEMORY_PRESSURE_CODE = "SERVER_MEMORY_PRESSURE"
SERVER_MEMORY_PRESSURE_RETRY_AFTER = "10"
SERVER_MEMORY_PRESSURE_THRESHOLD_PERCENT = 80.0

_PROC_MEMINFO_PATH = Path("/proc/meminfo")
_CGROUP_V2_MEMORY_CURRENT_PATH = Path("/sys/fs/cgroup/memory.current")
_CGROUP_V2_MEMORY_MAX_PATH = Path("/sys/fs/cgroup/memory.max")


def _read_int_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _usage_exceeds_threshold(used: int, total: int, *, threshold_percent: float) -> bool | None:
    if total <= 0 or used < 0:
        return None
    return (used / total * 100.0) > threshold_percent


def _meminfo_usage_exceeds_threshold(
    path: Path = _PROC_MEMINFO_PATH,
    *,
    threshold_percent: float = SERVER_MEMORY_PRESSURE_THRESHOLD_PERCENT,
) -> bool | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    values: dict[str, int] = {}
    for line in lines:
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        parts = raw_value.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0])
        except ValueError:
            continue

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return None

    return _usage_exceeds_threshold(
        total - available,
        total,
        threshold_percent=threshold_percent,
    )


def _cgroup_v2_usage_exceeds_threshold(
    current_path: Path = _CGROUP_V2_MEMORY_CURRENT_PATH,
    max_path: Path = _CGROUP_V2_MEMORY_MAX_PATH,
    *,
    threshold_percent: float = SERVER_MEMORY_PRESSURE_THRESHOLD_PERCENT,
) -> bool | None:
    try:
        raw_max = max_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw_max or raw_max == "max":
        return None
    try:
        total = int(raw_max)
    except ValueError:
        return None

    used = _read_int_file(current_path)
    if used is None:
        return None

    return _usage_exceeds_threshold(used, total, threshold_percent=threshold_percent)


def _is_server_memory_pressure_exceeded(
    *,
    proc_meminfo_path: Path = _PROC_MEMINFO_PATH,
    cgroup_current_path: Path = _CGROUP_V2_MEMORY_CURRENT_PATH,
    cgroup_max_path: Path = _CGROUP_V2_MEMORY_MAX_PATH,
    threshold_percent: float = SERVER_MEMORY_PRESSURE_THRESHOLD_PERCENT,
) -> bool:
    """任一可用内存压力指标超过阈值时返回 True。

    指标缺失或格式异常时视为不可用，避免非 Linux 本地开发环境误拦截。
    """
    checks = (
        lambda: _meminfo_usage_exceeds_threshold(
            proc_meminfo_path,
            threshold_percent=threshold_percent,
        ),
        lambda: _cgroup_v2_usage_exceeds_threshold(
            cgroup_current_path,
            cgroup_max_path,
            threshold_percent=threshold_percent,
        ),
    )
    for check in checks:
        try:
            result = check()
        except Exception:
            continue
        if result is True:
            return True
    return False
