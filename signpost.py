#!/usr/bin/env python3
"""Signpost: names Hyprland workspaces after what's in them.

One file, no dependencies beyond Python 3 (which Omarchy ships). The Omarchy bar
widget runs `signpost.py watch`.

  signpost.py watch [--settings JSON]   JSON line on stdout whenever names change;
                                        JSON commands on stdin (see Daemon.command)
  signpost.py once [--json]             one snapshot
  signpost.py explain                   why each workspace has its name
  signpost.py facts [--ws N]            what the probe saw, per window
  signpost.py capture [--ws N]          save a workspace as a test fixture
  signpost.py pin NAME [--ws N]         pin a name to a workspace
  signpost.py unpin [--ws N]
  signpost.py alias NAME [--ws N]       rename the project on that workspace, everywhere
  signpost.py unalias [--ws N]
  signpost.py hooks status|install|remove [--agent claude|codex|opencode] [--config-dir DIR]
                                        manage optional agent status hooks
  signpost.py hook [--agent AGENT]       what the hook runs: reads one event on stdin
  signpost.py shell-init bash|zsh|fish   print an optional shell snippet for command exit status
  signpost.py doctor                    check what Signpost needs and which optional parts are set up
  signpost.py swap | unswap             put Signpost in the place of Omarchy's workspace widget, or undo

Names are copied, never invented: every name comes from a folder, a git repo,
an app's own Name=, an ssh host, a model on a command line, a title rule, or
text you typed. `explain` says which.
"""

import argparse
import concurrent.futures
import contextlib
import fcntl
import fnmatch
import glob
import hashlib
import io
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import socket
import stat as statmod
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

VERSION = "0.5.0"
HERE = os.path.dirname(os.path.realpath(__file__))
HOME = os.path.expanduser("~")
STATE_HOME = os.environ.get("XDG_STATE_HOME") or HOME + "/.local/state"
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or HOME + "/.config"
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
STATE_FILE = STATE_HOME + "/signpost/state.json"
USER_RULES = CONFIG_HOME + "/signpost/rules.json"
USER_ICONS = CONFIG_HOME + "/signpost/icons"
CAPTURE_DIR = STATE_HOME + "/signpost/captures"
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

ALL_SOURCES = ["agents", "projects", "ssh", "models", "titleRules", "webapps", "apps"]
BADGE_STATES = ["working", "needsYou", "ready"]

# The widget's settings, as manifest.json declares them (same order, same defaults). The bar sends what
# shell.json holds for the widget; resolve_settings() fills in, checks and migrates it, and the engine
# hands the result back to the bar, so both read the same values.
DEFAULT_SETTINGS = {
    # Bar
    "names": "smart",            # smart · all · focused · hover · none
    "fit": True,                 # shrink the least used names first when they don't fit
    "style": "tiles",            # tiles · logos · mono · minimal
    "density": "comfortable",    # comfortable · compact
    "showNumbers": True,
    "showMore": True,            # other projects and unread counts
    "plainWorkspaces": [],       # workspace ids that always show their number only
    "ignoreClasses": "",
    # Agents
    "agentBadge": True,
    "badgeStates": list(BADGE_STATES),
    "alerts": True,
    "showQuestions": True,       # off: an agent's question never leaves the engine
    "hideWhenSharing": True,
    "commandStatus": True,
    "unreadApps": [],
    # Hover and switcher
    "hoverCard": True,
    "switcherGit": "selected",   # always · selected · never
    "switcherRecents": True,
    "projectRoots": ["~/Projects"],
    "recentIgnore": ["/tmp/*", "~/.cache/*"],
    "agentCommand": "claude",
    # Advanced
    "maxWidthPercent": 40,
    "maxLength": 12,
    "longCommandSeconds": 30,
    "renameDelay": 5,            # only when another window wins because you focused it
    "sources": list(ALL_SOURCES),
    # Engine only, not in the manifest
    "pollSeconds": 2,            # cheap re-look at terminals and agent apps only
    "fullSeconds": 30,           # everything, as a safety net
}
SETTING_CHOICES = {"names": ("smart", "all", "focused", "hover", "none"), "style": ("tiles", "logos", "mono", "minimal"),
                   "density": ("comfortable", "compact"), "switcherGit": ("always", "selected", "never")}
SETTING_RANGES = {"maxWidthPercent": (10, 90), "maxLength": (6, 32), "renameDelay": (0, 60),
                  "longCommandSeconds": (1, 3600), "pollSeconds": (1, 60), "fullSeconds": (5, 600)}
# Settings of Signpost 0.3.0, read when the new key is absent. The user's shell.json is never rewritten.
OLD_SETTINGS = ("labelMode", "iconStyle", "projectTiles", "busyDot", "notifyWhenWaiting", "holdSeconds")


def _bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0", "yes", "no", "on", "off"):
        return value.strip().lower() in ("true", "1", "yes", "on")    # `omarchy bar set` stores strings
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return default


def _list(value):
    """A list setting, given as a JSON list or as comma-separated text."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return None
    return [clean_text(v) for v in value if isinstance(v, (str, int)) and not isinstance(v, bool) and clean_text(v)]


def resolve_settings(raw):
    """What shell.json holds for the widget (any keys, any types) → every setting, valid. Old keys
    are migrated when their new key is absent:
      labelMode fit → names smart (fit on) · all/focused/hover → names all/focused/hover (fit off)
      iconStyle + projectTiles → style: none → minimal · mono or glyph → mono ·
                                 color with projectTiles off → logos · otherwise tiles
      busyDot → agentBadge · notifyWhenWaiting → alerts · holdSeconds → renameDelay"""
    raw = dict(raw) if isinstance(raw, dict) else {}
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()}
    old_label = raw.get("labelMode")
    if "names" not in raw and old_label in ("fit", "all", "focused", "hover"):
        raw["names"] = "smart" if old_label == "fit" else old_label
        if "fit" not in raw:
            raw["fit"] = old_label == "fit"
    if "style" not in raw and ("iconStyle" in raw or "projectTiles" in raw):
        icons, tiles = raw.get("iconStyle", "color"), _bool(raw.get("projectTiles"), True)
        raw["style"] = ("minimal" if icons == "none" else "mono" if icons in ("mono", "glyph")
                        else "tiles" if tiles else "logos")
    for old, new in (("busyDot", "agentBadge"), ("notifyWhenWaiting", "alerts"), ("holdSeconds", "renameDelay"),
                     ("notify", "alerts"), ("busy", "agentBadge")):
        if new not in raw and old in raw:
            raw[new] = raw[old]
    for key, default in DEFAULT_SETTINGS.items():
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if key in SETTING_CHOICES:
            value = str(value).strip().lower()
            choices = {c.lower(): c for c in SETTING_CHOICES[key]}
            out[key] = choices.get(value, default)
        elif isinstance(default, bool):
            out[key] = _bool(value, default)
        elif isinstance(default, int):
            try:
                low, high = SETTING_RANGES[key]
                out[key] = max(low, min(high, int(float(value))))
            except (TypeError, ValueError, OverflowError):
                pass
        elif key == "plainWorkspaces":
            items = _list(value) or []
            out[key] = sorted({int(v) for v in items if str(v).isdigit() and 0 < int(v) < 10000})
        elif key in ("sources", "badgeStates"):
            allowed = ALL_SOURCES if key == "sources" else BADGE_STATES
            items = [v for v in (_list(value) or []) if v in allowed]
            out[key] = [v for v in allowed if v in items] or list(allowed)    # empty means all
        elif key in ("recentIgnore", "unreadApps", "projectRoots"):
            items = _list(value)
            out[key] = items if items is not None else default
        elif isinstance(default, str):
            out[key] = clean_text(value)[:200] if key != "ignoreClasses" else clean_text(value)[:1000]
    if not out["agentCommand"]:
        out["agentCommand"] = "claude"
    return out


def public_settings(settings):
    """The settings the bar draws with (engine-only keys left out)."""
    return {k: settings[k] for k in DEFAULT_SETTINGS if k not in ("pollSeconds", "fullSeconds")}
COUNTABLE = {"agent", "project", "ssh", "model"}
STRONG_SOURCES = {"agent", "ssh", "model", "project", "title", "webapp"}


def warn(msg):
    print(f"signpost: {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- rules

def _merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        elif isinstance(value, list) and isinstance(base.get(key), list):
            base[key] = value + base[key]
        else:
            base[key] = value
    return base


def load_rules(user_file=USER_RULES):
    with open(os.path.join(HERE, "rules.json")) as f:
        rules = json.load(f)
    try:
        with open(user_file) as f:
            _merge(rules, json.load(f))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        warn(f"ignoring {user_file}: {e}")
    rules["_terminals"] = {t.lower() for t in rules.get("terminals", [])}
    rules["_meaningless"] = {expand_home(p.replace("$XDG_RUNTIME_DIR", RUNTIME_DIR)) for p in rules.get("meaningless", [])}
    rules["_titleRules"] = []
    for r in rules.get("titleRules", []):
        try:
            rules["_titleRules"].append({**r, "_class": re.compile(r["class"], re.I), "_pattern": re.compile(r["pattern"])})
        except (KeyError, re.error) as e:
            warn(f"bad title rule {r!r}: {e}")
    rules["_commands"] = []
    rules["_commandIgnore"] = []
    for rule in rules.get("commands") or []:
        try:
            if rule["kind"] not in ("build", "test", "long"):
                raise ValueError("unknown command kind")
            rules["_commands"].append((re.compile(rule["pattern"]), rule["kind"]))
        except (KeyError, TypeError, ValueError, re.error) as e:
            warn(f"bad command rule {rule!r}: {e}")
    for pattern in rules.get("commandIgnore") or []:
        try:
            rules["_commandIgnore"].append(re.compile(pattern))
        except (TypeError, re.error) as e:
            warn(f"bad command ignore rule {pattern!r}: {e}")
    rules["_unread"] = {}
    unread = rules.get("unread")
    for key, rule in (unread.items() if isinstance(unread, dict) else ()):
        try:
            window_class = re.compile(rule["class"], re.I)
            title = re.compile(rule["unread"])
            if "count" not in title.groupindex:
                raise ValueError("missing named count group")
            rules["_unread"][key] = (window_class, title)
        except (KeyError, TypeError, ValueError, re.error) as e:
            warn(f"bad unread rule {key!r}: {e}")
    return rules


def icon_overrides(directory=USER_ICONS):
    try:
        files = sorted(Path(directory).iterdir())
    except OSError:
        return {}
    return {p.stem: p.absolute().as_uri() for p in files
            if p.suffix in (".png", ".svg") and re.fullmatch(r"[a-zA-Z0-9_.-]+", p.stem) and p.is_file()}


# --------------------------------------------------------------------------- small pure helpers

def expand_home(path):
    if path == "~" or path.startswith("~/"):
        return HOME + path[1:]
    return path


def collapse_home(path):
    if path and (path == HOME or path.startswith(HOME + "/")):
        return "~" + path[len(HOME):]
    return path


def clean_text(text):
    """Window titles come from apps and web pages: no control characters, one line."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def hue_of(key):
    """A stable colour for a project, 0-359. The same key always gives the same hue, on every run
    and every machine (a digest, not Python's per-process hash; crc32 clustered paths that share a
    prefix into one colour). Worktrees share their repo's key."""
    return int.from_bytes(hashlib.sha1(str(key).encode("utf-8")).digest()[:4], "big") % 360


def monogram_of(name):
    """The letter for a project's tile: the first letter of the first word that has one, so a dated or
    numbered folder ("2026-11-19-fix-login", "01_notes") gets "F" or "N", not "2" or "0". A word that
    starts with a digit but has letters ("3d-printer") keeps its digit. With no letters at all, the
    first digit."""
    words = [w for w in re.split(r"[\W_]+", clean_text(name)) if w]
    for word in words:
        if any(ch.isalpha() for ch in word):
            return word[0].upper()
    return words[0][0].upper() if words else "•"


def cut(name, limit):
    """Shorten to `limit` characters. "repo · label" keeps the label and shortens the repo."""
    if len(name) <= limit:
        return name
    i = name.rfind(" · ")
    if i > 0:
        tail = name[i:]
        room = limit - len(tail) - 1
        if room >= 3:
            return name[:room] + "…" + tail
        # The label is the specific part ("Obsidian · Starling Plans"); the logo already says the rest.
        label = name[i + 3:]
        return label if len(label) <= limit else label[:max(1, limit - 1)] + "…"
    return name[:max(1, limit - 1)] + "…"


def short_name(name, source):
    if source in ("ssh", "model") and " · " in name:
        return cut(name.split(" · ")[-1], 7)
    return cut(name, 7)


def worktree_label(folder, repo, branch):
    """Starling-wt-d1 → d1. Agents name the folder; the branch is often longer (landing/smooth)."""
    if folder.lower().startswith(repo.lower()) and len(folder) > len(repo):
        rest = folder[len(repo):]
        rest = re.sub(r"^[-_.]?(?:wt|worktrees?)[-_.]", "", rest, flags=re.I)
        rest = rest.lstrip("-_.")
        if rest:
            return rest
    if branch:
        return branch.rsplit("/", 1)[-1]
    return folder


def ssh_host(argv):
    takes_value = set("bcDEeFIiJLlmOopQRSWw")
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--":
            i += 1
            break
        if a.startswith("-") and len(a) >= 2:
            if a[1] in takes_value and len(a) == 2:
                i += 2
                continue
            i += 1
            continue
        break
    if i >= len(argv):
        return None
    host = argv[i]
    host = re.sub(r"^ssh://", "", host)
    host = host.rsplit("@", 1)[-1]
    host = re.sub(r":\d+$", "", host)
    return host or None


def model_name(raw):
    base = os.path.basename(raw or "")
    base = re.sub(r"\.gguf$", "", base, flags=re.I)
    base = re.sub(r"[-.](?:I?Q\d\w*|F16|BF16|F32)$", "", base, flags=re.I)
    return base.split(":")[0] or "model"


def short_app_name(name, rules):
    """A few apps have long names ("Visual Studio Code"): rules.json's appNames shortens them."""
    names = rules.get("appNames") if isinstance(rules.get("appNames"), dict) else {}
    short = names.get(name)
    return clean_text(short) if isinstance(short, str) and clean_text(short) else name


def tidy_class(cls):
    parts = [p for p in re.split(r"[.]", cls or "") if p]
    generic = {"com", "org", "io", "net", "app", "client", "desktop", "github", "gitlab"}
    for p in reversed(parts):
        if p.lower() not in generic:
            return p
    return cls or "?"


def pts_path(tty_nr):
    major = (tty_nr >> 8) & 0xFFF
    minor = (tty_nr & 0xFF) | ((tty_nr >> 12) & 0xFFF00)
    if 136 <= major <= 143:
        return f"/dev/pts/{(major - 136) * 256 + minor}"
    return None


def match_key(name, table, rules=None):
    """Exact name, or name-*. Short keys marked exact (pi) never match a prefix."""
    if not name:
        return None
    if name in table:
        return name
    for key, spec in table.items():
        if isinstance(spec, dict) and spec.get("exact"):
            continue
        if name.startswith(key + "-"):
            return key
    return None


# --------------------------------------------------------------------------- git

class Git:
    """Folder → repo, with worktrees resolved to their main repo. Reads git's files only."""

    def __init__(self, ttl=30.0):
        self.ttl = ttl
        self.cache = {}

    def info(self, path):
        if not path:
            return None
        hit = self.cache.get(path)
        now = time.monotonic()
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        result = self._resolve(path)
        self.cache[path] = (now, result)
        return result

    @staticmethod
    def _branch(gitdir):
        try:
            with open(os.path.join(gitdir, "HEAD")) as f:
                head = f.read().strip()
        except OSError:
            return None
        if head.startswith("ref:"):
            return head.split("refs/heads/", 1)[-1]
        return head[:7] or None

    def _resolve(self, path):
        p = path
        for _ in range(64):
            g = os.path.join(p, ".git")
            try:
                st = os.lstat(g)
            except OSError:
                st = None
            if st is not None:
                if statmod.S_ISDIR(st.st_mode):
                    branch = self._branch(g)
                    if branch is not None:
                        return {"kind": "repo", "root": p, "main": p, "repo": os.path.basename(p) or p, "label": None, "branch": branch}
                else:
                    try:
                        with open(g) as f:
                            text = f.read().strip()
                    except OSError:
                        text = ""
                    if text.startswith("gitdir:"):
                        return self._resolve_gitfile(p, text[7:].strip())
            parent = os.path.dirname(p)
            if parent == p:
                return None
            p = parent
        return None

    def _resolve_gitfile(self, path, gitdir):
        if not os.path.isabs(gitdir):
            gitdir = os.path.normpath(os.path.join(path, gitdir))
        match = re.match(r"(.+?)/\.git/worktrees/[^/]+/?$", gitdir)
        branch = self._branch(gitdir)
        if match:
            main = match.group(1)
            repo = os.path.basename(main)
            return {"kind": "worktree", "root": path, "main": main, "repo": repo,
                    "label": worktree_label(os.path.basename(path), repo, branch), "branch": branch}
        return {"kind": "repo", "root": path, "main": path, "repo": os.path.basename(path), "label": None, "branch": branch}


# --------------------------------------------------------------------------- desktop entries

WEBAPP_CLASS = re.compile(r"^[a-z][a-z-]*?-([^-]*\.[^_]*?__.*)$", re.I)


def webapp_key(cls):
    """chrome-x.com__home-Default and brave-x.com__home-Default are the same web app:
    Omarchy writes the chrome- form, each Chromium browser uses its own prefix."""
    m = WEBAPP_CLASS.match(cls or "")
    return m.group(1).lower() if m else None


class Desktop:
    """Window class → .desktop Name= / Icon=, refreshed now and then for new installs."""

    def __init__(self, ttl=60.0):
        self.ttl = ttl
        self.loaded = 0.0
        self.by_wmclass = {}
        self.by_id = {}
        self.by_webkey = {}

    @staticmethod
    def dirs():
        data_home = os.environ.get("XDG_DATA_HOME") or HOME + "/.local/share"
        data_dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
        out = [data_home + "/applications"] + [d + "/applications" for d in data_dirs if d]
        out += [HOME + "/.local/share/flatpak/exports/share/applications", "/var/lib/flatpak/exports/share/applications"]
        seen, uniq = set(), []
        for d in out:
            if d not in seen:
                seen.add(d)
                uniq.append(d)
        return uniq

    @staticmethod
    def parse(path):
        entry, section = {}, None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("["):
                        if section == "Desktop Entry":
                            break
                        section = line.strip("[]")
                        continue
                    if section != "Desktop Entry" or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k in ("Name", "Icon", "StartupWMClass", "Exec", "NoDisplay") and k not in entry:
                        entry[k] = v.strip()
        except OSError:
            return None
        return entry if entry.get("Name") else None

    def load(self):
        self.by_wmclass, self.by_id, self.by_webkey = {}, {}, {}
        for d in self.dirs():
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for n in names:
                if not n.endswith(".desktop"):
                    continue
                e = self.parse(os.path.join(d, n))
                if not e:
                    continue
                exec_ = e.get("Exec", "")
                item = {"name": e["Name"], "icon": e.get("Icon", ""), "id": n[:-8],
                        "webapp": "--app=" in exec_ or "omarchy-launch-webapp" in exec_}
                wm = e.get("StartupWMClass", "").lower()
                if wm and wm not in self.by_wmclass:
                    self.by_wmclass[wm] = item
                wk = webapp_key(wm)
                if wk and wk not in self.by_webkey:
                    self.by_webkey[wk] = {**item, "webapp": True}
                self.by_id.setdefault(n[:-8].lower(), item)
        self.loaded = time.monotonic()

    def lookup(self, cls):
        if time.monotonic() - self.loaded > self.ttl:
            self.load()
        c = (cls or "").lower()
        if not c:
            return None
        if c in self.by_wmclass:
            return self.by_wmclass[c]
        wk = webapp_key(c)
        if wk and wk in self.by_webkey:
            return self.by_webkey[wk]
        if c in self.by_id:
            return self.by_id[c]
        for key, item in self.by_id.items():
            if key.endswith("." + c):
                return item
        parts = [p for p in c.split(".") if p not in ("com", "org", "io", "net", "app", "client", "desktop")]
        for p in sorted(parts, key=len, reverse=True):
            if p in self.by_id:
                return self.by_id[p]
        return None


# --------------------------------------------------------------------------- /proc

