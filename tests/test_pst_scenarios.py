"""Plausible Scenario Tests (PST) -- Sites Registry, Part D.

Method: Docs/session-notes/SCENARIO_TESTING_STANDARD.md. The 2026-08-19 PST
run found coverage already complete (26/26 existing tests, no real gaps
after a false-positive name-scan on the expose_ prefix). This file adds the
still-missing Part D layers: D2 (idempotency/double-invocation) and D3
(security/SSRF surface).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imperal_sdk.testing import MockContext

import handlers as h
from schemas import AddSiteParams, RemoveSiteParams


# ── Part D2 (SCENARIO_TESTING_STANDARD.md): idempotency / double-invocation ─

@pytest.mark.asyncio
async def test_d2_double_remove_site_fails_clean_on_the_second_call():
    """remove_site checks storage.find_by_id before deleting -- a retried
    remove on a site already removed by the first call must return a clean
    SITE_NOT_FOUND, never crash or claim a second successful removal."""
    ctx = MockContext()
    added = await h.add_site(ctx, AddSiteParams(domain="example.com", platform="none"))
    assert added.error is None
    site_id = added.data.id

    first = await h.remove_site(ctx, RemoveSiteParams(site_id=site_id))
    assert first.error is None

    second = await h.remove_site(ctx, RemoveSiteParams(site_id=site_id))
    assert second.error is not None
    assert second.error_code == "SITE_NOT_FOUND"


# ── Part D3 (SCENARIO_TESTING_STANDARD.md): security / SSRF surface -------

def test_d3_no_ssrf_no_http_client_used_anywhere_in_this_app():
    """This app has no outbound HTTP surface at all -- add_site's platform=
    'wordpress' path talks to WordPress Hub via internal IPC (ext.expose),
    never a direct fetch of the site's own url/app_password. The `url`
    field on AddSiteParams is stored data / an IPC payload field, never a
    fetch target of this app's own. Regression trip-wire: if a future
    feature adds a direct outbound HTTP call, it needs its own explicit
    SSRF review at that point."""
    import inspect
    import handlers as mod
    import storage as st
    src = inspect.getsource(mod) + inspect.getsource(st)
    assert "ctx.http" not in src
    assert "httpx" not in src
    assert "requests." not in src
    assert "urlopen" not in src
