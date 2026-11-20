import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger as L  # noqa: E402
from ledger import LedgerError  # noqa: E402


# Set TEST_DATABASE_URL=postgresql://... to run the whole suite against Postgres.
PG_URL = os.environ.get("TEST_DATABASE_URL")


def fresh_conn():
    if not PG_URL:
        return L.connect(":memory:")
    c = L.connect(PG_URL)
    c.execute("DROP SCHEMA public CASCADE")
    c.execute("CREATE SCHEMA public")
    return c


class Base(unittest.TestCase):
    def setUp(self):
        self.c = fresh_conn()
        L.init_db(self.c)
        self.acme = L.create_client(self.c, "Acme Motors", retainer_cents=500000, payment_terms_days=30)
        self.vendor = L.create_vendor(self.c, "Meta Ads")
        self.addCleanup(self.c.close)

    def tb_ok(self, as_of="2026-12-31"):
        tb = L.trial_balance(self.c, as_of)
        self.assertTrue(tb["balanced"], tb)
        bs = L.balance_sheet(self.c, as_of)
        self.assertTrue(bs["balanced"], bs)


class JournalTests(Base):
    def test_unbalanced_rejected(self):
        with self.assertRaises(LedgerError):
            L.post_journal_entry(self.c, "2026-01-05", "bad", [
                {"account_code": "1000", "debit_cents": 100},
                {"account_code": "3000", "credit_cents": 99}])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0], 0)

    def test_line_must_be_one_sided(self):
        with self.assertRaises(LedgerError):
            L.post_journal_entry(self.c, "2026-01-05", "bad", [
                {"account_code": "1000", "debit_cents": 100, "credit_cents": 100},
                {"account_code": "3000", "credit_cents": 0}])

    def test_lock_date(self):
        L.update_settings(self.c, {"lock_date": "2026-03-31"})
        with self.assertRaises(LedgerError):
            L.post_journal_entry(self.c, "2026-03-31", "x", [
                {"account_code": "1000", "debit_cents": 100}, {"account_code": "3000", "credit_cents": 100}])
        L.post_journal_entry(self.c, "2026-04-01", "x", [
            {"account_code": "1000", "debit_cents": 100}, {"account_code": "3000", "credit_cents": 100}])

    def test_reverse_manual_entry(self):
        eid = L.post_journal_entry(self.c, "2026-01-05", "capital", [
            {"account_code": "1000", "debit_cents": 1000000}, {"account_code": "3000", "credit_cents": 1000000}])
        L.reverse_journal_entry(self.c, eid, "2026-01-06")
        self.assertEqual(L.dashboard(self.c, "2026-01-31")["cash_cents"], 0)
        with self.assertRaises(LedgerError):
            L.reverse_journal_entry(self.c, eid, "2026-01-07")

    def test_cannot_reverse_invoice_entry_directly(self):
        inv = L.create_invoice(self.c, self.acme, "2026-02-01", [{"description": "x", "unit_price_cents": 100}])
        L.issue_invoice(self.c, inv)
        eid = L.get_invoice(self.c, inv)["entry_id"]
        with self.assertRaises(LedgerError):
            L.reverse_journal_entry(self.c, eid)


