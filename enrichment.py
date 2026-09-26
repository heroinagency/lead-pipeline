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
(HEROIN Agency), die lokale Unternehmen und Dienstleister JEDER Branche \
(Handwerk, Gastronomie, Gesundheit/Praxen, Beauty, Fitness, Kanzleien, \
Handel, Bildung, u.v.m.) beim Aufbau ihrer Personal Brand durch \
professionellen Video-Content unterstuetzt. Der Lead kann aus einer \
beliebigen Branche stammen - bewerte jeden Lead nach denselben Kriterien, \
unabhaengig davon, was fuer ein Geschaeft es ist.

Methodik: Du suchst nach konkreten "Intent-Signalen" - beobachtbaren \
Anzeichen, dass ein Unternehmen bereits aktiv an seiner Sichtbarkeit \
arbeitet (und damit den Wert von Content bereits versteht), aber an \
Qualitaet, Konsistenz oder Kapazitaet scheitert. Ein Intent-Signal ist \
z.B. ein aktiver, aber unprofessionell wirkender Account, eine \
persoenliche Storytelling-Stimme auf der Website, oder erkennbare \
Selbstinszenierung der Inhaberin/des Inhabers. Je konkreter das Signal, \
desto verwertbarer der Lead - "koennte Interesse haben" ist kein \
Intent-Signal, ein tatsaechlich beobachtetes Verhalten schon.

Bewerte NICHT, ob das Unternehmen seine Kunden gut bedient (das ist \
bereits gut, siehe Google-Bewertungen) - bewerte ausschliesslich die \
Content- und Sichtbarkeitsluecke, die HEROIN schliessen kann.

Ordne jeden Lead einem von drei Segmenten zu. WICHTIG: "warm" verlangt \
NICHT zwingend einen vorhandenen Instagram-Kanal - eine erkennbare \
persoenliche Ich-Erzaehlstimme auf der Website reicht dafuer bereits FUER \
SICH ALLEIN aus. Das ist kein schwaecheres Signal als ein Instagram-Account, \
sondern oft das staerkere: die Person denkt bereits in "meine Marke", ihr \
fehlt nur die professionelle Video-Umsetzung - genau das ist der Idealkunde, \
keine Vorstufe zu einem Idealkunden.
- "warm": mindestens eines der folgenden Signale liegt vor - (a) eine klar \
  persoenliche Ich-Erzaehlstimme auf der Website (Inhaberin/Inhaber erzaehlt \
  ihre/seine Geschichte, Werte oder Beweggruende - unabhaengig davon, ob es \
  einen Instagram-Kanal gibt), (b) ein vorhandener, aber unprofessionell \
  wirkender oder kleiner/inaktiver Instagram-Account, (c) die Website IST \
  eine Instagram-Seite (kein eigener Webauftritt) - das Unternehmen hat sich \
  bereits fuer Social statt klassische Website entschieden und zeigt damit \
  aktives Content-Bewusstsein.
- "kalt": keinerlei persoenliche Ich-Stimme, keinerlei Instagram-Hinweis, \
  nur eine rein sachliche/unpersoenliche Website oder gar keine Website.
- "mischtyp": schwaechere/uneindeutige Signale - z.B. eine professionell \
  wirkende, aber komplett unpersoenliche Unternehmens-Website (keine \
  Ich-Stimme, aber erkennbarer Wert auf Aussendarstellung, etwa \
  professionelle Fotos oder eine Team-Seite), oder ein bereits etablierter, \
  grosser und professionell gepflegter Instagram-Account (weniger Luecke zu \
  schliessen, aber nicht komplett kalt).

Falls Instagram-Daten vorliegen: viele Follower UND viele Posts UND ein \
gepflegter Bio-Text deuten auf eine bereits etablierte Marke hin - das \
ist tendenziell ein schwaecherer Fit (weniger Luecke zu schliessen), \
tendenziell "mischtyp" statt automatisch ein besserer Lead. Wenige Follower \
bei aktivem Posting ist dagegen oft der ideale Fall: Ambition UND Luecke \
gleichzeitig vorhanden -> "warm". Ein gefundener Instagram-HANDLE OHNE \
weitere Kennzahlen (Follower/Posts/Bio leer) zaehlt selbst schon als \
Intent-Signal fuer "warm" - das Unternehmen hat sich zumindest einen Kanal \
angelegt oder nutzt Instagram sogar als einzige Praesenz.

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
Unternehmen: {lead['name']}
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
        # 500 war zu knapp: reasoning + opener sind freier deutscher Text,
        # und bei laengeren Antworten wurde das JSON von Claude mitten im
        # letzten Feld abgeschnitten (unvollstaendiges JSON -> Parsing
        # scheiterte reihenweise, Leads fielen faelschlich auf Score 0
        # zurueck). 1024 gibt genug Puffer fuer alle sechs Felder.
        max_tokens=1024,
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
        # Volle Antwort loggen (nicht nur die ersten 200 Zeichen) - so ist
        # im Actions-Log sofort erkennbar, ob die Antwort tatsaechlich
        # abgeschnitten wurde (z.B. durch max_tokens) oder aus einem
        # anderen Grund kein valides JSON war.
        print(
            f"[Scoring] Konnte Antwort fuer '{lead['name']}' nicht parsen "
            f"({len(raw_text)} Zeichen): {raw_text}"
        )
        return dict(FALLBACK_RESULT)

    # Minimal-Validierung, damit main.py sich auf die Struktur verlassen kann
    for key in ("score", "segment", "channel", "intent_signal", "reasoning", "opener"):
        result.setdefault(key, FALLBACK_RESULT[key])

    return result
