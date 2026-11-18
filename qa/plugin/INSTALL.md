# The QA engine in Claude Code: plugin, gate and ledger

Two parts, wired differently (changed 2026-10-22):

- **Gate and ledger hooks:** command hooks in the workspace's `.claude/settings.json`. They run
  `qa/runner/gate_hook.py` before every tool call and `qa/runner/ledger_hook.py` after it, straight from this workspace.
  They load only in sessions started at the workspace root (`__WORKSPACE__`), after the
  folder is trusted. A session started in a subfolder, such as `qa/plugin`, has no gate.
- **Plugin `wingtra-qa`:** the `/qa-status` pane and the QA skills (`/qa-run`, bug-triage, bug-draft, `/sak-submit`). It
  is listed by the local marketplace `wingtra-qa-local` (`qa/plugin/.claude-plugin/marketplace.json`) and installed at
  project scope. Sessions load the **installed copy** from `~/.claude/plugins/cache/wingtra-qa-local/wingtra-qa/<version>/`,
  not this folder. Edits here change nothing until the copy is updated.

## The gate

Every tool call goes through `qa/runner/gate.py`:
- Denied: raw adb taps, adb without `-s`, `adb kill-server`, `forward --remove-all`, port 9333, the retired tap/CDP
  helpers, raw CDP Input, reading `qa/secrets.env`, rewriting hashed records or the ledger, posting ClickUp comments,
  git writes (push, commit, checkout, switch, reset, stash, worktree add, tag and branch writes), `gh`, GitHub write
  tools, and writes into the WingtraCloud or WingtraPilot clones.
- Asks you first: installs, uninstalls, `pm clear`, `settings put`, Wi-Fi changes, `appops set`, act.py write-list taps,
  other ClickUp writes, ClickUp operators other than `task.get_many`, ssh to webpc044 beyond status reads, git
  commands that are not known reads, and connector tools whose name is a write.
- If `gate_hook.py` cannot start, the hook command asks for every call instead of letting it pass.
- Passes without asking (engine piece 2, 2026-10-26): `act.py <target> deliver <CASE>`. It copies only the test files
  a version of that case pins (the newest, or the one run.py names with `--case-file`), and only to a target the case
  runs on: source under `tests/<folder>/files/` or `qa/cases/fixtures/<CASE>/` with the case's sha256, to
  `/sdcard/Download/…` on a tablet or `~/qa-fixtures/` on webpc044. It checks the sha256 there and never overwrites
  a different file, folder or link (`mv -n`; `qa/runner/fixtures.py`). A raw `adb push` still asks.

The ledger hook writes one line per relevant call to `qa/ledger/<day>.jsonl`, only for sessions whose folder is the
workspace or inside it. Note: PermissionDenied fires only for auto-mode denials, so a "no" in a permission dialog
leaves no PermissionDenied line.

## After any change under qa/plugin (yours)

Bump `version` in `qa/plugin/wingtra-qa/.claude-plugin/plugin.json` (the copy is only replaced when the version
changes), then run in Terminal.app. `claude` is not on PATH, so use its full path, which changes when the app updates:

```
cd "__WORKSPACE__"
CLAUDE="/Users/owner/Library/Application Support/Claude/claude-code/2.1.308/a93ded4b1b47/claude.app/Contents/MacOS/claude"
"$CLAUDE" plugin marketplace update wingtra-qa-local
"$CLAUDE" plugin update wingtra-qa@wingtra-qa-local
"$CLAUDE" plugin list --json
```

The last command must show `wingtra-qa@wingtra-qa-local` with the new `version` and an `installPath` ending in it.
Then start a new session in the workspace (or run `/reload-plugins` in an open one).

First install on a new machine: `"$CLAUDE" plugin marketplace add ./qa/plugin --scope project`, then
`"$CLAUDE" plugin install wingtra-qa@wingtra-qa-local --scope project`.

## Checks

1. In a new session started at the workspace root: `git -C WingtraCloud stash list` is refused with "QA gate: git
   stash changes a repository: the clones are read-only (G1)". It only reads, so it is harmless if the gate is off.
2. `qa/ledger/<today>.jsonl` gains `src: "hook"` lines for that session's relevant calls.
3. `/qa-status` opens the pane and shows TA5, ET65 and webpc044.

## Tests (no device needed)

```
python3 -I qa/runner/tests/test_settings_hooks.py
python3 -I qa/runner/tests/test_gate.py
"$CLAUDE" plugin validate qa/plugin
"$CLAUDE" plugin test qa/plugin/wingtra-qa
```
