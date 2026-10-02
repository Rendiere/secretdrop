# secretdrop — requirements

Status: approved (2 Oct 2026). No open questions.

## Problem

An agent needs a secret (API token, password) that only a human has. Today that means
the human pastes it into a chat window, where it gets logged, synced, and archived
forever. There is no clean path from "human's password manager" to "file on the box".

## Shape of the solution

The agent creates a one-time link that already knows where the secret goes and what it
is called. The human opens the link on their phone over Tailscale, reads the label,
pastes the value, submits. The value lands in the destination file. A callback tells the
agent it happened so the agent can go verify.

The human never types a path. The agent never sees the value.

## Functional requirements

### Link creation
- CLI: `secretdrop link --dest PATH --label "Telegram bot token" [--mode file|env --key NAME] [--ttl 30m]`
- Generates a random unguessable id (>=128 bits, URL-safe).
- Stores a pending request server-side: id, dest, label, mode, key, created, expires.
- Prints the URL, e.g. `http://100.64.0.1:7391/d/<id>`.
- Links are single-use and expire. Default TTL 30 minutes.

### Serving
- `secretdrop serve` binds a configured address. Default is the machine's Tailscale IP.
  Never 0.0.0.0 by default. Port configurable, default 7391.
- `GET /d/<id>` renders a page showing only label, destination, mode/key, expiry, and a
  password field. Minimal HTML, no JS frameworks, mobile first.
- `POST /d/<id>` writes the value and consumes the request.
- Unknown, used, or expired ids return the same generic 404 page. No oracle.

### Write modes
- `file`: the whole value becomes the contents of PATH.
- `env`: PATH is a dotenv file. `KEY=value` replaces an existing `KEY=` line in place;
  every other line is untouched; appended if absent.
- All writes atomic (temp file in the same directory, fsync, rename).
- Destination mode 0600. Created parent directories 0700. Existing file modes tightened
  to 0600.

### Destination allowlist
- Config holds a list of allowed paths/globs, e.g. `~/.openclaw/credentials/*`,
  `~/.openclaw/workspace-*/.secrets.env`.
- A destination is accepted only if, after expanding `~`, resolving symlinks on every
  existing ancestor, and rejecting any `..` component, the final absolute path matches
  an allowlist entry.
- Rejection happens at link-creation time, and again at write time.

### Secret handling
- The value is never written to any log, never echoed in any response body, never stored
  in the pending-request store, never passed to a callback.
- It exists only in the request body, in memory during the write, and in the destination
  file.
- Request logging records method, path prefix, id, and status. Never a body.

### Callbacks
On successful submit, fire whichever of these is configured:
- `command`: run via `sh -c` with env `SECRETDROP_ID`, `SECRETDROP_LABEL`,
  `SECRETDROP_DEST`, `SECRETDROP_MODE`, `SECRETDROP_KEY`. Never the value.
- `webhook`: POST JSON `{id,label,dest,mode,key}` to a URL.
Callback failure is logged and does not fail the submit — the secret is already written.

### Packaging
- Python 3 standard library only. One small package. No dependencies.
- Runs as a `systemd --user` service on Raspberry Pi arm64.
- State and config files are gitignored and created 0600.

## Tests (unittest)
Allowlist enforcement, path traversal, single-use, expiry, env-mode replace, 0600 perms,
no value in logs or responses, callback env carries no value.

## Threat model

Protects against: secrets sitting in chat logs and transcripts; an agent handling a
value it only needs to reference; a stray link being replayed later (single-use +
expiry); a compromised agent writing a secret somewhere it shouldn't (allowlist).

Does not protect against: anyone on the tailnet who gets the link inside its TTL —
the link is the authentication; a compromised host, which can read the destination
file anyway; the human pasting the wrong secret; traffic being plain HTTP, which is
fine only because WireGuard already encrypts the tailnet.

## Out of scope
Multi-user auth, secret rotation, reading secrets back, any storage backend beyond
the destination file.
