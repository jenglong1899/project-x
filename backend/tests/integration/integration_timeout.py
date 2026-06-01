import os


def integration_timeout_s(*, default_timeout_s: float = 180.0) -> float:
    """
    真实 API / 端到端集成测试在网络不稳定时容易抖动。
    用环境变量统一控制超时，避免把很多 magic number 写死在测试逻辑里。
    """
    raw = os.getenv("PROJECT_X_INTEGRATION_TIMEOUT_S", "").strip()
    if not raw:
        return float(default_timeout_s)
    try:
        timeout_s = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"PROJECT_X_INTEGRATION_TIMEOUT_S 必须是数字，但拿到的是：{raw!r}")
    if timeout_s <= 0:
        raise ValueError(f"PROJECT_X_INTEGRATION_TIMEOUT_S 必须 > 0，但拿到的是：{raw!r}")
    return timeout_s
