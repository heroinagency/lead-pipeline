"""
Hauptskript der HEROIN Lead-Pipeline.

Ablauf pro Lauf (taeglich per GitHub Actions oder manuell):
  1. Sourcing:      neue Leads ueber Google Places holen
  2. Dedup:         gegen bereits im Sheet vorhandene place_ids abgleichen
  3. Website:       Text lesen + ggf. Instagram-Link im HTML finden
  4. Instagram:      (nur wenn ENABLE_INSTAGRAM_CHECK=true) Kennzahlen holen
  5. Scoring:        Claude bewertet Segment, Score, Kanal, Opener-Text
  6. Schreiben:       alle Ergebnisse in einem Batch ins Google Sheet

Aufruf:
    python main.py
"""

from __future__ import annotations

import re
from collections import Counter

from config import SCORE_THRESHOLD, ENABLE_INSTAGRAM_CHECK
from sourcing import source_all_leads
from website import (
    fetch_website_html,
    fetch_website_text,
    extract_instagram_handle,
    extract_email,
)
from enrichment import score_lead
from query_suggestions import generate_and_store_suggestions

# Supabase ist jetzt die Quelle fuers Dashboard. sheets_client.py bleibt im
# Projekt erhalten (Backup/Uebergang) - um wieder zusaetzlich ins Sheet zu
# schreiben, einfach den Import + den Aufruf in run() wieder einkommentieren.
from supabase_client import (
    get_active_search_queries,
    get_existing_place_ids,
    get_run_query_stats,
    update_query_performance,
    start_run,
    finish_run,
    append_leads_batch,
)
# from sheets_client import append_leads_batch as append_leads_batch_sheet

if ENABLE_INSTAGRAM_CHECK:
    from instagram_check import get_instagram_profile

# Manche Unternehmen haben keinen eigenen Webauftritt und hinterlegen bei
# Google Places stattdessen direkt ihre Instagram-Seite als "Website" - der
# Handle steht dann schon in der URL selbst, ein Crawl waere sinnlos
# (Instagram blockt anonyme Aufrufe seiner eigenen Profilseiten meist ohnehin).
INSTAGRAM_URL_RE = re.compile(r"instagram\.com/([A-Za-z0-9._]+)", re.IGNORECASE)


def process_lead(lead: dict) -> tuple[dict, dict, dict | None]:
    """Reichert einen einzelnen Lead an und bewertet ihn. Gibt
    (lead, scoring, instagram_data) zurueck. Der Instagram-HANDLE wird
    IMMER versucht zu finden (reines Auslesen der oeffentlichen Website
    des Leads, kein Instagram-Zugriff, daher kein Risiko und immer an) -
    nur die zusaetzlichen Profildaten (Follower/Posts/Bio) kommen dazu,
    wenn ENABLE_INSTAGRAM_CHECK an ist (das liest Instagram selbst aus,
    siehe instagram_check.py). None, wenn gar kein Handle gefunden wurde.
    Die E-Mail (falls auf der Website auffindbar) landet direkt im
    zurueckgegebenen lead-dict unter 'email'."""

    website = lead.get("website") or ""
    instagram_url_match = INSTAGRAM_URL_RE.search(website)

    if instagram_url_match:
        handle = instagram_url_match.group(1).strip("/").lower()
        website_text = (
            "(Kein eigener Webauftritt - das Unternehmen nutzt seine "
            "Instagram-Seite direkt als Online-Praesenz, siehe Handle unten)"
        )
        email = None
    else:
        html = fetch_website_html(website)
        website_text = fetch_website_text(html)
        handle = extract_instagram_handle(html)
        email = extract_email(html)

    instagram_data = None
    if handle:
        if ENABLE_INSTAGRAM_CHECK:
            instagram_data = get_instagram_profile(handle)
        if not instagram_data:
            # Instagram-Profil-Check aus oder fehlgeschlagen - trotzdem
            # wenigstens den Handle festhalten, den finden wir immer.
            instagram_data = {
                "handle": handle,
                "followers": None,
                "posts": None,
                "bio": None,
                "is_private": None,
            }

    if email:
        lead = {**lead, "email": email}

    scoring = score_lead(lead, website_text, instagram_data)
    return lead, scoring, instagram_data


