# Vinted-Monitoring-Bot – Umsetzungsplan (Entwurf v1)

Stand der Recherche: **14.10.2026**. Noch kein Code, zuerst muss der Plan freigegeben werden.
Offene Fragen stehen in Abschnitt 10. Sobald sie beantwortet sind, wird der Plan finalisiert (v2).

---

## 0. Kurzfassung (TL;DR)

- **Technisch machbar, aber Vinted hat seine Such-API vor vier Wochen umgebaut.** Der alte, überall dokumentierte Endpunkt `www.vinted.xx/api/v2/catalog/items` liefert seit ca. **15.09.2026** nur noch 404. Neu ist `https://api.vinted.xx/svc-catalogue/items`, mit anonymem Session-Token (`access_token_web`-Cookie) und `X-Anon-Id`-Header. Die meisten Bots auf GitHub sind deshalb gerade kaputt. Nur wenige Projekte haben schon migriert.
- **Größtes Risiko ist die IP-Reputation, nicht der Code.** Vinted steckt hinter Cloudflare und laut mehreren (kommerziellen, also mit Vorsicht zu lesenden) Quellen auch hinter DataDome. Rechenzentrums-IPs (VPS) werden oft sofort blockiert, private Internetanschlüsse deutlich seltener. Das „Reparieren“ einer blockierten VPS-IP ginge nur mit Residential-Proxys oder TLS-Fingerprint-Imitation. Beides schließen deine Leitplanken aus, **also empfehle ich Betrieb zu Hause** (Raspberry Pi, alter Laptop oder Mini-PC).
- **Eine offene Leitplanken-Frage ist kritisch:** Welchen User-Agent der Bot sendet. Ein ehrlicher Bot-UA wird vermutlich blockiert. Ein statischer Browser-UA ist branchenüblich, aber genau genommen eine „geliehene Identität“. Das musst du entscheiden (Frage F1). Meilenstein M0 testet es vorab mit 2–3 Requests.
- **Nicht alle Vintrez-Felder sind ohne Zusatzanfragen verfügbar.** Die Suchantwort enthält Titel, Preis, Gesamtpreis inkl. Käuferschutz, Marke, Größe, Zustand, Verkäufer und Hauptfoto. Beschreibung, weitere Fotos und eventuell die Verkäuferbewertung brauchen pro Artikel einen Zusatz-Request (Artikelseite). Der JSON-Detail-Endpunkt ist laut vinted_scraper dauerhaft mit 403 geschützt. Details in Abschnitt 4.
- **Empfohlenes Intervall: 60 s pro Filter** (Minimum 30 s), dazu ein **globaler Abstand von ≥ 5 s zwischen zwei beliebigen Vinted-Requests**. Schneller bringt kaum etwas, weil Vinted neue Inserate teils erst nach einer automatischen Prüfung (laut Vinted „etwa 15 bis 30 Minuten“ in manchen Fällen) im Katalog zeigt.
- **Rechtlich:** Vinteds AGB verbieten Bots, Scraper und Crawler ausdrücklich. Für dich ist das primär ein **Vertrags-/Hausrechtsrisiko** (IP-Sperre, ggf. Kontosperre), kein Strafrechtsrisiko, solange **keine Schutzmaßnahme umgangen** wird (§ 118a StGB). Genau deshalb sind die Leitplanken wichtig. Keine Rechtsberatung, siehe Abschnitt 1.3.

---

## 0a. Ergebnis der M0-Live-Probe (15.10.2026, Heimanschluss, 6 Requests)

Filter: vinted.at, Katalog 79, Marken 88 + 4273, Preis bis 40 €.

| Prüfpunkt | Ergebnis | Folge für den Plan |
|---|---|---|
| Session-Bootstrap über die Startseite | HTTP 200, `access_token_web` und `X-Anon-Id` vorhanden. Weitere Cookies: `refresh_token_web`, `anon_id`, `v_udt` | Protokoll wie in 1.1 bestätigt |
| `api.vinted.at/svc-catalogue/items` | HTTP 200, JSON mit `items` (20), `pagination` (`total_entries`, `time` = Serverzeit) | bestätigt |
| **Ehrlicher User-Agent** | **funktioniert**, ebenso der Browser-UA | **F1 entschieden: ehrlicher UA** (Standard in `config.py`). Kein Browser-UA nötig |
| Schutz | Cloudflare **und DataDome** aktiv (DataDome-Cookie auf der Startseite) | R1 bleibt real, zurückhaltende Rate ist Pflicht |
| Filter angewendet? | Alle 5 Stichproben passen (Marke, Preis ≤ 40 €) | Mapping auf `attribute_ids[...]` korrekt |
| Marke / Größe / Zustand | `brand_title`, `size_title`, `status` **fehlen**. Stattdessen: `item_box.first_line` = Marke, `item_box.second_line` = „M · Sehr gut“, `item_box.accessibility_label` = „…, Marke: X, Zustand: Y, Größe: Z, 35.00 €, 37.45 €“ | Parser liest Marke aus `first_line`, Größe und Zustand aus dem Label (Schlüsselwörter), mit `second_line` als Fallback |
| Preise | `price`, `total_item_price`, `service_fee` vorhanden | wie geplant |
| **Zeitstempel** | **keiner**: `photo.high_resolution` enthält nur noch `id`/`orientation`, es gibt kein `created_at` | „Aktualisiert“ = Zeitpunkt, zu dem der Bot den Artikel zuerst sah. Frische-Fenster entfällt, Ersatz siehe 3.3 |
| Fotos | `photos[]` enthält **nur 1 Foto** pro Artikel, `photo.thumbnails` in 6 Größen | Galerie mit 2–3 Bildern **nur mit Enrichment (M7)** |
| Verkäufer | nur `user.id`, `user.login`, `user.business`. **Keine** Bewertung, kein Avatar, kein Land | Feld „Bewertung“ und Länderflagge entfallen ohne Enrichment. `business` wird als „Pro“-Hinweis gezeigt |
| Herkunft der Artikel | Titel auf Französisch und Italienisch: vinted.at zeigt einen **internationalen Katalog** | Eine Domain-Flagge (🇦🇹) wäre irreführend, deshalb keine Flagge |
| Artikel-URL | relativ mit Slug: `/items/<id>-<slug>` | mit Domain zusammensetzen |
| Weiteres | `promoted`, `favourite_count`, `view_count`, `is_favourite`, `content_source` | Likes können in den Footer |
| Reihenfolge | „newest_first“, aber IDs sind **nicht streng absteigend** | Dedupe über ID-Menge (wie geplant), dazu ein ID-Wasserstand |

### Zweite Probe (15.10.2026, 6 Requests) – Länder, Verkäufer, Kauf-Link

| Prüfpunkt | Ergebnis | Folge |
|---|---|---|
| Kauf-Link `/transaction/buy/new?...` | **„Page not found“** (von dir im Browser geprüft). Die Artikelseite enthält keinen Kauf- oder Checkout-Link, der Kauf startet per JavaScript im eingeloggten Zustand | **Kein Kauf-Deeplink möglich.** Buttons: „Ansehen“ (Artikel) + „Verkäufer“ (Profil). Vintrez' Kauf-Button setzt vermutlich ein verknüpftes Konto voraus, das ist durch die Leitplanken ausgeschlossen |
| `GET /api/v2/countries` | 200, 27 Länder mit `id`, `iso_code`, `title` (z. B. AT = 4, DE = 2, IT = 18, FR = 16) | für Validierung und Anzeige der Länderkürzel |
| Suchparameter `country_ids=4` | 200, aber **gleiche Trefferzahl (960)** wie ohne | **serverseitiger Länderfilter wirkt nicht** → der Bot filtert selbst |
| `GET /api/v2/users/{id}` (anonym) | 200 mit `country_iso_code`, `feedback_reputation` (0..1), `feedback_count`, `photo`, `business` u. v. m. | **Land, Sterne und Avatar sind machbar**: 1 Request pro neuem Verkäufer, Cache in SQLite. Der Endpunkt liefert auch sensible Felder (Klarname, Stadt …). Gespeichert wird **nur** Land, Bewertung, Anzahl und Avatar-URL |

### Neue Anforderungen (15.10.2026)

- **Mehrere Filter gleichzeitig** (z. B. „Ralph Lauren Pullover“ + „Ralph Lauren Jacken“): war bereits vorgesehen. Jeder Filter hat seinen eigenen Poller und standardmäßig einen eigenen Kanal. Optional können mehrere Filter in einen gemeinsamen Kanal posten. Ein Artikel, der zu zwei Filtern passt, erscheint in beiden Kanälen.
- **Länderfilter pro Filter** mit Kürzeln wie `at,de,it,fr`: wird beim Anlegen angegeben, leer = alle Länder. Umsetzung: Nur für **neue** Artikel (nicht für die Baseline) wird das Verkäuferland nachgeschlagen (Cache 30 Tage) und dann gefiltert. Ist das Land nicht ermittelbar, wird trotzdem gepostet, mit „❔“ markiert, und es gibt eine Log-Warnung, damit nichts verloren geht.
- **Last-Abschätzung:** Pro neuem, noch unbekanntem Verkäufer kommt 1 Request hinzu. Er läuft über denselben globalen Rate-Limiter (≥ 5 s Abstand). Bei vielen Treffern verzögern sich Posts daher um einige Sekunden, die Last bleibt aber gedeckelt.

