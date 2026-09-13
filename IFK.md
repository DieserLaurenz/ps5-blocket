# IFK Göteborg ticket alerts

The **IFK Tickets** GitHub Actions workflow checks the official club website hourly,
at minute 17. It uses the existing Telegram bot/chat and works while your PC is off.
GitHub scheduling can be delayed; this is an hourly schedule, not an exact-time guarantee.

Sources:

- The public men's season selector on https://ifkgoteborg.se/spelschema/ and its
  `ifk_match_schedule` AJAX response, including the next men's season when published.
- The official WordPress posts API: ticket-related articles modified in the past
  120 days, with pagination. Ticket-release/info headlines must also have men's and
  home-match context. Women's-only and away-match headlines are excluded. Mixed
  articles can be included when they contain men's home-ticket information.

The AXS/ebiljett shop blocks automated access, including the tested Chrome driver.
The user chose the official club site instead. A club ticket link does **not** prove
that seats are available or general sale has started; alerts distinguish links from
announcements and link to the official information. News excerpts remain in Swedish.
This can differ in timing and coverage from the ticket shop. Text classification is
rule-based; ambiguous announcements without men's/home context may be missed.

The first successful run saves the current baseline and sends one setup confirmation.
Existing articles and links are not sent individually. Later runs alert on a new
home-match ticket link, a changed link, a new relevant article, or changed article
text (including subsequently announced sale dates). Date-only fixture changes do not
repeat alerts. A repeated URL/content version is remembered and not sent again.
Fixtures are identified by season, competition and home/away teams; multiple home
friendlies against the same opponent in one season share notification history.

State lives on the separate `ifk-state` branch in `state.json`. It contains hashes,
article IDs, timestamps and a recipient checksum, never tokens or chat IDs. Missing
or corrupt remote state fails explicitly. Notification progress is saved after each
confirmed send. A crash between delivery and persistence can cause a duplicate.
Source failures preserve history, fail the Actions run and send at most one warning
per UTC day during an outage; recovery sends one confirmation. A failure of GitHub
to start runs or of Telegram itself cannot be reported through this mechanism.

Controls:

- Run now: Actions → IFK Tickets → Run workflow.
- Read-only live check: select `dry_run`, or `python ifk_monitor.py --dry-run` locally.
- Pause: set repository variable `IFK_MONITOR_ENABLED=false`, or disable this workflow.
- Resume: set `IFK_MONITOR_ENABLED=true` and enable the workflow.
- Tests: `python -m unittest discover -s tests -v`.

No new Python dependencies or AI service are required. The PS5 workflow, external
five-minute timer, and PS5 notification history are independent of this workflow.
GitHub can disable scheduled workflows in public repositories after 60 days without
repository activity; check the Actions page if hourly runs stop.
