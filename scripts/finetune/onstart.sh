#!/usr/bin/env bash
# Runs on boot (via cron @reboot, or as the Vast on-start script).
#
# The power-cut path. A rebooted box has an empty tmux and an idle GPU that is
# still being billed; this notices training was unfinished and picks it back up
# from the newest checkpoint without anyone logging in.
set -uo pipefail
WORKSPACE="${WORKSPACE:-/workspace}"
exec >>"$WORKSPACE/onstart.log" 2>&1

echo "[onstart] boot at $(date -Is)"

# Finished runs must not restart — otherwise a reboot after success would
# retrain from scratch and quietly overwrite a good model.
if [ -f "$WORKSPACE/DONE" ]; then
  echo "[onstart] DONE marker present; nothing to do."
  exit 0
fi

if [ ! -f "$WORKSPACE/.env" ]; then
  echo "[onstart] no .env — cannot resume unattended."
  exit 1
fi

# The container may come back before networking does; pushes and pulls both
# need the Hub reachable.
for i in $(seq 1 30); do
  if curl -sSf -m 5 https://huggingface.co > /dev/null 2>&1; then break; fi
  echo "[onstart] waiting for network ($i/30)"
  sleep 10
done

cd "$WORKSPACE" || exit 1
bash "$WORKSPACE/run.sh" start
echo "[onstart] training relaunched"
