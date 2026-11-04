# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Record the terminal recordings in the docs as asciinema files, ``<casts>/<name>.cast``.

Each ``[[recording]]`` in the recordings file runs in a new home directory, in a pseudo-terminal, so the program
colours its output and draws its progress as it would for a user. The commands are typed out at a steady pace, then
run, keeping the time each one really takes. The README describes the recordings file.

The project's fixture, a Python file, can define two hooks. ``environment(root, scenarios)`` is a context manager
that's entered once around every recording but the live ones, such as to start a local server. It's given a
temporary directory and the parsed recordings file, and yields environment variables for the recordings.
``prepare(recording, directory)`` is called for each recording after its ``files`` are written, for anything else it
starts with, such as built artifacts.

A recording with ``schemes`` is recorded twice, for a page's dark and light colour schemes, as ``<name>.cast`` and
``<name>-light.cast``. Each sets ``COLORFGBG`` as rxvt does, so a program that picks its colours from the
terminal's background, as a TUI drawing its own background does, draws in each. cast.js shows the one matching the
page.

A ``live`` recording runs without the fixture's environment, so it's only recorded when named. It's written to
``<live-casts>/<name>.cast``, to be committed, with an animated SVG of it in GitHub's light and dark palettes for a
README, ``<name>-light.svg`` and ``<name>-dark.svg``, made by asg (``cargo install asg --locked``).
"""

from __future__ import annotations

import codecs
import collections.abc as _c
import concurrent.futures
import contextlib
import fcntl
import importlib.util
import json
import os
import pathlib
import pty
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tomllib
import types

from ._config import Config

COLUMNS = 80
"""The terminal's width while recording, unless a recording sets ``columns``. The player fits the recording to the
page's width, so fewer columns means larger text."""
ROWS = 48
"""The terminal's size while recording. The file's height is cut to the rows the recording uses. A recording that
needs more fails, as its first lines would scroll away before it ends. A recording that sets ``rows``, for a
full-screen program, is recorded and played at that height instead."""

PROMPT = "\x1b[1;34m{cwd}\x1b[0m \x1b[1;32m$\x1b[0m "
"""The shell's prompt, the working directory with the home directory as ~, as most shells show it."""
TYPING = 0.04
"""Seconds between each character typed."""
BEFORE_ENTER = 0.5
AFTER_OUTPUT = 1.5
"""Seconds to hold on each command's output before typing the next."""
IDLE_TIME_LIMIT = 1
"""The longest pause the player shows in a recording, in seconds. A live recording has none, as how long its commands
take is what it shows."""
TIMEOUT = 60
"""Seconds a command can run before it's stopped and the recording fails, unless the recording sets ``timeout``."""
KEY_PAUSE = 0.5
"""Seconds between each of a recording's ``keys``, after the program first writes something."""
KEYS_GRACE = 5.0
"""Seconds a program can keep running after its last key before it's stopped and the recording fails."""
KEYS = {
    "enter": b"\r",
    "tab": b"\t",
    "space": b" ",
    "escape": b"\x1b",
    "backspace": b"\x7f",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
}
"""The keys a recording's ``keys`` can name, as xterm sends them. Any other string is typed as it's written."""
SCHEMES = {"": {"COLORFGBG": "15;0"}, "-light": {"COLORFGBG": "0;15"}}
"""What each of a ``schemes`` recording's files is recorded with, by the suffix on its name: white text on black,
then black text on white."""
LIVE_HOLD = 5.0
"""Seconds a live recording holds on how it ends, so its SVG can be read before it starts over."""


