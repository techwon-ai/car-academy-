"""Sample agency books (June-November 2026) for trying Techwon Books. All names are fictional."""

import ledger as L


def build(path):
    c = L.connect(path)
    L.init_db(c)
    L.update_settings(c, {"company_name": "Techwon (DEMO DATA)"})

    L.post_journal_entry(c, "2026-06-01", "Owner investment", [
        {"account_code": "1000", "debit_cents": 5000000}, {"account_code": "3000", "credit_cents": 5000000}])

    northwind = L.create_client(c, "Northwind Dental Group", "ap@northwind.example", retainer_cents=450000)
    bluepeak = L.create_client(c, "BluePeak Roofing", "billing@bluepeak.example", retainer_cents=300000,
                               payment_terms_days=30)
    harbor = L.create_client(c, "Harbor Coffee Co.", "owner@harbor.example")
    vale = L.create_client(c, "Vale Auto Academy", "finance@vale.example", retainer_cents=600000)

    rebrand = L.create_project(c, harbor, "Brand refresh", budget_cents=1800000)
    launch = L.create_project(c, vale, "Spring enrollment campaign", budget_cents=2500000)

    meta = L.create_vendor(c, "Meta Ads")
    google = L.create_vendor(c, "Google Ads")
    designer = L.create_vendor(c, "Lena Park (freelance designer)")
    video = L.create_vendor(c, "Reelhouse Video")
    landlord = L.create_vendor(c, "Midtown Workspace")
    saas = L.create_vendor(c, "SaaS tools (Figma, HubSpot, Adobe)")
    payroll = L.create_vendor(c, "Payroll provider")

    months = ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]
    for i, m in enumerate(months):
        res = L.generate_retainer_invoices(c, m)
        # Clients pay last month's retainers; BluePeak pays slowly, and is behind on Oct/Nov.
        for inv in res["created"]:
            d = L.get_invoice(c, inv["invoice_id"])
            if m == "2026-11":
                continue
            if d["client_id"] == bluepeak and m in ("2026-09", "2026-10"):
                continue
            day = "28" if d["client_id"] == bluepeak else "12"
            L.record_payment(c, d["id"], f"{m}-{day}", d["total_cents"], reference="ACH")
        L.create_bill(c, landlord, f"{m}-01", 350000, "6100", "Office rent", paid_from="1000")
        L.create_bill(c, saas, f"{m}-03", 89000, "6200", "Software subscriptions", paid_from="2100")
        L.create_bill(c, payroll, f"{m}-28", 1150000 + i * 20000, "6000", "Staff payroll", paid_from="1000")
        L.create_bill(c, payroll, f"{m}-28", 160000, "6050", "Payroll taxes", paid_from="1000")
        L.create_bill(c, google, f"{m}-25", 6500, "6700", "Card processing fees", paid_from="1000")
        # Pass-through media for Northwind and Vale, rebilled with a 10% management markup
        for client, vendor, amount in ((northwind, meta, 800000), (vale, google, 1200000)):
            L.create_bill(c, vendor, f"{m}-05", amount + i * 25000, "5000", f"{m} ad spend",
                          client_id=client, billable=True, paid_from="2100")
            inv = L.create_rebill_invoice(c, client, f"{m}-06", markup_bp=1000)
            L.issue_invoice(c, inv)
            if m != "2026-11":
                L.record_payment(c, inv, f"{m}-20", L.get_invoice(c, inv)["total_cents"], reference="Wire")

    # Pay down the credit card each month-end
    card = L.account_by_code(c, "2100")
    for m in months[:-1]:
        bal = {r["code"]: r["balance_cents"] for r in L.trial_balance(c, f"{m}-28")["rows"]}.get("2100", 0)
        if bal > 0:
            L.post_journal_entry(c, f"{m}-28", "Credit card payment", [
                {"account_id": card["id"], "debit_cents": bal}, {"account_code": "1000", "credit_cents": bal}])

    # Harbor Coffee brand refresh: freelancer + production, billed in two milestones
    L.create_bill(c, designer, "2026-07-10", 420000, "5100", "Logo & identity design", project_id=rebrand,
                  due_date="2026-08-09")
    L.create_bill(c, video, "2026-08-15", 380000, "5200", "Brand film shoot", project_id=rebrand,
                  due_date="2026-09-14", billable=True)
    m1 = L.create_invoice(c, harbor, "2026-07-01", [
        {"description": "Brand refresh - milestone 1 (discovery & strategy)", "unit_price_cents": 900000,
         "revenue_account_code": "4100"}], project_id=rebrand, tax_rate_bp=0)
    L.issue_invoice(c, m1)
    L.record_payment(c, m1, "2026-07-14", 900000, reference="Check 1042")
    m2 = L.create_invoice(c, harbor, "2026-09-01", [
        {"description": "Brand refresh - milestone 2 (identity system)", "unit_price_cents": 900000,
         "revenue_account_code": "4100"}], project_id=rebrand)
    L.issue_invoice(c, m2)
    L.record_payment(c, m2, "2026-10-02", 450000, reference="Partial")
    for b in L.list_bills(c):
        if b["vendor_id"] == designer and b["balance_cents"]:
            L.pay_bill(c, b["id"], "2026-08-05", b["balance_cents"], "1000")

    # Vale spring campaign: hourly strategy work
    v1 = L.create_invoice(c, vale, "2026-10-15", [
        {"description": "Campaign strategy workshop", "quantity": "12", "unit_price_cents": 17500,
         "revenue_account_code": "4100"},
        {"description": "Landing page build", "quantity": "1", "unit_price_cents": 650000,
         "revenue_account_code": "4100"}], project_id=launch)
    L.issue_invoice(c, v1)
    L.create_invoice(c, vale, "2026-11-18", [
        {"description": "Creative production - phase 2", "unit_price_cents": 800000}], project_id=launch)  # draft

    L.post_journal_entry(c, "2026-11-01", "Laptop purchase", [
        {"account_code": "1500", "debit_cents": 280000}, {"account_code": "1000", "credit_cents": 280000}])
    L.update_settings(c, {"lock_date": "2026-08-31"})
    c.close()
