"""T15 request checks, headers, error hygiene and access log."""

import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path

from tests.support.harness import Client, WorkbenchTestCase, new_key

EXPECTED_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-resource-policy": "same-origin",
}
LEAD_BODY = {"label": "Fictional", "country_tag": "NL", "work_mode": "remote", "source_channel": "manual"}


class T15Checks(WorkbenchTestCase):
    def setUp(self):
        super().setUp()
        self.running = self.start_with_profile()
        self.client = self.running.client
        self.port = self.running.port
        self.before = self.snapshot()

    def assert_unchanged(self):
        self.assertEqual(self.snapshot(), self.before)

    def post_lead(self, **kw):
        return self.client.mutate("POST", "/api/v1/leads", LEAD_BODY, **kw)

    # --- session token

    def test_missing_or_wrong_session_token(self):
        self.assert_error(self.post_lead(token=None), 403, "SESSION_INVALID")
        self.assert_error(self.post_lead(token="wrong-token"), 403, "SESSION_INVALID")
        self.assert_error(self.post_lead(token=""), 403, "SESSION_INVALID")
        self.assert_error(self.post_lead(token=self.running.server.session_token + "x"), 403, "SESSION_INVALID")
        self.assert_unchanged()

    def test_token_from_another_server_process_is_rejected(self):
        other = self.start(data_dir=self.data_dir.parent / "other")
        self.assert_error(self.post_lead(token=other.server.session_token), 403, "SESSION_INVALID")
        self.assert_unchanged()

    def test_token_comes_from_session_endpoint(self):
        token = self.client.get("/api/v1/session").json()["token"]
        self.assertEqual(token, self.running.server.session_token)
        self.assertGreaterEqual(len(token), 32)

    # --- origin / fetch metadata

    def test_origin_rules(self):
        self.assert_error(self.post_lead(origin="http://evil.example"), 403, "ORIGIN_NOT_ALLOWED")
        self.assert_error(self.post_lead(origin=f"http://127.0.0.1:{self.port + 1}"), 403, "ORIGIN_NOT_ALLOWED")
        self.assert_error(self.post_lead(origin="null"), 403, "ORIGIN_NOT_ALLOWED")
        self.assert_error(self.post_lead(origin=f"https://127.0.0.1:{self.port}"), 403, "ORIGIN_NOT_ALLOWED")
        self.assert_error(self.post_lead(origin=None), 403, "ORIGIN_NOT_ALLOWED")
        self.assert_unchanged()

    def test_localhost_origin_matching_localhost_host_is_allowed(self):
        client = Client(self.port, self.running.server.session_token)
        client.host = f"localhost:{self.port}"
        self.assertEqual(client.mutate("POST", "/api/v1/leads", LEAD_BODY).status, 201)

    def test_cross_site_fetch_metadata_rejected(self):
        for value in ("cross-site", "same-site", "none"):
            with self.subTest(value):
                self.assert_error(self.post_lead(headers={"Sec-Fetch-Site": value}), 403, "ORIGIN_NOT_ALLOWED")
        self.assert_unchanged()
        self.assertEqual(self.post_lead(headers={"Sec-Fetch-Site": "same-origin"}).status, 201)

    # --- host header

    def test_wrong_host_rejected_even_for_get(self):
        for host in (f"evil.example:{self.port}", "evil.example", f"127.0.0.1:{self.port + 1}", f"127.0.0.1.evil.example:{self.port}", "127.0.0.1", ""):
            with self.subTest(host=host):
                self.assert_error(self.client.get("/api/v1/health", headers={"Host": host}), 403, "HOST_NOT_ALLOWED")
                self.assert_error(self.client.get("/", headers={"Host": host}), 403, "HOST_NOT_ALLOWED")
        self.assert_error(self.client.get("/api/v1/health", headers={"Host": None}), 403, "HOST_NOT_ALLOWED")
        self.assert_error(self.post_lead(headers={"Host": f"evil.example:{self.port}",
                                                  "Origin": f"http://evil.example:{self.port}"}),
                          403, "HOST_NOT_ALLOWED")
        self.assert_unchanged()

    def test_both_loopback_names_are_accepted(self):
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"LOCALHOST:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.client.get("/api/v1/health", headers={"Host": host}).status, 200)

    # --- methods

    def test_options_and_unknown_methods_are_405_without_cors(self):
        for path in ("/api/v1/leads", "/api/v1/health", "/", "/nope"):
            with self.subTest(path):
                response = self.client.request("OPTIONS", path, headers={
                    "Origin": f"http://{self.client.host}", "Access-Control-Request-Method": "POST"})
                self.assertEqual(response.status, 405)
                self.assertFalse([h for h in response.headers if h.startswith("access-control-")])
        for method in ("HEAD", "TRACE", "CONNECT", "FOO"):
            with self.subTest(method):
                response = self.client.request(method, "/api/v1/health")
                self.assertEqual(response.status, 405)
        self.assert_error(self.client.mutate("DELETE", "/api/v1/leads/1", {}), 405, "METHOD_NOT_ALLOWED")
        self.assert_error(self.client.mutate("PUT", "/api/v1/leads", {}), 405, "METHOD_NOT_ALLOWED")
        self.assert_error(self.client.request("POST", "/api/v1/health", {}), 403, "ORIGIN_NOT_ALLOWED")
        self.assert_unchanged()

    def test_no_cors_headers_on_any_response(self):
        responses = [self.client.get("/api/v1/health"), self.post_lead(), self.post_lead(token=None),
                     self.client.get("/api/v1/nope"), self.client.get("/nope")]
        for response in responses:
            self.assertFalse([h for h in response.headers if h.startswith("access-control-")])

    # --- body, content type, key

    def test_body_rules(self):
        cases = {
            "malformed json": (dict(content_type="application/json"), "{not json", 400, "MALFORMED_REQUEST"),
            "truncated json": (dict(), '{"label": "x"', 400, "MALFORMED_REQUEST"),
            "array body": (dict(), "[]", 400, "MALFORMED_REQUEST"),
            "string body": (dict(), '"hello"', 400, "MALFORMED_REQUEST"),
            "null body": (dict(), "null", 400, "MALFORMED_REQUEST"),
            "invalid utf8": (dict(), b'{"label": "\xff\xfe"}', 400, "MALFORMED_REQUEST"),
            "nan": (dict(), '{"label": NaN}', 400, "MALFORMED_REQUEST"),
            "duplicate keys": (dict(), '{"label": "a", "label": "b"}', 400, "MALFORMED_REQUEST"),
            "wrong content type": (dict(content_type="text/plain"), LEAD_BODY, 415, "UNSUPPORTED_MEDIA_TYPE"),
            "form content type": (dict(content_type="application/x-www-form-urlencoded"), LEAD_BODY, 415, "UNSUPPORTED_MEDIA_TYPE"),
            "no content type": (dict(content_type=None), LEAD_BODY, 415, "UNSUPPORTED_MEDIA_TYPE"),
            "oversized body": (dict(), b'{"label": "' + b"x" * 70000 + b'"}', 413, "BODY_TOO_LARGE"),
            "missing key": (dict(key=None), LEAD_BODY, 400, "IDEMPOTENCY_KEY_REQUIRED"),
            "short key": (dict(key="short"), LEAD_BODY, 400, "IDEMPOTENCY_KEY_REQUIRED"),
            "bad key characters": (dict(key="has spaces in it and more!"), LEAD_BODY, 400, "IDEMPOTENCY_KEY_REQUIRED"),
            "long key": (dict(key="k" * 129), LEAD_BODY, 400, "IDEMPOTENCY_KEY_REQUIRED"),
        }
        for name, (options, body, status, code) in cases.items():
            with self.subTest(name):
                self.assert_error(self.client.mutate("POST", "/api/v1/leads", body, **options), status, code)
        self.assert_unchanged()

    def test_content_type_parameters_are_allowed(self):
        response = self.post_lead(content_type="application/json; charset=utf-8")
        self.assertEqual(response.status, 201)

    def test_body_at_the_limit_is_read_not_rejected_for_size(self):
        padding = "x" * (65536 - len('{"label": "", "country_tag": "NL", "work_mode": "remote", "source_channel": "manual"}'))
        body = '{"label": "%s", "country_tag": "NL", "work_mode": "remote", "source_channel": "manual"}' % padding
        self.assertEqual(len(body.encode()), 65536)
        self.assert_error(self.client.mutate("POST", "/api/v1/leads", body), 422, "VALIDATION_FAILED")  # label too long
        self.assert_unchanged()

    def test_missing_content_length_is_411(self):
        response = self.client.request("POST", "/api/v1/leads", None, {
            "Origin": f"http://{self.client.host}", "X-Workbench-Session": self.client.token,
            "Content-Type": "application/json", "Idempotency-Key": new_key()})
        self.assertEqual(response.status, 411)

    def test_unknown_routes(self):
        self.assert_error(self.client.get("/api/v1/nope"), 404, "NOT_FOUND")
        self.assert_error(self.client.mutate("POST", "/api/v1/nope", {}), 404, "NOT_FOUND")
        self.assertEqual(self.client.get("/nope").status, 404)
        self.assert_unchanged()

    # --- stored text stays text

    def test_markup_in_text_is_stored_and_returned_verbatim(self):
        label = "<img src=x onerror=alert(1)>"
        summary = "</script><script>alert('x')</script> & \"quotes\" \u00e9\u4e2d\U0001f600"
        action = "<b>bold</b>&amp;"
        response = self.client.create_lead(label, source_label="<svg onload=alert(2)>")
        self.assertEqual(response.status, 201)
        lead_id = response.json()["lead"]["id"]
        self.assertEqual(self.client.outreach(lead_id, 1, text=action, summary=summary).status, 201)
        detail = self.client.lead(lead_id)
        self.assertEqual(detail["lead"]["label"], label)
        self.assertEqual(detail["lead"]["source_label"], "<svg onload=alert(2)>")
        self.assertEqual(detail["lead"]["next_action"]["text"], action)
        self.assertEqual(detail["activities"][0]["summary"], summary)
        self.assertIn(label, self.client.get("/api/v1/leads").raw.decode("utf-8").replace("\\u003c", "<"))
        self.assertTrue(self.client.get(f"/api/v1/leads/{lead_id}").headers["content-type"].startswith("application/json"))
        found = self.client.get("/api/v1/leads?q=ONERROR").json()
        self.assertEqual(found["total"], 1)

    # --- error hygiene

    def test_error_bodies_never_leak_sql_token_or_submitted_body(self):
        secret_label = "SECRET-LABEL-" + "x" * 130
        responses = [
            self.client.mutate("POST", "/api/v1/leads", {**LEAD_BODY, "label": secret_label}),
            self.client.mutate("POST", "/api/v1/leads", {**LEAD_BODY, "country_tag": "SECRET-ENUM"}),
            self.client.mutate("POST", "/api/v1/leads", '{"label": "SECRET-JSON'),
            self.client.mutate("POST", "/api/v1/leads", LEAD_BODY, token="SECRET-TOKEN-VALUE"),
            self.client.mutate("PATCH", "/api/v1/leads/999", {"version": 1, "label": "SECRET-PATCH"}),
            self.client.mutate("PUT", "/api/v1/profile", {"version": 0, "display_label": "SECRET-PROFILE"}),
            self.client.get("/api/v1/leads?q=" + "SECRET-QUERY" * 20),
            self.client.get("/api/v1/leads/abc"),
        ]
        for response in responses:
            self.assertGreaterEqual(response.status, 400)
            text = response.text
            self.assertNotIn("SECRET-LABEL", text)
            self.assertNotIn("SECRET-JSON", text)
            self.assertNotIn("SECRET-PATCH", text)
            self.assertNotIn("SECRET-PROFILE", text)
            self.assertNotIn("SECRET-TOKEN-VALUE", text)
            self.assertNotIn(self.running.server.session_token, text)
            for sql_word in ("SELECT", "INSERT", "UPDATE lead", "sqlite", "Traceback", "constraint", "CHECK"):
                self.assertNotIn(sql_word, text)
            self.assertEqual(set(response.json()), {"error"})

    # --- headers

    def test_security_headers_on_api_errors_and_pages(self):
        responses = [self.client.get("/api/v1/health"), self.client.get("/api/v1/dashboard"), self.post_lead(),
                     self.post_lead(token=None), self.client.get("/api/v1/nope"), self.client.get("/missing-page"),
                     self.client.get("/api/v1/health", headers={"Host": "evil.example"})]
        for response in responses:
            self.assert_security_headers(response)

    def assert_security_headers(self, response):
        for name, value in EXPECTED_HEADERS.items():
            self.assertEqual(response.headers.get(name), value, (name, response.status))
        csp = response.headers["content-security-policy"]
        for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self' data:",
                          "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'self'",
                          "frame-ancestors 'none'"):
            self.assertIn(directive, csp)
        self.assertFalse([h for h in response.headers if h.startswith("access-control-")])

    def test_json_content_type(self):
        self.assertEqual(self.client.get("/api/v1/health").headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(self.post_lead(token=None).headers["content-type"], "application/json; charset=utf-8")


class T15StaticFiles(WorkbenchTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.static = root / "static"
        (self.static / "js").mkdir(parents=True)
        (self.static / "index.html").write_text("<!doctype html><title>Fixture</title>", encoding="utf-8")
        (self.static / "styles.css").write_text("body{}", encoding="utf-8")
        (self.static / "js" / "app.js").write_text("export const x = 1;", encoding="utf-8")
        (self.static / "js" / "notes.xyz").write_text("unknown type", encoding="utf-8")
        (root / "secret.txt").write_text("outside the static folder", encoding="utf-8")
        self.running = self.start(static_dir=self.static)
        self.client = self.running.client

    def test_pages_serve_index_with_security_headers(self):
        for path in ("/", "/leads", "/leads/new", "/leads/12", "/settings"):
            with self.subTest(path):
                response = self.client.get(path)
                self.assertEqual(response.status, 200)
                self.assertIn("Fixture", response.text)
                self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
                for name, value in EXPECTED_HEADERS.items():
                    self.assertEqual(response.headers.get(name), value)
                self.assertIn("default-src 'self'", response.headers["content-security-policy"])

    def test_static_files_and_mime_types(self):
        js = self.client.get("/static/js/app.js")
        self.assertEqual((js.status, js.headers["content-type"]), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(js.text, "export const x = 1;")
        self.assertEqual(self.client.get("/static/styles.css").headers["content-type"], "text/css; charset=utf-8")
        self.assertEqual(self.client.get("/static/styles.css?v=3").status, 200)
        for name, value in EXPECTED_HEADERS.items():
            self.assertEqual(js.headers.get(name), value)
        self.assertIn("script-src 'self'", js.headers["content-security-policy"])

    def test_missing_and_unknown_type_are_404(self):
        for path in ("/static/nope.js", "/static/js", "/static/", "/static/js/notes.xyz", "/leads/abc", "/leads/1/extra",
                     "/index.html", "/static"):
            with self.subTest(path):
                response = self.client.get(path)
                self.assertEqual(response.status, 404)
                self.assertNotIn("Traceback", response.text)

    def test_directory_traversal_is_blocked(self):
        for path in ("/static/../secret.txt", "/static/..%2fsecret.txt", "/static/%2e%2e/secret.txt",
                     "/static/..%5csecret.txt", "/static/js/../../secret.txt", "/static/js/..%2f..%2fsecret.txt",
                     "/static/%2e%2e%2f%2e%2e%2fsecret.txt", "/static//etc/passwd", "/static/C:/Windows/win.ini",
                     "/static/js/app.js%00.png"):
            with self.subTest(path):
                response = self.client.get(path)
                self.assertNotEqual(response.status, 200)
                self.assertNotIn("outside the static folder", response.text)

    def test_missing_index_is_a_404_not_a_crash(self):
        (self.static / "index.html").unlink()
        self.assertEqual(self.client.get("/").status, 404)


class T15AccessLog(WorkbenchTestCase):
    def test_log_lines_contain_only_method_path_status(self):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            running = self.start(quiet=False)
            client = running.client
            client.put_profile("SECRET-PROFILE-LABEL")
            lead = client.create_lead("SECRET-LEAD-LABEL", source_label="SECRET-SOURCE").json()["lead"]
            client.outreach(lead["id"], 1, text="SECRET-ACTION", summary="SECRET-SUMMARY")
            client.get("/api/v1/leads?q=SECRET-LEAD-LABEL&country=NL")
            client.get(f"/api/v1/leads/{lead['id']}?token=" + client.token)
            client.mutate("POST", "/api/v1/leads", {"label": "SECRET-BAD"}, token="SECRET-WRONG-TOKEN")
            client.get("/nope?secret=1")
        output = stream.getvalue()
        lines = [line for line in output.splitlines() if line.strip()]
        self.assertEqual(len(lines), 7)
        for line in lines:
            self.assertRegex(line, r"^(GET|POST|PUT|PATCH) /[^ ?]* [0-9]{3}$")
        self.assertIn("GET /api/v1/leads 200", lines)
        self.assertIn("POST /api/v1/leads 403", lines)
        self.assertIn("GET /nope 404", lines)
        for secret in ("SECRET", "?", "q=", "token", client.token, "Origin", "localhost"):
            self.assertNotIn(secret, output)

    def test_internal_errors_log_only_the_exception_class(self):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            running = self.start(quiet=False)
            running.client.put_profile()
            running.faults.arm("before_commit")
            response = running.client.create_lead("SECRET-LEAD-LABEL")
        self.assertEqual(response.status, 500)
        output = stream.getvalue()
        self.assertIn("internal error: RuntimeError", output)
        self.assertNotIn("SECRET", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("injected fault", output)


if __name__ == "__main__":
    unittest.main()
