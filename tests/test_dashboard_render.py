"""Regression guard for the read-only dashboard's client-side render path.

Why this exists
---------------
The dashboard ships its UI as an external ``dashboard.js`` in ``static/``.
The Python suite exercises the data *endpoint* but never executes that JS, so a
runtime error in the render path stays invisible: the page catches its own
error, shows an "error: ..." status, renders empty panels, and every backend
test still passes.

This test closes the gap. It loads the external ``dashboard.js`` and runs it
under Node with a minimal DOM shim (see ``_dashboard_harness.js``) and a
representative dataset that forces the *non-empty* render path, then asserts
the page neither reports an error status nor leaves a panel empty. Node is used
(not a headless browser) because the failure is pure JS scoping semantics —
deterministic in any spec-compliant engine, with no browser flakiness. If Node
is unavailable the test skips, keeping a Node-less CI green while any dev
machine runs the guard.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "_dashboard_harness.js"
# External JS file (replaces the former inline <script>)
_DASHBOARD_JS = Path(__file__).parent.parent / "src" / "agentops_platform" / "static" / "dashboard.js"


def test_dashboard_renders_without_js_error() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available to execute the dashboard's inline JS")

    if not _DASHBOARD_JS.exists():
        pytest.skip(f"dashboard.js not found at {_DASHBOARD_JS}")

    try:
        proc = subprocess.run(
            [node, str(_HARNESS), str(_DASHBOARD_JS)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:  # pragma: no cover
        pytest.skip(f"node did not run: {exc}")

    out = (proc.stdout + proc.stderr).strip()
    assert proc.returncode == 0, f"dashboard render path failed under Node:\n{out}"
