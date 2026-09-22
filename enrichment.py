"""
Baustein 3: KI-Scoring.

Schickt Website-Text + Places-Daten + (falls verfuegbar) automatisch
ausgelesene Instagram-Kennzahlen an Claude und bekommt ein striktes
JSON zurueck: Score, Segment, Kanal-Empfehlung, Begruendung und einen
personalisierten Opener-Text.
"""

from __future__ import annotations

import json
import re

from anthropic import Anthropic, APIError

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from utils import retry

client = Anthropic(api_key=ANTHROPIC_API_KEY)

SCORING_SYSTEM_PROMPT = """\
Du bewertest Leads fuer eine Video-Produktions- und Social-Media-Agentur \
(HEROIN Agency), die Kosmetik- und Aesthetikstudios beim Aufbau ihrer \
Personal Brand durch professionellen Video-Content unterstuetzt.

Methodik: Du suchst nach konkreten "Intent-Signalen" - beobachtbaren \
Anzeichen, dass ein Studio bereits aktiv an seiner Sichtbarkeit arbeitet \
(und damit den Wert von Content bereits versteht), aber an Qualitaet, \
Konsistenz oder Kapazitaet scheitert. Ein Intent-Signal ist z.B. ein \
aktiver, aber unprofessionell wirkender Account, eine persoenliche \
Storytelling-Stimme auf der Website, oder erkennbare Selbstinszenierung \
der Inhaberin. Je konkreter das Signal, desto verwertbarer der Lead - \
"koennte Interesse haben" ist kein Intent-Signal, ein tatsaechlich \
beobachtetes Verhalten schon.

Bewerte NICHT, ob das Studio Kunden gut bedient (das ist bereits gut, \
siehe Google-Bewertungen) - bewerte ausschliesslich die Content- und \
Sichtbarkeitsluecke, die HEROIN schliessen kann.

Ordne jeden Lead einem von drei Segmenten zu:
- "warm": zeigt bereits Content-Ambition (aktiver Instagram-Account, \
  persoenliche Website-Stimme, Storytelling der Inhaberin) - Bedarf ist \
  da, nur die Qualitaet/Konsistenz fehlt.
- "kalt": keinerlei erkennbare Content- oder Personal-Brand-Ambition.
- "mischtyp": irgendwo dazwischen, z.B. Text-Storytelling vorhanden, \
  aber kein erkennbarer Video-/Social-Kanal, oder sehr kleiner/inaktiver \
  Instagram-Account.

Falls Instagram-Daten vorliegen: viele Follower UND viele Posts UND ein \
gepflegter Bio-Text deuten auf eine bereits etablierte Marke hin - das \
ist tendenziell ein schwaecherer Fit (weniger Luecke zu schliessen), \
nicht automatisch ein besserer Lead. Wenige Follower bei aktivem Posting \
ist dagegen oft der ideale Fall: Ambition UND Luecke gleichzeitig vorhanden.

Leite daraus eine Kanal-Empfehlung ab:
- "automatisiert": fuer "warm" - ein personalisierter E-Mail/WhatsApp-Opener \
  reicht, weil Kaufbereitschaft schon da ist.
- "persoenlich": fuer "kalt" - Anruf und Vor-Ort-Besuch, weil der Bedarf \
  erst geweckt werden muss und das kein Text leisten kann.
- "automatisiert": fuer "mischtyp" ebenfalls vertretbar, mit Fokus auf \
  das vorhandene Storytelling als Aufhaenger.

Antworte AUSSCHLIESSLICH mit validem JSON in genau dieser Struktur, ohne \
Markdown-Codeblock, ohne zusaetzlichen Text:
{
  "score": <ganzzahl 1-10>,
  "segment": "warm" | "kalt" | "mischtyp",
  "channel": "automatisiert" | "persoenlich",
  "intent_signal": "<das konkrete beobachtete Signal, ein paar Worte, oder 'keins gefunden'>",
  "reasoning": "<ein Satz, was konkret fehlt>",
  "opener": "<ein personalisierter Erstkontakt-Satz auf Deutsch, Du-Form>"
}
"""

FALLBACK_RESULT = {
    "score": 0,
    "segment": "unklar",
    "channel": "manuell pruefen",
    "intent_signal": "",
    "reasoning": "Scoring konnte nicht durchgefuehrt werden",
    "opener": "",
}


def _build_user_message(lead: dict, website_text: str, instagram_data: dict | None) -> str:
    if instagram_data:
        instagram_block = (
            f"@{instagram_data['handle']} - "
            f"{instagram_data.get('followers', '?')} Follower, "
            f"{instagram_data.get('posts', '?')} Posts, "
            f"Bio: {instagram_data.get('bio') or '(leer)'}"
        )
    else:
        instagram_block = "(kein Instagram-Handle gefunden oder Abfrage fehlgeschlagen)"

    return f"""\
Studio: {lead['name']}
Adresse: {lead['address']}
Google-Bewertung: {lead.get('rating')} ({lead.get('rating_count')} Bewertungen)
Oeffnungstage: {len(lead.get('opening_hours') or [])} von 7 Tagen hinterlegt
Website-Text (Auszug): {website_text or '(keine Website gefunden)'}
Instagram: {instagram_block}
"""


@retry(times=2, delay_seconds=3, exceptions=(APIError,))
def _call_claude(user_content: str) -> str:
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=500,
        system=SCORING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    # Alle Text-Bloecke zusammenfuegen statt blind content[0] anzunehmen -
    # robuster, falls die Antwort einmal aus mehreren Bloecken besteht.
    return "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_scoring_response(raw_text: str) -> dict:
    """Claude haelt sich trotz Anweisung nicht immer strikt an 'kein
    Markdown' und verpackt die Antwort gelegentlich in einen
    ```json ... ```-Codeblock. Macht das Parsing robust dagegen, statt
    bei jedem Codeblock in den Fallback (Score 0) zu fallen."""

    text = _CODE_FENCE_RE.sub("", raw_text.strip()).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Letzter Versuch: das erste { ... } Objekt im Text isolieren, falls
    # Claude zusaetzlichen Text vor/nach dem JSON erzeugt hat.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise json.JSONDecodeError("Kein JSON-Objekt gefunden", text, 0)


def score_lead(lead: dict, website_text: str, instagram_data: dict | None = None) -> dict:
    """Ruft Claude mit den gesammelten Signalen auf und gibt das
    geparste Scoring-Ergebnis zurueck. Bricht die Pipeline bei einem
    Fehler NICHT ab, sondern liefert ein neutrales Fallback-Ergebnis,
    das im Sheet als 'manuell pruefen' erkennbar ist."""

    user_content = _build_user_message(lead, website_text, instagram_data)

    try:
        raw_text = _call_claude(user_content)
    except APIError as exc:
        print(f"[Scoring] API-Fehler bei '{lead['name']}': {exc}")
        return dict(FALLBACK_RESULT)

    try:
        result = _parse_scoring_response(raw_text)
    except json.JSONDecodeError:
        print(
            f"[Scoring] Konnte Antwort fuer '{lead['name']}' nicht parsen: "
            f"{raw_text[:200]}"
        )
        return dict(FALLBACK_RESULT)

    # Minimal-Validierung, damit main.py sich auf die Struktur verlassen kann
    for key in ("score", "segment", "channel", "intent_signal", "reasoning", "opener"):
        result.setdefault(key, FALLBACK_RESULT[key])

    return result