class InvoiceTests(Base):
    def test_issue_pay_flow(self):
        inv = L.create_invoice(self.c, self.acme, "2026-02-01", [
            {"description": "Strategy", "quantity": "7.5", "unit_price_cents": 15000, "revenue_account_code": "4100"},
            {"description": "Ad spend (no tax)", "unit_price_cents": 200000, "revenue_account_code": "4300",
             "taxable": False}], tax_rate_bp=1000)
        # draft: no ledger impact
        self.assertEqual(L.dashboard(self.c, "2026-02-28")["ar_cents"], 0)
        num = L.issue_invoice(self.c, inv)
        self.assertEqual(num, "TW-1001")
        d = L.get_invoice(self.c, inv)
        self.assertEqual(d["subtotal_cents"], 112500 + 200000)
        self.assertEqual(d["tax_cents"], 11250)
        self.assertEqual(d["due_date"], "2026-03-03")  # 30-day terms
        self.assertEqual(L.dashboard(self.c, "2026-02-28")["ar_cents"], d["total_cents"])
        L.record_payment(self.c, inv, "2026-02-10", 100000)
        self.assertEqual(L.get_invoice(self.c, inv)["display_status"], "overdue" if L._today() > "2026-03-03" else "partial")
        with self.assertRaises(LedgerError):
            L.record_payment(self.c, inv, "2026-02-11", d["total_cents"])  # overpay
        L.record_payment(self.c, inv, "2026-02-11", d["total_cents"] - 100000)
        self.assertEqual(L.get_invoice(self.c, inv)["display_status"], "paid")
        self.tb_ok()

    def test_numbering_sequential(self):
        a = L.create_invoice(self.c, self.acme, "2026-02-01", [{"description": "a", "unit_price_cents": 100}])
        b = L.create_invoice(self.c, self.acme, "2026-02-01", [{"description": "b", "unit_price_cents": 100}])
        self.assertEqual([L.issue_invoice(self.c, a), L.issue_invoice(self.c, b)], ["TW-1001", "TW-1002"])
        with self.assertRaises(LedgerError):
            L.issue_invoice(self.c, a)

    def test_void_requires_no_payments_and_reverses(self):
        inv = L.create_invoice(self.c, self.acme, "2026-02-01", [{"description": "a", "unit_price_cents": 5000}])
        L.issue_invoice(self.c, inv)
        pid = L.record_payment(self.c, inv, "2026-02-02", 5000)
        with self.assertRaises(LedgerError):
            L.void_invoice(self.c, inv, "2026-02-03")
        L.void_payment(self.c, pid, "2026-02-03")
        L.void_invoice(self.c, inv, "2026-02-03")
        bal = {r["code"]: r["balance_cents"] for r in L.trial_balance(self.c, "2026-12-31")["rows"]}
        self.assertEqual(bal.get("1100", 0), 0)
        self.assertEqual(bal.get("4100", 0), 0)
        self.tb_ok()

    def test_discount_line(self):
        inv = L.create_invoice(self.c, self.acme, "2026-02-01", [
            {"description": "Work", "unit_price_cents": 10000},
            {"description": "Discount", "unit_price_cents": -1000}])
        L.issue_invoice(self.c, inv)
        self.assertEqual(L.get_invoice(self.c, inv)["total_cents"], 9000)
        self.tb_ok()

    def test_zero_or_negative_total_rejected(self):
        with self.assertRaises(LedgerError):
            L.create_invoice(self.c, self.acme, "2026-02-01", [{"description": "x", "unit_price_cents": -5}])

    def test_draft_edit_and_delete(self):
        inv = L.create_invoice(self.c, self.acme, "2026-02-01", [{"description": "a", "unit_price_cents": 100}])
        L.update_draft_invoice(self.c, inv, lines=[{"description": "b", "unit_price_cents": 250}], notes="hi")
        self.assertEqual(L.get_invoice(self.c, inv)["total_cents"], 250)
        L.delete_draft_invoice(self.c, inv)
        with self.assertRaises(LedgerError):
            L.get_invoice(self.c, inv)


class RetainerTests(Base):
    def test_generate_is_idempotent(self):
        L.create_client(self.c, "No Retainer Co")
        r1 = L.generate_retainer_invoices(self.c, "2026-11")
        self.assertEqual(len(r1["created"]), 1)
        r2 = L.generate_retainer_invoices(self.c, "2026-11")
        self.assertEqual(len(r2["created"]), 0)
        self.assertEqual(r2["skipped"], ["Acme Motors"])
        pl = L.profit_and_loss(self.c, "2026-11-01", "2026-11-30")
        self.assertEqual(pl["net_revenue_cents"], 500000)

    def test_voided_retainer_can_regenerate(self):
        r = L.generate_retainer_invoices(self.c, "2026-11")
        L.void_invoice(self.c, r["created"][0]["invoice_id"], "2026-11-02")
        self.assertEqual(len(L.generate_retainer_invoices(self.c, "2026-11", "2026-11-03")["created"]), 1)


