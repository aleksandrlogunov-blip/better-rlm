"""Tests for ``src.config_writer`` -- the comment-preserving YAML rewriter.

The TUI uses this module to update one or two keys in ``config.yaml``
without disturbing comments or the rest of the file. These tests pin
the behaviour so an upstream config refactor can't silently break the
TUI's persistence layer.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.config_writer import _atomic_write, _format_scalar, read_scalar, write_scalars


def test_round_trip_simple(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("mode: auto\nprovider: anthropic\nroot_model: claude-sonnet-5\n")
    assert read_scalar(p, "mode") == "auto"
    write_scalars(p, {"mode": "claude-cli"})
    assert read_scalar(p, "mode") == "claude-cli"
    assert read_scalar(p, "provider") == "anthropic"  # untouched
    assert read_scalar(p, "root_model") == "claude-sonnet-5"  # untouched


def test_preserves_inline_comments(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        "# header\n"
        "mode: auto   # auto | claude-cli | api. auto: prefer the claude CLI\n"
        "provider: anthropic\n"
    )
    assert read_scalar(p, "mode") == "auto"
    write_scalars(p, {"mode": "api"})
    body = p.read_text()
    assert "mode: api" in body
    # The original inline comment on the OLD value is gone (it was on the
    # line we replaced), but the comments above and below it stay.
    assert "# header" in body
    assert "provider: anthropic" in body
    # We do NOT leave a stale "auto" anywhere -- the rewrite is in-place.
    assert "auto" not in body


def test_strips_inline_comment_on_read(tmp_path: Path) -> None:
    """The trailing ``# ...`` is documentation, not the value. read_scalar
    must NOT return it as part of the value -- otherwise /status would
    show ``mode: auto # auto | claude-cli | api. ...`` as the value."""
    p = tmp_path / "config.yaml"
    p.write_text("mode: auto   # the auto mode\nprovider: anthropic\n")
    assert read_scalar(p, "mode") == "auto"


def test_inline_comment_inside_quoted_value_is_kept(tmp_path: Path) -> None:
    """A hash inside a quoted string is data, not a comment."""
    p = tmp_path / "config.yaml"
    p.write_text('name: "abc # not a comment"\n')
    assert read_scalar(p, "name") == "abc # not a comment"


def test_appends_unknown_keys_at_end(tmp_path: Path) -> None:
    """If a key isn't in the file yet, write_scalars appends it. Used by
    the TUI when the operator adds a config key that wasn't pre-baked."""
    p = tmp_path / "config.yaml"
    p.write_text("mode: auto\n")
    write_scalars(p, {"new_key": "fresh_value"})
    body = p.read_text()
    assert "mode: auto" in body
    assert "new_key: fresh_value" in body
    # The new key comes after a blank line so it's visually distinct.
    assert "auto\n\nnew_key" in body


def test_no_change_returns_false(tmp_path: Path) -> None:
    """write_scalars returns False when every requested key already holds
    the desired value -- so the caller can skip the "wrote X" log."""
    p = tmp_path / "config.yaml"
    p.write_text("mode: auto\nprovider: anthropic\n")
    assert write_scalars(p, {"mode": "auto"}) is False
    assert write_scalars(p, {"mode": "auto", "provider": "anthropic"}) is False


def test_write_to_missing_file_creates_it(tmp_path: Path) -> None:
    """If config.yaml doesn't exist (fresh clone with install.sh skipped),
    the TUI's first write must create it -- not crash on FileNotFoundError."""
    p = tmp_path / "config.yaml"
    assert not p.exists()
    write_scalars(p, {"mode": "api"})
    assert p.exists()
    assert read_scalar(p, "mode") == "api"


def test_atomic_write_replaces_atomically(tmp_path: Path) -> None:
    """``_atomic_write`` goes via a temp file + os.replace so an interrupted
    write can't leave a half-written config on disk."""
    p = tmp_path / "config.yaml"
    p.write_text("mode: auto\n")
    _atomic_write(p, "mode: api\n")
    assert p.read_text() == "mode: api\n"
    assert not (tmp_path / "config.yaml.tmp").exists()


def test_atomic_write_sets_0600_on_posix(tmp_path: Path) -> None:
    """config.yaml can carry secrets -- the temp file is written 0600 so an
    attacker who can read the directory during the write still can't read
    the keys."""
    if os.name != "posix":
        pytest.skip("mode bits are POSIX-only")
    p = tmp_path / "config.yaml"
    _atomic_write(p, "mode: auto\n")
    mode = stat_S_IMODE(p.stat().st_mode)
    # Either the file is exactly 0o600 or the umask tightened it further.
    # (umask 0o077 + 0o600 = 0o600; umask 0o022 + 0o600 = 0o600 anyway.)
    assert mode & 0o077 == 0


def test_format_scalar_quotes_when_necessary() -> None:
    """Values with a colon, hash, or leading whitespace must round-trip
    through yaml.safe_load, so we quote them."""
    assert _format_scalar("plain") == "plain"
    assert _format_scalar("with: colon") == '"with: colon"'
    assert _format_scalar("with # hash") == '"with # hash"'
    assert _format_scalar(" leading space") == '" leading space"'


def test_format_scalar_bools_bare() -> None:
    """Booleans come back as bare ``true`` / ``false`` so YAML parses them
    as booleans, not as the strings ``"true"`` / ``"false"``."""
    assert _format_scalar("true") == "true"
    assert _format_scalar("false") == "false"
    assert _format_scalar("True") == "true"
    assert _format_scalar("FALSE") == "false"


def test_format_scalar_empty_is_quoted_empty_string() -> None:
    """An empty string would otherwise be parsed as ``null``. Quote it."""
    assert _format_scalar("") == '""'


def stat_S_IMODE(mode: int) -> int:
    import stat
    return stat.S_IMODE(mode)
