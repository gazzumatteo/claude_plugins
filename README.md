# Gazzumatteo Claude Plugins

Claude Code plugins and skills maintained by [Matteo Gazzurelli](https://github.com/gazzumatteo).

## Plugins

| Plugin | Category | Description |
|---|---|---|
| [e2e-browser-testing](./plugins/e2e-browser-testing) | testing | Execute E2E test checklists (markdown) via browser automation with Playwright |

## Install

Add this marketplace to Claude Code:

```bash
claude plugin marketplace add git@github.com:gazzumatteo/claude_plugins.git
```

Then install individual plugins:

```bash
claude plugin install e2e-browser-testing@gazzumatteo-claude-plugins
```

## Requirements

- Claude Code CLI
- [Playwright MCP plugin](https://github.com/anthropics/claude-plugins-official) installed and enabled (for `e2e-browser-testing`)
- Python 3.10+ (for embedded scripts)

## Contributing

Each plugin lives under `plugins/<name>/` and is self-contained. Add new plugins by:

1. Creating `plugins/<name>/.claude-plugin/plugin.json`
2. Adding commands/agents/hooks/skills under the plugin directory
3. Registering the plugin in `.claude-plugin/marketplace.json`

See `CHANGELOG.md` for release history.
