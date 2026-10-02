# secretdrop

A one-time link that lets a person hand a secret to a machine. An agent asks for
"the Telegram bot token" and gets back a URL; you open it on your phone over Tailscale,
paste the token, and it lands in the file the agent named, owned by you at mode 0600.

The secret never goes through a chat window, a transcript, or a log.

## How it works

1. The agent runs `secretdrop link --dest ... --label ...` and prints a URL.
2. You open the URL. The page shows the label, the destination file, and a
   password box. Nothing else.
3. You paste and submit. secretdrop writes the file atomically at mode 0600 and
   destroys the link.
4. A callback fires with the metadata — id, label, destination, mode, key — so the
   agent knows to go and read the file. The callback never carries the value.

## Threat model

**What it protects against**

- Secrets ending up in chat logs, transcripts, and backups, because a human had to
  paste them somewhere to get them to a machine.
- An agent handling a value it only needs to *reference*. The agent sees a path, not
  a token.
- A link being replayed later. Links work once and expire, 30 minutes by default.
- A compromised or confused agent writing a secret somewhere it shouldn't. Every
  destination is checked against an allowlist, with symlinks resolved and `..`
  refused outright, both when the link is made and again when it is used.
- The value being persisted anywhere else. It is never logged, never echoed back in
  a response, and never written to the pending-request store. A refused submit always
  either reads the body or closes the connection, so an unread body cannot be parsed
  as the next request and quoted back in an error page.
- A directory being swapped for a symlink mid-write. Every path component is opened
  with `O_NOFOLLOW` relative to the one above it.

**What it does not protect against**

- Anyone else on your tailnet who gets the link before it expires. The link *is* the
  authentication. Keep your tailnet small and your TTLs short.
- A compromised host. Anything that can run as your user can read the destination
  file directly.
- You pasting the wrong secret, or pasting into a link someone else created.
- Network snooping, in the sense that traffic is plain HTTP. That is only acceptable
  because WireGuard already encrypts everything on the tailnet. Do not put this on a
  public interface: secretdrop refuses to bind `0.0.0.0` or `::` unless you set
  `allow_unspecified_bind`.

## Install

Python 3.10+ and nothing else. No dependencies.

```bash
git clone https://github.com/Rendiere/secretdrop.git
cd secretdrop
pip install .          # or run it in place with: python3 -m secretdrop
```

## Config

Lives at `~/.config/secretdrop/config.json`, or wherever `SECRETDROP_CONFIG` points.
See `examples/config.example.json`.

```json
{
  "bind": "tailscale",
  "port": 7391,
  "state_path": "~/.local/state/secretdrop/pending.json",
  "allowlist": [
    "~/.openclaw/credentials/*",
    "~/.openclaw/workspace-*/.secrets.env"
  ],
  "default_ttl_seconds": 1800,
  "callback_command": "~/.config/secretdrop/openclaw-wake.sh",
  "callback_url": null
}
```

| Key | Meaning |
| --- | --- |
| `bind` | `"tailscale"` resolves your tailnet IPv4 at startup. Or give a literal address. Falls back to `127.0.0.1`. `0.0.0.0` and `::` are refused unless you also set `allow_unspecified_bind`. |
| `port` | Default 7391. |
| `base_url` | Override the printed URL, for a reverse proxy or a MagicDNS name. |
| `allowlist` | Where secrets may be written. `*` matches within one path segment and never across a `/`. Empty means nothing is allowed. |
| `default_ttl_seconds` | Link lifetime when `--ttl` is not given. Clamped to between 1 minute and 24 hours. |
| `callback_command` | Shell command run after a successful drop. |
| `callback_url` | URL that gets a JSON POST after a successful drop. |
| `allow_unspecified_bind` | Set to `true` to permit binding `0.0.0.0` or `::`. |

Both callbacks can be set. Either can be left out.

## Running it

As a `systemd --user` service — copy `examples/secretdrop.service` to
`~/.config/systemd/user/`, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now secretdrop
```

Or in the foreground: `secretdrop serve --verbose`.

A `systemd --user` service gets a short `PATH` that holds little more than
`/usr/bin`. If your callback shells out to anything, set `Environment=PATH=` in the
unit or use absolute paths.

## CLI

```bash
# whole file
secretdrop link --dest ~/.openclaw/credentials/bot.token --label "Telegram bot token"

# one variable in a dotenv file
secretdrop link --dest ~/.openclaw/workspace-myagent/.secrets.env \
  --label "Telegram bot token" --mode env --key BOT_TOKEN --ttl 10m

secretdrop list            # pending drops
secretdrop revoke <id>     # kill one early
```

`--mode file` writes the whole value as the file's contents.

`--mode env` sets `KEY=value` in a dotenv file. An existing assignment to `KEY` is
replaced where it sits, keeping its `export` prefix if it had one. Every line that
assigns something else is left exactly as it was.

If the file already assigns `KEY` twice, secretdrop rewrites the first and deletes
the rest, because dotenv readers disagree about which duplicate wins.

An env value cannot contain a newline. Without that rule a pasted value could carry
a second assignment into the file. A paste containing one is rejected and the link
stays alive for another go.

Labels are capped at 200 characters and cannot contain control or
direction-override characters, which would otherwise forge log lines or disguise
the destination shown on the page.

Add `--json` to `link` or `list` when a script is reading the output.

## Callbacks

The command callback runs through `sh -c` with these in its environment:

| Variable | Contents |
| --- | --- |
| `SECRETDROP_ID` | The drop id |
| `SECRETDROP_LABEL` | The label you gave the link |
| `SECRETDROP_DEST` | The file that was written |
| `SECRETDROP_MODE` | `file` or `env` |
| `SECRETDROP_KEY` | The variable name in env mode, empty otherwise |

The webhook callback POSTs the same five fields as JSON.

Neither carries the secret. The callback tells the agent *where* to look, and the
agent reads the file with its own permissions.

A failing callback is logged and ignored. The secret is already on disk by then, and
a failed notification must not look like a failed drop.

## OpenClaw example

`examples/openclaw-wake.sh` wakes the OpenClaw session that asked for the secret. It
schedules a one-shot cron job carrying a system event, which wakes the session
without posting anything to the chat channel:

```sh
exec "$OPENCLAW_BIN" cron create \
  --at "+5s" \
  --name "secretdrop-${SECRETDROP_ID}" \
  --session-key "${SESSION}" \
  --system-event "secretdrop: '${SECRETDROP_LABEL}' was written to ${SECRETDROP_DEST} ..." \
  --wake now \
  --delete-after-run
```

Point `callback_command` at it and set `SECRETDROP_SESSION` to the session key, for
example `agent:main:telegram:group:-100123456789`. Because of the short service
`PATH`, either add `openclaw`'s directory to `Environment=PATH=` in the unit or set
`OPENCLAW_BIN` to its full path.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## License

MIT.
