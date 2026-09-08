# Remote-Suche mit Telegram

Repository: https://github.com/DieserLaurenz/ps5-blocket

## Aktivieren

`Setup-Telegram.cmd` auf deinem PC doppelklicken. Der Assistent fragt den Bot-Token unsichtbar ab, prüft ihn und zeigt eine einmalige Verbindungsnachricht. Diese Nachricht im privaten Chat mit deinem Bot senden. Dadurch wird die richtige Chat-ID ermittelt. Anschließend speichert der Assistent beide Werte als GitHub Actions Secrets, sendet eine Testnachricht, aktiviert den Zeitplan und startet sofort einen Suchlauf. Der Token wird nicht lokal gespeichert und steht nicht in der Kommandozeile oder im Repository.

Bei BotFather `/newbot` verwenden, wenn du noch keinen eigenen Bot hast. BotFather ist lediglich die Bot-Verwaltung. Für dieses Projekt einen eigenen Bot ohne Webhook verwenden. Alternativ können `TELEGRAM_BOT_TOKEN` und `TELEGRAM_CHAT_ID` in den Repository-Secrets sowie `PS5_MONITOR_ENABLED=true` als Repository-Variable manuell angelegt werden.

## Verhalten

- Zeitplan: `2-59/5 * * * *`, also Minute 2, 7, 12 usw., rund um die Uhr.
- Jeder Lauf durchsucht beide Suchbegriffe in allen Kategorien bis 4.000 SEK. Versand in Schweden oder Abholung Göteborg; Einstellungen in `config.json`.
- Beim ersten Lauf eine Nachricht pro passender Konsole, danach nur neue Anzeigen oder niedrigere Preise als bereits gemeldet. Preisbewegungen nach oben und zurück lösen keine Wiederholungen aus.
- Eine tägliche Statusmeldung bestätigt die erfolgreiche Suche. Bleibt sie aus, die Actions-Seite prüfen.
- Bestätigte Meldungen werden einzeln auf Branch `monitor-state` gespeichert. Dieser öffentliche Status enthält Anzeigen-IDs, Preise, Zeitstempel, eine Empfänger-Prüfsumme sowie die nachfolgend beschriebenen Bewertungs- und Vergleichsdaten, aber keine Chat-ID oder Tokens.
- Bei einem Absturz genau zwischen Telegram-Zustellung und Speicherung kann eine Meldung doppelt erscheinen. Exakt einmalige Zustellung über zwei unabhängige Dienste ist nicht garantiert.
- Der Workflow läuft höchstens acht Minuten; Läufe überschneiden sich nicht. Bei Ausfällen geht die Meldehistorie nicht verloren. Fehler erscheinen in GitHub Actions; ein späterer Zeitplanlauf versucht es erneut. Keine Umgehung von Blocket-Sperren.
- Es werden keine Actions-Artefakte oder Actions-Caches gespeichert. Der Status erhält bei Änderungen einen Commit; identische Daten verursachen keinen neuen Commit.

## Angebotsbewertung

