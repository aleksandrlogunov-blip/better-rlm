"""Tests for ``src.describe`` -- the data-only mode / provider catalogue.

Mirrors cline-2's ``describeMode()``-style tests: every value comes back
in the expected shape, every unknown input is rendered as a help hint
rather than raising, and ``all_modes`` / ``all_providers`` are stable.
"""

from __future__ import annotations

import pytest

from src.describe import (
    AUTO_DESCRIPTION,
    HOST_MODE_LABEL,
    MODE_API,
    MODE_AUTO,
    MODE_CLI,
    PROXY_MODE_LABEL,
    PROVIDERS,
    VALID_MODES,
    all_modes,
    all_providers,
    describe_mode,
    describe_provider,
)


def test_all_modes_returns_three_in_documented_order() -> None:
    """The picker shows modes in this exact order: auto first, then the two
    concrete ones. Same for VALID_MODES -- it is the source of truth for
    what is acceptable in ``mode:``."""
    assert all_modes() == (MODE_AUTO, MODE_CLI, MODE_API)
    assert VALID_MODES == (MODE_AUTO, MODE_CLI, MODE_API)


@pytest.mark.parametrize("mode", [MODE_AUTO, MODE_CLI, MODE_API])
def test_describe_mode_known_returns_full_description(mode: str) -> None:
    """Every known mode has a label, a non-empty summary, and pros/cons.

    Mirrors cline-2's contract: ``describeMode(mode)`` always returns a
    ModeDescription with at least one pro and at least one con so the
    picker's two-column compare never renders an empty column.
    """
    d = describe_mode(mode)
    assert d.label
    assert d.summary
    assert d.pros, f"mode {mode} has no pros"
    assert d.cons, f"mode {mode} has no cons"


def test_describe_mode_auto_uses_dedicated_entry() -> None:
    """auto is described separately from the cli/api pair -- the prose
    differs enough that conflating them would lie about fallback behaviour."""
    d = describe_mode(MODE_AUTO)
    assert d is AUTO_DESCRIPTION


def test_describe_mode_cli_is_proxy_label() -> None:
    """The picker's column header for ``claude-cli`` uses the proxy-mode
    label, matching cline-2's terminology."""
    d = describe_mode(MODE_CLI)
    assert d.label == PROXY_MODE_LABEL


def test_describe_mode_api_is_host_label() -> None:
    """The picker's column header for ``api`` uses the host-mode label."""
    d = describe_mode(MODE_API)
    assert d.label == HOST_MODE_LABEL


def test_describe_mode_unknown_renders_help_in_cons() -> None:
    """Unknown modes do NOT raise -- they return a description whose
    ``cons`` field carries the remediation hint, so the picker can render
    it without a try/except."""
    d = describe_mode("garbage")
    assert "Unrecognised mode" in d.summary
    assert "garbage" in d.summary
    assert d.pros == ()
    assert d.cons  # at least one remediation hint


@pytest.mark.parametrize("mode", [None, "", "  "])
def test_describe_mode_empty_or_none_safe(mode: str | None) -> None:
    """None / empty / whitespace must never raise -- the picker's /status
    line calls ``describe_mode`` on whatever is in config.yaml."""
    d = describe_mode(mode or "")
    assert d.summary
    assert d.cons


def test_all_providers_lists_anthropic_first() -> None:
    """Anthropic is the only keyless provider and the local default, so it
    sits at the top of the picker. Same ordering as PROVIDER_KEY_ENV."""
    providers = all_providers()
    assert providers[0] == "anthropic"
    assert "anthropic" in providers
    assert "openai" in providers
    assert "gemini" in providers


def test_providers_table_covers_config_provider_key_env() -> None:
    """Every entry in PROVIDERS has a label, env var, and keyless note --
    the picker row for each provider renders all three columns."""
    for pid, info in PROVIDERS.items():
        assert info["label"], f"{pid} missing label"
        assert info["env"], f"{pid} missing env"
        assert info["keyless"], f"{pid} missing keyless"


def test_describe_provider_known() -> None:
    info = describe_provider("anthropic")
    assert info["label"] == "Anthropic"
    assert info["env"] == "ANTHROPIC_API_KEY"
    assert "claude" in info["keyless"].lower()  # mentions the keyless path


def test_describe_provider_unknown_is_helpful() -> None:
    """Unknown provider ids produce a row that says so -- the picker shows
    the real label and a 'pick one from the list' hint, never a stack trace."""
    info = describe_provider("wat")
    assert info["label"] == "wat"
    assert info["env"] == "?"
    assert "pick one" in info["keyless"]


@pytest.mark.parametrize("provider", ["", "   "])
def test_describe_provider_empty_safe(provider: str) -> None:
    """Empty input never raises -- the picker relies on this for /status.
    Empty / whitespace returns a sentinel ``(unknown)`` label so the
    status line has something to render."""
    info = describe_provider(provider)
    assert info["label"] == "(unknown)"
    assert info["env"] == "?"
