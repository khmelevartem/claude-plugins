#!/usr/bin/env python3
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "no-comments.py")

spec = importlib.util.spec_from_file_location("no_comments", HOOK)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
EDIT_MSG = plugin.MSG % plugin.rule_path()
EDIT_FIRST_LINE = EDIT_MSG.split("\n", 1)[0]
SHELL_FIRST_LINE = ("The whole shell command's file changes were rolled back: "
                    "they contain comments.")

ROOT = os.path.realpath(tempfile.mkdtemp(prefix="no-comments-selftest-"))
PROJ = os.path.join(ROOT, "proj")
TMP = os.path.join(ROOT, "tmp")
PKG = "src/main/kotlin/app/"
A = PKG + "A.kt"
A_CONTENT = b"package app\n\nobject A {\n    fun twice(x: Int) = x * 2\n}\n"
T = PKG + "T.kt"
T_CONTENT = b"package app\n\n// TODO(PROJ-123) keep until upstream fix\nval t = 1\n"
R = PKG + "Resolver.kt"
R_CONTENT = b"package app\n\nobject Resolver\n"
failures = []
passed = 0


def hook(event, tool, tool_input, tool_use_id=None, raw=None):
    payload = {"session_id": "s", "transcript_path": "/dev/null", "cwd": PROJ,
               "hook_event_name": event, "tool_name": tool, "tool_input": tool_input,
               "tool_use_id": tool_use_id or "toolu_" + uuid.uuid4().hex}
    if event.startswith("Post"):
        payload["tool_response"] = {}
    env = dict(os.environ, CLAUDE_PROJECT_DIR=PROJ, TMPDIR=TMP)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    done = subprocess.run([sys.executable, HOOK], env=env, capture_output=True, text=True,
                          input=json.dumps(payload) if raw is None else raw)
    return done.returncode, done.stderr


def bash(command):
    tool_use_id = "toolu_" + uuid.uuid4().hex
    status, err = hook("PreToolUse", "Bash", {"command": command}, tool_use_id)
    assert status == 0 and err == "", err
    shell = subprocess.run(["bash", "-c", command], cwd=PROJ, capture_output=True, text=True)
    event = "PostToolUseFailure" if shell.returncode else "PostToolUse"
    status, err = hook(event, "Bash", {"command": command}, tool_use_id)
    return status, err, shell


def check(name, condition, detail=""):
    global passed
    if condition:
        passed += 1
        print("  ok   " + name)
    else:
        failures.append(name)
        print("  FAIL " + name + (" :: " + detail if detail else ""))


def path(rel):
    return os.path.join(PROJ, rel)


def read(rel):
    with open(path(rel), "rb") as handle:
        return handle.read()


def write(rel, content):
    os.makedirs(os.path.dirname(path(rel)), exist_ok=True)
    with open(path(rel), "wb") as handle:
        handle.write(content)


def stat(rel):
    info = os.stat(path(rel))
    return info.st_mode, info.st_mtime_ns


def snapshots():
    return [os.path.join(r, f) for r, _, fs in os.walk(TMP) for f in fs]


def git(*args):
    return subprocess.run(["git", "-C", PROJ, "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false"] + list(args),
                          capture_output=True, text=True)


def setup():
    os.makedirs(TMP)
    write(A, A_CONTENT)
    write(T, T_CONTENT)
    write(R, R_CONTENT)
    write("README.md", b"# app\n")
    os.chmod(path(A), 0o640)
    os.utime(path(A), ns=(1_600_000_000_000_000_000,) * 2)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")


def edit_tools():
    print("Edit/Write")
    target = path(PKG + "W.kt")
    status, err = hook("PreToolUse", "Write", {"file_path": target,
                                               "content": "package app\n// why\nval w = 1\n"})
    check("Write with comment blocked, plugin message", status == 2 and err == EDIT_MSG + "\n",
          repr(err[:120]))
    status, err = hook("PreToolUse", "Write", {"file_path": target,
                                               "content": "package app\nval w = 1\n"})
    check("Write without comment allowed", status == 0 and err == "")
    status, err = hook("PreToolUse", "Edit", {"file_path": path(A), "old_string": "fun twice",
                                              "new_string": "/** Doubles. */\n    fun twice"})
    check("Edit with comment blocked", status == 2 and err == EDIT_MSG + "\n")
    status, err = hook("PreToolUse", "Edit", {"file_path": path(A), "old_string": "fun twice",
                                              "new_string": "fun double"})
    check("Edit without comment allowed", status == 0 and err == "")
    status, err = hook("PreToolUse", "MultiEdit", {"file_path": path(A), "edits": [
        {"old_string": "x * 2", "new_string": "x * 2 // why"}]})
    check("MultiEdit with comment blocked", status == 2)
    status, err = hook("PreToolUse", "Write", {"file_path": path("NOTES.md"),
                                               "content": "# heading\n"})
    check("Write of .md allowed", status == 0)
    status, err = hook("PreToolUse", "Write", {"file_path": path(PKG + "K.kt"),
                                               "content": "// TODO(PROJ-77) upstream\nval k = 1\n"})
    check("Write with ticket-key comment allowed", status == 0)


