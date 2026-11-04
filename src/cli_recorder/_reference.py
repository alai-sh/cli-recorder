# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Write the CLI reference page from the program's argument parser and the recordings file.

Each command gets a section with its description, its recordings, its usage and a table of its options, so the page
says what ``<program> <command> --help`` does. The global options, when the parser has a group every command takes,
are listed once. With ``check``, the page isn't written, and a page that isn't up to date is an error.
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import re
import sys
import tomllib

from ._config import Config


def reference(config: Config, check: bool) -> int:
    if config.parser is None:
        _ = sys.stderr.write("[tool.cli-recorder] in pyproject.toml has no parser to write the reference from\n")
        return 1
    page_path, recordings_path = config.page, config.recordings
    scenarios = tomllib.loads(recordings_path.read_text(encoding="utf-8"))
    by_name: dict[str, dict[str, str]] = {r["name"]: r for r in scenarios.get("recording", [])}
    # Per command, the recordings shown under it, each with the description to show there, if any.
    reference: dict[str, list[dict[str, str]]] = scenarios.get("reference", {})
    problems = [
        f"[reference] '{command}' names no recording called '{entry['recording']}'"
        for command, entries in reference.items()
        for entry in entries
        if entry["recording"] not in by_name
    ]
    # Every recording is shown somewhere: in the reference, or linked from a page, so none is recorded for nothing.
    # The reference itself is left out: it's written from [reference], so it would count a stale entry as used.
    pages = [page for page in config.docs.rglob("*.md") if page != page_path]
    linked = {m for page in pages for m in re.findall(r"casts/([\w-]+)\.cast", page.read_text(encoding="utf-8"))}
    shown = {entry["recording"] for entries in reference.values() for entry in entries}
    problems += [
        f"recording '{name}' isn't in [reference] or linked from any page"
        for name in by_name
        if name not in shown | linked
    ]
    # A recording with schemes is only shown in the page's scheme when its link says it has them.
    problems += [
        f"{page}: the link to recording '{name}' needs the cli-cast-schemes class, as it has schemes"
        for page in pages
        for name, attributes in re.findall(r"casts/([\w-]+)\.cast\)\{([^}]*)\}", page.read_text(encoding="utf-8"))
        if by_name.get(name, {}).get("schemes") and ".cli-cast-schemes" not in attributes.split()
    ]
    recordings = {
        command: [
            {**by_name[entry["recording"]], "description": entry.get("description", "")}
            for entry in entries
            if entry["recording"] in by_name
        ]
        for command, entries in reference.items()
    }

    module, _, function = config.parser.partition(":")
    parser: argparse.ArgumentParser = getattr(importlib.import_module(module), function)()
    root = [parser.prog]
    commands = [(root, parser), *_commands(parser, root)]
    for _, command in commands:
        # From 3.14 argparse colours help on a terminal; the page is the same wherever it's written.
        command.color = False
    group = next((g for g in parser._action_groups if g.title == config.global_options), None)
    sections = [config.header, _command(root, parser, [], group)]
    if group is not None:
        sections.append(_global_options(parser, group))
    sections += [
        _command(names, command, recordings.get(" ".join(names[1:]), []), group) for names, command in commands[1:]
    ]
    known = {" ".join(names[1:]) for names, _ in commands}
    problems += [f"[reference] '{command}' isn't a command" for command in reference if command not in known]
    if problems:
        _ = sys.stderr.write("".join(f"{recordings_path}: {problem}\n" for problem in problems))
        return 1
    page = "\n".join(sections)

    current = page_path.read_text(encoding="utf-8") if page_path.is_file() else ""
    if not check:
        page_path.parent.mkdir(parents=True, exist_ok=True)
        _ = page_path.write_text(page, encoding="utf-8")
        return 0
    if current == page:
        return 0
    diff = difflib.unified_diff(current.splitlines(True), page.splitlines(True), str(page_path), "generated")
    _ = sys.stderr.write("".join(diff))
    _ = sys.stderr.write(f"\n{page_path} isn't up to date: run python -m cli_recorder reference\n")
    return 1


