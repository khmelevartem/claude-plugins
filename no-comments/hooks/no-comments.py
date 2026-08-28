#!/usr/bin/env python3
import json
import os
import re
import shlex
import sys
import tempfile

TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{2,}\b")
TRIPLE = re.compile(r'"""|\'\'\'')
DOC_PREFIXES = {"", "r", "f", "b", "u", "rb", "br", "rf", "fr"}
DELIMITERS = set(" \t\n\r,;:()[]}\"'`")

SKIP_EXT = {
    "md", "mdx", "markdown", "txt", "rst", "adoc", "csv", "tsv", "log",
    "json", "json5", "yaml", "yml", "toml", "ini", "cfg", "conf",
    "properties", "env", "lock", "gitignore", "gitattributes",
    "editorconfig", "svg", "png", "jpg", "jpeg", "gif", "pdf",
}


def syntax(line=(), block=(), star=False, doc=False):
    return {"line": tuple(line), "block": tuple(block), "star": star, "doc": doc}


C_LIKE = syntax(line=["//"], block=[("/*", "*/")], star=True)
PYTHON = syntax(line=["#"], doc=True)
HASH = syntax(line=["#"])
DASH = syntax(line=["--"])
SEMI = syntax(line=[";"])
PERCENT = syntax(line=["%"])
MARKUP = syntax(block=[("<!--", "-->")])
MIXED = syntax(line=["//"], block=[("/*", "*/"), ("<!--", "-->")], star=True)
DEFAULT = C_LIKE

EXT = {}
for group, names in [
    (C_LIKE, "kt kts java swift js mjs cjs jsx ts tsx mts cts c h cc cpp cxx"
             " hpp hxx m mm go rs scala cs dart php groovy gradle proto zig"
             " css scss less sass v sv"),
    (PYTHON, "py pyi ipynb"),
    (HASH, "rb sh bash zsh fish pl pm r jl nim cr ex exs tf tfvars ps1 psm1"),
    (DASH, "sql hs lua elm adb ads"),
    (SEMI, "clj cljs cljc edn el lisp scm rkt"),
    (PERCENT, "erl hrl tex"),
    (MARKUP, "html htm xml xhtml xsl xslt"),
    (MIXED, "vue svelte astro"),
]:
    for name in names.split():
        EXT[name] = group


def syntax_for(path):
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return DEFAULT
    ext = name.rsplit(".", 1)[-1].lower()
    if ext in SKIP_EXT:
        return None
    return EXT.get(ext, DEFAULT)


def skip_quoted(text, i):
    quote = text[i]
    j = i + 1
    while j < len(text) and text[j] != "\n":
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == quote:
            return j + 1
        j += 1
    return i + 1


def opener_at(text, i, markers):
    for marker in markers:
        if text.startswith(marker, i):
            return marker
    return None


def normalize(fragment):
    return " ".join(fragment.split())


def scan(text, cfg):
    closers = dict(cfg["block"])
    found = []
    masked = list(text)
    length = len(text)

    def blank(start, stop):
        for j in range(max(start, 0), min(stop, length)):
            if masked[j] != "\n":
                masked[j] = " "

    def upto(start, marker):
        end = text.find(marker, start)
        return length if end < 0 else end + len(marker)

    i = 0
    while i < length:
        char = text[i]
        if char == "\\":
            i += 2
            continue
        triple = TRIPLE.match(text, i)
        if triple:
            quote = triple.group(0)
            stop = upto(i + 3, quote)
            body_end = stop - 3 if stop < length else length
            head = text[text.rfind("\n", 0, i) + 1:i].strip()
            if cfg["doc"] and head in DOC_PREFIXES:
                found.append(normalize(text[i + 3:body_end]) or quote)
            blank(i + 3, body_end)
            i = stop
            continue
        if char in "\"'`":
            after = skip_quoted(text, i)
            blank(i + 1, after - 1)
            i = after
            continue
        block = opener_at(text, i, closers)
        if block:
            stop = upto(i + len(block), closers[block])
            found.append(normalize(text[i:stop]))
            blank(i, stop)
            i = stop
            continue
        if opener_at(text, i, cfg["line"]) and (i == 0 or text[i - 1] in DELIMITERS):
            stop = text.find("\n", i)
            stop = length if stop < 0 else stop
            found.append(normalize(text[i:stop]))
            blank(i, stop)
            i = stop
            continue
        i += 1
    return found, "".join(masked)


def is_block_continuation(stripped):
    if stripped.endswith("{"):
        return False
    return stripped == "*" or stripped.startswith("* ") or stripped.startswith("*/")


def comments(text, cfg):
    found, masked = scan(text, cfg)
    if cfg["star"]:
        found += [normalize(line) for line in masked.splitlines()
                  if is_block_continuation(line.strip())]
    return {c for c in found
            if c and not c.startswith("#!") and not TICKET.search(c)}


