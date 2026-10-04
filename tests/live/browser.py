"""Screenshots of the real interface, against the real backend.

``demo.md`` asks for the demo to be driven in a browser, and the acceptance asks for desktop
and mobile shots in which the interface does not overlap. Both are properties of what the page
actually renders, so they are measured on the rendered page rather than asserted about the
stylesheet — a rule that says the columns are 260px and 340px is not evidence about a 390px
viewport.

The browser talks to the *dev server*, which proxies ``/api`` to this round's backend. That is
the same path a person uses, and it is also the only one available: the app does not serve the
frontend, and the frontend's API base is a relative path.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.request import urlopen

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
DEV_SERVER_PORT = 3000
DEV_SERVER_URL = f"http://127.0.0.1:{DEV_SERVER_PORT}"
STARTUP_TIMEOUT_SECONDS = 180

#: The two viewports the acceptance names. They straddle the stylesheet's own 900px breakpoint
#: rather than being two arbitrary widths, so one of them exercises the single-column layout.
VIEWPORTS: tuple[tuple[str, int, int], ...] = (
    ("desktop", 1440, 900),
    ("mobile", 390, 844),
)

#: What "does not overlap" is measured on. The topbar and the workspace are the page's two
#: stacked regions, and the workspace's own children are the columns — so this measures the
#: layout the page produced rather than class names a refactor could rename without changing
#: anything visible.
MEASURE_JS = """
() => {
  const pick = (element) => {
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      tag: element.tagName.toLowerCase(),
      cls: element.className || '',
      x: box.x, y: box.y, width: box.width, height: box.height,
      visible:
        style.display !== 'none' &&
        style.visibility !== 'hidden' &&
        box.width > 0 && box.height > 0,
    };
  };
  const regions = [];
  const topbar = document.querySelector('header.topbar');
  if (topbar) regions.push({name: 'topbar', ...pick(topbar)});
  const workspace = document.querySelector('main.workspace');
  if (workspace) {
    regions.push({name: 'workspace', ...pick(workspace)});
    [...workspace.children].forEach((child, index) =>
      regions.push({name: `workspace>${index}:${child.className || child.tagName}`,
                    ...pick(child)}));
  }
  const root = document.documentElement;
  return {
    regions,
    overflowX: root.scrollWidth - root.clientWidth,
    overflowY: root.scrollHeight - root.clientHeight,
    title: document.title,
  };
}
"""


@contextlib.contextmanager
def running_dev_server(*, backend: str) -> Iterator[str]:
    """Start Vite with ``/api`` proxied to one backend, and stop it again."""
    env = {**os.environ, "VITE_BACKEND": backend}
    npm = "npm.cmd" if os.name == "nt" else "npm"
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [npm, "run", "dev", "--", "--host", "127.0.0.1"],
        cwd=str(FRONTEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        ready = False
        while time.monotonic() < deadline and not ready:
            if process.poll() is not None:
                output = (process.stdout.read() or b"").decode("utf-8", "replace")
                raise RuntimeError(f"vite exited with {process.returncode}: {output[-800:]}")
            try:
                with urlopen(DEV_SERVER_URL, timeout=2) as response:  # noqa: S310 - fixed local URL
                    ready = response.status == 200
            except OSError:
                time.sleep(0.5)
        if not ready:
            raise RuntimeError(f"the dev server did not answer on {DEV_SERVER_URL} in time")
        yield DEV_SERVER_URL
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()


def capture(*, base_url: str, out_dir: Path, settle_ms: int = 2500) -> dict[str, Any]:
    """Screenshot each viewport and check the layout it produced.

    Returns a record rather than raising on the first problem: a round wants to know which
    viewport overlapped, not merely that one did.
    """
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {"viewports": [], "problems": []}

    with sync_playwright() as play:
        browser = play.chromium.launch()
        try:
            for name, width, height in VIEWPORTS:
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(base_url, wait_until="domcontentloaded")
                _sign_in(page)
                page.wait_for_timeout(settle_ms)
                path = out_dir / f"{name}.png"
                page.screenshot(path=str(path))

                measured = page.evaluate(MEASURE_JS)
                entry = _judge(measured, name=name, width=width, height=height, path=path)
                record["viewports"].append(entry)
                record["problems"].extend(f"{name}: {item}" for item in entry["problems"])
                page.close()
        finally:
            browser.close()

    record["ok"] = not record["problems"]
    return record


def _sign_in(page: Any) -> None:  # noqa: ANN401 - a Playwright Page
    """Pick a demo account, which is what makes the workspace render at all.

    The interface refuses to auto-login on purpose, so until somebody chooses, the page shows
    the gate and there is no layout to photograph. Clicking it is the same act a person
    performs.

    The gate is *waited for* rather than assumed. ``goto(wait_until="domcontentloaded")``
    returns as soon as the HTML is parsed, which on the first viewport — while the dev server
    is still compiling on demand — is a page with no chips in it yet. Signing in was then
    silently skipped (``if chips``), and the desktop viewport was photographed as an empty
    page and reported as "no workspace rendered": a layout failure conjured by a race.
    """
    page.wait_for_selector(".chip, main.workspace", timeout=30000)
    chips = page.query_selector_all(".chip")
    if chips:
        chips[0].click()
    page.wait_for_selector("main.workspace", timeout=30000)


def _judge(
    measured: dict[str, Any], *, name: str, width: int, height: int, path: Path
) -> dict[str, Any]:
    """Turn one viewport's measurements into a verdict."""
    problems: list[str] = []
    regions = [item for item in (measured.get("regions") or []) if item.get("visible")]

    names = {str(item.get("name")) for item in regions}
    if "workspace" not in names:
        problems.append("没有渲染出 main.workspace；截图里没有工作区")

    # Horizontal overflow is the symptom mobile overlap actually shows: a fixed-width column
    # that does not fit pushes the page wider than the viewport, and the parts that spill are
    # exactly the ones drawn over each other.
    overflow = int(measured.get("overflowX") or 0)
    if overflow > 1:
        problems.append(f"页面横向溢出 {overflow}px（视口 {width}px）")

    columns = [item for item in regions if str(item.get("name", "")).startswith("workspace>")]
    for index, first in enumerate(columns):
        for second in columns[index + 1 :]:
            overlap = _overlap(first, second)
            if overlap[0] > 1 and overlap[1] > 1:
                problems.append(
                    f"{first['name']} 与 {second['name']} 重叠 "
                    f"{overlap[0]:.0f}×{overlap[1]:.0f}px"
                )

    topbar = next((item for item in regions if item.get("name") == "topbar"), None)
    workspace = next((item for item in regions if item.get("name") == "workspace"), None)
    if topbar and workspace:
        overlap = _overlap(topbar, workspace)
        if overlap[0] > 1 and overlap[1] > 1:
            problems.append(
                f"顶栏与工作区重叠 {overlap[0]:.0f}×{overlap[1]:.0f}px"
            )

    size = path.stat().st_size if path.is_file() else 0
    if size <= 0:
        problems.append("截图文件是空的")

    return {
        "viewport": name,
        "width": width,
        "height": height,
        "screenshot": path.name,
        "bytes": size,
        "regions": [
            {"name": item["name"], "x": round(item["x"]), "y": round(item["y"]),
             "width": round(item["width"]), "height": round(item["height"])}
            for item in regions
        ],
        "overflow_x": overflow,
        "problems": problems,
        "ok": not problems,
    }


def _overlap(first: dict[str, Any], second: dict[str, Any]) -> tuple[float, float]:
    """Width and height of the intersection, or zeroes when they are apart."""
    left = max(float(first["x"]), float(second["x"]))
    right = min(float(first["x"]) + float(first["width"]), float(second["x"]) + float(second["width"]))
    top = max(float(first["y"]), float(second["y"]))
    bottom = min(
        float(first["y"]) + float(first["height"]), float(second["y"]) + float(second["height"])
    )
    return (max(0.0, right - left), max(0.0, bottom - top))


__all__ = ["VIEWPORTS", "capture", "running_dev_server"]
