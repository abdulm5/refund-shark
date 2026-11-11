from typing import Protocol

from shark.models import Invoice


class InvoiceSource(Protocol):
    def get(self, invoice_id: str) -> Invoice: ...


class MockInvoiceSource:
    def __init__(self, invoices: list[Invoice]):
        self.invoices = {i.id: i for i in invoices}

    def get(self, invoice_id: str) -> Invoice:
        return self.invoices[invoice_id].model_copy(deep=True)
