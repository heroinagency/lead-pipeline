"""
Zentrale Konfiguration der Lead-Pipeline.
Lädt alle Zugangsdaten und Einstellungen aus der .env-Datei und
validiert beim Start, dass nichts Wichtiges fehlt - lieber ein klarer
Fehler beim Start als ein kryptischer Absturz mitten im Lauf.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"[Konfiguration] '{name}' fehlt in der .env-Datei. "
            f"Siehe .env.example."
        )
    return value


# --- Pflicht-Zugangsdaten -------------------------------------------------
# Supabase ist die aktuelle Datenquelle fuers Dashboard (siehe supabase_client.py).
# Google Sheets ist nur noch optionales Backup - dafuer sheets_client.py wieder
# in main.py einkommentieren UND unten GOOGLE_SHEETS_ID/GOOGLE_SERVICE_ACCOUNT_JSON
# in der .env setzen. Im Normalfall (Supabase-only) bleiben beide leer.
GOOGLE_PLACES_API_KEY = _require("GOOGLE_PLACES_API_KEY")
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
GOOGLE_SHEETS_ID = os.environ.get("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json"
)

# --- Sucheinstellungen ----------------------------------------------------
SEARCH_QUERIES = [
    "Kosmetikstudio Krefeld",
    "Kosmetikstudio Mönchengladbach",
    "Beauty Studio Düsseldorf",
    "Laserklinik Ästhetik NRW",
    "Kosmetikstudio Duisburg",
]

ANTHROPIC_MODEL = "claude-sonnet-5"
SHEET_TAB_NAME = "Leads"
SCORE_THRESHOLD = 6

# --- Instagram-Check (RISIKO-MODUL, standardmaessig AUS) --------------------
# Instagrams Nutzungsbedingungen verbieten automatisiertes Auslesen.
# Rechtlich kein Risiko fuer euch, operativ aber: Instagram kann die
# anfragende IP zeitweise blockieren. Deshalb per Flag steuerbar und mit
# eingebauter Pause zwischen Anfragen, um moeglichst unauffaellig zu bleiben.
# Ihr entscheidet bewusst, das einzuschalten - siehe README.
ENABLE_INSTAGRAM_CHECK = os.environ.get("ENABLE_INSTAGRAM_CHECK", "false").lower() == "true"
INSTAGRAM_REQUEST_DELAY_SECONDS = 4  # Mindestpause zwischen zwei IG-Abfragen
