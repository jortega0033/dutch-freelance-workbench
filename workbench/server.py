"""HTTP layer: loopback server, request checks, routing, static files, headers.

Request pipeline for every request (see _handle):
  Host check -> (mutations: Origin, session token, Content-Type, body, Idempotency-Key)
  -> route -> read query or leads.run_mutation -> JSON response.
All failures are ApiError and become the standard JSON error body.
"""

import hmac
import json
import re
import secrets
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import dates, db, discovery, leads, queries, validation
from .errors import ApiError
from .faults import NoFaults

DEFAULT_STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 65536
DRAIN_LIMIT = 1024 * 1024  # how much of an oversized body we read and discard
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SUPPORTED_METHODS = MUTATING_METHODS | {"GET"}
KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,128}")

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}

PAGE_ROUTES = (
    re.compile(r"/"),
    re.compile(r"/leads"),
    re.compile(r"/leads/new"),
    re.compile(r"/leads/[0-9]{1,18}"),
    re.compile(r"/settings"),
    re.compile(r"/discover"),
)


# --- read endpoints: fn(app, match, query) -> payload dict


def _read_health(app, match, query):
    return {"status": "ok", "schema_version": db.SCHEMA_VERSION}


def _read_session(app, match, query):
    with app.ctx.read() as conn:
        has_profile = queries.has_profile(conn)
    return {
        "token": app.session_token,
        "business_date": app.today(),
        "timezone": dates.TIMEZONE_NAME,
        "has_profile": has_profile,
    }


def _read_profile(app, match, query):
    with app.ctx.read() as conn:
        return {"profile": queries.profile_dict(conn)}


def _read_leads(app, match, query):
    filters = validation.parse_lead_filters(query)
    with app.ctx.read() as conn:
        return queries.list_leads(conn, app.today(), filters)


def _read_lead(app, match, query):
    lead_id = int(match.group(1))
    today = app.today()
    with app.ctx.read() as conn:
        lead = queries.lead_detail(conn, lead_id, today)
        if lead is None:
            raise ApiError(404, "NOT_FOUND", "Lead not found.")
        return {"business_date": today, "lead": lead, "activities": queries.lead_activities(conn, lead_id)}


def _read_catalog(app, match, query):
    catalog = app.ctx.catalog
    filters = discovery.parse_filters(query, catalog)
    with app.ctx.read() as conn:
        return discovery.browse(conn, catalog, filters)


def _read_dashboard(app, match, query):
    with app.ctx.read() as conn:
        return queries.dashboard(conn, app.today())


# (method, path pattern, function, is_write). Writes are commands from leads.py.
ROUTES = (
    ("GET", re.compile(r"/api/v1/health"), _read_health, False),
    ("GET", re.compile(r"/api/v1/session"), _read_session, False),
    ("GET", re.compile(r"/api/v1/profile"), _read_profile, False),
    ("PUT", re.compile(r"/api/v1/profile"), leads.save_profile, True),
    ("GET", re.compile(r"/api/v1/leads"), _read_leads, False),
    ("POST", re.compile(r"/api/v1/leads"), leads.create_lead, True),
    ("GET", re.compile(r"/api/v1/leads/([0-9]{1,18})"), _read_lead, False),
    ("PATCH", re.compile(r"/api/v1/leads/([0-9]{1,18})"), leads.update_lead, True),
    ("POST", re.compile(r"/api/v1/leads/([0-9]{1,18})/activities"), leads.add_activity, True),
    ("PUT", re.compile(r"/api/v1/leads/([0-9]{1,18})/next-action"), leads.set_next_action, True),
    ("POST", re.compile(r"/api/v1/leads/([0-9]{1,18})/stage"), leads.change_stage, True),
    ("GET", re.compile(r"/api/v1/dashboard"), _read_dashboard, False),
    ("GET", re.compile(r"/api/v1/catalog"), _read_catalog, False),
    ("POST", re.compile(r"/api/v1/catalog/items/(cat-[a-z0-9-]{1,60})/lead"), leads.save_catalog_item, True),
)


def _find_route(method, path):
    path_known = False
    for route_method, pattern, function, is_write in ROUTES:
        match = pattern.fullmatch(path)
        if match:
            path_known = True
            if route_method == method:
                return function, is_write, match
    if path_known:
        raise ApiError(405, "METHOD_NOT_ALLOWED", "Method not allowed.")
    raise ApiError(404, "NOT_FOUND", "Not found.")


# --- the server object


