"""Test harness: real HTTP server on a temp database, plus a small client.

- FixedClock / ScriptedFaults are injected through create_server (tests only).
- Client talks to the real handlers over http.client and sets Host, Origin,
  X-Workbench-Session, Content-Type and Idempotency-Key unless told otherwise.
- ServerProcess runs the real `python -m workbench` as a subprocess.
"""

import datetime as dt
import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workbench.server import create_server  # noqa: E402

DEFAULT_NOW = "2030-04-10T10:00:00Z"  # 12:00 in Amsterdam, business date 2030-04-10
DEFAULT = object()  # "use the normal header value"


def parse_utc(text):
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


class FixedClock:
    """A clock that only moves when the test says so."""

    def __init__(self, iso=DEFAULT_NOW):
        self._now = parse_utc(iso)

    def now_utc(self):
        return self._now

    def set(self, iso_or_datetime):
        if isinstance(iso_or_datetime, str):
            iso_or_datetime = parse_utc(iso_or_datetime)
        self._now = iso_or_datetime


class ScriptedFaults:
    """Raises RuntimeError from named fault points while armed."""

    def __init__(self):
        self.armed = {}
        self.hits = []

    def arm(self, point):
        self.armed[point] = True

    def disarm(self, point=None):
        if point is None:
            self.armed.clear()
        else:
            self.armed.pop(point, None)

    def hit(self, point):
        self.hits.append(point)
        if self.armed.get(point):
            raise RuntimeError(f"injected fault at {point}")


class Response:
    def __init__(self, status, headers, raw):
        self.status = status
        self.headers = headers  # lower-case name -> value
        self.raw = raw

    @property
    def text(self):
        return self.raw.decode("utf-8")

    def json(self):
        return json.loads(self.raw.decode("utf-8"))

    @property
    def error_code(self):
        return self.json()["error"]["code"]


def new_key():
    return "key-" + uuid.uuid4().hex


class Client:
    """Minimal HTTP client for one running server."""

    def __init__(self, port, token=None):
        self.port = port
        self.host = f"127.0.0.1:{port}"
        self.token = token if token is not None else self._fetch_token()

    def _fetch_token(self):
        return self.request("GET", "/api/v1/session").json()["token"]

    def request(self, method, path, body=None, headers=None):
        """Low level. `headers` overrides defaults; a None value removes a header."""
        send = {"Host": self.host}
        if headers:
            send.update(headers)
        send = {name: value for name, value in send.items() if value is not None}
        payload = body
        if isinstance(body, (dict, list)):
            payload = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            # Built by hand so tests control every header (no automatic Host or Content-Length).
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in send.items():
                conn.putheader(name, value)
            if payload is not None and not any(n.lower() == "content-length" for n in send):
                conn.putheader("Content-Length", str(len(payload)))
            conn.endheaders(payload)
            resp = conn.getresponse()
            raw = resp.read()
            lowered = {name.lower(): value for name, value in resp.getheaders()}
            return Response(resp.status, lowered, raw)
        finally:
            conn.close()

    def get(self, path, headers=None):
        return self.request("GET", path, headers=headers)

    def mutate(self, method, path, body=None, *, key=DEFAULT, token=DEFAULT, origin=DEFAULT,
               content_type=DEFAULT, headers=None):
        """A same-origin write. Pass None for key/token/origin/content_type to omit the header."""
        send = {
            "Origin": f"http://{self.host}" if origin is DEFAULT else origin,
            "X-Workbench-Session": self.token if token is DEFAULT else token,
            "Content-Type": "application/json" if content_type is DEFAULT else content_type,
            "Idempotency-Key": new_key() if key is DEFAULT else key,
        }
        if headers:
            send.update(headers)
        return self.request(method, path, body, send)

    # conveniences used by many tests
    def put_profile(self, label="Fictional Freelancer", version=0, **kw):
        return self.mutate("PUT", "/api/v1/profile", {"version": version, "display_label": label}, **kw)

    def create_lead(self, label="Acme Studio", country_tag="NL", work_mode="remote",
                    source_channel="manual", source_label=None, **kw):
        body = {"label": label, "country_tag": country_tag, "work_mode": work_mode,
                "source_channel": source_channel}
        if source_label is not None:
            body["source_label"] = source_label
        return self.mutate("POST", "/api/v1/leads", body, **kw)

    def activity(self, lead_id, version, kind="note", business_date="2030-04-10",
                 summary="Fictional summary", next_action=None, **kw):
        body = {"version": version, "kind": kind, "business_date": business_date, "summary": summary}
        if next_action is not None:
            body["next_action"] = next_action
        return self.mutate("POST", f"/api/v1/leads/{lead_id}/activities", body, **kw)

    def outreach(self, lead_id, version, text="Follow up", due="2030-04-11", business_date="2030-04-10",
                 summary="Sent a fictional message", **kw):
        return self.activity(lead_id, version, "outreach", business_date, summary,
                             {"text": text, "due_date": due}, **kw)

    def put_action(self, lead_id, version, text, due, **kw):
        return self.mutate("PUT", f"/api/v1/leads/{lead_id}/next-action",
                           {"version": version, "next_action": {"text": text, "due_date": due}}, **kw)

    def lead(self, lead_id):
        return self.get(f"/api/v1/leads/{lead_id}").json()