def shell():
    print("Bash")
    before, before_stat = read(A), stat(A)
    status, err, _ = bash("cat >> %s <<'EOF'\n/** Extra doc. */\nfun extra() = 1\nEOF" % A)
    check("heredoc append with KDoc -> exit 2", status == 2, err[:200])
    check("existing file restored byte-exact", read(A) == before)
    check("existing file mode and mtime restored", stat(A) == before_stat)
    check("message: shell line, rolled back line, plugin guidance",
          err.startswith(SHELL_FIRST_LINE + "\nRolled back: %s (modified)\n\n" % A)
          and err.endswith(EDIT_MSG[len(EDIT_FIRST_LINE):] + "\n"), err[:300])

    status, err, _ = bash("cat > %s <<'EOF'\npackage app\n\n// rewritten\nobject A\nEOF" % A)
    check("heredoc overwrite with comment rolled back exactly", status == 2 and read(A) == before)

    status, err, _ = bash("cat >> %s <<'EOF'\nfun extra() = 1\nEOF" % A)
    check("heredoc without comment kept",
          status == 0 and err == "" and read(A) == before + b"fun extra() = 1\n")
    kept = read(A)

    status, err, shell = bash("sed -i.bak 's/fun extra() = 1/fun extra() = 1 \\/\\/ one/' %s"
                              " && rm %s.bak" % (A, A))
    check("sed -i adding a comment rolled back", status == 2 and read(A) == kept,
          shell.stderr + err[:100])

    status, err, _ = bash("python3 - <<'EOF'\nopen('%sP.kt','w').write("
                          "'package app\\n/* block */\\nval p = 1\\n')\nEOF" % PKG)
    check("python writing a new file with a comment -> file deleted",
          status == 2 and not os.path.exists(path(PKG + "P.kt")))

    status, err, _ = bash("mkdir -p %sdeep/er && printf 'package x\\n// c\\n' > %sdeep/er/N.kt"
                          " && echo notes > %sdeep/notes.txt" % (PKG, PKG, PKG))
    check("new file with comment in new dirs deleted, empty dirs removed",
          status == 2 and not os.path.exists(path(PKG + "deep/er")), err[:200])
    check("non-source file in a new dir left alone",
          os.path.exists(path(PKG + "deep/notes.txt")))
    shutil.rmtree(path(PKG + "deep"))

    status, err, _ = bash("rm %s && printf 'package app\\n/** d */\\nobject R\\n' > %sR.kt" % (R, PKG))
    check("rm + write in one command: removed file restored, new file deleted",
          status == 2 and read(R) == R_CONTENT and not os.path.exists(path(PKG + "R.kt")))
    check("rolled back list names all three kinds",
          "%sR.kt (created)" % PKG in err and "%s (deleted)" % R in err, err[:300])

    status, err, shell = bash("printf '// c\\n' >> %s; exit 3" % A)
    check("failing command (PostToolUseFailure) still rolled back",
          shell.returncode == 3 and status == 2 and read(A) == kept)

    status, err, _ = bash("printf 'val t2 = 2 // TODO(PROJ-124)\\n' >> %s" % T)
    check("ticket-key comment through the shell allowed", status == 0 and err == "")

    status, err, _ = bash("printf '# heading\\n' > NOTES.md && printf 'x: 1 # c\\n' > conf.yaml")
    check(".md and .yaml ignored", status == 0 and os.path.exists(path("NOTES.md")))

    status, err, _ = bash("printf 'package app\\n\\x00// bin\\n' > %sB.kt" % PKG)
    check("binary file ignored", status == 0 and os.path.exists(path(PKG + "B.kt")))
    os.remove(path(PKG + "B.kt"))

    write(PKG + "Legacy.kt", b"package app\n\n// legacy\nval legacy = 1\n")
    status, err, _ = bash("mkdir -p %sold && mv %sLegacy.kt %sold/Legacy.kt" % (PKG, PKG, PKG))
    check("mv of a file with existing comments allowed",
          status == 0 and os.path.exists(path(PKG + "old/Legacy.kt")), err[:200])

    status, err, shell = bash("git add -A && git -c user.name=t -c user.email=t@t "
                              "-c commit.gpgsign=false commit -q -m wip")
    check("git commit is a no-op", status == 0 and shell.returncode == 0
          and git("status", "--porcelain").stdout == "", shell.stderr)
    status, err, _ = bash("git checkout -q HEAD -- %s && git log --oneline | head -1" % A)
    check("git checkout of an untouched file is a no-op", status == 0 and read(A) == kept)


