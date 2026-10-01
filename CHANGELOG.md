# Changelog

## 2.13.0

### Added
- **The panel updates itself** without reloading and without weight. Before, the site list was
  downloaded in full every 60 seconds whether or not anything had changed, and the site page
  never refreshed on its own. Now every 6 seconds (only while the tab is visible) the browser
  asks `GET /api/changes` for a one-line fingerprint of the fleet — last check, pending
  updates, sites not ok, diagnostics — computed with a single aggregate query; with
  `If-None-Match` the answer is an empty `304` when nothing changed. The list is downloaded
  only when the fingerprint changes, and the open site page (with `?site=ID`) only when that
  site changed, keeping what you are looking at. A full reload every 10 minutes remains as a
  safety net; coming back to the tab checks at once

## 2.12.3

### Fixed
- A WordPress site with pending updates could suddenly show as *ok* with nothing to update,
  and lose the pause of its failed items. When the forced refresh (before and after every
  update attempt) asked wordpress.org and the request failed — outside cron WordPress gives
  it only 3 seconds, easily exceeded on a slow hosting — WordPress left its update cache
  empty and silent, the connector reported zero updates and the panel believed it: counts
  zeroed, core update gone, and the pause dropped because the target version had vanished.
  The passive hourly checks then kept reading the same empty cache until a manual *Check*
  (forced again) found the updates, without their pause, so the next cycle retried them at
  once and notified the failure again.
  - Connector WordPress **2.19.2**: the forced refresh runs with cron timings (30 s instead
    of 3) and the status reply says whether the update cache was really there
    (`updates_known`, `core.known`)
  - Panel: when the cache was missing, the counts, pending updates and pauses of the last
    successful check are kept (an item whose installed version meanwhile reached the expected
    one is cleared), the site page says *Updates not verified* with the time, and the log
    records it. Older connectors behave as before

## 2.12.2

### Added
- Monthly report and client reports: the send **time** has minutes too (e.g. 08:30), with a
  time field instead of the hour number. The check runs every 5 minutes instead of once an
  hour, and a report is sent from its time until one hour later: a run missed exactly at that
  moment (worker restarting) is caught up, and the shared memory of what was sent in the period
  prevents duplicates. Existing settings keep their hour with minute 0

### Fixed
- Report previews sometimes showed only part of the report (seen in Firefox) until a reload.
  The height of the preview was measured once, when the document reported itself loaded; in
  Firefox the layout could still be unfinished then (preview loaded while its box was still
  hidden, or a column settling to its width), so the measure came out short — down to a
  quarter of the report. The preview is now measured on the content, again shortly after
  loading, when fonts and images are ready and whenever its content or width changes. The new
  measure ignores the box height, so measuring repeatedly cannot make the preview grow on its own

### Docs
- README (English and Italian) rewritten around every addition: site diagnostics, core check
  and size history, explicit update buttons with their outcome, background bulk install,
  brake for weak servers, taking a licensed package from a site, readable errors, cycle
  summaries, site status, client reports with groups and their own settings. The automatic
  cycle is described as it works — hourly, not nightly. New rows in *What goes where* (the
  Italian table also lacked the client-report rows) and two new troubleshooting entries

## 2.12.1

### Fixed
- Monthly report and Client reports → Settings: next to *Save settings* the panel said
  "No changes" right after a successful save, which read as if nothing had been saved. It now
  shows *Unsaved changes* while something is modified, *✓ Saved at HH:MM* after saving, and
  nothing otherwise. Day and hour mark the change while typing, not only when leaving the field

## 2.12.0

### Added
- **Client reports have their own settings**, one set for all clients and separate from your
  Monthly report: *Client reports → Settings* (next to *Clients*). Automatic sending switch,
  day and hour, company, title and intro, sections to include, expiry horizon and PDF layout,
  with a live preview on a client and month of choice (unsaved changes included)
- The first time, the client settings are a **copy of your current Monthly report settings**
  (custom layout included) and automatic sending to clients starts on, as before; from then on
  the two live separately
