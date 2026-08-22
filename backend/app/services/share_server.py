"""A second, deliberately tiny listener that serves signed share links to the container.

The Distribute engine attaches an image by *fetching* it, and it runs in a Docker container
inside the WSL2 VM. The only copy of a generated image is on the Windows host, behind a
backend bound to 127.0.0.1 — which the container cannot reach, because loopback there is the
container's own. Measured, rather than assumed:

  * From inside WSL, the Windows host answers on the WSL adapter's address (the VM's default
    gateway — 172.30.240.1 on this machine, assigned per boot, so it must be injected rather
    than hardcoded).
  * A Windows port bound to 0.0.0.0 IS reachable there, with no firewall rule needed.
  * The backend on 127.0.0.1:8756 is not, exactly as expected.

So the fix is a bind, and the question is how wide to make it. `--host 0.0.0.0` on the main
API would work and is the obvious move; it also publishes every route in this app to the
local network, where the session token is the only thing standing in front of them. That is
a large change to the app's exposure to fix one image fetch.

Instead this serves ONE route — the signed, expiring share link — bound to the WSL adapter
address alone. That adapter is a host-only virtual network for the VM: traffic from the
actual network arrives on a different interface and never reaches this socket. The main API
keeps its 127.0.0.1 bind and its token unchanged.

It listens on the SAME port as the main API on purpose. Two sockets on two addresses do not
collide, and it means the URL the engine fetches differs only in host, so `share_links.url_for`
and the engine's base URL need no special case.

Off unless MRAIM_SHARE_HOST is set. Anyone not using Distribute opens no socket at all.
"""

from __future__ import annotations

import logging
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

log = logging.getLogger(__name__)

_server: ThreadingHTTPServer | None = None

#: Only this prefix is served. Everything else is a flat 404 with no hint of what exists.
_PREFIX = "/shared/"


class _Handler(BaseHTTPRequestHandler):
    # Named so a stray request cannot fingerprint the app from the banner alone.
    server_version = "MrAIMarketerShare"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
        from . import share_links

        path = urlparse(self.path).path
        if not path.startswith(_PREFIX):
            self.send_error(404)
            return

        resolved = share_links.resolve(unquote(path[len(_PREFIX):]))
        if resolved is None:
            # One answer for a forged signature, an expired link and a missing file alike.
            # Distinguishing them would tell a caller which part of a guess was right.
            self.send_error(404)
            return

        try:
            body = resolved.read_bytes()
        except OSError:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(resolved.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        """Read-only, and explicitly so — the default would 501, which is a subtler no."""
        self.send_error(405)

    def log_message(self, fmt: str, *args) -> None:
        log.debug("[share-server] " + fmt, *args)


def start(host: str, port: int) -> bool:
    """Serve signed links on `host`. Returns False if it could not bind.

    A failure here must not stop the app: the only thing lost is attaching a locally
    generated image in Distribute, and every other feature is unaffected. The WSL adapter
    also simply may not exist yet on a machine where WSL has never started.
    """
    global _server
    if _server is not None:
        current_host, current_port = _server.server_address[:2]
        if current_host == host and (port == 0 or current_port == port):
            return True
        # The WSL adapter address is reassigned when its VM restarts. Re-announcing a
        # different address must move the listener rather than report success while it
        # remains pinned to an interface the container can no longer reach.
        stop()
    try:
        _server = ThreadingHTTPServer((host, port), _Handler)
    except OSError as err:
        log.warning("[share-server] could not bind %s:%s — %s", host, port, err)
        return False

    threading.Thread(target=_server.serve_forever, daemon=True, name="share-server").start()
    log.info("[share-server] serving signed links on %s:%s", host, port)
    return True


def stop() -> None:
    global _server
    if _server is not None:
        server = _server
        _server = None
        server.shutdown()
        server.server_close()


def is_listening() -> bool:
    """Whether the signed-link listener currently owns a socket."""
    return _server is not None