MAX_BASELINE = 2_000_000


def baseline(path):
    try:
        if os.path.getsize(path) > MAX_BASELINE:
            return ""
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def edits(tool_input, path):
    if isinstance(tool_input.get("edits"), list):
        for edit in tool_input["edits"]:
            yield edit.get("old_string", ""), edit.get("new_string", "")
        return
    new = (tool_input.get("new_string") or tool_input.get("content")
           or tool_input.get("new_source") or "")
    old = tool_input.get("old_string") or tool_input.get("old_source") or ""
    if not old and tool_input.get("content"):
        old = baseline(path)
    yield old, new


def blocked(tool_input):
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    cfg = syntax_for(path)
    if cfg is None:
        return False
    return any(comments(new, cfg) - comments(old, cfg)
               for old, new in edits(tool_input, path))


HEREDOC = re.compile(r"<<-?\s*[\"']?(\w+)[\"']?")
SEGMENT = re.compile(r"[|;&\n]+")
REDIRECT = re.compile(r"^\d*&?>>?\|?(?P<rest>.*)$")
IN_PLACE = re.compile(r"^-\w*i")
SCRATCH = re.compile(r"/tmp/|/dev/|scratch|tmpdir", re.IGNORECASE)


def strip_heredocs(command):
    kept = []
    closing = None
    for line in command.split("\n"):
        if closing is not None:
            if line.strip() == closing:
                closing = None
            continue
        kept.append(line)
        match = HEREDOC.search(line)
        if match:
            closing = match.group(1)
    return "\n".join(kept)


def tokens(segment):
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return segment.split()


def write_targets(segment):
    parts = tokens(segment)
    targets = []
    collecting = False
    for n, part in enumerate(parts):
        if not part:
            continue
        redirect = REDIRECT.match(part)
        if redirect:
            rest = redirect.group("rest")
            if rest:
                targets.append(rest)
            elif n + 1 < len(parts):
                targets.append(parts[n + 1])
            continue
        if part == "tee":
            collecting = True
            continue
        if part in ("sed", "perl") and any(IN_PLACE.match(p) for p in parts[n + 1:]):
            collecting = True
            continue
        if collecting and not part.startswith("-"):
            targets.append(part)
    return targets


def blocked_shell(command):
    for segment in SEGMENT.split(strip_heredocs(command)):
        for target in write_targets(segment):
            if SCRATCH.search(target):
                continue
            name = target.rsplit("/", 1)[-1]
            if "." not in name:
                continue
            if name.rsplit(".", 1)[-1].lower() in SKIP_EXT:
                continue
            return target
    return None


def rule_path():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "no-comments.md")


SHELL_MSG = """Writing to a source file through the shell is rejected: %s

Edit/Write go through no-comments, the shell does not.
Redo the edit with Edit or Write.
The shell may only write into temporary directories (/tmp, scratchpad), and
only through a literal path: a path built from a variable cannot be checked."""

MSG = """The whole edit is rejected: it contains comments.

Do not strip the comment and repeat the same edit, that is separately banned.
A comment means the code does not say what you wanted to say.
Rebuild the edit in one of these ways:

- extract the fragment into a private method with a telling name
- replace the literal with a named constant
- rename the variable or the function
- sharpen the type (a duration type instead of a number, an enum instead of
  free-form strings)
- pin the behaviour with a test whose name states it
- switch the pattern if the explanation would take more than one line

If the problem is in the current implementation, solve it instead of tagging it.

A comment carrying a ticket key (`// TODO(PROJ-123)`, `/* PROJ-123: temp */`)
is let through: the ticket already exists and describes a problem outside this
code. Filing a ticket just to keep a comment is not allowed.

If none of these fit and the situation is critical, stop, do not repeat the
edit, and offer the user to file a ticket.

State in one line which way you picked.

The full rule: %s"""


