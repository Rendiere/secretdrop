#!/bin/sh
# secretdrop callback: wake an OpenClaw session once a secret has landed.
#
# Creates a one-shot cron job carrying a system event. A system event nudges the
# session awake without posting anything to the chat channel; the agent reads the
# event, goes and reads the file, and carries on. The value is never in here.
#
# Point callback_command at this script. Set SECRETDROP_SESSION to the session
# key that asked for the secret, and OPENCLAW_BIN if openclaw is not on PATH
# (systemd --user services get a very short PATH).
set -eu

OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
SESSION="${SECRETDROP_SESSION:-main}"
KEY_NOTE=""
[ -n "${SECRETDROP_KEY:-}" ] && KEY_NOTE=", key ${SECRETDROP_KEY}"

exec "$OPENCLAW_BIN" cron create \
  --at "+5s" \
  --name "secretdrop-${SECRETDROP_ID}" \
  --session-key "${SESSION}" \
  --system-event "secretdrop: '${SECRETDROP_LABEL}' was written to ${SECRETDROP_DEST} (mode ${SECRETDROP_MODE}${KEY_NOTE}). Read the file and carry on with whatever needed it." \
  --wake now \
  --delete-after-run
