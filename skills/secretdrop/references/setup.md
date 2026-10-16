# Setting up secretdrop

Follow these steps in order when `secretdrop` is missing or not running. Tell the human what you are about to do before each step that changes the machine, and stop at the first step that fails.

Two rules hold throughout:

- The server listens only on this machine's Tailscale address. Never set `bind` to `0.0.0.0`, a LAN address, or set `allow_unspecified_bind`. If Tailscale is not working, stop and ask the human to fix it.
- The allowlist decides where secrets may be written. Propose it; the human approves it. Never save one they have not seen.

## 1. Check what is already there

```sh
secretdrop --version
tailscale ip -4
```

`tailscale ip -4` must print an address such as `100.101.102.103`. If it fails, ask the human to install Tailscale on this machine and on their phone, and to sign both into the same account. Wait until it works.

If `secretdrop --version` prints 0.2.0 or later and `~/.config/secretdrop/config.json` exists, skip to step 5.

## 2. Install the CLI

secretdrop needs Python 3.10 or later and has no dependencies. Use the first installer that exists:

```sh
uv tool install git+https://github.com/Rendiere/secretdrop
pipx install git+https://github.com/Rendiere/secretdrop
python3 -m pip install --user git+https://github.com/Rendiere/secretdrop
```

Then run `command -v secretdrop` and keep the path it prints; the service in step 4 runs that exact file. If it prints nothing, the installer's bin directory (usually `~/.local/bin`) is not on `PATH`: use the full path.

## 3. Propose the allowlist and write the config

Each allowlist entry is a path pattern where `*` matches within one directory level. Propose entries that cover only where this machine's tools read secrets. For OpenClaw:

```json
["~/.openclaw/credentials/*", "~/.openclaw/workspace-*/.secrets.env"]
```

For other setups, suggest one directory for this purpose, such as `~/.config/secrets/*`. Never propose `~/*`, `~/.ssh/*`, a shell startup file, or any entry whose fixed part is the home directory itself.

Show the human the list and ask them to approve it. If they reject it, save nothing and ask what to allow instead.

After approval, write `~/.config/secretdrop/config.json` with mode 0600, in a directory with mode 0700:

```json
{
  "bind": "tailscale",
  "port": 7391,
  "state_path": "~/.local/state/secretdrop/pending.json",
  "allowlist": ["<approved entries>"],
  "default_ttl_seconds": 1800,
  "callback_command": null,
  "callback_url": null
}
```

On OpenClaw, set `callback_command` in step 5. For each allowlist entry, take its leading directories up to the first segment with a `*` (for `~/.openclaw/workspace-*/.secrets.env` that is `~/.openclaw`) and create that directory with mode 0700 if it does not exist.

## 4. Run it as a service

The server must survive a reboot. Replace `<secretdrop>` below with the path from step 2, and `<tailscale-dir>` with the directory holding `tailscale` (`dirname "$(command -v tailscale)"`).

### Linux (systemd user service)

Write `~/.config/systemd/user/secretdrop.service` from this template:

```ini
[Unit]
Description=secretdrop
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=<secretdrop> serve
Environment=PYTHONUNBUFFERED=1
Environment=PATH=<tailscale-dir>:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=SECRETDROP_CONFIG=%h/.config/secretdrop/config.json
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=%h/.local/state/secretdrop <leading directories from step 3>

[Install]
WantedBy=default.target
```

`ReadWritePaths` takes no wildcards and fails on a missing path, which is why step 3 creates each leading directory. If OpenClaw's `openclaw` binary is not in one of the `PATH` directories, add its directory too.

```sh
mkdir -p ~/.local/state/secretdrop && chmod 700 ~/.local/state/secretdrop
systemctl --user daemon-reload
systemctl --user enable --now secretdrop
loginctl enable-linger "$USER"
systemctl --user status secretdrop --no-pager
```

`enable-linger` keeps the service running when nobody is logged in; it may ask for the human's password, so ask them to run it if it fails.

### macOS (launchd agent)

Write `~/Library/LaunchAgents/com.secretdrop.serve.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.secretdrop.serve</string>
  <key>ProgramArguments</key>
  <array><string><secretdrop></string><string>serve</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string><tailscale-dir>:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>Umask</key><integer>63</integer>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/Users/<user>/Library/Logs/secretdrop.log</string>
</dict>
</plist>
```

```sh
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.secretdrop.serve.plist
launchctl print "gui/$(id -u)/com.secretdrop.serve" | grep state
```

To restart after a config change: `launchctl kickstart -k "gui/$(id -u)/com.secretdrop.serve"`. On Linux: `systemctl --user restart secretdrop`.

## 5. Wake OpenClaw when a secret lands (OpenClaw only)

Skip this on Claude Code and Codex; they use `secretdrop wait`.

```sh
curl -fsSL https://raw.githubusercontent.com/Rendiere/secretdrop/main/examples/openclaw-wake.sh -o ~/.config/secretdrop/openclaw-wake.sh
chmod 700 ~/.config/secretdrop/openclaw-wake.sh
```

Read the script before using it. Set `"callback_command": "~/.config/secretdrop/openclaw-wake.sh"` in the config and restart the service. The script wakes the session named on the link (`secretdrop link --session`), or the main session when the link has none.

## 6. Test drop

Make a test link to a file inside the allowlist, for example:

```sh
secretdrop link --dest ~/.openclaw/credentials/secretdrop-test.token --label "secretdrop test (type anything)" --ttl 10m --json
```

Send the human the `url` and ask them to open it on their phone and submit any dummy text, such as `hello`. Then wait as the skill describes for your host. When it reports `landed`, confirm the file exists with mode 0600 (`ls -l`), delete it, and tell the human setup is done.

If the link does not open on the phone, check in this order: the phone is connected to Tailscale; the URL starts with this machine's `tailscale ip -4` address (if it shows `127.0.0.1`, Tailscale was not up when the server started, so restart the service); the service is running.
