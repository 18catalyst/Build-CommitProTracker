#!/usr/bin/env python3
"""
Build & Commit Pro Tracker: token usage and API-equivalent cost for Claude Code, Codex,
Hermes Agent, Gemini CLI, OpenCode and Aider, plus Claude service status, usage
alerts and a crypto ticker, in one local HTML page. Python 3.9+, stdlib only.
Works on macOS, Linux and Windows.

  python3 usage.py                      collect, rebuild dashboard, open it
  python3 usage.py --no-open            collect + rebuild only
  python3 usage.py --install            refresh automatically (every refresh_minutes)
  python3 usage.py --uninstall          stop the automatic refresh
  python3 usage.py --settings           show current settings and where they live
  python3 usage.py --set KEY=VALUE      change a setting, e.g. --set currency=GBP
  python3 usage.py --unset KEY          put a setting back to its default
  python3 usage.py --cmc-key KEY        save a free CoinMarketCap key (used first for prices)
  python3 usage.py --coingecko-key KEY  save a free CoinGecko demo key
  python3 usage.py --test-alert         send a test desktop notification
  python3 usage.py --export [FOLDER]    write every request and commit to CSV, plus a JSON summary
  python3 usage.py --install-menubar    optional: add the menu bar meter to SwiftBar/xbar (macOS) or Argos (Linux)
  python3 usage.py --menubar            print the menu bar output (used by that plugin)
  python3 usage.py --offline            skip status and price fetches
  python3 usage.py --inspect-hermes     print Hermes DB tables/columns (debugging)
  python3 usage.py --version

Data lives in ~/.ai-usage/ (usage.db, dashboard.html, config.json). Nothing is
sent anywhere except the status and price requests.
"""
import argparse, copy, json, os, platform, re, shlex, shutil, sqlite3, statistics, subprocess, sys, tempfile, time, urllib.error, urllib.request, webbrowser
from datetime import datetime, timedelta
from pathlib import Path

VERSION = "2.9.6"  # bump on every change so the page and Terminal show which copy is running

HOME = Path.home()
DATA_DIR = HOME / ".ai-usage"
DB_PATH = DATA_DIR / "usage.db"
OUT_HTML = DATA_DIR / "dashboard.html"
CONFIG_PATH = DATA_DIR / "config.json"

CODEX_DIR = Path(os.environ.get("CODEX_HOME", HOME / ".codex")) / "sessions"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", HOME / ".hermes"))
# Claude Code's own logs, plus the copies it keeps inside Hermes' sandboxes when Hermes runs it there
CLAUDE_DIRS = [HOME / ".claude" / "projects", HOME / ".config" / "claude" / "projects",
               *sorted(HERMES_HOME.glob("sandboxes/*/*/home/.claude/projects"))]
OPENCODE_DIR = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share") / "opencode"


def gemini_dirs():
    env = os.environ.get("GEMINI_DATA_DIR")
    return [Path(p.strip()).expanduser() for p in env.split(",") if p.strip()] if env else [HOME / ".gemini" / "tmp"]


STATUS_API = "https://status.claude.com/api/v2"
PLIST_LABEL = "com.ai-usage.dashboard"
PLIST_PATH = HOME / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"

SOURCES = ["Claude Code", "Codex", "Hermes", "Gemini CLI", "OpenCode", "Aider"]
WINDOW_5H = 5 * 3600
WEEK = 7 * 86400
LIMIT_RE = re.compile(r"usage limit|limit reached|hit your .{0,20}limit|limit will reset", re.I)

# ---------- settings ----------
DEFAULT_SETTINGS = {
    "currency": "USD",                      # crypto prices: USD, GBP, EUR, JPY, ...
    "coins": ["SOL", "BTC", "ETH", "XMR", "BNB", "XRP", "LINK"],   # symbols; use "SYM:coingecko-id" for unlisted coins
    "show_prices": True,
    "screenshot_mode": False,               # replace project/repo names with "Project A", "Project B", ...
    "hidden_tools": [],                     # tools hidden on the dashboard by default, e.g. ["Hermes"]
    "refresh_minutes": 15,                  # background refresh (re-run --install after changing) and page reload
    "alerts": {
        "enabled": True,
        "claude_5h_percent": 85,            # % of your estimated Claude 5h limit (needs one past limit hit)
        "codex_percent": 85,                # % of Codex's 5h or weekly limit
        "limit_hits": True,                 # when you actually hit a limit
        "incidents": True,                  # new Claude incidents
    },
    "plans": {},                            # monthly plan cost in USD per tool, e.g. {"Claude Code": 20, "Codex": 20}
    "prices": {},                           # model price overrides, USD per 1M tokens: {"model-name": [input, output, cache_write, cache_read]}
    "theme": {
        "accent": "#ff6a13",
        "edge": "rgba(129,140,248,.38)",    # panel border colour
        "panel_opacity": 0.05,              # 0 = fully see-through, 1 = solid
        "animate_background": True,
        "colors": {},                       # per-tool colours, e.g. {"Codex": "#e6e6e6"}
    },
    "git": {
        "enabled": True,
        "author": "me",                     # default view: "me" = commits by the user.email set in each repo, "all" = everyone's
        "extra_repos": [],                  # more repos to include, e.g. ["~/code/site"]
    },
    "aider_dirs": ["~/code", "~/projects", "~/dev", "~/src", "~/repos", "~/Documents/GitHub", "~/tools"],
}
PROJECT_DIRS = set()   # folders the AI tools ran in; used to find git repos
HERMES_TRACKED = [0]   # Hermes sessions/models seen on the last read (for the summary line)
SOURCE_COLORS = {"Claude Code": "#ff6a13", "Codex": "#e6e6e6", "Hermes": "#8a8a8a",
                 "Gemini CLI": "#60a5fa", "OpenCode": "#2dd4bf", "Aider": "#f472b6"}
