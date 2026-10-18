# Hamilton H36215140 monitor

Independent watch alerts in the existing Telegram chat. No changes to PS5/IFK filtering,
secrets or notification histories. Watches are new/unworn **and** used.

## VPS operation (18 October 2026)

Production now runs on the HostBrr VPS as `hamilton-watch.timer`, every 15 minutes
(minutes 10, 25, 40, 55, with up to 15 seconds jitter). GitHub's `Hamilton Watch`
workflow is disabled, `WATCH_MONITOR_ENABLED=false`, and its cron trigger was removed.
Other monitors and the test workflow are unchanged.

Code: `/opt/hamilton-watch`; isolated Python environment: `.venv`; service user:
`hamilton`. `deploy/watch/install.sh` installs the environment and units but does not
enable notifications. `deploy/watch/run.sh` captures Chrono24 and eBay sequentially,
then checks the retailer sources and evaluates all snapshots.
Chrono24 first uses curl_cffi 0.16.3 with its Chrome 150 profile. If strict analysis
reports incomplete coverage, one Playwright/Google Chrome capture runs under Xvfb
with its sandbox enabled (`WATCH_BROWSER_BACKEND=auto`). The more complete current
snapshot is retained, so a blocked fallback cannot discard verified HTTP offers.
The HTTP capture checks at most five details, spaces requests by three seconds and
stops on 403/429. There is no repeated profile rotation inside a scheduled run.
Notification credentials are removed from the capture subprocess environment.
eBay uses Camoufox 0.5.8 / Firefox 156.0.1-beta.36, also without notification secrets.
For Chrono24, Camoufox is available only as a manual diagnostic
(`WATCH_BROWSER_BACKEND=camoufox`), not an automatic fallback. The installer provisions
the required Firefox browser and fpgen model. Chrome and Firefox run sequentially;
the complete service has a 2.2 GB memory ceiling and a ten-minute timeout.

Live VPS checks on 18 October: **Uret and Corso Vinci verified successfully**.
Chrono24's browser access was intermittent overnight. The new curl_cffi capture
retrieved the search and all five detail pages; three passed the existing strict
Sweden-price and EU-origin checks. This is a successful live check, not a guarantee
against future blocking. Unverified hints remain separate when details fail.
No CAPTCHA solving or proxy rotation is used.

eBay remains intermittent: Camoufox loaded search and details on 17 October, but
on 18 October it returned 403, as did curl_cffi 0.16.3 with Chrome 150, Firefox 147
and Safari 26.0.1 profiles, HTTP/1.1, persistent Chrome/Firefox sessions via the
homepage, and nodriver. These unsuccessful experiments were not substituted for
the production Camoufox path. The search parser skips eBay's placeholder advert
and unrelated recommendations. Main-item reference numbers come from the visible item
specifics; item IDs are not mistaken for model SKUs. Explicit non-EU item locations are
excluded under the existing import policy. A Netherlands/USD shipping quote is never
treated as Sweden/SEK: missing Sweden shipping or SEK pricing remains incomplete coverage.
No unverified eBay hints or currency conversions were added.

Each eBay run checks the first search page and at most five details. Missing, blocked,
redirected or stale snapshots report errors, and skipped details report partial coverage.

Telegram credentials are in `/etc/hamilton-watch.env` (root only, mode 0600).
History was copied from `watch-state:watch-state.json` after disabling Actions, with
the recipient hash verified. Active history is now `/var/lib/hamilton-watch/watch-state.json`;
the old GitHub branch is an archive, not a live mirror. A migration backup is retained
on the server. Never reset the state or re-enable the Actions notifier alongside the
server: doing so can duplicate alerts. The service refuses to run with missing/empty
history. Reports are in `/var/lib/hamilton-watch/output`.

```sh
systemctl list-timers hamilton-watch.timer --no-pager
journalctl -u hamilton-watch.service -n 30 --no-pager
systemctl disable --now hamilton-watch.timer  # pause
systemctl enable --now hamilton-watch.timer  # resume
```

The historical GitHub access results below describe the old deployment; VPS source
coverage is recorded in each live report. A running timer alone does not mean that
every source is accessible.

## Price policy

Research snapshot: 14 September 2026. These are adjustable buying targets, **not a
valuation, authenticated offers, or verified completed-sale prices**.

| Condition | Alert at total ≤ | Bargain highlight at total ≤ |
| --- | ---: | ---: |
| New / unworn | 11,500 SEK | 10,500 SEK |
| Used, acceptable stated condition | 9,000 SEK | 8,000 SEK |

