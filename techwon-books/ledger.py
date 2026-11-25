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
import re

import db
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
DISCOUNTS = "4950"
ADDED_LATER = {DISCOUNTS, "6550", "1030"}
CASH_IN_HAND = "1030"
GENERAL_PAYEE = "General expenses"  # used when an expense has no "paid to"

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
    ("1030", "Cash in Hand", "asset", "cash", 0),
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
    ("4950", "Discounts Given", "income", "revenue", 1),
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
    ("6550", "Client Visits & Meetings", "expense", "opex", 0),
    ("6600", "Office & Utilities", "expense", "opex", 0),
    ("6700", "Bank & Payment Processing Fees", "expense", "opex", 0),
    ("6800", "Depreciation", "expense", "opex", 0),
    ("6900", "Miscellaneous", "expense", "opex", 0),
    ("7000", "Interest Expense", "expense", "other_expense", 0),
]

CONTRACT_CLAUSES = (
    "1. Services. The Agency will provide the services described in the Scope of Work. Work outside that scope "
    "needs a written change request and may be charged separately.\n"
    "2. Fees and payment. The Client will pay the fees in this agreement according to the payment terms. The Agency "
    "may pause work while any invoice is overdue.\n"
    "3. Client responsibilities. The Client will provide content, approvals, access and feedback on time. Delays in "
    "the Client's input may move deadlines.\n"
    "4. Revisions. Two rounds of revisions per deliverable are included unless stated otherwise. Further revisions "
    "are billed at the Agency's standard rates.\n"
    "5. Intellectual property. Ownership of the final deliverables passes to the Client on full payment. The Agency "
    "keeps its pre-existing tools, templates and know-how, and may show the work in its portfolio unless the Client "
    "objects in writing.\n"
    "6. Third-party costs. Advertising spend, stock media, software licences and similar costs are paid by the "
    "Client, directly or by reimbursing the Agency.\n"
    "7. Confidentiality. Each party will keep the other's confidential information private, during this agreement "
    "and after it ends.\n"
    "8. No guaranteed results. The Agency will work with reasonable skill and care but does not guarantee specific "
    "outcomes such as rankings, sales or advertising performance.\n"
    "9. Liability. Each party's total liability under this agreement is limited to the fees paid in the three months "
    "before the claim, except for unpaid fees or a breach of confidentiality.\n"
    "10. Term and termination. Either party may end this agreement with 30 days' written notice. The Client will pay "
    "for work done up to the end date. Advances are non-refundable once work has started.\n"
    "11. Governing law. This agreement is governed by the laws of Pakistan, and the courts of Karachi have "
    "jurisdiction.")
SHORT_TERMS = (
    "1. 50% advance is required to start work; the balance is due on delivery.\n"
    "2. The price covers the agreed scope only; extra work is billed separately.\n"
    "3. Includes 2 rounds of design revisions.\n"
    "4. Ownership of deliverables transfers on full payment.\n"
    "5. Advances are non-refundable once work begins; cancellation after 50% completion requires full payment.\n"
    "6. Approved refunds are processed within 14 business days.")

DEFAULT_SETTINGS = {
    "company_name": "Techwon",
    "currency": "USD",
    "lock_date": "",
    "fiscal_year_start_month": "1",
    "invoice_prefix": "TW-",
    "number_digits": "0",  # zero-pad document numbers, e.g. 4 -> INV-0012
    "quote_prefix": "Q-",
    "next_quote_number": "1001",
    "quote_valid_days": "15",
    "contract_prefix": "C-",
    "next_contract_number": "1001",
    "contract_payment_terms": "50% advance on signing; balance on delivery. Invoices are due on receipt.",
    "contract_clauses": CONTRACT_CLAUSES,
    "next_invoice_number": "1001",
    "default_tax_rate_bp": "0",
    # shown on invoices
    "company_address": "",
    "company_email": "",
    "company_phone": "",
    "company_website": "",
    "company_tax_id": "",
    "payment_instructions": "",
    "invoice_footer": "Thank you for your business.",
    "invoice_terms": SHORT_TERMS,
    "brand_color": "#1a73d1",
}
LOGO_KEY = "logo"  # stored in settings, but served separately because it can be large
TERMS_SEEDED_KEY = "terms_seeded"
INTERNAL_KEYS = (LOGO_KEY, TERMS_SEEDED_KEY)
MAX_LOGO_CHARS = 700_000  # roughly a 500 KB image

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_secrets (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS login_attempts (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL,
  ip TEXT NOT NULL DEFAULT '',
  at INTEGER NOT NULL,
  success INTEGER NOT NULL
);

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
  is_active INTEGER NOT NULL DEFAULT 1,
  external_id TEXT
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

CREATE TABLE IF NOT EXISTS partners (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  share_bp INTEGER NOT NULL DEFAULT 0 CHECK (share_bp >= 0 AND share_bp <= 10000),
  capital_account_id INTEGER NOT NULL REFERENCES accounts(id),
  drawings_account_id INTEGER NOT NULL REFERENCES accounts(id),
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
  created_by TEXT,
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

CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  project_id INTEGER REFERENCES projects(id),
  issue_date TEXT NOT NULL,
  valid_until TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','accepted','declined')),
  tax_rate_bp INTEGER NOT NULL DEFAULT 0 CHECK (tax_rate_bp >= 0),
  discount_type TEXT CHECK (discount_type IN ('percent','amount')),
  discount_value INTEGER NOT NULL DEFAULT 0 CHECK (discount_value >= 0),
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  tax_override_cents INTEGER,
  external_id TEXT,
  meta TEXT
);

CREATE TABLE IF NOT EXISTS quote_lines (
  id INTEGER PRIMARY KEY,
  quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
  product TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  quantity TEXT NOT NULL DEFAULT '1',
  unit_price_cents INTEGER NOT NULL,
  amount_cents INTEGER NOT NULL,
  revenue_account_id INTEGER NOT NULL REFERENCES accounts(id),
  taxable INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS contracts (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  project_id INTEGER REFERENCES projects(id),
  quote_id INTEGER REFERENCES quotes(id),
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'project' CHECK (kind IN ('project','retainer')),
  start_date TEXT NOT NULL,
  end_date TEXT,
  value_cents INTEGER NOT NULL DEFAULT 0 CHECK (value_cents >= 0),
  monthly_fee_cents INTEGER NOT NULL DEFAULT 0 CHECK (monthly_fee_cents >= 0),
  payment_terms TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '',
  clauses TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','signed','cancelled')),
  signed_date TEXT,
  signed_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY,
  number TEXT UNIQUE,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  project_id INTEGER REFERENCES projects(id),
  issue_date TEXT NOT NULL,
  due_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','issued','void')),
  tax_rate_bp INTEGER NOT NULL DEFAULT 0 CHECK (tax_rate_bp >= 0),
  discount_type TEXT CHECK (discount_type IN ('percent','amount')),
  discount_value INTEGER NOT NULL DEFAULT 0 CHECK (discount_value >= 0),
  notes TEXT NOT NULL DEFAULT '',
  retainer_period TEXT,
  entry_id INTEGER REFERENCES journal_entries(id),
  void_entry_id INTEGER REFERENCES journal_entries(id),
  quote_id INTEGER REFERENCES quotes(id),
  quote_pct_bp INTEGER,
  tax_override_cents INTEGER,
  external_id TEXT,
  meta TEXT
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

CREATE TABLE IF NOT EXISTS profit_allocations (
  id INTEGER PRIMARY KEY,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  net_income_cents INTEGER NOT NULL,
  entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invoice_lines (
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  product TEXT NOT NULL DEFAULT '',
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
  void_entry_id INTEGER REFERENCES journal_entries(id),
  method TEXT NOT NULL DEFAULT ''
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

def connect(target):
    """target: SQLite file path / ':memory:', or a postgres:// URL."""
    return db.connect(target)


@contextlib.contextmanager
def tx(conn):
    """Write transaction. The write lock is taken up front so
    read-then-write sequences (e.g. invoice numbering) cannot race."""
    if conn.in_transaction:  # already inside a transaction: join it
        yield conn
        return
    conn.begin()
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def init_db(conn):
    conn.executescript(SCHEMA)
    # Upgrades for books created by older versions. Several servers may start at
    # once, so a column another one just added is not an error.
    for table, col, ddl in (
            ("journal_entries", "created_by", "created_by TEXT"),
            ("invoices", "discount_type", "discount_type TEXT CHECK (discount_type IN ('percent','amount'))"),
            ("invoices", "discount_value", "discount_value INTEGER NOT NULL DEFAULT 0 CHECK (discount_value >= 0)"),
            ("invoice_lines", "product", "product TEXT NOT NULL DEFAULT ''"),
            ("invoices", "quote_id", "quote_id INTEGER REFERENCES quotes(id)"),
            ("invoices", "quote_pct_bp", "quote_pct_bp INTEGER"),
            ("clients", "external_id", "external_id TEXT"),
            ("invoices", "tax_override_cents", "tax_override_cents INTEGER"),
            ("invoices", "external_id", "external_id TEXT"),
            ("invoices", "meta", "meta TEXT"),
            ("quotes", "tax_override_cents", "tax_override_cents INTEGER"),
            ("quotes", "external_id", "external_id TEXT"),
            ("quotes", "meta", "meta TEXT"),
            ("payments", "method", "method TEXT NOT NULL DEFAULT ''")):
        if col not in conn.columns(table):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            except Exception:
                if col not in conn.columns(table):
                    raise
    for table in ("clients", "invoices", "quotes"):  # one Books record per outside record
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{table}_external ON {table}(external_id)"
                     " WHERE external_id IS NOT NULL")
    with tx(conn):
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING", (k, v))
        _seed_partners_from_env(conn)
        # Books created before the standard terms existed got an empty field: fill it
        # once. Never again after that, so terms a user clears stay cleared.
        if not conn.execute("SELECT 1 FROM settings WHERE key = ?", (TERMS_SEEDED_KEY,)).fetchone():
            conn.execute("UPDATE settings SET value = ? WHERE key = 'invoice_terms' AND value = ''", (SHORT_TERMS,))
            conn.execute("INSERT INTO settings(key, value) VALUES (?, '1') ON CONFLICT(key) DO NOTHING",
                         (TERMS_SEEDED_KEY,))
        if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO accounts(code, name, type, subtype, is_system) VALUES (?,?,?,?,?)",
                DEFAULT_ACCOUNTS)
        for code, name, typ, sub, system in DEFAULT_ACCOUNTS:
            if code in ADDED_LATER:  # accounts added after the first release
                conn.execute("INSERT INTO accounts(code, name, type, subtype, is_system) VALUES (?,?,?,?,?)"
                             " ON CONFLICT(code) DO NOTHING", (code, name, typ, sub, system))


def get_settings(conn):
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings WHERE key NOT IN (?, ?)",
                                                       INTERNAL_KEYS)}


def get_logo(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (LOGO_KEY,)).fetchone()
    return row[0] if row else ""


def set_logo(conn, data_uri):
    """data_uri: a base64 PNG/JPEG/WebP/SVG data: URI, or '' to remove the logo."""
    data_uri = str(data_uri or "")
    if data_uri:
        if len(data_uri) > MAX_LOGO_CHARS:
            raise LedgerError("Logo file is too large; use an image under 500 KB")
        if not re.fullmatch(r"data:image/(png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/]+={0,2}", data_uri):
            raise LedgerError("Logo must be a PNG, JPG, WebP or SVG image")
    with tx(conn):
        conn.execute("DELETE FROM settings WHERE key = ?", (LOGO_KEY,))
        if data_uri:
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?)", (LOGO_KEY, data_uri))


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
    if "brand_color" in values and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(values["brand_color"])):
        raise LedgerError("brand_color must look like #1d4ed8")
    for k in ("company_address", "payment_instructions", "invoice_footer", "invoice_terms"):
        if k in values and len(str(values[k])) > 5000:
            raise LedgerError(f"{k} is too long")
    if "number_digits" in values and not 0 <= _int(values["number_digits"], "number_digits") <= 8:
        raise LedgerError("number_digits must be between 0 and 8")
    if "next_quote_number" in values and _int(values["next_quote_number"], "next_quote_number") < 1:
        raise LedgerError("next_quote_number must be positive")
    if "next_contract_number" in values and _int(values["next_contract_number"], "next_contract_number") < 1:
        raise LedgerError("next_contract_number must be positive")
    for k in ("contract_clauses", "contract_payment_terms"):
        if k in values and len(str(values[k])) > 30000:
            raise LedgerError(f"{k} is too long")
    if "quote_valid_days" in values and not 0 <= _int(values["quote_valid_days"], "quote_valid_days") <= 365:
        raise LedgerError("quote_valid_days must be between 0 and 365")
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


