"""Handlers for Sites Registry: add/list/update/remove a site, plus the
upsert_site IPC surface that WordPress Hub (and future connectors) call to
push their own connect/disconnect state in here automatically.
"""
from app import chat, ext
from imperal_sdk import ActionResult

from schemas import (
    Site, SiteList, AddSiteParams, ListSitesParams, UpdateSiteParams,
    RemoveSiteParams, PLATFORM_CHOICES, SyncConnectedSitesParams,
    BackfillProjectFanoutParams,
)
from converters import now_iso, normalize_domain, site_id_from_domain, to_site
import storage

# Registry of app_id -> IPC method that actually connects a site live on
# that platform, keyed by the platform name chosen in add_site. Adding a
# new platform connector later (e.g. Shopify) means adding one line here --
# no other code in this file changes.
_PLATFORM_CONNECT_IPC = {
    "wordpress": ("wordpress-hub", "connect_site_ipc"),
}

# Every app that owns a "projects" concept keyed by site/domain, and the
# idempotent @ext.expose surface each one offers to register a site there
# automatically. Adding a future app here is one line -- no other code in
# this file changes. Per explicit platform rule: any site that lands in
# Sites Registry (manually, via add_site, or via a connector's upsert_site/
# sync_connected_sites) must show up as an existing project everywhere else
# without the user re-adding it four more times.
_PROJECT_FANOUT_TARGETS: list[tuple[str, str]] = [
    ("content-strategy-app", "register_project"),
    ("brand-strategy-hub", "register_project"),
    ("media-studio", "register_project"),
    ("seo-audit-engine", "register_known_site"),
]


async def _fanout_new_site(ctx, *, domain: str, name: str) -> None:
    """Best-effort: tell every downstream app with a 'projects' concept about
    a newly registered site, so it appears there as an existing project.

    Deliberately swallows every failure per target -- a downstream app being
    uninstalled, unreachable, or erroring must never block or fail the one
    write the caller actually asked for (registering the site here). Each
    target's own surface is idempotent (create-if-missing), so calling this
    more than once for the same domain is always safe.
    """
    for app_id, method in _PROJECT_FANOUT_TARGETS:
        try:
            await ctx.extensions.call(
                app_id, method, site_id=domain, domain=domain, name=name or domain,
            )
        except Exception as e:
            await ctx.log(f"_fanout_new_site: {app_id}.{method} skipped: {e}", level="info")


@chat.function(
    "backfill_project_fanout",
    description=(
        "One-time catch-up: re-run the new-site fan-out for EVERY site already "
        "registered here, regardless of when or how it was added. Use this once "
        "after the fan-out rule shipped, so sites that predate it also appear as "
        "existing projects in Content Strategy, Brand Strategy, Media Hub and "
        "SEO Audit Engine. Safe to run more than once -- each downstream app's "
        "own register surface is idempotent, so an already-known site is left "
        "untouched."
    ),
    action_type="write",
    data_model=SiteList,
    effects=["site.fanout_backfill"],
    event="sites-registry.backfill_project_fanout",
)
async def backfill_project_fanout(ctx, params: BackfillProjectFanoutParams) -> ActionResult:
    """Re-run _fanout_new_site for every existing registry record."""
    rows = await storage.list_records(ctx)
    for r in rows:
        await _fanout_new_site(ctx, domain=r.get("domain", ""), name=r.get("name") or r.get("domain", ""))
    items = [to_site(r) for r in rows]
    return ActionResult.success(
        SiteList(items=items, total=len(items)),
        summary=f"Re-announced {len(items)} site(s) to Content Strategy, Brand Strategy, Media Hub and SEO Audit Engine.")


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
            await ctx.log(f"add_site: wordpress-hub IPC call failed: {e}", level="error")
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
    await _fanout_new_site(ctx, domain=domain, name=name)
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
            await _fanout_new_site(ctx, domain=domain, name=record["name"])

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


@ext.expose("ping", action_type="read")
async def expose_ping(ctx, **kwargs) -> dict:
    """Read-only inter-extension IPC surface with no side effects at all --
    doesn't touch ctx.store, doesn't need a populated ctx.user. Exists purely
    so another extension (WordPress Hub's sidebar today) can cheaply detect
    "is Sites Registry installed and reachable for this user right now" and
    conditionally show/hide UI, without the risk a write call (upsert_site)
    would carry if used just as an installed-check, and without depending on
    the panel-render ctx.user population gap already seen with other IPC
    reads made during a panel render (see Content Strategy Hub's
    _cache_connected_sites for that same platform-side gap).

    Returns a plain dict (never surfaced to the LLM/user directly):
    {"ok": True}. A caller that gets an exception instead (NotFoundError —
    app not installed/enabled, AuthError, etc.) should treat that as "not
    available" -- ctx.extensions.call has no separate is_installed() API.
    """
    return {"ok": True}


@ext.expose("list_connected_sites", action_type="read")
async def expose_list_connected_sites(ctx, **kwargs):
    """Inter-extension IPC surface (ctx.extensions.call) for apps that want a
    platform-agnostic site list -- today Page Speed Insights, which prefers
    reading straight from the registry over asking WordPress Hub directly
    (a site here may not even be WordPress). Same shape as WordPress Hub's
    own list_connected_sites so callers can treat either provider identically.

    Returns plain dicts (never surfaced to the LLM/user directly):
    [{"site_id", "name", "url", "status"}, ...]
    """
    rows = await storage.list_records(ctx)
    return [
        {"site_id": r["id"], "name": r.get("name") or r.get("domain", r["id"]),
         "url": r.get("domain", ""), "status": r.get("status", "manual")}
        for r in rows
    ]


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
    await _fanout_new_site(ctx, domain=d, name=record["name"])
    return {"ok": True, "site_id": doc.id}
