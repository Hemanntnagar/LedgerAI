"""
Browser invoice upload (multipart CSV / JSON / PDF).

Authenticated store owners upload files; invoices are forced onto their store.
"""
from __future__ import annotations

import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from tenant_auth import require_store_auth

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])

_EXT_TO_SOURCE = {
    ".csv": "csv",
    ".json": "json",
    ".pdf": "pdf",
    ".png": "ocr_image",
    ".jpg": "ocr_image",
    ".jpeg": "ocr_image",
    ".tif": "ocr_image",
    ".tiff": "ocr_image",
    ".webp": "ocr_image",
}


def _source_type_for(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    source = _EXT_TO_SOURCE.get(ext)
    if not source:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or '(none)'}'. Use CSV, JSON, PDF, or an image.",
        )
    return source


@router.post("/invoices")
async def upload_invoices(
    file: UploadFile = File(...),
    auth: dict = Depends(require_store_auth),
    mapping_profile: str = "default",
):
    """
    Ingest a single invoice export file for the signed-in store.
    Store name in the file is overridden so tenants cannot write to another store.
    """
    filename = file.filename or "upload.bin"
    source_type = _source_type_for(filename)
    store_name = auth["store_name"]
    store_id = auth["store_id"]

    suffix = os.path.splitext(filename)[1] or ".bin"
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name

        from pipeline.ingest import ingest_file

        result = ingest_file(
            tmp_path,
            source_type,
            mapping_profile=mapping_profile,
            force_store_name=store_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}") from exc
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return {
        "store_id": store_id,
        "store_name": store_name,
        "filename": filename,
        "source_type": source_type,
        "invoices_found": result.invoices_found,
        "invoices_loaded": result.invoices_loaded,
        "invoices_skipped_duplicate": result.invoices_skipped_duplicate,
        "invoices_failed_validation": result.invoices_failed_validation,
        "errors": list(result.errors or []),
    }