---

## 1. Rechercheergebnisse

### 1.1 Wie die Vinted-Webseite heute Katalogdaten lädt

**Ablauf (Stand Mitte Sept. 2026, aus Quellcode von vinted_scraper 5.0.x rekonstruiert):**

1. **Session-Bootstrap:** `GET https://www.vinted.<tld>/` (normale Startseite, Browser-ähnliche `Accept`-Header).
   Die Antwort setzt u. a. das Cookie **`access_token_web`** (anonymes Token, kein Login) und liefert den Header **`X-Anon-Id`**. Optional steckt ein CSRF-Token im HTML. Laut vinted_scraper wird er aktuell **nicht** benötigt.
2. **Katalogabfrage:** `GET https://api.vinted.<tld>/svc-catalogue/items?...`
   - Header: `Cookie: access_token_web=…` (bzw. in manchen Implementierungen `Authorization: Bearer <token>`), `X-Anon-Id: …`, `Accept: application/json`, `Accept-Language: de-AT,…`, `Origin/Referer: https://www.vinted.<tld>`.
   - **401** → Token abgelaufen → neu bootstrappen. **403** → Client/IP wird abgewiesen. **429** → zu schnell. **400 `INVALID_REQUEST`** → leere Parameter mitgeschickt.
3. Der **alte** Endpunkt `www.vinted.<tld>/api/v2/catalog/items` antwortet seit ~15.09.2026 mit **404**. Der JSON-Detail-Endpunkt `/api/v2/items/{id}/details` liefert schon länger **403** (Anti-Bot).

**Parameter-Mapping Filter-Link → API (geändert seit Sept. 2026):**

| Im Browser-Link (`vinted.at/catalog?...`) | Neuer API-Parameter | Anmerkung |
|---|---|---|
| `search_text` | `search_text` | `+` als Leerzeichen erhalten |
| `catalog[]` / `catalog_ids` / Pfad `/catalog/123-...` | `attribute_ids[catalog]` | kommagetrennt |
| `brand_ids[]` / Pfad `/brand/53-...` | `attribute_ids[brand]` | |
| `size_ids[]` | `attribute_ids[size]` | |
| `status_ids[]` (Zustand) | `attribute_ids[status]` | |
| `color_ids[]`, `material_ids[]` | `attribute_ids[color]`, `attribute_ids[material]` | lt. PR bestätigt |
| `price_from`, `price_to`, `currency` | unverändert | **leere Werte weglassen**, sonst 400 |
| `country_ids`, `city_ids` | unverändert (alte Namen!) | `attribute_ids[country]` matcht nichts |
| `order` | `order=newest_first` | wird von uns **immer erzwungen** |
| `time`, `search_id`, `page`, `utm_*`, … | verwerfen | volatile Tracking-Parameter |
| – | `per_page=20`, `page=1` | wir holen nur Seite 1 |

**Wichtige Falle:** Alte Parameternamen (`brand_ids=53`) werden **stillschweigend ignoriert**. Die API antwortet trotzdem mit 200, aber ungefiltert. Unser URL-Parser muss daher streng sein, und die Tests müssen das Mapping abdecken.

**Felder pro Artikel (aus Test-Fixture von vinted_scraper und Parser von vinted-sniper; der neue Endpunkt muss in M0 live verifiziert werden):**

| Feld | Inhalt | Status nach API-Umbau |
|---|---|---|
| `id` | Artikel-ID | sicher |
| `title` | Titel | sicher |
| `price.amount` / `price.currency_code` | Preis | sicher |
| `total_item_price.amount` | Preis inkl. Käuferschutz | sehr wahrscheinlich |
| `service_fee.amount` | Käuferschutz-Gebühr | sehr wahrscheinlich |
| `brand_title`, `size_title`, `status` | Marke, Größe, Zustand | laut PR teils **nach `item_box.first_line/second_line` verschoben**, M0 prüfen |
| `item_box.accessibility_label` | Klartext inkl. Marke, Zustand, Preise | Fallback-Quelle |
| `url` / `path` | Artikel-Link | jetzt **relativ**, selbst zusammenbauen |
| `photo.url`, `photo.full_size_url`, `photo.thumbnails[]` | Hauptfoto | sicher |
| `photo.high_resolution.timestamp` | Upload-Zeit des Hauptfotos | **einziger Zeitstempel-Ersatz**. Laut einem PR fehlen echte „listed at“-Zeitstempel. M0 prüfen |
| `photos[]` | alle Fotos | unsicher, ggf. nur auf Artikelseite |
| `user.id`, `user.login`, `user.profile_url`, `user.photo` | Verkäufer | sicher |
| `user.feedback_reputation`, `user.feedback_count` | Bewertung (0..1) und Anzahl | **unsicher** |
| `promoted` | „Hochgeschoben“, alter Artikel | zum Ausfiltern |
| `favourite_count`, `view_count` | Likes/Views | nice-to-have |
| Beschreibung | – | **nicht** in der Suchantwort, nur Artikelseite (`og:description`) |

### 1.2 Open-Source-Bots und -Wrapper im Vergleich

| Projekt | Sprache | Stand 10/2026 | Architektur & Umgang mit Blockierung | Bewertung |
|---|---|---|---|---|
| **Giglium/vinted_scraper** (PyPI `vinted_scraper` 5.0.3, MIT, ~100★) | Python, httpx, sync+async | **aktiv**, am 16.09.2026 binnen 1 Tag auf `svc-catalogue` migriert | Session-Bootstrap mit 3 Versuchen und exp. Backoff (2^n s); 401 → Session neu; **zufälliger User-Agent aus Liste**; Detailseite via `<head>`-Streaming statt 403-JSON | **Beste Protokoll-Referenz.** Als Abhängigkeit nur bedingt geeignet: Breaking Changes (5.0.0) und UA-Rotation widersprechen den Leitplanken |
| **jasp-nerd/vinted-sniper** (MIT) | Python 3.13+, httpx, SQLite | Architektur sehr gut, aber **aktuell kaputt**: noch alter Endpunkt, Issue #12 vom 09.10.2026 „no catalog items“ | Eine Prozess/Event-Loop/SQLite-Datei; **Fehlerklassen** (401/403/429/Fehlformat) getrennt behandelt; **Outbox-Pattern**; Watchdog für „eingefrorene“ Kataloge; Baseline beim ersten Lauf; 20-min-Frische-Fenster; optional curl_cffi-Impersonation und Proxys | **Beste Architektur-Vorlage.** Wir übernehmen die Konzepte, nicht die Umgehungsoptionen |
| **Fuyucch1/Vinted-Notifications** (AGPL-3.0, ~180★) | Python, Web-UI, Telegram/RSS | letzter Push 11/2025, 38 offene Issues; Fork-PR vom 20.09.2026 migriert Endpunkt | Monolith mit Web-UI | Lizenz (AGPL) und Wartungsstand sprechen gegen Übernahme |
| **teddy-vltn/vinted-discord-bot** (~190★) | JavaScript | aktiv (09/2026) | „Zero Delay“, Kanalverwaltung | Feature-Vorbild für Discord-UX; „Zero Delay“ geht nur mit sehr hoher Last/Proxys |
| **JakobAIOdev/Vintrack** (MIT, ~170★) | Go + Next.js | sehr aktiv | Proxy-Pools, **Account-Aktionen (Liken, Angebote, Checkout)** | Widerspricht Leitplanken (Proxy-Rotation, Account-Automation) |
| **2spy/Vinted-Discord-Bot** (~340★) | Go | 07/2026 | Lernprojekt | nur Inspiration |
| **vlourme/vintedpy** | Python | archiviert | Slash-Commands `/subscribe` etc. | veraltet |
| **herissondev/vinted-api-wrapper**, **pyVinted** | Python, requests | alt, synchron | – | ungeeignet (sync, nicht migriert) |

**Was wir übernehmen:**
- Session-Bootstrap über Startseite, Token-Wiederverwendung statt ständig neuer Sessions (vinted_scraper, vinted-sniper)
- strikte **Fehlerklassifikation** (401 ≠ 403 ≠ 429 ≠ „200 aber kein Katalog“). Einen 403 wie einen Timeout zu wiederholen, macht aus kurzen Sperren lange.
- **Baseline beim ersten Lauf**, Dedupe über ID-Menge, **Frische-Fenster**, `promoted`-Artikel ignorieren
- „Erst speichern, dann senden“ (Outbox), damit nach einem Neustart weder doppelt noch gar nicht gepostet wird
- Gültigkeitsprüfung: Antwort muss JSON mit `items`-Liste sein, sonst Alarm statt stiller Fehler

