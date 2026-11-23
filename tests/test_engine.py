"""Signpost engine tests. Run: python3 -I -m unittest discover -s tests   (from the repo root)

Fixtures in tests/fixtures/ are per-window facts (what the probe saw) plus the names
they must produce. `signpost.py capture` writes new ones from a real desktop."""

import glob
import io
from contextlib import redirect_stdout
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("signpost", os.path.join(ROOT, "signpost.py"))
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

RULES = sp.load_rules(user_file="/nonexistent")


def load_fixture(path):
    with open(path) as f:
        text = f.read().replace("$HOME", sp.HOME)
    return json.loads(text)


class Helpers(unittest.TestCase):
    def test_cut_keeps_worktree_label(self):
        self.assertEqual(sp.cut("Starling · d1", 12), "Starli… · d1")
        self.assertEqual(sp.cut("omarchy-plugin-ideas", 12), "omarchy-plu…")
        self.assertEqual(sp.cut("short", 12), "short")
        self.assertEqual(sp.cut("Obsidian · Starling Plans", 12), "Starling Pl…")
        self.assertEqual(sp.cut("ssh · build-server", 12), "build-server")

    def test_short_name(self):
        self.assertEqual(sp.short_name("ssh · vps", "ssh"), "vps")
        self.assertEqual(sp.short_name("Ollama · qwen3", "model"), "qwen3")
        self.assertEqual(sp.short_name("omaglimpse", "project"), "omagli…")

    def test_worktree_label(self):
        self.assertEqual(sp.worktree_label("Starling-wt-d1", "Starling", "landing/smooth"), "d1")
        self.assertEqual(sp.worktree_label("Starling-landing", "Starling", "landing/cloudflare"), "landing")
        self.assertEqual(sp.worktree_label("feature-x", "Starling", "landing/smooth"), "smooth")

    def test_ssh_host(self):
        self.assertEqual(sp.ssh_host(["ssh", "vps"]), "vps")
        self.assertEqual(sp.ssh_host(["ssh", "-p", "2222", "dev@vps"]), "vps")
        self.assertEqual(sp.ssh_host(["ssh", "-A", "-i", "~/.ssh/k", "ssh://me@box:22"]), "box")
        self.assertIsNone(sp.ssh_host(["ssh"]))

    def test_model_name(self):
        self.assertEqual(sp.model_name("qwen3:8b"), "qwen3")
        self.assertEqual(sp.model_name("/m/gemma-3-12b-it-Q4_K_M.gguf"), "gemma-3-12b-it")
        self.assertEqual(sp.model_name("llama-3-IQ3_XS.gguf"), "llama-3")

    def test_clean_text(self):
        self.assertEqual(sp.clean_text("a\nb\x07  c"), "a b c")

    def test_pts_path(self):
        self.assertEqual(sp.pts_path((136 << 8) | 3), "/dev/pts/3")
        self.assertIsNone(sp.pts_path(0))

    def test_tidy_class(self):
        self.assertEqual(sp.tidy_class("com.spotify.Client"), "spotify")
        self.assertEqual(sp.tidy_class("com.t3tools.T3Code"), "T3Code")

    def test_webapp_key_ignores_browser_prefix(self):
        self.assertEqual(sp.webapp_key("brave-x.com__home-Default"), "x.com__home-default")
        self.assertEqual(sp.webapp_key("chrome-x.com__home-Default"), "x.com__home-default")
        self.assertEqual(sp.webapp_key("brave-origin-nightly-web.whatsapp.com__-Default"), "web.whatsapp.com__-default")
        self.assertIsNone(sp.webapp_key("com.spotify.Client"))
        self.assertIsNone(sp.webapp_key("brave-origin-nightly"))

    def test_agent_match_is_exact_or_prefix(self):
        agents = RULES["agents"]
        self.assertEqual(sp.match_key("claude", agents), "claude")
        self.assertEqual(sp.match_key("codex-cli", agents), "codex")
        self.assertIsNone(sp.match_key("pi-hole", agents))   # short names never match by prefix
        self.assertIsNone(sp.match_key("claudette", agents))

    def test_icon_overrides_use_named_local_images(self):
        with tempfile.TemporaryDirectory(prefix="signpost icons ") as folder:
            directory = Path(folder)
            for name in ("openai.png", "openai.svg", "org.mozilla.firefox.png", "bad name.png", "notes.txt"):
                (directory / name).touch()
            (directory / "folder.png").mkdir()
            self.assertEqual(sp.icon_overrides(directory), {
                "openai": (directory / "openai.svg").as_uri(),
                "org.mozilla.firefox": (directory / "org.mozilla.firefox.png").as_uri(),
            })

    def test_figma_title_rule_is_scoped_to_the_web_app(self):
        window = {"address": "0xf", "class": "brave-www.figma.com__-Default", "title": "Project — Figma",
                  "app": {"name": "Figma", "icon": "figma", "desktop": None, "webapp": True}}
        selected, _ = sp.window_cands(window, RULES, {})
        self.assertEqual((selected["source"], selected["name"]), ("title", "Figma · Project"))
        other = {**window, "class": "brave-mirror.figma.com.example.org__-Default"}
        selected, _ = sp.window_cands(other, RULES, {})
        self.assertEqual(selected["source"], "webapp")


class GitResolution(unittest.TestCase):
    def test_repo_and_worktree(self):
        with tempfile.TemporaryDirectory() as d:
            main = os.path.join(d, "Starling")
            os.makedirs(os.path.join(main, ".git", "worktrees", "Starling-wt-d1"))
            os.makedirs(os.path.join(main, "src", "deep"))
            with open(os.path.join(main, ".git", "HEAD"), "w") as f:
                f.write("ref: refs/heads/main\n")
            with open(os.path.join(main, ".git", "worktrees", "Starling-wt-d1", "HEAD"), "w") as f:
                f.write("ref: refs/heads/landing/smooth\n")
            wt = os.path.join(d, "Starling-wt-d1")
            os.makedirs(wt)
            with open(os.path.join(wt, ".git"), "w") as f:
                f.write(f"gitdir: {main}/.git/worktrees/Starling-wt-d1\n")
            g = sp.Git()
            r = g.info(os.path.join(main, "src", "deep"))
            self.assertEqual((r["kind"], r["repo"], r["root"], r["branch"]), ("repo", "Starling", main, "main"))
            w = g.info(wt)
            self.assertEqual((w["kind"], w["repo"], w["main"], w["label"], w["branch"]),
                             ("worktree", "Starling", main, "d1", "landing/smooth"))
            self.assertIsNone(sp.Git().info(d))


class NativePanes(unittest.TestCase):
    def setUp(self):
        self.probe = sp.Probe(RULES)
        self.facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "kitty-tabs-one-agent.json"))["facts"][0]
        self.facts["tabs"][1]["pid"] = 5001

    def test_kitty_active_tab_beats_background_agent_and_keeps_it_as_extra(self):
        layout = [{"tabs": [
            {"is_active": True, "windows": [{"pid": 5000, "title": "zsh", "is_active": True}]},
            {"is_active": False, "windows": [{"pid": 5001, "title": "zsh", "is_active": True}]},
        ]}]
        with patch.object(self.probe, "terminal_layout", return_value=layout):
            self.probe.use_terminal_panes(self.facts, "kitty", sp.kitty_panes, "pid", "pid")
        result = sp.decide([self.facts], {}, {}, RULES)[4]
        self.assertEqual((result["full"], result["layer"], result["more"]), ("omaglimpse", "terminal", 1))
        self.assertIn("active pane", result["rows"][0]["detail"])
        self.assertIn("other pane", result["rows"][1]["detail"])

    def test_kitty_shared_pid_requires_unique_title_and_excludes_other_windows(self):
        self.facts["shared"] = True
        panes = [{"pid": pid, "title": "zsh", "is_active": True} for pid in (5000, 5001)]
        layout = [{"tabs": [{"is_active": True, "windows": [pane]}]} for pane in panes]
        with patch.object(self.probe, "terminal_layout", return_value=layout):
            self.probe.use_terminal_panes(self.facts, "kitty", sp.kitty_panes, "pid", "pid")
            self.assertNotIn("activeTab", self.facts)
            panes[1]["title"] = "other window"
            self.probe.use_terminal_panes(self.facts, "kitty", sp.kitty_panes, "pid", "pid")
        self.assertEqual([tab["pid"] for tab in self.facts["tabs"]], [5000])
        self.assertEqual(self.facts["activeTab"], 0)

    def test_title_beats_an_agent_in_a_different_tab(self):
        self.facts["title"] = "~/Projects/omaglimpse"
        result = sp.decide([self.facts], {}, {}, RULES)[4]
        self.assertEqual((result["full"], result["layer"], result["more"]), ("omaglimpse", "title", 1))
        self.assertCountEqual([p["name"] for p in result["_projects"]], ["omaglimpse", "OmaWidgets"])

    def test_unmapped_shared_terminal_does_not_list_another_windows_tabs(self):
        first = {**self.facts, "shared": True, "title": "~/Projects/omaglimpse"}
        second = {**self.facts, "shared": True, "workspace": 5, "address": "0xe1", "title": "zsh"}
        result = sp.decide([first, second], {}, {}, RULES)
        self.assertEqual([p["name"] for p in result[4]["_projects"]], ["omaglimpse"])
        self.assertEqual(result[5]["full"], "kitty")
        self.assertEqual(result[5]["_projects"], [])

    def test_terminal_tabs_in_distinct_worktrees_do_not_agree_on_a_label(self):
        base = "/example/repo"
        for tab, label in zip(self.facts["tabs"], ("docs", "search")):
            tab.update(cwd=base + "-" + label, fg=None, idle=3)
            tab["git"].update(kind="worktree", root=tab["cwd"], main=base, repo="repo", label=label)
        result = sp.decide([self.facts], {}, {}, RULES)[4]
        self.assertEqual(result["full"], "kitty")
        self.assertCountEqual([p["name"] for p in result["_projects"]], ["repo · docs", "repo · search"])

    def test_native_cwd_fills_a_shell_whose_proc_cwd_is_unavailable(self):
        with tempfile.TemporaryDirectory(prefix="signpost pane ") as folder:
            self.facts["tabs"] = [{"pid": 5000, "cwd": None, "git": None, "pts": "/dev/pts/3"}]
            pane = {"pid": 5000, "is_active": True, "cwd": folder}
            layout = [{"tabs": [{"is_active": True, "windows": [pane]}]}]
            with patch.object(self.probe, "terminal_layout", return_value=layout):
                self.probe.use_terminal_panes(self.facts, "kitty", sp.kitty_panes, "pid", "pid")
            self.assertEqual(self.facts["activeTab"], 0)
            self.assertEqual(self.facts["tabs"][0]["cwd"], folder)

    def test_wezterm_uses_numbered_title_with_identical_pane_titles(self):
        self.facts["title"] = "[2/2] zsh"
        layout = [{"window_id": 0, "tab_id": i, "title": "zsh", "is_active": True, "tty_name": tty}
                  for i, tty in enumerate(("/dev/pts/3", "/dev/pts/4"), 7)]
        with patch.object(self.probe, "terminal_layout", return_value=layout):
            self.probe.use_terminal_panes(self.facts, "WezTerm", sp.wezterm_panes, "tty_name", "pts")
        result = sp.decide([self.facts], {}, {}, RULES)[4]
        self.assertEqual((result["full"], result["source"], result["layer"]), ("OmaWidgets", "agent", "terminal"))
        self.assertIn("WezTerm reports the active pane", result["how"])

    def test_wezterm_old_output_accepts_local_cwd_and_skips_remote_cwd(self):
        with tempfile.TemporaryDirectory(prefix="signpost pane ") as folder:
            self.facts["tabs"] = []
            pane = {"window_id": 0, "tab_id": 7, "cwd": "file://remote.example" + folder}
            with patch.object(self.probe, "terminal_layout", return_value=[pane]):
                self.probe.use_terminal_panes(self.facts, "WezTerm", sp.wezterm_panes, "tty_name", "pts")
                self.assertFalse(self.facts["tabs"])
                pane["cwd"] = Path(folder).as_uri()
                self.probe.use_terminal_panes(self.facts, "WezTerm", sp.wezterm_panes, "tty_name", "pts")
            self.assertEqual(self.facts["tabs"][0]["cwd"], folder)

    def test_a_remote_wezterm_pane_cannot_name_a_local_folder(self):
        # An SSH-domain pane: WezTerm reports a tty_name (null: no local terminal), and the remote host
        # sets the folder with OSC 7. It must not become a project here.
        with tempfile.TemporaryDirectory(prefix="signpost pane ") as folder:
            self.facts["tabs"] = []
            pane = {"window_id": 0, "tab_id": 7, "tty_name": None, "cwd": Path(folder).as_uri()}
            with patch.object(self.probe, "terminal_layout", return_value=[pane]):
                self.probe.use_terminal_panes(self.facts, "WezTerm", sp.wezterm_panes, "tty_name", "pts")
            self.assertFalse(self.facts["tabs"])

    def test_failed_adapter_waits_before_retrying(self):
        socket_stat = Mock(st_uid=os.getuid(), st_mode=sp.statmod.S_IFSOCK)
        with patch.object(sp.os, "stat", return_value=socket_stat), patch.object(
            sp.subprocess, "run", side_effect=sp.subprocess.TimeoutExpired("kitten", 0.04)
        ) as run:
            self.assertIsNone(self.probe.terminal_layout(4121, "kitty"))
            self.probe.pane_lists.clear()  # next refresh
            self.assertIsNone(self.probe.terminal_layout(4121, "kitty"))
        run.assert_called_once()


class Fixtures(unittest.TestCase):
    def test_fixtures(self):
        paths = sorted(glob.glob(os.path.join(ROOT, "tests", "fixtures", "*.json")))
        self.assertTrue(paths)
        for path in paths:
            fx = load_fixture(path)
            with self.subTest(fixture=fx.get("name", os.path.basename(path))):
                state = {"pins": {}, "aliases": {}, "recent": [], **(fx.get("state") or {})}
                out = sp.decide(fx["facts"], state, fx.get("settings") or {}, RULES)
                for ws, want in fx["expect"].items():
                    got = out.get(int(ws)) or {"name": None}
                    for key, value in want.items():
                        self.assertEqual(got.get(key, 0 if key == "more" else None), value, f"ws {ws} {key}: {got.get('how')}")


class ProjectTiles(unittest.TestCase):
    """The coloured letter tile: stable per repo, and only for projects of yours."""

    def facts(self, name="t3-title-picks-agent"):
        return load_fixture(os.path.join(ROOT, "tests", "fixtures", f"{name}.json"))["facts"]

    def test_hue_is_stable_and_in_range(self):
        self.assertEqual(sp.hue_of("/home/a/Starling"), sp.hue_of("/home/a/Starling"))
        self.assertEqual(sp.hue_of("/home/a/Starling"), 341)  # a digest, so the same on every machine and run
        for key in ("", "x", "/p/ü", "a" * 500):
            self.assertIn(sp.hue_of(key), range(360))

    def test_hue_separates_similar_paths(self):
        names = ["Starling", "Signpost", "Arc-docs", "Arc-search", "Radar", "omaglimpse", "OmaWidgets", "vps-notes"]
        hues = {sp.hue_of(f"/home/u/Projects/{n}") for n in names}
        self.assertGreaterEqual(len(hues), len(names) - 1)
        self.assertGreater(max(hues) - min(hues), 180)  # not bunched into one colour

    def test_monogram(self):
        self.assertEqual(sp.monogram_of("Starling · d1"), "S")
        self.assertEqual(sp.monogram_of("  _omaglimpse"), "O")
        self.assertEqual(sp.monogram_of("é"), "É")
        self.assertEqual(sp.monogram_of("···"), "•")
        self.assertEqual(sp.monogram_of(""), "•")

    def test_monogram_skips_dates_and_numbers_to_the_first_word(self):
        self.assertEqual(sp.monogram_of("2026-11-19-i-would-like-u-to-f4324d3b"), "I")   # a T3 scratch folder
        self.assertEqual(sp.monogram_of("01_notes"), "N")
        self.assertEqual(sp.monogram_of("2024 · Budget"), "B")
        self.assertEqual(sp.monogram_of("🚀 rocket"), "R")
        self.assertEqual(sp.monogram_of("v2-api"), "V")
        self.assertEqual(sp.monogram_of("3d-printer"), "3")    # a word with letters keeps its own first character
        self.assertEqual(sp.monogram_of("2026-11-19"), "2")    # no letters at all: the first digit
        self.assertEqual(sp.monogram_of("Ölçer"), "Ö")

    def test_project_and_agent_workspaces_are_tiled_and_apps_are_not(self):
        project = sp.decide(self.facts(), {}, {}, RULES)[3]
        self.assertEqual((project["source"], project["monogram"]), ("agent", "S"))
        self.assertIn(project["hue"], range(360))
        app = sp.decide(self.facts("home-terminal-alone"), {}, {}, RULES)[1]
        self.assertEqual(app["source"], "app")
        self.assertIsNone(app["hue"])

    def test_worktree_shares_its_repos_colour(self):
        facts = self.facts()
        main = sp.decide(facts, {}, {}, RULES)[3]
        window = facts[0]
        for agent in window["agents"]:
            if agent["cwd"].endswith("Starling"):
                agent["git"].update(kind="worktree", root=agent["cwd"] + "-d1", label="d1")
                agent["cwd"] += "-d1"
        rows = {row["name"]: row for row in sp.decide(facts, {}, {}, RULES)[3]["_projects"]}
        self.assertEqual(rows["Starling · d1"]["hue"], main["hue"])
        self.assertEqual(rows["Starling · d1"]["monogram"], "S")

    def test_renaming_does_not_recolour(self):
        facts = self.facts("kitty-agent-in-worktree")
        before = sp.decide(facts, {}, {}, RULES)[1]
        key = before["aliasKey"]
        after = sp.decide(facts, {"aliases": {key: "Client app"}}, {}, RULES)[1]
        self.assertEqual(after["full"], "Client app · d1")
        self.assertEqual((after["hue"], after["monogram"]), (before["hue"], "C"))
        self.assertEqual(after["_projects"][0]["monogram"], "S")

    def test_held_name_keeps_its_own_tile(self):
        old = sp.decide(self.facts("kitty-agent-in-worktree"), {}, {}, RULES)
        new = sp.decide(self.facts("kitty-agent-in-worktree"), {"pins": {"1": "Inbox"}}, {}, RULES)
        self.assertNotEqual(old[1]["monogram"], new[1]["monogram"])
        holder = sp.Holder()
        holder.update(old, 0.0, settings={"holdSeconds": 5}, active=None)
        holder.update(new, 1.0, settings={"holdSeconds": 5}, active=None)
        shown = holder.view()[0]
        self.assertEqual((shown["full"], shown["monogram"], shown["hue"]), (old[1]["full"], "S", old[1]["hue"]))

    def test_project_rows_report_busy_only_when_asked(self):
        facts = self.facts()
        for agent in facts[0]["agents"]:
            agent["busy"] = agent["cwd"].endswith("Starling")
        on = {r["name"]: r["busy"] for r in sp.decide(facts, {}, {"busy": True}, RULES)[3]["_projects"]}
        off = {r["name"]: r["busy"] for r in sp.decide(facts, {}, {"busy": False}, RULES)[3]["_projects"]}
        self.assertEqual(on, {"Starling": True, "Signpost": False})
        self.assertEqual(off, {"Starling": False, "Signpost": False})

    def test_recent_projects_are_tiled_too(self):
        row = sp.project_row({"directory": "/p/Radar", "originalName": "Radar", "aliasKey": None, "wt": None, "glyph": "git"},
                             0, "/p/Radar", {})
        self.assertEqual((row["monogram"], row["busy"], row["hue"]), ("R", False, sp.hue_of("/p/Radar")))
        self.assertEqual(row["host"], "")

    def test_app_list_says_what_is_open_most_recent_first(self):
        base = self.facts("home-terminal-alone")[0]
        def win(addr, name, icon, focus):
            return {**deepcopy(base), "address": addr, "focus": focus, "app": {**base["app"], "name": name, "icon": icon}}
        facts = [win("0xd1", "kitty", "kitty", 2), win("0xd2", "Obsidian", "obsidian", 0), win("0xd3", "kitty", "kitty", 1)]
        result = sp.decide(facts, {}, {}, RULES)[base["workspace"]]
        self.assertEqual(result["appList"], [{"name": "Obsidian", "icon": "obsidian", "count": 1},
                                             {"name": "kitty", "icon": "kitty", "count": 2}])
        many = [win(f"0xe{i}", f"App {i}", "x", i) for i in range(12)]
        self.assertEqual(len(sp.app_list(many)), 8)

    def test_open_project_rows_say_which_app_holds_them(self):
        facts = self.facts()
        rows = sp.decide(facts, {}, {}, RULES)[3]["_projects"]
        self.assertEqual({r["host"] for r in rows}, {facts[0]["app"]["name"]})
        twice = [facts[0], {**deepcopy(facts[0]), "address": "0xc2", "workspace": 5, "app": {**facts[0]["app"], "name": "kitty"}}]
        results = sp.decide(twice, {}, {}, RULES)
        here = {r["name"]: r["host"] for r in results[3]["_projects"]}
        there = {r["name"]: r["host"] for r in results[5]["_projects"]}
        self.assertEqual(here["Starling"], facts[0]["app"]["name"])
        self.assertEqual(there["Starling"], "kitty")