def run():
    print("Starte Lauf ...")
    print(f"Instagram-Check: {'AN' if ENABLE_INSTAGRAM_CHECK else 'AUS'}")

    run_id = start_run()
    existing_ids = get_existing_place_ids()
    print(f"{len(existing_ids)} Leads bereits in Supabase.")

    search_queries = get_active_search_queries()
    print(f"{len(search_queries)} aktive Suchbegriffe (aus Supabase/Dashboard).")

    raw_leads = source_all_leads(search_queries)
    new_leads = [lead for lead in raw_leads if lead["place_id"] not in existing_ids]
    print(f"{len(raw_leads)} Leads gefunden, davon {len(new_leads)} neu.\n")

    results = []
    warm_count = kalt_count = mischtyp_count = 0

    for lead in new_leads:
        lead, scoring, instagram_data = process_lead(lead)
        results.append((lead, scoring, instagram_data))

        marker = "🔥" if scoring["score"] >= SCORE_THRESHOLD else "  "
        ig_note = f" | IG @{instagram_data['handle']}" if instagram_data else ""
        signal_note = f" | Signal: {scoring['intent_signal']}" if scoring["intent_signal"] else ""
        print(
            f"{marker} {lead['name']:<40} "
            f"Score {scoring['score']:>2} | {scoring['segment']:<8} | "
            f"{scoring['channel']}{ig_note}{signal_note}"
        )

        if scoring["segment"] == "warm":
            warm_count += 1
        elif scoring["segment"] == "kalt":
            kalt_count += 1
        elif scoring["segment"] == "mischtyp":
            mischtyp_count += 1

    append_leads_batch(run_id, results)
    finish_run(
        run_id,
        {
            "leads_found": len(raw_leads),
            "leads_new": len(new_leads),
            "warm_count": warm_count,
            "kalt_count": kalt_count,
            "mischtyp_count": mischtyp_count,
        },
    )

    print("\nZusammenfassung:")
    print(f"  Neue Leads gesamt: {len(new_leads)}")
    print(f"  Warm  (-> automatisierte Ansprache): {warm_count}")
    print(f"  Kalt  (-> Anruf/Vor-Ort-Liste):       {kalt_count}")
    print(f"  Mischtyp:                             {mischtyp_count}")

    print("\nSuchbegriff-Optimierung (vollautonom) ...")
    try:
        # Pro Suchbegriff festhalten, wie viele Treffer (auch Duplikate) und
        # wie viele davon NEU waren - Basis fuer die automatische Stilllegung
        # leergesuchter Kombinationen in retire_exhausted_queries().
        raw_by_query = Counter(lead["source_query"] for lead in raw_leads)
        new_by_query = Counter(lead["source_query"] for lead in new_leads)
        run_stats_by_query = {s["query"]: s for s in get_run_query_stats(run_id)}

        performance_entries = [
            {
                "query": q,
                "found": raw_by_query.get(q, 0),
                "new": new_by_query.get(q, 0),
                "warm": run_stats_by_query.get(q, {}).get("warm", 0),
                "mischtyp": run_stats_by_query.get(q, {}).get("mischtyp", 0),
                "kalt": run_stats_by_query.get(q, {}).get("kalt", 0),
            }
            for q in search_queries
        ]
        update_query_performance(performance_entries)

        generate_and_store_suggestions(run_id)
    except Exception as exc:
        # Optimierung ist "nice to have" - ein Fehler hier darf einen sonst
        # erfolgreichen Lauf nicht als fehlgeschlagen markieren.
        print(f"[Vorschlaege] Unerwarteter Fehler, uebersprungen: {exc}")


if __name__ == "__main__":
    run()