**Was wir besser machen bzw. bewusst anders:**
- **Kein** zufälliger User-Agent, **keine** TLS-Impersonation, **keine** Proxys (Leitplanken)
- **globaler Rate-Limiter** über alle Filter (die meisten Bots limitieren nur pro Suche, 10 Filter ergeben dann 10× Last)
- **Circuit-Breaker**: nach wiederholtem 403 pausiert der Bot **alle** Abfragen dieser Domain für längere Zeit und meldet das im Admin-Kanal, statt weiter anzuklopfen
- Discord-native Bedienung (Slash-Commands, Kanal pro Filter) statt Web-Dashboard
- **Vertrags-Test mit Fixture**: Parser-Tests gegen eine echte (anonymisierte) Antwort, damit API-Änderungen sofort als roter Test bzw. Admin-Meldung auffallen

### 1.3 Rechtliche und vertragliche Lage (Österreich/EU)

**Keine Rechtsberatung.** Das ist eine nüchterne Einordnung, damit du informiert entscheidest.

| Thema | Lage | Risiko für dich |
|---|---|---|
| **Vinted-AGB** | Die AGB (UK-Fassung v12, gleichlautend in älteren US-Fassungen) verbieten externe Software wie Bots, Scraping-Programme, Crawler und Spider sowie Data-Mining/Screen-Scraping, sofern nicht von Vinted autorisiert. Vinted setzt dafür automatisierte Erkennung ein. Die AT-AGB-Seite lädt den Text dynamisch. Den genauen österreichischen Wortlaut konnte ich nicht maschinell auslesen und habe ihn deshalb **nicht verifiziert**. Sekundärquellen berichten von verschärfter Durchsetzung gegen Drittanbieter-Tools ab 08.10.2026 (unbestätigt). | **Hoch** im Sinne von „Verstoß gegen die Nutzungsbedingungen“. Folge: Sperre von IP und ggf. **Konto** |
| **robots.txt** (vinted.at) | `/api` und `/svc-catalogue` nicht ausgeschlossen. Kommentare verbieten Bots ausdrücklich **transaktionale** Aktionen (Konto, Warenkorb, Kauf) und KI-Training. Kein Crawl-Delay. | robots.txt ist kein Gesetz, hier aber ein Signal: Lesen wird toleriert, Transaktionen nicht. Unser Design (nur Lesen, Kauf nur per Link durch dich) passt dazu |
| **§ 118a StGB** (Widerrechtlicher Zugriff auf ein Computersystem) | setzt u. a. die **Überwindung einer spezifischen Sicherheitsvorkehrung** voraus. Öffentliche Seiten mit dem Token abzurufen, das Vinted jedem anonymen Besucher gibt, ist nach h. M. keine Überwindung. Captcha-Lösen, Fingerprint-Spoofing oder Proxy-Rotation gegen Sperren **könnte** dagegen als Umgehung gewertet werden. | Gering, **solange die Leitplanken eingehalten werden**. Deshalb sind sie nicht verhandelbar |
| **Datenbankrecht** (EU-RL 96/9, § 76c ff. UrhG) | Entnahme **wesentlicher** Teile einer Datenbank ist dem Hersteller vorbehalten. Wenige neueste Artikel pro Minute für dich sind nach meiner Einschätzung nicht „wesentlich“. Laut EuGH C-30/14 (Ryanair) kann ein Betreiber die Nutzung aber vertraglich (AGB) beschränken. | Gering. Vertragliches Risiko siehe AGB |
| **DSGVO** | Verkäufernamen und Bewertungen sind personenbezogen. Rein persönliche Nutzung fällt unter die **Haushaltsausnahme** (Art. 2 Abs. 2 lit. c). Die gilt nur, solange nichts veröffentlicht wird. | Gering, wenn der Discord-Server privat bleibt. Wir speichern minimal (IDs, keine Profile) und löschen nach 30 Tagen |
| **Urheberrecht an Fotos** | Fotos gehören den Verkäufern. Wir betten nur die Bild-URLs von Vinteds CDN ein und kopieren keine Dateien. | Gering bei privatem Server |
| **Discord-ToS** | Eigener Bot auf eigenem Server ist unproblematisch. | keins |

**Ehrliche Risikoeinschätzung für Konto und IP:**
- **Vinted-Konto:** Der Bot nutzt dein Konto **nicht** (anonymes Token, kein Login), deshalb gibt es keine direkte technische Verknüpfung. Indirekt besteht sie, wenn du **im selben Netz (gleiche IP)** eingeloggt surfst. Vinted kann IP-Signale korrelieren. Dass Vinted dafür Konten sperrt, ist nicht belegt, aber auch nicht auszuschließen. Risiko: **gering bis mittel**, nicht null.
- **IP:** Bei zu hoher Rate oder schlechter Reputation sind **403/Challenge** für die IP wahrscheinlich, meist temporär (Stunden bis Tage). Dann kannst du **auch im Browser** von dieser IP Captchas oder Sperren sehen. Risiko: **mittel**, durch zurückhaltende Rate deutlich senkbar.
- Wenn Vinted dich auffordert aufzuhören: aufhören.

### 1.4 Alternativen zum direkten Abfragen

| Alternative | Latenz | Kosten | Vorteile | Nachteile |
|---|---|---|---|---|
| **Vinteds gespeicherte Suchen + Push in der App** | keine offizielle Angabe. Nutzer berichten von Minuten bis Stunden, teils gebündelt | 0 € | 100 % regelkonform, null Risiko | kein Discord, kein eigenes Layout, gefühlt langsam und unzuverlässig |
| **Vinted Pro Integrations API** (offiziell) | – | – | offiziell | **nur für ausgewählte Pro-Händler**, deckt **nur das eigene Inventar** ab (Items/Orders/Webhooks), **keine Suche**. Für dich nicht nutzbar |
| **Apify-Actors** (z. B. `crawloop/vinted-monitor`) | Intervall 2–5 min empfohlen | ab ~1,99 $/1000 Listings plus Apify-Plattform; realistisch ~5–20 $/Monat | kein Blockierrisiko für **deine** IP, Discord-Webhook eingebaut | arbeitet **mit Residential-Proxys und TLS-Impersonation** (curl_cffi). Die Umgehung wird damit nur ausgelagert, ethisch also nicht sauberer. Zudem langsamer und laufende Kosten |
| **Andere Scraping-APIs** (ScrapeBadger, ReefAPI u. a.) | Sekunden bis Minuten | nutzungsabhängig | wenig Aufwand | gleiche Umgehungsproblematik, Datenschutz/Drittanbieter |
| **Kommerzielle Discord-Bots** (Vintrez, Distrobot o. Ä.) | Sekunden | ca. 5–15 €/Monat | sofort nutzbar | gleiche Problematik, Abhängigkeit |
| **Eigenbau (dieser Plan)** | ~30–90 s nach Sichtbarkeit im Katalog | Strom bzw. Pi | volle Kontrolle, Lernprojekt, keine Umgehung | Blockierrisiko für eigene IP, Wartung bei API-Änderungen |

**Empfehlung:** Eigenbau wie geplant, Vinteds eigene gespeicherte Suchen als **Fallback** parallel aktiv lassen.

### 1.5 discord.py (aktuell 2.7.1, 03.03.2026)

- **Slash-Commands:** `app_commands.Group("filter")` mit Subcommands. Für sofortige Verfügbarkeit **nur für deinen Server syncen** (`tree.copy_global_to(guild)` + `tree.sync(guild=…)`). Globaler Sync kann dauern.
- **Embeds (offizielle Limits):** Titel 256, Beschreibung 4096, max. 25 Felder (Name 256 / Wert 1024), Footer 2048, Autor 256, **insgesamt 6000 Zeichen**, max. 10 Embeds pro Nachricht.
- **Link-Buttons:** `discord.ui.Button(style=ButtonStyle.link, url=…)`, URL max. 512 Zeichen, max. 5 pro Reihe. Link-Buttons brauchen **keine** Interaktionsbehandlung und funktionieren auch nach Bot-Neustart.
- **Galerie (bis 3 Bilder), zwei Wege:**
  - **A) Klassisches Embed + „Gallery-Trick“:** mehrere Embeds mit **derselben `url`**, die zusätzlichen nur mit `image`. Discord rendert sie als Bildraster in einem Embed. Das ist **nicht offiziell dokumentiert** (die Doku erwähnt nur die Deduplizierung nach URL), wird aber von vielen Bots genutzt (vermutlich auch Vintrez). Optik wie im Screenshot inkl. Inline-Feld-Raster.
  - **B) Components V2** (`ui.LayoutView`, `Container`, `MediaGallery` mit 1–10 Bildern, `TextDisplay`, `Section`): **offiziell dokumentiert**. Aber: dann **keine Embeds/kein Content** in derselben Nachricht, max. 40 Komponenten, kein Inline-Feld-Raster (Felder als Markdown-Text).
  - → **Frage F3.** Meine Empfehlung: A (Vintrez-Optik), mit automatischem Fallback auf 1 Bild.