def git_history():
    print("Bash and git history")
    git("checkout", "-q", "-b", "documented")
    write(R, b"package app\n\n/** Resolves. */\nobject Resolver\n")
    git("commit", "-q", "-am", "doc")
    git("checkout", "-q", "main")
    time.sleep(1.1)
    status, err, shell = bash("git checkout -q documented")
    check("git checkout of a branch with existing comments allowed",
          status == 0 and b"Resolves" in read(R), err[:200] + shell.stderr)
    status, err, _ = bash("git checkout -q main")
    check("and back", status == 0 and read(R) == R_CONTENT)

    status, err, _ = bash("git checkout -q documented -- %s" % R)
    check("git checkout <rev> -- file with existing comments allowed",
          status == 0 and b"Resolves" in read(R))
    git("checkout", "-q", "HEAD", "--", R)

    write(A, read(A) + b"// stashed\n")
    git("stash", "-q")
    time.sleep(1.1)
    status, err, _ = bash("git stash pop -q")
    check("git stash pop restoring an existing comment allowed",
          status == 0 and read(A).endswith(b"// stashed\n"), err[:200])
    git("checkout", "-q", "--", A)

    status, err, _ = bash("printf '// sneaky\\n' >> %s && git -c user.name=t -c user.email=t@t "
                          "-c commit.gpgsign=false commit -q -am sneaky" % A)
    check("write + commit in one command still rolled back",
          status == 2 and not read(A).endswith(b"// sneaky\n"), err[:200])
    git("reset", "-q", "--soft", "HEAD~1")
    git("reset", "-q")


def robustness():
    print("Robustness")
    check("snapshots cleaned up after Post", snapshots() == [], str(snapshots()))
    status, err = hook("PostToolUse", "Bash", {"command": "true"}, "toolu_never_seen")
    check("Post without a snapshot is a no-op", status == 0 and err == "")
    status, err = hook("PreToolUse", "Bash", {}, raw="{not json")
    check("broken input fails open", status == 0)
    status, err = hook("PreToolUse", "Read", {"file_path": path(A)})
    check("other tools ignored", status == 0 and snapshots() == [])


def performance():
    print("Performance")
    big = path("src/main/kotlin/big")
    os.makedirs(big)
    for i in range(300):
        with open(os.path.join(big, "F%d.kt" % i), "w") as handle:
            handle.write("package big\n\n" + "".join(
                "fun f%d_%d(x: Int) = x * %d + \"s/%d\".length\n" % (i, j, j, j)
                for j in range(34)))
    tool_use_id = "toolu_perf"
    t0 = time.time()
    hook("PreToolUse", "Bash", {"command": "true"}, tool_use_id)
    t1 = time.time()
    status, _ = hook("PostToolUse", "Bash", {"command": "true"}, tool_use_id)
    t2 = time.time()
    check("~10k LOC / 300 files, nothing changed: pre %.0f ms, post %.0f ms"
          % (1000 * (t1 - t0), 1000 * (t2 - t1)), status == 0 and t2 - t0 < 1.0)
    hook("PreToolUse", "Bash", {"command": "true"}, tool_use_id)
    t3 = time.time()
    with open(os.path.join(big, "F7.kt"), "a") as handle:
        handle.write("// perf\n")
    status, _ = hook("PostToolUse", "Bash", {"command": "true"}, tool_use_id)
    t4 = time.time()
    check("~10k LOC rollback: post %.0f ms" % (1000 * (t4 - t3)), status == 2 and t4 - t3 < 1.0)
    shutil.rmtree(big)


def main():
    detector = subprocess.run([sys.executable, HOOK, "--selftest"], capture_output=True, text=True)
    print("Detector")
    check("no-comments.py --selftest", detector.stdout.strip() == "ok", detector.stderr[-500:])
    setup()
    try:
        edit_tools()
        shell()
        git_history()
        robustness()
        performance()
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
