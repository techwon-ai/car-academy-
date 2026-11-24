import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import integration as X  # noqa: E402
import ledger as L  # noqa: E402
import server  # noqa: E402
from ledger import LedgerError  # noqa: E402
from test_ledger import PG_URL, fresh_conn  # noqa: E402

KEY = "k" * 40
CLIENT = {"external_id": "portal-client-1", "name": "Bella Vista", "email": "hi@bv.example"}


class IntegrationLogicTests(unittest.TestCase):
    def setUp(self):
        self.c = fresh_conn()
        L.init_db(self.c)
        self.addCleanup(self.c.close)

    def test_client_upsert_links_and_never_duplicates(self):
        existing = L.create_client(self.c, "bella vista")  # made by hand in Books earlier
        a = X.upsert_client(self.c, "portal-client-1", "Bella Vista")
        self.assertEqual(a, existing)  # linked, not duplicated
        self.assertEqual(X.upsert_client(self.c, "portal-client-1", "Bella Vista Pvt Ltd"), a)
        self.assertEqual(L._client(self.c, a)["name"], "Bella Vista Pvt Ltd")  # rename follows the portal
        b = X.upsert_client(self.c, "portal-client-2", "Bella Vista Pvt Ltd")  # different portal client, same name
        self.assertNotEqual(a, b)
        self.assertEqual(L._client(self.c, b)["name"], "Bella Vista Pvt Ltd (2)")

    def test_invoice_import_keeps_number_dates_and_payments(self):
        p = {"external_id": "inv-abc", "client": CLIENT, "number": "INV-0007", "issue_date": "2026-09-24",
             "due_date": "2026-09-24", "items": [{"label": "Website Development", "qty": 3, "rate_cents": 2500000}],
             "discount_cents": 2500000, "tax_cents": 0,
             "payments": [{"amount_cents": 2000000, "date": "2026-09-25", "method": "cash", "note": "advance"}],
             "meta": {"createdByName": "Abrar", "advance": False}}
        iid = X.create_invoice(self.c, p)
        d = X.invoice_doc(self.c, iid)
        self.assertEqual((d["number"], d["issue_date"], d["total_cents"], d["paid_cents"]),
                         ("INV-0007", "2026-09-24", 5000000, 2000000))
        self.assertEqual(d["payments"][0]["method"], "cash")
        self.assertEqual(d["payments"][0]["deposit_account_code"], "1030")  # cash in hand
        self.assertEqual(d["meta"]["createdByName"], "Abrar")
        self.assertEqual(X.create_invoice(self.c, p), iid)  # re-running the import changes nothing
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)
        with self.assertRaisesRegex(LedgerError, "already used"):
            X.create_invoice(self.c, {**p, "external_id": "other"})
        self.assertTrue(L.trial_balance(self.c, "2026-12-31")["balanced"])

    def test_fixed_tax_amount_and_overpayment(self):
        iid = X.create_invoice(self.c, {"client": CLIENT, "items": [{"label": "SEO", "rate_cents": 7500000}],
                                        "tax_cents": 100000, "issue_date": "2026-10-01"})
        d = X.invoice_doc(self.c, iid)
        self.assertEqual((d["tax_cents"], d["total_cents"]), (100000, 7600000))
        with self.assertRaisesRegex(LedgerError, "exceeds the open balance"):
            X.record_payment(self.c, iid, {"amount_cents": 9000000, "date": "2026-10-02"})
        self.assertTrue(L.balance_sheet(self.c, "2026-12-31")["balanced"])

    def test_remove_semantics(self):
        iid = X.create_invoice(self.c, {"client": CLIENT, "items": [{"label": "Ads", "rate_cents": 1000}],
                                        "issue_date": "2026-10-01"})
        X.record_payment(self.c, iid, {"amount_cents": 500, "date": "2026-10-01"})
        with self.assertRaisesRegex(LedgerError, "has payments"):
            X.remove_invoice(self.c, iid)
        iid2 = X.create_invoice(self.c, {"client": CLIENT, "items": [{"label": "Ads", "rate_cents": 1000}],
                                         "issue_date": "2026-10-01"})
        X.remove_invoice(self.c, iid2)
        self.assertEqual([d["id"] for d in X.list_invoices(self.c)], [iid])  # void ones are hidden
        self.assertEqual(len(X.list_invoices(self.c, include_void=True)), 2)  # but kept

    def test_update_due_date_does_not_repost(self):
        iid = X.create_invoice(self.c, {"client": CLIENT, "items": [{"label": "Ads", "rate_cents": 1000}],
                                        "issue_date": "2026-10-01"})
        before = self.c.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
        X.update_invoice(self.c, iid, due_date="2026-10-20", meta={"emailedAt": "x"})
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0], before)
        d = X.invoice_doc(self.c, iid)
        self.assertEqual((d["due_date"], d["meta"]["emailedAt"]), ("2026-10-20", "x"))
        with self.assertRaises(LedgerError):
            X.update_invoice(self.c, iid, due_date="2026-09-01")

    def test_quotes_status_mapping_and_advance_link(self):
        qid = X.create_quote(self.c, {"external_id": "qt-1", "client": CLIENT, "number": "QT-0003",
                                      "items": [{"label": "Branding", "rate_cents": 10000000}],
                                      "valid_until": "2099-01-01", "status": "sent"})
        X.update_quote(self.c, qid, status="accepted", meta={"approvedByClient": True})
        q = X.quote_doc(self.c, qid)
        self.assertEqual((q["number"], q["status"], q["meta"]["approvedByClient"]), ("QT-0003", "accepted", True))
        X.create_invoice(self.c, {"client": CLIENT, "items": [{"label": "50% advance", "rate_cents": 5000000}],
                                  "quote_id": qid, "quote_pct_bp": 5000, "issue_date": "2026-10-01"})
        self.assertEqual(X.quote_doc(self.c, qid)["display_status"], "part-invoiced")
        q2 = X.create_quote(self.c, {"client": CLIENT, "items": [{"label": "x", "rate_cents": 100}], "status": "sent"})
        X.update_quote(self.c, q2, status="rejected")
        self.assertEqual(X.quote_doc(self.c, q2)["status"], "declined")
        with self.assertRaises(LedgerError):
            X.update_quote(self.c, q2, status="maybe")


