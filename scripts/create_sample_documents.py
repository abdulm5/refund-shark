"""Rebuild the clearly labeled PDF fixtures; ReportLab is a development dependency."""
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

from shark.pipeline import load_scenarios

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/fixtures/documents"


def make_pdf(path, title, lines):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("Label", fontName="Helvetica", fontSize=11, leading=18, spaceAfter=7, textColor=colors.HexColor('#33372f')))
    doc = SimpleDocTemplate(str(path), pagesize=(612, 792), leftMargin=52, rightMargin=52, topMargin=55, bottomMargin=55,
                            title=title, author="Refund Shark demo", invariant=1)
    story = [Paragraph("REFUND SHARK / SIMULATED SAMPLE", styles["Heading3"]), Spacer(1, 15), Paragraph(title, styles["Title"]), Spacer(1, 24)]
    for line in lines:
        story.append(Paragraph(escape(line), styles["Label"]))
    def footer(canvas, doc):
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#75796f'))
        canvas.drawString(52, 30, 'Sample document for local testing. No real charge or credit.')
        canvas.drawRightString(560, 30, f'Page {doc.page}')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    s = next(s for s in load_scenarios() if s.id == "jira-sept-claim")
    base = [f"Vendor: {s.vendor}", f"Customer: {s.account.display_name}", f"Account: {s.account.account_reference}",
            f"Product: {s.product}", f"Plan: {s.plan}", "Currency: USD", "Billing cycle: monthly", "Product fee: 9000.00"]
    make_pdf(OUT / 'sample-invoice.pdf', 'September subscription invoice', base + ["Invoice number: DEMO-SEP-001", "Billing period: 2026-09", "Issue date: 2026-09-01", "Service credit: 0.00"])
    make_pdf(OUT / 'sample-credit.pdf', 'October invoice with service credit', base + ["Invoice number: DEMO-OCT-002", "Billing period: 2026-10", "Issue date: 2026-10-08", "Service credit: 900.00", "Credit period: 2026-09", "Claim reference: RS-JIRA-SEPT-CLAIM"])
    lines = ["This layout fixture contains selected public Atlassian SLA quotes used by the demo. It is not a complete contract.",
             "Source: https://www.atlassian.com/legal/sla"]
    lines += [term.quote for term in (getattr(s.terms, name) for name in type(s.terms).model_fields) if term.quote]
    make_pdf(OUT / 'sample-contract.pdf', 'Agreement excerpts for PDF testing', lines)


if __name__ == '__main__':
    main()
