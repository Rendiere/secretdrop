# secretdrop — implementation plan

Derived from docs/requirements.md. Tests first for every unit.

## Modules

| Module | Responsibility |
|---|---|
| `secretdrop/config.py` | Load/merge JSON config, expand `~`, defaults |
| `secretdrop/paths.py` | Allowlist matching, symlink resolution, traversal rejection |
| `secretdrop/store.py` | Pending-request store: create, get, consume (single-use), purge expired |
| `secretdrop/writer.py` | Atomic `file` and `env` writes, 0600 / 0700 modes |
| `secretdrop/callback.py` | Shell command + webhook POST, value-free env/payload |
| `secretdrop/pages.py` | HTML for drop page, done page, generic 404 |
| `secretdrop/server.py` | `http.server` handler: GET/POST `/d/<id>`, safe logging |
| `secretdrop/cli.py` | `link`, `serve`, `list`, `revoke` |

## Order of work

1. `paths.py` + tests — the security boundary, so it goes first.
2. `store.py` + tests — ids, expiry, single-use consume under a file lock.
3. `writer.py` + tests — atomicity, modes, env replace semantics.
4. `callback.py` + tests — env has no value.
5. `pages.py` + `server.py` + tests — end-to-end over a loopback server.
6. `cli.py` + tests.
7. README, LICENSE, systemd unit, examples.
8. Deploy on the Pi, verify end to end, delete the test secret.

## Key decisions

- **Store format**: one JSON file, whole-file rewrite under `fcntl.flock`. Volume is a
  handful of pending links; a database would be overbuilding.
- **Single-use**: consume is read-modify-write inside the same flock, so two concurrent
  submits cannot both win.
- **Expiry**: checked on read and purged on every write.
- **Generic 404**: unknown/used/expired all return the same page and status so the page
  cannot be used to probe which ids existed.
- **Allowlist matching**: `fnmatch` against the resolved absolute path. `*` does not
  cross `/`, enforced by comparing path segment counts.
- **Logging**: a custom `log_message` that prints only method, id, and status.
