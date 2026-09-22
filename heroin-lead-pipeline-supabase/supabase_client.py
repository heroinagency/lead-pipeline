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
