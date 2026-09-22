"""
Baustein 7: Vollautonome Suchbegriff-Optimierung.

Laeuft am Ende jedes Pipeline-Laufs. Schaut sich die KUMULIERTE Performance
aller je verwendeten Suchbegriffe an (nicht nur den letzten Lauf - eine
einzelne Stichprobe ist zu verrauscht fuer eine gute Entscheidung), und
laesst Claude daraus 1-3 neue, verwandte Suchbegriffe ableiten (z.B. eine
Nachbarstadt oder eine angrenzende Branche zu den bisher staerksten
Performern). Diese Vorschlaege werden SOFORT aktiv geschaltet (status=
'active') - keine manuelle Freigabe mehr noetig (User-Entscheidung vom
22.09.: "er soll die suchbegriffe anhand von hochprofessionellen parametern
immer automatisch wechseln ... testen und alles immer so optimieren, dass
die warme leads outcome immer erhoeht wird").

Als Gegenstueck dazu legt supabase_client.retire_exhausted_queries() vor
diesem Schritt automatisch Suchbegriffe still, die mehrere Laeufe in Folge
keine neuen Leads mehr gebracht haben (z.B. eine Stadt, in der inzwischen
alle Studios bereits bekannt sind) - sonst wuerde die Pipeline taeglich
dieselbe leergesuchte Kombination wiederholen und dafuer nur API-/Scoring-
Budget verschwenden, das an anderer Stelle mehr warme Leads bringen koennte.

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
    get_query_performance_summary,
    get_existing_query_texts,
    insert_query_suggestions,
    retire_exhausted_queries,
)

client = Anthropic(api_key=ANTHROPIC_API_KEY)

SUGGESTION_SYSTEM_PROMPT = """\
Du optimierst vollautonom die Such-Strategie einer Lead-Pipeline fuer eine \
Video-Produktions-/Social-Media-Agentur (HEROIN Agency), die Kosmetik- und \
Aesthetikstudios (Branchen wie "Kosmetikstudio", "Beauty Studio", \
"Laserklinik", "Nagelstudio", "Permanent Make-up Studio" etc.) in und um \
Nordrhein-Westfalen sucht. Deine Vorschlaege werden SOFORT aktiv geschaltet \
und beim naechsten Lauf mitgesucht - es gibt keine manuelle Pruefung mehr. \
Triff deine Wahl deshalb konservativ und datenbasiert.

Du bekommst die KUMULIERTE Performance (ueber ALLE bisherigen Laeufe, nicht \
nur den letzten) aller bisher verwendeten Suchbegriffe: wie oft ein \
Suchbegriff schon gelaufen ist, wie viele neue Leads er insgesamt gebracht \
hat, und wie viele davon "warm" oder "mischtyp" eingestuft wurden (gut - \
zeigt Content-Ambition) vs. "kalt" (schlecht). Ausserdem eine Liste bereits \
bekannter Suchbegriffe (aktiv, stillgelegt oder abgelehnt), die du NICHT \
erneut vorschlagen darfst - stillgelegte Suchbegriffe sind bereits \
"leergesucht" (keine neuen Leads mehr) und eine Wiederholung waere \
Budget-Verschwendung.

Leite aus den Suchbegriffen mit der besten warm/mischtyp-Quote pro neuem \
Lead 1-3 neue, verwandte Suchbegriffe ab - z.B. dieselbe Branche in einer \
benachbarten Stadt/Region, oder eine verwandte Branche in derselben Stadt. \
Bevorzuge Kombinationen, die den bisher staerksten Performern strukturell \
aehneln. Schlage NUR Staedte/Regionen in oder nahe Nordrhein-Westfalen vor. \
Ignoriere Suchbegriffe mit zu wenig Datenbasis (weniger als 3 neue Leads \
insgesamt) fuer die Ableitung - sie sagen noch nichts Verlaessliches aus.

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
    # Erst erschoepfte Suchbegriffe stilllegen, DANN Vorschlaege generieren -
    # so sieht die KI im selben Lauf noch den korrekten aktiven Suchraum und
    # ersetzt leergesuchte Kombinationen direkt statt sie einfach zu ergaenzen.
    retired = retire_exhausted_queries()
    if retired:
        print(f"[Suchbegriffe] {len(retired)} automatisch stillgelegt (keine neuen Leads mehr):")
        for q in retired:
            print(f"  - {q}")

    performance = get_query_performance_summary()
    performance = [p for p in performance if p.get("total_new", 0) >= 3]

    if not performance:
        print("[Vorschlaege] Noch zu wenig kumulierte Daten fuer Vorschlaege - ueberspringe.")
        return

    known_queries = get_existing_query_texts()

    perf_lines = "\n".join(
        f"- \"{p['query']}\" ({p['status']}, {p['times_used']}x gelaufen): "
        f"{p['total_new']} neue Leads gesamt, "
        f"{p['total_warm']} warm, {p['total_mischtyp']} mischtyp, {p['total_kalt']} kalt"
        for p in performance
    )
    known_lines = "\n".join(f"- {q}" for q in sorted(known_queries)) or "(keine)"

    user_content = f"""\
Kumulierte Performance aller bisher verwendeten Suchbegriffe:
{perf_lines}

Bereits bekannte Suchbegriffe (nicht wiederholen):
{known_lines}

Schlage maximal {MAX_QUERY_SUGGESTIONS_PER_RUN} neue Suchbegriffe vor, die \
sofort aktiv geschaltet werden."""

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
    print(f"[Vorschlaege] {written} neue Suchbegriffe automatisch aktiviert:")
    for s in suggestions:
        print(f"  - {s['query']}  ({s.get('reasoning', '')})")
