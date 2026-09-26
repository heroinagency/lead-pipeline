"""
Website-Enrichment.

Drei getrennte Funktionen mit unterschiedlichem Zweck:
- fetch_website_text: sichtbarer Text fuer die KI-Bewertung
- extract_instagram_handle: durchsucht das rohe HTML nach einem Link auf
  instagram.com - das Studio verlinkt sein eigenes Profil ja meistens
  selbst im Footer/Header. Das ist der sauberste, ToS-unbedenkliche Weg,
  an den Handle zu kommen (wir lesen nur die oeffentliche Website des
  Studios, nicht Instagram selbst).
- extract_email: durchsucht das rohe HTML nach einer Kontakt-E-Mail
  (mailto:-Link bevorzugt, sonst Freitext-Adresse z.B. auf Impressum-/
  Kontaktseiten). Google Places liefert keine E-Mail - das ist bislang
  der einzige Weg, ueberhaupt an eine E-Mail-Adresse zu kommen.
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from utils import retry

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HEROIN-LeadBot/1.0)"}

INSTAGRAM_LINK_PATTERN = re.compile(
    r"instagram\.com/([A-Za-z0-9._]+)", re.IGNORECASE
)

# Diese Pfade sind keine echten Profile, sondern generische Instagram-Seiten -
# tauchen aber häufig versehentlich in Footer-Links auf und würden sonst
# fälschlich als "Handle" erkannt.
IGNORED_HANDLES = {"explore", "accounts", "reel", "p", "direct", "about", "legal"}

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Sehen aus wie E-Mails, sind aber Tracking-/Platzhalter-/Baukasten-Adressen
# (Sentry-Fehlertracking, CMS-Platzhalter, Bildoptimierer etc.) - wuerden
# sonst faelschlich als Kontakt-Adresse durchgereicht.
IGNORED_EMAIL_DOMAINS = {
    "sentry.io", "sentry-next.io", "example.com", "example.org", "domain.com",
    "wixpress.com", "godaddy.com", "cloudflare.com", "schema.org",
}


@retry(times=2, delay_seconds=2, exceptions=(requests.RequestException,))
def _get(url: str) -> requests.Response:
    response = requests.get(url, timeout=10, headers=HEADERS)
    response.raise_for_status()
    return response


def fetch_website_html(url: str) -> str:
    """Holt das rohe HTML einer Website. Leerer String bei fehlender
    URL oder endgueltigem Fehlschlag - wird von den Aufrufern als
    'keine Daten verfuegbar' interpretiert, nicht als Programmabbruch."""

    if not url:
        return ""
    try:
        return _get(url).text
    except requests.RequestException as exc:
        print(f"[Website] Abruf von {url} fehlgeschlagen: {exc}")
        return ""


def fetch_website_text(html: str, max_chars: int = 3000) -> str:
    """Extrahiert sichtbaren Fliesstext aus HTML fuer die KI-Bewertung."""

    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()

    text = re.sub(r"\s+", " ", soup.get_text()).strip()
    return text[:max_chars]


def extract_instagram_handle(html: str) -> str | None:
    """Sucht im HTML nach einem Link auf ein Instagram-Profil und gibt
    den ersten plausiblen Handle zurueck, oder None, wenn nichts gefunden
    wurde."""

    if not html:
        return None

    for match in INSTAGRAM_LINK_PATTERN.finditer(html):
        handle = match.group(1).strip("/").lower()
        if handle and handle not in IGNORED_HANDLES:
            return handle

    return None


def extract_email(html: str) -> str | None:
    """Sucht zuerst nach einem mailto:-Link (eindeutigste Quelle - bewusst
    als Kontakt-Adresse hinterlegt), dann nach einer E-Mail-Adresse im
    rohen HTML (z.B. auf Kontakt-/Impressum-Seiten als Freitext). Filtert
    offensichtliche Tracking-/Platzhalter-Domains raus. None, wenn nichts
    Plausibles gefunden wurde."""

    if not html:
        return None

    for raw in re.findall(r'mailto:([^"\'\s?<>]+)', html, re.IGNORECASE):
        candidate = raw.strip()
        domain = candidate.rsplit("@", 1)[-1].lower() if "@" in candidate else ""
        if candidate and domain and domain not in IGNORED_EMAIL_DOMAINS:
            return candidate

    for candidate in EMAIL_PATTERN.findall(html):
        domain = candidate.rsplit("@", 1)[-1].lower()
        if domain not in IGNORED_EMAIL_DOMAINS:
            return candidate

    return None