def _money(conn, cents):
    """Amount for messages people read, e.g. 'PKR 25,000.00'."""
    cur = get_settings(conn).get("currency", "")
    return f"{cur} {cents / 100:,.2f}".strip()


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
        "INSERT INTO journal_entries(date, memo, source_type, source_id, reverses_entry_id, created_by)"
        " VALUES (?,?,?,?,?,?)",
        (date, str(memo or ""), source_type, source_id, reverses, getattr(conn, "user", None)))
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
        except db.IntegrityError:
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
        except db.IntegrityError:
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
        except db.IntegrityError:
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
        except db.IntegrityError:
            raise LedgerError(f"Vendor '{name}' already exists") from None


# --------------------------------------------------------------------------
# Invoices (A/R)
# --------------------------------------------------------------------------

def _prepare_lines(conn, lines, what="invoice"):
    """Validate document lines; returns rows of
    (product, description, quantity, unit_price_cents, amount_cents, revenue_account_id, taxable, bill_id)."""
    if not lines:
        raise LedgerError(f"A{'n' if what[0] in 'aeiou' else ''} {what} needs at least one line")
    rows = []
    for i, ln in enumerate(lines, 1):
        product = str(ln.get("product") or "").strip()
        desc = str(ln.get("description") or "").strip()
        if not desc and not product:
            raise LedgerError(f"Line {i}: enter a product/service or a description")
        qty = _qty(ln.get("quantity", 1))
        price = _cents(ln.get("unit_price_cents"), f"line {i} unit price", allow_zero=True, allow_negative=True)
        acct = account_by_code(conn, ln.get("revenue_account_code") or "4100")
        if acct["type"] != "income":
            raise LedgerError(f"Line {i}: account {acct['code']} is not an income account")
        rows.append((product[:200], desc, str(qty), price, _round(qty * price), acct["id"],
                     1 if ln.get("taxable", True) else 0, ln.get("bill_id")))
    return rows


def _insert_invoice_lines(conn, invoice_id, lines):
    for r in _prepare_lines(conn, lines, "invoice"):
        conn.execute(
            "INSERT INTO invoice_lines(invoice_id, product, description, quantity, unit_price_cents, amount_cents,"
            " revenue_account_id, taxable, bill_id) VALUES (?,?,?,?,?,?,?,?,?)", (invoice_id, *r))


def create_invoice(conn, client_id, issue_date, lines, due_date=None, project_id=None,
                   tax_rate_bp=None, notes="", retainer_period=None, discount_type=None, discount_value=0):
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
        discount_type, discount_value = _discount(discount_type, discount_value)
        inv_id = conn.execute(
            "INSERT INTO invoices(client_id, project_id, issue_date, due_date, tax_rate_bp, notes, retainer_period,"
            " discount_type, discount_value) VALUES (?,?,?,?,?,?,?,?,?)",
            (c["id"], project_id or None, issue_date, due_date, tax_rate_bp, str(notes or ""), retainer_period,
             discount_type, discount_value)).lastrowid
        _insert_invoice_lines(conn, inv_id, lines)
        _check_invoice_totals(conn, inv_id)
        return inv_id


def _invoice_totals(conn, invoice_id):
    inv = conn.execute("SELECT tax_rate_bp, discount_type, discount_value, tax_override_cents FROM invoices WHERE id = ?",
                       (invoice_id,)).fetchone()
    sub = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM invoice_lines WHERE invoice_id = ?",
                       (invoice_id,)).fetchone()[0]
    taxable = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM invoice_lines WHERE invoice_id = ? AND taxable = 1",
                           (invoice_id,)).fetchone()[0]
    t = _totals_math(sub, taxable, inv["tax_rate_bp"], inv["discount_type"], inv["discount_value"], inv["tax_override_cents"])
    paid = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE invoice_id = ? AND voided = 0",
                        (invoice_id,)).fetchone()[0]
    return {**t, "paid_cents": paid, "balance_cents": t["total_cents"] - paid}


def _totals_math(sub, taxable, tax_rate_bp, discount_type, discount_value, tax_override=None):
    if discount_type == "percent":
        discount = _round(Decimal(sub) * discount_value / Decimal(10000))
    elif discount_type == "amount":
        discount = discount_value
    else:
        discount = 0
    # The discount comes off before tax; with mixed lines it reduces the taxable
    # part in proportion to its share of the subtotal.
    if discount and sub > 0:
        taxable -= _round(Decimal(discount) * taxable / Decimal(sub))
    tax = tax_override if tax_override is not None else _round(Decimal(taxable) * tax_rate_bp / Decimal(10000))
    return {"subtotal_cents": sub, "discount_cents": discount, "tax_cents": tax, "total_cents": sub - discount + tax}


def _discount(discount_type, discount_value):
    """Normalise a discount: percent is in basis points (1000 = 10%), amount in cents."""
    if discount_type in (None, "", "none"):
        return None, 0
    if discount_type not in ("percent", "amount"):
        raise LedgerError("discount_type must be 'percent' or 'amount'")
    v = _int(discount_value, "discount_value")
    if v <= 0:
        raise LedgerError("Discount must be greater than zero")
    if discount_type == "percent" and v > 10000:
        raise LedgerError("A percentage discount can't be more than 100%")
    return discount_type, v


def _check_invoice_totals(conn, invoice_id):
    t = _invoice_totals(conn, invoice_id)
    if t["discount_cents"] > t["subtotal_cents"]:
        raise LedgerError("The discount is larger than the invoice subtotal")
    if t["total_cents"] <= 0:
        raise LedgerError("Invoice total must be greater than zero")
    return t


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
                     " p.name AS project_name, q.number AS quote_number FROM invoices i JOIN clients c ON c.id = i.client_id"
                     " LEFT JOIN quotes q ON q.id = i.quote_id"
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


INVOICE_FIELDS = {"client_id", "issue_date", "due_date", "tax_rate_bp", "notes", "project_id",
                  "discount_type", "discount_value"}


def _apply_invoice_changes(conn, inv, lines, fields):
    """Validate and apply edits to an invoice's header and lines (caller holds the transaction)."""
    bad = set(fields) - INVOICE_FIELDS
    if bad:
        raise LedgerError(f"Unknown invoice field(s): {', '.join(sorted(bad))}")
    has_rebilled = conn.execute("SELECT 1 FROM invoice_lines WHERE invoice_id = ? AND bill_id IS NOT NULL",
                                (inv["id"],)).fetchone()
    if "client_id" in fields:
        new_client = _client(conn, fields["client_id"])
        if new_client["id"] == inv["client_id"]:
            del fields["client_id"]
        elif inv["status"] != "draft":
            raise LedgerError("An issued invoice can't move to another client; void it and create a new one")
        elif has_rebilled:
            raise LedgerError("This invoice rebills one client's expenses, so its client can't change")
        elif not new_client["is_active"]:
            raise LedgerError("Client is inactive")
        else:
            fields["client_id"] = new_client["id"]
    client_id = fields.get("client_id", inv["client_id"])
    for k in ("issue_date", "due_date"):
        if k in fields:
            fields[k] = _date(fields[k], k)
    if "tax_rate_bp" in fields:
        fields["tax_rate_bp"] = _int(fields["tax_rate_bp"], "tax_rate_bp")
        if fields["tax_rate_bp"] < 0:
            raise LedgerError("tax_rate_bp cannot be negative")
    if fields.get("project_id"):
        fields["project_id"] = _project(conn, fields["project_id"], client_id)["id"]
    elif "project_id" in fields:
        fields["project_id"] = None
    elif "client_id" in fields and inv["project_id"]:
        fields["project_id"] = None  # the old project belonged to the old client
    if "discount_type" in fields or "discount_value" in fields:
        fields["discount_type"], fields["discount_value"] = _discount(
            fields.get("discount_type", inv["discount_type"]), fields.get("discount_value", inv["discount_value"]))
    for k, v in fields.items():
        conn.execute(f"UPDATE invoices SET {k} = ? WHERE id = ?", (v, inv["id"]))
    cur = conn.execute("SELECT * FROM invoices WHERE id = ?", (inv["id"],)).fetchone()
    if cur["due_date"] < cur["issue_date"]:
        raise LedgerError("due_date cannot be before issue_date")
    if lines is not None:
        if any(l.get("bill_id") for l in lines) or has_rebilled:
            raise LedgerError("Rebilled-expense invoices can't have lines edited; delete or void it and rebill again")
        conn.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (inv["id"],))
        _insert_invoice_lines(conn, inv["id"], lines)
    return _check_invoice_totals(conn, inv["id"])


def update_draft_invoice(conn, invoice_id, lines=None, **fields):
    with tx(conn):
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (_int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] != "draft":
            raise LedgerError("This invoice has been issued; use revise instead")
        _apply_invoice_changes(conn, inv, lines, fields)


def revise_invoice(conn, invoice_id, lines=None, **fields):
    """Edit an issued invoice, keeping its number. The original posting is reversed
    on its own date and the corrected invoice is posted, so the journal shows both."""
    with tx(conn):
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (_int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] == "draft":
            raise LedgerError("Drafts are edited directly, not revised")
        if inv["status"] != "issued":
            raise LedgerError("A void invoice can't be edited")
        _reverse(conn, inv["entry_id"], inv["issue_date"], f"Invoice {inv['number']} revised: original reversed")
        totals = _apply_invoice_changes(conn, inv, lines, fields)
        if totals["total_cents"] < totals["paid_cents"]:
            raise LedgerError(f"The new total ({totals['total_cents']} cents) is less than the "
                              f"{totals['paid_cents']} cents already paid. Void a payment first.")
        cur = conn.execute("SELECT * FROM invoices WHERE id = ?", (inv["id"],)).fetchone()
        paid_before = conn.execute("SELECT MIN(date) FROM payments WHERE invoice_id = ? AND voided = 0",
                                   (inv["id"],)).fetchone()[0]
        if paid_before and cur["issue_date"] > paid_before:
            raise LedgerError("The invoice date can't move after a payment already received on it")
        eid = _post(conn, cur["issue_date"], f"Invoice {inv['number']} (revised)",
                    _invoice_entry_lines(conn, cur, totals, inv["number"]), "invoice", inv["id"])
        conn.execute("UPDATE invoices SET entry_id = ? WHERE id = ?", (eid, inv["id"]))


def delete_draft_invoice(conn, invoice_id):
    with tx(conn):
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (_int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] != "draft":
            raise LedgerError("Only drafts can be deleted; void issued invoices instead")
        conn.execute("UPDATE bills SET rebilled_invoice_id = NULL WHERE rebilled_invoice_id = ?", (inv["id"],))
        conn.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (inv["id"],))
        conn.execute("DELETE FROM invoices WHERE id = ?", (inv["id"],))


