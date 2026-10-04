# Changelog

## 2.28.17 — Registro offline persistente

- Statistiche → Registro offline: una riga per episodio confermato, primo errore,
  primo check riuscito, durata rilevata, motivo e numero di check offline.
- Conteggi per sito e IP/server, filtri 7/30/90/365 giorni e paginazione.
- Orari Europe/Rome con ora legale automatica; corrispondente UTC nel tooltip.
- Usa i controlli esistenti: nessun nuovo cron, rilevamento risorse o notifica.
  DNS temporanei, controlli rinviati e errori recuperati prima della conferma
  non aggiungono episodi. Connettori invariati.
- Storico in PostgreSQL, persistente dopo recreate; una sola riga aperta per sito,
  snapshot mantenuti dopo rinomina/eliminazione. Registrazione dal nuovo aggiornamento.

## 2.28.16

- Configure total site-check attempts (1–5) and retry delay (1–60 seconds) in Settings.
  Defaults remain 3 attempts and 15 seconds, honoring prior environment defaults.
- Persist these values in the shared database and read them for each logical check,
  so API/manual checks and worker checks pick up saved changes without a restart.
- Retry only connection errors and temporary DNS failures. Read timeouts and update
  POST requests keep their existing behavior. No connector change is required.

## 2.28.15

- Completely retire site diagnostics: remove the action, endpoint, worker requests,
  recursive size/log scans, core checksum checks and synthetic space-write tests.
- Remove both notification events (Space and logs / Core files to check), previews,
  dispatch helpers and diagnostic findings from dashboard and monthly/client reports.
  Migrate known prior default report templates to the current version.
- WordPress **2.32.0** also removes write probes before plugin/theme/core updates and
  backups, probes after failed updates, and package-size HEAD requests for those probes.
  Normal CMS updates, actual backups and their error handling remain available.
- Joomla **1.40.0** removes diagnostic scans and write probes; obsolete diagnostic
  tasks return an explicit retired response without reading site files.
- Discard previously queued diagnostic jobs without HTTP requests or notifications.
  Legacy database data remains stored but is no longer exposed or scanned.

## 2.28.14

- Remove the Server status menu/page, overview endpoint, resource history, periodic
  CPU/RAM/disk sampling, related settings and machine splitting.
- Remove the nighttime diagnostics batch, its scheduled delivery and the server
  notification event, examples and rendering. Previously queued batch diagnostics
  become no-ops. Manual site diagnostics remain available.
- WordPress connector **2.31.0** and Joomla connector **1.39.0** remove the Linux
  collectors, command fallbacks and resource collection from status/diagnostics.
  Rebuild both installable connector packages.
- Keep dashboard warnings available through a separate read-only endpoint using
  existing site records, with no server sampling or extra site requests.
- Coordinate ordinary connector status GETs across API and worker: at most two
  concurrent requests overall, one per resolved server IP, with a two-second pause.
  Repeated Check all clicks cannot start overlapping batches; disabled sites are skipped.
- Apply the configured offline confirmation window to transient manual/pre-update
  check timeouts as well. Pending checks are shown as warnings and retried in one
  minute. Connection allowance is 15 seconds; response time is bounded. HTTP overload
  responses no longer trigger an extra alternative WordPress REST request.
- Do not delete existing database tables or site data during upgrade. Old resource
  snapshots are no longer exposed through site diagnostics.

## 2.28.13

- Rename the CPU load column in the server overview to "CPU · Carico", with translations
  in English, French and German. Sorting and detailed CPU metrics keep their behavior.
- Refresh dashboard problems and security counters on every dashboard entry, on site
  changes, when the tab becomes visible and every minute while visible. Security scans
  no longer require visiting the security page before dashboard alerts appear.
- Keep critical security alerts in the first dashboard rows, show a link for further
  alerts, and distinguish loading/failed requests from a successfully empty alert list.
  Disabled or removed sites are excluded from the dashboard's site warnings.
- Fix narrow-screen topbar overflow: actions and language selection wrap instead of
  extending beyond the viewport. Dashboard warning rows stack on small screens.
- Refresh open security/domain/component-expiry views independently of CMS checks;
  refresh server data immediately after site changes. DNS problems have a server filter.
- Do not display unknown free disk space as zero (a false 100% used). Missing CPU,
  RAM and disk readings are labeled unavailable without promising imminent arrival.
- When server readings remain partial, the next resource cycle tries other sites on
  the same server instead of permanently checking only the first three. Each cycle
  still makes at most three connector requests and prefers a complete connector found.
  Partial readings log the connector version, missing fields and measurement sources.
- The Linux collectors already support native PHP, /proc and bounded command fallbacks
  on both CMS platforms. Missing metrics are not replaced with PHP process memory.

### Verified
- 147 Python tests, including rotation across partial server readings and unknown/free
  disk-space handling; 7 dashboard behavior tests and 5 translation tests.
- Real-template browser checks cover dashboard loading/failure/resolution, critical
  alerts and overflow, mobile layout, the CPU heading and WHOIS pacing/source UI.

## 2.28.12

### Fixed
- Domain checks run one domain at a time, with a configurable pause (default 30 seconds)
  shared by manual/automatic scans in each worker. Each public source also has a request
  gate so waiting for a slot cannot consume its network timeout.
