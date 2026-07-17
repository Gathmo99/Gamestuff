# Sale Watcher

Zeigt aktuelle Rabatte, zeitlich begrenzt kostenlose Spiele und eine persönliche Wunschliste
für **Steam, Xbox, PlayStation und Epic Games Store** – jeweils als eigene Tab-Gruppe. Steam-
und Xbox-Einträge zeigen zusätzlich, ob das Spiel im Xbox Game Pass (PC und/oder Konsole)
enthalten ist.

Es gibt zwei Varianten:

- **Desktop-App** (`steam_sale_watcher.py`, bzw. `dist/Sale-Watcher.exe`) – lädt alle Daten live
  beim Öffnen, für alle vier Plattformen.
- **Website** (`docs/`) – statische Seite für GitHub Pages. Rabatte/Kostenlos-Tabs kommen aus
  JSON-Dateien unter `docs/data/`, die automatisch per GitHub Actions aktualisiert werden (alle
  6 Stunden) – **nicht** live beim Öffnen der Seite, da keiner der vier Stores direkte
  Browser-Anfragen von fremden Domains erlaubt (kein CORS).

## Wichtig: Wie aktuell sind die Daten?

- **Rabatte / Kostenlos-Tabs** (alle vier Plattformen): statisch, aktualisiert alle 6 Stunden
  durch den GitHub-Actions-Workflow. Beim Öffnen der Seite wird nicht neu geladen, sondern die
  zuletzt committete `docs/data/*.json` angezeigt (Zeitstempel steht oben auf der Seite).
- **Wunschliste Steam/Xbox**: lädt bei jedem Seitenbesuch live die aktuellen Preise – dafür wird
  ein öffentlicher CORS-Proxy angefragt (siehe unten), da eine statische Seite sonst nicht direkt
  mit den Stores sprechen kann.
- **Wunschliste PlayStation & Epic**: **nicht live möglich.** Beide Stores blocken Anfragen von
  fremden Websites strikt – das greift auch über den CORS-Proxy (Sony per Bot-Schutz, Epic per
  Referer-Prüfung). Läuft stattdessen über eine Konfigurationsdatei, siehe unten.

## Plattform-Eigenheiten

Die vier Stores haben unterschiedliche (und unterschiedlich gut zugängliche) öffentliche APIs,
daher unterscheiden sich die Tabs leicht:

- **Steam**: vollständige, paginierte Rabatt-Liste (bis zu 2000 Spiele wählbar).
- **Xbox**: "Rabatte" zeigt nur eine **Momentaufnahme der aktuellen Top-Deals** (~25 Spiele) –
  Xbox bietet keine öffentlich zugängliche, vollständig paginierbare Liste wie Steam.
  "Free Play Days" (zeitlich begrenzte Gratis-Testphasen für Gold-/Game-Pass-Mitglieder) ist
  leer, wenn gerade keine Aktion läuft (Xbox liefert dafür keine eigene Seite/Daten, wenn
  nichts aktiv ist – das ist ein normaler Zustand, kein Fehler).
- **PlayStation**: "Rabatte" ist eine vollständige, paginierte Liste (ähnlich wie Steam).
  "PS Plus Monatsspiele" statt "Aktuell kostenlos" – PlayStation hat kein Steam-artiges
  "zeitlich begrenzt kostenlos"-Konzept, das nächstliegende Äquivalent sind die monatlichen
  PS-Plus-Spiele (benötigen ein aktives Abo). Kein Game-Pass-Abgleich (nicht relevant für
  PlayStation-exklusive Titel).
- **Epic Games Store**: vollständige, paginierte Rabatt-Liste. "Aktuell kostenlos" sind Epics
  bekannte wöchentliche Gratis-Spiele (echt zeitlich begrenzt, wie bei Steam). Kein
  Game-Pass-Abgleich.

## Website auf GitHub Pages einrichten

1. Dieses Verzeichnis in ein GitHub-Repository pushen.
2. Im Repo unter **Settings → Actions → General → Workflow permissions** die Option
   **"Read and write permissions"** aktivieren (damit der Workflow `docs/data/*.json`
   committen darf).
3. Unter **Settings → Pages**: Source = "Deploy from a branch", Branch = `main`, Ordner = `/docs`.
4. Einmal manuell den Workflow **"Update Steam Sale Data"** unter dem Actions-Tab ausführen
   (Button "Run workflow"), damit `docs/data/*.json` mit echten Daten befüllt wird.
5. Die Seite ist danach unter `https://<username>.github.io/<repo>/` erreichbar.

Der Workflow läuft danach automatisch alle 6 Stunden. Da PlayStations API eine inoffizielle,
nicht dokumentierte GraphQL-Signatur (`sha256Hash`) verwendet und Epics API eine strikte
Referer-Prüfung hat, können diese beiden Teile brechen, falls die Anbieter etwas ändern – die
anderen Plattformen laufen in dem Fall unabhängig weiter (jede Plattform wird einzeln versucht,
ein Fehler bei einer bricht die anderen nicht ab).

## Wunschliste

**Steam & Xbox (Website):** Liegt nur lokal in deinem Browser (localStorage) – nicht im
Repository, nicht auf einem Server. "+ Spiel hinzufügen" sucht live im jeweiligen Store (über
einen öffentlichen CORS-Proxy) und speichert Treffer im Browser. Andere Geräte/Browser haben
eine eigene, leere Wunschliste. Der Proxy (`corsproxy.io`, mit `allorigins.win` als Fallback)
ist ein kostenloser Drittanbieter-Dienst ohne Verfügbarkeitsgarantie – falls die Suche mal nicht
lädt, liegt es meist daran.

**PlayStation & Epic (Website):** `ps-wishlist.json` bzw. `epic-wishlist.json` im Repo-Root
bearbeiten und pushen:

```json
[
  { "appid": "EP4295-CUSA17368_00-TITEUFMEGA0000EU", "name": "Spielname" }
]
```

Die App-ID steht bei PlayStation in der Store-URL (`store.playstation.com/de-de/product/<appid>`),
bei Epic ist es die interne Katalog-ID (am einfachsten über die Desktop-App-Suche herausfinden).
Preise werden beim nächsten Workflow-Lauf abgerufen und unter `docs/data/ps_wishlist.json` bzw.
`docs/data/epic_wishlist.json` angezeigt.

**Desktop-App (alle vier Plattformen):** Komplett unabhängig von der Website – eigene
Speicherung unter `%APPDATA%\SteamSaleWatcher\` (`wunschliste.json`, `xbox_wunschliste.json`,
`ps_wunschliste.json`, `epic_wunschliste.json`). Steam/Xbox/Epic haben ein Such-Fenster,
PlayStation ein "Store-Link einfügen"-Fenster (dort direkt gegen die jeweilige API, kein Proxy
nötig, da kein Browser-Kontext).

## Desktop-App

Einfach `dist/Sale-Watcher.exe` starten (kein Python nötig).

Zum selbst Bauen: `python -m PyInstaller --onefile --windowed --name "Sale-Watcher" steam_sale_watcher.py`

## Daten manuell aktualisieren (lokal)

```
python scripts/fetch_data.py
```

Schreibt `docs/data/discounts.json`, `free.json`, `xbox_discounts.json`, `xbox_free.json`,
`ps_discounts.json`, `ps_free.json`, `ps_wishlist.json`, `epic_discounts.json`, `epic_free.json`,
`epic_wishlist.json`, `gamepass.json` und `meta.json`.
