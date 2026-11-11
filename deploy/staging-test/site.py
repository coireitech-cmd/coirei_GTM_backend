"""TEST-ONLY company site for the staging smoke test (deploy/staging-test).

Serves a small synthetic company, Acme Staging Inc, with stated facts (name,
founding year, headquarters, a price) on a few pages plus ``PAGES`` filler
blog pages, so a crawl lasts long enough to restart a worker mid-job.
Standard library only; runs inside the crawler image.
"""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGES = int(os.environ.get("SITE_PAGES", "40"))


def page(title: str, body: str) -> bytes:
    return (f"<html><head><title>{title}</title>"
            '<meta property="og:site_name" content="Acme Staging">'
            f"</head><body>{body}</body></html>").encode()  # fmt: skip


def links() -> str:
    blog = "".join(f'<a href="/blog/{i}">Post {i}</a> ' for i in range(1, PAGES + 1))
    return f'<nav><a href="/about">About us</a> <a href="/pricing">Pricing</a> {blog}</nav>'


ROUTES = {
    "/robots.txt": ("text/plain", b"User-agent: *\nAllow: /\n"),
    "/": ("text/html", page("Acme Staging", links() + "<p>Acme Staging builds inventory "
                            "software for independent retailers.</p>")),
    "/about": ("text/html", page("About Acme Staging", "<p>Acme Staging Inc was founded in "
                                 "2016. Acme Staging is headquartered in Austin, Texas.</p>")),
    "/pricing": ("text/html", page("Pricing", "<h2>Starter</h2><p>Starter $29 per month.</p>"
                                   "<h2>Growth</h2><p>Growth $99 per month.</p>")),
    **{f"/blog/{i}": ("text/html", page(f"Post {i}", f"<p>Stock counting tip number {i}.</p>"))
       for i in range(1, PAGES + 1)},
}  # fmt: skip


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        content_type, body = ROUTES.get(self.path, ("text/plain", b""))
        self.send_response(200 if self.path in ROUTES else 404)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()  # noqa: S104 - test network
