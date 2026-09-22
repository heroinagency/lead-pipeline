# HEROIN Lead-Pipeline

Automatisiertes Sourcing + Scoring von Beauty-/Ästhetikstudios in NRW.
Läuft täglich automatisch über GitHub Actions und schreibt neue Leads
direkt in Supabase — dieselbe Datenbank, aus der das Business-OS-Dashboard
(Pipeline-Board) liest. Kein Google Sheet mehr nötig.

## Was die Pipeline pro Lauf tut

1. Sucht neue Studios über Google Places (Zielbranchen/Städte in `config.py`)
2. Gleicht gegen bereits in Supabase vorhandene Leads ab (keine Duplikate)
3. Liest die Website jedes neuen Leads aus
4. Lässt Claude jeden Lead bewerten: Score (1-10), Segment (warm/kalt/mischtyp),
   Kanal-Empfehlung und einen personalisierten Opener-Satz
5. Schreibt alles in einem Batch nach Supabase (Tabellen `leads` + `scoring`) —
   taucht danach sofort im Dashboard unter "Pipeline" auf
6. Instagram-Check bleibt standardmäßig aus (siehe unten, optional)

## Einmaliges Setup

### 1. Google Places API (Sourcing)

1. [console.cloud.google.com](https://console.cloud.google.com) → Projekt anlegen
2. "Places API (New)" aktivieren
3. API-Schlüssel erstellen → als `GOOGLE_PLACES_API_KEY`

### 2. Anthropic API (Scoring)

In der [Console](https://console.anthropic.com) einen API-Key erstellen →
als `ANTHROPIC_API_KEY`.

### 3. Supabase (Speicherort — bereits vorhanden)

Dasselbe Supabase-Projekt wie das Dashboard, keine neue Einrichtung nötig:

- `SUPABASE_URL`: Project Settings → API → Project URL
- `SUPABASE_SERVICE_ROLE_KEY`: Project Settings → API Keys → "Legacy API Keys"
  → Wert bei `service_role` (beginnt mit `eyJ...`) — **nicht** der `anon`-Key

### 4. Tägliche Automatisierung über GitHub Actions

1. Dieses gesamte Verzeichnis (inkl. des unsichtbaren Ordners `.github`) in ein
   neues, **privates** GitHub-Repository hochladen — entweder per `git push`
   oder einfach per Drag & Drop über "Add file" → "Upload files" auf der
   Repo-Seite (kein Git-Kenntnis nötig)
2. Im Repo unter **Settings → Secrets and variables → Actions → "New repository
   secret"** folgende vier Secrets anlegen:
   - `GOOGLE_PLACES_API_KEY`
   - `ANTHROPIC_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
3. Fertig. Der Workflow in `.github/workflows/daily_run.yml` läuft ab jetzt
   jeden Morgen automatisch (06:00 UTC, also 07-08 Uhr deutscher Zeit je nach
   Jahreszeit). Manuell starten geht jederzeit über den Tab **"Actions" →
   "Tägliche Lead-Pipeline" → "Run workflow"**.
4. Ergebnis jedes Laufs siehst du im Actions-Tab (Log) und direkt im
   Dashboard unter "Pipeline".

### Optional: lokal testen, bevor es auf GitHub läuft

```bash
cp .env.example .env
# .env mit den echten Werten befüllen
pip install -r requirements.txt
python main.py
```

## Instagram-Check (Risiko-Modul, optional)

Standardmäßig **aus**. Wenn aktiviert, sucht die Pipeline den Instagram-Link
direkt auf der Studio-Website und liest darüber öffentliche Kennzahlen aus
(Follower, Postanzahl) — ganz ohne Login.

**Was das bedeutet, bevor du es aktivierst:**
- Rechtlich unbedenklich — es werden nur ohnehin öffentlich sichtbare Zahlen
  gelesen, kein Login, kein Passwortschutz wird umgangen.
- Verstößt aber technisch gegen Instagrams eigene Nutzungsbedingungen — Meta
  kann die anfragende IP-Adresse zeitweise blockieren. Deshalb ist eine feste
  Pause zwischen den Anfragen eingebaut.
- Bricht dadurch nie den Lauf ab — die Instagram-Felder bleiben einfach leer,
  wenn der Abruf scheitert.

**Aktivieren:** In den GitHub Secrets zusätzlich `ENABLE_INSTAGRAM_CHECK=true`
setzen und in `daily_run.yml` den entsprechenden Wert im `env:`-Block anpassen.

## Google Sheets (optionales Backup)

`sheets_client.py` bleibt im Projekt erhalten, wird aber standardmäßig nicht
mehr aufgerufen. Um zusätzlich weiter ins Sheet zu schreiben: Import + Aufruf
in `main.py` wieder einkommentieren und `GOOGLE_SHEETS_ID` +
`GOOGLE_SERVICE_ACCOUNT_JSON` setzen.

## Nächste Ausbaustufe (sobald mehr Budget da ist)

- Warme Leads automatisch in eine WhatsApp/Instagram-Sequenz übergeben
- Kalte Leads automatisch in eine Anruf-Liste sortieren
- Instagram-Check professionalisieren (z. B. über einen bezahlten Anbieter)