- **Kanäle:** `guild.create_category("Vinted")`, `guild.create_text_channel(name, category=…)`. Limits: 500 Kanäle pro Server, **50 Kanäle pro Kategorie**.
- **Intents:** `discord.Intents.default()` reicht. **Keine privilegierten Intents** (kein Message Content, keine Members).
- **Bot-Berechtigungen:** View Channels, Manage Channels, Send Messages, Embed Links, Read Message History → **Permission-Integer `85008`**. OAuth-Scopes: `bot` + `applications.commands`.
- **Rate-Limits:** global 50 Requests/s pro Bot. Pro-Route-Limits (z. B. Nachrichten pro Kanal) **nicht fest codieren**, discord.py wertet die `X-RateLimit-*`-Header automatisch aus. **Wichtig:** 10 000 ungültige Requests (401/403/429) in 10 Minuten führen zu einer **temporären Cloudflare-Sperre der IP**, deshalb keine Retry-Schleifen gegen Discord-Fehler.
- **Python-Version:** **3.14** (aktuell 3.14.8). Python 3.15.0 ist erst am 09.10.2026 erschienen. discord.py-Unterstützung dafür ist noch nicht erprobt, deshalb jetzt 3.14.

### 1.6 Hosting für 24/7-Betrieb

| Option | Einmalig | Laufend | Aufwand | IP-Reputation bei Vinted | Bewertung |
|---|---|---|---|---|---|
| **Raspberry Pi 4/5 zu Hause** (Pi 5 4 GB derzeit ~100–150 € wegen RAM-Preiserhöhungen; ein Pi 4 oder 2 GB reicht völlig) | 60–150 € + Netzteil/SD/Gehäuse ~30 € | Strom ~3–5 W ≈ **10–15 €/Jahr** | mittel (Linux, systemd) | **gut** (Privatanschluss) | **Empfehlung** |
| **Vorhandener Rechner / alter Laptop / Mini-PC zu Hause** | 0 € | 10–40 €/Jahr Strom | gering | gut | Empfehlung, falls vorhanden |
| **Windows-PC (dieser Rechner)** | 0 € | höherer Strom | gering | gut | gut für Entwicklung, nicht für 24/7 (Ruhezustand, Updates) |
| **VPS** (z. B. Hetzner CX23 ~5,49 €/Monat netto seit Juni 2026, Verfügbarkeit schwankt) | 0 € | ~66 €/Jahr | mittel | **schlecht**: Rechenzentrums-IPs werden laut mehreren Quellen oft sofort blockiert | **nicht empfohlen**. Bei Sperre gäbe es keine leitplankenkonforme Lösung |
| **Free Tiers** (Oracle, Render, Railway, Fly) | – | – | – | schlecht | ungeeignet (Idle-Löschung, keine Worker, Free Tier entfallen) |

**Docker oder nicht?** Für dich als Python-Lerner empfehle ich zuerst **venv + systemd** (transparent, keine zusätzliche Abstraktion). Ein `Dockerfile`/`docker-compose.yml` liefere ich optional mit (gleiche Funktion, einfacher umzuziehen).

### 1.7 Latenz: was realistisch ist

Gesamtlatenz = **(A) Vinted-interne Verzögerung** + **(B) Abfrageintervall** + **(C) Verarbeitung + Discord**

- **(A)** Vinted prüft neue Inserate teils automatisch, bevor sie im Katalog erscheinen. Die Vinted-Oberfläche selbst nennt dafür in manchen Fällen „etwa 15 bis 30 Minuten“. Zusätzlich hat der Suchindex Verzögerung. **Darauf haben wir keinen Einfluss.** Sie liegt oft über dem Abfrageintervall.
- **(B)** Bei Intervall T beträgt die durchschnittliche Zusatzlatenz T/2. Bei 60 s also ~30 s im Mittel, ~60 s im schlechtesten Fall.
- **(C)** < 2 s.

| Intervall | Ø Zusatzlatenz | Requests/Tag bei 5 Filtern | Blockierrisiko |
|---|---|---|---|
| 10 s | 5 s | 43 200 | hoch |
| 30 s | 15 s | 14 400 | mittel |
| **60 s** | **30 s** | **7 200** | **gering–mittel** |
| 120 s | 60 s | 3 600 | gering |

**Empfehlung: 60 s Standard, 30 s Untergrenze pro Filter, ±15 % Zufallsstreuung (Jitter)** (damit nicht alle Filter gleichzeitig feuern) **und ≥ 5 s globaler Mindestabstand** zwischen zwei Vinted-Requests. Begründung: Unter 60 s gewinnst du im Mittel nur Sekunden gegenüber einer Vinted-internen Verzögerung, die oft Minuten beträgt, erhöhst aber das Risiko einer Sperre deutlich. Bei vielen Filtern lieber das Intervall erhöhen als die Parallelität. Pro Prüfung **genau 1 Request** (Seite 1, newest_first), kein Durchblättern.

### 1.8 Quellen und Bewertung

