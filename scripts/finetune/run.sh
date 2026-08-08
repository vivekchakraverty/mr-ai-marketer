#!/usr/bin/env bash
# Supervisor. Keeps training alive across crashes and survives SSH dropping.
#
# Two separate problems, two separate fixes:
#   * SSH disconnects  -> the work runs inside tmux, so it is never a child of
#                         your shell and closing the laptop cannot kill it.
#   * the trainer dies -> this loop restarts it, and train.py resumes from the
#                         newest checkpoint rather than starting over.
#
# Usage:  ./run.sh          start (or attach to) training
#         ./run.sh logs     follow the log
#         ./run.sh stop     stop everything

set -uo pipefail
WORKSPACE="${WORKSPACE:-/workspace}"
SESSION=finetune
LOG="$WORKSPACE/train.log"

# A crash loop that retries forever burns money on a rented GPU without making
# progress. Bail out if it fails repeatedly in quick succession — that pattern
# means a real error (OOM, bad config), not a transient blip.
MAX_FAILS=6
FAIL_WINDOW=600

start_loop() {
  cd "$WORKSPACE" || exit 1
  # shellcheck disable=SC1091
  [ -f "$WORKSPACE/.env" ] && set -a && . "$WORKSPACE/.env" && set +a

  fails=0
  window_start=$(date +%s)

  while true; do
    if [ -f "$WORKSPACE/DONE" ]; then
      echo "[run] DONE marker present — training complete." | tee -a "$LOG"
      break
    fi

    echo "[run] starting train.py at $(date -Is)" | tee -a "$LOG"
    "${PY:-/venv/main/bin/python}" "$WORKSPACE/train.py" 2>&1 | tee -a "$LOG"
    status=${PIPESTATUS[0]}

    if [ "$status" -eq 0 ]; then
      echo "[run] train.py exited cleanly." | tee -a "$LOG"
      break
    fi

    now=$(date +%s)
    if [ $((now - window_start)) -gt "$FAIL_WINDOW" ]; then
      fails=0
      window_start=$now
    fi
    fails=$((fails + 1))

    if [ "$fails" -ge "$MAX_FAILS" ]; then
      echo "[run] $fails failures inside ${FAIL_WINDOW}s — stopping. Check $LOG;" \
           "this is a real error, not a blip." | tee -a "$LOG"
      break
    fi

    echo "[run] exit $status — restarting in 30s (failure $fails/$MAX_FAILS)" | tee -a "$LOG"
    sleep 30
  done
}

case "${1:-start}" in
  start)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "Already running. Attach with: tmux attach -t $SESSION"
      exit 0
    fi
    tmux new-session -d -s "$SESSION" "bash $0 _loop"
    echo "Training started in tmux session '$SESSION'."
    echo "  follow:  $0 logs"
    echo "  attach:  tmux attach -t $SESSION   (detach with Ctrl-b then d)"
    echo "You can safely close this SSH connection now."
    ;;
  _loop)  start_loop ;;
  logs)   tail -f "$LOG" ;;
  stop)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "stopped." || echo "not running."
    ;;
  *) echo "usage: $0 [start|logs|stop]"; exit 1 ;;
esac
