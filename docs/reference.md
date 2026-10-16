# secretdrop reference

This page holds the detail behind the [README](../README.md): how a drop works, the configuration file, every command, callbacks, the OpenClaw wake script, running it as a service, the threat model, and the tests.

## How a drop works

1. The agent runs `secretdrop link --dest ... --label ...` and gets a URL.
2. You open the URL. The page shows the label, the destination file and a password box. Nothing else.
3. You paste and submit. secretdrop writes the file atomically at mode 0600 and destroys the link.
4. A callback fires with the metadata (id, label, destination, mode, key, session) so the agent knows to read the file. The callback never carries the value.

## Install

secretdrop needs Python 3.10 or later and has no dependencies.

```bash
uv tool install git+https://github.com/Rendiere/secretdrop
# or: pipx install git+https://github.com/Rendiere/secretdrop
# or, from a clone: pip install .   (or run in place with python3 -m secretdrop)
```

The agent skill installs separately; see the README. On Codex you can also install it with `$skill-installer https://github.com/Rendiere/secretdrop/tree/main/skills/secretdrop`.

## Configuration

The config lives at `~/.config/secretdrop/config.json`, or wherever `SECRETDROP_CONFIG` points. See `examples/config.example.json`.

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
| `bind` | `"tailscale"` resolves your tailnet IPv4 at startup, waiting up to two minutes for it and then exiting non-zero rather than falling back to `127.0.0.1`. Or give a literal address. `0.0.0.0` and `::` are refused unless you also set `allow_unspecified_bind`. |
| `port` | Default 7391. |
| `base_url` | Override the printed URL, for a reverse proxy or a MagicDNS name. |
| `state_path` | Where pending links are kept. Outcomes for `secretdrop wait` go in a `.outcomes.json` file beside it. |
| `allowlist` | Where secrets may be written. `*` matches within one path segment and never across a `/`. Empty means nothing is allowed. |
| `default_ttl_seconds` | Link lifetime when `--ttl` is not given. Clamped to between 1 minute and 24 hours. |
| `callback_command` | Shell command run after a successful drop. |
| `callback_url` | URL that gets a JSON POST after a successful drop. |
| `callback_timeout_seconds` | How long a callback may run. Default 20. |
| `allow_unspecified_bind` | Set to `true` to permit binding `0.0.0.0` or `::`. |

Both callbacks can be set. Either can be left out. The server reads the config when it starts, so restart it after a change.

## Commands

```bash
# whole file
secretdrop link --dest ~/.openclaw/credentials/bot.token --label "Telegram bot token"

# one variable in a dotenv file
secretdrop link --dest ~/.openclaw/workspace-myagent/.secrets.env \
  --label "Telegram bot token" --mode env --key BOT_TOKEN --ttl 10m

# wake a particular OpenClaw session instead of the service default
secretdrop link --dest ~/.openclaw/workspace-etch/.secrets.env \
  --label "Etch API key" --mode env --key ETCH_API_KEY \
  --session agent:etch:telegram:group:-100123456789:topic:2577

secretdrop wait <id> --timeout 90s   # block until it lands, fails, is revoked or expires
secretdrop list                      # pending drops
secretdrop revoke <id>               # kill one early
secretdrop serve --verbose           # run the server in the foreground
```

`--mode file` writes the whole value as the file's contents.

`--mode env` sets `KEY=value` in a dotenv file. An existing assignment to `KEY` is replaced where it sits, keeping its `export` prefix if it had one. Every line that assigns something else is left exactly as it was.

If the file already assigns `KEY` twice, secretdrop rewrites the first and deletes the rest, because dotenv readers disagree about which duplicate wins.

An env value cannot contain a newline. Without that rule a pasted value could carry a second assignment into the file. A paste containing one is rejected and the link stays alive for another go.

Labels are capped at 200 characters and cannot contain control or direction-override characters, which would otherwise forge log lines or disguise the destination shown on the page.

`--session` takes an OpenClaw session key: letters, digits and `:._@+-`, at most 200 characters. The key reaches a shell script, so anything else is refused.

Add `--json` to `link`, `list` or `wait` when a script is reading the output. `link --json` prints `id`, `url`, `dest`, `created_at`, `expires_at` and `session`.

### secretdrop wait

`wait` takes a full id or a unique prefix and reports how the link ended:

| Outcome | Exit | Meaning |
| --- | --- | --- |
| `landed` | 0 | The secret was written to the destination. |
| `failed` | 1 | The server could not write the file. |
| `revoked` | 1 | `secretdrop revoke` killed the link. |
| `expired` | 1 | The link passed its expiry unused. |
| `unknown` | 1 | No such link, or its outcome is more than 24 hours old. |
| `timeout` | 3 | `--timeout` passed while the link was still waiting. |

An ambiguous prefix exits 2. The server records `landed`, `failed` and `revoked` and keeps each record for 24 hours, so running `wait` after the drop finished still gives the right answer. Agents on hosts that cannot be woken, such as Claude Code and Codex, run `wait --timeout 90s` in a loop while it reports `timeout`.

