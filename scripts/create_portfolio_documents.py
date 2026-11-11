"""Deterministic, explicitly simulated invoices for the isolated portfolio replay."""
from create_sample_documents import make_pdf, OUT


def main():
    folder = OUT / "portfolio"
    folder.mkdir(exist_ok=True)
    for slug, customer, fee in [("northstar", "Northstar Analytics (demo)", "9000.00"),
                                ("harbor", "Harbor Logistics (demo)", "4800.00"),
                                ("cobalt", "Cobalt Studio (demo)", "2400.00")]:
        base = ["Vendor: Atlassian", f"Customer: {customer}", f"Account: DEMO-{slug.upper()}-JIRA", "Product: Jira", "Plan: Premium",
                "Currency: USD", "Billing cycle: monthly", f"Product fee: {fee}"]
        for month in ("09", "10"):
            make_pdf(folder / f"{slug}-{month}.pdf", f"{'September' if month == '09' else 'October'} demo subscription invoice",
                base + [f"Invoice number: PORTFOLIO-{slug.upper()}-{month}", f"Billing period: 2026-{month}",
                        f"Issue date: 2026-{month}-01", "Service credit: 0.00"])


if __name__ == "__main__":
    main()
