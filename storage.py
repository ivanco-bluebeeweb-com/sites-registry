"""Store access helpers for the sites collection. Kept thin and boring:
no business logic here, just find/save primitives (same shape as WP Site
Connector's own storage.py, so behavior is easy to reason about across
both apps).
"""

SITES_COLLECTION = "sites"


async def _find_by_domain(ctx, domain: str):
    page = await ctx.store.query(SITES_COLLECTION, limit=100)
    for doc in page.data:
        if doc.data.get("domain", "").lower() == domain.lower():
            return doc
    return None


async def find_by_id(ctx, site_id: str):
    return await ctx.store.get(SITES_COLLECTION, site_id)


async def list_records(ctx, *, platform: str | None = None, status: str | None = None, limit: int = 100):
    page = await ctx.store.query(SITES_COLLECTION, order_by="-created_at", limit=limit)
    rows = [doc.data | {"id": doc.id} for doc in page.data]
    if platform:
        rows = [r for r in rows if r.get("platform") == platform]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows
