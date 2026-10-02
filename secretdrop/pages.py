"""The three pages. Plain HTML, no JavaScript, sized for a phone."""

from __future__ import annotations

import datetime as _dt
import html

STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1.5rem 1.25rem 3rem;
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  max-width: 34rem; margin-inline: auto;
}
h1 { font-size: 1.35rem; margin: 0 0 1.25rem; }
dl { margin: 0 0 1.5rem; }
dt { font-size: 0.78rem; text-transform: uppercase; letter-spacing: .06em; opacity: .6; }
dd { margin: .15rem 0 .9rem; word-break: break-all; font-variant-numeric: tabular-nums; }
code { font: 0.9rem/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
label { display: block; font-weight: 600; margin-bottom: .4rem; }
input[type=password] {
  width: 100%; padding: .8rem .75rem; font-size: 1rem;
  border: 1px solid rgba(128,128,128,.5); border-radius: 8px;
  background: transparent; color: inherit;
}
button {
  margin-top: 1rem; width: 100%; padding: .85rem 1rem; font-size: 1rem; font-weight: 600;
  border: 0; border-radius: 8px; background: #1d6fdc; color: #fff;
}
.note { font-size: .85rem; opacity: .65; margin-top: 1.5rem; }
.bad { color: #c0392b; font-weight: 600; }
""".strip()


def _page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
{body}
</body>
</html>
""".encode()


def _expiry_text(expires_at: float) -> str:
    when = _dt.datetime.fromtimestamp(expires_at).strftime("%H:%M on %d %b")
    remaining = max(0, int(expires_at - _dt.datetime.now().timestamp()))
    minutes, seconds = divmod(remaining, 60)
    left = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
    return f"{when} ({left} left)"


def drop_page(req, error: str | None = None) -> bytes:
    key_row = (
        f"<dt>Key</dt><dd><code>{html.escape(req.key)}</code></dd>" if req.key else ""
    )
    error_row = f'<p class="bad">{html.escape(error)}</p>' if error else ""
    body = f"""<h1>{html.escape(req.label)}</h1>
{error_row}
<dl>
  <dt>Writes to</dt><dd><code>{html.escape(req.dest)}</code></dd>
  <dt>Mode</dt><dd>{html.escape(req.mode)}</dd>
  {key_row}
  <dt>Link expires</dt><dd>{html.escape(_expiry_text(req.expires_at))}</dd>
</dl>
<form method="post" autocomplete="off">
  <label for="value">Paste the secret</label>
  <input id="value" name="value" type="password" autocomplete="off"
         autocapitalize="off" autocorrect="off" spellcheck="false" required>
  <button type="submit">Drop it</button>
</form>
<p class="note">This link works once and is not stored anywhere after you submit.</p>"""
    return _page(req.label, body)


def done_page(req) -> bytes:
    body = f"""<h1>Saved</h1>
<p>{html.escape(req.label)} was written to <code>{html.escape(req.dest)}</code>.</p>
<p class="note">The link is now dead. Close this tab.</p>"""
    return _page("Saved", body)


def failed_page() -> bytes:
    body = """<h1>Could not save</h1>
<p>The secret was not written. Nothing was stored and the link is spent.</p>
<p class="note">Ask for a new link.</p>"""
    return _page("Could not save", body)


def gone_page() -> bytes:
    body = """<h1>Nothing here</h1>
<p>This link is unknown, already used, or expired.</p>
<p class="note">Ask for a new one.</p>"""
    return _page("Nothing here", body)
