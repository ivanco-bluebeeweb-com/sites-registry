"""Data models for Sites Registry.

One platform-agnostic catalogue of every site the user manages -- the
single place that answers "what sites do I have?" regardless of whether
a site lives on WordPress, Shopify, some other platform, or has no
platform/connector at all yet.

Canonical identity is the bare domain (e.g. "climtec.md"), matching the
convention Content Strategy Hub and DataForSEO Connector already use for
their own Quick Add site matching -- so a future cross-app join by domain
never needs a translation table.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from imperal_sdk import sdl

PLATFORM_CHOICES = ("wordpress", "shopify", "none", "other")


class Site(sdl.Entity):
    """One registered site. `connector_app`/`connector_ref` are set only
    when a real connector extension (e.g. wp-site-connector) is actually
    driving this site; a manually-added site with platform="none" or a
    not-yet-connected platform choice leaves both blank."""
    domain: str = ""
    platform: str = "none"
    connector_app: str = ""
    connector_ref: str = ""
    status: str = "manual"  # manual | connected | pending | disconnected
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""


class SiteList(sdl.EntityList[Site]):
    pass


class AddSiteParams(BaseModel):
    domain: str = Field(description="Bare domain, e.g. 'example.com' (scheme/path stripped automatically)")
    name: str = Field(default="", description="Display name; defaults to the domain if left blank")
    platform: str = Field(
        default="none",
        description="One of: wordpress, shopify, none, other. 'wordpress' with url/username/app_password "
                    "provided also connects it live in WordPress Hub in the same call.",
    )
    notes: str = Field(default="", description="Optional free-text note about this site")
    url: str | None = Field(default=None, description="Required for platform='wordpress': full https:// site URL")
    username: str | None = Field(default=None, description="Required for platform='wordpress': WP username")
    app_password: str | None = Field(default=None, description="Required for platform='wordpress': WP Application Password")


class ListSitesParams(BaseModel):
    platform: str | None = Field(default=None, description="Filter by platform: wordpress, shopify, none, other")
    status: str | None = Field(default=None, description="Filter by status: manual, connected, pending, disconnected")
    limit: int = Field(default=50, ge=1, le=100, description="Max items to return, 1-100")


class UpdateSiteParams(BaseModel):
    site_id: str = Field(description="Site id from a previous list_sites/add_site call")
    name: str | None = Field(default=None, description="New display name")
    notes: str | None = Field(default=None, description="New notes text")
    status: str | None = Field(default=None, description="New status: manual, connected, pending, disconnected")


class RemoveSiteParams(BaseModel):
    site_id: str = Field(description="Site id from a previous list_sites/add_site call")


class UpsertSiteIPCResult(BaseModel):
    """Return shape for the upsert_site IPC surface -- not user-facing."""
    ok: bool = True
    site_id: str = ""


class SyncConnectedSitesParams(BaseModel):
    source: str = Field(
        default="wordpress",
        description="Which connector to pull already-connected sites from. Only 'wordpress' exists today.",
    )
