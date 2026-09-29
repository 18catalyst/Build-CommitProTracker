#!/usr/bin/env python3
"""
AI usage dashboard: Claude Code, Codex and Hermes Agent token usage, Claude
service status and crypto prices, in one local HTML page. Python 3.9+, stdlib only.
Works on macOS, Linux and Windows.

  python3 usage.py                      collect, rebuild dashboard, open it
  python3 usage.py --no-open            collect + rebuild only
  python3 usage.py --install            refresh automatically every 15 minutes
  python3 usage.py --uninstall          stop the automatic refresh
  python3 usage.py --cmc-key KEY        save a free CoinMarketCap key (used first for prices)
  python3 usage.py --coingecko-key KEY  save a free CoinGecko demo key
  python3 usage.py --offline            skip status and price fetches
  python3 usage.py --inspect-hermes     print Hermes DB tables/columns (debugging)
  python3 usage.py --version

Data lives in ~/.ai-usage/ (usage.db, dashboard.html, config.json). Nothing is
sent anywhere except the status and price requests.
"""
import argparse, json, os, platform, re, shlex, sqlite3, statistics, subprocess, sys, time, urllib.error, urllib.request, webbrowser
from datetime import datetime, timedelta
from pathlib import Path

VERSION = "2.3"  # bump on every change so the page and Terminal show which copy is running

HOME = Path.home()
DATA_DIR = HOME / ".ai-usage"
DB_PATH = DATA_DIR / "usage.db"
OUT_HTML = DATA_DIR / "dashboard.html"

CLAUDE_DIRS = [HOME / ".claude" / "projects", HOME / ".config" / "claude" / "projects"]
CODEX_DIR = Path(os.environ.get("CODEX_HOME", HOME / ".codex")) / "sessions"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", HOME / ".hermes"))

STATUS_API = "https://status.claude.com/api/v2"
CURRENCY = "USD"
PRICES_API = ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
              "&ids=solana,bitcoin,ethereum,monero,binancecoin,ripple,chainlink&sparkline=true&price_change_percentage=1h,24h,7d")
CONFIG_PATH = DATA_DIR / "config.json"
PLIST_LABEL = "com.ai-usage.dashboard"
PLIST_PATH = HOME / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"

WINDOW_5H = 5 * 3600
WEEK = 7 * 86400
LIMIT_RE = re.compile(r"usage limit|limit reached|hit your .{0,20}limit|limit will reset", re.I)


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


FETCH_ERRORS = []


def fetch_json(url, headers=None):
    """urllib first; fall back to curl (some APIs block Python's client, some Macs lack CA certs)."""
    host = url.split("/")[2]
    headers = headers or {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ai-usage-dashboard/2.3", "Accept": "application/json", **headers})
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


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------- collectors ----------
# row = (id, ts, source, model, input, output, cache_write, cache_read, project, session)
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
            session = f.stem
            for i, e in read_jsonl(f):
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
                rows.append((key, ts, "Claude Code", model,
                             n(u.get("input_tokens")), n(u.get("output_tokens")),
                             n(u.get("cache_creation_input_tokens")), n(u.get("cache_read_input_tokens")),
                             project, session))
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
                         0, cached, Path(cwd).name if cwd else "codex", f.stem))
    return rows, events, latest


def hermes_db():
    for name in ("state.db", "hermes.db", "sessions.db"):
        p = HERMES_HOME / name
        if p.exists():
            return p
    return None


