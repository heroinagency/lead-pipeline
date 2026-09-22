"""
Gemeinsame Hilfsfunktionen für die ganze Pipeline.
"""

import time
from functools import wraps


def retry(times: int = 3, delay_seconds: float = 2.0, exceptions=(Exception,)):
    """Einfacher Retry-Decorator ohne externe Abhängigkeit.

    Wiederholt eine fehlschlagende Funktion bis zu `times`-mal mit
    steigender Pause zwischen den Versuchen (exponentielles Backoff).
    Nach dem letzten Fehlschlag wird die Exception weitergereicht, damit
    der Aufrufer bewusst entscheiden kann, wie er damit umgeht.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:  # noqa: BLE001 - bewusst breit gefasst
                    last_exc = exc
                    if attempt < times:
                        wait = delay_seconds * attempt
                        print(
                            f"[Retry] {func.__name__} fehlgeschlagen "
                            f"(Versuch {attempt}/{times}): {exc}. "
                            f"Neuer Versuch in {wait:.0f}s ..."
                        )
                        time.sleep(wait)
            raise last_exc

        return wrapper

    return decorator