def selftest():
    def edit(path, new, old=""):
        return {"file_path": path, "content": new, "old_string": old,
                "new_string": new}

    assert blocked(edit("a.kt", "// why\nval x = 1"))
    assert blocked(edit("a.py", "# why\nx = 1"))
    assert blocked(edit("a.kt", "/**\n * doc\n */\nfun f() {}"))
    assert blocked(edit("a.kt", " * doc line inside an existing block"))
    assert blocked(edit("a.kt", "val x = 1 // why"))
    assert blocked(edit("a.ts", "const x = 1; // why"))
    assert blocked(edit("a.py", "x = 1  # why"))
    assert blocked(edit("a.py", "d = {1: 2},# why"))
    assert blocked(edit("a.sql", "select 1 -- why"))
    assert blocked(edit("a.lua", "--[[ why ]]"))
    assert blocked(edit("a.clj", "; why"))
    assert blocked(edit("a.erl", "% why"))
    assert blocked(edit("a.html", "<!-- nav -->"))
    assert blocked(edit("a.vue", "<!-- nav -->"))
    assert blocked(edit("a.py", 'def f():\n    """Does a thing."""\n    return 1'))
    assert blocked(edit("a.py", 'def f():\n    """\n    Does a thing.\n    """'))
    assert blocked(edit("a.kt", "// TODO: finish later"))
    assert blocked(edit("a.py", "# UTF-8 encoding is required"))
    assert blocked(edit("a.py", 'SQL = """\nselect 1\n"""\nx = 1  # why'))
    assert blocked({"file_path": "a.kt", "edits": [
        {"old_string": "val x = 1", "new_string": "// why\nval x = 1"}]})
    assert blocked({"notebook_path": "a.ipynb", "new_source": "# why\nx = 1"})

    assert not blocked(edit("a.rs", "#[derive(Debug)]\nstruct S;"))
    assert not blocked(edit("a.rs", "#![no_std]"))
    assert not blocked(edit("a.c", "#include <stdio.h>\n#define N 1"))
    assert not blocked(edit("a.php", "#[Route('/')]"))
    assert not blocked(edit("Dockerfile", "# base image\nFROM x"))
    assert not blocked(edit("a.kt", "// PROJ-12\nval x = 1"))
    assert not blocked(edit("a.kt", "// TODO(KTLT-1101)\nval x = 1"))
    assert not blocked(edit("a.kt", "/* KTLT-1101: temporary */"))
    assert not blocked(edit("a.md", "# Title"))
    assert not blocked(edit("a.yaml", "# comment"))
    assert not blocked(edit("a.kt", 'val u = "https://x"'))
    assert not blocked(edit("a.py", 'u = "http://x"'))
    assert not blocked(edit("a.py", 'SCRIPT = """\n# not mine\nrun()\n"""'))
    assert not blocked(edit("a.kt", 'val q = """\n// not mine\n"""'))
    assert not blocked(edit("a.sh", "#!/bin/sh\necho hi"))
    assert not blocked(edit("a.sh", 'echo "${#items[@]}"'))
    assert not blocked(edit("a.rs", "struct Foo<'a> { name: &'a str }"))
    assert not blocked(edit("a.py", 'SQL = """\nselect 1\n"""'))
    assert not blocked(edit("a.c", "int f(int *p) {\n    *p = 1;\n    return *p;\n}"))
    assert not blocked(edit("a.kt", "// why", "// why\nval x = 1"))
    assert not blocked(edit("a.kt", "// why\nval x = 1", "// why"))

    with tempfile.TemporaryDirectory() as folder:
        kdoc = "/**\n * Returns the user.\n */\nfun user() = current\n"
        existing = os.path.join(folder, "Api.kt")
        with open(existing, "w", encoding="utf-8") as handle:
            handle.write(kdoc)
        assert not blocked({"file_path": existing, "content": kdoc})
        assert not blocked({"file_path": existing,
                            "content": kdoc.replace("current", "currentUser")})
        assert blocked({"file_path": existing, "content": kdoc + "// extra\n"})
        assert blocked({"file_path": os.path.join(folder, "New.kt"),
                        "content": kdoc})

    assert blocked_shell("cat > src/Main.kt <<EOF") == "src/Main.kt"
    assert blocked_shell("sed -i '' 's/a/b/' app/Foo.swift") == "app/Foo.swift"
    assert blocked_shell("printf 'x' >> build.gradle.kts") == "build.gradle.kts"
    assert blocked_shell("cat > app/x.php") == "app/x.php"
    assert blocked_shell("cat > app/X.vue") == "app/X.vue"
    assert blocked_shell("cat > lib/main.dart") == "lib/main.dart"
    assert blocked_shell("echo x | tee src/Main.scala") == "src/Main.scala"
    assert blocked_shell("cat > $DIR/Main.kt") == "$DIR/Main.kt"

    assert not blocked_shell("grep -r foo --include=*.kt . > out.txt")
    assert not blocked_shell("./gradlew build 2>/dev/null")
    assert not blocked_shell("cat > /tmp/x.py <<EOF")
    assert not blocked_shell("cat > $SCRATCHPAD/x.py")
    assert not blocked_shell("sed -n '1,20p' src/Main.kt")
    assert not blocked_shell("git diff -U0 | head -50")
    assert not blocked_shell("cat > notes.md")
    assert not blocked_shell("cat > out")
    assert not blocked_shell("cat <<EOF\ncat > src/Main.kt\nEOF")
    print("ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input", {})
    if data.get("tool_name") == "Bash":
        target = blocked_shell(tool_input.get("command", ""))
        if target:
            print(SHELL_MSG % target, file=sys.stderr)
            sys.exit(2)
    elif blocked(tool_input):
        print(MSG % rule_path(), file=sys.stderr)
        sys.exit(2)
