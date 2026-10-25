#!/usr/bin/env bash
# Data Compare: Linux launcher. No root is needed except for a system-wide service.
#
#   ./deploy.sh setup               Check Python, create .venv with PyArrow, write deploy.env
#   ./deploy.sh start               Start in the background (nohup), or through systemd once installed
#   ./deploy.sh stop | restart      Stop after active comparisons finish; restart waits for that too
#   ./deploy.sh status | logs       Show whether it runs and where; follow the server log
#   ./deploy.sh install-service     Start at boot and after a crash (systemd); run with sudo for all users
#   ./deploy.sh uninstall-service   Remove that service again
#   ./deploy.sh run                 Run in the foreground (used by the systemd unit)
#
# Settings are read from deploy.env next to this script, or from COMPARE_CONFIG=/absolute/path.
set -euo pipefail
umask 077
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${COMPARE_CONFIG:-$APP_DIR/deploy.env}"
ACTION="${1:-start}"

die() { echo "Error: $*" >&2; exit 1; }
warn() { echo "Warning: $*" >&2; }
usage() { sed -n '4,10p' "$APP_DIR/deploy.sh" | sed 's/^# //'; }

case "$ACTION" in
  setup|start|stop|restart|status|logs|run|install-service|uninstall-service) ;;
  help|-h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if [[ -f "$CONFIG_FILE" ]]; then
  # Your own trusted shell configuration, editable only by the service administrator; never an uploaded file.
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi
BIND_HOST="${BIND_HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
DATA_DIR="${DATA_DIR:-$APP_DIR/data}"
RUN_DIR="${RUN_DIR:-$APP_DIR/run}"
MAX_JOBS="${MAX_JOBS:-}"                       # empty: the server picks from the CPU count
MAX_SORT_MB="${MAX_SORT_MB:-0}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
ANALYSIS_RETENTION_DAYS="${ANALYSIS_RETENTION_DAYS:-3}"
SERVICE_NAME="${SERVICE_NAME:-key-compare}"
STOP_TIMEOUT_SECONDS="${STOP_TIMEOUT_SECONDS:-900}"
PID_FILE="$RUN_DIR/server.pid"
LOG_FILE="$RUN_DIR/server.log"

require_linux() {
  [[ "$(uname -s)" == Linux ]] || die 'deploy.sh is for Linux servers. On a laptop use "Start CSV Compare.command" (macOS) or "Start CSV Compare.bat" (Windows).'
}

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) and sys.maxsize > 2**32 else 1)' >/dev/null 2>&1
}

# Sets PYTHON_BIN. "server" prefers the app's .venv (it has PyArrow); "base" looks for a system Python.
find_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "PYTHON_BIN=$PYTHON_BIN in $CONFIG_FILE was not found."
    python_ok "$PYTHON_BIN" || die "PYTHON_BIN=$PYTHON_BIN is not 64-bit Python 3.9 or newer."
    return
  fi
  local candidates=(python3.13 python3.12 python3.11 python3.10 python3.9 python3) candidate
  if [[ "$1" == server && -x "$APP_DIR/.venv/bin/python" ]]; then
    candidates=("$APP_DIR/.venv/bin/python" "${candidates[@]}")
  fi
  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
      PYTHON_BIN="$(command -v "$candidate")"
      return
    fi
  done
  die 'Python 3.9 or newer (64-bit) is required. Install it, for example "sudo dnf install python3.11" or
       "sudo apt install python3.11 python3.11-venv", or set PYTHON_BIN in deploy.env.'
}

