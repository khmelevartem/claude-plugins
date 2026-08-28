# no-comments

Based on https://www.youtube.com/watch?v=Bf7vDBBOBUA&t=332s

A Claude Code plugin. Forbids the agent from writing comments and doc blocks in
code. Once a comment shows up, the whole edit is rejected — not just that line —
and the agent rewrites the code so the explanation is carried by the code
itself. The rejection message hands it the alternatives: a private method with a
telling name, a named constant, a sharper type, a test.

Why comments are banned at all: [no-comments.md](no-comments.md).

## Install

```
/plugin marketplace add khmelevartem/claude-plugins
/plugin install no-comments@khmelev-plugins
```

Restart Claude Code afterwards. Nothing to put into `settings.json` and no paths
to edit; `/plugin` updates it later.

## What it catches

| Marker        | Languages                                                              |
| ------------- | ---------------------------------------------------------------------- |
| `//`, `/* */` | Kotlin, Java, Swift, JS/TS, C/C++, Go, Rust, Scala, C#, Dart, PHP, CSS |
| `#`           | Python, Ruby, shell, Perl, R, Elixir, Terraform, PowerShell            |
| `--`          | SQL, Haskell, Lua, Elm                                                 |
| `;`           | Clojure, Lisp, Scheme                                                  |
| `%`           | Erlang, LaTeX                                                          |
| `<!-- -->`    | HTML, XML, Vue, Svelte                                                 |
| `"""`, `'''`  | Python docstrings                                                      |

Editing a source through the shell (redirect into a file, `sed -i`, `tee`) is
rejected as well, otherwise the hook is bypassed with a single command.

Only what the edit *adds* counts. `Edit` compares `old_string` against
`new_string`; `Write` compares the new content against the file already on
disk. So rewriting a file whole keeps its existing comments — a public API's
Doc written by hand stays put, and only a freshly added comment is rejected.
A file that does not exist yet has no baseline: every comment in it is new.

## What gets through

- a comment carrying a ticket key (`// TODO(PROJ-123)`) — the ticket already
  exists and describes a problem outside this code. The key needs two or more
  digits, so that `UTF-8` is not mistaken for one;
- a shebang;
- md, txt, json, yaml, toml, csv, ini, lock and the rest of `SKIP_EXT` — not
  sources;
- a file with no extension at all (`Dockerfile`, `Makefile`);
- an unknown extension, treated as C-like: `//` and `/* */` are caught, `#` is
  not;
- shell writes into temporary directories (`/tmp`, scratchpad), and into
  anything from `SKIP_EXT`.

## Known gaps

- A path built from a variable cannot be resolved, so a shell write through one
  is rejected rather than guessed. Use a literal path.
- A shell write to a file with no extension is not checked.
- Source embedded in a string literal (a code sample inside a triple-quoted
  block) can be misread in both directions. Measured on the Python stdlib:
  2252 of 2257 files match the reference tokenizer exactly, and every mismatch
  is in a file that stores Python source inside strings.

## Check

```
python3 hooks/no-comments.py --selftest
```

Prints `ok`. The selftest lists what the hook catches and what it lets through.
Fix an arguable case in the same file: the syntax table at the top, a new assert
line in the selftest below.

## What's here

- `hooks/no-comments.py` — the hook itself, no dependencies, needs python3
- `hooks/hooks.json` — where it is wired into `PreToolUse`
- `no-comments.md` — the reasoning: why comments are banned and what to do
  instead. Written for whoever is deciding whether to put this on. The agent
  does not need it — the rejection message already carries the instruction —
  but the message names the file, so it can be read when the reasoning is
  disputed
