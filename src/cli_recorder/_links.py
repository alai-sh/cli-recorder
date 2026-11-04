# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Check every link between the docs pages, anchors included, against the built site.

``zensical build --strict`` reports a link to a page that doesn't exist, but not one to a heading that doesn't, such as
``page.md#heading`` after the heading is renamed. Run it after building the site.
"""

from __future__ import annotations

import pathlib
import re
import sys

from ._config import Config

# [text](page.md), [text](page.md#anchor) and [text](#anchor), and recordings, [text](casts/name.cast), ignoring
# external links.
_LINK = re.compile(r"\]\((?P<page>[\w./-]*\.(?:md|cast))?(?:#(?P<anchor>[\w-]+))?\)")
_ID = re.compile(r'\bid="([^"]+)"')


def _built(config: Config, page: pathlib.Path) -> pathlib.Path:
    """Where a docs page is in the site, ``index.md`` at its root and ``x.md`` as ``x/index.html``."""
    relative = page.relative_to(config.docs.resolve()).with_suffix("")
    if relative.name == "index":
        return config.site / relative.parent / "index.html"
    return config.site / relative / "index.html"


def check_links(config: Config) -> int:
    if not config.site.is_dir():
        _ = sys.stderr.write(f"{config.site}/ doesn't exist: build the docs first\n")
        return 1
    ids: dict[pathlib.Path, set[str]] = {}
    problems: list[str] = []
    for page in sorted(config.docs.rglob("*.md")):
        text = page.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            for match in _LINK.finditer(line):
                target = (page.parent / match.group("page")).resolve() if match.group("page") else page.resolve()
                if not target.is_file():
                    problems.append(f"{page}:{number}: {match.group('page')} doesn't exist")
                    continue
                anchor = match.group("anchor")
                if anchor is None:
                    continue
                html = _built(config, target)
                if html not in ids:
                    ids[html] = set(_ID.findall(html.read_text(encoding="utf-8"))) if html.is_file() else set()
                if anchor not in ids[html]:
                    problems.append(f"{page}:{number}: no #{anchor} in {target.name}")
    for problem in problems:
        _ = sys.stderr.write(f"{problem}\n")
    return 1 if problems else 0
