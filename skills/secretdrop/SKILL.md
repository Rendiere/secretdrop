---
name: secretdrop
description: Get an API key, token, password or other credential from the human without it ever appearing in chat. Use whenever you need a secret you do not have, or the human is about to paste one into the conversation. Sends a one-time link they open on their phone over Tailscale; the value lands in a file at mode 0600. Also sets secretdrop up on a machine that lacks it.
---

# secretdrop

secretdrop hands a secret from the human to this machine without it passing through the chat. You make a one-time link, the human opens it on their phone over their Tailscale network, pastes the value, and secretdrop writes it to a file only this user can read. You never see the value unless you read the file, and you never print it.

## When to use it

- You need a credential you do not have: an API key, a bot token, a password, a webhook secret.
- The human offers to paste a secret into the chat. Stop them, and send a link instead.
- The human already pasted a secret into the chat. Do not repeat it. Tell them in one sentence that it is now in the chat history, suggest they rotate it, and offer a link for the new value.

Never ask for a secret's value in chat, and never write one into a message, a log, a commit or a command line.

## Check that secretdrop is ready

Run `secretdrop --version`. If the command is missing, or a link you make does not open for the human, read `references/setup.md` next to this file and offer to set it up. Do not request the secret until setup has finished.

## Request a secret

1. Pick where the secret should go. It must match the allowlist in `~/.config/secretdrop/config.json`. Prefer the place the tool that needs it already reads from, for example `~/.openclaw/credentials/<name>.token`, or a key in a `.secrets.env` file with `--mode env --key NAME`.
2. If no allowlist entry covers that place, propose the single new entry to the human and say why. Edit the config only after they approve, then restart the service. Never widen the allowlist on your own.
3. Make the link with a label the human will recognise:

   ```sh
   secretdrop link --dest ~/.openclaw/credentials/telegram-bot.token --label "Telegram bot token" --json
   ```

   The JSON holds `id` and `url`. On OpenClaw, add `--session <key>` (see below).
4. Send the human the `url` with one line saying what to paste. The link works once and expires after 30 minutes by default (`--ttl 10m` to shorten it).
5. Wait for it to land, as described for your host below.
6. Use the file. Point the tool at its path, or load it into the environment of the one command that needs it. Do not `cat` it or echo it.

## Waiting on OpenClaw

Before making the link, call your `session_status` tool with `sessionKey="current"` and pass the session key it reports as `--session <key>`. When the secret lands, secretdrop wakes this session with a system event naming the label and the file. End your turn after sending the link, and carry on when that event arrives.

If the human says they submitted it and no event has arrived, check that the file exists and is non-empty, or run `secretdrop wait <id> --timeout 5s --json`. A sub-agent without `session_status` makes the link without `--session`; the main session is woken instead.

## Waiting on Claude Code, Codex and other hosts

Nothing wakes you, so wait for the result:

```sh
secretdrop wait <id> --timeout 90s --json
```

Run it again while it reports `timeout`; each call stays short enough for a command time limit. It prints `{"outcome": ..., "label": ..., "dest": ...}`:

| outcome | exit | what to do |
|---|---|---|
| `landed` | 0 | The file is written. Use it. |
| `timeout` | 3 | Still waiting. Run `wait` again, or tell the human you are still waiting if it has been several minutes. |
| `expired` | 1 | The link ran out. Tell the human and offer a fresh link. |
| `revoked` | 1 | Someone revoked it. Ask whether to make a new one. |
| `failed` | 1 | The server could not write the file. Tell the human, check the service log, and do not claim it worked. |
| `unknown` | 1 | No such link. Check the id. |

Report only what `wait` says. If it did not print `landed`, the secret is not there.

## Other commands

- `secretdrop list --json` shows pending links.
- `secretdrop revoke <id>` kills a link you no longer need, for example after the human tells you they sent the link somewhere by mistake.