def collect_hermes():
    """Best effort: any table with input/output token columns counts."""
    path, rows = hermes_db(), []
    if not path:
        return rows
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            low = {r[1].lower(): r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
            pick = lambda *names: next((low[c] for c in names if c in low), None)
            inp, out = pick("input_tokens", "prompt_tokens"), pick("output_tokens", "completion_tokens")
            if not (inp and out):
                continue
            cols = (pick("started_at", "created_at", "timestamp", "updated_at", "ended_at"), pick("model"), inp, out,
                    pick("cache_write_tokens"), pick("cache_read_tokens", "cached_tokens"), pick("title", "name"))
            sel = ", ".join(f'"{c}"' if c else "NULL" for c in cols)
            for rowid, ts, model, i_, o_, w_, r_, title in con.execute(f'SELECT rowid, {sel} FROM "{t}"'):
                ts = parse_ts(ts)
                if ts is None:
                    continue
                rows.append((f"hermes:{t}:{rowid}", ts, "Hermes", model or "?", n(i_), n(o_), n(w_), n(r_),
                             str(title) if title else "hermes", f"{t}:{rowid}"))
    finally:
        con.close()
    return rows


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


COINS = [("solana", "SOL", "Solana"), ("bitcoin", "BTC", "Bitcoin"), ("ethereum", "ETH", "Ethereum"), ("monero", "XMR", "Monero"),
         ("binancecoin", "BNB", "BNB"), ("ripple", "XRP", "XRP"), ("chainlink", "LINK", "Chainlink")]
CMC_API = ("https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol="
           + ",".join(s for _, s, _ in COINS) + f"&convert={CURRENCY}")


def prices_from_cmc(key):
    """1 credit per call; every 15 min is ~2,900 of the free plan's 15,000/month."""
    data = fetch_json(CMC_API, headers={"X-CMC_PRO_API_KEY": key})
    got = (data or {}).get("data") or {}
    coins = []
    for cid, sym, name in COINS:
        entry = got.get(sym)
        if isinstance(entry, list):
            entry = entry[0] if entry else None
        q = ((entry or {}).get("quote") or {}).get(CURRENCY)
        if not q or q.get("price") is None:
            continue
        coins.append({"id": cid, "symbol": sym.lower(), "name": name, "current_price": q["price"],
                      "price_change_percentage_1h_in_currency": q.get("percent_change_1h"),
                      "price_change_percentage_24h_in_currency": q.get("percent_change_24h"),
                      "price_change_percentage_7d_in_currency": q.get("percent_change_7d"),
                      "sparkline_in_7d": {"price": []}})  # filled from local history later
    return coins


def collect_prices():
    cfg = load_config()
    if cfg.get("cmc_key"):
        coins = prices_from_cmc(cfg["cmc_key"])
        if coins:
            return {"fetched": time.time(), "source": "CoinMarketCap", "currency": CURRENCY, "coins": coins}
    key = cfg.get("coingecko_key")
    data = fetch_json(PRICES_API + (f"&x_cg_demo_api_key={key}" if key else ""))
    if isinstance(data, list) and data:
        return {"fetched": time.time(), "source": "CoinGecko", "currency": CURRENCY, "coins": data}
    return None


# ---------- store ----------
def store(rows, events, meta):
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS usage(
        id TEXT PRIMARY KEY, ts REAL, source TEXT, model TEXT,
        input INT, output INT, cache_write INT, cache_read INT, project TEXT)""")
    if "session" not in [r[1] for r in con.execute("PRAGMA table_info(usage)")]:
        con.execute("ALTER TABLE usage ADD COLUMN session TEXT")
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, ts REAL, kind TEXT, detail TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS prices(ts REAL, coin TEXT, price REAL, PRIMARY KEY(ts, coin))")
    p = meta.get("prices")
    if p:
        con.executemany("INSERT OR IGNORE INTO prices VALUES (?,?,?)",
                        [(p["fetched"], f'{c["id"]}:{p.get("currency", "GBP")}', c["current_price"]) for c in p["coins"] if c.get("current_price") is not None])
        con.execute("DELETE FROM prices WHERE ts < ?", (time.time() - 8 * 86400,))
    con.executemany("INSERT OR REPLACE INTO usage(id,ts,source,model,input,output,cache_write,cache_read,project,session) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", [r for r in rows if r[1] is not None])
    con.executemany("INSERT OR REPLACE INTO events VALUES (?,?,?,?)", events)
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


def aggregate(con):
    now = time.time()
    since30 = now - 30 * 86400
    data = con.execute(
        "SELECT ts, source, model, input, output, cache_write, cache_read, project, session "
        "FROM usage WHERE ts >= ? ORDER BY ts", (since30,)).fetchall()
    sources = ["Claude Code", "Codex", "Hermes"]

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    day_starts = [today - timedelta(days=d) for d in range(29, -1, -1)]
    days = [d.strftime("%Y-%m-%d") for d in day_starts]
    top = datetime.now().replace(minute=0, second=0, microsecond=0)
    hour_starts = [top - timedelta(hours=h) for h in range(47, -1, -1)]
    hours = [h.strftime("%Y-%m-%d %H") for h in hour_starts]

    daily = {s: {d: 0 for d in days} for s in sources}
    hourly = {s: {h: 0 for h in hours} for s in sources}
    heat = [[0] * 24 for _ in range(7)]
    models, projects, sessions = {}, {}, {}
    win5 = {s: 0 for s in sources}
    week = {s: 0 for s in sources}
    ev = {s: [] for s in sources}
    cache_now, cache_prev = [0, 0, 0], [0, 0, 0]
    cache_daily = {d: [0, 0, 0] for d in days}

    for ts, src, model, i, o, cw, cr, proj, sess in data:
        if src not in win5:
            continue
        w = i + o + cw  # "work" tokens; cache reads tracked separately
        dt = datetime.fromtimestamp(ts)
        d, hk = dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d %H")
        if d in daily[src]:
            daily[src][d] += w
        if hk in hourly[src]:
            hourly[src][hk] += w
        heat[dt.weekday()][dt.hour] += w
        m = models.setdefault((src, model), [0, 0, 0, 0])
        for k, v in enumerate((i, o, cw, cr)):
            m[k] += v
        if src == "Claude Code":
            projects[proj] = projects.get(proj, 0) + w
            bucket = cache_now if ts >= now - WEEK else cache_prev if ts >= now - 2 * WEEK else None
            if bucket:
                for k, v in enumerate((i, cw, cr)):
                    bucket[k] += v
            if d in cache_daily:
                for k, v in enumerate((i, cw, cr)):
                    cache_daily[d][k] += v
        s = sessions.setdefault((src, sess or "?"), {"source": src, "project": proj, "start": ts, "end": ts,
                                                      "tokens": 0, "calls": 0, "models": {}})
        s["end"] = max(s["end"], ts)
        s["tokens"] += w
        s["calls"] += 1
        s["models"][model] = s["models"].get(model, 0) + w
        ev[src].append((ts, w))
        if ts >= now - WINDOW_5H:
            win5[src] += w
        if ts >= now - WEEK:
            week[src] += w

    # limit hits and what the 5h window looked like when they happened
    limits = []
    for eid, ts, detail in con.execute(
            "SELECT id, ts, detail FROM events WHERE kind='limit' AND ts >= ? ORDER BY ts", (since30,)):
        d = json.loads(detail)
        src = d.get("source", "Claude Code")
        limits.append({"ts": ts, "source": src, "reset": d.get("reset"),
                       "window": window_before(ev.get(src, []), ts, WINDOW_5H)})
    claude_hits = [l["window"] for l in limits if l["source"] == "Claude Code" and l["window"] > 0]

    incidents = []
    for eid, ts, detail in con.execute(
            "SELECT id, ts, detail FROM events WHERE kind='incident' AND ts >= ? ORDER BY ts DESC", (since30,)):
        d = json.loads(detail)
        d["start"] = ts
        incidents.append(d)

    sess_list = sorted(sessions.values(), key=lambda s: -s["end"])[:20]
    for s in sess_list:
        s["model"] = max(s.pop("models").items(), key=lambda kv: kv[1])[0]

    meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
    p = meta.get("prices")
    if p:
        for c in p.get("coins", []):
            if not (c.get("sparkline_in_7d") or {}).get("price"):
                c["sparkline_in_7d"] = {"price": [r[0] for r in con.execute(
                    "SELECT price FROM prices WHERE coin=? AND ts >= ? ORDER BY ts", (f'{c.get("id")}:{p.get("currency", "GBP")}', now - WEEK))]}
    return {
        "version": VERSION,
        "generated": datetime.now().strftime("%a %d %b %Y, %H:%M"),
        "generated_ts": now,
        "days": days, "day0": day_starts[0].timestamp(), "daily": daily,
        "hours": hours, "hour0": hour_starts[0].timestamp(), "hourly": hourly,
        "heat": heat,
        "win5": win5, "week": week,
        "peak5": {s: peak_window(ev[s], WINDOW_5H) for s in sources},
        "limit_est": int(statistics.median(claude_hits)) if claude_hits else None,
        "limit_hits": len(claude_hits),
        "limits": limits,
        "cache": {"now": hit_rate(*cache_now), "prev": hit_rate(*cache_prev),
                  "daily": [hit_rate(*cache_daily[d]) for d in days]},
        "codex_limits": meta.get("codex_limits"),
        "status": meta.get("status"),
        "incidents": incidents,
        "prices": meta.get("prices"),
        "cg_key": load_config().get("coingecko_key"),
        "models": sorted(([s, m, *v] for (s, m), v in models.items()), key=lambda r: -(r[2] + r[3] + r[4])),
        "projects": sorted(projects.items(), key=lambda kv: -kv[1])[:10],
        "sessions": sess_list,
        "row_count": len(data),
    }


# ---------- render ----------
HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Usage</title>
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
  border-bottom:1px solid var(--line);overflow-x:auto;scrollbar-width:none}
.ticker::-webkit-scrollbar{display:none}
.tk{display:flex;align-items:center;gap:7px;padding:5px 9px;border-radius:6px;background:var(--panel2);white-space:nowrap;flex:none}
.tk b{font-family:var(--mono);font-weight:700;font-size:13px;letter-spacing:.03em}
.tk .px{font-family:var(--mono);font-weight:500;font-size:12.5px;font-variant-numeric:tabular-nums}
.tk .ch{font-family:var(--mono);font-size:11px}
.tk svg{width:38px;height:16px}
.tk-meta{font-family:var(--mono);font-size:10.5px;color:var(--dim);white-space:nowrap;flex:none;margin-left:6px}
.ver{font-family:var(--mono);font-size:11px;color:var(--dim);margin-left:auto;flex:none;padding-left:12px}

/* header */
.top{display:flex;flex-direction:column;align-items:center;gap:4px;margin:4px 0 10px;text-align:center}
h1{font-family:var(--pixel);font-weight:700;font-size:36px;line-height:1;margin:0;letter-spacing:-.01em}
.sub{font-family:var(--mono);color:var(--mute);font-size:11.5px;display:flex;gap:14px;flex-wrap:wrap;align-items:center;justify-content:center}
.pill{display:inline-flex;align-items:center;gap:6px}

/* grid */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:var(--gap);margin-bottom:var(--gap)}
.row{display:grid;gap:var(--gap);margin-bottom:var(--gap)}
.r2{grid-template-columns:minmax(0,2.2fr) minmax(0,1fr)}
.r3{grid-template-columns:minmax(0,1fr) minmax(0,1.35fr) minmax(0,1fr)}
@media(max-width:1100px){.r3{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.r3>.card:last-child{grid-column:1/-1}}
@media(max-width:860px){.r2,.r3{grid-template-columns:minmax(0,1fr)}.r3>.card:last-child{grid-column:auto}}

.card{text-shadow:0 1px 2px rgba(0,0,0,.9);background:var(--panel-a);-webkit-backdrop-filter:var(--blur);backdrop-filter:var(--blur);border:1px solid var(--edge);border-radius:8px;padding:12px 14px;min-width:0;transition:border-color .2s}
.card:hover{border-color:var(--edge-hi)}
.ch{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap}
h2{font-family:var(--pixel);font-weight:500;font-size:17px;margin:0}
h3{font-family:var(--mono);font-size:10.5px;font-weight:400;color:var(--mute);text-transform:uppercase;letter-spacing:.08em;margin:12px 0 4px}
.hint{font-family:var(--mono);font-size:10.5px;color:var(--mute)}

/* kpi cards */
.lbl{font-family:var(--mono);font-size:10.5px;color:var(--mute);text-transform:uppercase;letter-spacing:.08em;display:flex;gap:7px;align-items:center;white-space:nowrap;overflow:hidden}
.dot{width:8px;height:8px;border-radius:1px;flex:none;display:inline-block}
.big{font-family:var(--mono);font-weight:700;font-size:26px;line-height:1.15;margin:6px 0 2px;font-variant-numeric:tabular-nums;white-space:nowrap;letter-spacing:-.02em}
.small{font-size:11.5px;color:var(--mute);line-height:1.35}
.bar{height:5px;background:var(--panel2);border-radius:2px;margin-top:8px;overflow:hidden}
.bar>i{display:block;height:100%}
.kspark{width:100%;height:22px;margin-top:6px;display:block}

/* chart */
.seg{display:flex;gap:3px;background:var(--panel2);padding:3px;border-radius:7px}
.seg button{font:500 12.5px Inter,sans-serif;padding:5px 12px;border:0;border-radius:5px;background:transparent;color:#d6d6d6;cursor:pointer;transition:background .15s,color .15s}
.seg button:hover{color:#fff}
.seg button.on{background:var(--accent);color:#fff}
.chartcard{display:flex;flex-direction:column}
.chartbox{position:relative;flex:1;min-height:210px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-family:var(--mono);font-size:10.5px;color:var(--mute);margin-left:auto}
.legend span{display:inline-flex;align-items:center;gap:6px}
.swatch{width:9px;height:9px;border-radius:1px;display:inline-block}
.swatch-limit{width:0;height:11px;border-left:2px dashed var(--accent)}
.swatch-out{width:11px;height:11px;background:rgba(255,69,58,.28);border-radius:1px}

/* status */
.status-top{display:flex;align-items:center;gap:9px}
.status-top b{font-family:var(--pixel);font-weight:500;font-size:19px}
.comps{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.comps span{font-family:var(--mono);font-size:10.5px;padding:3px 7px;border-radius:4px;background:var(--panel2);display:inline-flex;gap:6px;align-items:center}
.incs{max-height:228px;overflow-y:auto}
.inc{display:grid;grid-template-columns:auto 1fr auto;gap:2px 10px;padding:7px 0;border-bottom:1px solid var(--line);font-size:12.5px;align-items:baseline}
.inc:last-child{border-bottom:0}
.inc .when,.inc .dur{font-family:var(--mono);font-size:11px;color:var(--mute);white-space:nowrap}
.inc .meta{grid-column:2/-1;font-size:10.5px;color:var(--dim);font-family:var(--mono)}
.tag{font-family:var(--mono);font-size:9.5px;text-transform:uppercase;letter-spacing:.06em;padding:1px 5px;border-radius:3px;margin-left:6px;vertical-align:1px;background:var(--panel2)}

/* tables */
.scroll{overflow:auto}
.scroll,.incs{scrollbar-width:none;-ms-overflow-style:none}
.scroll::-webkit-scrollbar,.incs::-webkit-scrollbar{display:none;width:0;height:0}
.tall{max-height:290px}
table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 6px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:0}
th{font-family:var(--mono);font-size:10px;color:var(--mute);font-weight:400;text-transform:uppercase;letter-spacing:.07em;position:sticky;top:0;background:rgba(10,10,10,.85)}
td{font-family:var(--mono)}td.r,th.r{text-align:right}
td.trunc{max-width:180px;overflow:hidden;text-overflow:ellipsis}

/* heatmap */
.heat{display:grid;grid-template-columns:28px repeat(24,minmax(0,1fr));gap:2px;font-family:var(--mono);font-size:9.5px;color:var(--mute)}
.heat i{aspect-ratio:1;border-radius:2px;background:var(--panel2);display:block}
.heat .hl{align-self:center}
.heat .hh{text-align:center;overflow:visible;white-space:nowrap}

.up{color:var(--up)}.down{color:var(--down)}
.note{font-family:var(--mono);font-size:10.5px;color:var(--dim);margin:6px 2px 0;line-height:1.55}
</style></head><body>
<pre id="ascii" aria-hidden="true"></pre>
<div class="ticker" aria-label="Crypto prices"><div id="coins" style="display:flex;gap:6px;align-items:center"></div><span class="ver" id="ver"></span></div>
<main>
<header class="top"><h1>AI Usage</h1><div class="sub" id="sub"></div></header>

<section class="kpis" id="cards"></section>

<section class="row r2">
  <div class="card chartcard">
    <div class="ch"><h2 id="charttitle">Hourly · last 48h</h2>
      <div class="legend" id="legend"></div>
      <div class="seg" id="viewseg"><button data-v="hourly" class="on">Hourly</button><button data-v="daily">Daily</button></div></div>
    <div class="chartbox"><canvas id="chart"></canvas></div>
  </div>
  <div class="card"><div class="ch"><h2>Claude status</h2><span class="hint" id="statushint"></span></div><div id="status"></div></div>
</section>

<section class="row r3">
  <div class="card"><div class="ch"><h2>When you work</h2><span class="hint">tokens by hour · 30d</span></div><div class="heat" id="heat"></div></div>
  <div class="card"><div class="ch"><h2>Recent sessions</h2><span class="hint">latest 20</span></div><div class="scroll tall"><table id="sessions"></table></div></div>
  <div class="card"><div class="ch"><h2>Models</h2><span class="hint">30d</span></div><div class="scroll"><table id="models"></table></div>
    <h3>Claude Code projects</h3><div class="scroll"><table id="projects"></table></div></div>
</section>

<p class="note">Tokens = input + output + cache writes (cache reads listed separately). Claude 5h gauge uses the median usage at your real
limit hits once there's one, otherwise your busiest 5h window. Codex % from Codex's own reports. Status: status.claude.com ·
prices (USD): CoinMarketCap or CoinGecko. Prices are informational only.</p>
</main><script>
const D = __DATA__;
const C = {"Claude Code":"var(--claude)","Codex":"var(--codex)","Hermes":"var(--hermes)"};
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v.slice(4,-1)).trim();
const fmt = x => x==null ? "—" : x>=1e6 ? (x/1e6).toFixed(2)+"M" : x>=1e3 ? (x/1e3).toFixed(1)+"k" : String(x);
const esc = s => String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const pad = x => String(x).padStart(2,"0");
const when = t => {const d=new Date(t*1000); return `${pad(d.getDate())}/${pad(d.getMonth()+1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;};
const dur = s => { if(s==null||s<0) return "—"; const h=Math.floor(s/3600), m=Math.round(s%3600/60); return h? `${h}h ${m}m` : `${m}m`; };
document.getElementById("ver").textContent = "v" + (D.version || "?");

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
  size(); addEventListener("resize",()=>{size();draw(0)});
  if(still){draw(0);return;}
  let last=0; (function loop(ts){ if(!document.hidden && ts-last>90){draw(ts/1400); last=ts;} requestAnimationFrame(loop); })(0);
})();

/* ---- header ---- */
const STATUS_COL = {none:"var(--up)",operational:"var(--up)",minor:"var(--amber)",degraded_performance:"var(--amber)",
  partial_outage:"var(--down)",major:"var(--down)",major_outage:"var(--down)",critical:"var(--down)",under_maintenance:"var(--codex)",maintenance:"var(--codex)"};
function renderSub(){
  const st=D.status;
  const s = st ? `<span class="pill"><i class="dot" style="background:${STATUS_COL[st.indicator]||"var(--mute)"};border-radius:50%"></i>Claude: ${esc(st.description.toLowerCase())}</span>` : "";
  document.getElementById("sub").innerHTML = `<span>updated ${D.generated}</span><span>${D.row_count.toLocaleString()} entries / 30d</span>${s}`;
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
const hasCodex = !!L || Object.values(D.daily["Codex"]).some(v=>v>0);
function renderCards(){
  let h = "";
  const cn = D.win5["Claude Code"];
  if(D.limit_est){
    h += card("Claude Code","Claude · 5h",fmt(cn), `${Math.round(cn/D.limit_est*100)}% of est. limit (${fmt(D.limit_est)})`, cn/D.limit_est*100);
  } else {
    const cp = D.peak5["Claude Code"];
    h += card("Claude Code","Claude · 5h",fmt(cn), cp ? `${Math.round(cn/cp*100)}% of busiest 5h (${fmt(cp)})` : "no history yet", cp? cn/cp*100 : null);
  }
  const hits=D.limits.filter(l=>l.source==="Claude Code").length;
  const d7=D.days.slice(-7).map(d=>D.daily["Claude Code"][d]);
  h += card("Claude Code","Claude · 7d",fmt(D.week["Claude Code"]),`${hits} limit hit${hits===1?"":"s"} in 30d`,null,sparkSvg(d7,css("var(--claude)")));
  const c=D.cache;
  const cnote = c.now==null ? "no Claude usage this week" : c.prev==null ? "input served from cache" :
    `<span class="${c.now>=c.prev?"up":"down"}">${c.now>=c.prev?"▲":"▼"} ${Math.abs(c.now-c.prev).toFixed(1)} pts</span> vs prior week`;
  h += card("var(--accent)","Cache hit · 7d", c.now==null?"—":c.now+"%", cnote, null, sparkSvg(c.daily,css("var(--accent)")));
  if(hasCodex){
    if(L && L.primary) h += card("Codex","Codex · 5h",Math.round(L.primary.used_percent)+"%",resetText(L.primary)||"used",L.primary.used_percent);
    else h += card("Codex","Codex · 5h",fmt(D.win5["Codex"]),"no rate-limit report yet");
    if(L && L.secondary) h += card("Codex","Codex · week",Math.round(L.secondary.used_percent)+"%",resetText(L.secondary)||"used",L.secondary.used_percent);
    else h += card("Codex","Codex · 7d",fmt(D.week["Codex"]),"rolling week");
  }
  const hd=D.days.slice(-7).map(d=>D.daily["Hermes"][d]);
  h += card("Hermes","Hermes · 7d",fmt(D.week["Hermes"]), D.week["Hermes"] ? "direct model calls" : "none found (--inspect-hermes)",null,sparkSvg(hd,css("var(--mute)")));
  const st=D.status;
  h += card(st?STATUS_COL[st.indicator]||"var(--mute)":"var(--mute)","Claude status", st? (st.indicator==="none"?"OK":esc(st.indicator)) : "—",
    `${D.incidents.length} incident${D.incidents.length===1?"":"s"} in 30d`);
  document.getElementById("cards").innerHTML = h;
}
renderCards();

/* ---- main chart with limit + outage marks ---- */
Chart.defaults.font.family = "JetBrains Mono, monospace";
Chart.defaults.font.size = 10.5;
const VIEWS = {
  hourly:{title:"Hourly · last 48h", keys:D.hours, src:D.hourly, t0:D.hour0, step:3600,
    label:k=>{const h=k.slice(11); return h==="00" ? k.slice(8,10)+"/"+k.slice(5,7) : h+":00";},
    tip:k=>`${k.slice(8,10)}/${k.slice(5,7)} ${k.slice(11)}:00–${k.slice(11)}:59`},
  daily:{title:"Daily · last 30d", keys:D.days, src:D.daily, t0:D.day0, step:86400,
    label:k=>k.slice(8,10)+"/"+k.slice(5,7), tip:k=>k}
};
let view="hourly";
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
  for(const l of D.limits){
    const x=px(l.ts); if(x<left||x>right) continue;
    ctx.beginPath(); ctx.moveTo(x,top); ctx.lineTo(x,bottom); ctx.stroke();
  }
  ctx.restore();
}};
function dataFor(v){const V=VIEWS[v]; return {labels:V.keys.map(V.label),
  datasets:Object.keys(V.src).filter(s=>s!=="Codex"||hasCodex).map(s=>({label:s,data:V.keys.map(k=>V.src[s][k]),backgroundColor:css(C[s]),borderRadius:2,maxBarThickness:24}))};}
const chart = new Chart(document.getElementById("chart"),{type:"bar", data:dataFor(view), plugins:[marks],
  options:{responsive:true,maintainAspectRatio:false,
    layout:{padding:{top:4,right:2,bottom:0,left:0}},
    plugins:{legend:{display:false},
    tooltip:{backgroundColor:"#1c1c1c",borderColor:"#333",borderWidth:1,titleColor:"#fff",bodyColor:"#d6d6d6",padding:9,
      callbacks:{title:it=>VIEWS[view].tip(VIEWS[view].keys[it[0].dataIndex]),label:c=>` ${c.dataset.label}: ${fmt(c.raw)}`}}},
    scales:{x:{stacked:true,grid:{display:false},ticks:{color:css("var(--mute)"),maxRotation:0,autoSkipPadding:10}},
            y:{stacked:true,grid:{color:css("var(--line)")},border:{display:false},ticks:{color:css("var(--mute)"),callback:v=>fmt(v)}}}}});
document.getElementById("legend").innerHTML =
  Object.keys(D.hourly).filter(s=>s!=="Codex"||hasCodex).filter(s=>Object.values(D.daily[s]).some(v=>v>0))
    .map(s=>`<span><i class="swatch" style="background:${C[s]}"></i>${s}</span>`).join("") +
  `<span><i class="swatch-limit"></i>limit hit</span><span><i class="swatch-out"></i>incident</span>`;
document.querySelectorAll("#viewseg button").forEach(b=>b.onclick=()=>{
  view=b.dataset.v; document.querySelectorAll("#viewseg button").forEach(x=>x.classList.toggle("on",x===b));
  document.getElementById("charttitle").textContent=VIEWS[view].title;
  chart.data=dataFor(view); chart.update();
});

/* ---- heatmap ---- */
(function(){
  const days=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"], max=Math.max(1,...D.heat.flat());
  let h="<span></span>"+Array.from({length:24},(_,i)=>`<span class="hh">${i%3===0?pad(i):""}</span>`).join("");
  D.heat.forEach((row,d)=>{
    h+=`<span class="hl">${days[d]}</span>`+row.map((v,hr)=>{
      const a=v? 0.15+0.85*Math.sqrt(v/max) : 0;
      return `<i title="${days[d]} ${pad(hr)}:00 · ${fmt(v)} tokens" style="${v?`background:rgba(255,106,19,${a.toFixed(2)})`:""}"></i>`;
    }).join("");
  });
  document.getElementById("heat").innerHTML=h;
})();

/* ---- sessions ---- */
document.getElementById("sessions").innerHTML =
  `<tr><th>Session</th><th>Started</th><th class="r">Length</th><th class="r">Calls</th><th class="r">Tokens</th><th>Model</th></tr>` +
  (D.sessions.map(s=>`<tr><td class="trunc" title="${esc(s.project)}"><span class="dot" style="background:${C[s.source]};margin-right:7px"></span>${esc(s.project)}</td>
   <td>${when(s.start)}</td><td class="r">${s.source==="Hermes"?"—":dur(s.end-s.start)}</td><td class="r">${s.calls}</td>
   <td class="r">${fmt(s.tokens)}</td><td class="trunc" title="${esc(s.model)}">${esc(s.model)}</td></tr>`).join("") || `<tr><td>No sessions yet</td></tr>`);

/* ---- models / projects ---- */
document.getElementById("models").innerHTML =
  `<tr><th>Model</th><th class="r">In</th><th class="r">Out</th><th class="r">Cache w</th><th class="r">Cache r</th></tr>` +
  (D.models.map(([s,m,i,o,w,r])=>`<tr><td class="trunc" title="${esc(m)}"><span class="dot" style="background:${C[s]||"#999"};margin-right:7px"></span>${esc(m)}</td>
  <td class="r">${fmt(i)}</td><td class="r">${fmt(o)}</td><td class="r">${fmt(w)}</td><td class="r">${fmt(r)}</td></tr>`).join("") ||
  `<tr><td>No data yet</td></tr>`);
document.getElementById("projects").innerHTML =
  (D.projects.map(([p,t])=>`<tr><td class="trunc" title="${esc(p)}">${esc(p)}</td><td class="r">${fmt(t)}</td></tr>`).join("") || `<tr><td>No data yet</td></tr>`);

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
const ORDER=["solana","bitcoin","ethereum","monero","binancecoin","ripple","chainlink"];
const money = v => v==null ? "—" : v.toLocaleString("en-US",{style:"currency",currency:"USD",maximumFractionDigits:v<10?4:v>=10000?0:2});
function spark(prices,up){
  if(!prices||prices.length<2) return "";
  const lo=Math.min(...prices), hi=Math.max(...prices), r=hi-lo||1, n=prices.length-1;
  const pts=prices.map((p,i)=>`${(i/n*50).toFixed(1)},${(15-(p-lo)/r*14).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 50 16" preserveAspectRatio="none"><polyline fill="none" stroke="${css(up?"var(--up)":"var(--down)")}" stroke-width="1.3" vector-effect="non-scaling-stroke" points="${pts}"/></svg>`;
}
function renderCoins(p){
  const el=document.getElementById("coins");
  if(p && (p.currency||"GBP")!=="USD") p=null;  // old GBP data: wait for a USD refresh
  const good=(p&&Array.isArray(p.coins)?p.coins:[]).filter(c=>c&&c.id&&c.symbol&&typeof c.current_price==="number");
  if(!good.length){ el.innerHTML=`<span class="tk-meta">prices unavailable right now</span>`; return; }
  const by=Object.fromEntries(good.map(c=>[c.id,c]));
  const f=v=>v==null?"—":`${v>=0?"+":""}${v.toFixed(2)}%`;
  el.innerHTML = ORDER.filter(id=>by[id]).map(id=>{const c=by[id], d24=c.price_change_percentage_24h_in_currency, d7=c.price_change_percentage_7d_in_currency;
    return `<div class="tk" title="${esc(c.name)} · 1h ${f(c.price_change_percentage_1h_in_currency)} · 24h ${f(d24)} · 7d ${f(d7)}">
      <b>${esc(c.symbol.toUpperCase())}</b><span class="px">${money(c.current_price)}</span>
      <span class="ch ${(d24??0)>=0?"up":"down"}">${(d24??0)>=0?"▲":"▼"} ${d24==null?"—":Math.abs(d24).toFixed(2)+"%"}</span>
      ${spark(c.sparkline_in_7d&&c.sparkline_in_7d.price,(d7??0)>=0)}</div>`;}).join("") +
    `<span class="tk-meta">USD · 24h · ${esc(p.source||"CoinGecko")} · ${when(p.fetched).slice(6)}</span>`;
}
renderCoins(D.prices);
async function livePrices(){
  if(D.prices && D.prices.source==="CoinMarketCap") return;  // CMC updates via the background refresh
  try{
    const r=await fetch("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=solana,bitcoin,ethereum,monero,binancecoin,ripple,chainlink&sparkline=true&price_change_percentage=1h,24h,7d"+(D.cg_key?"&x_cg_demo_api_key="+encodeURIComponent(D.cg_key):""));
    if(!r.ok) return; const coins=await r.json();
    if(Array.isArray(coins)&&coins.length){ D.prices={fetched:Date.now()/1000,source:"CoinGecko",currency:"USD",coins}; renderCoins(D.prices); }
  }catch(e){}
}

/* ---- live refresh ---- */
liveStatus(); livePrices();
setInterval(liveStatus, 2*60e3);
setInterval(livePrices, 60e3);
setTimeout(()=>location.reload(), 10*60e3);
</script></body></html>"""


def render(agg):
    OUT_HTML.write_text(HTML.replace("__DATA__", json.dumps(agg)), encoding="utf-8")
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
    system = platform.system()
    if system == "Darwin":
        PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLIST_PATH.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{script}</string><string>--no-open</string></array>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{DATA_DIR / 'refresh.log'}</string>
  <key>StandardErrorPath</key><string>{DATA_DIR / 'refresh.log'}</string>
</dict></plist>
""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        r = subprocess.run(["launchctl", "load", "-w", str(PLIST_PATH)], capture_output=True, text=True)
        ok, err = r.returncode == 0, r.stderr
    elif system == "Windows":
        r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "15", "/TN", TASK_NAME,
                            "/TR", f'"{py}" "{script}" --no-open'], capture_output=True, text=True)
        ok, err = r.returncode == 0, r.stderr or r.stdout
    else:  # Linux and other Unix: a user crontab line
        try:
            cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            lines = [l for l in (cur.stdout if cur.returncode == 0 else "").splitlines() if CRON_TAG not in l]
            log = shlex.quote(str(DATA_DIR / "refresh.log"))
            lines.append(f"*/15 * * * * {shlex.quote(py)} {shlex.quote(script)} --no-open >> {log} 2>&1 {CRON_TAG}")
            r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True)
            ok, err = r.returncode == 0, r.stderr
        except FileNotFoundError:
            ok, err = False, "crontab isn't installed (install cron, or run --no-open from any scheduler)"
    if ok:
        print(f"Auto-refresh on: every 15 minutes (runs {script}).")
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
    ap = argparse.ArgumentParser(description="Local dashboard for Claude Code, Codex and Hermes usage.")
    ap.add_argument("--version", action="version", version=f"ai-usage v{VERSION}")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--inspect-hermes", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--offline", action="store_true", help="skip status and price fetches")
    ap.add_argument("--coingecko-key", metavar="KEY", help="save a free CoinGecko demo API key")
    ap.add_argument("--cmc-key", metavar="KEY", help="save a free CoinMarketCap API key")
    a = ap.parse_args()
    if a.inspect_hermes:
        return inspect_hermes()
    if a.install:
        return install()
    if a.uninstall:
        return uninstall()
    for opt, field, label in (("coingecko_key", "coingecko_key", "CoinGecko"), ("cmc_key", "cmc_key", "CoinMarketCap")):
        if getattr(a, opt):
            DATA_DIR.mkdir(exist_ok=True)
            cfg = load_config(); cfg[field] = getattr(a, opt).strip()
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8"); CONFIG_PATH.chmod(0o600)
            print(f"Saved {label} key to {CONFIG_PATH}.")

    claude, claude_ev = collect_claude()
    codex, codex_ev, limits = collect_codex()
    hermes = collect_hermes()
    status, incidents = (None, []) if a.offline else collect_status()
    prices = None if a.offline else collect_prices()
    print(f"v{VERSION} · {datetime.now():%H:%M} · Claude Code: {len(claude)} · Codex: {len(codex)} · Hermes: {len(hermes)} · "
          f"limit hits: {len(claude_ev) + len(codex_ev)} · status: {'ok' if status else 'offline'} · "
          f"prices: {prices['source'] if prices else 'offline'}")
    for e in FETCH_ERRORS:
        print("  ! " + e)
    con = store(claude + codex + hermes, claude_ev + codex_ev + incidents,
                {"codex_limits": limits, "status": status, "prices": prices})
    out = render(aggregate(con))
    print(f"Dashboard: {out}")
    if not a.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
