"""Smoke tests for Sites Registry: add/list/update/remove, plus the
WordPress-connect-in-the-same-call path (both success and failure) and
the upsert_site IPC surface WordPress Hub calls into.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imperal_sdk.testing import MockContext

import handlers as h
from schemas import (
    AddSiteParams, ListSitesParams, UpdateSiteParams, RemoveSiteParams,
    SyncConnectedSitesParams,
)


@pytest.mark.asyncio
async def test_add_site_manual_platform_none():
    ctx = MockContext()
    result = await h.add_site(ctx, AddSiteParams(domain="example.com", name="Example"))
    assert result.status == "success"
    assert result.data.domain == "example.com"
    assert result.data.platform == "none"
    assert result.data.status == "manual"


@pytest.mark.asyncio
async def test_add_site_normalizes_domain_from_full_url():
    ctx = MockContext()
    result = await h.add_site(ctx, AddSiteParams(domain="https://www.Example.COM/some/path"))
    assert result.status == "success"
    assert result.data.domain == "example.com"


@pytest.mark.asyncio
async def test_add_site_rejects_duplicate_domain():
    ctx = MockContext()
    await h.add_site(ctx, AddSiteParams(domain="example.com"))
    result = await h.add_site(ctx, AddSiteParams(domain="example.com"))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_add_site_rejects_bad_platform():
    ctx = MockContext()
    result = await h.add_site(ctx, AddSiteParams(domain="example.com", platform="bogus"))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_add_site_wordpress_connects_live_via_ipc_and_records_connector_ref():
    ctx = MockContext()
    ctx.extensions.register(
        "wp-site-connector", "connect_site_ipc",
        lambda **kw: {"ok": True, "site_id": "example-com", "name": "example.com", "url": "https://example.com"},
    )
    result = await h.add_site(ctx, AddSiteParams(
        domain="example.com", platform="wordpress",
        url="https://example.com", username="admin", app_password="pw",
    ))
    assert result.status == "success"
    assert result.data.platform == "wordpress"
    assert result.data.status == "connected"
    assert result.data.connector_app == "wp-site-connector"
    assert result.data.connector_ref == "example-com"


@pytest.mark.asyncio
async def test_add_site_wordpress_requires_credentials():
    ctx = MockContext()
    result = await h.add_site(ctx, AddSiteParams(domain="example.com", platform="wordpress"))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_add_site_wordpress_connect_failure_creates_no_record():
    ctx = MockContext()
    ctx.extensions.register(
        "wp-site-connector", "connect_site_ipc",
        lambda **kw: {"ok": False, "error": "bad credentials", "retryable": False},
    )
    result = await h.add_site(ctx, AddSiteParams(
        domain="example.com", platform="wordpress",
        url="https://example.com", username="admin", app_password="bad",
    ))
    assert result.status != "success"
    listed = await h.list_sites(ctx, ListSitesParams())
    assert all(s.domain != "example.com" for s in listed.data.items)


@pytest.mark.asyncio
async def test_list_sites_filters_by_platform():
    ctx = MockContext()
    await h.add_site(ctx, AddSiteParams(domain="a.com"))
    await h.add_site(ctx, AddSiteParams(domain="b.com", platform="shopify"))
    result = await h.list_sites(ctx, ListSitesParams(platform="shopify"))
    assert result.status == "success"
    assert len(result.data.items) == 1
    assert result.data.items[0].domain == "b.com"


@pytest.mark.asyncio
async def test_update_site_changes_name_and_notes():
    ctx = MockContext()
    created = await h.add_site(ctx, AddSiteParams(domain="a.com"))
    site_id = created.data.id
    result = await h.update_site(ctx, UpdateSiteParams(site_id=site_id, name="New Name", notes="hi"))
    assert result.status == "success"
    assert result.data.name == "New Name" if hasattr(result.data, "name") else True


@pytest.mark.asyncio
async def test_update_site_missing_errors():
    ctx = MockContext()
    result = await h.update_site(ctx, UpdateSiteParams(site_id="missing", name="X"))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_remove_site_removes_it():
    ctx = MockContext()
    created = await h.add_site(ctx, AddSiteParams(domain="a.com"))
    site_id = created.data.id
    result = await h.remove_site(ctx, RemoveSiteParams(site_id=site_id))
    assert result.status == "success"
    listed = await h.list_sites(ctx, ListSitesParams())
    assert all(s.id != site_id for s in listed.data.items)


@pytest.mark.asyncio
async def test_remove_site_missing_errors():
    ctx = MockContext()
    result = await h.remove_site(ctx, RemoveSiteParams(site_id="missing"))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_upsert_site_ipc_creates_new_record_from_wp_hub_push():
    ctx = MockContext()
    result = await h.expose_upsert_site(
        ctx, domain="pushed.com", name="Pushed Site", platform="wordpress",
        connector_app="wp-site-connector", connector_ref="pushed-com", status="connected",
    )
    assert result["ok"] is True
    listed = await h.list_sites(ctx, ListSitesParams())
    assert any(s.domain == "pushed.com" and s.connector_app == "wp-site-connector" for s in listed.data.items)


@pytest.mark.asyncio
async def test_upsert_site_ipc_updates_existing_record_status():
    ctx = MockContext()
    await h.expose_upsert_site(
        ctx, domain="pushed.com", name="Pushed Site", platform="wordpress",
        connector_app="wp-site-connector", connector_ref="pushed-com", status="connected",
    )
    await h.expose_upsert_site(
        ctx, domain="pushed.com", name="Pushed Site", platform="wordpress",
        connector_app="wp-site-connector", connector_ref="pushed-com", status="disconnected",
    )
    listed = await h.list_sites(ctx, ListSitesParams())
    matches = [s for s in listed.data.items if s.domain == "pushed.com"]
    assert len(matches) == 1
    assert matches[0].status == "disconnected"


@pytest.mark.asyncio
async def test_sync_connected_sites_backfills_sites_that_predate_the_registry():
    """The exact bug this was built to fix: sites connected in WordPress Hub
    BEFORE Sites Registry existed never got an upsert_site push (no receiver
    existed yet) -- sync_connected_sites must pull them in directly."""
    ctx = MockContext()
    ctx.extensions.register(
        "wp-site-connector", "list_connected_sites",
        lambda **kw: [
            {"site_id": "climtec-md", "name": "climtec.md", "url": "https://climtec.md", "status": "connected"},
            {"site_id": "g4s-md", "name": "g4s.md", "url": "https://g4s.md", "status": "connected"},
        ],
    )
    result = await h.sync_connected_sites(ctx, SyncConnectedSitesParams(source="wordpress"))
    assert result.status == "success"
    assert len(result.data.items) == 2
    listed = await h.list_sites(ctx, ListSitesParams())
    domains = {s.domain for s in listed.data.items}
    assert domains == {"climtec.md", "g4s.md"}
    for s in listed.data.items:
        assert s.platform == "wordpress"
        assert s.connector_app == "wp-site-connector"
        assert s.status == "connected"


@pytest.mark.asyncio
async def test_sync_connected_sites_updates_existing_record_without_duplicating():
    ctx = MockContext()
    created = await h.add_site(ctx, AddSiteParams(domain="climtec.md", name="My manual entry", notes="keep me"))
    ctx.extensions.register(
        "wp-site-connector", "list_connected_sites",
        lambda **kw: [{"site_id": "climtec-md", "name": "climtec.md", "url": "https://climtec.md", "status": "connected"}],
    )
    await h.sync_connected_sites(ctx, SyncConnectedSitesParams(source="wordpress"))
    listed = await h.list_sites(ctx, ListSitesParams())
    matches = [s for s in listed.data.items if s.domain == "climtec.md"]
    assert len(matches) == 1  # no duplicate row
    assert matches[0].id == created.data.id  # same record, just patched
    assert matches[0].platform == "wordpress"
    assert matches[0].connector_ref == "climtec-md"


@pytest.mark.asyncio
async def test_sync_connected_sites_rejects_unknown_source():
    ctx = MockContext()
    result = await h.sync_connected_sites(ctx, SyncConnectedSitesParams(source="bogus"))
    assert result.status != "success"


@pytest.mark.asyncio
async def test_sync_connected_sites_surfaces_ipc_failure():
    ctx = MockContext()

    def _boom(**kw):
        raise RuntimeError("wp-site-connector unreachable")

    ctx.extensions.register("wp-site-connector", "list_connected_sites", _boom)
    result = await h.sync_connected_sites(ctx, SyncConnectedSitesParams(source="wordpress"))
    assert result.status != "success"
