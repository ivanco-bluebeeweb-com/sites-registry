"""Handlers for Sites Registry: add/list/update/remove a site, plus the
upsert_site IPC surface that WordPress Hub (and future connectors) call to
push their own connect/disconnect state in here automatically.
"""
from app import chat, ext
from imperal_sdk import ActionResult

from schemas import (
    Site, SiteList, AddSiteParams, ListSitesParams, UpdateSiteParams,
    RemoveSiteParams, PLATFORM_CHOICES, SyncConnectedSitesParams,
)
from converters import now_iso, normalize_domain, site_id_from_domain, to_site
import storage

# Registry of app_id -> IPC method that actually connects a site live on
# that platform, keyed by the platform name chosen in add_site. Adding a
# new platform connector later (e.g. Shopify) means adding one line here --
# no other code in this file changes.
_PLATFORM_CONNECT_IPC = {
    "wordpress": ("wp-site-connector", "connect_site_ipc"),
}


@chat.function(
    "add_site",
    description=(
        "Register a site in the platform-agnostic Sites Registry. Works for any "
        "platform, or none at all. Choosing platform='wordpress' AND supplying "
        "url/username/app_password connects it live in WordPress Hub in this same "
        "call -- not a separate step."
    ),
    action_type="write",
    data_model=Site,
    effects=["site.create"],
    event="sites-registry.add_site",
)
async def add_site(ctx, params: AddSiteParams) -> ActionResult:
    """Register a site; connects it live in WordPress Hub first if platform='wordpress'."""
    domain = normalize_domain(params.domain)
    if not domain:
        return ActionResult.error("A domain is required.", retryable=False)
    if params.platform not in PLATFORM_CHOICES:
        return ActionResult.error(
            f"platform must be one of: {', '.join(PLATFORM_CHOICES)}", retryable=False)

    existing = await storage._find_by_domain(ctx, domain)
    if existing is not None:
        return ActionResult.error(
            f"'{domain}' is already registered (site_id={existing.id}). Use update_site instead.",
            retryable=False)

    name = params.name.strip() or domain
    now = now_iso()
    connector_app, connector_ref, status = "", "", "manual"

    if params.platform == "wordpress":
        if not (params.url and params.username and params.app_password):
            return ActionResult.error(
                "platform='wordpress' requires url, username and app_password "
                "to connect it live in WordPress Hub.", retryable=False)
        target_app, method = _PLATFORM_CONNECT_IPC["wordpress"]
        try:
            result = await ctx.extensions.call(
                target_app, method,
                url=params.url, username=params.username, app_password=params.app_password,
            )
        except Exception as e:
            await ctx.log(f"add_site: wp-site-connector IPC call failed: {e}", level="error")
            return ActionResult.error(
                "Could not reach WordPress Hub to connect this site -- try again shortly.",
                retryable=True)
        if not result.get("ok"):
            return ActionResult.error(
                result.get("error", "WordPress Hub could not connect this site."),
                retryable=bool(result.get("retryable")))
        connector_app = target_app
        connector_ref = result.get("site_id", "")
        status = "connected"
        name = result.get("name", name)

    record = {
        "domain": domain, "name": name, "platform": params.platform,
        "connector_app": connector_app, "connector_ref": connector_ref,
        "status": status, "notes": params.notes,
        "created_at": now, "updated_at": now,
    }
    doc = await ctx.store.create(storage.SITES_COLLECTION, record)
    site = to_site(record | {"id": doc.id})
    return ActionResult.success(
        site, summary=f"Registered '{domain}'" + (" and connected it in WordPress Hub" if status == "connected" else ""),
        refresh_panels=["sites"])