def _invoice_entry_lines(conn, inv, totals, number):
    """Journal lines for an invoice: Dr A/R; Cr revenue; Dr discounts; Cr sales tax."""
    cid, pid = inv["client_id"], inv["project_id"]
    lines = [{"account_code": AR, "debit_cents": totals["total_cents"], "client_id": cid,
              "project_id": pid, "memo": f"Invoice {number}"}]
    for l in conn.execute("SELECT * FROM invoice_lines WHERE invoice_id = ? AND amount_cents != 0", (inv["id"],)):
        amt = l["amount_cents"]
        lines.append({"account_id": l["revenue_account_id"], "client_id": cid, "project_id": pid,
                      "memo": " - ".join(x for x in (l["product"], l["description"]) if x),
                      **({"credit_cents": amt} if amt > 0 else {"debit_cents": -amt})})
    if totals["discount_cents"]:
        disc = account_by_code(conn, DISCOUNTS)
        if disc["type"] != "income":
            raise LedgerError(f"Account {DISCOUNTS} must be an income account to record discounts")
        lines.append({"account_id": disc["id"], "debit_cents": totals["discount_cents"], "client_id": cid,
                      "project_id": pid, "memo": f"Discount on {number}"})
    if totals["tax_cents"]:
        lines.append({"account_code": SALES_TAX, "credit_cents": totals["tax_cents"],
                      "client_id": cid, "memo": f"Tax on {number}"})
    return lines


def issue_invoice(conn, invoice_id, number=None):
    """Assign the next invoice number (or a given one, for imports) and post: Dr A/R, Cr revenue (+ Cr sales tax)."""
    with tx(conn):
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (_int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] != "draft":
            raise LedgerError(f"Invoice is already {inv['status']}")
        totals = _check_invoice_totals(conn, inv["id"])
        if number:
            number = str(number).strip()[:40]
            if conn.execute("SELECT 1 FROM invoices WHERE number = ?", (number,)).fetchone():
                raise LedgerError(f"Invoice number {number} is already used")
        else:
            s = get_settings(conn)
            n = int(s["next_invoice_number"])
            fmt_no = lambda k: f"{s['invoice_prefix']}{str(k).zfill(int(s.get('number_digits') or 0))}"  # noqa: E731
            number = fmt_no(n)
            while conn.execute("SELECT 1 FROM invoices WHERE number = ?", (number,)).fetchone():
                n += 1
                number = fmt_no(n)
            conn.execute("UPDATE settings SET value = ? WHERE key = 'next_invoice_number'", (str(n + 1),))
        eid = _post(conn, inv["issue_date"], f"Invoice {number}", _invoice_entry_lines(conn, inv, totals, number),
                    "invoice", inv["id"])
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


def record_payment(conn, invoice_id, date, amount_cents, deposit_account_code=CASH, reference="", method=""):
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
            raise LedgerError(f"Payment is more than the {_money(conn, bal)} still owed on this invoice. "
                              "Record any extra as a journal entry to Deferred Revenue.")
        bank = account_by_code(conn, deposit_account_code)
        if bank["subtype"] != "cash":
            raise LedgerError("Deposit account must be a bank/cash account")
        eid = _post(conn, date, f"Payment for {inv['number']}", [
            {"account_id": bank["id"], "debit_cents": amount, "client_id": inv["client_id"]},
            {"account_code": AR, "credit_cents": amount, "client_id": inv["client_id"],
             "project_id": inv["project_id"]},
        ], "payment", inv["id"])
        return conn.execute(
            "INSERT INTO payments(invoice_id, date, amount_cents, deposit_account_id, reference, entry_id, method)"
            " VALUES (?,?,?,?,?,?,?)", (inv["id"], date, amount, bank["id"], str(reference or ""), eid,
                                       str(method or "")[:30])).lastrowid


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
# Quotations (no effect on the books until converted to an invoice)
# --------------------------------------------------------------------------

QUOTE_FIELDS = {"client_id", "project_id", "issue_date", "valid_until", "tax_rate_bp", "notes",
                "discount_type", "discount_value"}


def _quote(conn, quote_id):
    return _one(conn, "SELECT * FROM quotes WHERE id = ?", (_int(quote_id, "quote_id"),), "Quotation")


def _invoiced_bp(conn, quote_id):
    return conn.execute("SELECT COALESCE(SUM(quote_pct_bp),0) FROM invoices WHERE quote_id = ? AND status != 'void'",
                        (quote_id,)).fetchone()[0]


def _quote_totals(conn, quote_id):
    q = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    sub = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM quote_lines WHERE quote_id = ?", (quote_id,)).fetchone()[0]
    taxable = conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM quote_lines WHERE quote_id = ? AND taxable = 1",
                           (quote_id,)).fetchone()[0]
    t = _totals_math(sub, taxable, q["tax_rate_bp"], q["discount_type"], q["discount_value"], q["tax_override_cents"])
    return {**t, "invoiced_bp": _invoiced_bp(conn, quote_id)}


def _quote_status(q, t, today=None):
    if t["invoiced_bp"] >= 10000:
        return "invoiced"
    if t["invoiced_bp"] > 0:
        return "part-invoiced"
    if q["status"] in ("draft", "sent") and q["valid_until"] < (today or _today()):
        return "expired"
    return q["status"]


def _check_quote(conn, quote_id):
    t = _quote_totals(conn, quote_id)
    if t["discount_cents"] > t["subtotal_cents"]:
        raise LedgerError("The discount is larger than the quotation subtotal")
    if t["total_cents"] <= 0:
        raise LedgerError("Quotation total must be greater than zero")
    return t


def _write_quote_lines(conn, quote_id, lines):
    conn.execute("DELETE FROM quote_lines WHERE quote_id = ?", (quote_id,))
    for r in _prepare_lines(conn, lines, "quotation"):
        conn.execute("INSERT INTO quote_lines(quote_id, product, description, quantity, unit_price_cents, amount_cents,"
                     " revenue_account_id, taxable) VALUES (?,?,?,?,?,?,?,?)", (quote_id, *r[:7]))


def _quote_header(conn, fields, current=None):
    """Validate header fields for a new quote (current=None) or an edit."""
    bad = set(fields) - QUOTE_FIELDS
    if bad:
        raise LedgerError(f"Unknown quotation field(s): {', '.join(sorted(bad))}")
    out = {}
    client_id = fields.get("client_id", current["client_id"] if current else None)
    c = _client(conn, client_id)
    if "client_id" in fields or not current:
        if not c["is_active"]:
            raise LedgerError("Client is inactive")
        out["client_id"] = c["id"]
    for k in ("issue_date", "valid_until"):
        if fields.get(k):
            out[k] = _date(fields[k], k)
    if "tax_rate_bp" in fields and fields["tax_rate_bp"] not in (None, ""):
        out["tax_rate_bp"] = _int(fields["tax_rate_bp"], "tax_rate_bp")
        if out["tax_rate_bp"] < 0:
            raise LedgerError("tax_rate_bp cannot be negative")
    if "project_id" in fields:
        out["project_id"] = _project(conn, fields["project_id"], c["id"])["id"] if fields["project_id"] else None
    elif current and "client_id" in out and current["project_id"] and out["client_id"] != current["client_id"]:
        out["project_id"] = None
    if "notes" in fields:
        out["notes"] = str(fields["notes"] or "")
    if "discount_type" in fields or "discount_value" in fields:
        out["discount_type"], out["discount_value"] = _discount(
            fields.get("discount_type", current["discount_type"] if current else None),
            fields.get("discount_value", current["discount_value"] if current else 0))
    return out


