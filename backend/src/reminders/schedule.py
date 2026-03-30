import re
from dataclasses import dataclass
from datetime import datetime, timezone

MIN_INTERVAL_SECONDS = 10

_UNIT_SECONDS: dict[str, int] = {
    "year": 365 * 24 * 60 * 60,
    "month": 30 * 24 * 60 * 60,
    "day": 24 * 60 * 60,
    "hour": 60 * 60,
    "min": 60,
    "sec": 1,
}

_UNIT_ORDER: list[str] = ["year", "month", "day", "hour", "min", "sec"]
_TOKEN_RE = re.compile(r"^(?P<digit>[1-9]\d*)(?P<unit>year|month|day|hour|min|sec)$")


def parse_interval_seconds(interval: str) -> int:
    """
    解析 interval 字符串，语法：
    - `{digit}{unit}(-{digit}{unit})*`
    - digit 为正整数，不允许 0
    - unit 严格顺序：year-month-day-hour-min-sec，不允许重复
    - 不允许空格
    """
    if not isinstance(interval, str) or not interval:
        raise ValueError("interval 不能为空")
    if interval.strip() != interval:
        raise ValueError("interval 不允许包含前后空格")
    if " " in interval:
        raise ValueError("interval 不允许包含空格")

    parts = interval.split("-")
    if any(not p for p in parts):
        raise ValueError("interval 语法错误：不允许出现空段")

    used_units: set[str] = set()
    last_unit_index = -1
    total_seconds = 0

    for part in parts:
        match = _TOKEN_RE.match(part)
        if not match:
            raise ValueError(f"interval 语法错误: {part}")

        digit = int(match.group("digit"))
        unit = match.group("unit")

        if unit in used_units:
            raise ValueError(f"interval 单位重复: {unit}")
        used_units.add(unit)

        unit_index = _UNIT_ORDER.index(unit)
        if unit_index <= last_unit_index:
            raise ValueError("interval 单位顺序错误：必须按 year-month-day-hour-min-sec 递减")
        last_unit_index = unit_index

        total_seconds += digit * _UNIT_SECONDS[unit]

    if total_seconds < MIN_INTERVAL_SECONDS:
        raise ValueError(f"interval 过小：最小间隔为 {MIN_INTERVAL_SECONDS}sec")
    return total_seconds


def parse_one_time_utc_minute(one_time_utc: str) -> datetime:
    """
    解析一次性触发时间，格式：YYYY-MM-DDTHH:mmZ（UTC，精确到分钟）。
    """
    if not isinstance(one_time_utc, str) or not one_time_utc:
        raise ValueError("one_time_utc 不能为空")
    if one_time_utc.strip() != one_time_utc:
        raise ValueError("one_time_utc 不允许包含前后空格")

    # 例：2026-03-30T01:23Z
    try:
        dt = datetime.strptime(one_time_utc, "%Y-%m-%dT%H:%MZ")
    except ValueError as exc:
        raise ValueError("one_time_utc 格式错误：必须是 YYYY-MM-DDTHH:mmZ（UTC，精确到分钟）") from exc
    return dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class IntervalSchedule:
    interval_seconds: int


@dataclass(frozen=True)
class OneTimeSchedule:
    one_time_utc: datetime