class BillTests(Base):
    def test_bill_and_payment(self):
        b = L.create_bill(self.c, self.vendor, "2026-03-01", 30000, "6200", "Figma", due_date="2026-03-15")
        self.assertEqual(L.dashboard(self.c, "2026-03-31")["ap_cents"], 30000)
        L.pay_bill(self.c, b, "2026-03-10", 30000, "2100")
        self.assertEqual(L.dashboard(self.c, "2026-03-31")["ap_cents"], 0)
        with self.assertRaises(LedgerError):
            L.pay_bill(self.c, b, "2026-03-10", 1)
        self.tb_ok()

    def test_quick_expense_paid_immediately(self):
        L.create_bill(self.c, self.vendor, "2026-03-01", 5000, "6500", "Lunch", paid_from="1000")
        d = L.dashboard(self.c, "2026-03-31")
        self.assertEqual(d["ap_cents"], 0)
        self.assertEqual(d["cash_cents"], -5000)

    def test_billable_requires_client(self):
        with self.assertRaises(LedgerError):
            L.create_bill(self.c, self.vendor, "2026-03-01", 5000, "5100", "x", billable=True)

    def test_rebill_passthrough_media_and_agi(self):
        proj = L.create_project(self.c, self.acme, "Q2 Launch", budget_cents=2000000)
        L.create_bill(self.c, self.vendor, "2026-04-01", 1000000, "5000", "April Meta spend",
                      project_id=proj, billable=True)
        free = L.create_vendor(self.c, "Jane Freelancer")
        L.create_bill(self.c, free, "2026-04-02", 200000, "5100", "Video edit", project_id=proj, billable=True)
        self.assertEqual(L.dashboard(self.c, "2026-04-30")["unbilled_expenses_cents"], 1200000)
        inv = L.create_rebill_invoice(self.c, self.acme, "2026-04-30", markup_bp=1500)
        d = L.get_invoice(self.c, inv)
        self.assertEqual(d["project_id"], proj)
        codes = sorted(l["revenue_account_code"] for l in d["lines"])
        self.assertEqual(codes, ["4300", "4400"])
        self.assertEqual(d["subtotal_cents"], 1150000 + 230000)
        with self.assertRaises(LedgerError):
            L.create_rebill_invoice(self.c, self.acme, "2026-04-30")  # nothing left
        L.issue_invoice(self.c, inv)
        pl = L.profit_and_loss(self.c, "2026-04-01", "2026-04-30")
        self.assertEqual(pl["gross_billings_cents"], 1380000)
        self.assertEqual(pl["passthrough_cents"], 1000000)
        self.assertEqual(pl["net_revenue_cents"], 380000)
        self.assertEqual(pl["gross_profit_cents"], 180000)
        cp = L.client_profitability(self.c, "2026-04-01", "2026-04-30")["rows"][0]
        self.assertEqual(cp["contribution_cents"], 180000)
        pp = L.project_profitability(self.c, "2026-04-01", "2026-04-30")["rows"][0]
        self.assertEqual(pp["budget_billed_pct"], 69.0)
        # voiding the invoice frees the bills for rebilling
        L.void_invoice(self.c, inv, "2026-05-01")
        self.assertEqual(len(L.unbilled_expenses(self.c, self.acme)), 2)
        self.tb_ok()

    def test_delete_rebill_draft_releases_bills(self):
        L.create_bill(self.c, self.vendor, "2026-04-01", 1000, "5200", "x", client_id=self.acme, billable=True)
        inv = L.create_rebill_invoice(self.c, self.acme, "2026-04-30")
        self.assertEqual(L.unbilled_expenses(self.c), [])
        L.delete_draft_invoice(self.c, inv)
        self.assertEqual(len(L.unbilled_expenses(self.c)), 1)


