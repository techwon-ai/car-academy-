"""Server-to-server link used by the Techwon Portal.

Books is the single source of truth for money. The portal keeps its own screens,
client approvals and WhatsApp messages, but every quotation, invoice and payment
it makes is stored here through these functions.

Amounts cross the link in integer cents. Records the portal created carry its id
in `external_id`, so a retried request or a re-run import never makes a second copy.
"""

import json

import ledger as L
from ledger import LedgerError, tx

METHOD_ACCOUNTS = {"cash": L.CASH_IN_HAND}  # anything else lands in the bank account


def _meta(raw):
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def _clean_meta(meta):
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise LedgerError("meta must be an object")
    text = json.dumps(meta, default=str)
    if len(text) > 200_000:
        raise LedgerError("meta is too large")
    return text


def _ext(value, what="external_id"):
    v = str(value or "").strip()
    if not v or len(v) > 100:
        raise LedgerError(f"{what} is required (up to 100 characters)")
    return v


# --------------------------------------------------------------------------
# Clients
# --------------------------------------------------------------------------

def upsert_client(conn, external_id, name, email="", address=""):
    """Find the Books client linked to a portal client, linking or creating one if needed."""
    ext = _ext(external_id, "client external_id")
    name = " ".join(str(name or "").split())[:200] or f"Client {ext[:8]}"
    email, address = str(email or "")[:200], str(address or "")[:1000]
    with tx(conn):
        row = conn.execute("SELECT * FROM clients WHERE external_id = ?", (ext,)).fetchone()
        if row:
            if row["name"] != name and not conn.execute(
                    "SELECT 1 FROM clients WHERE lower(name) = lower(?) AND id != ?", (name, row["id"])).fetchone():
                conn.execute("UPDATE clients SET name = ? WHERE id = ?", (name, row["id"]))
            if email and email != row["email"]:
                conn.execute("UPDATE clients SET email = ? WHERE id = ?", (email, row["id"]))
            if address and address != row["address"]:
                conn.execute("UPDATE clients SET address = ? WHERE id = ?", (address, row["id"]))
            if not row["is_active"]:
                conn.execute("UPDATE clients SET is_active = 1 WHERE id = ?", (row["id"],))
            return row["id"]
        # Same name already in Books and not linked to anything yet: link it instead of duplicating.
        row = conn.execute("SELECT * FROM clients WHERE lower(name) = lower(?) AND external_id IS NULL",
                           (name,)).fetchone()
        if row:
            conn.execute("UPDATE clients SET external_id = ?, is_active = 1 WHERE id = ?", (ext, row["id"]))
            return row["id"]
        unique, n = name, 2
        while conn.execute("SELECT 1 FROM clients WHERE lower(name) = lower(?)", (unique,)).fetchone():
            unique, n = f"{name} ({n})", n + 1
        cid = L.create_client(conn, unique, email, address)
        conn.execute("UPDATE clients SET external_id = ? WHERE id = ?", (ext, cid))
        return cid


def _client_from(conn, p):
    c = p.get("client") or {}
    return upsert_client(conn, c.get("external_id"), c.get("name"), c.get("email", ""), c.get("address", ""))


def _lines(items):
    if not isinstance(items, list) or not items:
        raise LedgerError("Add at least one line item")
    out = []
    for i, it in enumerate(items, 1):
        label = " ".join(str(it.get("label") or "").split())[:200] or "Service"
        out.append({"product": label, "description": str(it.get("description") or "")[:1000],
                    "quantity": str(it.get("qty") or 1), "unit_price_cents": L._cents(
                        it.get("rate_cents", 0), f"line {i} rate", allow_zero=True),
                    "revenue_account_code": it.get("account_code") or "4100", "taxable": True})
    return out


def _amount(p, key):
    return L._cents(p.get(key) or 0, key, allow_zero=True)


# --------------------------------------------------------------------------
# Invoices
# --------------------------------------------------------------------------

