"""Pytest hooks for local MDGA benches."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "local: uses private local data/; enable with MDGA_LOCAL_BENCH=1",
    )
