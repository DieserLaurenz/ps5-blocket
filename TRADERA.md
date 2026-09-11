# Optional Tradera monitor

Tradera is a separate source in `marketplaces.py`, implemented in `tradera.py`.
It uses the official stable SOAP v3 API, not scraped search HTML. No extra Python
packages, server, second cron job or paid LLM is needed. Tradera's API is
[free for approved developers](https://api.tradera.com/llms-full.txt); approval and
API availability are controlled by Tradera. This module is **disabled by default**
until you supply your own developer application credentials.

## Enable remotely

1. [Register a developer account](https://api.tradera.com/register), separate from
   your normal Tradera account. Create an application for personal, read-only PS5
   search alerts and obtain its AppId and AppKey. Wait for approval if required.
2. Run `Setup-Tradera.cmd` on this PC with Python and an authenticated GitHub CLI.
   Both values use hidden input; do not post them in chat. The helper verifies a
   search, saves `TRADERA_APP_ID` and `TRADERA_APP_KEY` as repository Actions secrets,
   and sets the Actions variable `TRADERA_ENABLED=true`.
3. Check the next `PS5 Search` workflow's JSON output: `sources.tradera.status`
   should be `ok`, or `incomplete` with explicit coverage warnings. Once credentials
   are available, verify a live run before relying on the monitor.

You can also set those secrets/variable manually in the GitHub repository's
Settings → Secrets and variables → Actions. The existing cron-job.org dispatch
runs both sources. To stop **only Tradera**, set the repository variable
`TRADERA_ENABLED=false`. Blocket continues. No new Telegram bot is required.

For local CLI use, set the two credential environment variables and
`TRADERA_ENABLED=true`, then run `python monitor.py --dry-run` (no Telegram or AI
generation). The old `Start.cmd`/HTML dashboard remains Blocket-only.

## Matching and notifications

- Same PS5 title/fault rules and ceiling **up to and including 4,000 SEK before
  shipping/buyer protection**. Known shipping cost is displayed separately.
- Sellers must explicitly be located in Sweden. Shipping options must be present,
  or pickup must be offered in an exact configured Göteborg city alias. Missing
  country, delivery or active-status information is excluded rather than guessed.
- Fixed-price offers and auctions are enabled in the module's defaults. Set
  `sources.tradera.include_auctions=false` for fixed-price listings only (auction
  listings with an optional Buy Now price are also excluded in that mode).
- Auctions use the **minimum next bid**, not the current leading bid, for the
  ceiling. They show the UTC end time and reserve-price warning when available.
  This is not a guaranteed purchase price; bids can rise after an alert. The
  module does not bid, buy, snipe, remind before closing, or contact sellers.
- All qualifying results checked in a run use the shared English Telegram layout,
  listing-photo albums, AI text assessment, and copyable Swedish seller draft.
  Tradera's feedback summary is shown as the positive percentage of ratings in
  the **last 12 months**, with the count, not an invented five-star seller score.
  It is user feedback, not a guarantee of seller reliability. Unavailable feedback
  does not stop the alert. Images are only taken from the item's API image fields.
- AI limits remain shared: 3 calls/run and 20/day, including both sources.
  Tradera has no market-price verdict yet; auction bids are never mixed into the
  Blocket asking-price reference sample.
- Tradera state uses `tradera:<id>`; existing Blocket IDs/history/cache are retained.
  Unchanged listings and bid increases are not re-sent. New lowest prices or a
  newly available/changed AI assessment can trigger an update as on Blocket.

## Coverage and failure handling

Two queries (`ps5`, `playstation 5`), at most ten pages per query and 24 detail
requests per run. These hard upper bounds leave room below the API's documented
default 10,000 calls/day **per method** at one run per five minutes. Manual or
other applications' calls still consume quota. Requests are paced and the source
has a roughly 100-second time budget; it never retries an access block in a loop.

If more candidates need checking, the least-recently checked are processed first
on later runs. This avoids starving later items, but **does not guarantee every
listing is checked every five minutes**. Page/detail/time limits are reported as
incomplete coverage; live pagination can also shift. State stores only item IDs
and last-check timestamps for rotation, not raw descriptions or seller profiles.

Source errors are isolated: one working marketplace can continue while the other
fails. JSON output and the daily Telegram status identify disabled/failed/incomplete
sources. If none succeeds, the run fails and preserves notification history.
The shared GitHub workflow still has its existing eight-minute overall timeout;
cron dispatch acceptance is not proof of a completed search.

API contracts: [Search](https://api.tradera.com/documentation/searchservice/Search),
[Search WSDL](https://api.tradera.com/v3/SearchService.asmx?WSDL),
[Public WSDL](https://api.tradera.com/v3/PublicService.asmx?WSDL).
Tests use synthetic contract-shaped fixtures; live authenticated behavior still
needs verification with your approved credentials.