def _by_external(conn, table, ext):
    if not ext:
        return None
    row = conn.execute(f"SELECT id FROM {table} WHERE external_id = ?", (str(ext),)).fetchone()
    return row["id"] if row else None


def create_invoice(conn, p):
    """Create and issue an invoice. With `number`, `issue_date` and `payments` this also
    imports a historical invoice exactly as it was."""
    with tx(conn):
        existing = _by_external(conn, "invoices", p.get("external_id"))
        if existing:
            return existing
        cid = _client_from(conn, p)
        issue = L._date(p.get("issue_date") or L._today(), "issue_date")
        due = L._date(p.get("due_date"), "due_date") if p.get("due_date") else issue
        discount = _amount(p, "discount_cents")
        inv = L.create_invoice(conn, cid, issue, _lines(p.get("items")), due_date=due, tax_rate_bp=0,
                               notes=str(p.get("notes") or ""),
                               discount_type="amount" if discount else None, discount_value=discount)
        quote_id = int(p["quote_id"]) if p.get("quote_id") else None
        pct = L._int(p.get("quote_pct_bp") or 10000, "quote_pct_bp") if quote_id else None
        if quote_id:
            L._quote(conn, quote_id)
        conn.execute("UPDATE invoices SET tax_override_cents = ?, external_id = ?, meta = ?, quote_id = ?,"
                     " quote_pct_bp = ? WHERE id = ?",
                     (_amount(p, "tax_cents") or None, str(p["external_id"]) if p.get("external_id") else None,
                      _clean_meta(p.get("meta") or {}), quote_id, pct, inv))
        L._check_invoice_totals(conn, inv)
        L.issue_invoice(conn, inv, number=p.get("number"))
        for pay in p.get("payments") or []:
            record_payment(conn, inv, pay)
        return inv


def record_payment(conn, invoice_id, pay):
    method = str(pay.get("method") or "bank").lower()[:30]
    with tx(conn):
        return L.record_payment(conn, invoice_id, pay.get("date") or L._today(), pay.get("amount_cents"),
                                METHOD_ACCOUNTS.get(method, L.CASH), str(pay.get("note") or "")[:200], method)


def update_invoice(conn, invoice_id, due_date=None, notes=None, meta=None):
    """Change things that don't affect amounts (so nothing is re-posted)."""
    with tx(conn):
        inv = L._one(conn, "SELECT * FROM invoices WHERE id = ?", (L._int(invoice_id, "invoice_id"),), "Invoice")
        if due_date is not None:
            due = L._date(due_date, "due_date") if due_date else inv["issue_date"]
            if due < inv["issue_date"]:
                raise LedgerError("The due date can't be before the invoice date")
            conn.execute("UPDATE invoices SET due_date = ? WHERE id = ?", (due, inv["id"]))
        if notes is not None:
            conn.execute("UPDATE invoices SET notes = ? WHERE id = ?", (str(notes)[:5000], inv["id"]))
        if meta:
            conn.execute("UPDATE invoices SET meta = ? WHERE id = ?",
                         (_clean_meta({**_meta(inv["meta"]), **meta}), inv["id"]))


def remove_invoice(conn, invoice_id):
    """What 'delete' means for an invoice: drafts go; issued ones are voided (kept for the
    record); invoices with payments are refused until the payments are voided."""
    with tx(conn):
        inv = L._one(conn, "SELECT * FROM invoices WHERE id = ?", (L._int(invoice_id, "invoice_id"),), "Invoice")
        if inv["status"] == "draft":
            return L.delete_draft_invoice(conn, inv["id"])
        if inv["status"] == "void":
            return None
        if L._invoice_totals(conn, inv["id"])["paid_cents"]:
            raise LedgerError(f"Invoice {inv['number']} has payments recorded, so it can't be deleted. "
                              "Remove the payments in Techwon Books first.")
        return L.void_invoice(conn, inv["id"])