def record(config: Config, names: list[str]) -> int:
    scenarios = tomllib.loads(config.recordings.read_text(encoding="utf-8"))
    recordings: list[dict[str, object]] = scenarios.get("recording", [])
    unknown = set(names) - {r["name"] for r in recordings}
    if unknown:
        _ = sys.stderr.write(f"no recording named {', '.join(sorted(unknown))} in {config.recordings}\n")
        return 1
    if config.program is None:
        _ = sys.stderr.write("[tool.cli-recorder] in pyproject.toml has no program to record\n")
        return 1
    program = shutil.which(config.program)
    if program is None:
        _ = sys.stderr.write(f"{config.program} isn't installed in this environment\n")
        return 1
    # A live recording takes time and maybe the network, and is committed, so it's only made when asked for by name.
    selected = [r for r in recordings if r["name"] in names or (not names and not r.get("live"))]
    if any(r.get("live") for r in selected) and shutil.which("asg") is None:
        _ = sys.stderr.write("a live recording needs asg to draw its SVGs: cargo install asg --locked\n")
        return 1
    if not selected:
        return 0

    fixture = _fixture(config.fixture)
    environment = getattr(fixture, "environment", None)
    config.casts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cli-recorder-") as temp:
        context: contextlib.AbstractContextManager[_c.Mapping[str, str]] = (
            environment(pathlib.Path(temp), scenarios) if environment else contextlib.nullcontext({})
        )
        with context as extra:
            # Every recording has its own home, so they run at once. Most of each is spent waiting on the program.
            # They're written as they finish.
            bin_dir = pathlib.Path(program).parent
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected)) as pool:
                jobs = [pool.submit(_write, config, r, fixture, extra, bin_dir) for r in selected]
                for job in concurrent.futures.as_completed(jobs):
                    _ = sys.stderr.write(f"recorded {job.result()}\n")
    return 0


def _fixture(path: pathlib.Path | None) -> types.ModuleType | None:
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("cli_recorder_fixture", path)
    if spec is None or spec.loader is None:
        msg = f"{path} can't be loaded as a Python file"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(
    config: Config,
    recording: dict[str, object],
    fixture: types.ModuleType | None,
    extra: _c.Mapping[str, str],
    bin_dir: pathlib.Path,
) -> str:
    """Record one recording into its file, or a file for each colour scheme, returning its name."""
    live = bool(recording.get("live"))
    if live and recording.get("schemes"):
        # A live recording's SVGs already come in light and dark, from asg's palettes.
        msg = f"{recording['name']}: schemes can't be used with live"
        raise TypeError(msg)
    schemes: dict[str, dict[str, str]] = SCHEMES if recording.get("schemes") else {"": {}}
    for suffix, scheme in schemes.items():
        home = pathlib.Path(tempfile.mkdtemp(prefix="cli-recorder-home-")).resolve()
        try:
            env = _environment(home, {} if live else extra, bin_dir)
            # The recording's own env comes last, so it can still say otherwise.
            env.update(scheme)
            env.update({k: str(v) for k, v in _as_dict(recording.get("env")).items()})
            cast = _record(recording, home, env, getattr(fixture, "prepare", None))
        finally:
            shutil.rmtree(home)
        path = (config.live_casts if live else config.casts) / f"{recording['name']}{suffix}.cast"
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(cast, encoding="utf-8")
        if live:
            for theme in ("light", "dark"):
                svg = path.with_name(f"{path.stem}-{theme}.svg")
                args = ["asg", "--theme", f"github-{theme}", "--padding", "16", path, svg]
                _ = subprocess.run(args, check=True)  # noqa: S603 - asg from PATH, checked in record, on our own files
    return str(recording["name"])


def _environment(home: pathlib.Path, extra: _c.Mapping[str, str], bin_dir: pathlib.Path) -> dict[str, str]:
    """A clean environment with nothing from the user's, an empty home and config, and the fixture's variables."""
    return {
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "HOME": str(home),
        "XDG_CONFIG_DIRS": str(home / ".xdg"),
        "LANG": "C.UTF-8",
        "TERM": "xterm-256color",
        **extra,
    }