def read_proc(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            s = f.read()
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    r = s.rfind(")")
    fields = s[r + 2:].split()
    try:
        return {
            "pid": pid, "comm": s[s.find("(") + 1:r],
            "ppid": int(fields[1]), "pgrp": int(fields[2]), "session": int(fields[3]),
            "tty": int(fields[4]), "tpgid": int(fields[5]),
            "cpu": int(fields[11]) + int(fields[12]), "start": int(fields[19]),
            "argv": [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a],
        }
    except (IndexError, ValueError):
        return None


def proc_children(pid):
    out = []
    try:
        tasks = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return out
    for t in tasks:
        try:
            with open(f"/proc/{pid}/task/{t}/children") as f:
                out.extend(int(x) for x in f.read().split())
        except (OSError, ValueError):
            pass
    return out


def proc_cwd(pid):
    try:
        path = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None
    return None if path.endswith(" (deleted)") else path


def shells_from_etc():
    names = {"sh", "bash", "zsh", "fish", "nu", "dash", "ksh", "tcsh", "xonsh", "elvish", "oil", "osh"}
    try:
        with open("/etc/shells") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    names.add(os.path.basename(line))
    except OSError:
        pass
    return names


class ProcTable:
    """One probe's view of /proc. Cached per probe; never trusted across probes."""

    def __init__(self, rules):
        self.rules = rules
        self.cache = {}

    def get(self, pid):
        if pid not in self.cache:
            self.cache[pid] = read_proc(pid)
        return self.cache[pid]

    def tree(self, root, limit=3000):
        """Processes under `root` in depth-first order, each with its depth."""
        out, stack, seen = [], [(root, 0)], set()
        while stack and len(out) < limit:
            pid, depth = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            p = self.get(pid)
            if not p:
                continue
            out.append(p)
            stack.extend((c, depth + 1) for c in reversed(proc_children(pid)))
        return out

    def prog(self, p):
        argv = p["argv"]
        exe = os.path.basename(argv[0]) if argv else p["comm"]
        exe = exe.lstrip("-")
        interp = self.rules.get("interpreters", [])
        if exe in interp or re.fullmatch(r"python3(\.\d+)?", exe):
            for a in argv[1:]:
                if a.startswith("-") or a in ("run", "exec", "x"):
                    continue
                for hint, key in self.rules.get("scriptHints", {}).items():
                    if hint in a:
                        return key
                b = re.sub(r"\.(?:[cm]?js|ts|py)$", "", os.path.basename(a))
                if b in ("cli", "index", "main"):
                    parent = os.path.basename(os.path.dirname(a.rstrip("/")))
                    for key in self.rules.get("agents", {}):
                        if key in parent:
                            return key
                return b or exe
        return exe


# --------------------------------------------------------------------------- probe → facts

class Activity:
    """CPU use of agent processes between probes: who is working, who was used last."""

    def __init__(self, busy_share=0.05):
        self.busy_share = busy_share
        self.seen = {}  # pid -> (cpu ticks, monotonic time, start, last active wall time)

    def sample(self, pid, cpu, start):
        now_m, now_w = time.monotonic(), time.time()
        prev = self.seen.get(pid)
        busy, last = False, 0.0
        if prev and prev[2] == start:
            dt = now_m - prev[1]
            if dt > 0:
                busy = (cpu - prev[0]) / CLK_TCK / dt >= self.busy_share
            last = prev[3]
        if busy:
            last = now_w
        self.seen[pid] = (cpu, now_m, start, last)
        return busy, last

    def prune(self, alive):
        for pid in list(self.seen):
            if pid not in alive:
                del self.seen[pid]


def kitty_panes(layout, title, shared):
    choices = []
    for window in layout:
        tabs = window["tabs"]
        active = [pane for tab in tabs if tab.get("is_active") is True
                  for pane in tab["windows"] if pane.get("is_active") is True]
        if len(active) == 1:
            choices.append((active[0], [pane for tab in tabs for pane in tab["windows"]]))
    if len(choices) != len(layout):
        return None
    if not shared and len(choices) == 1:
        return choices[0]
    matches = [choice for choice in choices if title and clean_text(choice[0].get("title")) == title]
    return matches[0] if len(matches) == 1 else None


def wezterm_panes(layout, title, shared):
    windows = {}
    for pane in layout:
        windows.setdefault(pane["window_id"], {}).setdefault(pane["tab_id"], []).append(pane)
    choices, matches = [], []
    for tabs in windows.values():
        all_panes = [pane for panes in tabs.values() for pane in panes]
        for index, panes in enumerate(tabs.values(), 1):
            active = [p for p in panes if p.get("is_active") is True]
            if len(panes) == 1 and "is_active" not in panes[0]:
                active = panes  # older CLI output, one pane in the tab
            if len(active) != 1:
                continue
            pane = active[0]
            title_prefix = "[Z] " if pane.get("is_zoomed") else ""
            if len(tabs) > 1:
                title_prefix += f"[{index}/{len(tabs)}] "
            pane_title = clean_text(pane.get("title"))
            titles = {title_prefix + pane_title} if pane_title else set()
            if len(tabs) == 1:
                titles.update((pane_title, clean_text(pane.get("window_title"))))
            choice = (pane, all_panes)
            choices.append(choice)
            if title and title in titles:
                matches.append(choice)
    if not shared and len(choices) == 1 and sum(len(tabs) for tabs in windows.values()) == 1:
        return choices[0]
    return matches[0] if len(matches) == 1 else None


def local_pane_folder(cwd):
    if not isinstance(cwd, str):
        return None
    if cwd.startswith("file:"):
        try:
            uri = urlsplit(cwd)
        except ValueError:
            return None
        if uri.netloc not in ("", "localhost", socket.gethostname()):
            return None
        cwd = unquote(uri.path)
    return cwd if "\x00" not in cwd and os.path.isabs(cwd) and os.path.isdir(cwd) else None


class Probe:
    def __init__(self, rules, activity=None):
        self.rules = rules
        self.git = Git()
        self.desktop = Desktop()
        self.activity = activity or Activity()
        self.shells = shells_from_etc()
        self.cache = {}  # window address -> facts seen last time
        self.pane_lists = {}
        self.pane_retry = {}
        self.pane_budget = 0.06
        self._dynamic = [r["_class"] for r in rules["_titleRules"] if r.get("kind") in ("agentHost", "project")]

    def clients(self):
        return hypr_request("j/clients") or []

    def dynamic(self, f):
        """Windows whose name can change without their title changing: terminals and agent apps."""
        return "tabs" in f or bool(f.get("agents")) or any(c.search(f["class"]) for c in self._dynamic)

    @staticmethod
    def agent_pids(f):
        pids = [a["pid"] for a in f.get("agents") or [] if a.get("pid")]
        for t in f.get("tabs") or []:
            a = (t.get("fg") or {}).get("agent")
            if a and a.get("pid"):
                pids.append(a["pid"])
        return pids

    def facts(self, clients=None, refresh="all"):
        """Facts for every window. `refresh` says which to look at again: "all", "poll"
        (terminals and agent apps) or a set of addresses. The rest reuse what was seen last
        time, with workspace, focus order and title kept current. /proc is only walked for those."""
        clients = self.clients() if clients is None else clients
        procs = ProcTable(self.rules)
        self.pane_lists.clear()
        self.pane_budget = 0.06  # total wall time available to optional local CLI calls this refresh
        pid_count = {}
        for c in clients:
            pid_count[c.get("pid")] = pid_count.get(c.get("pid"), 0) + 1
        out, alive, seen = [], set(), set()
        for c in clients:
            ws = (c.get("workspace") or {}).get("id", -1)
            if ws is None or ws <= 0:
                continue  # special workspaces (scratchpads) are not on the bar
            addr = c.get("address")
            seen.add(addr)
            cls = c.get("class") or c.get("initialClass") or ""
            shared = pid_count.get(c.get("pid"), 1) > 1
            old = self.cache.get(addr)
            fresh = (refresh == "all" or old is None or old["class"] != cls or old["pid"] != c.get("pid")
                     or ("tabs" in old and old["shared"] != shared)
                     or (refresh == "poll" and self.dynamic(old))
                     or (isinstance(refresh, set) and addr in refresh))
            if fresh:
                f = self.window(c, procs, shared, alive)
            else:
                f = dict(old, workspace=ws, focus=c.get("focusHistoryID", 99), floating=bool(c.get("floating")),
                         title=clean_text(c.get("title")), shared=shared)
                alive.update(self.agent_pids(f))
            self.cache[addr] = f
            out.append(f)
        for addr in list(self.cache):
            if addr not in seen:
                del self.cache[addr]
        self.pane_lists.clear()
        self.pane_retry = {pid: retry for pid, retry in self.pane_retry.items() if pid in pid_count}
        self.activity.prune(alive)
        return out

    def git_of(self, path):
        g = self.git.info(path)
        return dict(g) if g else None

    def window(self, c, procs, shared, alive):
        rules = self.rules
        cls = c.get("class") or c.get("initialClass") or ""
        pid = c.get("pid") or -1
        entry = self.desktop.lookup(cls)
        f = {
            "address": c.get("address"), "class": cls, "title": clean_text(c.get("title")),
            "pid": pid, "workspace": (c.get("workspace") or {}).get("id"),
            "focus": c.get("focusHistoryID", 99), "floating": bool(c.get("floating")), "shared": shared,
            "app": {"name": short_app_name(entry["name"] if entry else tidy_class(cls), rules),
                    "icon": entry["icon"] if entry and entry["icon"] else cls.lower(),
                    "desktop": entry["id"] if entry else None,
                    "webapp": bool(entry and entry["webapp"])},
        }
        wk = webapp_key(cls)
        if wk and not f["app"]["webapp"]:
            f["app"]["webapp"] = True
            if not entry:
                f["app"]["name"] = re.sub(r"^www\.", "", wk.split("__", 1)[0])
        if pid <= 0:
            return f
        tree = procs.tree(pid)
        # A terminal by class, or by the program that owns the window: Omarchy starts
        # kitty with its own classes (org.omarchy.terminal, org.omarchy.btop, …).
        owner = procs.get(pid)
        program = procs.prog(owner).lower() if owner else ""
        if cls.lower() in rules["_terminals"] or program in rules["_terminals"]:
            f["tabs"] = self.tabs(tree, procs, alive)
            if program == "kitty" and (shared or len(f["tabs"]) != 1 or not f["tabs"][0].get("cwd")):
                self.use_terminal_panes(f, "kitty", kitty_panes, "pid", "pid")
            elif program == "wezterm-gui":
                self.use_terminal_panes(f, "WezTerm", wezterm_panes, "tty_name", "pts")
        else:
            f["agents"] = self.agents(tree, procs, alive)
        return f

    def terminal_layout(self, pid, terminal):
        if pid in self.pane_lists:
            return self.pane_lists[pid]
        if self.pane_budget < 0.005 or time.monotonic() < self.pane_retry.get(pid, 0):
            return None
        if terminal == "kitty":
            path = os.path.join(RUNTIME_DIR, f"omarchy-kitty-{pid}")
            cmd = ["kitten", "@", "--to", "unix:" + path, "--use-password", "never", "ls"]
            env = None
        else:
            path = os.path.join(RUNTIME_DIR, "wezterm", f"gui-sock-{pid}")
            cmd = ["wezterm", "cli", "list", "--format", "json"]
            env = {**os.environ, "WEZTERM_UNIX_SOCKET": path}
        try:
            st = os.stat(path, follow_symlinks=False)
        except OSError:
            return None
        if st.st_uid != os.getuid() or not statmod.S_ISSOCK(st.st_mode):
            return None
        started = time.monotonic()
        layout = None
        try:
            result = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=min(0.04, self.pane_budget), check=True)
            if len(result.stdout) <= 1024 * 1024:
                layout = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        self.pane_budget -= time.monotonic() - started
        if not isinstance(layout, list):
            self.pane_retry[pid] = started + 15  # absent, disabled or unresponsive control socket
            layout = None
        self.pane_lists[pid] = layout
        return layout

    def use_terminal_panes(self, facts, terminal, select_pane, native_key, proc_key):
        layout = self.terminal_layout(facts["pid"], terminal)
        if not layout:
            return
        try:
            selected = select_pane(layout, facts["title"], facts["shared"])
            if not selected:
                return
            pane, group = selected
            owned = {p.get(native_key) for p in group if p.get(native_key)}
        except (AttributeError, KeyError, TypeError):
            return  # older or unexpected CLI output
        tabs = facts["tabs"]
        if facts["shared"]:
            tabs = [tab for tab in tabs if tab.get(proc_key) in owned]
        identity = pane.get(native_key)
        active = next((tab for tab in tabs if identity and tab.get(proc_key) == identity), None)
        if active is None:
            cwd = local_pane_folder(pane.get("cwd"))
            if not cwd:
                return
            active = {"cwd": cwd, "git": self.git_of(cwd)}
            tabs = [*tabs, active]
        elif not active.get("cwd"):
            cwd = local_pane_folder(pane.get("cwd"))
            if cwd:
                active.update(cwd=cwd, git=self.git_of(cwd))
        facts.update(tabs=tabs, activeTab=tabs.index(active), paneSource=terminal)

    @staticmethod
    def subtree_cpu(pid, tree):
        """CPU ticks of a process and everything under it: an agent's tools run as children."""
        kids = {}
        for q in tree:
            kids.setdefault(q["ppid"], []).append(q)
        total, stack = 0, [pid]
        by_pid = {q["pid"]: q for q in tree}
        while stack:
            cur = stack.pop()
            q = by_pid.get(cur)
            if q:
                total += q["cpu"]
            stack.extend(k["pid"] for k in kids.get(cur, []))
        return total

    def agent_fact(self, p, key, procs, alive, tree=()):
        spec = self.rules["agents"][key]
        cwd = proc_cwd(p["pid"])
        alive.add(p["pid"])
        cpu = self.subtree_cpu(p["pid"], tree) if tree else p["cpu"]
        busy, last = self.activity.sample(p["pid"], cpu, p["start"])
        return {"agent": key, "label": spec.get("label", key), "logo": spec.get("logo"), "pid": p["pid"],
                "cwd": cwd, "git": self.git_of(cwd), "start": p["start"], "busy": busy, "lastActive": last}

    def agents(self, tree, procs, alive):
        """Agent processes anywhere under a window, not counting an agent's own children."""
        found, inside = [], set()
        for p in tree:
            if p["ppid"] in inside:
                inside.add(p["pid"])
                continue
            key = match_key(procs.prog(p), self.rules["agents"])
            if key:
                inside.add(p["pid"])
                found.append(self.agent_fact(p, key, procs, alive, tree))
        return found

    def tabs(self, tree, procs, alive):
        """One entry per shell the terminal owns (a tab, a split, or a window of a shared server)."""
        rules = self.rules
        out = []
        for sh in tree:
            if not (sh["session"] == sh["pid"] and sh["tty"] and procs.prog(sh) in self.shells):
                continue
            pts = pts_path(sh["tty"])
            try:
                idle = time.time() - os.stat(pts).st_mtime if pts else None
            except OSError:
                idle = None
            cwd = proc_cwd(sh["pid"])
            tab = {"shell": procs.prog(sh), "pid": sh["pid"], "start": sh["start"], "cwd": cwd, "git": self.git_of(cwd),
                   "pts": pts, "idle": round(idle, 1) if idle is not None else None, "fg": None}
            if sh["tpgid"] > 0 and sh["tpgid"] != sh["pid"]:
                group = [p for p in tree if p["pgrp"] == sh["tpgid"]]
                lead = procs.get(sh["tpgid"]) or (group[0] if group else None)
                agent = next((p for p in group if match_key(procs.prog(p), rules["agents"])), None)
                pick = agent or lead
                if pick:
                    prog = procs.prog(pick)
                    fcwd = proc_cwd(pick["pid"])
                    fg = {"prog": prog, "pid": pick["pid"], "start": pick["start"],
                          "argv": pick["argv"][:12], "cwd": fcwd, "git": self.git_of(fcwd)}
                    key = match_key(prog, rules["agents"])
                    if key:
                        fg["agent"] = self.agent_fact(pick, key, procs, alive, tree)
                    if prog == "tmux" and pts:
                        fg["tmux"] = self.tmux_pane(pts)
                    tab["fg"] = fg
            out.append(tab)
        return out

    def tmux_pane(self, pts):
        try:
            r = subprocess.run(["tmux", "display-message", "-p", "-c", pts, "#{pane_current_path}\t#{pane_current_command}"],
                               capture_output=True, text=True, timeout=0.5)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0 or "\t" not in r.stdout:
            return None
        path, cmd = r.stdout.rstrip("\n").split("\t", 1)
        return {"cwd": path, "cmd": cmd, "git": self.git_of(path)}


# --------------------------------------------------------------------------- facts → names (pure)

def project_of(path, git, rules):
    if git:
        if git["kind"] == "worktree":
            return {"kind": "worktree", "name": f"{git['repo']} · {git['label']}", "key": git["main"], "repo": git["repo"],
                    "wt": git["label"], "path": path, "root": git.get("root") or path}
        return {"kind": "repo", "name": git["repo"], "key": git["main"], "repo": git["repo"], "wt": None,
                "path": path, "root": git.get("root") or git["main"]}
    if not path:
        return {"kind": "none"}
    if path in rules["_meaningless"] or path.rstrip("/") in rules["_meaningless"]:
        return {"kind": "meaningless", "path": path}
    return {"kind": "folder", "name": os.path.basename(path.rstrip("/")) or path, "key": path, "repo": None, "wt": None,
            "path": path, "root": path}


def git_note(pr):
    return {"worktree": f" (worktree of {pr.get('repo')})", "repo": " (git)", "folder": " (folder, not a repo)"}.get(pr["kind"], "")


def app_cand(f, how=None):
    return {"name": f["app"]["name"], "source": "app", "strength": "weak", "key": None,
            "icon": {"kind": "app", "name": f["app"]["icon"]}, "glyph": "app", "detail": "app",
            "how": how or f"{f['class']} → {f['app']['desktop'] or 'no .desktop'} Name={f['app']['name']}",
            "address": f["address"], "layer": "none"}


def proj_cand(pr, f, source, strength, icon, glyph, detail, how, layer):
    return {"name": pr["name"], "source": source, "strength": strength, "key": pr.get("key"), "aliasKey": pr.get("key"),
            "repo": pr.get("repo"), "wt": pr.get("wt"), "path": pr.get("path"), "projectRoot": pr.get("root"),
            "icon": icon, "glyph": glyph, "detail": detail, "how": how, "address": f["address"], "layer": layer}


def tab_cand(f, tab, layer, note, rules, sources):
    """What one terminal tab says it is."""
    term = f["app"]["name"]
    tag = f"{term} pid {f['pid']}"
    why = f" · {note}" if note else ""
    tabtxt = ""
    if f.get("paneSource") and len(f.get("tabs") or []) > 1:
        tabtxt = " · active pane" if layer == "terminal" else " · other pane"
    fg = tab.get("fg") or {}
    prog = fg.get("prog")
    argv = fg.get("argv") or []
    tm = fg.get("tmux")
    if tm:  # tmux: the active pane decides, as if it were the shell
        prog = tm.get("cmd") if tm.get("cmd") not in (None, "") else None
        tab = {**tab, "cwd": tm.get("cwd") or tab.get("cwd"), "git": tm.get("git")}
        argv = [prog] if prog else []
        if prog in shells_default():
            prog = None
    agent = fg.get("agent")
    if prog and not agent and match_key(prog, rules["agents"]):
        key = match_key(prog, rules["agents"])
        spec = rules["agents"][key]
        agent = {"agent": key, "label": spec.get("label", key), "logo": spec.get("logo"), "cwd": tab.get("cwd"),
                 "git": tab.get("git"), "busy": False}
    if agent and "agents" in sources:
        pr = project_of(agent.get("cwd"), agent.get("git"), rules)
        if pr["kind"] in ("meaningless", "none"):
            pr = {"kind": "folder", "name": "~", "key": agent.get("cwd"), "path": agent.get("cwd")}
        c = proj_cand(pr, f, "agent", "strong", agent_icon(agent), "agent", f"{agent['label']} · in {term}{tabtxt}",
                      f"{agent['label']} in {tag}{why} → {collapse_home(agent.get('cwd') or '?')}{git_note(pr)}", layer)
        c["busy"] = bool(agent.get("busy"))
        c["agentLabel"] = agent["label"]
        return c
    if prog in rules.get("remotes", []) and "ssh" in sources:
        host = ssh_host(argv) or "?"
        return {"name": f"ssh · {host}", "source": "ssh", "strength": "strong", "key": f"ssh:{host}",
                "icon": {"kind": "glyph", "key": "ssh"}, "glyph": "ssh", "detail": f"ssh session · in {term}{tabtxt}",
                "how": f"{tag} · {tab.get('pts')}{why} · fg {' '.join(argv[:4])}", "address": f["address"], "layer": layer}
    mkey = match_key(prog, rules.get("models", {})) if prog else None
    if mkey and "models" in sources:
        spec = rules["models"][mkey]
        raw = None
        if spec.get("after") and spec["after"] in argv:
            i = argv.index(spec["after"])
            raw = argv[i + 1] if i + 1 < len(argv) else None
        elif spec.get("flag") and spec["flag"] in argv:
            i = argv.index(spec["flag"])
            raw = argv[i + 1] if i + 1 < len(argv) else None
        if raw:
            model = model_name(raw)
            return {"name": f"{spec['label']} · {model}", "source": "model", "strength": "strong", "key": f"model:{model}",
                    "icon": {"kind": "agent", "key": spec["logo"]} if spec.get("logo") else {"kind": "glyph", "key": "chip"},
                    "glyph": "chip", "detail": f"local model · in {term}{tabtxt}",
                    "how": f"{tag}{why} · fg {mkey} · {raw} → {model}", "address": f["address"], "layer": layer}
    cwd, git = tab.get("cwd"), tab.get("git")
    if prog in rules.get("editors", []) and fg.get("cwd"):
        cwd, git = fg.get("cwd"), fg.get("git")
    pr = project_of(cwd, git, rules)
    detail = (f"{prog} · in {term}" if prog in rules.get("editors", []) else f"shell · in {term}") + tabtxt
    what = prog if prog in rules.get("editors", []) else "shell"
    if "projects" in sources and pr["kind"] in ("repo", "worktree"):
        return dict(proj_cand(pr, f, "project", "strong", {"kind": "glyph", "key": "git"}, "git", detail,
                              f"{tag} · {tab.get('pts') or 'no tty'}{why} → {collapse_home(cwd)}{git_note(pr)}", layer), what=what)
    if "projects" in sources and pr["kind"] == "folder":
        return dict(proj_cand(pr, f, "folder", "weak", {"kind": "glyph", "key": "folder"}, "folder", detail,
                              f"{tag}{why} → {collapse_home(cwd)}{git_note(pr)}", layer), what=what)
    if "apps" in sources:
        return app_cand(f, f"{tag}{why} → {collapse_home(cwd) or '?'} says nothing about your work, so the app name")
    return None


_SHELLS = None


def shells_default():
    global _SHELLS
    if _SHELLS is None:
        _SHELLS = shells_from_etc()
    return _SHELLS


def agent_icon(agent):
    return {"kind": "agent", "key": agent["logo"]} if agent.get("logo") else {"kind": "glyph", "key": "agent"}


def is_loud(tab, rules):
    fg = tab.get("fg") or {}
    if fg.get("agent") or fg.get("tmux"):
        return True
    prog = fg.get("prog")
    return bool(prog and (prog in rules.get("remotes", []) or match_key(prog, rules.get("models", {}))
                          or prog in rules.get("editors", []) or match_key(prog, rules["agents"])))


def title_points_at(title, tab):
    """Does the window title name this tab's folder or program? Longest folder match wins."""
    t = title or ""
    score = 0
    cwd = tab.get("cwd")
    if cwd:
        for form in {cwd, collapse_home(cwd)}:
            if form and form in t:
                score = max(score, len(form))
        base = os.path.basename(cwd.rstrip("/"))
        if score == 0 and base and len(base) > 2 and re.search(r"(^|[\s/:·])" + re.escape(base) + r"($|[\s/:·])", t):
            score = len(base) // 2
    prog = (tab.get("fg") or {}).get("prog")
    if prog and prog not in shells_default() and re.search(r"(^|\W)" + re.escape(prog) + r"(\W|$)", t):
        score += 1
    return score


def terminal_cands(f, rules, sources):
    tabs = f.get("tabs") or []
    if not tabs:
        return (app_cand(f, f"{f['app']['name']} pid {f['pid']} · no shell found under it → app name") if "apps" in sources else None), []
    chosen, layer, note = tabs[0], "proc", ""
    active = f.get("activeTab")
    if isinstance(active, int) and 0 <= active < len(tabs):
        chosen, layer, note = tabs[active], "terminal", f"{f['paneSource']} reports the active pane"
    elif len(tabs) > 1 or f.get("shared"):
        loud = [t for t in tabs if is_loud(t, rules)]
        scores = [(title_points_at(f["title"], t), t) for t in tabs]
        best = max(s for s, _ in scores)
        by_title = [t for s, t in scores if s == best and s > 0]
        idle = [t for t in tabs if t.get("idle") is not None]
        recent = sorted(idle, key=lambda t: t["idle"])
        if len(by_title) == 1:
            chosen, layer, note = by_title[0], "title", f"{len(tabs)} shells, the window title points at {by_title[0].get('pts') or 'one'}"
        elif f.get("shared"):
            return (app_cand(f, "terminal process is shared, and the active pane could not be matched to this window")
                    if "apps" in sources else None), []
        elif len(loud) == 1:
            chosen, note = loud[0], f"{len(tabs)} shells, only {loud[0].get('pts') or 'one'} runs {(loud[0].get('fg') or {}).get('prog')}"
        elif len(recent) >= 2 and recent[1]["idle"] - recent[0]["idle"] >= 10:
            chosen, note = recent[0], f"{len(tabs)} tabs, {recent[0].get('pts')} was active {recent[0]['idle']:.0f} s ago"
        else:
            projects = [project_of(t.get("cwd"), t.get("git"), rules) for t in tabs]
            folders = {p.get("root") or p.get("path") for p in projects}
            if not loud and len(folders) == 1:
                note = f"{len(tabs)} tabs, all in the same project"
            else:
                extras = [c for c in (tab_cand(f, t, "proc", "", rules, sources) for t in tabs) if c and c["source"] in COUNTABLE]
                prim = app_cand(f, f"{f['app']['name']} pid {f['pid']} · {len(tabs)} shells disagree and the title doesn't say which → app name (never guess)") if "apps" in sources else None
                if prim:
                    prim["detail"] = "tabs disagree → app name"
                return prim, extras
    prim = tab_cand(f, chosen, layer, note, rules, sources)
    if f.get("shared") and layer != "terminal":
        return prim, []  # A title can identify the visible tab but cannot establish ownership of the others.
    extras = [c for c in (tab_cand(f, t, "proc", "", rules, sources) for t in tabs if t is not chosen) if c and c["source"] in COUNTABLE]
    return prim, extras