- Up to three configurable attempts per source on transient failures, with 15/30-second
  backoff and bounded Retry-After handling. Registry timeout is 20 seconds; HTTPS read
  timeout is 20 seconds and connect timeout is 10 seconds. CAPTCHA, invalid domains and
  missing expiry fields are explicit failures, without blind retries.
- Configured scan days use calendar days to avoid a daily cron missing its due date
  just because the previous lookup completed a few minutes after the cron time.
- Automatic scans persist each completed domain immediately. Cron and manual jobs allow
  six hours for a paced batch; selected domains use one job rather than filling worker
  slots with individually waiting jobs. Failed checks preserve known dates and retry daily.
- Existing multi-source comparison, .it renewal status, DNS retries, resource history,
  screenshots and WordPress/Joomla connectors are retained.

### Verified
- 144 Python tests, including cross-job domain pacing, per-source pacing, timeout isolation,
  retry recovery/exhaustion, configured-day intervals, immediate persistence and preferences.
- 5 translation tests and real-template browser checks verify default pacing, saved
  pause/attempt/scan-day values, source comparison, progress, renewal state and mobile.

## 2.28.11

### Added
- Compare registry WHOIS, RDAP where supported, who.is and whois.com on every domain lookup,
  with sequential source requests, bounded timeouts and recorded source evidence in PostgreSQL.
- Select a newer domain record or comparable newer snapshot; prefer current authoritative
  data over undated web copies. Unresolved conflicts preserve the known expiry and suppress
  misleading expiry alerts. A longer expiry alone never indicates fresher data.
- Expandable source comparison shows dates, record updates, snapshot times and source failures.
- Recognize fresh .it ok/autoRenewPeriod as renewal in progress, for its documented 15-day
  window, without inventing next year’s expiry. Inactive or stale states remain visible.
- Existing sequential domain scan, progress, DNS retries, persistent resource history and
  WordPress 2.30.0 / Joomla 1.38.0 connectors are retained.

### Verified
- 121 Python regression tests and 5 translation tests. Real-template browser checks
  cover source comparison, renewal state, sequential progress, visible failures,
  automatic refresh and mobile layout.
- The who.is parser also reads the actual vivaiomares.it HTML response received
  during verification; no future expiry was invented.

## 2.28.10

### Fixed
- .it lookups query the registry WHOIS at whois.nic.it before secondary web pages.
  RDAP remains the first source for other extensions. HTTPS fallback remains available;
  an expired secondary result is marked unverified if the registry cannot be reached.
- Failed lookups preserve the previous date, display the error even when a date exists,
  retry at the next daily scan and suppress expiry reminders based on unverified data.
- WHOIS updates participate in the panel change fingerprint. Manual refresh waits for
  completed checks rather than reloading once after twelve seconds. Domain groups use
  their most recently checked date and stale scan results cannot overwrite newer ones.
- Domain expiry page now has "Aggiorna tutti in sequenza", one domain at a time, with
  completed/total/error progress and immediate persistence after each domain. Duplicate
  sequential requests are rejected while a scan is queued or running.

### Verified
- 94 Python regression tests, 5 translation tests, and real-template browser checks for
  sequential progress, renewed dates, visible failures and automatic panel refresh.
- Existing DNS retries, persistent resource history and connector versions are retained.

## 2.28.9

### Fixed
- Status GET requests retry temporary DNS and connection failures up to three times,
  with 15 seconds between attempts. WordPress no longer changes REST paths on DNS
  failures; the alternate path is remembered only after a successful HTTP response.
- Exhausted temporary DNS failures are shown as an unverified DNS check, separately
  from offline sites. They do not send site-offline/online notifications; a new check
  is queued after one minute. Pre-update checks preserve pending updates and skip
  installation until the connector can be reached.
- Status requests reuse a managed HTTP connection pool and allow at most four
  concurrent GETs per process. Connections are closed on API/worker shutdown.
- All failed checks and delayed retries leave their exact error in the logs.

### Verified
- Regression tests cover DNS recovery/exhaustion, WordPress and Joomla, REST fallback,
  permanent DNS/TLS errors, cancellation, preserved update counts, rechecks and
  notification suppression. Real local HTTP requests verify TCP connection reuse.

## 2.28.8

### Fixed
- CPU, RAM and disk readings are stored in PostgreSQL, in the existing persistent
  database volume. Redis resets, service restarts and container recreation no longer
  clear resource history. The chart still displays the latest 24 hours.
- API and worker automatically create the new table and import any available Redis
  history once at startup, preserving timestamps and avoiding duplicate samples.
  A failed import is retried on the next startup. No Redis configuration changes.
- Stored readings older than 48 hours are pruned as new measurements arrive.

### Verified
- Regression coverage includes Redis reset, application/database reconnection,
  legacy import and retries, timestamp preservation, deduplication and retention.

## 2.28.7

### Changed
- Fresh complete release archive with application version 2.28.7 in the backend.
  All changes through 2.28.6 are retained, including WordPress 2.30.0 and Joomla 1.38.0.
- Added release identification and explicit deployment instructions for the existing
  /root/panopticon-lite project, avoiding an extra nested panopticon-lite directory.

## 2.28.6