class AgentHooks(unittest.TestCase):
    def test_events_map_to_states(self):
        state = lambda **kw: sp.hook_state(kw)
        self.assertEqual(state(hook_event_name="UserPromptSubmit"), "work")
        self.assertEqual(state(hook_event_name="PostToolUse"), "work")
        self.assertEqual(state(hook_event_name="Stop"), "done")
        self.assertEqual(state(hook_event_name="SessionEnd"), "end")
        self.assertEqual(state(hook_event_name="Notification", notification_type="permission_prompt"), "wait")
        self.assertEqual(state(hook_event_name="Notification", notification_type="elicitation_dialog"), "wait")
        self.assertEqual(state(hook_event_name="Notification", notification_type="idle_prompt"), "done")
        self.assertEqual(state(hook_event_name="Notification", message="Claude needs your permission to use Bash"), "wait")
        self.assertIsNone(state(hook_event_name="Notification", message="Claude is waiting for your input"))
        self.assertIsNone(state(hook_event_name="PreToolUse"))
        self.assertIsNone(sp.hook_state({}))
        # Checked against the hooks reference (Claude Code 2.1.318):
        self.assertEqual(state(hook_event_name="PermissionRequest", tool_name="Bash"), "wait")      # at once
        self.assertEqual(state(hook_event_name="Elicitation"), "wait")
        self.assertEqual(state(hook_event_name="ElicitationResult"), "work")
        self.assertEqual(state(hook_event_name="PostToolUseFailure"), "work")
        self.assertEqual(state(hook_event_name="StopFailure"), "done")
        self.assertEqual(state(hook_event_name="Notification", notification_type="elicitation_url_dialog"), "wait")
        self.assertEqual(state(hook_event_name="Notification", notification_type="elicitation_complete"), "work")
        for ignored in ("auth_success", "auth_storage_failure", "agent_needs_input", "agent_completed",
                        "quota_auto_resume_fired", "plugin_notification", "something_new"):
            self.assertIsNone(state(hook_event_name="Notification", notification_type=ignored), ignored)

    def test_permission_request_says_what_it_wants(self):
        msg = lambda **kw: sp.hook_message({"hook_event_name": "PermissionRequest", **kw})
        self.assertEqual(msg(tool_name="Bash", tool_input={"command": "npm test", "description": "Run the tests"}),
                         "Allow Bash: Run the tests")
        self.assertEqual(msg(tool_name="Edit", tool_input={"file_path": "/p/a/src/app.ts"}), "Allow Edit: app.ts")
        self.assertEqual(msg(tool_name="mcp__github__create_issue", tool_input={}), "Allow github create_issue?")
        self.assertEqual(msg(), "Allow a tool?")

    def test_the_same_question_keeps_when_it_was_asked(self):
        with tempfile.TemporaryDirectory() as d:
            ask = {"session_id": "s", "cwd": "/p", "hook_event_name": "PermissionRequest", "tool_name": "Bash",
                   "tool_input": {"command": "ls"}}
            sp.write_hook(ask, d, now=100.0)
            sp.write_hook({**ask, "hook_event_name": "Notification", "notification_type": "permission_prompt",
                           "message": "Claude needs your permission to use Bash"}, d, now=106.0)
            s = sp.AgentSignals(d).read(now=107.0)[0]
            self.assertEqual((s["state"], s["ts"], s["message"]), ("wait", 100.0, "Allow Bash: ls"))
            sp.write_hook({**ask, "hook_event_name": "PostToolUse"}, d, now=108.0)
            sp.write_hook(ask, d, now=110.0)                                  # answered, then asked again: new
            self.assertEqual(sp.AgentSignals(d).read(now=111.0)[0]["ts"], 110.0)

    def test_a_session_round_trips_and_ends(self):
        with tempfile.TemporaryDirectory() as d:
            sig = sp.AgentSignals(d)
            sp.write_hook({"hook_event_name": "Notification", "notification_type": "permission_prompt", "session_id": "abc-1",
                           "cwd": "/p/Arc", "message": "Claude needs\npermission"}, d, now=1000)
            [s] = sig.read(now=1001)
            self.assertEqual((s["session"], s["state"], s["cwd"], s["message"]), ("abc-1", "wait", "/p/Arc", "Claude needs permission"))
            sp.write_hook({"hook_event_name": "Stop", "session_id": "abc-1", "cwd": "/p/Arc"}, d, now=1002)
            self.assertEqual([x["state"] for x in sig.read(now=1003)], ["done"])
            self.assertEqual(sig.read(now=1003)[0]["message"], "")
            sp.write_hook({"hook_event_name": "SessionEnd", "session_id": "abc-1"}, d, now=1004)
            self.assertEqual(sig.read(now=1010), [])

    def test_odd_input_is_harmless(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sp.write_hook({"hook_event_name": "Stop"}, d))            # no session
            self.assertIsNone(sp.write_hook({"hook_event_name": "Other", "session_id": "x"}, d))
            sp.write_hook({"hook_event_name": "Stop", "session_id": "../../etc/passwd", "cwd": 5}, d, now=1)
            self.assertEqual(os.listdir(d)[0].count("/"), 0)                              # never leaves the folder
            self.assertEqual(sp.AgentSignals(d).read(now=2)[0]["cwd"], "")
            Path(d, "junk.json").write_text("{nope")
            Path(d, "other.txt").write_text("x")
            self.assertEqual(len(sp.AgentSignals(d).read(now=2)), 1)

    def test_old_and_dead_sessions_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            sp.write_hook({"hook_event_name": "UserPromptSubmit", "session_id": "old", "cwd": "/p"}, d, now=1000)
            sp.write_hook({"hook_event_name": "UserPromptSubmit", "session_id": "dead", "cwd": "/p"}, d, now=1000, pid=2 ** 22 + 12345)
            sp.write_hook({"hook_event_name": "UserPromptSubmit", "session_id": "live", "cwd": "/p"}, d, now=1000, pid=os.getpid())
            names = lambda now: [s["session"] for s in sp.AgentSignals(d).read(now=now)]
            self.assertEqual(names(1100), ["live", "old"])
            self.assertEqual(names(1000 + sp.AGENT_TTL["work"] + 1), [])                 # work is dropped after the TTL
            self.assertFalse(os.path.exists(sp.agent_session_file("dead", d)))             # and the dead one is cleaned up

    def test_sessions_go_to_the_most_specific_project(self):
        sessions = [{"session": "a", "state": "work", "ts": 5, "cwd": "/p/Arc/src", "message": ""},
                    {"session": "b", "state": "wait", "ts": 3, "cwd": "/p/Arc/.wt/docs", "message": "ok?"},
                    {"session": "c", "state": "done", "ts": 9, "cwd": "/p/Arc", "message": ""},
                    {"session": "d", "state": "work", "ts": 9, "cwd": "/elsewhere", "message": ""},
                    {"session": "e", "state": "done", "ts": 9, "cwd": "/p/Arcade", "message": ""}]
        got = sp.agent_by_directory(sessions, ["/p/Arc", "/p/Arc/.wt/docs"])
        self.assertEqual(got["/p/Arc"]["state"], "work")                    # work outranks done
        self.assertEqual(got["/p/Arc/.wt/docs"], {"state": "wait", "ts": 3, "message": "ok?", "name": "", "kind": "claude"})
        self.assertEqual(len(got), 2)                                       # /p/Arcade is not inside /p/Arc
        self.assertEqual(sp.agent_of(["/p/Arc", "/p/Arc/.wt/docs"], got)["state"], "wait")
        self.assertEqual(sp.agent_of(["/nowhere"], got)["state"], "")
        two = [{"session": "x", "state": "wait", "ts": 50, "cwd": "/p/Arc", "message": "new"},
               {"session": "y", "state": "wait", "ts": 10, "cwd": "/p/Arc", "message": "old"}]
        self.assertEqual(sp.agent_by_directory(two, ["/p/Arc"])["/p/Arc"]["message"], "old")   # waited longest

    def test_snapshot_lays_agent_state_over_projects_and_workspaces(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        with tempfile.TemporaryDirectory() as d:
            engine = sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json"))
            engine.results = sp.decide(facts, {}, {}, RULES)
            engine.holder.update(engine.results, 0.0, engine.settings, force=True)
            sp.write_hook({"hook_event_name": "Notification", "notification_type": "permission_prompt", "session_id": "s1",
                           "cwd": sp.HOME + "/Projects/Starling/src", "message": "Run tests?"}, os.path.join(d, "agents"))
            snap = engine.snapshot()
            rows = {r["name"]: r for r in snap["projects"]}
            self.assertEqual(rows["Starling"]["agent"], "wait")
            self.assertEqual(rows["Starling"]["agentMessage"], "Run tests?")
            self.assertNotIn("agent", rows["Signpost"])
            self.assertEqual([w.get("agent") for w in snap["workspaces"] if w["id"] == 3], ["wait"])


class HookInstall(unittest.TestCase):
    CMD = "python3 -I /x/signpost.py hook"

    def test_install_keeps_other_hooks_and_is_repeatable(self):
        mine = {"hooks": [{"type": "command", "command": "echo hi"}]}
        settings = {"model": "x", "hooks": {"Stop": [mine]}}
        once = sp.with_hooks(settings, self.CMD)
        self.assertEqual(once["model"], "x")
        self.assertEqual(once["hooks"]["Stop"][0], mine)
        self.assertEqual(sorted(sp.hooks_installed(once)), sorted(sp.HOOK_EVENTS))
        self.assertEqual(sp.with_hooks(once, self.CMD), once)                  # again: unchanged
        gone = sp.with_hooks(once, self.CMD, install=False)
        self.assertEqual(gone, settings)                                       # removal leaves only what was there
        self.assertEqual(sp.with_hooks({}, self.CMD, install=False), {})

    def test_a_similar_command_is_not_ours(self):
        other = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 notsignpost.py hookup"}]}]}}
        self.assertEqual(sp.hooks_installed(other), [])
        self.assertEqual(sp.with_hooks(other, self.CMD, install=False), other)

    def test_cli_writes_a_backup_and_reports(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "settings.json")
            path.write_text(json.dumps({"theme": "dark"}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(sp.hooks_cli(["install", "--config-dir", d]), 0)
                sp.hooks_cli(["install", "--config-dir", d])
                sp.hooks_cli(["status", "--config-dir", d])
            self.assertIn("already installed", out.getvalue())
            self.assertIn(": installed\n", out.getvalue())
            self.assertEqual(json.loads(Path(d, "settings.json.signpost-backup").read_text()), {"theme": "dark"})
            self.assertEqual(len(sp.hooks_installed(json.loads(path.read_text()))), len(sp.HOOK_EVENTS))
            with contextlib.redirect_stdout(io.StringIO()):
                sp.hooks_cli(["remove", "--config-dir", d])
            self.assertEqual(json.loads(path.read_text()), {"theme": "dark"})

    def test_cli_leaves_a_settings_file_it_cannot_read(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as d:
            Path(d, "settings.json").write_text("{broken")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                sp.hooks_cli(["install", "--config-dir", d])
            self.assertIn("not touched", out.getvalue())
            self.assertEqual(Path(d, "settings.json").read_text(), "{broken")


class GitInfo(unittest.TestCase):
    def test_status_and_numstat(self):
        text = "# branch.oid abc1234567\n# branch.head feat/fuzzy\n# branch.ab +2 -1\n1 .M N... 100644 100644 100644 a b f.py\n? new.txt\n"
        self.assertEqual(sp.parse_git_status(text), {"branch": "feat/fuzzy", "ahead": 2, "behind": 1, "files": 2})
        self.assertEqual(sp.parse_git_status("# branch.oid abc1234567\n# branch.head (detached)\n")["branch"], "abc1234")
        self.assertEqual(sp.parse_git_status("")["branch"], "")
        self.assertEqual(sp.parse_numstat("3\t1\ta.py\n-\t-\timg.png\n10\t0\tb.py\n"), (13, 1))

    def test_remote_becomes_a_safe_web_address(self):
        ok = {"git@github.com:me/repo.git": "https://github.com/me/repo",
              "https://github.com/me/repo.git": "https://github.com/me/repo",
              "https://user:token@gitlab.com/g/s/repo": "https://gitlab.com/g/s/repo",
              "ssh://git@host.example.org:2222/me/repo.git": "https://host.example.org/me/repo"}
        for given, want in ok.items():
            self.assertEqual(sp.web_remote(given), want, given)
        for bad in ("", "/srv/git/repo.git", "file:///x", "https://evil.com/a b", "https://evil.com/../x", "git@host:a;rm -rf",
                    "javascript:alert(1)", "https://localhost/x", "git@github.com:me/repo$(id)"):
            self.assertEqual(sp.web_remote(bad), "", bad)

    def make_repo(self, d):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        run = lambda *a: subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, env=env)
        run("init", "-q", "-b", "main")
        Path(d, "a.txt").write_text("one\ntwo\n")
        run("add", "."); run("commit", "-qm", "first")
        run("remote", "add", "origin", "git@github.com:me/repo.git")
        Path(d, "a.txt").write_text("one\nTWO\nthree\n")
        Path(d, "b.txt").write_text("new\n")
        return run

    def test_details_of_a_real_repo(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as d:
            self.make_repo(d)
            got = sp.git_details(d)
            self.assertEqual((got["branch"], got["files"], got["added"], got["removed"], got["remote"]),
                             ("main", 2, 2, 1, "https://github.com/me/repo"))
            self.assertEqual(sp.git_details(tempfile.gettempdir() + "/definitely-not-a-repo-xyz"), {})

    def test_a_repos_own_config_cannot_run_programs(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        with tempfile.TemporaryDirectory() as d:
            self.make_repo(d)
            marker = Path(d, "ran")
            script = Path(d, "evil.sh")
            script.write_text(f"#!/bin/sh\ntouch {marker}\n")
            script.chmod(0o755)
            with open(Path(d, ".git", "config"), "a") as f:
                f.write(f"[core]\n\tfsmonitor = {script}\n\thooksPath = {d}\n[diff]\n\texternal = {script}\n")
            sp.git_details(d)
            self.assertFalse(marker.exists())

    def test_neutralizers_cover_every_driver_name(self):
        listing = "\0".join(["core.bare\nfalse", "filter.evil.clean\n/x", "filter.a.b=c.process\n/y", "diff.tc.textconv\n/z",
                             "protocol.ssh.allow\nalways", "remote.origin.promisor\ntrue", "user.name\nme", ""])
        pairs = dict(sp.git_neutralizers(listing))
        for key in ("filter.evil.clean", "filter.evil.smudge", "filter.evil.process", "filter.a.b=c.process",
                    "diff.tc.textconv", "diff.tc.command"):
            self.assertEqual(pairs[key], "", key)
        self.assertEqual(pairs["filter.evil.required"], "false")
        self.assertEqual(pairs["protocol.ssh.allow"], "never")
        self.assertEqual(pairs["protocol.file.allow"], "never")
        self.assertEqual(pairs["remote.origin.promisor"], "false")
        self.assertNotIn("user.name", pairs)
        many = "\0".join(f"filter.f{i}.clean\nx" for i in range(400))
        self.assertIsNone(sp.git_neutralizers(many))       # too many to pass: no git at all

    def test_details_are_cached_and_refetched_when_stale(self):
        calls = []
        details = sp.GitDetails(load=lambda d: calls.append(d) or {"branch": "main"})
        details.want(["/tmp", "/tmp", ""], now=100)
        details.pool.shutdown(wait=True)
        details.pool = None
        self.assertEqual(calls, ["/tmp"])
        self.assertEqual(details.get("/tmp"), {"branch": "main"})
        self.assertEqual(details.get("/never"), {})
        self.assertTrue(details.take_dirty())
        self.assertFalse(details.take_dirty())
        time_now = sp.time.monotonic() + 1
        details.want(["/tmp"], now=time_now)                                    # fresh: nothing to do
        self.assertEqual(calls, ["/tmp"])


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitSafety(unittest.TestCase):
    """A repo that tries to make git run a program. Each case first proves, with plain git (the control),
    that the trap really fires; then that git_details() never springs it and still reads the repo."""

    ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.marker = self.root / "ran"
        self.script = self.root / "trap.sh"
        self.script.write_text(f"#!/bin/sh\necho \"$0 $*\" >> {self.marker}\ncat\n")
        self.script.chmod(0o755)
        self.exits = self.root / "trap-exit.sh"      # for ssh and credential helpers: never waits on stdin
        self.exits.write_text(f"#!/bin/sh\necho \"$0 $*\" >> {self.marker}\nexit 1\n")
        self.exits.chmod(0o755)
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("hello\n")
        (self.repo / "b.txt").write_text("one\n")
        self.git("add", "."); self.git("commit", "-qm", "first")

    def git(self, *args, check=True, stdin=None):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=check, capture_output=True, env=self.ENV,
                              input=stdin or b"", timeout=20)    # never the runner's stdin: a trap may read it

    def touch_same_size(self):
        # Same size, new mtime: git has to hash the file, which runs its clean filter.
        (self.repo / "a.txt").write_text("jello\n")
        os.utime(self.repo / "a.txt", (1e9 + 7, 1e9 + 7))

    def sprung(self):
        ran = self.marker.exists()
        self.marker.unlink(missing_ok=True)
        return ran

    def check(self, *control):
        """`control` is the plain git command that must spring the trap."""
        self.git(*control, check=False)
        self.assertTrue(self.sprung(), f"control did not run the trap: git {' '.join(control)}")
        details = sp.git_details(str(self.repo))
        self.assertFalse(self.sprung(), "git_details ran the trap")
        self.assertEqual(details.get("branch"), "main")
        return details

    def config(self, *pairs):
        for key, value in pairs:
            self.git("config", key, value)

    def test_clean_filter_from_gitattributes(self):
        (self.repo / ".gitattributes").write_text("a.txt filter=trap\n")
        self.git("add", ".gitattributes"); self.git("commit", "-qm", "attrs")
        self.config(("filter.trap.clean", f"{self.script} clean"), ("filter.trap.smudge", f"{self.script} smudge"))
        self.touch_same_size()
        self.check("status", "--porcelain=v2")
        self.check("diff", "HEAD", "--numstat")
        self.assertEqual(sp.git_details(str(self.repo))["added"], 1)

    def test_process_filter(self):
        (self.repo / ".gitattributes").write_text("a.txt filter=trap\n")
        self.config(("filter.trap.process", f"{self.script} process"))
        self.touch_same_size()
        self.check("diff", "HEAD", "--numstat")

    def test_filter_assigned_by_info_attributes(self):
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        (self.repo / ".git" / "info" / "attributes").write_text("* filter=trap\n")
        self.config(("filter.trap.clean", f"{self.script} clean"))
        self.touch_same_size()
        self.check("diff", "HEAD", "--numstat")

    def test_filter_from_an_included_config(self):
        extra = self.root / "extra.conf"
        extra.write_text(f"[filter \"trap\"]\n\tclean = {self.script} clean\n")
        (self.repo / ".gitattributes").write_text("a.txt filter=trap\n")
        self.config(("include.path", str(extra)))
        self.touch_same_size()
        self.check("diff", "HEAD", "--numstat")

    def test_filter_from_an_include_if(self):
        extra = self.root / "extra.conf"
        extra.write_text(f"[filter \"trap=1\"]\n\tclean = {self.script} clean\n")    # "=" can't go through -c
        (self.repo / ".gitattributes").write_text("a.txt filter=trap=1\n")
        self.config((f"includeIf.gitdir:{self.repo}/.git.path", str(extra)))
        self.touch_same_size()
        self.check("diff", "HEAD", "--numstat")

    def test_required_lfs_style_filter_degrades_gracefully(self):
        (self.repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
        self.config(("filter.lfs.clean", f"{self.script} clean"), ("filter.lfs.smudge", f"{self.script} smudge"),
                    ("filter.lfs.process", f"{self.script} process"), ("filter.lfs.required", "true"))
        (self.repo / "big.bin").write_bytes(b"\0binary\0")
        self.git("add", "big.bin", check=False)
        self.sprung()
        (self.repo / "a.txt").write_text("hello\nmore\n")
        details = sp.git_details(str(self.repo))
        self.assertFalse(self.sprung())
        self.assertEqual((details["branch"], details["added"]), ("main", 1))

    def test_textconv_and_diff_command_drivers(self):
        (self.repo / ".gitattributes").write_text("a.txt diff=trap\n")
        self.config(("diff.trap.textconv", f"{self.script} textconv"))
        (self.repo / "a.txt").write_text("changed\n")
        self.check("diff", "HEAD")
        self.git("config", "--unset", "diff.trap.textconv")
        self.config(("diff.trap.command", f"{self.script} command"))
        self.check("diff", "HEAD")

    def test_external_diff(self):
        self.config(("diff.external", str(self.script)))
        (self.repo / "a.txt").write_text("changed\n")
        self.check("diff", "HEAD")

    def test_fsmonitor(self):
        self.config(("core.fsmonitor", str(self.script)))
        self.check("status")

    def test_hooks(self):
        hooks = self.root / "hooks"
        hooks.mkdir()
        (hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch {self.marker}\n")
        (hooks / "pre-commit").chmod(0o755)
        self.config(("core.hooksPath", str(hooks)))
        self.check("commit", "-q", "--allow-empty", "-m", "x")

    def test_partial_clone_lazy_fetch_and_ssh_command(self):
        blob = self.git("rev-parse", "HEAD:a.txt").stdout.decode().strip()
        (self.repo / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
        self.config(("extensions.partialClone", "origin"), ("remote.origin.url", "ssh://example.invalid/x"),
                    ("remote.origin.promisor", "true"), ("core.sshCommand", str(self.exits)),
                    ("protocol.ssh.allow", "always"))
        (self.repo / "a.txt").write_text("changed\n")
        self.git("diff", "HEAD", "--numstat", check=False)
        self.assertTrue(self.sprung(), "control: a lazy fetch runs core.sshCommand")
        sp.git_details(str(self.repo))
        self.assertFalse(self.sprung())

    def test_a_config_git_cannot_read_as_text_does_not_switch_the_safety_off(self):
        # The audit's attack: a byte that is not UTF-8 made the config read fail, and git then ran with the
        # repo's filter still in place. The read must fail closed: no git at all.
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        (self.repo / ".git" / "info" / "attributes").write_text("* filter=x\n")
        with open(self.repo / ".git" / "config", "ab") as f:
            f.write(f"[filter \"x\"]\n\tclean = {self.script} clean\n[junk]\n\tv = \xff\n".encode("latin-1"))
        self.touch_same_size()
        self.git("diff", "HEAD", "--numstat", check=False)
        self.assertTrue(self.sprung(), "control: plain git runs the filter")
        sp.git_details(str(self.repo))
        self.assertFalse(self.sprung(), "git_details ran the trap")

    def test_a_driver_name_that_is_not_utf8_is_still_switched_off(self):
        name = b"tr\xe9p"
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        (self.repo / ".git" / "info" / "attributes").write_bytes(b"* filter=" + name + b"\n")
        with open(self.repo / ".git" / "config", "ab") as f:
            f.write(b'[filter "' + name + b'"]\n\tclean = ' + str(self.script).encode() + b" clean\n")
        self.touch_same_size()
        self.check("diff", "HEAD", "--numstat")

    def test_when_the_config_read_fails_git_is_not_run_again(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, 3)
        self.assertEqual(sp.git_details(str(self.repo), run=run), {})
        self.assertEqual(len(calls), 1)
        calls.clear()

        def failing(argv, **kw):
            calls.append(argv)
            return Mock(returncode=128, stdout=b"")
        self.assertEqual(sp.git_details(str(self.repo), run=failing), {})
        self.assertEqual(len(calls), 1)

    def test_the_folder_is_not_in_gits_arguments_and_git_gets_no_stdin(self):
        seen = []
        real = subprocess.run

        def spy(argv, **kw):
            seen.append((argv, kw.get("cwd"), kw.get("stdin")))
            return real(argv, **kw)
        details = sp.git_details(str(self.repo), run=spy)
        self.assertEqual(details["branch"], "main")
        for argv, cwd, stdin in seen:
            self.assertNotIn(str(self.repo), " ".join(argv))
            self.assertEqual((cwd, stdin), (str(self.repo), subprocess.DEVNULL))

    def test_ssh_command_and_credential_helper_never_run_for_local_reads(self):
        self.config(("remote.origin.url", "ssh://example.invalid/x"), ("core.sshCommand", str(self.exits)),
                    ("credential.helper", f"!{self.exits}"))
        self.check("ls-remote", "origin")
        self.git("credential", "fill", check=False, stdin=b"protocol=https\nhost=example.invalid\n\n")
        self.assertTrue(self.sprung(), "control: git credential fill runs the helper")
        sp.git_details(str(self.repo))
        self.assertFalse(self.sprung())


class UntrustedFiles(unittest.TestCase):
    """Files under a project folder may come from an archive: `.git` or `HEAD` as a FIFO or a link to
    /dev/zero must neither hang the engine nor fill its memory."""

    def probe(self, folder):
        """Git().info() in a child with a time and memory limit, so a regression can't hang the suite."""
        code = ("import importlib.util,resource,sys\n"
                "resource.setrlimit(resource.RLIMIT_AS,(1<<30,1<<30))\n"
                f"spec=importlib.util.spec_from_file_location('sp',{os.path.join(ROOT, 'signpost.py')!r})\n"
                "sp=importlib.util.module_from_spec(spec);spec.loader.exec_module(sp)\n"
                "print(repr(sp.Git().info(sys.argv[1])))\n")
        return subprocess.run([sys.executable, "-I", "-c", code, folder], capture_output=True, text=True, timeout=20)

    def test_fifo_and_device_traps(self):
        with tempfile.TemporaryDirectory() as d:
            cases = {}
            cases["git-fifo"] = os.path.join(d, "a")
            os.makedirs(cases["git-fifo"])
            os.mkfifo(os.path.join(cases["git-fifo"], ".git"))
            cases["git-zero"] = os.path.join(d, "b")
            os.makedirs(cases["git-zero"])
            os.symlink("/dev/zero", os.path.join(cases["git-zero"], ".git"))
            cases["head-fifo"] = os.path.join(d, "c")
            os.makedirs(os.path.join(cases["head-fifo"], ".git"))
            os.mkfifo(os.path.join(cases["head-fifo"], ".git", "HEAD"))
            cases["head-zero"] = os.path.join(d, "e")
            os.makedirs(os.path.join(cases["head-zero"], ".git"))
            os.symlink("/dev/zero", os.path.join(cases["head-zero"], ".git", "HEAD"))
            for name, folder in cases.items():
                with self.subTest(name):
                    r = self.probe(folder)
                    self.assertEqual(r.returncode, 0, r.stderr[-400:])
                    self.assertEqual(r.stdout.strip(), "None")

    def test_desktop_files_and_icons(self):
        with tempfile.TemporaryDirectory() as d:
            fifo = os.path.join(d, "trap.desktop")
            os.mkfifo(fifo)
            self.assertIsNone(sp.Desktop.parse(fifo))                      # returns at once, no hang
            image = os.path.join(d, "logo.png")
            Path(image).write_bytes(b"\x89PNG")
            os.mkfifo(os.path.join(d, "fifo.png"))
            self.assertEqual(sp.safe_icon("firefox"), "firefox")
            self.assertEqual(sp.safe_icon("org.gnome.Nautilus"), "org.gnome.Nautilus")
            self.assertEqual(sp.safe_icon(image), image)                     # a .desktop file may name a picture
            self.assertEqual(sp.safe_icon(image, files=False), "")           # a window class never may
            self.assertEqual(sp.safe_icon(os.path.join(d, "fifo.png")), "")
            self.assertEqual(sp.safe_icon("/home/u/secret.txt"), "")
            self.assertEqual(sp.safe_icon("/dev/zero"), "")
            self.assertEqual(sp.safe_icon("../../x"), "")


class WaitAlerts(unittest.TestCase):
    def session(self, name, state, ts, message=""):
        return {"session": name, "state": state, "ts": ts, "cwd": "/p/Arc", "message": message}

    def test_each_request_is_announced_once(self):
        seen = {}
        self.assertEqual([s["session"] for s in sp.new_waits([self.session("a", "wait", 1)], seen)], ["a"])
        self.assertEqual(sp.new_waits([self.session("a", "wait", 1)], seen), [])        # same request, no repeat
        self.assertEqual(sp.new_waits([self.session("a", "work", 2)], seen), [])        # it carried on
        self.assertEqual(len(sp.new_waits([self.session("a", "wait", 3)], seen)), 1)    # asking again is news
        self.assertEqual(sp.new_waits([self.session("a", "done", 4)], seen), [])
        self.assertEqual(seen, {})

    def test_two_sessions_do_not_hide_each_other(self):
        seen = {}
        both = [self.session("a", "wait", 1), self.session("b", "wait", 1)]
        self.assertEqual(len(sp.new_waits(both, seen)), 2)
        self.assertEqual(sp.new_waits(both, seen), [])

    def test_the_notification_call(self):
        values = sp.notify_values("Claude needs you", "Arc · search\nRun <b>tests</b> & <a href='x'>go</a>?", jump=True)
        self.assertEqual(values[:5], ["Signpost", "0", "", "Claude needs you",
                                      "Arc · search Run &lt;b&gt;tests&lt;/b&gt; &amp; &lt;a href='x'&gt;go&lt;/a&gt;?"])
        self.assertIn("Go to it", values)
        # two hints: normal urgency, and transient (kept out of the notification history); ten seconds
        self.assertEqual(values[-8:], ["2", "urgency", "y", "1", "transient", "b", "true", "10000"])
        argv = sp.notify_argv("t", "b", jump=False)
        self.assertEqual(argv[:4], ["busctl", "--user", "--", "call"])                    # options end here, so "-x" text is text
        self.assertNotIn("Go to it", argv)
        self.assertEqual(argv[-9], "0")                                                   # no actions
        # every value fits the signature the bus call checks it against
        self.assertEqual(len(sp.bus_args(sp.NOTIFY_SIGNATURE, values)), 18)

    def test_parsing_what_dbus_says(self):
        self.assertEqual(sp.parse_notify_id("u 42\n"), 42)
        self.assertIsNone(sp.parse_notify_id("Error"))
        self.assertEqual(sp.parse_action("/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 42, 'go')"), (42, "go"))
        self.assertIsNone(sp.parse_action("... NotificationClosed (uint32 42, uint32 2)"))

    def test_the_alert_text(self):
        self.assertEqual(sp.wait_alert({"message": "Run tests?"}, "Arc · search", 2), ("Arc · search needs you", "Workspace 2 · Run tests?"))
        self.assertEqual(sp.wait_alert({"message": ""}, "", 0), ("Claude needs you", "Claude is waiting for you"))
        self.assertEqual(sp.wait_alert({"message": ""}, "Arc", 2), ("Arc needs you", "Workspace 2 · Claude is waiting for you"))


class WaitAcknowledged(unittest.TestCase):
    """Esc on a permission prompt sends no event: a wait you have sat with counts as seen."""

    def session(self, state="wait", ts=1.0, cwd="/p/Arc/src", name="a"):
        return {"session": name, "state": state, "ts": ts, "cwd": cwd, "message": "Run tests?"}

    def test_seen_after_a_few_seconds_in_its_project(self):
        acks = sp.WaitAcks()
        s = [self.session()]
        self.assertFalse(acks.update(s, "/p/Arc", 10.0))
        self.assertEqual(acks.apply(s)[0]["state"], "wait")              # a glance is not enough
        self.assertTrue(acks.update(s, "/p/Arc", 10.0 + sp.ACK_SECONDS))
        self.assertEqual(acks.apply(s)[0], {**s[0], "state": "done", "message": ""})
        self.assertFalse(acks.update(s, "/p/Arc", 20.0))                  # reported once
        self.assertEqual(s[0]["state"], "wait")                           # the input is not changed

    def test_elsewhere_does_not_count_and_leaving_restarts_the_clock(self):
        acks = sp.WaitAcks()
        s = [self.session()]
        acks.update(s, "/p/Arcade", 0.0)                                   # not inside /p/Arc
        acks.update(s, None, 1.0)
        acks.update(s, "/p/Arc", 5.0)
        acks.update(s, "/p/Other", 7.0)                                    # left before it counted
        self.assertFalse(acks.update(s, "/p/Arc", 8.0))
        self.assertEqual(acks.apply(s)[0]["state"], "wait")
        self.assertTrue(acks.update(s, "/p/Arc", 8.0 + sp.ACK_SECONDS))

    def test_a_new_question_needs_you_again(self):
        acks = sp.WaitAcks()
        acks.update([self.session(ts=1.0)], "/p/Arc", 0.0)
        acks.update([self.session(ts=1.0)], "/p/Arc", 5.0)
        again = [self.session(ts=2.0)]
        self.assertFalse(acks.update(again, "/p/Arc", 5.5))
        self.assertEqual(acks.apply(again)[0]["state"], "wait")
        acks.update([self.session("work", ts=3.0)], "/p/Arc", 6.0)        # it carried on: forgotten
        self.assertEqual(acks.watch, {})

    def test_other_states_and_sessions_pass_through(self):
        acks = sp.WaitAcks()
        both = [self.session(), self.session("work", name="b", cwd="/p/Other")]
        acks.update(both, "/p/Arc", 0.0)
        acks.update(both, "/p/Arc", 9.0)
        self.assertEqual([s["state"] for s in acks.apply(both)], ["done", "work"])

    def test_two_sessions_in_one_project_are_told_apart_by_process(self):
        """T3 Code and kitty both in Starling: looking at kitty's Claude Code marks only its question seen."""
        acks = sp.WaitAcks()
        t3 = {**self.session(name="t3"), "pid": 100}
        kitty = {**self.session(name="kitty", ts=2.0), "pid": 200}
        looking_at_kitty = {"dir": "/p/Arc", "pids": {200}}
        acks.update([t3, kitty], looking_at_kitty, 0.0)
        acks.update([t3, kitty], looking_at_kitty, sp.ACK_SECONDS)
        self.assertEqual([s["state"] for s in acks.apply([t3, kitty])], ["wait", "done"])

    def test_without_a_process_a_shared_folder_is_too_ambiguous(self):
        acks = sp.WaitAcks()
        both = [self.session(name="a"), self.session(name="b", ts=2.0)]
        acks.update(both, "/p/Arc", 0.0)
        self.assertFalse(acks.update(both, "/p/Arc", 9.0))
        self.assertEqual([s["state"] for s in acks.apply(both)], ["wait", "wait"])

    def test_an_exact_wait_is_never_marked_seen(self):
        acks = sp.WaitAcks()
        s = [{**self.session(), "exact": True}]
        acks.update(s, "/p/Arc", 0.0)
        acks.update(s, "/p/Arc", 9.0)
        self.assertEqual(acks.apply(s)[0]["state"], "wait")

    def test_snapshot_shows_a_seen_wait_as_finished(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        with tempfile.TemporaryDirectory() as d:
            engine = sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json"))
            engine.results = sp.decide(facts, {}, {}, RULES)
            engine.holder.update(engine.results, 0.0, engine.settings, force=True)
            sp.write_hook({"hook_event_name": "Notification", "notification_type": "permission_prompt", "session_id": "s1",
                           "cwd": sp.HOME + "/Projects/Starling/src", "message": "Run tests?"}, os.path.join(d, "agents"))
            engine.foreground_project = {"directory": sp.HOME + "/Projects/Starling"}
            with patch.object(sp.time, "monotonic", return_value=1000.0):
                self.assertEqual({r["name"]: r.get("agent") for r in engine.snapshot()["projects"]}["Starling"], "wait")
            with patch.object(sp.time, "monotonic", return_value=1000.0 + sp.ACK_SECONDS):
                row = {r["name"]: r for r in engine.snapshot()["projects"]}["Starling"]
            self.assertEqual((row["agent"], row["agentMessage"]), ("done", ""))


class AlertFlow(unittest.TestCase):
    """The daemon's side of "needs you": when it alerts, and what a press of "Go to it" does."""

    def daemon(self, d):
        engine = sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json"))
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        engine.results = sp.decide(facts, {}, {}, RULES)
        return sp.Daemon(engine)

    def ask(self, d, session, cwd, now, message="Run it?", state="Notification"):
        sp.write_hook({"hook_event_name": state, "notification_type": "permission_prompt", "session_id": session,
                       "cwd": cwd, "message": message}, os.path.join(d, "agents"), now=now)

    def test_alerts_once_for_a_new_request_and_never_for_old_news(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            daemon = self.daemon(d)
            sent = []
            daemon.notify = lambda title, body, ws, image="", **k: sent.append((title, body, ws))
            now = sp.time.time()
            self.ask(d, "old", sp.HOME + "/Projects/Starling", now - 5)
            with patch.object(sp, "focused_workspace", return_value=1):
                daemon.tick_agents()                                   # already waiting when we started: not news
                self.assertEqual(sent, [])
                self.ask(d, "new", sp.HOME + "/Projects/Starling/src", now + 1, "Claude needs your permission to use Bash")
                daemon.e.signals.stamp = None
                daemon.tick_agents()
                daemon.tick_agents()
            self.assertEqual(sent, [("Starling needs you", "Workspace 3 · Claude needs your permission to use Bash", 3)])

    def test_quiet_when_you_are_looking_sharing_or_have_it_off(self):
        from unittest.mock import patch
        for name, here, casts, streams, notify in (
                ("looking", 3, {}, 0, True), ("sharing", 1, {"x": 1}, 0, True),
                ("share found through the portal", 1, {}, 1, True), ("pipewire silent", 1, {}, None, True),
                ("off", 1, {}, 0, False)):
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                daemon = self.daemon(d)
                daemon.e.settings = {**daemon.e.settings, "alerts": notify}
                daemon.casts = casts
                daemon.streams = streams
                sent = []
                daemon.notify = lambda *a, **k: sent.append(a[:3])
                daemon.primed = True
                self.ask(d, "s", sp.HOME + "/Projects/Starling", sp.time.time())
                with patch.object(sp, "focused_workspace", return_value=here):
                    daemon.tick_agents()
                self.assertEqual(sent, [])

    def test_an_agent_outside_any_open_project_still_alerts_without_a_button(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            daemon = self.daemon(d)
            sent = []
            daemon.notify = lambda *a, **k: sent.append(a[:3])
            daemon.primed = True
            self.ask(d, "s", "/srv/elsewhere/tool", sp.time.time(), "")
            with patch.object(sp, "focused_workspace", return_value=1):
                daemon.tick_agents()
            self.assertEqual(sent, [("tool needs you", "Claude is waiting for you", 0)])

    def test_go_to_it_jumps_to_the_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            daemon = self.daemon(d)
            read, write = os.pipe()
            daemon.monitor = type("M", (), {"stdout": os.fdopen(read, "rb", buffering=0), "poll": lambda self: None,
                                            "wait": lambda self: 0, "terminate": lambda self: None})()
            daemon.sel.register(daemon.monitor.stdout, sp.selectors.EVENT_READ, "notif")
            daemon.alerts = {7: {"ws": 3, "address": "0xabc", "pid": 42}, 8: {"ws": 5, "address": "0xdef", "pid": 50}}
            jumps = []
            daemon.send = lambda message: jumps.append(message)
            lines = [
                "/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 7, 'go')",
                "/org/freedesktop/Notifications: org.freedesktop.Notifications.NotificationClosed (uint32 8, uint32 2)",
                "/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 99, 'go')",    # not ours
                "/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 8, 'other')",  # not our button
            ]
            os.write(write, ("\n".join(lines) + "\n").encode())
            # 0xabc is still the agent's window (pid 42); 0xdef now belongs to another process
            pids = {"0xabc": 42, "0xdef": 99}
            with patch.object(sp, "window_pid", side_effect=lambda a: pids.get(a)):
                daemon.read_alert_actions()
            self.assertEqual(jumps, [{"jump": 3, "address": "0xabc"}])     # to the agent's window, if it moved
            self.assertEqual(daemon.alerts, {8: {"ws": 5, "address": "0xdef", "pid": 50}})
            os.write(write, b"/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 8, 'default')\n")
            with patch.object(sp, "window_pid", side_effect=lambda a: pids.get(a)):
                daemon.read_alert_actions()
            # a reused address is not trusted: the workspace the alert named instead
            self.assertEqual(jumps, [{"jump": 3, "address": "0xabc"}, {"jump": 5, "address": ""}])
            os.close(write)
            daemon.read_alert_actions()                                    # the monitor ended: let go of it
            self.assertIsNone(daemon.monitor)


class WatcherCleanup(unittest.TestCase):
    """The "Go to it" watcher is a child process. It must never outlive its engine or linger when idle."""

    ENGINE = ["python3", "-I", "/x/signpost.py", "watch"]

    def test_the_watcher_dies_with_its_engine(self):
        self.assertEqual(sp.monitor_argv(lambda name: "/usr/bin/setpriv"),
                         ["setpriv", "--pdeathsig", "TERM", "--", *sp.MONITOR_ARGV])
        self.assertEqual(sp.monitor_argv(lambda name: None), sp.MONITOR_ARGV)

    def test_nothing_is_stopped_by_its_command_line(self):
        # A gdbus monitor of your own with the very same arguments is left alone: no reaping at all.
        self.assertFalse(hasattr(sp, "stale_monitors") or hasattr(sp, "reap_stale_monitors"))
        killed = []
        with patch.object(sp.os, "kill", side_effect=lambda *a: killed.append(a)):
            daemon = sp.Daemon(sp.Engine(rules=RULES, state_file="/nonexistent"), streams=lambda: 0)
            self.addCleanup(daemon.sel.close)
            daemon.send = Mock(side_effect=KeyboardInterrupt)
            daemon.e.recompute = lambda *a, **k: []
            daemon.e.snapshot = lambda: {"workspaces": []}
            with patch.object(sp, "active_window", return_value=None), patch.object(sp, "hypr_dir", return_value=None), \
                    self.assertRaises(KeyboardInterrupt):
                daemon.run()
        self.assertEqual(killed, [])

    def test_a_live_watcher_really_ends_when_its_parent_is_killed(self):
        if not shutil.which("setpriv"):
            self.skipTest("setpriv not installed")
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "child")
            # A parent that starts `sleep` the way the engine starts its watcher, then is SIGKILLed.
            script = ("import subprocess,sys,time\n"
                      "p=subprocess.Popen(['setpriv','--pdeathsig','TERM','--','sleep','30'])\n"
                      f"open({pidfile!r},'w').write(str(p.pid))\n"
                      "time.sleep(30)\n")
            parent = subprocess.Popen([sys.executable, "-I", "-c", script])
            for _ in range(100):
                if os.path.exists(pidfile) and open(pidfile).read():
                    break
                time.sleep(0.05)
            child = int(open(pidfile).read())
            parent.kill()
            parent.wait()
            for _ in range(100):
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                os.kill(child, 9)
                self.fail("the child outlived its killed parent")

    def test_the_process_table_reads_this_process(self):
        table = sp.proc_table()
        self.assertIn(os.getpid(), table)
        self.assertEqual(table[os.getpid()][0], os.getppid())

    def fake_monitor(self, daemon):
        read, write = os.pipe()
        state = {"stopped": False}
        proc = type("M", (), {
            "stdout": os.fdopen(read, "rb", buffering=0),
            "poll": lambda self: 0 if state["stopped"] else None,
            "terminate": lambda self: state.update(stopped=True),
            "kill": lambda self: state.update(stopped=True),
            "wait": lambda self, timeout=None: 0})()
        daemon.monitor = proc
        daemon.sel.register(proc.stdout, sp.selectors.EVENT_READ, "notif")
        return proc, state, write

    def daemon(self, d):
        return sp.Daemon(sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json")))

    def test_the_watcher_stops_when_no_alert_is_left(self):
        with tempfile.TemporaryDirectory() as d:
            daemon = self.daemon(d)
            proc, state, write = self.fake_monitor(daemon)
            now = sp.time.monotonic()
            daemon.alerts, daemon.alert_times = {5: 2}, {5: now}
            daemon.expire_alerts()
            self.assertFalse(state["stopped"])                          # a fresh alert still needs its watcher
            daemon.alert_times[5] = now - sp.ALERT_LIFE - 1
            daemon.expire_alerts()
            self.assertEqual((daemon.alerts, daemon.alert_times), ({}, {}))
            self.assertTrue(state["stopped"])
            self.assertIsNone(daemon.monitor)
            os.close(write)

    def test_closing_the_daemon_stops_the_watcher(self):
        with tempfile.TemporaryDirectory() as d:
            daemon = self.daemon(d)
            proc, state, write = self.fake_monitor(daemon)
            daemon.close()
            self.assertTrue(state["stopped"])
            daemon.close()                                              # twice is fine
            os.close(write)

    def test_sigterm_is_a_clean_exit(self):
        import signal as sg
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "XDG_STATE_HOME": d, "XDG_CONFIG_HOME": d}
            proc = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "signpost.py"), "watch"], env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                self.assertTrue(proc.stdout.readline())                 # it is up: its first snapshot
                proc.send_signal(sg.SIGTERM)
                self.assertEqual(proc.wait(timeout=10), 0)              # not -15: its cleanup ran
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.stdout.close()
                proc.stdin.close()


class ProjectCatalog(unittest.TestCase):
    def test_t3_placeholder_does_not_name_the_workspace_or_create_a_project(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        facts[0]["title"] = "No project / New thread"
        result = sp.decide(facts, {}, {}, RULES, active=facts[0]["address"])[3]
        self.assertEqual(result["full"], "T3 Code")
        self.assertIsNone(result["_activeProject"])
        self.assertCountEqual([row["name"] for row in result["_projects"]], ["Signpost", "Starling"])

    def test_t3_shared_process_lists_only_the_project_visible_in_each_window(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        first = {**facts[0], "shared": True}
        second = {**first, "workspace": 4, "address": "0xa2", "title": "Signpost / Prepare notes"}
        result = sp.decide([first, second], {}, {}, RULES)
        self.assertEqual([row["name"] for row in result[3]["_projects"]], ["Starling"])
        self.assertEqual([row["name"] for row in result[4]["_projects"]], ["Signpost"])

    def test_t3_project_title_does_not_resolve_to_an_arbitrary_worktree(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        window = facts[0]
        for label, agent in zip(("docs", "search"), window["agents"][1:]):
            root = agent["cwd"] + "-" + label
            agent["git"].update(kind="worktree", root=root, label=label)
            agent["cwd"] = root
        result = sp.decide(facts, {}, {}, RULES, active=window["address"])[3]
        self.assertEqual((result["full"], result["source"]), ("Starling", "project"))
        self.assertEqual(result["_activeProject"]["directory"], "")
        self.assertCountEqual([row["name"] for row in result["_projects"]],
                              ["Starling", "Signpost", "Starling · docs", "Starling · search"])

    def test_identical_worktree_names_keep_separate_targets(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "kitty-agent-in-worktree.json"))["facts"]
        worktree = facts[0]
        same_worktree = deepcopy(worktree)
        same_worktree["address"] = "0xb2"
        other_worktree = deepcopy(worktree)
        other_worktree["address"] = "0xb3"
        agent = other_worktree["tabs"][0]["fg"]["agent"]
        agent["git"]["root"] += "-other"
        agent["cwd"] = agent["git"]["root"]
        alias_key = agent["git"]["main"]
        on_another_workspace = {**worktree, "workspace": 2, "address": "0xb4"}
        results = sp.decide([worktree, same_worktree, other_worktree, on_another_workspace],
                            {"aliases": {alias_key: "Client app"}}, {}, RULES)
        first, second = results[1]["_projects"]
        self.assertEqual(first["name"], "Client app · d1")
        self.assertEqual(first["originalName"], "Starling · d1")
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["detail"], second["detail"])
        self.assertEqual(results[2]["_projects"][0]["workspace"], 2)

    def test_catalog_includes_secondary_projects_when_label_is_pinned(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-without-agent.json"))["facts"]
        engine = sp.Engine(rules=RULES, state_file="/nonexistent")
        engine.results = sp.decide(facts, {"pins": {"3": "Client work"}}, {}, RULES)
        self.assertEqual(engine.results[3]["full"], "Client work")
        self.assertEqual([project["name"] for project in engine.snapshot()["projects"]],
                         ["OmaWidgets", "Signpost", "Starling"])
        ignored = sp.decide(facts, {}, {"ignoreClasses": "com.t3tools.T3Code"}, RULES)
        self.assertEqual(ignored[3]["_projects"], [])


class RecentProjects(unittest.TestCase):
    def test_foreground_project_survives_restart_with_its_exact_worktree_path(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "kitty-agent-in-worktree.json"))["facts"]
        with tempfile.TemporaryDirectory() as folder:
            state_file = Path(folder) / "state.json"
            project = Path(folder) / "Client's preview worktree"
            project.mkdir()
            agent = facts[0]["tabs"][0]["fg"]["agent"]
            agent["git"]["root"] = str(project)
            agent["cwd"] = str(project / "src")
            engine = sp.Engine({"recentIgnore": []}, rules=RULES, state_file=state_file)   # the test runs in /tmp
            with patch.object(sp, "hypr_request", return_value=[]), patch.object(engine.probe, "facts", return_value=facts):
                engine.recompute(active=facts[0]["address"])
            active = engine.snapshot()["projects"]
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]["workspace"], 1)
            self.assertEqual(active[0]["address"], facts[0]["address"])
            reopened = sp.Engine({"recentIgnore": []}, rules=RULES, state_file=state_file)
            reopened.state["aliases"][agent["git"]["main"]] = "Client app"
            recent = reopened.snapshot()["projects"][0]
            self.assertEqual((recent["workspace"], recent["name"]), (0, "Client app · d1"))
            self.assertEqual(reopened.select_recent(recent["id"]), {"directory": str(project)})
            reopened.results = engine.results
            self.assertEqual(reopened.select_recent(recent["id"]), {"workspace": 1, "address": facts[0]["address"]})
            reopened.results = {}
            project.rmdir()
            self.assertEqual(reopened.select_recent(recent["id"]), {"error": "missingFolder"})

    def test_history_merges_writers_and_keeps_the_most_recent_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recent-projects.json"
            left, right = sp.ProjectHistory(path), sp.ProjectHistory(path)
            left.LIMIT = right.LIMIT = 2
            first = {"directory": "/projects/one", "originalName": "One"}
            second = {"directory": "/projects/two", "originalName": "Two"}
            third = {"directory": "/projects/three", "originalName": "Three"}
            left.touch(first)
            right.touch(second)
            left.touch(first)
            self.assertEqual([r["originalName"] for r in left.entries], ["One", "Two"])
            right.touch(third)
            left.refresh()
            self.assertEqual([r["originalName"] for r in left.entries], ["Three", "One"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_title_only_stays_out_of_history_while_secondary_projects_are_recorded(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-without-agent.json"))["facts"]
        with tempfile.TemporaryDirectory() as folder:
            engine = sp.Engine({"recentIgnore": []}, rules=RULES, state_file=Path(folder) / "state.json")
            with patch.object(sp, "hypr_request", return_value=[]), patch.object(engine.probe, "facts", return_value=facts):
                engine.recompute(active=facts[0]["address"])
            names = [entry["originalName"] for entry in engine.history.entries]
            self.assertNotIn("OmaWidgets", names)
            self.assertEqual(set(names), {"Starling", "Signpost"})
            row = next(row for row in engine.snapshot()["projects"] if row["name"] == "Signpost")
            engine.remember_project(row["id"])
            self.assertEqual(engine.history.entries[0]["originalName"], "Signpost")

    def test_secondary_projects_respect_history_cap_and_keep_foreground_first(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "recent-projects.json"
            hist = sp.ProjectHistory(path)
            hist.LIMIT = 3
            rows = [{"directory": f"/projects/p{i}", "originalName": f"P{i}"} for i in range(5)]
            hist.remember_open(rows, front=rows[0])
            self.assertEqual(len(hist.entries), 3)
            self.assertEqual([e["originalName"] for e in hist.entries], ["P0", "P1", "P2"])
            hist.remember_open([{"directory": "/projects/p9", "originalName": "P9"}], front=None)
            self.assertEqual(len(hist.entries), 3)
            self.assertEqual(hist.entries[0]["originalName"], "P0")
            self.assertEqual(hist.entries[1]["originalName"], "P9")


class Hold(unittest.TestCase):
    """A name changes at once when what it names changes; it waits only for focus shifts."""
    S = {"holdSeconds": 5}

    @staticmethod
    def res(name, address, offers, primary=None, strength="strong"):
        return {1: {"id": 1, "name": name, "full": name, "strength": strength, "address": address,
                    "_offers": offers, "_primary": primary or {a: o[0] for a, o in offers.items() if o}}}

    def test_first_name_lands_at_once(self):
        h = sp.Holder()
        h.update(self.res("omaglimpse", "0xk", {"0xk": ["omaglimpse"]}), 0, self.S)
        self.assertEqual(h.shown[1]["name"], "omaglimpse")

    def test_weak_placeholder_upgrades_at_once(self):
        h = sp.Holder()
        h.update(self.res("kitty", "0xk", {"0xk": ["kitty"]}, strength="weak"), 0, self.S)
        h.update(self.res("omaglimpse", "0xk", {"0xk": ["omaglimpse"]}), 1, self.S)
        self.assertEqual(h.shown[1]["name"], "omaglimpse")

    def test_cd_in_the_named_window_is_immediate(self):
        h = sp.Holder()
        h.update(self.res("Starling", "0xk", {"0xk": ["Starling"]}), 0, self.S)
        h.update(self.res("OmaWidgets", "0xk", {"0xk": ["OmaWidgets"]}), 1, self.S)   # same window, says something else now
        self.assertEqual(h.shown[1]["name"], "OmaWidgets")
        self.assertFalse(h.pending)

    def test_named_window_closing_is_immediate(self):
        h = sp.Holder()
        h.update(self.res("Starling", "0xt", {"0xt": ["Starling"], "0xs": ["Spotify"]}), 0, self.S)
        h.update(self.res("Spotify", "0xs", {"0xs": ["Spotify"]}), 1, self.S)
        self.assertEqual(h.shown[1]["name"], "Spotify")

    def test_focusing_another_window_waits_then_renames(self):
        h = sp.Holder()
        both = {"0xt": ["Starling"], "0xk": ["admin-mobile"]}
        h.update(self.res("Starling", "0xt", both), 0, self.S)
        h.update(self.res("admin-mobile", "0xk", both), 10, self.S, active="0xk")
        self.assertEqual(h.shown[1]["name"], "Starling")
        self.assertEqual(h.view()[0]["pending"]["name"], "admin-mobile")
        h.tick(14.9)
        self.assertEqual(h.shown[1]["name"], "Starling")
        h.tick(15.0)
        self.assertEqual(h.shown[1]["name"], "admin-mobile")

    def test_going_back_before_the_hold_never_flickers(self):
        h = sp.Holder()
        both = {"0xt": ["Starling"], "0xk": ["admin-mobile"]}
        h.update(self.res("Starling", "0xt", both), 0, self.S)
        h.update(self.res("admin-mobile", "0xk", both), 10, self.S, active="0xk")
        events = h.update(self.res("Starling", "0xt", both), 12, self.S, active="0xt")
        self.assertFalse(h.pending)
        self.assertEqual(h.shown[1]["name"], "Starling")
        self.assertTrue(any("before the hold ended" in e for e in events))

    def test_switching_thread_in_the_active_window_is_immediate(self):
        h = sp.Holder()
        t3 = {"0xt": ["Starling", "Signpost"]}
        h.update(self.res("Starling", "0xt", t3, primary={"0xt": "Starling"}), 0, self.S, active="0xt")
        h.update(self.res("Signpost", "0xt", t3, primary={"0xt": "Signpost"}), 1, self.S, active="0xt")
        self.assertEqual(h.shown[1]["name"], "Signpost")

    def test_busier_agent_in_a_background_window_waits(self):
        h = sp.Holder()
        t3 = {"0xt": ["Starling", "Signpost"]}
        h.update(self.res("Starling", "0xt", t3, primary={"0xt": "Starling"}), 0, self.S, active="0xother")
        h.update(self.res("Signpost", "0xt", t3, primary={"0xt": "Signpost"}), 1, self.S, active="0xother")
        self.assertEqual(h.shown[1]["name"], "Starling")
        self.assertIn(1, h.pending)

    def test_view_is_stable_while_waiting(self):
        h = sp.Holder()
        both = {"0xt": ["Starling"], "0xk": ["admin-mobile"]}
        h.update(self.res("Starling", "0xt", both), 0, self.S)
        h.update(self.res("admin-mobile", "0xk", both), 10, self.S)
        self.assertEqual(h.view(), h.view())
        self.assertNotIn("_offers", h.view()[0])


class ProbeCache(unittest.TestCase):
    def test_only_refreshed_windows_are_probed_again(self):
        calls = []

        class Fake(sp.Probe):
            def window(self, c, procs, shared, alive):
                calls.append(c["address"])
                f = {"address": c["address"], "class": c["class"], "title": c["title"], "pid": c["pid"],
                     "workspace": c["workspace"]["id"], "focus": c["focusHistoryID"], "floating": False, "shared": shared,
                     "app": {"name": c["class"], "icon": "", "desktop": None, "webapp": False}}
                if c["class"] == "kitty":
                    f["tabs"] = []
                else:
                    f["agents"] = []
                return f

        clients = [{"address": "0xk", "class": "kitty", "title": "~", "pid": 1, "workspace": {"id": 1}, "focusHistoryID": 0},
                   {"address": "0xb", "class": "brave", "title": "tab", "pid": 2, "workspace": {"id": 2}, "focusHistoryID": 1}]
        pr = Fake(RULES)
        pr.facts(clients, "all")
        self.assertEqual(calls, ["0xk", "0xb"])
        calls.clear()
        clients[1]["focusHistoryID"] = 0
        clients[1]["workspace"] = {"id": 3}
        out = pr.facts(clients, set())                         # a focus / move event: no /proc walk at all
        self.assertEqual(calls, [])
        self.assertEqual(out[1]["workspace"], 3)
        pr.facts(clients, "poll")                              # the poll looks at terminals only
        self.assertEqual(calls, ["0xk"])
        calls.clear()
        pr.facts(clients, {"0xb"})                             # a title event looks at that window
        self.assertEqual(calls, ["0xb"])


class Activity(unittest.TestCase):
    def test_busy_when_cpu_moves(self):
        a = sp.Activity(busy_share=0.05)
        clock = [100.0]
        real = sp.time.monotonic
        sp.time.monotonic = lambda: clock[0]
        try:
            a.sample(1, 0, 7)
            clock[0] += 1.0
            busy, last = a.sample(1, int(sp.CLK_TCK * 0.5), 7)   # half a core for a second
            self.assertTrue(busy)
            self.assertGreater(last, 0)
            clock[0] += 1.0
            busy, last2 = a.sample(1, int(sp.CLK_TCK * 0.5), 7)  # idle
            self.assertFalse(busy)
            self.assertEqual(last, last2)                         # remembers when it was last busy
        finally:
            sp.time.monotonic = real


class PortalStreams(unittest.TestCase):
    """What `pw-dump` says about screen shares. Hyprland's event socket never replays a share's start."""

    which = staticmethod(lambda name: "/usr/bin/pw-dump")

    @staticmethod
    def node(oid=None, **props):
        return {"id": oid, "type": "PipeWire:Interface:Node", "info": {"props": {k.replace("_", "."): v for k, v in props.items()}}}

    @staticmethod
    def client(oid, **props):
        return {"id": oid, "type": "PipeWire:Interface:Client", "info": {"props": {k.replace("_", "."): v for k, v in props.items()}}}

    @staticmethod
    def dump(*objects, code=0, out=None):
        text = json.dumps(list(objects)) if out is None else out
        return lambda *a, **k: Mock(stdout=text, returncode=code)

    def count(self, run):
        return sp.portal_streams(run=run, which=self.which)

    # What pw_stream_new(core, "xdph-streaming-…", {media.class: Video/Source}) produces inside
    # xdg-desktop-portal-hyprland: the name lands in media.name, node.name is the program's name.
    def xdph_share(self, oid, client_id=90):
        return self.node(oid, media_name=f"xdph-streaming-{oid:x}", node_name="xdg-desktop-portal-hyprland",
                         media_class="Video/Source", client_id=client_id)

    def test_counts_the_portals_real_streams_by_media_name(self):
        run = self.dump(self.client(90, application_name="xdg-desktop-portal-hyprland"),
                        self.xdph_share(101), self.xdph_share(102))
        self.assertEqual(self.count(run), 2)

    def test_the_stream_name_alone_is_enough(self):
        named_media = self.node(1, media_name="xdph-streaming-1a2b")
        named_node = self.node(2, node_name="xdph-streaming-3c4d")
        self.assertEqual(self.count(self.dump(named_media, named_node)), 2)

    def test_a_portal_video_source_counts_whatever_its_name(self):
        by_node = self.node(1, media_name="screen-1", node_name="xdg-desktop-portal-hyprland", media_class="Video/Source")
        by_binary = self.node(2, media_name="screen-2", application_process_binary="xdg-desktop-portal-wlr",
                              media_class="Video/Source")
        by_client = self.node(3, media_name="screen-3", node_name="screen-3", media_class="Video/Source", client_id=77)
        portal = self.client(77, application_process_binary="xdg-desktop-portal-hyprland")
        self.assertEqual(self.count(self.dump(portal, by_node, by_binary, by_client)), 3)

    def test_cameras_audio_and_other_programs_do_not_count(self):
        objects = [
            self.client(60, application_name="WirePlumber"),
            self.node(10, node_name="v4l2_input.pci-0000_00_14.0-usb-0_3_1.0", media_class="Video/Source", client_id=60),
            self.node(11, node_name="alsa_output.speaker", media_class="Audio/Sink", client_id=60),
            # the portal's own audio, if any, is not a screen share
            self.node(12, node_name="xdg-desktop-portal-hyprland", media_class="Audio/Source"),
            self.node(13, node_name="obs", media_name="Screen capture", media_class="Video/Source"),
            self.client(70, application_name="xdph-streaming-lookalike"),   # a client, not a node
        ]
        self.assertEqual(self.count(self.dump(*objects)), 0)
        self.assertEqual(self.count(self.dump()), 0)

    def test_values_that_are_not_text_are_ignored(self):
        odd = self.node(1, media_name=["xdph-streaming-1"], node_name=7, media_class="Video/Source", client_id=[1])
        self.assertEqual(self.count(self.dump(odd)), 0)

    def test_objects_without_props_are_skipped(self):
        run = self.dump(out=json.dumps([{"type": "PipeWire:Interface:Core"}, "stray", None,
                                        {"info": {"props": None}}, {"info": {"props": {"node.name": None}}}]))
        self.assertEqual(self.count(run), 0)

    def test_none_when_pipewire_is_there_but_will_not_say(self):
        self.assertIsNone(self.count(self.dump(self.node(1, media_name="xdph-streaming-1"), code=1)))
        self.assertIsNone(self.count(self.dump(out="not json")))
        self.assertIsNone(self.count(self.dump(out="{}")))

        def hang(*a, **k):
            raise subprocess.TimeoutExpired("pw-dump", 4)

        def gone(*a, **k):
            raise OSError("no such file")
        self.assertIsNone(self.count(hang))
        self.assertIsNone(self.count(gone))

    def test_without_pw_dump_only_the_events_are_left(self):
        run = Mock(side_effect=AssertionError("must not run"))
        self.assertEqual(sp.portal_streams(run=run, which=lambda name: None), 0)


class ShareAlreadyRunning(unittest.TestCase):
    """A watcher that starts, restarts or reconnects mid-share must not show names until it has looked."""

    def daemon(self, probe):
        warning = patch.object(sp, "warn")
        warning.start()
        self.addCleanup(warning.stop)
        daemon = sp.Daemon(sp.Engine(rules=RULES, state_file="/nonexistent"), streams=probe)
        self.addCleanup(daemon.sel.close)
        return daemon

    def first_snapshot(self, daemon):
        """Start the watch loop and return the first thing it tells the bar."""
        class Stop(Exception):
            pass
        sent = []

        def send(message):
            sent.append(message)
            raise Stop
        daemon.send = send
        daemon.e.recompute = lambda *a, **k: []
        daemon.e.snapshot = lambda: {"workspaces": []}
        with patch.object(sp, "active_window", return_value=None), \
                patch.object(sp, "hypr_dir", return_value=None), self.assertRaises(Stop):
            daemon.run()
        return sent[0]

    def test_the_first_line_hides_names_when_a_share_began_before_we_did(self):
        daemon = self.daemon(lambda: 1)
        first = self.first_snapshot(daemon)
        self.assertTrue(first["sharing"])
        self.assertFalse(daemon.casts)        # no event was ever seen: this came from the portal

    def test_the_first_line_shows_names_when_nothing_is_shared(self):
        self.assertFalse(self.first_snapshot(self.daemon(lambda: 0))["sharing"])

    def test_the_first_line_assumes_a_share_when_pipewire_will_not_say(self):
        daemon = self.daemon(lambda: None)
        self.assertTrue(self.first_snapshot(daemon)["sharing"])
        self.assertTrue(daemon.warned_streams)

    def test_names_come_back_once_pipewire_answers(self):
        answers = iter([None, 0])
        daemon = self.daemon(lambda: next(answers))
        daemon.streams = None
        daemon.check_streams(100.0)
        self.assertTrue(daemon.sharing())
        self.assertEqual(daemon.next_streams, 100.0 + daemon.STREAMS_RETRY)
        daemon.check_streams(102.0)
        self.assertFalse(daemon.sharing())
        self.assertEqual(daemon.next_streams, 102.0 + daemon.STREAMS_EVERY)
        self.assertFalse(daemon.warned_streams)

    def test_one_failed_look_after_an_answer_does_not_flip_the_bar(self):
        answers = iter([0, None, 1])
        daemon = self.daemon(lambda: next(answers))
        daemon.streams = None
        daemon.check_streams(0.0)
        daemon.check_streams(5.0)
        self.assertFalse(daemon.sharing())    # kept the last answer through the hiccup
        self.assertEqual(daemon.next_streams, 5.0 + daemon.STREAMS_RETRY)
        daemon.check_streams(7.0)
        self.assertTrue(daemon.sharing())

    def test_a_share_that_ends_is_noticed_without_any_event(self):
        answers = iter([2, 0])
        daemon = self.daemon(lambda: next(answers))
        daemon.check_streams(0.0)
        self.assertTrue(daemon.sharing())
        daemon.check_streams(5.0)
        self.assertFalse(daemon.sharing())

    def test_either_source_alone_is_enough_to_hide(self):
        daemon = self.daemon(lambda: 0)
        self.assertFalse(daemon.sharing())
        daemon.casts = {"monitor": 1}
        self.assertTrue(daemon.sharing())
        daemon.casts = {}
        daemon.streams = 1
        self.assertTrue(daemon.sharing())

    def test_reconnecting_the_event_socket_asks_pipewire_again(self):
        daemon = self.daemon(lambda: 0)
        daemon.next_streams = 999.0
        with patch.object(sp, "hypr_dir", return_value=None):
            daemon.connect()
        self.assertEqual(daemon.next_streams, 0.0)


class PrivateAlerts(unittest.TestCase):
    """What an alert says (the project, what the agent asked) must not land on a command line, which
    every account can read in /proc, and must not stay up once the screen is shared."""

    class FakeBus:
        def __init__(self):
            self.sent, self.closed = [], []

        def notify(self, values):
            sp.bus_args(sp.NOTIFY_SIGNATURE, values)        # what sd-bus would be handed must fit
            self.sent.append(values)
            return 7 + len(self.sent)

        def close_notification(self, nid):
            self.closed.append(nid)

        def close(self):
            pass

    def daemon(self, bus):
        warning = patch.object(sp, "warn")
        warning.start()
        self.addCleanup(warning.stop)
        daemon = sp.Daemon(sp.Engine(rules=RULES, state_file="/nonexistent"), streams=lambda: 0, bus=bus)
        self.addCleanup(daemon.sel.close)
        daemon.watch_alerts = lambda: None
        return daemon

    def test_alerts_go_over_the_bus_and_never_through_a_command(self):
        bus = self.FakeBus()
        daemon = self.daemon(lambda: bus)
        with patch.object(sp.subprocess, "run", side_effect=AssertionError("no subprocess")):
            daemon.notify("acme-merger needs you", "Workspace 3 · Allow Bash: curl -H 'Authorization: Bearer x'", 3)
        self.assertEqual(bus.sent[0][3], "acme-merger needs you")
        self.assertEqual(daemon.alerts, {8: {"ws": 3, "address": "", "pid": None}})

    def test_without_sd_bus_the_fallback_says_nothing_private(self):
        def no_bus():
            raise OSError("no libsystemd")
        daemon = self.daemon(no_bus)
        seen = []
        with patch.object(sp.subprocess, "run", side_effect=lambda argv, **k: seen.append(argv) or Mock(stdout="u 9\n")):
            daemon.notify("acme-merger needs you", "Workspace 3 · Allow Bash: curl secret", 3, "/tmp/acme.svg")
            daemon.notify("Signpost is on your bar twice over", "Replace them?", 0, actions=[("replace", "Replace")],
                          private=False)
        first = " ".join(seen[0])
        for secret in ("acme", "curl", "secret"):
            self.assertNotIn(secret, first)
        self.assertIn("An agent needs you", first)
        self.assertIn("Workspace 3", first)
        self.assertIn("Replace them?", " ".join(seen[1]))           # nothing private there: sent as it is
        self.assertEqual(daemon.alerts[9], {"hint": True})

    def test_alerts_close_when_a_share_starts(self):
        bus = self.FakeBus()
        daemon = self.daemon(lambda: bus)
        daemon.notify("a needs you", "Workspace 3", 3)
        daemon.notify("b needs you", "Workspace 4", 4)
        daemon.close_alerts_for_sharing()
        self.assertEqual(bus.closed, [])                            # not sharing: they stay
        daemon.casts = {"monitor": 1}
        daemon.close_alerts_for_sharing()
        self.assertEqual(sorted(bus.closed), [8, 9])
        self.assertEqual(daemon.alerts, {})                         # a late press can't jump either

    def test_bus_arguments_follow_the_signature(self):
        args = sp.bus_args("sa{sv}as", ["x", "2", "urgency", "y", "1", "transient", "b", "true", "0"])
        kinds = [type(a).__name__ for a in args]
        self.assertEqual(kinds, ["c_char_p", "c_uint", "c_char_p", "c_char_p", "c_int", "c_char_p", "c_char_p",
                                 "c_int", "c_uint"])
        with self.assertRaises(ValueError):
            sp.bus_args("ss", ["only one"])
        with self.assertRaises(ValueError):
            sp.bus_args("s", ["one", "two"])


class CaptureDetection(unittest.TestCase):
    def daemon(self, probe):
        warning = patch.object(sp, "warn")
        warning.start()
        self.addCleanup(warning.stop)
        daemon = sp.Daemon(sp.Engine(rules=RULES, state_file="/nonexistent"), streams=probe)
        self.addCleanup(daemon.sel.close)
        return daemon

    def test_recorders_and_remote_desktop_are_found_by_their_executable(self):
        with tempfile.TemporaryDirectory() as proc:
            def fake(pid, exe):
                os.makedirs(os.path.join(proc, pid))
                os.symlink(exe, os.path.join(proc, pid, "exe"))
            fake("10", "/usr/bin/gpu-screen-recorder")
            fake("11", "/usr/bin/gliff-server (deleted)")              # upgraded while running
            fake("12", "/usr/bin/wf-recorder")
            fake("13", "/usr/bin/kitty")
            fake("14", "/home/u/bin/not-gpu-screen-recorder")
            os.makedirs(os.path.join(proc, "self"))
            self.assertEqual(sp.capture_processes(proc, uid=os.getuid()), 3)
            self.assertEqual(sp.capture_processes(proc, uid=os.getuid() + 1), 0)   # another account's: its own desktop
        self.assertEqual(sp.screen_captures(lambda: 1, lambda: 2), 3)
        self.assertIsNone(sp.screen_captures(lambda: None, lambda: 2))

    def test_a_pause_in_frames_does_not_bring_names_back(self):
        daemon = self.daemon(lambda: 0)
        daemon.sock = Mock()
        daemon.sock.recv.side_effect = [b"screencast>>1,monitor\n", b"screencast>>0,monitor\n"]
        daemon.read_events()
        self.assertTrue(daemon.sharing())
        self.assertEqual(daemon.next_streams, 0.0)                      # looked at again at once
        daemon.read_events()
        self.assertFalse(daemon.casts)
        self.assertTrue(daemon.sharing())                               # Hyprland said stop: not yet
        self.assertTrue(daemon.sharing(daemon.cast_ended + daemon.SHARE_GRACE - 0.1))
        self.assertFalse(daemon.sharing(daemon.cast_ended + daemon.SHARE_GRACE + 0.1))
        daemon.streams = 1                                              # a recorder or portal stream still runs
        self.assertTrue(daemon.sharing(daemon.cast_ended + 60))


class RenameIsBoundToWhatWasShown(unittest.TestCase):
    def test_a_rename_for_a_project_that_moved_off_the_workspace_renames_nothing(self):
        state = {"pins": {}, "aliases": {}, "recent": []}
        results = {2: {"aliasKey": "/p/Other", "aliasRepo": "Other"}}
        msg = sp.apply_command(state, {"cmd": "alias", "ws": 2, "name": "card", "key": "/p/Starling"}, results)
        self.assertIn("changed", msg)
        self.assertEqual(state["aliases"], {})
        sp.apply_command(state, {"cmd": "alias", "ws": 2, "name": "card", "key": "/p/Other"}, results)
        self.assertEqual(state["aliases"], {"/p/Other": "card"})
        sp.apply_command(state, {"cmd": "unalias", "ws": 2, "key": "/p/Starling"}, results)
        self.assertEqual(state["aliases"], {"/p/Other": "card"})           # not that one either
        sp.apply_command(state, {"cmd": "alias", "ws": 2, "name": "ipc"}, results)   # no editor: what is there
        self.assertEqual(state["aliases"], {"/p/Other": "ipc"})


class StateEditsFromSeveralBars(unittest.TestCase):
    def test_two_bars_pinning_at_once_keep_both_pins(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            left = sp.Engine(rules=RULES, state_file=path)
            right = sp.Engine(rules=RULES, state_file=path)         # read the file before left's pin
            left.edit_state(lambda s: sp.apply_command(s, {"cmd": "pin", "ws": 1, "name": "inbox"}, {}))
            right.edit_state(lambda s: sp.apply_command(s, {"cmd": "pin", "ws": 2, "name": "mail"}, {}))
            self.assertEqual(sp.load_state(path)["pins"], {"1": "inbox", "2": "mail"})
            # a colour-slot save from a bar that hasn't seen either pin keeps them too
            stale = sp.Engine(rules=RULES, state_file=path)
            stale.state["pins"] = {}
            stale.results = {1: {"_colorKey": "/p/new", "_projects": []}}
            stale.history.entries = []
            stale.give_slots()
            self.assertEqual(sp.load_state(path)["pins"], {"1": "inbox", "2": "mail"})
            self.assertEqual(os.stat(path + ".lock").st_mode & 0o777, 0o600)


class SecondReview(unittest.TestCase):
    def test_a_stale_entry_in_any_folder_is_reported_before_installed(self):
        with tempfile.TemporaryDirectory() as d:
            good, old = os.path.join(d, "good"), os.path.join(d, "old")
            script = os.path.join(d, "signpost.py")
            open(script, "w").close()
            for folder, cmd in ((good, sp.hook_command(script)), (old, f"python3 -I {script} hook")):
                os.makedirs(folder)
                with open(os.path.join(folder, "settings.json"), "w") as f:
                    json.dump(sp.with_hooks({}, cmd), f)
            self.assertEqual(sp.hook_install_state([good, old]), ("partial", [sp.collapse_home(old)]))

    def test_deleting_through_a_symlink_removes_the_link_only(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d, "dotfiles-shell.json")
            target.write_text("{}")
            link = Path(d, "shell.json")
            link.symlink_to(target)
            sp.atomic_write(link, None)
            self.assertFalse(link.is_symlink())
            self.assertTrue(target.exists())

    def test_percent_escapes_are_not_icon_paths(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d, "%66ifo.png")
            real.write_bytes(b"x")
            self.assertEqual(sp.safe_icon(str(real)), "")
            self.assertEqual(sp.safe_icon(d + "/a?.png"), "")

    def test_a_local_wezterm_mux_pane_keeps_its_folder(self):
        tty = os.ttyname(0) if os.isatty(0) else None
        self.assertFalse(sp.own_tty(None))
        self.assertFalse(sp.own_tty("/dev/null"))
        self.assertFalse(sp.own_tty("/etc/passwd"))
        if tty and tty.startswith("/dev/pts/"):
            self.assertTrue(sp.own_tty(tty))

    def test_a_failed_bus_call_opens_a_new_connection_next_time(self):
        opened = []

        class Broken:
            def notify(self, values):
                raise OSError("connection reset")

            def close(self):
                opened.append("closed")
        warning = patch.object(sp, "warn")
        warning.start()
        self.addCleanup(warning.stop)
        daemon = sp.Daemon(sp.Engine(rules=RULES, state_file="/nonexistent"), streams=lambda: 0,
                           bus=lambda: opened.append("open") or Broken())
        self.addCleanup(daemon.sel.close)
        daemon.notify("t", "b", 3)
        daemon.notify("t", "b", 3)
        self.assertEqual(opened, ["open", "closed", "open", "closed"])

    def test_a_capture_start_is_sent_before_anything_else(self):
        warning = patch.object(sp, "warn")
        warning.start()
        self.addCleanup(warning.stop)
        daemon = sp.Daemon(sp.Engine(rules=RULES, state_file="/nonexistent"), streams=lambda: 0)
        self.addCleanup(daemon.sel.close)
        sent = []
        daemon.e.snapshot = lambda: {"workspaces": []}
        daemon.send = lambda message: sent.append(message)
        daemon.sock = Mock()
        daemon.sock.recv.side_effect = [b"screencast>>1,monitor\n"]
        daemon.read_events()
        self.assertEqual([m["sharing"] for m in sent], [True])


class Commands(unittest.TestCase):
    def test_stopping_one_capture_keeps_other_captures_hidden(self):
        daemon = sp.Daemon(sp.Engine(rules=RULES, state_file="/nonexistent"))
        self.addCleanup(daemon.sel.close)
        daemon.sock = Mock()
        daemon.sock.recv.side_effect = [b"screencast>>1,monitor\nscreencast>>1,monitor\nscreencast>>1,window\n",
                                        b"screencast>>0,monitor\nscreencast>>0,window\n",
                                        b"screencast>>0,monitor\n"]
        daemon.read_events()
        daemon.read_events()
        self.assertEqual(daemon.casts, {"monitor": 1})
        daemon.read_events()
        self.assertFalse(daemon.casts)

    def test_pin_alias_unpin(self):
        state = {"pins": {}, "aliases": {}, "recent": []}
        results = {2: {"aliasKey": "/p/Starling", "aliasRepo": "Starling", "aliasWt": "d1"}}
        sp.apply_command(state, {"cmd": "pin", "ws": 3, "name": "inbox"}, results)
        self.assertEqual(state["pins"], {"3": "inbox"})
        sp.apply_command(state, {"cmd": "alias", "ws": 2, "name": "card · d1"}, results)
        self.assertEqual(state["aliases"], {"/p/Starling": "card"})
        sp.apply_command(state, {"cmd": "unpin", "ws": 3}, results)
        self.assertEqual(state["pins"], {})
        self.assertEqual(state["recent"][:2], ["card", "inbox"])
        self.assertIn("no project", sp.apply_command(state, {"cmd": "alias", "ws": 9, "name": "x"}, results))

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s", "state.json")
            sp.save_state({"pins": {"3": "inbox"}, "aliases": {}, "recent": ["inbox"]}, path)
            self.assertEqual(sp.load_state(path)["pins"], {"3": "inbox"})
            self.assertEqual(sp.load_state(os.path.join(d, "missing.json"))["pins"], {})


if __name__ == "__main__":
    unittest.main()


class ColorSlots(unittest.TestCase):
    """Remembered colours: the first projects you open get different ones, and keep them."""

    def facts(self, name="t3-title-picks-agent"):
        return load_fixture(os.path.join(ROOT, "tests", "fixtures", f"{name}.json"))["facts"]

    def engine(self, d):
        engine = sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json"))
        engine.history = sp.ProjectHistory(os.path.join(d, "recent-projects.json"))
        return engine

    def test_first_projects_get_distinct_slots_in_order(self):
        state = {"colors": {}, "nextSlot": 0}
        self.assertTrue(sp.assign_slots(state, ["/p/a", "/p/b", "/p/a", None, "/p/c"]))
        self.assertEqual(state["colors"], {"/p/a": 0, "/p/b": 1, "/p/c": 2})
        self.assertFalse(sp.assign_slots(state, ["/p/b"]))               # known: unchanged
        self.assertEqual(state["nextSlot"], 3)

    def test_slots_survive_a_restart_and_reach_rows_and_workspaces(self):
        with tempfile.TemporaryDirectory() as d:
            first = self.engine(d)
            first.results = sp.decide(self.facts(), {}, {}, RULES)
            first.give_slots()
            ws = first.results[3]
            rows = {r["name"]: r["slot"] for r in ws["_projects"]}
            self.assertEqual(len(set(rows.values())), len(rows))             # different projects, different slots
            self.assertEqual(ws["slot"], rows[ws["full"]])                    # the workspace wears its project's slot
            again = self.engine(d)
            again.results = sp.decide(self.facts(), {}, {}, RULES)
            again.give_slots()
            self.assertEqual({r["name"]: r["slot"] for r in again.results[3]["_projects"]}, rows)
            again.holder.update(again.results, 0.0, again.settings, force=True)
            snap = again.snapshot()
            self.assertEqual({r["name"]: r["slot"] for r in snap["projects"]}, rows)
            self.assertFalse(any(k.startswith("_") for r in snap["projects"] for k in r))
            self.assertIn("slot", snap["workspaces"][0])

    def test_worktree_shares_its_repos_slot(self):
        main = sp.decide(self.facts("kitty-agent-in-worktree"), {}, {}, RULES)[1]
        self.assertEqual(main["_colorKey"], main["aliasKey"])                  # the main checkout, not the worktree
        state = {"colors": {}, "nextSlot": 0}
        sp.assign_slots(state, [main["_colorKey"], main["_projects"][0]["_colorKey"]])
        self.assertEqual(len(state["colors"]), 1)

    def test_pins_and_aliases_do_not_recolour(self):
        facts = self.facts("kitty-agent-in-worktree")
        plain = sp.decide(facts, {}, {}, RULES)[1]
        key = plain["aliasKey"]
        renamed = sp.decide(facts, {"aliases": {key: "Client app"}}, {}, RULES)[1]
        pinned = sp.decide(facts, {"pins": {"1": "Inbox"}}, {}, RULES)[1]
        self.assertEqual({plain["_colorKey"], renamed["_colorKey"], pinned["_colorKey"]}, {key})

    def test_old_state_files_load_and_bad_values_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w") as f:
                json.dump({"pins": {"2": "Inbox"}, "aliases": {}, "recent": ["x"]}, f)   # before colours existed
            state = sp.load_state(path)
            self.assertEqual((state["colors"], state["nextSlot"], state["pins"]), ({}, 0, {"2": "Inbox"}))
            with open(path, "w") as f:
                json.dump({"colors": {"/a": 4, "/b": -1, "/c": "x", "": 1, "/d": True}, "nextSlot": 2}, f)
            state = sp.load_state(path)
            self.assertEqual(state["colors"], {"/a": 4})
            self.assertEqual(state["nextSlot"], 5)                             # never hands out a slot in use
            with open(path, "w") as f:
                f.write("[1, 2]")
            self.assertEqual(sp.load_state(path)["colors"], {})

    def test_the_oldest_are_forgotten_past_the_cap(self):
        state = {"colors": {}, "nextSlot": 0}
        sp.assign_slots(state, [f"/p/{i}" for i in range(sp.COLOR_LIMIT + 5)])
        self.assertEqual(len(state["colors"]), sp.COLOR_LIMIT)
        self.assertNotIn("/p/0", state["colors"])
        self.assertEqual(state["colors"][f"/p/{sp.COLOR_LIMIT + 4}"], sp.COLOR_LIMIT + 4)

    def test_saving_keeps_colours_another_bar_handed_out(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            mine = self.engine(d)
            sp.save_state({"pins": {}, "aliases": {}, "recent": [], "colors": {"/other": 0}, "nextSlot": 1}, path)
            mine.state["colors"] = {"/mine": 0}
            mine.state["pins"] = {"3": "Inbox"}
            mine.save()
            saved = sp.load_state(path)
            self.assertEqual(saved["colors"], {"/mine": 0, "/other": 0})
            self.assertEqual((saved["pins"], saved["nextSlot"]), ({"3": "Inbox"}, 1))


class PrivateFiles(unittest.TestCase):
    """Session files name your projects and quote your agents: only you can read them."""

    def mode(self, path):
        return os.stat(path).st_mode & 0o777

    def test_hook_files_and_folder_are_private(self):
        with tempfile.TemporaryDirectory() as d:
            agents = os.path.join(d, "agents")
            sp.write_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/p"}, agents)
            self.assertEqual(self.mode(agents), 0o700)
            self.assertEqual(self.mode(os.path.join(agents, "s1.json")), 0o600)

    def test_an_open_folder_is_tightened(self):
        with tempfile.TemporaryDirectory() as d:
            agents = os.path.join(d, "agents")
            os.mkdir(agents, 0o755)
            os.chmod(agents, 0o755)
            sp.AgentSignals(agents).read(now=1)                              # the engine just looking at it
            self.assertEqual(self.mode(agents), 0o700)
            os.chmod(agents, 0o755)
            sp.write_hook({"hook_event_name": "Stop", "session_id": "s2", "cwd": "/p"}, agents)
            self.assertEqual(self.mode(agents), 0o700)

    def test_state_file_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "signpost", "state.json")
            sp.save_state({"pins": {"1": "x"}}, path)
            self.assertEqual((self.mode(path), self.mode(os.path.dirname(path))), (0o600, 0o700))
            self.assertEqual(sp.load_state(path)["pins"], {"1": "x"})


class HookStatus(unittest.TestCase):
    def write(self, d, settings):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump(settings, f)

    def script(self, d):
        path = os.path.join(d, "plugin", "signpost.py")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def test_reports_installed_partial_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            full, part, bare, old = (os.path.join(d, n) for n in ("full", "part", "bare", "old"))
            cmd = sp.hook_command(self.script(d))
            self.write(full, sp.with_hooks({}, cmd))
            some = sp.with_hooks({}, cmd)
            some["hooks"] = {k: v for k, v in some["hooks"].items() if k == "Stop"}
            self.write(part, some)
            self.write(bare, {"theme": "dark"})
            self.assertEqual(sp.hook_install_state([full, bare]), ("installed", [sp.collapse_home(full)]))
            self.assertEqual(sp.hook_install_state([part])[0], "partial")
            self.assertEqual(sp.hook_install_state([bare, os.path.join(d, "missing")]), ("none", []))

    def test_an_install_from_signpost_0_3_is_partial_and_install_adds_the_rest_once(self):
        with tempfile.TemporaryDirectory() as d:
            cmd = sp.hook_command(sp.os.path.realpath(sp.__file__))
            old = {"hooks": {e: [{"hooks": [{"type": "command", "command": cmd}]}] for e in sp.HOOK_EVENTS_V1}}
            old["hooks"]["Stop"].insert(0, {"hooks": [{"type": "command", "command": "notify-send done"}]})
            self.write(d, old)
            self.assertEqual(sp.hook_install_state([d])[0], "partial")
            out = io.StringIO()
            with redirect_stdout(out):
                sp.hooks_cli(["status", "--config-dir", d])
            self.assertIn("partly installed (5 of 10 events", out.getvalue())
            self.assertIn("PermissionRequest", out.getvalue())
            with redirect_stdout(io.StringIO()):
                sp.hooks_cli(["install", "--config-dir", d])
            with open(os.path.join(d, "settings.json")) as f:
                new = json.load(f)
            for event in sp.HOOK_EVENTS:
                self.assertEqual(sum(sp._ours(g) for g in new["hooks"][event]), 1, event)
            self.assertEqual(new["hooks"]["Stop"][0], old["hooks"]["Stop"][0])          # theirs kept, first
            self.assertEqual(sp.hook_install_state([d])[0], "installed")

    def run_installed(self, command, how, payload=b'{"hook_event_name":"Stop","session_id":"s"}'):
        """Run an installed hook command as an agent would: through a shell, or split into words."""
        argv = ["sh", "-c", command] if how == "shell" else shlex.split(command)
        return subprocess.run(argv, input=payload, capture_output=True, timeout=20)

    def test_a_removed_plugin_never_blocks_the_agent(self):
        # python3 exits 2 for a missing script, and 2 is "block" to Claude Code (prompts refused, permissions
        # denied). The installed command must say 0 instead, whichever way the agent runs it.
        with tempfile.TemporaryDirectory() as d:
            gone = os.path.join(d, "removed-plugin", "signpost.py")
            self.assertEqual(subprocess.run([sys.executable, "-I", gone, "hook"], capture_output=True).returncode, 2)
            for agent in ("claude", "codex"):
                for how in ("shell", "words"):
                    with self.subTest(agent=agent, how=how):
                        r = self.run_installed(sp.hook_command(gone, agent), how)
                        self.assertEqual((r.returncode, r.stdout), (0, b""))

    def test_the_installed_command_still_runs_the_hook(self):
        with tempfile.TemporaryDirectory() as d:
            script = self.script(d)
            with open(script, "w") as f:      # stands in for signpost.py: records what it was given
                f.write("import sys\nopen(sys.argv[0] + '.ran', 'w').write(' '.join(sys.argv[1:]) + '|' + sys.stdin.read())\n"
                        "print('{}')\nsys.exit(2)\n")
            for agent, args in (("claude", "hook"), ("codex", "hook --agent codex")):
                for how in ("shell", "words"):
                    with self.subTest(agent=agent, how=how):
                        r = self.run_installed(sp.hook_command(script, agent), how)
                        self.assertEqual(r.returncode, 0)                 # even when the hook itself fails
                        self.assertEqual(r.stdout, b"{}\n")               # Codex's Stop reply still gets through
                        with open(script + ".ran") as f:
                            self.assertEqual(f.read(), args + '|{"hook_event_name":"Stop","session_id":"s"}')

    def test_odd_folder_names_stay_one_argument(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "it's a \"dir\"; $(touch pwned) `x`", "signpost.py")
            os.makedirs(os.path.dirname(script))
            with open(script, "w") as f:
                f.write("import sys\nopen(sys.argv[0] + '.ran', 'w').write('ok')\n")
            for how in ("shell", "words"):
                r = self.run_installed(sp.hook_command(script), how)
                self.assertEqual(r.returncode, 0)
                self.assertTrue(os.path.exists(script + ".ran"))
                os.unlink(script + ".ran")
            self.assertNotIn("pwned", os.listdir(d))
            self.assertNotIn("pwned", os.listdir(os.path.dirname(script)))
            self.assertFalse(os.path.exists(os.path.join(os.getcwd(), "pwned")))

    def test_old_and_broken_entries_are_found_and_reinstall_replaces_them(self):
        with tempfile.TemporaryDirectory() as d:
            script = self.script(d)
            legacy = f"python3 -I {shlex.quote(script)} hook"         # what Signpost 0.5.0 installed
            self.write(d, {"hooks": {e: [{"hooks": [{"type": "command", "command": legacy}]}] for e in sp.HOOK_EVENTS}})
            self.assertEqual(sp.hook_install_state([d])[0], "partial")
            out = io.StringIO()
            with redirect_stdout(out):
                sp.hooks_cli(["status", "--config-dir", d])
            self.assertIn("needs `hooks install` again", out.getvalue())
            with redirect_stdout(io.StringIO()):
                sp.hooks_cli(["install", "--config-dir", d])
            with open(os.path.join(d, "settings.json")) as f:
                new = json.load(f)
            for event in sp.HOOK_EVENTS:
                commands = [h["command"] for g in new["hooks"][event] for h in g["hooks"]]
                self.assertEqual(commands, [sp.hook_command()], event)      # the old one is gone, not doubled
            # an entry left pointing at a plugin that was removed is flagged too
            gone = sp.with_hooks({}, sp.hook_command(os.path.join(d, "nowhere", "signpost.py")))
            self.assertEqual(sp.stale_handlers(gone), [os.path.join(d, "nowhere", "signpost.py")])
            with redirect_stdout(io.StringIO()):
                sp.hooks_cli(["remove", "--config-dir", d])
            with open(os.path.join(d, "settings.json")) as f:
                self.assertEqual(sp.hooks_installed(json.load(f)), [])

    def test_install_goes_to_the_folder_in_use_only(self):
        with tempfile.TemporaryDirectory() as home:
            for name in (".claude", ".claude-old"):
                os.makedirs(os.path.join(home, name))
            self.write(os.path.join(home, ".claude-old"), {"theme": "dark"})      # only the old one has settings
            with patch.object(sp, "HOME", home), patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
                self.assertEqual(sp.hook_dirs("claude", "install"), [os.path.join(home, ".claude")])
                self.assertIn(os.path.join(home, ".claude-old"), sp.hook_dirs("claude", "status"))
                os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(home, "work")
                self.assertEqual(sp.hook_dirs("claude", "install"), [os.path.join(home, "work")])
                # remove also cleans a folder an older Signpost installed into
                self.write(os.path.join(home, ".claude-old"), sp.with_hooks({}, sp.hook_command(self.script(home))))
                self.assertEqual(sp.hook_dirs("claude", "remove"),
                                 [os.path.join(home, "work"), os.path.join(home, ".claude-old")])

    def test_first_backup_is_the_original_and_a_concurrent_edit_is_not_lost(self):
        with tempfile.TemporaryDirectory() as d:
            original = {"theme": "dark"}
            self.write(d, original)
            path = os.path.join(d, "settings.json")
            with redirect_stdout(io.StringIO()):
                sp.hooks_cli(["install", "--config-dir", d])
                sp.hooks_cli(["remove", "--config-dir", d])
                sp.hooks_cli(["install", "--config-dir", d])
            with open(path + ".signpost-backup") as f:
                self.assertEqual(json.load(f), original)                   # still the pre-Signpost file
            # someone saves the file between Signpost's read and its write: refuse, keep their save
            real = sp.read_bytes
            calls = []

            def racing(p):
                calls.append(p)
                if p == path and len([c for c in calls if c == path]) == 2:
                    with open(path, "w") as f:
                        json.dump({"theme": "light"}, f)
                return real(p)
            with patch.object(sp, "read_bytes", racing), self.assertRaises(ValueError):
                sp.json_hook_config("remove", d, "claude")
            with open(path) as f:
                self.assertEqual(json.load(f), {"theme": "light"})

    def test_summary_says_where_needs_you_comes_from(self):
        short = lambda hook, view: sp.claude_summary(hook, view, script="/x/signpost.py")[0]
        self.assertEqual(short(("installed", ["~/.claude"]), True), "claude: hook installed + agents view")
        self.assertEqual(short(("none", []), True), "claude: agents view")
        self.assertEqual(short(("partial", ["~/.claude"]), False), "claude: hook partly installed")
        self.assertEqual(short(("none", []), False), "claude: none")
        detail = sp.claude_summary(("none", []), True, script="/x/signpost.py")[1]
        self.assertIn("claude agents --json", detail)
        self.assertIn("python3 -I /x/signpost.py hooks install", detail)

    def test_explain_and_snapshot_carry_the_line(self):
        engine = sp.Engine(rules=RULES, state_file="/nonexistent/state.json")
        engine.hook = ("none", [])
        engine.view.command = ""
        snap = engine.snapshot()
        self.assertEqual(snap["claude"], "claude: none")
        self.assertEqual(sp.explain_lines(snap)[-1], snap["claudeDetail"])


class AlertPicture(unittest.TestCase):
    def test_the_project_tile_or_signposts_icon(self):
        with tempfile.TemporaryDirectory() as d:
            palette = sp.clean_palette([["#AABBCC", "#101010"], ["#112233", "#f0f0f0"], ["red", "#000000"], "x"])
            self.assertEqual(palette, [["#aabbcc", "#101010"], ["#112233", "#f0f0f0"]])
            path = sp.alert_picture(3, "C", palette, d)                     # slot 3 of 2 colours: the second
            self.assertTrue(path.startswith(d) and path.endswith(".svg"))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with open(path) as f:
                svg = f.read()
            self.assertIn('fill="#112233"', svg)
            self.assertIn("&#67;", svg)                                       # the letter, as a character reference
            self.assertEqual(sp.alert_picture(None, "C", palette, d), sp.SIGNPOST_ICON)
            self.assertEqual(sp.alert_picture(1, "C", [], d), sp.SIGNPOST_ICON)
            self.assertNotIn("<", sp.alert_picture(0, "<", palette, d).rsplit("/", 1)[1])

    def test_the_picture_travels_as_an_image_path_hint(self):
        values = sp.notify_values("t", "b", jump=True, image="/home/u/icon.png")
        self.assertEqual(values[-11:], ["3", "urgency", "y", "1", "transient", "b", "true",
                                        "image-path", "s", "/home/u/icon.png", "10000"])
        self.assertEqual(sp.notify_values("t", "b", jump=True, image="relative.png")[-8:],
                         ["2", "urgency", "y", "1", "transient", "b", "true", "10000"])


# The shape `claude agents --json` printed on the author's machine (Claude Code 2.1.318), with the
# names, folders and ids replaced. Only "busy" was seen live; "waiting" / "idle" and waitingFor are
# from the agent-view reference.
VIEW_BUSY = [{"pid": 4242, "cwd": "/p/Arc", "kind": "interactive", "startedAt": 1795235209178,
              "sessionId": "11111111-aaaa", "name": "fix the login", "status": "busy"}]
VIEW_MIXED = [
    {"pid": 4242, "cwd": "/p/Arc", "kind": "interactive", "startedAt": 1795235209178, "sessionId": "s-wait",
     "name": "fix the login", "status": "waiting", "waitingFor": "permission prompt"},
    {"pid": 4343, "cwd": "/p/Bolt", "kind": "interactive", "startedAt": 1795235209999, "sessionId": "s-idle",
     "name": "docs", "status": "idle"},
    {"cwd": "/p/Gone", "kind": "background", "startedAt": 1, "id": "bg1", "state": "blocked"},     # no process
    {"pid": 4444, "cwd": "/p/New", "kind": "interactive", "startedAt": 1, "sessionId": "s-odd", "status": "thinking"},
]


class AgentsViewSource(unittest.TestCase):
    def test_parses_the_documented_list_and_skips_what_it_does_not_know(self):
        got = sp.parse_agents_view(json.dumps(VIEW_MIXED))
        self.assertEqual([(s["session"], s["status"], s["waitingFor"]) for s in got],
                         [("s-wait", "waiting", "permission prompt"), ("s-idle", "idle", "")])
        self.assertEqual(got[0]["startedAt"], 1795235209.178)
        self.assertEqual(sp.parse_agents_view(json.dumps(VIEW_BUSY))[0]["name"], "fix the login")
        for bad in ("", "not json", "{}", '{"sessions": []}', "null", '"x"'):
            self.assertIsNone(sp.parse_agents_view(bad), bad)
        self.assertEqual(sp.parse_agents_view("[]"), [])
        self.assertEqual(sp.parse_agents_view('[1, "x", {"pid": true, "status": "busy"}]'), [])

    def test_config_folder_comes_from_the_process(self):
        self.assertEqual(sp.claude_config_dir(os.getpid(), home="/h"),
                         os.path.normpath(os.environ["CLAUDE_CONFIG_DIR"]) if os.path.isabs(os.environ.get("CLAUDE_CONFIG_DIR", ""))
                         else "/h/.claude")
        self.assertIsNone(sp.claude_config_dir(999999999))

    def test_session_files_stamp_changes_when_one_is_rewritten(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sp.sessions_stamp(d))
            os.mkdir(os.path.join(d, "sessions"))
            Path(d, "sessions", "1.json").write_text("{}")
            first = sp.sessions_stamp(d)
            os.utime(os.path.join(d, "sessions", "1.json"), ns=(1, 1))
            self.assertNotEqual(sp.sessions_stamp(d), first)               # unreadable as a session: by time
            busy = {"pid": 1, "status": "busy", "updatedAt": 1}
            Path(d, "sessions", "1.json").write_text(json.dumps(busy))
            first = sp.sessions_stamp(d)
            Path(d, "sessions", "1.json").write_text(json.dumps({**busy, "updatedAt": 2, "name": "x"}))
            self.assertEqual(sp.sessions_stamp(d), first)                  # busy and still busy: no need to ask
            Path(d, "sessions", "1.json").write_text(json.dumps({**busy, "status": "idle"}))
            self.assertEqual(sp.sessions_stamp(d), first)                  # a turn ended: the 2-minute look will see it
            Path(d, "sessions", "2.json").write_text(json.dumps(busy))
            self.assertNotEqual(sp.sessions_stamp(d), first)               # a new session: ask
            os.unlink(os.path.join(d, "sessions", "2.json"))
            Path(d, "sessions", "1.json").write_text(json.dumps({**busy, "status": "waiting", "waitingFor": "permission prompt"}))
            self.assertNotEqual(sp.sessions_stamp(d), first)

    def view(self, d, outputs, calls):
        def run(argv, **kw):
            calls.append(kw["env"].get("CLAUDE_CONFIG_DIR"))
            out = outputs.pop(0) if outputs else ""
            if isinstance(out, Exception):
                raise out
            return subprocess.CompletedProcess(argv, 0, out, "")
        view = sp.AgentsView(os.path.join(d, "view.json"), run=run, which=lambda name: "/bin/claude",
                             environ={"PATH": "/bin", "CLAUDE_CONFIG_DIR": "/leak", "CLAUDECODE": "1"})
        view.active = [os.path.join(d, "cfg")]
        return view

    def test_fetch_keeps_when_a_wait_began_and_runs_only_when_due(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            view = self.view(d, [json.dumps(VIEW_MIXED), json.dumps(VIEW_MIXED), json.dumps(VIEW_BUSY)], calls)
            cfg = view.active[0]
            os.makedirs(os.path.join(cfg, "sessions"))
            view.refresh([cfg])
            self.assertEqual(calls, [cfg])                                  # the folder's own list, no inherited env
            first = {s["session"]: s for s in view.sessions()}
            since = first["s-wait"]["waitingSince"]
            self.assertFalse(view.due(view.load()["dirs"][cfg], sp.sessions_stamp(cfg), time.time()))
            Path(cfg, "sessions", "4242.json").write_text("{}")           # a session changed: look again
            entry = dict(view.load()["dirs"][cfg], fetchedAt=time.time() - 2)
            self.assertTrue(view.due(entry, sp.sessions_stamp(cfg), time.time()))
            view.data["dirs"][cfg]["fetchedAt"] -= 2
            write = sp.write_private(view.path, json.dumps(view.data))
            view.refresh([cfg])
            self.assertEqual({s["session"]: s for s in view.sessions()}["s-wait"]["waitingSince"], since)
            view.data["dirs"][cfg]["fetchedAt"] -= 2
            sp.write_private(view.path, json.dumps(view.data))
            Path(cfg, "sessions", "4242.json").write_text("{ }")
            view.refresh([cfg])
            self.assertEqual([s["status"] for s in view.sessions()], ["busy"])

    def test_a_missing_or_broken_command_is_tolerated(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            view = self.view(d, [OSError("no such file"), "Error: unknown option"], calls)
            view.refresh(view.active)
            self.assertEqual(view.sessions(), [])
            self.assertEqual(view.state(), "failing")
            self.assertFalse(view.due(view.load()["dirs"][view.active[0]], None, time.time() + 30))   # waits RETRY
            gone = sp.AgentsView(os.path.join(d, "v2.json"), which=lambda name: None)
            gone.poll(set())
            self.assertEqual(gone.state(), "")
            gone.active = ["/x"]
            self.assertEqual(gone.state(), "unavailable")

    def test_two_bars_share_one_look(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            a = self.view(d, [json.dumps(VIEW_MIXED)], calls)
            b = self.view(d, [json.dumps(VIEW_BUSY)], calls)
            a.refresh(a.active)
            b.refresh(b.active)                                             # not due: a's result is fresh
            self.assertEqual(len(calls), 1)
            self.assertEqual([s["session"] for s in b.sessions()], ["s-wait", "s-idle"])
            with open(a.path + ".lock", "a") as lock:                       # a is looking right now
                sp.fcntl.flock(lock, sp.fcntl.LOCK_EX)
                b.data["dirs"] = {}
                os.unlink(a.path)
                b.refresh(b.active)
            self.assertEqual(len(calls), 1)


class MergeSessions(unittest.TestCase):
    def hook(self, state, ts, session="s-wait", pid=4242, message=""):
        return {"session": session, "state": state, "ts": ts, "cwd": "/p/Arc", "pid": pid, "message": message, "source": "hook"}

    def viewed(self, status, fetched=100.0, waiting_since=90.0, first=10.0, session="s-wait", waiting_for="permission prompt"):
        return {"session": session, "pid": 4242, "cwd": "/p/Arc", "status": status, "name": "fix the login",
                "waitingFor": waiting_for if status == "waiting" else "", "startedAt": 5.0,
                "waitingSince": waiting_since if status == "waiting" else None, "fetchedAt": fetched, "firstAt": first}

    def test_waiting_in_the_list_needs_you_without_a_hook(self):
        [s] = sp.merge_sessions([], [self.viewed("waiting")])
        self.assertEqual((s["state"], s["ts"], s["message"], s["exact"], s["old"]),
                         ("wait", 90.0, "Waiting for your permission", True, False))
        [s] = sp.merge_sessions([], [self.viewed("waiting", waiting_since=10.0)])
        self.assertTrue(s["old"])                       # already waiting at the first look: not news

    def test_the_hook_gives_the_time_and_words_of_the_same_wait(self):
        [s] = sp.merge_sessions([self.hook("wait", 80.0, message="Allow Bash: ls")], [self.viewed("waiting")])
        self.assertEqual((s["state"], s["ts"], s["message"], s["exact"]), ("wait", 80.0, "Allow Bash: ls", True))

    def test_idle_in_the_list_clears_an_older_hook_wait_but_not_newer_news(self):
        [s] = sp.merge_sessions([self.hook("wait", 80.0)], [self.viewed("idle", fetched=100.0)])
        self.assertEqual(s["state"], "done")            # Esc on the prompt: the list says idle
        [s] = sp.merge_sessions([self.hook("work", 120.0)], [self.viewed("idle", fetched=100.0)])
        self.assertEqual(s["state"], "work")            # the hook spoke after the list was read

    def test_busy_in_the_list_never_claims_anything(self):
        [s] = sp.merge_sessions([self.hook("wait", 80.0)], [self.viewed("busy")])
        self.assertEqual(s["state"], "wait")
        self.assertEqual(sp.merge_sessions([], [self.viewed("busy")]), [])

    def test_matched_by_pid_when_the_ids_differ_and_unmatched_hooks_stand(self):
        hooked = [self.hook("wait", 80.0, session="other-id"), self.hook("work", 50.0, session="b", pid=7)]
        got = sp.merge_sessions(hooked, [self.viewed("idle")])
        self.assertEqual(sorted((s["session"], s["state"]) for s in got), [("b", "work"), ("other-id", "done")])


class Settings(unittest.TestCase):
    def test_defaults_match_the_manifest(self):
        with open(os.path.join(ROOT, "manifest.json")) as f:
            manifest = json.load(f)["barWidget"]
        schema = {item["key"]: item for item in manifest["schema"]}
        public = sp.public_settings(sp.resolve_settings({}))
        self.assertEqual(list(schema), list(public))                       # same keys, same order
        for key, value in public.items():
            want = manifest["defaults"][key]
            if key in ("plainWorkspaces", "recentIgnore", "projectRoots") and isinstance(want, str):
                want = sp.resolve_settings({key: want})[key]
            self.assertEqual(value, want, key)
            self.assertEqual(schema[key].get("defaultValue", manifest["defaults"][key]), manifest["defaults"][key], key)
            for option in schema[key].get("options", []):
                if schema[key]["type"] == "enum":
                    self.assertIn(option["value"], sp.SETTING_CHOICES[key])

    def test_old_keys_are_read_when_the_new_one_is_absent(self):
        r = sp.resolve_settings
        self.assertEqual((r({"labelMode": "fit"})["names"], r({"labelMode": "fit"})["fit"]), ("smart", True))
        self.assertEqual((r({"labelMode": "all"})["names"], r({"labelMode": "all"})["fit"]), ("all", False))
        self.assertEqual(r({"labelMode": "hover"})["names"], "hover")
        self.assertEqual(r({"labelMode": "all", "names": "none"})["names"], "none")       # the new key wins
        self.assertEqual(r({"iconStyle": "none"})["style"], "minimal")
        self.assertEqual(r({"iconStyle": "glyph"})["style"], "mono")
        self.assertEqual(r({"iconStyle": "mono", "projectTiles": True})["style"], "mono")
        self.assertEqual(r({"projectTiles": False})["style"], "logos")
        self.assertEqual(r({"iconStyle": "color", "projectTiles": True})["style"], "tiles")
        self.assertEqual(r({"busyDot": False})["agentBadge"], False)
        self.assertEqual(r({"notifyWhenWaiting": False})["alerts"], False)
        self.assertEqual(r({"holdSeconds": 9})["renameDelay"], 9)
        self.assertEqual(r({"holdSeconds": 9, "renameDelay": 2})["renameDelay"], 2)

    def test_values_are_checked(self):
        r = sp.resolve_settings
        self.assertEqual(r({"names": "SMART"})["names"], "smart")
        self.assertEqual(r({"names": "loud"})["names"], "smart")
        self.assertEqual(r({"alerts": "false"})["alerts"], False)          # `omarchy bar set` stores strings
        self.assertEqual(r({"alerts": "maybe"})["alerts"], True)
        self.assertEqual(r({"maxLength": 999})["maxLength"], 32)
        self.assertEqual(r({"maxLength": "x"})["maxLength"], 12)
        self.assertEqual(r({"plainWorkspaces": "1, 9,x, 0"})["plainWorkspaces"], [1, 9])
        self.assertEqual(r({"plainWorkspaces": [3, "4"]})["plainWorkspaces"], [3, 4])
        self.assertEqual(r({"badgeStates": ["ready", "bogus"]})["badgeStates"], ["ready"])
        self.assertEqual(r({"badgeStates": []})["badgeStates"], sp.BADGE_STATES)     # empty means all
        self.assertEqual(r({"sources": "agents,ssh"})["sources"], ["agents", "ssh"])
        self.assertEqual(r({"recentIgnore": "~/.t3/scratch/*"})["recentIgnore"], ["~/.t3/scratch/*"])
        self.assertEqual(r({"recentIgnore": ""})["recentIgnore"], [])
        self.assertEqual(r({})["projectRoots"], ["~/Projects"])
        self.assertEqual(r({"projectRoots": "~/Projects, ~/work"})["projectRoots"], ["~/Projects", "~/work"])
        self.assertEqual(r({"projectRoots": ""})["projectRoots"], [])
        self.assertEqual(r({"agentCommand": ""})["agentCommand"], "claude")
        self.assertEqual(r(None), r({}))

    def test_the_engine_uses_the_renamed_settings(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        for f in facts:
            for a in f.get("agents") or []:
                a["busy"] = True
        busy = sp.decide(facts, {}, {"agentBadge": True}, RULES)
        quiet = sp.decide(facts, {}, {"busyDot": False}, RULES)
        self.assertTrue(any(r.get("busy") for r in busy.values()))
        self.assertFalse(any(r.get("busy") for r in quiet.values()))


class SwitcherSettings(unittest.TestCase):
    def engine(self, d, **settings):
        engine = sp.Engine(settings, rules=RULES, state_file=os.path.join(d, "state.json"))
        engine.view.command = ""
        for directory in ("/srv/one", "/tmp/scratch-x", sp.HOME + "/.cache/thing", sp.HOME + "/.t3/scratch/a"):
            engine.history.entries.append({"directory": directory, "originalName": os.path.basename(directory),
                                           "aliasKey": None, "wt": None, "glyph": "folder"})
        return engine

    def test_recent_ignore_and_recents_off(self):
        with tempfile.TemporaryDirectory() as d:
            names = lambda e: [p["directory"] for p in e.snapshot()["projects"]]
            self.assertEqual(names(self.engine(d)), ["/srv/one", sp.HOME + "/.t3/scratch/a"])
            self.assertEqual(names(self.engine(d, recentIgnore="~/.t3/scratch/*")), ["/srv/one", "/tmp/scratch-x", sp.HOME + "/.cache/thing"])
            self.assertEqual(names(self.engine(d, switcherRecents=False)), [])
            self.assertTrue(sp.ignored_folder("/tmp/a/b", ["/tmp/*"]))
            self.assertFalse(sp.ignored_folder("/tmpfoo", ["/tmp/*"]))

    def test_git_never_looks_nothing_up(self):
        with tempfile.TemporaryDirectory() as d:
            engine = self.engine(d, switcherGit="never")
            engine.gitd.want = Mock()
            engine.want_details()
            engine.gitd.want.assert_not_called()
            engine.gitd.cache["/srv/one"] = (1e12, {"branch": "main"})
            self.assertNotIn("branch", engine.snapshot()["projects"][0])

    def test_show_questions_off_keeps_the_words_in_the_engine(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        with tempfile.TemporaryDirectory() as d:
            engine = sp.Engine({"showQuestions": False}, rules=RULES, state_file=os.path.join(d, "state.json"))
            engine.view.command = ""
            engine.results = sp.decide(facts, {}, {}, RULES)
            engine.holder.update(engine.results, 0.0, engine.settings, force=True)
            sp.write_hook({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "session_id": "s1",
                           "tool_input": {"command": "rm -rf build"}, "cwd": sp.HOME + "/Projects/Starling"},
                          os.path.join(d, "agents"))
            snap = engine.snapshot()
            self.assertNotIn("rm -rf", json.dumps(snap))
            row = {r["name"]: r for r in snap["projects"]}["Starling"]
            self.assertEqual((row["agent"], row["agentMessage"]), ("wait", ""))
            self.assertEqual(sp.wait_alert(engine.agent_sessions()[0], "Starling", 3)[1], "Workspace 3 · Claude is waiting for you")


class ProjectIndexTests(unittest.TestCase):
    def test_root_index_skips_hidden_non_dirs_respects_cap_and_marks_git(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "Projects"
            root.mkdir()
            (root / ".hidden").mkdir()
            (root / "notes.txt").write_text("hi")
            (root / "alpha").mkdir()
            (root / "alpha" / ".git").mkdir()
            (root / "beta").mkdir()
            (root / "worktree").mkdir()
            (root / "worktree" / ".git").write_text("gitdir: ../alpha/.git/worktrees/wt\n")
            (root / "ignored-dir").mkdir()

            idx = sp.ProjectIndex(which=lambda _: None)
            with patch.object(subprocess, "run", side_effect=AssertionError("git must not run")):
                entries = idx.refresh([str(root)], [f"{root}/ignored-*"], now=10.0)
            by_name = {e["originalName"]: e for e in entries}
            self.assertEqual(set(by_name), {"alpha", "beta", "worktree"})
            self.assertEqual(by_name["alpha"]["glyph"], "git")
            self.assertEqual(by_name["beta"]["glyph"], "folder")
            self.assertEqual(by_name["worktree"]["glyph"], "git")

            # Cap at MAX_ROOT_ENTRIES
            cap_root = Path(d) / "Many"
            cap_root.mkdir()
            for i in range(10):
                (cap_root / f"proj-{i:02d}").mkdir()
            capped = sp.ProjectIndex(which=lambda _: None)
            capped.MAX_ROOT_ENTRIES = 4
            got = capped.refresh([str(cap_root)], [], now=10.0)
            self.assertEqual(len(got), 4)

    def test_index_dedups_against_open_and_recent_by_real_path_and_only_shows_when_open(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "kitty-agent-in-worktree.json"))["facts"]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "Projects"
            root.mkdir()
            open_dir = root / "OpenRepo"
            recent_dir = root / "RecentRepo"
            indexed_dir = root / "IndexedRepo"
            for p in (open_dir, recent_dir, indexed_dir):
                p.mkdir()
            (indexed_dir / ".git").mkdir()
            # Symlink pointing to recent_dir under another root
            other_root = Path(d) / "Links"
            other_root.mkdir()
            (other_root / "RecentLink").symlink_to(recent_dir)

            agent = facts[0]["tabs"][0]["fg"]["agent"]
            agent["git"]["root"] = str(open_dir)
            agent["cwd"] = str(open_dir)

            engine = sp.Engine({"projectRoots": [str(root), str(other_root)], "recentIgnore": []},
                               rules=RULES, state_file=os.path.join(d, "state.json"))
            engine.index = sp.ProjectIndex(which=lambda _: None)
            engine.view.command = ""
            engine.results = sp.decide(facts, {}, engine.settings, RULES)
            engine.history.touch({"directory": str(recent_dir), "originalName": "RecentRepo", "glyph": "folder"})

            # Switcher closed: index is not scanned or included in snapshot
            closed_rows = engine.snapshot()["projects"]
            self.assertEqual({r["group"] for r in closed_rows}, {"open", "recent"})

            # Switcher opens: details command builds index and adds Projects group, deduplicated
            daemon = sp.Daemon(engine)
            daemon.send = Mock()
            daemon.command({"cmd": "details", "on": True})
            rows = engine.snapshot()["projects"]
            by_group = {}
            for r in rows:
                by_group.setdefault(r["group"], []).append(r)
            self.assertEqual(len(by_group["open"]), 1)
            self.assertEqual(by_group["open"][0]["address"], facts[0]["address"])
            self.assertEqual([r["originalName"] for r in by_group["recent"]], ["RecentRepo"])
            self.assertEqual([r["originalName"] for r in by_group["projects"]], ["IndexedRepo"])
            self.assertEqual(by_group["projects"][0]["glyph"], "git")

            # Selecting an indexed project returns its directory and adds it to recent history
            reply = engine.select_recent(by_group["projects"][0]["id"])
            self.assertEqual(reply, {"directory": str(indexed_dir)})
            self.assertEqual(engine.history.entries[0]["originalName"], "IndexedRepo")

    def test_zoxide_merge_and_missing_zoxide_and_empty_roots(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "Projects"
            root.mkdir()
            (root / "InRoot").mkdir()
            z_dir = Path(d) / "Extra" / "ZProject"
            z_dir.mkdir(parents=True)
            (z_dir / ".git").mkdir()
            z_ignored = Path(d) / "Scratch" / "TempThing"
            z_ignored.mkdir(parents=True)
            missing = Path(d) / "DoesNotExist"

            fake_bin = Path(d) / "bin"
            fake_bin.mkdir()
            fake_zoxide = fake_bin / "zoxide"
            fake_zoxide.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' {str(root / 'InRoot')!r} {str(z_dir)!r} {str(z_ignored)!r} {str(missing)!r} {sp.HOME!r}\n"
            )
            fake_zoxide.chmod(0o755)

            with patch.dict(os.environ, {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}):
                idx = sp.ProjectIndex()
                entries = idx.refresh([str(root)], [f"{d}/Scratch/*"], now=100.0)
            names = [e["originalName"] for e in entries]
            self.assertEqual(names, ["InRoot", "ZProject"])
            self.assertEqual(entries[1]["glyph"], "git")

            # Empty projectRoots turns the index off completely
            with patch.dict(os.environ, {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}):
                off = sp.ProjectIndex()
                self.assertEqual(off.refresh([], [], now=100.0), [])

            # No zoxide on PATH produces no error
            no_z = sp.ProjectIndex(which=lambda _: None)
            self.assertEqual([e["originalName"] for e in no_z.refresh([str(root)], [], now=100.0)], ["InRoot"])

    def test_root_index_uses_mtime_cache(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "Projects"
            root.mkdir()
            (root / "One").mkdir()
            idx = sp.ProjectIndex(which=lambda _: None)
            self.assertEqual([e["originalName"] for e in idx.refresh([str(root)], [], now=10.0)], ["One"])
            with patch.object(os, "listdir", side_effect=AssertionError("should use mtime cache")):
                self.assertEqual([e["originalName"] for e in idx.refresh([str(root)], [], now=15.0)], ["One"])
            # Touching root directory invalidates mtime cache
            (root / "Two").mkdir()
            st = os.stat(root)
            os.utime(root, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
            self.assertEqual([e["originalName"] for e in idx.refresh([str(root)], [], now=16.0)], ["One", "Two"])


class HoverSentence(unittest.TestCase):
    def test_the_sentence_matches_the_name(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        results = sp.decide(facts, {}, {}, RULES)
        for r in results.values():
            if r.get("source") == "agent":
                self.assertRegex(r["summary"], r"^Claude Code in .+, in ~/Projects/")
                self.assertNotIn("Named after", r["reason"])

    def test_each_source_reads_as_a_plain_sentence(self):
        cand = lambda src, **kw: {"source": src, **kw}
        self.assertEqual(sp.summary_of(cand("agent", agentLabel="Claude Code", projectRoot=sp.HOME + "/p/Arc"), "T3 Code", "Arc"),
                         "Claude Code in T3 Code, in ~/p/Arc.")
        self.assertEqual(sp.summary_of(cand("project", projectRoot="/srv/x", what="shell"), "kitty", "x"), "A kitty shell in /srv/x.")
        self.assertEqual(sp.summary_of(cand("project", projectRoot="/srv/x", what="nvim"), "kitty", "x"), "nvim in kitty, in /srv/x.")
        self.assertEqual(sp.summary_of(cand("ssh"), "kitty", "ssh · vps"), "An ssh session to vps, in kitty.")
        self.assertEqual(sp.summary_of(cand("model"), "kitty", "Ollama · qwen3"), "Ollama running qwen3, in kitty.")
        self.assertEqual(sp.summary_of(cand("app"), "kitty", "kitty"), "kitty, with no project open in it.")
        self.assertEqual(sp.summary_of(cand("pinned"), "", "inbox"), "A name you pinned.")

    def test_long_app_names_are_shortened_by_rule(self):
        self.assertEqual(sp.short_app_name("Visual Studio Code", RULES), "VS Code")
        self.assertEqual(sp.short_app_name("T3 Code (Nightly)", RULES), "T3 Code")
        self.assertEqual(sp.short_app_name("kitty", RULES), "kitty")
        self.assertEqual(sp.short_app_name("kitty", {"appNames": {"kitty": "  "}}), "kitty")


class OneAlertAcrossMonitors(unittest.TestCase):
    """Each monitor's bar runs its own engine. One of them sends the alert; "Go to it" jumps once."""

    def daemon(self, d):
        engine = sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json"))
        engine.view.command = ""
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        engine.results = sp.decide(facts, {}, {}, RULES)
        daemon = sp.Daemon(engine)
        daemon.primed = True
        return daemon

    def test_claims_are_one_per_key(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(sp.claim(d, "wait:s:1"))
            self.assertFalse(sp.claim(d, "wait:s:1"))
            self.assertTrue(sp.claim(d, "wait:s:2"))
            stale = os.path.join(d, "claims", os.listdir(os.path.join(d, "claims"))[0])
            os.utime(stale, (1, 1))
            sp.claim(d, "other")                                             # stale claims are dropped
            self.assertEqual(len(os.listdir(os.path.join(d, "claims"))), 2)

    def test_two_engines_in_one_process_send_one_alert_and_jump_once(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = self.daemon(d), self.daemon(d)
            sent, jumps = [], []
            for name, daemon in (("left", left), ("right", right)):
                daemon.notify = lambda title, body, ws, image="", actions=None, name=name, **k: sent.append(name)
                daemon.send = lambda message, name=name: jumps.append((name, message))
            sp.write_hook({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "session_id": "s1",
                           "cwd": sp.HOME + "/Projects/Starling"}, os.path.join(d, "agents"))
            with patch.object(sp, "focused_workspace", return_value=1):
                left.tick_agents()
                right.tick_agents()
                left.tick_agents()
            self.assertEqual(sent, ["left"])
            # The left engine's watcher sees the press; the right one sees it too, but it isn't its alert.
            left.alerts[7], right.alerts = {"ws": 3, "address": ""}, {}
            line = "/org/freedesktop/Notifications: org.freedesktop.Notifications.ActionInvoked (uint32 7, 'go')\n"
            for daemon in (left, right):
                daemon.monitor = Mock()
                daemon.monitor.stdout.fileno.return_value = 0
                with patch.object(sp.os, "read", return_value=line.encode()):
                    daemon.read_alert_actions()
            self.assertEqual(jumps, [("left", {"jump": 3, "address": ""})])

    def test_two_real_processes_send_one_alert(self):
        with tempfile.TemporaryDirectory() as d:
            sp.write_hook({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "session_id": "s1",
                           "cwd": sp.HOME + "/Projects/Starling"}, os.path.join(d, "agents"))
            script = f"""
import importlib.util, json, os, sys, time
spec = importlib.util.spec_from_file_location("signpost", {os.path.join(ROOT, "signpost.py")!r})
sp = importlib.util.module_from_spec(spec); spec.loader.exec_module(sp)
d = sys.argv[1]
rules = sp.load_rules(user_file="/nonexistent")
engine = sp.Engine(rules=rules, state_file=os.path.join(d, "state.json")); engine.view.command = ""
with open({os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json")!r}) as f:
    facts = json.loads(f.read().replace("$HOME", sp.HOME))["facts"]
engine.results = sp.decide(facts, {{}}, {{}}, rules)
daemon = sp.Daemon(engine); daemon.primed = True
sp.focused_workspace = lambda: 1
def notify(title, body, ws, image="", actions=None, **k):
    with open(os.path.join(d, "sent"), "a") as f: f.write(str(os.getpid()) + "\\n")
daemon.notify = notify
while time.time() < float(sys.argv[2]): time.sleep(0.001)
daemon.tick_agents()
"""
            start = str(time.time() + 0.6)
            procs = [subprocess.Popen([sys.executable, "-I", "-c", script, d, start]) for _ in range(2)]
            for p in procs:
                self.assertEqual(p.wait(timeout=20), 0)
            with open(os.path.join(d, "sent")) as f:
                self.assertEqual(len(f.read().split()), 1)


class BarSwap(unittest.TestCase):
    """`signpost.py swap` / `unswap` and the hint, on copies of shell.json layouts, never the real one."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / "shell.json"
        self.record = self.root / "state" / "bar-restore.json"
        self.defaults = self.root / "pkg" / "config" / "omarchy" / "shell.json"
        self.defaults.parent.mkdir(parents=True)
        self.defaults.write_text(json.dumps({"version": 1, "bar": {"layout": {"left": ["omarchy.menu", sp.STOCK_ID]}}}))
        self.plugins = self.root / "plugins"
        (self.plugins / "me.clone").mkdir(parents=True)
        (self.plugins / "me.clone" / "manifest.json").write_text(json.dumps(
            {"id": "me.clone", "kinds": ["bar-widget"], "omarchy": {"clonedFrom": sp.STOCK_ID}}))

    def write(self, left):
        self.config.write_text(json.dumps({"version": 1, "bar": {"layout": {"left": left, "right": ["omarchy.clock"]}},
                                           "idle": {"x": 1}}, indent=2))

    def cli(self, cmd):
        out = io.StringIO()
        with redirect_stdout(out), patch.object(sp, "warn") as warned:
            code = sp.swap_cli(cmd, ["--config", str(self.config), "--record", str(self.record), "--defaults", str(self.defaults),
                                     "--plugins", str(self.plugins), "--no-reload"])
        return code, out.getvalue() + "".join(str(c.args[0]) for c in warned.call_args_list)

    def left(self):
        return json.loads(self.config.read_text())["bar"]["layout"]["left"]

    def test_replaces_the_stock_widget_and_undo_puts_it_back(self):
        stock = {"id": sp.STOCK_ID, "someSetting": 1}
        self.write(["omarchy.menu", stock, {"id": sp.SIGNPOST_ID, "names": "all"}, "omarchy.active-window"])
        before = self.config.read_bytes()
        self.assertEqual(sp.bar_hint(str(self.root / "state"), str(self.config), str(self.plugins)), {"id": sp.STOCK_ID})
        self.assertEqual(self.cli("swap")[0], 0)
        self.assertEqual(self.left(), ["omarchy.menu", {"id": sp.SIGNPOST_ID, "names": "all"}, "omarchy.active-window"])
        self.assertTrue(self.record.exists())
        self.assertEqual(oct(self.record.stat().st_mode & 0o777), "0o600")
        self.assertIsNone(sp.bar_hint(str(self.root / "state"), str(self.config), str(self.plugins)))
        self.assertEqual(json.loads(self.config.read_text())["idle"], {"x": 1})        # the rest untouched
        self.assertEqual(self.cli("unswap")[0], 0)
        self.assertEqual(self.left(), ["omarchy.menu", stock, "omarchy.active-window"])
        self.assertFalse(self.record.exists())
        self.assertNotEqual(self.config.read_bytes(), before)        # Signpost's own entry was removed by undo

    def test_a_clone_is_replaced_too(self):
        self.write(["omarchy.menu", {"id": "me.clone"}, {"id": sp.SIGNPOST_ID}])
        self.assertEqual(sp.bar_hint(str(self.root / "state"), str(self.config), str(self.plugins)), {"id": "me.clone"})
        self.cli("swap")
        self.assertEqual(self.left(), ["omarchy.menu", {"id": sp.SIGNPOST_ID}])
        self.cli("unswap")
        self.assertEqual(self.left(), ["omarchy.menu", {"id": "me.clone"}])

    def test_no_other_widget_no_hint_and_swap_changes_nothing(self):
        self.write(["omarchy.menu", {"id": sp.SIGNPOST_ID}])
        before = self.config.read_bytes()
        self.assertIsNone(sp.bar_hint(str(self.root / "state"), str(self.config), str(self.plugins)))
        self.assertEqual(self.cli("swap"), (0, "nothing to change\n"))
        self.assertEqual(self.config.read_bytes(), before)

    def test_unswap_without_a_record_refuses(self):
        self.write(["omarchy.menu", {"id": sp.SIGNPOST_ID}])
        code, text = self.cli("unswap")
        self.assertEqual(code, 1)
        self.assertIn("no saved layout", text)

    def test_keep_both_silences_the_hint_for_good(self):
        self.write(["omarchy.menu", sp.STOCK_ID, {"id": sp.SIGNPOST_ID}])
        state = str(self.root / "state")
        self.assertIsNotNone(sp.bar_hint(state, str(self.config), str(self.plugins)))
        sp.keep_both(state)
        self.assertIsNone(sp.bar_hint(state, str(self.config), str(self.plugins)))
        self.assertEqual(json.loads(self.config.read_text())["bar"]["layout"]["left"][1], sp.STOCK_ID)   # untouched

    def test_the_offer_is_sent_once_ever_and_never_changes_the_bar_by_itself(self):
        self.write(["omarchy.menu", sp.STOCK_ID, {"id": sp.SIGNPOST_ID}])
        before = self.config.read_bytes()
        with tempfile.TemporaryDirectory() as d:
            for _ in range(2):
                engine = sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json"))
                engine.view.command = ""
                engine.hint = sp.bar_hint(d, str(self.config), str(self.plugins))
                daemon = sp.Daemon(engine)
                sent = []
                daemon.notify = lambda *a, **kw: sent.append(kw.get("actions"))
                daemon.tick_agents()
                if _ == 0:
                    self.assertEqual(sent, [[("replace", "Replace"), ("keep", "Keep both")]])
                else:
                    self.assertEqual(sent, [])                        # once, ever
        self.assertEqual(self.config.read_bytes(), before)

    def test_the_hint_buttons_ask_dbus_for_two_actions(self):
        argv = sp.notify_argv("t", "b", False, "", [("replace", "Replace"), ("keep", "Keep both")])
        i = argv.index("Replace")
        self.assertEqual(argv[i - 2:i + 3], ["4", "replace", "Replace", "keep", "Keep both"])


class Round3AgentHooks(unittest.TestCase):
    def test_codex_uses_documented_events_and_ignores_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            payload = {"session_id": "same", "cwd": "/projects/pine", "turn_id": "turn",
                       "tool_name": "Bash", "tool_input": {"description": "Run tests"}}
            for event, expected in (("UserPromptSubmit", "work"), ("PermissionRequest", "wait"),
                                    ("UnrelatedFutureEvent", None), ("PostToolUse", "work"),
                                    ("Stop", "done"), ("Interrupt", "done"), ("SessionEnd", "end")):
                self.assertEqual(sp.write_hook({**payload, "hook_event_name": event}, d, 10, agent="codex"), expected)
                signals = sp.AgentSignals(d).read(11)
                if expected == "end":
                    self.assertEqual(signals, [])
                else:
                    self.assertEqual(signals[0]["state"], expected or "wait")
                    self.assertEqual(signals[0]["agent"], "codex")
                    if expected == "wait":
                        self.assertEqual(signals[0]["message"], "Allow Bash: Run tests")

    def test_agents_with_same_session_id_keep_distinct_state_and_alert_claims(self):
        with tempfile.TemporaryDirectory() as d:
            path = sp.agent_session_file("same", d)
            Path(path).write_text(json.dumps(dict(session="same", state="wait", ts=10, cwd="/pine")))
            payload = {"session_id": "same", "cwd": "/pine", "hook_event_name": "PermissionRequest"}
            sp.write_hook(payload, d, 10, agent="codex")
            readers = sp.AgentSignals(d)
            sessions = readers.read(11)
            self.assertEqual({s["agent"] for s in sessions}, {"claude", "codex"})
            viewed = [{"session": "same", "pid": 55, "status": "idle", "fetchedAt": 11}]
            merged = sp.merge_sessions(sessions, viewed)
            self.assertEqual({(s["agent"], s["state"]) for s in merged}, {("claude", "done"), ("codex", "wait")})
            notified = {}
            self.assertEqual(len(sp.new_waits(sessions, notified)), 2)
            self.assertEqual(sp.new_waits(sessions, notified), [])
            self.assertEqual(sp.wait_alert(next(s for s in sessions if s["agent"] == "codex"), "Pine", 4, mixed=True)[0], "Pine · Codex needs you")

    def test_codex_wait_remains_visible_when_focused_until_lifecycle_event(self):
        with tempfile.TemporaryDirectory() as d:
            payload = {"session_id": "pine", "cwd": "/pine", "hook_event_name": "PermissionRequest"}
            sp.write_hook(payload, d, 10, agent="codex")
            readings = sp.AgentSignals(d)
            acks = sp.WaitAcks()
            sessions = readings.read(11)
            for now in (11, 15):
                acks.update(sessions, "/pine", now)
            self.assertEqual(acks.apply(sessions)[0]["state"], "wait")
            sp.write_hook({**payload, "hook_event_name": "Interrupt"}, d, 16, agent="codex")
            self.assertEqual(readings.read(17)[0]["state"], "done")

    def test_codex_install_keeps_other_hooks_inside_same_group(self):
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()):
            path = Path(d, "hooks.json")
            other = {"type": "command", "command": "echo original"}
            ours = {"type": "command", "command": "python3 -I /old/signpost.py hook --agent codex"}
            original = {"description": "My hooks", "hooks": {"Stop": [{"matcher": "", "hooks": [other, ours]}]}}
            path.write_text(json.dumps(original))
            args = ["--agent", "codex", "--config-dir", d]
            self.assertEqual(sp.hooks_cli(["install", *args]), 0)
            current = path.read_bytes()
            self.assertEqual(set(sp.hooks_installed(json.loads(current), "codex")), set(sp.CODEX_EVENTS))
            self.assertEqual(sp.hooks_cli(["install", *args]), 0)
            self.assertEqual(path.read_bytes(), current)
            self.assertEqual(json.loads(Path(d, "hooks.json.signpost-backup").read_bytes()), original)
            self.assertFalse(Path(d, "config.toml").exists())
            self.assertEqual(sp.hooks_cli(["remove", *args]), 0)
            self.assertEqual(json.loads(path.read_bytes())["hooks"], {"Stop": [{"matcher": "", "hooks": [other]}]})

    def test_codex_stop_is_neutral_even_when_state_is_read_only(self):
        with patch.object(sp.sys, "stdin", io.StringIO('{"hook_event_name":"Stop","session_id":"foo"}')), \
             patch.object(sp, "write_hook", side_effect=OSError("read-only")), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sp.hook_main(["--agent", "codex"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {})

    def test_opencode_config_is_opt_in_and_uses_v2_events(self):
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()) as out:
            args = ["--agent", "opencode", "--config-dir", d]
            self.assertEqual(sp.hooks_cli(["install", *args]), 0)
            path = Path(d, "plugins", "signpost", "index.ts")
            source = path.read_text()
            for event in sp.OPENCODE_EVENTS:
                self.assertIn(event, source)
            self.assertIn("ctx.event.subscribe", source)
            self.assertIn("location?.directory", source)
            self.assertEqual(sp.hooks_cli(["install", *args]), 0)
            self.assertEqual(path.read_text(), source)
            self.assertEqual(sp.hooks_cli(["status", *args]), 0)
            self.assertEqual(sp.hooks_cli(["remove", *args]), 0)
            self.assertFalse(path.exists())
            self.assertIn("experimental", out.getvalue())

    def test_opencode_permissions_replies_and_execution_events(self):
        with tempfile.TemporaryDirectory() as d:
            payload = {"session_id": "ses_pine", "cwd": "/pine", "message": "Allow shell?"}
            for event, state in sp.OPENCODE_EVENTS.items():
                self.assertEqual(sp.write_hook({**payload, "hook_event_name": event}, d, 10, agent="opencode"), state)
            self.assertEqual(sp.AgentSignals(d).read(11), [])
            self.assertIsNone(sp.hook_state({"hook_event_name": "session.idle"}, "opencode"))


class CommandStatus(unittest.TestCase):
    @staticmethod
    def foreground(prog, *args):
        argv = list(args) if args else [prog]
        return {"prog": prog, "argv": argv, "pid": 301, "start": 80 * sp.CLK_TCK, "cwd": "/pine"}

    def test_named_commands_ignore_programs_and_long_threshold(self):
        for prog, argv, want in [
            ("cargo", ["cargo", "test"], "test"), ("pytest", ["pytest"], "test"),
            ("pytest", ["python3", "/bin/pytest"], "test"), ("npm", ["node", "/usr/lib/npm-cli.js", "run", "build"], "build"),
            ("go", ["go", "test", "./..."], "test"), ("make", ["make"], "build"),
            ("vite", ["vite", "build"], "build"), ("npm", ["npm", "run", "dev"], None),
            ("tsc", ["tsc", "--watch"], None), ("claude", ["claude"], None),
            ("bash", ["bash"], None), ("ssh", ["ssh", "bastion"], None),
            ("python", ["python"], None), ("watch", ["watch", "date"], None),
            ("mu", ["mu", "index"], "long"),
        ]:
            with self.subTest(argv=argv):
                self.assertEqual(sp.command_kind(self.foreground(prog, *argv), RULES, {"bash"}, 120, 30), want)
        self.assertIsNone(sp.command_kind(self.foreground("mu"), RULES, {}, 109, 30))

    def test_unseen_completion_then_focus_or_disable_clears(self):
        with tempfile.TemporaryDirectory() as d:
            tracker = sp.CommandSignals(d)
            tab = {"pid": 51, "start": 100, "fg": self.foreground("make")}
            facts = [{"address": "0x1", "workspace": 5, "tabs": [tab]}]
            cfg = sp.resolve_settings({})
            tracker.update(facts, 1, RULES, cfg, {}, now=90)
            self.assertEqual(tracker.statuses()[5], {"command": "work", "commandText": "Build running"})
            tab["fg"] = None
            tracker.update(facts, 1, RULES, cfg, {}, now=91, exited=lambda pid: True)
            self.assertEqual(tracker.statuses()[5], {"command": "ended", "commandText": "A long command finished"})
            tracker.update(facts, 5, RULES, cfg, {}, now=92)
            self.assertEqual(tracker.statuses(), {})
            tab["fg"] = self.foreground("make")
            tracker.update(facts, 1, RULES, sp.resolve_settings({"commandStatus": False}), {}, now=93)
            self.assertEqual(tracker.statuses(), {})

    def test_reports_explicit_pass_and_fail_only_for_matching_records(self):
        for code, expected in [(7, "failed"), (0, "ready")]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as d:
                tracker = sp.CommandSignals(d)
                tab = {"pid": 51, "start": 100, "fg": self.foreground("make")}
                facts = [{"address": "0x1", "workspace": 5, "tabs": [tab]}]
                cfg = sp.resolve_settings({})
                tracker.update(facts, 1, RULES, cfg, {}, now=90)
                tab["fg"] = None
                path = os.path.join(d, "51.json")
                result = {"start": 100, "cwd": "/pine", "command": "make", "launched": 79.5, "ended": 90.5, "code": code}
                sp.write_private(path, json.dumps({**result, "cwd": "/other"}))
                tracker.update(facts, 1, RULES, cfg, {}, now=91, exited=lambda pid: True)
                self.assertEqual(tracker.statuses()[5]["commandText"], "A long command finished")
                sp.write_private(path, json.dumps(result))
                tracker.update(facts, 1, RULES, cfg, {}, now=92)
                self.assertEqual(tracker.statuses()[5]["command"], expected)
                self.assertEqual(tracker.statuses()[5]["commandText"], "Command failed" if code else "Command passed")
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_stopped_job_or_closed_tab_does_not_mark_ready(self):
        with tempfile.TemporaryDirectory() as d:
            tracker = sp.CommandSignals(d)
            tab = {"pid": 51, "start": 100, "fg": self.foreground("make")}
            facts = [{"address": "0x1", "workspace": 5, "tabs": [tab]}]
            cfg = sp.resolve_settings({})
            tracker.update(facts, 1, RULES, cfg, {}, now=90)
            tab["fg"] = None
            tracker.update(facts, 1, RULES, cfg, {}, now=91, exited=lambda pid: False)
            self.assertEqual(tracker.statuses(), {})
            tab["fg"] = self.foreground("make")
            tracker.update(facts, 1, RULES, cfg, {}, now=92)
            tracker.update([], 1, RULES, cfg, {}, now=93, exited=lambda pid: True)
            self.assertEqual(tracker.statuses(), {})

    def test_shell_snippets_and_private_result_writer(self):
        for shell, feature in (("bash", "PROMPT_COMMAND"), ("zsh", "preexec"), ("fish", "fish_postexec")):
            with self.subTest(shell=shell):
                snippet = sp.shell_init(shell, "/a path/signpost.py")
                self.assertIn(feature, snippet)
                self.assertIn("'/a path/signpost.py' shell-record", snippet)
                if shutil.which(shell):
                    command = [shell, "-n"]
                    self.assertEqual(subprocess.run(command, input=snippet, text=True, capture_output=True).returncode, 0)
        with tempfile.TemporaryDirectory() as d, patch.object(sp, "read_proc", return_value={"session": 123, "tty": 7, "start": 9}):
            with patch.object(sp.sys, "stdin", io.StringIO("make private-argument")):
                sp.shell_record(["start", "123", "/work"], d)
            sp.shell_record(["end", "123", "/work", "4"], d)
            path = Path(d, "123.json")
            result = json.loads(path.read_text())
            self.assertEqual(result["command"], "make")
            self.assertEqual(result["cwd"], "/work")
            self.assertEqual(result["code"], 4)
            self.assertNotIn("private-argument", path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertGreaterEqual(result["ended"], result["launched"])
            self.assertEqual(sp.shell_command_name("make || true"), "")

    def test_snippets_keep_the_folder_off_the_command_line(self):
        for shell in ("bash", "zsh", "fish"):
            snippet = sp.shell_init(shell, "/opt/signpost.py")
            self.assertNotIn("$PWD", snippet, shell)
            self.assertIn("_signpost_run", snippet)
        with tempfile.TemporaryDirectory() as d, patch.object(sp, "read_proc", return_value={"session": 123, "tty": 7, "start": 9}):
            here = os.getcwd()
            try:
                os.chdir(d)
                with patch.object(sp.sys, "stdin", io.StringIO("make")):
                    sp.shell_record(["start", "123", "-"], d)       # "-": the recorder's own folder
                sp.shell_record(["end", "123", "-", "0"], d)
            finally:
                os.chdir(here)
            record = json.loads(Path(d, "123.json").read_text())
            self.assertEqual((record["cwd"], record["code"]), (os.path.realpath(d), 0))

    @unittest.skipUnless(shutil.which("bash"), "bash not installed")
    def test_bash_snippet_runs_and_is_quiet_once_signpost_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, "signpost.py")
            log = os.path.join(d, "calls")
            with open(script, "w") as f:      # stands in for signpost.py: logs its arguments and folder
                f.write(f"import os,sys\nopen({log!r},'a').write(' '.join(sys.argv[1:]) + ' @' + os.getcwd() + '\\n')\n")
            rc = os.path.join(d, "rc")
            work = os.path.join(d, "client-acme")
            os.makedirs(work)
            Path(rc).write_text(sp.shell_init("bash", script))
            session = f"cd {shlex.quote(work)}\ntrue\nrm {shlex.quote(script)}\ntrue\nexit\n"
            r = subprocess.run(["bash", "--rcfile", rc, "-i"], input=session, text=True, capture_output=True,
                               timeout=30, env={**os.environ, "HISTFILE": os.path.join(d, "hist"), "PS1": "$ "})
            calls = Path(log).read_text().splitlines()
            self.assertTrue(calls, r.stderr)
            for line in calls:
                args, _, folder = line.partition(" @")
                self.assertNotIn("client-acme", args)                   # never an argument
            self.assertTrue(any(line.endswith("@" + os.path.realpath(work)) for line in calls))
            self.assertNotIn("can't open file", r.stderr)              # removed: nothing on every prompt


class UnreadTitles(unittest.TestCase):
    CLASS = "chrome-web.whatsapp.com__-Default"

    def test_matches_only_known_app_with_its_documented_shape(self):
        windows = lambda cls, title: [{"class": cls, "title": title}]
        for cls, title, expected in [
            (self.CLASS, "(3) WhatsApp", 3), (self.CLASS.upper(), "(99) WhatsApp", 99),
            (self.CLASS, "(100) WhatsApp", 100), (self.CLASS, "(999999999) WhatsApp", 100),
            (self.CLASS, "(0) WhatsApp", 0), (self.CLASS, "(3) Other", 0),
            (self.CLASS, "(3) WhatsApp - Brave", 0), (self.CLASS, "(+3) WhatsApp", 0),
            (self.CLASS, "(-3) WhatsApp", 0), (self.CLASS, "(٣) WhatsApp", 0),
            (self.CLASS, "(2) WhatsApp | Chats", 0), (self.CLASS, "Inbox (5) - Gmail", 0),
            ("chromium", "(3) WhatsApp", 0), ("discord", "(2) Discord | #general", 0),
            (self.CLASS + ".suffix", "(3) WhatsApp", 0),
            ("chrome-web.whatsapp.com.evil__-Default", "(3) WhatsApp", 0),
        ]:
            with self.subTest(cls=cls, title=title):
                self.assertEqual(sp.unread_count(windows(cls, title), ["whatsapp"], RULES), expected)
        self.assertEqual(sp.unread_count(windows(self.CLASS, "(3) WhatsApp"), [], RULES), 0)

    def test_duplicate_windows_use_max_and_user_rule_works_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "rules.json")
            path.write_text(json.dumps({"unread": {"mail": {"class": "^MailPWA$", "unread": r"^Inbox \((?P<count>\d+)\) - Mail$"}}}))
            rules = sp.load_rules(path)
            windows = [{"class": self.CLASS, "title": "(3) WhatsApp"},
                       {"class": self.CLASS, "title": "(4) WhatsApp"},
                       {"class": "MailPWA", "title": "Inbox (5) - Mail"}]
            self.assertEqual(sp.unread_count(windows, ["whatsapp", "whatsapp"], rules), 4)
            self.assertEqual(sp.unread_count(windows, ["whatsapp", "mail"], rules), 9)
            self.assertEqual(sp.resolve_settings({})["unreadApps"], [])

    def test_decide_publishes_only_the_count_on_the_workspace(self):
        window = {"address": "0x7", "class": self.CLASS, "title": "(17) WhatsApp", "workspace": 7,
                  "focus": 0, "app": {"name": "WhatsApp", "icon": "whatsapp", "desktop": "WhatsApp", "webapp": True}}
        settings = {"unreadApps": ["whatsapp"]}
        state = {"pins": {}, "aliases": {}}
        row = sp.decide([window], state, settings, RULES)[7]
        self.assertEqual(row["unread"], 17)
        self.assertNotIn("(17) WhatsApp", json.dumps(row))
        self.assertEqual(sp.decide([window], state, {}, RULES)[7]["unread"], 0)


class ReviewFixes(unittest.TestCase):
    def test_same_repo_on_two_workspaces_routes_status_and_alert_by_pid(self):
        with tempfile.TemporaryDirectory() as d:
            repo = "/p/Arc"
            facts = [
                {"address": "0x1", "class": "com.t3tools.T3Code", "title": "Arc / chat", "pid": 100, "workspace": 1,
                 "focus": 0, "floating": False, "shared": False,
                 "app": {"name": "T3 Code", "icon": "t3", "desktop": "t3", "webapp": False},
                 "agents": [{"agent": "claude", "label": "Claude Code", "logo": "claude", "pid": 101,
                             "cwd": repo, "git": {"kind": "repo", "root": repo, "main": repo, "repo": "Arc", "label": None, "branch": "main"},
                             "start": 10, "busy": False, "lastActive": 0}]},
                {"address": "0x2", "class": "kitty", "title": "codex", "pid": 200, "workspace": 4,
                 "focus": 1, "floating": False, "shared": False,
                 "app": {"name": "kitty", "icon": "kitty", "desktop": "kitty", "webapp": False},
                 "tabs": [{"shell": "bash", "pid": 201, "start": 20, "cwd": repo,
                           "git": {"kind": "repo", "root": repo, "main": repo, "repo": "Arc", "label": None, "branch": "main"},
                           "pts": "/dev/pts/1", "idle": 0.0,
                           "fg": {"prog": "codex", "pid": 202, "start": 21, "argv": ["codex"], "cwd": repo,
                                  "git": {"kind": "repo", "root": repo, "main": repo, "repo": "Arc", "label": None, "branch": "main"},
                                  "agent": {"agent": "codex", "label": "Codex", "logo": "openai", "pid": 202,
                                            "cwd": repo, "git": {"kind": "repo", "root": repo, "main": repo, "repo": "Arc", "label": None, "branch": "main"},
                                            "start": 21, "busy": False, "lastActive": 0}}}]},
            ]
            engine = sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json"))
            engine.view.command = ""
            engine.results = sp.decide(facts, {}, {}, RULES)
            engine.see_agents(facts, active="0x1")
            engine.holder.update(engine.results, 0.0, engine.settings, force=True)
            agents_dir = os.path.join(d, "agents")
            now = time.time()
            sp.write_hook({"hook_event_name": "Stop", "session_id": "s-claude", "cwd": repo}, agents_dir, now - 2, pid=101, agent="claude")
            sp.write_hook({"hook_event_name": "PermissionRequest", "session_id": "s-codex", "cwd": repo,
                           "tool_name": "Bash", "tool_input": {"command": "pytest"}}, agents_dir, now - 1, pid=202, agent="codex")
            with patch.object(sp.os.path, "isdir", return_value=True):
                snap = engine.snapshot()
                by_ws = {w["id"]: w for w in snap["workspaces"]}
                self.assertEqual(by_ws[1].get("agent"), "done")
                self.assertEqual(by_ws[4].get("agent"), "wait")
                self.assertEqual(by_ws[4].get("agentLabel"), "Codex")
                by_proj_ws = {p["workspace"]: p for p in snap["projects"]}
                self.assertEqual(by_proj_ws[1].get("agent"), "done")
                self.assertEqual(by_proj_ws[4].get("agent"), "wait")

                daemon = sp.Daemon(engine)
                daemon.primed = True
                sent = []
                daemon.notify = lambda title, body, ws, image="", actions=None, **k: sent.append((title, body, ws))
                with patch.object(sp, "focused_workspace", return_value=1):
                    daemon.tick_agents()
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][0], "Arc · Codex needs you")
            self.assertEqual(sent[0][2], 4)

    def test_interpreter_commands_ignore_http_server_and_match_shell_records_and_chdir(self):
        http_fg = {"prog": "http.server", "argv": ["python3", "-m", "http.server"], "pid": 401, "start": 10 * sp.CLK_TCK, "cwd": "/pine"}
        self.assertIsNone(sp.command_kind(http_fg, RULES, {"bash"}, 120, 30))

        with tempfile.TemporaryDirectory() as d:
            tracker = sp.CommandSignals(d)
            pytest_fg = {"prog": "pytest", "argv": ["python3", "-m", "pytest"], "pid": 402, "start": 80 * sp.CLK_TCK, "cwd": "/pine/sub"}
            tab = {"pid": 51, "start": 100, "cwd": "/pine", "fg": pytest_fg}
            facts = [{"address": "0x1", "workspace": 5, "tabs": [tab]}]
            cfg = sp.resolve_settings({})
            tracker.update(facts, 1, RULES, cfg, {"bash"}, now=90)
            self.assertEqual(tracker.statuses()[5], {"command": "work", "commandText": "Tests running"})
            self.assertEqual(tracker.by_project({5: {"/pine"}})[(5, "/pine")]["command"], "work")
            tab["fg"] = None
            path = os.path.join(d, "51.json")
            sp.write_private(path, json.dumps({"start": 100, "cwd": "/pine", "command": "python3",
                                               "launched": 79.5, "ended": 90.5, "code": 1}))
            tracker.update(facts, 1, RULES, cfg, {"bash"}, now=91, exited=lambda pid: True)
            self.assertEqual(tracker.statuses()[5], {"command": "failed", "commandText": "Command failed"})
            self.assertEqual(tracker.by_project({5: {"/pine"}})[(5, "/pine")]["command"], "failed")

    def test_recompute_clears_finished_command_when_active_window_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            engine = sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json"))
            engine.view.command = ""
            tab = {"pid": 51, "start": 100, "cwd": "/pine", "fg": None}
            facts = [{"address": "0x1", "class": "kitty", "title": "kitty", "pid": 50, "workspace": 5,
                      "focus": 0, "floating": False, "shared": False,
                      "app": {"name": "kitty", "icon": "kitty", "desktop": "kitty", "webapp": False},
                      "tabs": [tab]}]
            engine.commands.finished[("0x1", 51, 100)] = {
                "job": {"id": 301, "started": 80.0, "cwd": "/pine", "cwds": {"/pine"},
                        "command": "make", "commands": {"make"}, "kind": "build", "ws": 5, "seen": 90.0},
                "at": 91.0, "outcome": False,
            }
            with patch.object(sp, "hypr_request", return_value=[]), \
                 patch.object(engine.probe, "facts", return_value=facts), \
                 patch.object(sp, "focused_workspace", return_value=5):
                engine.recompute(refresh=set(), active=None)
            self.assertEqual(engine.commands.statuses(), {})

    def test_bash_repeat_history_and_zsh_preserve_status_and_skip_empty_enter(self):
        zsh = sp.shell_init("zsh", "/opt/signpost.py")
        self.assertIn('return "$result"', zsh)
        self.assertIn("_signpost_active", zsh)
        self.assertIn("_signpost_shell_init", zsh)
        with tempfile.TemporaryDirectory() as d, patch.object(sp, "read_proc", return_value={"session": 123, "tty": 7, "start": 9}):
            with patch.object(sp.sys, "stdin", io.StringIO("cargo test")):
                sp.shell_record(["start", "123", "/work", "42"], d)
            sp.shell_record(["end", "123", "/work", "1"], d)
            # Same history number under HISTCONTROL=ignoredups must still record the repeat run.
            with patch.object(sp.sys, "stdin", io.StringIO("cargo test")):
                sp.shell_record(["start", "123", "/work", "42"], d)
            sp.shell_record(["end", "123", "/work", "0"], d)
            result = json.loads(Path(d, "123.json").read_text())
            self.assertEqual((result["command"], result["code"]), ("cargo", 0))

    def test_write_hook_preserves_cwd_and_supports_replace_message(self):
        with tempfile.TemporaryDirectory() as d:
            sp.write_hook({"hook_event_name": "permission.asked", "session_id": "s1", "cwd": "/pine",
                           "message": "Allow bash?"}, d, 100, agent="opencode")
            sp.write_hook({"hook_event_name": "permission.asked", "session_id": "s1", "cwd": "",
                           "message": "Allow edit?", "replace_message": True}, d, 105, agent="opencode")
            [sig] = sp.AgentSignals(d).read(106)
            self.assertEqual((sig["cwd"], sig["ts"], sig["message"]), ("/pine", 100.0, "Allow edit?"))
            sp.write_hook({"hook_event_name": "permission.replied", "session_id": "s1"}, d, 110, agent="opencode")
            [sig] = sp.AgentSignals(d).read(111)
            self.assertEqual((sig["cwd"], sig["state"]), ("/pine", "work"))

    def test_whatsapp_unread_matches_brave_and_other_chromium_browsers(self):
        windows = lambda cls, title: [{"class": cls, "title": title}]
        for cls, expected in [
            ("brave-web.whatsapp.com__-Default", 5),
            ("chromium-web.whatsapp.com__-Profile_1", 5),
            ("vivaldi-web.whatsapp.com__-Default", 5),
            ("brave-web.whatsapp.com__-Default.suffix", 0),
            ("brave-web.whatsapp.com.evil__-Default", 0),
        ]:
            with self.subTest(cls=cls):
                self.assertEqual(sp.unread_count(windows(cls, "(5) WhatsApp"), ["whatsapp"], RULES), expected)

    def test_decide_sets_aliased_flag_and_engine_reloads_user_rules(self):
        facts = load_fixture(os.path.join(ROOT, "tests", "fixtures", "t3-title-picks-agent.json"))["facts"]
        plain = sp.decide(facts, {"pins": {}, "aliases": {}}, {}, RULES)
        ws = next(k for k, v in plain.items() if v.get("aliasKey"))
        self.assertFalse(plain[ws]["aliased"])
        aliased = sp.decide(facts, {"pins": {}, "aliases": {plain[ws]["aliasKey"]: "MyApp"}}, {}, RULES)
        self.assertTrue(aliased[ws]["aliased"])

        with tempfile.TemporaryDirectory() as d:
            rules_path = Path(d, "rules.json")
            rules_path.write_text(json.dumps({"appNames": {"kitty": "Kit1"}}))
            engine = sp.Engine(state_file=os.path.join(d, "state.json"), user_rules=str(rules_path))
            self.assertEqual(engine.rules["appNames"]["kitty"], "Kit1")
            self.assertFalse(engine.rules_changed())
            rules_path.write_text(json.dumps({"appNames": {"kitty": "Kit22"}}))
            self.assertTrue(engine.rules_changed())
            self.assertEqual(engine.rules["appNames"]["kitty"], "Kit22")
            self.assertEqual(engine.probe.rules["appNames"]["kitty"], "Kit22")

    def test_unknown_exit_is_ended_never_green_and_is_kept_without_a_known_focus(self):
        with tempfile.TemporaryDirectory() as d:
            tracker = sp.CommandSignals(d)
            tab = {"pid": 51, "start": 100, "fg": CommandStatus.foreground("make")}
            facts = [{"address": "0x1", "workspace": 5, "tabs": [tab]}]
            cfg = sp.resolve_settings({})
            tracker.update(facts, None, RULES, cfg, {}, now=90)
            tab["fg"] = None
            tracker.update(facts, None, RULES, cfg, {}, now=91, exited=lambda pid: True)
            self.assertEqual(tracker.statuses()[5]["command"], "ended")       # not dropped, not green
            self.assertEqual(sp.COMMAND_STATES, {None: "ended", True: "ready", False: "failed"})

    def test_completion_survives_a_brief_prompt_hook_in_the_foreground(self):
        # The shell snippet (or a prompt tool) runs as its own foreground job right after the command:
        # a poll that lands then must still see the command as finished.
        with tempfile.TemporaryDirectory() as d:
            tracker = sp.CommandSignals(d)
            tab = {"pid": 51, "start": 100, "fg": CommandStatus.foreground("make")}
            facts = [{"address": "0x1", "workspace": 5, "tabs": [tab]}]
            cfg = sp.resolve_settings({})
            tracker.update(facts, 1, RULES, cfg, {}, now=90)
            tab["fg"] = {"prog": "signpost", "argv": ["python3", "-I", "/x/signpost.py", "shell-record", "end"],
                         "pid": 777, "start": 91 * sp.CLK_TCK, "cwd": "/pine"}
            tracker.update(facts, 1, RULES, cfg, {}, now=91, exited=lambda pid: pid == 301)
            self.assertEqual(tracker.statuses()[5]["command"], "ended")
            sp.write_private(os.path.join(d, "51.json"), json.dumps(
                {"start": 100, "cwd": "/pine", "command": "make", "launched": 79.5, "ended": 90.5, "code": 2}))
            tab["fg"] = None
            tracker.update(facts, 1, RULES, cfg, {}, now=92, exited=lambda pid: True)
            self.assertEqual(tracker.statuses()[5]["command"], "failed")

    def test_bash_snippet_skips_empty_enter_and_keeps_dollar_underscore(self):
        if not shutil.which("bash"):
            self.skipTest("bash not installed")
        with tempfile.TemporaryDirectory() as d:
            log = Path(d, "calls")
            fake = Path(d, "fake.py")
            fake.write_text(f"import sys\nopen({str(log)!r}, 'a').write(' '.join(sys.argv[1:3] + sys.argv[5:]) + '\\n')\n")
            rc = Path(d, "rc")
            rc.write_text("HISTFILE=/dev/null\nPROMPT_COMMAND='history -a'\n" + sp.shell_init("bash", str(fake)))
            script = "true\n\n\nfalse\necho one two\n\necho \"[$_]\"\nexit\n"
            out = subprocess.run(["bash", "--rcfile", str(rc), "-i"], input=script, text=True, capture_output=True,
                                 timeout=30, env={**os.environ, "PS1": "$ "})
            calls = log.read_text().splitlines()
            self.assertEqual([c for c in calls if c.startswith("shell-record end")],
                             ["shell-record end 0", "shell-record end 1", "shell-record end 0", "shell-record end 0"])
            # Five commands (exit is one; it never returns to a prompt); the three empty lines start nothing.
            self.assertEqual(sum(c.startswith("shell-record start") for c in calls), 5)
            self.assertIn("[two]", out.stdout)

    def test_status_names_codex_and_opencode_adapters(self):
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()):
            codex, opencode = Path(d, "codex"), Path(d, "opencode")
            self.assertEqual(sp.other_hooks_state(str(codex), str(opencode)), {"codex": "none", "opencode": "none"})
            self.assertEqual(sp.others_line({"codex": "none", "opencode": "none"}), "")
            self.assertEqual(sp.others_line({"codex": "none", "opencode": "none"}, {"codex"}), "codex: CPU only")
            sp.hooks_cli(["install", "--agent", "codex", "--config-dir", str(codex)])
            sp.hooks_cli(["install", "--agent", "opencode", "--config-dir", str(opencode)])
            state = sp.other_hooks_state(str(codex), str(opencode))
            self.assertEqual(state, {"codex": "installed", "opencode": "installed"})
            self.assertEqual(sp.others_line(state), "codex: hook installed · opencode: plugin installed")
        engine = sp.Engine(rules=RULES, state_file="/nonexistent/state.json")
        engine.view.command = ""
        engine.others = {"codex": "installed", "opencode": "none"}
        snap = engine.snapshot()
        self.assertEqual(snap["others"], "codex: hook installed")
        self.assertIn("codex: hook installed", sp.explain_lines(snap))

    def test_doctor_marks_problems_and_optional_parts(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d, "state")
            state.mkdir(mode=0o755)
            os.chmod(state, 0o755)
            rules = Path(d, "rules.json")
            rules.write_text(json.dumps({"commands": [{"kind": "test", "pattern": "("}]}))
            have = {"git", "busctl"}
            run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "git version 2.39.1\n", "")
            with patch.object(sp, "hook_install_state", return_value=("none", [])), \
                 patch.object(sp, "other_hooks_state", return_value={"codex": "partial", "opencode": "none"}):
                rows = dict((text.split(" ")[0] + ("" if not text.startswith(("Claude", "Codex", "OpenCode")) else text.split(" ")[1]), mark)
                            for mark, text in sp.doctor_report(which=lambda t: f"/bin/{t}" if t in have else None, run=run,
                                                               rules_file=str(rules), state_dir=str(state),
                                                               processes={1: (0, ["python3", "-I", "/x/signpost.py", "watch"])}))
                texts = [text for _, text in sp.doctor_report(which=lambda t: None, run=run, rules_file=str(rules),
                                                               state_dir=str(state), processes={})]
            self.assertEqual(rows["git"], "!!")                 # 2.39 is too old for the switcher's git line
            self.assertEqual(rows["busctl"], "ok")
            self.assertEqual(rows["gdbus"], "!!")
            self.assertEqual(rows["claude"], "--")
            self.assertEqual(rows["ClaudeCode"], "--")
            self.assertEqual(rows["Codexhooks:"], "!!")        # partly installed
            self.assertEqual(rows["engine:"], "ok")
            self.assertTrue(any("bad command rule" in t for t in texts))
            self.assertTrue(any("others can read it" in t for t in texts))
            self.assertTrue(any("engine: not running" in t for t in texts))

    def test_capture_writes_private_mode(self):
        with tempfile.TemporaryDirectory() as d:
            engine = sp.Engine(rules=RULES, state_file=os.path.join(d, "state.json"))
            with patch.object(sp, "CAPTURE_DIR", d), patch.object(engine.probe, "facts", return_value=[]):
                path = sp.capture(engine, 1)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