def _record(
    recording: dict[str, object],
    home: pathlib.Path,
    env: dict[str, str],
    prepare: _c.Callable[[dict[str, object], pathlib.Path], None] | None,
) -> str:
    columns = recording.get("columns", COLUMNS)
    if not isinstance(columns, int):
        msg = f"{recording['name']}: columns must be an integer"
        raise TypeError(msg)
    timeout = recording.get("timeout", TIMEOUT)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        msg = f"{recording['name']}: timeout must be a number of seconds"
        raise TypeError(msg)
    keys = _keys(recording)
    fixed = recording.get("rows")
    if fixed is not None and (not isinstance(fixed, int) or not 0 < fixed <= ROWS or "panes" in recording):
        msg = f"{recording['name']}: rows must be an integer from 1 to {ROWS}, and can't be used with panes"
        raise TypeError(msg)
    project = home / str(recording.get("directory", "project"))
    project.mkdir(exist_ok=True)
    for path, content in _as_dict(recording.get("files")).items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(str(content).lstrip("\n"), encoding="utf-8")
    if prepare is not None:
        prepare(recording, project)
    for command in _as_list(recording.get("setup")):
        result = subprocess.run(  # noqa: S602 - the commands are the recording's own
            command, shell=True, cwd=project, env=env, capture_output=True, text=True, check=False
        )
        if result.returncode:
            msg = f"{recording['name']}: setup command {command!r} failed:\n{result.stdout}{result.stderr}"
            raise RuntimeError(msg)

    if "panes" in recording:
        events, rows = _record_panes(recording, project, home, env, columns)
    else:
        events: list[tuple[float, str]] = []
        clock = pause = 0.0
        cwd = project
        commands = _as_list(recording.get("commands"))
        for i, command in enumerate(commands):
            events.append((clock, _prompt(cwd, home)))
            clock += pause
            for char in command:
                clock += TYPING
                events.append((clock, char))
            clock += BEFORE_ENTER
            events.append((clock, "\r\n"))
            last = i == len(commands) - 1
            cwd, output = _command(
                recording, command, cwd, home, env, columns, fixed or ROWS, timeout, keys if last else []
            )
            for offset, data in output or []:
                events.append((clock + offset, data))
            # The prompt shows as soon as the command finishes, and the next is typed after time to read its output,
            # or at once after a cd, which prints nothing.
            clock = events[-1][0]
            pause = 0.0 if output is None else AFTER_OUTPUT
        events += [(clock, _prompt(cwd, home)), (clock + pause, "")]
        # A full-screen program draws anywhere on the screen, so only the rows it was given say how tall it is.
        rows = fixed or _rows_used(_output_text(events), columns)

    if rows > ROWS:
        # The output, without escapes, says why: a warning or traceback only one machine prints is otherwise lost.
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r", "", _output_text(events))
        msg = (
            f"{recording['name']}: needs {rows} rows, more than the {ROWS} recorded; shorten it or raise ROWS:\n{text}"
        )
        raise RuntimeError(msg)
    # asciicast v3, which asg needs for a live recording's SVGs: each event's time is from the one before it.
    header: dict[str, object] = {
        "version": 3,
        "term": {"cols": columns, "rows": rows, "type": env["TERM"]},
        "title": recording["title"],
        "env": {"SHELL": "/bin/sh"},
    }
    if recording.get("live"):
        # Held from the last thing drawn, in place of the usual hold on output.
        last = max(t for t, data in events if data)
        events = [(t, data) for t, data in events if data] + [(last + LIVE_HOLD, "")]
    elif fixed is None:
        # A full-screen program's pauses are its script's own, so they're kept as they are.
        header["idle_time_limit"] = IDLE_TIME_LIMIT
    lines = [json.dumps(header)]
    # Rounded from the start rather than each interval, so the rounding doesn't add up over the recording.
    previous = 0.0
    for t, data in events:
        kind = "i" if isinstance(data, _Key) else "o"
        lines.append(json.dumps([round(round(t, 3) - previous, 3), kind, data], ensure_ascii=False))
        previous = round(t, 3)
    return "\n".join(lines) + "\n"


