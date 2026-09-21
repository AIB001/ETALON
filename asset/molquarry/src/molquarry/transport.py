"""Standard-library HTTPS transport for ClinicalTrials.gov client interoperability.

This is a fixed transport choice for that public API, not a retry/fallback after a 403.
It keeps normal certificate verification, environment proxies and HTTP error statuses.
"""

import http.client
import urllib.error
import urllib.request

import httpx


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class StdlibTransport(httpx.BaseTransport):
    def __init__(self):
        # Keep urllib's native verified HTTPS context and ALPN defaults.
        self.opener = urllib.request.build_opener(NoRedirect())

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise httpx.UnsupportedProtocol("The clinical API transport only supports GET")
        headers = dict(request.headers)
        headers["accept-encoding"] = "identity"
        req = urllib.request.Request(str(request.url), headers=headers, method="GET")
        timeout = request.extensions.get("timeout", {}).get("read") or 30
        try:
            try:
                response = self.opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return httpx.Response(
                    response.code, headers=list(response.headers.items()), content=response.read()
                )
        except (TimeoutError, urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            reason = getattr(exc, "reason", exc)
            cls = httpx.ReadTimeout if isinstance(reason, TimeoutError) else httpx.ConnectError
            raise cls("ClinicalTrials.gov transport error", request=request) from exc