`Setup-KI.cmd` verbindet einen [Gemini-API-Key](https://aistudio.google.com/api-keys) als GitHub-Secret `GEMINI_API_KEY`. Für kostenlosen Betrieb einen Free-Tier-Schlüssel aus einem Projekt ohne aktivierte Abrechnung verwenden. Das Setup aktiviert keine Abrechnung. Der Key wird verdeckt eingegeben, nicht lokal gespeichert und nicht als Kommandozeilenargument übergeben. Gemini 2.5 Flash-Lite bietet derzeit einen [Free Tier](https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-flash-lite); verfügbare Kontingente sind projektspezifisch und können sich ändern.

Das Modell erhält nur den gekürzten Titel und Beschreibungstext. Erkennbare URLs, E-Mail-Adressen und Telefonnummern werden vorher entfernt. Keine Verkäuferprofile, Bilder, Chat-Nachrichten oder Telegram-Zugangsdaten werden an Google gesendet. Es extrahiert Modellvariante, Lieferumfang, genannte Mängel, Informationslücken und Nachfragen auf Deutsch. Verkäuferangaben sind nicht verifiziert; KI-Ergebnisse können Fehler enthalten. Es gibt keinen automatischen Kauf und keine vom Modell aufrufbaren Tools.

Die KI bewertet keine Marktpreise aus ihrem Gedächtnis. Stattdessen sammelt der Scraper alle sechs Stunden eine getrennte Vergleichsstichprobe in der Kategorie „Spelkonsoler“ von 1.500 bis 10.000 SEK. Die Meldegrenze bleibt 4.000 SEK. Verglichen werden nur im Titel erkennbare gleiche Generationen und Disc/Digital-Varianten; erkennbare Bundles und der bewertete Artikel selbst sind ausgeschlossen. Mindestens fünf Vergleichsanzeigen sind erforderlich. Der Median und die Abweichung werden ausgewiesen; ab 10 % unter dem Median heißt die Einordnung „Preislich interessant“. Unbekannte Varianten, zu kleine Stichproben, über 24 Stunden alte oder durch das Seitenlimit abgeschnittene Vergleiche ergeben ausdrücklich kein Preisurteil. Die Stichprobe erfasst verlangte Preise, keine erzielten Verkaufspreise; Zustand, Zubehör, Versand und mögliche Fehlklassifizierungen bleiben Einschränkungen.

Die KI-Bewertung ist rein ergänzend und entfernt keine zuvor passenden Treffer. Ohne Key, bei einem API-Ausfall oder ausgeschöpftem Kontingent gehen die normalen Meldungen mit einem Hinweis trotzdem raus. Bereits gemeldete Angebote bekommen einmal eine Ergänzung, sobald die KI-Bewertung erstmals verfügbar ist oder sich der ausgewertete Text geändert hat. Die bisher niedrigste gemeldete Preisschwelle bleibt erhalten.

Effizienz: Ergebnisse werden anhand von Titel/Beschreibung, Modell und Prompt-Version gespeichert; Preisänderungen allein lösen keinen neuen KI-Aufruf aus. Maximal 3 Aufrufe pro Lauf und 20 pro UTC-Tag, inklusive fehlgeschlagener Versuche. Bei 429/403/401 pausiert die KI sechs Stunden, bei anderen Fehlern mindestens 15 Minuten. Preisvergleich und Scraper laufen weiter. Der öffentliche Status speichert höchstens 200 kompakte KI-Auswertungen mit Texthashes (keine Rohbeschreibungen), einen Aufrufzähler und die Vergleichsstichprobe. Einstellungen stehen unter `assessment` in `config.json`; mit `enabled: false` kann die Bewertung ausgeschaltet werden.

## Kosten und Zeitplan

Standard-GitHub-Runner in öffentlichen Repositories sind laut [GitHub-Abrechnung](https://docs.github.com/en/billing/concepts/product-billing/github-actions) kostenlos. Dieser Workflow führt in privaten Repositories keine Jobs aus. Telegram-Bot-Nachrichten in diesem Umfang sind [kostenlos](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

GitHub bietet [keine Garantie für einen exakten Fünf-Minuten-Takt](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule): Läufe können verspätet starten oder ausfallen. Öffentliche Zeitpläne können nach 60 Tagen ohne Repository-Aktivität deaktiviert werden. Dies ist keine dauerhaft garantierte Hosting-Zusage; die Plattformbedingungen gelten. Ein eigener bereits vorhandener Server mit Cron wäre zeitlich besser steuerbar, setzt aber entsprechende Hardware/Hosting voraus.

## Bedienen

- Sofort suchen: GitHub → Actions → PS5 Suche → Run workflow.
- Erst ohne Nachrichten testen: dabei `dry_run` aktivieren.
- Ausschalten: Actions → PS5 Suche → Menü → Disable workflow; alternativ `PS5_MONITOR_ENABLED=false` setzen.
- Filter ändern: `config.json` auf Branch `main` bearbeiten.
- Token wechseln: `Setup-Telegram.cmd` erneut ausführen.
- Ohne GitHub auf einem vorhandenen Linux-Server: Secrets als Umgebungsvariablen setzen und mit Cron `*/5 * * * * flock -n /tmp/ps5-blocket.lock /usr/bin/python3 /pfad/ps5-blocket/monitor.py` starten. Der lokale Status liegt dann in `output/notifications.json`.

Vor Aktivierung müssen die in der README genannten Blocket-Nutzungsbedingungen berücksichtigt werden. Ob Blocket Zugriffe aus GitHub-Rechenzentren akzeptiert, wird durch den Remote-Test geprüft und kann sich ändern.
