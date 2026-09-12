"""Mode / provider descriptions for the TUI selector.

Cline-2 ships ``describeMode()`` (``sdk/packages/llms/src/providers/
session-mode.ts``) — a small, data-only function that returns label +
summary + pros + cons for a mode, so every picker / status line / doc
shares the same prose and the same comparison.

This module is the Python twin for the better-rlm TUI. It maps better-rlm's
three transport modes (``auto`` | ``claude-cli`` | ``api``) onto the
host / proxy terminology the cline-2 picker speaks, because the comparison
itself is the same shape:

  * host mode = talk to a model endpoint directly (API key)
  * proxy mode = spawn an installed agent CLI (reuses the existing login)

For better-rlm, "spawn the agent CLI" is ``claude-cli`` mode (drives the
official ``claude`` binary, reusing the Claude Code keychain), and "talk
to the endpoint" is ``api`` mode (Anthropic SDK + ``ANTHROPIC_API_KEY``).
``auto`` is the same as ``host`` but with keyless fallback — described
under the auto entry below so the picker's two-column compare stays a
strict binary.
"""

from __future__ import annotations

from dataclasses import dataclass

# -- Mode catalogue ----------------------------------------------------------

# These strings match ``auth.MODE_AUTO/CLI/API`` exactly. Re-stated as
# module-level constants so describe.py has no import dependency on
# auth.py (which would import rlm via config.py and slow CLI startup).
MODE_AUTO = "auto"
MODE_CLI = "claude-cli"
MODE_API = "api"

VALID_MODES: tuple[str, ...] = (MODE_AUTO, MODE_CLI, MODE_API)

# Cline-2 uses host / proxy terminology. We mirror it in the picker so the
# comparison reads identically to anyone familiar with that UI, but the
# persistence keys remain the better-rlm-native names (so existing
# config.yaml files keep working).
PROXY_MODE_LABEL = "Binary — proxy mode"
HOST_MODE_LABEL = "API — host mode"


@dataclass(frozen=True)
class ModeDescription:
    """Two-column compare entry for the mode picker."""

    label: str
    summary: str
    pros: tuple[str, ...]
    cons: tuple[str, ...]


# Mirrors MODE_DESCRIPTIONS in cline-2's session-mode.ts, with the
# better-rlm-specific facts woven in. ``claude-cli`` is the proxy path
# (it spawns the `claude` binary); ``api`` is the host path (it talks to
# the Anthropic endpoint directly with ANTHROPIC_API_KEY).
_MODE_DESCRIPTIONS: dict[str, ModeDescription] = {
    MODE_CLI: ModeDescription(
        label=PROXY_MODE_LABEL,
        summary="Spawn the installed `claude` CLI. The CLI runs the session.",
        pros=(
            "Reuses your existing Claude Code login (keychain or setup-token)",
            "No ANTHROPIC_API_KEY needed — uses the subscription",
            "No premium-model gating to work around",
            "Nothing extra to configure once `claude` is on PATH and signed in",
        ),
        cons=(
            "Requires the `claude` CLI installed and signed in",
            "Subject to subscription rate limits (retried with backoff)",
            "Anthropic-only — Gemini / OpenAI / Azure / Portkey need `mode: api`",
        ),
    ),
    MODE_API: ModeDescription(
        label=HOST_MODE_LABEL,
        summary="Talk to the model endpoint directly. better-rlm runs the session.",
        pros=(
            "Works with any provider (Anthropic, Gemini, OpenAI, Azure, Portkey)",
            "Higher limits than the subscription path",
            "Direct HTTP path — easier to debug and profile",
        ),
        cons=(
            "Needs an API key (ANTHROPIC_API_KEY / GEMINI_API_KEY / …) in .env",
            "Per-token cost on paid endpoints",
            "No reuse of an existing Claude Code login",
        ),
    ),
}

# `auto` shares the api-mode cons (it can fall back to api) but keeps the
# zero-setup property of cli. Rendered as its own row so the user sees
# "auto" before being asked to pick a concrete mode.
AUTO_DESCRIPTION = ModeDescription(
    label="Auto — pick the best path",
    summary=(
        "Prefer the `claude` CLI (proxy); fall back to ANTHROPIC_API_KEY (host) "
        "if the CLI isn't installed. Anthropic-only."
    ),
    pros=(
        "Zero setup — reuses the Claude Code login when present",
        "Falls back to ANTHROPIC_API_KEY so a fresh machine still works",
        "Anthropic-only path that picks the right transport per launch",
    ),
    cons=(
        "Non-Anthropic providers always resolve to `api`",
        "Less predictable than pinning `claude-cli` or `api` directly",
    ),
)


def describe_mode(mode: str) -> ModeDescription:
    """Return label/summary/pros/cons for a transport mode.

    Mirrors cline-2's ``describeMode()`` so the picker compares pros/cons
    the same way. Unknown modes return an empty description rather than
    raising — callers (the picker, the status line) render an empty entry
    as a validation hint instead of crashing on a config typo.
    """
    m = (mode or "").strip().lower()
    if m == MODE_AUTO:
        return AUTO_DESCRIPTION
    return _MODE_DESCRIPTIONS.get(
        m,
        ModeDescription(
            label=m or "(unknown mode)",
            summary=f"Unrecognised mode: {mode!r}. Expected one of {', '.join(VALID_MODES)}.",
            pros=(),
            cons=(
                "Pick a mode from the picker so config.yaml is rewritten with a valid value.",
            ),
        ),
    )


def all_modes() -> tuple[str, ...]:
    """Modes the picker offers, in the order they should be shown."""
    return VALID_MODES


# -- Provider catalogue ------------------------------------------------------

# Re-stated from config.PROVIDER_KEY_ENV so describe.py stays standalone.
# Tied by hand: if a provider is added in config, add it here too.
PROVIDERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "label": "Anthropic",
        "env": "ANTHROPIC_API_KEY",
        "keyless": "yes — works with `claude` CLI in claude-cli/auto mode",
    },
    "gemini": {
        "label": "Google Gemini",
        "env": "GEMINI_API_KEY",
        "keyless": "no — requires GEMINI_API_KEY",
    },
    "openai": {
        "label": "OpenAI",
        "env": "OPENAI_API_KEY",
        "keyless": "no — requires OPENAI_API_KEY",
    },
    "azure_openai": {
        "label": "Azure OpenAI",
        "env": "AZURE_OPENAI_API_KEY",
        "keyless": "no — requires AZURE_OPENAI_API_KEY",
    },
    "portkey": {
        "label": "Portkey",
        "env": "PORTKEY_API_KEY",
        "keyless": "no — requires PORTKEY_API_KEY",
    },
}


def describe_provider(provider: str) -> dict[str, str]:
    """Label / env-var / keyless flag for the provider picker row."""
    p = (provider or "").strip().lower()
    if not p:
        return {
            "label": "(unknown)",
            "env": "?",
            "keyless": "no — provider not recognised; pick one from the list",
        }
    info = PROVIDERS.get(p)
    if info is None:
        return {
            "label": provider or "(unknown)",
            "env": "?",
            "keyless": "no — provider not recognised; pick one from the list",
        }
    return {"label": info["label"], "env": info["env"], "keyless": info["keyless"]}


def all_providers() -> tuple[str, ...]:
    """Provider ids the picker offers, in declaration order."""
    return tuple(PROVIDERS.keys())
