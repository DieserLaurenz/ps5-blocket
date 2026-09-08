# Remote search with Telegram

Repository: https://github.com/DieserLaurenz/ps5-blocket

## Enable

Double-click `Setup-Telegram.cmd` on your PC. The wizard reads the bot token through hidden input, verifies it and displays a one-time connection message. Send that message in a private chat with your bot to identify the correct chat ID. It then stores both values as GitHub Actions secrets, sends a test message, enables the workflow and starts a search immediately. Recurring execution is configured separately on cron-job.org as described below. The token is not saved locally or placed in command-line arguments or the repository.

Use `/newbot` with BotFather if you do not have a bot yet. BotFather manages bots; use a separate bot without an active webhook for this project. Alternatively, manually create repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, and repository variable `PS5_MONITOR_ENABLED=true`.

## Behaviour

- Schedule: an external cron-job.org job triggers the workflow every five minutes, around the clock. GitHub's built-in `schedule` trigger is removed to avoid duplicate searches. External triggers appear as `workflow_dispatch`; correlate cron-job.org history with GitHub run timestamps to distinguish them from manual tests.
- Each run searches both terms across all categories up to 4,000 SEK. Shipping within Sweden or pickup in Göteborg; settings are in `config.json`.
- The first run sends one message per matching console. Later runs alert on new listings or prices below the lowest previously alerted price. A price going up and back down does not trigger a duplicate.
- A daily status message confirms a successful search. If it stops arriving, check the Actions page.
- Confirmed sends are persisted individually on branch `monitor-state`. This public state contains listing IDs, prices, timestamps, a recipient checksum, Telegram message IDs, public listing-photo URLs and delivery progress, and the assessment/reference data described below, but no chat ID or tokens.
- A crash between Telegram delivery and state persistence can cause a duplicate. Exactly-once delivery across two independent services is not guaranteed.
- Each workflow has an eight-minute timeout; runs do not overlap. Failures preserve notification history and appear in GitHub Actions. A later scheduled run can retry. Blocket access blocks are not bypassed.
- No Actions artifacts or Actions caches are uploaded. State changes create commits; identical state does not.
- Alerts, AI assessments, setup prompts and dashboard controls are in English. Original seller text is preserved. Old German AI assessments are regenerated under the normal API budget when their listings next qualify.

## Offer assessment

`Setup-AI.cmd` (or the existing `Setup-KI.cmd` shortcut) stores a [Gemini API key](https://aistudio.google.com/api-keys) as GitHub secret `GEMINI_API_KEY`. For free operation, use a free-tier project without billing enabled. Setup does not enable billing. The key is entered invisibly, not stored locally and not passed as a command-line argument. See the [Gemini pricing page](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-lite) for Gemini 3.1 Flash-Lite availability and free-tier terms; quotas vary by project and can change.

The model receives only the shortened title and description. Recognizable URLs, email addresses and phone numbers are removed first. Seller profiles, images, chat messages and Telegram credentials are not sent to Google. It extracts model variant, included items, stated faults, information gaps and questions in English. Seller claims are unverified and AI output can be wrong. There is no automatic purchase and the model cannot call tools.

The AI does not estimate market prices from memory. Instead, every six hours the scraper gathers a separate reference sample in “Spelkonsoler” from 1,500 to 10,000 SEK. The alert ceiling remains 4,000 SEK. Comparisons require the same identifiable generation and Disc/Digital edition; recognizable bundles and the assessed listing itself are excluded. At least five reference listings are required. The median and percentage difference are shown; 10% or more below the median is labelled “Attractive asking price”. Unknown variants, small samples, samples older than 24 hours or searches truncated by the page limit produce no price verdict. These are asking prices, not completed sales. Condition, accessories, shipping and possible classification errors remain limitations.

AI assessment is supplementary and never removes a qualifying match. Without a key, during an API outage or when quota is exhausted, normal alerts still go out with a notice. Previously alerted listings receive an update when an assessment first becomes available or its text/prompt changes. The lowest alerted price is preserved.

Efficiency: cache keys include title/description, model and prompt version; price-only changes do not trigger another AI request. Limits are 3 attempts per run and 20 per UTC day, including failed attempts. HTTP 429/403/401 pauses AI for six hours; other errors pause it for at least 15 minutes. Search and price comparison continue. Public state stores at most 200 compact assessments with text hashes (not raw descriptions), an attempt counter and the reference sample. Configure these under `assessment` in `config.json`; set `enabled: false` to disable assessment.

## Costs and scheduling

Standard GitHub-hosted runners in public repositories are [free](https://docs.github.com/en/billing/concepts/product-billing/github-actions). This workflow skips jobs in private repositories. Telegram bot messages at this volume are [free](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

The external timer replaces GitHub's built-in scheduler, not its runners. Requests and queued workflow starts can still be delayed. cron-job.org is [free but does not guarantee exact timing](https://cron-job.org/en/faq/); platform terms apply. A successful trigger response only confirms that GitHub accepted the request, not that the scraper completed. Check Actions results and the daily Telegram status too.

## External cron setup

Create a fine-grained GitHub token restricted to `DieserLaurenz/ps5-blocket` with **Actions: read and write** permission. Give it an expiry date and a renewal reminder. Do not use a broad account token or share the token in chat, screenshots, source files or URL parameters.

On cron-job.org, create an enabled job with an **every five minutes** schedule and the following request:

```text
POST https://api.github.com/repos/DieserLaurenz/ps5-blocket/actions/workflows/monitor.yml/dispatches
Authorization: Bearer YOUR_GITHUB_TOKEN
Accept: application/vnd.github+json
Content-Type: application/json
X-GitHub-Api-Version: 2026-03-10
```

Request body:

```json
{"ref":"main"}
```

The scheduler stores this restricted GitHub token; Telegram and Gemini keys stay in GitHub secrets. Enable failure notifications. When the token expires, replace it in the Authorization header. Test once, then verify recurring runs without clicking Test run again. An expired token, disabled cron job or failed trigger requires checking cron-job.org, not just GitHub's run history.

## Controls

- Search now: GitHub → Actions → PS5 Search → Run workflow.
- Test without messages: enable `dry_run`.
- Preview formatting: enable `preview_format` to send one current listing without changing alert history.
- Pause recurring searches: disable the cron-job.org job. To stop all new workflow runs, also use Actions → PS5 Search → menu → Disable workflow. Keep the workflow enabled during normal external scheduling.
- `PS5_MONITOR_ENABLED=false` does not block `workflow_dispatch` requests and is not a pause switch for the external cron job.
- Change filters: edit `config.json` on branch `main`.
- Change Telegram token: run `Setup-Telegram.cmd` again.
- Without GitHub, on an existing Linux server: set secrets as environment variables and schedule `*/5 * * * * flock -n /tmp/ps5-blocket.lock /usr/bin/python3 /path/ps5-blocket/monitor.py` with cron. Local notification state is stored in `output/notifications.json`.

Before activation, consider the Blocket access requirements noted in the README. Whether Blocket accepts requests from GitHub data centres is checked by a remote run and can change.
