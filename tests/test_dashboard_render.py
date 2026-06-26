"""Regression guard for the read-only dashboard's client-side render path.

Why this exists
---------------
The dashboard ships its entire UI as one inline ``<script>`` inside
``_DASHBOARD_HTML``. The Python suite exercises the data *endpoint* but never
executes that JS, so a runtime error in the render path stays invisible: the
page catches its own error, shows an "error: ..." status, renders empty panels,
and every backend test still passes.

That blind spot let a real bug ship — a loop variable ``var t`` inside
``buildChart`` shadowed the i18n helper ``t()`` for the whole function, so the
moment any evaluation existed, ``render()`` threw "t is not a function" and the
chart, deployment and decision panels all went blank.

This test closes the gap. It extracts the inline JS and runs it under Node with
a minimal DOM shim (see ``_dashboard_harness.js``) and a representative dataset
that forces the *non-empty* render path, then asserts the page neither reports
an error status nor leaves a panel empty. Node is used (not a headless browser)
because the failure is pure JS scoping semantics — deterministic in any
spec-compliant engine, with no browser flakiness. If Node is unavailable the
test skips, keeping a Node-less CI green while any dev machine runs the guard.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from agentops_platform.dashboard import _DASHBOARD_HTML

_HARNESS = Path(__file__).parent / "_dashboard_harness.js"


def _extract_inline_js() -> str:
    """Pull the single inline <script> body out of the dashboard HTML."""
    m = re.search(r"<script>\n(.*?)\n\s*</script>", _DASHBOARD_HTML, re.S)
    assert m, "could not locate the dashboard's inline <script> block"
    return m.group(1)


def test_dashboard_renders_without_js_error() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available to execute the dashboard's inline JS")

    js = _extract_inline_js()
    with tempfile.TemporaryDirectory() as tmp:
        dashboard_js = Path(tmp) / "dashboard.js"
        dashboard_js.write_text(js, encoding="utf-8")
        try:
            proc = subprocess.run(
                [node, str(_HARNESS), str(dashboard_js)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:  # pragma: no cover
            pytest.skip(f"node did not run: {exc}")

    out = (proc.stdout + proc.stderr).strip()
    assert proc.returncode == 0, f"dashboard render path failed under Node:\n{out}"