def _command(
    recording: dict[str, object],
    command: str,
    cwd: pathlib.Path,
    home: pathlib.Path,
    env: dict[str, str],
    columns: int,
    rows: int = ROWS,
    timeout: float = TIMEOUT,
    keys: _c.Sequence[tuple[float, bytes]] = (),
) -> tuple[pathlib.Path, list[tuple[float, str]] | None]:
    """Run one of a recording's commands in ``cwd``, pressing ``keys`` once it starts writing, returning the
    directory the next runs in and what it wrote and when, or None for a ``cd``."""
    args = shlex.split(command)
    if args[0] == "cd":
        # Each command runs in a shell of its own, so a cd is followed here and shown by the next prompt.
        cwd = (cwd / args[1]).resolve()
        if not cwd.is_dir() or not cwd.is_relative_to(home):
            msg = f"{recording['name']}: {command!r} isn't a directory in the recording's home"
            raise RuntimeError(msg)
        return cwd, None
    try:
        output, status = _run(command, cwd, env, columns, rows, timeout, keys)
    except _Stopped as e:
        text = re.sub(r"\x1b\[[0-9;?<>=$]*[A-Za-z~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r", "", e.text)
        msg = f"{recording['name']}: {command!r} {e.reason}, so it was stopped. It wrote:\n{text[-4000:]}"
        raise RuntimeError(msg) from None
    if status == 127:  # the shell couldn't find the command, rather than it failing on purpose
        msg = f"{recording['name']}: {command!r} wasn't found:\n{''.join(data for _, data in output)}"
        raise RuntimeError(msg)
    # The home directory is a temporary one, shown as ~ as in the prompt; ansible-galaxy prints its paths.
    return cwd, [(offset, data if isinstance(data, _Key) else data.replace(str(home), "~")) for offset, data in output]


def _record_panes(
    recording: dict[str, object], project: pathlib.Path, home: pathlib.Path, env: dict[str, str], columns: int
) -> tuple[list[tuple[float, str]], int]:
    """Record a split screen: each pane's commands in real time, below a rule with its title and a timer of how long its
    commands have been running, played as if the panes ran at once. Returns the recording's events and its rows."""
    panes = _as_dicts(recording["panes"])
    # Every pane's first command is entered at the same moment, however long it takes to type.
    enter = 0.5 + max(len(_as_list(pane.get("commands"))[0]) * TYPING for pane in panes) + BEFORE_ENTER
    # One after the other, each timed from its own start, so one pane's downloads don't slow another's.
    recorded = [_pane(recording, pane, project, home, env, columns, enter) for pane in panes]

    # Each pane is as tall as its output reaches, drawn on a screen too tall to scroll to find out.
    heights: list[int] = []
    for pane_events, _ in recorded:
        screen = _Screen(columns, 1000)
        for _, data in pane_events:
            screen.write(data)
        heights.append(screen.deepest + 1)
    tops = [sum(h + 1 for h in heights[:i]) for i in range(len(panes))]
    screens = [_Screen(columns, h) for h in heights]

    end = max(pane_events[-1][0] for pane_events, _ in recorded)
    timeline: list[tuple[float, int, str]] = [(t, i, data) for i, (pe, _) in enumerate(recorded) for t, data in pe]
    # Ticks redraw the bars: every tenth of a second, and as each command finishes, so a pane's timer stops on its
    # real total.
    ticks = {round(i / 10, 1) for i in range(int(end * 10) + 1)} | {e for _, busy in recorded for _, e in busy}
    timeline += [(t, -1, "") for t in ticks]
    timeline.sort(key=lambda entry: entry[0])

    def bars(t: float) -> list[str]:
        drawn: list[str] = []
        for i, (pane, (_, busy)) in enumerate(zip(panes, recorded, strict=True)):
            running = sum(min(e, t) - s for s, e in busy if s < t)
            done = bool(busy) and t >= busy[-1][1]
            title = str(pane["title"])
            timer = f"{'✓ ' if done else ''}{running:.1f} s"
            # A dim rule with the title in blue and the timer, green once the pane's commands have all finished:
            # coloured text rather than a background, as each palette picks its colours to read on its own.
            rule = "─" * (columns - len(title) - len(timer) - 8)
            drawn.append(
                f"\x1b[{tops[i] + 1};1H\x1b[0;2m── \x1b[0;1;34m{title}\x1b[0;2m {rule} "
                + f"\x1b[0;1{';32' if done else ''}m{timer}\x1b[0;2m ──\x1b[0m"
            )
        return drawn

    shown = bars(0.0)
    events: list[tuple[float, str]] = [(0.0, "\x1b[?25l\x1b[2J" + "".join(shown))]
    for t, i, data in timeline:
        if i < 0:
            now = bars(t)
            changed = "".join(bar for bar, was in zip(now, shown, strict=True) if bar != was)
            shown = now
        else:
            screens[i].write(data)
            changed = "".join(
                f"\x1b[{tops[i] + row + 2};1H\x1b[2K{screens[i].line(row)}" for row in sorted(screens[i].dirty)
            )
            screens[i].dirty.clear()
        if changed:
            events.append((t, changed))
    # Held on the last prompts, as a pane's last event changes nothing on screen.
    events.append((end, ""))
    return events, sum(h + 1 for h in heights)