check_config() {
  [[ -n "${PUBLIC_URL:-}" ]] || die "PUBLIC_URL is not set. Run ./deploy.sh setup, or set it in $CONFIG_FILE (for example http://compare.internal:8765)."
  [[ "$PUBLIC_URL" =~ ^https?://[^/]+/?$ ]] || die "PUBLIC_URL must be the address users open, such as http://10.0.0.25:8765, with no path."
  [[ "$BIND_HOST" == 0.0.0.0 || "$BIND_HOST" == 127.0.0.1 ]] || die 'BIND_HOST must be 0.0.0.0 (all interfaces) or 127.0.0.1 (behind a reverse proxy).'
  [[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || die 'PORT must be a number from 1 to 65535.'
  local name
  for name in MAX_SORT_MB RETENTION_DAYS ANALYSIS_RETENTION_DAYS STOP_TIMEOUT_SECONDS; do
    [[ "${!name}" =~ ^[0-9]+$ ]] || die "$name must be a whole number."
  done
  [[ -z "$MAX_JOBS" || "$MAX_JOBS" =~ ^[1-9][0-9]*$ ]] || die 'MAX_JOBS must be empty (automatic) or a positive number.'
}

prepare_dirs() {
  mkdir -p -- "$RUN_DIR" "$DATA_DIR"
  DATA_DIR="$(cd -- "$DATA_DIR" && pwd)"
  RUN_DIR="$(cd -- "$RUN_DIR" && pwd)"
  PID_FILE="$RUN_DIR/server.pid"
  LOG_FILE="$RUN_DIR/server.log"
}

# The full server command line, in SERVER_ARGS.
server_args() {
  SERVER_ARGS=(-u "$APP_DIR/server.py" --host "$BIND_HOST" --port "$PORT" --public-url "${PUBLIC_URL%/}"
               --data-dir "$DATA_DIR" --max-sort-mb "$MAX_SORT_MB" --retention-days "$RETENTION_DAYS"
               --analysis-retention-days "$ANALYSIS_RETENTION_DAYS")
  if [[ -n "$MAX_JOBS" ]]; then
    SERVER_ARGS+=(--max-jobs "$MAX_JOBS")
  fi
  local directory
  while IFS= read -r directory; do
    [[ -z "$directory" ]] && continue
    [[ -d "$directory" ]] || die "SOURCE_ROOTS entry is not a directory: $directory"
    SERVER_ARGS+=(--source-root "$directory")
  done <<< "${SOURCE_ROOTS:-}"
}

# A PID from the PID file, only if that process is this app on this data directory (never trust a stale file).
read_pid() {
  SERVER_PID=''
  if [[ -f "$PID_FILE" ]]; then read -r SERVER_PID < "$PID_FILE" || true; fi
  [[ "$SERVER_PID" =~ ^[0-9]+$ ]] || return 1
  "$PYTHON_BIN" - "$SERVER_PID" "$APP_DIR/server.py" "$DATA_DIR" <<'PY'
import pathlib, subprocess, sys
pid, script, data = sys.argv[1:4]
try:
    args = [a.decode() for a in pathlib.Path('/proc', pid, 'cmdline').read_bytes().split(b'\0') if a]
    valid = script in args and '--data-dir' in args and args[args.index('--data-dir') + 1] == data
except FileNotFoundError:
    # No /proc (not Linux): compare the command line as text.
    line = subprocess.run(['ps', '-o', 'command=', '-p', pid], capture_output=True, text=True).stdout
    valid = script in line and f'--data-dir {data}' in line
except (OSError, ValueError, IndexError, UnicodeError):
    valid = False
sys.exit(0 if valid else 1)
PY
}

# Waits for /health on this port; with a PID, the answer must come from that process.
wait_healthy() {
  "$PYTHON_BIN" - "$PORT" "${1:-}" "${2:-40}" <<'PY'
import json, sys, time, urllib.request
port, pid, tries = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(tries):
    try:
        with opener.open(f'http://127.0.0.1:{port}/health', timeout=.5) as response:
            result = json.load(response)
        if result.get('service') == 'key-based-compare' and (not pid or result.get('pid') == int(pid)):
            sys.exit(0)
    except (OSError, ValueError):
        pass
    time.sleep(.25)
sys.exit(1)
PY
}

# systemd service: "system", "user", or empty when this app is not installed as a service.
SYSTEM_UNIT="/etc/systemd/system/$SERVICE_NAME.service"
USER_UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$SERVICE_NAME.service"
service_scope() {
  if [[ -f "$SYSTEM_UNIT" ]] && grep -qF "$APP_DIR/deploy.sh" "$SYSTEM_UNIT"; then echo system
  elif [[ -f "$USER_UNIT" ]] && grep -qF "$APP_DIR/deploy.sh" "$USER_UNIT"; then echo user
  fi
}
as_admin() { if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi; }
service_ctl() {
  if [[ "$SCOPE" == user ]]; then systemctl --user "$@"
  elif [[ "$1" == status ]]; then systemctl "$@"   # reading status needs no sudo
  else as_admin systemctl "$@"; fi
}

lock_launcher() {
  command -v flock >/dev/null || die 'Install util-linux (it provides flock).'
  exec 9>"$RUN_DIR/deploy.lock"
  flock -n 9 || die 'Another deploy.sh command is running.'
}

started_message() {
  echo "Started: ${PUBLIC_URL%/}"
  echo "$1 | Data: $DATA_DIR"
}

nohup_start() {
  if read_pid; then echo "Already running (PID $SERVER_PID): ${PUBLIC_URL%/}"; return 0; fi
  server_args
  # The launcher lock is closed in the server; the server takes its own lock on the data directory.
  nohup "$PYTHON_BIN" "${SERVER_ARGS[@]}" >>"$LOG_FILE" 2>&1 < /dev/null 9>&- &
  SERVER_PID=$!
  printf '%s\n' "$SERVER_PID" > "$PID_FILE"
  if wait_healthy "$SERVER_PID"; then
    started_message "PID: $SERVER_PID | Log: $LOG_FILE"
  else
    echo "Startup could not be confirmed. The last lines of $LOG_FILE:" >&2
    tail -n 15 "$LOG_FILE" >&2
    exit 1
  fi
}

# Stops a nohup-started server. Returns 1 if it is still draining after $1 seconds.
nohup_stop() {
  local wait_seconds="$1" waited=0
  if ! read_pid; then echo 'Not running.'; rm -f -- "$PID_FILE"; return 0; fi
  kill -TERM "$SERVER_PID"
  echo "Stopping PID $SERVER_PID: new requests are refused; active comparisons finish first."
  while read_pid; do
    if (( waited >= wait_seconds )); then return 1; fi
    if (( waited > 0 && waited % 30 == 0 )); then echo "Still finishing active work ($waited s)…"; fi
    sleep 1
    waited=$((waited + 1))
  done
  rm -f -- "$PID_FILE"
  echo 'Stopped.'
}

do_setup() {
  require_linux
  find_python base
  echo "Python: $PYTHON_BIN ($("$PYTHON_BIN" -c 'import platform; print(platform.python_version())'))"
  local venv="$APP_DIR/.venv"
  if [[ ! -x "$venv/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$venv" || die 'Could not create .venv. On Debian/Ubuntu install the venv package for this Python
       (for example "sudo apt install python3.11-venv"), then run ./deploy.sh setup again.'
    echo "Created $venv"
  fi
  if "$venv/bin/python" -c 'import pyarrow' >/dev/null 2>&1; then
    echo 'PyArrow: installed (Parquet files are supported).'
  elif "$venv/bin/python" -m pip install --disable-pip-version-check -q -r "$APP_DIR/requirements.txt"; then
    echo 'PyArrow: installed (Parquet files are supported).'
  else
    warn 'PyArrow could not be installed, so Parquet files are not supported. CSV, DAT, Excel and JSON work.
         If the server has no internet access, set PIP_INDEX_URL to your internal package mirror and run ./deploy.sh setup again.'
  fi
  if [[ -n "${PYTHON_BIN_FROM_CONFIG:-}" ]]; then
    warn "$CONFIG_FILE sets PYTHON_BIN, so .venv (with PyArrow) is not used. Remove that line to use it."
  fi
  if [[ ! -f "$CONFIG_FILE" ]]; then
    local host
    host="$(hostname -f 2>/dev/null || hostname)"
    sed -e "s|^PUBLIC_URL=.*|PUBLIC_URL=\"http://$host:$PORT\"|" -e "s|^PORT=.*|PORT=$PORT|" "$APP_DIR/deploy.env.example" > "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    echo "Wrote $CONFIG_FILE with PUBLIC_URL=http://$host:$PORT"
    echo '  Users must open exactly this address. If they reach the server by IP or another DNS name, edit PUBLIC_URL.'
  else
    echo "Settings: $CONFIG_FILE (unchanged)"
  fi
  command -v flock >/dev/null || warn 'flock was not found. Install util-linux before starting.'
  prepare_dirs
  local cpus
  cpus="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo '?')"
  echo "Data directory: $DATA_DIR ($(df -h "$DATA_DIR" | awk 'NR==2 {print $4 " free"}')); CPUs: $cpus"
  echo
  echo 'Next: ./deploy.sh start        (or ./deploy.sh install-service to start at boot and after a crash)'
}

do_install_service() {
  require_linux
  command -v systemctl >/dev/null || die 'systemd is not available on this server. Use ./deploy.sh start instead.'
  check_config
  find_python server
  prepare_dirs
  local unit wanted user_line=''
  if [[ $EUID -eq 0 ]]; then
    SCOPE=system unit="$SYSTEM_UNIT" wanted=multi-user.target
    local owner="${SERVICE_USER:-$(ls -ld -- "$APP_DIR" | awk '{print $3}')}"
    [[ "$owner" != root ]] || die 'Set SERVICE_USER in deploy.env to the ordinary account that should run the app.'
    id -u "$owner" >/dev/null 2>&1 || die "SERVICE_USER $owner does not exist."
    chown -- "$owner" "$DATA_DIR" "$RUN_DIR"
    user_line="User=$owner"
  else
    SCOPE=user unit="$USER_UNIT" wanted=default.target
  fi
  # A server started with nohup must exit first; both cannot use one data directory.
  if read_pid; then
    lock_launcher
    nohup_stop "$STOP_TIMEOUT_SECONDS" || die "The running server is still finishing work. Try again when ./deploy.sh status says it stopped."
  fi
  mkdir -p -- "$(dirname -- "$unit")"
  cat > "$unit" <<UNIT
[Unit]
Description=Data Compare ($APP_DIR)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
$user_line
WorkingDirectory=$APP_DIR
Environment="COMPARE_CONFIG=$CONFIG_FILE"
ExecStart="$APP_DIR/deploy.sh" run
Restart=on-failure
RestartSec=10
# Only the server gets SIGTERM, so it can finish active comparisons; its workers are stopped at the timeout.
KillMode=mixed
TimeoutStopSec=$STOP_TIMEOUT_SECONDS
LimitNOFILE=65536

[Install]
WantedBy=$wanted
UNIT
  chmod 644 "$unit"
  service_ctl daemon-reload
  service_ctl enable --now "$SERVICE_NAME"
  if [[ "$SCOPE" == user ]] && ! loginctl enable-linger "$(id -un)" >/dev/null 2>&1; then
    warn "To start at boot without anyone logged in, an administrator runs: sudo loginctl enable-linger $(id -un)"
  fi
  if wait_healthy '' 80; then
    started_message "Service: $SERVICE_NAME ($SCOPE) | Logs: ./deploy.sh logs"
  else
    die "The service did not answer. See ./deploy.sh logs"
  fi
}

do_uninstall_service() {
  require_linux
  SCOPE="$(service_scope)"
  [[ -n "$SCOPE" ]] || { echo "No $SERVICE_NAME service is installed for $APP_DIR."; return 0; }
  service_ctl disable --now "$SERVICE_NAME"
  if [[ "$SCOPE" == system ]]; then as_admin rm -f -- "$SYSTEM_UNIT"; else rm -f -- "$USER_UNIT"; fi
  service_ctl daemon-reload
  echo "Removed the $SERVICE_NAME service. Use ./deploy.sh start to run without it."
}

PYTHON_BIN_FROM_CONFIG="${PYTHON_BIN:-}"
case "$ACTION" in
  setup) do_setup; exit 0 ;;
  install-service) do_install_service; exit 0 ;;
  uninstall-service) do_uninstall_service; exit 0 ;;
esac

require_linux
find_python server
SCOPE="$(service_scope)"
if [[ "$ACTION" != logs && "$ACTION" != status ]]; then check_config; fi
prepare_dirs

case "$ACTION" in
  run)
    server_args
    exec "$PYTHON_BIN" "${SERVER_ARGS[@]}"
    ;;
  start)
    if [[ -n "$SCOPE" ]]; then
      service_ctl start "$SERVICE_NAME"
      wait_healthy '' 80 || die "The service did not answer. See ./deploy.sh logs"
      started_message "Service: $SERVICE_NAME ($SCOPE) | Logs: ./deploy.sh logs"
    else
      lock_launcher
      nohup_start
    fi
    ;;
  stop)
    if [[ -n "$SCOPE" ]]; then
      echo "Stopping $SERVICE_NAME; active comparisons finish first (up to $STOP_TIMEOUT_SECONDS s)…"
      service_ctl stop "$SERVICE_NAME"
      echo 'Stopped.'
    else
      lock_launcher
      nohup_stop 30 || echo "Still finishing active work. Large files can take a while; check ./deploy.sh status before starting again."
    fi
    ;;
  restart)
    if [[ -n "$SCOPE" ]]; then
      service_ctl restart "$SERVICE_NAME"
      wait_healthy '' 80 || die "The service did not answer. See ./deploy.sh logs"
      started_message "Service: $SERVICE_NAME ($SCOPE) | Logs: ./deploy.sh logs"
    else
      lock_launcher
      nohup_stop "$STOP_TIMEOUT_SECONDS" || die "Still finishing active work after $STOP_TIMEOUT_SECONDS s; not restarted. Check ./deploy.sh status."
      nohup_start
    fi
    ;;
  status)
    if [[ -n "$SCOPE" ]]; then
      service_ctl status --no-pager "$SERVICE_NAME" || true
      if wait_healthy '' 4; then echo "Healthy: ${PUBLIC_URL:-http://127.0.0.1:$PORT}"; else echo 'Not answering.'; exit 1; fi
    elif read_pid; then
      if wait_healthy "$SERVER_PID" 4; then health=healthy; else health='not answering yet'; fi
      echo "Running (PID $SERVER_PID, $health): ${PUBLIC_URL:-http://127.0.0.1:$PORT}"
      echo "Log: $LOG_FILE | Data: $DATA_DIR"
    else
      echo 'Not running.'
      exit 1
    fi
    ;;
  logs)
    if [[ -n "$SCOPE" ]]; then
      if [[ "$SCOPE" == system ]]; then as_admin journalctl -u "$SERVICE_NAME" -n 200 -f; else journalctl --user -u "$SERVICE_NAME" -n 200 -f; fi
    else
      [[ -f "$LOG_FILE" ]] || die "No log yet at $LOG_FILE."
      tail -n 200 -f -- "$LOG_FILE"
    fi
    ;;
esac
