# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""The project's settings, from ``[tool.cli-recorder]`` in its ``pyproject.toml``."""

from __future__ import annotations

import dataclasses
import pathlib
import tomllib


@dataclasses.dataclass(frozen=True)
class Config:
    program: str | None
    """The command the recordings run. Its directory is the only one on PATH besides the system's."""
    parser: str | None
    """The argument parser the reference is written from, as ``module:function``."""
    header: str
    """The reference page's front matter and introduction."""
    global_options: str | None
    """The title of the argparse group every command takes, listed once in the reference."""
    fixture: pathlib.Path | None
    """A Python file with ``environment`` and ``prepare`` hooks for the recordings."""
    recordings: pathlib.Path
    casts: pathlib.Path
    live_casts: pathlib.Path
    page: pathlib.Path
    docs: pathlib.Path
    site: pathlib.Path


def load() -> Config:
    path = pathlib.Path("pyproject.toml")
    settings: dict[str, object] = (
        tomllib.loads(path.read_text(encoding="utf-8")).get("tool", {}).get("cli-recorder", {})
    )

    def text(key: str) -> str | None:
        value = settings.get(key)
        if value is not None and not isinstance(value, str):
            msg = f"{path}: [tool.cli-recorder] {key} must be a string"
            raise TypeError(msg)
        return value

    def where(key: str, default: str) -> pathlib.Path:
        return pathlib.Path(text(key) or default)

    fixture = text("fixture")
    return Config(
        program=text("program"),
        parser=text("parser"),
        header=text("header") or "# CLI reference\n",
        global_options=text("global-options"),
        fixture=pathlib.Path(fixture) if fixture else None,
        recordings=where("recordings", "docs/recordings.toml"),
        casts=where("casts", "docs/casts"),
        live_casts=where("live-casts", "docs/assets/casts"),
        page=where("page", "docs/cli.md"),
        docs=where("docs", "docs"),
        site=where("site", "site"),
    )