def title_rule(f, rules, kinds):
    for r in rules["_titleRules"]:
        if r.get("kind") not in kinds or not r["_class"].search(f["class"]):
            continue
        m = r["_pattern"].search(f["title"])
        if m:
            try:
                name = clean_text(r["name"].format(**{k: (v or "").strip() for k, v in m.groupdict().items()}))
            except (KeyError, IndexError):
                continue
            if r["kind"] == "agentHost" and f["class"].casefold() == "com.t3tools.t3code" and name.casefold() == "no project":
                return r, None, m  # T3 Code uses this title for a thread without a project.
            return r, name, m
    return None, None, None


def window_cands(f, rules, settings):
    """(primary candidate, extra candidates) for one window."""
    sources = set(settings.get("sources") or DEFAULT_SETTINGS["sources"])
    if "tabs" in f:
        return terminal_cands(f, rules, sources)
    agents = f.get("agents") or []
    app = f["app"]
    shared = f.get("shared")
    rule, project, _ = title_rule(f, rules, {"agentHost"})
    if agents and "agents" in sources and (not shared or project):
        cands = []
        for a in agents:
            pr = project_of(a.get("cwd"), a.get("git"), rules)
            if pr["kind"] in ("meaningless", "none"):
                pr = {"kind": "folder", "name": "~", "key": a.get("cwd"), "path": a.get("cwd")}
            c = proj_cand(pr, f, "agent", "strong", agent_icon(a), "agent", f"{a['label']} · in {app['name']}",
                          f"{a['label']} in {app['name']} → {collapse_home(a.get('cwd') or '?')}{git_note(pr)}", "proc")
            c["busy"] = bool(a.get("busy"))
            c["agentLabel"] = a["label"]
            c["_order"] = (a.get("lastActive") or 0, a.get("start") or 0)
            cands.append(c)
        if rule and not project:
            return (app_cand(f, "T3 Code has no project selected") if "apps" in sources else None), cands
        chosen = None
        if project:
            want = project.casefold()
            matching = [c for c in cands if c["name"].casefold() == want]
            if not matching:
                matching = [c for c in cands if (c.get("repo") or "").casefold() == want]
            roots = {c.get("projectRoot") or c["key"] for c in matching}
            if len(roots) == 1:
                match = matching[0]
                chosen = dict(match, layer="title", how=match["how"] + f" · the window title shows “{project}”")
                cands = [chosen if c is match else c for c in cands]
        if not chosen and project and "titleRules" in sources:
            # The title can be useful even without a uniquely matching agent project.
            reason = "agents match multiple project folders" if matching else "no agent matches that project"
            chosen = {"name": project, "source": "project", "strength": "strong", "key": f"title:{project.lower()}",
                      "aliasKey": None, "repo": project, "icon": {"kind": "app", "name": app["icon"]}, "glyph": "git",
                      "detail": f"on screen in {app['name']}", "address": f["address"], "layer": "title",
                      "how": f"{app['name']} title shows “{project}” ({reason})"}
            return chosen, [] if shared else cands
        if not chosen:
            if shared:
                return (app_cand(f) if "apps" in sources else None), []
            cands.sort(key=lambda c: c["_order"], reverse=True)
            chosen = cands[0]
            if len(cands) > 1:
                chosen = dict(chosen, how=chosen["how"] + (" · most recently busy" if chosen["_order"][0] else " · newest agent"))
                cands[0] = chosen
        extras = [] if shared else [c for c in cands if c is not chosen]
        return chosen, extras
    rule, name, _ = title_rule(f, rules, {"agentHost", "project", "title"})
    if name and "titleRules" in sources:
        source = "project" if rule["kind"] in ("project", "agentHost") else "title"
        return ({"name": name, "source": source, "strength": "strong", "key": f"title:{name.lower()}", "aliasKey": None,
                 "repo": name if source == "project" else None, "icon": {"kind": "app", "name": app["icon"]},
                 "glyph": "git" if source == "project" else "app", "detail": f"title rule · {app['name']}",
                 "how": f"{f['class']} title “{f['title'][:60]}” → {name}", "address": f["address"], "layer": "title"}, [])
    if app["webapp"] and "webapps" in sources:
        return ({"name": app["name"], "source": "webapp", "strength": "strong", "key": None,
                 "icon": {"kind": "app", "name": app["icon"]}, "glyph": "web", "detail": "web app",
                 "how": f"{f['class']} → {app['desktop'] or 'class'} Name={app['name']}", "address": f["address"], "layer": "none"}, [])
    return (app_cand(f) if "apps" in sources else None), []


def aliased(c, aliases):
    if c.get("aliasKey") and c["aliasKey"] in aliases:
        return aliases[c["aliasKey"]] + (f" · {c['wt']}" if c.get("wt") else "")
    return c["name"]


def project_record(candidate):
    if not candidate or candidate["source"] not in ("project", "agent", "folder"):
        return None
    directory = candidate.get("projectRoot") or ""
    if not directory and not candidate.get("repo"):
        return None
    return {"directory": directory, "originalName": clean_text(candidate["name"]),
            "aliasKey": candidate.get("aliasKey"), "wt": candidate.get("wt"),
            "glyph": "git" if candidate.get("repo") else "folder"}


def project_row(record, workspace, key, aliases, detail="", busy=False, host="", prefix=None, group=None, address=""):
    directory = record["directory"]
    name = aliased({**record, "name": record["originalName"]}, aliases)
    row_group = group or ("open" if workspace else "recent")
    row_prefix = prefix if prefix is not None else (workspace or "recent")
    # The colour follows the repo (aliasKey is the main checkout), so worktrees match their repo.
    # The letter follows the original name: renaming a project must not recolour it.
    row = {**record, "id": f"{row_prefix}:{key}", "workspace": workspace, "group": row_group, "name": clean_text(name),
           "detail": clean_text(collapse_home(directory) if directory else detail),
           "hue": hue_of(record.get("aliasKey") or directory or key), "_colorKey": record.get("aliasKey") or directory or key,
           "monogram": monogram_of(record["originalName"]), "busy": bool(busy), "host": clean_text(host)}
    if address:
        row["address"] = clean_text(address)
    return row


def workspace_projects(ws, candidates, aliases, busy_on=False, hosts=None):
    """The projects open on one workspace. `hosts` maps a window address to its app's name, so a
    project open in two places ("T3 Code" on one workspace, "kitty" on another) can say where."""
    projects = {}
    busy = {}
    apps = {}
    addrs = {}
    for candidate in candidates:
        record = project_record(candidate)
        if not record:
            continue
        key = record["directory"] or candidate["key"]
        if not key:
            continue
        busy[key] = busy.get(key, False) or (busy_on and bool(candidate.get("busy")))
        addr = candidate.get("address")
        if addr and key not in addrs:
            addrs[key] = addr
        app = (hosts or {}).get(addr)
        if app:
            apps.setdefault(key, set()).add(app)
        if key not in projects:
            projects[key] = project_row(record, ws, key, aliases, candidate["detail"])
    for key, row in projects.items():
        row["busy"] = busy[key]
        row["host"] = clean_text(" + ".join(sorted(apps.get(key, ()), key=str.casefold)))
        if key in addrs:
            row["address"] = clean_text(addrs[key])
    return list(projects.values())


def unread_count(windows, enabled, rules):
    """Sum the maximum count per selected app. Duplicated app windows may show the same inbox."""
    total = 0
    for key in dict.fromkeys(enabled):
        rule = rules["_unread"].get(key)
        if not rule:
            continue
        window_class, title = rule
        counts = []
        for window in windows:
            if not window_class.fullmatch(window["class"]):
                continue
            match = title.fullmatch(window.get("title") or "")
            count = match and match.group("count")
            if count and re.fullmatch(r"[0-9]{1,9}", count):
                counts.append(int(count))
        total += max(counts, default=0)
    return min(total, 100)


def decide(facts, state, settings, rules, active=None):
    """Pure: per-window facts + pins/aliases + settings → {workspace id: result}."""
    settings = resolve_settings(settings)
    pins = {int(k): v for k, v in (state.get("pins") or {}).items()}
    aliases = state.get("aliases") or {}
    ignored = {s.strip().lower() for s in str(settings.get("ignoreClasses") or "").split(",") if s.strip()}
    limit = int(settings.get("maxLength") or 12)
    busy_on = settings["agentBadge"]  # off when the bar shows no badge: fewer updates
    by_ws = {}
    for f in facts:
        if f.get("workspace") is None:
            continue
        by_ws.setdefault(f["workspace"], []).append(f)
    out = {}
    for ws in sorted(set(by_ws) | set(pins)):
        wins = sorted(by_ws.get(ws, []), key=lambda f: f.get("focus", 99))
        prim, every, offers, primary_of = [], [], {}, {}
        active_project = None
        for f in wins:
            if f["class"].lower() in ignored:
                continue
            p, extras = window_cands(f, rules, settings)
            if p:
                prim.append(p)
                every.append(p)
                primary_of[f["address"]] = clean_text(aliased(p, aliases))
                if active and f["address"] == active:
                    active_project = project_record(p)
            every.extend(extras)
            offers[f["address"]] = [clean_text(aliased(c, aliases)) for c in ([p] if p else []) + extras]
        projects = workspace_projects(ws, every, aliases, busy_on, {f["address"]: f["app"]["name"] for f in wins})
        auto, reason, overridden = None, "", None
        if prim:
            auto = prim[0]
            first_app = next((f["app"]["name"] for f in wins if f["address"] == auto["address"]), "?")
            reason = f"From {first_app}, the window you used last here."
            if auto["strength"] == "weak":
                strong = next((c for c in prim if c["strength"] == "strong"), None)
                if strong:
                    strong_app = next((f["app"]["name"] for f in wins if f["address"] == strong["address"]), "?")
                    reason = (f"{first_app} was used last, but it only gives “{auto['name']}”, so "
                              f"“{clean_text(aliased(strong, aliases))}” from {strong_app} names the workspace.")
                    overridden = first_app
                    auto = strong
        winner = auto
        if ws in pins:
            winner = {"name": pins[ws], "source": "pinned", "strength": "pinned", "key": None,
                      "icon": {"kind": "glyph", "key": "pin"}, "glyph": "pin", "detail": "pinned by you",
                      "how": f"pinned (auto would be “{aliased(auto, aliases)}”)" if auto else "pinned (empty)", "address": None, "layer": "none"}
            reason = "Pinned by you. Pins win until you choose Back to auto."
        keys = set()
        if winner and winner["source"] != "pinned":
            for c in every:
                if c["source"] in COUNTABLE and c.get("key") and c["key"] != winner.get("key"):
                    keys.add(c["key"])
        if not winner:
            out[ws] = {"id": ws, "name": None, "windows": len(wins), "_offers": offers, "_primary": primary_of,
                       "_projects": projects, "_activeProject": active_project}
            continue
        full = clean_text(aliased(winner, aliases))
        rows = []
        seen = set()
        for c in ([winner] if winner["source"] == "pinned" else []) + sorted(every, key=lambda c: c is not auto):
            sig = (c["name"], c.get("address"), c["source"])
            if sig in seen:
                continue
            seen.add(sig)
            rows.append({"name": clean_text(aliased(c, aliases)), "detail": c["detail"], "strength": c["strength"],
                         "icon": c["icon"], "winner": c is winner, "busy": busy_on and bool(c.get("busy"))})
        # Projects, agents and pins get a coloured letter tile. Apps, ssh hosts, models and web apps
        # keep their own logo on a neutral tile, so the colour always means "a project of yours".
        tiled = winner["source"] in ("project", "agent", "folder", "pinned")
        out[ws] = {
            "id": ws, "name": cut(full, limit), "full": full, "short": short_name(full, winner["source"]),
            "hue": hue_of(winner.get("aliasKey") or (auto or {}).get("aliasKey") or full) if tiled else None,
            "_colorKey": (winner.get("aliasKey") or (auto or {}).get("aliasKey") or full) if tiled else None,
            "monogram": monogram_of(full),
            "source": winner["source"], "strength": winner["strength"], "icon": winner["icon"], "glyph": winner["glyph"],
            "address": winner.get("address"), "key": winner.get("key"), "aliasKey": (auto or {}).get("aliasKey"),
            "aliasRepo": (auto or {}).get("repo"), "aliasWt": (auto or {}).get("wt"),
            "aliased": bool((auto or {}).get("aliasKey") in aliases),
            "autoName": clean_text(aliased(auto, aliases)) if auto else None,
            "more": len(keys), "unread": unread_count(wins, settings["unreadApps"], rules),
            "busy": busy_on and any(c.get("busy") for c in every), "pinned": ws in pins,
            "planned": ws in pins and not wins, "windows": len(wins), "reason": reason, "overridden": overridden,
            "how": winner["how"], "layer": winner.get("layer", "none"), "rows": rows,
            "summary": summary_of(winner, next((f["app"]["name"] for f in wins if f["address"] == winner.get("address")), ""), full),
            "apps": [f["app"]["icon"] for f in wins][:6], "appNames": sorted({f["app"]["name"] for f in wins}),
            "appList": app_list(wins),
            "suggestions": suggestions(auto, every, wins, state),
            "_offers": offers, "_primary": primary_of,
            "_projects": projects,
            "_activeProject": active_project,
        }
    return out


def summary_of(c, app, name):
    """One plain sentence on what a workspace's name stands for, for the top of the hover card. It never
    contradicts the name: it describes the window and source the name came from."""
    src = c["source"]
    path = collapse_home(c.get("projectRoot") or c.get("path") or "")
    app = clean_text(app) or "an app"
    if src == "pinned":
        return "A name you pinned."
    if src == "agent":
        return f"{c.get('agentLabel') or 'An agent'} in {app}, in {path}." if path else f"{c.get('agentLabel') or 'An agent'} in {app}."
    if src in ("project", "folder") and path:
        what = c.get("what") or "shell"
        lead = f"A {app} shell" if what == "shell" else f"{what} in {app},"
        return f"{lead} in {path}" + (", a folder that isn't a git repo." if src == "folder" else ".")
    if src == "project":
        return f"{app} shows this project in its window title."
    if src == "ssh":
        return f"An ssh session to {name.split(' · ', 1)[-1]}, in {app}."
    if src == "model":
        label, _, model = name.partition(" · ")
        return f"{label} running {model or 'a model'}, in {app}."
    if src == "webapp":
        return f"{name}, a web app."
    if src == "title":
        return f"From {app}'s window title."
    return f"{app}, with no project open in it."


def app_list(wins, limit=8):
    """What is open on a workspace, for the hover card: each app once, with its icon and how many
    windows it has there, most recently used first."""
    out, at = [], {}
    for f in wins:
        name = clean_text(f["app"]["name"])
        if name in at:
            out[at[name]]["count"] += 1
        elif len(out) < limit:
            at[name] = len(out)
            out.append({"name": name, "icon": f["app"]["icon"], "count": 1})
    return out


def suggestions(auto, every, wins, state):
    out = []

    def add(text, why):
        if text and all(t != text for t, _ in out):
            out.append((text, why))
    if auto:
        add(clean_text(aliased(auto, state.get("aliases") or {})), "auto")
    for c in every:
        if c["source"] in ("agent", "project", "folder"):
            add(c.get("repo") or c["name"], "folder" if c["source"] == "folder" else "project")
    for f in wins:
        add(f["app"]["name"], "app")
    for r in (state.get("recent") or [])[:4]:
        add(r, "used before")
    return [{"text": t, "why": w} for t, w in out[:8]]


# --------------------------------------------------------------------------- hold filter (pure, clock injected)

class Holder:
    """A name changes at once when what it names changes, and waits only when you merely looked elsewhere.

    At once: a workspace's first name; anything replacing a weak name ("kitty"); the window behind the
    name closing, moving away, or no longer offering that name (you cd'd elsewhere, quit the agent);
    the window you're using naming itself differently (you switched T3 thread).
    Waits `renameDelay` seconds: another window winning only because you focused it, or an agent elsewhere got
    busier. If it goes back before then, nothing changes. Clock injected, so this is pure and tested."""

    def __init__(self):
        self.shown = {}
        self.pending = {}

    @staticmethod
    def content_changed(cur, new, active):
        addr = cur.get("address")
        if not addr:
            return True
        offers = (new.get("_offers") or {}).get(addr)
        if not offers or cur.get("full") not in offers:
            return True
        return addr == active and (new.get("_primary") or {}).get(addr) != cur.get("full")

    def update(self, results, now, settings, active=None, force=False):
        dwell = float(settings.get("renameDelay", DEFAULT_SETTINGS["renameDelay"]))
        events = []
        for ws in sorted(set(results) | set(self.shown)):
            new = results.get(ws) or {"id": ws, "name": None}
            cur = self.shown.get(ws)
            new_name, cur_name = new.get("name"), (cur or {}).get("name")
            if (force or not cur_name or not new_name or new_name == cur_name or dwell <= 0
                    or cur.get("strength") == "weak" or self.content_changed(cur, new, active)):
                if ws in self.pending and new_name == cur_name and not force:
                    events.append(f"ws {ws} back to “{cur_name}” before the hold ended")
                self.pending.pop(ws, None)
                if new_name or results.get(ws):
                    self.shown[ws] = new
                else:
                    self.shown.pop(ws, None)
                continue
            p = self.pending.get(ws)
            if not p or p["result"].get("name") != new_name:
                self.pending[ws] = {"result": new, "due": now + dwell, "dueWall": time.time() + dwell}
                events.append(f"ws {ws} “{cur_name}” → “{new_name}” in {dwell:g} s (focus change)")
            else:
                p["result"] = new
            # keep the old name; let the rest (+N, rows, offers) stay current
            keep = ("name", "full", "short", "source", "strength", "icon", "glyph", "how", "address", "layer", "key",
                    "hue", "monogram", "slot")
            self.shown[ws] = {**new, **{k: cur.get(k) for k in keep}}
        return events

    def tick(self, now):
        changed = []
        for ws, p in list(self.pending.items()):
            if now >= p["due"]:
                self.shown[ws] = p["result"]
                del self.pending[ws]
                changed.append(ws)
        return changed

    def next_due(self):
        return min((p["due"] for p in self.pending.values()), default=None)

    def view(self):
        out = []
        for ws in sorted(self.shown):
            r = {k: v for k, v in self.shown[ws].items() if not k.startswith("_")}
            p = self.pending.get(ws)
            if p:  # an absolute time, so the output doesn't change every second while it waits
                r["pending"] = {"name": p["result"].get("name"), "due": int(p["dueWall"] * 1000)}
            out.append(r)
        return out


# --------------------------------------------------------------------------- state (pins, aliases)

