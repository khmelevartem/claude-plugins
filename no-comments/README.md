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

Editing a source through the shell — a heredoc, `sed -i`, `tee`, a Python
one-liner — is checked as well, otherwise the hook is bypassed with a single
command. The shell is not blocked: before every `Bash` call the hook snapshots
the project's source files, after it (on success and on failure alike) it
compares. If the command added a comment to any source file, every
file the command created, modified or deleted is put back as it was — content
and mode; created files and the directories left empty are removed — and the
agent gets the same message as for an edit, headed by the list of what was
rolled back. Blocking the shell outright turned out to cost the agent far more
than the rare rollback: it gets pushed off heredocs onto `Write`/`Edit`.

Only the project directory (`$CLAUDE_PROJECT_DIR`) is watched, and only files
with a known source extension. Skipped: `.git`, `build`, `.gradle`, `.kotlin`,
`node_modules`, `target`, `dist`, `.venv` and the rest of `SKIP_DIRS`; nested
repositories; files over 2 MB and binary ones. Snapshots live in the system
temp directory, never in the project, and are deleted after the command.

Only what the edit *adds* counts. `Edit` compares `old_string` against
`new_string`; `Write` compares the new content against the file already on
disk. So rewriting a file whole keeps its existing comments — a public API's
Doc written by hand stays put, and only a freshly added comment is rejected.
A file that does not exist yet has no baseline: every comment in it is new.
For the shell the baseline is the file before the command; a file moved with
`mv` keeps its comments; and a comment that git brings back from a commit that
existed before the command (`git checkout`, `pull`, `merge`, `reset`,
`stash pop`) is not new either.

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
- shell writes outside the project (`/tmp`, scratchpad), and into anything
  from `SKIP_EXT`.

## Known gaps

- A background shell command (`run_in_background`) is compared when it is
  launched, not when it finishes: what it writes later is not checked.
- Through the shell only known source extensions are checked; a file with no
  extension or an unknown one is not.
- A project with more than 20 000 source files or 64 MB of sources is not
  snapshotted, and its shell commands are not checked.
- Two commands running at once in the same project (parallel agents) see each
  other's changes; a rollback puts back both.
- A comment copied from a commit by path (`git show rev:A.kt > B.kt`) or from
  any other file is new.
- Source embedded in a string literal (a code sample inside a triple-quoted
  block) can be misread in both directions. Measured on the Python stdlib:
  2252 of 2257 files match the reference tokenizer exactly, and every mismatch
  is in a file that stores Python source inside strings.

## Check

```
python3 hooks/no-comments.py --selftest
python3 hooks/selftest.py
```

The first prints `ok`: it lists what the detector catches and what it lets
through. Fix an arguable case in the same file: the syntax table at the top, a
new assert line in the selftest below. The second drives the hook the way
Claude Code does — edits, shell commands with and without comments, git, the
timing on 10k lines — in a throwaway project.

## What's here

- `hooks/no-comments.py` — the hook itself, no dependencies, needs python3
  (3.9+) and, for the git allowance, git
- `hooks/hooks.json` — where it is wired into `PreToolUse`, `PostToolUse` and
  `PostToolUseFailure`
- `hooks/selftest.py` — the end-to-end check
- `no-comments.md` — the reasoning: why comments are banned and what to do
  instead. Written for whoever is deciding whether to put this on. The agent
  does not need it — the rejection message already carries the instruction —
  but the message names the file, so it can be read when the reasoning is
  disputed
