#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# aggiorna-tutto.sh — aggiornamento AUTOMATICO di Sentinel TD, ogni notte (come il CRM)
# ─────────────────────────────────────────────────────────────────────────────
# Cosa aggiorna:
#   • api e worker: Python 3.12 all'ultima patch + Debian (con l'immagine base) e TUTTE le
#     librerie di api/requirements.txt all'ultima versione, major comprese;
#   • shooter: Playwright all'ultima versione insieme alla sua immagine (Chromium e Node
#     compresi) e le altre librerie di shooter/requirements.txt;
#     le righe con un limite (es. «httpx<1  # motivo») restano come sono: è il modo per
#     bloccarne una;
#   • Postgres (stessa major: 16) e Redis (stessa major: 7): l'immagine nuova, se è uscita.
#
# Come, nell'ordine:
#   1. calcola l'ultima versione delle librerie (pip, in un container usa e getta);
#   2. se è cambiato qualcosa (librerie, immagini base, Dockerfile) ricostruisce api, worker e
#      shooter e li riavvia (Sentinel resta giù 10-30 secondi);
#   3. li prova (api: /healthz, shooter: Chrome avviato): se non va → ROLLBACK ai
#      requirements, ai Dockerfile e alle immagini di prima;
#   4. stesso giro per Postgres / Redis: riavvio solo se l'immagine è cambiata, controllo
#      che risponda, altrimenti torna all'immagine di prima;
#   5. pulizia di Docker che non tocca le immagini di rollback;
#   6. email di riepilogo, spedita da Sentinel col server SMTP del .env (SMTP_*).
#
#   bash aggiorna-tutto.sh              → aggiornamento vero
#   bash aggiorna-tutto.sh --dry-run    → dice cosa farebbe, non tocca niente
#
# Variabili (nel .env oppure davanti al comando):
#   AGG_MAIL_A=tuo@indirizzo.it   chi riceve l'email (vince su AGG_MAIL_A_DEF qui sotto; vuoto ovunque = REPORT_TO)
#   AGG_MAIL_SEMPRE=0       1 = email anche quando era già tutto aggiornato
#   AGG_TIENI_ROLLBACK=2    quante immagini di rollback tenere (lo shooter pesa ~2,5 GB l'una)
# ═════════════════════════════════════════════════════════════════════════════
PROGETTO="sentinel"
NOME="Sentinel TD"
APP="api"                           # le prove e l'email partono da qui
SERVIZI_APP="api worker shooter"    # i servizi costruiti da questa cartella (gli altri sono immagini scaricate)
TIENI_DEF=2
AGG_MAIL_A_DEF="tuo@indirizzo.it"   # chi riceve l'email di riepilogo se nel .env non c'è AGG_MAIL_A

dentro() {   # $1 URL → la risposta, letta dal container dell'api (che non ha curl)
  docker compose exec -T "$APP" python -c 'import sys, urllib.request as u; print(u.urlopen(sys.argv[1], timeout=30).read().decode())' "$1" 2>/dev/null
}

prova() {   # $1 servizio → 0 se risponde come deve (worker, postgres, redis: stato e healthcheck)
  case "$1" in
    api)     dentro http://127.0.0.1:8080/healthz | grep -q '"ok"' ;;
    shooter) dentro http://shooter:8090/healthz | grep -q '"ok":true' ;;   # true = Chrome avviato
    *)       return 0 ;;
  esac
}

versione() {   # $1 servizio → versione leggibile per log ed email
  case "$1" in
    api)      docker compose exec -T api python --version 2>/dev/null | tr -d '\r' ;;
    shooter)  docker compose exec -T shooter python -c 'import os, subprocess, playwright; from importlib.metadata import version; n = subprocess.check_output([os.path.join(os.path.dirname(playwright.__file__), "driver", "node"), "--version"], text=True).strip(); print("Playwright", version("playwright") + ", Node", n)' 2>/dev/null | tr -d '\r' ;;
    postgres) docker compose exec -T postgres postgres --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+)+' | head -1 | sed 's/^/PostgreSQL /' ;;
    redis)    docker compose exec -T redis redis-server --version 2>/dev/null | grep -oE 'v=[0-9.]+' | sed 's/^v=/Redis /' ;;
    worker)   ;;
    *)        local i; i=$(immagine_di "$1"); [[ -n "$i" ]] && docker image inspect -f '{{.Created}}' "$i" 2>/dev/null | cut -c1-10 | sed 's/^/immagine del /' ;;
  esac
}

