#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
bundled_python="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ -x "$bundled_python" ]]; then
  "$bundled_python" server.py --open
else
  python3 server.py --open
fi