def create_quote(conn, client_id, issue_date, lines, number=None, **fields):
    with tx(conn):
        h = _quote_header(conn, {**fields, "client_id": client_id, "issue_date": issue_date})
        s = get_settings(conn)
        if "valid_until" not in h:
            h["valid_until"] = (dt.date.fromisoformat(h["issue_date"])
                                + dt.timedelta(days=int(s["quote_valid_days"]))).isoformat()
        if h["valid_until"] < h["issue_date"]:
            raise LedgerError("valid_until cannot be before the quotation date")
        h.setdefault("tax_rate_bp", int(s["default_tax_rate_bp"]))
        if number:
            h["number"] = str(number).strip()[:40]
            if conn.execute("SELECT 1 FROM quotes WHERE number = ?", (h["number"],)).fetchone():
                raise LedgerError(f"Quotation number {h['number']} is already used")
        else:
            n = int(s["next_quote_number"])
            fmt_no = lambda k: f"{s['quote_prefix']}{str(k).zfill(int(s.get('number_digits') or 0))}"  # noqa: E731
            while conn.execute("SELECT 1 FROM quotes WHERE number = ?", (fmt_no(n),)).fetchone():
                n += 1
            conn.execute("UPDATE settings SET value = ? WHERE key = 'next_quote_number'", (str(n + 1),))
            h["number"] = fmt_no(n)
        cols = list(h)
        qid = conn.execute(f"INSERT INTO quotes({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [h[c] for c in cols]).lastrowid
        _write_quote_lines(conn, qid, lines)
        _check_quote(conn, qid)
        return qid


def update_quote(conn, quote_id, lines=None, **fields):
    with tx(conn):
        q = _quote(conn, quote_id)
        if _invoiced_bp(conn, q["id"]):
            raise LedgerError("This quotation has been invoiced, so it can't change. Duplicate it to make a new version.")
        h = _quote_header(conn, fields, q)
        for k, v in h.items():
            conn.execute(f"UPDATE quotes SET {k} = ? WHERE id = ?", (v, q["id"]))
        q = conn.execute("SELECT * FROM quotes WHERE id = ?", (q["id"],)).fetchone()
        if q["valid_until"] < q["issue_date"]:
            raise LedgerError("valid_until cannot be before the quotation date")
        if lines is not None:
            _write_quote_lines(conn, q["id"], lines)
        _check_quote(conn, q["id"])


def set_quote_status(conn, quote_id, status):
    if status not in ("draft", "sent", "accepted", "declined"):
        raise LedgerError("status must be draft, sent, accepted or declined")
    with tx(conn):
        q = _quote(conn, quote_id)
        if status in ("declined", "draft") and _invoiced_bp(conn, q["id"]):
            raise LedgerError("This quotation has already been invoiced")
        conn.execute("UPDATE quotes SET status = ? WHERE id = ?", (status, q["id"]))


def delete_quote(conn, quote_id):
    with tx(conn):
        q = _quote(conn, quote_id)
        if conn.execute("SELECT 1 FROM invoices WHERE quote_id = ? AND status != 'void'", (q["id"],)).fetchone():
            raise LedgerError("This quotation has invoices; delete or void them first")
        conn.execute("UPDATE invoices SET quote_id = NULL WHERE quote_id = ?", (q["id"],))
        conn.execute("DELETE FROM quote_lines WHERE quote_id = ?", (q["id"],))
        conn.execute("DELETE FROM quotes WHERE id = ?", (q["id"],))


def _quote_lines_for_copy(conn, quote_id):
    return [{"product": l["product"], "description": l["description"], "quantity": l["quantity"],
             "unit_price_cents": l["unit_price_cents"], "revenue_account_code": l["code"], "taxable": bool(l["taxable"])}
            for l in conn.execute("SELECT l.*, a.code FROM quote_lines l JOIN accounts a ON a.id = l.revenue_account_id"
                                  " WHERE quote_id = ? ORDER BY l.id", (quote_id,))]


def duplicate_quote(conn, quote_id, issue_date=None):
    with tx(conn):
        q = _quote(conn, quote_id)
        return create_quote(conn, q["client_id"], issue_date or _today(), _quote_lines_for_copy(conn, q["id"]),
                            project_id=q["project_id"], tax_rate_bp=q["tax_rate_bp"], notes=q["notes"],
                            discount_type=q["discount_type"], discount_value=q["discount_value"])


def invoice_from_quote(conn, quote_id, percent_bp=10000, issue_date=None, due_date=None):
    """Create a DRAFT invoice for all or part of a quotation (e.g. 5000 = a 50% advance).
    The last part bills exactly what is left, so the parts add up to the quotation."""
    percent_bp = _int(percent_bp, "percent_bp")
    with tx(conn):
        q = _quote(conn, quote_id)
        done = _invoiced_bp(conn, q["id"])
        left = 10000 - done
        if left <= 0:
            raise LedgerError("This quotation has already been fully invoiced")
        if not 0 < percent_bp <= left:
            raise LedgerError(f"You can invoice up to {left / 100:g}% of this quotation")
        is_last = percent_bp == left
        pct_txt = f"{percent_bp / 100:g}%"
        label = ("" if percent_bp == 10000 else f"{pct_txt} advance" if done == 0
                 else f"balance ({pct_txt})" if is_last else f"part payment ({pct_txt})")

        def part(amount):  # this invoice's share of an amount
            if percent_bp == 10000:
                return amount
            if is_last:
                return amount - _round(Decimal(amount) * done / Decimal(10000))
            return _round(Decimal(amount) * percent_bp / Decimal(10000))

        lines = []
        for l in _quote_lines_for_copy(conn, q["id"]):
            if percent_bp == 10000:
                lines.append(l)
                continue
            amount = _round(_qty(l["quantity"]) * l["unit_price_cents"])
            desc = f"{l['description']} - {label}" if l["description"] else label
            lines.append({**l, "description": desc, "quantity": "1", "unit_price_cents": part(amount)})
        dvalue = q["discount_value"]
        if q["discount_type"] == "amount" and percent_bp != 10000:
            dvalue = part(dvalue)
        notes = "\n".join(x for x in (f"Quotation {q['number']}" + (f": {label}" if label else ""), q["notes"]) if x)
        inv_id = create_invoice(conn, q["client_id"], issue_date or _today(), lines, due_date=due_date,
                                project_id=q["project_id"], tax_rate_bp=q["tax_rate_bp"], notes=notes,
                                discount_type=q["discount_type"],
                                discount_value=dvalue if q["discount_type"] else 0)
        conn.execute("UPDATE invoices SET quote_id = ?, quote_pct_bp = ? WHERE id = ?", (q["id"], percent_bp, inv_id))
        if q["status"] in ("draft", "sent"):
            conn.execute("UPDATE quotes SET status = 'accepted' WHERE id = ?", (q["id"],))
        return inv_id


def get_quote(conn, quote_id):
    q = _one(conn, "SELECT q.*, c.name AS client_name, c.email AS client_email, c.address AS client_address,"
                   " p.name AS project_name FROM quotes q JOIN clients c ON c.id = q.client_id"
                   " LEFT JOIN projects p ON p.id = q.project_id WHERE q.id = ?",
             (_int(quote_id, "quote_id"),), "Quotation")
    out = dict(q)
    t = _quote_totals(conn, q["id"])
    out.update(t)
    out["display_status"] = _quote_status(q, t)
    out["lines"] = [dict(r) for r in conn.execute(
        "SELECT l.*, a.code AS revenue_account_code FROM quote_lines l JOIN accounts a ON a.id = l.revenue_account_id"
        " WHERE quote_id = ? ORDER BY l.id", (q["id"],))]
    out["invoices"] = [dict(r) for r in conn.execute(
        "SELECT id, number, status, issue_date, quote_pct_bp FROM invoices WHERE quote_id = ? ORDER BY id", (q["id"],))]
    return out


def list_quotes(conn):
    rows = []
    for q in conn.execute("SELECT q.*, c.name AS client_name, p.name AS project_name FROM quotes q"
                          " JOIN clients c ON c.id = q.client_id LEFT JOIN projects p ON p.id = q.project_id"
                          " ORDER BY q.issue_date DESC, q.id DESC").fetchall():
        d = dict(q)
        t = _quote_totals(conn, q["id"])
        d.update(t)
        d["display_status"] = _quote_status(q, t)
        rows.append(d)
    return rows


# --------------------------------------------------------------------------
# Contracts (documents only; they never touch the books)
# --------------------------------------------------------------------------

CONTRACT_FIELDS = {"client_id", "project_id", "quote_id", "title", "kind", "start_date", "end_date", "value_cents",
                   "monthly_fee_cents", "payment_terms", "scope", "clauses"}


def _contract(conn, contract_id):
    return _one(conn, "SELECT * FROM contracts WHERE id = ?", (_int(contract_id, "contract_id"),), "Contract")


def _contract_status(c, today=None):
    today = today or _today()
    if c["status"] == "signed":
        if c["end_date"] and c["end_date"] < today:
            return "ended"
        if c["start_date"] > today:
            return "signed"  # signed, starts later
        return "active"
    return c["status"]


def _contract_values(conn, fields, current=None):
    bad = set(fields) - CONTRACT_FIELDS
    if bad:
        raise LedgerError(f"Unknown contract field(s): {', '.join(sorted(bad))}")
    v = dict(current) if current else {}
    v.update({k: x for k, x in fields.items()})
    out = {}
    c = _client(conn, v.get("client_id"))
    if not current or "client_id" in fields:
        if not c["is_active"]:
            raise LedgerError("Client is inactive")
    out["client_id"] = c["id"]
    out["project_id"] = _project(conn, v["project_id"], c["id"])["id"] if v.get("project_id") else None
    out["quote_id"] = _quote(conn, v["quote_id"])["id"] if v.get("quote_id") else None
    out["title"] = " ".join(str(v.get("title") or "").split())[:200]
    if not out["title"]:
        raise LedgerError("Give the contract a title, e.g. 'Website development agreement'")
    out["kind"] = v.get("kind") or "project"
    if out["kind"] not in ("project", "retainer"):
        raise LedgerError("kind must be 'project' or 'retainer'")
    out["start_date"] = _date(v.get("start_date"), "start_date")
    out["end_date"] = _date(v["end_date"], "end_date") if v.get("end_date") else None
    if out["end_date"] and out["end_date"] < out["start_date"]:
        raise LedgerError("The end date can't be before the start date")
    out["value_cents"] = _cents(v.get("value_cents") or 0, "value_cents", allow_zero=True)
    out["monthly_fee_cents"] = _cents(v.get("monthly_fee_cents") or 0, "monthly_fee_cents", allow_zero=True)
    if out["kind"] == "retainer" and not out["monthly_fee_cents"]:
        raise LedgerError("Enter the monthly fee for a retainer contract")
    if out["kind"] == "project" and not out["value_cents"]:
        raise LedgerError("Enter the contract value for a project contract")
    for k in ("payment_terms", "scope", "clauses"):
        out[k] = str(v.get(k) or "")
        if len(out[k]) > 30000:
            raise LedgerError(f"{k} is too long")
    return out


def create_contract(conn, **fields):
    with tx(conn):
        s = get_settings(conn)
        fields.setdefault("payment_terms", s["contract_payment_terms"])
        fields.setdefault("clauses", s["contract_clauses"])
        v = _contract_values(conn, fields)
        n = int(s["next_contract_number"])
        while conn.execute("SELECT 1 FROM contracts WHERE number = ?", (f"{s['contract_prefix']}{n}",)).fetchone():
            n += 1
        conn.execute("UPDATE settings SET value = ? WHERE key = 'next_contract_number'", (str(n + 1),))
        v["number"] = f"{s['contract_prefix']}{n}"
        cols = list(v)
        return conn.execute(f"INSERT INTO contracts({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            [v[c] for c in cols]).lastrowid


def update_contract(conn, contract_id, **fields):
    with tx(conn):
        c = _contract(conn, contract_id)
        if c["status"] in ("signed", "cancelled"):
            raise LedgerError(f"A {c['status']} contract can't change. Duplicate it to make a new version.")
        v = _contract_values(conn, fields, c)
        for k, x in v.items():
            conn.execute(f"UPDATE contracts SET {k} = ? WHERE id = ?", (x, c["id"]))


def sign_contract(conn, contract_id, signed_date, signed_by, update_retainer=False):
    """Record the client's signature. For a retainer, optionally make its monthly fee the
    client's retainer so the monthly 'Generate retainers' run bills it."""
    with tx(conn):
        c = _contract(conn, contract_id)
        if c["status"] not in ("draft", "sent"):
            raise LedgerError(f"This contract is already {c['status']}")
        signed_by = " ".join(str(signed_by or "").split())[:120]
        if not signed_by:
            raise LedgerError("Enter the name of the person who signed for the client")
        conn.execute("UPDATE contracts SET status = 'signed', signed_date = ?, signed_by = ? WHERE id = ?",
                     (_date(signed_date, "signed_date"), signed_by, c["id"]))
        if update_retainer and c["kind"] == "retainer":
            conn.execute("UPDATE clients SET retainer_cents = ? WHERE id = ?", (c["monthly_fee_cents"], c["client_id"]))


def set_contract_status(conn, contract_id, status):
    if status not in ("draft", "sent", "cancelled"):
        raise LedgerError("Use sign to mark a contract signed; status must be draft, sent or cancelled")
    with tx(conn):
        c = _contract(conn, contract_id)
        if c["status"] == "signed" and status != "cancelled":
            raise LedgerError("A signed contract can only be cancelled")
        conn.execute("UPDATE contracts SET status = ? WHERE id = ?", (status, c["id"]))


def delete_contract(conn, contract_id):
    with tx(conn):
        c = _contract(conn, contract_id)
        if c["status"] == "signed":
            raise LedgerError("Signed contracts are kept for your records; cancel it instead")
        conn.execute("DELETE FROM contracts WHERE id = ?", (c["id"],))


def duplicate_contract(conn, contract_id):
    with tx(conn):
        c = _contract(conn, contract_id)
        return create_contract(conn, **{k: c[k] for k in CONTRACT_FIELDS if k != "quote_id"})


def contract_from_quote(conn, quote_id):
    """Draft a project contract from a quotation: same client, project, value and scope."""
    with tx(conn):
        q = get_quote(conn, quote_id)
        scope = "\n".join(
            "- " + " - ".join(x for x in (l["product"], l["description"]) if x)
            + (f" (x{l['quantity']})" if l["quantity"] not in ("1", "1.0") else "") for l in q["lines"])
        if q["notes"]:
            scope += "\n\n" + q["notes"]
        first = next((l["product"] or l["description"] for l in q["lines"]), "Services")
        return create_contract(conn, client_id=q["client_id"], project_id=q["project_id"], quote_id=q["id"],
                               title=f"{first} agreement", kind="project", start_date=_today(),
                               value_cents=q["total_cents"], scope=scope)


def get_contract(conn, contract_id):
    c = _one(conn, "SELECT k.*, c.name AS client_name, c.email AS client_email, c.address AS client_address,"
                   " p.name AS project_name, q.number AS quote_number FROM contracts k JOIN clients c ON c.id = k.client_id"
                   " LEFT JOIN projects p ON p.id = k.project_id LEFT JOIN quotes q ON q.id = k.quote_id WHERE k.id = ?",
             (_int(contract_id, "contract_id"),), "Contract")
    d = dict(c)
    d["display_status"] = _contract_status(c)
    return d


def list_contracts(conn):
    rows = []
    for c in conn.execute("SELECT k.*, c.name AS client_name FROM contracts k JOIN clients c ON c.id = k.client_id"
                          " ORDER BY k.start_date DESC, k.id DESC").fetchall():
        d = dict(c)
        d["display_status"] = _contract_status(c)
        d.pop("clauses", None)
        rows.append(d)
    return rows


# --------------------------------------------------------------------------
# Partners & capital
# --------------------------------------------------------------------------

RETAINED = "3900"


def _seed_partners_from_env(conn):
    """One-time setup from the TECHWON_PARTNERS setting ("Shayan,Kashmala,Anas"), equal shares.
    Runs only when the books have no partners yet; afterwards partners are managed in the app."""
    import os
    names = [" ".join(n.split()) for n in os.environ.get("TECHWON_PARTNERS", "").split(",") if n.strip()]
    if not names or conn.execute("SELECT 1 FROM partners").fetchone() or \
            conn.execute("SELECT 1 FROM settings WHERE key = 'partners_seeded'").fetchone():
        return
    shares = _equal_shares(len(names))
    for name, bp in zip(names, shares):
        create_partner(conn, name, bp)
    conn.execute("INSERT INTO settings(key, value) VALUES ('partners_seeded', '1') ON CONFLICT(key) DO NOTHING")


def _equal_shares(n):
    base = 10000 // n
    return [base] * (n - 1) + [10000 - base * (n - 1)]


def _free_code(conn, lo, hi, step=10):
    used = {r[0] for r in conn.execute("SELECT code FROM accounts")}
    for c in range(lo, hi + 1, step):
        if str(c) not in used:
            return str(c)
    raise LedgerError("No free account numbers left for partners")


def create_partner(conn, name, share_bp=0):
    name = " ".join(str(name or "").split())[:80]
    if not name:
        raise LedgerError("Enter the partner's name")
    share_bp = _int(share_bp or 0, "share_bp")
    if not 0 <= share_bp <= 10000:
        raise LedgerError("A profit share must be between 0% and 100%")
    with tx(conn):
        if conn.execute("SELECT 1 FROM partners WHERE lower(name) = lower(?)", (name,)).fetchone():
            raise LedgerError(f"{name} is already a partner")
        cap = create_account(conn, _free_code(conn, 3010, 3090), f"Capital - {name}", "equity", "equity")
        drw = create_account(conn, _free_code(conn, 3110, 3190), f"Drawings - {name}", "equity", "drawings")
        return conn.execute("INSERT INTO partners(name, share_bp, capital_account_id, drawings_account_id)"
                            " VALUES (?,?,?,?)", (name, share_bp, cap, drw)).lastrowid


def _partner(conn, partner_id):
    return _one(conn, "SELECT * FROM partners WHERE id = ?", (_int(partner_id, "partner_id"),), "Partner")


def update_partner(conn, partner_id, name=None, is_active=None):
    with tx(conn):
        p = _partner(conn, partner_id)
        if name is not None:
            name = " ".join(str(name).split())[:80]
            if not name:
                raise LedgerError("Enter the partner's name")
            if conn.execute("SELECT 1 FROM partners WHERE lower(name) = lower(?) AND id != ?", (name, p["id"])).fetchone():
                raise LedgerError(f"{name} is already a partner")
            conn.execute("UPDATE partners SET name = ? WHERE id = ?", (name, p["id"]))
            conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (f"Capital - {name}", p["capital_account_id"]))
            conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (f"Drawings - {name}", p["drawings_account_id"]))
        if is_active is not None:
            conn.execute("UPDATE partners SET is_active = ? WHERE id = ?", (1 if is_active else 0, p["id"]))
            if not is_active:
                conn.execute("UPDATE partners SET share_bp = 0 WHERE id = ?", (p["id"],))


