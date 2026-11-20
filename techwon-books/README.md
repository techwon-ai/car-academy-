# Techwon Books

Double-entry accounting for Techwon, a marketing agency. It covers the core QuickBooks bookkeeping workflow and adds features agencies need that QuickBooks handles poorly.

Pure Python 3.9+ standard library + SQLite. No `pip install`, no cloud, no subscription.

## Run it

```bash
cd techwon-books
python3 server.py demo                      # optional: sample agency data in demo.db
python3 server.py serve --db demo.db        # open http://localhost:8080
```

For real books:

```bash
python3 server.py init                      # creates books.db with the agency chart of accounts
python3 server.py serve                     # http://localhost:8080
python3 server.py backup backups/books-$(date +%F).db   # safe while the server is running
```

Tests: `python3 -m unittest test_ledger -v`

## What it does

| QuickBooks-style core | Agency-specific |
|---|---|
| Chart of accounts (39 agency accounts, editable) | **Pass-through media**: ad spend you rebill isn't counted as your revenue. The P&L shows gross billings → **net revenue (AGI)** → gross profit → net income |
| Customers, vendors, projects | **Retainers**: one click invoices every retainer client for the month (safe to re-run) |
| Invoices: draft → issue → partial/paid, print/PDF, sales tax, discounts | **Rebill expenses**: billable costs → draft invoice, optional markup |
| Bills (pay later) and expenses (paid now, bank or card) | **Client & project profitability**, budget used % |
| Journal entries, reversals | Hourly lines (qty = hours) |
| Bank & credit-card reconciliation | |
| P&L, Balance Sheet, Trial Balance, A/R & A/P aging, General Ledger, CSV export | |
| Period lock date, fiscal year setting | |

### Accounting integrity guarantees
- Every transaction is a balanced double-entry; unbalanced entries are rejected.
- Posted entries are never edited or deleted. Mistakes are fixed by voiding, which posts a reversing entry, so there's a full audit trail.
- Nothing can be posted on or before the lock date.
- Reconciled bank transactions can't be voided.
- Money is integer cents throughout: no floating-point rounding errors.
- Tests check that the books balance after every workflow.

## What it does NOT do (read this before you rely on it)

1. **No bank feeds.** You enter transactions by hand. This is the main reason to use QuickBooks/Xero instead.
2. **No payroll or tax filing.** Record payroll from your provider's report as an expense.
3. **No users, logins or permissions.** It only accepts connections from the same computer (127.0.0.1). Don't expose it to a network.
4. **No emailing invoices.** Print to PDF and send them yourself.
5. **Single currency.**
6. **Client profitability ignores staff time.** Salaries aren't allocated to clients, so margins look better than they really are until you add time tracking.
7. **Backups are your job.** Everything is one file, `books.db`. Back it up daily, off this machine.

Have an accountant review the chart of accounts and your first month-end before you file anything from these books.
