# Steam Sale Watcher

Zeigt aktuelle Steam-Rabatte, zeitlich begrenzt kostenlose Spiele und eine persönliche
Wunschliste – jeweils mit Hinweis, ob das Spiel im Xbox Game Pass (PC und/oder Konsole)
enthalten ist.

Es gibt zwei Varianten:

- **Desktop-App** (`steam_sale_watcher.py`, bzw. `dist/Steam-Sale-Watcher.exe`) – lädt die
  Daten live beim Öffnen.
- **Website** (`docs/`) – statische Seite für GitHub Pages. Rabatte und Gratis-Spiele kommen
  aus JSON-Dateien unter `docs/data/`, die automatisch per GitHub Actions aktualisiert werden
  (alle 6 Stunden) – **nicht** live beim Öffnen der Seite, da Steams API keine direkten
  Browser-Anfragen von fremden Domains erlaubt (kein CORS).

## Wichtig: Wie aktuell sind die Daten?

- **Rabatte / Aktuell kostenlos**: statisch, aktualisiert alle 6 Stunden durch den GitHub-Actions-
  Workflow. Beim Öffnen der Seite wird nicht neu von Steam geladen, sondern die zuletzt
  committete `docs/data/*.json` angezeigt (Zeitstempel steht oben auf der Seite).
- **Wunschliste**: lädt bei jedem Seitenbesuch live die aktuellen Preise – dafür wird ein
  öffentlicher CORS-Proxy angefragt (siehe unten), da eine statische Seite sonst nicht direkt
  mit Steam sprechen kann.

## Website auf GitHub Pages einrichten

1. Dieses Verzeichnis in ein GitHub-Repository pushen.
2. Im Repo unter **Settings → Actions → General → Workflow permissions** die Option
   **"Read and write permissions"** aktivieren (damit der Workflow `docs/data/*.json`
   committen darf).
3. Unter **Settings → Pages**: Source = "Deploy from a branch", Branch = `main`, Ordner = `/docs`.
4. Einmal manuell den Workflow **"Update Steam Sale Data"** unter dem Actions-Tab ausführen
   (Button "Run workflow"), damit `docs/data/*.json` mit echten Daten befüllt wird.
5. Die Seite ist danach unter `https://<username>.github.io/<repo>/` erreichbar.

Der Workflow läuft danach automatisch alle 6 Stunden.

## Wunschliste (Website)

Liegt **nur lokal in deinem Browser** (localStorage) – nicht im Repository, nicht auf einem
Server, nicht in `docs/data/`. "+ Spiel hinzufügen" auf der Seite sucht direkt bei Steam (über
einen öffentlichen CORS-Proxy, da Steam selbst keine Browser-Anfragen von fremden Seiten
zulässt) und speichert Treffer im Browser. Andere Geräte/Browser haben eine eigene, leere
Wunschliste. Ein kurzer Hinweis dazu (keine Cookies, nur localStorage, Proxy-Nutzung) wird
beim ersten Besuch angezeigt.

Der Proxy (`corsproxy.io`, mit `allorigins.win` als Fallback) ist ein kostenloser Drittanbieter-
Dienst ohne Verfügbarkeitsgarantie – falls die Wunschlisten-Suche mal nicht lädt, liegt es meist
daran und nicht an der Seite selbst.

## Desktop-App

Einfach `dist/Steam-Sale-Watcher.exe` starten (kein Python nötig). Die Wunschliste dort ist
komplett unabhängig von der Website – eigene Speicherung unter
`%APPDATA%\SteamSaleWatcher\wunschliste.json`, eigenes Suchfeld, kein CORS-Proxy nötig (die
Desktop-App ruft Steam direkt auf, das ist kein Browser-Kontext).

Zum selbst Bauen: `python -m PyInstaller --onefile --windowed --name "Steam-Sale-Watcher" steam_sale_watcher.py`

## Daten manuell aktualisieren (lokal)

```
python scripts/fetch_data.py
```

Schreibt `docs/data/discounts.json`, `free.json`, `gamepass.json` und `meta.json`.