- Your report and the client reports go out each on their own day and hour; the memory of what
  was already sent in the period is shared, so no duplicates even on different days. New
  endpoints `GET/PUT /api/clients/config`; the client preview accepts unsaved settings

### Changed
- The client list shows the clients' schedule (or that sending to clients is off) instead of
  the Monthly report's

## 2.11.4

### Added
- Client reports — **autocomplete** on the client name in *Create from sites* (group mode) and
  in *New client*: existing groups first, then single clients, then folders (a folder also
  picks all its sites). Arrow keys and Enter choose; Esc closes only the menu
- **Add sites to an existing client/group**: when the name is the one of a client that already
  exists, the chosen sites are added to it instead of creating a duplicate (sites already in
  are shown as *already in* and locked; typed addresses are added to the existing ones). The
  button says what will happen, e.g. *Add 3 sites to Flash Factory*. New endpoint
  `POST /api/clients/{id}/add-sites`
- *Add sites* button inside a group, which opens *Create from sites* already pointed at it

## 2.11.3

### Changed
- Client reports: the PDF footer says only *Report di Tastiere Digitali* (the company set in
  Monthly report), without the "generated automatically by Sentinel TD", the generation time
  and the Sentinel version. Your own reports keep the full footer. A saved copy of the previous
  default layout gets the new footer automatically

## 2.11.2

