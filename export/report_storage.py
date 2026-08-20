"""Persist completed reports to GCS so a dropped browser connection can't
lose an already-finished result -- see config.REPORTS_BUCKET docstring."""
import re
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

import config

_client = None


def _get_client():
    global _client
    if _client is None:
        from google.cloud import storage
        _client = storage.Client()
    return _client


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text or "").strip().lower()
    return re.sub(r"[\s_]+", "-", text)[:60] or "report"


def upload_report(docx_bytes: bytes, company_name: str | None, period: str | None) -> str | None:
    """Upload a completed report to the reports bucket. Returns the object
    name, or None if persistence is disabled or the upload fails -- this
    must never block the user from getting their live download, so any
    error here is logged and swallowed, not raised."""
    if not config.REPORTS_BUCKET:
        return None
    try:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = _slugify(company_name or period or "report")
        object_name = f"reports/{timestamp}-{slug}.docx"
        bucket = _get_client().bucket(config.REPORTS_BUCKET)
        blob = bucket.blob(object_name)
        blob.metadata = {
            "company_name": company_name or "",
            "period": period or "",
        }
        blob.upload_from_string(
            docx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        return object_name
    except Exception as e:
        logger.warning(f"Could not persist report to GCS (live download is unaffected): {e}")
        return None


@dataclass
class ReportListing:
    object_name: str
    display_name: str
    uploaded_at: datetime


def list_recent_reports(limit: int = 20) -> list[ReportListing]:
    """Most-recent-first list of persisted reports. Returns an empty list
    (not an error) if persistence is disabled or listing fails, since this
    feeds a UI convenience list, not a critical path."""
    if not config.REPORTS_BUCKET:
        return []
    try:
        bucket = _get_client().bucket(config.REPORTS_BUCKET)
        blobs = sorted(
            bucket.list_blobs(prefix="reports/"),
            key=lambda b: b.time_created,
            reverse=True,
        )[:limit]
        listings = []
        for b in blobs:
            meta = b.metadata or {}
            company = meta.get("company_name") or ""
            period = meta.get("period") or ""
            label = " — ".join(p for p in (company, period) if p) or b.name.rsplit("/", 1)[-1]
            when = b.time_created.strftime("%d %b %Y %H:%M") if b.time_created else ""
            listings.append(ReportListing(
                object_name=b.name,
                display_name=f"{label} ({when})" if when else label,
                uploaded_at=b.time_created,
            ))
        return listings
    except Exception as e:
        logger.warning(f"Could not list persisted reports: {e}")
        return []


def download_report(object_name: str) -> bytes | None:
    """Fetch a persisted report's bytes by object name. Returns None on
    failure rather than raising, so a stale/deleted entry in the UI list
    fails gracefully instead of crashing the app."""
    if not config.REPORTS_BUCKET:
        return None
    try:
        bucket = _get_client().bucket(config.REPORTS_BUCKET)
        return bucket.blob(object_name).download_as_bytes()
    except Exception as e:
        logger.warning(f"Could not download persisted report {object_name!r}: {e}")
        return None
