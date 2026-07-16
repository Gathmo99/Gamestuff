# Steam Sale Watcher

Zeigt aktuelle Steam-Rabatte, zeitlich begrenzt kostenlose Spiele und eine persönliche
Wunschliste – jeweils mit Hinweis, ob das Spiel im Xbox Game Pass (PC und/oder Konsole)
enthalten ist.

Es gibt zwei Varianten:

- **Desktop-App** (`steam_sale_watcher.py`, bzw. `dist/Steam-Sale-Watcher.exe`) – lädt die
  Daten live beim Öffnen.
- **Website** (`docs/`) – statische Seite für GitHub Pages, die Daten kommen aus JSON-Dateien
  unter `docs/data/`, die automatisch per GitHub Actions aktualisiert werden (alle 6 Stunden).

## Website auf GitHub Pages einrichten

1. Dieses Verzeichnis in ein GitHub-Repository pushen.
2. Im Repo unter **Settings → Actions → General → Workflow permissions** die Option
   **"Read and write permissions"** aktivieren (damit der Workflow `docs/data/*.json`
   committen darf).
3. Unter **Settings → Pages**: Source = "Deploy from a branch", Branch = `main`, Ordner = `/docs`.
4. Einmal manuell den Workflow **"Update Steam Sale Data"** unter dem Actions-Tab ausführen
   (Button "Run workflow"), damit `docs/data/*.json` mit echten Daten befüllt wird.
5. Die Seite ist danach unter `https://<username>.github.io/<repo>/` erreichbar.

Der Workflow läuft danach automatisch alle 6 Stunden und bei jeder Änderung an `wishlist.json`.

## Spiele zur Wunschliste hinzufügen (Website)

`wishlist.json` im Repo-Root bearbeiten (Format unten) und pushen. Die Preise werden beim
nächsten Workflow-Lauf abgerufen:

```json
[
  { "appid": 1091500, "name": "Cyberpunk 2077" }
]
```

Die App-ID steht in der Steam-Store-URL des Spiels, z. B.
`store.steampowered.com/app/1091500/...` → `1091500`.

## Desktop-App

Einfach `dist/Steam-Sale-Watcher.exe` starten (kein Python nötig). Die Wunschliste dort hat
ein eigenes "Spiel hinzufügen"-Suchfeld und ist unabhängig von `wishlist.json`.

Zum selbst Bauen: `python -m PyInstaller --onefile --windowed --name "Steam-Sale-Watcher" steam_sale_watcher.py`

## Daten manuell aktualisieren (lokal)

```
python scripts/fetch_data.py
```

Schreibt `docs/data/discounts.json`, `free.json`, `wishlist.json` und `meta.json`.
