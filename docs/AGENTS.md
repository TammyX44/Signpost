# Agent hooks, terminal commands and unread counts

Claude Code works out of the box. The hooks below make status updates faster or add other agents. Each installer merges into your existing config, keeps your other hooks and saves the original once (`*.signpost-backup`). A hook installed by Signpost 0.5.1 or later runs Signpost only while `signpost.py` exists and always exits 0, so it can never block an agent, not even after the plugin was removed without `hooks remove`. Hooks from 0.5.0 show as needing `hooks install` again; run it once (Codex then asks you to trust the updated definitions in `/hooks`).

```sh
p=~/.config/omarchy/plugins/io.github.tammyx44.signpost/signpost.py
python3 -I "$p" hooks status            # what is installed, for every agent
```

## Supported agents

| Agent | How Signpost knows | How exact |
|---|---|---|
| **Claude Code** | Claude Code's own session list (`claude agents --json`), plus an optional hook | Exact working, needs you and finished. Needs you works with no setup; the hook makes it instant instead of a few seconds behind |
| **Codex CLI** | Optional trusted lifecycle hooks | Exact permission requests once installed and trusted. Checked against 0.171.0 help, the official docs and fixtures |
| **OpenCode 2.x** (experimental) | Optional server plugin | Permission and execution events. Checked against the official docs, the 2.3.2 executable and fixtures |
| Gemini CLI, Aider, Goose, Crush, Amp, Cursor Agent, pi | CPU use of the agent's processes | Inferred working and finished. Codex and OpenCode fall back to this without their hook |

With several kinds of agents running, the desktop alert title names the agent, for example "Lumen · Codex needs you".

## Claude Code hook

Without setup, Signpost reads Claude Code's own session list, so **needs you** appears a few seconds after Claude Code asks. The hook makes it instant and adds the question's words for terminal sessions.

```sh
python3 -I "$p" hooks install
python3 -I "$p" hooks remove            # undo
```

`install` writes only to `CLAUDE_CONFIG_DIR` or `~/.claude`; add `--config-dir DIR` for another folder. `status` lists every `~/.claude*` folder and flags entries from an older Signpost or pointing at a missing `signpost.py` (run `hooks install` again to replace them). `remove` cleans `~/.claude` and any other `~/.claude*` folder that still has Signpost entries. Restart running Claude Code sessions afterwards. An install from Signpost 0.3 shows as "partly installed", and `hooks install` adds the new events without duplicating the old ones.

The hook listens to `UserPromptSubmit`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Notification`, `Elicitation`, `ElicitationResult`, `Stop`, `StopFailure` and `SessionEnd`, as documented in the [hooks reference](https://code.claude.com/docs/en/hooks) for Claude Code 2.1.318. It writes one small file per session, takes about 65 ms, prints nothing and never fails.

## Codex CLI

```sh
python3 -I "$p" hooks install --agent codex
python3 -I "$p" hooks remove --agent codex   # undo
```

Then open `/hooks` in Codex, review the Signpost definitions and trust them. Codex skips new or changed hooks until you approve them. The installer merges `$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`); use `--config-dir DIR` for a different Codex home. Signpost only reports the request. The approval decision always stays with Codex. See the [Codex hooks docs](https://developers.openai.com/codex/hooks).

## OpenCode 2.x (experimental)

```sh
python3 -I "$p" hooks install --agent opencode
python3 -I "$p" hooks remove --agent opencode   # undo
```

This places a plugin at `plugins/signpost/index.ts` in your OpenCode config folder (`OPENCODE_CONFIG_DIR` or the XDG default; `--config-dir DIR` for another). Reload OpenCode's server to load it. It listens for permission and execution events and uses each event's directory to match a session to a workspace. See [OpenCode server plugins](https://opencode.ai/docs/build/plugins/server).

## Terminal command status

Signpost samples foreground commands in terminal panes. Builds and tests listed in [`rules.json`](../rules.json) show as soon as they are seen; any other foreground program shows after `longCommandSeconds` (30 by default). Shells, editors, agents, REPLs, remote sessions and common dev servers are left out. A finished command on another workspace keeps its badge until you look there.

Pass and fail are opt-in. Print the snippet for your shell, read it, and add it to your interactive shell startup file if you want it:

```sh
python3 -I "$p" shell-init bash         # or zsh, fish
```

The snippet writes the executable name, folder, timestamps and exit code to a private file. The folder is read from the recorder's own working directory, never passed on its command line, and the snippet does nothing once `signpost.py` is gone. It keeps `$?` (zsh) and `$_` (bash) intact for your prompt. Without the snippet, a finished command shows a hollow grey ring rather than green, so a failed build never looks green. Compound commands and jobs that finish between polls can end up without an outcome.

To add your own rules, put `commands` entries (an anchored `pattern` and a `kind` of `build`, `test` or `long`) and `commandIgnore` regexes in `~/.config/signpost/rules.json`. Changes apply within a second.

## Unread counts

Select **WhatsApp web app** under **Activity · Unread counts** to show counts like `(3) WhatsApp`. The rule matches the dedicated WhatsApp web-app window from any Chromium-based browser and profile, and the number is capped at `99+`.

To add another app, give it a key under `unread` in `~/.config/signpost/rules.json` with a `class` regex for its window class and an `unread` regex with a named `count` group, then add that key to `unreadApps`.

See also [`README.md`](../README.md), [`docs/SETTINGS.md`](SETTINGS.md), and [`docs/HOW-IT-WORKS.md`](HOW-IT-WORKS.md).