class PortUnavailable(OSError):
    """Binding the loopback port failed (usually: already in use)."""

    def __init__(self, port, cause):
        super().__init__(f"could not listen on 127.0.0.1:{port} ({cause.strerror or cause})")
        self.port = port


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # on Windows SO_REUSEADDR would let a second server share the port

    def __init__(self, address, ctx, static_dir, quiet):
        self.ctx = ctx
        self.static_dir = Path(static_dir).resolve()
        self.quiet = quiet
        self.session_token = secrets.token_urlsafe(32)
        self.db_path = ctx.db_path
        super().__init__(address, Handler)

    def today(self):
        """Current Amsterdam business date as 'YYYY-MM-DD'."""
        return dates.business_date(self.ctx.clock.now_utc()).isoformat()

    def handle_error(self, request, client_address):
        if not self.quiet:
            kind = type(sys.exc_info()[1]).__name__
            sys.stderr.write(f"connection error: {kind}\n")


def create_server(data_dir, port, clock=dates.SystemClock(), faults=NoFaults(), quiet=False,
                  busy_timeout_ms=5000, static_dir=DEFAULT_STATIC_DIR,
                  catalog_path=discovery.DEFAULT_CATALOG_PATH):
    """Build (but do not start) the server. Raises db.DatabaseRejected or PortUnavailable.

    Only data_dir and port are reachable from the command line; the other
    arguments exist for tests.
    """
    db_path = db.prepare_database(data_dir)
    ctx = db.Context(db_path, clock, faults, busy_timeout_ms, discovery.Catalog(catalog_path))
    try:
        return WorkbenchServer(("127.0.0.1", port), ctx, static_dir, quiet)
    except OSError as exc:
        raise PortUnavailable(port, exc) from exc