def invoice_doc(conn, invoice_id):
    d = L.get_invoice(conn, invoice_id)
    extra = conn.execute("SELECT c.external_id AS client_external_id, q.external_id AS quote_external_id"
                         " FROM invoices i JOIN clients c ON c.id = i.client_id LEFT JOIN quotes q ON q.id = i.quote_id"
                         " WHERE i.id = ?", (d["id"],)).fetchone()
    d.update(dict(extra))
    d["meta"] = _meta(d.get("meta"))
    d["payments"] = [p for p in d["payments"] if not p["voided"]]
    return d


def list_invoices(conn, client_external_id=None, include_void=False):
    q, args = "SELECT i.id FROM invoices i JOIN clients c ON c.id = i.client_id WHERE 1=1", []
    if client_external_id:
        q += " AND c.external_id = ?"
        args.append(str(client_external_id))
    if not include_void:
        q += " AND i.status != 'void'"
    return [invoice_doc(conn, r["id"]) for r in conn.execute(q + " ORDER BY i.id DESC", args).fetchall()]


# --------------------------------------------------------------------------
# Quotations
# --------------------------------------------------------------------------

PORTAL_TO_BOOKS_STATUS = {"draft": "draft", "sent": "sent", "accepted": "accepted", "rejected": "declined",
                          "declined": "declined"}


def create_quote(conn, p):
    with tx(conn):
        existing = _by_external(conn, "quotes", p.get("external_id"))
        if existing:
            return existing
        cid = _client_from(conn, p)
        discount = _amount(p, "discount_cents")
        fields = {"tax_rate_bp": 0, "notes": str(p.get("notes") or "")}
        if discount:
            fields.update(discount_type="amount", discount_value=discount)
        if p.get("valid_until"):
            fields["valid_until"] = p["valid_until"]
        qid = L.create_quote(conn, cid, p.get("issue_date") or L._today(), _lines(p.get("items")),
                             number=p.get("number"), **fields)
        conn.execute("UPDATE quotes SET tax_override_cents = ?, external_id = ?, meta = ? WHERE id = ?",
                     (_amount(p, "tax_cents") or None, str(p["external_id"]) if p.get("external_id") else None,
                      _clean_meta(p.get("meta") or {}), qid))
        L._check_quote(conn, qid)
        status = PORTAL_TO_BOOKS_STATUS.get(str(p.get("status") or "draft"))
        if status is None:
            raise LedgerError("Unknown quotation status")
        if status != "draft":
            L.set_quote_status(conn, qid, status)
        return qid


def update_quote(conn, quote_id, status=None, notes=None, meta=None):
    with tx(conn):
        q = L._quote(conn, quote_id)
        if status is not None:
            st = PORTAL_TO_BOOKS_STATUS.get(str(status))
            if st is None:
                raise LedgerError("Unknown quotation status")
            if st != q["status"]:
                L.set_quote_status(conn, q["id"], st)
        if notes is not None:
            conn.execute("UPDATE quotes SET notes = ? WHERE id = ?", (str(notes)[:5000], q["id"]))
        if meta:
            conn.execute("UPDATE quotes SET meta = ? WHERE id = ?", (_clean_meta({**_meta(q["meta"]), **meta}), q["id"]))


def quote_doc(conn, quote_id):
    d = L.get_quote(conn, quote_id)
    d["client_external_id"] = conn.execute("SELECT external_id FROM clients WHERE id = ?",
                                           (d["client_id"],)).fetchone()[0]
    d["meta"] = _meta(d.get("meta"))
    return d


def list_quotes(conn, client_external_id=None):
    q, args = "SELECT q.id FROM quotes q JOIN clients c ON c.id = q.client_id WHERE 1=1", []
    if client_external_id:
        q += " AND c.external_id = ?"
        args.append(str(client_external_id))
    return [quote_doc(conn, r["id"]) for r in conn.execute(q + " ORDER BY q.id DESC", args).fetchall()]