class ReportTests(Base):
    def test_balance_sheet_retained_earnings_rollover(self):
        L.post_journal_entry(self.c, "2025-06-01", "cap", [
            {"account_code": "1000", "debit_cents": 100000}, {"account_code": "3000", "credit_cents": 100000}])
        inv = L.create_invoice(self.c, self.acme, "2025-07-01", [{"description": "w", "unit_price_cents": 50000}])
        L.issue_invoice(self.c, inv)
        inv2 = L.create_invoice(self.c, self.acme, "2026-02-01", [{"description": "w", "unit_price_cents": 20000}])
        L.issue_invoice(self.c, inv2)
        bs = L.balance_sheet(self.c, "2026-06-30")
        eq = {e["name"]: e["amount_cents"] for e in bs["equity"]}
        self.assertEqual(eq["Retained Earnings (prior years, computed)"], 50000)
        self.assertEqual(eq["Net Income (current fiscal year)"], 20000)
        self.assertTrue(bs["balanced"])

    def test_fiscal_year_start(self):
        L.update_settings(self.c, {"fiscal_year_start_month": "7"})
        self.assertEqual(L._fy_start(self.c, "2026-03-15"), "2025-07-01")
        self.assertEqual(L._fy_start(self.c, "2026-07-01"), "2026-07-01")

    def test_ar_aging_buckets_and_point_in_time(self):
        inv = L.create_invoice(self.c, self.acme, "2026-01-01", [{"description": "w", "unit_price_cents": 10000}],
                               due_date="2026-01-31")
        L.issue_invoice(self.c, inv)
        L.record_payment(self.c, inv, "2026-05-01", 4000)
        a = L.ar_aging(self.c, "2026-03-15")
        self.assertEqual(a["items"][0]["bucket"], "31_60")
        self.assertEqual(a["totals"]["total"], 10000)  # payment after as_of not counted
        self.assertEqual(L.ar_aging(self.c, "2026-05-02")["totals"]["over_90"], 6000)
        self.assertEqual(L.ar_aging(self.c, "2025-12-31")["totals"]["total"], 0)

    def test_general_ledger_running_balance(self):
        L.post_journal_entry(self.c, "2026-01-01", "a", [
            {"account_code": "1000", "debit_cents": 500}, {"account_code": "3000", "credit_cents": 500}])
        L.post_journal_entry(self.c, "2026-02-01", "b", [
            {"account_code": "6100", "debit_cents": 200}, {"account_code": "1000", "credit_cents": 200}])
        gl = L.general_ledger(self.c, "1000", "2026-02-01", "2026-02-28")
        self.assertEqual(gl["opening_balance_cents"], 500)
        self.assertEqual(gl["closing_balance_cents"], 300)


class ReconciliationTests(Base):
    def test_reconcile(self):
        L.post_journal_entry(self.c, "2026-01-01", "a", [
            {"account_code": "1000", "debit_cents": 1000}, {"account_code": "3000", "credit_cents": 1000}])
        L.create_bill(self.c, self.vendor, "2026-01-10", 300, "6700", "fee", paid_from="1000")
        ws = L.reconciliation_worksheet(self.c, "1000", "2026-01-31")
        ids = [l["id"] for l in ws["uncleared"]]
        self.assertEqual(len(ids), 2)
        with self.assertRaises(LedgerError):
            L.reconcile(self.c, "1000", "2026-01-31", 1000, ids)  # wrong balance
        L.reconcile(self.c, "1000", "2026-01-31", 700, ids)
        ws2 = L.reconciliation_worksheet(self.c, "1000", "2026-01-31")
        self.assertEqual(ws2["cleared_balance_cents"], 700)
        self.assertEqual(ws2["uncleared"], [])
        bp = self.c.execute("SELECT id FROM bill_payments").fetchone()[0]
        with self.assertRaises(LedgerError):
            L.void_bill_payment(self.c, bp)


class AccountTests(Base):
    def test_system_account_cannot_be_deactivated(self):
        ar = L.account_by_code(self.c, "1100")
        with self.assertRaises(LedgerError):
            L.update_account(self.c, ar["id"], is_active=False)

    def test_create_account_validates_subtype(self):
        with self.assertRaises(LedgerError):
            L.create_account(self.c, "6950", "X", "expense", "revenue")
        L.create_account(self.c, "6950", "Training", "expense", "opex")
        with self.assertRaises(LedgerError):
            L.create_account(self.c, "6950", "Dup", "expense", "opex")


if __name__ == "__main__":
    unittest.main()
