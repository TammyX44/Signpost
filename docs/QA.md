# Release QA checklist

What to check before a release, and how far each item was checked before the first public release
(November 2026, three polish rounds). Everything "live" was on one machine: Omarchy 4.5, Hyprland
0.57.2, one 1920×1080 laptop screen at scale 1.25, horizontal bar at the top, one dark theme
(japan-night), git 2.56.1, Claude Code 2.1.318. No check moved the pointer or typed, except where a
row says so.

Legend: **live** seen on the real bar · **offscreen** rendered with the real QML files and Omarchy's
real `Commons`/`Ui` modules in an offscreen Quickshell, popup windows stubbed, a fake engine that uses
the real settings code · **tests** unit tests in `tests/` · **docs** checked against the official
documentation only · **needs a human** not checked yet.

## Bar

| Check | Status |
|---|---|
| Names, tiles and the focus pill, dark theme | live |
| `names: smart` (default): plain app workspaces show number + logo, projects keep names | live (T3 Code, VS Code), offscreen |
| `names` all / focused / none, `style` logos / mono / minimal, `density` compact, `showNumbers` false, `showMore` false, `plainWorkspaces` | live (each set by a JSON edit, applied without a restart, then removed), offscreen |
| `names: hover` | offscreen for the not-hovered look; the hover itself **needs a human** (no pointer) |
| Old 0.3 keys read when the new ones are absent (`labelMode`, `projectTiles`, `iconStyle`, `busyDot`, `notifyWhenWaiting`, `holdSeconds`) | tests; live (`labelMode: all` + `projectTiles: false` gave names on all and the logo row) |
| Widths predicted by `cellWidth()` equal the drawn cells | offscreen: 16 bar configurations × 8 workspaces, 0 mismatches |
| `fit` shrinks the least used names first | offscreen |
| Logos tinted by the theme (`style: mono`, the Ollama and OpenAI logos) | live; they don't draw offscreen (the software renderer skips the effect) |
| Three agent states on a tile, with `badgeStates` subsets and `agentBadge` off | offscreen; working and needs-you live |
| Reduced motion (`omarchy toggle animations`): spinner, ping and busy dot still | code only (`Style.reduceMotion`); **needs a human** (the toggle wasn't flipped on the user's desktop) |
| Calmer motion: spinner 1.6 s a turn; ping every 2.5 s for 30 s, then every 10 s | code; the first 30 s seen live; the 10 s phase **needs a human** |
| Engine down: plain numbers, a small ring after them, the reason on hover | offscreen (a fake engine that exits); not live on purpose (the user's engine was not killed) |
| Light theme | offscreen in round 2; not re-rendered this round; **needs a human** live |
| Vertical bar (numbers only) | code path unchanged; **needs a human** |
| Multi-monitor | **needs a human** (one screen here) |
| `hideWhenSharing` | live (a `grim` screenshot counts as a screencast; the setting was set to `false` by a JSON edit while taking screenshots and removed afterwards) |

## Agent status and the alert

| Check | Status |
|---|---|
| Hook event mapping: PermissionRequest at once, Elicitation, idle_prompt, unrelated notifications ignored | docs (hooks reference for 2.1.318), tests |
| The delayed `permission_prompt` notification after PermissionRequest is not a second question | live (fed to the installed hook: one alert, one claim), tests |
| `claude agents --json`: real output read, ~120 ms, ~0.13 s CPU, per `CLAUDE_CONFIG_DIR` | live (T3 Code sessions: `busy`, and later `idle`) |
| `waiting` / `idle` from that list, `waitingFor` texts | docs (agent view reference) and test fixtures only: no real session could be made to wait |
| Merge rules (list vs hook) and precedence | tests |
| "Seen after 3 s" per session (by process), T3 Code and kitty in one project told apart | tests; **needs a human** live (would need moving the user's focus) |
| Hook-only needs-you: badge, switcher row first with its age, alert with the question and Go to it | live (temp kitty on workspace 5 in a fictional repo, events piped to the installed hook, cleaned up after) |
| Go to it jumps to the workspace | live in round 2 (by the lead); unchanged code path |
| Exactly one alert with two bars (one engine per monitor), and Go to it jumps once | tests: two engines in one process, and two real processes; **needs a human** on two monitors |
| `omarchy-shell signpost next` | live with nothing waiting ("nothing is waiting for you"); the jump itself **needs a human** (it would move the user's focus); it uses the switcher's jump |
| `hooks status` / `install` on an install from 0.3 (partly installed, no duplicates) | tests (temp config folders only, never `~/.claude*`) |
| `status`: `engine up · N workspaces · claude: …` | live, tests |
| `showQuestions: false` keeps the words out of alert, hover card and switcher | tests |
| Agent session files and folder are 0600 / 0700 | tests, live |

## Git safety

| Check | Status |
|---|---|
| Hooks, fsmonitor, external diff, textconv, diff `command`, clean / smudge / process filters (from `.gitattributes`, `.git/info/attributes`, `include.path`, `includeIf`), lazy fetch in a partial clone with `core.sshCommand`, `credential.helper` | tests: each with a control that springs the trap with plain git, then `git_details()` doesn't |
| A Git LFS-style required filter still gives a git line | tests |
| Submodule configs | by design (`--ignore-submodules=all`); no test |

## Switcher

| Check | Status |
|---|---|
| Waiting projects first, oldest first; age in the pill; the question on its own line | live, offscreen |
| `switcherGit` selected / always | offscreen; selected live |
| `switcherGit: never` runs no git; `switcherRecents` off; `recentIgnore` | tests |
| Keyboard and Ctrl actions | live in round 2 (by the lead, typing from an empty workspace); unchanged |

## Hover card

| Check | Status |
|---|---|
| The first sentence says what and where; "why this name" only with a choice; no legend | offscreen, tests (sentence) |
| Wait age in the status pill | offscreen |
| On the real bar under the pointer | **needs a human** |

## Install and the bar hint

| Check | Status |
|---|---|
| `swap` / `unswap` on copies of shell.json: stock widget, a clone, none; undo; refusing without a record | tests (temp files only; never the real shell.json) |
| The Replace / Keep both offer: once ever, never changes the bar by itself | tests |
| The hint in the hover card | offscreen; the buttons **need a human** |
| `omarchy plugin add` + `signpost.py swap` reports `engine up` | live |

## Always

- `python3 -I -m unittest discover -s tests` passes.
- `node --test tests/test_rank.js` passes.
- `omarchy plugin validate .` passes.
- `omarchy-shell signpost status` starts with `engine up`.

## Round 3b

| Check | Status |
|---|---|
| Codex CLI 0.171.0 hook and trust contract | Installed CLI help and [official hooks reference](https://developers.openai.com/codex/hooks). Lifecycle fixtures, neutral Stop result, backup, repeat install and removal in scratch |
| OpenCode 2.3.2 permission and execution event shapes | Installed executable and [V2 server plugin docs](https://opencode.ai/docs/build/plugins/server). Scratch install and replay through the adapter. Live server delivery unverified |
| Mixed agent label on the tile, hover, switcher and alert | Engine tests and offscreen fixtures. Real notification delivery and Go to it need a live desktop |
| Running build/test, unseen completion, clearing after focus | Engine tests and offscreen states. Live job and focus transitions still need testing |
| Opt-in pass/fail | Matching tests with stale and mismatched records. Isolated Bash fixture produced private exit status files for true and false. Zsh and Fish runtime behavior still needs checking |
| Unread title parsing, ordinary browser exclusions, duplicate windows, cap and `showMore` | Parser tests and offscreen rendering. Class verified from the WhatsApp launcher. Live title coverage still needs checking |
| Bar width prediction | Offscreen, 16 configurations × 8 workspace cells with zero mismatches |
| Failed badge and unread pill | Offscreen dark and light samples. Badge on ring 6.44:1 / 4.62:1, ring on tile 7.36:1 / 5.24:1, unread pill on surface 3.16:1 / 3.04:1, pill text 5.40:1 / 6.12:1 |
| Engine CPU | 12-window fixture replay, 0.803 ms CPU per poll and snapshot vs 0.754 ms for the installed version. Live process probing excluded |

The desktop socket returned `Couldn't set socket timeout (2)` and `omarchy-shell` was unavailable
in the round 3b environment. Round 3b was verified offscreen and in scratch directories.

## Round 4 (review fixes)

Live checks used three throwaway repos in kitty windows opened silently on workspaces 3–5, a Codex
`PermissionRequest` fed to the installed hook, and temporary `shell.json` edits. Everything was
removed afterwards and `state.json`, `recent-projects.json` and `shell.json` were compared
byte-for-byte with backups. The pointer was moved with `ydotool` for the hover cards and put back.

| Check | Status |
|---|---|
| Running build/test spinners, then failed (red, with the Bash snippet) and finished-unknown (hollow grey ring, without it) | live, tests |
| A failed test re-run in one Bash shell (same history number) still reports pass/fail | live (record `history 482` twice), tests |
| A completion seen while the snippet itself holds the foreground is not lost | found live (missed red badge), fixed, live 3 of 3, tests |
| Empty Enter starts no Python; `$_` survives (Bash); `$?` survives (Zsh) | Bash interactive test; Zsh by code and syntax only (zsh not installed) |
| Crowded bar (`maxWidthPercent: 10`): needs-you and failed keep tile and badge, others shrink | live |
| `style: logos`: amber, red, hollow grey dots | live |
| Hover card pill for commands (failed, finished) and agents (needs you · 1m) | live |
| Switcher: needs you first with its question, failed and finished pills | live |
| `omarchy-shell signpost rename`, Reset on a renamed project, disabled Rename for an app | live |
| `status` names Codex (`codex: CPU only` with Codex running and no hook) | live |
| `signpost.py doctor` | live, tests |
| Same repo on two workspaces: badge and alert follow the agent's process | tests only |
| `python3 -m pytest` pass/fail, `python3 -m http.server` ignored | tests only |
| OpenCode cwd cache, second pending permission, abort on unload | tests (engine side), syntax check (plugin); live OpenCode server unverified |
| WhatsApp unread on Brave/Chromium/Vivaldi classes | tests; live title **needs a human** |
| Live rename countdown in the hover card | code only; **needs a human** (needs a focus change mid-hover) |
| `rules.json` hot reload | tests |
| `capture` files and `bar-restore.json` are 0600 | tests |
