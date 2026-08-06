# Sites Registry

A single platform-agnostic catalogue of every site you manage -- domain, name, and
platform (WordPress, Shopify, other, or none yet). This app holds **no business
logic about the sites themselves** (no posts, no WooCommerce, no SEO) -- that stays
in each platform's own connector (WordPress Hub today, others later). Sites Registry
only ever answers "what sites do I have, and where do I manage them."

## Why this exists

Before this app, "what sites do I have" was answered separately and inconsistently
by WordPress Hub, Content Strategy Hub, DataForSEO Connector, and Brand Strategy Hub
-- each with its own Quick Add list sourced only from WordPress Hub. A site on
Shopify, or a site with no connector at all yet, had nowhere to live. Sites Registry
is the one list that covers all of them.

## Two-way sync with WordPress Hub (the concrete case today)

- **Add a site here with `platform="wordpress"`** (+ `url`/`username`/`app_password`)
  -> `add_site` calls WordPress Hub's `connect_site_ipc` exposed method in the SAME
  call, so the site is immediately live and connected in WordPress Hub too. No
  second step, no re-entering credentials there.
- **Connect a site directly in WordPress Hub** -> WordPress Hub pushes it here via
  `upsert_site` (best-effort, never blocks WordPress Hub's own connect/disconnect
  flow) so it appears in this registry automatically, `platform="wordpress"` already
  set, `connector_app`/`connector_ref` filled in.

## Adding a future platform (e.g. Shopify)

When a Shopify connector app exists: add one entry to `_PLATFORM_CONNECT_IPC` in
`handlers.py` mapping `"shopify" -> ("shopify-connector", "connect_site_ipc")`, and
have that connector expose a matching `connect_site_ipc` + push via `upsert_site`
on its own connect/disconnect, same shape as WordPress Hub. No other code here
changes -- the platform list, the form, and the IPC dispatch are already generic.

## Tools

- `add_site` -- register a site; for `platform="wordpress"` also connects it live.
- `list_sites` -- list all registered sites, optionally filtered by platform/status.
- `update_site` -- edit name/notes/platform on an existing registry entry.
- `remove_site` -- remove a site from the registry (does not disconnect it from its
  connector -- do that in the connector itself first if needed).

## Inter-extension surface

- `@ext.expose("upsert_site")` -- called by connector apps (WordPress Hub today) to
  push their own connect/disconnect state into this registry.