def _pane(
    recording: dict[str, object],
    pane: dict[str, object],
    project: pathlib.Path,
    home: pathlib.Path,
    env: dict[str, str],
    columns: int,
    enter: float,
) -> tuple[list[tuple[float, str]], list[tuple[float, float]]]:
    """Type and run a pane's commands as the clock goes, returning what it showed and when, and when each of its
    commands, other than a cd, started and finished, all in seconds from when it started, its first command entered
    ``enter`` seconds in."""
    start = time.monotonic()

    def now() -> float:
        return time.monotonic() - start

    events: list[tuple[float, str]] = []
    busy: list[tuple[float, float]] = []
    cwd = (project / str(pane.get("directory", "."))).resolve()
    pause = 0.0
    for i, command in enumerate(_as_list(pane.get("commands"))):
        events.append((now(), _prompt(cwd, home)))
        time.sleep(max(0.0, enter - len(command) * TYPING - BEFORE_ENTER - now()) if i == 0 else pause)
        for char in command:
            time.sleep(TYPING)
            events.append((now(), char))
        time.sleep(BEFORE_ENTER)
        events.append((now(), "\r\n"))
        began = now()
        cwd, output = _command(recording, command, cwd, home, env, columns, timeout=_timeout(recording))
        if output is not None:
            busy.append((began, now()))
            events += [(began + offset, data) for offset, data in output]
        # As in a recording of one terminal: the prompt at once, then time to read the output before the next.
        pause = 0.0 if output is None else AFTER_OUTPUT
    events += [(now(), _prompt(cwd, home)), (now() + pause, "")]
    return events, busy


def _prompt(cwd: pathlib.Path, home: pathlib.Path) -> str:
    relative = cwd.relative_to(home) if cwd.is_relative_to(home) else None
    shown = str(cwd) if relative is None else "~" if relative == pathlib.Path() else f"~/{relative}"
    return PROMPT.format(cwd=shown)


class _Key(str):
    """A key pressed, written to the recording as an input event rather than output."""

    __slots__ = ()


def _output_text(events: _c.Iterable[tuple[float, str]]) -> str:
    """What the events wrote, without the keys pressed."""
    return "".join(data for _, data in events if not isinstance(data, _Key))


