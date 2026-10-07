#!/usr/bin/env python3
import hashlib
import json
import marshal
import os
import re
import stat
import subprocess
import sys
import tempfile
import time

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


def rule_path():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "no-comments.md")


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

SHELL_MSG = ("The whole shell command's file changes were rolled back: "
             "they contain comments.\nRolled back: %s")


EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".gradle", ".kotlin", "build",
    "out", "target", "dist", "node_modules", "bower_components", "vendor",
    ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox",
    ".ruff_cache", ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache",
    ".parcel-cache", "coverage", ".dart_tool", ".build", "Pods",
    "DerivedData", ".terraform", ".cxx", ".externalNativeBuild",
}
MAX_FILES = 20_000
MAX_TOTAL = 64_000_000
STALE_SECONDS = 24 * 3600
CLOCK_SLACK_NS = 2_000_000_000
GIT_TOKEN = re.compile(r"[\w./@{}^~-]{1,100}")


class TooLarge(Exception):
    pass


def project_dir(data):
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR")
                            or data.get("cwd") or os.getcwd())


def state_root():
    uid = os.getuid() if hasattr(os, "getuid") else None
    name = "claude-no-comments" if uid is None else "claude-no-comments-%d" % uid
    root = os.path.join(tempfile.gettempdir(), name)
    os.makedirs(root, mode=0o700, exist_ok=True)
    info = os.lstat(root)
    if stat.S_ISLNK(info.st_mode) or (uid is not None and info.st_uid != uid):
        raise OSError("state directory is not ours: " + root)
    return root


def snapshot_path(data, project):
    key = data.get("tool_use_id") or "cmd-" + hashlib.sha1(
        (data.get("tool_input") or {}).get("command", "").encode()).hexdigest()
    folder = os.path.join(state_root(),
                          hashlib.sha1(project.encode()).hexdigest()[:16])
    return folder, os.path.join(folder, re.sub(r"[^\w.-]", "_", key))


def tracked(name):
    return "." in name and name.rsplit(".", 1)[-1].lower() in EXT


def walk(root, before=None, unchanged_before_ns=0, limited=False):
    files, dirs, total = {}, set(), 0
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath != root and (".git" in dirnames or ".git" in filenames):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, root)
        dirs.add(rel_dir)
        for name in filenames:
            if not tracked(name):
                continue
            rel = os.path.normpath(os.path.join(rel_dir, name))
            path = os.path.join(dirpath, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BASELINE:
                continue
            sig = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)
            known = before.get(rel) if before else None
            if known and known[1] == sig and info.st_ctime_ns < unchanged_before_ns:
                files[rel] = known
                continue
            try:
                with open(path, "rb") as handle:
                    content = handle.read()
            except OSError:
                continue
            if b"\0" in content[:8192]:
                continue
            total += len(content)
            if limited and (total > MAX_TOTAL or len(files) >= MAX_FILES):
                raise TooLarge(root)
            files[rel] = (info.st_mode, sig, content)
    return files, dirs


def decoded(content):
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def file_comments(rel, content):
    text = decoded(content) if content is not None else None
    return comments(text, syntax_for(rel)) if text is not None else set()


def git(root, args, stdin=None):
    try:
        done = subprocess.run(["git"] + args, cwd=root, input=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def stash_commits(root):
    listing = git(root, ["stash", "list", "--format=%H"])
    return listing.decode().split() if listing else []


def named_revisions(command):
    names = []
    for word in re.split(r"[\s;&|()<>'\"`]+", command):
        word = word.split("=", 1)[-1].split(":", 1)[0]
        if word and not word.startswith("-") and GIT_TOKEN.fullmatch(word):
            names.append(word)
    return names[:40]


def commits_before(root, started_ns, command, stashes):
    since = started_ns // 1_000_000_000
    listing = git(root, ["rev-list", "--parents", "--since=%d" % since, "HEAD"])
    if listing is None:
        return []
    rows = [line.split() for line in listing.decode().splitlines()]
    fresh = {row[0] for row in rows}
    candidates = ["HEAD"] if not rows else []
    candidates += [p for row in rows for p in row[1:] if p not in fresh]
    candidates += list(stashes)
    names = named_revisions(command)
    if names:
        found = git(root, ["cat-file", "--batch-check"],
                    "".join(n + "^{commit}\n" for n in names).encode())
        for line in (found or b"").decode().splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1] == "commit":
                candidates.append(parts[0])
    if not candidates:
        return []
    dated = git(root, ["log", "--no-walk=unsorted", "--format=%H %ct"]
                + sorted(set(candidates)))
    return [sha for sha, when in (line.split() for line in
                                  (dated or b"").decode().splitlines())
            if int(when) < since]


