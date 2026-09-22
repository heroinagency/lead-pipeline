"""
RISIKO-MODUL - Instagram-Profildaten auslesen.

WICHTIG, bitte wirklich lesen:
Instagram (Meta) verbietet automatisiertes Auslesen seiner Plattform in
seinen Nutzungsbedingungen. Das hier verwendete Verfahren nutzt einen
inoffiziellen, öffentlichen Endpunkt (keine Anmeldedaten, kein Login,
keine Umgehung von Zugriffsschutz) - dieselbe Technik, die z.B. das
verbreitete Open-Source-Tool "instaloader" für öffentliche Profile nutzt.

Was das bedeutet:
- Rechtlich: kein Zugriff auf fremde Accounts, keine Umgehung von
  Passwortschutz - es werden nur ohnehin öffentlich sichtbare Zahlen
  gelesen (Follower, Postanzahl, Bio-Text).
- Operativ: Meta kann die anfragende IP jederzeit temporär blockieren
  oder den Endpunkt ohne Ankündigung ändern. Deshalb: eingebaute Pause
  zwischen Anfragen (siehe config.INSTAGRAM_REQUEST_DELAY_SECONDS),
  niedriges Volumen, und robuste Fehlerbehandlung, die das Sheet nie
  blockiert - im Zweifel bleibt die Zeile einfach leer und ihr prüft
  manuell nach.
- Standardmäßig ist dieses Modul AUS (config.ENABLE_INSTAGRAM_CHECK).
  Ihr aktiviert es bewusst und tragt das operative Risiko selbst.
"""

from __future__ import annotations

import time

import requests

from config import INSTAGRAM_REQUEST_DELAY_SECONDS

# Öffentliche App-ID, die Instagrams eigene Web-Oberfläche für
# nicht eingeloggte Anfragen an diesen Endpunkt mitschickt.
PROFILE_ENDPOINT = "https://i.instagram.com/api/v1/users/web_profile_info/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "X-IG-App-ID": "936619743392459",
    "Accept": "*/*",
}

_last_request_time: float = 0.0


def _respect_rate_limit() -> None:
    """Erzwingt eine Mindestpause zwischen zwei Anfragen, auch wenn der
    Aufrufer die Funktion schneller hintereinander ruft."""

    global _last_request_time
    elapsed = time.time() - _last_request_time
    remaining = INSTAGRAM_REQUEST_DELAY_SECONDS - elapsed
    if remaining > 0:
        time.sleep(remaining)
    _last_request_time = time.time()


def get_instagram_profile(handle: str) -> dict | None:
    """Versucht, öffentliche Profildaten zu einem Instagram-Handle zu
    holen. Gibt None zurück, wenn irgendetwas schiefgeht - der Aufrufer
    behandelt das als 'kein Instagram-Signal verfügbar', niemals als
    Programmfehler."""

    if not handle:
        return None

    _respect_rate_limit()

    try:
        response = requests.get(
            PROFILE_ENDPOINT,
            params={"username": handle},
            headers=HEADERS,
            timeout=10,
        )
    except requests.RequestException as exc:
        print(f"[Instagram] Anfrage für @{handle} fehlgeschlagen: {exc}")
        return None

    if response.status_code == 429:
        print(f"[Instagram] Rate-Limit erreicht bei @{handle} - überspringe.")
        return None

    if response.status_code != 200:
        print(f"[Instagram] @{handle}: HTTP {response.status_code} - überspringe.")
        return None

    try:
        data = response.json()["data"]["user"]
    except (ValueError, KeyError, TypeError):
        print(f"[Instagram] @{handle}: Antwortformat unerwartet - überspringe.")
        return None

    return {
        "handle": handle,
        "followers": data.get("edge_followed_by", {}).get("count"),
        "posts": data.get("edge_owner_to_timeline_media", {}).get("count"),
        "bio": (data.get("biography") or "")[:200],
        "is_private": data.get("is_private", False),
    }