### Added
- Shared Linux resource collector in WordPress 2.30.0 and Joomla 1.38.0. Load falls
  back to /proc/loadavg and uptime; CPU count also tries getconf and lscpu; RAM
  accepts byte/KiB and legacy/BusyBox free output; disk falls back to POSIX df.
- Fixed command locale/PATH, permission-aware PHP runners and bounded command
  execution. Direct/native readings remain the first choice.
- Neutral installable connector ZIPs in packages/ and 45 PHP regression checks.

### Fixed
- Partial RAM now triggers fallback even when total RAM is known. Zero free space,
  available RAM and swap remain valid readings. Truncated cpuinfo cannot undercount
  large servers; lscpu honors offline CPUs where available.
- Passive sampling continues across up to three sites on the same server when a
  response has only some resources. Complementary actual readings, including CPU
  counts alone, remain visible in the shared history without synthetic RAM/disk pairs.

### Verified
- 52 Python tests and 5 translation tests pass. All connector PHP files lint cleanly;
  native/command collection and both CMS wrappers were exercised on Linux. The 45
  PHP checks also pass with key PHP functions disabled and cover a timed-out command.

## 2.28.5

### Added
- A dedicated Server · nightly summary notification with editable email/Telegram
  templates, full previews, local delivery time, timezone and optional healthy reports.
  Default delivery is 09:00 Europe/Rome; DST follows the selected IANA timezone.
- Durable collections group all night diagnostics by server with CPU load per core,
  RAM, disk, writable space, large log paths/sizes, core files and diagnostic errors.
  Existing diagnostic channel choices are inherited until the new settings are saved.

### Fixed
- Night diagnostics no longer deliver individual immediate site alerts. The digest
  waits for every diagnostic, survives restarts and retries only undelivered channels
  after the configured daily time. Backlogs coalesce into one digest per channel/day.
- Long Telegram digests use a single complete HTML document instead of splitting
  notifications. Muted/disabled/deleted sites are excluded at delivery time.
- Manual diagnostics retain their existing events, clearly identified in the editor.

### Verified
- All 48 Python tests and 5 translation tests pass. Browser checks cover both preview
  formats, settings persistence and mobile/desktop layout without Alpine errors.

## 2.28.4

### Fixed
- The sidebar footer stays visible after the folder list loads, on smaller screens
  and while the menu scrolls. Only navigation scrolls; logo, version and credits stay
  in place. Version metadata also loads after password, TOTP or passkey login.
  Empty or failed metadata responses preserve the last successful version.
- CPU and RAM history now uses fixed half-hour intervals starting at :00 or :30.
  Refreshes no longer shift every tile's time label. Previous readings stay in the
  same completed interval when the current interval advances. Both partial edge
  intervals retain the actual measurements from the full rolling 24-hour window.

### Added
- A compact history legend explains averages, reading order, colors and 24-hour
  retention. The current half-hour tile has a blue border; hover/touch includes the
  date to distinguish today from yesterday. Existing current-reading timestamps
  and configurable sample intervals are preserved.
- Three regression tests cover stationary labels, completed-interval rollover and
  retention at the 24-hour edge. All 26 Python tests and the translation suite pass.

## 2.28.3

### Changed
- Expanded server details use two equal-width columns. Sites, domains, updates,
  problems and weight form a compact stack on the left; CPU, RAM and disk remain
  on the right. Desktop columns finish at the same height as their contents change.
  On narrow screens, cards flow in one column without a fixed height.
- CPU and RAM history now uses larger, full-surface buttons. Hover, keyboard focus
  or touch shows the half-hour interval, average value and status. Missing readings
  remain gray; zero readings are retained and unavailable CPU cores are explicit.
- Resource spacing and section separators are more compact. Removed the dashed
  100% and 90% chart guides and their standalone labels; thresholds stay in tooltips.
- Weight totals and the largest sites share the wider card without another outer
  column. Screenshot scheduling and configurable resource sampling are preserved.

## 2.28.2

### Added
- CPU (load), RAM and disk now have separate sections and separators. Current readings,
  measurement age, typical values and peaks are clearly distinguished. Thresholds are
  explained in the section title tooltips; missing and outdated readings are explicit.
- Resources use an independent passive timer, every 5 minutes by default, configurable
  from 1 to 180 minutes in Settings. It schedules at startup and once per server or
  configured machine split, independently of normal site polling. Failed attempts retry
  at the chosen interval; another site is tried when the representative is unavailable.
- The server status page refreshes every 30 seconds while visible, even when only Redis
  measurements change. No connector changes or additional file scans are required.

### Fixed
- Current-second samples are included in the final chart bucket. Small CPU loads have a
  visible bar height. RAM and disk remain available when CPU measurements are unsupported.
- The settings save button reads "Salva impostazioni". Previous screenshot fixes remain.

## 2.28.1

### Fixed
- The existing **preview refresh interval** in Settings now drives a dedicated scheduler,
  checked every minute and at worker startup. It no longer depends on site polling or a
  working connector. Changed settings apply without restarting; unchanged pages are captured too.
- Preview jobs share a stable per-site queue id across the scheduler and manual queue commands.
  Already queued/running jobs are not duplicated; completed screenshot jobs have no result
  retention cooldown, so an immediate manual refresh remains possible.
