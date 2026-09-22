"""
Baustein 5: Google Sheets als kostenloses Mini-CRM.
"""

from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEETS_ID, SHEET_TAB_NAME
from utils import retry

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = [
    "place_id",
    "name",
    "address",
    "phone",
    "website",
    "rating",
    "rating_count",
    "score",
    "segment",
    "intent_signal",
    "channel",
    "reasoning",
    "opener",
    "instagram_handle",
    "instagram_followers",
    "instagram_check_manuell",
    "status",
]

_worksheet_cache = None


@retry(times=3, delay_seconds=3, exceptions=(gspread.exceptions.APIError,))
def _get_worksheet():
    """Holt (und cached innerhalb eines Laufs) das Ziel-Arbeitsblatt.
    Legt Tabellenblatt und Kopfzeile beim allerersten Lauf automatisch an."""

    global _worksheet_cache
    if _worksheet_cache is not None:
        return _worksheet_cache

    creds = Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_JSON, scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(GOOGLE_SHEETS_ID)

    try:
        worksheet = spreadsheet.worksheet(SHEET_TAB_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=SHEET_TAB_NAME, rows=1000, cols=len(HEADER)
        )
        worksheet.append_row(HEADER)

    if worksheet.row_values(1) != HEADER:
        worksheet.update("A1", [HEADER])

    _worksheet_cache = worksheet
    return worksheet


def get_existing_place_ids() -> set[str]:
    """Liest alle bereits erfassten place_ids, damit wir bei jedem Lauf
    nur wirklich neue Leads hinzufuegen statt Duplikate zu erzeugen."""

    worksheet = _get_worksheet()
    ids = worksheet.col_values(1)  # Spalte A = place_id
    return set(ids[1:])  # erste Zeile ist die Kopfzeile


def _build_row(lead: dict, scoring: dict, instagram_data: dict | None) -> list:
    # Telefonnummern im Format "+49 ..." wuerden Google Sheets sonst als
    # Formelanfang interpretieren (fuehrendes "+") und #ERROR! werfen.
    # Ein fuehrendes Apostroph zwingt die Zelle zu reinem Text, unabhaengig
    # von value_input_option="USER_ENTERED".
    phone = lead.get("phone") or ""
    phone_cell = f"'{phone}" if phone else ""

    return [
        lead["place_id"],
        lead["name"],
        lead["address"],
        phone_cell,
        lead["website"],
        lead.get("rating", ""),
        lead.get("rating_count", ""),
        scoring["score"],
        scoring["segment"],
        scoring["intent_signal"],
        scoring["channel"],
        scoring["reasoning"],
        scoring["opener"],
        instagram_data["handle"] if instagram_data else "",
        instagram_data.get("followers", "") if instagram_data else "",
        "",  # instagram_check_manuell - Platz fuer euren manuellen Check
        "neu",
    ]


def append_leads_batch(rows: list[tuple[dict, dict, dict | None]]) -> None:
    """Schreibt mehrere Leads in EINEM API-Aufruf statt einem pro Zeile.
    Das schont das Google-Sheets-Kontingent (100 Schreibzugriffe/100s)
    deutlich, sobald das Suchvolumen waechst."""

    if not rows:
        return

    worksheet = _get_worksheet()
    values = [_build_row(lead, scoring, ig) for lead, scoring, ig in rows]
    worksheet.append_rows(values, value_input_option="USER_ENTERED")