manda_email() {   # $1 oggetto, $2 testo — la spedisce Sentinel (app/email.py), stampa "a <indirizzo>"
  local a="${AGG_MAIL_A:-$(envval AGG_MAIL_A)}"; a="${a:-$AGG_MAIL_A_DEF}"
  docker compose exec -T -e AGG_A="$a" -e AGG_OGGETTO="$1" -e AGG_TESTO="$2" "$APP" python -c '
import asyncio, html, os
from app.config import settings
from app.email import send_report
a = os.environ["AGG_A"] or settings.REPORT_TO
if not settings.SMTP_HOST or not a:
    raise SystemExit("posta non configurata: nel .env servono SMTP_HOST e REPORT_TO (o AGG_MAIL_A)")
asyncio.run(send_report(os.environ["AGG_OGGETTO"], "<pre>" + html.escape(os.environ["AGG_TESTO"]) + "</pre>", to=a))
print("a", a)' 2>&1
}

set -uo pipefail
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
export TZ=Europe/Rome   # orari del log e delle immagini di rollback in ora italiana (il CT può essere in UTC)

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "Argomento sconosciuto: $arg"; exit 2 ;;
  esac
done

# ── Un solo aggiornamento alla volta ────────────────────────────────────────
exec 9> "/tmp/$PROGETTO-aggiorna-tutto.lock"
if ! flock -n 9; then
  echo "$(date '+%F %T') · un altro aggiornamento è già in corso, esco."
  exit 0
fi

log() { echo "$(date '+%F %T') · $*"; }
envval() { grep -E "^[[:space:]]*$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r'; }

MAIL_SEMPRE="${AGG_MAIL_SEMPRE:-$(envval AGG_MAIL_SEMPRE)}";   MAIL_SEMPRE="${MAIL_SEMPRE:-0}"
TIENI="${AGG_TIENI_ROLLBACK:-$(envval AGG_TIENI_ROLLBACK)}";   TIENI="${TIENI:-$TIENI_DEF}"
HEALTH_TRIES="${AGG_HEALTH_TRIES:-36}"                         # x5 s = 3 minuti

START_TS=$(date +%s)
STAMP="$(date '+%Y%m%d-%H%M')"
mkdir -p backups logs
RIEPILOGO=""        # righe dell'email
ESITO="nessuno"     # nessuno | ok | rollback | errore
aggiungi() { RIEPILOGO+="$*"$'\n'; }
peggiora() {        # l'esito finale è il peggiore incontrato
  case "$1" in
    errore)   ESITO="errore" ;;
    rollback) [[ "$ESITO" != "errore" ]] && ESITO="rollback" ;;
    ok)       [[ "$ESITO" == "nessuno" ]] && ESITO="ok" ;;
  esac
}

log "── Aggiornamento automatico di $NOME $( [[ $DRY_RUN == 1 ]] && echo '(dry-run) ' )──"