- `sites.shot_attempted_at` records each attempt before contacting the shooter. Failed or
  blocked captures retry after the configured interval, preserving the previous good image
  and its real timestamp. New jobs are staggered by 12 seconds to limit shooter contention.
- Screenshot success, scheduling, failures and anti-bot refusals are recorded in worker logs.
  The new nullable database column is migrated automatically and exposed by the site API.

## 2.28.0

### Fixed
- **Previews and homepage checks of sites behind an anti-bot** (seen on a SiteGround site: `403
  Forbidden` / the *Robot Challenge Screen*). Not a user-agent block alone: SiteGround answers
  `202` with a verification page (`sg-captcha: challenge`) that a real browser passes after a few
  seconds, while the shooter photographed it at once, and its browser signature was an
  incomplete `Chrome/131.0 … SentinelTD/1.0` that no real Chrome sends. Shooter now:
  - presents itself as a real Chrome — the browser's real full version, no `SentinelTD` or
    `HeadlessChrome`, Italian language and time zone, `navigator.webdriver` not declared,
    `--disable-blink-features=AutomationControlled` (`SHOT_USER_AGENT` to force another one)
  - waits for a verification screen (SiteGround, Cloudflare and similar) to pass, up to
    `SHOT_CHALLENGE_WAIT_MS` (15 s) per attempt, then takes the shot; the final status is the page's
    own, not the initial 202
  - tries a second time from scratch if it is still refused
  - keeps the **pass** (the cookie the site hands out) in `SHOTS_DIR/.state/`, only for sites
    that actually showed a verification, so the next previews and before/after shots do not
    repeat it
  - no longer launches Chrome with site isolation disabled (not what a real Chrome does); iframes
    of other sites still render
- If the site refuses anyway: the **previous good preview is kept** instead of being replaced
  by an error page, the site is marked (`sites.shot_blocked_at`: a shield on the thumbnail and a
  note on the site page: have the panel's IP allowed in the site's firewall), and the manual
  preview says so. The homepage check treats a refusal as **"not verifiable", never as a break**:
  it can no longer trigger the automatic rollback. A real 5xx still does
- Timeouts of the preview calls raised (35 s and 60 s → 120 s), which the verification wait needs

## 2.27.2

### Added
- *Split by machine* has a **How it works** explanation (ⓘ) with the three things to know: it is
  all or nothing per IP (give the same name to machines you want to keep together, whose
  measurements then mix again), machines are told apart by their hostname (cloned containers
  with the same name look like one), and the name is read by the nightly diagnostics. The ⓘ
  boxes show a list when the text has `•` separators, and long ones (`hint-wide`) are wider

### Fixed
- The ⓘ explanation box could make the page jump when it opened, and close at once: it was
  positioned inside the page, so filling it stretched the document for an instant and, with the
  page scrolled to the bottom, the browser moved it by a few pixels, taking the label from
  under the mouse. It is now fixed to the window (never changes the page length) and always
  kept inside the screen

## 2.27.1

### Added
- Server status: **sortable columns** (click the title, ▲▼) — server name, sites, PHP (the oldest
  version in use first), load, RAM, disk use, size, problems — and **by server name, A to Z, by
  default**. Number columns put the highest values first on the first click

## 2.27.0

### Added
- **Split a server by machine.** Servers are recognised by the IP of the site's domain, so two
  machines behind the same public IP (a WordPress container and a Joomla container, say) were
  one server: their load samples, cores and RAM mixed, and the page showed half of the real
  figures (the highest core count and the RAM of whichever sample came last). In *Settings →
  Site servers*, an IP whose sites report more than one machine name (the hostname the connector
  sends) now shows **Split by machine**; split, each machine is a server of its own everywhere —
  Server status (title = the name you give it, or the machine name), the reports' *Site status*
  (*name (IP)*) and the abandoned plugins — with its own load, RAM, disk, cores and name.
  Sites whose machine name is not known yet (read by the nightly diagnostics) stay on the IP
  meanwhile. The brake stays per IP, as the updates are limited by what answers on that address.
  New setting `server_split`; label keys can now be `IP|machine` (up to 160 characters)

## 2.26.0

### Added
- **Server RAM.** Connectors WordPress **2.29.0** and Joomla **1.37.0** send the memory of the
  server (total, available, swap) with every regular status check, read from `/proc/meminfo`
  (`MemAvailable`, or MemFree + Buffers + Cached on old kernels) or, when that is closed and
  `shell_exec` is allowed, from `free -b`. Available memory includes the cache the system frees
  at once, so "used" is what programs really hold. The panel keeps 24 hours of samples like
  the load. Server status: a **RAM** column in the closed row (the usual value of the last 24 hours
  — the median — with a word: *fine* under 75%, *high* 75–90%, *full* over 90%), and a *Memory
  (RAM)* block in the open server with used now, in use out of the total, usual, peak with its time,
  swap in use, and a 24-hour chart (dashed line at 90%)

### Fixed
- The Server status subtitle still said load came from the nightly diagnostics; it now says
  load, RAM and disk come from the site checks and only the size from the nightly diagnostics
- The "· calm / busy / overloaded" words next to the load were not translated

## 2.25.0

