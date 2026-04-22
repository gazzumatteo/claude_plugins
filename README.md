# Gazzumatteo Claude Plugins

Claude Code plugins and skills maintained by [Matteo Gazzurelli](https://github.com/gazzumatteo).

## Plugins

| Plugin | Category | Description |
|---|---|---|
| [e2e-testing](./plugins/e2e-testing) | testing | Create, validate, and execute E2E markdown test checklists. Browser automation via Playwright. |

## Install

Add this marketplace to Claude Code:

```bash
claude plugin marketplace add git@github.com:gazzumatteo/claude_plugins.git
```

Then install individual plugins:

```bash
claude plugin install e2e-testing@gazzumatteo-claude-plugins
```

## Requirements

- Claude Code CLI
- [Playwright MCP plugin](https://github.com/anthropics/claude-plugins-official) installed and enabled (for `e2e-testing`)
- Python 3.10+ (for embedded scripts)

## Contributing

Each plugin lives under `plugins/<name>/` and is self-contained. Add new plugins by:

1. Creating `plugins/<name>/.claude-plugin/plugin.json`
2. Adding commands/agents/hooks/skills under the plugin directory
3. Registering the plugin in `.claude-plugin/marketplace.json`

See `CHANGELOG.md` for release history.
