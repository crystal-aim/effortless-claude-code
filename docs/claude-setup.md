# Claude Setup Panel

The admin dashboard's **Claude Setup** panel installs Claude Code enhancements directly into `~/.claude/` or a project's `.claude/`. Everything happens locally — no external plugin system is used.

## Sources

- **Everything-CC** (`everything-claude-code`) — auto-install agents, skills, commands, rules, MCP servers, and hooks
- **Awesome-CC** (`awesome-claude-code`) — searchable catalog of external Claude Code tools (view-only, click out to the project)

## Install targets

- **User level** — `~/.claude/` (applies to every project)
- **Project level** — `<your-project>/.claude/` (per-project, absolute path)

## Everything-CC flow

1. **Sync** — clones `affaan-m/everything-claude-code` into `~/.cache/claude-croxy/ecc-repo/`. Nothing is touched in `~/.claude/` yet.
2. **Choose items** — pick a curated preset (Starter / Web Dev / Security / Full) or tick individual items in Browse.
3. **Install** — a dry-run modal shows creates/overwrites; optionally backs up existing files to `*.bak.<timestamp>` before writing.

MCP servers merge into `~/.claude.json` (user) or `<project>/.mcp.json` (project); existing entries are preserved. Hooks add `~/.claude/plugins/everything-claude-code/` as a symlink to the cache and merge entries into `settings.json` (deduped by hook `id`).

## Installed + uninstall

The **Installed** sub-tab lists every tracked install with:

- `modified` badge — on-disk content differs from what was installed (user edited the file)
- `upstream changed` badge — repo has a newer version since install
- `backup` badge — original file was preserved

Uninstall restores backups when available and reverses JSON-merge installs (MCP / hooks) back to their pre-ECC state.

## Export / Import profile

Installed tab has:

- **Export profile** — download a JSON snapshot
- **Import profile…** — apply a snapshot from another machine

Portable bundle includes file items, MCP server IDs, hooks flag, and target preferences.

## Auto-sync

Toggle in the Sync status card — runs `git pull` of ECC + refresh of ACC on an interval (default 24h). State persists in the `settings` table.

## Credits

The Claude Setup panel integrates two upstream community projects. Neither is bundled with this repo — they are fetched on demand and cached under `~/.cache/claude-croxy/`. All credit for the content installed by this panel goes to the original authors.

- **[everything-claude-code](https://github.com/affaan-m/everything-claude-code)** by [Affaan Mustafa](https://github.com/affaan-m) — MIT.
  Source of the 48 agents, 183 skills, 79 commands, 88 rules, 14 MCP server definitions, and the hook suite. Claude Croxy only provides a local installer around it; the definitions themselves are upstream's work.

- **[awesome-claude-code](https://github.com/hesreallyhim/awesome-claude-code)** by [hesreallyhim](https://github.com/hesreallyhim) — MIT.
  Source of the curated external catalog (`THE_RESOURCES_TABLE.csv`). Claude Croxy displays it as a browse-only table that links out to each project.
