"""
Shooter: servizio interno che cattura l'anteprima di un sito.

Contratto (usato dal worker):
    POST /shot  {"url": "...", "site_id": 12}  ->  {"path": "site_12.jpg"}

Il file viene scritto in /data/screenshots (volume condiviso in sola lettura con l'api,
che lo serve su /api/sites/{id}/image). Nessuna autenticazione: il servizio non e'
esposto all'esterno, vive solo sulla rete interna di compose.
"""
import asyncio
import logging
import os
import time
import re

from fastapi import FastAPI, HTTPException, Body
from playwright.async_api import async_playwright

log = logging.getLogger("shooter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SHOTS_DIR = os.getenv("SHOTS_DIR", "/data/screenshots")
WIDTH = int(os.getenv("SHOT_WIDTH", "1280"))
HEIGHT = int(os.getenv("SHOT_HEIGHT", "800"))
QUALITY = int(os.getenv("SHOT_QUALITY", "72"))
THUMB_W = int(os.getenv("SHOT_THUMB_WIDTH", "360"))      # miniatura per la lista siti
NAV_TIMEOUT = int(os.getenv("SHOT_TIMEOUT_MS", "25000"))
SETTLE_MS = int(os.getenv("SHOT_SETTLE_MS", "1800"))     # respiro prima dello scatto
VIDEO_WAIT_MS = int(os.getenv("SHOT_VIDEO_WAIT_MS", "6000"))
CHALLENGE_WAIT_MS = int(os.getenv("SHOT_CHALLENGE_WAIT_MS", "15000"))   # attesa massima di una schermata antibot, per tentativo
STATE_DIR = os.path.join(os.getenv("SHOTS_DIR", "/data/screenshots"), ".state")   # "lasciapassare" degli antibot per sito (cartella nascosta)
USER_AGENT = os.getenv("SHOT_USER_AGENT", "").strip()   # se vuoto: Chrome vero, con la versione del browser in uso
VIDEO_FRAME_AT = float(os.getenv("SHOT_VIDEO_FRAME_AT", "0.5"))   # secondo del video fotografato, uguale prima e dopo

# Zone che cambiano da sole: escluse dal confronto prima/dopo, altrimenti un video o uno
# slider risultano "cambiamento" a ogni aggiornamento
DYNAMIC_SELECTORS = ", ".join([
    "video", "iframe", "embed", "object", "canvas",
    "[uk-slideshow]", "[data-uk-slideshow]", ".uk-slideshow", "[uk-slider]", "[data-uk-slider]", ".uk-slider",
    ".swiper", ".swiper-container", ".slick-slider", ".owl-carousel", ".flexslider", ".carousel",
    ".elementor-slides", ".elementor-widget-slides", ".elementor-background-video-container", ".elementor-background-slideshow",
    ".rev_slider_wrapper", "rs-module-wrap", "sr7-module", ".n2-section-smartslider", ".metaslider", ".splide",
])

app = FastAPI(title="Sentinel shooter")

_browser = None
_lock = asyncio.Lock()      # una cattura per volta: Chromium in container e' pesante


@app.on_event("startup")
async def _startup():
    global _browser
    os.makedirs(SHOTS_DIR, exist_ok=True)
    pw = await async_playwright().start()
    # --autoplay-policy: senza, i video di sfondo non partono in automatico e restano neri
    args = ["--no-sandbox", "--disable-dev-shm-usage",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-blink-features=AutomationControlled"]   # il browser non si dichiara "automatizzato"
    try:
        # Chrome ha i codec H.264/AAC: indispensabile per i video di sfondo in MP4
        _browser = await pw.chromium.launch(channel="chrome", args=args)
        engine = "Google Chrome (codec H.264 disponibili)"
    except Exception as ex:  # noqa: BLE001
        _browser = await pw.chromium.launch(args=args)
        engine = "Chromium senza codec proprietari: i video MP4 resteranno vuoti"
        log.warning("Chrome non disponibile (%s)", str(ex)[:160])
    log.info("shooter pronto (%sx%s, q=%s) - %s", WIDTH, HEIGHT, QUALITY, engine)


def _user_agent() -> str:
    """Firma del browser. Prima era "Chrome/131.0 ... SentinelTD/1.0": una versione incompleta che
    nessun Chrome vero manda, piu' un nome di robot: gli antibot (SiteGround, Cloudflare,
    Imunify, Wordfence) rispondevano con una verifica o con un 403. Ora e' quella di un Chrome vero,
    con la versione reale del browser in uso. SHOT_USER_AGENT per forzarne un'altra."""
    if USER_AGENT:
        return USER_AGENT
    ver = str(getattr(_browser, "version", "") or "")
    if not ver[:1].isdigit():
        ver = "131.0.0.0"
    return f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"


# Schermate di verifica degli antibot: la pagina vera arriva solo dopo qualche secondo, quando il
# browser ha superato il controllo. Prima si fotografava subito, cioe' la schermata di verifica.
_CHALLENGE_URLS = ("/.well-known/sgcaptcha", "/cdn-cgi/challenge-platform", "/cdn-cgi/l/chk_")
_CHALLENGE_TITLES = ("robot challenge screen", "just a moment", "checking your browser", "attention required",
                     "un momento", "verifica in corso", "ddos protection")


async def _in_challenge(page) -> bool:
    try:
        url = page.url.lower()
        if any(x in url for x in _CHALLENGE_URLS):
            return True
        title = (await page.title()).strip().lower()
        return any(x in title for x in _CHALLENGE_TITLES)
    except Exception:  # noqa: BLE001
        return True    # navigazione in corso: spesso e' proprio la verifica che rimanda alla pagina vera


async def _wait_challenge(page, wait_ms: int = 0) -> bool:
    """True se non c'era nessuna verifica o e' stata superata; False se dopo l'attesa e' ancora li'."""
    if not await _in_challenge(page):
        return True
    deadline = time.time() + (wait_ms or CHALLENGE_WAIT_MS) / 1000
    while time.time() < deadline:
        await page.wait_for_timeout(500)
        if not await _in_challenge(page):
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            return True
    return False


def _state_path(site_id: int) -> str:
    return os.path.join(STATE_DIR, f"site_{site_id}.json")


async def _open_page(url: str, state_path: str | None):
    """Apre il sito e, se c'e' una verifica antibot, aspetta che venga superata.
    Ritorna (contesto, pagina, stato http, respinto, c'era_una_verifica)."""
    kwargs = {}
    if state_path and os.path.isfile(state_path):
        kwargs["storage_state"] = state_path       # lasciapassare di una verifica gia' superata
    ctx = await _browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        ignore_https_errors=True,            # certificati interni/hairpin non devono bloccare
        user_agent=_user_agent(),
        locale="it-IT",
        timezone_id="Europe/Rome",
        **kwargs,
    )
    # un browser comandato da un programma dichiara navigator.webdriver: gli antibot lo leggono
    await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
    page = await ctx.new_page()
    last_doc = {"s": 0}   # stato dell'ultima pagina caricata: dopo una verifica non e' piu' il 202 iniziale
    page.on("response", lambda r: last_doc.update(s=r.status)
            if r.request.resource_type == "document" and r.frame == page.main_frame else None)
    http_status = 0
    try:
        resp = await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT)
        http_status = resp.status if resp else 0
    except Exception:                        # noqa: BLE001
        # se "networkidle" non arriva (chat widget, polling, pubblicita') scatto lo stesso:
        # meglio un'anteprima imperfetta che nessuna anteprima
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:  # noqa: BLE001
            pass
    had_challenge = await _in_challenge(page)
    challenge_ok = await _wait_challenge(page)
    if last_doc["s"]:
        http_status = last_doc["s"]
    blocked = (not challenge_ok) or http_status in (403, 429)
    return ctx, page, http_status, blocked, had_challenge