def _commands(parser: argparse.ArgumentParser, names: list[str]) -> list[tuple[list[str], argparse.ArgumentParser]]:
    """Every command under ``parser``, depth first, in the order they're registered."""
    found: list[tuple[list[str], argparse.ArgumentParser]] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, command in action.choices.items():  # pyright: ignore[reportUnknownVariableType]
                found.append(([*names, name], command))
                found += _commands(command, [*names, name])
    return found


def _global_options(parser: argparse.ArgumentParser, group: argparse._ArgumentGroup) -> str:
    return "## Global options\n\nEvery command takes these, before or after its name.\n\n" + _table(
        parser, group._group_actions
    )


def _command(
    names: list[str],
    parser: argparse.ArgumentParser,
    recordings: list[dict[str, str]],
    group: argparse._ArgumentGroup | None,
) -> str:
    lines = [f"## {' '.join(names)}", "", _prose(parser.description or ""), ""]
    for recording in recordings:
        classes = ".cli-cast .cli-cast-schemes" if recording.get("schemes") else ".cli-cast"
        lines += [f"[{recording['title']}](casts/{recording['name']}.cast){{ {classes} }}", ""]
        # cast.js puts the paragraphs describing it between its title and the player; without JavaScript they follow
        # the link.
        for paragraph in re.split(r"\n\s*\n", recording.get("description", "").strip()):
            if paragraph:
                lines += [" ".join(paragraph.split()), "{: .cli-cast-description }", ""]

    formatter = parser._get_formatter()
    positionals = [a for a in parser._actions if not a.option_strings and a.help != argparse.SUPPRESS]
    usage = [*names, "[OPTIONS]"]
    for action in positionals:
        if isinstance(action, argparse._SubParsersAction):
            usage.append("<COMMAND>")
        else:
            usage.append(formatter._format_args(action, formatter._get_default_metavar_for_positional(action)))
    lines += ["```console", f"$ {' '.join(usage)}", "```", ""]

    for action in positionals:
        if isinstance(action, argparse._SubParsersAction):
            lines += ["| Command | Description |", "|---|---|"]
            for sub in action._choices_actions:
                anchor = "-".join([*names, sub.dest])
                lines.append(f"| [`{sub.dest}`](#{anchor}) | {_prose(sub.help or '')} |")
            lines.append("")
    arguments = [a for a in positionals if not isinstance(a, argparse._SubParsersAction)]
    if arguments:
        lines += ["### Arguments", "", _table(parser, arguments, "Argument")]

    for options in parser._action_groups:
        actions = [a for a in options._group_actions if a.option_strings and a.help != argparse.SUPPRESS]
        actions = [a for a in actions if not isinstance(a, argparse._HelpAction)]
        if (group is not None and options.title == group.title) or not actions:
            continue
        lines += [f"### {(options.title or '').capitalize()}", "", _table(parser, actions)]
    if group is not None and len(names) > 1:
        lines.append("Also takes the [global options](#global-options).\n")
    return "\n".join(lines)


def _table(parser: argparse.ArgumentParser, actions: list[argparse.Action], heading: str = "Option") -> str:
    formatter = parser._get_formatter()
    rows = [f"| {heading} | Description |", "|---|---|"]
    for action in actions:
        invocation = formatter._format_action_invocation(action)
        if action.option_strings:
            # "-P, --upgrade-collection NAME" as `-P`, `--upgrade-collection NAME`
            invocation = ", ".join(f"`{part}`" for part in invocation.split(", "))
        else:
            invocation = f"`{invocation}`"
        description = _prose(formatter._expand_help(action))
        # argparse's usage shows these as [--locked | --frozen]; the page has no full usage, so the table says it.
        others = [
            f"`{max(other.option_strings, key=len)}`"
            for group in parser._mutually_exclusive_groups
            if action in group._group_actions
            for other in group._group_actions
            if other is not action
        ]
        if others:
            description += f". Can't be used with {' or '.join(others)}"
        rows.append(f"| {invocation} | {description} |")
    return "\n".join(rows) + "\n"


def _prose(text: str) -> str:
    """Help text as a sentence for the page: a capital letter, and options and variables as code."""
    text = re.sub(r"(?<![\w`-])(--?[a-zA-Z][\w-]*(?: [A-Z]+\b| [\w.-]+$)?|[A-Z][A-Z0-9]*_[A-Z0-9_]+)", r"`\1`", text)
    text = text.replace("|", "\\|")
    return text[:1].upper() + text[1:]