async def _do_sync_connected_sites(ctx, source: str) -> dict:
    """Shared core: pulls a connector's connected sites via IPC and backfills/
    refreshes matching registry records by domain. Used by both the chat tool
    (LLM/manual call) and the sync_connected_sites_ipc @ext.expose surface
    (what WP Hub's sidebar button actually calls -- ctx.extensions.call only
    ever reaches @ext.expose surfaces, never @chat.function ones)."""
    if source not in _PLATFORM_CONNECT_IPC:
        return {"ok": False, "error": f"Unknown source '{source}'. Known: {', '.join(_PLATFORM_CONNECT_IPC)}"}
    target_app, _connect_method = _PLATFORM_CONNECT_IPC[source]

    try:
        rows = await ctx.extensions.call(target_app, "list_connected_sites")
    except Exception as e:
        await ctx.log(f"sync_connected_sites: IPC call to {target_app} failed: {e}", level="error")
        return {"ok": False, "error": f"Could not reach {target_app} to read its connected sites.", "retryable": True}

    now = now_iso()
    synced: list[Site] = []
    for row in (rows or []):
        raw = row.get("name") or row.get("url") or row.get("site_id", "")
        domain = normalize_domain(raw)
        if not domain:
            continue
        connector_ref = row.get("site_id", "")
        status = row.get("status", "connected")

        existing = await storage._find_by_domain(ctx, domain)
        if existing is not None:
            patch = {
                "platform": source, "connector_app": target_app,
                "connector_ref": connector_ref, "status": status, "updated_at": now,
            }
            await ctx.store.update(storage.SITES_COLLECTION, existing.id, patch)
            merged = existing.data | patch
            synced.append(to_site(merged | {"id": existing.id}))
        else:
            record = {
                "domain": domain, "name": row.get("name") or domain, "platform": source,
                "connector_app": target_app, "connector_ref": connector_ref,
                "status": status, "notes": "", "created_at": now, "updated_at": now,
            }
            doc = await ctx.store.create(storage.SITES_COLLECTION, record)
            synced.append(to_site(record | {"id": doc.id}))

    return {"ok": True, "target_app": target_app, "items": synced}


@chat.function(
    "sync_connected_sites",
    description=(
        "Pull sites that are ALREADY connected in a platform connector (WordPress Hub today) into "
        "the registry. Fixes sites connected before Sites Registry existed, or any time the two "
        "drift out of sync -- safe to run any time, matches by domain, fills in platform/connector "
        "info, never touches notes."
    ),
    action_type="write",
    data_model=SiteList,
    effects=["site.sync"],
    event="sites-registry.sync_connected_sites",
)
async def sync_connected_sites(ctx, params: SyncConnectedSitesParams) -> ActionResult:
    """Backfill/refresh registry entries from an already-connected platform connector."""
    result = await _do_sync_connected_sites(ctx, params.source or "wordpress")
    if not result.get("ok"):
        return ActionResult.error(result.get("error", "Sync failed."), retryable=bool(result.get("retryable")))
    synced = result["items"]
    return ActionResult.success(
        SiteList(items=synced, total=len(synced)),
        summary=f"Synced {len(synced)} site(s) from {result['target_app']}.",
        refresh_panels=["sites"])


@ext.expose("sync_connected_sites_ipc", action_type="write")
async def expose_sync_connected_sites(ctx, *, source: str = "wordpress", **kwargs) -> list[dict]:
    """Inter-extension IPC surface: this is what WP Hub's sidebar sync button
    actually calls (ctx.extensions.call routes to @ext.expose surfaces, never
    to @chat.function ones -- sync_connected_sites above is the LLM/manual
    version of the exact same core logic).

    Returns a plain list of dicts (never surfaced to the LLM/user directly):
    [{"id", "domain", "name", "status"}, ...]
    """
    result = await _do_sync_connected_sites(ctx, source)
    if not result.get("ok"):
        return []
    return [
        {"id": s.id, "domain": s.domain, "name": s.title, "status": s.status}
        for s in result["items"]
    ]