### Changed
- **Server load over the last 24 hours instead of one night-time snapshot.** Load and disk came
  only from the nightly diagnostics at 03:40, when nobody visits the sites: one sample a day, at
  the least meaningful moment. Connectors WordPress **2.28.0** and Joomla **1.36.0** add load
  (1, 5, 15 min), cores and disk to every regular status check (instant readings; WordPress
  counts the cores once a day). The panel keeps 24 hours of samples per site in Redis
  (`load:site:{id}`, self-trimming, `app/load_history.py`) and sums them up per server. Server
  status: the closed row shows the **usual** load of the last 24 hours — the median, which an
  isolated peak such as a nightly backup does not move; open, *Now* (1 · 5 · 15 min), *Usually*,
  *Peak* with its time, cores, a 24-hour chart (one bar per half hour, coloured by level, dashed
  100% line), the number of samples and the latest disk reading

## 2.24.1

### Fixed
- **Homepage check: false "changed" on sites with a video header or a slider.** The shooter
  started background videos to get a frame and shot a moment later — a different frame before
  and after the update — and sliders changed slide by themselves, so the pixel comparison saw a
  change that was not there. Now (shooter):
  - videos are paused and moved to the **same second** (`SHOT_VIDEO_FRAME_AT`, 0.5 s) before
    the shot, waiting for the frame to be drawn, so before and after show the same frame
  - the areas that change by themselves — `video`, `iframe` (YouTube, maps), `canvas` and the
    common sliders (YOOtheme slideshow/slider, Elementor slides and background video/slideshow,
    Swiper, Slick, Owl, Flexslider, Revolution, Smart Slider, MetaSlider, Splide) — are saved
    with each snapshot (`site_{id}_{before|after}.json`) and **left out of the comparison**; the
    percentage is computed on the rest of the homepage. Errors, blank page and 5xx are checked
    as before. The report says when video and sliders were excluded, with how much of the
    homepage when it is most of it

## 2.24.0

### Fixed
- Server status: fixed column widths — PHP no longer takes the free space, load is wide enough
  for "6% · calm" on one line, more room for problems
- Load without a percentage: on hostings that block `/proc/cpuinfo` the connector could not read
  the cores and the panel showed the bare load average ("13.07"). Connectors WordPress **2.27.0**
  and Joomla **1.35.0** also read the cores from `/sys/devices/system/cpu/online` and, when
  `shell_exec` is allowed, from `nproc`; when nothing works the panel says "cores not detected"

## 2.23.4

