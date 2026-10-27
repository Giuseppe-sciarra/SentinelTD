#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# installa-aggiornamento.sh — pianifica aggiorna-tutto.sh ogni notte (come il CRM)
#
#   bash installa-aggiornamento.sh            → ogni notte alle 02:30
#   bash installa-aggiornamento.sh 3          → ogni notte alle 03:00
#   bash installa-aggiornamento.sh 1:30       → ogni notte alle 01:30
#   bash installa-aggiornamento.sh --status   → se è attivo, quando gira e l'ultimo esito
#   bash installa-aggiornamento.sh --remove   → toglie la pianificazione
#
# Usa un TIMER SYSTEMD (c'è anche nei container LXC di Proxmox): è "Persistent", quindi se
# all'ora stabilita il server era spento l'aggiornamento parte appena si riaccende.
# Se systemd non c'è ripiega su cron. Il log finisce in ./logs/aggiorna-tutto.log
#
# Perché le 02:30: lontano dal giro degli aggiornamenti dei siti (ogni ora alle :00) e prima
# della pulizia del registro eventi (03:10).
# ═════════════════════════════════════════════════════════════════════════════
PROGETTO="sentinel"
NOME="Sentinel TD"
ORA_DEF="2:30"
set -euo pipefail
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
UNIT="$PROGETTO-aggiorna-tutto"
CRON="/etc/cron.d/$PROGETTO-aggiorna-tutto"
LOG="$DIR/logs/aggiorna-tutto.log"

has_systemd() { [[ -d /run/systemd/system ]] && command -v systemctl > /dev/null 2>&1; }

remove_all() {
  if has_systemd; then
    systemctl disable --now "$UNIT.timer" > /dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$UNIT.service" "/etc/systemd/system/$UNIT.timer"
    systemctl daemon-reload || true
  fi
  rm -f "$CRON"
}

status() {
  echo "── Aggiornamento automatico di $NOME ──"
  if has_systemd && [[ -f "/etc/systemd/system/$UNIT.timer" ]]; then
    echo "Metodo: timer systemd ($(systemctl is-enabled "$UNIT.timer" 2>/dev/null || echo '?'))"
    systemctl list-timers "$UNIT.timer" --no-pager 2>/dev/null | sed -n '1,2p'
  elif [[ -f "$CRON" ]]; then
    echo "Metodo: cron"; cat "$CRON"
  else
    echo "⚠ NON pianificato. Lancia: bash installa-aggiornamento.sh"
  fi
  echo
  if [[ -s "$LOG" ]]; then
    echo "Ultime righe del log ($LOG):"
    tail -n 12 "$LOG"
  else
    echo "Log vuoto: l'aggiornamento automatico non è ancora partito."
  fi
}

case "${1:-}" in
  --remove) remove_all; echo "Pianificazione rimossa."; exit 0 ;;
  --status) status; exit 0 ;;
esac

if [[ $EUID -ne 0 ]]; then echo "Serve root."; exit 1; fi
ORA="${1:-$ORA_DEF}"
if ! [[ "$ORA" =~ ^([0-9]{1,2})(:([0-9]{2}))?$ ]] || (( 10#${BASH_REMATCH[1]} > 23 || 10#${BASH_REMATCH[3]:-0} > 59 )); then
  echo "Ora non valida: $ORA (es. 2 oppure 2:30)"; exit 2
fi
H=$((10#${BASH_REMATCH[1]})); M=$((10#${BASH_REMATCH[3]:-0}))
HHMM=$(printf '%02d:%02d' "$H" "$M")
mkdir -p "$DIR/logs"
chmod +x "$DIR/aggiorna-tutto.sh"

remove_all

if has_systemd; then
  cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=$NOME: aggiornamento automatico di tutto
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=$DIR
# docker compose vuole HOME per la sua configurazione; PATH esplicito per curl/flock/docker
Environment=HOME=/root
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/bin/bash -c 'bash "$DIR/aggiorna-tutto.sh" >> "$LOG" 2>&1'
TimeoutStartSec=3h
EOF
  cat > "/etc/systemd/system/$UNIT.timer" <<EOF
[Unit]
Description=$NOME: aggiornamento automatico ogni notte alle $HHMM

[Timer]
# ora italiana anche se il CT è in UTC (cambio ora legale/solare compreso)
OnCalendar=*-*-* $HHMM:00 Europe/Rome
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$UNIT.timer" > /dev/null
  echo "✅ Timer systemd installato: ogni notte alle $HHMM"
  systemctl list-timers "$UNIT.timer" --no-pager | sed -n '1,2p'
else
  { echo "CRON_TZ=Europe/Rome"; echo "$M $H * * * root cd $DIR && /usr/bin/flock -n /tmp/$UNIT.cron bash aggiorna-tutto.sh >> $LOG 2>&1"; } > "$CRON"
  echo "✅ Cron installato: ogni notte alle $HHMM"
  if ! pgrep -x cron > /dev/null 2>&1 && ! pgrep -x crond > /dev/null 2>&1; then
    echo "⚠ Il servizio cron NON è in esecuzione: avvialo con  systemctl enable --now cron"
  fi
fi

echo
echo "Stato:    bash installa-aggiornamento.sh --status"
echo "Log:      tail -f $LOG"
echo "Prova:    bash aggiorna-tutto.sh --dry-run"
echo "Adesso:   bash aggiorna-tutto.sh"
echo "Rimuovi:  bash installa-aggiornamento.sh --remove"
