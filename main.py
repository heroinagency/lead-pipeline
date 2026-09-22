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

from config import SCORE_THRESHOLD, ENABLE_INSTAGRAM_CHECK
from sourcing import source_all_leads
from website import fetch_website_html, fetch_website_text, extract_instagram_handle
from enrichment import score_lead
from query_suggestions import generate_and_store_suggestions

# Supabase ist jetzt die Quelle fuers Dashboard. sheets_client.py bleibt im
# Projekt erhalten (Backup/Uebergang) - um wieder zusaetzlich ins Sheet zu
# schreiben, einfach den Import + den Aufruf in run() wieder einkommentieren.
from supabase_client import (
    get_active_search_queries,
    get_existing_place_ids,
    start_run,
    finish_run,
    append_leads_batch,
)
# from sheets_client import append_leads_batch as append_leads_batch_sheet

if ENABLE_INSTAGRAM_CHECK:
    from instagram_check import get_instagram_profile


def process_lead(lead: dict) -> tuple[dict, dict, dict | None]:
    """Reichert einen einzelnen Lead an und bewertet ihn. Gibt
    (lead, scoring, instagram_data) zurueck - instagram_data ist None,
    wenn das Modul aus ist oder nichts gefunden wurde."""

    html = fetch_website_html(lead["website"])
    website_text = fetch_website_text(html)

    instagram_data = None
    if ENABLE_INSTAGRAM_CHECK:
        handle = extract_instagram_handle(html)
        if handle:
            instagram_data = get_instagram_profile(handle)

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

    print("\nSuchbegriff-Optimierung ...")
    try:
        generate_and_store_suggestions(run_id)
    except Exception as exc:
        # Optimierung ist "nice to have" - ein Fehler hier darf einen sonst
        # erfolgreichen Lauf nicht als fehlgeschlagen markieren.
        print(f"[Vorschlaege] Unerwarteter Fehler, uebersprungen: {exc}")


if __name__ == "__main__":
    run()
