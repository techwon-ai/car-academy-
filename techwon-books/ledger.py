"""Techwon Books - double-entry accounting core for a marketing agency.

Every financial event (invoice, payment, bill, expense, manual adjustment)
becomes a balanced journal entry. Reports are computed from journal lines only,
so the books can never drift from the source documents.

Money is stored as integer cents. Posted entries are never edited or deleted:
mistakes are corrected with reversing entries, and a lock date freezes closed
periods.
"""

import contextlib
import datetime as dt
import sqlite3
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class LedgerError(Exception):
    """A business-rule violation the user can fix (shown as a 400)."""


# Codes the application relies on. They are marked as system accounts.
CASH = "1000"
AR = "1100"
AP = "2000"
SALES_TAX = "2200"
RETAINER_REVENUE = "4000"
PASSTHROUGH_REVENUE = "4300"
REIMBURSED_REVENUE = "4400"

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")
SUBTYPES = {
    "asset": ("cash", "receivable", "current_asset", "fixed_asset", "contra_asset"),
    "liability": ("payable", "credit_card", "tax", "deferred_revenue",
                  "current_liability", "long_term_liability"),
    "equity": ("equity", "drawings", "retained_earnings"),
    "income": ("revenue", "passthrough_revenue", "other_income"),
    "expense": ("passthrough_cost", "cogs", "opex", "other_expense"),
}
DEBIT_NORMAL = ("asset", "expense")

DEFAULT_ACCOUNTS = [
    # code, name, type, subtype, system
    ("1000", "Operating Bank Account", "asset", "cash", 1),
    ("1010", "Savings Account", "asset", "cash", 0),
    ("1020", "Payment Processor Clearing (Stripe/PayPal)", "asset", "cash", 0),
    ("1100", "Accounts Receivable", "asset", "receivable", 1),
    ("1200", "Client Media Advances", "asset", "current_asset", 0),
    ("1300", "Prepaid Expenses", "asset", "current_asset", 0),
    ("1500", "Computer & Production Equipment", "asset", "fixed_asset", 0),
    ("1510", "Accumulated Depreciation", "asset", "contra_asset", 0),
    ("2000", "Accounts Payable", "liability", "payable", 1),
    ("2100", "Company Credit Card", "liability", "credit_card", 0),
    ("2200", "Sales Tax / VAT Payable", "liability", "tax", 1),
    ("2300", "Deferred Revenue (Client Prepayments)", "liability", "deferred_revenue", 0),
    ("2400", "Accrued Payroll", "liability", "current_liability", 0),
    ("2500", "Loans Payable", "liability", "long_term_liability", 0),
    ("3000", "Owner's Capital", "equity", "equity", 0),
    ("3100", "Owner's Drawings", "equity", "drawings", 0),
    ("3900", "Retained Earnings", "equity", "retained_earnings", 0),
    ("4000", "Retainer Revenue", "income", "revenue", 1),
    ("4100", "Project Revenue", "income", "revenue", 0),
    ("4200", "Media Management Fees", "income", "revenue", 0),
    ("4300", "Pass-through Media Billings", "income", "passthrough_revenue", 1),
    ("4400", "Reimbursed Client Expenses", "income", "revenue", 1),
    ("4900", "Other Income", "income", "other_income", 0),
    ("5000", "Pass-through Media Spend", "expense", "passthrough_cost", 0),
    ("5100", "Freelancers & Contractors", "expense", "cogs", 0),
    ("5200", "Production Costs (stock, print, video)", "expense", "cogs", 0),
    ("5300", "Client Project Software & Tools", "expense", "cogs", 0),
    ("6000", "Salaries & Wages", "expense", "opex", 0),
    ("6050", "Payroll Taxes & Benefits", "expense", "opex", 0),
    ("6100", "Rent", "expense", "opex", 0),
    ("6200", "Software & Subscriptions", "expense", "opex", 0),
    ("6300", "Agency Marketing & New Business", "expense", "opex", 0),
    ("6400", "Legal & Accounting", "expense", "opex", 0),
    ("6500", "Travel & Meals", "expense", "opex", 0),
    ("6600", "Office & Utilities", "expense", "opex", 0),
    ("6700", "Bank & Payment Processing Fees", "expense", "opex", 0),
    ("6800", "Depreciation", "expense", "opex", 0),
    ("6900", "Miscellaneous", "expense", "opex", 0),
    ("7000", "Interest Expense", "expense", "other_expense", 0),
]

DEFAULT_SETTINGS = {
    "company_name": "Techwon",
    "currency": "USD",
    "lock_date": "",
    "fiscal_year_start_month": "1",
    "invoice_prefix": "TW-",
    "next_invoice_number": "1001",
    "default_tax_rate_bp": "0",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('asset','liability','equity','income','expense')),
  subtype TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  is_system INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  retainer_cents INTEGER NOT NULL DEFAULT 0 CHECK (retainer_cents >= 0),
  payment_terms_days INTEGER NOT NULL DEFAULT 15 CHECK (payment_terms_days >= 0),
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  name TEXT NOT NULL,
  budget_cents INTEGER NOT NULL DEFAULT 0 CHECK (budget_cents >= 0),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','cancelled')),
  UNIQUE (client_id, name)
);

