# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""``python -m cli_recorder COMMAND``, run from the root of the project whose docs it builds."""

from __future__ import annotations

import argparse
import importlib.resources
import pathlib
import shutil
import sys

from ._config import load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cli_recorder",
        description="Record terminal sessions and write the CLI reference for a project's docs, as configured in "
        + "[tool.cli-recorder] in its pyproject.toml.",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    record = commands.add_parser("record", help="record the recordings, all but the live ones when none are named")
    record.add_argument("names", nargs="*", metavar="NAME", help="a recording, live or not")
    reference = commands.add_parser("reference", help="write the CLI reference page")
    reference.add_argument("--check", action="store_true", help="fail when the page isn't up to date, not writing it")
    assets = commands.add_parser("assets", help="copy the player's scripts and styles into the docs")
    assets.add_argument("directory", type=pathlib.Path, help="where to copy them, a directory under the docs")
    commands.add_parser("check-links", help="check every link between pages, anchors included, in the built site")
    args = parser.parse_args(argv)

    config = load()
    if args.command == "record":
        from ._record import record as run

        return run(config, args.names)
    if args.command == "reference":
        from ._reference import reference as run

        return run(config, args.check)
    if args.command == "assets":
        source = importlib.resources.files("cli_recorder") / "assets"
        with importlib.resources.as_file(source) as path:
            shutil.copytree(path, args.directory, dirs_exist_ok=True)
        return 0
    from ._links import check_links

    return check_links(config)


sys.exit(main())
