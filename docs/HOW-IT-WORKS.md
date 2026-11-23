# How Signpost works

## How names are chosen

For each workspace, Signpost asks the window you used last what it is. The first match wins:

1. **Your pin** (right-click a workspace)
2. **AI agent** → the repo it works in
3. **ssh** → `ssh · host`
4. **Local model** → `Ollama · qwen3`
5. **Terminal or editor folder** → its git repo; worktrees read `repo · label` (`Arc-wt-docs` → `Arc · docs`)
6. **App title rule** → `Obsidian · notes`, `Figma · Design`, the project in VS Code, Zed or JetBrains, the thread's project in T3 Code
7. **Web app** → its name
8. A plain folder, or the **app's own name**. These are weak: a stronger name elsewhere on the workspace wins, and with `names: smart` an app name isn't shown at all.

Other projects on the same workspace show as **+N**. A change in the window you are using renames the workspace at once; focus switches and background changes wait `renameDelay` seconds and are dropped if things go back. When kitty or WezTerm can say which pane is active, that pane names the workspace. If tabs disagree and nothing says which one you see, Signpost shows the terminal's name rather than guess.

When the bar runs short of room, workspaces that need you, are working or failed are the last to shrink, and they always keep their tile and badge.

## Tiles and colours

Projects, agents and pins get a letter on a tile in the project's colour. Apps, hosts and models keep their logo on a neutral tile, so colour always means one of your projects. The letter skips leading dates and numbers (`2026-11-19-fix-login` → **F**).

Tile colours come from your theme's `colors.toml` and follow it when you switch. Near-duplicate hues are dropped and a theme with too few is filled in; the 26 themes checked give 5 to 9 tile colours, with letters at 4.5:1 contrast or better. Each new project gets the next colour slot and keeps it (worktrees share their repo's colour; pins and aliases don't change it).

## Agent status

Claude Code's session list (`claude agents --json`) runs only while a Claude Code process is open, in the background, with a 3 second timeout. It runs when a wait starts or ends or a session comes or goes, and otherwise every 2 minutes, and all bars share the result. So "needs you" shows within a second or two, while "finished" from this source can take up to 2 minutes (the hook and CPU use are faster). Codex and OpenCode hook events go into the same session list. Without a hook, Signpost watches the CPU use of the agent and its children, and treats a few quiet seconds as finished.

On several monitors only one bar sends the desktop alert, and alerts pause while you share your screen. With Omarchy's reduced motion (`omarchy toggle animations`) every badge state is still. The hover card and the switcher always spell the state out in words.

## The engine

All the naming lives in [`signpost.py`](../signpost.py): one Python 3 file, no dependencies. The bar widget runs `signpost.py watch` and only draws what it prints. A probe takes a few milliseconds; in a 12-window fixture replay the engine used about 0.8 ms of CPU per poll, roughly 0.04% of one core at a two-second cadence. For debugging:

```sh
python3 -I signpost.py once             # 3  Arc · docs +1
python3 -I signpost.py explain          # why each workspace has its name
python3 -I signpost.py facts --ws 3     # what the probe saw
python3 -I signpost.py capture --ws 3   # save a workspace as a test fixture
python3 -I signpost.py doctor           # what Signpost needs, and which optional parts are set up
```

## Privacy and git safety