def set_partner_shares(conn, shares):
    """shares: {partner_id: basis points}. Active partners' shares must add up to exactly 100%."""
    with tx(conn):
        active = {r["id"] for r in conn.execute("SELECT id FROM partners WHERE is_active = 1")}
        given = {_int(k, "partner_id"): _int(v, "share") for k, v in (shares or {}).items()}
        if set(given) != active:
            raise LedgerError("Give a share for every active partner")
        if any(not 0 <= v <= 10000 for v in given.values()):
            raise LedgerError("Each share must be between 0% and 100%")
        if sum(given.values()) != 10000:
            raise LedgerError(f"Shares add up to {sum(given.values()) / 100:g}%; they must add up to exactly 100%")
        for pid, bp in given.items():
            conn.execute("UPDATE partners SET share_bp = ? WHERE id = ?", (bp, pid))


def _cash_account(conn, code):
    a = account_by_code(conn, code)
    if a["subtype"] not in ("cash", "credit_card"):
        raise LedgerError("Choose a bank, cash or card account")
    return a


def partner_contribution(conn, partner_id, date, amount_cents, account_code=CASH, memo=""):
    """Partner puts money into the business: Dr bank/cash, Cr their capital."""
    with tx(conn):
        p = _partner(conn, partner_id)
        amt = _cents(amount_cents)
        bank = _cash_account(conn, account_code)
        return _post(conn, date, f"Capital added by {p['name']}" + (f": {memo}" if memo else ""), [
            {"account_id": bank["id"], "debit_cents": amt},
            {"account_id": p["capital_account_id"], "credit_cents": amt, "memo": memo},
        ], "capital", p["id"])