# --- request handler


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DutchFreelanceWorkbench"
    sys_version = ""
    timeout = 30

    def log_message(self, format, *args):  # default access log is replaced by _log_access
        pass

    def do_GET(self):
        self._handle()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_GET

    def send_error(self, code, message=None, explain=None):
        # Used by http.server for unparseable requests and unknown methods.
        status = 405 if code == 501 else code
        names = {405: "METHOD_NOT_ALLOWED", 408: "MALFORMED_REQUEST", 414: "MALFORMED_REQUEST"}
        error = ApiError(status, names.get(status, "MALFORMED_REQUEST"), "The request could not be handled.")
        self._send_json(status, error.to_payload())

    # -- pipeline

    def _handle(self):
        try:
            self._pipeline()
        except ApiError as err:
            self._send_api_error(err)
        except sqlite3.OperationalError as exc:
            if db.is_busy(exc):
                self._send_api_error(db.storage_busy())
            else:
                self._send_internal_error(exc)
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - last line of defence
            self._send_internal_error(exc)

    def _pipeline(self):
        app = self.server
        method = self.command
        parts = urlsplit(self.path)
        path = parts.path
        raw_body, body_error = self._read_raw_body()

        self._check_host()
        if method not in SUPPORTED_METHODS:
            raise ApiError(405, "METHOD_NOT_ALLOWED", "Method not allowed.")

        if method in MUTATING_METHODS:
            self._check_origin()
            self._check_session()
            body = self._parse_json_body(raw_body, body_error)
            key = self._idempotency_key()
            function, is_write, match = _find_route(method, path)
            if not is_write:
                raise ApiError(405, "METHOD_NOT_ALLOWED", "Method not allowed.")
            # the path id: a lead id (digits) or a catalog key ("cat-...")
            target = match.group(1) if match.groups() else None
            if target is not None and target.isdigit():
                target = int(target)
            result = leads.run_mutation(app.ctx, method, path, key, body, function, target)
            headers = {"Idempotent-Replayed": "true"} if result.replayed else {}
            self._send(result.status, result.body_text.encode("utf-8"), "application/json; charset=utf-8", headers)
            return

        if not path.startswith("/api/"):
            self._serve_page_or_static(path)
            return
        function, is_write, match = _find_route(method, path)
        if is_write:
            raise ApiError(405, "METHOD_NOT_ALLOWED", "Method not allowed.")
        try:
            query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=50)
        except ValueError:
            raise ApiError(400, "MALFORMED_REQUEST", "The query string could not be read.") from None
        self._send_json(200, function(app, match, query))

    # -- request checks, in contract order

    def _check_host(self):
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_port
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise ApiError(403, "HOST_NOT_ALLOWED", "This host name is not allowed.")

    def _check_origin(self):
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        if origin is None or origin != f"http://{host}":
            raise ApiError(403, "ORIGIN_NOT_ALLOWED", "This request did not come from this app.")
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            raise ApiError(403, "ORIGIN_NOT_ALLOWED", "This request did not come from this app.")

    def _check_session(self):
        supplied = (self.headers.get("X-Workbench-Session") or "").encode("utf-8")
        expected = self.server.session_token.encode("utf-8")
        if not hmac.compare_digest(supplied, expected):
            raise ApiError(403, "SESSION_INVALID", "The session token is missing or out of date. Reload the page.")

    def _read_raw_body(self):
        """Always consume the declared body so replies are never lost to a connection reset.

        Returns (bytes or None, ApiError or None); the error is raised later, at its
        place in the check order.
        """
        if self.headers.get("Transfer-Encoding") is not None:
            self.close_connection = True
            return None, ApiError(411, "LENGTH_REQUIRED", "Send a Content-Length header.")
        length_text = self.headers.get("Content-Length")
        if length_text is None:
            return None, ApiError(411, "LENGTH_REQUIRED", "Send a Content-Length header.")
        if not (length_text.isascii() and length_text.isdigit()):
            self.close_connection = True
            return None, ApiError(400, "MALFORMED_REQUEST", "The Content-Length header is not valid.")
        length = int(length_text)
        if length > MAX_BODY_BYTES:
            remaining = min(length, DRAIN_LIMIT)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
            self.close_connection = True
            return None, ApiError(413, "BODY_TOO_LARGE", f"The request body is limited to {MAX_BODY_BYTES} bytes.")
        data = self.rfile.read(length)
        if len(data) < length:
            self.close_connection = True
            return None, ApiError(400, "MALFORMED_REQUEST", "The request body was incomplete.")
        return data, None

    def _parse_json_body(self, raw_body, body_error):
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Send the body as application/json.")
        if body_error is not None:
            raise body_error
        try:
            body = json.loads(raw_body.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                              parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            raise ApiError(400, "MALFORMED_REQUEST", "The request body is not valid JSON.") from None
        if not isinstance(body, dict):
            raise ApiError(400, "MALFORMED_REQUEST", "The request body must be a JSON object.")
        return body

    def _idempotency_key(self):
        key = self.headers.get("Idempotency-Key")
        if key is None or not KEY_PATTERN.fullmatch(key):
            raise ApiError(
                400, "IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key of 16-128 letters, digits, - or _."
            )
        return key

    # -- static pages and files

    def _serve_page_or_static(self, path):
        static_dir = self.server.static_dir
        if any(pattern.fullmatch(path) for pattern in PAGE_ROUTES):
            target = static_dir / "index.html"
        elif path.startswith("/static/"):
            target = self._resolve_static(static_dir, path[len("/static/"):])
        else:
            target = None
        if target is None or not target.is_file():
            raise ApiError(404, "NOT_FOUND", "Not found.")
        content_type = CONTENT_TYPES.get(target.suffix.lower())
        if content_type is None:
            raise ApiError(404, "NOT_FOUND", "Not found.")
        self._send(200, target.read_bytes(), content_type)

    @staticmethod
    def _resolve_static(static_dir, relative):
        try:
            candidate = (static_dir / unquote(relative)).resolve()
            if candidate.is_relative_to(static_dir):
                return candidate
        except (ValueError, OSError):
            pass
        return None

    # -- responses

    def _send_json(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _send_api_error(self, err):
        is_page = self.command == "GET" and not self._request_path().startswith("/api/")
        if is_page and err.status in (404, 405):
            page = b"<!doctype html><meta charset=utf-8><title>Not found</title><p>Not found.</p>"
            self._send(err.status, page, "text/html; charset=utf-8", err.headers)
        else:
            self._send_json(err.status, err.to_payload(), err.headers)

    def _send_internal_error(self, exc):
        if not self.server.quiet:
            sys.stderr.write(f"internal error: {type(exc).__name__}\n")
        err = ApiError(500, "INTERNAL_ERROR", "Something went wrong. Nothing was saved.")
        self._send_api_error(err)

    def _send(self, status, body, content_type, headers=None):
        self._log_access(status)
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass
        self.close_connection = True

    def _request_path(self):
        try:
            return urlsplit(getattr(self, "path", "-")).path
        except ValueError:
            return "-"

    def _log_access(self, status):
        """METHOD PATH STATUS only: never query strings, headers, bodies or labels."""
        if self.server.quiet:
            return
        path = self._request_path()[:200]
        path = "".join(ch if ch.isprintable() else "?" for ch in path)
        sys.stderr.write(f"{self.command or '-'} {path} {status}\n")


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(name):
    raise ValueError("NaN and Infinity are not valid JSON")
