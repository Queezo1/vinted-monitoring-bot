# Vinted-Bot (privat)

Privater Discord-Bot, der Vinted-Filter überwacht und neue Artikel postet. Den Plan mit Recherche,
Architektur und Meilensteinen findest du in [PLAN.md](PLAN.md).

**Stand: M6.** Filter werden per Slash-Command in Discord verwaltet. Jeder Filter bekommt einen eigenen Kanal in der Kategorie „Vinted“. Posts zeigen bis zu 3 Fotos, eine Kurzbeschreibung, die Verkäufer-Flagge und die Bewertung.

## Slash-Commands (nur für dich sichtbar)

| Befehl | Wirkung |
|---|---|
| `/filter add link:<Vinted-Link> [name] [laender:at,de] [intervall:60] [kanal]` | Filter anlegen. Ohne `kanal` legt der Bot einen eigenen Kanal an. |
| `/filter list` | Alle Filter mit Status, Ländern und letzter Abfrage |
| `/filter pause` / `/filter resume` | Filter anhalten bzw. fortsetzen |
| `/filter laender filter:<…> kuerzel:at,de` | Länderfilter ändern (leer = alle Länder) |
| `/filter intervall filter:<…> sekunden:120` | Abfrageintervall ändern (30–3600 s) |
| `/filter remove filter:<…> [kanal_loeschen]` | Filter löschen, optional samt Kanal |

Beim Parameter `filter` schlägt Discord deine Filter automatisch vor. Bei einem neuen Filter merkt sich der Bot beim ersten Abruf nur den aktuellen Stand. Gepostet wird erst ab dem nächsten neuen Artikel.

## Autostart beim Windows-Login (M6)

Der Bot läuft, solange dein PC an ist. Er startet unsichtbar bei der Anmeldung und endet beim Herunterfahren. Nach dem Einschalten postet er verpasste Artikel nach, bis zu 10 pro Filter.

| Zweck | Befehl (PowerShell im Projektordner) |
|---|---|
| Autostart einrichten und gleich starten | `powershell -ExecutionPolicy Bypass -File scripts\windows\install_autostart.ps1 -StartNow` |
| Bot manuell starten | `powershell -ExecutionPolicy Bypass -File scripts\windows\start_bot.ps1` |
| Bot beenden | `powershell -ExecutionPolicy Bypass -File scripts\windows\stop_bot.ps1` |
| Autostart entfernen | `powershell -ExecutionPolicy Bypass -File scripts\windows\uninstall_autostart.ps1` |
| Log ansehen | `Get-Content logs\bot.log -Tail 30 -Wait -Encoding utf8` |

Der Bot läuft immer nur einmal. Ein zweiter Start beendet sich sofort, die Sperrdatei ist `data\bot.lock`. Ist das Netzwerk direkt nach dem Login noch nicht bereit, versucht er es nach 30 s erneut, bei weiteren Fehlschlägen mit steigenden Pausen bis 5 min. Mit `/status` in Discord siehst du jederzeit, ob er läuft.

## Tempo: wie schnell sehe ich neue Artikel?

- **Takt:** Höchstens 20 Anfragen/min an Vinted (3 s Abstand), verteilt auf alle Filter. Pro Filter mindestens alle 10 s. Bei mehreren Filtern wird das automatisch gestreckt: 6 Filter × 3 s × 1,5 ≈ 27 s. `/status` zeigt den aktuellen Wert.
- **Sofort posten:** Neue Artikel gehen direkt nach dem Abruf raus. Fotos 2–3, Beschreibung und bei Filtern ohne Länderfilter auch Flagge und Bewertung werden danach nachgeladen, und die Nachricht wird ergänzt.
- **Länderfilter kosten Zeit:** Für jeden noch unbekannten Verkäufer ist vor dem Posten eine Abfrage nötig. Bekannte Verkäufer liegen 30 Tage im Cache.
- **Selbstschutz:** Nach 429/403 bremst der Bot selbst ab (Abstand × 1,5, bis 10 s) und wird nach 30 ruhigen Minuten wieder schneller. Schneller als 20 Anfragen/min ist bewusst nicht einstellbar (Minimum 2 s ≈ 30/min).
- **Grenzen:** Vinted prüft neue Inserate teils, bevor sie in der Suche erscheinen. Diese Verzögerung kann kein Bot umgehen. Kommerzielle Bots, die mit vielen IPs (Proxys) im Sekundentakt abfragen, sind ggf. schneller. Das schließen die Leitplanken dieses Projekts bewusst aus.

