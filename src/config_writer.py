"""Tiny config.yaml updater used by the TUI.

We need to update one or two keys in ``config.yaml`` from the picker
without disturbing the rest of the file (which carries comments, blank
lines, and a key order the README and ``config.yaml`` were hand-tuned
for). PyYAML's loader destroys comments and key order; ruamel.yaml keeps
both but is an extra dep.

So this module ships a hand-rolled line-rewriter. The format better-rlm
emits is a flat ``key: value`` document at the top of the file with one
key per line -- see the existing ``config.yaml``. Anything that does not
match ``^key: value$`` is left alone (comments, blanks, the long-form
multi-line keys, nested mappings). Only top-level scalar ``key: value``
lines are rewritten.

Why not ruamel.yaml? Two reasons:

  1. ``pyproject.toml`` keeps a tight, audited dependency surface. Adding
     ruamel.yaml to support a feature that only the TUI uses pulls a
     whole new library in for every install.
  2. The hand-rolled rewriter is easier to audit -- it has one branch per
     line and tests can pin its output exactly.

If the file ever gains nested mappings that the TUI must rewrite, this
module should be replaced by ruamel.yaml. Until then, ~50 lines and no
extra dep.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Match ``key: value`` where the value is a single scalar token (no
# nested mapping, no list, no further colons). Anchored to start-of-line
# so indented continuations of nested mappings are not rewritten.
_SCALAR_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


def _format_scalar(value: str) -> str:
    """Render a scalar value back to YAML -- quoted only when needed.

    The TUI only writes back booleans, enum strings and short free text.
    Booleans are bare; everything else is bare too unless it contains a
    colon, a hash or leading/trailing whitespace -- in which case a quoted
    scalar keeps the loader honest on the next ``load_config()``.
    """
    raw = str(value)
    v = raw.strip()
    if not v:
        return '""'
    if v.lower() in ("true", "false", "null", "yes", "no", "on", "off"):
        return v.lower()
    needs_quote = (
        ":" in v
        or "#" in v
        or v.startswith(("{", "[", "&", "*", "!", "|", ">", "%", "@", "`"))
        or v.endswith((",", "{", "["))
        or any(ch.isspace() for ch in v)
        or raw != v  # leading or trailing whitespace was stripped above
    )
    if needs_quote:
        escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return v


def read_scalar(path: Path, key: str) -> str | None:
    """Return the current string value of a top-level scalar key, or None.

    Used by the TUI's /status line and pickers so they show what's on disk
    rather than what's in the in-memory Config -- those can disagree
    when ``RLM_MODE`` / ``RLM_PROVIDER`` env vars win (see config.load_config).

    Inline ``# ...`` comments after the value are stripped, because the
    existing ``config.yaml`` ships keys like::

        mode: auto   # auto | claude-cli | api. auto: prefer the `claude` CLI

    and we want ``mode == "auto"`` to come back, not the trailing prose.
    """
    if not path.exists():
        return None
    for raw in path.read_text().splitlines():
        m = _SCALAR_LINE.match(raw)
        if m and m.group(1) == key:
            value = m.group(2)
            value = _strip_inline_comment(value)
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
                value = value.replace('\\"', '"').replace("\\\\", "\\")
            return value.strip() or None
    return None


def _strip_inline_comment(value: str) -> str:
    """Remove a trailing ``# comment`` if one is present outside quotes.

    Handles the common case of ``key: value   # comment`` without
    breaking on values that legitimately contain a hash (e.g. a URL or
    a fragment). Walking the string character-by-character respects
    single and double quotes.
    """
    in_single = False
    in_double = False
    for i, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return value[:i].rstrip()
    return value


def write_scalars(path: Path, updates: dict) -> bool:
    """Rewrite the listed top-level scalar keys in ``path``.

    Returns True if any line was changed. Unknown keys are appended at
    the end of the file (with a leading blank line if needed) so a
    config that doesn't yet have, e.g., ``mode:`` still gets one.

    Atomicity: write to ``path.with_suffix(path.suffix + ".tmp")`` first
    and rename, so an interrupted TUI session can never leave the file
    half-written.
    """
    if not updates:
        return False
    if not path.exists():
        lines = [f"{k}: {_format_scalar(v)}" for k, v in updates.items()]
        _atomic_write(path, "\n".join(lines) + "\n")
        return True

    text = path.read_text()
    lines = text.splitlines()
    seen: set = set()
    out: list = []
    changed = False
    for raw in lines:
        m = _SCALAR_LINE.match(raw)
        if m and m.group(1) in updates:
            key = m.group(1)
            new_value = _format_scalar(updates[key])
            new_line = f"{key}: {new_value}"
            if raw.strip() != new_line:
                changed = True
            out.append(new_line)
            seen.add(key)
        else:
            out.append(raw)

    missing = [k for k in updates if k not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("")
        for key in missing:
            out.append(f"{key}: {_format_scalar(updates[key])}")
        changed = True

    if not changed:
        return False
    _atomic_write(path, "\n".join(out) + "\n")
    return True


def _atomic_write(path: Path, body: str) -> None:
    """Write ``body`` to ``path`` via a same-directory temp file + rename.

    ``os.replace`` is atomic on POSIX and Windows, so an interrupted
    write can never leave a partial ``config.yaml`` on disk -- readers
    either see the old version or the new one, never a half-written
    intermediate. Mode bits are copied from the existing file so we
    don't accidentally tighten or loosen permissions on a previously
    private config.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
