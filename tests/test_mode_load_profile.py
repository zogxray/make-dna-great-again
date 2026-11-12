"""Local load profile: open F1–F10, time until idle, write cProfile reports.

Requires your real ``data/`` (private). Not for CI.

    MDGA_LOCAL_BENCH=1 pytest tests/test_mode_load_profile.py -s
    # or:
    python -m ydna.bench.mode_load
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.local,
    pytest.mark.skipif(
        os.environ.get("MDGA_LOCAL_BENCH") != "1",
        reason="Set MDGA_LOCAL_BENCH=1 to run local data/ load profiling",
    ),
]


@pytest.mark.asyncio
async def test_mode_load_profile() -> None:
    from ydna.bench.mode_load import run_mode_load_bench
    from ydna.core.store import cache_dir

    out = cache_dir() / "bench" / "pytest-last"
    try:
        report = await run_mode_load_bench(
            timeout=float(os.environ.get("MDGA_BENCH_TIMEOUT", "180")),
            quick=os.environ.get("MDGA_BENCH_QUICK") == "1",
            out_dir=out,
            profile=True,
        )
    except RuntimeError as exc:
        pytest.skip(str(exc))

    print("\n" + report.summary_table() + "\n")
    assert report.timings, "no mode timings recorded"
    assert len(report.timings) == 9
    failed = [t for t in report.timings if not t.ok]
    assert not failed, "mode load failures:\n" + "\n".join(
        f"  {t.key}: {t.error}" for t in failed
    )
    # Soft budget: flag pathological hangs (> timeout already errors).
    # Startup+all modes on a warm cache should finish; wall per mode printed above.
    assert report.total_s < 600, f"whole bench too slow: {report.total_s:.1f}s"