def partner_drawing(conn, partner_id, date, amount_cents, account_code=CASH, memo=""):
    """Partner takes money out: Dr their drawings, Cr bank/cash."""
    with tx(conn):
        p = _partner(conn, partner_id)
        amt = _cents(amount_cents)
        bank = _cash_account(conn, account_code)
        return _post(conn, date, f"Drawings by {p['name']}" + (f": {memo}" if memo else ""), [
            {"account_id": p["drawings_account_id"], "debit_cents": amt, "memo": memo},
            {"account_id": bank["id"], "credit_cents": amt},
        ], "drawing", p["id"])


def allocate_profit(conn, start, end):
    """Share the period's net profit (or loss) into partners' capital by their ratio.
    Posted on the last day; a period can't overlap one already allocated."""
    start, end = _date(start, "start"), _date(end, "end")
    if end < start:
        raise LedgerError("The end date must be after the start date")
    with tx(conn):
        clash = conn.execute("SELECT * FROM profit_allocations WHERE start_date <= ? AND end_date >= ?", (end, start)).fetchone()
        if clash:
            raise LedgerError(f"Profit for {clash['start_date']} to {clash['end_date']} is already allocated; periods can't overlap")
        partners = conn.execute("SELECT * FROM partners WHERE is_active = 1 AND share_bp > 0 ORDER BY id").fetchall()
        if not partners or sum(p["share_bp"] for p in partners) != 10000:
            raise LedgerError("Set the partners' profit shares (adding up to 100%) before allocating profit")
        ni = profit_and_loss(conn, start, end)["net_income_cents"]
        if ni == 0:
            raise LedgerError("There is no profit or loss in that period to allocate")
        parts, given = [], 0
        for i, p in enumerate(partners):
            amt = ni - given if i == len(partners) - 1 else _round(Decimal(ni) * p["share_bp"] / Decimal(10000))
            given += amt
            parts.append((p, amt))
        retained = account_by_code(conn, RETAINED, must_be_active=False)
        lines = [{"account_id": retained["id"], **({"debit_cents": ni} if ni > 0 else {"credit_cents": -ni}),
                  "memo": "Profit allocated to partners" if ni > 0 else "Loss allocated to partners"}]
        for p, amt in parts:
            if amt:
                lines.append({"account_id": p["capital_account_id"],
                              **({"credit_cents": amt} if amt > 0 else {"debit_cents": -amt}),
                              "memo": f"{p['share_bp'] / 100:g}% share"})
        eid = _post(conn, end, f"{'Profit' if ni > 0 else 'Loss'} allocation {start} to {end}", lines, "profit_allocation")
        conn.execute("INSERT INTO profit_allocations(start_date, end_date, net_income_cents, entry_id) VALUES (?,?,?,?)",
                     (start, end, ni, eid))
        return eid


def _acct_movement(conn, account_id, start=None, end=None, source=None, exclude_source=None):
    q = ("SELECT COALESCE(SUM(l.credit_cents - l.debit_cents),0) FROM journal_lines l JOIN journal_entries e"
         " ON e.id = l.entry_id WHERE l.account_id = ?")
    args = [account_id]
    if start:
        q += " AND e.date >= ?"; args.append(start)
    if end:
        q += " AND e.date <= ?"; args.append(end)
    if source:
        q += " AND e.source_type = ?"; args.append(source)
    if exclude_source:
        q += " AND e.source_type != ?"; args.append(exclude_source)
    return conn.execute(q, args).fetchone()[0]