## Callbacks

The command callback runs through `sh -c` with these in its environment:

| Variable | Contents |
| --- | --- |
| `SECRETDROP_ID` | The drop id |
| `SECRETDROP_LABEL` | The label you gave the link |
| `SECRETDROP_DEST` | The file that was written |
| `SECRETDROP_MODE` | `file` or `env` |
| `SECRETDROP_KEY` | The variable name in env mode, empty otherwise |
| `SECRETDROP_SESSION` | The link's `--session`, set only when the link has one |

Because `SECRETDROP_SESSION` is set only when the link carries a session, a value set in the service's environment acts as the default for links made without one.

The webhook callback POSTs the same fields as JSON: `id`, `label`, `dest`, `mode`, `key` and `session` (null when absent).

Neither carries the secret. The callback tells the agent where to look, and the agent reads the file with its own permissions.

A failing callback is logged and ignored. The secret is already on disk by then, and a failed notification must not look like a failed drop.

## OpenClaw wake script

`examples/openclaw-wake.sh` wakes the OpenClaw session that asked for the secret. It sends a system event straight to that session, which wakes the agent without posting anything to the chat channel:

```sh
openclaw system event --text "secretdrop: 'Telegram bot token' was written to ..." \
  --session-key "$SECRETDROP_SESSION" --mode now
```

Point `callback_command` at it. One agent can ask for a secret that belongs to another, for example a drop headed for `workspace-etch/.secrets.env` that should wake the Etch session. The session comes from the link's `--session`, then from `SECRETDROP_SESSION` in the service environment; with neither, the script leaves out `--session-key` and the main session is woken. An agent finds its own key with its `session_status` tool (`sessionKey="current"`). Because of the short service `PATH`, either add `openclaw`'s directory to `Environment=PATH=` in the unit or set `OPENCLAW_BIN` to its full path.

If the OpenClaw gateway restarts while the event is in flight, the event is lost. The agent skill covers this by checking the destination file when the human says they submitted.

## Running it as a service

### Linux

Copy `examples/secretdrop.service` to `~/.config/systemd/user/`, adjust `ExecStart` to the path `command -v secretdrop` prints, then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now secretdrop
loginctl enable-linger "$USER"   # keep it running when you are logged out
```

With `bind: "tailscale"`, startup polls for the tailnet address with a backoff for up to two minutes, because a reboot brings secretdrop and `tailscaled` up at about the same moment. If the address never arrives, the process exits non-zero and `Restart=on-failure` tries again. It will not quietly bind `127.0.0.1`: that looks like a working service while every link it prints is unreachable from the phone meant to open it.

A `systemd --user` service gets a short `PATH` that holds little more than `/usr/bin`. If your callback shells out to anything, set `Environment=PATH=` in the unit or use absolute paths.

The unit's sandbox (`ProtectSystem=strict`) only allows writes to the paths in `ReadWritePaths`. Add the fixed leading directory of each allowlist entry there (for `~/.openclaw/workspace-*/.secrets.env`, that is `%h/.openclaw`). `ReadWritePaths` takes no wildcards, and each directory must exist before the service starts.

### macOS

Copy `examples/com.secretdrop.serve.plist` to `~/Library/LaunchAgents/`, replace `YOU` with your username and the `secretdrop` path with the one `command -v secretdrop` prints, then:

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.secretdrop.serve.plist
launchctl kickstart -k "gui/$(id -u)/com.secretdrop.serve"   # restart after a config change
```

## Threat model

### What it protects against

- Secrets ending up in chat logs, transcripts and backups, because a human had to paste them somewhere to get them to a machine.
- An agent handling a value it only needs to reference. The agent sees a path, not a token.
- A link being replayed later. Links work once and expire, 30 minutes by default.
- A compromised or confused agent writing a secret somewhere it shouldn't. Every destination is checked against an allowlist, with symlinks resolved and `..` refused outright, both when the link is made and again when it is used.
- The value being persisted anywhere else. It is never logged, never echoed back in a response, and never written to the pending-link store or the outcome records. A refused submit always either reads the body or closes the connection, so an unread body cannot be parsed as the next request and quoted back in an error page.
- A directory being swapped for a symlink mid-write. Every path component is opened with `O_NOFOLLOW` relative to the one above it.

### What it does not protect against

- Anyone else on your tailnet who gets the link before it expires. The link is the authentication. Keep your tailnet small and your TTLs short.
- A compromised host. Anything that can run as your user can read the destination file directly.
- You pasting the wrong secret, or pasting into a link someone else created.
- Network snooping, in the sense that traffic is plain HTTP. That is only acceptable because WireGuard already encrypts everything on the tailnet. Do not put this on a public interface: secretdrop refuses to bind `0.0.0.0` or `::` unless you set `allow_unspecified_bind`.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## Regenerating the images

```bash
scripts/render-assets.sh
```

It needs Google Chrome and rebuilds the PNGs in `docs/assets/` from the SVG and HTML sources there.