SECRET_KEYS = ("cmc_key", "coingecko_key")


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    DATA_DIR.mkdir(exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def settings():
    """Defaults overlaid with config.json (keys included)."""
    return _merge(DEFAULT_SETTINGS, load_config())


def _parse_value(key, raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    if key.split(".")[-1] in ("coins", "aider_dirs"):
        return [x.strip() for x in raw.split(",") if x.strip()]
    if raw.lower() in ("true", "on", "yes"):
        return True
    if raw.lower() in ("false", "off", "no"):
        return False
    return raw


def set_setting(expr):
    if "=" not in expr:
        sys.exit("Use --set key=value, e.g. --set currency=GBP or --set alerts.codex_percent=90")
    key, raw = (s.strip() for s in expr.split("=", 1))
    cfg, node = load_config(), None
    node = cfg
    parts = key.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            sys.exit(f"{key}: '{p}' isn't a group of settings")
    value = _parse_value(key, raw)
    if parts[0] == "currency" and isinstance(value, str):
        value = value.upper()
    if parts[0] == "coins" and isinstance(value, list):
        value = [str(c).upper() if ":" not in str(c) else str(c).split(":")[0].upper() + ":" + str(c).split(":", 1)[1] for c in value]
    node[parts[-1]] = value
    save_config(cfg)
    print(f"Set {key} = {json.dumps(value)}")
    if parts[0] == "refresh_minutes":
        print("Run --install again so the background refresh uses the new interval.")


def unset_setting(key):
    cfg = load_config()
    node, parts = cfg, key.split(".")
    for p in parts[:-1]:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            break
    if isinstance(node, dict) and parts[-1] in node:
        del node[parts[-1]]
        save_config(cfg)
        print(f"{key} reset to default.")
    else:
        print(f"{key} wasn't set.")


def show_settings():
    s = settings()
    for k in SECRET_KEYS:
        if s.get(k):
            s[k] = s[k][:4] + "…" + s[k][-4:] if len(s[k]) > 10 else "(saved)"
    print(f"Settings file: {CONFIG_PATH}\n")
    print(json.dumps(s, indent=2))
    print("\nChange with --set key=value (nested keys use dots, e.g. --set theme.accent=#22d3ee).")


# ---------- helpers ----------
def parse_ts(v):
    """ISO string or epoch (s or ms) -> epoch seconds, or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else float(v)
    s = str(v).strip()
    try:
        f = float(s)
        return f / 1000 if f > 1e12 else f
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def n(x):
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return 0


def read_jsonl(path):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for i, line in enumerate(fh):
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


def text_of(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def open_sqlite_ro(path):
    """Read-only connection; falls back to a temp copy if the app holds the DB in WAL mode."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        return con
    except sqlite3.Error:
        tmp = Path(tempfile.mkdtemp()) / Path(path).name
        shutil.copy2(path, tmp)
        for ext in ("-wal", "-shm"):
            if Path(str(path) + ext).exists():
                shutil.copy2(str(path) + ext, str(tmp) + ext)
        return sqlite3.connect(tmp)


FETCH_ERRORS = []
WARNINGS = []


def fetch_json(url, headers=None):
    """urllib first; fall back to curl (some APIs block Python's client, some Macs lack CA certs)."""
    host = url.split("/")[2]
    headers = headers or {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"ai-usage-dashboard/{VERSION}", "Accept": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        err = f"HTTP {e.code}"
        if e.code not in (401, 403, 429):
            FETCH_ERRORS.append(f"{host}: {err}")
            return None
    except Exception as e:
        err = type(e).__name__
    try:
        hdr_args = [a for k, v in headers.items() for a in ("-H", f"{k}: {v}")]
        out = subprocess.run(["curl", "-sSL", "--max-time", "10", "-H", "Accept: application/json", *hdr_args,
                              "-w", "\\n%{http_code}", url], capture_output=True, text=True, timeout=14)
        body, _, code = out.stdout.rpartition("\n")
        if out.returncode == 0 and code.startswith("2"):
            return json.loads(body)
        FETCH_ERRORS.append(f"{host}: python {err}, curl {code or out.stderr.strip()[:80]}")
    except Exception as e:
        FETCH_ERRORS.append(f"{host}: python {err}, curl failed ({type(e).__name__})")
    return None


# ---------- pricing ----------
# (pattern on the normalised model name, input, output, cache_write, cache_read) in USD per 1M tokens.
# List prices checked Sep 2026: platform.claude.com/docs/en/about-claude/pricing, OpenAI API pricing,
# ai.google.dev/gemini-api/docs/pricing. First match wins, so specific names come before general ones.
# Override or add models with --set 'prices.my-model=[in,out,cache_write,cache_read]'.
PRICING = [
    # Anthropic
    (r"(fable|mythos)-5-1", 10, 50, 12.5, 0.25),
    (r"(fable|mythos)-5", 10, 50, 12.5, 1.0),
    (r"opus-5-5", 4, 20, 5, 0.20),
    (r"opus-5|opus-4-[5-9]", 5, 25, 6.25, 0.50),
    (r"opus-4|opus-3|3-opus", 15, 75, 18.75, 1.50),
    (r"sonnet-5", 2, 10, 2.5, 0.20),
    (r"sonnet-4|sonnet-3-7|3-7-sonnet|3-5-sonnet|sonnet-3-5", 3, 15, 3.75, 0.30),
    (r"haiku-4", 1, 5, 1.25, 0.10),
    (r"haiku-3-5|3-5-haiku", 0.8, 4, 1, 0.08),
    (r"haiku", 0.25, 1.25, 0.30, 0.03),
    # OpenAI
    (r"gpt-5-6-sol", 5, 30, 0, 0.50),
    (r"gpt-5-6-terra", 2, 12, 0, 0.20),
    (r"gpt-5-6-luna", 0.2, 1.2, 0, 0.02),
    (r"gpt-5-[45]-pro", 30, 180, 0, 30),
    (r"gpt-5-5", 5, 30, 0, 0.50),
    (r"gpt-5-4-mini", 0.75, 4.5, 0, 0.075),
    (r"gpt-5-4-nano", 0.2, 1.25, 0, 0.02),
    (r"gpt-5-4", 2.5, 15, 0, 0.25),
    (r"gpt-5-[23]", 1.75, 14, 0, 0.175),
    (r"gpt-5(-1)?-codex-mini", 0.25, 2, 0, 0.025),
    (r"codex-mini", 1.5, 6, 0, 0.375),
    (r"gpt-5(-1)?-mini", 0.25, 2, 0, 0.025),
    (r"gpt-5(-1)?-nano", 0.05, 0.4, 0, 0.005),
    (r"gpt-5", 1.25, 10, 0, 0.125),
    (r"(^|-)o4-mini", 1.1, 4.4, 0, 0.275),
    (r"(^|-)o3", 2, 8, 0, 0.50),
    (r"gpt-4-1-mini", 0.4, 1.6, 0, 0.10),
    (r"gpt-4-1", 2, 8, 0, 0.50),
    (r"gpt-4o-mini", 0.15, 0.6, 0, 0.075),
    (r"gpt-4o", 2.5, 10, 0, 1.25),
    # Google (<=200k-token prompt tier)
    (r"gemini-3-[6-9]-flash", 0.75, 3.75, 0, 0.075),
    (r"gemini-3-5-flash-lite", 0.3, 2.5, 0, 0.30),
    (r"gemini-3-5-flash", 1.5, 9, 0, 0.15),
    (r"gemini-3-1-flash-lite", 0.25, 1.5, 0, 0.025),
    (r"gemini-3(-1)?-pro", 2, 12, 0, 0.20),
    (r"gemini-3-flash", 0.5, 3, 0, 0.05),
    (r"gemini-2-5-pro", 1.25, 10, 0, 0.125),
    (r"gemini-2-5-flash-lite", 0.1, 0.4, 0, 0.01),
    (r"gemini-2-5-flash", 0.3, 2.5, 0, 0.03),
]


def norm_model(m):
    m = str(m or "").lower().split("/")[-1]
    return re.sub(r"[._:\s]+", "-", m)


class Pricer:
    def __init__(self, overrides):
        self.rules = [(re.compile(re.escape(norm_model(k))), *[float(x) for x in v]) for k, v in (overrides or {}).items()
                      if isinstance(v, (list, tuple)) and len(v) == 4]
        self.rules += [(re.compile(p), *v) for p, *v in PRICING]
        self.cache = {}

    def rate(self, model):
        key = norm_model(model)
        if key not in self.cache:
            self.cache[key] = next((r[1:] for r in self.rules if r[0].search(key)), None)
        return self.cache[key]

    def cost(self, model, i, o, cw, cr, explicit=None):
        if explicit is not None:
            return float(explicit)
        r = self.rate(model)
        if r is None:
            return None
        pi, po, pw, pr = r
        return (i * pi + o * po + cw * (pw or pi) + cr * pr) / 1e6


# ---------- collectors ----------
_PNAME = {}


def project_name(path, fallback):
    """Repo name if the folder is inside a git repo, else the folder name."""
    if not path:
        return fallback
    if path not in _PNAME:
        root = git_root(path)
        _PNAME[path] = root.name if root else (Path(str(path).rstrip("/\\")).name or fallback)
    return _PNAME[path]
# row = (id, ts, source, model, input, output, cache_write, cache_read, project, session, cost_or_None)
def claude_project(dirname):
    name = re.sub(r"^(?:[A-Za-z]-)?-(?:Users|home)-[^-]+-", "", dirname).lstrip("-")
    return name or dirname


def collect_claude():
    rows, events = [], []
    for base in CLAUDE_DIRS:
        if not base.exists():
            continue
        for f in base.glob("**/*.jsonl"):
            rel = f.relative_to(base).parts
            project = claude_project(rel[0]) if len(rel) > 1 else "?"
            session, named = f.stem, False
            for i, e in read_jsonl(f):
                if e.get("cwd"):
                    PROJECT_DIRS.add(e["cwd"])
                    if not named:
                        project, named = project_name(e["cwd"], project), True
                if e.get("type") != "assistant":
                    continue
                m = e.get("message") or {}
                model = m.get("model") or "?"
                ts = parse_ts(e.get("timestamp"))
                if (e.get("isApiErrorMessage") or model == "<synthetic>") and ts:
                    txt = text_of(m)
                    if LIMIT_RE.search(txt):
                        reset = re.search(r"\|(\d{10})\b", txt)
                        events.append((f"limit:{session}:{i}", ts, "limit",
                                       json.dumps({"source": "Claude Code", "reset": int(reset.group(1)) if reset else None,
                                                   "text": txt[:160]})))
                    continue
                u = m.get("usage")
                if not u:
                    continue
                key = f"claude:{m.get('id')}:{e.get('requestId')}" if m.get("id") else f"claude:{f.name}:{i}"
                used = [x.get("name") for x in (m.get("content") or []) if isinstance(x, dict) and x.get("type") == "tool_use" and x.get("name")]
                rows.append((key, ts, "Claude Code", model,
                             n(u.get("input_tokens")), n(u.get("output_tokens")),
                             n(u.get("cache_creation_input_tokens")), n(u.get("cache_read_input_tokens")),
                             project, session, None, ",".join(used) or None))
    return rows, events


def collect_codex():
    rows, events, latest = [], [], None
    if not CODEX_DIR.exists():
        return rows, events, None
    for f in CODEX_DIR.glob("**/*.jsonl"):
        model, cwd = "?", None
        for i, e in read_jsonl(f):
            p = e.get("payload") or {}
            if e.get("type") in ("turn_context", "session_meta"):
                model = p.get("model") or model
                cwd = p.get("cwd") or cwd
                if cwd:
                    PROJECT_DIRS.add(cwd)
            if e.get("type") != "event_msg" or p.get("type") != "token_count":
                continue
            ts = parse_ts(e.get("timestamp"))
            rl = p.get("rate_limits")
            if rl and ts:
                if latest is None or ts > latest["ts"]:
                    latest = {"ts": ts, "limits": rl}
                for k in ("primary", "secondary"):
                    if (rl.get(k) or {}).get("used_percent", 0) >= 100:
                        events.append((f"codexlimit:{k}:{int(ts // 3600)}", ts, "limit",
                                       json.dumps({"source": "Codex", "reset": None, "text": f"{k} limit at 100%"})))
            last = (p.get("info") or {}).get("last_token_usage")
            if not last:
                continue
            cached = n(last.get("cached_input_tokens"))
            rows.append((f"codex:{f.name}:{i}", ts, "Codex", model,
                         max(n(last.get("input_tokens")) - cached, 0), n(last.get("output_tokens")),
                         0, cached, project_name(cwd, "codex"), f.stem, None))
    return rows, events, latest


def hermes_db():
    for name in ("state.db", "hermes.db", "sessions.db"):
        p = HERMES_HOME / name
        if p.exists():
            return p
    return None


def _hermes_cost(actual, estimated):
    """Hermes' own cost figure, if it has a real one. $0 usually means a subscription or free tier, which isn't the
    API value, so return None and let the price table value it (free models with no list price show as '—')."""
    for v in (actual, estimated):
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def collect_hermes():
    """Hermes Agent keeps running totals per session (and per model). We record how much each total has grown since
    the last refresh, timestamped at the session's last activity, so long-running chats land on the right day and
    nothing is counted twice. Falls back to a best-effort reader for unknown database layouts."""
    path, rows = hermes_db(), []
    if not path:
        return rows
    src = open_sqlite_ro(path)
    try:
        tables = {t for (t,) in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cols = lambda t: {r[1] for r in src.execute(f'PRAGMA table_info("{t}")')}
        snaps = []  # (key, ts, model, input, output, cache_write, cache_read, cost, project, session)
        if "sessions" in tables:
            sc = cols("sessions")
            pick = lambda *names: next((c for c in names if c in sc), None)
            q = [pick("id"), pick("cwd"), pick("git_repo_root"), pick("title"), pick("last_activity_at", "ended_at", "started_at"), pick("model"),
                 pick("input_tokens"), pick("output_tokens"), pick("cache_write_tokens"), pick("cache_read_tokens"),
                 pick("actual_cost_usd"), pick("estimated_cost_usd")]
            sessions = {}
            for r in src.execute("SELECT " + ", ".join(f'"{c}"' if c else "NULL" for c in q) + " FROM sessions"):
                sid, cwd, root, title, last, model, i_, o_, w_, cr_, act, est = r
                where = root or cwd
                if where:
                    PROJECT_DIRS.add(where)
                sessions[sid] = {"project": project_name(where, None) if where else (str(title)[:40] if title else "hermes"),
                                 "last": parse_ts(last), "row": (model, i_, o_, w_, cr_, act, est)}
        else:
            sessions = {}
        if "session_model_usage" in tables and src.execute("SELECT 1 FROM session_model_usage LIMIT 1").fetchone():
            for sid, model, i_, o_, cr_, cw_, act, est, first, last in src.execute(
                    "SELECT session_id, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
                    "actual_cost_usd, estimated_cost_usd, first_seen, last_seen FROM session_model_usage"):
                s = sessions.get(sid, {})
                ts = parse_ts(last) or s.get("last") or parse_ts(first)
                snaps.append((f"{sid}|{model}", ts, model or "?", n(i_), n(o_), n(cw_), n(cr_), _hermes_cost(act, est),
                              s.get("project", "hermes"), str(sid)))
        elif sessions:
            for sid, s in sessions.items():
                model, i_, o_, w_, cr_, act, est = s["row"]
                snaps.append((f"{sid}|{model}", s["last"], model or "?", n(i_), n(o_), n(w_), n(cr_), _hermes_cost(act, est),
                              s["project"], str(sid)))
        else:  # unknown layout: best effort, one table only
            for t in sorted(tables):
                c = cols(t)
                if {"input_tokens", "output_tokens"} <= c or {"prompt_tokens", "completion_tokens"} <= c:
                    i_c = "input_tokens" if "input_tokens" in c else "prompt_tokens"
                    o_c = "output_tokens" if "output_tokens" in c else "completion_tokens"
                    t_c = next((x for x in ("last_seen", "last_activity_at", "ended_at", "updated_at", "timestamp", "created_at", "started_at") if x in c), None)
                    m_c = "model" if "model" in c else None
                    for rowid, ts, model, i_, o_ in src.execute(
                            f'SELECT rowid, {t_c or "NULL"}, {m_c or "NULL"}, "{i_c}", "{o_c}" FROM "{t}"'):
                        snaps.append((f"{t}:{rowid}", parse_ts(ts), model or "?", n(i_), n(o_), 0, 0, None, "hermes", f"{t}:{rowid}"))
                    break
    finally:
        src.close()

    HERMES_TRACKED[0] = len(snaps)
    # turn running totals into growth since the last refresh
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS hermes_seen(key TEXT PRIMARY KEY, input INT, output INT, cache_write INT, cache_read INT, cost REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    if not con.execute("SELECT 1 FROM meta WHERE key='hermes_v3'").fetchone():
        # rebuild Hermes history once: v2.9.1 and earlier double-counted and dated at session start,
        # v2.9.2-2.9.4 took Hermes' $0 subscription costs literally
        try:
            con.execute("DELETE FROM usage WHERE source='Hermes'")
        except sqlite3.Error:
            pass
        con.execute("DELETE FROM hermes_seen")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('hermes_v3', '1')")
    seen = {k: v for k, *v in con.execute("SELECT key, input, output, cache_write, cache_read, cost FROM hermes_seen")}
    now = time.time()
    for key, ts, model, i_, o_, cw_, cr_, cost, project, sid in snaps:
        prev = seen.get(key, [0, 0, 0, 0, 0.0])
        cur = [i_, o_, cw_, cr_, cost or 0.0]
        if any(c < p for c, p in zip(cur[:4], prev[:4])):   # totals went down (session reset): start over
            prev = [0, 0, 0, 0, 0.0]
        d = [c - p for c, p in zip(cur, prev)]
        if not any(d[:4]):
            continue
        dcost = round(d[4], 8) if cost is not None else None
        rows.append((f"hermes2:{key}:{i_}:{o_}:{cr_}", min(ts or now, now), "Hermes", model, d[0], d[1], d[2], d[3],
                     project, sid, dcost))
        con.execute("INSERT OR REPLACE INTO hermes_seen VALUES (?,?,?,?,?,?)", (key, *cur))
    con.commit()
    con.close()
    return rows


def _gemini_project(tmpdir):
    for name in (".project_root", "project_root"):
        p = tmpdir / name
        if p.exists():
            try:
                root = p.read_text(encoding="utf-8").strip()
                if root:
                    PROJECT_DIRS.add(root)
                return project_name(root, tmpdir.name[:8])
            except OSError:
                pass
    return tmpdir.name[:8] if re.fullmatch(r"[0-9a-f]{16,}", tmpdir.name) else tmpdir.name


def collect_gemini():
    """Gemini CLI chat files: ~/.gemini/tmp/<project>/chats/*.json(l). Messages can appear twice; keep the fullest."""
    rows = []
    for base in gemini_dirs():
        if not base.exists():
            continue
        for f in list(base.glob("*/chats/*.json")) + list(base.glob("*/chats/*.jsonl")):
            project, session, start, msgs = _gemini_project(f.parent.parent), f.stem, None, []
            if f.suffix == ".json":
                try:
                    doc = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(doc, dict):
                    msgs, session, start = doc.get("messages") or [], doc.get("sessionId") or f.stem, parse_ts(doc.get("startTime"))
                elif isinstance(doc, list):
                    msgs = doc
            else:
                msgs = [e for _, e in read_jsonl(f)]
            best = {}
            for i, m in enumerate(msgs):
                if not isinstance(m, dict):
                    continue
                if isinstance(m.get("message"), dict) and "tokens" not in m:
                    m = {**m["message"], **{k: v for k, v in m.items() if k != "message"}}
                t = m.get("tokens")
                if not isinstance(t, dict):
                    continue
                mid = str(m.get("id") or i)
                total = n(t.get("total")) or sum(n(t.get(k)) for k in ("input", "output", "thoughts", "tool"))
                if mid not in best or total >= best[mid][0]:
                    best[mid] = (total, m, t)
            for mid, (_, m, t) in best.items():
                ts = parse_ts(m.get("timestamp")) or start
                if ts is None:
                    continue
                cached = n(t.get("cached"))
                rows.append((f"gemini:{session}:{mid}", ts, "Gemini CLI", m.get("model") or "gemini",
                             max(n(t.get("input")) - cached, 0) + n(t.get("tool")), n(t.get("output")) + n(t.get("thoughts")),
                             0, cached, project, session, None))
    return rows


def collect_opencode():
    """OpenCode: ~/.local/share/opencode/opencode.db (v1.14+) or the older storage/message/*.json files."""
    rows = []

    def add(d, mid, sid, directory):
        if not isinstance(d, dict) or d.get("role") != "assistant":
            return
        t = d.get("tokens") or {}
        if not t:
            return
        ts = parse_ts((d.get("time") or {}).get("created"))
        if ts is None:
            return
        cache = t.get("cache") or {}
        cost = d.get("cost")
        directory = directory or (d.get("path") or {}).get("cwd") or (d.get("path") or {}).get("root")
        if directory:
            PROJECT_DIRS.add(directory)
        rows.append((f"opencode:{mid}", ts, "OpenCode", d.get("modelID") or "?",
                     n(t.get("input")), n(t.get("output")) + n(t.get("reasoning")), n(cache.get("write")), n(cache.get("read")),
                     project_name(directory, "opencode"), sid or "?",
                     float(cost) if isinstance(cost, (int, float)) and cost > 0 else None))

    db = OPENCODE_DIR / "opencode.db"
    if db.exists():
        try:
            con = open_sqlite_ro(db)
            try:
                dirs = {}
                try:
                    dirs = dict(con.execute("SELECT id, directory FROM session"))
                except sqlite3.Error:
                    pass
                for mid, sid, data in con.execute("SELECT id, session_id, data FROM message"):
                    try:
                        add(json.loads(data), mid, sid, dirs.get(sid))
                    except (json.JSONDecodeError, TypeError):
                        continue
            finally:
                con.close()
        except sqlite3.Error as e:
            WARNINGS.append(f"OpenCode database not readable ({e}); skipped")
    legacy = OPENCODE_DIR / "storage" / "message"
    if legacy.exists():
        for f in legacy.glob("*/*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
            except (json.JSONDecodeError, OSError):
                continue
            add(d, d.get("id") or f.stem, d.get("sessionID") or f.parent.name, None)
    return rows


AIDER_START = re.compile(r"^# aider chat started at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
AIDER_MODEL = re.compile(r"^>\s*(?:Main model|Model):\s*(\S+)")
AIDER_TOKENS = re.compile(
    r"Tokens:\s*([\d.,]+[kKmM]?)\s*sent"
    r"(?:,\s*([\d.,]+[kKmM]?)\s*cache write)?(?:,\s*([\d.,]+[kKmM]?)\s*cache hit)?"
    r",\s*([\d.,]+[kKmM]?)\s*received\.(?:\s*Cost:\s*\$([\d.,]+)\s*message)?")
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "Library", "build", "dist", ".cache", "target", ".next"}


def _knum(s):
    if not s:
        return 0
    s = s.replace(",", "")
    mult = {"k": 1e3, "m": 1e6}.get(s[-1].lower(), 1)
    try:
        return int(float(s.rstrip("kKmM")) * mult)
    except ValueError:
        return 0


def aider_files(dirs, max_depth=4, max_dirs=20000):
    for root in dirs:
        root = Path(os.path.expanduser(root))
        if not root.is_dir():
            continue
        seen, base_depth = 0, len(root.parts)
        for cur, subdirs, files in os.walk(root):
            seen += 1
            if seen > max_dirs:
                break
            if ".aider.chat.history.md" in files:
                yield Path(cur) / ".aider.chat.history.md"
            if len(Path(cur).parts) - base_depth >= max_depth:
                subdirs[:] = []
            else:
                subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS and not d.startswith(".")]


def collect_aider(dirs):
    """Aider keeps a .aider.chat.history.md in each project; token lines carry Aider's own cost figure."""
    rows = []
    for f in aider_files(dirs):
        project, start, model = f.parent.name, None, "?"
        PROJECT_DIRS.add(str(f.parent))
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for ln, line in enumerate(lines):
            m = AIDER_START.match(line)
            if m:
                start, model = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp(), "?"
                continue
            m = AIDER_MODEL.match(line)
            if m:
                model = m.group(1)
                continue
            m = AIDER_TOKENS.search(line)
            if m and start:
                sent, cw, hit, recv = (_knum(m.group(k)) for k in (1, 2, 3, 4))
                cost = float(m.group(5).replace(",", "")) if m.group(5) else None
                rows.append((f"aider:{f}:{ln}", start + ln * 0.001, "Aider", model, max(sent - cw - hit, 0), recv, cw, hit,
                             project, f"{project}:{int(start)}", cost))
    return rows


AI_COAUTHORS = [("Claude", r"claude|anthropic"), ("Codex", r"codex|openai|chatgpt"), ("Gemini", r"gemini|jules"),
                ("Aider", r"aider"), ("OpenCode", r"opencode"), ("Copilot", r"copilot"), ("Cursor", r"cursor")]
COAUTHOR_RE = re.compile(r"^\s*co-authored-by:\s*(.+)$", re.I | re.M)


def git_root(path):
    try:
        p = Path(os.path.expanduser(str(path))).resolve()
    except (OSError, RuntimeError):
        return None
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


def _git(repo, *args, timeout=30):
    # Read-only and inert: a cloned repo's own config can't make these commands run programs
    # (no pager, fsmonitor, signature checks, text conversion or external diff) or write lock files.
    cmd = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "log.showSignature=false",
           "-c", "diff.external=", "-c", "core.pager=cat", "-C", str(repo), *args]
    out = subprocess.run(cmd, capture_output=True, timeout=timeout, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None


def ai_tool_of(author, body):
    """Which AI tool (if any) helped with a commit: Co-Authored-By trailers, Aider's '(aider)' author tag, or Claude Code's footer."""
    names = " ".join(COAUTHOR_RE.findall(body or ""))
    if "(aider)" in (author or "").lower():
        names += " aider"
    if "generated with [claude code]" in (body or "").lower():
        names += " claude"
    for tool, pat in AI_COAUTHORS:
        if re.search(pat, names, re.I):
            return tool
    return None


def collect_git(cfg, known_repos):
    """Commits from the last 31 days in repos your AI tools ran in (plus extra_repos). Stores numbers only, never messages or code."""
    g = cfg.get("git") or {}
    if not g.get("enabled", True):
        return [], known_repos
    if not shutil.which("git"):
        WARNINGS.append("git not found; commit tracking skipped")
        return [], known_repos
    repos = {str(r) for r in (git_root(d) for d in PROJECT_DIRS | set(known_repos) | set(g.get("extra_repos") or [])) if r}
    rows, since = [], int(time.time() - 31 * 86400)
    for repo in sorted(repos):
        me = (_git(repo, "config", "user.email") or "").strip().lower() or None
        try:
            # no --since: git stops walking at the first older-dated commit (rebases, clock skew); filter below instead
            log = _git(repo, "log", "--branches", "--no-merges", "--max-count=3000", "--numstat", "--no-textconv", "--no-ext-diff",
                       "--pretty=format:%x1e%H%x1f%at%x1f%ae%x1f%an%x1f%B%x1d")
        except (subprocess.TimeoutExpired, OSError) as e:
            WARNINGS.append(f"git log failed in {Path(repo).name} ({type(e).__name__})")
            continue
        if log is None:
            continue
        name = Path(repo).name
        for chunk in log.split("\x1e")[1:]:
            head, _, stats = chunk.partition("\x1d")
            parts = head.split("\x1f")
            if len(parts) < 5:
                continue
            sha, at, email, author, body = parts[0], parts[1], parts[2].lower(), parts[3], parts[4]
            if not at.isdigit() or int(at) < since:
                continue
            added = deleted = files = 0
            for line in stats.strip().splitlines():
                cols = line.split("\t")
                if len(cols) >= 3:
                    files += 1
                    added += int(cols[0]) if cols[0].isdigit() else 0
                    deleted += int(cols[1]) if cols[1].isdigit() else 0
            rows.append((f"{name}:{sha[:12]}", float(at), name, added, deleted, files, ai_tool_of(author, body),
                         1 if (not me or email == me) else 0))
    return rows, sorted(repos)


def collect_status():
    summary = fetch_json(f"{STATUS_API}/summary.json")
    incidents = fetch_json(f"{STATUS_API}/incidents.json")
    status = None
    if summary:
        status = {"fetched": time.time(),
                  "indicator": (summary.get("status") or {}).get("indicator", "none"),
                  "description": (summary.get("status") or {}).get("description", ""),
                  "components": [{"name": c.get("name"), "status": c.get("status")}
                                 for c in summary.get("components", []) if not c.get("group")]}
    events = []
    for inc in (incidents or {}).get("incidents", []):
        ts = parse_ts(inc.get("created_at"))
        if not ts:
            continue
        events.append((f"incident:{inc.get('id')}", ts, "incident", json.dumps({
            "name": inc.get("name"), "impact": inc.get("impact"), "status": inc.get("status"),
            "resolved": parse_ts(inc.get("resolved_at")), "link": inc.get("shortlink"),
            "components": [c.get("name") for c in inc.get("components", [])]})))
    return status, events


# ---------- prices ----------
GECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XMR": "monero", "BNB": "binancecoin", "XRP": "ripple",
             "LINK": "chainlink", "ADA": "cardano", "DOGE": "dogecoin", "DOT": "polkadot", "AVAX": "avalanche-2", "TRX": "tron",
             "TON": "the-open-network", "LTC": "litecoin", "BCH": "bitcoin-cash", "ATOM": "cosmos", "UNI": "uniswap",
             "XLM": "stellar", "NEAR": "near", "APT": "aptos", "ARB": "arbitrum", "OP": "optimism", "SUI": "sui", "PEPE": "pepe",
             "SHIB": "shiba-inu", "USDT": "tether", "USDC": "usd-coin", "HBAR": "hedera-hashgraph", "FIL": "filecoin",
             "ICP": "internet-computer", "KAS": "kaspa", "TAO": "bittensor", "HYPE": "hyperliquid", "ETC": "ethereum-classic",
             "AAVE": "aave", "RNDR": "render-token", "RENDER": "render-token", "INJ": "injective-protocol", "SEI": "sei-network"}


def coin_list(cfg):
    """[(id, SYMBOL)] from settings; id is the CoinGecko id when known."""
    out = []
    for c in cfg.get("coins") or []:
        sym, _, gid = str(c).partition(":")
        sym = sym.strip().upper()
        if sym:
            out.append((gid.strip() or GECKO_IDS.get(sym, sym.lower()), sym))
    return out


# Popular coins fetched alongside your own, so the page's coin picker works without extra requests
# (CoinMarketCap still counts this as 1 credit; CoinGecko returns them in one call).
COIN_CATALOG = ["BTC", "ETH", "SOL", "XMR", "BNB", "XRP", "LINK", "ADA", "DOGE", "DOT", "AVAX", "TRX",
                "TON", "LTC", "SUI", "NEAR", "ATOM", "XLM", "UNI", "HBAR", "PEPE", "SHIB", "TAO", "KAS"]


def fetch_coins(cfg):
    """Your coins first, then the rest of the catalog: [(id, SYMBOL)]."""
    mine = coin_list(cfg)
    have = {sym for _, sym in mine}
    return mine + [(GECKO_IDS.get(sym, sym.lower()), sym) for sym in COIN_CATALOG if sym not in have]


def coin_spec(cid, sym):
    """How a coin is written in the `coins` setting."""
    return sym if cid == GECKO_IDS.get(sym, sym.lower()) else f"{sym}:{cid}"


def gecko_url(cfg):
    ids = ",".join(i for i, _ in fetch_coins(cfg))
    return (f"https://api.coingecko.com/api/v3/coins/markets?vs_currency={cfg['currency'].lower()}"
            f"&ids={ids}&sparkline=true&price_change_percentage=1h,24h,7d")


def prices_from_cmc(key, coins, cur):
    """1 credit per call; every 15 min is ~2,900 of the free plan's 15,000/month."""
    url = ("https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol="
           + ",".join(s for _, s in coins) + f"&convert={cur}")
    data = fetch_json(url, headers={"X-CMC_PRO_API_KEY": key})
    got = (data or {}).get("data") or {}
    out = []
    for cid, sym in coins:
        entry = got.get(sym)
        if isinstance(entry, list):
            entry = entry[0] if entry else None
        q = ((entry or {}).get("quote") or {}).get(cur)
        if not q or q.get("price") is None:
            continue
        out.append({"id": cid, "symbol": sym.lower(), "name": (entry or {}).get("name") or sym, "current_price": q["price"],
                    "price_change_percentage_1h_in_currency": q.get("percent_change_1h"),
                    "price_change_percentage_24h_in_currency": q.get("percent_change_24h"),
                    "price_change_percentage_7d_in_currency": q.get("percent_change_7d"),
                    "sparkline_in_7d": {"price": []}})  # filled from local history later
    return out


def collect_prices(cfg):
    if not cfg.get("show_prices", True) or not coin_list(cfg):
        return None
    cur, coins = cfg["currency"].upper(), fetch_coins(cfg)
    if cfg.get("cmc_key"):
        got = prices_from_cmc(cfg["cmc_key"], coins, cur)
        if got:
            return {"fetched": time.time(), "source": "CoinMarketCap", "currency": cur, "coins": got}
    key = cfg.get("coingecko_key")
    data = fetch_json(gecko_url(cfg) + (f"&x_cg_demo_api_key={key}" if key else ""))
    if isinstance(data, list) and data:
        return {"fetched": time.time(), "source": "CoinGecko", "currency": cur, "coins": data}
    return None


# ---------- store ----------
def store(rows, events, meta):
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS usage(
        id TEXT PRIMARY KEY, ts REAL, source TEXT, model TEXT,
        input INT, output INT, cache_write INT, cache_read INT, project TEXT)""")
    cols = [r[1] for r in con.execute("PRAGMA table_info(usage)")]
    if "session" not in cols:
        con.execute("ALTER TABLE usage ADD COLUMN session TEXT")
    if "cost" not in cols:
        con.execute("ALTER TABLE usage ADD COLUMN cost REAL")
    if "tools" not in cols:
        con.execute("ALTER TABLE usage ADD COLUMN tools TEXT")
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, ts REAL, kind TEXT, detail TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS prices(ts REAL, coin TEXT, price REAL, PRIMARY KEY(ts, coin))")
    con.execute("CREATE TABLE IF NOT EXISTS commits(id TEXT PRIMARY KEY, ts REAL, repo TEXT, added INT, deleted INT, files INT, ai TEXT, mine INT DEFAULT 1)")
    con.executemany("INSERT OR REPLACE INTO commits VALUES (?,?,?,?,?,?,?,?)", meta.pop("_commits", None) or [])
    p = meta.get("prices")
    if p:
        con.executemany("INSERT OR IGNORE INTO prices VALUES (?,?,?)",
                        [(p["fetched"], f'{c["id"]}:{p.get("currency", "GBP")}', c["current_price"]) for c in p["coins"] if c.get("current_price") is not None])
        con.execute("DELETE FROM prices WHERE ts < ?", (time.time() - 8 * 86400,))
    con.executemany("INSERT OR REPLACE INTO usage(id,ts,source,model,input,output,cache_write,cache_read,project,session,cost,tools) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [tuple(r) + (None,) * (12 - len(r)) for r in rows if r[1] is not None])
    con.executemany("INSERT OR REPLACE INTO events VALUES (?,?,?,?)", events)
    # Commits made by hand carry no AI trailer: count one as AI-assisted when a tool was working up to 30 minutes before it,
    # either in that repo or somewhere we can't tie to a repo (a sandbox, a chat), but not in a different tracked repo.
    con.execute("""UPDATE commits SET ai = (
        SELECT REPLACE(REPLACE(u.source, ' Code', ''), ' CLI', '') FROM usage u
        WHERE u.ts BETWEEN commits.ts - 1800 AND commits.ts
          AND (u.project = commits.repo OR u.project NOT IN (SELECT repo FROM commits))
        ORDER BY u.ts DESC LIMIT 1) WHERE ai IS NULL AND mine = 1""")
    for k, v in meta.items():
        if v is not None:
            con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(v)))
    con.commit()
    return con


# ---------- aggregate ----------
def peak_window(events, width):
    """Largest token total inside any sliding window of `width` seconds."""
    best, j, run = 0, 0, 0
    for ts, w in events:
        run += w
        while events[j][0] < ts - width:
            run -= events[j][1]
            j += 1
        best = max(best, run)
    return best


def window_before(events, t, width):
    return sum(w for ts, w in events if t - width <= ts <= t)


def hit_rate(i, cw, cr):
    tot = i + cw + cr
    return round(cr / tot * 100, 1) if tot else None


# Model advice: big models doing small jobs. (pattern, cheaper model to price against, labels)
ADVICE = [
    (r"(fable|mythos)-5", "claude-opus-5-5", "Fable/Mythos", "Opus 5.5"),
    (r"opus", "claude-sonnet-5-5", "Opus", "Sonnet 5.5"),
    (r"gpt-5-6-sol|gpt-5-5(?!-pro)", "gpt-5.4-mini", "GPT-5.5-class", "GPT-5.4 mini"),
]
SMALL_OUTPUT = 800   # a request that writes fewer tokens than this counts as "small"


def forecast_window(used_pct, window_s, remaining_s):
    """Linear pace for a rate-limit window: where it lands at reset, and when it would hit 100%."""
    elapsed = window_s - remaining_s
    if used_pct is None or elapsed < 600 or used_pct <= 0 or remaining_s is None or remaining_s <= 0:
        return None
    rate = used_pct / elapsed
    projected = used_pct + rate * remaining_s
    hit = time.time() + (100 - used_pct) / rate if projected > 100 and used_pct < 100 else None
    return {"used": used_pct, "projected": round(projected, 1), "hit_ts": hit, "reset_ts": time.time() + remaining_s}


def aggregate(con, cfg):
    now = time.time()
    since30 = now - 30 * 86400
    pricer = Pricer(cfg.get("prices"))
    data = con.execute(
        "SELECT ts, source, model, input, output, cache_write, cache_read, project, session, cost, tools "
        "FROM usage WHERE ts >= ? ORDER BY ts", (since30,)).fetchall()
    sources = SOURCES

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    day_starts = [today - timedelta(days=d) for d in range(29, -1, -1)]
    days = [d.strftime("%Y-%m-%d") for d in day_starts]
    top = datetime.now().replace(minute=0, second=0, microsecond=0)
    hour_starts = [top - timedelta(hours=h) for h in range(71, -1, -1)]   # 72h, so the page can show 24/48/72
    hours = [h.strftime("%Y-%m-%d %H") for h in hour_starts]

    daily = {s: {d: 0 for d in days} for s in sources}
    hourly = {s: {h: 0 for h in hours} for s in sources}
    daily_cost = {s: {d: 0.0 for d in days} for s in sources}
    hourly_cost = {s: {h: 0.0 for h in hours} for s in sources}
    heat_by = {s: [[0] * 24 for _ in range(7)] for s in sources}
    models, projects, sessions = {}, {}, {}
    win5 = {s: 0 for s in sources}
    week = {s: 0 for s in sources}
    cost30 = {s: 0.0 for s in sources}
    cost7 = {s: 0.0 for s in sources}
    unpriced = {}
    ev = {s: [] for s in sources}
    cache_now, cache_prev = [0, 0, 0], [0, 0, 0]
    cache_daily = {d: [0, 0, 0] for d in days}
    advice_rules = [(re.compile(p), cheap, a, b) for p, cheap, a, b in ADVICE]
    advice = {}
    last_hour = {s: 0 for s in sources}

    for ts, src, model, i, o, cw, cr, proj, sess, explicit, tools in data:
        if src not in win5:
            continue
        w = i + o + cw  # "work" tokens; cache reads tracked separately
        c = pricer.cost(model, i, o, cw, cr, explicit)
        if c is None:
            unpriced[model] = unpriced.get(model, 0) + w + cr
            c = 0.0
        dt = datetime.fromtimestamp(ts)
        d, hk = dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d %H")
        if d in daily[src]:
            daily[src][d] += w
            daily_cost[src][d] += c
        if hk in hourly[src]:
            hourly[src][hk] += w
            hourly_cost[src][hk] += c
        heat_by[src][dt.weekday()][dt.hour] += w
        m = models.setdefault((src, model), [0, 0, 0, 0, 0.0])
        for k, v in enumerate((i, o, cw, cr, c)):
            m[k] += v
        if src != "Hermes":
            pr = projects.setdefault(proj, {"tokens": 0, "cost": 0.0, "sources": set()})
            pr["tokens"] += w
            pr["cost"] += c
            pr["sources"].add(src)
        if src == "Claude Code":
            bucket = cache_now if ts >= now - WEEK else cache_prev if ts >= now - 2 * WEEK else None
            if bucket:
                for k, v in enumerate((i, cw, cr)):
                    bucket[k] += v
            if d in cache_daily:
                for k, v in enumerate((i, cw, cr)):
                    cache_daily[d][k] += v
        s = sessions.setdefault((src, sess or "?"), {"source": src, "project": proj, "start": ts, "end": ts,
                                                      "tokens": 0, "cost": 0.0, "calls": 0, "models": {},
                                                      "cache": [0, 0, 0], "tools": {}, "timeline": []})
        s["end"] = max(s["end"], ts)
        s["tokens"] += w
        s["cost"] += c
        s["calls"] += 1
        s["models"][model] = s["models"].get(model, 0) + w
        for k, v in enumerate((i, cw, cr)):
            s["cache"][k] += v
        s["timeline"].append([round(ts), i, o, cw, cr, round(c, 5)])
        for t in (tools or "").split(","):
            if t:
                s["tools"][t] = s["tools"].get(t, 0) + 1
        if explicit is None and c:
            key = norm_model(model)
            for rx, cheap, big_label, cheap_label in advice_rules:
                if rx.search(key):
                    a = advice.setdefault((src, big_label), {"source": src, "from": big_label, "to": cheap_label,
                                                            "calls": 0, "small": 0, "cost_small": 0.0, "cost_alt": 0.0})
                    a["calls"] += 1
                    if o < SMALL_OUTPUT:
                        a["small"] += 1
                        a["cost_small"] += c
                        a["cost_alt"] += pricer.cost(cheap, i, o, cw, cr) or c
                    break
        if ts >= now - 3600:
            last_hour[src] += w
        ev[src].append((ts, w))
        cost30[src] += c
        if ts >= now - WINDOW_5H:
            win5[src] += w
        if ts >= now - WEEK:
            week[src] += w
            cost7[src] += c

    # limit hits and what the 5h window looked like when they happened
    limits = []
    for eid, ts, detail in con.execute(
            "SELECT id, ts, detail FROM events WHERE kind='limit' AND ts >= ? ORDER BY ts", (since30,)):
        d = json.loads(detail)
        src = d.get("source", "Claude Code")
        limits.append({"id": eid, "ts": ts, "source": src, "reset": d.get("reset"),
                       "window": window_before(ev.get(src, []), ts, WINDOW_5H)})
    claude_hits = [l["window"] for l in limits if l["source"] == "Claude Code" and l["window"] > 0]

    incidents = []
    for eid, ts, detail in con.execute(
            "SELECT id, ts, detail FROM events WHERE kind='incident' AND ts >= ? ORDER BY ts DESC", (since30,)):
        d = json.loads(detail)
        d["id"], d["start"] = eid, ts
        incidents.append(d)

    sess_list = sorted(sessions.values(), key=lambda s: -s["end"])[:20]
    for s in sess_list:
        mods = s.pop("models")
        s["model"] = max(mods.items(), key=lambda kv: kv[1])[0]
        s["models"] = sorted(mods, key=lambda m: -mods[m])
        s["cache_hit"] = hit_rate(*s.pop("cache"))
        tl = s["timeline"]
        if len(tl) > 400:  # keep the page light: merge neighbouring requests
            step = -(-len(tl) // 400)
            tl = [[g[0][0], *[sum(x[k] for x in g) for k in range(1, 6)]] for g in (tl[j:j + step] for j in range(0, len(tl), step))]
        s["timeline"] = tl

    meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
    p = meta.get("prices")
    if p:
        for c in p.get("coins", []):
            if not (c.get("sparkline_in_7d") or {}).get("price"):
                c["sparkline_in_7d"] = {"price": [r[0] for r in con.execute(
                    "SELECT price FROM prices WHERE coin=? AND ts >= ? ORDER BY ts", (f'{c.get("id")}:{p.get("currency", "GBP")}', now - WEEK))]}
    present = [s for s in sources if s == "Claude Code" or any(daily[s].values())]

    # git activity: sent to the page as a compact list so it can filter (mine / everyone) instantly
    repos_i, tools_i, commits = {}, {}, []
    for ts, repo, added, deleted, ai, mine in con.execute(
            "SELECT ts, repo, added, deleted, ai, mine FROM commits WHERE ts >= ? ORDER BY ts", (since30,)):
        ri = repos_i.setdefault(repo, len(repos_i))
        ti = tools_i.setdefault(ai, len(tools_i)) if ai else -1
        commits.append([round(ts), ri, added, deleted, ti, mine])
    gcfg = cfg.get("git") or {}
    git = {"enabled": bool(gcfg.get("enabled", True)), "commits": commits,
           "repos": list(repos_i), "tools": list(tools_i), "mine_default": gcfg.get("author", "me") != "all"}
    # pace forecasts
    limit_est = int(statistics.median(claude_hits)) if claude_hits else None
    fc = {"claude5h": None, "codex": {}, "week": {}, "today": {}}
    if limit_est and last_hour["Claude Code"] > 0 and win5["Claude Code"] < limit_est:
        fc["claude5h"] = {"eta_ts": now + (limit_est - win5["Claude Code"]) / last_hour["Claude Code"] * 3600,
                          "rate_hr": last_hour["Claude Code"]}
    cl = meta.get("codex_limits")
    if cl and now - cl.get("ts", 0) < 6 * 3600:
        for k, default_min in (("primary", 300), ("secondary", 10080)):
            wdw = (cl.get("limits") or {}).get(k) or {}
            rem = wdw.get("resets_in_seconds")
            rem = rem - (now - cl["ts"]) if rem is not None else (wdw["resets_at"] - now if wdw.get("resets_at") else None)
            f = forecast_window(wdw.get("used_percent"), (wdw.get("window_minutes") or default_min) * 60, rem)
            if f:
                fc["codex"][k] = f
    for src in sources:
        vals = [daily[src][d] for d in days]
        prev = vals[-28:-7]
        avg_prev = sum(prev) / 3 if any(prev) else None
        fc["week"][src] = {"now": sum(vals[-7:]), "avg_prev": avg_prev}
    frac = (now - today.timestamp()) / 86400
    if frac > 0.1:
        fc["today"] = {"frac": frac,
                       "tokens": {s: daily[s][days[-1]] / frac for s in sources},
                       "cost": {s: daily_cost[s][days[-1]] / frac for s in sources}}
    theme = cfg.get("theme") or {}
    colors = {**SOURCE_COLORS, "Claude Code": theme.get("accent") or SOURCE_COLORS["Claude Code"], **(theme.get("colors") or {})}
    plans = {k: float(v) for k, v in (cfg.get("plans") or {}).items() if isinstance(v, (int, float)) and v > 0}
    return {
        "version": VERSION,
        "generated": datetime.now().strftime("%a %d %b %Y, %H:%M"),
        "generated_ts": now,
        "sources": present,
        "colors": colors,
        "days": days, "day0": day_starts[0].timestamp(), "daily": daily, "daily_cost": daily_cost,
        "hours": hours, "hour0": hour_starts[0].timestamp(), "hourly": hourly, "hourly_cost": hourly_cost,
        "heat_by": heat_by,
        "win5": win5, "week": week,
        "cost30": cost30, "cost7": cost7, "plans": plans,
        "unpriced": sorted(unpriced.items(), key=lambda kv: -kv[1])[:5],
        "peak5": {s: peak_window(ev[s], WINDOW_5H) for s in sources},
        "limit_est": limit_est,
        "forecast": fc,
        "advice": [a for a in advice.values() if a["small"]],
        "privacy": bool(cfg.get("screenshot_mode", False)),
        "alert_pct": (cfg.get("alerts") or {}).get("claude_5h_percent", 85),
        "limit_hits": len(claude_hits),
        "limits": limits,
        "cache": {"now": hit_rate(*cache_now), "prev": hit_rate(*cache_prev),
                  "daily": [hit_rate(*cache_daily[d]) for d in days]},
        "codex_limits": meta.get("codex_limits"),
        "status": meta.get("status"),
        "incidents": incidents,
        "prices": meta.get("prices") if cfg.get("show_prices", True) else None,
        "show_prices": bool(cfg.get("show_prices", True)),
        "currency": cfg["currency"].upper(),
        "coins": [i for i, _ in coin_list(cfg)],
        "coin_catalog": [[i, sym, coin_spec(i, sym)] for i, sym in fetch_coins(cfg)],
        "hidden": [t for t in (cfg.get("hidden_tools") or []) if t in SOURCES or t == "Git"],
        "git": git,
        "gecko_url": gecko_url(cfg),  # keyless: API keys never go into the page
        "refresh_minutes": max(1, int(cfg.get("refresh_minutes") or 15)),
        "animate": bool(theme.get("animate_background", True)),
        "models": sorted(([s, m, *v] for (s, m), v in models.items()), key=lambda r: -(r[2] + r[3] + r[4])),
        "projects": [(k, v["tokens"], v["cost"], sorted(v["sources"])) for k, v in
                     sorted(projects.items(), key=lambda kv: -kv[1]["tokens"])[:12]],
        "sessions": sess_list,
        "row_count": len(data),
    }


# ---------- export ----------
def export_data(con, cfg, out_dir):
    """Every request and commit as CSV (plus a JSON summary), costs priced with your current settings."""
    import csv
    out = Path(os.path.expanduser(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    pricer = Pricer(cfg.get("prices"))
    stamp = datetime.now().strftime("%Y-%m-%d")
    alias = {}
    anon = (lambda p: alias.setdefault(p, f"Project {len(alias) + 1}")) if cfg.get("screenshot_mode") else (lambda p: p)
    paths = []
    p = out / f"build-commit-pro-requests-{stamp}.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "tool", "model", "input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens",
                    "api_cost_usd", "project", "session", "claude_code_tools"])
        for ts, src, model, i, o, cw, cr, proj, sess, explicit, tools in con.execute(
                "SELECT ts, source, model, input, output, cache_write, cache_read, project, session, cost, tools FROM usage ORDER BY ts"):
            cost = pricer.cost(model, i or 0, o or 0, cw or 0, cr or 0, explicit)
            w.writerow([datetime.fromtimestamp(ts).isoformat(timespec="seconds"), src, model, i, o, cw, cr,
                        "" if cost is None else round(cost, 6), anon(proj), sess, tools or ""])
    paths.append(p)
    p = out / f"build-commit-pro-commits-{stamp}.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "repo", "lines_added", "lines_deleted", "files_changed", "ai_tool", "mine"])
        for ts, repo, a, d, f, ai, mine in con.execute("SELECT ts, repo, added, deleted, files, ai, mine FROM commits ORDER BY ts"):
            w.writerow([datetime.fromtimestamp(ts).isoformat(timespec="seconds"), anon(repo), a, d, f, ai or "", mine])
    paths.append(p)
    agg = aggregate(con, cfg)
    summary = {k: agg[k] for k in ("version", "generated", "sources", "win5", "week", "cost30", "cost7", "plans",
                                   "limit_est", "forecast", "advice", "cache", "codex_limits")}
    summary["models_30d"] = [dict(zip(["tool", "model", "input", "output", "cache_write", "cache_read", "api_cost_usd"], m)) for m in agg["models"]]
    summary["projects_30d"] = [{"project": anon(k), "tokens": t, "api_cost_usd": round(c, 4), "tools": srcs} for k, t, c, srcs in agg["projects"]]
    p = out / f"build-commit-pro-summary-{stamp}.json"
    p.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    paths.append(p)
    return paths


# ---------- menu bar (SwiftBar / xbar on macOS, Argos on Linux) ----------
def menubar(cfg):
    if not DB_PATH.exists():
        print("BCP –\n---\nNo data yet: run usage.py once | color=gray")
        return
    con = sqlite3.connect(DB_PATH)
    try:
        a = aggregate(con, cfg)
    finally:
        con.close()
    now = time.time()
    dash = OUT_HTML.as_uri()
    fmt = lambda v: f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v / 1e3:.0f}k" if v >= 1e3 else str(int(v))
    hhmm = lambda t: datetime.fromtimestamp(t).strftime("%a %H:%M" if t - now > 20 * 3600 else "%H:%M")
    title, lines, worst = [], [], 0

    def add(text, **params):
        # every line opens the dashboard: clickable items show in normal (not greyed-out) text
        params.setdefault("href", dash)
        lines.append(text + " | " + " ".join(f"{k}={v}" for k, v in params.items()))

    c5 = a["win5"]["Claude Code"]
    wk = a["forecast"]["week"]["Claude Code"]
    if a["limit_est"]:
        pct = c5 / a["limit_est"] * 100
        worst = max(worst, pct)
        title.append(f"CC {pct:.0f}%")
        add(f"Claude 5h: {fmt(c5)} tokens ({pct:.0f}% of est. limit {fmt(a['limit_est'])})")
    else:
        title.append(f"CC {fmt(c5)}" if c5 else f"CC 7d {fmt(wk['now'])}")
        add(f"Claude 5h: {fmt(c5)} tokens (Claude Code only)")
        add("   forecast starts after your first Claude Code limit hit", color="gray")
        add("   Claude app use shares your limit: see Claude → Settings → Usage", color="gray", href="https://claude.ai/settings/usage")
    if a["forecast"]["claude5h"]:
        add(f"   at this pace: limit around {hhmm(a['forecast']['claude5h']['eta_ts'])}", color="#ff9f0a")
    add(f"Claude 7d: {fmt(wk['now'])}" + (f" ({(wk['now'] / wk['avg_prev'] - 1) * 100:+.0f}% vs 3-wk avg)" if wk["avg_prev"] else ""))
    cl = (a.get("codex_limits") or {}).get("limits") or {}
    if cl.get("primary"):
        p5 = cl["primary"].get("used_percent") or 0
        pw = (cl.get("secondary") or {}).get("used_percent")
        worst = max(worst, p5, pw or 0)
        title.append(f"CX {p5:.0f}%")
        add(f"Codex: 5h {p5:.0f}%" + (f" · week {pw:.0f}%" if pw is not None else ""))
        for k, label in (("primary", "5h"), ("secondary", "weekly")):
            f = a["forecast"]["codex"].get(k)
            if f and f["hit_ts"]:
                add(f"   {label} limit at this pace: {hhmm(f['hit_ts'])}", color="#ff453a")
            elif f:
                add(f"   {label} on pace for {f['projected']:.0f}% by reset")
    add(f"API value 30d: ${sum(a['cost30'].values()):,.2f}")
    commits = [c for c in a["git"]["commits"] if c[5]] if a["git"]["enabled"] else []
    if commits:
        ai = sum(1 for c in commits if c[4] >= 0)
        add(f"Commits 30d: {len(commits)} ({ai * 100 // len(commits)}% with AI)")
    st = a.get("status")
    if st and st.get("indicator") not in (None, "none"):
        title.append("⚠")
        add(f"Claude: {st.get('description')}", color="#ff453a", href="https://status.claude.com")
    color = " | color=#ff453a" if worst >= 85 else ""
    print(" · ".join(title) + color)
    print("---")
    print("\n".join(lines))
    print("---")
    print(f"Open dashboard | href={dash}")
    py, script = _runner()
    dq = lambda v: f'"{v}"' if " " in v else v   # SwiftBar/xbar want double quotes around paths with spaces
    print(f"Refresh now | bash={dq(py)} param1={dq(script)} param2=--no-open terminal=false refresh=true")
    print(f"Updated {datetime.fromtimestamp(DB_PATH.stat().st_mtime):%H:%M} | color=gray size=11")


def install_menubar(target=None):
    system = platform.system()
    candidates = []
    if target:
        candidates = [Path(os.path.expanduser(target))]
    elif system == "Darwin":
        try:
            d = subprocess.run(["defaults", "read", "com.ameba.SwiftBar", "PluginDirectory"], capture_output=True, text=True).stdout.strip()
            if d:
                candidates.append(Path(os.path.expanduser(d)))
        except OSError:
            pass
        candidates.append(HOME / "Library" / "Application Support" / "xbar" / "plugins")
    elif system == "Linux":
        candidates.append(HOME / ".config" / "argos")
    else:
        print("The menu bar meter is optional and only available on macOS (SwiftBar/xbar) and Linux GNOME (Argos).")
        print("Everything else works on Windows as normal.")
        return
    dest = next((c for c in candidates if c.is_dir()), None)
    if not dest:
        print("The menu bar meter is optional, and needs a menu bar app with a plugin folder, which wasn't found.")
        print("To add it: install SwiftBar (brew install --cask swiftbar, or swiftbar.app), open it once and pick a plugin folder,")
        print("then run this again, or pass the folder: usage.py --install-menubar ~/path/to/plugins")
        print("Everything else (dashboard, alerts, background refresh) works without it.")
        return
    py, script = _runner()
    plugin = dest / "build-commit-pro.2m.sh"
    plugin.write_text("#!/bin/bash\n"
                      "# <swiftbar.hideAbout>true</swiftbar.hideAbout><swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>\n"
                      "# <xbar.title>Build & Commit Pro Tracker</xbar.title>\n"
                      f"exec {shlex.quote(py)} {shlex.quote(script)} --menubar\n", encoding="utf-8")
    plugin.chmod(0o755)
    print(f"Menu bar meter installed: {plugin}")
    print("It re-reads your data every 2 minutes; keep --install on so the data itself stays fresh.")


# ---------- alerts ----------
def notify(title, msg):
    """Best-effort desktop notification on macOS, Windows and Linux; always echoed to the console."""
    print(f"  [alert] {title}: {msg}")
    system = platform.system()
    try:
        if system == "Darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display notification "{esc(msg)}" with title "{esc(title)}"'],
                           capture_output=True, timeout=10)
        elif system == "Windows":
            ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
                  "$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                  "$t=$x.GetElementsByTagName('text');$t.Item(0).AppendChild($x.CreateTextNode($env:AIU_T))|Out-Null;"
                  "$t.Item(1).AppendChild($x.CreateTextNode($env:AIU_M))|Out-Null;"
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                  "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')"
                  ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
                           env={**os.environ, "AIU_T": title, "AIU_M": msg}, capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "-a", "Build & Commit Pro Tracker", title, msg], capture_output=True, timeout=10)
    except Exception:
        pass


def check_alerts(con, agg, cfg):
    a = cfg.get("alerts") or {}
    if not a.get("enabled", True):
        return
    now = time.time()
    row = con.execute("SELECT value FROM meta WHERE key='alerts_sent'").fetchone()
    sent = {k: v for k, v in (json.loads(row[0]) if row else {}).items() if now - v < 8 * 86400}
    out = []

    def fire(key, title, msg):
        if key not in sent:
            sent[key] = now
            out.append((title, msg))

    if a.get("limit_hits", True):
        for l in agg["limits"]:
            if now - l["ts"] < 2 * 3600:
                reset = f" Resets at {datetime.fromtimestamp(l['reset']):%H:%M}." if l.get("reset") else ""
                fire(f"hit:{l['id']}", f"{l['source']} limit reached", f"You've hit your {l['source']} usage limit.{reset}")
    pct = a.get("claude_5h_percent")
    if pct and agg["limit_est"]:
        used = agg["win5"]["Claude Code"] / agg["limit_est"] * 100
        if used >= pct:
            fire(f"c5h:{int(now // WINDOW_5H)}", "Claude usage high",
                 f"{used:.0f}% of your usual Claude limit used in the last 5 hours.")
    cl, cpct = agg.get("codex_limits"), a.get("codex_percent")
    if cpct and cl and now - cl.get("ts", 0) < 3600:
        for k, label in (("primary", "5-hour"), ("secondary", "weekly")):
            w = (cl.get("limits") or {}).get(k) or {}
            used = w.get("used_percent") or 0
            if used >= cpct:
                window = int((w.get("resets_at") or cl["ts"] + (w.get("resets_in_seconds") or 0)) // 600)
                fire(f"codex:{k}:{window}", "Codex usage high", f"Codex {label} limit is {used:.0f}% used.")
    if a.get("incidents", True):
        for inc in agg["incidents"]:
            if not inc.get("resolved") and now - inc["start"] < 3 * 3600 and inc.get("impact") not in (None, "none"):
                fire(f"inc:{inc['id']}", f"Claude incident ({inc.get('impact')})", inc.get("name") or "Service disruption")
    for title, msg in out:
        notify(title, msg)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('alerts_sent', ?)", (json.dumps(sent),))
    con.commit()


# ---------- render ----------
HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Build &amp; Commit Pro Tracker</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Pixelify+Sans:wght@500;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0a0a0a;--panel:#141414;--panel2:#1c1c1c;--line:#262626;--ink:#f3f3f3;--mute:#a3a3a3;--dim:#6e6e6e;
--accent:#ff6a13;--claude:#ff6a13;--codex:#e6e6e6;--hermes:#6f6f6f;--warn:#ff3b30;--up:#4ade80;--down:#ff453a;--amber:#ffb020;
--mono:"JetBrains Mono",ui-monospace,Menlo,monospace;--pixel:"Pixelify Sans",var(--mono);--gap:10px;
--panel-a:rgba(20,20,20,.05);--blur:none;
/* panel borders: indigo sits opposite the orange without fighting it; swap here to restyle */
--edge:rgba(129,140,248,.38);--edge-hi:rgba(129,140,248,.75)}
*{box-sizing:border-box}
html,body{background:var(--bg)}
body{margin:0;color:var(--ink);font:14px/1.45 Inter,-apple-system,BlinkMacSystemFont,sans-serif;min-height:100vh}
a{color:inherit}
#ascii{position:fixed;inset:0;margin:0;overflow:hidden;pointer-events:none;z-index:0;
  font:12px/15px var(--mono);color:#6a6a6a;opacity:.34;white-space:pre;user-select:none}
#ascii::after{content:"";position:fixed;inset:0;background:radial-gradient(ellipse at 50% 20%,transparent 0,rgba(10,10,10,.3) 80%)}
main{position:relative;z-index:1;max-width:1800px;margin:0 auto;padding:12px 16px 24px}

/* ticker */
.ticker{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:6px;padding:6px 16px;
  background:rgba(10,10,10,.8);-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--line)}
.tkwrap{flex:1;min-width:0;display:flex;flex-direction:column;gap:4px}
.coins{display:flex;gap:6px;align-items:center;overflow-x:auto;overscroll-behavior-x:contain;scrollbar-width:none}
.coins::-webkit-scrollbar{display:none}
.coins:focus{outline:none}.coins:focus-visible{outline:1px solid var(--edge-hi);outline-offset:2px;border-radius:6px}
/* own scroll track: always visible when coins overflow (macOS hides native scrollbars until you scroll) */
.tkbar{position:relative;height:3px;border-radius:2px;background:rgba(255,255,255,.07);cursor:pointer;touch-action:none}
.tkbar::before{content:"";position:absolute;left:0;right:0;top:-5px;bottom:-5px}
.tkbar[hidden]{display:none}
.tkbar i{position:absolute;top:0;height:100%;border-radius:2px;background:#555;transition:background .15s}
.tkbar:hover i,.tkbar.drag i{background:var(--accent)}
.tk{display:flex;align-items:center;gap:7px;padding:5px 9px;border-radius:6px;background:var(--panel2);white-space:nowrap;flex:none}
.tk b{font-family:var(--mono);font-weight:700;font-size:13px;letter-spacing:.03em}
.tk .px{font-family:var(--mono);font-weight:500;font-size:12.5px;font-variant-numeric:tabular-nums}
.tk .ch{font-family:var(--mono);font-size:11px}
.tk svg{width:38px;height:16px}
.tk-meta{font-family:var(--mono);font-size:10.5px;color:var(--dim);white-space:nowrap;flex:none;margin-left:6px}
.ver{font-family:var(--mono);font-size:11px;color:var(--dim);flex:none;padding-left:12px}
.gear{font:500 12px var(--mono);color:#d6d6d6;background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer;flex:none}
.gear:hover,.gear[aria-expanded=true]{color:#fff;border-color:var(--edge-hi)}

/* settings panel */
.drawer{position:fixed;top:50px;right:12px;z-index:30;width:330px;max-width:calc(100vw - 24px);max-height:calc(100vh - 64px);overflow:auto;
  background:rgba(14,14,14,.96);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);border:1px solid var(--edge-hi);
  border-radius:10px;padding:14px 16px 16px;box-shadow:0 14px 44px rgba(0,0,0,.65);scrollbar-width:none}
.drawer::-webkit-scrollbar{display:none}
.drawer[hidden]{display:none}
.dh{display:flex;justify-content:space-between;align-items:center;margin-bottom:2px}
.x{background:none;border:0;color:var(--mute);font-size:15px;cursor:pointer;padding:4px}
.x:hover{color:#fff}
.sw{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;font-size:13px;border-bottom:1px solid var(--line);cursor:pointer}
.sw:last-child{border-bottom:0}
.sw>span{display:flex;flex-direction:column;gap:1px}
.sw>span>span{display:flex;align-items:center;gap:7px}
.sw small{font-size:10.5px;color:var(--dim)}
.sw input{position:absolute;opacity:0;width:1px;height:1px}
.sw>i{width:32px;height:18px;border-radius:9px;background:var(--panel2);border:1px solid #333;position:relative;flex:none;transition:background .15s}
.sw>i::after{content:"";position:absolute;top:2px;left:2px;width:12px;height:12px;border-radius:50%;background:var(--mute);transition:left .15s}
.sw input:checked+i{background:var(--accent);border-color:var(--accent)}
.sw input:checked+i::after{left:16px;background:#fff}
.sw input:focus-visible+i{outline:2px solid var(--edge-hi);outline-offset:2px}
.sw input:disabled+i{opacity:.35}
.coinpick{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
.coinpick button{font:600 11px var(--mono);padding:4px 8px;border-radius:5px;border:1px solid #333;background:var(--panel2);color:var(--mute);cursor:pointer}
.coinpick button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.coinpick.off{opacity:.35;pointer-events:none}
.dfoot{margin-top:14px;font-size:11.5px;color:var(--mute);line-height:1.45}
.dfoot p{margin:0}
.cmd{display:block;font:10.5px/1.5 var(--mono);background:#050505;border:1px solid var(--line);border-radius:6px;padding:8px;margin:6px 0 8px;word-break:break-all;color:var(--ink);user-select:all}
.dbtns{display:flex;gap:6px}
.btn{font:500 12px Inter,sans-serif;padding:5px 12px;border-radius:6px;border:1px solid #333;background:var(--panel2);color:#ddd;cursor:pointer}
.btn:hover{color:#fff;border-color:var(--edge-hi)}

/* header */
.top{display:flex;flex-direction:column;align-items:center;gap:4px;margin:4px 0 10px;text-align:center}
h1{font-family:var(--mono);font-weight:700;font-size:clamp(24px,5.2vw,34px);line-height:1.15;margin:0;letter-spacing:-.015em;color:#fff;
  text-shadow:0 0 8px var(--bg),0 0 16px var(--bg),0 0 28px var(--bg),0 0 40px var(--bg)}
h1 .amp{color:var(--accent)}
.sub{text-shadow:0 0 6px var(--bg),0 0 14px var(--bg)}
.sub{font-family:var(--mono);color:var(--mute);font-size:11.5px;display:flex;gap:14px;flex-wrap:wrap;align-items:center;justify-content:center}
.pill{display:inline-flex;align-items:center;gap:6px}

/* grid */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(142px,1fr));gap:var(--gap);margin-bottom:var(--gap)}
.row{display:grid;gap:var(--gap);margin-bottom:var(--gap)}
.r2{grid-template-columns:minmax(0,2.2fr) minmax(0,1fr)}
.r3{grid-template-columns:minmax(0,1fr) minmax(0,1.35fr) minmax(0,1fr)}
@media(max-width:1100px){.r3{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.r3>.card:last-child{grid-column:1/-1}}
@media(max-width:860px){.r2,.r3{grid-template-columns:minmax(0,1fr)}.r3>.card:last-child{grid-column:auto}}

.card{text-shadow:0 1px 2px rgba(0,0,0,.9);background:var(--panel-a);-webkit-backdrop-filter:var(--blur);backdrop-filter:var(--blur);border:1px solid var(--edge);border-radius:8px;padding:12px 14px;min-width:0;transition:border-color .2s}
.card:hover{border-color:var(--edge-hi)}
.ch{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap}
.ctrls{display:flex;gap:6px;flex-wrap:wrap}
@media(max-width:520px){.seg button{padding:5px 9px}.ctrls{width:100%}}
h2{font-family:var(--pixel);font-weight:500;font-size:17px;margin:0}
h3{font-family:var(--mono);font-size:10.5px;font-weight:400;color:var(--mute);text-transform:uppercase;letter-spacing:.08em;margin:12px 0 4px}
.hint{font-family:var(--mono);font-size:10.5px;color:var(--mute)}

/* kpi cards */
.lbl{font-family:var(--mono);font-size:10.5px;color:var(--mute);text-transform:uppercase;letter-spacing:.08em;display:flex;gap:7px;align-items:center;white-space:nowrap;overflow:hidden}
.dot{width:8px;height:8px;border-radius:1px;flex:none;display:inline-block}
@media(max-width:1380px){.kpis .lbl{letter-spacing:.03em;font-size:10px;gap:6px}}
.big{font-family:var(--mono);font-weight:700;font-size:26px;line-height:1.15;margin:6px 0 2px;font-variant-numeric:tabular-nums;white-space:nowrap;letter-spacing:-.02em}
.small{font-size:11.5px;color:var(--mute);line-height:1.35}
.bar{height:5px;background:var(--panel2);border-radius:2px;margin-top:8px;overflow:hidden}
.bar>i{display:block;height:100%}
.kspark{width:100%;height:22px;margin-top:6px;display:block}
.sub2{font-size:15px;color:var(--mute);font-weight:500}
.kpis>.wide{grid-column:span 2}
@media(max-width:330px){.kpis>.wide{grid-column:auto}}
.mini{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:3px 10px;margin-top:8px;font-family:var(--mono);font-size:11.5px;align-items:center}
.mini span{display:flex;align-items:center;gap:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--mute)}
.mini b{font-weight:700;text-align:right}.mini em{font-style:normal;color:var(--mute);text-align:right}

/* chart */
.seg{display:flex;gap:3px;background:var(--panel2);padding:3px;border-radius:7px}
.seg button{font:500 12.5px Inter,sans-serif;padding:5px 12px;border:0;border-radius:5px;background:transparent;color:#d6d6d6;cursor:pointer;transition:background .15s,color .15s}
.seg button:hover{color:#fff}
.seg button.on{background:var(--accent);color:#fff}
.chartcard{display:flex;flex-direction:column}
.chartbox{position:relative;flex:1;min-height:210px}
.legend{display:flex;gap:4px 14px;flex-wrap:wrap;font-family:var(--mono);font-size:10.5px;color:var(--mute);margin-bottom:6px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.swatch{width:9px;height:9px;border-radius:1px;display:inline-block}
.swatch-limit{width:0;height:11px;border-left:2px dashed var(--accent)}
.swatch-out{width:11px;height:11px;background:rgba(255,69,58,.28);border-radius:1px}

/* status */
.status-top{display:flex;align-items:center;gap:9px}
.status-top b{font-family:var(--pixel);font-weight:500;font-size:19px}
.comps{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.comps span{font-family:var(--mono);font-size:10.5px;padding:3px 7px;border-radius:4px;background:var(--panel2);display:inline-flex;gap:6px;align-items:center}
.incs{max-height:172px;overflow-y:auto}
.inc{display:grid;grid-template-columns:auto 1fr auto;gap:2px 10px;padding:7px 0;border-bottom:1px solid var(--line);font-size:12.5px;align-items:baseline}
.inc:last-child{border-bottom:0}
.inc .when,.inc .dur{font-family:var(--mono);font-size:11px;color:var(--mute);white-space:nowrap}
.inc .meta{grid-column:2/-1;font-size:10.5px;color:var(--dim);font-family:var(--mono)}
.tag{font-family:var(--mono);font-size:9.5px;text-transform:uppercase;letter-spacing:.06em;padding:1px 5px;border-radius:3px;margin-left:6px;vertical-align:1px;background:var(--panel2)}

/* tables */
.scroll{overflow:auto}
.scroll,.incs{scrollbar-width:none;-ms-overflow-style:none}
.scroll::-webkit-scrollbar,.incs::-webkit-scrollbar{display:none;width:0;height:0}
.tall{max-height:212px}
table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 6px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:0}
th{font-family:var(--mono);font-size:10px;color:var(--mute);font-weight:400;text-transform:uppercase;letter-spacing:.07em;position:sticky;top:0;background:rgba(10,10,10,.85)}
td{font-family:var(--mono)}td.r,th.r{text-align:right}
td.trunc{max-width:170px;overflow:hidden;text-overflow:ellipsis}
.r3>.card:last-child td.trunc{max-width:130px}

/* heatmap */
.heatcard{display:flex;flex-direction:column}
/* session drill-down */
#sessions tr.click{cursor:pointer}
#sessions tr.click:hover td,#sessions tr.click:focus td{background:rgba(255,255,255,.04)}
#sessions tr.click:focus{outline:none}
.modal{position:fixed;inset:0;z-index:40;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.55);padding:16px}
.modal[hidden]{display:none}
.mbox{width:min(760px,100%);max-height:calc(100vh - 32px);overflow:auto;scrollbar-width:none;background:rgba(14,14,14,.97);border:1px solid var(--edge-hi);
  border-radius:10px;padding:16px 18px 18px;box-shadow:0 18px 60px rgba(0,0,0,.7)}
.mbox::-webkit-scrollbar{display:none}
.mstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px;margin:12px 0 4px}
.mstats div{background:var(--panel2);border-radius:6px;padding:8px 10px}
.mstats b{display:block;font:700 17px var(--mono);margin-top:2px}
.tl{width:100%;height:120px;display:block;margin-top:6px}
.tl rect:hover{opacity:.7}
.toolbars{display:grid;grid-template-columns:auto 1fr auto;gap:5px 10px;align-items:center;font:12px var(--mono);margin-top:6px}
.toolbars i{display:block;height:8px;border-radius:2px;background:var(--accent)}
.toolbars em{font-style:normal;color:var(--mute);text-align:right}
/* model advice */
.tip:not([hidden])+.scroll{max-height:98px!important}
.tip{font-size:11px;line-height:1.35;cursor:help;color:var(--mute);background:rgba(74,222,128,.07);border:1px solid rgba(74,222,128,.22);border-radius:6px;padding:6px 8px;margin:-2px 0 8px}
.tip b{color:var(--ink)}
.tip[hidden]{display:none}
.fc{color:var(--amber)}.fc.bad{color:var(--down)}
.hintline{color:var(--dim);cursor:help;border-bottom:1px dotted var(--dim)}
.heat{flex:1;min-height:132px;display:grid;grid-template-columns:28px repeat(24,minmax(0,1fr));grid-template-rows:auto repeat(7,minmax(10px,1fr));
  gap:2px;font-family:var(--mono);font-size:9.5px;color:var(--mute)}
.heat i{border-radius:2px;background:var(--panel2);display:block}
.heatsum{display:flex;gap:4px 14px;flex-wrap:wrap;font-family:var(--mono);font-size:10.5px;color:var(--mute);margin-top:10px}
.heatsum b{color:var(--ink);font-weight:600}
.heat .hl{align-self:center}
.heat .hh{text-align:center;overflow:visible;white-space:nowrap}

.up{color:var(--up)}.down{color:var(--down)}
.note{font-family:var(--mono);font-size:10.5px;color:var(--dim);margin:6px 2px 0;line-height:1.55}
</style><style id="theme">__THEME__</style></head><body>
<pre id="ascii" aria-hidden="true"></pre>
<div class="ticker"><div class="tkwrap"><div class="coins" id="coins" aria-label="Crypto prices" tabindex="0"></div>
  <div class="tkbar" id="tkbar" hidden aria-hidden="true"><i id="tkthumb"></i></div></div><span class="ver" id="ver"></span>
  <button class="gear" id="gear" aria-expanded="false" aria-controls="drawer">⚙ Settings</button></div>
<div class="drawer" id="drawer" role="dialog" aria-labelledby="dtitle" hidden>
  <div class="dh"><h2 id="dtitle">Settings</h2><button class="x" id="dclose" aria-label="Close settings">✕</button></div>
  <h3>Trackers</h3><div id="s-tools"></div>
  <div id="s-git-wrap"><h3>Git</h3><div id="s-git"></div></div>
  <h3>Display</h3><div id="s-display"></div>
  <h3>Coins in the ticker</h3><div class="coinpick" id="s-coins"></div>
  <div class="dfoot"><p>Saved in this browser. To make these the defaults everywhere, run:</p>
    <code class="cmd" id="s-cmd"></code>
    <div class="dbtns"><button class="btn" id="s-copy">Copy command</button><button class="btn" id="s-reset">Reset</button></div>
    <h3 style="margin-top:14px">Export</h3>
    <div class="dbtns"><button class="btn" id="x-csv">Daily CSV</button><button class="btn" id="x-json">Full JSON</button></div>
    <p style="margin-top:6px">Every single request: <code>usage.py --export</code></p></div>
</div>
<div class="modal" id="modal" hidden><div class="mbox" role="dialog" aria-modal="true" aria-labelledby="mtitle" id="mbox"></div></div>
<main>
<header class="top"><h1>Build <span class="amp">&amp;</span> Commit Pro Tracker</h1><div class="sub" id="sub"></div></header>

<section class="kpis" id="cards"></section>

<section class="row r2">
  <div class="card chartcard">
    <div class="ch" style="margin-bottom:4px"><h2 id="charttitle">Hourly · last 48h</h2>
      <div class="ctrls"><div class="seg" id="metricseg"><button data-m="tokens" class="on">Tokens</button><button data-m="cost">Cost</button><button data-m="commits" id="m-commits">Commits</button></div>
      <div class="seg" id="viewseg" role="tablist" aria-label="Time range"><button data-v="h24" role="tab">24h</button><button data-v="h48" role="tab" class="on">48h</button><button data-v="h72" role="tab">72h</button><button data-v="d7" role="tab">7d</button><button data-v="d14" role="tab">14d</button><button data-v="daily" role="tab">30d</button></div></div></div>
    <div class="legend" id="legend"></div>
    <div class="chartbox"><canvas id="chart"></canvas></div>
  </div>
  <div class="card"><div class="ch"><h2>Claude status</h2><span class="hint" id="statushint"></span></div><div id="status"></div></div>
</section>

<section class="row r3">
  <div class="card heatcard"><div class="ch"><h2>When you work</h2><span class="hint">tokens by hour · 30d</span></div><div class="heat" id="heat"></div><div class="heatsum" id="heatsum"></div></div>
  <div class="card"><div class="ch"><h2>Recent sessions</h2><span class="hint">latest 20</span></div><div class="scroll tall"><table id="sessions"></table></div></div>
  <div class="card"><div class="ch"><h2>Models</h2><span class="hint">30d</span></div><div class="tip" id="tip" hidden></div><div class="scroll" style="max-height:120px"><table id="models"></table></div>
    <h3>Projects</h3><div class="scroll" style="max-height:84px"><table id="projects"></table></div></div>
</section>

<p class="note">Tokens = input + output + cache writes (cache reads listed separately). Cost = what the same tokens would cost at API list
prices (Aider and OpenCode figures use the tool's own cost where it reports one); it's an estimate, not a bill. Claude 5h gauge uses
the median usage at your real limit hits once there's one, otherwise your busiest 5h window. Codex % from Codex's own reports.
Status: status.claude.com · prices: CoinMarketCap or CoinGecko, informational only. Settings: <code>usage.py --settings</code>.</p>
</main><script>
const D = __DATA__;
const C = Object.assign({}, D.colors, {"Claude Code":"var(--claude)"});
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v.slice(4,-1)).trim();
const fmt = x => x==null ? "—" : x>=1e6 ? (x/1e6).toFixed(2)+"M" : x>=1e3 ? (x/1e3).toFixed(1)+"k" : String(x);
const usd = v => v==null ? "—" : v>=1000 ? "$"+Math.round(v).toLocaleString("en-US") : v>=100 ? "$"+v.toFixed(0) : v>0 && v<0.01 ? "<$0.01" : "$"+v.toFixed(2);
const sum = o => Object.values(o).reduce((a,b)=>a+b,0);
const esc = s => String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const pad = x => String(x).padStart(2,"0");
const when = t => {const d=new Date(t*1000); return `${pad(d.getDate())}/${pad(d.getMonth()+1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;};
const dur = s => { if(s==null||s<0) return "—"; const h=Math.floor(s/3600), m=Math.round(s%3600/60); return h? `${h}h ${m}m` : `${m}m`; };
document.getElementById("ver").textContent = "v" + (D.version || "?");
const $ = q => document.querySelector(q);
const DAYS=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];

/* ---- viewer preferences: saved in this browser, defaults come from the settings file ---- */
const PKEY = "ai-usage-prefs";
const DEF = {hidden:D.hidden||[], prices:true, animate:D.animate, coins:D.coins, gitMine:D.git.mine_default, privacy:D.privacy, view:"h48"};
let P = (()=>{ try{ const s=JSON.parse(localStorage.getItem(PKEY)||"null"); if(s&&typeof s==="object") return {...DEF, ...s}; }catch(e){} return {...DEF}; })();
function savePrefs(){ try{ localStorage.setItem(PKEY, JSON.stringify(P)); }catch(e){} }
const on = s => !P.hidden.includes(s);
// screenshot mode: stable stand-in names, assigned in order of appearance
const ALIAS = {};
[...D.projects.map(p=>p[0]), ...D.sessions.map(s=>s.project), ...D.git.repos].forEach(n=>{ if(!(n in ALIAS)) ALIAS[n]="Project "+String.fromCharCode(65+Object.keys(ALIAS).length%26)+(Object.keys(ALIAS).length>=26?Math.floor(Object.keys(ALIAS).length/26):""); });
const pname = n => P.privacy ? (ALIAS[n]||"Project") : n;
const VIS = () => D.sources.filter(on);
const hasGit = () => D.git.enabled && D.git.commits.length>0;
const gitOn = () => hasGit() && on("Git");
function gitStats(){
  const dk=t=>{const d=new Date(t*1000);return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;};
  const hk=t=>{const d=new Date(t*1000);return `${dk(t)} ${pad(d.getHours())}`;};
  const g={n:0, ai:0, added:0, deleted:0, daily:{}, hourly:{}, repo:{}, tool:{}};
  D.days.forEach(d=>g.daily[d]=[0,0]); D.hours.forEach(h=>g.hourly[h]=[0,0]);
  for(const [ts,ri,a,dl,ti,mine] of D.git.commits){
    if(P.gitMine && !mine) continue;
    const k=ti>=0?0:1; g.n++; if(!k) g.ai++; g.added+=a; g.deleted+=dl;
    if(g.daily[dk(ts)]) g.daily[dk(ts)][k]++;
    if(g.hourly[hk(ts)]) g.hourly[hk(ts)][k]++;
    const name=D.git.repos[ri], r=g.repo[name]||(g.repo[name]=[0,0]); r[0]++; if(!k) r[1]++;
    if(ti>=0){ const t=D.git.tools[ti]; g.tool[t]=(g.tool[t]||0)+1; }
  }
  return g;
}

/* ---- ASCII field ---- */
(function(){
  const el=document.getElementById("ascii"), ramp=" ..--//||\\\\pp@@@";
  const still=matchMedia("(prefers-reduced-motion: reduce)").matches;
  let cols=0, rows=0;
  function size(){cols=Math.ceil(innerWidth/7.2)+1; rows=Math.ceil(innerHeight/15)+1;}
  function draw(t){
    const cx=cols/2, cy=rows*0.35, out=[];
    for(let y=0;y<rows;y++){let line="";
      for(let x=0;x<cols;x++){
        const dx=(x-cx)*0.5, dy=y-cy, d=Math.sqrt(dx*dx+dy*dy);
        const v=(Math.sin(d*0.42-t)+Math.sin(d*0.11-t*0.5))*0.25+0.5;
        line+=ramp[Math.min(ramp.length-1,Math.max(0,Math.floor(v*v*ramp.length)))];
      } out.push(line);}
    el.textContent=out.join("\n");
  }
  size(); draw(0); addEventListener("resize",()=>{size();draw(0)});
  if(still) return;
  let last=0; (function loop(ts){ if(P.animate && !document.hidden && ts-last>90){draw(ts/1400); last=ts;} requestAnimationFrame(loop); })(0);
})();

/* ---- header ---- */
const STATUS_COL = {none:"var(--up)",operational:"var(--up)",minor:"var(--amber)",degraded_performance:"var(--amber)",
  partial_outage:"var(--down)",major:"var(--down)",major_outage:"var(--down)",critical:"var(--down)",under_maintenance:"var(--codex)",maintenance:"var(--codex)"};
function renderSub(){
  const st=D.status;
  const s = st ? `<span class="pill"><i class="dot" style="background:${STATUS_COL[st.indicator]||"var(--mute)"};border-radius:50%"></i>Claude: ${esc(st.description.toLowerCase())}</span>` : "";
  document.getElementById("sub").innerHTML = `<span>updated ${D.generated}</span><span>${D.row_count.toLocaleString()} entries / 30d</span>${s}`+
    (P.privacy?`<span class="pill" title="Project names are hidden (⚙ Settings)">🙈 names hidden</span>`:"");
}
renderSub();

/* ---- kpi cards ---- */
function sparkSvg(vals,color,h=22){
  const pts=vals.map((v,i)=>[i,v]).filter(p=>p[1]!=null);
  if(pts.length<2) return "";
  const lo=Math.min(...pts.map(p=>p[1])), hi=Math.max(...pts.map(p=>p[1])), r=hi-lo||1, n=vals.length-1;
  return `<svg class="kspark" viewBox="0 0 100 ${h}" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="1.4" vector-effect="non-scaling-stroke" points="${pts.map(([i,v])=>`${(i/n*100).toFixed(2)},${(h-1-(v-lo)/r*(h-2)).toFixed(2)}`).join(" ")}"/></svg>`;
}
function card(src,label,value,note,pct,extra=""){
  const bar = pct==null ? "" : `<div class="bar"><i style="width:${Math.min(pct,100)}%;background:${pct>85?"var(--warn)":C[src]}"></i></div>`;
  return `<div class="card"><div class="lbl"><span class="dot" style="background:${C[src]||src}"></span>${label}</div>
  <div class="big">${value}</div><div class="small">${note}</div>${bar}${extra}</div>`;
}
function resetText(w){
  if(!w) return "";
  let secs = w.resets_in_seconds;
  if(secs==null && w.resets_at) secs = w.resets_at - Date.now()/1000;
  if(secs==null) return "";
  secs -= (Date.now()/1000 - D.codex_limits.ts); if(secs<=0) return "window has reset";
  return `resets in ${dur(secs)}`;
}
const L = D.codex_limits && D.codex_limits.limits;
const hasCodex = !!L || D.sources.includes("Codex");
function codexPace(){
  const cf=(D.forecast||{}).codex||{}, out=[];
  for(const [k,label] of [["primary","5h"],["secondary","week"]]){
    const f=cf[k]; if(!f) continue;
    if(f.hit_ts){ const d=new Date(f.hit_ts*1000); out.push(`<span class="fc bad">${label} limit ~${DAYS[(d.getDay()+6)%7]} ${pad(d.getHours())}:${pad(d.getMinutes())}</span>`); }
    else if(k==="secondary") out.push(`<span title="projected use when the weekly window resets">week on pace for ${Math.round(f.projected)}%</span>`);
  }
  return out.length ? "<br>"+out.join(" · ") : "";
}
function renderCards(){
  let h = "";
  const cn = D.win5["Claude Code"], vs = VIS();
  if(on("Claude Code")){
  const F=D.forecast||{}, at=t=>{const d=new Date(t*1000), soon=t-Date.now()/1000<20*3600; return soon?`${pad(d.getHours())}:${pad(d.getMinutes())}`:`${DAYS[(d.getDay()+6)%7]} ${pad(d.getHours())}:${pad(d.getMinutes())}`;};
  if(D.limit_est){
    const eta=F.claude5h&&F.claude5h.eta_ts>Date.now()/1000 ? `<br><span class="fc bad">limit ~${at(F.claude5h.eta_ts)} at this pace</span>` : "";
    h += card("Claude Code","Claude · 5h",fmt(cn), `${Math.round(cn/D.limit_est*100)}% of est. limit (${fmt(D.limit_est)})${eta}`, cn/D.limit_est*100);
  } else {
    const cp = D.peak5["Claude Code"];
    const why=`Counts Claude Code on this computer only. Your Claude limit is shared with the Claude app and claude.ai, which aren't stored locally; see Settings → Usage in Claude for the full figure. The forecast and the ${D.alert_pct||85}% alert start once Claude Code has hit a limit, so the tracker knows where your limit sits.`;
    h += card("Claude Code","Claude · 5h",fmt(cn), (cp ? `${Math.round(cn/cp*100)}% of busiest 5h (${fmt(cp)})` : "no history yet")+
      `<br><span class="hintline" title="${esc(why)}">forecast after first limit hit ⓘ</span>`, cp? cn/cp*100 : null);
  }
  const hits=D.limits.filter(l=>l.source==="Claude Code").length;
  const d7=D.days.slice(-7).map(d=>D.daily["Claude Code"][d]);
  const wk=(F.week||{})["Claude Code"], vsAvg=wk&&wk.avg_prev ? (wk.now/wk.avg_prev-1)*100 : null;
  h += card("Claude Code","Claude · 7d",fmt(D.week["Claude Code"]),
    (vsAvg==null?"":`<span class="${vsAvg>25?"fc":""}">${vsAvg>=0?"+":""}${vsAvg.toFixed(0)}% vs 3-wk avg</span> · `)+`${hits} limit hit${hits===1?"":"s"}`,null,sparkSvg(d7,css("var(--claude)")));
  }
  const tot=vs.reduce((a,s)=>a+D.cost30[s],0), plan=vs.reduce((a,s)=>a+(D.plans[s]||0),0), dc=D.days.slice(-14).map(d=>vs.reduce((a,s)=>a+D.daily_cost[s][d],0));
  h += card("var(--up)","API value · 30d", usd(tot),
    plan ? `vs ${usd(plan)}/mo in plans · <b>${(tot/plan).toFixed(1)}×</b>` : "at API list prices", null, sparkSvg(dc,css("var(--up)")));
  const c=D.cache;
  const cnote = c.now==null ? "no Claude usage this week" : c.prev==null ? "input served from cache" :
    `<span class="${c.now>=c.prev?"up":"down"}">${c.now>=c.prev?"▲":"▼"} ${Math.abs(c.now-c.prev).toFixed(1)} pts</span> vs prior week`;
  if(on("Claude Code")) h += card("var(--accent)","Cache hit · 7d", c.now==null?"—":c.now+"%", cnote, null, sparkSvg(c.daily,css("var(--accent)")));
  if(hasCodex && on("Codex")){
    const bar=(p)=>`<div class="bar"><i style="width:${Math.min(p,100)}%;background:${p>85?"var(--warn)":C["Codex"]}"></i></div>`;
    if(L && L.primary){
      const p5=L.primary.used_percent, pw=L.secondary?L.secondary.used_percent:null;
      h += `<div class="card"><div class="lbl"><span class="dot" style="background:${C["Codex"]}"></span>Codex · 5h / wk</div>
        <div class="big">${Math.round(p5)}%${pw!=null?`<span class="sub2"> / ${Math.round(pw)}%</span>`:""}</div>
        <div class="small">${resetText(L.primary)||"used"}${codexPace()}</div>${bar(p5)}${pw!=null?bar(pw):""}</div>`;
    } else h += card("Codex","Codex · 7d",fmt(D.week["Codex"]),"no rate-limit report yet");
  }
  const others=vs.filter(s=>s!=="Claude Code"&&s!=="Codex");
  if(others.length) h += `<div class="card wide"><div class="lbl"><span class="dot" style="background:var(--mute)"></span>Other tools · 7d</div>
    <div class="mini">${others.map(s=>`<span><i class="dot" style="background:${C[s]}"></i>${esc(s)}</span><b>${fmt(D.week[s])}</b><em>${D.cost7[s]?usd(D.cost7[s]):"—"}</em>`).join("")}</div></div>`;
  if(gitOn()){
    const g=gitStats(), pct=g.n?Math.round(g.ai/g.n*100):0, apiv=vs.reduce((a,s)=>a+D.cost30[s],0);
    const tools=Object.entries(g.tool).sort((a,b)=>b[1]-a[1]).map(([t,n])=>`${t} ${n}`).join(", ");
    h += `<div class="card" title="+${g.added.toLocaleString()} / −${g.deleted.toLocaleString()} lines · ${tools?"AI-assisted: "+esc(tools):"no AI-assisted commits"}${g.n&&apiv?` · ${usd(apiv/g.n)} of API usage per commit`:""}"><div class="lbl"><span class="dot" style="background:var(--edge-hi)"></span>Commits · 30d</div>
      <div class="big">${g.n}</div><div class="small">${pct}% with AI · <span class="up">+${fmt(g.added)}</span> <span class="down">−${fmt(g.deleted)}</span></div>
      ${sparkSvg(D.days.slice(-14).map(d=>g.daily[d][0]+g.daily[d][1]),css("var(--edge-hi)"))}</div>`;
  }
  document.getElementById("cards").innerHTML = h;
}
renderCards();

/* ---- main chart with limit + outage marks ---- */
Chart.defaults.font.family = "JetBrains Mono, monospace";
Chart.defaults.font.size = 10.5;
let metric="tokens";
const mfmt = v => metric==="cost" ? usd(v) : metric==="commits" ? String(Math.round(v)) : fmt(v);
const hourView = n => ({title:`Hourly · last ${n}h`, keys:D.hours.slice(-n), src:D.hourly, csrc:D.hourly_cost,
    t0:D.hour0+(D.hours.length-Math.min(n,D.hours.length))*3600, step:3600,
    label:k=>{const h=k.slice(11); return h==="00" ? k.slice(8,10)+"/"+k.slice(5,7) : h+":00";},
    tip:k=>`${k.slice(8,10)}/${k.slice(5,7)} ${k.slice(11)}:00–${k.slice(11)}:59`});
const dayView = n => ({title:`Daily · last ${n}d`, keys:D.days.slice(-n), src:D.daily, csrc:D.daily_cost,
    t0:D.day0+(D.days.length-Math.min(n,D.days.length))*86400, step:86400,
    label:k=>k.slice(8,10)+"/"+k.slice(5,7), tip:k=>{const d=new Date(k+"T12:00:00"); return `${DAYS[(d.getDay()+6)%7]} ${k.slice(8,10)}/${k.slice(5,7)}`;}});
const VIEWS = {
  h24:hourView(24), h48:hourView(48), h72:hourView(72),
  d7:dayView(7), d14:dayView(14), daily:dayView(30)
};
const isDaily = v => !v.startsWith("h");
let view = VIEWS[P.view] ? P.view : "h48";
const marks = {id:"marks", beforeDatasetsDraw(ch){
  const V=VIEWS[view], xs=ch.scales.x, {top,bottom,left,right}=ch.chartArea, ctx=ch.ctx, n=V.keys.length;
  const w=(xs.getPixelForValue(n-1)-xs.getPixelForValue(0))/Math.max(n-1,1);
  const px=t=>xs.getPixelForValue(0)+((t-V.t0)/V.step-0.5)*w;
  ctx.save(); ctx.beginPath(); ctx.rect(left,top,right-left,bottom-top); ctx.clip();
  for(const inc of D.incidents){
    const a=px(inc.start), b=px(inc.resolved||Date.now()/1000);
    if(b<left||a>right) continue;
    ctx.fillStyle = (inc.impact==="major"||inc.impact==="critical") ? "rgba(255,69,58,.28)" : "rgba(255,69,58,.14)";
    ctx.fillRect(a,top,Math.max(b-a,2),bottom-top);
  }
  ctx.strokeStyle=css("var(--accent)"); ctx.lineWidth=1.5; ctx.setLineDash([4,3]);
  for(const l of D.limits.filter(l=>on(l.source))){
    const x=px(l.ts); if(x<left||x>right) continue;
    ctx.beginPath(); ctx.moveTo(x,top); ctx.lineTo(x,bottom); ctx.stroke();
  }
  ctx.restore();
}};
const colorOf = s => { const c=C[s]||"#999"; return c.startsWith("var(") ? css(c) : c; };
function dataFor(v){const V=VIEWS[v];
  if(metric==="commits"){ const g=gitStats(), src=isDaily(v)?g.daily:g.hourly;
    return {labels:V.keys.map(V.label), datasets:[["With AI",0,css("var(--accent)")],["Without AI",1,"#5f5f5f"]].map(([label,i,col])=>
      ({label,data:V.keys.map(k=>(src[k]||[0,0])[i]),backgroundColor:col,borderRadius:2,maxBarThickness:24}))}; }
  const src=metric==="cost"?V.csrc:V.src;
  const ds=VIS().map(s=>({label:s,data:V.keys.map(k=>src[s][k]),backgroundColor:colorOf(s),borderRadius:2,maxBarThickness:24}));
  const T=(D.forecast||{}).today;
  if(isDaily(v) && T && T[metric]){
    const today=V.keys.length-1, actual=VIS().reduce((a,s)=>a+src[s][V.keys[today]],0), proj=VIS().reduce((a,s)=>a+(T[metric][s]||0),0);
    if(proj>actual) ds.push({label:"Projected rest of today",data:V.keys.map((k,i)=>i===today?proj-actual:0),
      backgroundColor:"rgba(255,255,255,.08)",borderColor:"rgba(255,255,255,.45)",borderWidth:{top:1,left:1,right:1,bottom:0},borderRadius:2,maxBarThickness:24});
  }
  return {labels:V.keys.map(V.label), datasets:ds};}
const chart = new Chart(document.getElementById("chart"),{type:"bar", data:dataFor(view), plugins:[marks],
  options:{responsive:true,maintainAspectRatio:false,
    layout:{padding:{top:4,right:2,bottom:0,left:0}},
    plugins:{legend:{display:false},
    tooltip:{backgroundColor:"#1c1c1c",borderColor:"#333",borderWidth:1,titleColor:"#fff",bodyColor:"#d6d6d6",padding:9,
      callbacks:{title:it=>VIEWS[view].tip(VIEWS[view].keys[it[0].dataIndex]),label:c=>` ${c.dataset.label}: ${mfmt(c.raw)}`}}},
    scales:{x:{stacked:true,grid:{display:false},ticks:{color:css("var(--mute)"),maxRotation:0,autoSkipPadding:10}},
            y:{stacked:true,grid:{color:css("var(--line)")},border:{display:false},ticks:{color:css("var(--mute)"),callback:v=>mfmt(v)}}}}});
function renderLegend(){
  $("#m-commits").hidden = !gitOn();
  if(metric==="commits" && !gitOn()){ metric="tokens"; document.querySelectorAll("#metricseg button").forEach(x=>x.classList.toggle("on",x.dataset.m==="tokens")); }
  if(metric==="commits"){ $("#legend").innerHTML=`<span><i class="swatch" style="background:var(--accent)"></i>With AI</span><span><i class="swatch" style="background:#5f5f5f"></i>Without AI</span>`+
    `<span><i class="swatch-limit"></i>limit hit</span><span><i class="swatch-out"></i>incident</span>`; return; }
  $("#legend").innerHTML = (isDaily(view)&&(D.forecast||{}).today&&(D.forecast.today||{}).frac?`<span><i class="swatch" style="background:rgba(255,255,255,.12);outline:1px solid rgba(255,255,255,.45)"></i>projected today</span>`:"") +
  VIS().map(s=>`<span><i class="swatch" style="background:${C[s]}"></i>${esc(s)}</span>`).join("") +
  `<span><i class="swatch-limit"></i>limit hit</span><span><i class="swatch-out"></i>incident</span>`; }
renderLegend();
function setView(v){
  view=v; P.view=v; savePrefs();
  document.querySelectorAll("#viewseg button").forEach(x=>{ const on=x.dataset.v===v; x.classList.toggle("on",on); x.setAttribute("aria-selected",String(on)); });
  document.getElementById("charttitle").textContent=VIEWS[view].title;
  renderLegend(); chart.data=dataFor(view); chart.update();
}
document.querySelectorAll("#viewseg button").forEach(b=>b.onclick=()=>setView(b.dataset.v));
document.querySelectorAll("#viewseg button").forEach(x=>{ const on=x.dataset.v===view; x.classList.toggle("on",on); x.setAttribute("aria-selected",String(on)); });
document.getElementById("charttitle").textContent=VIEWS[view].title;
document.querySelectorAll("#metricseg button").forEach(b=>b.onclick=()=>{
  metric=b.dataset.m; document.querySelectorAll("#metricseg button").forEach(x=>x.classList.toggle("on",x===b));
  renderLegend(); chart.data=dataFor(view); chart.update();
});

/* ---- heatmap ---- */
function renderHeat(){
  const vs=VIS(), grid=DAYS.map((_,d)=>Array.from({length:24},(_,hr)=>vs.reduce((a,s)=>a+(((D.heat_by[s]||[])[d]||[])[hr]||0),0)));
  const flat=grid.flat(), max=Math.max(1,...flat), total=flat.reduce((a,b)=>a+b,0);
  const hx=(css("var(--accent)").match(/^#?([0-9a-f]{6})$/i)||[,"ff6a13"])[1], rgb=[0,2,4].map(i=>parseInt(hx.slice(i,i+2),16)).join(",");
  let h="<span></span>"+Array.from({length:24},(_,i)=>`<span class="hh">${i%3===0?pad(i):""}</span>`).join("");
  grid.forEach((row,d)=>{
    h+=`<span class="hl">${DAYS[d]}</span>`+row.map((v,hr)=>{
      const a=v? 0.15+0.85*Math.sqrt(v/max) : 0;
      return `<i title="${DAYS[d]} ${pad(hr)}:00 · ${fmt(v)} tokens" style="${v?`background:rgba(${rgb},${a.toFixed(2)})`:""}"></i>`;
    }).join("");
  });
  $("#heat").innerHTML=h;
  if(!total){ $("#heatsum").innerHTML="<span>No usage in the last 30 days.</span>"; return; }
  const argmax=a=>a.indexOf(Math.max(...a)), peak=flat.indexOf(max);
  const byHour=Array.from({length:24},(_,hr)=>grid.reduce((a,row)=>a+row[hr],0)), byDay=grid.map(r=>r.reduce((a,b)=>a+b,0));
  const active=D.days.filter(d=>vs.some(s=>D.daily[s][d]>0)).length, bh=argmax(byHour);
  $("#heatsum").innerHTML=`<span>Peak <b>${DAYS[Math.floor(peak/24)]} ${pad(peak%24)}:00</b></span><span>Top hour <b>${pad(bh)}:00</b></span>`+
    `<span>Top day <b>${DAYS[argmax(byDay)]}</b></span><span><b>${active}/30</b> days active</span>`;
}
renderHeat();

/* ---- sessions / models / projects ---- */
function renderTables(){
document.getElementById("sessions").innerHTML =
  `<tr><th>Session</th><th>Started</th><th class="r">Length</th><th class="r">Calls</th><th class="r">Tokens</th><th class="r">Cost</th><th>Model</th></tr>` +
  (D.sessions.map((s,i)=>[s,i]).filter(([s])=>on(s.source)).map(([s,i])=>`<tr class="click" tabindex="0" data-i="${i}" title="Click for details"><td class="trunc"><span class="dot" style="background:${C[s.source]};margin-right:7px"></span>${esc(pname(s.project))}</td>
   <td>${when(s.start)}</td><td class="r">${s.source==="Hermes"?"—":dur(s.end-s.start)}</td><td class="r">${s.calls}</td>
   <td class="r">${fmt(s.tokens)}</td><td class="r">${s.cost?usd(s.cost):"—"}</td><td class="trunc" title="${esc(s.model)}">${esc(s.model)}</td></tr>`).join("") || `<tr><td>No sessions yet</td></tr>`);

document.getElementById("models").innerHTML =
  `<tr><th>Model</th><th class="r">In</th><th class="r">Out</th><th class="r" title="cache reads (hover a row for cache writes)">Cache</th><th class="r">Cost</th></tr>` +
  (D.models.filter(([s])=>on(s)).map(([s,m,i,o,w,r,cost])=>`<tr><td class="trunc" title="${esc(m)}"><span class="dot" style="background:${C[s]||"#999"};margin-right:7px"></span>${esc(m)}</td>
  <td class="r">${fmt(i)}</td><td class="r">${fmt(o)}</td><td class="r" title="cache writes ${fmt(w)} · reads ${fmt(r)}">${fmt(r)}</td><td class="r" ${cost?"":`title="no price for this model; add one with --set prices.MODEL=[in,out,cache_write,cache_read]"`}>${cost?usd(cost):"—"}</td></tr>`).join("") ||
  `<tr><td>No data yet</td></tr>`);
const gs=gitOn()?gitStats():null;
document.getElementById("projects").innerHTML =
  (gs?`<tr><th>Project</th><th class="r">Tokens</th><th class="r">Cost</th><th class="r" title="commits in the last 30 days (with AI)">Commits</th></tr>`:"") +
  (D.projects.filter(([,,,srcs])=>srcs.some(on)).map(([p,t,cost,srcs])=>`<tr><td class="trunc" title="${esc(pname(p))}">${srcs.map(s=>`<span class="dot" style="background:${C[s]};margin-right:3px"></span>`).join("")}<span style="margin-left:4px">${esc(pname(p))}</span></td><td class="r">${fmt(t)}</td><td class="r">${cost?usd(cost):"—"}</td>${gs?`<td class="r" title="${gs.repo[p]?gs.repo[p][1]+" with AI":"no git commits found"}">${gs.repo[p]?gs.repo[p][0]:"—"}</td>`:""}</tr>`).join("") || `<tr><td>No data yet</td></tr>`);
document.querySelectorAll("#sessions tr.click").forEach(r=>{
  r.onclick=()=>openSession(+r.dataset.i);
  r.onkeydown=e=>{ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); openSession(+r.dataset.i); } };
});
renderAdvice();
}

/* ---- model advice ---- */
function renderAdvice(){
  const el=$("#tip"), tot=VIS().reduce((a,s)=>a+D.cost30[s],0);
  const by={}; for(const a of (D.advice||[]).filter(a=>on(a.source))){ const k=a.from+">"+a.to; const b=by[k]||(by[k]={from:a.from,to:a.to,calls:0,small:0,cs:0,ca:0}); b.calls+=a.calls; b.small+=a.small; b.cs+=a.cost_small; b.ca+=a.cost_alt; }
  const best=Object.values(by).map(b=>({...b,save:b.cs-b.ca})).filter(b=>b.save>=0.5 && b.small/b.calls>=0.2).sort((a,b)=>b.save-a.save)[0];
  if(!best){ el.hidden=true; return; }
  el.hidden=false;
  el.innerHTML=`💡 <b>${Math.round(best.small/best.calls*100)}%</b> of ${esc(best.from)} requests were small: on ${esc(best.to)} ≈<b>${usd(best.save)}</b> less`+(tot?` (${Math.round(best.save/tot*100)}%)`:"");
  el.title=`${best.small} of ${best.calls} ${best.from} requests in the last 30 days wrote under 800 tokens. Priced at ${best.to} rates they'd have cost ${usd(best.ca)} instead of ${usd(best.cs)}. Worth trying the smaller model for quick edits and questions.`;
}

/* ---- session drill-down ---- */
const modal=$("#modal"), mbox=$("#mbox"); let lastFocus=null;
function closeSession(){ modal.hidden=true; if(lastFocus) lastFocus.focus(); }
modal.addEventListener("click",e=>{ if(e.target===modal) closeSession(); });
document.addEventListener("keydown",e=>{ if(e.key==="Escape" && !modal.hidden) closeSession(); });
function openSession(i){
  const s=D.sessions[i]; if(!s) return;
  lastFocus=document.activeElement;
  const tl=s.timeline||[], W=720, H=120, n=Math.max(tl.length,1), bw=Math.max(1,W/n-1);
  const max=Math.max(1,...tl.map(x=>x[1]+x[2]+x[3]));
  const bars=tl.map((x,k)=>{ const v=x[1]+x[2]+x[3], h=Math.max(1,v/max*(H-4)), d=new Date(x[0]*1000);
    return `<rect x="${(k*W/n).toFixed(1)}" y="${(H-h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${C[s.source]&&C[s.source].startsWith("var(")?css(C[s.source]):C[s.source]}"><title>${pad(d.getHours())}:${pad(d.getMinutes())} · in ${fmt(x[1])} · out ${fmt(x[2])} · cache write ${fmt(x[3])} · cache read ${fmt(x[4])} · ${usd(x[5])}</title></rect>`; }).join("");
  const tools=Object.entries(s.tools||{}).sort((a,b)=>b[1]-a[1]).slice(0,10), tmax=Math.max(1,...tools.map(t=>t[1]));
  mbox.innerHTML=`<div class="dh"><h2 id="mtitle"><span class="dot" style="background:${C[s.source]};margin-right:8px"></span>${esc(pname(s.project))}</h2><button class="x" id="mclose" aria-label="Close">✕</button></div>
    <div class="small">${esc(s.source)} · ${when(s.start)} → ${when(s.end)}${s.source==="Hermes"?"":` · ${dur(s.end-s.start)}`}</div>
    <div class="mstats"><div class="small">Requests<b>${s.calls}</b></div><div class="small">Tokens<b>${fmt(s.tokens)}</b></div>
      <div class="small">API value<b>${s.cost?usd(s.cost):"—"}</b></div><div class="small">Cache hit<b>${s.cache_hit==null?"—":s.cache_hit+"%"}</b></div>
      <div class="small">Per request<b>${fmt(Math.round(s.tokens/Math.max(1,s.calls)))}</b></div></div>
    <h3>Tokens per request</h3><svg class="tl" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${bars}</svg>
    <h3>Models</h3><div class="small">${(s.models||[s.model]).map(esc).join(" · ")}</div>
    ${tools.length?`<h3>Claude Code tools used</h3><div class="toolbars">${tools.map(([t,c])=>`<span>${esc(t)}</span><i style="width:${(c/tmax*100).toFixed(0)}%"></i><em>${c}</em>`).join("")}</div>`:""}`;
  modal.hidden=false; $("#mclose").onclick=closeSession; $("#mclose").focus();
}

renderTables();

/* ---- Claude status (embedded, then live) ---- */
const IMPACT_COL={none:"var(--mute)",minor:"var(--amber)",major:"var(--down)",critical:"var(--down)",maintenance:"var(--codex)"};
function renderStatus(){
  const st=D.status, el=document.getElementById("status");
  let h="";
  if(st){
    h+=`<div class="status-top"><i class="dot" style="width:10px;height:10px;border-radius:50%;background:${STATUS_COL[st.indicator]||"var(--mute)"}"></i><b>${esc(st.description)}</b></div>
      <div class="comps">${st.components.map(c=>`<span><i class="dot" style="border-radius:50%;background:${STATUS_COL[c.status]||"var(--mute)"}"></i>${esc(c.name)}</span>`).join("")}</div>`;
  } else h+=`<div class="small">Couldn't reach status.claude.com on the last refresh.</div>`;
  h+=`<h3>Incidents · 30d (${D.incidents.length})</h3><div class="incs">`;
  h+= D.incidents.length ? D.incidents.map(i=>`<div class="inc"><span class="when">${when(i.start)}</span>
      <span>${i.link?`<a href="${esc(i.link)}" target="_blank" rel="noopener">${esc(i.name)}</a>`:esc(i.name)}<span class="tag" style="color:${IMPACT_COL[i.impact]||"var(--mute)"}">${esc(i.impact)}</span></span>
      <span class="dur">${i.resolved?dur(i.resolved-i.start):"ongoing"}</span>
      ${i.components&&i.components.length?`<span class="meta">${esc(i.components.join(" · "))}</span>`:""}</div>`).join("")
    : `<div class="small" style="margin-top:6px">None recorded.</div>`;
  el.innerHTML=h+"</div>";
  document.getElementById("statushint").textContent = st ? `checked ${when(st.fetched)}` : "";
  renderSub(); renderCards();
}
renderStatus();
async function liveStatus(){
  try{
    const r=await fetch("https://status.claude.com/api/v2/summary.json"); if(!r.ok) return;
    const s=await r.json();
    D.status={fetched:Date.now()/1000,indicator:s.status.indicator,description:s.status.description,
      components:s.components.filter(c=>!c.group).map(c=>({name:c.name,status:c.status}))};
    for(const inc of s.incidents||[]){
      const start=Date.parse(inc.created_at)/1000;
      if(!D.incidents.some(x=>x.link===inc.shortlink)) D.incidents.unshift({name:inc.name,impact:inc.impact,start,resolved:null,link:inc.shortlink,components:(inc.components||[]).map(c=>c.name)});
    }
    renderStatus(); chart.update();
  }catch(e){}
}

/* ---- markets ticker ---- */
const money = v => { if(v==null) return "—"; try{ return v.toLocaleString(undefined, v<1 ? {style:"currency",currency:D.currency,maximumSignificantDigits:4}
    : {style:"currency",currency:D.currency,maximumFractionDigits:v<10?4:v>=10000?0:2}); }catch(e){ return v.toPrecision(4)+" "+D.currency; } };
function spark(prices,up){
  if(!prices||prices.length<2) return "";
  const lo=Math.min(...prices), hi=Math.max(...prices), r=hi-lo||1, n=prices.length-1;
  const pts=prices.map((p,i)=>`${(i/n*50).toFixed(1)},${(15-(p-lo)/r*14).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 50 16" preserveAspectRatio="none"><polyline fill="none" stroke="${css(up?"var(--up)":"var(--down)")}" stroke-width="1.3" vector-effect="non-scaling-stroke" points="${pts}"/></svg>`;
}
function renderCoins(p){
  const el=document.getElementById("coins");
  requestAnimationFrame(()=>{ if(typeof syncTicker==="function") syncTicker(); });
  if(!D.show_prices || !P.prices){ el.innerHTML=""; return; }
  if(p && (p.currency||"GBP")!==D.currency) p=null;  // currency changed: wait for a refresh in the new one
  const good=(p&&Array.isArray(p.coins)?p.coins:[]).filter(c=>c&&c.id&&c.symbol&&typeof c.current_price==="number");
  if(!good.length){ el.innerHTML=`<span class="tk-meta">prices unavailable right now</span>`; return; }
  const by=Object.fromEntries(good.map(c=>[c.id,c]));
  const f=v=>v==null?"—":`${v>=0?"+":""}${v.toFixed(2)}%`;
  const pick=P.coins.filter(id=>by[id]);
  if(!pick.length){ el.innerHTML=`<span class="tk-meta">no coins selected — pick some in ⚙ Settings</span>`; return; }
  el.innerHTML = pick.map(id=>{const c=by[id], d24=c.price_change_percentage_24h_in_currency, d7=c.price_change_percentage_7d_in_currency;
    return `<div class="tk" title="${esc(c.name)} · 1h ${f(c.price_change_percentage_1h_in_currency)} · 24h ${f(d24)} · 7d ${f(d7)}">
      <b>${esc(c.symbol.toUpperCase())}</b><span class="px">${money(c.current_price)}</span>
      <span class="ch ${(d24??0)>=0?"up":"down"}">${(d24??0)>=0?"▲":"▼"} ${d24==null?"—":Math.abs(d24).toFixed(2)+"%"}</span>
      ${spark(c.sparkline_in_7d&&c.sparkline_in_7d.price,(d7??0)>=0)}</div>`;}).join("") +
    `<span class="tk-meta">${esc(D.currency)} · 24h · ${esc(p.source||"CoinGecko")} · ${when(p.fetched).slice(6)}</span>`;
}
renderCoins(D.prices);
// ticker scrolling: wheel, drag/click on the track, keyboard arrows (the strip is focusable), touch swipe
const tk=$("#coins"), tkbar=$("#tkbar"), tkthumb=$("#tkthumb");
function syncTicker(){
  const over = tk.scrollWidth > tk.clientWidth + 1;
  tkbar.hidden = !over; if(!over) return;
  tkthumb.style.width = (tk.clientWidth/tk.scrollWidth*100)+"%";
  tkthumb.style.left = (tk.scrollLeft/tk.scrollWidth*100)+"%";
}
tk.addEventListener("scroll", syncTicker, {passive:true});
addEventListener("resize", syncTicker);
tk.addEventListener("wheel", e=>{
  if(tk.scrollWidth>tk.clientWidth && Math.abs(e.deltaY)>Math.abs(e.deltaX)){ tk.scrollLeft+=e.deltaY; e.preventDefault(); }
}, {passive:false});
function seekTicker(clientX){
  const r=tkbar.getBoundingClientRect(), frac=Math.min(1,Math.max(0,(clientX-r.left)/r.width));
  tk.scrollLeft = frac*tk.scrollWidth - tk.clientWidth/2;
}
tkbar.addEventListener("pointerdown", e=>{ tkbar.setPointerCapture(e.pointerId); tkbar.classList.add("drag"); seekTicker(e.clientX); });
tkbar.addEventListener("pointermove", e=>{ if(tkbar.classList.contains("drag")) seekTicker(e.clientX); });
["pointerup","pointercancel"].forEach(t=>tkbar.addEventListener(t, ()=>tkbar.classList.remove("drag")));
syncTicker();
async function livePrices(){
  if(!D.show_prices || !P.prices || (D.prices && D.prices.source==="CoinMarketCap")) return;  // CMC updates via the background refresh
  try{
    const r=await fetch(D.gecko_url);
    if(!r.ok) return; const coins=await r.json();
    if(Array.isArray(coins)&&coins.length){ D.prices={fetched:Date.now()/1000,source:"CoinGecko",currency:D.currency,coins}; renderCoins(D.prices); }
  }catch(e){}
}

/* ---- settings panel ---- */
const drawer=$("#drawer"), gear=$("#gear");
function openDrawer(open){
  drawer.hidden=!open; gear.setAttribute("aria-expanded", String(open));
  if(open){ renderSettings(); const f=drawer.querySelector("input:not(:disabled),button"); if(f) f.focus(); }
}
gear.onclick=e=>{ e.stopPropagation(); openDrawer(drawer.hidden); };
$("#dclose").onclick=()=>{ openDrawer(false); gear.focus(); };
document.addEventListener("keydown",e=>{ if(e.key==="Escape" && !drawer.hidden){ openDrawer(false); gear.focus(); } });
document.addEventListener("click",e=>{ if(e.isTrusted && !drawer.hidden && !drawer.contains(e.target)) openDrawer(false); });
function sw(id,label,checked,disabled,note){
  return `<label class="sw"><span><span>${label}</span>${note?`<small>${note}</small>`:""}</span><input type="checkbox" id="${id}" ${checked?"checked":""} ${disabled?"disabled":""}><i></i></label>`;
}
function renderSettings(){
  $("#s-tools").innerHTML = D.sources.length
    ? D.sources.map((s,k)=>sw("s-t"+k, `<span class="dot" style="background:${C[s]}"></span>${esc(s)}`, on(s))).join("")
    : `<p class="small">No tools found on this machine yet.</p>`;
  D.sources.forEach((s,k)=>{ $("#s-t"+k).onchange=e=>{ P.hidden = e.target.checked ? P.hidden.filter(x=>x!==s) : [...P.hidden, s]; changed(); }; });
  $("#s-git-wrap").hidden = !(D.git.enabled);
  if(D.git.enabled){
    $("#s-git").innerHTML = sw("s-gitshow","Commit tracking", on("Git") && hasGit(), !hasGit(), hasGit()?`${D.git.repos.length} repo${D.git.repos.length===1?"":"s"} found`:"no git repos found for your projects yet")
      + sw("s-gitmine","Only my commits", P.gitMine, !hasGit(), "off = everyone's commits in those repos");
    $("#s-gitshow").onchange=e=>{ P.hidden = e.target.checked ? P.hidden.filter(x=>x!=="Git") : [...P.hidden,"Git"]; changed(); };
    $("#s-gitmine").onchange=e=>{ P.gitMine=e.target.checked; changed(); };
  }
  $("#s-display").innerHTML = sw("s-prices","Crypto prices", P.prices && D.show_prices, !D.show_prices, D.show_prices?"":"turned off in the settings file")
    + sw("s-anim","Moving background", P.animate, false, matchMedia("(prefers-reduced-motion: reduce)").matches?"your system has reduced motion on":"")
    + sw("s-priv","Screenshot mode", P.privacy, false, "hides project and repo names");
  $("#s-priv").onchange=e=>{ P.privacy=e.target.checked; changed(); };
  $("#s-prices").onchange=e=>{ P.prices=e.target.checked; changed(); if(P.prices) livePrices(); };
  $("#s-anim").onchange=e=>{ P.animate=e.target.checked; changed(); };
  const box=$("#s-coins");
  box.classList.toggle("off", !(P.prices && D.show_prices));
  box.innerHTML = D.coin_catalog.map(([id,sym])=>`<button type="button" data-id="${esc(id)}" aria-pressed="${P.coins.includes(id)}">${esc(sym)}</button>`).join("");
  box.querySelectorAll("button").forEach(b=>b.onclick=e=>{
    e.stopPropagation();
    const id=b.dataset.id; P.coins = P.coins.includes(id) ? P.coins.filter(x=>x!==id) : [...P.coins, id];
    b.setAttribute("aria-pressed", String(P.coins.includes(id))); changed(false);
  });
  renderCmd();
}
function renderCmd(){
  const spec=Object.fromEntries(D.coin_catalog.map(([id,,sp])=>[id,sp]));
  const parts=[`--set coins=${P.coins.map(id=>spec[id]||id.toUpperCase()).join(",")||'""'}`,
    `--set 'hidden_tools=${JSON.stringify(P.hidden)}'`, `--set theme.animate_background=${P.animate}`];
  if(D.git.enabled) parts.push(`--set git.author=${P.gitMine?"me":"all"}`);
  parts.push(`--set screenshot_mode=${P.privacy}`);
  if(D.show_prices) parts.push(`--set show_prices=${P.prices}`);
  $("#s-cmd").textContent = "python3 usage.py " + parts.join(" ");
}
function changed(redrawPanel=true){
  savePrefs(); renderCards(); renderLegend(); chart.data=dataFor(view); chart.update();
  renderSub(); renderHeat(); renderTables(); renderCoins(D.prices);
  if(redrawPanel) renderSettings(); else renderCmd();
}
$("#s-copy").onclick=async e=>{
  e.stopPropagation(); const t=$("#s-cmd").textContent, b=e.currentTarget;
  try{ await navigator.clipboard.writeText(t); }catch(err){ const r=document.createRange(); r.selectNodeContents($("#s-cmd")); const sel=getSelection(); sel.removeAllRanges(); sel.addRange(r); document.execCommand("copy"); }
  b.textContent="Copied"; setTimeout(()=>b.textContent="Copy command",1500);
};
function download(name, text, type){
  const a=document.createElement("a"); a.href=URL.createObjectURL(new Blob([text],{type})); a.download=name;
  document.body.appendChild(a); a.click(); setTimeout(()=>{ URL.revokeObjectURL(a.href); a.remove(); },500);
}
const stamp=()=>new Date().toISOString().slice(0,10);
$("#x-csv").onclick=e=>{
  e.stopPropagation();
  const vs=VIS(), g=gitOn()?gitStats():null, q=v=>/[",\n]/.test(v)?`"${String(v).replace(/"/g,'""')}"`:v;
  const head=["date",...vs.flatMap(s=>[`${s} tokens`,`${s} cost_usd`]),...(g?["commits","commits_with_ai"]:[])];
  const rows=D.days.map(d=>[d,...vs.flatMap(s=>[D.daily[s][d],D.daily_cost[s][d].toFixed(4)]),...(g?[g.daily[d][0]+g.daily[d][1],g.daily[d][0]]:[])]);
  download(`build-commit-pro-daily-${stamp()}.csv`,[head,...rows].map(r=>r.map(q).join(",")).join("\n"),"text/csv");
};
$("#x-json").onclick=e=>{
  e.stopPropagation();
  const vs=VIS(), g=gitOn()?gitStats():null;
  const out={tool:"Build & Commit Pro Tracker", version:D.version, generated:D.generated, tools:vs,
    daily:D.days.map(d=>({date:d, ...Object.fromEntries(vs.map(s=>[s,{tokens:D.daily[s][d],cost_usd:+D.daily_cost[s][d].toFixed(4)}]))})),
    models:D.models.filter(([s])=>on(s)).map(([s,m,i,o,w,r,c])=>({tool:s,model:m,input:i,output:o,cache_write:w,cache_read:r,cost_usd:+c.toFixed(4)})),
    projects:D.projects.filter(([,,,srcs])=>srcs.some(on)).map(([p,t,c,srcs])=>({project:pname(p),tokens:t,cost_usd:+c.toFixed(4),tools:srcs})),
    sessions:D.sessions.filter(s=>on(s.source)).map(s=>({tool:s.source,project:pname(s.project),start:new Date(s.start*1000).toISOString(),end:new Date(s.end*1000).toISOString(),requests:s.calls,tokens:s.tokens,cost_usd:+s.cost.toFixed(4),model:s.model,claude_code_tools:s.tools})),
    commits:g?{total:g.n,with_ai:g.ai,lines_added:g.added,lines_deleted:g.deleted,by_tool:g.tool,by_repo:Object.fromEntries(Object.entries(g.repo).map(([k,v])=>[pname(k),{commits:v[0],with_ai:v[1]}]))}:null,
    forecast:D.forecast, screenshot_mode:P.privacy};
  download(`build-commit-pro-${stamp()}.json`,JSON.stringify(out,null,2),"application/json");
};
$("#s-reset").onclick=e=>{ e.stopPropagation(); P={...DEF, hidden:[...DEF.hidden], coins:[...DEF.coins]}; try{ localStorage.removeItem(PKEY); }catch(err){} changed(); };

/* ---- live refresh ---- */
liveStatus(); livePrices();
setInterval(liveStatus, 2*60e3);
setInterval(livePrices, 60e3);
setTimeout(()=>location.reload(), Math.max(5, D.refresh_minutes)*60e3);
</script></body></html>"""


def theme_css(cfg, colors):
    t = cfg.get("theme") or {}
    clean = lambda v, d: re.sub(r"[<>{};\\]", "", str(v)) if v else d
    edge = clean(t.get("edge"), DEFAULT_SETTINGS["theme"]["edge"])
    m = re.fullmatch(r"rgba\((.+),\s*([\d.]+)\)", edge.replace(" ", ""))
    edge_hi = f"rgba({m.group(1)},{min(1.0, float(m.group(2)) * 2):.2f})" if m else edge
    try:
        op = min(1.0, max(0.0, float(t.get("panel_opacity", 0.05))))
    except (TypeError, ValueError):
        op = 0.05
    return (f":root{{--accent:{clean(t.get('accent'), '#ff6a13')};--claude:{clean(colors.get('Claude Code'), '#ff6a13')};"
            f"--edge:{edge};--edge-hi:{edge_hi};--panel-a:rgba(20,20,20,{op})}}")


def render(agg, cfg):
    data = json.dumps(agg).replace("</", "<\\/")  # a project named "</script>" can't break out of the page
    html = HTML.replace("__THEME__", theme_css(cfg, agg["colors"])).replace("__DATA__", data)
    OUT_HTML.write_text(html, encoding="utf-8")
    return OUT_HTML


def inspect_hermes():
    path = hermes_db()
    if not path:
        print(f"No Hermes database found in {HERMES_HOME}. Files there:")
        for p in sorted(HERMES_HOME.glob("*")) if HERMES_HOME.exists() else []:
            print("  ", p.name)
        return
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    print(path)
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
        print(f"  {t}: {', '.join(cols)}")


TASK_NAME = "ai-usage-dashboard"
CRON_TAG = "# ai-usage-dashboard"


def _runner():
    """Interpreter + script for scheduled runs (pythonw on Windows so no console window flashes up)."""
    py = Path(sys.executable)
    if os.name == "nt" and (py.parent / "pythonw.exe").exists():
        py = py.parent / "pythonw.exe"
    return str(py), str(Path(__file__).resolve())


def install():
    DATA_DIR.mkdir(exist_ok=True)
    py, script = _runner()
    mins = max(1, int(settings().get("refresh_minutes") or 15))
    system = platform.system()
    if system == "Darwin":
        PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLIST_PATH.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{script}</string><string>--no-open</string></array>
  <key>StartInterval</key><integer>{mins * 60}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{DATA_DIR / 'refresh.log'}</string>
  <key>StandardErrorPath</key><string>{DATA_DIR / 'refresh.log'}</string>
</dict></plist>
""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        r = subprocess.run(["launchctl", "load", "-w", str(PLIST_PATH)], capture_output=True, text=True)
        ok, err = r.returncode == 0, r.stderr
    elif system == "Windows":
        r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(min(mins, 1439)), "/TN", TASK_NAME,
                            "/TR", f'"{py}" "{script}" --no-open'], capture_output=True, text=True)
        ok, err = r.returncode == 0, r.stderr or r.stdout
    else:  # Linux and other Unix: a user crontab line
        try:
            cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            lines = [l for l in (cur.stdout if cur.returncode == 0 else "").splitlines() if CRON_TAG not in l]
            log = shlex.quote(str(DATA_DIR / "refresh.log"))
            when = f"*/{mins} * * * *" if mins < 60 else f"0 */{max(1, mins // 60)} * * *"
            lines.append(f"{when} {shlex.quote(py)} {shlex.quote(script)} --no-open >> {log} 2>&1 {CRON_TAG}")
            r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True)
            ok, err = r.returncode == 0, r.stderr
        except FileNotFoundError:
            ok, err = False, "crontab isn't installed (install cron, or run --no-open from any scheduler)"
    if ok:
        print(f"Auto-refresh on: every {mins} minutes (runs {script}).")
        print("If you move this script, run --install again from its new location.")
    else:
        print("Couldn't set up auto-refresh:", (err or "").strip())


def uninstall():
    system = platform.system()
    if system == "Darwin":
        if PLIST_PATH.exists():
            subprocess.run(["launchctl", "unload", "-w", str(PLIST_PATH)], capture_output=True)
            PLIST_PATH.unlink()
            return print("Auto-refresh off.")
    elif system == "Windows":
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], capture_output=True, text=True)
        if r.returncode == 0:
            return print("Auto-refresh off.")
    else:
        try:
            cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        except FileNotFoundError:
            cur = None
        if cur and cur.returncode == 0 and CRON_TAG in cur.stdout:
            keep = [l for l in cur.stdout.splitlines() if CRON_TAG not in l]
            subprocess.run(["crontab", "-"], input="\n".join(keep) + ("\n" if keep else ""), text=True)
            return print("Auto-refresh off.")
    print("Auto-refresh wasn't installed.")


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Build & Commit Pro Tracker: local dashboard for AI coding tool usage (Claude Code, Codex, Hermes, Gemini CLI, OpenCode, Aider).")
    ap.add_argument("--version", action="version", version=f"Build & Commit Pro Tracker v{VERSION}")
    ap.add_argument("--no-open", action="store_true", help="rebuild without opening a browser")
    ap.add_argument("--install", action="store_true", help="refresh automatically in the background")
    ap.add_argument("--uninstall", action="store_true", help="remove the automatic refresh")
    ap.add_argument("--settings", action="store_true", help="show current settings")
    ap.add_argument("--set", metavar="KEY=VALUE", action="append", help="change a setting (repeatable)")
    ap.add_argument("--unset", metavar="KEY", action="append", help="reset a setting to its default")
    ap.add_argument("--cmc-key", metavar="KEY", help="save a free CoinMarketCap API key")
    ap.add_argument("--coingecko-key", metavar="KEY", help="save a free CoinGecko demo API key")
    ap.add_argument("--test-alert", action="store_true", help="send a test desktop notification")
    ap.add_argument("--offline", action="store_true", help="skip status and price fetches")
    ap.add_argument("--inspect-hermes", action="store_true", help="show the Hermes database layout")
    ap.add_argument("--export", metavar="FOLDER", nargs="?", const=".", help="write every request and commit to CSV + a JSON summary")
    ap.add_argument("--menubar", action="store_true", help="print a SwiftBar/xbar/Argos menu (used by the menu bar plugin)")
    ap.add_argument("--install-menubar", metavar="FOLDER", nargs="?", const="", help="optional: add the menu bar meter to SwiftBar/xbar/Argos")
    a = ap.parse_args()
    if a.inspect_hermes:
        return inspect_hermes()
    if a.install:
        return install()
    if a.uninstall:
        return uninstall()
    if a.menubar:
        return menubar(settings())
    if a.install_menubar is not None:
        return install_menubar(a.install_menubar or None)
    if a.test_alert:
        return notify("Build & Commit Pro Tracker", "Test notification: alerts are working.")
    for opt, label in (("coingecko_key", "CoinGecko"), ("cmc_key", "CoinMarketCap")):
        if getattr(a, opt):
            cfg = load_config()
            cfg[opt] = getattr(a, opt).strip()
            save_config(cfg)
            print(f"Saved {label} key to {CONFIG_PATH}.")
    for expr in a.set or []:
        set_setting(expr)
    for key in a.unset or []:
        unset_setting(key)
    if a.settings:
        return show_settings()
    if a.set or a.unset:
        print()

    cfg = settings()
    claude, claude_ev = collect_claude()
    codex, codex_ev, limits = collect_codex()
    hermes = collect_hermes()
    gemini = collect_gemini()
    opencode = collect_opencode()
    aider = collect_aider(cfg.get("aider_dirs") or [])
    try:
        known = json.loads((sqlite3.connect(DB_PATH).execute("SELECT value FROM meta WHERE key='git_repos'").fetchone() or ["[]"])[0]) if DB_PATH.exists() else []
    except sqlite3.Error:
        known = []
    commits, repos = collect_git(cfg, known)
    status, incidents = (None, []) if a.offline else collect_status()
    prices = None if a.offline else collect_prices(cfg)
    counts = {"Claude Code": claude, "Codex": codex, "Hermes": hermes, "Gemini CLI": gemini, "OpenCode": opencode, "Aider": aider}
    found = " · ".join((f"Hermes: {HERMES_TRACKED[0]} session{'s' if HERMES_TRACKED[0] != 1 else ''}" + (f" (+{len(v)} updated)" if v else "")) if k == "Hermes"
                       else f"{k}: {len(v)}" for k, v in counts.items() if v or k in ("Claude Code", "Codex") or (k == "Hermes" and HERMES_TRACKED[0]))
    if repos:
        found += f" · commits: {len(commits)} in {len(repos)} repo{'s' if len(repos) != 1 else ''}"
    print(f"v{VERSION} · {datetime.now():%H:%M} · {found} · limit hits: {len(claude_ev) + len(codex_ev)} · "
          f"status: {'ok' if status else 'offline'} · prices: {prices['source'] if prices else 'off' if not cfg.get('show_prices', True) else 'offline'}")
    for e in FETCH_ERRORS + WARNINGS:
        print("  ! " + e)
    con = store(claude + codex + hermes + gemini + opencode + aider, claude_ev + codex_ev + incidents,
                {"codex_limits": limits, "status": status, "prices": prices, "git_repos": repos or None, "_commits": commits})
    agg = aggregate(con, cfg)
    named = [m for m, _ in agg["unpriced"] if m and m != "?"]
    if named:
        print("  ! no price for: " + ", ".join(named) + " (add with --set prices.MODEL=[in,out,cache_write,cache_read])")
    check_alerts(con, agg, cfg)
    out = render(agg, cfg)
    print(f"Dashboard: {out}")
    if a.export:
        for p in export_data(con, cfg, a.export):
            print(f"Exported: {p}")
    if not a.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:   # output piped into e.g. `head`
        sys.stderr.close()
