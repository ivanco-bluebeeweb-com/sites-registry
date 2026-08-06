"""Extension declaration for Sites Registry.

WHY THIS APP EXISTS (explicit user directive): a single platform-agnostic
catalogue of every site the user manages -- domain, name, platform
(wordpress/shopify/none/other), and which connector (if any) is actually
driving it. Two-way sync with WordPress Hub specifically:

  - Add a site here with platform="wordpress" (+ url/username/app_password)
    -> this app calls WordPress Hub's connect_site_ipc in the SAME call, so
    the site is immediately live and connected there too.
  - Connect a site directly in WordPress Hub -> WordPress Hub pushes it here
    via upsert_site (best-effort, never blocks WP Hub's own flow) so it
    shows up in this registry automatically, platform already set.

No business logic about the sites themselves lives here (no posts, no
WooCommerce, no SEO) -- that stays in each platform's own connector. This
app only ever answers "what sites do I have, and where do I manage them."
"""

from imperal_sdk import Extension, ChatExtension

ext = Extension(
    "sites-registry",
    version="0.1.0",
    display_name="Sites Registry",
    description=(
        "A single platform-agnostic catalogue of every site you manage -- "
        "domain, name, and platform (WordPress, Shopify, other, or none yet). "
        "Add a WordPress site here and it connects live in WordPress Hub in "
        "the same step; connect one directly in WordPress Hub and it appears "
        "here automatically. One list for sites on any platform, or no "
        "platform at all."
    ),
    icon="icon.svg",
    actions_explicit=True,
    capabilities=["sites:read", "sites:write"],
)

chat = ChatExtension(
    ext,
    tool_name="sites-registry",
    description="A platform-agnostic catalogue of every site you manage, across any connector.",
)


@ext.health_check
async def health_check(ctx) -> bool:
    """Basic liveness check -- confirms the store surface is reachable."""
    await ctx.store.query("sites", limit=1)
    return True