All limits include the publicly stated shipping cost to Sweden. The new target is
about 24% below the manufacturer's Swedish list price and below the roughly
12,200–12,400 SEK delivered prices observed directly on qualifying Chrono24 offers.
The used target is about 22% below our new buying target, allowing a discount for
wear, remaining warranty and private-sale risk. Used-market evidence is sparse;
the ceiling is a conservative recommendation, not a claimed statistical median.

Sources used to set the initial targets:

- [Hamilton Sweden](https://www.hamiltonwatch.com/sv-se/h36215140-jazzmaster-performer-auto.html): recommended price 15,095 SEK.
- [Uret](https://www.uret.se/hamilton/jazzmaster/h36215140/1080987): 14,395 SEK + 99 SEK shipping, ordered on demand, 1–3 weeks.
- [Chrono24 / Corso Vinci](https://www.chrono24.se/hamilton/mens-h36215140-jazzmaster-performer-auto-38mm--id48515488.htm): direct Sweden-context check about 12,176 SEK including shipping. Prices and currency conversion fluctuate.
- [Chrono24 / Wear the Time](https://www.chrono24.se/hamilton/jazzmaster-performer-auto-38-mm--h36215140--id43482599.htm): direct Sweden-context check about 12,361 SEK including shipping, procurement required.
- An older [used full-set asking-price listing](https://www.chrono24.com.tr/hamilton/jazzmaster-performer-auto--38mm--id44945952.htm) was indexed at €750. It is now removed/redirects. This is historical asking-price context **only**, not a live recommendation or proof of sale.

Do not use a search snippet's low price as a Sweden-delivered price: a `.se` page
can still contain another destination's pricing. One indexed 9,886 SEK offer was
about 12,361 SEK when fetched with Sweden as the actual destination.

## Sources and limitations

- **Chrono24:** exact-reference search, followed by individual offer checks. Public
  product JSON-LD supplies exact reference, condition, price, shipping destination,
  shipping rate and location. The visible price is preferred if consistent; table
  fields preserve procurement time, box/papers and native price. No purchase/account
  activity or nationality/identity assertions are performed.
- **Uret:** current exact-reference search discovers new stock IDs, including used
  items if returned. Parse the product's Angular server-rendered state and visible
  Sweden selector/shipping quote. A product that is orderable but not in stock is
  labelled as such. Removed items (HTTP 410) are not live stock.
- **eBay:** the VPS uses Camoufox for public search and item pages. Verified price
  alerts require an exact-reference main product, acceptable condition, EU item
  location, explicit Sweden shipping and a SEK total. Many pages show another
  destination or currency and therefore cannot support a verified alert. These
  limitations appear in coverage reports. Non-EU items are explicitly excluded.
  No search-price alerts are sent for eBay. A Browse API integration is not included.
- **Corso Vinci:** direct retailer product, not the Chrono24 listing. Require exact
  SKU, orderable main-product form, matching visible price and variant price,
  storefront country `SE` and currency `SEK`. The product page includes its
  [Europe shipping tariff](https://www.corsovinci.com/pages/spedizioni). Convert that
  tariff from EUR using the current storefront's EUR/SEK rate, round shipping up to
  the next öre, and label the total **approximate** in alerts/reports. This is not a
  checkout quote. No cart is created. One fixed new-product URL is checked, not a
  used-market search. Locally observed: 11,458 SEK + approximately 114.58 SEK shipping.
- EveryWatch, WatchCharts, Kleinanzeigen, Blocket and Tradera are **not** monitored.

`curl_cffi` impersonates a supported browser's TLS/HTTP fingerprint; it does not run
JavaScript, solve CAPTCHAs or guarantee access. There is no proxy rotation, login,
challenge solving or retry loop for a blocked source. Requests are spaced at least
1.5 seconds apart within a source. A block ends that source's check; the next
scheduled run starts a fresh check. Respect the sites' terms and permissions.
For an explicit compatibility check, `WATCH_BROWSER_PROFILE=chrome142` selects the
newer profile instead of the default `chrome124`; the manual workflow exposes the
same choice. No automatic profile rotation occurs.

Deployment checks on 14 September: both Linux and Windows GitHub runners returned
HTTP 403 for Chrono24/eBay; changing Chrome 124 to 142 on Linux did not resolve it.
Uret worked on both. **Local Chrono24 success does not imply remote coverage.**
The subsequent Linux check also verified Corso Vinci with Sweden selected:
approximately 11,572.58 SEK including converted shipping versus 14,494 SEK at Uret.
At that point, working remote coverage was **Uret + Corso Vinci**, not Chrono24/eBay.
The manual workflow's runner selector is diagnostic only; scheduling defaults to
Linux, with no automatic runner/IP rotation.

### One-off remote browser diagnostic

The manual-only **Hamilton Browser Test** workflow runs ordinary headed Chromium
under Xvfb on a Linux GitHub runner, using Playwright 1.62.0. It reads the public
reference search and at most three offer pages, stopping on HTTP 403/429. It has
read-only repository permissions, no Telegram secrets, no notification/state writes,
no proxy or CAPTCHA solving, and does not change the production monitor.

[Test on 14 September 2026](https://github.com/DieserLaurenz/ps5-blocket/actions/runs/34863282444):
Chromium 151 loaded the search with HTTP 200 and found five offer links. The first
offer navigation returned HTTP 307 followed by HTTP 403 and a `Vänta...` challenge
page. No further offers were opened. Consequently **zero Sweden-delivered offers
were verified**; search-page access alone is not sufficient for reliable alerts.
The diagnostic intentionally exits with code 2 when no qualifying detail page can
be verified, so this run is marked failed rather than presenting false coverage.
This does not establish that all offers or all hosting providers are blocked.

To repeat explicitly: Actions → Hamilton Browser Test → Run workflow. Compact
`WATCH_BROWSER_SEARCH`, `WATCH_BROWSER_DETAIL` and `WATCH_BROWSER_RESULT` log records
contain HTTP results and parsing outcomes, not raw HTML, cookies or screenshots.

### Remote Chrono24 discoveries (18 September 2026)

The user explicitly enabled separate **unverified discovery hints**. The scheduled
Hamilton Watch workflow now runs ordinary Chromium/Playwright before the notification
step. A fresh test on 18 September again read four search listings but hit HTTP 403
on the first detail page. This is **search coverage, not verified buying coverage**.

`watch_chrono_browser.py` reads the exact-reference search and up to five details,
stopping immediately on HTTP 403/429. Only result cards under the exact-reference
heading are used; links in recommendations/footer content and visibly different
references are excluded. When detail pages are readable, the existing strict
reference, condition, EU-origin, SEK-total and Sweden-shipping checks still apply.

If details are blocked/unreadable, `chrono24_unverified_hints=true` allows a separate
Telegram message headed **Chrono24 · ungeprüfter Hinweis**. It explicitly states:

- Found in the H36215140 search, but exact product details and used/new condition
  have not been confirmed. The listing may be new rather than used.
- Sweden shipping and the delivered total are unknown. Search-card prices may be
  for another destination and are never used as validated SEK totals.
- No price-ceiling check is possible. These hints are not counted as matching offers.

Each newly discovered listing ID is sent once, including current listings on the
first successful run. "Newly discovered" does not assert that the seller just posted
it. Disappearing/reappearing IDs do not alert again. Search-only price changes do not
trigger price-drop alerts. Confirmed offer-price drops retain their existing rules.
IDs already notified as verified offers do not generate subsequent unverified hints;
a previously hinted listing can still trigger a later verified qualifying alert.

Hint timestamps are saved separately in `unverified_notified` on the existing
`watch-state` branch. Version-1 state is extended without clearing old history.
The browser runs without notification secrets. Its temporary snapshot at
`output/chrono-browser.json` stays on the ephemeral runner; it is not logged,
committed or uploaded as an artifact. The notification step re-parses the snapshot
and rejects missing, corrupt, future-dated or older-than-30-minute input. Browser
failures preserve history and do not stop healthy retailer sources.

The main workflow remains scheduled every 15 minutes on a best-effort basis. There
is no new server, paid service, login, proxy, CAPTCHA solver or stealth extension.
The separate Hamilton Browser Test still requires a verified detail offer to pass;
its stricter diagnostic result is distinct from successful search-only monitoring.

The [full remote dry run on 18 September](https://github.com/DieserLaurenz/ps5-blocket/actions/runs/35366634514)
successfully produced four unverified Chrono24 hints, zero verified Chrono24 offers,
and two verified retailer offers. eBay remained blocked. No Telegram messages or
notification-state writes were made by that dry run. The offline suite has 175 tests.

Unknown shipping cost/destination, ambiguous reference, unsupported condition,
sold-out goods and non-EU/unknown listing locations do not trigger verified offer alerts.
The EU-location check is a conservative import filter, **not verification of the
actual dispatch warehouse or final tax treatment**. The displayed total is the
listed watch price plus listed shipping, not a guaranteed checkout total. Optional
insurance, payment/FX fees, and undisclosed checkout charges are not estimated.
Confirm origin, checkout total, condition, authenticity, bracelet links, warranty
and payment without Swedish BankID before buying. The monitor needs no BankID,
but cannot guarantee a seller/payment provider never requests identification.

Only the first search page and up to 25 HTTP detail pages per source (five in the
Chrono24 browser step and five in the eBay browser step, fewer on a block) are checked. Detected
pagination/limits generate a partial-coverage warning. Seller relists can have new
IDs; cross-platform duplicate watches are not automatically equated. Broken site
formats are reported, not bypassed. Remote access may differ from local access.

## Run locally

Python 3.10+. The isolated environment avoids changing dependencies for PS5/IFK.

```powershell
python -m venv .watch-venv
.watch-venv\Scripts\python.exe -m pip install -r requirements-watch.txt
.watch-venv\Scripts\python.exe watch_monitor.py --dry-run
.watch-venv\Scripts\python.exe -m unittest discover -s tests -v
```

`Watch.cmd` runs a live read-only check and opens the local report once the
environment is installed. `output/watch-offers.html` and `watch-offers.json` include
all verified offers, including those over the alert limits, source coverage and
exclusion reasons. When supplied with `--chrono-snapshot`, they also contain a separate
unverified-hints section. Check the timestamp; reports are snapshots, not live pages.
`--dry-run` writes only those local reports: no Telegram or notification-state writes.
To deliberately send locally with configured Telegram environment variables, first
provision a valid state file (migrate the existing history, or explicitly initialize
with `empty_state()` for a genuinely new monitor), then use
`--local-state output/watch-state.json`; do not run it alongside the remote notifier
unless you want a separate alert history.

## Remote operation

### Telegram presentation

Offer alerts, unverified hints and the status summary use compact Telegram HTML:
clear headings, German-formatted SEK amounts and named links instead of bare URLs.
Unverified hints show a **Suchpreis, unbestätigt** only when a recognizable SEK price
is present in the result card. This is display-only, never a validated delivered
total or an input to the budget filter. Shipping-only amounts are not promoted to
watch prices. Model/condition, Sweden shipping and final-price caveats remain visible.
Technical source errors remain in the report/logs; Telegram uses short status labels.
Layout changes apply to future messages and do not clear history or resend listings.
The manual **Hamilton Telegram Preview** workflow sends one clearly labelled sample
with `--test-message`. It uses no source fetches or state reads/writes and has read-only
repository permissions. Sample prices are explicitly not live offers.

### Schedule and history

Previously, `.github/workflows/watch.yml` was scheduled at minutes 8, 23, 38 and 53. GitHub Actions
schedules are best-effort and may be delayed or skipped; this is **not** an exact
15-minute SLA. Public repositories can have schedules disabled after prolonged
inactivity. A daily heartbeat helps identify problems while the workflow runs, but
cannot report the scheduler itself stopping. Check Actions if heartbeats stop.

For the old workflow, repository variable `WATCH_MONITOR_ENABLED=true` enables notifications.
It is now `false` and the workflow itself is disabled. Manual dry runs require explicitly
re-enabling the workflow; keep the variable false. Existing `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` secrets are retained. Archived state lives at
`watch-state:watch-state.json`, independently of other bots.
Provision this file with the result of `watch_monitor.empty_state()` before the first
notifying run. Missing/corrupt remote state is an error, never an automatic reset.

New qualifying IDs alert once. Further alerts require a new **lowest previously
notified total** at least 100 SEK lower; small FX/rounding fluctuations are suppressed,
but larger FX changes can still trigger an alert. Initial qualifying offers alert on
the first run. Text delivery is saved immediately, then up to ten listing images are
sent. Failed photo batches retry at most three times while the offer qualifies; no
text resend is required. A crash between Telegram delivery and durable save can still
duplicate a message; exactly-once delivery across both services is not possible.

A status message is sent initially, every 24 hours and on source health changes. It
includes the cheapest verified offer per condition, even above the limits, and any
failed sources. Partial failure does not suppress healthy sources; all-source failure
also fails the workflow. Repository state is public but contains no tokens, chat ID
or seller contact data (only a recipient hash and notification metadata).
