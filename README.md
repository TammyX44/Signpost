<h1 align="center">Signpost</h1>

<p align="center">
  Workspaces that name themselves, for <a href="https://omarchy.org">Omarchy</a>.<br>
  See what is on every workspace, and which AI agent needs you, right from your bar.
</p>

<p align="center">
  <img alt="Version 0.5.1" src="https://img.shields.io/badge/version-0.5.1-blue">
  <img alt="MIT license" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Omarchy plugin" src="https://img.shields.io/badge/Omarchy-plugin-orange">
  <img alt="Python 3 and QML" src="https://img.shields.io/badge/Python%203-QML-lightgrey">
</p>

<p align="center">
  <img src="preview.png" alt="Signpost: workspaces that name themselves, showing a bar of named workspaces with a needs-you badge" width="900">
</p>

Signpost replaces Omarchy's workspace numbers with **number + tile + name**: the git repo in your terminal, the project an AI agent is working on, the ssh host, the local model or the web app. Plain terminals and browsers without a project keep their number and logo. When an agent stops to ask for permission, its tile pulses amber and an alert with a **Go to it** button jumps straight to that workspace.

## Highlights

- **Real names, nothing guessed.** Every name comes from an open folder, git repo, app, ssh host, local model or pin you set.
- **Agent status at a glance.** Claude Code works with no setup. Codex and OpenCode report through optional hooks; other agents show working and finished from CPU use.
- **Project switcher.** Middle-click the bar (or bind a key) to fuzzy-search open, recent and indexed projects (`~/Projects` + `zoxide`), focus a window or open your editor with Enter, or launch a terminal, editor or agent in one keystroke.
- **Terminal command status.** Long builds and test runs get a badge; an optional shell snippet adds pass and fail colour.
- **Theme-aware, local and light.** Tile colours come from your Omarchy theme. Everything runs locally in one Python file with no dependencies.

## Install

```sh
omarchy plugin add https://github.com/TammyX44/Signpost --enable
```

Enabling Signpost puts it in the place of Omarchy's workspace numbers: it is declared as a clone of `omarchy.workspaces`, so Omarchy swaps the two and keeps that widget's position and settings. Disabling or removing Signpost puts Omarchy's widget back (Omarchy does that, not Signpost). Check that the engine is running:

```sh
omarchy-shell signpost status
# engine up · 6 workspaces · claude: agents view
```

### Update

```sh
omarchy plugin update io.github.tammyx44.signpost
```

### Uninstall

Remove any optional agent hooks first, then the plugin (Omarchy puts its workspace widget back) and its saved state:

```sh
p=~/.config/omarchy/plugins/io.github.tammyx44.signpost/signpost.py
python3 -I "$p" hooks remove                    # add --agent codex or --agent opencode if installed
omarchy plugin remove io.github.tammyx44.signpost --yes
rm -rf ~/.local/state/signpost                  # pins, aliases, colours and recent projects
```

Skipped `hooks remove`? Nothing breaks: a hook installed by Signpost 0.5.1 or later checks that `signpost.py` is still there and otherwise exits quietly, so your agents are never blocked (hooks from 0.5.0 lack that check: run `hooks install` once to update them, or `hooks remove` before removing the plugin); remove the leftover `signpost` entries from the agent's hook config when convenient. The same goes for the optional shell snippet from 0.5.1 on: it does nothing once Signpost is gone (print it again with `shell-init` to replace an older one), and can be deleted from your shell startup file.

## Works with

| Category | Supported |
|---|---|
| **AI agents** | Claude Code (live, no setup + optional hook), Codex CLI (optional trusted hook), OpenCode 2.x (experimental plugin); CPU fallback for Gemini CLI, Aider, Goose, Crush, Amp, Cursor Agent, pi |
| **Terminals and tmux** | kitty (live), Alacritty, foot, Ghostty, WezTerm, GNOME Console, Konsole, and the active tmux pane |
| **Editors and agent apps** | Neovim, Vim, Helix, VS Code (live), Cursor, Zed, JetBrains, and T3 Code (live) |
| **Web apps, ssh, models** | Omarchy web apps in Chromium-based browsers (live: Brave), Figma, `ssh · host`, Ollama, `llama.cpp`, vLLM |

Items marked "live" were checked on the author's machine; the rest are covered by recorded fixtures in [`tests/`](tests). See [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md) and [`docs/QA.md`](docs/QA.md).

## Using Signpost

| On a workspace | What happens |
|---|---|
| Left-click | Switch to it; on the focused workspace, cycle its layout like the stock widget |
| Middle-click | Toggle the project switcher on that monitor |
| Right-click | Pin a name, rename the project everywhere, reset a rename, or return to automatic |
| Hover | See what is open there and why it got its name |
| Scroll | Next or previous workspace |

### The badge and the alert

<p align="center">
  <img src="assets/screenshots/bar.png" alt="Close-up of the Signpost bar showing working, needs you, finished and failed badges" width="900">
