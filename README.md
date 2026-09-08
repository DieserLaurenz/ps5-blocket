# PS5-Angebote auf Blocket

Lokaler Scraper für Windows und Python 3.10+. Keine zusätzlichen Pakete, kein Konto oder API-Schlüssel nötig.

`Start.cmd` doppelklicken: sucht aktuell und öffnet `output/angebote.html` im Browser.
`Monitor.cmd` sucht alle 15 Minuten, solange das Fenster geöffnet ist. Strg+C beendet den Monitor.
Neue Treffer und Preissenkungen erscheinen in der Übersicht und werden im Terminal mit einem Signalton angekündigt; keine E-Mails oder Nachrichten.

Voreinstellung: bis einschließlich **4.000 SEK**, Versand innerhalb Schwedens oder Abholung in **Göteborg**. Versand wird anhand von Blockets `shipping_exists` oder einer ausdrücklichen Versandzusage in der geprüften Beschreibung erkannt. Die Quelle wird pro Treffer angezeigt; unbekannter Versand außerhalb Göteborgs wird ausgeschlossen. Göteborg wird anhand des Ortsnamens erkannt, nicht als Umkreis. Stadtteile, die Blocket separat benennt, können in `pickup_cities` ergänzt werden.

## Einstellungen

`config.json` mit einem Texteditor ändern. `max_price` ist die Suchobergrenze, `deal_price` lediglich deine persönliche Schnäppchen-Markierung (kein ermittelter Marktwert). `detail_limit` bestimmt, für wie viele der günstigsten passenden Treffer die Beschreibung zusätzlich geprüft wird. Die Preisgrenze enthält keine Versandkosten oder Käuferschutzgebühren.

```powershell
python scraper.py --max-price 3500 --open
python scraper.py --all-categories --open
python scraper.py --watch 900
python scraper.py --recheck-cache
python -m unittest discover -s tests -v
```

Die Suche kombiniert `ps5` und `playstation 5`, sortiert nach Preis und entfernt doppelte Anzeigen. Standardmäßig werden alle Kategorien durchsucht, um auch falsch kategorisierte Konsolen zu finden; ein Durchlauf kann einige Minuten dauern. Mit `"all_categories": false` in der Konfiguration kann die Suche auf „Spelkonsoler“ begrenzt werden. Das Seitenlimit gilt pro Suchbegriff und wird bei Erreichen sichtbar gemeldet. Neue Anzeigen während eines Durchlaufs können die Seitensortierung verändern; es ist keine garantierte Vollerfassung.

Zubehör, Spiele, Portal, Tauschgesuche und offensichtliche Defekte werden durch Textregeln ausgefiltert. Zusätzlich muss der Titel auf eine Konsole hindeuten: etwa „PS5“, „PS5 Slim“ oder „PlayStation 5 spelkonsol“. Unklare Titel und Titel mit mehreren PlayStation-Generationen werden vorsichtig ausgeschlossen. Bundles mit Konsolen und Controllern bleiben grundsätzlich erhalten. Ein Textfilter kann sowohl Fehlalarme als auch übersehene Probleme verursachen; „Beschreibung geprüft“ ist keine Bestätigung der Funktionsfähigkeit oder Seriosität. Ausgeschlossene Titel mit Begründung stehen im Dashboard und JSON. Versandangebote mit ausdrücklich widersprechender Beschreibung werden ausgeschlossen, außer eine Abholung in Göteborg passt.

## Dateien und Aktualität

- `output/angebote.html`: Übersicht mit Filtern, Bildern und direkten Links.
- `output/angebote.csv`: Excel-kompatible Tabelle (UTF-8, Semikolon).
- `output/angebote.json`: Treffer, Suchumfang, Ausschlüsse und Abrufzeit.
- `output/history.json`: zuletzt beobachteter Preis und erster/letzter Fund je Anzeige.
- `output/last-search.json`: letzte erfolgreich geladene Suchdaten zur Fehlersuche, vor dem Konsolenfilter.

„Neu“ bedeutet erstmals lokal gesehen, nicht Veröffentlichungszeitpunkt. Beim ersten Durchlauf sind alle Treffer neu. Eine Preissenkung bezieht sich auf den zuletzt beobachteten Preis. Alte Historieneinträge belegen keine aktuelle Verfügbarkeit. Ein fehlgeschlagener Abruf stoppt auch den Monitor und lässt den bisherigen Bericht bestehen; dessen Abrufzeit bleibt sichtbar. Kein automatischer Wiederholungsversuch bei Sperren oder Ratenlimits.

Gespeicherte Blocket-Suchseiten können ohne Netzwerk ausgewertet werden:

```powershell
python scraper.py --import-html sample-consoles.html sample-page2.html --output output-import
```

Ein Import wird als solcher markiert und verändert die Live-Historie nicht. Die HTML-Dateien müssen die eingebetteten `data-react-query-state`-Suchdaten enthalten. Änderungen an Blockets Seitenformat können Anpassungen am Parser erfordern.

`--recheck-cache` filtert die zuletzt abgerufenen Suchdaten erneut und fragt die Beschreibungen nochmals ab. Die Übersicht zeigt weiterhin den ursprünglichen Abrufzeitpunkt der Suchdaten und einen Cache-Hinweis. Neue Suchfilter, Suchbegriffe oder eine höhere Preisgrenze benötigen einen vollständigen neuen Durchlauf.

Blocket weist in seiner [robots.txt](https://www.blocket.se/robots.txt) darauf hin, dass automatisiertes Crawling eine schriftliche Genehmigung voraussetzt. Dies vor regelmäßiger Nutzung klären. Der Scraper verwendet öffentliche Seiten, zeitlich begrenzte Anfragen mit Abstand und keine Umgehung von Zugriffssperren.