@app.get("/healthz")
async def healthz():
    return {"ok": _browser is not None}


@app.post("/shot")
async def shot(payload: dict = Body(...)):
    url = str(payload.get("url", "")).strip()
    site_id = int(payload.get("site_id") or 0)
    if not url.startswith(("http://", "https://")) or site_id <= 0:
        raise HTTPException(422, "url e site_id obbligatori")

    # Variante "before"/"after": istantanee per il controllo visivo degli aggiornamenti.
    # Nome sempre costruito qui (mai un path dal chiamante) e URL con parametro unico, cosi'
    # la cache del reverse proxy non restituisce la pagina di prima dell'aggiornamento.
    variant = str(payload.get("variant") or "").strip().lower()
    if variant not in ("", "before", "after"):
        raise HTTPException(422, "variant non valida")
    if variant:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sentinel_check={int(time.time())}"
    name = f"site_{site_id}_{variant}.jpg" if variant else f"site_{site_id}.jpg"
    dest = os.path.join(SHOTS_DIR, name)
    tmp = dest + ".tmp"
    thumb = os.path.join(SHOTS_DIR, f"site_{site_id}_thumb.jpg")
    http_status, error_text = 0, ""
    masks = []
    blocked = False        # il sito ha respinto l'accesso automatico (verifica non superata, 403, 429)

    state_path = _state_path(site_id)
    async with _lock:
        ctx, page, http_status, blocked, had_challenge = await _open_page(url, state_path)
        if blocked:
            # un secondo tentativo da zero: il lasciapassare salvato puo' essere scaduto e la verifica
            # degli antibot e' a volte instabile (dipende anche dalla reputazione dell'IP)
            log.warning("accesso automatico respinto da %s (http=%s): secondo tentativo", url, http_status)
            await page.close()
            await ctx.close()
            try:
                os.remove(state_path)
            except OSError:
                pass
            await asyncio.sleep(2)
            ctx, page, http_status, blocked, had_challenge = await _open_page(url, None)
            if blocked:
                log.warning("accesso automatico respinto da %s anche al secondo tentativo (http=%s)", url, http_status)
        try:
            # banner cookie piu' diffusi: tolgono meta' pagina all'anteprima
            await page.add_style_tag(content="""
                #cookie-law-info-bar, .cli-modal, #CybotCookiebotDialog, #onetrust-consent-sdk,
                .iubenda-cs-container, #iubenda-cs-banner, .cmplz-cookiebanner, #cmplz-cookiebanner-container,
                .moove_gdpr_cookie_modal, #moove_gdpr_cookie_info_bar, .cc-window, #usercentrics-root
                { display: none !important; }
                html { scroll-behavior: auto !important; }
            """)

            # 1) sveglia le immagini lazy: molti temi le caricano solo allo scroll
            await page.evaluate("""() => {
                window.scrollTo(0, window.innerHeight);
                window.scrollTo(0, 0);
                document.querySelectorAll('img[loading="lazy"]').forEach(i => { i.loading = 'eager'; });
            }""")

            # 2) VIDEO: senza questo passaggio l'area del video resta BIANCA nello
            # screenshot, perche' al momento dello scatto non e' ancora stato disegnato
            # nessun frame. Li avviamo muti, aspettiamo che abbiano dati, e forziamo un
            # frame spostando leggermente currentTime.
            await page.evaluate("""async () => {
                const vids = Array.from(document.querySelectorAll('video'));
                await Promise.all(vids.map(async v => {
                    try {
                        v.muted = true; v.defaultMuted = true; v.playsInline = true;
                        v.setAttribute('playsinline', '');
                        if (v.preload === 'none') { v.preload = 'auto'; v.load(); }
                        await v.play().catch(() => {});
                    } catch (e) {}
                }));
            }""")
            try:
                # aspetta che ogni video abbia almeno un frame disponibile (o sia in errore)
                await page.wait_for_function(
                    """() => Array.from(document.querySelectorAll('video'))
                            .every(v => v.readyState >= 2 || v.error || v.networkState === 3)""",
                    timeout=VIDEO_WAIT_MS)
            except Exception:  # noqa: BLE001
                pass
            await page.evaluate("""() => {
                document.querySelectorAll('video').forEach(v => {
                    try {
                        // un piccolo salto costringe il browser a disegnare il frame
                        if (v.readyState >= 2 && v.currentTime < 0.05) v.currentTime = 0.1;
                        // ripieghi se il video resta senza frame (codec mancante, rete, formato):
                        if (v.readyState < 2) {
                            if (v.poster) {
                                // 1) usa il poster dichiarato
                                v.style.background = 'url("' + v.poster + '") center center / cover no-repeat';
                            } else {
                                // 2) niente poster: nascondi il video e lascia emergere lo sfondo
                                //    della sezione (immagine o colore), meglio di un rettangolo vuoto
                                v.style.visibility = 'hidden';
                            }
                        }
                    } catch (e) {}
                });
            }""")

            # 3) respiro finale per font, animazioni d'ingresso e frame video
            await page.wait_for_timeout(SETTLE_MS)

            # 4) VIDEO FERMI SULLO STESSO FOTOGRAMMA: prima e dopo l'aggiornamento il video
            # veniva fotografato in un istante diverso e risultava "cambiamento". Pausa e
            # sempre lo stesso secondo (VIDEO_FRAME_AT), aspettando che il frame sia disegnato.
            try:
                await page.evaluate("""async (at) => {
                    const vids = Array.from(document.querySelectorAll('video')).filter(v => v.readyState >= 1);
                    await Promise.all(vids.map(v => new Promise(res => {
                        try {
                            v.pause();
                            const t = Math.min(at, Math.max(0, (v.duration || at) - 0.05));
                            const done = () => { v.removeEventListener('seeked', done); res(); };
                            v.addEventListener('seeked', done);
                            v.currentTime = t;
                            setTimeout(done, 2500);
                        } catch (e) { res(); }
                    })));
                }""", VIDEO_FRAME_AT)
                await page.wait_for_timeout(250)
            except Exception:  # noqa: BLE001
                pass

            # 5) zone che cambiano da sole (video, iframe, slider): annotate per il confronto
            masks = []
            if variant:
                try:
                    masks = await page.evaluate("""(sel) => {
                        const W = window.innerWidth, H = window.innerHeight, out = [];
                        document.querySelectorAll(sel).forEach(el => {
                            const r = el.getBoundingClientRect();
                            const x = Math.max(0, r.left), y = Math.max(0, r.top);
                            const w = Math.min(W, r.right) - x, h = Math.min(H, r.bottom) - y;
                            if (w >= 40 && h >= 40) out.push([Math.round(x), Math.round(y), Math.round(w), Math.round(h)]);
                        });
                        return { w: W, h: H, masks: out.slice(0, 60) };
                    }""", DYNAMIC_SELECTORS)
                except Exception:  # noqa: BLE001
                    masks = []

            # segni inequivocabili di sito rotto nel testo della pagina
            if variant:
                try:
                    body_text = await page.evaluate("document.body ? document.body.innerText.slice(0, 30000) : ''")
                    error_text = _find_error_text(body_text or "")
                except Exception:  # noqa: BLE001
                    pass
            await page.screenshot(path=tmp, type="jpeg", quality=QUALITY, full_page=False,
                                  animations="disabled")
            if had_challenge and not blocked:
                # verifica superata: si tiene il lasciapassare (cookie) cosi' i prossimi scatti non la rifanno
                try:
                    os.makedirs(STATE_DIR, exist_ok=True)
                    await ctx.storage_state(path=state_path)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            await page.close()
            await ctx.close()

    if blocked and not variant and os.path.isfile(dest):
        # il sito ha respinto l'accesso: si tiene l'ultima anteprima buona invece di sostituirla
        # con una pagina di errore o di verifica
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"path": name, "blocked": True, "kept_previous": True}

    os.replace(tmp, dest)                        # scrittura atomica: mai un file mezzo scritto

    if variant:
        try:
            import json as _json
            with open(dest[:-4] + ".json", "w", encoding="utf-8") as fh:
                _json.dump(masks or {"w": WIDTH, "h": HEIGHT, "masks": []}, fh)
        except Exception:  # noqa: BLE001
            pass
        log.info("istantanea %s: %s (%s) http=%s", variant, name, url, http_status)
        return {"path": name, "http_status": http_status, "error_text": error_text, "blocked": blocked}

    # Miniatura per la lista siti: senza, il pannello caricherebbe 40+ screenshot a
    # piena risoluzione a ogni apertura (megabyte inutili). ~10 KB l'una.
    try:
        from PIL import Image
        with Image.open(dest) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_W, THUMB_W), Image.LANCZOS)
            im.save(thumb + ".tmp", "JPEG", quality=68, optimize=True)
        os.replace(thumb + ".tmp", thumb)
    except Exception as ex:  # noqa: BLE001
        log.warning("miniatura non creata per %s: %s", name, ex)

    log.info("screenshot ok: %s (%s)%s", name, url, " [accesso respinto dall'antibot]" if blocked else "")
    return {"path": name, "blocked": blocked}


