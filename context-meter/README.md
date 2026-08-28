# context-meter

Prints context window usage as one line after every turn, and warns the model
when the window is running out.

```
ctx 87k/1M · 9%
```

Below the yellow threshold the line has no formatting; above it the line is
yellow, above the red threshold it is red.

## Install

```
/plugin marketplace add khmelevartem/claude-plugins
/plugin install context-meter@khmelev-plugins
```

## Configuration

Prompted when the plugin is enabled; change it later in `/plugin` → the plugin →
configuration, then run `/reload-plugins`.

| Option | Default | Meaning |
| --- | --- | --- |
| Context window, tokens | detected | Total window size. Detected from the model name; set it explicitly if the percentages look wrong. |
| Yellow from, % | 20 | |
| Red from, % | 30 | Takes precedence over yellow. |
| Warn the model at, % | 40 | Set to 100 to disable the warning. |

Values live in your `~/.claude/settings.json` under
`pluginConfigs["context-meter@khmelev-plugins"].options`.

The warning is sent once per 10% band (40, 50, 60 …), not on every prompt, so it
does not fill the window it is warning about. Session state is kept in
`~/.claude/cache/context-meter/`; deleting it makes the warnings fire again.

## Auto-compact

The plugin only reports; it does not change when compaction happens. Set that
yourself in `~/.claude/settings.json`:

```json
{ "autoCompactWindow": 633000 }
```

Range 100000–1000000. Compaction fires at `autoCompactWindow − 33000`, so 633000
means it fires at 600000 tokens.

## Requirements

`python3` on `PATH`.