## Sicherheit

- **`.env` schützen:** Sie enthält dein Bot-Token. Auf `D:\` erbt sie die Rechte des Laufwerks und ist für alle lokalen Benutzer lesbar. Einmalig ausführen (PowerShell im Projektordner):
  `icacls .env /inheritance:r /grant:r "${env:USERNAME}:(R,W)"`
- **Token geleakt?** Im Developer Portal unter *Bot → Reset Token* ein neues Token erzeugen, in die `.env` eintragen und den Bot neu starten.
- **Was der Bot absichert:** Er pingt niemanden (`AllowedMentions.none`). Texte von Verkäufern werden entschärft, sodass getarnte Links nicht klickbar sind. Artikel-Links sind nur auf `vinted.at`/`vinted.de` erlaubt. Antworten von Vinted sind auf 8 MB begrenzt. Nur `OWNER_ID` darf Commands nutzen, auch die Autovervollständigung.
- **Prüfen:** `pip-audit -r requirements.txt` sucht nach bekannten Lücken in den Abhängigkeiten (Stand 15.10.2026: keine).

## Einrichtung (Windows, PowerShell)

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e . --no-deps
copy .env.example .env   # danach .env ausfüllen
```

## Discord vorbereiten (einmalig, siehe PLAN.md Abschnitt 9)

1. <https://discord.com/developers/applications> → *New Application* → Reiter **Bot** → *Reset Token*. Token in `.env` als `DISCORD_TOKEN` eintragen. *Public Bot* ausschalten.
2. Einladen: `https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&scope=bot+applications.commands&permissions=85008`
3. Discord-Entwicklermodus an (Einstellungen → Erweitert), dann per Rechtsklick „ID kopieren“ und in die `.env` eintragen: Server → `GUILD_ID`, dein Name → `OWNER_ID`.
4. Filter danach per `/filter add` anlegen. Die `SEED_*`-Werte in der `.env` werden nur bei der Ersteinrichtung genutzt, also solange noch kein Filter existiert.

## Befehle

| Zweck | Befehl |
|---|---|
| Bot starten | `python -m vinted_bot` (Beenden mit Strg+C) |
| Tests | `pytest` |
| Formatieren (2 Leerzeichen) | `ruff format .` |
| Linter | `ruff check .` |
| Link-Mapping prüfen (ohne Netzwerk) | `python -m scripts.probe "<Filter-Link>" --dry-run` |

Logs landen in der Konsole und in `logs/bot.log`, die Datenbank liegt in `data/bot.db`.

## M2-Abnahme (manuell)

1. Bot starten. Im Log erscheinen „Start-Filter … angelegt“ und „Baseline mit N Artikeln gesetzt“. Im Kanal wird **nichts** gepostet.
2. Etwa 30 Minuten laufen lassen. Neue passende Artikel erscheinen im Kanal, mit Flagge, Marke, Größe, Zustand, Bewertung und Preis.
3. Mit Strg+C beenden und neu starten. Es entstehen **keine Doppelposts**.
4. Mit Länderfilter: Flaggen anderer Länder dürfen nicht auftauchen. Ausnahme ist ❔ für „Land unbekannt“, solche Artikel werden absichtlich trotzdem gepostet.

## Live-Proben (nur bei Bedarf, einmalig und manuell)

- `python -m scripts.probe "<Link>"` testet Session, Katalog und Feld-Inventar und speichert eine anonymisierte Fixture.
- `python -m scripts.probe_extra "<Link>"` testet Länder-IDs, den Verkäufer-Endpunkt und die Artikelseite.
- Bei einem Fehlschlag **nicht wiederholt starten**, sondern die Ausgabe weitergeben.
