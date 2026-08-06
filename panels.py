"""Panel UI: single list+form panel. One Add Site form covers every
platform choice; picking 'WordPress' in the platform Select re-renders
this panel (on_change -> __panel__sites) and reveals the WordPress-only
caption + url/username/app_password fields inline, in the same form --
they stay hidden for every other platform choice.
"""
from __future__ import annotations

from imperal_sdk import ui

from app import ext
import storage

_STATUS_COLOR = {
    "connected": "green", "manual": "gray", "pending": "yellow", "disconnected": "gray",
}

_PLATFORM_ICON = {"wordpress": "🌐", "shopify": "🛍️", "other": "🔗", "none": "📄"}


def _add_site_form(selected_platform: str = "none") -> ui.UINode:
    """The platform Select re-renders THIS panel with the chosen value via
    on_change -- that's what lets the WordPress-only caption + url/username/
    app_password fields appear ONLY when platform='wordpress' is selected,
    instead of always being visible for every platform choice."""
    children = [
        ui.Input(param_name="domain", placeholder="Domain, e.g. example.com"),
        ui.Input(param_name="name", placeholder="Display name (optional)"),
        ui.Select(
            param_name="platform",
            value=selected_platform,
            options=[
                {"value": "none", "label": "No platform yet"},
                {"value": "wordpress", "label": "WordPress"},
                {"value": "shopify", "label": "Shopify"},
                {"value": "other", "label": "Other"},
            ],
            on_change=ui.Call("__panel__sites", platform="{{value}}"),
        ),
    ]
    if selected_platform == "wordpress":
        children.extend([
            ui.Text(
                "WordPress only -- fills in and connects live in WordPress Hub too:",
                variant="caption",
            ),
            ui.Input(param_name="url", placeholder="https://example.com"),
            ui.Input(param_name="username", placeholder="WordPress username"),
            ui.Password(param_name="app_password", placeholder="WordPress Application Password"),
        ])
    children.append(ui.TextArea(param_name="notes", placeholder="Notes (optional)"))

    return ui.Card(
        title="Add a site",
        subtitle="Any platform, or none yet",
        content=ui.Form(
            action="add_site",
            submit_label="Add site",
            defaults={"platform": selected_platform},
            children=children,
        ),
    )


def _site_row(record: dict) -> ui.ListItem:
    platform = record.get("platform", "none")
    status = record.get("status", "manual")
    subtitle_bits = [record.get("domain", "")]
    if record.get("connector_app"):
        subtitle_bits.append(f"via {record['connector_app']}")
    return ui.ListItem(
        id=record.get("id", ""),
        title=record.get("name") or record.get("domain", "(untitled site)"),
        subtitle=" · ".join(b for b in subtitle_bits if b),
        icon=_PLATFORM_ICON.get(platform, "📄"),
        badge=ui.Badge(status, color=_STATUS_COLOR.get(status, "gray")),
    )


@ext.panel(
    "sites",
    slot="left",
    title="Sites Registry",
    icon="🗂️",
    default_width=320,
    min_width=260,
    max_width=460,
)
async def sites_panel(ctx, platform: str = "none", **kwargs) -> object:
    rows = await storage.list_records(ctx, limit=100)

    items = [_site_row(r) for r in rows]
    list_section = ui.Section(
        title=f"📋 Sites ({len(items)})",
        children=[ui.List(items=items) if items else ui.Text("No sites registered yet.", variant="caption")],
    )

    root = ui.Stack(direction="v", gap=3, children=[
        _add_site_form(platform),
        _sync_button(),
        ui.Divider(),
        list_section,
    ])
    return root
