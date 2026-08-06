"""Handlers for Sites Registry: add/list/update/remove a site, plus the
upsert_site IPC surface that WordPress Hub (and future connectors) call to
push their own connect/disconnect state in here automatically.
"""
from app import chat, ext
from imperal_sdk import ActionResult

from schemas import (
    Site, SiteList, AddSiteParams, ListSitesParams, UpdateSiteParams,
    RemoveSiteParams, PLATFORM_CHOICES,
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
