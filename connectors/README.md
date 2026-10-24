# Connectors

Sentinel TD talks to each website through a small connector installed on the site.

| Platform | Source | Current version |
|---|---|---|
| WordPress | `wordpress/td-panopticon/` | 2.41.3 |
| Joomla 4/5/6 | `joomla/plg_system_tdpanopticon/` | 1.43.1 |

The internal identifiers (`td-panopticon` folder, `tdpanopticon` Joomla element, REST
namespace `tdpanopticon/v1`, token option name) are kept from the original project name
on purpose: changing them would turn every installed connector into a *different*
plugin and disconnect the existing sites. Only the visible names say "Sentinel TD".

The sources here are **neutral**: no panel address, no registration key. Nothing in this
repository points at a particular installation.

## Getting a package (normal way)

The packages ship inside Sentinel TD: **nothing to zip, nothing to upload**.

1. Open *Settings → Connectors* and set **Public address of this panel**.
2. Press **Download** next to WordPress or Joomla. The panel builds the package from these
   sources; the WordPress one is written with your address and registration key, so a site
   connects itself right after activation.

Uploading a zip is only needed to override the bundled version (for example to test a newer
connector); *Use the bundled one* removes the override.

## Configuring a site by hand

Works with a neutral package too: install it, then in the site's own admin page
(*Settings → Sentinel TD* on WordPress) fill in the panel address and the registration key,
or simply copy the site token into Sentinel TD when adding the site.

## Building outside the panel

```sh
SENTINEL_HUB_URL=https://sentinel.example.com SENTINEL_HUB_KEY=your-key \
    python scripts/build_connectors.py

python scripts/build_connectors.py      # neutral package
```

Output in `dist/` (ignored by Git):

- `sentinel-td-wp-<version>.zip` → WordPress: *Plugins → Add New → Upload Plugin*
- `sentinel-td-jm-<version>.zip` → Joomla: *System → Install → Extensions*

Address and key are written only into the generated zip, never into the sources.
**Do not commit `dist/`**: a personalized WordPress package contains your key.

## Updating the connectors on all sites

Nothing to do by hand: every night the panel installs the connector it ships on the sites
that run an older one (*Settings → Connectors*, where it can be switched off), and
*Update on the sites* does it right away. Only copies already on the site are updated, in
their own folder and without activating them, keeping each site's token and connection.

A WordPress connector installed as a **mu-plugin** is never touched by the panel: download it
again from the site page (*mu-plugin connector*, with the site's token already inside) and
replace the file in `wp-content/mu-plugins/`.


## Firewall mode (WordPress 2.41.0, Joomla 1.43.0)

When a firewall or anti-bot in front of a site (Cloudflare, SiteGround, Sucuri, a banned IP…)
blocks the panel, the connector calls the panel instead: firewalls filter what comes *in*, not
what the site sends *out*.

- The connector learns the panel address from the `X-Sentinel-Hub` header of authenticated panel
  requests (or uses the one built into the package / set on the settings page).
- If the panel has not called it for 30 minutes, it sends a light ping to `POST /api/agent/relay`
  (site token in `X-Sentinel-Token`); the answer says which mode to use.
- In firewall mode (decided by the panel when it meets a firewall) it asks for queued jobs every
  few minutes with a light request, and sends the full site status less often (both set in the
  panel, *Settings → Firewall mode*), after every job and after an update search. It runs the jobs
  (update, rollback, install, uninstall, vendor update, update search) with the same functions as
  the REST/com_ajax handlers and sends the results in the same session.
- WordPress 2.41.3: the update search runs in the site's admin context too (`admin-ajax.php`
  action `tdpanop_status`, token required), so licensed products report their new versions.
- WordPress runs the round from WP-Cron, and after a page view (once the response has been sent)
  if the cron is late. Joomla has no cron of its own: the round runs after a page view, and jobs
  only where PHP can close the response first (PHP-FPM, LiteSpeed).
- Every 3 hours the panel retries the direct route; when it works, firewall mode ends by itself.
- WordPress 2.41.1: plugin and theme updates run in the site's **admin context**, like without a
  firewall: the connector calls its own `admin-ajax.php` pinned to the machine it runs on
  (127.0.0.1, then the server address), so the request never leaves the server or meets the
  firewall. Licensed products that load their updater only in the admin (Elementor Pro, ACF Pro,
  Gravity Forms, WP Rocket, YOOtheme…) get their package. If the server does not answer that way,
  the update runs inside the round as before.

WordPress 2.31.0 and Joomla 1.39.0 no longer collect CPU, RAM or server disk metrics. The Linux collectors and their command fallbacks have been removed.

WordPress 2.32.0 and Joomla 1.40.0 also remove site diagnostics, large-log discovery, core-file checks, size scans and synthetic space probes. No extra probe is run before or after updates or backups.
