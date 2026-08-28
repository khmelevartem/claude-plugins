# khmelev-plugins

A Claude Code plugin marketplace. Two plugins, no dependencies beyond `python3`
on `PATH`.

## Install

```
/plugin marketplace add khmelevartem/claude-plugins
/plugin install context-meter@khmelev-plugins
/plugin install no-comments@khmelev-plugins
```

Install only what you need — the plugins are independent. Restart Claude Code
afterwards; `/plugin` updates and removes them later.

## Plugins

| Plugin | What it does |
| --- | --- |
| [context-meter](context-meter/README.md) | Prints context window usage as one line after every turn (`ctx 87k/1M · 9%`), and warns the model before auto-compact. Thresholds are configurable. |
| [no-comments](no-comments/README.md) | Rejects the whole edit when the agent writes a comment or a doc block, so the explanation goes back into the code. Comments carrying a ticket key are let through. |
