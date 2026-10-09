"""
Rate limiter condiviso (slowapi).

Sta in un modulo dedicato cosi' i router lo importano senza creare cicli con main.py
(main.py importa i router; se i router importassero main.py si rischia un import circolare).

Identificazione del client:
- se c'e' X-Forwarded-For (NPMplus davanti), si usa l'ULTIMO IP della catena: e' quello che
  il proprio proxy ha appena aggiunto e che il client non puo' scegliere. Il primo IP lo
  scrive chi fa la richiesta (NPMplus accoda, non sovrascrive): usandolo, bastava cambiare
  l'header a ogni tentativo per aggirare il limite del login
- altrimenti l'IP del peer

Storage condiviso su Redis: con piu' worker gunicorn il limite e' GLOBALE (sommato
tra i worker), non piu' per-worker. Cosi' "10/minute" sul login resta 10/minute
davvero, non 10*N. Se Redis e' irraggiungibile, slowapi degrada in modo sicuro.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import settings


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return get_remote_address(request)


limiter = Limiter(
    key_func=_client_ip,
    default_limits=[],
    storage_uri=settings.REDIS_URL,
)
