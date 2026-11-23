# Settings, commands and customisation

Change settings in your Omarchy settings app, or in the widget's entry in `~/.config/omarchy/shell.json`. Changes apply immediately. In JSON, prefer real `true`/`false` and numbers; Signpost also reads the text values that `omarchy bar set` stores.

## Settings reference

| Key | Default | What it does |
|---|---|---|
| **Bar** | | |
| `names` | `smart` | `smart`: names for projects, agents, hosts, models, title rules and web apps, while a plain app shows its logo · `all` · `focused` · `hover` · `none` (logos only) |
| `fit` | `true` | When names don't fit, the least recently used shrink first: short name, then logo, then number |
| `style` | `tiles` | `tiles` (projects in their colour, apps on a neutral tile) · `logos` · `mono` (logos tinted to the theme) · `minimal` (no icons) |
| `density` | `comfortable` | `compact`: smaller tiles, and a tile stands in for its number |
| `showNumbers` | `true` | Off hides the number where a tile or name shows. <kbd>Super</kbd>+number still works |
| `showMore` | `true` | Other projects on a workspace (`+2`) and enabled unread counts |
| `plainWorkspaces` | | Workspaces that only ever show their number, e.g. `"9, 10"` (Hyprland numbering: 11, 12… on a second monitor) |
| `ignoreClasses` | | Window classes that never name a workspace (comma-separated) |
| **Agents** | | |
| `agentBadge` | `true` | The status badge on tiles |
| `badgeStates` | all | `working`, `needsYou`, `ready`. These also filter terminal commands |
| `alerts` | `true` | The desktop alert when a supported agent needs you |
| `showQuestions` | `true` | Off: the alert, hover card and switcher only say that it needs you, not what it asked |
| `hideWhenSharing` | `true` | Numbers only while your screen is shared |
| **Activity** | | |
| `commandStatus` | `true` | Show terminal command activity |
| `unreadApps` | `[]` | Apps whose title counts can appear |
| **Hover and switcher** | | |
| `hoverCard` | `true` | The card on hover |
| `switcherGit` | `selected` | Git line on `always` every row, the `selected` row, or `never` (git is not run at all) |
| `switcherRecents` | `true` | Recent projects in the switcher |
| `projectRoots` | `~/Projects` | Comma-separated folders whose immediate subfolders are indexed when the switcher opens (empty turns the index off; merges `zoxide` when installed) |
| `recentIgnore` | `/tmp/*, ~/.cache/*` | Folders never listed as recent or indexed (comma-separated, `*` matches anything) |
| `agentCommand` | `claude` | What the switcher's Agent action runs |
| **Advanced** | | |
| `maxWidthPercent` | `40` | How much of the screen width names may take |
| `maxLength` | `12` | Longer names are cut with "…" (`repo · label` keeps the label) |
| `longCommandSeconds` | `30` | Seconds before other foreground commands show as running |
| `renameDelay` | `5` | Seconds before a focus or background change renames a workspace |
| `sources` | all | `agents`, `projects`, `ssh`, `models`, `titleRules`, `webapps`, `apps` |

## Coming from Signpost 0.3

Old keys are still read when the new key is absent, and your `shell.json` is never rewritten.

| 0.3 key | Now |
|---|---|
| `labelMode` `fit` / `all` / `focused` / `hover` | `names` `smart` (+ `fit` on) / `all` / `focused` / `hover` (+ `fit` off) |
| `iconStyle` + `projectTiles` | `style`: `none` → `minimal`; `mono` or `glyph` → `mono`; `color` with tiles off → `logos`; otherwise `tiles` |
| `busyDot` | `agentBadge` |
| `notifyWhenWaiting` | `alerts` |
| `holdSeconds` | `renameDelay` |

## Custom rules and logos

Your own rules (more agents, title rules, terminals, short app names, command rules or unread rules) go in `~/.config/signpost/rules.json`, in the same shape as [`rules.json`](../rules.json). Lists are prepended and objects are merged; changes apply within a second.

For a custom logo, drop an SVG or PNG into `~/.config/signpost/icons/` named after the icon it replaces (`openai.svg` for Codex, `firefox.png` for Firefox).

## Commands and keybindings

```sh
omarchy-shell signpost projects         # open the project switcher
omarchy-shell signpost next             # jump to the workspace that has waited for you longest
omarchy-shell signpost explain          # why each workspace has its name (handy in bug reports)
omarchy-shell signpost rename           # the right-click editor for the focused workspace
omarchy-shell signpost pin "inbox"      # pin a name to the focused workspace
omarchy-shell signpost unpin
omarchy-shell signpost alias "admin"    # rename the focused workspace's project, everywhere
omarchy-shell signpost unalias          # back to the project's own name
omarchy-shell signpost status           # engine state, and which agent adapters are in use
```

Suggested keys for `~/.config/hypr/bindings.lua` (pick combinations you are not using yet):

```lua
o.bind("SUPER + ALT + P", "Signpost project switcher", { ipc = "signpost.projects" })
o.bind("SUPER + ALT + N", "Go to the agent that needs me", { ipc = "signpost.next" })
o.bind("SUPER + ALT + R", "Name this workspace", { ipc = "signpost.rename" })
```

In the rename editor, double-click a suggestion to pin it at once. A renamed project gets a **Reset** button that puts its own name back.

### Project switcher keys

Open the switcher by middle-clicking anywhere on Signpost in the bar, running `omarchy-shell signpost projects`, or binding `signpost.projects`.

| Key | Does |
|---|---|
| <kbd>↑</kbd> <kbd>↓</kbd> | Move |
| <kbd>Enter</kbd> | Focus the open window (or workspace), or open a recent or indexed project in your editor (`omarchy-launch-editor`) |
| <kbd>Ctrl</kbd>+<kbd>T</kbd> | Terminal in its folder |
| <kbd>Ctrl</kbd>+<kbd>E</kbd> | Your editor (`omarchy-launch-editor`) |
| <kbd>Ctrl</kbd>+<kbd>G</kbd> | An agent in a terminal in its folder (`agentCommand`, `claude` by default) |
| <kbd>Ctrl</kbd>+<kbd>R</kbd> | Its `origin` remote in the browser (https only) |
| <kbd>Esc</kbd> | Close |

Search ranks exact matches ahead of word-start/prefix, fuzzy subsequences, and substrings across project names, original names, branches, folder paths, and visible workspace keycap numbers. Type `needs` or `wait` to find agents waiting for you.

See also [`README.md`](../README.md), [`docs/AGENTS.md`](AGENTS.md), and [`docs/HOW-IT-WORKS.md`](HOW-IT-WORKS.md).