def private_dir(path):
    """A folder only you can read: Signpost's state names your projects and quotes your agents.
    An existing folder that others can read is tightened."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        st = os.stat(path)
        if st.st_uid == os.getuid() and statmod.S_IMODE(st.st_mode) & 0o077:
            os.chmod(path, 0o700)
    except OSError:
        pass


def write_private(path, text):
    """Replace `path` with `text` in one step, readable by you alone (0600, in a 0700 folder)."""
    private_dir(os.path.dirname(path))
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


COLOR_LIMIT = 200    # projects whose colour is remembered; the oldest are forgotten first


def clean_colors(raw):
    """The remembered colour slots from a state file: {project key: slot ≥ 0}, anything else dropped."""
    out = {}
    if isinstance(raw, dict):
        for key, slot in raw.items():
            if isinstance(key, str) and key and isinstance(slot, int) and not isinstance(slot, bool) and slot >= 0:
                out[key] = slot
    while len(out) > COLOR_LIMIT:
        del out[next(iter(out))]
    return out


def load_state(path=STATE_FILE):
    try:
        with open(path) as f:
            s = json.load(f)
        if not isinstance(s, dict):
            raise ValueError("not an object")
        colors = clean_colors(s.get("colors"))
        next_slot = s.get("nextSlot")
        if not isinstance(next_slot, int) or isinstance(next_slot, bool) or next_slot < 0:
            next_slot = 0
        next_slot = max([next_slot] + [slot + 1 for slot in colors.values()])
        return {"pins": s.get("pins") or {}, "aliases": s.get("aliases") or {}, "recent": s.get("recent") or [],
                "colors": colors, "nextSlot": next_slot}
    except (OSError, ValueError):
        return {"pins": {}, "aliases": {}, "recent": [], "colors": {}, "nextSlot": 0}


def save_state(state, path=STATE_FILE):
    write_private(path, json.dumps({"pins": state.get("pins", {}), "aliases": state.get("aliases", {}),
                                    "recent": (state.get("recent") or [])[:8],
                                    "colors": clean_colors(state.get("colors")),
                                    "nextSlot": int(state.get("nextSlot") or 0)},
                                   indent=2, ensure_ascii=False) + "\n")


def assign_slots(state, keys):
    """Give each project key not seen before the next colour slot, in order, so the first projects you
    open get different colours and keep them. Returns True when something was added. A key already
    known keeps its slot, so pins and aliases (which don't change the key) never recolour."""
    colors = state.setdefault("colors", {})
    added = False
    for key in keys:
        if not key or key in colors:
            continue
        slot = int(state.get("nextSlot") or 0)
        colors[key] = slot
        state["nextSlot"] = slot + 1
        added = True
    while len(colors) > COLOR_LIMIT:
        del colors[next(iter(colors))]
    return added


class ProjectHistory:
    """A bounded list of local project roots, shared by the bars on all monitors."""

    LIMIT = 30

    def __init__(self, path):
        self.path = Path(path)
        self.stamp = None
        self.entries = []
        self.refresh()

    @staticmethod
    def entry(row):
        if not isinstance(row, dict):
            return None
        directory = row.get("directory")
        name = clean_text(row.get("originalName"))
        if not isinstance(directory, str) or not os.path.isabs(directory) or "\x00" in directory or not name:
            return None
        return {"directory": directory, "originalName": name,
                "aliasKey": row["aliasKey"] if isinstance(row.get("aliasKey"), str) else None,
                "wt": clean_text(row.get("wt")) or None,
                "glyph": "git" if row.get("glyph") == "git" else "folder"}

    def refresh(self):
        try:
            st = self.path.stat()
            stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            self.stamp, self.entries = None, []
            return
        except OSError:
            return
        if stamp == self.stamp:
            return
        try:
            with self.path.open(encoding="utf-8") as f:
                rows = json.load(f)
        except (OSError, ValueError):
            return
        entries = {}
        for row in rows if isinstance(rows, list) else []:
            entry = self.entry(row)
            if entry:
                entries.setdefault(entry["directory"], entry)
                if len(entries) == self.LIMIT:
                    break
        self.stamp, self.entries = stamp, list(entries.values())

    def _write_locked(self, entries):
        fd, temp_path = tempfile.mkstemp(dir=self.path.parent, prefix=".recent-projects-")
        draft = Path(temp_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
                f.write("\n")
            draft.replace(self.path)
        finally:
            draft.unlink(missing_ok=True)

    def touch(self, row):
        entry = self.entry(row)
        if not entry:
            return
        self.refresh()
        if self.entries[:1] == [entry]:
            return
        try:
            private_dir(str(self.path.parent))
            with self.path.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self.refresh()
                rest = [other for other in self.entries if other["directory"] != entry["directory"]]
                entries = [entry, *rest[:self.LIMIT - 1]]
                if entries == self.entries:
                    return
                self._write_locked(entries)
                self.refresh()
        except OSError as exc:
            warn(f"could not save recent projects: {exc}")

    def remember_open(self, rows, front=None):
        front_entry = self.entry(front) if front else None
        extras = []
        seen_dirs = {front_entry["directory"]} if front_entry else set()
        for row in rows or ():
            e = self.entry(row)
            if e and e["directory"] not in seen_dirs:
                seen_dirs.add(e["directory"])
                extras.append(e)
        if not front_entry and not extras:
            return
        self.refresh()
        existing_by_dir = {e["directory"]: e for e in self.entries}
        if (not front_entry or self.entries[:1] == [front_entry]) and all(existing_by_dir.get(e["directory"]) == e for e in extras):
            return
        try:
            private_dir(str(self.path.parent))
            with self.path.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self.refresh()
                by_dir = {e["directory"]: e for e in extras}
                new_extras = [e for e in extras if e["directory"] not in {x["directory"] for x in self.entries}]
                kept = [by_dir.get(x["directory"], x) for x in self.entries
                        if not front_entry or x["directory"] != front_entry["directory"]]
                if front_entry:
                    entries = [front_entry, *new_extras, *kept][:self.LIMIT]
                elif kept and new_extras:
                    entries = [kept[0], *new_extras, *kept[1:]][:self.LIMIT]
                else:
                    entries = [*new_extras, *kept][:self.LIMIT]
                if entries == self.entries:
                    return
                self._write_locked(entries)
                self.refresh()
        except OSError as exc:
            warn(f"could not save recent projects: {exc}")


class ProjectIndex:
    """Optional index of unopened project folders from projectRoots and zoxide.

    Built lazily when the switcher opens and cached by root mtimes + zoxide TTL.
    Never runs git; marks git repos by `.git` presence only."""

    MAX_ROOT_ENTRIES = 300
    MAX_ZOXIDE_ENTRIES = 100
    ZOXIDE_TTL = 30.0

    def __init__(self, which=None, run=None):
        self._which = which or shutil.which
        self._run = run or subprocess.run
        self.entries = []
        self._stamp = None
        self._zoxide_at = None
        self._zoxide_entries = []

    def invalidate(self):
        self._stamp = None
        self._zoxide_at = None

    def _root_stamps(self, roots):
        stamps = []
        for root in roots:
            try:
                st = os.stat(root)
                stamps.append((root, st.st_ino, st.st_mtime_ns))
            except OSError:
                stamps.append((root, None, None))
        return tuple(stamps)

    def _scan_zoxide(self, ignore_patterns, home_real):
        binary = self._which("zoxide")
        if not binary:
            return []
        try:
            proc = self._run([binary, "query", "--list"], capture_output=True, text=True, timeout=2)
            if proc.returncode != 0:
                return []
        except (OSError, subprocess.SubprocessError):
            return []
        out = []
        seen = set()
        for raw in proc.stdout.splitlines():
            path = raw.strip()
            if not path or not os.path.isabs(path) or "\x00" in path or not os.path.isdir(path):
                continue
            real = os.path.realpath(path)
            if real in ("/", home_real) or real in seen:
                continue
            if ignored_folder(path, ignore_patterns) or ignored_folder(real, ignore_patterns):
                continue
            name = clean_text(os.path.basename(path.rstrip("/")))
            if not name:
                continue
            seen.add(real)
            is_git = os.path.exists(os.path.join(path, ".git"))
            out.append({"directory": path, "_realPath": real, "originalName": name,
                        "aliasKey": real if is_git else None, "wt": None,
                        "glyph": "git" if is_git else "folder"})
            if len(out) >= self.MAX_ZOXIDE_ENTRIES:
                break
        return out

    def refresh(self, roots, ignore_patterns=None, now=None):
        patterns = tuple(ignore_patterns or ())
        if not roots:
            self.entries = []
            self._stamp = ((), patterns)
            return self.entries
        expanded = []
        for r in roots:
            p = expand_home(r)
            if p and os.path.isabs(p) and "\x00" not in p:
                expanded.append(p)
        if not expanded:
            self.entries = []
            self._stamp = ((), patterns)
            return self.entries
        now = time.monotonic() if now is None else now
        stamp = (self._root_stamps(expanded), patterns)
        zoxide_fresh = self._zoxide_at is not None and (now - self._zoxide_at) < self.ZOXIDE_TTL
        if stamp == self._stamp and zoxide_fresh:
            return self.entries

        home_real = os.path.realpath(HOME)
        seen = set()
        entries = []
        for root_dir in expanded:
            if not os.path.isdir(root_dir):
                continue
            try:
                names = sorted(os.listdir(root_dir), key=str.casefold)
            except OSError:
                continue
            for name in names:
                if name.startswith("."):
                    continue
                full = os.path.join(root_dir, name)
                if not os.path.isdir(full):
                    continue
                real = os.path.realpath(full)
                if real in seen:
                    continue
                if ignored_folder(full, patterns) or ignored_folder(real, patterns):
                    continue
                clean = clean_text(name)
                if not clean:
                    continue
                seen.add(real)
                is_git = os.path.exists(os.path.join(full, ".git"))
                entries.append({"directory": full, "_realPath": real, "originalName": clean,
                                "aliasKey": real if is_git else None, "wt": None,
                                "glyph": "git" if is_git else "folder"})
                if len(entries) >= self.MAX_ROOT_ENTRIES:
                    break
            if len(entries) >= self.MAX_ROOT_ENTRIES:
                break

        if not zoxide_fresh or stamp != self._stamp:
            self._zoxide_entries = self._scan_zoxide(patterns, home_real)
            self._zoxide_at = now
        for z in self._zoxide_entries:
            if z["_realPath"] not in seen:
                seen.add(z["_realPath"])
                entries.append(z)

        self.entries = entries
        self._stamp = stamp
        return self.entries


def remember(state, name):
    state["recent"] = [name] + [r for r in state.get("recent", []) if r != name]


def apply_command(state, cmd, results):
    """pin / unpin / alias / unalias against the current results. Returns a message."""
    op, ws = cmd.get("cmd"), cmd.get("ws")
    name = clean_text(cmd.get("name", ""))[:64]
    if op == "pin":
        if not name or ws is None:
            return "pin: need a name and a workspace"
        state["pins"][str(ws)] = name
        remember(state, name)
        return f"pinned ws {ws} → {name}"
    if op == "unpin":
        state["pins"].pop(str(ws), None)
        return f"ws {ws} back to auto"
    if op in ("alias", "unalias"):
        r = results.get(ws) or {}
        key = r.get("aliasKey")
        if not key:
            return f"{op}: no project on ws {ws}"
        if op == "unalias":
            state["aliases"].pop(key, None)
            return f"{r.get('aliasRepo') or key}: alias removed"
        if not name:
            return "alias: need a name"
        wt = r.get("aliasWt")
        if wt and name.endswith(" · " + wt):
            name = name[: -len(" · " + wt)]
        state["aliases"][key] = name
        remember(state, name)
        return f"{r.get('aliasRepo') or key} → {name} (everywhere)"
    return f"unknown command {op!r}"


# --------------------------------------------------------------------------- hyprland

def hypr_dir():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    base = os.path.join(RUNTIME_DIR, "hypr")
    if not sig:
        try:
            cands = sorted(os.listdir(base), key=lambda d: os.path.getmtime(os.path.join(base, d)), reverse=True)
        except OSError:
            cands = []
        sig = cands[0] if cands else None
    return os.path.join(base, sig) if sig else None


def hypr_request(cmd):
    d = hypr_dir()
    if d:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(os.path.join(d, ".socket.sock"))
                s.sendall(cmd.encode())
                chunks = []
                while True:
                    b = s.recv(65536)
                    if not b:
                        break
                    chunks.append(b)
            return json.loads(b"".join(chunks).decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass
    try:
        r = subprocess.run(["hyprctl", "-j", cmd.split("/", 1)[-1]], capture_output=True, text=True, timeout=2)
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def focused_workspace():
    w = hypr_request("j/activeworkspace") or {}
    return w.get("id")


def active_window():
    w = hypr_request("j/activewindow") or {}
    return w.get("address")


# --------------------------------------------------------------------------- agent hooks
#
# Claude Code can tell Signpost what it is doing. `signpost.py hook` runs as a Claude Code hook,
# reads the event on stdin and writes one small file per session. The engine reads those files and
# lays them over the projects, which is how "needs you" can be told apart from "finished": CPU use
# can't do that.

AGENT_DIR = STATE_HOME + "/signpost/agents"
AGENT_TTL = {"work": 600, "wait": 4 * 3600, "done": 2 * 3600}   # seconds without news before a state is dropped
AGENT_RANK = {"": 0, "done": 1, "work": 2, "wait": 3}
HOOK_MARK = "signpost.py"                                          # how our entries are recognised in settings.json
# Checked against https://code.claude.com/docs/en/hooks for Claude Code 2.1.318 (November 2026).
# PermissionRequest and Elicitation fire the moment Claude asks; the Notification types for the same
# prompts only fire after about six seconds without typing, so they only fill in the question's text.
HOOK_EVENTS = ("UserPromptSubmit", "PermissionRequest", "PostToolUse", "PostToolUseFailure", "Notification",
               "Elicitation", "ElicitationResult", "Stop", "StopFailure", "SessionEnd")
HOOK_EVENTS_V1 = ("UserPromptSubmit", "PostToolUse", "Notification", "Stop", "SessionEnd")   # Signpost 0.3.0
CODEX_EVENTS = {"UserPromptSubmit": "work", "PostToolUse": "work", "PermissionRequest": "wait",
                "Stop": "done", "Interrupt": "done", "SessionEnd": "end"}
OPENCODE_EVENTS = {"permission.asked": "wait", "permission.replied": "work",
                   "session.execution.started": "work", "session.execution.succeeded": "done",
                   "session.execution.failed": "done", "session.execution.interrupted": "done",
                   "session.deleted": "end"}
HOOK_AGENTS = ("claude", "codex", "opencode")
WAIT_NOTIFICATIONS = ("permission_prompt", "elicitation_dialog", "elicitation_url_dialog")
WORK_NOTIFICATIONS = ("elicitation_complete", "elicitation_response")


def hook_state(payload, agent="claude"):
    """The state a Claude Code event puts a session in: work · wait · done · end · None (ignore).
    Anything not listed (auth_success, quota notices, a type added later) is ignored: it says nothing
    about whether the session needs you."""
    event = payload.get("hook_event_name")
    if not isinstance(event, str):
        return None
    if agent != "claude":
        return {"codex": CODEX_EVENTS, "opencode": OPENCODE_EVENTS}.get(agent, {}).get(event)
    if event in ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "ElicitationResult"):
        return "work"    # a tool ran, so any permission prompt before it was answered
    if event in ("PermissionRequest", "Elicitation"):
        return "wait"
    if event in ("Stop", "StopFailure"):
        return "done"
    if event == "SessionEnd":
        return "end"
    if event == "Notification":
        kind = str(payload.get("notification_type") or "")
        if kind in WAIT_NOTIFICATIONS or (not kind and "permission" in str(payload.get("message") or "").lower()):
            return "wait"
        if kind in WORK_NOTIFICATIONS:
            return "work"
        if kind == "idle_prompt":
            return "done"    # it finished a minute ago and nobody has typed since
    return None


def hook_message(payload):
    """What the agent is asking, in one line: the notification's own words, or the tool it wants."""
    event = payload.get("hook_event_name")
    if event == "PermissionRequest":
        tool = clean_text(payload.get("tool_name"))
        if tool.startswith("mcp__"):
            tool = " ".join(p for p in tool[5:].split("__") if p)
        args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        detail = next((clean_text(args.get(k)) for k in ("description", "command", "url", "pattern", "query")
                       if isinstance(args.get(k), str) and args.get(k).strip()), "")
        if not detail and isinstance(args.get("file_path"), str):
            detail = os.path.basename(args["file_path"])
        return f"Allow {tool or 'a tool'}: {detail}" if detail else f"Allow {tool or 'a tool'}?"
    if event == "Elicitation":
        return clean_text(payload.get("message")) or "An MCP server asks for your input"
    return clean_text(payload.get("message"))


def agent_session_file(session, directory=AGENT_DIR, agent="claude"):
    clean = re.sub(r"[^A-Za-z0-9_-]", "", str(session))[:64]
    if not clean or clean != str(session):
        clean = hashlib.sha1(str(session).encode("utf-8", "replace")).hexdigest()[:20]
    folder = directory if agent == "claude" else os.path.join(directory, agent)
    return os.path.join(folder, clean + ".json")


def agent_pid(start=None, agent="claude"):
    """The agent process that ran this hook, if it can be told apart from its shells."""
    pid = start if start is not None else os.getppid()
    names = {"claude": ("claude", "claude-code"), "codex": ("codex", "codex-cli"),
             "opencode": ("opencode",)}[agent]
    for _ in range(6):
        if pid <= 1:
            return None
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
            with open(f"/proc/{pid}/stat") as f:
                nxt = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        if any(os.path.basename(a) in names or (agent == "codex" and os.path.basename(a).startswith("codex-")
               and os.path.basename(a).endswith("-linux-gnu")) for a in argv[:3]):
            return pid
        pid = nxt
    return None


def write_hook(payload, directory=AGENT_DIR, now=None, pid=None, agent="claude"):
    """Apply one agent event to the session files. Returns the state it wrote, or None."""
    state = hook_state(payload, agent)
    session = payload.get("session_id")
    if state is None or not isinstance(session, str) or not session:
        return None
    path = agent_session_file(session, directory, agent)
    if agent != "claude" and state != "end":
        private_dir(directory)
    if state == "end":
        try:
            os.unlink(path)
        except OSError:
            pass
        return "end"
    old = None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            old = data
    except (OSError, ValueError):
        pass
    raw_cwd = payload.get("cwd")
    cwd = raw_cwd if isinstance(raw_cwd, str) and raw_cwd else (old.get("cwd") if old and isinstance(old.get("cwd"), str) else "")
    ts = round(now if now is not None else time.time(), 3)
    message = hook_message(payload)[:160] if state == "wait" else ""
    if state == "wait" and old and old.get("state") == "wait" and isinstance(old.get("ts"), (int, float)):
        # The same question again (PermissionRequest at once, its Notification six seconds later) is not a
        # new one: keep when it was first asked, so it is announced once, and the first words given.
        ts = old["ts"]
        if not payload.get("replace_message"):
            message = clean_text(old.get("message"))[:160] or message
    record = {"session": session[:64], "agent": agent, "state": state, "cwd": cwd,
              "ts": ts, "pid": pid, "message": message}
    write_private(path, json.dumps(record))
    return state


def hook_main(args):
    """Record status without making a decision for the agent."""
    agent = args[1] if len(args) == 2 and args[0] == "--agent" else "claude" if not args else None
    if agent not in HOOK_AGENTS:
        return 0
    try:
        payload = json.loads(sys.stdin.read(2 ** 20))
    except (OSError, ValueError):
        return 0
    if isinstance(payload, dict):
        # Stop expects a JSON result. An empty object leaves Codex's decision unchanged.
        if agent == "codex" and payload.get("hook_event_name") == "Stop":
            print("{}")
        try:
            write_hook(payload, pid=agent_pid(agent=agent), agent=agent)
        except OSError:
            pass
    return 0


class AgentSignals:
    """The session files, read back. Re-read when the folder changes and every few seconds
    (so a dead session or an old state is noticed without anything being written)."""

    def __init__(self, directory=AGENT_DIR):
        self.directory = directory
        self.stamp = None
        self.cache = []

    def read(self, now=None):
        now = time.time() if now is None else now
        folders = [(agent, self.directory if agent == "claude" else os.path.join(self.directory, agent))
                   for agent in HOOK_AGENTS]
        stamps = []
        for _, folder in folders:
            try:
                st = os.stat(folder)
            except OSError:
                stamps.append(None)
                continue
            if st.st_uid == os.getuid() and statmod.S_IMODE(st.st_mode) & 0o077:
                private_dir(folder)
            stamps.append(st.st_mtime_ns)
        stamp = (tuple(stamps), int(now // 5))
        if stamp == self.stamp:
            return self.cache
        self.stamp = stamp
        sessions = []
        for (agent, folder), present in zip(folders, stamps):
            if present is not None:
                sessions.extend(self.read_folder(folder, agent, now))
        self.cache = sorted(sessions, key=session_key)
        return self.cache

    @staticmethod
    def read_folder(folder, agent, now):
        try:
            names = os.listdir(folder)
        except OSError:
            return
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as f:
                    r = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(r, dict) or r.get("agent", "claude") != agent:
                continue
            state, ts, pid = r.get("state"), r.get("ts"), r.get("pid")
            if not isinstance(state, str) or state not in AGENT_TTL or not isinstance(ts, (int, float)) or now - ts > AGENT_TTL[state]:
                continue
            pid = pid if type(pid) is int and pid > 0 else None
            if pid and not os.path.isdir(f"/proc/{pid}"):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            yield {"session": str(r.get("session") or name[:-5]), "agent": agent, "state": state, "ts": float(ts),
                   "cwd": r.get("cwd") if isinstance(r.get("cwd"), str) else "", "pid": pid,
                   "message": clean_text(r.get("message")), "source": "hook",
                   "exact": agent != "claude"}


def session_key(session):
    agent = session.get("agent", "claude")
    return session["session"] if agent == "claude" else f"{agent}:{session['session']}"


# --- Claude Code's own list of sessions: `claude agents --json`
#
# Documented at https://code.claude.com/docs/en/agent-view as "the supported way to read session state
# from outside Claude Code". It needs no hook. Each live session has `status` busy · waiting · idle and,
# while waiting, `waitingFor`. It lists the sessions of one config folder (CLAUDE_CONFIG_DIR), so it
# runs once per folder that a running Claude Code uses. It costs about 0.13 s of CPU (measured on
# Claude Code 2.1.318), so it runs when a wait starts or ends (or a session comes or goes) in that
# folder's session files, and otherwise only every SAFETY seconds; all bars share one result through a
# file in $XDG_RUNTIME_DIR.

VIEW_WAITING = {"permission prompt": "Waiting for your permission", "input needed": "Has a question for you",
                "sandbox request": "Wants to reach the network", "worker request": "Waiting for you",
                "dialog open": "Waiting for you"}
VIEW_FILE = os.path.join(RUNTIME_DIR, "signpost", "agents-view.json")


def claude_config_dir(pid, home=HOME):
    """The config folder a running Claude Code uses: its CLAUDE_CONFIG_DIR, or ~/.claude."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            env = f.read(256 * 1024).split(b"\0")
    except OSError:
        return None
    for item in env:
        if item.startswith(b"CLAUDE_CONFIG_DIR="):
            value = os.fsdecode(item[18:])
            if os.path.isabs(value):
                return os.path.normpath(value)
    return home + "/.claude"


def parse_agents_view(text):
    """`claude agents --json` → [{session, pid, cwd, status, waitingFor, name, startedAt}], or None when the
    output isn't the documented list (a changed schema, an error message, nothing)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        pid, status = item.get("pid"), item.get("status")
        if not isinstance(pid, int) or isinstance(pid, bool) or status not in ("busy", "waiting", "idle"):
            continue                          # not alive, or a status this version doesn't know
        session = item.get("sessionId") or item.get("id") or f"pid-{pid}"
        started = item.get("startedAt")
        out.append({"session": clean_text(session)[:64], "pid": pid,
                    "cwd": item.get("cwd") if isinstance(item.get("cwd"), str) else "", "status": status,
                    "waitingFor": clean_text(item.get("waitingFor"))[:40] if status == "waiting" else "",
                    "name": clean_text(item.get("name"))[:80],
                    "startedAt": started / 1000 if isinstance(started, (int, float)) and started > 0 else None})
    return out


def sessions_stamp(config_dir):
    """What changed in a config folder's session files, cheaply. Claude Code rewrites a session's file
    whenever anything about it changes, often while it works; what matters here is whether it waits.
    So the stamp is, per file, waiting (and for what) or not, when the file says so (read as a hint
    only: the list itself always comes from `claude agents --json`), else the file's time and size.
    A session starting or ending changes it too. None when the folder isn't there (then Signpost asks
    on a timer)."""
    folder = os.path.join(config_dir, "sessions")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError:
        return None
    stamp = []
    for name in names[:200]:
        path = os.path.join(folder, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        status = None
        if st.st_size <= 64 * 1024:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("status"), str):
                    # busy and idle flip on every turn and matter little here (working comes from
                    # the hook or CPU): only a wait starting or ending is worth asking at once.
                    waiting = data["status"] == "waiting"
                    status = ["waiting" if waiting else "active", str(data.get("waitingFor") or "") if waiting else ""]
            except (OSError, ValueError):
                pass
        stamp.append([name, *status] if status else [name, st.st_mtime_ns, st.st_size])
    return stamp


class AgentsView:
    SAFETY = 120.0     # even with no wait starting or ending, look again this often
    BLIND = 5.0        # how often with no session files to watch
    MIN_GAP = 1.0      # never twice in one second
    RETRY = 60.0       # after the command failed or is missing
    TIMEOUT = 3.0

    def __init__(self, path=VIEW_FILE, run=subprocess.run, which=shutil.which, environ=None):
        self.path = path
        self.run = run
        self.which = which
        self.environ = os.environ if environ is None else environ
        self.dirs_of = {}          # pid → config folder
        self.data = {"dirs": {}}   # what the shared file says
        self.file_stamp = None
        self.pool = None
        self.busy = False
        self.lock = threading.Lock()
        self.dirty = False
        self.active = []           # config folders in use right now
        self.command = None

    def available(self):
        if self.command is None:
            self.command = self.which("claude") or ""
        return bool(self.command)

    # ---- the shared file
    def load(self):
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            self.file_stamp, self.data = None, {"dirs": {}}
            return self.data
        if stamp != self.file_stamp:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(data.get("dirs"), dict):
                    self.data = data
                    self.file_stamp = stamp
            except (OSError, ValueError):
                pass
        return self.data

    def due(self, entry, stamp, now):
        if not entry:
            return True
        age = now - float(entry.get("fetchedAt") or 0)
        if age < self.MIN_GAP:
            return False
        if not entry.get("ok"):
            return age >= self.RETRY
        if stamp is None:
            return age >= self.BLIND
        return stamp != entry.get("stamp") or age >= self.SAFETY

    # ---- called by the engine every second or so; never blocks
    def poll(self, pids, now=None):
        now = time.time() if now is None else now
        for pid in list(self.dirs_of):
            if pid not in pids:
                del self.dirs_of[pid]
        for pid in pids:
            if pid not in self.dirs_of:
                self.dirs_of[pid] = claude_config_dir(pid)
        self.active = sorted({d for d in self.dirs_of.values() if d})
        if not self.active or not self.available():
            return
        data = self.load()
        todo = [d for d in self.active if self.due(data["dirs"].get(d), sessions_stamp(d), now)]
        if not todo:
            return
        with self.lock:
            if self.busy:
                return
            self.busy = True
        if self.pool is None:
            self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="signpost-agents")
        self.pool.submit(self.refresh, todo)

    def refresh(self, todo):
        try:
            private_dir(os.path.dirname(self.path))
            with open(self.path + ".lock", "a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return            # another bar is looking right now; its result lands in the file
                self.file_stamp = None
                data = self.load()
                changed = False
                for d in todo:
                    now = time.time()
                    stamp = sessions_stamp(d)
                    if self.due(data["dirs"].get(d), stamp, now):
                        data["dirs"][d] = self.fetch(d, stamp, data["dirs"].get(d), now)
                        changed = True
                if changed:
                    for d in [d for d in data["dirs"] if d not in self.active]:
                        del data["dirs"][d]
                    write_private(self.path, json.dumps(data, ensure_ascii=False))
                    self.file_stamp = None
                    self.dirty = True
        except OSError:
            pass
        finally:
            with self.lock:
                self.busy = False

    def fetch(self, config_dir, stamp, old, now):
        env = {k: v for k, v in self.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
        if os.path.normpath(config_dir) != os.path.normpath(HOME + "/.claude"):
            env["CLAUDE_CONFIG_DIR"] = config_dir
        entry = {"fetchedAt": now, "stamp": stamp, "ok": False, "sessions": [],
                 "firstAt": (old or {}).get("firstAt") or now}
        try:
            r = self.run([self.command, "agents", "--json"], capture_output=True, text=True, timeout=self.TIMEOUT,
                         env=env, stdin=subprocess.DEVNULL, cwd=HOME)
            sessions = parse_agents_view(r.stdout) if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            sessions = None
        if sessions is None:
            return entry
        # When each session started waiting: kept from the last look while it still waits, so every bar
        # gives the same wait the same time (and it is announced once).
        since = {s["session"]: s.get("waitingSince") for s in (old or {}).get("sessions", [])
                 if s.get("status") == "waiting" and s.get("waitingSince")}
        for s in sessions:
            if s["status"] == "waiting":
                s["waitingSince"] = since.get(s["session"]) or round(now, 3)
        entry.update(ok=True, sessions=sessions)
        return entry

    def sessions(self):
        """Every live session of the folders in use, each with when that folder was first looked at
        (a wait older than that was already there: not news)."""
        data = self.load()
        out = []
        for d in self.active:
            entry = data["dirs"].get(d) or {}
            for s in entry.get("sessions") or []:
                if isinstance(s, dict) and s.get("session"):
                    out.append({**s, "firstAt": float(entry.get("firstAt") or 0),
                                "fetchedAt": float(entry.get("fetchedAt") or 0)})
        return out

    def state(self):
        """"" (no Claude Code running), "ok", "unavailable" (no claude command) or "failing"."""
        if not self.active:
            return ""
        if not self.available():
            return "unavailable"
        entries = [self.load()["dirs"].get(d) for d in self.active]
        if any(e and e.get("ok") for e in entries):
            return "ok"
        return "failing" if any(entries) else ""

    def take_dirty(self):
        dirty, self.dirty = self.dirty, False
        return dirty


def merge_sessions(hooked, viewed):
    """One list of sessions from the hook files (instant, needs the hook) and Claude Code's own list
    (no hook needed, a few seconds behind). Rules, per session (matched by session id, else by pid):
      * waiting in Claude Code's list: needs you, exactly (it clears itself when the prompt goes away,
        so the "seen" rule never applies). Since: the hook's time when the hook said so too.
      * idle in the list: finished, unless the hook has said something newer.
      * busy in the list: no claim. It can stay "busy" long after a session stopped (seen in T3 Code:
        1.5 hours), so working comes from the hook or from CPU use.
      * not in the list: the hook's word stands."""
    claude = [s for s in hooked if s.get("agent", "claude") == "claude"]
    by_id = {s["session"]: s for s in claude}
    by_pid = {s["pid"]: s for s in claude if s.get("pid")}
    out, used = [], set()
    for v in viewed:
        h = by_id.get(v["session"]) or by_pid.get(v["pid"])
        if h:
            used.add(h["session"])
        base = {"session": (h or v)["session"], "agent": "claude", "cwd": (h or {}).get("cwd") or v.get("cwd") or "", "pid": v["pid"],
                "name": v.get("name") or "", "message": "", "ts": 0.0, "state": "", "source": "view"}
        if v["status"] == "waiting":
            ts = h["ts"] if h and h["state"] == "wait" else float(v.get("waitingSince") or 0)
            message = (h["message"] if h and h["state"] == "wait" else "") or VIEW_WAITING.get(v.get("waitingFor"), "Waiting for you")
            out.append({**base, "state": "wait", "ts": ts, "message": message, "exact": True,
                        "old": not (h and h["state"] == "wait") and ts <= float(v.get("firstAt") or 0) + 0.001})
        elif h and (v["status"] == "busy" or h["ts"] > float(v.get("fetchedAt") or 0)):
            out.append({**h, "name": v.get("name") or "", "pid": h.get("pid") or v["pid"], "source": "hook"})
        elif v["status"] == "idle":
            out.append({**base, "state": "done", "ts": h["ts"] if h else float(v.get("startedAt") or 0)})
    out.extend(h for h in hooked if session_key(h) not in used)
    return sorted(out, key=session_key)


ACK_SECONDS = 3    # in the agent's window this long after it asked, and the question counts as seen


class WaitAcks:
    """A question you have seen stops calling for you.

    Claude Code sends no event when you press Esc on a permission prompt, so its wait known only from the
    hook could stay up for hours. Rule: once the window you are using has been on that very session for
    ACK_SECONDS after it asked, the wait counts as seen and shows as finished ("ready"). The session is
    matched by its process: the Claude Code inside the window you are using, and showing that project
    (two sessions in one project, in T3 Code and in a terminal, are told apart). A session whose
    process isn't known is matched by folder, and only when it is the only session in that folder.
    Claude's own list and other adapters use their lifecycle signals without this fallback. A new question is a new
    wait. Clock injected, so this is pure and tested."""

    def __init__(self):
        self.watch = {}    # session → {"ts": when it asked, "since": when you came to it, "seen": bool}

    @staticmethod
    def looking_at(s, focus, sessions):
        pids, folder = focus.get("pids") or set(), focus.get("dir")
        if s.get("pid"):
            return s["pid"] in pids
        if not folder or not session_directory(s["cwd"], [folder]):
            return False
        return sum(1 for o in sessions if session_directory(o["cwd"], [folder])) == 1

    def update(self, sessions, focus, now):
        """Look at who is waiting and where you are (focus: {"dir", "pids"}). Returns True when a wait
        became seen."""
        if not isinstance(focus, dict):
            focus = {"dir": focus, "pids": set()}
        changed = False
        waiting = set()
        for s in sessions:
            if s["state"] != "wait" or s.get("exact"):
                continue
            waiting.add(session_key(s))
            w = self.watch.get(session_key(s))
            if not w or w["ts"] != s["ts"]:
                w = self.watch[session_key(s)] = {"ts": s["ts"], "since": None, "seen": False}
            if w["seen"]:
                continue
            if not self.looking_at(s, focus, sessions):
                w["since"] = None
                continue
            if w["since"] is None:
                w["since"] = now
            if now - w["since"] >= ACK_SECONDS:
                w["seen"] = changed = True
        for key in list(self.watch):
            if key not in waiting:
                del self.watch[key]
        return changed

    def apply(self, sessions):
        """The sessions as the bar should see them: a seen wait is a finished one."""
        out = []
        for s in sessions:
            w = self.watch.get(session_key(s))
            if s["state"] == "wait" and not s.get("exact") and w and w["seen"] and w["ts"] == s["ts"]:
                s = {**s, "state": "done", "message": ""}
            out.append(s)
        return out


def session_directory(cwd, directories):
    """The most specific project folder that contains this session's folder."""
    best = ""
    for d in directories:
        if d and (cwd == d or cwd.startswith(d.rstrip("/") + "/")) and len(d) > len(best):
            best = d
    return best or None


def urgency(s):
    """Sort key: needs you before working before finished; among waits the one that has waited longest,
    otherwise the newest news."""
    return (AGENT_RANK[s["state"]], -s["ts"] if s["state"] == "wait" else s["ts"])


def agent_by_directory(sessions, directories, ws=None, pid_workspace=None):
    """{project folder: {"state", "ts", "message", "name", "kind"}}: the most urgent session in each.
    When `ws` and `pid_workspace` are given, a session whose PID is known to live on another workspace
    is excluded so the same repo open on two workspaces is not double-badged."""
    out = {}
    for s in sessions:
        if ws is not None and pid_workspace:
            owner_ws = pid_workspace.get(s.get("pid"))
            if owner_ws is not None and owner_ws != ws:
                continue
        d = session_directory(s["cwd"], directories)
        if not d:
            continue
        cur = out.get(d)
        if not cur or urgency(s) > urgency(cur):
            out[d] = {"state": s["state"], "ts": s["ts"], "message": s["message"], "name": s.get("name") or "",
                      "kind": s.get("agent", "claude")}
    return out


def agent_of(directories, by_directory):
    """The most urgent state among some folders: state "" when none has an agent."""
    best = {"state": "", "ts": 0, "message": "", "name": ""}
    for d in directories:
        cur = by_directory.get(d)
        if cur and urgency(cur) > urgency(best):
            best = cur
    return best


# --- installing the hook into Claude Code's settings.json

def hook_command(script=None, agent="claude"):
    suffix = "" if agent == "claude" else f" --agent {agent}"
    return f"python3 -I {shlex.quote(script or os.path.realpath(__file__))} hook{suffix}"


def claude_config_dirs():
    """Every Claude Code config folder on this machine that has a settings.json (or the one in use)."""
    found = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    for d in ([env] if env else []) + sorted(glob.glob(HOME + "/.claude*")):
        d = os.path.abspath(os.path.expanduser(d))
        if os.path.isdir(d) and d not in found and (os.path.isfile(d + "/settings.json") or d == env):
            found.append(d)
    return found or [HOME + "/.claude"]


def hook_events(agent):
    return HOOK_EVENTS if agent == "claude" else CODEX_EVENTS


def own_handler(handler, agent):
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return False
    command = handler.get("command")
    try:
        words = shlex.split(command) if isinstance(command, str) else []
    except ValueError:
        return False
    suffix = ["hook"] if agent == "claude" else ["hook", "--agent", agent]
    return (len(words) == 3 + len(suffix) and words[:2] == ["python3", "-I"]
            and os.path.basename(words[2]) == HOOK_MARK and words[3:] == suffix)


def _ours(group, agent="claude"):
    handlers = group.get("hooks") if isinstance(group, dict) else None
    return isinstance(handlers, list) and any(own_handler(h, agent) for h in handlers)


def with_hooks(settings, command, install=True, agent="claude"):
    """Add or remove Signpost handlers while keeping other handlers, even in a shared group."""
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    out = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            out[event] = groups
            continue
        kept = []
        for group in groups:
            if not _ours(group, agent):
                kept.append(group)
                continue
            handlers = [h for h in group["hooks"] if not own_handler(h, agent)]
            if handlers:
                kept.append({**group, "hooks": handlers})
        if kept or not groups:
            out[event] = kept
    if install:
        for event in hook_events(agent):
            if event in out and not isinstance(out[event], list):
                raise ValueError(f"{event} must be a list")
            out.setdefault(event, []).append({"hooks": [{"type": "command", "command": command}]})
    result = {k: v for k, v in settings.items() if k != "hooks"}
    if out:
        result["hooks"] = out
    return result


def hooks_installed(settings, agent="claude"):
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    return [e for e in hook_events(agent) if isinstance(hooks.get(e), list) and any(_ours(g, agent) for g in hooks[e])]


def hook_install_state(dirs=None):
    """("installed" | "partial" | "none", [folders]): whether Claude Code reports to Signpost through its
    hook. Reads only the settings.json files that exist (no more than `hooks status` does). An install
    from an older Signpost (fewer events) is "partial": `hooks install` adds the rest."""
    full, part = [], []
    for d in (claude_config_dirs() if dirs is None else dirs):
        try:
            with open(d + "/settings.json", encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, ValueError):
            continue
        have = hooks_installed(settings) if isinstance(settings, dict) else []
        if len(have) == len(HOOK_EVENTS):
            full.append(collapse_home(d))
        elif have:
            part.append(collapse_home(d))
    return ("installed", full) if full else ("partial", part) if part else ("none", [])


def claude_summary(hook, view, script=None):
    """(short, detail) about where "needs you" for Claude Code comes from. `hook` is hook_install_state();
    `view` is True when the claude command (for `claude agents --json`) exists."""
    state, dirs = hook
    how = f"python3 -I {shlex.quote(script or os.path.realpath(__file__))} hooks install"
    parts = (["hook installed"] if state == "installed" else ["hook partly installed"] if state == "partial" else [])
    parts += ["agents view"] if view else []
    short = "claude: " + (" + ".join(parts) or "none")
    if state == "installed":
        detail = f"claude: the hook reports at once ({', '.join(dirs)})" + ("; Claude Code's own session list confirms waits" if view else "")
    elif view:
        detail = ("claude: needs you comes from Claude Code's own session list (claude agents --json), a few seconds behind; "
                  + (f"the hook is from an older Signpost ({', '.join(dirs)}), add the new events: {how}" if state == "partial"
                     else f"for instant updates add the hook: {how}"))
    else:
        detail = ("claude: no needs-you source: the claude command was not found and the hook is "
                  + (f"only partly installed ({', '.join(dirs)})" if state == "partial" else "not installed") + f" (run: {how})")
    return short, detail


def hook_dirs(agent, all_claude=False):
    if agent == "claude":
        dirs = claude_config_dirs()
        return dirs if all_claude else dirs[:1]
    if agent == "codex":
        return [os.environ.get("CODEX_HOME") or HOME + "/.codex"]
    return [os.environ.get("OPENCODE_CONFIG_DIR") or CONFIG_HOME + "/opencode"]


def opencode_hook_config(op, directory):
    path = Path(directory, "plugins", "signpost", "index.ts")
    backup = Path(directory, "signpost-opencode-plugin.ts.signpost-backup")
    before = read_bytes(path)
    if before is not None and not before.startswith(b"// Signpost OpenCode 2.x adapter\n"):
        raise ValueError(f"{path} belongs to another plugin")
    if op == "status":
        print(f"{path}: {'installed' if before is not None else 'not installed'} (OpenCode 2.x experimental)")
        return
    if op == "remove":
        if before is not None:
            atomic_write(backup, before, 0o600)
            path.unlink()
            if not any(path.parent.iterdir()):
                path.parent.rmdir()
        print(f"{path}: removed (OpenCode 2.x experimental)")
        return
    template = Path(HERE, "assets", "opencode-plugin.ts").read_text()
    script = json.dumps(os.path.realpath(__file__))
    after = template.replace('"__SIGNPOST_SCRIPT__"', script).encode()
    if before != after:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if before is None and any(path.parent.iterdir()):
            raise ValueError(f"{path.parent} belongs to another plugin")
        if before is not None:
            atomic_write(backup, before, 0o600)
        atomic_write(path, after, 0o600)
    print(f"{path}: {'already installed' if before == after else 'installed'} (OpenCode 2.x experimental)")


def json_hook_config(op, directory, agent):
    filename = "settings.json" if agent == "claude" else "hooks.json"
    path = os.path.join(directory, filename)
    before = read_bytes(path)
    settings = json.loads(before) if before is not None else {}
    if not isinstance(settings, dict):
        raise ValueError("not an object")
    have = hooks_installed(settings, agent)
    events = hook_events(agent)
    if op == "status":
        if len(have) == len(events):
            print(f"{path}: installed")
        elif have:
            missing = ", ".join(e for e in events if e not in have)
            print(f"{path}: partly installed ({len(have)} of {len(events)} events; `hooks install` adds {missing})")
        else:
            print(f"{path}: not installed")
        return
    new = with_hooks(settings, hook_command(agent=agent), install=(op == "install"), agent=agent)
    verb = "installed" if op == "install" else "removed"
    if new == settings:
        print(f"{path}: already {verb}")
        return
    if before is not None:
        atomic_write(path + ".signpost-backup", before, 0o600)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    atomic_write(path, json_bytes(new), 0o600)
    print(f"{path}: {verb}" + (f" (backup: {filename}.signpost-backup)" if before is not None else ""))


def other_hooks_state(codex_dir=None, opencode_dir=None):
    """{"codex": state, "opencode": state}, each "installed" · "partial" · "none": whether Signpost's
    Codex hooks and OpenCode plugin are in place. Reads two small files, nothing else."""
    codex_dir = codex_dir or hook_dirs("codex")[0]
    opencode_dir = opencode_dir or hook_dirs("opencode")[0]
    try:
        with open(os.path.join(codex_dir, "hooks.json"), encoding="utf-8") as f:
            settings = json.load(f)
        have = hooks_installed(settings, "codex") if isinstance(settings, dict) else []
    except (OSError, ValueError):
        have = []
    codex = "installed" if len(have) == len(CODEX_EVENTS) else "partial" if have else "none"
    plugin = read_bytes(os.path.join(opencode_dir, "plugins", "signpost", "index.ts"))
    opencode = "installed" if plugin and plugin.startswith(b"// Signpost OpenCode 2.x adapter\n") else "none"
    return {"codex": codex, "opencode": opencode}


def others_line(installed, running=()):
    """"codex: hook installed · opencode: CPU only", for `status` and `explain`. An agent is named when
    its adapter is installed, or when it runs without one (then its states are only inferred)."""
    parts = []
    for agent, adapter in (("codex", "hook"), ("opencode", "plugin")):
        state = (installed or {}).get(agent, "none")
        if state == "installed":
            parts.append(f"{agent}: {adapter} installed")
        elif state == "partial":
            parts.append(f"{agent}: {adapter} partly installed")
        elif agent in running:
            parts.append(f"{agent}: CPU only")
    return " · ".join(parts)


def doctor_report(which=shutil.which, run=subprocess.run, rules_file=USER_RULES,
                  state_dir=STATE_HOME + "/signpost", processes=None):
    """[(mark, text)] for `signpost.py doctor`: "ok", "--" (optional, not set up) or "!!" (a problem)."""
    rows = []

    def add(ok, text):
        rows.append(("ok" if ok is True else "--" if ok is None else "!!", text))
    add(sys.version_info >= (3, 10), f"python {sys.version.split()[0]} (3.10 or newer)")
    version = None
    if which("git"):
        try:
            m = re.search(r"(\d+)\.(\d+)", run(["git", "--version"], capture_output=True, text=True, timeout=3).stdout)
            version = (int(m.group(1)), int(m.group(2))) if m else None
        except (OSError, subprocess.SubprocessError):
            pass
    add(bool(version and version >= (2, 40)),
        f"git {'.'.join(map(str, version)) if version else 'not found'} (the switcher's git line needs 2.40 or newer)")
    for tool, why in (("busctl", "sends the alerts"), ("gdbus", "hears the Go to it button")):
        add(bool(which(tool)), f"{tool} {'found' if which(tool) else 'missing'}: it {why}")
    add(True if which("claude") else None, "claude " + ("found: Claude Code's needs you works without a hook"
                                                        if which("claude") else "not on PATH (only matters for Claude Code)"))
    state, dirs = hook_install_state()
    add(True if state == "installed" else None if state == "none" else False,
        f"Claude Code hook: {state}" + (f" ({', '.join(dirs)})" if dirs else "") + ("" if state == "installed" else ": hooks install"))
    others = other_hooks_state()
    add(True if others["codex"] == "installed" else None if others["codex"] == "none" else False,
        f"Codex hooks: {others['codex']}" + ("; approve them in Codex's /hooks" if others["codex"] != "none" else ": hooks install --agent codex"))
    add(True if others["opencode"] == "installed" else None,
        f"OpenCode plugin (experimental): {others['opencode']}" + ("" if others["opencode"] == "installed" else ": hooks install --agent opencode"))
    try:
        newest = max((e.stat().st_mtime for e in os.scandir(os.path.join(state_dir, "commands")) if e.name.endswith(".json")), default=0)
    except OSError:
        newest = 0
    add(True if time.time() - newest < 86400 else None,
        "shell snippet: reported a command today" if time.time() - newest < 86400
        else "shell snippet: no reports in the last day (optional, for pass/fail: shell-init bash|zsh|fish)")
    if os.path.exists(rules_file):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            load_rules(rules_file)
        problems = [line.removeprefix("signpost: ") for line in err.getvalue().splitlines() if line]
        add(not problems, f"{collapse_home(rules_file)}: " + ("; ".join(problems) if problems else "read without problems"))
    else:
        add(None, f"{collapse_home(rules_file)}: none (optional)")
    try:
        loose = statmod.S_IMODE(os.stat(state_dir).st_mode) & 0o077
        add(not loose, f"{collapse_home(state_dir)}: " + ("readable by you only" if not loose else "others can read it"))
    except OSError:
        add(None, f"{collapse_home(state_dir)}: not created yet")
    table = proc_table() if processes is None else processes
    engines = sum(1 for _, argv in table.values() if len(argv) >= 2 and argv[-1] == "watch"
                  and any(os.path.basename(a) == "signpost.py" for a in argv))
    add(engines > 0, f"engine: {engines} running (one per bar)" if engines else "engine: not running (is Signpost on the bar?)")
    return rows


def doctor_cli():
    rows = doctor_report()
    for mark, text in rows:
        print(f"{mark}  {text}")
    return 1 if any(mark == "!!" for mark, _ in rows) else 0


def hooks_cli(args):
    parser = argparse.ArgumentParser(prog="signpost.py hooks")
    parser.add_argument("op", choices=("status", "install", "remove"), nargs="?", default="status")
    parser.add_argument("--agent", choices=HOOK_AGENTS)
    parser.add_argument("--config-dir", action="append")
    options = parser.parse_args(args)
    agents = HOOK_AGENTS if options.op == "status" and not options.agent and not options.config_dir else (options.agent or "claude",)
    failed = False
    for agent in agents:
        for directory in options.config_dir or hook_dirs(agent, options.op == "status"):
            directory = os.path.abspath(os.path.expanduser(directory))
            try:
                if agent == "opencode":
                    opencode_hook_config(options.op, directory)
                else:
                    json_hook_config(options.op, directory, agent)
            except (OSError, ValueError, TypeError) as e:
                print(f"{directory}: not touched ({e})")
                failed = True
        if agent == "codex" and options.op != "remove":
            print("Codex: review and trust the hook definitions in /hooks before they can run.")
        if agent == "claude" and options.op == "status":
            print(claude_summary(hook_install_state(options.config_dir), bool(shutil.which("claude")))[1])
    return 1 if failed else 0


# --------------------------------------------------------------------------- the bar layout (swap, unswap)
#
# Signpost takes the place of Omarchy's workspace widget (omarchy.workspaces, or a clone of it) in
# shell.json's bar layout, and remembers the entry it replaced in bar-restore.json, so `unswap` puts it
# back with its settings and position. Nothing here runs unless you ask: `signpost.py swap`, or the
# Replace button of the hint.

SIGNPOST_ID = "io.github.tammyx44.signpost"
STOCK_ID = "omarchy.workspaces"
BAR_SECTIONS = ("left", "centerBegin", "center", "centerEnd", "right", "tray")
SHELL_CONFIG = CONFIG_HOME + "/omarchy/shell.json"
PLUGINS_DIR = CONFIG_HOME + "/omarchy/plugins"
OMARCHY_DEFAULTS = (os.environ.get("OMARCHY_PATH") or "/usr/share/omarchy") + "/config/omarchy/shell.json"
RESTORE_FILE = STATE_HOME + "/signpost/bar-restore.json"
HINT_FILE = STATE_HOME + "/signpost/bar-hint.json"


def widget_id(widget):
    return widget.get("id") if isinstance(widget, dict) else widget


def locations(layout, ids):
    return [(section, index) for section in BAR_SECTIONS for index, entry in enumerate(layout.get(section, []))
            if widget_id(entry) in ids]


def remove_entries(layout, widget):
    for section, index in reversed(locations(layout, (widget,))):
        del layout[section][index]


def layout_slot(layout, section, index, entry):
    widgets = layout[section]
    return {"section": section, "index": index, "entry": entry,
            "previous": widget_id(widgets[index - 1]) if index else None,
            "following": widget_id(widgets[index + 1]) if index + 1 < len(widgets) else None}


def restore_index(widgets, previous, current):
    ids = [widget_id(widget) for widget in widgets]
    before = ids.index(previous["previous"]) + 1 if previous["previous"] in ids else None
    after = ids.index(previous["following"]) if previous["following"] in ids else None
    if current is not None and (before is None or before <= current) and (after is None or current <= after):
        return current
    if before is not None:
        return before
    if after is not None:
        return after
    return min(previous["index"], len(widgets))


def layout_for(config):
    bar = config.get("bar", {})
    layout = bar.get("layout", {}) if isinstance(bar, dict) else None
    if not isinstance(layout, dict) or any(not isinstance(layout.get(section, []), list) for section in BAR_SECTIONS):
        raise ValueError("shell.json has an invalid bar layout")
    return layout


def swap_layout(config, record, workspace_ids):
    layout = layout_for(config)
    had_layout = "layout" in config.get("bar", {})
    config.setdefault("bar", {}).setdefault("layout", layout)
    stock = locations(layout, workspace_ids)
    signpost = locations(layout, (SIGNPOST_ID,))
    if len(stock) > 1 or len(signpost) > 1:
        raise ValueError("the bar has multiple workspace widgets of the same kind")
    disabled = config.get("disabledPlugins", [])
    if not isinstance(disabled, list):
        raise ValueError("shell.json has an invalid disabledPlugins list")
    if record is None:
        record = {"version": 1, "original": None,
                  "disabledIndex": disabled.index(SIGNPOST_ID) if SIGNPOST_ID in disabled else None,
                  "createdLeft": "left" not in layout, "createdLayout": not had_layout}
    if SIGNPOST_ID in disabled:
        disabled.remove(SIGNPOST_ID)

    if stock:
        section, index = stock[0]
        original = layout[section][index]
        record = {**record, "original": layout_slot(layout, section, index, original)}
        replacement = layout[signpost[0][0]][signpost[0][1]] if signpost else {"id": SIGNPOST_ID}
        layout[section][index] = replacement
        if signpost:
            old_section, old_index = signpost[0]
            del layout[old_section][old_index]
    elif not signpost:
        left = layout.setdefault("left", [])
        menu = locations({"left": left}, ("omarchy.menu",))
        left.insert(menu[0][1] + 1 if menu else 0, {"id": SIGNPOST_ID})
    elif record["original"] is None and not record.get("managed"):
        # An older install did not keep the replaced entry. Adopt its current slot.
        section, index = signpost[0]
        record = {**record, "original": layout_slot(layout, section, index, {"id": STOCK_ID}), "legacy": True}
    record["managed"] = True
    return record


def undo_layout(config, record, workspace_ids):
    layout = layout_for(config)
    signpost = locations(layout, (SIGNPOST_ID,))
    original = record["original"] if record else None
    legacy = bool(record and record.get("legacy"))
    if record is None and signpost and not locations(layout, workspace_ids):
        section, index = signpost[0]
        original = layout_slot(layout, section, index, {"id": STOCK_ID})
        legacy = True
    remove_entries(layout, SIGNPOST_ID)

    if original and not locations(layout, (widget_id(original["entry"]),)):
        section = original["section"]
        widgets = layout.setdefault(section, [])
        current = next((index for current_section, index in signpost if current_section == section), None)
        widgets.insert(restore_index(widgets, original, current), original["entry"])
    if record and record.get("createdLeft") and layout.get("left") == []:
        del layout["left"]
    if record and record.get("createdLayout") and not layout and "bar" in config:
        config["bar"].pop("layout", None)
    if record and record["disabledIndex"] is not None:
        disabled = config.setdefault("disabledPlugins", [])
        if SIGNPOST_ID not in disabled:
            disabled.insert(record["disabledIndex"], SIGNPOST_ID)
    if isinstance(config.get("plugins"), list):
        config["plugins"] = [entry for entry in config["plugins"] if widget_id(entry) != SIGNPOST_ID]
    return legacy


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def config_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def workspace_widget_ids(plugins_dir=PLUGINS_DIR):
    """omarchy.workspaces and every installed clone of it (Omarchy's `plugin clone` marks them)."""
    ids = {STOCK_ID}
    for path in Path(plugins_dir).glob("*/manifest.json"):
        try:
            manifest = json.loads(path.read_bytes())
            if (manifest.get("omarchy", {}).get("clonedFrom") == STOCK_ID
                    and "bar-widget" in manifest.get("kinds", [])):
                ids.add(manifest["id"])
        except (ValueError, OSError, AttributeError, TypeError, KeyError):
            continue
    return ids - {SIGNPOST_ID}


def plan_layout(operation, config_bytes, record_bytes, defaults_bytes, ids):
    """(was active, active, new shell.json bytes, new restore record bytes, legacy) for "swap",
    "remove" (undo) or None (just look). Pure: reads and writes nothing."""
    blank_config = not (config_bytes or b"").strip()
    config = json.loads(defaults_bytes if blank_config else config_bytes)
    if not isinstance(config, dict) or type(config.get("version")) is not int or config["version"] != 1:
        raise ValueError("shell.json needs Omarchy's version 1 format")
    original = config_digest(config)
    was_active = bool(locations(layout_for(config), (SIGNPOST_ID,))) and SIGNPOST_ID not in (config.get("disabledPlugins") or [])
    legacy = False
    if operation:
        record = json.loads(record_bytes) if record_bytes is not None else None
        if record is not None and (not isinstance(record, dict) or record.get("version") != 1):
            raise ValueError("Signpost's saved bar layout has an unsupported format")
        if operation == "swap":
            inherited_bar = "bar" not in config
            if inherited_bar:
                config["bar"] = json.loads(defaults_bytes)["bar"]
                inherited_digest = config_digest(config["bar"])
            record = swap_layout(config, record, ids)
            legacy = bool(record.get("legacy"))
            if inherited_bar:
                record["inheritedBar"] = inherited_digest
            if blank_config:
                record["baseline"] = original
                record["emptyConfig"] = config_bytes.decode() if config_bytes is not None else None
            record_bytes = json_bytes(record)
        else:
            legacy = undo_layout(config, record, ids)
            if record and record.get("inheritedBar") == config_digest(config.get("bar")):
                del config["bar"]
            if record and record.get("baseline") == config_digest(config):
                empty = record.get("emptyConfig")
                return was_active, False, empty.encode() if empty is not None else None, None, legacy
            record_bytes = None
    active = bool(locations(layout_for(config), (SIGNPOST_ID,))) and SIGNPOST_ID not in (config.get("disabledPlugins") or [])
    return was_active, active, json_bytes(config) if config_digest(config) != original else config_bytes, record_bytes, legacy


def read_bytes(path):
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def atomic_write(path, data, mode=None):
    path = Path(path)
    if data is None:
        path.unlink(missing_ok=True)
        return
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    draft = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            stream.write(data)
        draft.replace(path)
    finally:
        draft.unlink(missing_ok=True)


def bar_swap(operation, config=SHELL_CONFIG, record=RESTORE_FILE, defaults=OMARCHY_DEFAULTS, plugins=PLUGINS_DIR,
             reload=True):
    """`signpost.py swap` / `unswap`: the installer's bar change, from the installed plugin. Writes the
    restore record first and shell.json last (each in one step), then asks the shell to reload it.
    Returns a message. Raises ValueError when the layout can't be changed safely."""
    config, record = Path(config), Path(record)
    if config.resolve().is_relative_to(Path(defaults).resolve().parents[2]):
        raise ValueError("shell.json points into Omarchy's packaged files")
    before, saved = read_bytes(config), read_bytes(record)
    ids = workspace_widget_ids(plugins)
    if operation == "swap" and before and before.strip():
        layout = layout_for(json.loads(before))
        if locations(layout, (SIGNPOST_ID,)) and not locations(layout, ids):
            return "nothing to change"     # Signpost is on the bar, and no other workspace widget is
    if operation == "remove" and saved is None:
        raise ValueError("there is no saved layout to go back to (bar-restore.json is missing)")
    _, active, after, record_after, legacy = plan_layout(operation, before, saved, read_bytes(defaults) or b"{}", ids)
    mode = statmod.S_IMODE(config.stat().st_mode) if before is not None else None
    if after == before and record_after == saved:
        return "nothing to change"
    private_dir(str(record.parent))
    if operation == "swap" and record_after != saved:
        atomic_write(record, record_after, 0o600)
    if read_bytes(config) != before:
        raise ValueError("shell.json changed meanwhile; nothing was changed. Try again.")
    if after != before:
        atomic_write(config, after, mode)
    if operation == "remove":
        record.unlink(missing_ok=True)
    if reload:
        try:
            subprocess.run(["omarchy-shell", "shell", "reloadConfig"], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
    if operation == "swap":
        return "Signpost now has the workspace widget's place." + (" (earlier install: current slot kept)" if legacy else "")
    return "The previous workspace widget is back." if active is False else "Done."


def bar_hint(state_dir, config=SHELL_CONFIG, plugins=PLUGINS_DIR):
    """{"id": the other widget} when Signpost and Omarchy's workspace widget (or a clone) are both on the
    bar, so the bar shows two rows of numbers; None otherwise, or once you chose Keep both."""
    if not config or os.path.exists(os.path.join(state_dir, "bar-hint.json")):
        return None
    try:
        layout = layout_for(json.loads(Path(config).read_bytes()))
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    ids = workspace_widget_ids(plugins)
    stock = locations(layout, ids)
    if not stock or not locations(layout, (SIGNPOST_ID,)):
        return None
    section, index = stock[0]
    return {"id": widget_id(layout[section][index])}


def keep_both(state_dir):
    """Keep both widgets: the hint never comes back (delete bar-hint.json to see it again)."""
    write_private(os.path.join(state_dir, "bar-hint.json"), json.dumps({"keepBoth": True, "at": int(time.time())}) + "\n")


# --------------------------------------------------------------------------- git details (for the switcher)

# A project folder may be a repo somebody else wrote, copied with its .git folder. Its own config and
# attributes can name programs for git to run even on a read: core.fsmonitor, hooks, an external diff,
# textconv and diff drivers, clean/smudge/process filters (run by `status` and `diff` when a file's
# stat changed), and a transport (ssh command, credential helper) when a partial clone fetches a
# missing object on demand. Every call switches all of those off:
#   * attributes are read from the empty tree, and no attributes file, so .gitattributes assigns nothing
#   * every filter and diff driver named in any config git sees is blanked (.git/info/attributes can
#     still assign a driver, but a blank driver runs nothing), see git_neutralizers()
#   * no transport at all (protocol.allow=never, each protocol.<name>.allow too) and no lazy fetch
#   * --ignore-submodules=all, so git never steps into a submodule with its own config
# --attr-source needs git 2.40; an older git fails the call, and the switcher shows no git line.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
GIT_SAFE = ["git", "--no-optional-locks", "--no-pager", f"--attr-source={EMPTY_TREE}",
            "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.pager=cat",
            "-c", "core.attributesFile=/dev/null", "-c", "diff.external=", "-c", "credential.helper=",
            "-c", "protocol.allow=never", "-c", "submodule.recurse=false"]
GIT_PROTOCOLS = ("file", "git", "ssh", "http", "https", "ext", "ftp", "ftps", "rsync")
GIT_ENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
           "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_ATTR_NOSYSTEM": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1"}
GIT_MAX_OVERRIDES = 600


def git_neutralizers(listing):
    """`git config --list --null` → the (key, value) pairs that blank every filter and diff driver and
    every transport the config names. Passed as GIT_CONFIG_KEY_n / VALUE_n (command-line scope, the
    strongest), which takes any driver name as it is. None when there are too many to pass."""
    pairs = {f"protocol.{p}.allow": "never" for p in GIT_PROTOCOLS}
    for item in (listing or "").split("\0"):
        key = item.split("\n", 1)[0]
        section, _, rest = key.partition(".")
        sub, _, var = rest.rpartition(".")
        section, var = section.lower(), var.lower()
        if not sub:
            continue
        if section == "filter":
            for name in ("clean", "smudge", "process"):
                pairs[f"filter.{sub}.{name}"] = ""
            pairs[f"filter.{sub}.required"] = "false"
        elif section == "diff":
            pairs[f"diff.{sub}.textconv"] = ""
            pairs[f"diff.{sub}.command"] = ""
        elif section == "protocol" and var == "allow":
            pairs[f"protocol.{sub}.allow"] = "never"
        elif section == "remote" and var == "promisor":
            pairs[f"remote.{sub}.promisor"] = "false"
    return None if len(pairs) > GIT_MAX_OVERRIDES else sorted(pairs.items())


def git_env(pairs):
    env = dict(GIT_ENV, GIT_CONFIG_COUNT=str(len(pairs)))
    for i, (key, value) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    return env


def parse_git_status(text):
    """`git status --porcelain=v2 --branch` → branch, ahead, behind, number of changed files."""
    out = {"branch": "", "ahead": 0, "behind": 0, "files": 0}
    head = oid = ""
    for line in text.splitlines():
        if line.startswith("# branch.head "):
            head = line[14:].strip()
        elif line.startswith("# branch.oid "):
            oid = line[13:].strip()
        elif line.startswith("# branch.ab "):
            m = re.match(r"# branch\.ab \+(\d+) -(\d+)", line)
            if m:
                out["ahead"], out["behind"] = int(m.group(1)), int(m.group(2))
        elif line[:2] in ("1 ", "2 ", "u ", "? "):
            out["files"] += 1
    out["branch"] = clean_text(head if head and head != "(detached)" else (oid[:7] if oid and oid != "(initial)" else head))
    return out


def parse_numstat(text):
    added = removed = 0
    for line in text.splitlines():
        a, _, rest = line.partition("\t")
        r = rest.partition("\t")[0]
        if a.isdigit() and r.isdigit():    # binary files say "-"
            added, removed = added + int(a), removed + int(r)
    return added, removed


def web_remote(url):
    """A remote as an https page, or "" when it isn't something safe to open in a browser."""
    url = str(url or "").strip()
    m = (re.match(r"^(?:ssh://)?[\w.-]+@([\w.-]+)(?::\d+)?[:/](.+)$", url)
         or re.match(r"^(?:https?|git)://(?:[^@/]*@)?([\w.-]+)(?::\d+)?/(.+)$", url))
    if not m:
        return ""
    host, path = m.group(1), re.sub(r"\.git/?$", "", m.group(2).strip("/"))
    if not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", host) or not re.fullmatch(r"[A-Za-z0-9._~/-]+", path) or ".." in path:
        return ""
    return f"https://{host}/{path}"


def git_details(directory, run=subprocess.run):
    """Branch, how far ahead/behind, changed lines and the web address of origin. {} when it is not a repo."""
    def git(*args, env=None):
        try:
            r = run([*GIT_SAFE, "-C", directory, *args], capture_output=True, text=True, timeout=3,
                    env=env or git_env(sorted({f"protocol.{p}.allow": "never" for p in GIT_PROTOCOLS}.items())))
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return r.stdout if r.returncode == 0 else None

    # First the config, read as text (reading runs nothing), to learn every driver name it defines.
    pairs = git_neutralizers(git("config", "--list", "--null"))
    if pairs is None:
        return {}           # too many drivers to switch off one by one: don't run git on it at all
    env = git_env(pairs)
    status = git("status", "--porcelain=v2", "--branch", "--ignore-submodules=all", env=env)
    if status is None:
        return {}
    out = parse_git_status(status)
    numstat = git("diff", "HEAD", "--numstat", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", env=env)
    out["added"], out["removed"] = parse_numstat(numstat or "")
    out["remote"] = web_remote(git("remote", "get-url", "origin", env=env))
    return out


class GitDetails:
    """Looked up in the background, and only while the switcher is open: git costs milliseconds per
    project, which is nothing next to a click and too much to pay on every bar update."""

    FRESH = 8.0

    def __init__(self, load=git_details):
        self.load = load
        self.cache = {}        # directory → (when, details)
        self.pending = set()
        self.lock = threading.Lock()
        self.pool = None
        self.dirty = False

    def want(self, directories, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            todo = [d for d in dict.fromkeys(directories)
                    if d and d not in self.pending and now - self.cache.get(d, (-1e9, 0))[0] > self.FRESH]
            self.pending.update(todo)
        if todo and self.pool is None:
            self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="signpost-git")
        for d in todo:
            self.pool.submit(self.fetch, d)

    def fetch(self, directory):
        try:
            details = self.load(directory) if os.path.isdir(directory) else {}
        except Exception:
            details = {}
        with self.lock:
            old = self.cache.get(directory, (0, None))[1]
            self.cache[directory] = (time.monotonic(), details)
            self.pending.discard(directory)
            if old != details:
                self.dirty = True

    def get(self, directory):
        with self.lock:
            return self.cache.get(directory, (0, {}))[1] or {}

    def take_dirty(self):
        with self.lock:
            dirty, self.dirty = self.dirty, False
        return dirty


# --------------------------------------------------------------------------- "needs you" alerts

NOTIFY_ARGS = ["busctl", "--user", "--", "call", "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
               "org.freedesktop.Notifications", "Notify", "susssasa{sv}i"]


# One process per engine watches D-Bus for presses of the "Go to it" button.
MONITOR_ARGV = ["gdbus", "monitor", "--session", "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications"]
ALERT_LIFE = 600     # seconds an alert's button may still jump; after that the watcher is not needed


def proc_table():
    """{pid: (parent pid, argv)} for the processes we may read. Called once, when the engine starts."""
    table = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
            with open(f"/proc/{name}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        table[int(name)] = (ppid, argv)
    return table


def stale_monitors(table):
    """Watchers left behind by an engine that was stopped without time to clean up: ours by their exact
    command line, and whose parent is no longer a Signpost engine. A live engine's own watcher is kept."""
    out = []
    for pid, (ppid, argv) in table.items():
        if argv == MONITOR_ARGV and not any(os.path.basename(a) == "signpost.py" for a in table.get(ppid, (0, []))[1]):
            out.append(pid)
    return out


def reap_stale_monitors():
    for pid in stale_monitors(proc_table()):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def notify_argv(title, body, jump, image="", actions=None):
    """The busctl call for one notification. With `jump` it carries a "Go to it" button. "--" ends busctl's
    options, so text that starts with a dash (a folder called -x) is still just text. `image` is a local
    picture for the card (the hint "image-path"; the service won't take a path as app_icon)."""
    # D-Bus counts the strings of the actions array, and each action is an id and a label: two actions, four strings.
    if actions:
        flat = [clean_text(x)[:40] for pair in actions for x in pair]
        actions = [str(len(flat)), *flat]
    else:
        actions = ["4", "go", "Go to it", "default", ""] if jump else ["0"]
    hints = ["1", "urgency", "y", "1"]
    if image and image.startswith("/") and "\n" not in image:
        hints = ["2", "urgency", "y", "1", "image-path", "s", image]
    return [*NOTIFY_ARGS, "Signpost", "0", "", clean_text(title)[:80], clean_text(body)[:200], *actions, *hints, "10000"]


def clean_palette(raw):
    """The bar's tile colours as [[tile, ink], …] (hex), from QML; anything else is ignored."""
    out = []
    for pair in raw if isinstance(raw, list) else []:
        if (isinstance(pair, list) and len(pair) == 2 and all(isinstance(c, str) and HEX.fullmatch(c) for c in pair)
                and len(out) < 16):
            out.append([pair[0].lower(), pair[1].lower()])
    return out


SIGNPOST_ICON = os.path.join(os.path.dirname(os.path.realpath(__file__)), "icon.png")
HEX = re.compile(r"#[0-9a-fA-F]{6}")


def alert_picture(slot, letter, palette, directory=None):
    """The project's own tile (its colour and letter, as on the bar) for its alert, as a small SVG in the
    runtime folder; Signpost's icon when the bar hasn't sent its colours or the project has no slot."""
    if not isinstance(slot, int) or slot < 0 or not palette:
        return SIGNPOST_ICON
    pair = palette[slot % len(palette)]
    if not (isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(c, str) and HEX.fullmatch(c) for c in pair)):
        return SIGNPOST_ICON
    tile, ink = (c.lower() for c in pair)
    letter = letter if isinstance(letter, str) and len(letter) == 1 and letter.isalnum() else "•"
    directory = directory or os.path.join(os.environ.get("XDG_RUNTIME_DIR") or STATE_HOME, "signpost")
    name = f"tile-{tile[1:]}-{ink[1:]}-{ord(letter):x}.svg"
    path = os.path.join(directory, name)
    if not os.path.exists(path):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
               f'<rect width="64" height="64" rx="19" fill="{tile}"/>'
               f'<text x="32" y="44" font-family="JetBrainsMono Nerd Font, monospace" font-size="36" font-weight="800" '
               f'text-anchor="middle" fill="{ink}">&#{ord(letter)};</text></svg>')
        try:
            write_private(path, svg)
        except OSError:
            return SIGNPOST_ICON
    return path


def parse_notify_id(text):
    m = re.match(r"\s*u\s+(\d+)\s*$", text or "")
    return int(m.group(1)) if m else None


def parse_action(line):
    m = re.search(r"ActionInvoked \(uint32 (\d+), '([^']*)'\)", line or "")
    return (int(m.group(1)), m.group(2)) if m else None


def new_waits(sessions, notified):
    """Sessions that have just started waiting for you. `notified` is updated: a session that goes
    back to work and then waits again is a new request."""
    waiting = {session_key(s): s["ts"] for s in sessions if s["state"] == "wait"}
    fresh = [s for s in sessions if s["state"] == "wait" and notified.get(session_key(s)) != s["ts"]]
    for key in list(notified):
        if key not in waiting:
            del notified[key]
    for s in fresh:
        notified[session_key(s)] = s["ts"]
    return fresh


def wait_alert(session, project, workspace, mixed=False):
    """Title and body for one waiting session. The project leads, so two alerts read apart at a glance;
    then where it is, then the question (last, so a long one wraps on its own)."""
    agent = {"claude": "Claude", "codex": "Codex", "opencode": "OpenCode"}[session.get("agent", "claude")]
    subject = f"{project} · {agent}" if project and mixed else (project or agent)
    title = f"{subject} needs you"
    ask = session["message"] or f"{agent} is waiting for you"
    return title, f"Workspace {workspace} · {ask}" if workspace else ask


CLAIM_LIFE = 24 * 3600     # a claim this old is forgotten


def claim(runtime, key, keep=False, now=None):
    """True for exactly one caller per key, across every bar's engine (one per monitor): the first to
    create the claim file. A dead engine's claims are just files: they are dropped after CLAIM_LIFE
    (or at logout, with the runtime folder). `keep` claims live in the state folder instead, so they
    survive a reboot (the bar hint is offered once, ever)."""
    now = time.time() if now is None else now
    folder = os.path.join(runtime if not keep else os.path.join(runtime, "keep"), "claims")
    try:
        private_dir(folder)
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if not keep and now - os.stat(path).st_mtime > CLAIM_LIFE:
                    os.unlink(path)
            except OSError:
                pass
        name = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:24]
        fd = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return True       # can't coordinate: better two alerts than none


# --------------------------------------------------------------------------- engine + daemon

def ignored_folder(directory, patterns):
    """Is a folder on the recentIgnore list (globs, ~ for home)?"""
    if not directory:
        return False
    return any(fnmatch.fnmatchcase(directory, expand_home(p)) or fnmatch.fnmatchcase(directory + "/", expand_home(p))
               for p in patterns or [])


def window_agents(f):
    """Every agent process in a window: (pid, rules key, folder)."""
    out = [(a.get("pid"), a.get("agent"), a.get("cwd")) for a in f.get("agents") or []]
    for t in f.get("tabs") or []:
        a = (t.get("fg") or {}).get("agent")
        if a:
            out.append((a.get("pid"), a.get("agent"), a.get("cwd")))
    return [x for x in out if x[0]]


COMMAND_DIR = os.path.join(STATE_HOME, "signpost", "commands")
COMMAND_LABELS = {"test": "Tests running", "build": "Build running", "long": "Long command running"}
COMMAND_RESULTS = {None: "A long command finished", True: "Command passed", False: "Command failed"}
# A finish whose exit code is unknown (no shell snippet) is "ended": never green, which would read as passed.
COMMAND_STATES = {None: "ended", True: "ready", False: "failed"}


def boot_seconds():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def command_args(fg):
    argv = fg.get("argv") or []
    executable = os.path.basename(argv[0]).lstrip("-") if argv else ""
    words = [executable, *argv[1:]] if executable else []
    # A shebang script may show up as "node /path/to/npm ..." or "python /path/to/pytest ...".
    tool = fg["prog"]
    if executable != tool:
        for i, arg in enumerate(argv[1:], 1):
            name = re.sub(r"\.(?:[cm]?js|ts|py)$", "", os.path.basename(arg))
            if name in (tool, tool + "-cli"):
                words = [tool, *argv[i + 1:]]
                break
    return words


def raw_command_args(fg):
    argv = fg.get("argv") or []
    executable = os.path.basename(argv[0]).lstrip("-") if argv else ""
    return [executable, *argv[1:]] if executable else []


def command_kind(fg, rules, shells, now, threshold):
    if not fg or fg.get("agent") or not fg.get("pid") or not fg.get("cwd"):
        return None
    prog = fg["prog"]
    if (prog in shells or prog == "tmux" or prog in rules.get("editors", ())
            or prog in rules.get("remotes", ()) or match_key(prog, rules["agents"])):
        return None
    words = command_args(fg)
    if not words:
        return None
    raw_words = raw_command_args(fg)
    forms = [" ".join(words)[:1024]]
    raw_cmd = " ".join(raw_words)[:1024]
    if raw_cmd and raw_cmd != forms[0]:
        forms.append(raw_cmd)
    for pattern in rules["_commandIgnore"]:
        if any(pattern.match(cmd) for cmd in forms):
            return None
    for pattern, kind in rules["_commands"]:
        if any(pattern.match(cmd) for cmd in forms):
            return kind
    if now - fg["start"] / CLK_TCK >= threshold:
        return "long"
    return None


def command_result(directory, pane, job, now):
    """A shell result must bracket this process and match the shell, cwd and executable."""
    pid, start = pane[1:]
    try:
        with open(os.path.join(directory, f"{pid}.json"), encoding="utf-8") as f:
            if os.fstat(f.fileno()).st_mode & 0o077:
                return None
            r = json.loads(f.read(4096))
        cwds = job.get("cwds") or (job["cwd"],)
        commands = job.get("commands") or (job["command"],)
        if (not isinstance(r, dict) or r.get("start") != start or r.get("cwd") not in cwds
                or r.get("command") not in commands or type(r.get("code")) is not int
                or not 0 <= r["code"] <= 255):
            return None
        launched, ended = r.get("launched"), r.get("ended")
        if (type(launched) not in (int, float) or type(ended) not in (int, float)
                or not launched - 1 / CLK_TCK <= job["started"] <= job["seen"] <= ended <= now
                or job["started"] - launched > 2 or now - ended > 6):
            return None
        return r["code"] == 0
    except (OSError, ValueError, UnicodeError):
        return None


class CommandSignals:
    """Track jobs already visible in the terminal probe. Completions stay in memory until seen."""

    def __init__(self, directory):
        self.directory = directory
        self.running = {}
        self.finished = {}

    def update(self, facts, active, rules, settings, shells, now=None, exited=None):
        if not settings["commandStatus"]:
            self.running.clear()
            self.finished.clear()
            return
        now = boot_seconds() if now is None else now
        if exited is None:
            exited = lambda pid: not os.path.exists(f"/proc/{pid}")
        panes = {(f["address"], t["pid"], t.get("start")): (f["workspace"], t.get("fg"), t.get("cwd"))
                 for f in facts for t in f.get("tabs", [])
                 if t.get("pid") and t.get("start") is not None}
        current, finished = {}, {}
        for pane, (ws, fg, tab_cwd) in panes.items():
            kind = command_kind(fg, rules, shells, now, settings["longCommandSeconds"])
            if kind:
                raw_words = raw_command_args(fg)
                tool = command_args(fg)[0]
                commands = {c for c in (tool, raw_words[0] if raw_words else "") if c}
                cwds = {c for c in (tab_cwd, fg["cwd"]) if c}
                current[pane] = {"id": fg["pid"], "started": fg["start"] / CLK_TCK,
                                 "cwd": fg["cwd"], "cwds": cwds, "command": tool, "commands": commands,
                                 "kind": kind, "ws": ws, "seen": now}
                continue
            if ws == active:
                continue
            item = self.finished.get(pane)
            previous = self.running.get(pane)
            # The job's process is gone and the shell is back, or something else (the shell snippet, a
            # prompt tool) briefly holds the foreground: either way the job finished.
            if (previous and previous["ws"] == ws and (fg is None or fg.get("pid") != previous["id"])
                    and exited(previous["id"])):
                item = {"job": previous, "at": now, "outcome": None}
            if item and item["job"]["ws"] == ws:
                if item["outcome"] is None and now - item["at"] <= 4:
                    item["outcome"] = command_result(self.directory, pane, item["job"], now)
                finished[pane] = item
        self.running, self.finished = current, finished

    def statuses(self):
        """One command state per workspace, active commands ahead of recent completions."""
        out = {}
        for item in sorted(self.finished.values(), key=lambda item: item["at"]):
            out[item["job"]["ws"]] = self.finished_fields(item)
        for job in self.running.values():
            out[job["ws"]] = {"command": "work", "commandText": COMMAND_LABELS[job["kind"]]}
        return out

    @staticmethod
    def finished_fields(item):
        return {"command": COMMAND_STATES[item["outcome"]], "commandText": COMMAND_RESULTS[item["outcome"]]}

    def by_project(self, ws_dirs):
        """{(workspace, project directory): {"command", "commandText"}} for the switcher."""
        out = {}
        def directory(job):
            dirs = ws_dirs.get(job["ws"]) or ()
            return next((d for d in (session_directory(c, dirs) for c in job.get("cwds") or (job["cwd"],)) if d), None)
        for item in sorted(self.finished.values(), key=lambda item: item["at"]):
            d = directory(item["job"])
            if d:
                out[(item["job"]["ws"], d)] = self.finished_fields(item)
        for job in self.running.values():
            d = directory(job)
            if d:
                out[(job["ws"], d)] = {"command": "work", "commandText": COMMAND_LABELS[job["kind"]]}
        return out


def shell_command_name(raw):
    # Reject shell syntax whose overall result could belong to a different command.
    if re.search(r"[;&|<>\n\r`$]", raw):
        return ""
    try:
        words = shlex.split(raw)
    except ValueError:
        return ""
    return os.path.basename(words[0])[:128] if words else ""


def shell_record(args, directory=COMMAND_DIR):
    """Record the start or exit of one interactive command, without saving arguments."""
    try:
        phase, shell_pid, cwd = args[:3]
        pid = int(shell_pid)
        facts = read_proc(pid)
        if not facts or facts["session"] != pid or not facts["tty"] or not os.path.isabs(cwd):
            return 0
        path = os.path.join(directory, f"{pid}.json")
        if phase == "start" and len(args) in (3, 4):
            raw = sys.stdin.read(16384).strip()
            command = shell_command_name(raw)
            record = {"command": command, "start": facts["start"], "cwd": cwd,
                      "launched": boot_seconds()}
            if len(args) == 4:
                record["history"] = int(args[3])
        elif phase == "end" and len(args) == 4:
            with open(path, encoding="utf-8") as f:
                record = json.loads(f.read(4096))
            if (record.get("start") != facts["start"] or record.get("cwd") != cwd
                    or "ended" in record or not record.get("command")):
                return 0
            record.update(ended=boot_seconds(), code=int(args[3]))
        else:
            return 0
        write_private(path, json.dumps(record))
    except (OSError, ValueError, IndexError, AttributeError, TypeError):
        pass
    return 0


def shell_init(shell, script=None):
    quoted = shlex.quote(script or os.path.realpath(__file__))
    runner = f"command python3 -I {quoted} shell-record"
    if shell == "bash":
        # PS0 runs after a complete interactive command is read, before it executes (never for an
        # empty line). Its expansion sets _signpost_ran in this shell, so an empty Enter runs nothing.
        # The hook is called with "$_", so $_ keeps the previous command's last argument afterwards.
        return r'''# Signpost command exit reporting (Bash)
if [[ $- == *i* && -z ${_signpost_shell_init-} ]]; then
    _signpost_shell_init=1
    _signpost_start() {
        local HISTTIMEFORMAT= line
        line=$(history 1)
        [[ $line =~ ^[[:space:]]*([0-9]+)[[:space:]]+(.*)$ ]] || return 0
        printf '%s' "${BASH_REMATCH[2]}" | @RUN@ start "$$" "$PWD" "${BASH_REMATCH[1]}"
    }
    _signpost_end() {
        local result=$?
        [[ -n ${_signpost_ran-} ]] || return "$result"
        unset _signpost_ran
        @RUN@ end "$$" "$PWD" "$result"
        return "$result"
    }
    PS0="${PS0-}"'${_signpost_none[_signpost_ran=1]-}$(_signpost_start)'
    if [[ $(declare -p PROMPT_COMMAND 2>/dev/null) == 'declare -a '* ]]; then
        PROMPT_COMMAND=('_signpost_end "$_"' "${PROMPT_COMMAND[@]}")
    else
        PROMPT_COMMAND='_signpost_end "$_"'"${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
    fi
fi
'''.replace("@RUN@", runner)
    if shell == "zsh":
        return r'''# Signpost command exit reporting (Zsh)
if [[ -o interactive && -z ${_signpost_shell_init-} ]]; then
    _signpost_shell_init=1
    _signpost_active=0
    autoload -Uz add-zsh-hook
    _signpost_start() {
        _signpost_active=1
        printf '%s' "$1" | @RUN@ start "$$" "$PWD"
    }
    _signpost_end() {
        local result=$?
        (( _signpost_active )) || return "$result"
        _signpost_active=0
        @RUN@ end "$$" "$PWD" "$result"
        return "$result"
    }
    add-zsh-hook preexec _signpost_start
    precmd_functions=(_signpost_end ${precmd_functions:#_signpost_end})
fi
'''.replace("@RUN@", runner)
    if shell == "fish":
        return r'''# Signpost command exit reporting (Fish)
if status is-interactive
    function _signpost_start --on-event fish_preexec
        printf '%s' "$argv[1]" | @RUN@ start "$fish_pid" "$PWD"
    end
    function _signpost_end --on-event fish_postexec
        set -l result $status
        @RUN@ end "$fish_pid" "$PWD" "$result"
    end
end
'''.replace("@RUN@", runner)
    raise ValueError("expected bash, zsh or fish")


class Engine:
    def __init__(self, settings=None, rules=None, state_file=STATE_FILE, runtime_dir=None, user_rules=USER_RULES):
        self.rules_file = user_rules if rules is None else None
        self.rules = rules or load_rules(user_rules)
        self.rules_mtime = self._rules_mtime()
        self.settings = resolve_settings(settings)
        self.state_file = state_file
        self.state = load_state(state_file)
        self.state_mtime = self._mtime()
        self.probe = Probe(self.rules)
        self.holder = Holder()
        self.history = ProjectHistory(Path(state_file).with_name("recent-projects.json"))
        self.signals = AgentSignals(os.path.join(os.path.dirname(state_file), "agents"))
        self.commands = CommandSignals(os.path.join(os.path.dirname(state_file), "commands"))
        # Shared by the bars on every monitor: Claude Code's session list and who sent which alert.
        # Tests give a state file of their own, and get a runtime folder of their own with it.
        self.runtime = runtime_dir or (os.path.join(RUNTIME_DIR, "signpost") if state_file == STATE_FILE
                                       else os.path.dirname(state_file))
        self.view = AgentsView(os.path.join(self.runtime, "agents-view.json"))
        self.acks = WaitAcks()
        self.palette = []        # the bar's tile colours, [[tile, ink], …], for an alert's picture
        self.hook = None         # hook_install_state(); looked at on full refreshes only
        self.others = None       # other_hooks_state() for Codex and OpenCode; full refreshes only
        self.hint = None         # the stock workspace widget sits on the bar next to Signpost
        self.shell_config = SHELL_CONFIG if state_file == STATE_FILE else None
        self.gitd = GitDetails()
        self.index = ProjectIndex()
        self.details_on = False
        self.foreground_project = None
        self.seen_open_dirs = set()
        self.claude_pids = set()
        self.agent_kinds = set()
        self.focus_pids = set()
        self.pid_workspace = {}
        self.results = {}
        self.icons = {}
        self.probe_ms = 0.0
        self.windows = 0

    def _mtime(self):
        try:
            return os.stat(self.state_file).st_mtime
        except OSError:
            return 0

    def _rules_mtime(self):
        if not self.rules_file:
            return None
        try:
            st = os.stat(self.rules_file)
            return (st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def reload_rules(self):
        if not self.rules_file:
            return
        self.rules = load_rules(self.rules_file)
        self.rules_mtime = self._rules_mtime()
        self.probe.rules = self.rules
        self.probe._dynamic = [r["_class"] for r in self.rules["_titleRules"] if r.get("kind") in ("agentHost", "project")]

    def rules_changed(self):
        if not self.rules_file:
            return False
        m = self._rules_mtime()
        if m != self.rules_mtime:
            self.reload_rules()
            return True
        return False

    def state_changed(self):
        m = self._mtime()
        if m != self.state_mtime:
            self.state_mtime = m
            self.state = load_state(self.state_file)
            return True
        return False

    def recompute(self, refresh="all", force=False, active=None):
        t0 = time.perf_counter()
        facts = self.probe.facts(hypr_request("j/clients") or [], refresh)
        self.results = decide(facts, self.state, self.settings, self.rules, active=active)
        self.history.refresh()
        self.give_slots()
        if refresh == "all" or self.hook is None:
            self.hook = hook_install_state()
            self.others = other_hooks_state()
            self.hint = bar_hint(os.path.dirname(self.state_file), self.shell_config)
        foreground = next((result["_activeProject"] for result in self.results.values() if result["_activeProject"]), None)
        if foreground and ignored_folder(foreground.get("directory"), self.settings["recentIgnore"]):
            foreground = None
        front_changed = foreground != self.foreground_project
        if front_changed:
            self.foreground_project = foreground
        open_records = [
            row for result in self.results.values() for row in result["_projects"]
            if row.get("directory") and not ignored_folder(row.get("directory"), self.settings["recentIgnore"])
        ]
        new_records = [row for row in open_records if row["directory"] not in self.seen_open_dirs]
        self.seen_open_dirs = {row["directory"] for row in open_records}
        if (front_changed and foreground) or new_records:
            self.history.remember_open(new_records, front=foreground if front_changed else None)
        self.see_agents(facts, active)
        active_ws = next((f["workspace"] for f in facts if f["address"] == active), None)
        if active_ws is None and (self.commands.running or self.commands.finished):
            active_ws = focused_workspace()
        self.commands.update(facts, active_ws, self.rules, self.settings, self.probe.shells)
        self.icons = icon_overrides()
        self.probe_ms = (time.perf_counter() - t0) * 1000
        self.windows = len(facts)
        return self.holder.update(self.results, time.monotonic(), self.settings, active=active, force=force)

    def see_agents(self, facts, active):
        """Which Claude Code processes run (for its session list), and which agents are in the window
        you are using and on the project it shows (for "seen" questions)."""
        agents = [agent for f in facts for agent in window_agents(f)]
        self.agent_kinds = {kind for _, kind, _ in agents}
        self.claude_pids = {pid for pid, key, _ in agents if key == "claude"}
        self.pid_workspace = {pid: f["workspace"] for f in facts for pid, _, _ in window_agents(f) if f.get("workspace")}
        folder = (self.foreground_project or {}).get("directory")
        self.focus_pids = {pid for f in facts if active and f.get("address") == active
                           for pid, _, cwd in window_agents(f) if folder and cwd and session_directory(cwd, [folder])}

    def agent_sessions(self, now=None):
        """Every Claude Code session we know of, merged from its hook and its own list, with seen
        questions turned to finished, and the question's words dropped when showQuestions is off."""
        merged = merge_sessions(self.signals.read(), self.view.sessions())
        focus = {"dir": (self.foreground_project or {}).get("directory"), "pids": self.focus_pids}
        self.acks.update(merged, focus, time.monotonic() if now is None else now)
        sessions = self.acks.apply(merged)
        if not self.settings["showQuestions"]:
            sessions = [{**s, "message": "", "name": ""} for s in sessions]
        return sessions

    def claude_line(self):
        return claude_summary(self.hook or ("none", []), self.view.available())

    def give_slots(self):
        """Every project on screen (and in the recent list) gets its colour slot: new ones are written to
        the state file under a lock, after a fresh read, so the bars on other monitors agree."""
        keys = [r.get("_colorKey") for _, r in sorted(self.results.items())]
        keys += [p["_colorKey"] for _, r in sorted(self.results.items()) for p in r["_projects"]]
        keys += [row.get("aliasKey") or row["directory"] for row in self.history.entries]
        if any(k and k not in self.state.get("colors", {}) for k in keys):
            try:
                state_file = str(self.state_file)
                private_dir(os.path.dirname(state_file))
                with open(state_file + ".lock", "a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    fresh = load_state(self.state_file)
                    self.state.update(colors=fresh["colors"], nextSlot=fresh["nextSlot"])
                    if assign_slots(self.state, keys):
                        self.save()
            except OSError:
                assign_slots(self.state, keys)      # can't write: still consistent for this run
        colors = self.state.get("colors", {})
        for r in self.results.values():
            if r.get("_colorKey") in colors:
                r["slot"] = colors[r["_colorKey"]]
            for p in r["_projects"]:
                if p["_colorKey"] in colors:
                    p["slot"] = colors[p["_colorKey"]]

    def slot_of(self, key):
        return self.state.get("colors", {}).get(key)

    def open_projects(self):
        projects = [project for result in self.results.values() for project in result["_projects"]]
        projects.sort(key=lambda project: (project["name"].casefold(), project["workspace"], project["id"]))
        return projects

    def recent_entries(self):
        """The recent projects the switcher may show: none with switcherRecents off, never an ignored folder."""
        if not self.settings["switcherRecents"]:
            return []
        return [row for row in self.history.entries if not ignored_folder(row["directory"], self.settings["recentIgnore"])]

    def remember_project(self, project_id):
        project = next((row for row in self.open_projects() if row["id"] == project_id), None)
        if project and not ignored_folder(project.get("directory"), self.settings["recentIgnore"]):
            self.history.touch(project)

    def refresh_index(self):
        return self.index.refresh(self.settings["projectRoots"], self.settings["recentIgnore"])

    def select_recent(self, project_id):
        self.history.refresh()
        entry = next((row for row in self.recent_entries() if f"recent:{row['directory']}" == project_id), None)
        if not entry:
            entry = next((row for row in self.index.entries if f"index:{row['directory']}" == project_id), None)
        if not entry:
            return {"error": "unavailable"}
        directory = entry["directory"]
        real = os.path.realpath(directory)
        current = next((row for row in self.open_projects()
                        if row["directory"] == directory or (row["directory"] and os.path.realpath(row["directory"]) == real)), None)
        if current:
            self.history.touch(current)
            reply = {"workspace": current["workspace"]}
            if current.get("address"):
                reply["address"] = current["address"]
            return reply
        if not os.path.isdir(directory):
            return {"error": "missingFolder"}
        self.history.touch(entry)
        return {"directory": directory}

    def project_directories(self):
        return {row["directory"] for row in self.open_projects() if row["directory"]}

    def want_details(self):
        """Ask for git details of every project the switcher can show (it fills in a moment later)."""
        if self.settings["switcherGit"] == "never":
            return
        dirs = self.project_directories() | {row["directory"] for row in self.recent_entries()}
        self.gitd.want(sorted(dirs))

    def snapshot(self):
        projects = self.open_projects()
        open_dirs = {row["directory"] for row in projects if row["directory"]}
        seen_reals = {os.path.realpath(d) for d in open_dirs}
        aliases = self.state["aliases"]
        for row in self.recent_entries():
            d = row["directory"]
            real = os.path.realpath(d)
            if d in open_dirs or real in seen_reals:
                continue
            seen_reals.add(real)
            recent = project_row(row, 0, d, aliases, prefix="recent", group="recent")
            if self.slot_of(recent["_colorKey"]) is not None:
                recent["slot"] = self.slot_of(recent["_colorKey"])
            projects.append(recent)
        if self.details_on:
            for row in self.index.entries:
                d = row["directory"]
                real = row.get("_realPath") or os.path.realpath(d)
                if d in open_dirs or real in seen_reals:
                    continue
                if ignored_folder(d, self.settings["recentIgnore"]) or ignored_folder(real, self.settings["recentIgnore"]):
                    continue
                seen_reals.add(real)
                indexed = project_row(row, 0, d, aliases, prefix="index", group="projects")
                if self.slot_of(indexed["_colorKey"]) is not None:
                    indexed["slot"] = self.slot_of(indexed["_colorKey"])
                projects.append(indexed)
        sessions = self.agent_sessions()
        mixed = self.mixed_agents(sessions)
        ws_dirs = {}
        for row in projects:
            if row["workspace"] > 0 and row["directory"]:
                ws_dirs.setdefault(row["workspace"], set()).add(row["directory"])
        by_ws_dir = {ws: agent_by_directory(sessions, open_dirs, ws=ws, pid_workspace=self.pid_workspace)
                     for ws in set(ws_dirs) | set(self.results)}
        cmd_by_proj = self.commands.by_project(ws_dirs)
        git_on = self.settings["switcherGit"] != "never"
        rows = []
        for row in projects:
            d = row["directory"]
            ws = row["workspace"]
            extra = dict(self.gitd.get(d)) if d and git_on else {}
            if ws > 0 and d:
                a = by_ws_dir.get(ws, {}).get(d)
                if a:
                    extra.update(self.agent_fields(a, mixed))
                cmd_info = cmd_by_proj.get((ws, d))
                if cmd_info:
                    extra.update(cmd_info)
            rows.append({k: v for k, v in {**row, **extra}.items() if not k.startswith("_")})
        workspaces = self.holder.view()
        commands = self.commands.statuses()
        for w in workspaces:
            w.update(commands.get(w["id"], {}))
            dirs = [p["directory"] for p in (self.results.get(w["id"]) or {}).get("_projects", []) if p["directory"]]
            best = agent_of(dirs, by_ws_dir.get(w["id"], {}))
            if best["state"]:
                w.update(self.agent_fields(best, mixed))
        short, detail = self.claude_line()
        return {"v": 1, "probeMs": round(self.probe_ms, 1), "windows": self.windows,
                "workspaces": workspaces, "projects": rows, "iconOverrides": self.icons,
                "claude": short, "claudeDetail": detail, "others": others_line(self.others, self.agent_kinds),
                "settings": public_settings(self.settings),
                "hint": self.hint}

    def mixed_agents(self, sessions):
        return len(self.agent_kinds | {s.get("agent", "claude") for s in sessions}) > 1

    def agent_fields(self, status, mixed):
        kind = status.get("kind", "claude")
        label = self.rules["agents"].get(kind, {}).get("label", kind)
        return {"agent": status["state"], "agentTs": status["ts"], "agentMessage": status["message"],
                "agentName": status["name"], "agentLabel": label, "mixedAgents": mixed}

    def save(self):
        # Another monitor's bar may have handed out colours since we read the file: keep them.
        fresh = load_state(self.state_file)
        colors = self.state.setdefault("colors", {})
        for key, slot in fresh["colors"].items():
            colors.setdefault(key, slot)
        self.state["nextSlot"] = max(int(self.state.get("nextSlot") or 0), fresh["nextSlot"])
        save_state(self.state, self.state_file)
        self.state_mtime = self._mtime()


def explain_lines(snapshot):
    lines = []
    proc = title = native = 0
    for r in snapshot["workspaces"]:
        if not r.get("name"):
            continue
        layer = r.get("layer")
        proc += layer == "proc"
        title += layer == "title"
        native += layer == "terminal"
        more = f" +{r['more']}" if r.get("more") else ""
        lines.append(f"ws {r['id']:<2} {(r['name'] + more):<18} {r['source']:<8} {r.get('how', '')}")
        if r.get("overridden"):
            lines.append(f"      ↳ last used was {r['overridden']} (weak name), so the strong name won")
        if r.get("pending"):
            p = r["pending"]
            lines.append(f"      ↳ you focused another window: → “{p['name']}” in {max(0.0, p['due'] / 1000 - time.time()):.0f} s unless you go back")
    lines.append("")
    lines.append(f"probe {snapshot.get('probeMs', 0):.1f} ms · {snapshot.get('windows', 0)} windows · layer hits: proc {proc}, title {title}, native terminal {native}")
    if snapshot.get("claudeDetail"):
        lines.append(snapshot["claudeDetail"])
    if snapshot.get("others"):
        lines.append(snapshot["others"])
    if snapshot.get("hint"):
        lines.append(f"bar: Omarchy's workspace widget ({snapshot['hint']['id']}) is on the bar too; "
                     "`signpost.py swap` replaces it (undo: `signpost.py unswap`)")
    return lines


def portal_streams(run=subprocess.run, which=shutil.which):
    """How many screen-share streams the desktop portal has open right now: a number, or None when PipeWire
    is there but would not say.

    Hyprland's event socket reports a share when it starts and when it stops. It does not replay the start to
    a watcher that connects later, so the events alone cannot show a share that began before Signpost did.
    The portal publishes each share as a PipeWire node named xdph-streaming-…, and that outlasts the event.
    Without pw-dump there is nothing to ask, and only the events are left (0 here, with a warning at start)."""
    if not which("pw-dump"):
        return 0
    try:
        r = run(["pw-dump"], capture_output=True, text=True, timeout=4)
        objects = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if r.returncode != 0 or not isinstance(objects, list):
        return None
    count = 0
    for obj in objects:
        info = obj.get("info") if isinstance(obj, dict) else None
        props = info.get("props") if isinstance(info, dict) else None
        if isinstance(props, dict) and str(props.get("node.name") or "").startswith("xdph-streaming"):
            count += 1
    return count


class Daemon:
    TICK = 0.5   # how often the agent files and git lookups are checked
    STREAMS_EVERY = 5.0   # how often PipeWire is asked about screen shares
    STREAMS_RETRY = 2.0   # ...and how soon again when it would not say
    COALESCE = 0.04  # events that arrive together (a workspace switch sends several) are handled once
    EVENTS = {"openwindow", "closewindow", "movewindowv2", "movewindow", "workspacev2", "workspace", "focusedmonv2",
              "createworkspacev2", "destroyworkspacev2", "moveworkspacev2", "changefloatingmode", "configreloaded",
              "activewindowv2", "windowtitlev2"}

    def __init__(self, engine, streams=portal_streams):
        self.e = engine
        self.probe_streams = streams
        self.sel = selectors.DefaultSelector()
        self.sock = None
        self.buf = b""
        self.inbuf = b""
        self.active = None
        self.due = None
        self.refresh = set()
        now = time.monotonic()
        self.next_poll = now + engine.settings["pollSeconds"]
        self.next_full = now + engine.settings["fullSeconds"]
        self.casts = {}           # screen shares Hyprland has announced since we connected, by what is shared
        self.streams = 0          # shares the portal has open, as PipeWire last said; None: it would not say
        self.next_streams = now   # when to ask PipeWire again
        self.warned_streams = False
        self.last_out = None
        self.reconnect_at = 0.0
        self.next_tick = now + self.TICK
        self.notified = {}        # session → when it started waiting, for sessions already announced
        self.primed = False       # the first look at the agent files only records what was already waiting
        self.alerts = {}          # notification id → workspace its "Go to it" jumps to
        self.alert_times = {}     # notification id → when it was sent (monotonic)
        self.monitor = None       # gdbus watching for button presses on those notifications
        self.mon_buf = b""

    def sharing(self):
        """Is the screen possibly being shared? Yes when Hyprland announced a share, when the portal has a
        stream open, and when we cannot tell: showing names by mistake costs more than hiding them."""
        return bool(self.casts) or self.streams is None or self.streams > 0

    def check_streams(self, now):
        """Ask PipeWire about open shares. Catches one that began before we started, or while the event socket
        was down; both left the events with nothing to report."""
        found = self.probe_streams()
        if found is None:
            if not self.warned_streams:
                self.warned_streams = True
                warn("pw-dump did not answer; names stay hidden until screen-share detection works "
                     "(or set hideWhenSharing to false)")
            # Keep the last answer through a hiccup; before any answer, `streams` is still None.
            self.next_streams = now + self.STREAMS_RETRY
            return
        self.warned_streams = False
        self.streams = found
        self.next_streams = now + self.STREAMS_EVERY

    def connect(self):
        self.next_streams = 0.0   # events may have been missed while the socket was down: ask PipeWire now
        d = hypr_dir()
        if not d:
            return
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(os.path.join(d, ".socket2.sock"))
            s.setblocking(False)
            self.sock = s
            self.sel.register(s, selectors.EVENT_READ, "hypr")
        except OSError as e:
            warn(f"event socket: {e}; polling only")
            self.sock = None
            self.reconnect_at = time.monotonic() + 5

    def emit(self, force=False):
        """One line, only when something on the bar would change."""
        snap = self.e.snapshot()
        snap["sharing"] = self.sharing()
        key = json.dumps({k: v for k, v in snap.items() if k != "probeMs"}, sort_keys=True, ensure_ascii=False)
        if force or key != self.last_out:
            self.last_out = key
            self.send(snap)

    @staticmethod
    def send(message):
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def log(self, events):
        for ev in events:
            warn(ev)

    def schedule(self, addr=None):
        if addr:
            self.refresh.add(addr)
        if self.due is None:
            self.due = time.monotonic() + self.COALESCE

    def run(self):
        reap_stale_monitors()
        self.active = active_window()
        self.connect()
        try:
            self.sel.register(sys.stdin, selectors.EVENT_READ, "stdin")
        except (ValueError, OSError):
            pass
        self.log(self.e.recompute("all", force=True, active=self.active))
        # The first line the bar sees must already know about a share that is running: Hyprland does not
        # replay its start. Until PipeWire has answered, assume one is.
        self.streams = None
        self.check_streams(time.monotonic())
        self.emit(force=True)
        while True:
            now = time.monotonic()
            deadlines = [self.next_poll, self.next_full, self.next_tick, self.next_streams]
            if self.due is not None:
                deadlines.append(self.due)
            nd = self.e.holder.next_due()
            if nd is not None:
                deadlines.append(nd)
            for key, _ in self.sel.select(max(0.0, min(deadlines) - now)):
                if key.data == "hypr":
                    self.read_events()
                elif key.data == "stdin":
                    self.read_stdin()
                elif key.data == "notif":
                    self.read_alert_actions()
            now = time.monotonic()
            if self.sock is None and now >= self.reconnect_at:
                self.connect()
            if self.e.rules_changed():
                self.log(self.e.recompute("all", force=True, active=self.active))
            if self.e.state_changed():
                self.log(self.e.recompute(set(), force=True, active=self.active))
            if self.due is not None and now >= self.due:
                refresh, self.refresh, self.due = self.refresh, set(), None
                self.log(self.e.recompute(refresh, active=self.active))
            if now >= self.next_full:
                self.next_full = now + self.e.settings["fullSeconds"]
                self.next_poll = now + self.e.settings["pollSeconds"]
                self.log(self.e.recompute("all", active=self.active))
            elif now >= self.next_poll:
                self.next_poll = now + self.e.settings["pollSeconds"]
                self.log(self.e.recompute("poll", active=self.active))
            self.e.holder.tick(now)
            if now >= self.next_streams:
                self.check_streams(now)
            if now >= self.next_tick:
                self.next_tick = now + self.TICK
                self.tick_agents()
                if self.e.details_on:
                    self.e.want_details()
                if self.e.gitd.take_dirty():
                    self.last_out = None
            self.emit()

    # ---- agents that need you
    def tick_agents(self):
        self.expire_alerts()
        self.e.view.poll(self.e.claude_pids)
        if self.e.view.take_dirty():
            self.last_out = None
        sessions = self.e.agent_sessions()
        fresh = new_waits(sessions, self.notified)
        if not self.primed:     # waiting before we started: not news
            self.primed = True
            self.offer_swap()
            return
        fresh = [s for s in fresh if not s.get("old")]     # already waiting when Claude Code's list was first read
        if not fresh or not self.e.settings["alerts"] or self.sharing():
            return
        rows = {}
        rows_by_ws = {}
        for row in self.e.open_projects():
            if row["directory"]:
                rows.setdefault(row["directory"], row)
                rows_by_ws.setdefault((row["workspace"], row["directory"]), row)
        here = None
        mixed = self.e.mixed_agents(sessions)
        for s in fresh:
            d = session_directory(s["cwd"], rows)
            pid_ws = self.e.pid_workspace.get(s.get("pid"))
            row = rows_by_ws.get((pid_ws, d)) if pid_ws and d else None
            if row is None and pid_ws:
                row = next((r for (w, _), r in rows_by_ws.items() if w == pid_ws), None)
            if row is None:
                row = rows.get(d)
            ws = pid_ws or (row["workspace"] if row else 0)
            if ws and here is None:
                here = focused_workspace()
            if ws and ws == here:
                continue      # you are looking at it
            if not claim(self.e.runtime, f"wait:{session_key(s)}:{s['ts']}"):
                continue      # the bar on another monitor has announced it
            project = row["name"] if row else (os.path.basename(s["cwd"].rstrip("/")) if s["cwd"] else "")
            picture = alert_picture(row.get("slot"), row.get("monogram"), self.e.palette) if row else SIGNPOST_ICON
            self.notify(*wait_alert(s, project, ws, mixed), ws, picture)

    def offer_swap(self):
        """Once ever: Signpost sits on the bar next to Omarchy's workspace widget (two rows of numbers),
        so ask whether to replace it. Nothing changes without the Replace button."""
        if not self.e.hint or not claim(os.path.dirname(self.e.state_file), "swap-offer", keep=True):
            return
        self.notify("Signpost is on your bar twice over",
                    "Omarchy's workspace numbers are still there too. Replace them with Signpost? "
                    "(Undo any time: signpost.py unswap)", 0, SIGNPOST_ICON,
                    actions=[("replace", "Replace"), ("keep", "Keep both")])

    def run_hint(self, choice):
        """Replace (the installer's swap, with its restore record) or Keep both (never ask again)."""
        state_dir = os.path.dirname(self.e.state_file)
        if choice == "replace":
            try:
                warn(bar_swap("swap"))
            except (OSError, ValueError) as e:
                warn(f"swap: {e}")
        elif choice == "keep":
            keep_both(state_dir)
        self.e.hint = bar_hint(state_dir, self.e.shell_config)
        self.last_out = None

    def notify(self, title, body, ws, image="", actions=None):
        try:
            r = subprocess.run(notify_argv(title, body, bool(ws), image, actions), capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return
        nid = parse_notify_id(r.stdout)
        if nid and (ws or actions):
            if len(self.alerts) > 50:
                self.forget_alert(next(iter(self.alerts)))
            self.alerts[nid] = ws if ws else {"hint": True}
            self.alert_times[nid] = time.monotonic()
            self.watch_alerts()

    def watch_alerts(self):
        if self.monitor and self.monitor.poll() is None:
            return
        try:
            self.monitor = subprocess.Popen(
                MONITOR_ARGV,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            os.set_blocking(self.monitor.stdout.fileno(), False)
            self.sel.register(self.monitor.stdout, selectors.EVENT_READ, "notif")
        except (OSError, ValueError):
            self.monitor = None

    def read_alert_actions(self):
        try:
            data = os.read(self.monitor.stdout.fileno(), 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self.sel.unregister(self.monitor.stdout)
            self.monitor.stdout.close()
            self.monitor.wait()
            self.monitor, self.mon_buf = None, b""
            return
        self.mon_buf += data
        *lines, self.mon_buf = self.mon_buf.split(b"\n")
        for raw in lines:
            action = parse_action(raw.decode("utf-8", "replace"))
            if not action or action[0] not in self.alerts:
                continue      # another bar's alert, or not ours at all
            target = self.alerts[action[0]]
            if isinstance(target, dict):
                if action[1] in ("replace", "keep"):
                    self.forget_alert(action[0])
                    self.run_hint(action[1])
            elif action[1] in ("go", "default"):
                self.forget_alert(action[0])
                self.send({"jump": target})

    def forget_alert(self, nid):
        self.alerts.pop(nid, None)
        self.alert_times.pop(nid, None)

    def expire_alerts(self):
        """Old alerts can no longer jump; with none left there is nothing to watch for."""
        now = time.monotonic()
        for nid in [n for n, t in self.alert_times.items() if now - t > ALERT_LIFE]:
            self.forget_alert(nid)
        if not self.alerts:
            self.stop_monitor()

    def stop_monitor(self):
        mon, self.monitor, self.mon_buf = self.monitor, None, b""
        if not mon:
            return
        try:
            self.sel.unregister(mon.stdout)
        except (KeyError, ValueError, OSError):
            pass
        if mon.poll() is None:
            mon.terminate()
        try:
            mon.wait(timeout=2)
        except subprocess.TimeoutExpired:
            mon.kill()
            mon.wait()
        try:
            mon.stdout.close()
        except OSError:
            pass

    def close(self):
        self.stop_monitor()

    def read_events(self):
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self.sel.unregister(self.sock)
            self.sock.close()
            self.sock = None
            self.reconnect_at = time.monotonic() + 2
            return
        self.buf += data
        *lines, self.buf = self.buf.split(b"\n")
        for raw in lines:
            name, _, arg = raw.decode("utf-8", "replace").partition(">>")
            if name == "activewindowv2":
                self.active = ("0x" + arg if arg and not arg.startswith("0x") else arg) or None
            if name == "screencast":
                state, _, owner = arg.partition(",")
                count = self.casts.get(owner, 0)
                if state == "1":
                    self.casts[owner] = count + 1
                elif state == "0" and count > 1:
                    self.casts[owner] = count - 1
                elif state == "0":
                    self.casts.pop(owner, None)
            elif name == "windowtitlev2":
                addr = arg.split(",", 1)[0]
                self.schedule(addr if addr.startswith("0x") else "0x" + addr)  # look at that window again
            elif name in self.EVENTS:
                self.schedule()  # positions and focus only: cached facts are enough

    def read_stdin(self):
        try:
            data = os.read(sys.stdin.fileno(), 65536)
        except OSError:
            data = b""
        if not data:
            # No more commands (stdin closed, or /dev/null under another bar). Keep naming.
            self.sel.unregister(sys.stdin)
            return
        self.inbuf += data
        *lines, self.inbuf = self.inbuf.split(b"\n")
        for raw in lines:
            try:
                cmd = json.loads(raw)
            except ValueError:
                continue
            self.command(cmd)

    def command(self, cmd):
        """{"cmd":"settings","settings":{…}} · {"cmd":"refresh"} · {"cmd":"pin","ws":3,"name":"inbox"} · unpin · alias · unalias · capture"""
        op = cmd.get("cmd")
        if op == "settings":
            new = resolve_settings(cmd.get("settings"))
            if new != self.e.settings:
                index_changed = (new["projectRoots"] != self.e.settings["projectRoots"]
                                 or new["recentIgnore"] != self.e.settings["recentIgnore"])
                self.e.settings = new
                if index_changed:
                    self.e.index.invalidate()
                    if self.e.details_on:
                        self.e.refresh_index()
                self.log(self.e.recompute(set(), force=True, active=self.active))
        elif op == "refresh":
            self.e.reload_rules()
            self.log(self.e.recompute("all", force=True, active=self.active))
        elif op in ("pin", "unpin", "alias", "unalias"):
            ws = cmd.get("ws") or focused_workspace()
            warn(apply_command(self.e.state, {**cmd, "ws": ws}, self.e.results))
            self.e.save()
            self.log(self.e.recompute(set(), force=True, active=self.active))
        elif op == "capture":
            warn(f"capture saved {capture(self.e, cmd.get('ws') or focused_workspace())}")
        elif op == "details":
            self.e.details_on = bool(cmd.get("on"))
            if self.e.details_on:
                self.e.refresh_index()
                self.e.want_details()
        elif op == "palette":
            self.e.palette = clean_palette(cmd.get("tiles"))
        elif op in ("replace", "keepBoth"):
            self.run_hint("replace" if op == "replace" else "keep")
        elif op == "rememberProject":
            self.e.remember_project(cmd.get("id"))
        elif op == "selectRecent":
            self.log(self.e.recompute("all", active=self.active))
            self.send({"recentSelection": {"request": cmd.get("request"), **self.e.select_recent(cmd.get("id"))}})
        self.emit()


def capture(engine, ws):
    facts = [f for f in engine.probe.facts(refresh="all") if f.get("workspace") == ws]
    results = decide(facts, engine.state, engine.settings, engine.rules)
    r = results.get(ws) or {}
    fixture = {
        "name": f"captured ws {ws} on {time.strftime('%Y-%m-%d %H:%M')}",
        "note": "Contains window titles and folder paths. Check before sharing.",
        "signpost": VERSION, "settings": {k: engine.settings[k] for k in ("maxLength", "sources", "ignoreClasses")},
        "state": {"pins": {str(ws): engine.state["pins"][str(ws)]} if str(ws) in engine.state["pins"] else {}, "aliases": engine.state["aliases"]},
        "facts": facts,
        "expect": {str(ws): {"name": r.get("name"), "source": r.get("source"), "more": r.get("more", 0)}},
    }
    private_dir(CAPTURE_DIR)
    path = os.path.join(CAPTURE_DIR, f"ws{ws}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    write_private(path, json.dumps(fixture, indent=2, ensure_ascii=False))
    return path


# --------------------------------------------------------------------------- CLI

def swap_cli(cmd, args):
    """signpost.py swap|unswap [--config F] [--record F] [--defaults F] [--plugins D] [--no-reload]"""
    paths = {"--config": SHELL_CONFIG, "--record": RESTORE_FILE, "--defaults": OMARCHY_DEFAULTS, "--plugins": PLUGINS_DIR}
    rest = list(args)
    for flag in paths:
        if flag in rest:
            i = rest.index(flag)
            if i + 1 >= len(rest):
                warn(f"{flag} needs a value")
                return 2
            paths[flag] = os.path.abspath(os.path.expanduser(rest[i + 1]))
            del rest[i:i + 2]
    try:
        print(bar_swap("swap" if cmd == "swap" else "remove", paths["--config"], paths["--record"], paths["--defaults"],
                       paths["--plugins"], reload="--no-reload" not in rest))
    except (OSError, ValueError, KeyError, TypeError) as e:
        warn(f"{cmd}: {e}")
        return 1
    return 0


def main(argv):
    args = argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    if args[0] in ("-V", "--version"):
        print(VERSION)
        return 0
    cmd, rest = args[0], args[1:]
    if cmd == "hook":
        return hook_main(rest)
    if cmd == "shell-init":
        try:
            print(shell_init(rest[0] if len(rest) == 1 else ""), end="")
            return 0
        except ValueError as e:
            warn(str(e))
            return 2
    if cmd == "shell-record":
        return shell_record(rest)
    if cmd == "hooks":
        return hooks_cli(rest)
    if cmd == "doctor":
        return doctor_cli()
    if cmd in ("swap", "unswap"):
        return swap_cli(cmd, rest)

    def opt(name, default=None):
        if name in rest:
            i = rest.index(name)
            val = rest[i + 1] if i + 1 < len(rest) else default
            del rest[i:i + 2]
            return val
        return default

    settings = {}
    raw = opt("--settings")
    if raw:
        try:
            settings = json.loads(raw)
        except ValueError:
            warn("--settings is not JSON; using defaults")
    ws_arg = opt("--ws")
    engine = Engine(settings)
    if cmd == "watch":
        if not shutil.which("pw-dump"):
            warn("pw-dump not found: a screen share that began before Signpost started cannot be detected "
                 "(install PipeWire's command-line tools)")
        daemon = Daemon(engine)
        # The widget stops us with SIGTERM. Leave through the normal door so the watcher process goes too.
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda *_: sys.exit(0))
        try:
            return daemon.run()
        except KeyboardInterrupt:
            return 0
        finally:
            daemon.close()
    if cmd in ("once", "explain"):
        engine.recompute(force=True)
        # a second look so agent CPU use (busy / most recently used) has something to compare with
        time.sleep(0.25)
        engine.recompute(force=True)
        snap = engine.snapshot()
        if cmd == "once" and "--json" in rest:
            print(json.dumps(snap, indent=2, ensure_ascii=False))
        elif cmd == "once":
            for r in snap["workspaces"]:
                if r.get("name"):
                    print(f"{r['id']:>2}  {r['name']}{' +' + str(r['more']) if r.get('more') else ''}")
        else:
            print("\n".join(explain_lines(snap)))
        return 0
    ws = int(ws_arg) if ws_arg else focused_workspace()
    if cmd == "facts":
        facts = engine.probe.facts()
        if ws_arg:
            facts = [f for f in facts if f.get("workspace") == ws]
        print(json.dumps(facts, indent=2, ensure_ascii=False))
        return 0
    if cmd == "capture":
        print(capture(engine, ws))
        return 0
    if cmd in ("pin", "unpin", "alias", "unalias"):
        engine.recompute(force=True)
        msg = apply_command(engine.state, {"cmd": cmd, "ws": ws, "name": " ".join(rest)}, engine.results)
        engine.save()
        print(msg)
        return 0
    warn(f"unknown command {cmd!r}; try --help")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
