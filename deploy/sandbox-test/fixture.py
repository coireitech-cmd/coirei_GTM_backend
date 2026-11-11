"""TEST-ONLY fixture for deploy/sandbox-test: a logging DNS server and a web site.

DNS (UDP 53): answers every ``*.test`` A query with this container's address,
everything else with NXDOMAIN, and prints ``dns <name> <type>`` for each query,
so run.sh can see every name anything in the probe container looked up.

HTTP (80): pages keyed by Host header and path. ``acme.test/`` is a thin
JavaScript page whose script comes from ``cdn-ok.test`` (a request the crawler
performs, so the guard resolves that name) and which tries every other way a
browser reaches the network on ``*-leak.test`` names: images, styles, fonts,
DNS prefetch, preconnect, beacons, WebSockets, WebRTC/STUN. None of those
names may ever be queried. ``/heavy`` builds a large DOM (memory measurement);
``/bomb`` grows it without end (memory-limit test). Standard library only.
"""

import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LEAK = """
<link rel="dns-prefetch" href="//prefetch-leak.test">
<link rel="preconnect" href="http://preconnect-leak.test">
<link rel="stylesheet" href="http://css-leak.test/s.css">
<style>@font-face{font-family:x;src:url(http://font-leak.test/f.woff)}
body{font-family:x;background:url(http://bg-leak.test/b.png)}</style>
<img src="http://img-leak.test/i.png">
<script>
navigator.sendBeacon("http://beacon-leak.test/b", "x");
try { new WebSocket("ws://ws-leak.test/s"); } catch (e) {}
try {
  const pc = new RTCPeerConnection({iceServers: [{urls: "stun:stun-leak.test:3478"}]});
  pc.createDataChannel("d");
  pc.createOffer().then(o => pc.setLocalDescription(o));
} catch (e) {}
fetch("/slow").then(r => r.text()).then(t => document.body.append(t));
</script>
"""
ROOT = f"""<html><head><title>Acme</title>{LEAK}</head><body><div id=app></div>
<script src="http://cdn-ok.test/app.js"></script></body></html>"""
APP = ("document.getElementById('app').innerHTML = '<h1>Acme Sandbox Inc</h1>' + "
       "'<p>Acme Sandbox Inc builds rendered pages. </p>'.repeat(20);")  # fmt: skip
HEAVY = """<html><head><title>Heavy</title><script>
const box = document.createElement('div');
for (let i = 0; i < 200000; i++) { const d = document.createElement('div');
  d.textContent = 'row ' + i + ' of a heavy rendered page'; box.append(d); }
document.addEventListener('DOMContentLoaded', () => document.body.append(box));
</script></head><body></body></html>"""
BOMB = """<html><head><title>Bomb</title><script>
const keep = [];
(function grow() { const box = document.createElement('div');
  for (let i = 0; i < 50000; i++) { const d = document.createElement('div');
    d.textContent = 'x'.repeat(200) + i; box.append(d); }
  keep.push(box); document.documentElement.append(box); setTimeout(grow, 0); })();
</script></head><body></body></html>"""
ROBOTS = "User-agent: *\nAllow: /\n"
ROUTES = {
    ("acme.test", "/"): ("text/html", ROOT),
    ("acme.test", "/heavy"): ("text/html", HEAVY),
    ("acme.test", "/heavy?2"): ("text/html", HEAVY),
    ("acme.test", "/bomb"): ("text/html", BOMB),
    ("acme.test", "/robots.txt"): ("text/plain", ROBOTS),
    ("cdn-ok.test", "/robots.txt"): ("text/plain", ROBOTS),
    ("cdn-ok.test", "/app.js"): ("application/javascript", APP),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        host = (self.headers.get("Host") or "").split(":")[0]
        print(f"http {host}{self.path}", flush=True)
        if self.path == "/slow":  # keeps the page busy while the probe looks at /proc
            time.sleep(2)
            route = ("text/plain", "<p>Loaded late.</p>")
        else:
            route = ROUTES.get((host, self.path), ("text/plain", ""))
        body = route[1].encode()
        self.send_response(200 if route[1] else 404)
        self.send_header("Content-Type", route[0])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def serve_dns(address: bytes) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 53))  # noqa: S104 - test network
    while True:
        query, peer = sock.recvfrom(512)
        labels, i = [], 12
        while query[i]:
            labels.append(query[i + 1 : i + 1 + query[i]].decode("ascii", "replace"))
            i += query[i] + 1
        name = ".".join(labels).lower()
        qtype = struct.unpack("!H", query[i + 1 : i + 3])[0]
        print(f"dns {name} {qtype}", flush=True)
        question = query[12 : i + 5]
        found = name.endswith(".test")
        answer = b""
        if found and qtype == 1:  # A
            answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 5, 4) + address
        flags = 0x8180 if found else 0x8183  # response, recursion available; NXDOMAIN
        header = query[:2] + struct.pack("!HHHHH", flags, 1, 1 if answer else 0, 0, 0)
        sock.sendto(header + question + answer, peer)


if __name__ == "__main__":
    me = socket.inet_aton(socket.gethostbyname(socket.gethostname()))
    threading.Thread(target=serve_dns, args=(me,), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()  # noqa: S104 - test network