def capital_statement(conn, start, end):
    """Per partner: opening + money in + profit share - drawings = closing (credit-positive)."""
    start, end = _date(start, "start"), _date(end, "end")
    before = (dt.date.fromisoformat(start) - dt.timedelta(days=1)).isoformat()
    fy = _fy_start(conn, end)
    unallocated = profit_and_loss(conn, fy, end)["net_income_cents"] - sum(
        r[0] for r in conn.execute("SELECT net_income_cents FROM profit_allocations WHERE start_date >= ? AND end_date <= ?",
                                   (fy, end)))
    rows = []
    for p in conn.execute("SELECT * FROM partners ORDER BY is_active DESC, id").fetchall():
        cap, drw = p["capital_account_id"], p["drawings_account_id"]
        opening = _acct_movement(conn, cap, end=before) + _acct_movement(conn, drw, end=before)
        profit = _acct_movement(conn, cap, start, end, source="profit_allocation")
        added = _acct_movement(conn, cap, start, end, exclude_source="profit_allocation")
        drawn = -_acct_movement(conn, drw, start, end)
        closing = opening + added + profit - drawn
        rows.append({"partner_id": p["id"], "name": p["name"], "share_bp": p["share_bp"], "is_active": bool(p["is_active"]),
                     "opening_cents": opening, "added_cents": added, "profit_cents": profit, "drawn_cents": drawn,
                     "closing_cents": closing,
                     "unallocated_share_cents": _round(Decimal(unallocated) * p["share_bp"] / Decimal(10000)) if p["is_active"] else 0,
                     "capital_account_code": conn.execute("SELECT code FROM accounts WHERE id = ?", (cap,)).fetchone()[0]})
    total = {k: sum(r[k] for r in rows) for k in ("opening_cents", "added_cents", "profit_cents", "drawn_cents", "closing_cents")}
    allocations = [dict(r) for r in conn.execute("SELECT * FROM profit_allocations ORDER BY end_date DESC")]
    return {"start": start, "end": end, "fiscal_year_start": fy, "rows": rows, "totals": total,
            "unallocated_profit_cents": unallocated, "allocations": allocations}


def list_partners(conn):
    return [dict(r) | {"capital_account_code": r["cap_code"], "drawings_account_code": r["drw_code"]} for r in conn.execute(
        "SELECT p.*, a.code AS cap_code, d.code AS drw_code FROM partners p JOIN accounts a ON a.id = p.capital_account_id"
        " JOIN accounts d ON d.id = p.drawings_account_id ORDER BY p.is_active DESC, p.id")]


def capital_history(conn, limit=50):
    return [dict(r) for r in conn.execute(
        "SELECT e.id, e.date, e.memo, e.source_type, e.created_by, e.reversed_by_entry_id, e.reverses_entry_id,"
        " (SELECT SUM(debit_cents) FROM journal_lines WHERE entry_id = e.id) AS amount_cents FROM journal_entries e"
        " WHERE e.source_type IN ('capital','drawing','profit_allocation') ORDER BY e.date DESC, e.id DESC LIMIT ?",
        (_int(limit, "limit"),))]


def reverse_capital_entry(conn, entry_id, date=None):
    with tx(conn):
        e = _one(conn, "SELECT * FROM journal_entries WHERE id = ?", (_int(entry_id, "entry_id"),), "Entry")
        if e["source_type"] not in ("capital", "drawing", "profit_allocation"):
            raise LedgerError("That isn't a capital, drawing or profit allocation entry")
        rid = _reverse(conn, e["id"], date or max(_today(), e["date"]), f"Reversal: {e['memo']}")
        if e["source_type"] == "profit_allocation":
            conn.execute("DELETE FROM profit_allocations WHERE entry_id = ?", (e["id"],))
        return rid


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
            raise LedgerError(f"Payment is more than the {_money(conn, bal)} still owed on this expense")
        src = account_by_code(conn, paid_from_account_code)
        # A partner's capital account means it was paid from their own pocket: it counts as capital they put in.
        by_partner = conn.execute("SELECT name FROM partners WHERE capital_account_id = ?", (src["id"],)).fetchone()
        if not by_partner and src["subtype"] not in ("cash", "credit_card"):
            raise LedgerError("Pay bills from a bank/cash or credit card account, or as paid personally by a partner")
        eid = _post(conn, date, f"Payment to {b['vendor_name']}" + (f" (paid personally by {by_partner['name']})" if by_partner else ""), [
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


def _payee_vendor(conn, payee):
    """Expenses only need an optional 'paid to' name; it is kept as a vendor row behind the scenes."""
    name = " ".join(str(payee or "").split())[:120] or GENERAL_PAYEE
    row = conn.execute("SELECT id FROM vendors WHERE lower(name) = lower(?)", (name,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO vendors(name) VALUES (?)", (name,)).lastrowid


def record_expense(conn, date, amount_cents, category_code, paid_from=None, payee="", description="",
                   due_date=None, client_id=None, project_id=None, billable=False, reference=""):
    """Record money spent. paid_from: a bank/cash/card account code, or empty for 'not paid yet'
    (it then waits in Accounts Payable until paid)."""
    with tx(conn):
        acct = account_by_code(conn, category_code)
        if acct["type"] != "expense":
            raise LedgerError("Choose an expense category")
        return create_bill(conn, _payee_vendor(conn, payee), date, amount_cents, acct["code"], description,
                           due_date=due_date or None, client_id=client_id or None, project_id=project_id or None,
                           billable=billable, reference=reference, paid_from=paid_from or None)


def delete_expense(conn, bill_id, date=None):
    """Void an expense together with its payments, all or nothing. It stays in the
    journal as reversed entries; a rebilled or bank-reconciled expense is refused."""
    with tx(conn):
        b = _one(conn, "SELECT * FROM bills WHERE id = ?", (_int(bill_id, "bill_id"),), "Expense")
        if b["status"] != "open":
            raise LedgerError("This expense is already deleted")
        if b["rebilled_invoice_id"]:
            raise LedgerError("This expense was charged to a client on an invoice; delete or void that invoice first")
        for p in conn.execute("SELECT id FROM bill_payments WHERE bill_id = ? AND voided = 0", (b["id"],)).fetchall():
            void_bill_payment(conn, p["id"], date)
        void_bill(conn, b["id"], date)


def list_payees(conn):
    return [r[0] for r in conn.execute("SELECT name FROM vendors WHERE name != ? ORDER BY lower(name)",
                                       (GENERAL_PAYEE,))]


CATEGORY_KINDS = {"overhead": ("opex", 6000, 6999), "client": ("cogs", 5100, 5999)}


def expense_categories(conn):
    rows = []
    for a in conn.execute("SELECT a.*, (SELECT COUNT(*) FROM journal_lines l WHERE l.account_id = a.id) AS uses"
                          " FROM accounts a WHERE a.type = 'expense' ORDER BY a.code"):
        d = dict(a)
        d["kind"] = {"opex": "overhead", "cogs": "client", "passthrough_cost": "media"}.get(a["subtype"], "other")
        rows.append(d)
    return rows


def create_expense_category(conn, name, kind="overhead"):
    name = " ".join(str(name or "").split())
    if not name:
        raise LedgerError("Enter a category name")
    if kind not in CATEGORY_KINDS:
        raise LedgerError("kind must be 'overhead' or 'client'")
    subtype, lo, hi = CATEGORY_KINDS[kind]
    with tx(conn):
        if conn.execute("SELECT 1 FROM accounts WHERE lower(name) = lower(?)", (name,)).fetchone():
            raise LedgerError(f"A category or account named '{name}' already exists")
        used = {int(r[0]) for r in conn.execute("SELECT code FROM accounts") if str(r[0]).isdigit()}
        top = max([c for c in used if lo <= c <= hi], default=lo - 10)
        code = (top // 10 + 1) * 10
        if code > hi or code in used:
            code = next((c for c in range(lo, hi + 1) if c not in used), None)
            if code is None:
                raise LedgerError("No free account numbers left for this kind of category")
        create_account(conn, str(code), name, "expense", subtype)
        return str(code)


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
                "description": f"{b['description'] or 'Expense'} ("
                               + (f"{b['vendor_name']}, " if b['vendor_name'] != GENERAL_PAYEE else "") + f"{b['bill_date']})",
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
    if conn.execute("SELECT 1 FROM partners").fetchone():
        kept = sum(e["amount_cents"] for e in equity if e["subtype"] == "retained_earnings")
        equity = [e for e in equity if e["subtype"] != "retained_earnings"]
        undistributed = retained + current + kept
        if undistributed:
            equity.append({"code": "", "name": "Profit not yet allocated to partners", "subtype": "computed",
                           "amount_cents": undistributed})
        retained = current = 0
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
