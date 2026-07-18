"""
Common interface every ingestion adapter must implement, plus the
standardized intermediate representation they all produce.

Whatever the source (CSV, PDF, scanned image), by the time data leaves
an adapter it looks the same to the rest of the pipeline.
"""
from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel, Field


class RawLineItem(BaseModel):
    product_name: str
    category: Optional[str] = None
    quantity: Optional[str] = None      # kept as raw string; parsed/cleaned downstream
    unit_price: Optional[str] = None
    discount_pct: Optional[str] = None
    tax_pct: Optional[str] = None
    line_total: Optional[str] = None


class RawInvoiceData(BaseModel):
    store_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    invoice_time: Optional[str] = None
    customer_name: Optional[str] = None
    payment_method: Optional[str] = None
    line_items: List[RawLineItem] = Field(default_factory=list)
    raw_totals: Optional[dict] = None
    source_type: str                    # 'csv' | 'pdf' | 'ocr_image'
    source_file_path: str
    confidence: Optional[float] = None  # populated by OCR adapter; None otherwise


class InvoiceAdapter(ABC):
    """Every adapter takes a file path and returns a list of RawInvoiceData.

    A list, not a single object, because one file (e.g. a CSV export) can
    contain many invoices.
    """

    @abstractmethod
    def parse(self, file_path: str) -> List[RawInvoiceData]:
        ...