### Changed
- Server status, load: next to the percentage a word with fixed thresholds — **calm** under 70%,
  **busy** 70–150%, **overloaded** over 150% — with the same colours, and a legend in the
  resources box. The explanations are rewritten in plain words ("how hard the server is working
  compared to what it can do") without the load-average arithmetic
- The *Cores* explanation just says it is the number of processors of the server

## 2.23.3

### Changed
- The ⓘ explanations open in a styled box (panel colours, readable size, shown above the label
  so it does not cover the values, gone when the mouse leaves) instead of the browser's plain
  tooltip; the text still comes from the translated `title`. The *Cores* explanation uses the
  server's own number of cores in its example

## 2.23.2

### Changed
- Server status, wording reviewed on screen: sites as "17 WordPress" / "36 WordPress · 2 Joomla",
  PHP as "7.4 ×1" (with a hover text), disk as "85% used", size change "in 30 days", problems in
  words ("1 space · 1 PHP · 2 domains") instead of bare icons; the subtitle says a row opens on
  click and points to the ⓘ. Open: "Load average (1 · 5 · 15 min)", "Highest among the sites",
  problem kinds with the same names as the box on top, and under each kind only the detail
  ("for 5 days", "7.4.33") instead of repeating the kind. Low space reads "only N MB free"
  everywhere

## 2.23.1

### Changed
- Large logs are no longer listed as a problem (Server status, dashboard, reports): too much
  noise. They still trigger the *Site space and logs* notification and show in the site
  diagnostics
- Server status: load shown as a **percentage of the cores** (100% = every core busy, above =
  work queued), with the raw load average on hover; column headers and box labels carry an ⓘ
  with what each value means

## 2.23.0

### Changed
- Dashboard: *Keep an eye on* uses the same rows as Server status' *What is wrong* — site, tag,
  left edge in the colour of the severity
- History: grouped **by day** (today, yesterday, then the date, in local time) with the day's
  successes and failures; each row shows the time, failures have a red edge, and **⏪ Restore x.y**
  is a visible button on the right
- Domain expiries: **sortable columns** (domain, site, expiry, renewal) inside each group
- Security: severities in the same colours as Server status (critical and high red, medium
  yellow, low neutral), with the edge on the row

## 2.22.2

### Changed
- Server status, *What is wrong*: back to one row per site, with the new style — site, tags and
  server in aligned columns, the left edge in the colour of the worst problem

## 2.22.1

### Changed
- Server status, *What is wrong*: a title with the total ("N sites with something to look at out
  of M", or all fine), kind filters as pills coloured by severity, and the sites as cards in a
  grid with the server under the name, the problems as tags and a left edge in the colour of the
  worst problem (red: offline, expired domain; yellow: low space, failed updates; neutral: PHP,
  logs). Tags no longer repeat the kind before their detail

## 2.22.0

### Changed
- **Server status redone as one row per server that opens.** Closed, the rows line up in
  columns to compare servers at a glance: name and IPs, sites (WordPress / Joomla), PHP versions,
  load on cores, disk use, size with its 30-day change, problems counted by kind; servers with
  problems get a yellow edge. Open, six boxes: **Sites** (online, offline, automatic update, locked
  components, WordPress / Joomla versions), **Server resources** (load at 1 · 5 · 15 minutes,
  cores, highest load seen, disk, web server software, hostname, when it was measured),
  **Size** (total, 30-day change, average, sparkline, the five largest sites), **Updates**
  (pending, failed, monthly bars), **Domains** (expired and expiring within 30 days, with the
  sites), **Problems** (each kind with its sites); then the full sortable site list. The
  connector versions are gone from this page

## 2.21.4

### Fixed
- Names with HTML entities (`Booking Calendar &#8211; Event Calendar`, `&amp;`): wordpress.org and
  some plugins send names already encoded, and the panel showed them as they came. They are
  decoded on the way in (extension list, plugin catalog) and on the way out for what is already
  stored (plugin catalog, history, monthly totals in the reports), so they read right at once

## 2.21.3

### Added
- Abandoned plugins: **Group by** plugin, folder, server (with its name), client or status. In
  a group, each plugin lists only the sites of that group and still opens to show them. The
  folders of the affected sites are shown on each plugin row; search also matches folder,
  server and client

## 2.21.2

### Fixed
- **Settings that looked reset after a deploy.** While the API restarts, the Settings page read
  `/api/preferences`, failed silently and showed the defaults written in the page — and saving
  then wrote those defaults for real. The page now shows a loading message and retries every
  3 seconds until the real settings arrive, and refuses to save before they did. The monthly
  report and client report pages refuse to save a configuration they never loaded. The API
  rejects empty saves too (`PUT /api/preferences` without the thresholds,
  `PUT /api/reports/config` and `PUT /api/clients/config` with an empty configuration): 422
  instead of turning them into defaults

## 2.21.1

### Fixed
- Settings: number fields with a unit ("days before", "minutes"…) no longer show the spin
  arrows, which covered the unit text; the room kept for the unit is wider. *Also warn for
  domains not to be renewed* moved under *Ask for the renewal decision*, its own setting; the
  automatic rollback switch spans the full width
- No spin arrows on any number field of the panel (they were left on the fields without a
  unit); *Concurrent domain lookups* shows its unit, "at a time", like the others

## 2.21.0

### Changed
- Server status, *What is wrong*: **one row per site** with all its problems as chips on the same
  line and the server on the right, in fixed columns (rows were one per problem, the site name
  repeated, and columns drifted). Chips no longer repeat themselves ("expired domain · domain
  expired…")
- Server status cards: no site names any more. Problems are **counted by kind** (sites out of
  the total), and the card shows server stats — web server software, connector versions in use,
  average size per site — next to PHP, load, disk and updates. The badge reads *N sites with
  problems*, consistent with the box above. The month legend is coloured
- Abandoned plugins: each plugin is a **row that opens**, with the first sites already named
  on the row; open, it lists every site with version, **server** (with its name), **client** and
  **folder**. The table uses the full width

## 2.20.1

### Added
- **Servers grouped by name**: servers given the same name in Settings → Site servers become
  one group in Server status (title with the name, its IPs and server names below) and in the
  reports' *Site status* (*Name (ip1, ip2)*). Servers without a name stay one per IP. The name
  field suggests the names already used

## 2.20.0

### Changed
- **One definition of what is wrong with a site**, used by the dashboard, Server status and the
  reports (`app/problems.py`): offline, low writable space, large logs, updates **still** failed,
  unsupported PHP, expired domain. The dashboard's *Keep an eye on* used to list failures from
  the 7-day history, so a failure stayed there for a week after it was solved; it now reads
  `GET /api/servers/problems`, where a failed update counts only while it is still failed
- **Server names**: Settings → Site servers has a name field next to each IP
  (`server_labels`); the name is shown with the IP everywhere — Server status cards and
  problems, and the reports
- Server status: a **What is wrong** box on top with every problem of every server, counted by
  kind and clickable to filter; monthly updates as HTML bars with month/year labels and a
  legend (the SVG text grew huge on wide screens); a size change under 1 MB reads *stable*
- Reports (yours and the clients'): *Site status* split **by server** (with its name), a count
  of the sites with problems, and the problems under each site
- History: search by site, plugin or theme, filters (plugins, themes, core, failed,
  restorable), up to 2000 rows loaded on that page (was 200)
- Abandoned plugins: plugins not on wordpress.org are gone; four clickable boxes (closed,
  abandoned, idle for a year, up to date) that also filter
- **Sortable columns** (click the header, ▲▼) on site extensions, plugin and theme expiries,
  security, Server status sites and abandoned plugins; empty values always last

## 2.19.2

### Fixed
- *Update on the sites* (Settings → Connectors) said to follow the progress from *Install*, but
  nothing showed: the job id was stored without starting to follow it (that happened only on a
  page reload), and the Install drawer lives on the site list anyway. The progress now shows
  right under the connector — bar, sites done out of total, installed, failed, running and
  queued, and the site-by-site list on demand — and the connector versions are re-read at the
  end. The button is disabled while a distribution is running

## 2.19.1

### Fixed
- Core files check: harmless files (`wp-config-sample.php`, readme and licence files, `error_log`,
  `*.log`, `.user.ini`, `php.ini`, `.htaccess`…) are now filtered in the panel too, so they
  disappear at once also from results saved by older connectors; counts and status are
  recomputed
- **Automatic rollback** only when the homepage was really seen healthy *before* the update:
  without that snapshot (screenshot service down) a site already broken before would have been
  rolled back as if the update had broken it
- Connector WordPress **2.26.1**: the copy before an update checks the account's real writable
  space (the write test, cached 15 minutes) instead of the server disk, and leaves the space
  to the update when there is not room for both — on hostings with a quota the copy could
  otherwise use up the space the update needs

### Changed
- Server status: the core files are no longer listed among the problems, and the brake switch
  and badge are gone from the cards (the brake stays in Settings → Site servers)
- Settings → Connectors: one aligned block per connector — tag and name, buttons on their own
  line, status on the sites below
- Sidebar: *7-day history* and *Statistics* right after *Security*, then *Abandoned plugins*

## 2.19.0

### Added
- **Copy before the update and restore.** Connector WordPress **2.26.0**: before updating a
  plugin or a theme, its folder is zipped into `wp-content/uploads/sentinel-backups/`
  (`{type}-{slug}-{version}-{date}.zip`, folder protected by `.htaccess` and `index.php`); no
  copy over 300 MB or when space is short, and the update goes ahead anyway with the reason in
  the reply. Last two copies per item, nothing older than 14 days. New endpoints `GET /backups`
  and `POST /rollback`, which swaps the current folder with the copy and puts things back as
  they were if anything fails, then clears the caches on the next request
- The panel stores the copy name on each history row (`update_history.backup_file`); the site
  history and the 7-day history show **⏪ Restore x.y** on updates that have one
  (`POST /api/sites/{id}/rollback`). A restored component is **locked** to the restored version
  so the next cycle does not update it again, and the site status is re-read
- **Automatic rollback**: when the homepage check after an update says *ko* (an error or a
  5xx that was not there before) and the updated items have copies, they are restored from
  the last to the first, locked, the homepage is checked again, and the new notification
  *Automatic rollback after an update* (email and Telegram) tells what was restored and whether
  the homepage came back. Switch in Settings (`auto_rollback`, on by default)

## 2.18.0

### Added
- **Server status** page (sidebar, under Statistics). One card per server — the IP of the sites'
  domain, with the server name — or, with one click, per client folder. In each card: number of
  sites (WordPress / Joomla), the folders on it, PHP versions (unsupported in red), load and
  cores, server disk, pending and failed updates, the total size of its sites with a 30-day
  sparkline, updates per month over the last six months (successes and failures), the list of
  problems with a link to each site (offline, low writable space, large logs, failed updates,
  core files, unsupported PHP), and the brake switch for that server. The site list of a card
  opens on demand with CMS, PHP, state, updates, size, connector version and last check.
  Connectors WordPress **2.25.0** and Joomla **1.34.0** report load average, cores, server disk,
  software and hostname in the diagnostics. Endpoint `GET /api/servers/overview?by=server|folder`

## 2.17.0

### Added
- **Abandoned plugins** page (sidebar, under Security). Every WordPress plugin of the fleet with
  the sites that run it and their versions, and from wordpress.org the author's last update,
  the *tested up to* version, the current version and closures (the API answers 404 with the
  closure data in the body for closed plugins). Statuses: closed, abandoned (idle for 2+
  years), idle for a year, up to date, not on wordpress.org (licensed products), not checked
  yet. *To look at* shows the first three; *All* everything, with search by plugin or site.
  Catalog table `plugin_catalog`, refreshed every Sunday at 05:00 and at the first start, or
  right now with *Refresh the catalog*. Endpoints `GET /api/plugins/catalog`,
  `POST /api/plugins/catalog/scan`

## 2.16.0

### Added
- **Space and logs watch.** Connectors WordPress **2.24.0** and Joomla **1.33.0**: while
  measuring the site size they list the **large logs** (over 10 MB: `error_log`, `debug.log`,
  `*.log`, `*.error.log`), inside the site and in the account folder next to it (the user's home
  and `~/logs`, where cPanel writes the PHP error log — invisible from the site and the usual
  cause of a full quota). The nightly diagnostics now also runs a light **50 MB write test**
  (the space an average update needs). New notification *Site space and logs* (Telegram on by
  default): one per site when the writable space is under the test size or a large log
  appears, again only when something changes (a new log, a log grown by another 50 MB, space
  gone or back); the memory is kept in Redis. The site page lists the large logs in the
  Diagnostics box

## 2.15.0

### Added
- **Connector kept up to date by itself.** Connectors WordPress **2.23.0** and Joomla **1.32.0**
  declare their version in the status reply (`connector`); the panel stores it per site
  (`sites.connector_version`, for older WordPress connectors read from the `td-panopticon`
  plugin in the extension list). *Settings → Connectors* shows, for each CMS, the version the
  panel ships, how many sites are at it and which are behind, with **Update on the sites**: a
  background install job (one per site, server brake respected, progress in *Install*). A
  nightly run at 04:30 does the same by itself; switch *Every night install the new connector
  by itself…* in the same place (`connector_auto_update`, on by default). New endpoints
  `GET /api/connectors/rollout`, `POST /api/connectors/{kind}/rollout`

## 2.14.4

### Fixed
- Component lock vs the Elementor / Elementor Pro pairing. With Elementor Pro locked and
  Elementor jumping to a new major version, the pairing rule tried the Pro anyway (it saw it
  "behind" and not in the queue), overriding the lock; with Elementor locked, the Pro could be
  taken to a new major alone, the combination that breaks sites. Now a locked member of the
  pair is never attempted, and the other one waits with an explicit reason ("… is locked to
  x.y, unlock it to update them together"). Minor updates within the same major still go
  through

## 2.14.3

### Fixed
- A renewed domain could keep showing as expired for up to a week: the registry was read again
  only every *domain scan days* (7 by default), whatever the expiry. Domains already expired or
  expiring within 30 days are now read again **every day** (the daily run at 07:15), the others
  keep the configured interval. A domain checked less than 20 hours ago is not read again.
  *Check all now* in Domain expiries still forces an immediate read of every domain

## 2.14.2

### Added
- **YOOtheme Pro configuration cache cleared after updates**, on both platforms — connector
  WordPress **2.22.0** and Joomla **1.31.0**. YOOtheme's *Clear cache* button empties two folders:
  `~theme/cache`, the compiled configuration of the builder, its elements and dynamic sources,
  which goes stale after updates (e.g. a plugin adding builder elements) and rebuilds by itself;
  and the image cache (`uploads/yootheme/cache`, `media/yootheme/cache`). After any update the
  connectors now empty the first one, before YOOtheme reads it again (WordPress: at
  `plugins_loaded`; Joomla: at `onAfterInitialise`, marked by `onExtensionAfterInstall`,
  `onExtensionAfterUpdate` and `onJoomlaAfterUpdate`, so updates made from Joomla's
  administration count too). The image cache is deliberately left alone: not needed after an
  update, and regenerating every resized image would weigh on weak servers. The theme CSS is in
  neither folder: YOOtheme compiles it in the browser, in the customizer

## 2.14.1

### Changed
- Connector WordPress **2.21.0**: after **any** plugin, theme or core update or install (not only
  Elementor's), the site's caches are cleared on the next request, each with its own official
  command and only when present; one failing never stops the others nor the site:
  - CSS generated by page builders: Elementor, Essential Addons, Beaver Builder, Divi, Avada
  - page and minify caches: WP Rocket, W3 Total Cache, LiteSpeed Cache, WP Super Cache,
    WP Fastest Cache, SiteGround Optimizer, Breeze, Cache Enabler, Hummingbird, Nginx Helper,
    Autoptimize
  - object cache: cached options only, or the whole cache when Redis Object Cache is set to
    flush only this site's keys (`WP_REDIS_SELECTIVE_FLUSH`) — a full `wp_cache_flush()` on a
    Redis shared by several sites or applications would empty all of them
  YOOtheme Pro's cache is deliberately not touched

## 2.14.0

### Added
- **Lock a single plugin or theme** to the installed version: site page → *Extensions* → *Lock*
  (with confirmation) and *Unlock*. A locked item is never updated — not by the automatic cycle,
  not by *Update* or *Update all* — and is not counted as pending, so the site does not stay
  yellow because of a deliberate choice; it keeps showing the version available, marked
  *🔒 locked*. Stored per site (`sites.locked_items`), so it survives the status refreshes that
  rebuild the extension rows. New endpoint `POST /api/sites/{id}/lock`
- Connector WordPress **2.20.0**: after an update or install of Elementor, Elementor Pro, any
  theme or the core — from Sentinel, from wp-admin or from WordPress's own automatic updates —
  Elementor's CSS cache is cleared, as `wp elementor flush-css` does. It happens on the next
  request (which comes right away: the panel re-reads the site after every update), not in the
  update request itself where the old Elementor code is still loaded. Seen on a site that hung
  with 504 Gateway Timeout after a batch of 17 updates until the cache was cleared by hand

## 2.13.2

### Fixed
- A site just added (by hand or through the connector's automatic registration) could show
  *ok* with nothing to update until the first manual *Check*. Its first check was a passive
  one, reading WordPress's update cache as it was — stale, or missing until WordPress's own
  12-hour check runs. The first check of a new site is now a forced refresh, like *Check now*

## 2.13.1

### Changed
- The *Core files check* notification is **off by default** (email and Telegram): the nightly
  check sent one message per site, mostly for harmless findings. The result stays on the site
  page and in the *Site status* column of the reports. Turn it on in Notifications if wanted
- Connector WordPress **2.19.3**: the core check no longer reports files WordPress never runs
  and that hosts and tools keep touching (`wp-config-sample.php`, readme and licence files),
  nor server logs and configs among the extra files (`error_log`, `*.log`, `.user.ini`,
  `php.ini`, `.htaccess`, `web.config`, OS leftovers). They are counted as `ignored_count`

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