| # | Quelle | Datum | Verlässlichkeit |
|---|---|---|---|
| 1 | [Giglium/vinted_scraper – Repo & Quellcode](https://github.com/Giglium/vinted_scraper) (`_constants.py`, `_headers.py`, `_session.py`, Test-Fixture) | Release 5.0.3 vom 19.09.2026 | **Hoch**: Primärquelle Code, aktiv gepflegt, nachweislich funktionsfähig nach Umbau |
| 2 | [vinted_scraper PR #216 „move search to svc-catalogue“](https://github.com/Giglium/vinted_scraper/pull/216), [Issue #215](https://github.com/Giglium/vinted_scraper/issues/215), [#224](https://github.com/Giglium/vinted_scraper/issues/224), [#59 (403 Detail-Endpunkt)](https://github.com/Giglium/vinted_scraper/issues/59) | 15.–18.09.2026 | **Hoch** |
| 3 | [Vinted-Notifications PR #1 (Fork)](https://github.com/mariostavrou83-create/Vinted-Notifications/pull/1) | 20.09.2026 | **Mittel**: Einzelentwickler, aber mit Live-Belegen zum Parameter-Mapping |
| 4 | [MINLEGO/vntd Issue #2](https://github.com/MINLEGO/vntd/issues/2) | 18.09.2026 | Mittel (bestätigt Mapping) |
| 5 | [jasp-nerd/vinted-sniper](https://github.com/jasp-nerd/vinted-sniper) – Doku (architecture, troubleshooting, legal, self-hosting) & Code; [Issue #12](https://github.com/jasp-nerd/vinted-sniper/issues/12) | 08–10/2026 | **Hoch** für Architektur/Betrieb, Protokollteil **veraltet** |
| 6 | [Fuyucch1/Vinted-Notifications](https://github.com/Fuyucch1/Vinted-Notifications), [teddy-vltn/vinted-discord-bot](https://github.com/teddy-vltn/vinted-discord-bot), [Vintrack](https://github.com/JakobAIOdev/Vintrack-Vinted-Monitor), [vlourme/vintedpy](https://github.com/vlourme/vintedpy), [GitHub-Topic vinted-api](https://github.com/topics/vinted-api) | 2025–10/2026 | Mittel (nur README/Metadaten ausgewertet) |
| 7 | [vinted.at/robots.txt](https://www.vinted.at/robots.txt) | live 14.10.2026 | **Hoch** (Primärquelle) |
| 8 | [Vinted AGB UK v12 (PDF)](https://legal-assets.vinted.com/legal/terms/gb/en-GB/terms-and-conditions-v12.pdf), [ältere US-AGB](https://www.vinted.com/old-terms-and-conditions) | aktuell / älter | **Hoch** für UK. AT-Wortlaut **nicht verifiziert** |
| 9 | [crosslist.com: Vinted-Regeln](https://crosslist.com/blog/does-vinted-allow-resellers) (Durchsetzung ab 08.10.2026) | 10/2026 | **Niedrig** (Anbieter mit Eigeninteresse, unbestätigt) |
| 10 | [Vinted Pro Integrations](https://vinted.co.uk/pro/integrations) | aktuell | Hoch (offiziell) |
| 11 | [Apify crawloop/vinted-monitor](https://apify.com/crawloop/vinted-monitor.md) | 2026 | Mittel (Anbieterangaben zu Preis und DataDome) |
| 12 | [discord.py Changelog](https://discordpy.readthedocs.io/en/stable/whats_new.html), PyPI (2.7.1) | 03/2026 | **Hoch** |
| 13 | [Discord Docs: Rate Limits](https://docs.discord.com/developers/topics/rate-limits), [Message/Embed-Limits](https://docs.discord.com/developers/resources/message), [Components-Referenz](https://docs.discord.com/developers/components/reference) | live | **Hoch** (offiziell) |
| 14 | [discord.js Guide: Components V2](https://discordjs.guide/popular-topics/display-components) | 2026 | Mittel–hoch |
| 15 | [Hetzner-Preiserhöhung Juni 2026](https://wz-it.com/en/blog/hetzner-price-increase-june-2026-cpx-ccx-alternatives/), [Preisvergleich 08/2026](https://kimmo.suominen.com/stuff/cpc-2026-08.txt) | 06–08/2026 | Mittel (Sekundärquellen, Live-Preis prüfen) |
| 16 | [Raspberry-Pi-Preiserhöhungen](https://www.gigazine.net/gsc_news/en/20260402-raspberry-pi-price-increases), [geizhals.at](https://geizhals.at/raspberry-pi-5-modell-b-a3029965.html?va=b) | 04–08/2026 | Mittel |
| 17 | PyPI-Metadaten (httpx 0.28.1, aiosqlite 0.22.1, pytest 9.1.1, pytest-asyncio 1.4.0, respx 0.23.1, python-dotenv 1.2.4), endoflife.date (Python) | live | Hoch |
| 18 | vinted.at AGB-Seite (HTML, Übersetzungs-Strings: „Dieser Vorgang dauert normalerweise etwa 15 bis 30 Minuten“ für die automatische Inseratsprüfung) | live | Mittel (Kontext des Strings nicht 100 % eindeutig) |

**Nicht gefunden:** Zu „Vintrez“ gibt es keine öffentlichen Infos (vermutlich ein geschlossener kommerzieller Bot). Das Layout orientiert sich daher an deinem Screenshot.

---

## 2. Architekturentscheidung

**Entscheidung: Ein einzelner Python-Prozess mit einer asyncio-Event-Loop**, in der der discord.py-Client läuft. Pro Filter läuft eine Poller-Task, dazu ein zentraler Vinted-Client mit globalem Rate-Limiter, ein Dispatcher (Discord-Ausgabe) und eine SQLite-Datei.

```
┌──────────────────────────── Prozess (asyncio) ─────────────────────────────┐
│                                                                             │
│  discord.py Bot ── Slash-Commands ──► FilterManager ──startet/stoppt──┐     │
│        ▲                                   │                          ▼     │
│        │                                   │                 Poller-Task je │
│        │                                   │                 Filter (60 s)  │
│        │                                   ▼                          │     │
│  Dispatcher ◄── Outbox (SQLite) ◄── Dedupe/Baseline ◄── VintedClient ◄┘     │
│  (1 Nachricht nach der anderen,                       │  globaler Rate-     │
│   Embeds bauen, Admin-Meldungen)                      │  Limiter (≥5 s)     │
│                                                       │  Session-Cache      │
│                                                       ▼  Circuit-Breaker    │
│                                          www.vinted.at / api.vinted.at      │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Begründung:**
- Ein Prozess, eine Loop: discord.py ist ohnehin asyncio-basiert, die Poller teilen sich die Loop. Kein Threading, keine Locks, ein Log und ein Neustartpunkt.
- **SQLite** reicht für einen Nutzer und eine Handvoll Filter. Ein Transaktions-Commit pro Abfrage ist billig, und WAL-Modus erlaubt parallele Lesezugriffe.
- **Outbox in SQLite:** Gefundene Artikel werden **in derselben Transaktion** als „gesehen“ und „zu senden“ gespeichert. Gesendet gilt erst, wenn Discord die Nachricht angenommen hat. Ergebnis: nach einem Absturz kein Verlust und kein Doppelpost.
- **Eigener schlanker Vinted-Client statt Abhängigkeit von vinted_scraper:** ~150 Zeilen. Wir kontrollieren User-Agent und Retry-Verhalten (Leitplanken), vermeiden Breaking Changes einer Fremdbibliothek, und du verstehst jede Zeile. vinted_scraper dient als **Protokoll-Referenz**.

**Verworfene Alternativen:**

| Alternative | Warum verworfen |
|---|---|
| Discord-**Webhooks** statt Bot | keine Slash-Commands, keine Kanalerstellung |
| `vinted_scraper` als Dependency | zufälliger User-Agent (Leitplanke), Breaking Changes, versteckte Retry-Logik |
| Mehrere Prozesse / Celery / Redis / Postgres | Overkill für 1 Nutzer. Mehr Teile, die nachts kaputtgehen |
| Playwright/Headless-Browser | schwergewichtig (RAM auf dem Pi), und ein automatisierter Browser gilt als Bot-Tarnung, also Grauzone bei den Leitplanken |
| Threads statt asyncio | discord.py ist async. Mischbetrieb ist fehleranfällig |
| Apify/externer Dienst | Umgehung nur ausgelagert, laufende Kosten, langsamer |
| ORM (SQLAlchemy) | Magie für 3 Tabellen. Rohes SQL mit `aiosqlite` ist lesbarer und lehrreicher |

**Stack:** Python 3.14 · discord.py 2.7.x · httpx 0.28.x (HTTP/1.1, keine Impersonation) · aiosqlite · python-dotenv · pytest + pytest-asyncio + respx (HTTP-Mocking) · ruff (Formatter/Linter, `indent-width = 2`).

---

## 3. Projektstruktur, Datenmodell, Ablauf

### 3.1 Ordnerstruktur

```
Vinted-Bot/
├─ .env.example              # Vorlage, ohne Secrets (wird committet)
├─ .env                      # echte Secrets (in .gitignore!)
├─ .gitignore
├─ pyproject.toml            # ruff (2 Spaces), pytest-Konfig
├─ requirements.txt          # gepinnte Laufzeit-Abhängigkeiten
├─ requirements-dev.txt      # pytest, respx, ruff
├─ README.md                 # Setup, Betrieb, Troubleshooting
├─ PLAN.md                   # dieses Dokument
├─ src/vinted_bot/
│  ├─ __main__.py            # Einstieg: python -m vinted_bot
│  ├─ config.py              # .env laden + validieren (dataclass Settings)
│  ├─ logging_setup.py       # Logging: Konsole + rotierende Datei
│  ├─ vinted/
│  │  ├─ url_parser.py       # Filter-Link → Domain + API-Parameter + kanonische URL
│  │  ├─ session.py          # anonymes Token holen/cachen/erneuern
│  │  ├─ client.py           # 1 Request: Katalog abrufen, Antwort validieren
│  │  ├─ rate_limiter.py     # globaler Mindestabstand + Circuit-Breaker
│  │  ├─ models.py           # dataclass VintedItem + parse_item() (defensiv)
│  │  ├─ enrich.py           # optional: Artikelseite <head> → Beschreibung/Fotos
│  │  └─ errors.py           # Unauthorized / Blocked / RateLimited / Malformed
│  ├─ core/
│  │  ├─ poller.py           # Schleife pro Filter: holen → dedupe → outbox
│  │  ├─ dedupe.py           # reine Logik: was ist neu? (gut testbar)
│  │  ├─ filter_manager.py   # Tasks starten/stoppen/pausieren
│  │  └─ dispatcher.py       # Outbox abarbeiten → Discord
│  ├─ storage/
│  │  ├─ schema.sql
│  │  ├─ db.py               # Verbindung, Migration, WAL
│  │  └─ repository.py       # ALLE SQL-Statements an einem Ort
│  └─ discord_bot/
│     ├─ bot.py              # Client, setup_hook, Command-Sync
│     ├─ commands.py         # /filter add|list|pause|resume|remove|interval, /status
│     ├─ embeds.py           # VintedItem → Embeds + Buttons (reine Funktion)
│     ├─ channels.py         # Kategorie/Kanal anlegen, Namen säubern
│     └─ admin.py            # Statusmeldungen in Admin-Kanal (entprellt)
├─ tests/
│  ├─ fixtures/              # anonymisierte echte Vinted-Antworten (aus M0)
│  ├─ test_url_parser.py
│  ├─ test_models.py
│  ├─ test_dedupe.py
│  ├─ test_embeds.py
│  ├─ test_client.py         # respx: 200/401/403/429/HTML statt JSON
│  ├─ test_poller.py         # Poller + echte SQLite (tmp) + Fake-Client
│  └─ test_repository.py
├─ scripts/
│  └─ probe.py               # M0: einmaliger, manueller Live-Test (2–3 Requests)
└─ deploy/
   ├─ vinted-bot.service     # systemd-Unit
   ├─ Dockerfile             # optional
   └─ docker-compose.yml     # optional
```

### 3.2 SQLite-Schema

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE filters (
  id               INTEGER PRIMARY KEY,
  name             TEXT    NOT NULL,              -- Anzeigename, z. B. "nike-air-max"
  source_url       TEXT    NOT NULL,              -- so wie eingefügt
  canonical_url    TEXT    NOT NULL UNIQUE,       -- normalisiert, verhindert Duplikate
  domain           TEXT    NOT NULL,              -- "at", "de"
  api_params_json  TEXT    NOT NULL,              -- gemappte API-Parameter
  channel_id       INTEGER NOT NULL,              -- Discord-Kanal
  interval_s       INTEGER NOT NULL DEFAULT 60 CHECK (interval_s >= 30),
  countries        TEXT,                          -- z. B. "AT,DE,IT,FR"; NULL = alle Länder
  paused           INTEGER NOT NULL DEFAULT 0,
  baseline_done    INTEGER NOT NULL DEFAULT 0,    -- 0 = nächster Lauf setzt nur Baseline
  created_at       TEXT    NOT NULL,
  last_success_at  TEXT,
  last_error       TEXT,
  fail_count       INTEGER NOT NULL DEFAULT 0     -- aufeinanderfolgende Fehler
);

-- Gesehene Artikel + Outbox in einem: posted_at NULL = noch zu senden
CREATE TABLE seen_items (
  filter_id     INTEGER NOT NULL REFERENCES filters(id) ON DELETE CASCADE,
  item_id       INTEGER NOT NULL,
  first_seen_at TEXT    NOT NULL,
  is_baseline   INTEGER NOT NULL DEFAULT 0,       -- 1 = nie posten
  payload_json  TEXT,                             -- nur für ungesendete: Daten fürs Embed
  posted_at     TEXT,
  attempts      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (filter_id, item_id)
);
CREATE INDEX idx_outbox ON seen_items(posted_at) WHERE posted_at IS NULL AND is_baseline = 0;

-- Anonyme Vinted-Session pro Domain (überlebt Neustart, spart Bootstrap-Requests)
-- Verkäufer-Cache (Datensparsamkeit: nur was das Embed braucht). Ein Verkäufer kann
-- in mehreren Filtern auftauchen, deshalb global und nicht pro Filter.
CREATE TABLE sellers (
  user_id             INTEGER PRIMARY KEY,
  country_iso         TEXT,                       -- "DE"; NULL = unbekannt
  feedback_reputation REAL,                       -- 0..1
  feedback_count      INTEGER,
  photo_url           TEXT,
  fetched_at          TEXT NOT NULL               -- nach 30 Tagen neu abfragen
);

CREATE TABLE vinted_sessions (
  domain       TEXT PRIMARY KEY,
  cookies_json TEXT NOT NULL,
  anon_id      TEXT,
  fetched_at   TEXT NOT NULL
);

-- Einfache Schlüssel-Werte (Kategorie-ID, Admin-Kanal-Status, Circuit-Breaker bis ...)
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

**Aufräumen:** täglich `seen_items` älter als 30 Tage löschen und `payload_json` nach dem Senden auf NULL setzen (Datensparsamkeit). Ungesendete Einträge, die älter als 1 h sind, werden verworfen: Nach einem nächtlichen Discord-Ausfall willst du keine Flut alter Artikel.

### 3.3 Ablauf

```
Poller(filter)  – Endlosschleife, eine asyncio-Task pro Filter
│
├─ warten bis: interval_s ± 15 % Jitter vergangen  UND  Circuit-Breaker der Domain zu
├─ rate_limiter.acquire(domain)       # globaler Abstand ≥ 5 s zwischen ALLEN Vinted-Requests
├─ session.get(domain)                # aus Cache/DB, sonst Bootstrap (GET Startseite)
├─ client.fetch_catalog(params)       # 1 Request, Seite 1, newest_first, per_page=20
│     ├─ 200 + gültiges JSON  → weiter
│     ├─ 401                  → Session verwerfen, 1× sofort neu versuchen
│     ├─ 429                  → Retry-After respektieren (sonst 2 min), Backoff ×2 bis 30 min
│     ├─ 403 / Challenge-HTML → Circuit-Breaker der DOMAIN öffnen: 15 min → 30 → 60 → max 6 h,
│     │                          Session verwerfen, Admin-Meldung (entprellt)
│     ├─ 200 aber kein Katalog→ "Malformed": loggen inkl. Antwortanfang, Admin-Meldung, kein Retry
│     └─ Netzwerkfehler       → 10 s, 20 s, 40 s … max 5 min
├─ items = [parse_item(x) for x in json["items"]]   # defensiv, fehlende Felder → None
├─ new = dedupe.select_new(items, seen_ids, baseline_done, now)
│     ├─ promoted → ignorieren
│     ├─ bereits gesehen (filter_id,item_id) → ignorieren
│     ├─ ID-Wasserstand (M0: keine Zeitstempel mehr): ID deutlich kleiner als die kleinste
│     │   ID der letzten Abfrage → vermutlich alter, wieder aufgetauchter Artikel → als gesehen
│     │   speichern, nicht posten. Toleranz wird in M2 anhand echter Daten festgelegt
│     └─ baseline_done = 0 → ALLE als is_baseline=1 speichern, baseline_done = 1, nichts posten
├─ für jeden neuen Artikel: Verkäufer aus Cache, sonst GET /api/v2/users/{id} (über Rate-Limiter)
│     └─ Länderfilter gesetzt und Land nicht enthalten → als gesehen speichern, nicht posten
├─ EINE Transaktion: neue Einträge in seen_items (+ payload), last_success_at, fail_count=0
└─ dispatcher.wake()

Dispatcher  – eine Task für alle Filter
├─ hole älteste ungesendete Einträge (ältere zuerst)
├─ optional: enrich(item)             # nur wenn aktiviert (F4), eigener langsamer Rate-Limit
├─ embeds, view = build_message(item, filter)
├─ channel.send(...)                  # discord.py beachtet Discord-Rate-Limits selbst
│     ├─ Erfolg → posted_at = now, payload_json = NULL
│     ├─ Kanal gelöscht (404) → Filter pausieren + Admin-Meldung
│     └─ Fehler → attempts++, nach 5 Versuchen verwerfen + loggen
└─ schlafen bis wake() oder 5 s

Start/Neustart
├─ DB öffnen/migrieren → Bot einloggen → Commands syncen (nur Guild)
├─ ungesendete Outbox-Einträge > 1 h verwerfen
└─ für jeden nicht pausierten Filter Poller starten (gestaffelt, je +5 s)
```

---

## 4. Embed-Mapping (Vorbild Vintrez-Screenshot)

| Embed-Element | Quelle (Vinted-Feld) | Beispiel | Verfügbarkeit |
|---|---|---|---|
| **Autor-Zeile** (Name, Icon, Link) | `user.login` (+ „· Pro“ bei `user.business`), Icon = Avatar aus Verkäufer-Lookup, Link `https://www.vinted.<tld>/member/<user.id>` | „maria_92 · Pro“ | Name sicher, Avatar über Verkäufer-Lookup |
| **Titel** (Link) | Flagge aus `country_iso_code` (Verkäufer-Lookup) + `title` + ` – ` + Preis; Link = `https://www.vinted.<tld>` + `url` | „🇩🇪 Polo Ralph Lauren Pullover – 35,00 €“ | Flagge über Verkäufer-Lookup, sonst „❔“ |
| **Beschreibung** | Artikelbeschreibung, gekürzt auf ~200 Zeichen | „Kaum getragen, …“ | **nur mit Enrichment** (Artikelseite, `og:description`). Ohne: weglassen oder `item_box.accessibility_label` |
| Feld **⏱ Aktualisiert** | Zeitpunkt des ersten Sehens → Discord-Zeitstempel `<t:UNIX:R>`. Discord rendert „vor 10 Minuten“ **lokal und live aktualisierend** | „vor 2 Minuten“ | sicher. Vinted liefert keinen Upload-Zeitstempel mehr (M0) |
| Feld **📏 Größe** | `item_box.accessibility_label` („Größe: M“), Fallback: Teil vor „ · “ in `second_line` | „M“ | sicher (M0) |
| Feld **🏷 Marke** | `item_box.first_line` | „Ralph Lauren“ | sicher (M0) |
| Feld **✨ Zustand** | `item_box.accessibility_label` („Zustand: Sehr gut“), Fallback: letzter Teil von `second_line` | „Sehr gut“ | sicher (M0) |
| Feld **⭐ Bewertung** | `feedback_reputation` (0..1) × 5 → ★, + `feedback_count` aus Verkäufer-Lookup | „★★★★★ (14)“ | über Verkäufer-Lookup (zweite Probe). Fehlt er, entfällt das Feld |
| Feld **💶 Preis** | `price.amount` + `total_item_price.amount` | „45,00 € (≈ 48,95 € inkl. Käuferschutz)“ | sicher |
| **Bilder** (bis 3) | Hauptfoto `photo.full_size_url`. Fotos 2–3 nur per Enrichment | Galerie | 1 sicher. `photos[]` enthält in der Suche nur 1 Foto (M0) |
| **Footer** | Filtername + Domain | „nike-air-max · vinted.at“ | sicher |
| **Timestamp** | Zeitpunkt des Postens | | sicher |
| **Farbe** | Vinted-Türkis `#09B1BA` | | – |
| **Button „Ansehen“** | `https://www.vinted.<tld>/items/<id>` | Link-Button | sicher |
| **Button „Verkäufer“** | `https://www.vinted.<tld>/member/<user.id>` | Link-Button | sicher |
| ~~Button „Kaufen“~~ | – | – | **entfällt**: alter Deeplink liefert 404, ein neuer existiert nicht (zweite Probe) |

Weder für „Kaufen“ noch für „Angebot machen“ gibt es einen Deeplink. Den Kauf startest du auf der Artikelseite, das ist ein Klick mehr als bei Vintrez. Der Bot kauft nie selbst.

Formatierung: Preise im österreichischen Format (`45,00 €`). Alle Texte werden auf die Discord-Limits gekürzt (Funktion `truncate()` mit Test). Fehlende Felder lassen das Feld weg, statt „None“ anzuzeigen.

---

## 5. Meilensteine

Jeder Meilenstein endet mit einem lauffähigen Stand, grünen Tests und einem Abnahmekriterium, das du selbst prüfen kannst.

| # | Inhalt | Abnahmekriterium |
|---|---|---|
| **M0 – Live-Probe** (vor allem anderen, ~1 h) | `scripts/probe.py`: **manuell gestartet, von deinem Heimanschluss**, genau 1× Bootstrap + 1× Katalogabfrage für einen deiner Filter-Links. Antwort wird anonymisiert als Fixture gespeichert. Getestet wird der UA gemäß F1. Zusätzlich prüfst du den Kauf-Link einmal im Browser | (a) Status 200 mit `items` **oder** dokumentierter 403 → Entscheidungspunkt; (b) Feldliste aus Abschnitt 4 bestätigt/korrigiert; (c) Kauf-Deeplink funktioniert. **Bei 403 trotz zurückhaltendem Vorgehen stoppen wir hier und entscheiden gemeinsam**, es wird nichts umgangen |
| **M1 – Gerüst** | venv, requirements, ruff (2 Spaces), `config.py` mit `.env`, Logging, `pytest` läuft, `.gitignore` inkl. `.env`/`*.db` | `python -m vinted_bot` startet, loggt „Konfiguration ok“ und beendet sich. `pytest` ist grün |
| **M2 – Minimaler Durchstich** | URL-Parser, Session, Client (ohne Backoff-Feinheiten), `parse_item`, Verkäufer-Lookup + **Länderfilter**, SQLite-Dedupe + Baseline, mehrfilterfähiger Kern. Bot loggt ein und postet **ein einfaches Embed (Flagge+Titel+Preis+Link)** in **einen festen Kanal aus `.env`** für **einen Start-Filter aus `.env`** (inkl. Länderkürzel) | Bot läuft 30 min: beim Start **keine** Altartikel; ein neu auf Vinted erscheinender Artikel kommt im Kanal an; Neustart erzeugt **keine** Doppelposts |
| **M3 – Slash-Commands & Kanäle** | `/filter add <url> [name] [laender: at,de,it,fr] [intervall] [kanal]`, `/filter laender <id> <kürzel>`, `/filter list`, `/filter pause/resume <id>`, `/filter remove <id> [kanal_löschen]`, `/filter interval <id> <s>`; automatische Kategorie „Vinted“ + Kanal je Filter; Commands nur für dich (Owner-Check + `default_permissions(administrator)`) | Filter per Discord anlegen erzeugt Kanal und Baseline; pausieren stoppt Requests (im Log sichtbar); löschen entfernt Task + DB-Einträge |
| **M4 – Vintrez-Layout** | `embeds.py` komplett: alle Felder aus Abschnitt 4, Galerie (A oder B je nach F3), Link-Buttons | Screenshot-Vergleich mit Vorlage; Snapshot-Tests für 3 Fixture-Artikel (vollständig / ohne Marke / ohne Bewertung) |
| **M5 – Robustheit** | Fehlerklassen, globaler Rate-Limiter, Circuit-Breaker pro Domain, Backoff, Outbox-Wiederaufnahme, Admin-Kanal mit entprellten Meldungen („Domain vinted.at blockiert seit 12:03, nächster Versuch 12:33“ / „wieder ok“), `/status` | Tests mit respx für 401/403/429/HTML-Antwort/Timeout grün; manuell: Netzwerk trennen → Meldung → Netzwerk an → „wieder ok“ |
| **M6 – Betrieb** | systemd-Unit (oder Docker), Log-Rotation, tägliches Aufräumen, README mit Troubleshooting | **72 h Dauerlauf** auf dem Zielgerät ohne Absturz, ohne Doppelposts, ohne 403. Reboot → Bot läuft automatisch wieder |
| **M7 – Optional** | Enrichment (Beschreibung + Fotos 2–3 über Artikelseite, max. 1 Zusatz-Request pro neuem Artikel mit eigenem Limit), Watchdog „Filter seit X h ohne Treffer“, Preisfilter inkl. Käuferschutz, Ausschlusswörter | je Feature eigene Tests |

---

## 6. Teststrategie

**Grundsatz:** Kein Test erreicht das Netzwerk. Live-Kontakt mit Vinted gibt es nur in `scripts/probe.py`, manuell gestartet.

| Ebene | Was | Wie |
|---|---|---|
| **Unit: URL-Parser** | vinted.at/.de, `catalog[]`-Arrays, Pfad `/catalog/123-...`, `/brand/53-...`, `search_text` mit Leerzeichen/Umlauten/`+`, Tracking-Parameter entfernen, leere Werte weglassen, ungültige Domains, Link ohne Filter, **neue `attribute_ids[...]`-Namen** | parametrisierte pytest-Tests (`@pytest.mark.parametrize`), ~30 Fälle |
| **Unit: Modelle** | `parse_item` mit vollständigem, minimalem und kaputtem Artikel; alte vs. neue Feldorte (`brand_title` vs. `item_box`) | Fixtures aus M0 |
| **Unit: Dedupe** | Baseline beim ersten Lauf; nur Unbekannte; promoted ignoriert; Frische-Fenster; gleiche ID in zwei Filtern | reine Funktionen, keine DB |
| **Unit: Embeds** | Feldbelegung, fehlende Felder, Kürzung an Limits (6000/1024/256), Preisformat, Sterne-Rundung, Button-URLs ≤ 512 Zeichen | `embed.to_dict()` vergleichen |
| **Unit: Rate-Limiter/Backoff** | Abstand eingehalten, Backoff-Folge, Circuit-Breaker-Zustände | Fake-Uhr (injizierte `now()`-Funktion statt `time.sleep`) |
| **Integration: Client** | 200, 401→Refresh→200, 403, 429 mit/ohne Retry-After, HTML statt JSON, Timeout | **respx** mockt httpx |
| **Integration: Poller+DB** | kompletter Zyklus mit temporärer SQLite-Datei; Neustart-Simulation → keine Doppelposts; Absturz zwischen Speichern und Senden → wird nachgeholt | Fake-Vinted-Client + Fake-Sender |
| **Manuell auf dem Server** | Checkliste: Filter anlegen/pausieren/löschen, Layout auf Desktop **und Handy**, Buttons öffnen richtige Seiten, Neustart, Netztrennung, Admin-Meldungen | Checkliste im README |

Ziel: ≥ 80 % Abdeckung in `vinted/` und `core/`. Discord-Klebecode wird manuell getestet.

---

## 7. Risikoregister

| # | Risiko | Wahrsch. | Auswirkung | Gegenmaßnahme |
|---|---|---|---|---|
| R1 | **403/Challenge für deine IP** (Cloudflare/DataDome) | mittel | Bot blind, evtl. Captchas beim normalen Surfen | 60-s-Intervall, globaler Abstand, Session-Wiederverwendung, Circuit-Breaker mit langer Pause, Admin-Meldung. **Keine** Umgehung. Bei Dauer-403: Intervall erhöhen oder Projekt pausieren |
| R2 | **Bot läuft nur mit Umgehung stabil** (z. B. weil httpx' TLS-Fingerprint per se blockiert wird) | gering–mittel | Projekt nicht leitplankenkonform betreibbar | wird in **M0** geklärt, bevor nennenswert Code entsteht. Dann ehrlicher Abbruch bzw. Fallback auf Vinted-eigene Benachrichtigungen |
| R3 | **API-Änderung** (wie 09/2026) | **hoch** (passiert regelmäßig) | keine Treffer, 404/400 oder stilles Ignorieren von Filtern | Antwort-Validierung, „Malformed“-Alarm, Watchdog „keine Treffer seit X h“, Parser zentral in `models.py`/`url_parser.py`, Fixtures; vinted_scraper-Releases beobachten |
| R4 | **Filter werden stillschweigend ignoriert** (alte Parameternamen) | mittel | Spam mit unpassenden Artikeln | Mapping-Tests; beim `/filter add` Vorschau: „Erster Abruf: 20 Artikel, Beispiel: …“ zur Sichtkontrolle |
| R5 | **Kontosperre** wegen AGB-Verstoß | gering | Verlust Kaufhistorie/Bewertungen | kein Login, kein Kauf, nur Links. Optional: Bot über anderen Anschluss betreiben als deine Vinted-Nutzung (F6) |
| R6 | **Rechtliche Abmahnung/Aufforderung** | sehr gering | Aufwand | Eigenbedarf, geringe Last, keine Veröffentlichung. Bei Aufforderung sofort einstellen |
| R7 | **Discord-Rate-Limits/Cloudflare-Sperre** (10 000 ungültige Requests/10 min) | gering | Bot-IP bei Discord gesperrt | Dispatcher sendet seriell; discord.py-eigenes Limit-Handling; keine Retry-Schleifen bei 403/404; Filter mit gelöschtem Kanal automatisch pausieren |
| R8 | **Flut nach Ausfall** | mittel | Kanal voll alter Artikel | Frische-Fenster, Outbox-Ablauf nach 1 h, max. 10 Posts pro Filter und Zyklus |
| R9 | **Gallery-Trick** (Option A) bricht bei Discord-Update | gering–mittel | nur 1 Bild sichtbar | ist rein kosmetisch. Fallback 1 Bild, Option B als Plan B |
| R10 | **Token/Secrets leaken** | gering | fremde Kontrolle über Bot | `.env` in `.gitignore`, `.env.example` ohne Werte, Token nie loggen. Bei Leak: Token im Developer-Portal neu erzeugen |
| R11 | **Stromausfall/Reboot/SD-Karten-Defekt** (Pi) | mittel | Ausfall | systemd `Restart=always`, SQLite-WAL, wöchentliches DB-Backup (eine Datei); Datenverlust tut kaum weh (Baseline neu) |
| R12 | **Vinted verzögert neue Inserate intern** | hoch | Latenz Minuten statt Sekunden | nicht beeinflussbar, Erwartungsmanagement |

---

## 8. Deployment

> **Entscheidung vom 15.10.2026:** Kein 24/7-Betrieb. Der Bot läuft per **Autostart beim Windows-Login** auf deinem PC, weil du ohnehin nur am PC schaust und kein Handy nutzt. Umgesetzt sind Verknüpfung im Autostart-Ordner, `pythonw` ohne Fenster, Einzelinstanz-Sperre und Start-Wiederholung bei fehlendem Netz (siehe README). Nach einer Pause werden verpasste Artikel nachgeholt, bis zu 10 pro Filter. Die Pi-/VPS-Variante unten bleibt als Option für später.

### 8b. Option: 24/7-Betrieb

**Empfohlen: Raspberry Pi (oder vorhandener Linux-Rechner) zu Hause, venv + systemd.**

1. Raspberry Pi OS Lite (64 bit) per Raspberry Pi Imager aufspielen, SSH aktivieren
2. Python 3.14 installieren (falls die OS-Version älter ist: über `uv python install 3.14`; einfacher als selbst kompilieren)
3. Projekt per `git clone` (privates Repo) oder `scp` kopieren
4. `python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt`
5. `.env` aus `.env.example` anlegen, Token eintragen, `chmod 600 .env`
6. `deploy/vinted-bot.service` nach `/etc/systemd/system/` kopieren:
   - `User=vintedbot` (eigener unprivilegierter Benutzer), `WorkingDirectory=…`, `ExecStart=…/.venv/bin/python -m vinted_bot`
   - `Restart=always`, `RestartSec=30`
7. `sudo systemctl enable --now vinted-bot` · Logs: `journalctl -u vinted-bot -f`
8. Updates: `git pull && pip install -r requirements.txt && sudo systemctl restart vinted-bot`
9. Backup: `sqlite3 data/bot.db ".backup data/backup.db"` per wöchentlichem Cronjob

**Alternative Docker:** `docker compose up -d` mit Volume für `data/` und `restart: unless-stopped`. Funktioniert identisch auf Pi (ARM) und PC.

**Kostenschätzung:**

| Variante | Einmalig | Pro Jahr |
|---|---|---|
| Vorhandener Rechner/Laptop | 0 € | ~10–40 € Strom |
| Raspberry Pi 4 (2–4 GB) neu inkl. Netzteil, SD, Gehäuse | ~90–130 € | ~10–15 € Strom |
| Raspberry Pi 5 (4 GB) inkl. Zubehör | ~140–190 € | ~10–15 € Strom |
| VPS (nicht empfohlen) | 0 € | ~66–80 € |
| Discord, Python, alle Bibliotheken | 0 € | 0 € |

---

## 9. Was du manuell tun musst (alles andere übernehme ich)

1. **Discord-Server:** eigenen Server anlegen (oder vorhandenen nutzen). Unter *Benutzereinstellungen → Erweitert* den **Entwicklermodus** aktivieren.
2. **Bot-Application:** <https://discord.com/developers/applications> → *New Application* → Name → Reiter **Bot** → *Reset Token* → Token kopieren. *Public Bot* **ausschalten**. Privilegierte Intents **nicht** nötig.
3. **Token in `.env` eintragen**: `DISCORD_TOKEN=…` (die Datei lege ich mit Vorlage an). Token niemals in den Chat oder ins Git.
4. **Einladen:** Ich erzeuge den Link. Er hat diese Form:
   `https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&scope=bot+applications.commands&permissions=85008`
   Du gibst mir die **Application ID** (nicht geheim), öffnest den Link und wählst den Server.
5. **IDs kopieren** (Rechtsklick → „ID kopieren“): **Server-ID** und **deine User-ID** → `.env` (`GUILD_ID`, `OWNER_ID`). Den Admin-Kanal kann der Bot selbst anlegen.
6. **M0:** `scripts/probe.py` einmal starten und mir die Ausgabe zeigen (ich gebe dir den genauen Befehl), dazu einmal im Browser prüfen, ob der Kauf-Link mit einer echten Artikel-ID zur Kaufseite führt.
7. **Hosting:** Pi/Rechner bereitstellen und SSH-Zugang einrichten. Die Befehle liefere ich Schritt für Schritt.

---

## 10. Offene Fragen an dich

**Nach M0 (15.10.2026):** F1 → **ehrlicher User-Agent** (hat funktioniert).
**Entschieden am 14.10.2026:** F1 → in M0 beide UAs testen · F2 → Pi/Altgerät zu Hause (Entwicklung auf Windows) · F3 → Vintrez-Optik mit Embed-Gallery-Trick, Fallback 1 Bild · F4 → Enrichment erst in M7, optional.
**Noch offen (bis zur Antwort gilt die Empfehlung):** F5–F9.

| # | Frage | Meine Empfehlung |
|---|---|---|
| **F1** | **User-Agent:** (a) ehrlich, z. B. `VintedPrivateMonitor/1.0` (wird vermutlich geblockt), oder (b) **ein fester, aktueller Browser-UA** wie bei einem normalen Besuch, ohne Rotation und ohne TLS-Imitation. (b) ist branchenüblich, widerspricht aber streng gelesen „keine gefälschten Identitäten“. | In M0 **beides** testen (je 1 Request). Bei Erfolg von (a) nehmen wir (a). Sonst entscheidest du, ob (b) für dich in Ordnung ist |
| **F2** | **Hosting:** Pi kaufen, vorhandenes Gerät, oder vorerst auf deinem Windows-PC? | Entwicklung auf Windows, 24/7 auf Pi/Altgerät zu Hause |
| **F3** | **Galerie:** (A) Vintrez-Optik mit Embed + undokumentiertem Gallery-Trick oder (B) Components V2 (offiziell, aber andere Optik ohne Feld-Raster)? | A, mit Fallback auf 1 Bild |
| **F4** | **Enrichment:** Beschreibung + Fotos 2–3 kosten **einen Zusatz-Request pro neuem Artikel** (Artikelseite). Aktivieren? | Erst ohne (M2–M6), dann optional in M7 mit eigenem Limit |
| **F5** | **Domains:** nur vinted.at, oder auch .de (deutsche Verkäufer, anderer Katalog)? | Beide unterstützen, pro Filter aus dem Link ermittelt |
| **F6** | **Kontotrennung:** Stört es dich, dass der Bot dieselbe Heim-IP nutzt, mit der du eingeloggt auf Vinted surfst? | Akzeptables Restrisiko bei 60-s-Intervall |
| **F7** | **Anzahl Filter** ungefähr? (Bestimmt Intervall und Kategorie-Limit von 50 Kanälen) | ≤ 10 Filter, 60 s |
| **F8** | **Benachrichtigung bei Treffern:** nur Kanalnachricht, oder zusätzlich `@mention` an dich bzw. ein Ping-Kanal fürs Handy? | Kanal-Benachrichtigung per Discord-Kanaleinstellung, kein Code nötig |
| **F9** | **Git:** lokales Repo + privates GitHub-Repo anlegen? | ja, privat (Backup + Deployment per `git pull`) |