class _Stopped(Exception):
    """A command that was stopped, with why and what it wrote."""

    def __init__(self, reason: str, text: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.text = text


def _keys(recording: dict[str, object]) -> list[tuple[float, bytes]]:
    """A recording's ``keys``, each with the seconds to wait before it. A number is a pause, a name in ``KEYS`` is
    that key, ``{ click = [column, row] }`` clicks there, counting from 1, and any other string is typed."""
    value = recording.get("keys", [])
    if not isinstance(value, list):
        msg = f"{recording['name']}: keys must be a list"
        raise TypeError(msg)
    out: list[tuple[float, bytes]] = []
    pause = 0.0
    for entry in value:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            pause += entry
        elif isinstance(entry, str) and entry:
            if entry in KEYS:
                out.append((KEY_PAUSE + pause, KEYS[entry]))
            else:
                out += [(KEY_PAUSE + pause if i == 0 else TYPING, c.encode()) for i, c in enumerate(entry)]
            pause = 0.0
        elif (cell := _click(entry)) is not None:
            # A press and release of the left button, as xterm reports them in SGR mode.
            out.append((KEY_PAUSE + pause, "\x1b[<0;{0};{1}M\x1b[<0;{0};{1}m".format(*cell).encode()))
            pause = 0.0
        else:
            msg = (
                f"{recording['name']}: each of keys must be a key, text to type, a number of seconds or "
                + "{ click = [column, row] }"
            )
            raise TypeError(msg)
    return out


def _click(entry: object) -> tuple[int, int] | None:
    """The column and row of a ``{ click = [column, row] }`` entry, or None for anything else."""
    match _as_dict(entry).get("click"):
        case [int(column), int(row)] if column > 0 and row > 0 and not isinstance(column, bool):
            return column, row
        case _:
            return None


def _timeout(recording: dict[str, object]) -> float:
    timeout = recording.get("timeout", TIMEOUT)
    return float(timeout) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) else TIMEOUT


def _run(
    command: str,
    cwd: pathlib.Path,
    env: dict[str, str],
    columns: int,
    rows: int = ROWS,
    timeout: float = TIMEOUT,
    keys: _c.Sequence[tuple[float, bytes]] = (),
) -> tuple[list[tuple[float, str]], int]:
    """Run a command in a pseudo-terminal, returning what it wrote and when, in seconds from its start, and its exit
    status. ``keys`` are written to it from when it first writes something, each after its pause. It's stopped, and
    _Stopped raised, when it runs past ``timeout`` or past ``KEYS_GRACE`` after its last key."""
    controller, terminal = pty.openpty()
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
    start = time.monotonic()
    process = subprocess.Popen(  # noqa: S602 - the commands are the recording's own
        command,
        shell=True,
        cwd=cwd,
        env=env,
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        start_new_session=True,
    )
    os.close(terminal)
    decoder = codecs.getincrementaldecoder("utf-8")()
    output: list[tuple[float, str]] = []
    pending = list(keys)
    deadline = start + timeout
    reason = f"ran for longer than {timeout:g} seconds"
    next_key: float | None = None
    try:
        while True:
            now = time.monotonic()
            if now >= deadline and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                raise _Stopped(reason, _output_text(output))
            if next_key is not None and now >= next_key:
                key = pending.pop(0)[1]
                os.write(controller, key)
                # In the recording as an input event, which the player shows over the screen.
                output.append((now - start, _Key(key.decode())))
                next_key = now + pending[0][0] if pending else None
                if not pending and now + KEYS_GRACE < deadline:
                    deadline = now + KEYS_GRACE
                    reason = f"was still running {KEYS_GRACE:g} seconds after its last key"
            wait = min(0.05, max(0.0, (next_key or deadline) - now))
            ready, _, _ = select.select([controller], [], [], wait)
            if not ready:
                if process.poll() is not None:
                    break
                continue
            try:
                data = os.read(controller, 65536)
            except OSError:  # EIO: every process holding the terminal has exited
                break
            if not data:
                break
            if text := decoder.decode(data):
                output.append((time.monotonic() - start, text))
                if pending and next_key is None and len(pending) == len(keys):
                    next_key = time.monotonic() + pending[0][0]
    finally:
        os.close(controller)
        status = process.wait()
    return output, status


_TERMINAL_RE = re.compile(
    r"\x1b\[([0-9;?]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.|\r|\n|\x08|[^\x1b\r\n\x08]"
)