class RunningServer:
    """create_server() running in a background thread."""

    def __init__(self, data_dir, clock=None, faults=None, quiet=True, **kwargs):
        self.data_dir = data_dir
        self.clock = clock if clock is not None else FixedClock()
        self.faults = faults if faults is not None else ScriptedFaults()
        self.server = create_server(data_dir, 0, clock=self.clock, faults=self.faults, quiet=quiet, **kwargs)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()
        self.client = Client(self.port, self.server.session_token)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


# --- direct database inspection (a SEPARATE connection, bypassing the app)

TABLES = ("profile", "lead", "activity", "audit", "idempotency")


def db_file(data_dir):
    return Path(data_dir) / "workbench.sqlite3"


def db_query(data_dir, sql, params=()):
    conn = sqlite3.connect(str(db_file(data_dir)), timeout=10)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def table_counts(data_dir):
    return {table: db_query(data_dir, f"SELECT COUNT(*) FROM {table}")[0][0] for table in TABLES}


def db_dump(data_dir):
    conn = sqlite3.connect(str(db_file(data_dir)), timeout=10)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


class WorkbenchTestCase(unittest.TestCase):
    """Gives each test a temp data dir and helpers to start servers on it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"

    def start(self, data_dir=None, **kwargs):
        running = RunningServer(data_dir or self.data_dir, **kwargs)
        self.addCleanup(running.stop)
        return running

    def start_with_profile(self, **kwargs):
        """Server + client with the profile already saved."""
        running = self.start(**kwargs)
        self.assertEqual(running.client.put_profile().status, 200)
        return running

    def counts(self):
        return table_counts(self.data_dir)

    def snapshot(self):
        """Everything a rejected write must not change."""
        return {"dump": db_dump(self.data_dir)}

    def assert_error(self, response, status, code):
        self.assertEqual(response.status, status, response.raw[:300])
        self.assertEqual(response.json()["error"]["code"], code)


# --- real subprocess server

def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServerProcess:
    """`python -m workbench` as a child process (real system clock)."""

    def __init__(self, data_dir, port=None):
        self.data_dir = data_dir
        self.port = port or free_port()
        self.stdout_lines, self.stderr_lines = [], []
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "workbench", "--data-dir", str(data_dir), "--port", str(self.port)],
            cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self._ready = threading.Event()
        threading.Thread(target=self._drain, args=(self.proc.stdout, self.stdout_lines, True), daemon=True).start()
        threading.Thread(target=self._drain, args=(self.proc.stderr, self.stderr_lines, False), daemon=True).start()

    def _drain(self, stream, sink, watch):
        for line in stream:
            sink.append(line.rstrip("\n"))
            if watch and line.startswith("Press Ctrl+C"):
                self._ready.set()

    def wait_ready(self, timeout=20):
        if not self._ready.wait(timeout):
            raise AssertionError("server did not print its banner; stderr=%r" % self.stderr_lines)
        return self

    @property
    def output(self):
        return "\n".join(self.stdout_lines)

    def client(self):
        return Client(self.port)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
