#!/usr/bin/env python3
"""Techwon Books - local web server (Python standard library only).

    python3 server.py init                 # create books.db with the agency chart of accounts
    python3 server.py demo                 # create demo.db filled with sample agency data
    python3 server.py serve [--db books.db] [--port 8080]
    python3 server.py backup backups/books-2026-11-20.db

There is no login. The server binds to 127.0.0.1 only and rejects requests whose
Host/Origin is not localhost, so other machines and malicious web pages can't reach it.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger as L  # noqa: E402

ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def _ids(body, *keys):
    return {k: body.get(k) for k in keys if k in body}


# ---- reference data -------------------------------------------------------

@route("GET", "/api/settings")
def _(c, q, b): return L.get_settings(c)
@route("POST", "/api/settings")
def _(c, q, b): return L.update_settings(c, b)

@route("GET", "/api/accounts")
def _(c, q, b): return L.list_accounts(c)
@route("POST", "/api/accounts")
def _(c, q, b): return {"id": L.create_account(c, b.get("code"), b.get("name"), b.get("type"), b.get("subtype"))}
@route("POST", r"/api/accounts/(\d+)")
def _(c, q, b, aid): return L.update_account(c, aid, b.get("name"), b.get("is_active"))
@route("GET", "/api/account-types")
def _(c, q, b): return {t: list(s) for t, s in L.SUBTYPES.items()}

@route("GET", "/api/clients")
def _(c, q, b): return L.list_clients(c)
@route("POST", "/api/clients")
def _(c, q, b): return {"id": L.create_client(c, **_ids(b, "name", "email", "address", "retainer_cents", "payment_terms_days"))}
@route("POST", r"/api/clients/(\d+)")
def _(c, q, b, cid): return L.update_client(c, cid, **b)

@route("GET", "/api/projects")
def _(c, q, b): return L.list_projects(c, q.get("client_id"))
@route("POST", "/api/projects")
def _(c, q, b): return {"id": L.create_project(c, b.get("client_id"), b.get("name"), b.get("budget_cents", 0))}
@route("POST", r"/api/projects/(\d+)")
def _(c, q, b, pid): return L.update_project(c, pid, b.get("status"), b.get("budget_cents"))

@route("GET", "/api/vendors")
def _(c, q, b): return L.list_vendors(c)
@route("POST", "/api/vendors")
def _(c, q, b): return {"id": L.create_vendor(c, b.get("name"), b.get("email", ""))}

# ---- invoices -------------------------------------------------------------

@route("GET", "/api/invoices")
def _(c, q, b): return L.list_invoices(c, q.get("client_id"), q.get("status"))
@route("POST", "/api/invoices")
def _(c, q, b):
    return {"id": L.create_invoice(c, b.get("client_id"), b.get("issue_date"), b.get("lines"),
                                   **_ids(b, "due_date", "project_id", "tax_rate_bp", "notes"))}
@route("GET", r"/api/invoices/(\d+)")
def _(c, q, b, iid): return L.get_invoice(c, iid)
@route("POST", r"/api/invoices/(\d+)")
def _(c, q, b, iid):
    lines = b.pop("lines", None)
    return L.update_draft_invoice(c, iid, lines=lines, **b)
@route("POST", r"/api/invoices/(\d+)/issue")
def _(c, q, b, iid): return {"number": L.issue_invoice(c, iid)}
@route("POST", r"/api/invoices/(\d+)/void")
def _(c, q, b, iid): return L.void_invoice(c, iid, b.get("date"))
@route("POST", r"/api/invoices/(\d+)/delete")
def _(c, q, b, iid): return L.delete_draft_invoice(c, iid)
@route("POST", r"/api/invoices/(\d+)/payments")
def _(c, q, b, iid):
    return {"id": L.record_payment(c, iid, b.get("date"), b.get("amount_cents"),
                                   b.get("deposit_account_code") or L.CASH, b.get("reference", ""))}
@route("POST", r"/api/payments/(\d+)/void")
def _(c, q, b, pid): return L.void_payment(c, pid, b.get("date"))
@route("POST", "/api/retainers/generate")
def _(c, q, b): return L.generate_retainer_invoices(c, b.get("period"), b.get("issue_date"), b.get("auto_issue", True))
@route("GET", "/api/unbilled-expenses")
def _(c, q, b): return L.unbilled_expenses(c, q.get("client_id"))
@route("POST", "/api/rebill")
def _(c, q, b):
    return {"id": L.create_rebill_invoice(c, b.get("client_id"), b.get("issue_date"),
                                          b.get("markup_bp", 0), b.get("bill_ids"))}

# ---- bills ----------------------------------------------------------------

@route("GET", "/api/bills")
def _(c, q, b): return L.list_bills(c, q.get("status"))
@route("POST", "/api/bills")
def _(c, q, b):
    return {"id": L.create_bill(c, b.get("vendor_id"), b.get("bill_date"), b.get("amount_cents"),
                                b.get("expense_account_code"),
                                **_ids(b, "description", "due_date", "client_id", "project_id", "billable",
                                       "reference", "paid_from"))}
@route("POST", r"/api/bills/(\d+)/pay")
def _(c, q, b, bid):
    return {"id": L.pay_bill(c, bid, b.get("date"), b.get("amount_cents"),
                             b.get("paid_from_account_code") or L.CASH, b.get("reference", ""))}
@route("POST", r"/api/bills/(\d+)/void")
def _(c, q, b, bid): return L.void_bill(c, bid, b.get("date"))
@route("POST", r"/api/bill-payments/(\d+)/void")
def _(c, q, b, pid): return L.void_bill_payment(c, pid, b.get("date"))

# ---- journal & banking ----------------------------------------------------

@route("GET", "/api/journal")
def _(c, q, b): return L.list_journal(c, q.get("limit", 200), q.get("start"), q.get("end"))
@route("POST", "/api/journal")
def _(c, q, b): return {"id": L.post_journal_entry(c, b.get("date"), b.get("memo", ""), b.get("lines"))}
@route("POST", r"/api/journal/(\d+)/reverse")
def _(c, q, b, eid): return {"id": L.reverse_journal_entry(c, eid, b.get("date"), b.get("memo", ""))}
@route("GET", "/api/reconcile")
def _(c, q, b): return L.reconciliation_worksheet(c, q.get("account_code"), q.get("statement_date"))
@route("POST", "/api/reconcile")
def _(c, q, b):
    return {"id": L.reconcile(c, b.get("account_code"), b.get("statement_date"),
                              b.get("statement_balance_cents"), b.get("line_ids"))}

# ---- reports --------------------------------------------------------------

@route("GET", "/api/dashboard")
def _(c, q, b): return L.dashboard(c, q.get("today"))
@route("GET", "/api/reports/pnl")
def _(c, q, b): return L.profit_and_loss(c, q.get("start"), q.get("end"))
@route("GET", "/api/reports/balance-sheet")
def _(c, q, b): return L.balance_sheet(c, q.get("as_of"))
@route("GET", "/api/reports/trial-balance")
def _(c, q, b): return L.trial_balance(c, q.get("as_of"))
@route("GET", "/api/reports/ar-aging")
def _(c, q, b): return L.ar_aging(c, q.get("as_of"))
@route("GET", "/api/reports/ap-aging")
def _(c, q, b): return L.ap_aging(c, q.get("as_of"))
@route("GET", "/api/reports/client-profitability")
def _(c, q, b): return L.client_profitability(c, q.get("start"), q.get("end"))
@route("GET", "/api/reports/project-profitability")
def _(c, q, b): return L.project_profitability(c, q.get("start"), q.get("end"))
@route("GET", "/api/reports/general-ledger")
def _(c, q, b): return L.general_ledger(c, q.get("account_code"), q.get("start"), q.get("end"))


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


class Handler(BaseHTTPRequestHandler):
    db_path = "books.db"
    server_version = "TechwonBooks/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, fmt % args))

    def _send(self, status, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin:
            o = urlparse(origin).hostname or ""
            if o not in {"localhost", "127.0.0.1", "::1"}:
                return False
        return True

    def _dispatch(self, method):
        if not self._host_ok():
            return self._send(403, {"error": "Forbidden: Techwon Books only accepts local requests"})
        url = urlparse(self.path)
        if method == "GET" and url.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        body = {}
        if method == "POST":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._send(415, {"error": "Content-Type must be application/json"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > 5_000_000:
                return self._send(413, {"error": "Request too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "Invalid JSON"})
            if not isinstance(body, dict):
                return self._send(400, {"error": "JSON body must be an object"})
        for m, rx, fn in ROUTES:
            match = rx.match(url.path)
            if m == method and match:
                conn = L.connect(self.db_path)
                try:
                    result = fn(conn, query, body, *match.groups())
                    return self._send(200, result if result is not None else {"ok": True})
                except L.LedgerError as e:
                    return self._send(400, {"error": str(e)})
                except (TypeError, sqlite3.IntegrityError) as e:
                    return self._send(400, {"error": f"Invalid request: {e}"})
                except Exception:
                    traceback.print_exc()
                    return self._send(500, {"error": "Internal error - see server log"})
                finally:
                    conn.close()
        self._send(404, {"error": "Not found"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def main():
    ap = argparse.ArgumentParser(description="Techwon Books")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--db", default=os.path.join(HERE, "books.db"))
    s.add_argument("--port", type=int, default=8080)
    i = sub.add_parser("init")
    i.add_argument("--db", default=os.path.join(HERE, "books.db"))
    d = sub.add_parser("demo")
    d.add_argument("--db", default=os.path.join(HERE, "demo.db"))
    b = sub.add_parser("backup")
    b.add_argument("dest")
    b.add_argument("--db", default=os.path.join(HERE, "books.db"))
    a = ap.parse_args()

    if a.cmd == "backup":
        if not os.path.exists(a.db):
            sys.exit(f"No database at {a.db}")
        src, dst = sqlite3.connect(a.db), sqlite3.connect(a.dest)
        src.backup(dst)  # consistent online snapshot, safe while the server runs
        dst.close(); src.close()
        print(f"Backed up {a.db} -> {a.dest}")
        return
    if a.cmd == "demo":
        import demo
        if os.path.exists(a.db):
            sys.exit(f"{a.db} already exists; delete it first to rebuild the demo")
        demo.build(a.db)
        print(f"Demo books written to {a.db}. Run: python3 server.py serve --db {a.db}")
        return
    conn = L.connect(a.db)
    L.init_db(conn)
    conn.close()
    if a.cmd == "init":
        print(f"Initialized {a.db}")
        return
    Handler.db_path = a.db
    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"Techwon Books running at http://localhost:{a.port}  (db: {a.db})  Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