### Added
- Client reports — **inside a group**: the group's sites are shown as rows (*Show the N sites
  of the group*), each with a checkbox, plus *All / None / Remove from group (N)*. Removed sites
  leave the group's report; the sites themselves are untouched. Removing every site asks for
  an explicit confirmation (the group stays, without sites, and its report won't go out)
- **From outside**, in the selection bar: *All*, *Singles only*, *Groups only*. Bulk delete now
  names the groups among the selected clients in its confirmation — a folder button also
  selects that folder's group, not only the single clients

## 2.11.1

### Added
- Client reports — **groups**: *Create from sites* can now make *one single client with all
  the chosen sites* (name and address of the group, one report grouping every site), besides
  one client per site. In group mode every site can be chosen, including sites that already
  have their own client, so both the single-site report and the group report are sent
- **Select** on the client list: select clients one by one or a whole folder at once, then
  **Merge into one client** (sites and addresses of all of them, one report; the others are
  deleted, the sites are untouched) or **Delete** them
- Folder buttons in *Create from sites* and in the client editor: a whole folder in one click,
  a second click takes it out again. The name of the folder, without its leading number, is
  proposed as the client name

### Changed
- Statistics: the *most updated sites* and *most updated components* columns are now the same
  width (with 1.4fr / 1fr the component names on the right were cut)

## 2.11.0

### Added
- **Client reports** (new *Client reports* page). A client receives every month the same
  report you get, limited to their own sites, at the same day and hour as yours, to their own
  addresses only. Sites and clients are many-to-many (usually one site, one client). Every
  client has its own *automatic sending* switch, independent from your report's; it cannot
  be turned on without an address and at least one site. Per client: preview, PDF, send now,
  edit, delete. *Create from sites* makes one client per site that has none yet, with
  sending off, ready for the addresses
- New notification *Monthly report to the client*: subject and text written for the client,
  editable in Notifications; email only, never Telegram, never your address (a client with no
  address is stopped before sending — an empty recipient would fall back to `REPORT_TO`)
- **Site status** section in every report, yours included (*Monthly report → Site status*,
  on by default): for each site CMS and version, PHP with support state, domain expiry,
  size with its growth over about a month and the database, writable space from the latest
  diagnostics, core file check. Saved copies of the previous default layout get it
  automatically; customised layouts stay as they are

### Fixed
- Reports limited to a folder counted the security vulnerabilities of every site; they are
  now counted on the report's sites only
- A report with no sites in its perimeter included every site's updates (`or not allowed`):
  the filter is now strict. It never happened with folders, but a client without sites would
  have received the other clients' data
- Client reports leave out the agency's licences (global expiries)
- Summary boxes in the PDF had a box inside each box (`.kpi div` matched the inner blocks too)

## 2.10.0

### Added
- *Settings → Site servers*: **pause between updates on the same site** for braked servers
  (default 5 s, as the fixed pause used so far; 0–300 s). On servers without the brake the
  fixed pause stays as before

## 2.9.9

### Changed
- The content area uses almost the whole available width — 2.5% margin on each side, about
  95% of the space next to the sidebar — instead of stopping at 1500 px and leaving an empty
  band on the right of wide screens. Small screens keep their 16 px padding

## 2.9.8

### Changed
- *Settings → Site servers*: servers grouped by client folder, one full-width row per IP.
  Each folder has a header with its number of servers and sites; each server goes under the
  folder where it hosts most sites, with a short "also …" note for the others. A row holds
  the Brake switch, the IP, the server name, the number of sites and *Show the sites*;
  braked rows are highlighted

### Fixed
- The brake switch used the class `.sw`, which already belonged to the colour squares of the
  Statistics legend: since 2.9.6 the squares picked up a 2 px shift and a pointer cursor from
  the switch. The switch is now `.srv-sw` and the legend is back as before

## 2.9.7

### Changed
- *Settings → Site servers* moved out of the right column into its own full-width section
  under the two columns, opened by a labelled divider: explanation and settings (sites at
  the same time, rest, Save) on one row, then the server cards in a responsive grid (up to
  four per row), larger. Opening the site list of a card no longer stretches the others in
  the same row

## 2.9.6

### Changed
- *Settings → Site servers* laid out as one card per server: a **Brake** switch, the IP with
  the server name from reverse DNS (often the hosting provider), the folders of its sites
  with their counts (so a client's servers are recognisable at a glance), the number of
  sites, and the site names behind *Show the sites*. Braked servers are highlighted.
  Reverse DNS lookups run in parallel, time out after 3 s and are cached for a day

## 2.9.5

### Changed
- The server brake is now **opt-in**: by default every server works as before, with no
  limit and no rest, and the brake (limited sites at the same time, rest after an updated
  site) applies only to the servers ticked in *Settings → Site servers*. The 2.9.4 list of
  servers without limit is not converted, since the default is now the opposite. The
  background bulk install and its automatic retry still apply to every site

## 2.9.4

### Added
- **One site at a time per server.** A site's server is the IP address of its domain; on the
  same server Sentinel works on a limited number of sites at once (default 1), while
  different servers keep working in parallel. A job that finds its server busy goes back to
  the queue after a few seconds instead of holding a worker slot. Applies to updates and to
  bulk installs
- **Server rest**: after a site where something was installed or updated, its server rests
  for a configurable time (default 30 s). Empty checks (nothing to update) do not trigger it,
  so the hourly cycle does not get longer for nothing
- *Settings → Site servers*: sites at the same time per server, rest time, and the list of
  detected servers with how many sites each one hosts. Servers that cope well can be set
  to *no limit* — typically your own, where a reverse proxy puts many sites on one IP

### Changed
- **Bulk install runs in the background.** It used to run entirely inside the browser
  request, one site after another, and the page stayed busy until the last site was done
  (tens of minutes with 30 sites). Now every site is a worker job, the page shows the
  progress (queued, waiting for the server, installing, retrying, done) and picks it up again
  after a reload; the install goes on even if the browser is closed
- Bulk install: timeouts and server errors (5xx, dropped connection) are retried once after
  a minute — on weak servers they are almost always temporary. Other errors (token,
  connector, zip) are reported as they are

## 2.9.3 — connectors WordPress 2.19.1, Joomla 1.30.1

### Fixed
- Diagnostics showed the connector's own time limit (180 s, raised for the diagnostics run)
  instead of the site's `max_execution_time`: it was read after raising it. It is now read
  before. Only that line of the diagnostics changes; no hurry to roll the connectors out

## 2.9.2

### Fixed
- Site page layout broken on sites with failed updates in their history: an error saved
  before 2.9.0 still contained the whole vendor download link with its token, a very long
  string without spaces, and the grid columns (`1.4fr 1fr` with no minimum) grew to fit it —
  one column took the whole page and the page scrolled sideways. Columns can no longer be
  widened by their content (`minmax(0, …)`), long text in the timeline and in the key/value
  lists wraps, and the history endpoint cleans old errors too, as new ones already are

## 2.9.1

### Fixed
- *Update* on a site could stop without a word while the panel said "queued": the site had
  automatic updates switched off (the button respected that switch and queued nothing), the
  items were paused after a failed attempt (the pause applied to the button too), or the site
  did not answer the initial check (written only in the log). *Update* on a site and *Update
  selected* are explicit requests: they now try right away, also with automatic updates off
  and without the pause, and only sites disabled in Sentinel are left out. *Install now* in
  Packages behaves the same way
- After *Update* the panel follows the update and shows how it went: *2 updated, 1 failed*,
  *nothing to update*, *the site does not respond: reason*. The outcome is stored in pieces
  and translated piece by piece, so it reads correctly in every language

### Changed
- *Update*, *Update selected* and *Update all* no longer send their own Telegram summary:
  what they update goes into the summary of the next hourly cycle, so Telegram gets only the
  periodic, complete summaries. With the automatic cycle switched off they still send their
  own, otherwise nothing would ever arrive

## 2.9.0 — connectors WordPress 2.19.0, Joomla 1.30.0

### Added
- **Site diagnostics** on the site page: space that can really be written (measured by
  writing and deleting 150 MB, because the free-space figure of the server disk says nothing
  about the account quota — on a real site 72 GB were free on the disk and 1.6 MB on the
  quota), temporary folder, folder permissions, PHP, memory, zip module. *Run diagnostics*
  works in the background and the page shows the result when it is ready, so no proxy can
  cut the request
- **Site size over time**: uploads, plugins, themes, other content, system files and
  database, with a chart and the growth of the last 30 days. Collected every night at 03:40
  (no write test at night) and kept for two years
- **Core file check** (WordPress): every core file compared with the official wordpress.org
  checksums — modified, missing, and extra files in wp-admin and wp-includes (leftovers after
  an FTP update, or foreign files). A notification arrives only when the result changes
- **Take a package from a site** (*Settings → Packages*): search a plugin or theme, Sentinel
  takes the zip from the WordPress site with the highest version and stores it as a package,
  ready to install on the sites the vendor does not deliver it to (Elementor Pro and other
  licensed products)
- WordPress connector: before a large update (core, or packages above 5 MB from
  wordpress.org) it checks that the needed space can really be written; without it the update
  is not attempted and the message says how much is needed and how much is available. After
  an update fails with the errors a full quota produces (PCLZIP_ERR_BAD_FORMAT, copy failed,
  download failed) it measures the space and says so plainly

### Changed
- **Failed updates: one message per site**, listing every item that failed with its reason
  underneath, instead of one message per item
- **Readable errors** everywhere (reports, Telegram, email, history): no more `&#8217;` and
  `&#8230;` (WordPress sends them already encoded and they were encoded again), no full
  download links with the vendor tokens, no installer progress lines — only the sentence that
  matters, with a hint when the cause is usually a full quota

### Fixed
- Telegram cycle summaries could arrive twice. The cycle counters stayed in Redis after the
  summary was sent, so any other summary queued in the meantime (hourly cycle, *Update all*,
  *Update* on a single site) sent the same content again. The summary now takes and clears
  the counters in a single Redis transaction, and only one summary runs at a time

## 2.8.2

### Added
- Reports show the folder each site belongs to. Telegram: a 📁 line under every site and the
  folder next to the site name in *To check*. Per-site email: the folder under the address
  and at the end of the subject, so mail rules can sort reports by client. Cycle email:
  the folder under each site. Subfolders read as a path (*Clients / Flash Factory*), several
  folders are separated by commas, and sites without a folder simply show none
- Saved copies of the 2.8.0–2.8.1 report templates are recognised as old defaults too

## 2.8.1

### Changed
- Telegram cycle summary: one block per site — the site name, then every updated plugin and
  theme on its own line inside a frame that sets it apart from the next site. Core updates
  and new major versions come first and in bold; frames longer than six lines start
  collapsed and open with a tap. The per-site summary line and the single detail block at
  the bottom are gone

### Fixed
- Long Telegram messages were cut at 4000 characters: when the cut fell inside an HTML tag
  Telegram rejected the whole message and nothing arrived. Long messages are now split
  into several, always between one block and the next, so every piece stays valid and no
  site is ever cut in half

## 2.8.0

### Changed
- **Telegram cycle summary rewritten.** Totals first, then a *To check* section with
  everything that needs attention (failed updates with their reason, items on hold or to
  update manually, homepages that changed), then one line per site with only the versions
  that matter — WordPress or Joomla core, and new major versions of the products that can
  break a site (Elementor, WooCommerce, YOOtheme, ACF, WPML, Polylang, Divi). The full list,
  version by version, sits in a collapsible block, with a link to Sentinel at the bottom.
  When everything went fine the message says so in its second line
- **Per-site report emails**: the subject tells the outcome before opening it
  (*✅ 13 updated*, *❌ 1 failed of 9*, *⚠️ homepage to check*); the homepage check comes
  first, failed and waiting items come before the rest, core and major versions stand out,
  and there is a link to the site in Sentinel

### Added
- *Settings → Update report emails*: one email per site (as before) or a single summary per
  cycle with every updated site, laid out like the Telegram message
- Saved notification templates identical to the previous defaults are recognised and
  replaced by the new ones; templates you actually customised stay as they are

## 2.7.8

### Fixed
- *Check now*, the check icon in the sites list and *Refresh preview* could never be
  clicked. Their state was written as `:disabled="busy[detail.id]"`: when an Alpine binding
  gets `undefined` from an expression containing a dot it turns it into an empty string on
  purpose, and an empty string sets `disabled`. The buttons were born disabled and stayed
  so, since the only thing that would have reset them was clicking them. Every `:disabled`
  in the panel now evaluates to a real true/false

## 2.7.7

### Fixed
- *Check now* on a site marked offline ran the full refresh, where the site contacts
  wordpress.org and every vendor again: on a slow host the button stayed grey for minutes
  without saying anything, and when the proxy cut the request nothing was shown at all. On an
  offline site it now runs the quick check, which is all that is needed to know whether it
  answers; the full refresh stays for sites that are online
- *Check now* tells what is happening: a message when it starts, the outcome when it ends
  (online, or still unreachable with the reason), and a clear message when the request is
  cut or fails

## 2.7.6

### Fixed
- Elementor Pro failing at random with "WordPress did not have the update data". Elementor
  Pro asks its own server at most once a minute, and the connector threw away and rebuilt
  the update data before every single update — while every completed update empties it too.
  On a site with many updates in a row, by Pro's turn Elementor had called its server less
  than a minute earlier, refused to call again, and Pro vanished from the list. Whether it
  worked depended on the seconds in between, not on the Pro version. WordPress connector
  2.18.3:
  - keeps update data that is already good instead of rebuilding it every time
  - when it has to rebuild, restores a good entry that got lost
  - for Elementor Pro, clears Elementor's one-minute lock and, if needed, asks its licence
    module for the version and the download directly
  - tries a second time with fresh data when the first attempt does not raise the version
- Elementor Pro now goes first in each site's queue, right after the check, while its data
  is fresh

## 2.7.5

### Fixed
- Sites on hosts that drop the `Authorization` header before PHP (some Apache CGI/FastCGI
  setups) answered every request with 401 `rest_forbidden`. The panel now sends the token in
  `X-Sentinel-Token` as well, and WordPress connector 2.18.2 accepts it — over REST and from
  the admin — when `Authorization` does not arrive
- After *Connect this site to Sentinel TD* on a site that was already registered, the
  connector said "nothing was changed" even when the panel had just realigned its token
  (the typical fix after reinstalling the connector). It now says the connection was updated

## 2.7.4

### Fixed
- WordPress connector 2.18.1: after an update attempt WordPress clears its update cache by
  itself, and the connector read that cache right afterwards to explain a failure — so every
  failure looked like "the plugin did not provide its update data", and the real WordPress
  error (download refused, file not copied…) was never shown. The outcome now comes from the
  installation itself: real errors are reported as they are, with their code, and *to update
  manually* is used only when the vendor really did not deliver the file
- Elementor Pro creates its update system only when something asks for it, usually its own
  admin pages. The connector now asks for it before rebuilding the update data, both when
  updating and when checking, so WordPress knows about the new Pro version and where to
  download it from

## 2.7.3

### Changed
- *Settings → Report a site as unreachable after N minutes* (5 by default, 0 = immediately).
  A site that does not answer is checked again every minute and reported only if it stays
  unreachable for the whole time; meanwhile it does not turn red in the panel. Replaces the
  `OFFLINE_CONFIRM_CHECKS` and `OFFLINE_RETRY_DELAY_SECONDS` variables, which are no longer read

### Fixed
- The confirmation used to wait by sleeping inside the job, holding one of the worker's four
  slots for minutes per silent site: with several sites down at once everything else,
  updates included, stalled. Each recheck is now a separate short job one minute apart

## 2.7.2

### Fixed
- With an active licence, Elementor Pro often does not show its new version until it is
  asked for from the WordPress admin. 2.7.1 held Elementor back whenever Pro was not
  reported as updatable, which on such sites meant waiting forever. Sentinel now always
  tries Pro first — from the admin, even when it is not reported — and holds Elementor only
  if that attempt does not bring Pro to the new major version; a failed attempt is retried
  at most once a day
- Reports no longer claim "licence not active or expired" when a vendor withholds the update
  file: the licence is mentioned only when the connector actually found it missing or not
  valid

## 2.7.1

### Changed
- Elementor and Elementor Pro now move together. When Elementor jumps to a new major
  version (3 → 4), Elementor Pro is updated first and Elementor follows only if Pro made it;
  if Pro cannot be updated on that site (no licence and no uploaded package) Elementor waits
  instead of running a major version ahead of Pro, which is the combination WordPress itself
  flags as incompatible and that breaks sites. Minor updates are not held back. The report
  shows the item as *on hold* with the reason, it is not counted as a failure, and Telegram
  mentions it at most once a day

## 2.7.0

### Added
- **Packages** (*Settings → Packages for licensed plugins and themes*): upload the zip of a
  licensed product once — Elementor Pro, ACF Pro, a premium theme — and Sentinel installs it
  over the current version on every site where the vendor does not hand over the update
  file, typically because the licence is not active or has expired on that domain. It also
  covers sites that do not even report the update. The activation state is preserved, the
  report marks those lines as *Sentinel package*, and *Install now* starts right away on the
  sites that need it
- WordPress connector 2.18.0 runs updates inside the WordPress admin (`admin-ajax.php`),
  where licensed plugins load their own update system; over REST alone they often did not.
  The panel falls back to REST with older connectors
- When the vendor withholds the file, the report says why — licence not active or expired,
  and for Elementor Pro whether a licence key is entered at all

## 2.6.1

### Added
- The connectors speak the panel's four languages — Italian, English, French, German.
  WordPress 2.17.0 follows the language of the logged-in administrator and falls back to
  English for other languages; Joomla 1.29.0 ships standard `.ini` language files and shows
  up in the panel as *Sentinel TD Agent (Joomla)*

### Fixed
- WordPress connector: with two copies installed (old `td-panopticon` folder and new
  `sentinel-td` folder) PHP stopped with a fatal error and the site went blank, despite the
  guard meant to prevent it — top-level functions are registered while the file is compiled,
  before the guard runs. The connector body now sits in a conditional block, so the second
  copy declares nothing and only shows its warning

## 2.6.0

### Added
- **Homepage check after updates**: before touching a site Sentinel takes a snapshot of its
  homepage, and another one once the updates are done. The two are compared and the report
  says whether the home looks the same, changed noticeably (both snapshots attached to the
  email) or broke — critical error text, blank page, HTTP 5xx. Problems that were already
  there before the update are not reported. The cycle summary on Telegram lists the result
- Licensed products that cannot be updated remotely (e.g. Elementor Pro without a
  downloadable package) are reported as *to update manually* instead of *failed*, are not
  counted among the failures and are retried at most once a day

### Fixed
- WordPress connector 2.16.0: licensed plugins and themes vanished from the update data when
  the connector rebuilt it outside the WordPress admin, so WordPress answered "the plugin is
  at the latest version" and the update was reported as failed. Their update entry is now
  preserved
- An update is successful when the version goes up, even if it lands on a different version
  than the one announced or the product updated itself in the meantime; when it was already
  at the expected version it is reported as nothing to do, not as a failure

## 2.5.10

### Fixed
- On the domains page, clicking a folder in the sidebar filtered the domains without leaving
  the page, so two entries looked selected at once and the click seemed ignored. Sidebar
  folders always open the folder's sites again; on the domains page the folder is chosen
  from the page's own menu

## 2.5.9

### Added
- **Rename a folder** from the pencil next to it in the sidebar. Every site in the folder
  follows, subfolders included; renaming to an existing name merges the two folders without
  duplicates
- **Resizable sidebar**: drag its right edge (200–460 px). The width is remembered by the
  browser; a double click restores the standard width

## 2.5.8

### Added
- Domains page: *Sort by* folder (client), provider (registrar), domain name or expiry.
  Folder and provider views are grouped; the same provider written in different ways
  (*Register SPA*, *Register S.p.A.*, *Register.it S.p.A.*) ends up in one group

### Changed
- The registrar is shown as a clear label under each domain name, with the nameservers in
  its tooltip

## 2.5.7

### Changed
- Domains page redesigned: domains grouped by folder, the same folders as the sites. A folder
  can be chosen from the menu or from the sidebar, which on this page filters the domains
  instead of opening the sites. Summary cards double as filters; a single three-way control
  (*Renew · To decide · Do not renew*) replaces the separate icons, with the note underneath
- Reports now follow the version shown in Sentinel: when an item goes from 2.4.3.3 to
  2.4.3.4 in the panel it is reported as an update, even if the connector had nothing to
  download

### Fixed
- `.it` domains showed a stray "s" as nameserver: the WHOIS header *Nameservers* was read as
  a server name. Nameservers are now parsed correctly in both the gTLD and the `.it` format
- A stray `>` character was rendered under the domains table
- The automatic translator also translated user content — site and folder names, notes,
  registrars — when the interface was not in Italian. User data is now excluded

## 2.5.6

### Fixed
- Every action Sentinel takes on a site produces its email and Telegram report again, as
  before 2.5.1. When an extension turns out to be already up to date, the report says so
  explicitly (*already up to date, nothing installed*) instead of being suppressed; only the
  history and the monthly statistics leave those entries out

## 2.5.5

### Fixed
- The worker's informational log lines (updates applied, updates not needed, reports not
  sent) never reached `docker compose logs`: the logger had no level or handler of its own
  and fell back to Python's default, which only shows warnings

## 2.5.4

### Fixed
- Real updates were silently dropped — no history, no email, no Telegram — whenever the
  version read back after the update matched the starting one or was missing. That rule,
  introduced in 2.5.1, hid every update of a package that does not bump its own label
  (Balbooa does this). An update is now left out only when the connector explicitly
  reports *nothing to do*

## 2.5.3

### Fixed
- Balbooa products (BaForms, Gallery) updated through the Joomla channel were reported as
  "nothing to do" and sent no notification. Balbooa updates the component but leaves the
  *package* at the old version (`pkg_BaForms` 2.4.3.3 with `com_baforms` 2.4.3.4), and the
  channel path only read the package. Joomla connector 1.28.0 now takes the higher of the
  two, as the vendor path already did
- The same mismatch made Joomla recreate the stale `#__updates` row at every search. The
  connector now aligns the package version to the real product version after an update,
  so the phantom update disappears for good

## 2.5.2

### Fixed
- After 2.5.1 a site whose only pending item turned out to be *nothing to do* still received
  its report email, reading "0 updated, 0 failed" with an empty table. No report is sent when
  a cycle applied nothing on that site

## 2.5.1

### Fixed
- Updates that changed nothing were recorded and notified as applied, producing lines like
  `BaForms 2.4.3.3 → 2.4.3.3` in the cycle summary. They are now recognised and left out of
  the history, the monthly rollup and the notifications
- Joomla connector 1.27.0: when a channel announces a version that does not exceed the
  installed one, the stale row is removed from `#__updates` instead of triggering a pointless
  update at every cycle, and the outcome is reported as *nothing to do* rather than as a
  successful update

## 2.5.0

### Added
- Renewal decision per domain — *to decide*, *renew*, *do not renew* — with a free note,
  set on a single domain or on a selection
- Registrar and nameservers read from RDAP and WHOIS, with a **Refresh WHOIS** button for one
  domain or a selection
- Folders on the domains page, the same ones used for sites, with filters by renewal state
  and a search across domain, site, registrar and folder
- Early reminder for domains still to decide (*Settings → Ask for the renewal decision*,
  60 days by default) and an option to keep notifying for domains that will not be renewed

### Changed
- Domain reminders now carry the registrar and the renewal decision, so the message can be
  forwarded to the client as it is

## 2.4.0

### Added
- Daily view in statistics: day-by-day chart over 7, 30 or 90 days, with the rankings of
  sites and components for that period and a *by day of the week* summary
- Round-number axis, guide lines, average line, stacked succeeded/failed bars and a side
  tooltip (sites, components, change against the previous point) in both charts

### Changed
- The dashboard chart now uses the same design as the statistics one

### Fixed
- Language selector appearing over the toolbar buttons, or not appearing at all: it was
  created before the application rendered its topbar. Placement is now re-evaluated on
  every render
- Value labels overlapping the heading of the dashboard chart

## 2.3.0

### Added
- Connector packages built by the panel itself from the bundled sources: nothing to zip,
  nothing to upload
- Setting *Settings → Connectors → Public address of this panel*, written into the
  generated WordPress package together with the registration key
- `.github/FUNDING.yml` and a donation section

### Changed
- Published under the **GNU AGPL-3.0**
- The connector sources are neutral: no panel address and no key anywhere in the repository
- An uploaded connector zip is now only an optional override, removable with
  *Use the bundled one*
- `scripts/build_connectors.py` also accepts the panel address, for builds outside the panel

## 2.2.0

### Changed
- Redesigned statistics chart, detached from the period switch, with summary cards
  (total, monthly average, best month, change)

### Fixed
- The download icon rendered as a stray character: the bundled Inter font has no ⬇ glyph,
  replaced with an SVG icon on every download button

## 2.1.0

### Added
- Detailed reports on demand (PDF or CSV) for one site, several sites, a folder or all of
  them, over any range of months: per component, how many times it was updated and from
  which version to which, plus the history of every single update
- Guided bulk install and remove: platform, package or extension, target sites with folder
  shortcuts and a filter
- Starting version stored in the monthly rollup, and a configurable retention for the
  detailed update history (default 400 days)
- Connector sources in `connectors/`, with `scripts/build_connectors.py`

### Changed
- Removal lists only the sites that actually have the extension, and never calls the others;
  results distinguish succeeded, failed and skipped
- Joomla connector 1.26.0: `file`-type extensions such as language packs can be removed,
  while Joomla core stays protected through its `protected`/`locked` flags

### Fixed
- Selects with dynamic options showing the wrong initial value

## 2.0.0

### Added
- New Sentinel TD interface: dashboard, folders, site detail with preview, statistics with
  month-over-month comparison, monthly PDF reports (global or per folder)
- Editable email and Telegram notifications, domain and licence expiry tracking with
  recurring renewals, vulnerability matching
- Screenshot service on Google Chrome, so H.264 background videos render, with thumbnails
- Interface in English, Italian, French and German

### Changed
- Dependencies updated after a security audit (python-jose, Jinja2, python-multipart,
  WeasyPrint, aiosmtplib and others)
- Connector download now requires full authentication instead of a short-lived image token
- Rate limiting added to the endpoints used by the connectors

### Fixed
- Screenshot failures logged at info level, invisible in container logs for days
- Settings that reported themselves as saved without being sent
