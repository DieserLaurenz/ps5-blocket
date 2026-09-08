# PS5 deals on Blocket

The remote search service runs on [GitHub Actions](https://github.com/DieserLaurenz/ps5-blocket/actions/workflows/monitor.yml), triggered every five minutes by an external cron-job.org job. New matching listings, new price lows and a daily status message go to the connected Telegram chat. External triggers appear as `workflow_dispatch`, just like manual tests; verify recurrence using cron-job.org execution history and GitHub run timestamps. Setup, costs and controls: [REMOTE.md](REMOTE.md).

Telegram offers include an English AI text assessment and, when enough comparable listings are available, a calculated price comparison. Run `Setup-AI.cmd` to configure AI; `Setup-KI.cmd` remains a compatibility shortcut. The scraper continues sending matching listings when AI is unavailable. Assessments are cached and regenerated when the listing text or assessment prompt changes.

Telegram messages use bold prices/headings and emojis for model, condition, shipping/pickup and location. Included items, unknowns and questions have separate sections. Missing information is explicitly marked. Under “Run workflow”, enable `preview_format` to send one current listing as a format preview without changing notification history. Original seller titles and descriptions remain in Swedish; AI summaries and questions are in English.

Each offer alert also includes a copyable Swedish seller-message draft asking the listed open questions. It is generated and cached with the existing assessment, without a separate AI request. If AI is unavailable, a clearly labelled general Swedish message is provided instead. Review and send the draft yourself on Blocket; the scraper never contacts sellers.

## Local use

Windows and Python 3.10+. No additional packages, account or API key are needed for the basic local scraper.

Double-click `Start.cmd` to search and open `output/angebote.html` in your browser.
`Monitor.cmd` searches every 15 minutes while its window stays open; Ctrl+C stops it.
New matches and price drops appear in the local dashboard and trigger a terminal beep. Telegram runs separately through the remote service and does not require your PC to stay on.

Defaults: up to and including **4,000 SEK**, shipping within Sweden or pickup in **Göteborg**. Shipping is detected through Blocket's `shipping_exists` flag or an explicit shipping offer in a checked description. Each result shows its shipping evidence. Unknown shipping outside Göteborg is excluded. Pickup uses exact city-name matching, not a radius; separately named districts can be added to `pickup_cities`.

## Settings

Edit `config.json`. `max_price` is the search ceiling; `deal_price` is only your personal bargain highlight, not an estimated market value. `detail_limit` controls how many of the cheapest plausible matches get an additional description check. The price ceiling excludes shipping and buyer protection fees.

```powershell
python scraper.py --max-price 3500 --open
python scraper.py --all-categories --open
python scraper.py --watch 900
python scraper.py --recheck-cache
python -m unittest discover -s tests -v
```

The search combines `ps5` and `playstation 5`, sorts by price and deduplicates listings. All categories are searched by default to include miscategorized consoles; a run may take several minutes. Set `"all_categories": false` to restrict the search to “Spelkonsoler” (game consoles). The page limit applies per search term and produces a warning when reached. New listings can shift pagination during a run, so complete coverage is not guaranteed.

Text rules exclude accessories, games, Portal devices, trades/wanted ads and obvious faults. Titles must also clearly indicate a console, such as “PS5”, “PS5 Slim” or “PlayStation 5 spelkonsol”. Ambiguous titles and titles mentioning multiple PlayStation generations are conservatively excluded. Console/controller bundles generally remain eligible. Text filters can both miss genuine deals and overlook problems; “Description checked” does not verify functionality or seller reliability. Excluded titles and reasons appear in the dashboard and JSON. Listings whose descriptions explicitly rule out shipping are excluded unless pickup in Göteborg qualifies.

## Files and freshness

Existing report filenames are retained for compatibility with saved links and scripts:

- `output/angebote.html`: dashboard with filters, images and direct links.
- `output/angebote.csv`: Excel-compatible table (UTF-8, semicolon-separated).
- `output/angebote.json`: matches, search coverage, exclusions and fetch time.
- `output/history.json`: last observed price and first/last sighting per listing.
- `output/last-search.json`: last successfully fetched search data before console filtering, for diagnostics.

“New” means first seen locally, not the publication time. All matches are new on the first run. Local price drops are relative to the last observed price. Old history entries do not prove current availability. A failed fetch stops the local monitor and preserves the previous report with its original timestamp. There are no automatic retries for blocks or rate limits.

Saved Blocket search pages can be parsed offline:

```powershell
python scraper.py --import-html sample-consoles.html sample-page2.html --output output-import
```

Imports are labelled and do not change live history. HTML files must contain embedded `data-react-query-state` search data. Changes to Blocket's page format may require parser updates.

`--recheck-cache` re-filters the last search data and fetches descriptions again. The dashboard retains the original search-data timestamp and shows a cache notice. Changed search terms, filters or a higher price ceiling require a fresh full run.

Blocket states in its [robots.txt](https://www.blocket.se/robots.txt) that automated crawling requires written permission. Resolve this before regular use. The scraper uses public pages, spaced requests with timeouts and no access-block bypasses.