@chat.function(
    "list_sites",
    description="List every registered site, optionally filtered by platform or status.",
    action_type="read",
    data_model=SiteList,
)
async def list_sites(ctx, params: ListSitesParams) -> ActionResult:
    """List every registered site, optionally filtered by platform/status."""
    rows = await storage.list_records(ctx, platform=params.platform, status=params.status, limit=params.limit)
    items = [to_site(r) for r in rows]
    return ActionResult.success(
        SiteList(items=items, total=len(items)), summary=f"{len(items)} site(s) registered.")


@chat.function(
    "update_site",
    description="Update a registered site's name, notes, and/or status.",
    action_type="write",
    data_model=Site,
    effects=["site.update"],
    event="sites-registry.update_site",
)
async def update_site(ctx, params: UpdateSiteParams) -> ActionResult:
    """Update a registered site's name, notes, and/or status."""
    doc = await storage.find_by_id(ctx, params.site_id)
    if doc is None:
        return ActionResult.error("Site not found.", code="SITE_NOT_FOUND", retryable=False)
    patch = {"updated_at": now_iso()}
    if params.name is not None:
        patch["name"] = params.name
    if params.notes is not None:
        patch["notes"] = params.notes
    if params.status is not None:
        patch["status"] = params.status
    await ctx.store.update(storage.SITES_COLLECTION, doc.id, patch)
    updated = await storage.find_by_id(ctx, params.site_id)
    return ActionResult.success(
        to_site(updated.data | {"id": updated.id}), summary="Site updated.", refresh_panels=["sites"])


@chat.function(
    "remove_site",
    description="Remove a site from the registry. Does NOT disconnect it from WordPress Hub or any other connector -- it only removes this catalogue entry.",
    action_type="destructive",
    data_model=Site,
    effects=["site.delete"],
    event="sites-registry.remove_site",
)
async def remove_site(ctx, params: RemoveSiteParams) -> ActionResult:
    """Remove a site from the registry only -- never disconnects it from WordPress Hub or any other connector."""
    doc = await storage.find_by_id(ctx, params.site_id)
    if doc is None:
        return ActionResult.error("Site not found.", code="SITE_NOT_FOUND", retryable=False)
    await ctx.store.delete(storage.SITES_COLLECTION, doc.id)
    return ActionResult.success(
        Site(id=params.site_id, title=doc.data.get("name", params.site_id), status="removed"),
        summary="Site removed from the registry.", refresh_panels=["sites"])


@ext.expose("upsert_site", action_type="write")
async def expose_upsert_site(
    ctx, *, domain: str, name: str = "", platform: str = "other",
    connector_app: str = "", connector_ref: str = "", status: str = "connected", **kwargs,
) -> dict:
    """Inter-extension IPC surface: a connector extension (WordPress Hub
    today, more later) calls this whenever IT connects or disconnects a
    site directly, so the registry stays in sync without the user having
    to add the same site twice. Matches by domain -- creates the record if
    new, otherwise updates platform/connector/status in place.

    Returns a plain dict (never surfaced to the LLM/user directly):
    {"ok": True, "site_id": ...}
    """
    d = normalize_domain(domain)
    if not d:
        return {"ok": False, "error": "domain is required"}
    now = now_iso()
    existing = await storage._find_by_domain(ctx, d)
    if existing is not None:
        patch = {
            "platform": platform, "connector_app": connector_app,
            "connector_ref": connector_ref, "status": status, "updated_at": now,
        }
        if name:
            patch["name"] = name
        await ctx.store.update(storage.SITES_COLLECTION, existing.id, patch)
        return {"ok": True, "site_id": existing.id}

    record = {
        "domain": d, "name": name or d, "platform": platform,
        "connector_app": connector_app, "connector_ref": connector_ref,
        "status": status, "notes": "", "created_at": now, "updated_at": now,
    }
    doc = await ctx.store.create(storage.SITES_COLLECTION, record)
    return {"ok": True, "site_id": doc.id}
