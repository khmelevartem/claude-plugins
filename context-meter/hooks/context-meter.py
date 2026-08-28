#!/usr/bin/env python3
import json
import os
import sys

YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

DEFAULTS = {"YELLOW_AT": 20.0, "RED_AT": 30.0, "WARN_AT": 40.0}


def option(key):
    raw = (os.environ.get("CLAUDE_PLUGIN_OPTION_" + key) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def threshold(key):
    value = option(key)
    return DEFAULTS[key] if value is None else value


def state_dir():
    root = os.environ.get("CLAUDE_PLUGIN_DATA")
    if root:
        return os.path.join(root, "context-meter")
    return os.path.expanduser("~/.claude/cache/context-meter")


def read_tail(path, limit):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        start = max(0, size - limit)
        f.seek(start)
        return f.read().decode("utf-8", "replace"), start


def last_usage(path):
    for limit in (262144, 2097152, 16777216):
        try:
            chunk, start = read_tail(path, limit)
        except OSError:
            return None, ""
        lines = chunk.split("\n")
        if start:
            lines = lines[1:]
        for line in reversed(lines):
            line = line.strip()
            if not line or '"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant" or entry.get("isSidechain"):
                continue
            message = entry.get("message") or {}
            usage = message.get("usage") or {}
            if not usage:
                continue
            total = (
                usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
                + usage.get("output_tokens", 0)
            )
            return total, str(message.get("model") or "")
        if start == 0:
            break
    return None, ""


def window(model, tokens):
    configured = option("CONTEXT_WINDOW")
    if configured and configured > 0:
        return int(configured)
    raw = (os.environ.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    names = [model]
    try:
        with open(os.path.expanduser("~/.claude/settings.json")) as f:
            names.append(str(json.load(f).get("model") or ""))
    except (OSError, ValueError):
        pass
    if any("1m" in name.lower() for name in names):
        return 1_000_000
    return 1_000_000 if tokens > 200_000 else 200_000


def human(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    return f"{round(n / 1000)}k"


def measure(payload):
    path = payload.get("transcript_path")
    if not path or not os.path.exists(path):
        return None
    tokens, model = last_usage(path)
    if tokens is None:
        return None
    size = window(model, tokens)
    if size <= 0:
        return None
    return tokens, size, tokens * 100.0 / size


def on_stop(payload):
    measured = measure(payload)
    if not measured:
        return None
    tokens, size, pct = measured
    line = f"ctx {human(tokens)}/{human(size)} · {pct:.0f}%"
    if pct >= threshold("RED_AT"):
        line = RED + line + RESET
    elif pct >= threshold("YELLOW_AT"):
        line = YELLOW + line + RESET
    return {"systemMessage": line}


def already_warned(session, band):
    if not session:
        return False
    directory = state_dir()
    path = os.path.join(directory, f"{session}.json")
    try:
        with open(path) as f:
            seen = json.load(f).get("band", 0)
    except (OSError, ValueError):
        seen = 0
    if band <= seen:
        return True
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"band": band}, f)
    except OSError:
        pass
    return False


def on_prompt(payload):
    measured = measure(payload)
    if not measured:
        return None
    tokens, size, pct = measured
    if pct < threshold("WARN_AT"):
        return None
    band = int(pct // 10) * 10
    if already_warned(payload.get("session_id"), band):
        return None
    text = (
        f"Context is {pct:.0f}% full ({human(tokens)} of {human(size)}). "
        f"The window is running out, auto-compact is coming."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if payload.get("agent_id"):
        return
    event = payload.get("hook_event_name")
    result = on_prompt(payload) if event == "UserPromptSubmit" else on_stop(payload)
    if result:
        json.dump(result, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
