"""
Baustein 1: Lead-Sourcing.

Fragt die Google Places API (Text Search, "New" Endpoint) fuer jede
konfigurierte Suchanfrage ab und liefert eine saubere Liste roher
Lead-Datensaetze zurueck - unabhaengig von Enrichment/Scoring, damit sich
die API spaeter leicht gegen Apify o.ae. tauschen laesst, ohne den Rest
der Pipeline anzufassen.
"""

import requests

from config import GOOGLE_PLACES_API_KEY
from utils import retry

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

# Nur die Felder abfragen, die wir wirklich brauchen - Google berechnet
# nach Field Mask, das haelt die Kosten pro Anfrage niedrig.
# "regularOpeningHours" ist das verlaesslichere Feld gegenueber
# "currentOpeningHours" (das bei Sonderfaellen wie Feiertagen leer sein kann).
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.regularOpeningHours",
        "places.priceLevel",
    ]
)


@retry(times=3, delay_seconds=2, exceptions=(requests.RequestException,))
def _post_search(query: str, max_results: int) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    payload = {"textQuery": query, "languageCode": "de", "maxResultCount": max_results}

    response = requests.post(PLACES_URL, json=payload, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def search_places(query: str, max_results: int = 20) -> list[dict]:
    """Fuehrt eine einzelne Textsuche aus und gibt eine Liste normalisierter
    Lead-Dicts zurueck. Netzwerkfehler werden ueber @retry automatisch
    bis zu dreimal wiederholt, bevor sie an den Aufrufer durchgereicht werden."""

    data = _post_search(query, max_results)

    leads = []
    for place in data.get("places", []):
        place_id = place.get("id")
        if not place_id:
            continue  # unbrauchbarer Datensatz ohne eindeutige ID, ueberspringen

        opening_hours = place.get("regularOpeningHours", {}).get(
            "weekdayDescriptions", []
        )

        leads.append(
            {
                "place_id": place_id,
                "name": place.get("displayName", {}).get("text", "").strip(),
                "address": place.get("formattedAddress", ""),
                "phone": place.get("internationalPhoneNumber", ""),
                "website": place.get("websiteUri", ""),
                "rating": place.get("rating"),
                "rating_count": place.get("userRatingCount"),
                "opening_hours": opening_hours,
                "price_level": place.get("priceLevel"),
                "source_query": query,
            }
        )
    return leads


def source_all_leads(queries: list[str]) -> list[dict]:
    """Fuehrt alle konfigurierten Suchanfragen aus und dedupliziert ueber
    place_id (dieselbe Adresse kann bei mehreren Suchbegriffen auftauchen)."""

    seen_ids: set[str] = set()
    all_leads: list[dict] = []

    for query in queries:
        try:
            results = search_places(query)
        except requests.RequestException as exc:
            print(f"[Sourcing] Suche '{query}' endgueltig fehlgeschlagen: {exc}")
            continue

        for lead in results:
            if lead["place_id"] not in seen_ids:
                seen_ids.add(lead["place_id"])
                all_leads.append(lead)

    return all_leads
