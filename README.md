# cli-recorder

The terminal recordings and CLI reference used by the alai projects' docs.
It records each command in a pseudo-terminal with an empty home directory,
so the output is the program's own, colours and progress included. The
recordings are played on the page by a vendored
[asciinema-player](https://github.com/asciinema/asciinema-player).

## Using it in a project

The project adds it to its `docs` dependency group, which `uv.lock` pins to a
commit.

```toml
[dependency-groups]
docs = ["cli-recorder", "zensical"]

[tool.uv.sources]
cli-recorder = { git = "https://github.com/alai-sh/cli-recorder" }

[tool.cli-recorder]
program = "alai-index"                 # the command the recordings run
parser = "alai_index._cli:_parser"     # the argparse parser for the reference
fixture = "docs/recordings.py"         # optional setup hooks
header = "# CLI reference\n"           # the reference page's start
```

The docs build runs these from the project's root.

```console
$ python -m cli_recorder assets docs/assets/cli-recorder
$ python -m cli_recorder reference --check
$ python -m cli_recorder record
$ zensical build --strict
$ python -m cli_recorder check-links
```

alai-index's `docs/` is a complete example.

## Recordings

The recordings are described in `docs/recordings.toml`. Each one is a
`[[recording]]` table, and runs in a new, empty home directory.

```toml
[[recording]]
name = "add"                  # written to docs/casts/add.cast
title = "Adding collections"  # shown above the player
directory = "project"         # where it starts in the home directory, "project" by default
files = { "notes.txt" = "Some text" }  # written to that directory first
setup = ["my-tool init"]      # run first and not shown
commands = [                  # typed and run in the recording, in order
    "my-tool add thing",
    "cd sub",                 # changes the directory the next commands run in
]
columns = 80                  # the terminal's width, 80 by default
```

| Key | Means |
|---|---|
| `name` | The file the recording is written to, `docs/casts/<name>.cast` |
| `title` | The title shown above the player |
| `directory` | Where the recording starts in the home directory, `project` by default |
| `files` | Files written to that directory first, by relative path |
| `setup` | Shell commands run before recording starts, which aren't shown |
| `commands` | Shell commands typed and run, in order |
| `columns` | The terminal's width, 80 by default. The player fits a recording to the page, so a wider terminal gives smaller text |
| `rows` | The terminal's height for a full-screen program, such as a TUI, from 1 to 48. The recording is that tall, as a full-screen program draws anywhere on the screen, and its pauses aren't shortened. It can't be used with `panes` |
| `keys` | Keys pressed while the last command runs, for a full-screen program such as a TUI. Each is a key name (`enter`, `tab`, `space`, `escape`, `backspace`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`) or text to type, and a number is a pause in seconds. `{ click = [column, row] }` clicks the left button there, counting from 1. They start once the program writes something, half a second apart. A program still running 5 seconds after its last key is stopped and the recording fails. Each key is written to the recording as an input event. The player shows each key as it's pressed, and the page shows a click as a ring where it lands |
| `timeout` | Seconds a command can run before it's stopped and the recording fails, 60 by default |
| `env` | Environment variables for the recording's commands, on top of the fixture's |
| `live` | `true` to skip the fixture's environment and record only when named, as `record NAME`. A live recording is written to `docs/assets/casts/` to be committed, with light and dark SVGs made by asg |
| `panes` | A split screen in place of `commands`, as `[[recording.panes]]` tables from top to bottom. Each has a `title`, a `directory` and its `commands` |

A recording reaching past 48 rows fails, as its first lines would scroll
away. A command the shell can't find also fails. Any other failing command is
recorded, as a recording can show an error on purpose. Keys of the project's
own, such as alai-index's `tarballs`, are left to the fixture's `prepare`
hook.

A page shows a recording with a link that has the `cli-cast` class.

```markdown
[Adding collections](casts/add.cast){ .cli-cast }
```

The CLI reference shows a recording under a command when a
`[[reference."<command>"]]` table names it. The `description` is optional,
and its paragraphs are shown between the title and the player. `reference`
fails when a recording isn't shown anywhere, either in the reference or
linked from a page.

```toml
[[reference."add"]]
recording = "add"
description = """
Each version is read from its own artifact.
"""
```

## Fixture

When the recordings need something set up, such as a local server or built
files, the `fixture` file can define two hooks.

```python
@contextlib.contextmanager
def environment(root, scenarios):
    # Entered once around every recording but the live ones. root is a
    # temporary directory, and scenarios is the parsed recordings file.
    with start_server(root) as server:
        yield {"MY_TOOL_SERVER": server.url}  # environment variables for the recordings


def prepare(recording, directory):
    # Called for each recording after its files are written.
    ...
```

## Developing

cli-recorder runs on Python 3.15 and later, as it's only ever run by a docs
build. CI runs the same checks as these commands.

```console
$ uv sync
$ uv run ruff check . && uv run ruff format --check . && uv run basedpyright --warnings
$ uv run pytest
```

## License

MIT, see [LICENSE](LICENSE). asciinema-player is Apache-2.0, see
[its license](src/cli_recorder/assets/asciinema-player/LICENSE).