CREATE TABLE IF NOT EXISTS vendors (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reconciliations (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  statement_date TEXT NOT NULL,
  statement_balance_cents INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS journal_entries (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  memo TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL DEFAULT 'manual',
  source_id INTEGER,
  reverses_entry_id INTEGER REFERENCES journal_entries(id),
  reversed_by_entry_id INTEGER REFERENCES journal_entries(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS journal_lines (
  id INTEGER PRIMARY KEY,
  entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  debit_cents INTEGER NOT NULL DEFAULT 0 CHECK (debit_cents >= 0),
  credit_cents INTEGER NOT NULL DEFAULT 0 CHECK (credit_cents >= 0),
  client_id INTEGER REFERENCES clients(id),
  project_id INTEGER REFERENCES projects(id),
  memo TEXT NOT NULL DEFAULT '',
  reconciliation_id INTEGER REFERENCES reconciliations(id),
  CHECK ((debit_cents = 0) <> (credit_cents = 0))
);
CREATE INDEX IF NOT EXISTS ix_lines_account ON journal_lines(account_id);
CREATE INDEX IF NOT EXISTS ix_lines_entry ON journal_lines(entry_id);
CREATE INDEX IF NOT EXISTS ix_lines_client ON journal_lines(client_id);
CREATE INDEX IF NOT EXISTS ix_entries_date ON journal_entries(date);

CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY,
  number TEXT UNIQUE,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  project_id INTEGER REFERENCES projects(id),
  issue_date TEXT NOT NULL,
  due_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','issued','void')),
  tax_rate_bp INTEGER NOT NULL DEFAULT 0 CHECK (tax_rate_bp >= 0),
  notes TEXT NOT NULL DEFAULT '',
  retainer_period TEXT,
  entry_id INTEGER REFERENCES journal_entries(id),
  void_entry_id INTEGER REFERENCES journal_entries(id)
);

CREATE TABLE IF NOT EXISTS bills (
  id INTEGER PRIMARY KEY,
  vendor_id INTEGER NOT NULL REFERENCES vendors(id),
  bill_date TEXT NOT NULL,
  due_date TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  reference TEXT NOT NULL DEFAULT '',
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  expense_account_id INTEGER NOT NULL REFERENCES accounts(id),
  client_id INTEGER REFERENCES clients(id),
  project_id INTEGER REFERENCES projects(id),
  billable INTEGER NOT NULL DEFAULT 0,
  rebilled_invoice_id INTEGER REFERENCES invoices(id),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','void')),
  entry_id INTEGER REFERENCES journal_entries(id),
  void_entry_id INTEGER REFERENCES journal_entries(id)
);

CREATE TABLE IF NOT EXISTS invoice_lines (
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  description TEXT NOT NULL,
  quantity TEXT NOT NULL DEFAULT '1',
  unit_price_cents INTEGER NOT NULL,
  amount_cents INTEGER NOT NULL,
  revenue_account_id INTEGER NOT NULL REFERENCES accounts(id),
  taxable INTEGER NOT NULL DEFAULT 1,
  bill_id INTEGER REFERENCES bills(id)
);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  deposit_account_id INTEGER NOT NULL REFERENCES accounts(id),
  reference TEXT NOT NULL DEFAULT '',
  entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
  voided INTEGER NOT NULL DEFAULT 0,
  void_entry_id INTEGER REFERENCES journal_entries(id)
);

CREATE TABLE IF NOT EXISTS bill_payments (
  id INTEGER PRIMARY KEY,
  bill_id INTEGER NOT NULL REFERENCES bills(id),
  date TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  paid_from_account_id INTEGER NOT NULL REFERENCES accounts(id),
  reference TEXT NOT NULL DEFAULT '',
  entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
  voided INTEGER NOT NULL DEFAULT 0,
  void_entry_id INTEGER REFERENCES journal_entries(id)
);
"""


# --------------------------------------------------------------------------
# Connection, transactions, settings
# --------------------------------------------------------------------------

def connect(path):
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextlib.contextmanager
def tx(conn):
    """Write transaction. BEGIN IMMEDIATE takes the write lock up front so
    read-then-write sequences (e.g. invoice numbering) cannot race."""
    if conn.in_transaction:  # already inside a transaction: join it
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init_db(conn):
    conn.executescript(SCHEMA)
    with tx(conn):
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO accounts(code, name, type, subtype, is_system) VALUES (?,?,?,?,?)",
                DEFAULT_ACCOUNTS)


def get_settings(conn):
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def update_settings(conn, values):
    allowed = set(DEFAULT_SETTINGS)
    unknown = set(values) - allowed
    if unknown:
        raise LedgerError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
    if "lock_date" in values and values["lock_date"]:
        values["lock_date"] = _date(values["lock_date"], "lock_date")
    if "fiscal_year_start_month" in values:
        m = _int(values["fiscal_year_start_month"], "fiscal_year_start_month")
        if not 1 <= m <= 12:
            raise LedgerError("fiscal_year_start_month must be 1-12")
        values["fiscal_year_start_month"] = str(m)
    if "next_invoice_number" in values:
        if _int(values["next_invoice_number"], "next_invoice_number") < 1:
            raise LedgerError("next_invoice_number must be positive")
    if "default_tax_rate_bp" in values:
        if _int(values["default_tax_rate_bp"], "default_tax_rate_bp") < 0:
            raise LedgerError("default_tax_rate_bp cannot be negative")
    with tx(conn):
        for k, v in values.items():
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (str(v), k))
    return get_settings(conn)


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------

def _date(value, field="date"):
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        raise LedgerError(f"{field} must be a date in YYYY-MM-DD format") from None


def _int(value, field):
    if isinstance(value, bool):
        raise LedgerError(f"{field} must be a whole number")
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        return int(value)
    except (TypeError, ValueError):
        raise LedgerError(f"{field} must be a whole number") from None


def _cents(value, field="amount_cents", allow_zero=False, allow_negative=False):
    n = _int(value, field)
    if n < 0 and not allow_negative:
        raise LedgerError(f"{field} cannot be negative")
    if n == 0 and not allow_zero:
        raise LedgerError(f"{field} must be greater than zero")
    return n


def _qty(value):
    try:
        q = Decimal(str(value))
    except InvalidOperation:
        raise LedgerError("quantity must be a number") from None
    if not q.is_finite() or q <= 0:
        raise LedgerError("quantity must be greater than zero")
    return q


def _round(d):
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _today():
    return dt.date.today().isoformat()


def _check_open_period(conn, date):
    lock = get_settings(conn).get("lock_date", "")
    if lock and date <= lock:
        raise LedgerError(f"The books are locked through {lock}; cannot post on {date}")


def _one(conn, sql, args, what):
    row = conn.execute(sql, args).fetchone()
    if row is None:
        raise LedgerError(f"{what} not found")
    return row


def account_by_code(conn, code, must_be_active=True):
    row = _one(conn, "SELECT * FROM accounts WHERE code = ?", (str(code),), f"Account {code}")
    if must_be_active and not row["is_active"]:
        raise LedgerError(f"Account {code} is inactive")
    return row


def _client(conn, client_id):
    return _one(conn, "SELECT * FROM clients WHERE id = ?", (_int(client_id, "client_id"),), "Client")


def _project(conn, project_id, client_id=None):
    p = _one(conn, "SELECT * FROM projects WHERE id = ?", (_int(project_id, "project_id"),), "Project")
    if client_id is not None and p["client_id"] != int(client_id):
        raise LedgerError("Project does not belong to that client")
    return p


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------

def _post(conn, date, memo, lines, source_type="manual", source_id=None, reverses=None):
    """Insert a balanced entry. Caller must hold a transaction.

    lines: dicts with account_code (or account_id), debit_cents / credit_cents,
           optional client_id, project_id, memo.
    """
    date = _date(date)
    _check_open_period(conn, date)
    if not lines or len(lines) < 2:
        raise LedgerError("A journal entry needs at least two lines")
    prepared, total_d, total_c = [], 0, 0
    for i, ln in enumerate(lines, 1):
        if "account_id" in ln and ln["account_id"] is not None:
            acct = _one(conn, "SELECT * FROM accounts WHERE id = ?", (ln["account_id"],), "Account")
            if not acct["is_active"]:
                raise LedgerError(f"Account {acct['code']} is inactive")
        else:
            acct = account_by_code(conn, ln.get("account_code"))
        d = _cents(ln.get("debit_cents", 0) or 0, f"line {i} debit", allow_zero=True)
        c = _cents(ln.get("credit_cents", 0) or 0, f"line {i} credit", allow_zero=True)
        if (d == 0) == (c == 0):
            raise LedgerError(f"Line {i} must have either a debit or a credit (not both, not neither)")
        client_id = ln.get("client_id") or None
        project_id = ln.get("project_id") or None
        if client_id is not None:
            client_id = _client(conn, client_id)["id"]
        if project_id is not None:
            p = _project(conn, project_id, client_id)
            project_id, client_id = p["id"], p["client_id"]
        prepared.append((acct["id"], d, c, client_id, project_id, str(ln.get("memo", "") or "")))
        total_d += d
        total_c += c
    if total_d != total_c:
        raise LedgerError(f"Entry is out of balance: debits {total_d} != credits {total_c} (cents)")
    cur = conn.execute(
        "INSERT INTO journal_entries(date, memo, source_type, source_id, reverses_entry_id) VALUES (?,?,?,?,?)",
        (date, str(memo or ""), source_type, source_id, reverses))
    entry_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO journal_lines(entry_id, account_id, debit_cents, credit_cents, client_id, project_id, memo)"
        " VALUES (?,?,?,?,?,?,?)", [(entry_id, *p) for p in prepared])
    return entry_id


def _reverse(conn, entry_id, date, memo):
    entry = _one(conn, "SELECT * FROM journal_entries WHERE id = ?", (entry_id,), "Journal entry")
    if entry["reversed_by_entry_id"]:
        raise LedgerError("That entry has already been reversed")
    if entry["reverses_entry_id"]:
        raise LedgerError("A reversing entry cannot itself be reversed; post a new entry instead")
    date = _date(date)
    if date < entry["date"]:
        raise LedgerError("A reversal cannot be dated before the original entry")
    lines = conn.execute("SELECT * FROM journal_lines WHERE entry_id = ?", (entry_id,)).fetchall()
    rev_lines = [{"account_id": l["account_id"], "debit_cents": l["credit_cents"],
                  "credit_cents": l["debit_cents"], "client_id": l["client_id"],
                  "project_id": l["project_id"], "memo": l["memo"]} for l in lines]
    # Reversals must work even if an account was later deactivated.
    inactive = [l["account_id"] for l in lines
                if not conn.execute("SELECT is_active FROM accounts WHERE id=?", (l["account_id"],)).fetchone()[0]]
    for aid in inactive:
        conn.execute("UPDATE accounts SET is_active = 1 WHERE id = ?", (aid,))
    try:
        rid = _post(conn, date, memo or f"Reversal of entry #{entry_id}", rev_lines,
                    entry["source_type"], entry["source_id"], reverses=entry_id)
    finally:
        for aid in inactive:
            conn.execute("UPDATE accounts SET is_active = 0 WHERE id = ?", (aid,))
    conn.execute("UPDATE journal_entries SET reversed_by_entry_id = ? WHERE id = ?", (rid, entry_id))
    return rid


def post_journal_entry(conn, date, memo, lines):
    with tx(conn):
        return _post(conn, date, memo, lines, "manual")


def reverse_journal_entry(conn, entry_id, date=None, memo=""):
    with tx(conn):
        entry = _one(conn, "SELECT * FROM journal_entries WHERE id = ?", (_int(entry_id, "entry_id"),), "Journal entry")
        if entry["source_type"] != "manual":
            raise LedgerError(f"This entry belongs to a {entry['source_type']}; void the {entry['source_type']} instead")
        return _reverse(conn, entry["id"], date or _today(), memo)


def list_journal(conn, limit=200, start=None, end=None):
    q, args = "SELECT * FROM journal_entries WHERE 1=1", []
    if start:
        q += " AND date >= ?"; args.append(_date(start))
    if end:
        q += " AND date <= ?"; args.append(_date(end))
    q += " ORDER BY date DESC, id DESC LIMIT ?"
    args.append(_int(limit, "limit"))
    entries = [dict(r) for r in conn.execute(q, args)]
    for e in entries:
        e["lines"] = [dict(r) for r in conn.execute(
            "SELECT l.*, a.code AS account_code, a.name AS account_name FROM journal_lines l"
            " JOIN accounts a ON a.id = l.account_id WHERE l.entry_id = ? ORDER BY l.id", (e["id"],))]
    return entries


# --------------------------------------------------------------------------
# Accounts, clients, projects, vendors
# --------------------------------------------------------------------------

def list_accounts(conn, include_inactive=True):
    q = "SELECT * FROM accounts" + ("" if include_inactive else " WHERE is_active = 1") + " ORDER BY code"
    return [dict(r) for r in conn.execute(q)]


def create_account(conn, code, name, type, subtype):
    code, name = str(code).strip(), str(name).strip()
    if not code or not name:
        raise LedgerError("Account code and name are required")
    if type not in ACCOUNT_TYPES:
        raise LedgerError(f"type must be one of {', '.join(ACCOUNT_TYPES)}")
    if subtype not in SUBTYPES[type]:
        raise LedgerError(f"subtype for {type} must be one of {', '.join(SUBTYPES[type])}")
    with tx(conn):
        try:
            cur = conn.execute("INSERT INTO accounts(code, name, type, subtype) VALUES (?,?,?,?)",
                               (code, name, type, subtype))
        except sqlite3.IntegrityError:
            raise LedgerError(f"Account code {code} already exists") from None
        return cur.lastrowid


def update_account(conn, account_id, name=None, is_active=None):
    with tx(conn):
        acct = _one(conn, "SELECT * FROM accounts WHERE id = ?", (_int(account_id, "account_id"),), "Account")
        if name is not None:
            if not str(name).strip():
                raise LedgerError("Account name cannot be empty")
            conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (str(name).strip(), acct["id"]))
        if is_active is not None:
            if not is_active and acct["is_system"]:
                raise LedgerError("System accounts cannot be deactivated")
            conn.execute("UPDATE accounts SET is_active = ? WHERE id = ?", (1 if is_active else 0, acct["id"]))


def list_clients(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM clients ORDER BY name")]


def create_client(conn, name, email="", address="", retainer_cents=0, payment_terms_days=15):
    name = str(name or "").strip()
    if not name:
        raise LedgerError("Client name is required")
    with tx(conn):
        try:
            return conn.execute(
                "INSERT INTO clients(name, email, address, retainer_cents, payment_terms_days) VALUES (?,?,?,?,?)",
                (name, str(email or ""), str(address or ""),
                 _cents(retainer_cents or 0, "retainer_cents", allow_zero=True),
                 _int(payment_terms_days, "payment_terms_days"))).lastrowid
        except sqlite3.IntegrityError:
            raise LedgerError(f"Client '{name}' already exists") from None


def update_client(conn, client_id, **fields):
    allowed = {"name", "email", "address", "retainer_cents", "payment_terms_days", "is_active"}
    bad = set(fields) - allowed
    if bad:
        raise LedgerError(f"Unknown client field(s): {', '.join(sorted(bad))}")
    with tx(conn):
        c = _client(conn, client_id)
        if "retainer_cents" in fields:
            fields["retainer_cents"] = _cents(fields["retainer_cents"], "retainer_cents", allow_zero=True)
        if "payment_terms_days" in fields:
            fields["payment_terms_days"] = _int(fields["payment_terms_days"], "payment_terms_days")
        if "is_active" in fields:
            fields["is_active"] = 1 if fields["is_active"] else 0
        for k, v in fields.items():
            conn.execute(f"UPDATE clients SET {k} = ? WHERE id = ?", (v, c["id"]))


def list_projects(conn, client_id=None):
    q = "SELECT p.*, c.name AS client_name FROM projects p JOIN clients c ON c.id = p.client_id"
    args = []
    if client_id:
        q += " WHERE p.client_id = ?"; args.append(_int(client_id, "client_id"))
    return [dict(r) for r in conn.execute(q + " ORDER BY c.name, p.name", args)]


def create_project(conn, client_id, name, budget_cents=0):
    name = str(name or "").strip()
    if not name:
        raise LedgerError("Project name is required")
    with tx(conn):
        c = _client(conn, client_id)
        try:
            return conn.execute("INSERT INTO projects(client_id, name, budget_cents) VALUES (?,?,?)",
                                (c["id"], name, _cents(budget_cents or 0, "budget_cents", allow_zero=True))).lastrowid
        except sqlite3.IntegrityError:
            raise LedgerError(f"Project '{name}' already exists for this client") from None


def update_project(conn, project_id, status=None, budget_cents=None):
    with tx(conn):
        p = _project(conn, project_id)
        if status is not None:
            if status not in ("active", "completed", "cancelled"):
                raise LedgerError("status must be active, completed or cancelled")
            conn.execute("UPDATE projects SET status = ? WHERE id = ?", (status, p["id"]))
        if budget_cents is not None:
            conn.execute("UPDATE projects SET budget_cents = ? WHERE id = ?",
                         (_cents(budget_cents, "budget_cents", allow_zero=True), p["id"]))


def list_vendors(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM vendors ORDER BY name")]


def create_vendor(conn, name, email=""):
    name = str(name or "").strip()
    if not name:
        raise LedgerError("Vendor name is required")
    with tx(conn):
        try:
            return conn.execute("INSERT INTO vendors(name, email) VALUES (?,?)", (name, str(email or ""))).lastrowid
        except sqlite3.IntegrityError:
            raise LedgerError(f"Vendor '{name}' already exists") from None


# --------------------------------------------------------------------------
# Invoices (A/R)
# --------------------------------------------------------------------------

def _insert_invoice_lines(conn, invoice_id, lines):
    if not lines:
        raise LedgerError("An invoice needs at least one line")
    for i, ln in enumerate(lines, 1):
        desc = str(ln.get("description") or "").strip()
        if not desc:
            raise LedgerError(f"Line {i}: description is required")
        qty = _qty(ln.get("quantity", 1))
        price = _cents(ln.get("unit_price_cents"), f"line {i} unit price", allow_zero=True, allow_negative=True)
        acct = account_by_code(conn, ln.get("revenue_account_code") or "4100")
        if acct["type"] != "income":
            raise LedgerError(f"Line {i}: account {acct['code']} is not an income account")
        conn.execute(
            "INSERT INTO invoice_lines(invoice_id, description, quantity, unit_price_cents, amount_cents,"
            " revenue_account_id, taxable, bill_id) VALUES (?,?,?,?,?,?,?,?)",
            (invoice_id, desc, str(qty), price, _round(qty * price), acct["id"],
             1 if ln.get("taxable", True) else 0, ln.get("bill_id")))


def create_invoice(conn, client_id, issue_date, lines, due_date=None, project_id=None,
                   tax_rate_bp=None, notes="", retainer_period=None):
    """Create a DRAFT invoice. Nothing hits the ledger until issue_invoice()."""
    with tx(conn):
        c = _client(conn, client_id)
        if not c["is_active"]:
            raise LedgerError("Client is inactive")
        issue_date = _date(issue_date, "issue_date")
        if due_date:
            due_date = _date(due_date, "due_date")
        else:
            due_date = (dt.date.fromisoformat(issue_date) + dt.timedelta(days=c["payment_terms_days"])).isoformat()
        if due_date < issue_date:
            raise LedgerError("due_date cannot be before issue_date")
        if project_id:
            project_id = _project(conn, project_id, c["id"])["id"]
        if tax_rate_bp is None or tax_rate_bp == "":
            tax_rate_bp = get_settings(conn)["default_tax_rate_bp"]
        tax_rate_bp = _int(tax_rate_bp, "tax_rate_bp")
        if tax_rate_bp < 0:
            raise LedgerError("tax_rate_bp cannot be negative")
        inv_id = conn.execute(
            "INSERT INTO invoices(client_id, project_id, issue_date, due_date, tax_rate_bp, notes, retainer_period)"
            " VALUES (?,?,?,?,?,?,?)",
            (c["id"], project_id or None, issue_date, due_date, tax_rate_bp, str(notes or ""), retainer_period)).lastrowid
        _insert_invoice_lines(conn, inv_id, lines)
        totals = _invoice_totals(conn, inv_id)
        if totals["total_cents"] <= 0:
            raise LedgerError("Invoice total must be greater than zero")
        return inv_id


def _invoice_totals(conn, invoice_id):
    inv = conn.execute("SELECT tax_rate_bp FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    sub = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM invoice_lines WHERE invoice_id = ?",
                       (invoice_id,)).fetchone()[0]
    taxable = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM invoice_lines WHERE invoice_id = ? AND taxable = 1",
                           (invoice_id,)).fetchone()[0]
    tax = _round(Decimal(taxable) * inv["tax_rate_bp"] / Decimal(10000))
    paid = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE invoice_id = ? AND voided = 0",
                        (invoice_id,)).fetchone()[0]
    total = sub + tax
    return {"subtotal_cents": sub, "tax_cents": tax, "total_cents": total,
            "paid_cents": paid, "balance_cents": total - paid}


def _invoice_status(inv, totals, today=None):
    if inv["status"] != "issued":
        return inv["status"]
    if totals["balance_cents"] <= 0:
        return "paid"
    if inv["due_date"] < (today or _today()):
        return "overdue"
    return "partial" if totals["paid_cents"] > 0 else "open"


def get_invoice(conn, invoice_id):
    inv = _one(conn, "SELECT i.*, c.name AS client_name, c.email AS client_email, c.address AS client_address,"
                     " p.name AS project_name FROM invoices i JOIN clients c ON c.id = i.client_id"
                     " LEFT JOIN projects p ON p.id = i.project_id WHERE i.id = ?",
               (_int(invoice_id, "invoice_id"),), "Invoice")
    out = dict(inv)
    totals = _invoice_totals(conn, inv["id"])
    out.update(totals)
    out["display_status"] = _invoice_status(inv, totals)
    out["lines"] = [dict(r) for r in conn.execute(
        "SELECT l.*, a.code AS revenue_account_code, a.name AS revenue_account_name FROM invoice_lines l"
        " JOIN accounts a ON a.id = l.revenue_account_id WHERE invoice_id = ? ORDER BY l.id", (inv["id"],))]
    out["payments"] = [dict(r) for r in conn.execute(
        "SELECT p.*, a.code AS deposit_account_code FROM payments p JOIN accounts a ON a.id = p.deposit_account_id"
        " WHERE invoice_id = ? ORDER BY date, id", (inv["id"],))]
    return out


def list_invoices(conn, client_id=None, status=None):
    q, args = ("SELECT i.*, c.name AS client_name, p.name AS project_name FROM invoices i"
               " JOIN clients c ON c.id = i.client_id LEFT JOIN projects p ON p.id = i.project_id WHERE 1=1"), []
    if client_id:
        q += " AND i.client_id = ?"; args.append(_int(client_id, "client_id"))
    rows = []
    for r in conn.execute(q + " ORDER BY i.issue_date DESC, i.id DESC", args):
        d = dict(r)
        totals = _invoice_totals(conn, r["id"])
        d.update(totals)
        d["display_status"] = _invoice_status(r, totals)
        if status and d["display_status"] != status:
            continue
        rows.append(d)
    return rows


def update_draft_invoice(conn, invoice_id, lines=None, **fields):
    allowed = {"issue_date", "due_date", "tax_rate_bp", "notes", "project_id"}
    bad = set(fields) - allowed
    if bad:
        raise LedgerError(f"Unknown invoice field(s): {', '.join(sorted(bad))}")
    with tx(conn):
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (_int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] != "draft":
            raise LedgerError("Only draft invoices can be edited; void and re-issue instead")
        for k in ("issue_date", "due_date"):
            if k in fields:
                fields[k] = _date(fields[k], k)
        if "tax_rate_bp" in fields:
            fields["tax_rate_bp"] = _int(fields["tax_rate_bp"], "tax_rate_bp")
            if fields["tax_rate_bp"] < 0:
                raise LedgerError("tax_rate_bp cannot be negative")
        if fields.get("project_id"):
            fields["project_id"] = _project(conn, fields["project_id"], inv["client_id"])["id"]
        for k, v in fields.items():
            conn.execute(f"UPDATE invoices SET {k} = ? WHERE id = ?", (v or None if k == "project_id" else v, inv["id"]))
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (inv["id"],)).fetchone()
        if inv["due_date"] < inv["issue_date"]:
            raise LedgerError("due_date cannot be before issue_date")
        if lines is not None:
            if any(l.get("bill_id") for l in lines) or conn.execute(
                    "SELECT 1 FROM invoice_lines WHERE invoice_id = ? AND bill_id IS NOT NULL", (inv["id"],)).fetchone():
                raise LedgerError("Rebilled-expense invoices can't have lines edited; delete the draft and rebill again")
            conn.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (inv["id"],))
            _insert_invoice_lines(conn, inv["id"], lines)
        if _invoice_totals(conn, inv["id"])["total_cents"] <= 0:
            raise LedgerError("Invoice total must be greater than zero")


def delete_draft_invoice(conn, invoice_id):
    with tx(conn):
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (_int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] != "draft":
            raise LedgerError("Only drafts can be deleted; void issued invoices instead")
        conn.execute("UPDATE bills SET rebilled_invoice_id = NULL WHERE rebilled_invoice_id = ?", (inv["id"],))
        conn.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (inv["id"],))
        conn.execute("DELETE FROM invoices WHERE id = ?", (inv["id"],))


def issue_invoice(conn, invoice_id):
    """Assign the next invoice number and post: Dr A/R, Cr revenue (+ Cr sales tax)."""
    with tx(conn):
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (_int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] != "draft":
            raise LedgerError(f"Invoice is already {inv['status']}")
        totals = _invoice_totals(conn, inv["id"])
        if totals["total_cents"] <= 0:
            raise LedgerError("Invoice total must be greater than zero")
        s = get_settings(conn)
        n = int(s["next_invoice_number"])
        number = f"{s['invoice_prefix']}{n}"
        while conn.execute("SELECT 1 FROM invoices WHERE number = ?", (number,)).fetchone():
            n += 1
            number = f"{s['invoice_prefix']}{n}"
        conn.execute("UPDATE settings SET value = ? WHERE key = 'next_invoice_number'", (str(n + 1),))

        cid, pid = inv["client_id"], inv["project_id"]
        lines = [{"account_code": AR, "debit_cents": totals["total_cents"], "client_id": cid,
                  "project_id": pid, "memo": f"Invoice {number}"}]
        for l in conn.execute("SELECT * FROM invoice_lines WHERE invoice_id = ? AND amount_cents != 0", (inv["id"],)):
            amt = l["amount_cents"]
            lines.append({"account_id": l["revenue_account_id"], "client_id": cid, "project_id": pid,
                          "memo": l["description"],
                          **({"credit_cents": amt} if amt > 0 else {"debit_cents": -amt})})
        if totals["tax_cents"]:
            lines.append({"account_code": SALES_TAX, "credit_cents": totals["tax_cents"],
                          "client_id": cid, "memo": f"Tax on {number}"})
        eid = _post(conn, inv["issue_date"], f"Invoice {number}", lines, "invoice", inv["id"])
        conn.execute("UPDATE invoices SET status = 'issued', number = ?, entry_id = ? WHERE id = ?",
                     (number, eid, inv["id"]))
        return number


def void_invoice(conn, invoice_id, date=None):
    with tx(conn):
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (_int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] != "issued":
            raise LedgerError("Only issued invoices can be voided (delete drafts instead)")
        if _invoice_totals(conn, inv["id"])["paid_cents"] > 0:
            raise LedgerError("Void the payments on this invoice first")
        date = date or max(_today(), inv["issue_date"])
        rid = _reverse(conn, inv["entry_id"], date, f"Void invoice {inv['number']}")
        conn.execute("UPDATE invoices SET status = 'void', void_entry_id = ? WHERE id = ?", (rid, inv["id"]))
        conn.execute("UPDATE bills SET rebilled_invoice_id = NULL WHERE rebilled_invoice_id = ?", (inv["id"],))


def record_payment(conn, invoice_id, date, amount_cents, deposit_account_code=CASH, reference=""):
    """Customer payment: Dr bank, Cr A/R."""
    with tx(conn):
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (_int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] != "issued":
            raise LedgerError("Payments can only be recorded against issued invoices")
        amount = _cents(amount_cents)
        date = _date(date)
        if date < inv["issue_date"]:
            raise LedgerError("Payment date cannot be before the invoice date")
        bal = _invoice_totals(conn, inv["id"])["balance_cents"]
        if amount > bal:
            raise LedgerError(f"Payment exceeds the open balance ({bal} cents). Record overpayments as a "
                              "journal entry to Deferred Revenue.")
        bank = account_by_code(conn, deposit_account_code)
        if bank["subtype"] != "cash":
            raise LedgerError("Deposit account must be a bank/cash account")
        eid = _post(conn, date, f"Payment for {inv['number']}", [
            {"account_id": bank["id"], "debit_cents": amount, "client_id": inv["client_id"]},
            {"account_code": AR, "credit_cents": amount, "client_id": inv["client_id"],
             "project_id": inv["project_id"]},
        ], "payment", inv["id"])
        return conn.execute(
            "INSERT INTO payments(invoice_id, date, amount_cents, deposit_account_id, reference, entry_id)"
            " VALUES (?,?,?,?,?,?)", (inv["id"], date, amount, bank["id"], str(reference or ""), eid)).lastrowid


def void_payment(conn, payment_id, date=None):
    with tx(conn):
        p = _one(conn, "SELECT * FROM payments WHERE id = ?", (_int(payment_id, "payment_id"),), "Payment")
        if p["voided"]:
            raise LedgerError("Payment is already voided")
        _assert_not_reconciled(conn, p["entry_id"])
        rid = _reverse(conn, p["entry_id"], date or max(_today(), p["date"]), f"Void payment #{p['id']}")
        conn.execute("UPDATE payments SET voided = 1, void_entry_id = ? WHERE id = ?", (rid, p["id"]))


def generate_retainer_invoices(conn, period, issue_date=None, auto_issue=True):
    """Create one retainer invoice per active client with a retainer, for 'YYYY-MM'.
    Idempotent: clients that already have a non-void invoice for the period are skipped."""
    try:
        first = dt.date.fromisoformat(f"{period}-01")
    except (TypeError, ValueError):
        raise LedgerError("period must be YYYY-MM") from None
    issue_date = _date(issue_date or first.isoformat(), "issue_date")
    label = first.strftime("%B %Y")
    created, skipped = [], []
    with tx(conn):
        for c in conn.execute("SELECT * FROM clients WHERE is_active = 1 AND retainer_cents > 0 ORDER BY name").fetchall():
            if conn.execute("SELECT 1 FROM invoices WHERE client_id = ? AND retainer_period = ? AND status != 'void'",
                            (c["id"], period)).fetchone():
                skipped.append(c["name"])
                continue
            inv_id = create_invoice(conn, c["id"], issue_date, [{
                "description": f"Monthly retainer - {label}", "quantity": 1,
                "unit_price_cents": c["retainer_cents"], "revenue_account_code": RETAINER_REVENUE}],
                retainer_period=period)
            number = issue_invoice(conn, inv_id) if auto_issue else None
            created.append({"invoice_id": inv_id, "client": c["name"], "number": number})
    return {"created": created, "skipped": skipped}


# --------------------------------------------------------------------------
# Bills & expenses (A/P)
# --------------------------------------------------------------------------

def _bill_paid(conn, bill_id):
    return conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM bill_payments WHERE bill_id = ? AND voided = 0",
                        (bill_id,)).fetchone()[0]


def create_bill(conn, vendor_id, bill_date, amount_cents, expense_account_code, description="",
                due_date=None, client_id=None, project_id=None, billable=False, reference="", paid_from=None):
    """Vendor bill: Dr expense, Cr A/P. With paid_from set it is a QuickBooks-style
    'expense' (paid on the spot) and the payment is posted immediately too."""
    with tx(conn):
        v = _one(conn, "SELECT * FROM vendors WHERE id = ?", (_int(vendor_id, "vendor_id"),), "Vendor")
        bill_date = _date(bill_date, "bill_date")
        due_date = _date(due_date, "due_date") if due_date else bill_date
        if due_date < bill_date:
            raise LedgerError("due_date cannot be before bill_date")
        amount = _cents(amount_cents)
        acct = account_by_code(conn, expense_account_code)
        if acct["type"] not in ("expense", "asset"):
            raise LedgerError("Bills must post to an expense (or prepaid/fixed asset) account")
        if client_id:
            client_id = _client(conn, client_id)["id"]
        if project_id:
            p = _project(conn, project_id, client_id)
            project_id, client_id = p["id"], p["client_id"]
        if billable and not client_id:
            raise LedgerError("Billable expenses must be assigned to a client")
        eid = _post(conn, bill_date, f"Bill from {v['name']}: {description}".strip(": "), [
            {"account_id": acct["id"], "debit_cents": amount, "client_id": client_id,
             "project_id": project_id, "memo": description},
            {"account_code": AP, "credit_cents": amount, "memo": v["name"]},
        ], "bill")
        bid = conn.execute(
            "INSERT INTO bills(vendor_id, bill_date, due_date, description, reference, amount_cents,"
            " expense_account_id, client_id, project_id, billable, entry_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (v["id"], bill_date, due_date, str(description or ""), str(reference or ""), amount, acct["id"],
             client_id, project_id, 1 if billable else 0, eid)).lastrowid
        conn.execute("UPDATE journal_entries SET source_id = ? WHERE id = ?", (bid, eid))
        if paid_from:
            pay_bill(conn, bid, bill_date, amount, paid_from)
        return bid


def pay_bill(conn, bill_id, date, amount_cents, paid_from_account_code=CASH, reference=""):
    """Dr A/P, Cr bank or credit card."""
    with tx(conn):
        b = _one(conn, "SELECT b.*, v.name AS vendor_name FROM bills b JOIN vendors v ON v.id = b.vendor_id"
                       " WHERE b.id = ?", (_int(bill_id, "bill_id"),), "Bill")
        if b["status"] != "open":
            raise LedgerError("Bill is void")
        date = _date(date)
        if date < b["bill_date"]:
            raise LedgerError("Payment date cannot be before the bill date")
        amount = _cents(amount_cents)
        bal = b["amount_cents"] - _bill_paid(conn, b["id"])
        if amount > bal:
            raise LedgerError(f"Payment exceeds the open bill balance ({bal} cents)")
        src = account_by_code(conn, paid_from_account_code)
        if src["subtype"] not in ("cash", "credit_card"):
            raise LedgerError("Pay bills from a bank/cash or credit card account")
        eid = _post(conn, date, f"Payment to {b['vendor_name']}", [
            {"account_code": AP, "debit_cents": amount, "memo": b["vendor_name"]},
            {"account_id": src["id"], "credit_cents": amount, "memo": b["vendor_name"]},
        ], "bill_payment", b["id"])
        return conn.execute(
            "INSERT INTO bill_payments(bill_id, date, amount_cents, paid_from_account_id, reference, entry_id)"
            " VALUES (?,?,?,?,?,?)", (b["id"], date, amount, src["id"], str(reference or ""), eid)).lastrowid


def void_bill(conn, bill_id, date=None):
    with tx(conn):
        b = _one(conn, "SELECT * FROM bills WHERE id = ?", (_int(bill_id, "bill_id"),), "Bill")
        if b["status"] != "open":
            raise LedgerError("Bill is already void")
        if _bill_paid(conn, b["id"]) > 0:
            raise LedgerError("Void the payments on this bill first")
        if b["rebilled_invoice_id"]:
            raise LedgerError("This expense was rebilled to a client; void or delete that invoice first")
        rid = _reverse(conn, b["entry_id"], date or max(_today(), b["bill_date"]), f"Void bill #{b['id']}")
        conn.execute("UPDATE bills SET status = 'void', void_entry_id = ? WHERE id = ?", (rid, b["id"]))


def void_bill_payment(conn, bill_payment_id, date=None):
    with tx(conn):
        p = _one(conn, "SELECT * FROM bill_payments WHERE id = ?", (_int(bill_payment_id, "bill_payment_id"),),
                 "Bill payment")
        if p["voided"]:
            raise LedgerError("Bill payment is already voided")
        _assert_not_reconciled(conn, p["entry_id"])
        rid = _reverse(conn, p["entry_id"], date or max(_today(), p["date"]), f"Void bill payment #{p['id']}")
        conn.execute("UPDATE bill_payments SET voided = 1, void_entry_id = ? WHERE id = ?", (rid, p["id"]))


def list_bills(conn, status=None):
    rows = []
    for r in conn.execute(
            "SELECT b.*, v.name AS vendor_name, a.code AS expense_account_code, a.name AS expense_account_name,"
            " c.name AS client_name, p.name AS project_name, i.number AS rebilled_invoice_number"
            " FROM bills b JOIN vendors v ON v.id = b.vendor_id JOIN accounts a ON a.id = b.expense_account_id"
            " LEFT JOIN clients c ON c.id = b.client_id LEFT JOIN projects p ON p.id = b.project_id"
            " LEFT JOIN invoices i ON i.id = b.rebilled_invoice_id ORDER BY b.bill_date DESC, b.id DESC"):
        d = dict(r)
        d["paid_cents"] = _bill_paid(conn, r["id"])
        d["balance_cents"] = r["amount_cents"] - d["paid_cents"] if r["status"] == "open" else 0
        if r["status"] == "void":
            d["display_status"] = "void"
        elif d["balance_cents"] == 0:
            d["display_status"] = "paid"
        elif r["due_date"] < _today():
            d["display_status"] = "overdue"
        else:
            d["display_status"] = "partial" if d["paid_cents"] else "open"
        d["payments"] = [dict(x) for x in conn.execute(
            "SELECT * FROM bill_payments WHERE bill_id = ? ORDER BY date, id", (r["id"],))]
        if status and d["display_status"] != status:
            continue
        rows.append(d)
    return rows


def unbilled_expenses(conn, client_id=None):
    q = ("SELECT b.*, v.name AS vendor_name, c.name AS client_name, a.subtype AS expense_subtype FROM bills b"
         " JOIN vendors v ON v.id = b.vendor_id JOIN clients c ON c.id = b.client_id"
         " JOIN accounts a ON a.id = b.expense_account_id"
         " WHERE b.billable = 1 AND b.status = 'open' AND b.rebilled_invoice_id IS NULL")
    args = []
    if client_id:
        q += " AND b.client_id = ?"; args.append(_int(client_id, "client_id"))
    return [dict(r) for r in conn.execute(q + " ORDER BY b.bill_date", args)]


def create_rebill_invoice(conn, client_id, issue_date, markup_bp=0, bill_ids=None):
    """Turn a client's unbilled billable expenses into a draft invoice.
    Pass-through media costs are rebilled to Pass-through Media Billings so
    net revenue (AGI) stays accurate; everything else to Reimbursed Expenses."""
    markup_bp = _int(markup_bp, "markup_bp")
    if markup_bp < 0:
        raise LedgerError("markup_bp cannot be negative")
    with tx(conn):
        items = unbilled_expenses(conn, client_id)
        if bill_ids:
            wanted = {int(b) for b in bill_ids}
            items = [b for b in items if b["id"] in wanted]
            if len(items) != len(wanted):
                raise LedgerError("Some selected bills are not unbilled billable expenses for this client")
        if not items:
            raise LedgerError("No unbilled billable expenses for this client")
        lines = []
        for b in items:
            price = _round(Decimal(b["amount_cents"]) * (10000 + markup_bp) / Decimal(10000))
            lines.append({
                "description": f"{b['description'] or 'Expense'} ({b['vendor_name']}, {b['bill_date']})",
                "quantity": 1, "unit_price_cents": price, "bill_id": b["id"],
                "revenue_account_code": PASSTHROUGH_REVENUE if b["expense_subtype"] == "passthrough_cost"
                else REIMBURSED_REVENUE})
        project_ids = {b["project_id"] for b in items}
        inv_id = create_invoice(conn, client_id, issue_date, lines,
                                project_id=project_ids.pop() if len(project_ids) == 1 else None,
                                notes="Reimbursable expenses" + (f" (+{markup_bp / 100:g}% markup)" if markup_bp else ""))
        conn.executemany("UPDATE bills SET rebilled_invoice_id = ? WHERE id = ?", [(inv_id, b["id"]) for b in items])
        return inv_id


# --------------------------------------------------------------------------
# Bank reconciliation
# --------------------------------------------------------------------------

def _assert_not_reconciled(conn, entry_id):
    if conn.execute("SELECT 1 FROM journal_lines WHERE entry_id = ? AND reconciliation_id IS NOT NULL",
                    (entry_id,)).fetchone():
        raise LedgerError("This transaction has been reconciled against a bank statement and cannot be voided")


def reconciliation_worksheet(conn, account_code, statement_date):
    acct = account_by_code(conn, account_code, must_be_active=False)
    if acct["subtype"] not in ("cash", "credit_card"):
        raise LedgerError("Only bank and credit card accounts can be reconciled")
    statement_date = _date(statement_date, "statement_date")
    sign = 1 if acct["type"] in DEBIT_NORMAL else -1
    cleared = conn.execute(
        "SELECT COALESCE(SUM(debit_cents - credit_cents),0) FROM journal_lines WHERE account_id = ?"
        " AND reconciliation_id IS NOT NULL", (acct["id"],)).fetchone()[0] * sign
    uncleared = [dict(r) for r in conn.execute(
        "SELECT l.id, e.date, e.memo AS entry_memo, l.memo, l.debit_cents, l.credit_cents,"
        " (l.debit_cents - l.credit_cents) * ? AS amount_cents FROM journal_lines l"
        " JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id = ? AND l.reconciliation_id IS NULL"
        " AND e.date <= ? ORDER BY e.date, l.id", (sign, acct["id"], statement_date))]
    last = conn.execute("SELECT * FROM reconciliations WHERE account_id = ? ORDER BY statement_date DESC, id DESC"
                        " LIMIT 1", (acct["id"],)).fetchone()
    return {"account": dict(acct), "statement_date": statement_date, "cleared_balance_cents": cleared,
            "uncleared": uncleared, "last_reconciliation": dict(last) if last else None}


def reconcile(conn, account_code, statement_date, statement_balance_cents, line_ids):
    """Mark lines as cleared. Succeeds only if cleared balance == statement balance."""
    with tx(conn):
        ws = reconciliation_worksheet(conn, account_code, statement_date)
        target = _cents(statement_balance_cents, "statement_balance_cents", allow_zero=True, allow_negative=True)
        available = {l["id"]: l for l in ws["uncleared"]}
        ids = [int(i) for i in (line_ids or [])]
        missing = [i for i in ids if i not in available]
        if missing:
            raise LedgerError(f"Lines not available for this reconciliation: {missing}")
        new_balance = ws["cleared_balance_cents"] + sum(available[i]["amount_cents"] for i in ids)
        if new_balance != target:
            raise LedgerError(f"Not reconciled: cleared balance would be {new_balance} cents but the "
                              f"statement says {target}. Difference: {target - new_balance} cents.")
        rid = conn.execute(
            "INSERT INTO reconciliations(account_id, statement_date, statement_balance_cents) VALUES (?,?,?)",
            (ws["account"]["id"], ws["statement_date"], target)).lastrowid
        conn.executemany("UPDATE journal_lines SET reconciliation_id = ? WHERE id = ?", [(rid, i) for i in ids])
        return rid


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def _balances(conn, start=None, end=None):
    cond, args = [], []
    if start:
        cond.append("e.date >= ?"); args.append(_date(start, "start"))
    if end:
        cond.append("e.date <= ?"); args.append(_date(end, "end"))
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    rows = conn.execute(
        "SELECT a.*, COALESCE(s.d,0) AS debit_cents, COALESCE(s.c,0) AS credit_cents FROM accounts a"
        " LEFT JOIN (SELECT l.account_id, SUM(l.debit_cents) d, SUM(l.credit_cents) c FROM journal_lines l"
        f" JOIN journal_entries e ON e.id = l.entry_id {where} GROUP BY l.account_id) s ON s.account_id = a.id"
        " ORDER BY a.code", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        net = r["debit_cents"] - r["credit_cents"]
        d["balance_cents"] = net if r["type"] in DEBIT_NORMAL else -net
        out.append(d)
    return out


def _fy_start(conn, as_of):
    m = int(get_settings(conn)["fiscal_year_start_month"])
    d = dt.date.fromisoformat(as_of)
    return dt.date(d.year if d.month >= m else d.year - 1, m, 1).isoformat()


def trial_balance(conn, as_of=None):
    as_of = _date(as_of or _today(), "as_of")
    rows = [r for r in _balances(conn, end=as_of) if r["debit_cents"] or r["credit_cents"]]
    for r in rows:
        net = r["debit_cents"] - r["credit_cents"]
        r["tb_debit_cents"], r["tb_credit_cents"] = (net, 0) if net > 0 else (0, -net)
    td = sum(r["tb_debit_cents"] for r in rows)
    tc = sum(r["tb_credit_cents"] for r in rows)
    return {"as_of": as_of, "rows": rows, "total_debit_cents": td, "total_credit_cents": tc, "balanced": td == tc}


def _sum(rows, *subtypes):
    return sum(r["balance_cents"] for r in rows if r["subtype"] in subtypes)


def profit_and_loss(conn, start, end):
    start, end = _date(start, "start"), _date(end, "end")
    if end < start:
        raise LedgerError("end must be on or after start")
    rows = [r for r in _balances(conn, start, end) if r["type"] in ("income", "expense")]

    def section(*subtypes):
        return [{"code": r["code"], "name": r["name"], "amount_cents": r["balance_cents"]}
                for r in rows if r["subtype"] in subtypes and r["balance_cents"]]

    gross_billings = _sum(rows, "revenue", "passthrough_revenue")
    passthrough = _sum(rows, "passthrough_cost")
    net_revenue = gross_billings - passthrough
    direct = _sum(rows, "cogs")
    gross_profit = net_revenue - direct
    opex = _sum(rows, "opex")
    operating = gross_profit - opex
    other = _sum(rows, "other_income") - _sum(rows, "other_expense")
    net_income = operating + other

    def pct(x):
        return round(100 * x / net_revenue, 1) if net_revenue else None

    return {
        "start": start, "end": end,
        "revenue": section("revenue", "passthrough_revenue"), "gross_billings_cents": gross_billings,
        "passthrough": section("passthrough_cost"), "passthrough_cents": passthrough,
        "net_revenue_cents": net_revenue,
        "direct_costs": section("cogs"), "direct_costs_cents": direct,
        "gross_profit_cents": gross_profit, "gross_margin_pct": pct(gross_profit),
        "opex": section("opex"), "opex_cents": opex,
        "operating_income_cents": operating, "operating_margin_pct": pct(operating),
        "other": section("other_income", "other_expense"), "other_net_cents": other,
        "net_income_cents": net_income, "net_margin_pct": pct(net_income),
    }


def _net_income(rows):
    return sum(r["balance_cents"] if r["type"] == "income" else -r["balance_cents"]
               for r in rows if r["type"] in ("income", "expense"))


def balance_sheet(conn, as_of=None):
    as_of = _date(as_of or _today(), "as_of")
    fy = _fy_start(conn, as_of)
    rows = _balances(conn, end=as_of)
    prior = (dt.date.fromisoformat(fy) - dt.timedelta(days=1)).isoformat()
    retained = _net_income(_balances(conn, end=prior))
    current = _net_income(_balances(conn, fy, as_of))

    def section(t):
        return [{"code": r["code"], "name": r["name"], "subtype": r["subtype"], "amount_cents": r["balance_cents"]}
                for r in rows if r["type"] == t and r["balance_cents"]]

    assets, liabilities, equity = section("asset"), section("liability"), section("equity")
    if retained:
        equity.append({"code": "", "name": "Retained Earnings (prior years, computed)", "subtype": "computed",
                       "amount_cents": retained})
    if current:
        equity.append({"code": "", "name": "Net Income (current fiscal year)", "subtype": "computed",
                       "amount_cents": current})
    ta = sum(a["amount_cents"] for a in assets)
    tl = sum(a["amount_cents"] for a in liabilities)
    te = sum(a["amount_cents"] for a in equity)
    return {"as_of": as_of, "fiscal_year_start": fy, "assets": assets, "liabilities": liabilities,
            "equity": equity, "total_assets_cents": ta, "total_liabilities_cents": tl, "total_equity_cents": te,
            "balanced": ta == tl + te}


BUCKETS = ("current", "1_30", "31_60", "61_90", "over_90")


def _bucket(days_overdue):
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1_30"
    if days_overdue <= 60:
        return "31_60"
    if days_overdue <= 90:
        return "61_90"
    return "over_90"


def ar_aging(conn, as_of=None):
    as_of = _date(as_of or _today(), "as_of")
    asof_d = dt.date.fromisoformat(as_of)
    items, by_client = [], {}
    for inv in conn.execute(
            "SELECT i.*, c.name AS client_name FROM invoices i JOIN clients c ON c.id = i.client_id"
            " WHERE i.status IN ('issued','void') AND i.issue_date <= ?", (as_of,)).fetchall():
        if inv["status"] == "void":
            vdate = conn.execute("SELECT date FROM journal_entries WHERE id = ?", (inv["void_entry_id"],)).fetchone()[0]
            if vdate <= as_of:
                continue
        t = _invoice_totals(conn, inv["id"])
        paid = conn.execute(
            "SELECT COALESCE(SUM(p.amount_cents),0) FROM payments p WHERE p.invoice_id = ? AND p.date <= ?"
            " AND NOT (p.voided = 1 AND (SELECT date FROM journal_entries WHERE id = p.void_entry_id) <= ?)",
            (inv["id"], as_of, as_of)).fetchone()[0]
        bal = t["total_cents"] - paid
        if bal <= 0:
            continue
        days = (asof_d - dt.date.fromisoformat(inv["due_date"])).days
        b = _bucket(days)
        items.append({"invoice_id": inv["id"], "number": inv["number"], "client": inv["client_name"],
                      "issue_date": inv["issue_date"], "due_date": inv["due_date"],
                      "days_overdue": max(days, 0), "bucket": b, "balance_cents": bal})
        row = by_client.setdefault(inv["client_name"], {"client": inv["client_name"], **{k: 0 for k in BUCKETS},
                                                         "total": 0})
        row[b] += bal
        row["total"] += bal
    totals = {k: sum(r[k] for r in by_client.values()) for k in (*BUCKETS, "total")}
    return {"as_of": as_of, "items": sorted(items, key=lambda x: -x["days_overdue"]),
            "by_client": sorted(by_client.values(), key=lambda r: -r["total"]), "totals": totals}


def ap_aging(conn, as_of=None):
    as_of = _date(as_of or _today(), "as_of")
    asof_d = dt.date.fromisoformat(as_of)
    items, by_vendor = [], {}
    for b in conn.execute("SELECT b.*, v.name AS vendor_name FROM bills b JOIN vendors v ON v.id = b.vendor_id"
                          " WHERE b.bill_date <= ?", (as_of,)).fetchall():
        if b["status"] == "void":
            vdate = conn.execute("SELECT date FROM journal_entries WHERE id = ?", (b["void_entry_id"],)).fetchone()[0]
            if vdate <= as_of:
                continue
        paid = conn.execute(
            "SELECT COALESCE(SUM(p.amount_cents),0) FROM bill_payments p WHERE p.bill_id = ? AND p.date <= ?"
            " AND NOT (p.voided = 1 AND (SELECT date FROM journal_entries WHERE id = p.void_entry_id) <= ?)",
            (b["id"], as_of, as_of)).fetchone()[0]
        bal = b["amount_cents"] - paid
        if bal <= 0:
            continue
        days = (asof_d - dt.date.fromisoformat(b["due_date"])).days
        bk = _bucket(days)
        items.append({"bill_id": b["id"], "vendor": b["vendor_name"], "description": b["description"],
                      "bill_date": b["bill_date"], "due_date": b["due_date"], "days_overdue": max(days, 0),
                      "bucket": bk, "balance_cents": bal})
        row = by_vendor.setdefault(b["vendor_name"], {"vendor": b["vendor_name"], **{k: 0 for k in BUCKETS},
                                                       "total": 0})
        row[bk] += bal
        row["total"] += bal
    totals = {k: sum(r[k] for r in by_vendor.values()) for k in (*BUCKETS, "total")}
    return {"as_of": as_of, "items": sorted(items, key=lambda x: -x["days_overdue"]),
            "by_vendor": sorted(by_vendor.values(), key=lambda r: -r["total"]), "totals": totals}


def _profitability(conn, start, end, group):
    start, end = _date(start, "start"), _date(end, "end")
    col = "l.client_id" if group == "client" else "l.project_id"
    rows = conn.execute(
        f"SELECT {col} AS gid, a.type, a.subtype, SUM(l.debit_cents) d, SUM(l.credit_cents) c"
        " FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id"
        f" WHERE e.date BETWEEN ? AND ? AND a.type IN ('income','expense') AND {col} IS NOT NULL"
        f" GROUP BY {col}, a.type, a.subtype", (start, end)).fetchall()
    agg = {}
    for r in rows:
        g = agg.setdefault(r["gid"], {"revenue": 0, "passthrough": 0, "direct": 0})
        if r["subtype"] in ("revenue", "passthrough_revenue"):
            g["revenue"] += r["c"] - r["d"]
        elif r["subtype"] == "passthrough_cost":
            g["passthrough"] += r["d"] - r["c"]
        elif r["subtype"] in ("cogs", "opex", "other_expense"):
            g["direct"] += r["d"] - r["c"]
    return agg


def client_profitability(conn, start, end):
    agg = _profitability(conn, start, end, "client")
    out = []
    for c in conn.execute("SELECT * FROM clients ORDER BY name"):
        g = agg.get(c["id"])
        if not g:
            continue
        net = g["revenue"] - g["passthrough"]
        contrib = net - g["direct"]
        out.append({"client_id": c["id"], "client": c["name"], "gross_billings_cents": g["revenue"],
                    "passthrough_cents": g["passthrough"], "net_revenue_cents": net,
                    "direct_costs_cents": g["direct"], "contribution_cents": contrib,
                    "margin_pct": round(100 * contrib / net, 1) if net else None})
    out.sort(key=lambda r: -r["contribution_cents"])
    return {"start": start, "end": end, "rows": out,
            "note": "Direct costs are expenses tagged to the client. Staff salaries are not allocated, "
                    "so contribution overstates true client profit."}


def project_profitability(conn, start, end):
    agg = _profitability(conn, start, end, "project")
    out = []
    for p in conn.execute("SELECT p.*, c.name AS client_name FROM projects p JOIN clients c ON c.id = p.client_id"
                          " ORDER BY c.name, p.name"):
        g = agg.get(p["id"], {"revenue": 0, "passthrough": 0, "direct": 0})
        net = g["revenue"] - g["passthrough"]
        contrib = net - g["direct"]
        out.append({"project_id": p["id"], "project": p["name"], "client": p["client_name"], "status": p["status"],
                    "budget_cents": p["budget_cents"], "gross_billings_cents": g["revenue"],
                    "net_revenue_cents": net, "costs_cents": g["passthrough"] + g["direct"],
                    "contribution_cents": contrib,
                    "margin_pct": round(100 * contrib / net, 1) if net else None,
                    "budget_billed_pct": round(100 * g["revenue"] / p["budget_cents"], 1) if p["budget_cents"] else None})
    return {"start": _date(start), "end": _date(end), "rows": out}


def general_ledger(conn, account_code, start, end):
    acct = account_by_code(conn, account_code, must_be_active=False)
    start, end = _date(start, "start"), _date(end, "end")
    sign = 1 if acct["type"] in DEBIT_NORMAL else -1
    opening = conn.execute(
        "SELECT COALESCE(SUM(l.debit_cents - l.credit_cents),0) FROM journal_lines l"
        " JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id = ? AND e.date < ?",
        (acct["id"], start)).fetchone()[0] * sign
    bal, lines = opening, []
    for r in conn.execute(
            "SELECT l.*, e.date, e.memo AS entry_memo, e.source_type, c.name AS client_name FROM journal_lines l"
            " JOIN journal_entries e ON e.id = l.entry_id LEFT JOIN clients c ON c.id = l.client_id"
            " WHERE l.account_id = ? AND e.date BETWEEN ? AND ? ORDER BY e.date, e.id, l.id",
            (acct["id"], start, end)):
        bal += (r["debit_cents"] - r["credit_cents"]) * sign
        d = dict(r)
        d["running_balance_cents"] = bal
        lines.append(d)
    return {"account": dict(acct), "start": start, "end": end, "opening_balance_cents": opening,
            "lines": lines, "closing_balance_cents": bal}


def dashboard(conn, today=None):
    today = _date(today or _today())
    t = dt.date.fromisoformat(today)
    month_start = t.replace(day=1).isoformat()
    fy = _fy_start(conn, today)
    bal = {r["code"]: r for r in _balances(conn, end=today)}
    cash = sum(r["balance_cents"] for r in bal.values() if r["subtype"] == "cash")
    mtd = profit_and_loss(conn, month_start, today)
    ytd = profit_and_loss(conn, fy, today)
    aging = ar_aging(conn, today)
    overdue = [i for i in aging["items"] if i["bucket"] != "current"]
    unbilled = unbilled_expenses(conn)
    trend = []
    for k in range(5, -1, -1):
        y, m = t.year, t.month - k
        while m <= 0:
            m += 12; y -= 1
        ms = dt.date(y, m, 1)
        me = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        pl = profit_and_loss(conn, ms.isoformat(), min(me, t).isoformat())
        trend.append({"month": ms.strftime("%Y-%m"), "net_revenue_cents": pl["net_revenue_cents"],
                      "net_income_cents": pl["net_income_cents"]})
    return {
        "today": today, "cash_cents": cash,
        "ar_cents": bal[AR]["balance_cents"], "ap_cents": bal[AP]["balance_cents"],
        "sales_tax_payable_cents": bal[SALES_TAX]["balance_cents"],
        "mtd_net_revenue_cents": mtd["net_revenue_cents"], "mtd_net_income_cents": mtd["net_income_cents"],
        "ytd_gross_billings_cents": ytd["gross_billings_cents"], "ytd_net_revenue_cents": ytd["net_revenue_cents"],
        "ytd_net_income_cents": ytd["net_income_cents"], "ytd_net_margin_pct": ytd["net_margin_pct"],
        "overdue_count": len(overdue), "overdue_cents": sum(i["balance_cents"] for i in overdue),
        "unbilled_expenses_cents": sum(b["amount_cents"] for b in unbilled), "trend": trend,
    }
