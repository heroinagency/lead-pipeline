"""
Baustein 5 (neu): Supabase statt Google Sheets als Speicherort.

Ersetzt sheets_client.py 1:1 in main.py. Gleiche Grundidee wie vorher -
Leads werden gesammelt und am Ende eines Laufs in einem Batch geschrieben -
nur dass das Ziel jetzt drei Tabellen in Supabase sind (leads, scoring,
status) statt Zeilen in einem Sheet. Das Sheet kann als Backup parallel
weiterlaufen, solange sheets_client.py zusaetzlich aufgerufen wird -
supabase_client.py schreibt unabhaengig davon.

Benoetigt in der .env:
    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=eyJ...        (Project Settings -> API,
                                              NICHT der "anon"-Key - der
                                              Service-Key umgeht Row Level
                                              Security, was hier gewollt
                                              ist, weil nur dieses Skript
                                              serverseitig schreibt)

Setup: einmalig supabase/schema.sql im Supabase SQL-Editor ausfuehren
(im Ordner heroin-acquisition-os/app des Dashboard-Projekts).
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from supabase import create_client, Client

from utils import retry

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"[Konfiguration] '{name}' fehlt in der .env-Datei. "
            f"Siehe .env.example bzw. README, Abschnitt 'Supabase'."
        )
    return value


SUPABASE_URL = _require("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = _require("SUPABASE_SERVICE_ROLE_KEY")

_client: Client | None = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _client


@retry(times=3, delay_seconds=3, exceptions=(Exception,))
def get_existing_place_ids() -> set[str]:
    """Liest alle bereits erfassten place_ids, damit wir bei jedem Lauf
    nur wirklich neue Leads hinzufuegen statt Duplikate zu erzeugen."""

    client = _get_client()
    result = client.table("leads").select("place_id").execute()
    return {row["place_id"] for row in result.data}


def start_run() -> str:
    """Legt eine neue Zeile in 'runs' an und gibt deren id zurueck, damit
    alle in diesem Lauf gefundenen Leads darauf verweisen koennen."""

    client = _get_client()
    result = client.table("runs").insert({}).execute()
    return result.data[0]["id"]


def finish_run(run_id: str, stats: dict) -> None:
    """Traegt die Zusammenfassung eines Laufs nach (main.py ruft das am
    Ende auf, wenn alle Zahlen feststehen)."""

    client = _get_client()
    client.table("runs").update(
        {
            "finished_at": "now()",
            "leads_found": stats.get("leads_found", 0),
            "leads_new": stats.get("leads_new", 0),
            "warm_count": stats.get("warm_count", 0),
            "kalt_count": stats.get("kalt_count", 0),
            "mischtyp_count": stats.get("mischtyp_count", 0),
        }
    ).eq("id", run_id).execute()


def _build_lead_row(lead: dict, run_id: str) -> dict:
    return {
        "place_id": lead["place_id"],
        "name": lead["name"],
        "address": lead["address"],
        "phone": lead.get("phone") or None,
        "website": lead["website"],
        "rating": lead.get("rating"),
        "rating_count": lead.get("rating_count"),
        "run_id": run_id,
        "source_query": lead.get("source_query"),
    }


def _build_scoring_row(lead: dict, scoring: dict, instagram_data: dict | None) -> dict:
    return {
        "lead_id": lead["place_id"],
        "score": scoring["score"],
        "segment": scoring["segment"],
        "channel": scoring["channel"],
        "intent_signal": scoring["intent_signal"],
        "reasoning": scoring["reasoning"],
        "opener": scoring["opener"],
        "instagram_handle": instagram_data["handle"] if instagram_data else None,
        "instagram_followers": instagram_data.get("followers") if instagram_data else None,
        "instagram_check_manuell": None,
    }


@retry(times=3, delay_seconds=3, exceptions=(Exception,))
def append_leads_batch(run_id: str, rows: list[tuple[dict, dict, dict | None]]) -> None:
    """Schreibt mehrere Leads in wenigen Batch-Aufrufen statt einem pro
    Zeile: je ein Upsert fuer 'leads' und 'scoring'. 'status' wird bewusst
    NICHT hier angelegt - das passiert per Datenbank-Default ('neu'), sobald
    im Dashboard zum ersten Mal jemand den Status aendert."""

    if not rows:
        return

    client = _get_client()

    lead_records = [_build_lead_row(lead, run_id) for lead, _, _ in rows]
    scoring_records = [
        _build_scoring_row(lead, scoring, instagram_data)
        for lead, scoring, instagram_data in rows
    ]

    client.table("leads").upsert(lead_records).execute()
    client.table("scoring").upsert(scoring_records).execute()


# --- Suchbegriffe (Baustein 6: dashboard-gepflegte Queries + KI-Vorschlaege) --

from config import FALLBACK_SEARCH_QUERIES


@retry(times=3, delay_seconds=3, exceptions=(Exception,))
def get_active_search_queries() -> list[str]:
    """Liest die aktuell aktiven Suchbegriffe aus Supabase - dashboard-gepflegt
    statt hartcodiert. Faellt auf FALLBACK_SEARCH_QUERIES zurueck, falls
    Supabase (noch) keine aktiven Queries hat, damit ein leeres Ergebnis nie
    versehentlich einen kompletten Lauf ohne Suche verursacht."""

    client = _get_client()
    result = (
        client.table("search_queries")
        .select("query")
        .eq("status", "active")
        .execute()
    )
    queries = [row["query"] for row in result.data]
    if not queries:
        print(
            "[Suchbegriffe] Keine aktiven Queries in Supabase gefunden - "
            "nutze eingebauten Fallback aus config.py."
        )
        return list(FALLBACK_SEARCH_QUERIES)
    return queries


@retry(times=3, delay_seconds=3, exceptions=(Exception,))
def get_run_query_stats(run_id: str) -> list[dict]:
    """Aggregiert pro Suchbegriff, wie viele der in DIESEM Lauf neu
    gefundenen Leads warm/mischtyp/kalt/unklar bewertet wurden. Basis fuer
    die KI-Vorschlagslogik in query_suggestions.py: Suchbegriffe mit hoher
    warm-Quote sind das Muster, das wir mit neuen Staedten/Branchen
    wiederholen wollen."""

    client = _get_client()
    result = (
        client.table("leads")
        .select("source_query, scoring(segment)")
        .eq("run_id", run_id)
        .execute()
    )

    stats: dict[str, dict[str, int]] = {}
    for row in result.data:
        query = row.get("source_query") or "(unbekannt)"
        scoring_data = row.get("scoring")
        if isinstance(scoring_data, list):
            scoring_data = scoring_data[0] if scoring_data else {}
        segment = (scoring_data or {}).get("segment") or "unklar"
        bucket = stats.setdefault(
            query, {"total": 0, "warm": 0, "mischtyp": 0, "kalt": 0, "unklar": 0}
        )
        bucket["total"] += 1
        bucket[segment] = bucket.get(segment, 0) + 1

    return [{"query": query, **counts} for query, counts in stats.items()]


@retry(times=2, delay_seconds=3, exceptions=(Exception,))
def get_existing_query_texts() -> set[str]:
    """Alle bereits bekannten Suchbegriffe (aktiv, vorgeschlagen ODER
    abgelehnt) - damit die KI nicht staendig denselben Vorschlag wiederholt."""

    client = _get_client()
    result = client.table("search_queries").select("query").execute()
    return {row["query"] for row in result.data}


@retry(times=2, delay_seconds=3, exceptions=(Exception,))
def insert_query_suggestions(suggestions: list[dict]) -> int:
    """Schreibt neue KI-Vorschlaege (status='suggested') nach Supabase.
    Erwartet je Eintrag {'query': str, 'reasoning': str}. Ueberspringt
    Duplikate stillschweigend (unique-Constraint auf 'query') statt den
    ganzen Lauf daran scheitern zu lassen."""

    if not suggestions:
        return 0

    client = _get_client()
    written = 0
    for s in suggestions:
        try:
            client.table("search_queries").insert(
                {
                    "query": s["query"],
                    "status": "suggested",
                    "source": "ai",
                    "reasoning": s.get("reasoning", ""),
                }
            ).execute()
            written += 1
        except Exception as exc:
            # Vermutlich Duplikat (unique-Constraint) - kein Grund, den Lauf
            # abzubrechen, einfach ueberspringen.
            print(f"[Vorschlaege] Konnte '{s.get('query')}' nicht speichern: {exc}")
    return written
