import os


def pytest_configure(config) -> None:  # noqa: ARG001
    os.environ.setdefault("PROJECT_X_INTEGRATION_TIMEOUT_S", "180")

