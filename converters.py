"""Small pure helpers: domain normalization, id derivation, timestamps,
and the Site entity converter. Kept separate from main.py so handlers
stay focused on flow, not string-munging (same split as other apps in
this suite).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from schemas import Site


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_domain(raw: str) -> str:
    """Strip scheme/path/www from a URL or bare domain, lowercase it."""
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/", 1)[0]
    if d.startswith("www."):
        d = d[4:]
    return d


def site_id_from_domain(domain: str) -> str:
    """Same slug shape WP Site Connector already uses for its own site_id,
    so a future direct id-based join never needs translation."""
    return re.sub(r"[^a-z0-9]+", "-", domain).strip("-")


def to_site(record: dict) -> Site:
    return Site(
        id=record.get("id", ""),
        title=record.get("name") or record.get("domain", ""),
        kind="site",
        domain=record.get("domain", ""),
        platform=record.get("platform", "none"),
        connector_app=record.get("connector_app", ""),
        connector_ref=record.get("connector_ref", ""),
        status=record.get("status", "manual"),
        notes=record.get("notes", ""),
        created_at=record.get("created_at", ""),
        updated_at=record.get("updated_at", ""),
    )
