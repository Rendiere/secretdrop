#!/bin/sh
# secretdrop callback: wake the OpenClaw session that asked for a secret.
#
# Sends a system event straight to that session. It wakes the agent without
# posting anything to the chat channel; the agent reads the event, goes and
# reads the file, and carries on. The value is never in here.
#
# Point callback_command at this script. secretdrop sets SECRETDROP_SESSION when
# the link was made with --session; otherwise a SECRETDROP_SESSION in the
# service environment is used, and with neither the main session is woken.
# Set OPENCLAW_BIN if openclaw is not on PATH (systemd --user services get a
# very short PATH).
set -eu

OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
KEY_NOTE=""
[ -n "${SECRETDROP_KEY:-}" ] && KEY_NOTE=", key ${SECRETDROP_KEY}"
TEXT="secretdrop: '${SECRETDROP_LABEL}' was written to ${SECRETDROP_DEST} (mode ${SECRETDROP_MODE}${KEY_NOTE}). Read the file and carry on with whatever needed it."

if [ -n "${SECRETDROP_SESSION:-}" ]; then
  exec "$OPENCLAW_BIN" system event --text "$TEXT" --session-key "$SECRETDROP_SESSION" --mode now
fi
exec "$OPENCLAW_BIN" system event --text "$TEXT" --mode now