Everything stays on your machine. Signpost reads process, git, desktop-entry and terminal-pane data locally, and runs `claude agents --json` (Claude Code's own command) while Claude Code is open. It sends nothing anywhere, and its files are readable by you only (folders 0700, files 0600).

- **Git.** The switcher runs `git` on open and recent project folders only while it is open. A repository can ask git to run programs even on a read (hooks, `core.fsmonitor`, external diff, textconv, filters, transports). Signpost switches every one of these off for each call, and the tests prove it against a repository booby-trapped with each of them. This needs git 2.40 or newer; set `switcherGit` to `never` to run no git at all.
- **Project index.** When the switcher opens and `projectRoots` is non-empty (default `~/Projects`), Signpost lists immediate non-hidden subfolders (capped at 300) and merges `zoxide query --list` when `zoxide` is on `PATH`, honouring `recentIgnore` and de-duplicating by real path against open and recent projects. It checks only for `.git` presence and never runs `git` for indexed folders. Root listings are cached by directory mtime and never run on bar polls.
- **Questions.** When an agent shares what it is asking, the first 160 characters show in the alert, hover card and switcher. Turn `showQuestions` off and the words never leave the engine.
- **Screen sharing.** While Hyprland reports a screen share, names are hidden, the hover card, pin editor and switcher close, and no alerts are sent.
- **Unread counts.** Window titles are read locally, and only the capped count reaches the bar.
- **Captures.** `signpost.py capture` files contain window titles and folder paths. Check them before attaching one to an issue.

## Every file Signpost writes

| Where | What |
|---|---|
| `~/.config/omarchy/plugins/io.github.tammyx44.signpost/` | The plugin |
| `~/.local/state/signpost/state.json` (+ `.lock`) | Pins, aliases, colour slots |
| `~/.local/state/signpost/recent-projects.json` (+ `.lock`) | Up to 30 recent project folders |
| `~/.local/state/signpost/agents/` | One file per hooked session: agent, session id, folder, state, time, process id and any question text |
| `~/.local/state/signpost/commands/` | With the optional snippet, one private file per reporting shell |
| `~/.local/state/signpost/bar-restore.json` | The bar entry Signpost replaced, after Replace or `swap` |
| `~/.local/state/signpost/bar-hint.json`, `claims/` | That you chose Keep both; that the Replace offer was made |
| `~/.local/state/signpost/captures/` | Only when you run `capture` |
| `$XDG_RUNTIME_DIR/signpost/` | Claude Code's shared session list, alert claims and alert pictures; cleared at logout |
| `~/.config/omarchy/shell.json` | Only on Replace, `swap`, `unswap` or the installer |
| Claude Code `settings.json`, Codex `hooks.json`, OpenCode `plugins/signpost/index.ts` (+ backups) | Only on an explicit `hooks install` or `hooks remove` |

## Known limits

- **Needs you** comes from Claude Code's session list or hook, Codex's trusted `PermissionRequest` hook, or OpenCode's experimental permission events. Some prompts, including free-form questions, send no signal at all.
- Command status samples foreground jobs, so a job that starts and ends between polls can be missed.
- Claude Code's session list can report "busy" long after a session stopped, so Signpost never takes "working" from it. Working comes from the hook or from CPU use.
- When a wait is known only from the hook, pressing <kbd>Esc</kbd> on the prompt sends no event. The question counts as seen once you have spent 3 seconds in that session's window.
- Checked live on one machine: a single 1920×1080 laptop screen at scale 1.25 with a dark theme. Multi-monitor, vertical bars and light themes are covered by code, tests and offscreen renders, not live. See [`QA.md`](QA.md) for the full checklist.
- Colours are assigned per machine, so the same project can get a different colour elsewhere.

## Project structure

```text
Signpost.qml          Bar widget entry point
signpost.py           The engine: naming, agent status, hooks, the bar swap
rules.json            Built-in agents, title rules, terminals, app names and command rules
manifest.json         Omarchy plugin manifest (id, version, entry point, settings schema)
icon.png              Plugin and notification icon
preview.png           Marketplace preview
components/           Hover card, project switcher, pin editor, tiles, colours, icons
i18n/                 Interface strings
assets/               Logos, bundled agent icons, screenshots, the OpenCode plugin template
tests/                Engine, switcher ranking and QML tests, plus recorded fixtures
docs/                 Settings, agent hooks, architecture notes and QA checklist
```

## Development

```sh
python3 -I -m unittest discover -s tests   # engine tests
node --test tests/test_rank.js             # switcher ranking tests
tests/run-qml-smoke.sh                     # every QML file loads against the real Omarchy shell
omarchy plugin validate .
```

The Replace button and `signpost.py swap` / `unswap` share the same bar-swap code, keeping the replaced entry, with its settings and position, in `~/.local/state/signpost/bar-restore.json`.

Bug reports are welcome. Please include the output of `omarchy-shell signpost explain`.

See also [`README.md`](../README.md), [`docs/SETTINGS.md`](SETTINGS.md), [`docs/AGENTS.md`](AGENTS.md), and [`docs/QA.md`](QA.md).
