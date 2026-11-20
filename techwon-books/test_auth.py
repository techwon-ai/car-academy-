import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
import ledger as L  # noqa: E402
import server  # noqa: E402
from ledger import LedgerError  # noqa: E402
from test_ledger import PG_URL, fresh_conn  # noqa: E402

PW = "correct horse battery"


class AuthLogicTests(unittest.TestCase):
    def setUp(self):
        self.c = fresh_conn()
        L.init_db(self.c)
        self.addCleanup(self.c.close)
        self.admin_id = auth.create_user(self.c, "Boss@Techwon.example", PW, "Boss", is_admin=True)
        self.admin = self.c.execute("SELECT * FROM users WHERE id = ?", (self.admin_id,)).fetchone()

    def test_password_hash_roundtrip(self):
        h = auth.hash_password("abc")
        self.assertTrue(auth.verify_password("abc", h))
        self.assertFalse(auth.verify_password("abd", h))
        self.assertNotEqual(h, auth.hash_password("abc"))  # salted

    def test_rules(self):
        with self.assertRaises(LedgerError):
            auth.create_user(self.c, "x@techwon.example", "short")
        with self.assertRaises(LedgerError):
            auth.create_user(self.c, "boss@techwon.example", PW)  # duplicate, case-insensitive

    def test_login_and_throttle(self):
        u = auth.login(self.c, "boss@techwon.example", PW, "1.2.3.4")
        self.assertEqual(u["id"], self.admin_id)
        for _ in range(auth.MAX_FAILS_PER_EMAIL):
            with self.assertRaises(LedgerError):
                auth.login(self.c, "boss@techwon.example", "wrong password!!", "1.2.3.4")
        with self.assertRaises(auth.TooManyAttempts):
            auth.login(self.c, "boss@techwon.example", PW, "1.2.3.4")  # even the right password

    def test_unknown_email_same_error(self):
        with self.assertRaisesRegex(LedgerError, "Incorrect email or password"):
            auth.login(self.c, "nobody@techwon.example", PW)

    def test_session_invalidated_by_password_change_and_disable(self):
        tok = auth.make_session(self.c, self.admin)
        self.assertEqual(auth.read_session(self.c, tok)["id"], self.admin_id)
        self.assertIsNone(auth.read_session(self.c, tok[:-2] + "xx"))  # tampered
        auth.change_own_password(self.c, self.admin, PW, PW + "!")
        self.assertIsNone(auth.read_session(self.c, tok))
        uid = auth.create_user(self.c, "kim@techwon.example", PW)
        kim = self.c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        tok2 = auth.make_session(self.c, kim)
        auth.update_user(self.c, self.admin, uid, is_active=False)
        self.assertIsNone(auth.read_session(self.c, tok2))

    def test_last_admin_protected(self):
        with self.assertRaises(LedgerError):
            auth.update_user(self.c, self.admin, self.admin_id, is_admin=False)

    def test_bootstrap_admin(self):
        c = fresh_conn()
        self.addCleanup(c.close)
        L.init_db(c)
        os.environ.update(ADMIN_EMAIL="first@techwon.example", ADMIN_PASSWORD=PW)
        try:
            self.assertTrue(auth.bootstrap_admin(c))
            self.assertFalse(auth.bootstrap_admin(c))  # only on an empty users table
        finally:
            del os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"]
        self.assertEqual(auth.list_users(c)[0]["email"], "first@techwon.example")


class HTTPTests(unittest.TestCase):
    """Drives the real request handler in online (login required) mode."""

    @classmethod
    def setUpClass(cls):
        if PG_URL:
            fresh_conn().close()
            target = PG_URL
        else:
            cls.tmp = tempfile.TemporaryDirectory()
            target = os.path.join(cls.tmp.name, "t.db")
        c = L.connect(target)
        L.init_db(c)
        auth.create_user(c, "admin@techwon.example", PW, is_admin=True)
        auth.create_user(c, "clerk@techwon.example", PW)
        c.close()
        server.Handler.db_target, server.Handler.require_login = target, True
        server.Handler.log_message = lambda *a: None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.Handler.require_login = False

    def req(self, method, path, body=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"Host": "books.techwon.example", **(headers or {})}
        if body is not None:
            h["Content-Type"] = "application/json"
        if cookie:
            h["Cookie"] = cookie
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data), r

    def login(self, email):
        st, _, r = self.req("POST", "/api/login", {"email": email, "password": PW})
        self.assertEqual(st, 200)
        sc = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Strict", sc)
        return sc.split(";")[0]

    def test_everything_requires_login(self):
        for path in ("/api/dashboard", "/api/invoices", "/api/reports/pnl?start=2026-01-01&end=2026-12-31"):
            self.assertEqual(self.req("GET", path)[0], 401, path)
        self.assertEqual(self.req("POST", "/api/vendors", {"name": "x"})[0], 401)
        st, body, _ = self.req("GET", "/api/session")
        self.assertEqual((st, body["login_required"], body["user"]), (200, True, None))
        self.assertEqual(self.req("GET", "/")[0], 200)  # the sign-in page itself

    def test_login_flow_and_audit_trail(self):
        ck = self.login("clerk@techwon.example")
        st, body, _ = self.req("GET", "/api/session", cookie=ck)
        self.assertEqual(body["user"]["email"], "clerk@techwon.example")
        st, body, _ = self.req("POST", "/api/journal", {"date": "2026-11-02", "memo": "audit", "lines": [
            {"account_code": "1000", "debit_cents": 100}, {"account_code": "3000", "credit_cents": 100}]}, cookie=ck)
        self.assertEqual(st, 200, body)
        entries = self.req("GET", "/api/journal", cookie=ck)[1]
        self.assertEqual([e["created_by"] for e in entries if e["memo"] == "audit"], ["clerk@techwon.example"])

    def test_bad_password_and_forged_cookie(self):
        st, body, _ = self.req("POST", "/api/login", {"email": "admin@techwon.example", "password": "nope nope nope"})
        self.assertEqual(st, 400)
        self.assertEqual(self.req("GET", "/api/dashboard", cookie="tw_session=eyJ1aWQiOjF9.forged")[0], 401)

    def test_admin_only(self):
        clerk = self.login("clerk@techwon.example")
        self.assertEqual(self.req("GET", "/api/users", cookie=clerk)[0], 403)
        self.assertEqual(self.req("POST", "/api/settings", {"lock_date": "2026-01-31"}, cookie=clerk)[0], 403)
        admin = self.login("admin@techwon.example")
        st, users, _ = self.req("GET", "/api/users", cookie=admin)
        self.assertEqual(st, 200)
        self.assertNotIn("password_hash", users[0])

    def test_cross_site_post_rejected(self):
        ck = self.login("admin@techwon.example")
        st, _, _ = self.req("POST", "/api/vendors", {"name": "Evil"}, cookie=ck, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        st, _, _ = self.req("POST", "/api/vendors", {"name": "Fine Vendor"}, cookie=ck,
                            headers={"Origin": "https://books.techwon.example"})
        self.assertEqual(st, 200)

    def test_settings_never_leak_session_secret(self):
        ck = self.login("admin@techwon.example")
        self.assertNotIn("session_secret", self.req("GET", "/api/settings", cookie=ck)[1])

    def test_logout_clears_cookie(self):
        st, _, r = self.req("POST", "/api/logout", {})
        self.assertIn("Max-Age=0", r.getheader("Set-Cookie"))


if __name__ == "__main__":
    unittest.main()
