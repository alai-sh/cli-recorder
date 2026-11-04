# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

PYPROJECT = """\
[tool.cli-recorder]
program = "sh"
parser = "tool:build"
fixture = "fixture.py"
header = "# Reference\\n"
"""

RECORDINGS = """\
[[recording]]
name = "hello"
title = "Saying hello"
files = { "name.txt" = "world" }
commands = ["cd sub", "echo hello $(cat ../name.txt) $GREETING"]
setup = ["mkdir sub"]

[[reference.greet]]
recording = "hello"
description = "Greets."
"""

FIXTURE = """\
import contextlib

@contextlib.contextmanager
def environment(root, scenarios):
    yield {"GREETING": "from the fixture"}

def prepare(recording, directory):
    (directory / "prepared").write_text(recording["name"])
"""

TOOL = """\
import argparse

def build():
    parser = argparse.ArgumentParser(prog="tool", description="A tool.")
    commands = parser.add_subparsers(title="commands", metavar="COMMAND")
    greet = commands.add_parser("greet", help="say hello", description="Say hello.")
    greet.add_argument("--loud", action="store_true", help="say it with --force")
    return parser
"""


@pytest.fixture
def project(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "fixture.py").write_text(FIXTURE)
    (tmp_path / "tool.py").write_text(TOOL)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "recordings.toml").write_text(RECORDINGS)
    return tmp_path


def run(project: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the package under test, in this interpreter
        [sys.executable, "-m", "cli_recorder", *args],
        cwd=project,
        env={"PYTHONPATH": str(project), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="recording needs a pseudo-terminal")
def test_record(project: pathlib.Path) -> None:
    result = run(project, "record")
    assert result.returncode == 0, result.stderr

    lines = (project / "docs" / "casts" / "hello.cast").read_text().splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 3
    assert header["title"] == "Saying hello"
    assert header["term"]["rows"] == 4
    output = "".join(json.loads(line)[2] for line in lines[1:])
    assert "hello world from the fixture" in output
    assert "~/project/sub" in output


def test_reference(project: pathlib.Path) -> None:
    assert run(project, "reference", "--check").returncode == 1

    result = run(project, "reference")
    assert result.returncode == 0, result.stderr
    page = (project / "docs" / "cli.md").read_text()
    assert page.startswith("# Reference\n")
    assert "## tool greet\n\nSay hello.\n" in page
    assert "[Saying hello](casts/hello.cast){ .cli-cast }\n\nGreets.\n{: .cli-cast-description }" in page
    assert "| `--loud` | Say it with `--force` |" in page

    assert run(project, "reference", "--check").returncode == 0


def test_reference_unused_recording(project: pathlib.Path) -> None:
    recordings = project / "docs" / "recordings.toml"
    recordings.write_text(recordings.read_text().split("[[reference")[0])

    result = run(project, "reference")
    assert result.returncode == 1
    assert "recording 'hello' isn't in [reference] or linked from any page" in result.stderr