</p>

- **Blue spinning arc**: an agent or terminal command is working.
- **Amber pulsing dot**: an agent needs you (pings every 2.5 s for 30 s, then every 10 s).
- **Green dot**: finished on a workspace you are not looking at. For terminal commands, green and red need the opt-in shell snippet; without it, a finished command shows a hollow grey ring so a failed build never looks green.
- **Desktop alert**: when a supported agent reports a permission request on another workspace, one notification offers a **Go to it** button. Alerts pause during screen sharing, and motion stops with `omarchy toggle animations`.

<p align="center">
  <img src="assets/screenshots/alert.png" alt="Signpost bar with Workspace 2 pulsing amber and the Omarchy desktop alert offering a Go to it button" width="860">
</p>

### The project switcher

<p align="center">
  <img src="assets/screenshots/switcher.png" alt="The Signpost v0.5 project switcher filtered to ar, showing ranked results across Recent, Open and Projects with the Editor action chip" width="760">
</p>

Middle-click anywhere on Signpost in the bar, run `omarchy-shell signpost projects`, or bind `signpost.projects` in `~/.config/hypr/bindings.lua`. With an empty query, waiting projects sort to the top, followed by open, recent and indexed projects (from `projectRoots`, `~/Projects` by default, plus `zoxide` when installed). Typing ranks exact, word-start, fuzzy and substring hits across names, branches, paths and workspace keycaps (type `needs` or `wait` for waiting agents). Press <kbd>Enter</kbd> to focus an open window or open a closed project in your editor (`omarchy-launch-editor`), <kbd>Ctrl</kbd>+<kbd>T</kbd> for a terminal, <kbd>Ctrl</kbd>+<kbd>E</kbd> for your editor, <kbd>Ctrl</kbd>+<kbd>G</kbd> for an agent (`claude` by default), <kbd>Ctrl</kbd>+<kbd>R</kbd> for the `origin` remote (https only), and <kbd>Esc</kbd> to close.

See [`docs/SETTINGS.md`](docs/SETTINGS.md) for all `omarchy-shell signpost` commands, suggested keybindings, settings, 0.3 key migration, and custom rules or logos.

## Optional agent hooks

Claude Code works out of the box via `claude agents --json`. Optional hooks make Claude Code instant, add exact permission alerts for Codex CLI, and add experimental support for OpenCode 2.x. Each installer merges into your existing config, keeps your other hooks and saves the original once as `*.signpost-backup`:

```sh
p=~/.config/omarchy/plugins/io.github.tammyx44.signpost/signpost.py
python3 -I "$p" hooks status
python3 -I "$p" hooks install                   # add --agent codex or --agent opencode
```

See [`docs/AGENTS.md`](docs/AGENTS.md) for per-agent setup, opt-in terminal pass/fail snippets (`shell-init`), and opt-in unread counts.

## Privacy

Everything runs locally and nothing is sent anywhere. Files written by Signpost are readable by you only (folders `0700`, files `0600`), and Signpost doesn't put project names, folders or agent questions on command lines, where other accounts could read them: alerts go straight to the notification service over D-Bus, and git runs in the project folder rather than being told it. (The one exception is a terminal you open from the switcher, which is told its folder.) The switcher runs `git` only while open, with hooks, `core.fsmonitor`, external diff, textconv, filters and transports switched off, and not at all when a repo's config can't be read in full (needs git 2.40+, or set `switcherGit` to `never`). Turn off `showQuestions` to keep prompt text out of alerts and UI. Names hide and alerts close while the screen is captured: a screen share (including one already running when Signpost started, seen through `pw-dump` from PipeWire), Omarchy's screen recorder or remote desktop. See [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md) for the full file list and git safety details.

## Requirements

- Omarchy with its Quickshell bar and Hyprland's Lua config (checked on Omarchy 4.5 and Hyprland 0.57.2).
- `python3` 3.10+ (no Python packages) and `git` 2.40+ (for the switcher's git line).
- `busctl` and `gdbus` for alerts (shipped with Omarchy).
- Claude Code with `claude agents` for hook-free "needs you" (checked with 2.1.318).

Signpost installs no packages and never asks for elevated privileges.

## Documentation

- [`docs/SETTINGS.md`](docs/SETTINGS.md): settings reference, 0.3 key migration, commands, keybindings, custom rules and logos
- [`docs/AGENTS.md`](docs/AGENTS.md): Claude Code, Codex and OpenCode hooks, terminal command status, unread counts
- [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md): naming order, tiles and colours, engine details, privacy, files written, known limits, development
- [`docs/QA.md`](docs/QA.md): release QA checklist

## License

[MIT](LICENSE). The bundled Claude, OpenAI and Ollama logos are listed in [`assets/icons/ATTRIBUTION.md`](assets/icons/ATTRIBUTION.md); they belong to their owners and are used only to identify the program running in a workspace.