class _Screen:
    """A pane of a split screen, drawn as a terminal draws what's written to it: text, colours, carriage returns,
    newlines, backspaces, and moving the cursor and clearing lines as progress redraws. Enough for most
    progress output. Anything else, links (OSC 8) included, is dropped. Past its last row it scrolls."""

    def __init__(self, columns: int, rows: int) -> None:
        self.columns = columns
        self.rows = rows
        self.cells = [self._blank() for _ in range(rows)]
        self.row = self.column = self.deepest = 0
        self.style = ""
        self.dirty: set[int] = set()

    def _blank(self) -> list[tuple[str, str]]:
        return [(" ", "")] * self.columns

    def write(self, text: str) -> None:
        for match in _TERMINAL_RE.finditer(text):
            char, params, final = match.group(), match.group(1) or "", match.group(2)
            if final and not params.startswith("?"):
                count = int(params) if params.isdigit() else 1
                if final == "m":
                    # Each cell keeps the SGR parameters in effect, in order, from the last reset.
                    reset = params in ("", "0") or params.startswith("0;")
                    params = "" if params in ("", "0") else params.removeprefix("0;")
                    self.style = params if reset or not self.style else f"{self.style};{params}"
                elif final == "A":
                    self.row = max(0, self.row - count)
                elif final == "B":
                    self.row = min(self.rows - 1, self.row + count)
                elif final == "C":
                    self.column = min(self.columns - 1, self.column + count)
                elif final == "D":
                    self.column = max(0, self.column - count)
                elif final == "G":
                    self.column = min(self.columns - 1, count - 1)
                elif final == "K":
                    column = min(self.column, self.columns)
                    line = self.cells[self.row]
                    if params in ("", "0"):
                        line[column:] = [(" ", "")] * (self.columns - column)
                    elif params == "1":
                        line[: column + 1] = [(" ", "")] * (column + 1)
                    else:
                        line[:] = self._blank()
                    self.dirty.add(self.row)
            elif char == "\r":
                self.column = 0
            elif char == "\n":
                self._line_feed()
            elif char == "\b":
                self.column = max(0, min(self.column, self.columns) - 1)
            elif not char.startswith("\x1b") and ord(char) >= 32:
                # As xterm does, a line that fills the width only wraps when another character follows.
                if self.column == self.columns:
                    self.column = 0
                    self._line_feed()
                self.cells[self.row][self.column] = (char, self.style)
                self.column += 1
                self.dirty.add(self.row)

    def _line_feed(self) -> None:
        if self.row == self.rows - 1:
            _ = self.cells.pop(0)
            self.cells.append(self._blank())
            self.dirty.update(range(self.rows))
        else:
            self.row += 1
        self.deepest = max(self.deepest, self.row)

    def line(self, row: int) -> str:
        """The row as text, with SGR sequences where its colours change, and no blank cells at its end."""
        cells = self.cells[row]
        while cells and cells[-1] == (" ", ""):
            cells = cells[:-1]
        drawn: list[str] = []
        style = ""
        for char, cell in cells:
            if cell != style:
                drawn.append("\x1b[0m" + (f"\x1b[{cell}m" if cell else ""))
                style = cell
            drawn.append(char)
        return "".join(drawn) + ("\x1b[0m" if style else "")


def _rows_used(output: str, columns: int) -> int:
    """The rows the output reaches as the terminal draws it: following newlines, lines wider than ``columns`` wrapping
    onto the next row, the cursor moving up as progress redraws, and back over a backspace. Escape sequences, links
    (OSC 8) included, take no room."""
    row = column = deepest = 0
    for match in _TERMINAL_RE.finditer(output):
        text = match.group()
        if match.group(2) == "A":
            row = max(0, row - int(match.group(1) or 1))
        elif text == "\r":
            column = 0
        elif text == "\b":  # as ansible-galaxy's spinner draws each character over the last
            column = max(0, column - 1)
        elif text == "\n":
            row, column = row + 1, 0
        elif not text.startswith("\x1b"):
            # As xterm does, a line that fills the width only wraps when another character follows.
            if column == columns:
                row, column = row + 1, 0
            column += 1
        deepest = max(deepest, row)
    return deepest + 1


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def _as_dicts(value: object) -> list[dict[str, object]]:
    return [_as_dict(item) for item in value] if isinstance(value, list) else []  # pyright: ignore[reportUnknownVariableType]


def _as_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []  # pyright: ignore[reportUnknownVariableType]
