# Remote search with Telegram

Repository: https://github.com/DieserLaurenz/ps5-blocket

## Enable

Double-click `Setup-Telegram.cmd` on your PC. The wizard reads the bot token through hidden input, verifies it and displays a one-time connection message. Send that message in a private chat with your bot to identify the correct chat ID. It then stores both values as GitHub Actions secrets, sends a test message, enables the schedule and starts a search immediately. The token is not saved locally or placed in command-line arguments or the repository.

Use `/newbot` with BotFather if you do not have a bot yet. BotFather manages bots; use a separate bot without an active webhook for this project. Alternatively, manually create repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, and repository variable `PS5_MONITOR_ENABLED=true`.

## Behaviour

- Schedule: `3,8,13,18,23,28,33,38,43,48,53,58 * * * *` — minutes 3, 8, 13, etc., around the clock. Check the Actions history for automatic `schedule` events; successful manual runs alone do not verify the scheduler.
- Each run searches both terms across all categories up to 4,000 SEK. Shipping within Sweden or pickup in Göteborg; settings are in `config.json`.
- The first run sends one message per matching console. Later runs alert on new listings or prices below the lowest previously alerted price. A price going up and back down does not trigger a duplicate.
- A daily status message confirms a successful search. If it stops arriving, check the Actions page.
- Confirmed sends are persisted individually on branch `monitor-state`. This public state contains listing IDs, prices, timestamps, a recipient checksum and the assessment/reference data described below, but no chat ID or tokens.
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

GitHub [does not guarantee an exact five-minute interval](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule): runs can start late or be dropped. Public schedules may be disabled after 60 days without repository activity. This is not a permanent hosting guarantee; platform terms apply. Cron on an existing server offers more control over timing but requires hardware/hosting.

## Controls

- Search now: GitHub → Actions → PS5 Search → Run workflow.
- Test without messages: enable `dry_run`.
- Preview formatting: enable `preview_format` to send one current listing without changing alert history.
- Disable: Actions → PS5 Search → menu → Disable workflow, or set `PS5_MONITOR_ENABLED=false`.
- Change filters: edit `config.json` on branch `main`.
- Change Telegram token: run `Setup-Telegram.cmd` again.
- Without GitHub, on an existing Linux server: set secrets as environment variables and schedule `*/5 * * * * flock -n /tmp/ps5-blocket.lock /usr/bin/python3 /path/ps5-blocket/monitor.py` with cron. Local notification state is stored in `output/notifications.json`.

Before activation, consider the Blocket access requirements noted in the README. Whether Blocket accepts requests from GitHub data centres is checked by a remote run and can change.