class NumberingTests(unittest.TestCase):
    def test_padded_numbers_continue_a_series(self):
        c = fresh_conn()
        self.addCleanup(c.close)
        L.init_db(c)
        L.update_settings(c, {"invoice_prefix": "INV-", "number_digits": "4", "next_invoice_number": "13",
                              "quote_prefix": "QT-", "next_quote_number": "8"})
        iid = X.create_invoice(c, {"client": CLIENT, "items": [{"label": "x", "rate_cents": 100}], "issue_date": "2026-11-01"})
        self.assertEqual(X.invoice_doc(c, iid)["number"], "INV-0013")
        qid = X.create_quote(c, {"client": CLIENT, "items": [{"label": "x", "rate_cents": 100}]})
        self.assertEqual(X.quote_doc(c, qid)["number"], "QT-0008")


class IntegrationHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if PG_URL:
            fresh_conn().close()
            target = PG_URL
        else:
            cls.tmp = tempfile.TemporaryDirectory()
            target = os.path.join(cls.tmp.name, "x.db")
        c = L.connect(target)
        L.init_db(c)
        c.close()
        os.environ["BOOKS_INTEGRATION_KEY"] = KEY
        server.Handler.db_target, server.Handler.require_login = target, True
        server.Handler.log_message = lambda *a: None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.Handler.require_login = False
        del os.environ["BOOKS_INTEGRATION_KEY"]

    def req(self, method, path, body=None, key=KEY, actor="Abrar"):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1])
        h = {"Host": "books.example", "Content-Type": "application/json"}
        if key:
            h["Authorization"] = f"Bearer {key}"
        if actor:
            h["X-Acting-User"] = actor
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        r = conn.getresponse()
        data = json.loads(r.read() or b"null")
        conn.close()
        return r.status, data

    def test_key_required(self):
        self.assertEqual(self.req("GET", "/api/x/ping", key=None)[0], 401)
        self.assertEqual(self.req("GET", "/api/x/ping", key="wrong" * 10)[0], 401)
        self.assertEqual(self.req("GET", "/api/x/ping")[0], 200)
        # the key opens only the integration routes, not the rest of the app
        self.assertEqual(self.req("GET", "/api/invoices")[0], 401)

    def test_flow_and_audit_trail(self):
        st, inv = self.req("POST", "/api/x/invoices", {"client": CLIENT, "items": [{"label": "Logo", "rate_cents": 3000000}],
                                                       "issue_date": "2026-11-01", "due_date": "2026-11-08"})
        self.assertEqual(st, 200, inv)
        st, inv2 = self.req("POST", f"/api/x/invoices/{inv['id']}/payments", {"amount_cents": 1000000, "date": "2026-11-02"})
        self.assertEqual((st, inv2["paid_cents"]), (200, 1000000))
        st, err = self.req("POST", f"/api/x/invoices/{inv['id']}/payments", {"amount_cents": 99000000})
        self.assertEqual(st, 400)
        self.assertIn("exceeds", err["error"])
        c = L.connect(server.Handler.db_target)
        who = {r[0] for r in c.execute("SELECT created_by FROM journal_entries")}
        c.close()
        self.assertEqual(who, {"portal: Abrar"})
        st, lst = self.req("GET", "/api/x/invoices?client=portal-client-1")
        self.assertEqual((st, len(lst) >= 1), (200, True))


if __name__ == "__main__":
    unittest.main()
