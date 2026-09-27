#!/usr/bin/env bash
# Linux launcher. No root, pip installation, or login service required.
set -euo pipefail
umask 077
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${COMPARE_CONFIG:-$APP_DIR/deploy.env}"
if [[ -f "$CONFIG_FILE" ]]; then
  # This is your own trusted shell configuration file, not an uploaded file.
  source "$CONFIG_FILE"
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"
BIND_HOST="${BIND_HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
DATA_DIR="${DATA_DIR:-$APP_DIR/data}"
RUN_DIR="${RUN_DIR:-$APP_DIR/run}"
MAX_JOBS="${MAX_JOBS:-1}"
MAX_SORT_MB="${MAX_SORT_MB:-4096}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
mkdir -p -- "$RUN_DIR" "$DATA_DIR"
DATA_DIR="$(cd -- "$DATA_DIR" && pwd)"
RUN_DIR="$(cd -- "$RUN_DIR" && pwd)"
PID_FILE="$RUN_DIR/server.pid"
LOG_FILE="$RUN_DIR/server.log"
ACTION="${1:-start}"
case "$ACTION" in start|stop|status) ;; *) echo 'Usage: ./deploy.sh [start|stop|status]' >&2; exit 2;; esac
command -v "$PYTHON_BIN" >/dev/null || { echo 'Python 3.10+ is required.' >&2; exit 1; }
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"; assert sys.maxsize > 2**32, "64-bit Python is required"'
[[ "$(uname -s)" == Linux ]] || { echo 'deploy.sh is intended for Linux. Use the platform launcher locally.' >&2; exit 1; }
# Serialize start/stop commands, so two launches cannot overwrite each other's PID.
command -v flock >/dev/null || { echo 'Install util-linux (flock) before deployment.' >&2; exit 1; }
exec 9>"$RUN_DIR/deploy.lock"
flock -n 9 || { echo 'Another deployment command is running.' >&2; exit 1; }
read_pid() {
  SERVER_PID=''
  [[ -f "$PID_FILE" ]] && read -r SERVER_PID < "$PID_FILE" || true
  [[ "$SERVER_PID" =~ ^[0-9]+$ ]] || return 1
  # Verify identity before sending a signal; do not trust a stale PID file.
  "$PYTHON_BIN" - "$SERVER_PID" "$APP_DIR/server.py" "$DATA_DIR" <<'PY'
import pathlib,sys
try:
    args=pathlib.Path('/proc',sys.argv[1],'cmdline').read_bytes().split(b'\0')
    args=[a.decode() for a in args if a]
    valid=sys.argv[2] in args and '--data-dir' in args and args[args.index('--data-dir')+1]==sys.argv[3]
except (OSError,ValueError,IndexError,UnicodeError):
    valid=False
sys.exit(0 if valid else 1)
PY
}
if [[ "$ACTION" == status ]]; then
  if read_pid; then echo "Running (PID $SERVER_PID). Log: $LOG_FILE"; else echo 'Not running (or stale PID file).'; exit 1; fi
  exit 0
fi
if [[ "$ACTION" == stop ]]; then
  if ! read_pid; then echo 'No matching server process is running.'; exit 0; fi
  kill -TERM "$SERVER_PID"
  for ((attempt=0; attempt<30; attempt++)); do
    if ! read_pid; then rm -f -- "$PID_FILE"; echo 'Server stopped.'; exit 0; fi
    sleep 1
  done
  echo "Shutdown is draining active/queued jobs. It may take longer for large files. Check $LOG_FILE; do not start another instance yet."
  exit 0
fi
if read_pid; then echo "Already running (PID $SERVER_PID)."; exit 0; fi
[[ -n "${PUBLIC_URL:-}" ]] || { echo 'Set PUBLIC_URL in deploy.env, e.g. http://compare.internal:8765' >&2; exit 2; }
[[ "$BIND_HOST" == 0.0.0.0 || "$BIND_HOST" == 127.0.0.1 ]] || { echo "BIND_HOST must be 0.0.0.0 or 127.0.0.1" >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo 'PORT must be an integer.' >&2; exit 2; }
# Close the launcher lock in the server child; the server takes its own data lock.
nohup "$PYTHON_BIN" -u "$APP_DIR/server.py" --host "$BIND_HOST" --port "$PORT" \
  --public-url "$PUBLIC_URL" --data-dir "$DATA_DIR" --max-jobs "$MAX_JOBS" \
  --max-sort-mb "$MAX_SORT_MB" --retention-days "$RETENTION_DAYS" >>"$LOG_FILE" 2>&1 < /dev/null 9>&- &
SERVER_PID=$!
printf '%s\n' "$SERVER_PID" > "$PID_FILE"
if "$PYTHON_BIN" - "$PORT" "$SERVER_PID" <<'PY'
import json,sys,time,urllib.request
port,pid=int(sys.argv[1]),int(sys.argv[2])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(40):
    try:
        with opener.open(f'http://127.0.0.1:{port}/health',timeout=.5) as response:
            result=json.load(response)
        if result.get('service')=='key-based-compare' and result.get('pid')==pid:
            sys.exit(0)
    except (OSError,ValueError): pass
    time.sleep(.25)
sys.exit(1)
PY
then
  echo "Started: $PUBLIC_URL"
  echo "PID: $SERVER_PID | Log: $LOG_FILE | Data: $DATA_DIR"
else
  echo "Startup could not be confirmed. Inspect $LOG_FILE and ./deploy.sh status." >&2
  tail -n 15 "$LOG_FILE" >&2
  exit 1
fi
