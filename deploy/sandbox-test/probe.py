"""TEST-ONLY probe for deploy/sandbox-test, run inside the crawler image.

Renders fixture pages through the crawler's own code path (HttpFetcher fetch,
then BrowserFetcher.render with the guard and interception) and prints one
JSON report:

* ``render``: the JavaScript page, then /heavy twice at once, with ``browser.sandbox`` on (and
  the JavaScript page once with it off, for contrast); for every Chromium
  renderer process seen while rendering, its seccomp mode, NoNewPrivs and
  user/pid/net namespaces next to the probe's own; the container's peak
  memory (cgroup ``memory.peak``).
* ``oom``: /bomb (DOM grows without end) then the JavaScript page again, to
  show what a memory limit does to a render and to the worker process.
* ``control``: the JavaScript page in plain Playwright Chromium (no proxy, no
  interception), which must look up the leak names: the DNS log works.

Exits non-zero when a sandbox check fails; run.sh checks DNS and the rest.
"""

import asyncio
import glob
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from gtm_crawler.config import load_settings
from gtm_crawler.fetch.browser import BrowserFetcher
from gtm_crawler.fetch.http import HttpFetcher
from gtm_crawler.robots import RobotsCache
from gtm_crawler.throttle import HostThrottle

SITE = "http://acme.test"
FACTS = ("Seccomp", "Seccomp_filters", "NoNewPrivs", "NSpid")


def proc_facts(pid: str) -> dict[str, Any]:
    status = Path(f"/proc/{pid}/status").read_text().splitlines()
    facts: dict[str, Any] = {k: v.strip() for k, _, v in (line.partition(":") for line in status)
                             if k in FACTS}  # fmt: skip
    facts["ns"] = {n: os.readlink(f"/proc/{pid}/ns/{n}") for n in ("user", "pid", "net")}
    facts["oom_score_adj"] = int(Path(f"/proc/{pid}/oom_score_adj").read_text())
    return facts


def renderers() -> dict[str, dict[str, Any]]:
    found = {}
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        pid = path.split("/")[2]
        try:
            if b"--type=renderer" in Path(path).read_bytes():
                found[pid] = proc_facts(pid)
        except OSError:
            continue  # exited meanwhile
    return found


CGROUP = Path("/sys/fs/cgroup")
ANON_PEAK = [0]  # highest anonymous (non-page-cache) memory sampled during renders


def cgroup_stat(name: str) -> dict[str, int]:
    lines = (CGROUP / name).read_text().splitlines()
    return {k: int(v) for k, v in (line.split() for line in lines)}


def memory() -> dict[str, Any]:
    """The container's cgroup: peak usage (what the limit counts, page cache
    included), sampled peak of anonymous memory, OOM kills."""
    try:
        peak = int((CGROUP / "memory.peak").read_text())
        oom_kill = cgroup_stat("memory.events")["oom_kill"]
    except OSError:
        return {}
    return {"peak_mb": round(peak / 2**20, 1), "anon_peak_mb": round(ANON_PEAK[0] / 2**20, 1),
            "oom_kill": oom_kill}  # fmt: skip


async def control() -> dict[str, Any]:
    """Plain Playwright Chromium, none of the crawler's containment: it should
    look up the leak names itself, which shows the DNS log would catch a leak."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(f"{SITE}/", wait_until="domcontentloaded", timeout=10_000)
            await asyncio.sleep(3)
        finally:
            await browser.close()
    return {"control": "done"}


async def render(browser: BrowserFetcher, fetcher: HttpFetcher, url: str) -> dict[str, Any]:
    fetched = await fetcher.fetch(url)
    task = asyncio.create_task(browser.render(fetched))
    seen: dict[str, dict[str, Any]] = {}
    while not task.done():
        seen.update(renderers())
        try:
            ANON_PEAK[0] = max(ANON_PEAK[0], cgroup_stat("memory.stat")["anon"])
        except OSError:
            pass  # no cgroup v2 here
        await asyncio.sleep(0.1)
    result = task.result()
    text = (result.body or b"").decode("utf-8", "replace")
    return {"url": url, "error": result.error_detail, "bytes": len(result.body or b""),
            "rendered_text": "Acme Sandbox Inc" in text, "late_text": "Loaded late" in text,
            "renderers": list(seen.values())}  # fmt: skip


async def main(mode: str) -> int:
    settings = load_settings()
    report: dict[str, Any] = {"mode": mode, "self": proc_facts("self")}
    if mode == "control":
        print(json.dumps(report | await control()))
        return 0
    async with HttpFetcher(settings.fetch, settings.identity, settings.urls) as fetcher:
        robots = RobotsCache(fetcher, settings.robots, settings.identity.product_token)

        def browser_with(**changes: Any) -> BrowserFetcher:
            return BrowserFetcher(replace(settings.browser, **changes), settings.urls, fetcher,
                                  robots, HostThrottle(2, 0.0))  # fmt: skip

        browser = browser_with()
        try:
            if mode == "oom":
                report["bomb"] = await render(browser, fetcher, f"{SITE}/bomb")
                report["after"] = await render(browser, fetcher, f"{SITE}/")
            else:
                report["sandboxed"] = await render(browser, fetcher, f"{SITE}/")
                # Two at once: browser.concurrency's default, the worst case per worker.
                report["heavy"], report["heavy2"] = await asyncio.gather(
                    render(browser, fetcher, f"{SITE}/heavy"),
                    render(browser, fetcher, f"{SITE}/heavy?2"),
                )
        finally:
            await browser.aclose()
        if mode != "oom":
            plain = browser_with(sandbox=False, allow_unsandboxed=True)
            try:
                report["unsandboxed"] = await render(plain, fetcher, f"{SITE}/")
            finally:
                await plain.aclose()
    report["memory"] = memory()
    print(json.dumps(report, indent=1))
    return check_oom(report) if mode == "oom" else check(report)


def check(report: dict[str, Any]) -> int:
    me = report["self"]["ns"]
    failures = []
    for name in ("sandboxed", "heavy", "heavy2"):
        r = report[name]
        if r["error"] or not r["renderers"]:
            failures.append(f"{name}: render failed or no renderer seen: {r['error']}")
        for facts in r["renderers"]:
            # Chromium's own seccomp-bpf filter comes on top of the container's.
            own_filter = int(facts["Seccomp_filters"]) > int(report["self"]["Seccomp_filters"])
            if facts["Seccomp"] != "2" or not own_filter or facts["NoNewPrivs"] != "1":
                failures.append(f"{name}: renderer without seccomp-bpf/NoNewPrivs: {facts}")
            if any(facts["ns"][n] == me[n] for n in ("user", "pid", "net")):
                failures.append(f"{name}: renderer shares a namespace with the worker: {facts}")
    if not report["sandboxed"]["rendered_text"]:
        failures.append("the page's script did not run (no rendered text)")
    return failed(failures)


def check_oom(report: dict[str, Any]) -> int:
    failures = []
    if not report["bomb"]["error"]:
        failures.append("the DOM bomb rendered")
    if report["after"]["error"] or not report["after"]["rendered_text"]:
        failures.append(f"no render after the OOM kill: {report['after']['error']}")
    if report["memory"].get("oom_kill", 0) < 1:
        failures.append(f"the cgroup OOM killer did not act: {report['memory']}")
    return failed(failures)


def failed(failures: list[str]) -> int:
    for failure in failures:
        print("FAIL", failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "render")))