# Immagine e id di un servizio, letti dal suo container (vuoto se il servizio non è avviato).
immagine_di() { local c; c=$(docker compose ps -q "$1" 2>/dev/null | head -1); [[ -n "$c" ]] && docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null; }
id_di()       { local c; c=$(docker compose ps -q "$1" 2>/dev/null | head -1); [[ -n "$c" ]] && docker inspect -f '{{.Image}}' "$c" 2>/dev/null; }
base_di()     { awk '$1 == "FROM" { print $2; exit }' "$1"; }
# Immagine che il docker-compose.yml chiede per un servizio: può essere diversa da quella che gira
# (es. compose aggiornato a ghcr.io/browserless/chromium mentre gira ancora browserless/chrome).
immagine_compose() {
  docker compose config 2>/dev/null | awk -v s="  $1:" '
    $0 == s { f = 1; next }
    f && (/^[^ ]/ || /^  [^ ]/) { exit }
    f && $1 == "image:" { gsub(/"/, "", $2); print $2; exit }'
}

# Il servizio è in piedi, sano e risponde alla sua prova (al massimo HEALTH_TRIES x 5 s);
# poi deve restare su altri 20 s senza ripartire (un crash-loop qui si vede).
controlla() {
  local svc="$1" cid st h at
  for _ in $(seq 1 "$HEALTH_TRIES"); do
    sleep 5
    cid=$(docker compose ps -q "$svc" 2>/dev/null | head -1)
    [[ -n "$cid" ]] || continue
    read -r st h at <<< "$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}} {{.State.StartedAt}}' "$cid" 2>/dev/null)"
    [[ "$st" == running && ( "$h" == healthy || "$h" == - ) ]] || continue
    prova "$svc" || continue
    sleep 20
    [[ "$(docker inspect -f '{{.State.Status}} {{.State.StartedAt}}' "$cid" 2>/dev/null)" == "running $at" ]]
    return
  done
  return 1
}

mapfile -t DOCKERFILES < <(find . -maxdepth 2 -name Dockerfile -not -path './.git/*' | sort)
mapfile -t TUTTI < <(docker compose config --services 2>/dev/null)
ALTRI=()
for s in "${TUTTI[@]}"; do [[ " $SERVIZI_APP " == *" $s "* ]] || ALTRI+=("$s"); done

# ═════════════════════════════════════════════════════════════════════════════
# L'APP: librerie Python, immagine base, Dockerfile
# ═════════════════════════════════════════════════════════════════════════════
declare -A V_PRIMA
for s in $SERVIZI_APP; do V_PRIMA[$s]="$(versione "$s")"; done

# 1. Ultima versione di ogni libreria con "==" in requirements.txt, decisa da pip tutte insieme
#    (così restano compatibili fra loro). Le righe con un limite («httpx<1  # motivo») restano
#    come sono. Nel container usa e getta entra SOLO una cartella col requirements: niente .env.
rm -f .agg-modifiche.txt
LIB_ERR=0
: > logs/pip-risoluzione.log
for df in "${DOCKERFILES[@]}"; do
  d=$(dirname "$df"); req="$d/requirements.txt"; [[ -f "$req" ]] || continue
  base=$(base_di "$df")
  rm -rf .agg-tmp "$req.nuovo"; mkdir -p .agg-tmp; cp "$req" .agg-tmp/requirements.txt
  docker run --rm -v "$DIR/.agg-tmp:/w" -w /w "$base" sh -c '
    python3 -m pip install -q --root-user-action=ignore --disable-pip-version-check --upgrade pip > /dev/null 2>&1
    sed -E "s/^([A-Za-z0-9._-]+(\[[^]]*\])?)==[^ ;#]+/\1/" requirements.txt > sciolti.txt
    python3 -m pip install -q --root-user-action=ignore --disable-pip-version-check --dry-run --ignore-installed \
        --report /w/report.json -r sciolti.txt' >> logs/pip-risoluzione.log 2>&1
  if [[ -s .agg-tmp/report.json ]]; then
    docker run --rm -i -v "$DIR/.agg-tmp:/w" -w /w "$base" python3 - <<'PY' >> logs/pip-risoluzione.log 2>&1
import json, re
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
nuove = {norm(i["metadata"]["name"]): i["metadata"]["version"] for i in json.load(open("report.json"))["install"]}
def tipo(a, b):
    pa, pb = (re.split(r"[.\-+]", a) + ["0", "0"])[:3], (re.split(r"[.\-+]", b) + ["0", "0"])[:3]
    return "MAJOR" if pa[0] != pb[0] else "minor" if pa[1] != pb[1] else "patch"
righe, modifiche = [], []
for riga in open("requirements.txt"):
    m = re.match(r"^(\s*)([A-Za-z0-9._-]+)(\[[^\]]*\])?==([^\s;#]+)(.*)$", riga.rstrip("\n"))
    b = nuove.get(norm(m[2])) if m else None
    if b and b != m[4]:
        modifiche.append(f"{m[2]} {m[4]} → {b} ({tipo(m[4], b)})")
        riga = f"{m[1]}{m[2]}{m[3] or ''}=={b}{m[5]}\n"
    righe.append(riga if riga.endswith("\n") else riga + "\n")
open("requirements.txt.nuovo", "w").write("".join(righe))
open("modifiche.txt", "w").write("".join(x + "\n" for x in modifiche))
PY
  fi
  if [[ ! -s .agg-tmp/requirements.txt.nuovo ]]; then
    LIB_ERR=1
  elif [[ -s .agg-tmp/modifiche.txt ]]; then
    mv .agg-tmp/requirements.txt.nuovo "$req.nuovo"
    pre="   "; [[ "$d" != . ]] && pre="   ${d#./}: "
    sed "s#^#$pre#" .agg-tmp/modifiche.txt >> .agg-modifiche.txt
    # L'immagine Playwright deve avere la stessa versione del pacchetto pip.
    pw=$(grep -oiE '^playwright==[0-9.]+' "$req.nuovo" | cut -d= -f3)
    if [[ -n "$pw" ]] && grep -qE '^FROM mcr\.microsoft\.com/playwright/python:v' "$df"; then
      sed -E "s#^(FROM mcr\.microsoft\.com/playwright/python:v)[0-9.]+#\1$pw#" "$df" > "$df.nuovo"
      cmp -s "$df" "$df.nuovo" && rm -f "$df.nuovo"
    fi
  fi
done
rm -rf .agg-tmp

LIB_CAMBIATE=0
if [[ $LIB_ERR == 1 ]]; then
  log "⚠ non sono riuscito a calcolare le versioni nuove delle librerie (vedi logs/pip-risoluzione.log): tengo quelle di adesso"
  for df in "${DOCKERFILES[@]}"; do rm -f "$(dirname "$df")/requirements.txt.nuovo" "$df.nuovo"; done
  rm -f .agg-modifiche.txt
  peggiora errore
  aggiungi "❌ Librerie Python: controllo delle versioni nuove non riuscito, restano quelle di adesso."
  aggiungi "   Ultime righe di logs/pip-risoluzione.log:"
  aggiungi "$(tail -n 8 logs/pip-risoluzione.log 2>/dev/null | sed 's/^/   /')"
elif [[ -s .agg-modifiche.txt ]]; then
  LIB_CAMBIATE=1
  log "librerie Python da aggiornare:"; sed 's/^/   /' .agg-modifiche.txt
else
  log "librerie Python: già tutte all'ultima versione"
fi

# 2. Immagini base (Python + Debian, o Playwright): c'è una versione nuova?
#    Si scarica quella del Dockerfile che verrà usato (il .nuovo, se Playwright sale).
BASE_CAMBIATA=0
declare -A BASI_VECCHIE
for df in "${DOCKERFILES[@]}"; do
  BASI_VECCHIE[$(base_di "$df")]=1
  f="$df"; [[ -f "$df.nuovo" ]] && f="$df.nuovo"
  b=$(base_di "$f")
  prima=$(docker image inspect -f '{{.Id}}' "$b" 2>/dev/null || true)
  docker pull -q "$b" > /dev/null 2>&1 || log "⚠ pull di $b non riuscito"
  dopo=$(docker image inspect -f '{{.Id}}' "$b" 2>/dev/null || true)
  if [[ -n "$dopo" && "$prima" != "$dopo" ]]; then BASE_CAMBIATA=1; log "immagine base $b: NUOVA"
  else log "immagine base $b: invariata"; fi
done

# 3. Dockerfile, requirements o basi diversi da quelli dell'ultima build riuscita
#    (es. dopo uno zip, o una build fallita ieri). Al primo giro non c'è la firma: si ricostruisce.
firma() {
  { for df in "${DOCKERFILES[@]}"; do cat "$df" "$(dirname "$df")/requirements.txt" 2>/dev/null
      docker image inspect -f '{{.Id}}' "$(base_di "$df")" 2>/dev/null; done; } | sha256sum | cut -c1-16
}
FIRMA_BUILD=$(cat .agg-ultima-build 2>/dev/null || true)
FILE_CAMBIATI=0
[[ "$FIRMA_BUILD" != "$(firma)" ]] && FILE_CAMBIATI=1

DA_RICOSTRUIRE=0
MOTIVI=()
[[ $BASE_CAMBIATA == 1 ]] && { DA_RICOSTRUIRE=1; MOTIVI+=("immagine base nuova"); }
[[ $LIB_CAMBIATE == 1 ]]  && { DA_RICOSTRUIRE=1; MOTIVI+=("librerie Python"); }
[[ $FILE_CAMBIATI == 1 && $BASE_CAMBIATA == 0 ]] && { DA_RICOSTRUIRE=1; MOTIVI+=("$( [[ -z "$FIRMA_BUILD" ]] && echo 'primo giro' || echo 'Dockerfile/requirements/base diversi dall'"'"'ultima build')"); }

motivi() { local s; s=$(printf '%s, ' "${MOTIVI[@]}"); echo "${s%, }"; }
pulisci_nuovi() { for df in "${DOCKERFILES[@]}"; do rm -f "$(dirname "$df")/requirements.txt.nuovo" "$df.nuovo"; done; rm -f .agg-modifiche.txt; }

if [[ $DA_RICOSTRUIRE == 0 ]]; then
  log "$NOME: niente da aggiornare"
  pulisci_nuovi
elif [[ $DRY_RUN == 1 ]]; then
  log "dry-run: ricostruirei $SERVIZI_APP per: $(motivi)"
  for df in "${DOCKERFILES[@]}"; do [[ -f "$df.nuovo" ]] && log "dry-run: $df → $(base_di "$df.nuovo")"; done
  pulisci_nuovi
else
  log "ricostruisco $SERVIZI_APP per: $(motivi)"
  MODIFICHE="$(cat .agg-modifiche.txt 2>/dev/null)"
  # copia di riserva dei file che cambiano (backups/, le ultime 10 per file) e poi i nuovi al loro posto
  BAK=()
  for df in "${DOCKERFILES[@]}"; do
    for f in "$df" "$(dirname "$df")/requirements.txt"; do
      [[ -f "$f" ]] || continue
      b="backups/$(echo "${f#./}" | tr / _).$STAMP.bak"
      cp "$f" "$b"; BAK+=("$f=$b")
      ls -1t "backups/$(echo "${f#./}" | tr / _)".*.bak 2>/dev/null | tail -n +11 | xargs -r rm -f
      [[ -f "$f.nuovo" ]] && { cat "$f.nuovo" > "$f"; rm -f "$f.nuovo"; }   # cat: tiene proprietario e permessi
    done
  done
  rm -f .agg-modifiche.txt

  # l'immagine che gira adesso resta come <immagine>:rollback-<data>
  ROLLBACK=()
  for s in $SERVIZI_APP; do
    img=$(immagine_di "$s"); id=$(id_di "$s")
    [[ -n "$img" && -n "$id" ]] || continue
    docker tag "$id" "${img%%:*}:rollback-$STAMP"
    ROLLBACK+=("$s $img $id")
  done
  [[ ${#ROLLBACK[@]} -gt 0 ]] && log "immagini attuali salvate come <immagine>:rollback-$STAMP"

  rimetti_file() { local x; for x in "${BAK[@]}"; do cp "${x#*=}" "${x%%=*}"; done; }
  ripristina_app() {   # $1 = motivo
    local r s img id
    log "❌ $1: ROLLBACK di $NOME"
    rimetti_file
    for r in "${ROLLBACK[@]}"; do read -r s img id <<< "$r"; docker tag "$id" "$img"; done
    docker compose up -d --no-build $SERVIZI_APP >> logs/build.log 2>&1
    for s in $SERVIZI_APP; do
      if controlla "$s"; then log "$s tornato alla versione di prima e risponde"
      else log "⚠⚠ $s non risponde nemmeno con l'immagine di prima: controllare a mano (docker compose logs $s)"; fi
    done
  }

  if ! docker compose build --pull $SERVIZI_APP > logs/build.log 2>&1; then
    log "❌ build fallita (logs/build.log): non tocco $NOME che gira"
    rimetti_file
    peggiora errore
    aggiungi "❌ $NOME: la build della versione nuova non è riuscita. Quello che gira non è stato toccato."
    aggiungi "   Ultime righe di logs/build.log:"
    aggiungi "$(tail -n 12 logs/build.log | sed 's/^/   /')"
  else
    log "build ok, riavvio $SERVIZI_APP…"
    docker compose up -d $SERVIZI_APP >> logs/build.log 2>&1
    KO=""
    for s in $SERVIZI_APP; do controlla "$s" || { KO="$s"; break; }; done
    if [[ -n "$KO" ]]; then
      ERR=$(docker compose logs --tail 25 "$KO" 2>&1 | tail -25)
      ripristina_app "$KO non parte o non risponde con la versione nuova"
      peggiora rollback
      aggiungi "⚠ $NOME: con la versione nuova $KO non partiva o non rispondeva. È tornato alla versione di prima, funziona come ieri."
      aggiungi "   Ultime righe del log di $KO:"; aggiungi "$(printf '%s\n' "$ERR" | sed 's/^/   /')"
      [[ -n "$MODIFICHE" ]] && { aggiungi "   Versioni che erano state provate:"; aggiungi "$MODIFICHE"; }
      aggiungi "   Domani riprova da solo; se il problema resta, metti un limite alla libreria in requirements.txt (es. «nome<3  # motivo»)."
    else
      firma > .agg-ultima-build
      # basi non più usate (es. la Playwright vecchia): via il nome, i dati restano finché servono al rollback
      for b in "${!BASI_VECCHIE[@]}"; do
        grep -qsF "FROM $b" "${DOCKERFILES[@]}" || docker rmi "$b" > /dev/null 2>&1 || true
      done
      log "✅ $NOME aggiornato e provato"
      peggiora ok
      aggiungi "✅ $NOME aggiornato ($(motivi)), prove passate:"
      for s in $SERVIZI_APP; do
        v=$(versione "$s")
        [[ -n "$v" && "$v" != "${V_PRIMA[$s]}" ]] && aggiungi "   $s: ${V_PRIMA[$s]:-—} → $v"
      done
      [[ -n "$MODIFICHE" ]] && { aggiungi "   Librerie cambiate:"; aggiungi "$MODIFICHE"; }
      aggiungi "   Per tornare alla versione di prima, sull'host:"
      ripr="cd $DIR"
      for x in "${BAK[@]}"; do ripr+=" && cp ${x#*=} ${x%%=*}"; done
      for r in "${ROLLBACK[@]}"; do read -r s img id <<< "$r"; ripr+=" && docker tag ${img%%:*}:rollback-$STAMP $img"; done
      aggiungi "   $ripr && docker compose up -d --no-build $SERVIZI_APP"
    fi
  fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# Gli altri servizi: immagine nuova se c'è, riavvio solo se cambiata
# ═════════════════════════════════════════════════════════════════════════════
aggiorna_immagine() {   # $1 servizio
  local svc="$1" img voluta prima dopo vprima vdopo tag
  img=$(immagine_di "$svc")                       # quella che gira
  [[ -z "$img" ]] && { log "$svc: non avviato, salto"; return 0; }
  voluta=$(immagine_compose "$svc"); voluta="${voluta:-$img}"   # quella che chiede il compose
  prima=$(id_di "$svc")
  vprima=$(versione "$svc")
  if [[ $DRY_RUN == 1 ]]; then
    log "dry-run: controllerei se c'è un'immagine nuova di $svc ($voluta)"
    [[ "$voluta" != "$img" ]] && log "dry-run: $svc gira con $img ma il docker-compose.yml chiede $voluta: lo cambierei"
    return 0
  fi
  if ! docker compose pull -q "$svc" > /dev/null 2>&1; then
    log "⚠ $svc: pull di $voluta non riuscito, resta com'è"
    peggiora errore
    aggiungi "❌ $svc: non sono riuscito a scaricare l'immagine nuova (resta quella di prima)."
    return 0
  fi
  dopo=$(docker image inspect -f '{{.Id}}' "$voluta" 2>/dev/null || true)
  if [[ -z "$dopo" || "$prima" == "$dopo" ]]; then
    log "$svc: già all'ultima versione${vprima:+ ($vprima)}"
    return 0
  fi
  tag="rollback/$PROGETTO-$svc:$STAMP"
  docker tag "$prima" "$tag"
  if [[ "$voluta" != "$img" ]]; then log "$svc: il docker-compose.yml ora chiede $voluta (gira $img), cambio…"
  else log "$svc: immagine nuova, riavvio…"; fi
  docker compose up -d "$svc" > /dev/null 2>&1
  if controlla "$svc"; then
    vdopo=$(versione "$svc")
    log "✅ $svc aggiornato${vprima:+: $vprima}${vdopo:+ → $vdopo}"
    peggiora ok
    aggiungi "✅ $svc aggiornato${vprima:+: $vprima}${vdopo:+ → $vdopo}"
    [[ "$voluta" != "$img" ]] && aggiungi "   (immagine cambiata: $img → $voluta)"
    docker rmi "$tag" > /dev/null 2>&1   # funziona: l'immagine vecchia non serve più
    [[ "$voluta" != "$img" ]] && { docker rmi "$img" > /dev/null 2>&1 || true; }
  else
    log "❌ $svc non risponde con l'immagine nuova: torno a quella di prima"
    peggiora rollback
    aggiungi "⚠ $svc: con l'immagine nuova ($voluta) non rispondeva. È tornato alla versione di prima."
    docker tag "$prima" "$voluta"   # il compose usa questo nome: ci rimetto sotto l'immagine di prima
    docker compose up -d --no-build "$svc" > /dev/null 2>&1
    docker rmi "$tag" > /dev/null 2>&1
  fi
}
for s in "${ALTRI[@]}"; do aggiorna_immagine "$s"; done

# ═════════════════════════════════════════════════════════════════════════════
# Pulizia di Docker (senza `prune -a`: le immagini di rollback restano)
# ═════════════════════════════════════════════════════════════════════════════
if [[ $DRY_RUN == 0 ]]; then
  for s in $SERVIZI_APP; do
    img=$(immagine_di "$s"); [[ -n "$img" ]] || continue
    vecchie=$(docker image ls --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep "^${img%%:*}:rollback-" | sort -r | tail -n +$((TIENI + 1)))
    [[ -n "$vecchie" ]] && { log "$s: tolgo le immagini di rollback più vecchie (tengo le ultime $TIENI)"; echo "$vecchie" | xargs -r docker rmi > /dev/null 2>&1 || true; }
  done
  docker image prune -f > /dev/null 2>&1 || true
  docker builder prune -f --filter until=168h > /dev/null 2>&1 || true
fi

# ═════════════════════════════════════════════════════════════════════════════
# Email di riepilogo (la manda l'app, col suo SMTP del .env)
# ═════════════════════════════════════════════════════════════════════════════
DURATA=$(( $(date +%s) - START_TS ))
log "esito: $ESITO (${DURATA}s)"
if [[ $DRY_RUN == 0 && ( "$ESITO" != "nessuno" || "$MAIL_SEMPRE" == "1" ) ]]; then
  [[ "$ESITO" == "nessuno" ]] && aggiungi "Era già tutto all'ultima versione."
  aggiungi ""
  aggiungi "Durata: $((DURATA / 60)) min $((DURATA % 60)) s · log sull'host $(hostname): $DIR/logs/aggiorna-tutto.log"
  case "$ESITO" in
    ok)       OGGETTO="✅ $NOME: aggiornamento notturno riuscito" ;;
    rollback) OGGETTO="⚠ $NOME: aggiornamento notturno annullato, torna la versione di prima" ;;
    errore)   OGGETTO="❌ $NOME: aggiornamento notturno con errori" ;;
    *)        OGGETTO="$NOME: era già tutto aggiornato" ;;
  esac
  if RISP=$(manda_email "$OGGETTO" "$RIEPILOGO"); then
    log "email di riepilogo inviata $(printf '%s' "$RISP" | tail -1)"
  else
    log "⚠ email di riepilogo non inviata: $(printf '%s' "$RISP" | tail -1)"
  fi
fi
log "── fine ──"
[[ "$ESITO" == "errore" || "$ESITO" == "rollback" ]] && exit 1
exit 0