# Frasi che compaiono solo quando un sito e' rotto (WordPress, Joomla, PHP, server).
_ERROR_PATTERNS = [
    "there has been a critical error", "si è verificato un errore critico", "errore critico",
    "error establishing a database connection", "errore nello stabilire una connessione al database",
    "briefly unavailable for scheduled maintenance", "temporaneamente non disponibile per una manutenzione",
    "fatal error:", "parse error:", "uncaught error", "uncaught exception",
    "internal server error", "service unavailable", "bad gateway",
    "the website is temporarily unable to service", "500 - errore",
]


def _find_error_text(text: str) -> str:
    low = text.lower()
    for p in _ERROR_PATTERNS:
        i = low.find(p)
        if i >= 0:
            return text[max(0, i - 10): i + 140].strip().replace("\n", " ")
    return ""


@app.post("/compare")
async def compare(payload: dict = Body(...)):
    """Confronta le istantanee prima/dopo di un sito.

    diff  = percentuale di pixel cambiati in modo evidente (immagini ridotte a 240x150,
            scala di grigi, soglia 40/255: ignora compressione JPEG e piccoli spostamenti)
    blank = la pagina dopo e' praticamente uniforme (pagina bianca o solo sfondo)
    """
    from PIL import Image, ImageChops, ImageStat
    site_id = int(payload.get("site_id") or 0)
    if site_id <= 0:
        raise HTTPException(422, "site_id obbligatorio")
    a = os.path.join(SHOTS_DIR, f"site_{site_id}_before.jpg")
    b = os.path.join(SHOTS_DIR, f"site_{site_id}_after.jpg")
    if not (os.path.isfile(a) and os.path.isfile(b)):
        raise HTTPException(404, "istantanee mancanti")
    size = (240, 150)
    with Image.open(a) as ia, Image.open(b) as ib:
        ga = ia.convert("L").resize(size, Image.BILINEAR)
        gb = ib.convert("L").resize(size, Image.BILINEAR)
    diff = ImageChops.difference(ga, gb)
    # zone che cambiano da sole (video, iframe, slider) di ENTRAMBE le foto: fuori dal conto
    ignore = _mask_grid(site_id, size)
    total = changed = 0
    for i, v in enumerate(diff.getdata()):
        if ignore[i]:
            continue
        total += 1
        if v > 40:
            changed += 1
    pct = round(changed * 100 / total, 1) if total else 0.0
    blank_after = ImageStat.Stat(gb).stddev[0] < 6
    blank_before = ImageStat.Stat(ga).stddev[0] < 6
    masked = round(100 - total * 100 / (size[0] * size[1]), 1)
    return {"diff": pct, "blank_after": blank_after, "blank_before": blank_before, "masked": masked}


def _mask_grid(site_id: int, size: tuple) -> list:
    """Griglia (alla risoluzione del confronto) dei pixel da ignorare: unione delle zone mobili
    annotate nella foto prima e in quella dopo."""
    import json as _json
    W, H = size
    grid = [False] * (W * H)
    for variant in ("before", "after"):
        path = os.path.join(SHOTS_DIR, f"site_{site_id}_{variant}.json")
        try:
            with open(path, encoding="utf-8") as fh:
                d = _json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        sw, sh = float(d.get("w") or WIDTH), float(d.get("h") or HEIGHT)
        for m in d.get("masks") or []:
            x, y, w, h = m
            x0, y0 = int(x / sw * W), int(y / sh * H)
            x1, y1 = min(W, int((x + w) / sw * W) + 1), min(H, int((y + h) / sh * H) + 1)
            for yy in range(max(0, y0), y1):
                row = yy * W
                for xx in range(max(0, x0), x1):
                    grid[row + xx] = True
    return grid
