"""Client for the GTM crawler API (``gtm-api``): every endpoint, in one file.

Copy this file into the backend; it needs only ``httpx``. Settings come from
the environment unless given to ``ScraperClient``:

* ``SCRAPER_URL``: where gtm-api listens (default ``http://127.0.0.1:8001``).
* ``SCRAPER_TOKEN``: the bearer token, if the API has ``api.token`` set.

Typical use::

    from scraper_client import ScraperClient

    with ScraperClient() as scraper:
        result = scraper.crawl_to_json("https://example.com", "example.json")

From a shell: ``python scraper_client.py https://example.com [out.json]``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8001"
# A job in one of these states is not being worked on: stop polling.
SETTLED = {"COMPLETED", "FAILED", "CANCELLED", "PAUSED"}


class ScraperError(RuntimeError):
    """The API answered with an error; ``status`` is the HTTP status code."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"scraper API {status}: {detail}")
        self.status = status


class ScraperClient:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 **httpx_options: Any) -> None:  # fmt: skip
        token = token or os.environ.get("SCRAPER_TOKEN", "")
        httpx_options.setdefault("timeout", 30.0)
        self._http = httpx.Client(
            base_url=base_url or os.environ.get("SCRAPER_URL", DEFAULT_URL),
            headers={"Authorization": f"Bearer {token}"} if token else {},
            **httpx_options,
        )

    def __enter__(self) -> ScraperClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ScraperError(response.status_code, str(detail))
        return response.json()

    # --- probes ---

    def ready(self) -> bool:
        """True if the API is up and its database is ready."""
        try:
            return bool(self._call("GET", "/readyz")["status"] == "ok")
        except (ScraperError, httpx.HTTPError):
            return False

    # --- crawls ---

    def start_crawl(self, root_url: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Queue a crawl. Returns the job: note ``crawl_id`` and ``company_id``."""
        body = {"root_url": root_url}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        job: dict[str, Any] = self._call("POST", "/v1/crawls", json=body)
        return job

    def get_crawl(self, crawl_id: int) -> dict[str, Any]:
        job: dict[str, Any] = self._call("GET", f"/v1/crawls/{crawl_id}")
        return job

    def pause_crawl(self, crawl_id: int) -> dict[str, Any]:
        job: dict[str, Any] = self._call("POST", f"/v1/crawls/{crawl_id}/pause")
        return job

    def resume_crawl(self, crawl_id: int) -> dict[str, Any]:
        job: dict[str, Any] = self._call("POST", f"/v1/crawls/{crawl_id}/resume")
        return job

    def cancel_crawl(self, crawl_id: int) -> dict[str, Any]:
        job: dict[str, Any] = self._call("POST", f"/v1/crawls/{crawl_id}/cancel")
        return job

    def get_quality(self, crawl_id: int) -> dict[str, Any]:
        report: dict[str, Any] = self._call("GET", f"/v1/crawls/{crawl_id}/quality")
        return report

    # --- companies ---

    def get_dossier(self, company_id: int, clean: bool = True) -> dict[str, Any]:
        """The company's dossier: plain values, or with ``clean=False`` the full
        dossier (every field's state, confidence and citations)."""
        dossier: dict[str, Any] = self._call(
            "GET", f"/v1/companies/{company_id}/dossier", params={"clean": clean}
        )
        return dossier

    def get_cost(self, company_id: int) -> dict[str, Any]:
        cost: dict[str, Any] = self._call("GET", f"/v1/companies/{company_id}/cost")
        return cost

    def get_lineage(self, company_id: int, field: str, product_id: int | None = None) -> Any:
        params = {} if product_id is None else {"product_id": product_id}
        return self._call("GET", f"/v1/companies/{company_id}/lineage/{field}", params=params)

    # --- dead letters ---

    def list_dead_letters(self, include_resolved: bool = False,
                          limit: int = 100) -> list[dict[str, Any]]:  # fmt: skip
        params = {"include_resolved": include_resolved, "limit": limit}
        items: list[dict[str, Any]] = self._call("GET", "/v1/dead-letters", params=params)["items"]
        return items

    def requeue_dead_letter(self, item_id: int) -> dict[str, Any]:
        item: dict[str, Any] = self._call("POST", f"/v1/dead-letters/{item_id}/requeue")
        return item

    def discard_dead_letter(self, item_id: int) -> dict[str, Any]:
        item: dict[str, Any] = self._call("POST", f"/v1/dead-letters/{item_id}/discard")
        return item

    # --- the whole flow ---

    def wait(self, crawl_id: int, poll_seconds: float = 10.0,
             timeout_seconds: float = 3600.0) -> dict[str, Any]:  # fmt: skip
        """Poll the job until it is COMPLETED, FAILED, CANCELLED or PAUSED."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = self.get_crawl(crawl_id)
            if job["status"] in SETTLED:
                return job
            if time.monotonic() >= deadline:
                raise TimeoutError(f"crawl {crawl_id} still {job['status']}")
            time.sleep(poll_seconds)

    def crawl(self, root_url: str, **wait_options: float) -> dict[str, Any]:
        """Start a crawl, wait for it and return ``{"crawl": job, "dossier": dossier}``.

        The dossier (plain values, see ``get_dossier``) is returned whatever the job's
        final status: it holds what was assessed so far. Check ``result["crawl"]["status"]``.
        """
        job = self.wait(self.start_crawl(root_url)["crawl_id"], **wait_options)
        crawl = {k: job[k] for k in ("crawl_id", "company_id", "root_url", "status",
                                     "started_at", "finished_at", "error_summary")}  # fmt: skip
        return {"crawl": crawl, "dossier": self.get_dossier(job["company_id"])}

    def crawl_to_json(self, root_url: str, out_path: str | Path,
                      **wait_options: float) -> dict[str, Any]:  # fmt: skip
        """``crawl``, with the result also written to ``out_path`` as JSON."""
        result = self.crawl(root_url, **wait_options)
        Path(out_path).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
        )
        return result


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not 1 <= len(args) <= 2:
        print("usage: python scraper_client.py <url> [out.json]", file=sys.stderr)
        return 2
    out = args[1] if len(args) == 2 else "dossier.json"
    try:
        with ScraperClient() as scraper:
            status = scraper.crawl_to_json(args[0], out)["crawl"]["status"]
    except (ScraperError, httpx.HTTPError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{status}: {out}")
    return 0 if status == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