def committed_blobs(root, commits, paths):
    prefix = git(root, ["rev-parse", "--show-prefix"]) if commits else None
    if prefix is None:
        return {}
    prefix = prefix.decode().strip()
    requests = [(sha, rel) for rel in paths for sha in commits]
    batch = "".join("%s:%s%s\n" % (sha, prefix, rel.replace(os.sep, "/"))
                    for sha, rel in requests)
    output = git(root, ["cat-file", "--batch"], batch.encode()) or b""
    blobs, at = {}, 0
    for _, rel in requests:
        end = output.find(b"\n", at)
        if end < 0:
            break
        header = output[at:end].split()
        at = end + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            blobs.setdefault(rel, []).append(output[at:at + size])
            at += size + 1
    return blobs


def added_comments(before, after):
    created = sorted(p for p in after if p not in before)
    modified = sorted(p for p in after if p in before and after[p][2] != before[p][2])
    deleted = sorted(p for p in before if p not in after)
    moved = set()
    for p in deleted:
        moved |= file_comments(p, before[p][2])
    offenders = {}
    for p in created + modified:
        if decoded(after[p][2]) is None:
            continue
        known = file_comments(p, before[p][2]) if p in before else set(moved)
        added = file_comments(p, after[p][2]) - known
        if added:
            offenders[p] = added
    return created, modified, deleted, offenders


def drop_committed(root, snap, command, offenders):
    commits = commits_before(root, snap["started"], command, snap["stashes"])
    blobs = committed_blobs(root, commits, sorted(offenders))
    for rel, contents in blobs.items():
        for content in contents:
            offenders[rel] -= file_comments(rel, content)
    return {rel: added for rel, added in offenders.items() if added}


def write_back(path, mode, mtime_ns, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.islink(path):
        os.remove(path)
    try:
        handle = open(path, "wb")
    except PermissionError:
        os.chmod(path, 0o600)
        handle = open(path, "wb")
    with handle:
        handle.write(content)
    os.chmod(path, stat.S_IMODE(mode))
    os.utime(path, ns=(mtime_ns, mtime_ns))


def roll_back(root, snap, after_dirs, created, changed):
    for rel in created:
        try:
            os.remove(os.path.join(root, rel))
        except OSError:
            pass
    for rel in changed:
        mode, sig, content = snap["files"][rel]
        try:
            write_back(os.path.join(root, rel), mode, sig[1], content)
        except OSError:
            pass
    for rel in sorted(after_dirs - snap["dirs"], key=lambda d: d.count(os.sep),
                      reverse=True):
        try:
            os.rmdir(os.path.join(root, rel))
        except OSError:
            pass


def forget_stale(folder):
    limit = time.time() - STALE_SECONDS
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if os.lstat(path).st_mtime < limit:
                os.remove(path)
        except OSError:
            pass


def shell_before(data):
    project = project_dir(data)
    command = (data.get("tool_input") or {}).get("command", "")
    folder, path = snapshot_path(data, project)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    forget_stale(folder)
    started = time.time_ns()
    try:
        files, dirs = walk(project, limited=True)
    except TooLarge:
        return 0
    snap = {"project": project, "started": started, "files": files, "dirs": dirs,
            "stashes": stash_commits(project) if "stash" in command else []}
    partial = path + ".partial"
    with open(partial, "wb") as handle:
        marshal.dump(snap, handle)
    os.replace(partial, path)
    return 0


def shell_after(data):
    project = project_dir(data)
    command = (data.get("tool_input") or {}).get("command", "")
    _, path = snapshot_path(data, project)
    try:
        with open(path, "rb") as handle:
            snap = marshal.load(handle)
    except (OSError, EOFError, ValueError, TypeError):
        return 0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    root, before = snap["project"], snap["files"]
    after, after_dirs = walk(root, before, snap["started"] - CLOCK_SLACK_NS)
    created, modified, deleted, offenders = added_comments(before, after)
    if offenders:
        offenders = drop_committed(root, snap, command, offenders)
    if not offenders:
        return 0
    roll_back(root, snap, after_dirs, created, modified + deleted)
    restored = sorted([(p, "created") for p in created]
                      + [(p, "modified") for p in modified]
                      + [(p, "deleted") for p in deleted])
    listing = ", ".join("%s (%s)" % item for item in restored)
    message = MSG % rule_path()
    print(SHELL_MSG % listing + message[message.index("\n"):], file=sys.stderr)
    return 2


def main():
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    tool = data.get("tool_name")
    event = data.get("hook_event_name")
    if tool in EDIT_TOOLS:
        if event in (None, "PreToolUse") and blocked(data.get("tool_input") or {}):
            print(MSG % rule_path(), file=sys.stderr)
            return 2
        return 0
    if tool != "Bash":
        return 0
    if event == "PreToolUse":
        return shell_before(data)
    if event in ("PostToolUse", "PostToolUseFailure"):
        return shell_after(data)
    return 0


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

    print("ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    try:
        status = main()
    except Exception:
        status = 0
    sys.exit(status)
