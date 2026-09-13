"""Serve the cascade builder so the file it exports lands next to the HTML.

Opened from disk, the builder can only hand its JSON to the browser, and the
browser puts it wherever downloads go.  That is one copy-and-paste per export,
every export, for the whole life of a campaign -- and the alternatives a page
has are all closed: ``showSaveFilePicker`` is unavailable on ``file://`` origins
because they are not secure contexts, and the document's own
``connect-src 'none'`` makes a write-back request impossible, deliberately.

So the fix is not in the page.  ``molcascade generate config --serve`` writes
the same offline document it always wrote, then serves a second render of it --
identical but for a ``connect-src 'self'`` policy and a save endpoint in its
payload -- from the loopback interface, and writes what comes back beside the
HTML.  The file on disk is untouched by this: it stays the artifact that works
with the network cable out, and it carries no token and no endpoint.

What the endpoint accepts is deliberately narrow, because a loopback port is
reachable by every other page in the browser.  The destination is fixed by the
command line, not chosen by the request, so there is no filename to traverse
with; a token that only the served page can read must be echoed in a header
that a cross-origin ``fetch`` cannot set without a preflight this server never
answers; and the body must be a cascade before a byte of it is written.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from molcascade.environment.models import HostEnvironment
from molcascade.io.atomic import atomic_write_bytes
from molcascade.plugins.registry import PluginRegistry
from molcascade.ui.cascade_builder import (
    DEFAULT_CONFIG_FILENAME,
    build_cascade_builder_payload,
    render_cascade_builder,
)

#: The path the served page posts to, and the header it echoes the token in.
SAVE_PATH = "/save"
TOKEN_HEADER = "X-MolCascade-Token"

#: A cascade is a few hundred kilobytes of JSON at the outside.  The cap is here
#: so a stuck or hostile client cannot make the process hold an arbitrary body in
#: memory before any of the checks below have had a chance to reject it.
MAX_BODY_BYTES = 8 * 1024 * 1024

#: Host headers that mean "this machine".  Checked so a remote page that resolves
#: its own name to 127.0.0.1 cannot reach the endpoint by pretending to be local;
#: the token check would stop it anyway, and this stops it one step earlier.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


@dataclass(frozen=True)
class SaveResult:
    """One accepted export: where it went and how large it was."""

    path: Path
    size: int


class BuilderServer:
    """A loopback HTTP server that hands out the builder and takes back its file.

    Started but not serving: the caller decides whether to block in the
    foreground (``serve_forever``) or to run it on a thread, which is what the
    tests do.  ``url`` is complete and ``token`` is final as soon as the object
    exists, so nothing has to wait for the first request to know either.
    """

    def __init__(
        self,
        destination: Path,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        plugins: PluginRegistry | None = None,
        environment: HostEnvironment | None = None,
    ) -> None:
        self.destination = destination
        self.token = secrets.token_urlsafe(32)
        self.saves: list[SaveResult] = []
        payload = build_cascade_builder_payload(plugins=plugins, environment=environment)
        self.document = render_cascade_builder(
            payload,
            save_endpoint={
                "url": SAVE_PATH,
                "token": self.token,
                "header": TOKEN_HEADER,
                # Shown in the page's own status line, so the confirmation names
                # the file the operator is about to pass to ``--config`` rather
                # than a name they still have to go and find.
                "path": str(destination),
            },
        ).encode("utf-8")
        self._server = ThreadingHTTPServer((host, port), _handler_for(self))
        self._server.daemon_threads = True
        # ``BaseServer.shutdown`` waits on an event that only ``serve_forever``
        # ever sets, so calling it on a server that was never started blocks for
        # good.  This says whether there is a loop to stop, which makes
        # ``shutdown`` safe to call on any server this class hands out.
        self._serving = threading.Event()
        bound_host, bound_port = self._server.server_address[:2]
        self.host = str(bound_host)
        self.port = int(bound_port)
        self.url = f"http://{self.host}:{self.port}/"

    def record(self, body: bytes) -> SaveResult:
        """Publish one accepted body and remember it.

        The bytes are written exactly as they arrived rather than re-serialised
        from the parsed object, so the file beside the HTML is the same file the
        Download button would have produced -- indentation, key order and all.
        """

        path = atomic_write_bytes(self.destination, body, overwrite=True)
        result = SaveResult(path=path, size=len(body))
        self.saves.append(result)
        return result

    def serve_forever(self) -> None:
        self._serving.set()
        try:
            self._server.serve_forever()
        finally:
            self._serving.clear()

    def start_background(self) -> threading.Thread:
        # Set before the thread starts, so a caller that stops the server
        # immediately still stops it rather than racing past the flag.
        self._serving.set()
        thread = threading.Thread(target=self.serve_forever, name="molcascade-builder")
        thread.daemon = True
        thread.start()
        return thread

    def shutdown(self) -> None:
        if self._serving.is_set():
            self._server.shutdown()
        self._server.server_close()


def _handler_for(owner: BuilderServer) -> type[BaseHTTPRequestHandler]:
    """A request handler bound to one server, without a module-level global."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "MolCascadeBuilder"
        # The default handler speaks 1.0 and closes every connection; the page is
        # one document and one POST, so this only saves the browser a reconnect.
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            """Silence the per-request line; the command prints its own."""

        # -- checks ----------------------------------------------------------

        def _is_local(self) -> bool:
            host = self.headers.get("Host", "")
            name = host.rsplit(":", 1)[0] if ":" in host and not host.endswith("]") else host
            return name.strip().casefold() in _LOOPBACK_HOSTS

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            return origin.rstrip("/") == owner.url.rstrip("/")

        def _refuse(self, status: HTTPStatus, message: str) -> None:
            # A refusal often leaves an unread body on the socket, and a
            # keep-alive connection would deliver those bytes as the start of the
            # next request.  Nothing is ever worth reusing a refused connection.
            self.close_connection = True
            body = json.dumps({"ok": False, "error": message}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        # -- routes ----------------------------------------------------------

        # do_GET / do_POST are the names http.server dispatches on.
        def do_GET(self) -> None:
            if not self._is_local():
                self._refuse(HTTPStatus.MISDIRECTED_REQUEST, "this server answers loopback only")
                return
            if self.path not in {"/", "/index.html"}:
                self._refuse(HTTPStatus.NOT_FOUND, "this server serves one page")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(owner.document)))
            # The token is in this body.  Keeping it out of every cache the
            # browser and the disk have is cheap and removes a whole question.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(owner.document)

        def do_POST(self) -> None:
            if not self._is_local():
                self._refuse(HTTPStatus.MISDIRECTED_REQUEST, "this server answers loopback only")
                return
            if self.path != SAVE_PATH:
                self._refuse(HTTPStatus.NOT_FOUND, "nothing is posted here")
                return
            if not self._origin_ok():
                self._refuse(HTTPStatus.FORBIDDEN, "that request came from another page")
                return
            presented = self.headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(presented, owner.token):
                self._refuse(HTTPStatus.FORBIDDEN, "wrong or missing builder token")
                return
            media = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().casefold()
            if media != "application/json":
                self._refuse(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send application/json")
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._refuse(HTTPStatus.LENGTH_REQUIRED, "send a Content-Length")
                return
            if length < 0 or length > MAX_BODY_BYTES:
                self._refuse(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "that is not a cascade file")
                return
            body = self.rfile.read(length)
            problem = _rejection(body)
            if problem is not None:
                self._refuse(HTTPStatus.BAD_REQUEST, problem)
                return
            result = owner.record(body)
            print(f"Wrote {result.path} ({result.size} bytes)", flush=True)
            answer = json.dumps({"ok": True, "path": str(result.path)}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

    return Handler


def _rejection(body: bytes) -> str | None:
    """Why this body is not a cascade, or ``None`` if it is one.

    Parsed before anything is written, so a request that is not a cascade cannot
    replace a good file with rubbish.  The check is the same one the browser's
    own *Load config…* applies, which is what keeps a file this server accepted
    from being one the builder would then refuse to open.
    """

    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "that body is not UTF-8 JSON"
    if not isinstance(parsed, dict):
        return "a cascade file is a JSON object"
    if parsed.get("kind") != "cascade":
        return "that is not a MolCascade cascade file"
    return None


def serve_cascade_builder(
    html_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    plugins: PluginRegistry | None = None,
    environment: HostEnvironment | None = None,
) -> BuilderServer:
    """Build a server that writes its exports beside ``html_path``."""

    destination = html_path.expanduser().resolve().parent / DEFAULT_CONFIG_FILENAME
    return BuilderServer(
        destination,
        host=host,
        port=port,
        plugins=plugins,
        environment=environment,
    )


__all__ = [
    "MAX_BODY_BYTES",
    "SAVE_PATH",
    "TOKEN_HEADER",
    "BuilderServer",
    "SaveResult",
    "serve_cascade_builder",
]
