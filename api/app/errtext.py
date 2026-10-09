"""Errori leggibili per gli aggiornamenti falliti.

I messaggi di WordPress arrivano come li produce l'installatore: tutte le righe di
avanzamento in fila ("Download dell'aggiornamento da <link lunghissimo col token>… |
Estrazione… | Il pacchetto non può essere installato…"), con le entita' HTML gia'
codificate (&#8217; &#8230;) che poi venivano codificate una seconda volta e comparivano
cosi' nei messaggi. Qui resta solo la frase che conta, con i caratteri giusti.
"""
import html
import re
from urllib.parse import urlparse

# righe di avanzamento dell'installatore: non spiegano niente, si tolgono
_PROGRESS = re.compile(
    r"^(Download dell.aggiornamento|Downloading update|Downloading installation package|Download del pacchetto"
    r"|Estrazione dell.aggiornamento|Estrazione del pacchetto|Unpacking the update|Unpacking the package"
    r"|Installazione dell.ultima versione|Installazione del pacchetto|Installing the latest version|Installing the package"
    r"|Rimozione della vecchia versione|Removing the old version"
    r"|Disattivazione del plugin|Deactivating the plugin|Riattivazione del plugin|Reactivating the plugin"
    r"|Attivazione della modalit. manutenzione|Enabling Maintenance mode"
    r"|Disattivazione della modalit. manutenzione|Disabling Maintenance mode"
    r"|Verifica del|Verifying the|Creazione di un backup|Backing up|Moving the old version"
    r"|Aggiornamento di WordPress in corso|Updating to WordPress)",
    re.I,
)
_URL = re.compile(r"https?://[^\s'\"<>|]+")


def _host(m: re.Match) -> str:
    try:
        return urlparse(m.group(0)).hostname or "link"
    except Exception:  # noqa: BLE001
        return "link"


def clean_error(msg) -> str:
    """Testo d'errore pulito: entita' decodificate, niente link interi, niente righe di avanzamento."""
    if not msg:
        return ""
    text = str(msg)
    for _ in range(2):                      # anche le entita' codificate due volte
        text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _URL.sub(_host, text)            # i link (con i token dei produttori) diventano il solo dominio
    parts = [p.strip(" .…") for p in re.split(r"\s*\|\s*", text)]
    keep = [p for p in parts if p and not _PROGRESS.match(p)] or [p for p in parts if p]
    out: list[str] = []
    for p in keep:
        if p not in out:
            out.append(p)
    text = re.sub(r"\s+", " ", " — ".join(out)).strip()
    if re.search(r"PCLZIP_ERR_BAD_FORMAT|incompatible_archive", text) and "spazio" not in text.lower():
        text += " (il file scaricato è incompleto: spesso è lo spazio del sito esaurito)"
    if len(text) > 400:
        text = text[:397].rstrip() + "…"
    return text
