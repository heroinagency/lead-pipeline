"""
Baustein 6: Selbst-optimierende Suchbegriffe (Vorschlag statt Autopilot).

Laeuft am Ende jedes Pipeline-Laufs. Schaut sich an, welche Suchbegriffe
(Branche + Stadt) in DIESEM Lauf ueberdurchschnittlich viele warme/mischtyp
Leads produziert haben, und laesst Claude daraus 1-3 neue, verwandte
Suchbegriffe ableiten (z.B. eine Nachbarstadt oder eine angrenzende
Branche). Diese Vorschlaege werden NICHT automatisch aktiv - sie landen mit
status='suggested' in Supabase und muessen im Dashboard (Einstellungen)
manuell genehmigt werden, bevor die Pipeline sie beim naechsten Lauf
mitsucht. Das ist bewusst so (User-Entscheidung: "Vorschlaege machen, aber
ich muss zustimmen").

Schlaegt dieser Schritt fehl (z.B. Claude-API-Fehler), bricht die Pipeline
NICHT ab - Sourcing/Scoring/Schreiben sind bereits durch, das hier ist nur
Optimierung fuer den naechsten Lauf.
"""

from __future__ import annotations

import json
import re

from anthropic import Anthropic, APIError

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, MAX_QUERY_SUGGESTIONS_PER_RUN
from supabase_client import (
    get_run_query_stats,
    get_existing_query_texts,
    insert_query_suggestions,
)

client = Anthropic(api_key=ANTHROPIC_API_KEY)

SUGGESTION_SYSTEM_PROMPT = """\
Du optimierst die Such-Strategie einer Lead-Pipeline fuer eine \
Video-Produktions-/Social-Media-Agentur (HEROIN Agency), die Kosmetik- und \
Aesthetikstudios (Branchen wie "Kosmetikstudio", "Beauty Studio", \
"Laserklinik", "Nagelstudio", "Permanent Make-up Studio" etc.) in und um \
Nordrhein-Westfalen sucht.

Du bekommst eine Liste von Google-Places-Suchbegriffen (Format: "<Branche> \
<Stadt>") mit ihrer Trefferquote aus dem letzten Lauf: wie viele der \
gefundenen Leads als "warm" oder "mischtyp" eingestuft wurden (gut - zeigt \
Content-Ambition) vs. "kalt" (schlecht) oder "unklar" (Scoring fehlgeschlagen, \
ignorieren). Ausserdem eine Liste bereits bekannter Suchbegriffe (aktiv, \
vorgeschlagen oder abgelehnt), die du NICHT erneut vorschlagen darfst.

Leite aus den Suchbegriffen mit der besten warm/mischtyp-Quote 1-3 neue, \
verwandte Suchbegriffe ab - z.B. dieselbe Branche in einer benachbarten \
Stadt/Region, oder eine verwandte Branche in derselben Stadt. Schlage NUR \
Staedte/Regionen in oder nahe Nordrhein-Westfalen vor. Wenn keine Query \
genug Daten hatte (z.B. 0 Treffer insgesamt), ignoriere sie.

Antworte AUSSCHLIESSLICH mit validem JSON (Liste, kann leer sein), ohne \
Markdown-Codeblock, ohne zusaetzlichen Text:
[
  {"query": "<Branche> <Stadt>", "reasoning": "<ein Satz auf Deutsch, warum>"}
]
"""

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_suggestions(raw_text: str) -> list[dict]:
    text = _CODE_FENCE_RE.sub("", raw_text.strip()).strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Erwartete JSON-Liste")
    return data


def generate_and_store_suggestions(run_id: str) -> None:
    stats = get_run_query_stats(run_id)
    # Nur Queries mit nennenswerter Datenbasis beruecksichtigen.
    stats = [s for s in stats if s["total"] >= 3]

    if not stats:
        print("[Vorschlaege] Zu wenig Daten in diesem Lauf fuer Vorschlaege - ueberspringe.")
        return

    known_queries = get_existing_query_texts()

    stats_lines = "\n".join(
        f"- \"{s['query']}\": {s['total']} Leads, "
        f"{s['warm']} warm, {s['mischtyp']} mischtyp, {s['kalt']} kalt"
        for s in stats
    )
    known_lines = "\n".join(f"- {q}" for q in sorted(known_queries)) or "(keine)"

    user_content = f"""\
Trefferquote aus dem letzten Lauf:
{stats_lines}

Bereits bekannte Suchbegriffe (nicht wiederholen):
{known_lines}

Schlage maximal {MAX_QUERY_SUGGESTIONS_PER_RUN} neue Suchbegriffe vor."""

    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            system=SUGGESTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        raw_text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        suggestions = _parse_suggestions(raw_text)
    except (APIError, json.JSONDecodeError, ValueError) as exc:
        print(f"[Vorschlaege] Konnte keine Vorschlaege erzeugen: {exc}")
        return

    suggestions = suggestions[:MAX_QUERY_SUGGESTIONS_PER_RUN]
    # Bekannte Queries defensiv nochmal rausfiltern, falls Claude sich nicht
    # an die Anweisung haelt.
    suggestions = [s for s in suggestions if s.get("query") not in known_queries]

    if not suggestions:
        print("[Vorschlaege] Keine neuen Vorschlaege (alles schon bekannt oder leer).")
        return

    written = insert_query_suggestions(suggestions)
    print(f"[Vorschlaege] {written} neue Suchbegriff-Vorschlaege in Supabase gespeichert:")
    for s in suggestions:
        print(f"  - {s['query']}  ({s.get('reasoning', '')})")
