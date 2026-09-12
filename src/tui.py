"""Operator-facing TUI for configuring the better-rlm transport stack.

Mirrors cline-2's mode/model/provider picker (``apps/cli/src/tui/
components/model-selector/`` + ``hooks/use-model-selector.tsx``) on top of
rich.prompt -- so the same "compare two modes, pick a provider, pick a
model" flow is available here without pulling in a JS bundle. The CLI is
optional: the MCP server itself runs unchanged on ``python -m src.server``.

What this TUI does:

  * shows the current ``mode`` / ``provider`` / ``root_model`` /
    ``sub_model`` triple (the values that decide where model calls land);
  * lets the operator change any of them via rich.prompt pickers whose
    prose matches ``describe.py`` (which mirrors cline-2's describeMode);
  * runs ``uv run --extra dev pytest -q`` via the ``/test`` slash command
    so the TUI is the same surface operators use to verify a config
    change before they restart the server.

What it deliberately does NOT do:

  * does not spawn the MCP server, the rlm engine, or any model call;
  * does not write to ``.env`` (that's still the installer's job -- see
    install.sh --auth). It only touches ``config.yaml``.
  * does not require a TTY. The slash commands are read from stdin and
    output is plain text, so a one-shot ``/status`` works in CI / scripts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from . import config_writer
from .config import (
    MODEL_OPUS,
    MODEL_SONNET,
    MODEL_SONNET_5,
    MODEL_HAIKU,
    PROVIDER_KEY_ENV,
    PKG_ROOT,
)
from .describe import (
    MODE_API,
    MODE_AUTO,
    MODE_CLI,
    VALID_MODES,
    all_modes,
    all_providers,
    describe_mode,
    describe_provider,
)

# Sentinels that mean "the user wants to back out / re-enter the picker".
# The cline-2 picker uses the same idiom (``CHANGE_PROVIDER_ACTION``,
# ``BROWSE_ALL_ACTION`` in model-selector.tsx): a non-model string the
# caller pattern-matches on to redirect flow without raising.
ACTION_CHANGE_PROVIDER = "__change_provider__"
ACTION_CANCEL = "__cancel__"
ACTION_BACK = "__back__"


@dataclass(frozen=True)
class Status:
    """One snapshot of the operator-visible state.

    Frozen so the ``/status`` command and the pickers share one source of
    truth: a picker picks against this, then writes its result back and
    the next ``/status`` reflects the new disk state.
    """

    mode: str
    provider: str
    root_model: str
    root_model_override: str
    sub_model: str
    cli_path: str
    cli_available: bool
    cli_logged_in: bool | None  # None = unknown (probe failed)
    env_mode: str | None        # set when RLM_MODE override is in effect
    env_provider: str | None    # set when RLM_PROVIDER override is in effect
    has_api_key: bool

    def mode_is_pinned(self) -> bool:
        """True when an RLM_MODE env var overrides config.yaml.

        Tells the user the picker write may not take effect on the next
        server start -- the env var still wins.
        """
        return self.env_mode is not None and self.env_mode != self.mode


def load_status(config_path: Path | None = None) -> Status:
    """Snapshot the current configuration and runtime state from disk + env.

    The values shown are what ``config.load_config`` would resolve to on
    the NEXT server start, NOT the in-process state of any already-running
    MCP server. CLAUDE.md spells out why that distinction matters: a
    running server holds ``src/`` from startup, so a TUI write is not
    live until the server reconnects.
    """
    cfg_path = config_path or (PKG_ROOT / "config.yaml")

    def _read(key: str, default: str) -> str:
        return config_writer.read_scalar(cfg_path, key) or default

    mode = _read("mode", MODE_AUTO)
    provider = _read("provider", "anthropic")
    root_model = _read("root_model", MODEL_SONNET_5)
    root_model_override = _read("root_model_override", MODEL_OPUS)
    sub_model = _read("sub_model", MODEL_HAIKU)
    cli_path = _read("cli_path", "claude")

    env_mode = os.getenv("RLM_MODE")
    env_provider = os.getenv("RLM_PROVIDER")
    cli_available = shutil.which(cli_path) is not None

    # CLI login probe is non-fatal -- a probe failure is reported as
    # "unknown", not as an exception, so /status always renders.
    cli_logged_in: bool | None = None
    if cli_available:
        try:
            out = subprocess.run(
                [cli_path, "auth", "status"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout:
                cli_logged_in = "loggedIn" in out.stdout
        except (OSError, subprocess.TimeoutExpired):
            cli_logged_in = None

    api_env = PROVIDER_KEY_ENV.get(provider.strip().lower())
    has_api_key = bool(api_env and os.getenv(api_env))

    return Status(
        mode=mode,
        provider=provider,
        root_model=root_model,
        root_model_override=root_model_override,
        sub_model=sub_model,
        cli_path=cli_path,
        cli_available=cli_available,
        cli_logged_in=cli_logged_in,
        env_mode=env_mode,
        env_provider=env_provider,
        has_api_key=has_api_key,
    )


def render_status(st: Status) -> str:
    """Plain-text rendering of one Status -- used by /status and pickers.

    Kept string-returning (not Console.print) so the same function serves
    the interactive TUI and any future log/capture path.
    """
    cli_line = (
        "available" if st.cli_available else f"NOT on PATH (looked for {st.cli_path!r})"
    )
    if st.cli_available:
        if st.cli_logged_in is True:
            cli_line += ", logged in"
        elif st.cli_logged_in is False:
            cli_line += ", NOT LOGGED IN"
        else:
            cli_line += ", login status unknown"

    pin = ""
    if st.env_mode:
        pin += f"\n  RLM_MODE={st.env_mode} env var pins mode at registration (wins over config.yaml)"
    if st.env_provider:
        pin += f"\n  RLM_PROVIDER={st.env_provider} env var pins provider at registration"

    api_env = PROVIDER_KEY_ENV.get(st.provider.strip().lower())
    api_line = (
        f"{api_env}=set"
        if st.has_api_key and api_env
        else f"{api_env or '?'}=MISSING"
        if api_env
        else "no key required"
    )

    return (
        f"mode:           {st.mode}\n"
        f"provider:       {st.provider}\n"
        f"root_model:     {st.root_model}\n"
        f"override_model: {st.root_model_override}\n"
        f"sub_model:      {st.sub_model}\n"
        f"claude cli:     {cli_line}\n"
        f"api key:        {api_line}"
        f"{pin}"
    )


# -- Pickers ---------------------------------------------------------------

# Constants used as prompt answer values. Kept as module-level so test
# code can drive the pickers headlessly without spinning up a fake stdin.
PICKER_KEEP = "__keep__"
PICKER_CUSTOM = "__custom__"


def _prompt_choice(
    console: Console,
    title: str,
    options: list[tuple[str, str]],
    current: str,
    allow_custom: bool = False,
    custom_hint: str = "type a value",
) -> str:
    """Render a numbered picker and return the chosen value.

    Mirrors cline-2's ``ModelSelectorContent``: numbered list with the
    current value marked, plus an optional free-text "custom" entry that
    the cline-2 picker calls ``CreateCustomModelRow``. We use a plain
    numbered prompt (rich.prompt.IntPrompt doesn't expose ``choices=``,
    so we show the table, then ask for the index).

    Returns:
      * the option value (string)
      * ``PICKER_KEEP`` if the user just presses Enter on the current one
      * ``PICKER_CUSTOM`` if a custom value was entered and ``allow_custom``
      * ``ACTION_CANCEL`` if the user types ``q`` or blank
    """
    table = Table(title=title, show_header=False, header_style="bold magenta")
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("value", style="white")
    table.add_column("description", style="grey50")

    # Sentinel option: keep the current value (Enter on it). Always
    # offered first so the user can abort a picker without a back-out
    # key. Same role as cline-2's "Cancel" entry on the picker.
    table.add_row(
        "0",
        f"[bold cyan]keep[/bold cyan] {current}",
        "[grey50]no change[/grey50]",
    )
    for i, (value, description) in enumerate(options, start=1):
        marker = "  [bold green](current)[/bold green]" if value == current else ""
        table.add_row(str(i), f"{value}{marker}", description)

    if allow_custom:
        table.add_row("c", f"[bold yellow]custom[/bold yellow] …", f"[grey50]{custom_hint}[/grey50]")

    table.add_row("q", "[bold red]cancel[/bold red]", "[grey50]leave picker without changes[/grey50]")

    console.print(table)
    while True:
        raw = Prompt.ask(
            "[bold]Pick one[/bold]",
            default="0",
            console=console,
            show_default=False,
        ).strip()
        if not raw or raw.lower() in ("q", "cancel"):
            return ACTION_CANCEL
        if raw == "0":
            return PICKER_KEEP
        if allow_custom and raw.lower() in ("c", "custom"):
            return PICKER_CUSTOM
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(options):
                return options[n - 1][0]
        console.print(f"[red]enter a number 0–{len(options) + (1 if allow_custom else 0)}, or q[/red]")


def pick_mode(console: Console, current: str) -> str:
    """Two-column compare picker that returns the new mode or a sentinel.

    Mirrors ``ModePickerContent`` in cline-2 (host vs proxy), with an
    ``auto`` row added on top so the operator sees the zero-setup option
    first. ``PICKER_KEEP`` means "leave config.yaml alone".
    """
    options: list[tuple[str, str]] = []
    for m in all_modes():
        d = describe_mode(m)
        options.append((m, d.summary))
    console.print()
    console.print(
        Panel(
            render_mode_compare(),
            title="[bold]Mode comparison[/bold]",
            border_style="cyan",
            expand=False,
        )
    )
    return _prompt_choice(
        console,
        title=f"Mode (current: {current})",
        options=options,
        current=current,
    )


def render_mode_compare() -> str:
    """Two-column compare block: ``render_mode_compare``-equivalent.

    Mirrors the cline-2 ``ModePickerContent`` two-pane render (label +
    summary at top, pros/cons underneath) but laid out top-to-bottom so
    it survives a 24-line terminal. Each column is a labelled block.
    """
    parts: list[str] = []
    for m in all_modes():
        d = describe_mode(m)
        parts.append(f"[bold]{d.label}[/bold]   ({m})")
        parts.append(f"  {d.summary}")
        for pro in d.pros:
            parts.append(f"    [green]+ {pro}[/green]")
        for con in d.cons:
            parts.append(f"    [red]- {con}[/red]")
        parts.append("")
    return "\n".join(parts).rstrip()


def pick_provider(console: Console, current: str) -> str:
    """Provider picker. ``PICKER_KEEP`` means no change."""
    options: list[tuple[str, str]] = []
    for p in all_providers():
        info = describe_provider(p)
        options.append((p, f"{info['keyless']}  (key: {info['env']})"))
    return _prompt_choice(
        console,
        title=f"Provider (current: {current})",
        options=options,
        current=current,
    )


def pick_model(console: Console, current: str, kind: str = "root") -> str:
    """Model picker -- curated list of Anthropic models + custom entry.

    Curated list tracks the constants in ``src/config.py`` so the
    picker's defaults stay in sync with what the engine accepts.
    Custom entry covers the openai/gemini/azure/portkey case where the
    model id is vendor-specific.
    """
    curated: list[tuple[str, str]] = [
        (MODEL_SONNET_5, "current default root (1M ctx)"),
        (MODEL_SONNET, "prior root (1M ctx)"),
        (MODEL_OPUS, "override for the hardest tasks (1M ctx)"),
        (MODEL_HAIKU, "cheap sub-LLM (200K ctx)"),
    ]
    return _prompt_choice(
        console,
        title=f"{kind} model (current: {current})",
        options=curated,
        current=current,
        allow_custom=True,
        custom_hint=f"type a model id (e.g. gpt-4o, gemini-2.5-pro) and press Enter",
    )


# -- Persistence -----------------------------------------------------------

def _save(
    config_path: Path,
    updates: dict[str, str],
    console: Console,
) -> bool:
    """Write ``updates`` to ``config.yaml`` and report what changed.

    Returns True if anything was written, so the caller can decide whether
    to show the "restart the server" reminder.
    """
    if not updates:
        return False
    changed = config_writer.write_scalars(config_path, updates)
    if changed:
        console.print(
            f"[green]wrote[/green] {', '.join(f'{k}={v!r}' for k, v in updates.items())} "
            f"to {config_path}"
        )
        console.print(
            "[yellow]note[/yellow]: a running MCP server keeps the old config until reconnect; "
            "restart it (or run [bold]claude mcp restart rlm[/bold]) to pick up the change."
        )
    else:
        console.print("[grey50]no change[/grey50]")
    return changed


# -- Slash command loop ---------------------------------------------------

SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help",            "show this list"),
    ("/status",          "show current mode / provider / model / cli login"),
    ("/mode-help",       "compare the three transport modes side-by-side"),
    ("/mode",            "open the mode picker (writes config.yaml)"),
    ("/provider",        "open the provider picker (writes config.yaml)"),
    ("/model",           "open the root-model picker (writes config.yaml)"),
    ("/override",        "open the override-model picker (writes config.yaml)"),
    ("/sub",             "open the sub-model picker (writes config.yaml)"),
    ("/test",            "run `uv run --extra dev pytest -q` (the verify gate)"),
    ("/test-config",     "run a focused pytest on config/auth/transport modules"),
    ("/auth-probe",      "send one tiny sub-model call to verify the auth path"),
    ("/quit",            "exit the TUI (also /exit)"),
]


HELP_TEXT = "\n".join(f"  [bold cyan]{cmd:14}[/bold cyan] {desc}" for cmd, desc in SLASH_COMMANDS)


def _run_pytest(console: Console, args: list[str]) -> int:
    """Invoke the project's pytest command and stream its output.

    Used by /test and /test-config. Honours the same `uv run --extra dev`
    invocation as the pre-push hook (CLAUDE.md) so a TUI run produces
    the same green/red as `git push`.
    """
    cmd = ["uv", "run", "--extra", "dev", "pytest", "-q", *args]
    console.print(f"[grey50]$ {' '.join(cmd)}[/grey50]")
    try:
        return subprocess.call(cmd, cwd=str(PKG_ROOT))
    except FileNotFoundError:
        console.print(
            "[red]`uv` not found on PATH[/red] -- pytest cannot run. "
            "Install uv (https://docs.astral.sh/uv/) or run pytest directly "
            "from .venv_sh."
        )
        return 127


def _run_auth_probe(console: Console, st: Status) -> None:
    """Send one tiny sub-model call to verify the auth path.

    Mirrors ``server._auth_probe_line`` -- same payload, same threshold,
    so /auth-probe and the MCP startup probe are interchangeable. Calls
    directly into ``subquery.sub_query`` so it doesn't have to start the
    MCP server.
    """
    from .subquery import sub_query
    from . import models

    target = models.select(_status_to_config(st), models.Role.SUB)
    try:
        res = sub_query("Reply with exactly: ok", target, max_tokens=16)
    except Exception as exc:                       # noqa: BLE001
        console.print(f"[red]auth probe FAILED[/red]: {type(exc).__name__}: {exc}")
        return
    if res.error:
        console.print(f"[red]auth probe FAILED[/red]: {res.error}")
        return
    console.print(f"[green]auth probe ok[/green]: sub-model replied {res.answer.strip()[:20]!r}")


def _status_to_config(st: Status):
    """Bridge: turn a TUI Status into the Config subquery/auth expect.

    ``subquery.sub_query`` and ``models.select`` take a Config, but the
    TUI only ever holds the on-disk scalar subset + the env overrides.
    We rebuild the minimum Config needed for one targeted call.
    """
    from .config import load_config
    return load_config()


def _dispatch(
    line: str,
    console: Console,
    config_path: Path,
) -> bool:
    """Run one slash command.

    Returns False when the user asked to quit, so the REPL can break out.
    """
    cmd = line.strip()
    if not cmd:
        return True
    if cmd in ("/quit", "/exit"):
        return False

    if cmd == "/help":
        console.print(Panel(HELP_TEXT, title="[bold]better-rlm TUI commands[/bold]", border_style="cyan"))
        return True

    if cmd == "/status":
        console.print(Panel(render_status(load_status(config_path)), title="[bold]current configuration[/bold]", border_style="cyan"))
        return True

    if cmd == "/mode-help":
        console.print(Panel(render_mode_compare(), title="[bold]mode comparison[/bold]", border_style="cyan"))
        return True

    if cmd in ("/mode",):
        st = load_status(config_path)
        choice = pick_mode(console, st.mode)
        if choice == PICKER_KEEP or choice == ACTION_CANCEL:
            console.print("[grey50]no change[/grey50]")
        elif choice in VALID_MODES:
            _save(config_path, {"mode": choice}, console)
        return True

    if cmd == "/provider":
        st = load_status(config_path)
        choice = pick_provider(console, st.provider)
        if choice == PICKER_KEEP or choice == ACTION_CANCEL:
            console.print("[grey50]no change[/grey50]")
        elif choice in all_providers():
            _save(config_path, {"provider": choice}, console)
        return True

    if cmd in ("/model", "/override", "/sub"):
        st = load_status(config_path)
        if cmd == "/model":
            current, key = st.root_model, "root_model"
            kind = "root"
        elif cmd == "/override":
            current, key = st.root_model_override, "root_model_override"
            kind = "override"
        else:
            current, key = st.sub_model, "sub_model"
            kind = "sub"
        choice = pick_model(console, current, kind=kind)
        if choice == PICKER_KEEP or choice == ACTION_CANCEL:
            console.print("[grey50]no change[/grey50]")
        elif choice == PICKER_CUSTOM:
            custom = Prompt.ask(f"[bold]{kind} model id[/bold]", console=console).strip()
            if custom:
                _save(config_path, {key: custom}, console)
            else:
                console.print("[grey50]empty value, no change[/grey50]")
        elif choice:
            _save(config_path, {key: choice}, console)
        return True

    if cmd == "/test":
        rc = _run_pytest(console, [])
        console.print(f"[grey50]pytest exited with rc={rc}[/grey50]")
        return True

    if cmd == "/test-config":
        rc = _run_pytest(console, ["tests/test_config.py", "tests/test_auth.py", "tests/test_transport.py"])
        console.print(f"[grey50]pytest exited with rc={rc}[/grey50]")
        return True

    if cmd == "/auth-probe":
        st = load_status(config_path)
        _run_auth_probe(console, st)
        return True

    console.print(f"[red]unknown command[/red]: {cmd!r}. Type [bold]/help[/bold] for the list.")
    return True


def run_repl(config_path: Path | None = None, console: Console | None = None) -> int:
    """Main REPL -- reads slash commands from stdin until /quit.

    Headless: every line is read with input(); non-interactive shells
    (CI, scripts) feed the lines and the loop exits on EOF or /quit.
    Interactive: a prompt is drawn for each line via rich.prompt.Prompt.
    """
    cfg_path = config_path or (PKG_ROOT / "config.yaml")
    console = console or Console()
    console.print(
        Panel(
            "[bold]better-rlm TUI[/bold]\n"
            "Configure the transport / provider / model and run the verify gate.\n"
            "Type [bold cyan]/help[/bold cyan] for commands, [bold cyan]/quit[/bold cyan] to exit.",
            border_style="green",
        )
    )
    while True:
        try:
            line = Prompt.ask("[bold green]rlm[/bold green]", console=console, default="").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[grey50]bye[/grey50]")
            return 0
        if not _dispatch(line, console, cfg_path):
            console.print("[grey50]bye[/grey50]")
            return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``python -m src.cli [--config PATH] [--one-shot CMD]``.

    ``--one-shot`` runs ONE slash command and exits. Used by tests and
    by anyone scripting the picker from CI. Without it, run_repl takes
    over stdin.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    config_path: Path | None = None
    one_shot: str | None = None
    while args:
        a = args.pop(0)
        if a in ("-h", "--help"):
            print(__doc__ or "")
            return 0
        if a == "--config" and args:
            config_path = Path(args.pop(0)).expanduser().resolve()
            continue
        if a == "--one-shot" and args:
            one_shot = args.pop(0)
            continue
        print(f"unknown flag: {a}", file=sys.stderr)
        return 2

    console = Console()
    cfg_path = config_path or (PKG_ROOT / "config.yaml")
    if one_shot is not None:
        return 0 if _dispatch(one_shot, console, cfg_path) else 0
    return run_repl(cfg_path, console=console)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
