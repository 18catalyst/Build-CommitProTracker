# Claude & Codex Token Tracker

A local dashboard for your AI coding tools. It reads the usage logs that **Claude Code**, **OpenAI Codex CLI** and **Hermes Agent** already keep on your machine, and turns them into one page. The page also shows live **Claude service status** and a **crypto price ticker**.

A single Python file. No dependencies, no accounts, no server. Your usage data never leaves your computer.

<!-- Add a screenshot here: ![Dashboard](screenshot.png) -->

## What it shows

| Section | What you get |
|---|---|
| **Stat cards** | Claude tokens in the last 5 hours and 7 days, cache hit rate, Codex 5h/weekly limit %, Hermes usage, Claude status |
| **Usage chart** | Hourly (48h) or daily (30d) tokens per tool, with **limit hits** (dashed orange lines) and **Claude incidents** (red bands) marked |
| **Claude status** | Live component status and the last 30 days of incidents from status.claude.com |
| **When you work** | Weekday × hour heatmap of your token use |
| **Recent sessions** | Your last 20 sessions: project, start time, length, calls, tokens, model |
| **Models / projects** | Input, output and cache tokens by model; Claude Code tokens by project |
| **Price ticker** | SOL, BTC, ETH, XMR, BNB, XRP and LINK in USD, with 24h change and a 7-day sparkline |

A note on limits: Anthropic doesn't publish token limits for Claude subscriptions. So the 5-hour gauge compares against the usage level where you *actually* hit a limit, once that has happened at least once. Until then it compares against your busiest 5-hour window. Codex percentages come straight from Codex's own rate-limit reports.

## Requirements

- **Python 3.9 or newer.** Nothing to `pip install`.
- At least one of: Claude Code, Codex CLI, or Hermes Agent, used on this machine.
- A web browser.

## Install and run

### macOS

```bash
# 1. Python: macOS asks to install Xcode Command Line Tools the first time you run python3. Say yes.
python3 --version

# 2. Get the script
git clone https://github.com/18catalyst/ClaudeAndCodexTokenTracker.git ~/tools/ClaudeAndCodexTokenTracker
cd ~/tools/ClaudeAndCodexTokenTracker

# 3. (Optional) verify it matches the published checksum
shasum -a 256 -c SHA256SUMS

# 4. Run it. This builds the dashboard and opens it in your browser.
python3 usage.py

# 5. (Optional) refresh automatically every 15 minutes (uses launchd)
python3 usage.py --install
```

### Linux

```bash
# 1. Python is usually preinstalled. If not: sudo apt install python3  (or dnf / pacman)
python3 --version

# 2. Get the script
git clone https://github.com/18catalyst/ClaudeAndCodexTokenTracker.git ~/tools/ClaudeAndCodexTokenTracker
cd ~/tools/ClaudeAndCodexTokenTracker

# 3. (Optional) verify the checksum
sha256sum -c SHA256SUMS

# 4. Run it
python3 usage.py

# 5. (Optional) refresh every 15 minutes (adds a line to your user crontab; needs cron installed)
python3 usage.py --install
```

### Windows (PowerShell)

```powershell
# 1. Install Python from python.org or the Microsoft Store, then check it:
py --version

# 2. Get the script
git clone https://github.com/18catalyst/ClaudeAndCodexTokenTracker.git "$HOME\tools\ClaudeAndCodexTokenTracker"
cd "$HOME\tools\ClaudeAndCodexTokenTracker"

# 3. (Optional) verify the checksum. Compare the output with SHA256SUMS.
Get-FileHash .\usage.py -Algorithm SHA256

# 4. Run it
py usage.py

# 5. (Optional) refresh every 15 minutes (creates a Task Scheduler task)
py usage.py --install
```

No git? Download `usage.py` from this repo, using the **Raw** button then *Save as*, and run it the same way from the folder you saved it to.

## Opening it again

```bash
python3 usage.py          # refresh the data and open the dashboard (Windows: py usage.py)
```

Or open the last saved copy directly: `~/.ai-usage/dashboard.html` (Windows: `%USERPROFILE%\.ai-usage\dashboard.html`).

With `--install` turned on, the data refreshes every 15 minutes in the background. An open dashboard tab also reloads itself every 10 minutes. Status and prices update live while the page is open.

## Crypto prices (optional keys)

Prices try **CoinMarketCap** first if you've saved a key, then **CoinGecko**. Both have free tiers:

- **CoinMarketCap:** sign up at [coinmarketcap.com/api](https://coinmarketcap.com/api/) (Basic plan, free) and copy your key from *API Keys*. Each refresh uses 1 credit, so refreshing every 15 minutes uses about 2,900 of the free 15,000 credits a month.
- **CoinGecko:** create a free *Demo* key at [coingecko.com/en/developers/dashboard](https://www.coingecko.com/en/developers/dashboard).

```bash
python3 usage.py --cmc-key YOUR_KEY
# or
python3 usage.py --coingecko-key YOUR_KEY
```

Keys are stored only on your machine, in `~/.ai-usage/config.json`. They are never written into the dashboard page.

## All commands

| Command | What it does |
|---|---|
| `usage.py` | Collect, rebuild and open the dashboard |
| `usage.py --no-open` | Collect and rebuild without opening a browser |
| `usage.py --install` | Auto-refresh every 15 min (launchd / cron / Task Scheduler) |
| `usage.py --uninstall` | Remove the auto-refresh |
| `usage.py --cmc-key KEY` | Save a CoinMarketCap API key |
| `usage.py --coingecko-key KEY` | Save a CoinGecko demo API key |
| `usage.py --offline` | Skip the status and price requests |
| `usage.py --inspect-hermes` | Show the Hermes database layout (for troubleshooting) |
| `usage.py --version` | Print the version |

If you move `usage.py` after running `--install`, run `--install` again from the new location.

## Where the data comes from

| Tool | Log location | Override |
|---|---|---|
| Claude Code | `~/.claude/projects/` (or `~/.config/claude/projects/`) | n/a |
| Codex CLI | `~/.codex/sessions/` | `CODEX_HOME` |
| Hermes Agent | `~/.hermes/state.db` | `HERMES_HOME` |

`~` means your home folder; on Windows that's `C:\Users\<you>`. Hermes Agent runs under WSL on Windows, so its data lives inside your WSL home. Run the script inside WSL to include it.

The dashboard keeps its own history in `~/.ai-usage/usage.db`. That means it keeps working after Claude Code deletes logs older than 30 days.

## Privacy

- The only network requests are to `status.claude.com` (service status), `pro-api.coinmarketcap.com` / `api.coingecko.com` (prices), and Google Fonts and cdnjs (page fonts and the chart library). Use `--offline` to skip status and prices.
- Your token counts, project names and session history stay in `~/.ai-usage/` on your machine.
- **Before sharing a screenshot,** check the *Recent sessions* and *Projects* panels. They show your project folder names.

## Troubleshooting

- **`python3: command not found` (Windows):** use `py` instead of `python3`.
- **Prices say "unavailable":** run `python3 usage.py --no-open` and read the lines starting with `!`. `HTTP 401/403` means the price source wants a key; see *Crypto prices* above.
- **Hermes shows "none found":** run `python3 usage.py --inspect-hermes` and open an issue with the output.
- **Codex doesn't appear:** it only shows up once `~/.codex/sessions/` has logs from the Codex CLI. Codex runs in the ChatGPT app or on the web aren't stored locally.
- **Which version am I running?** It's shown top-right on the page and at the start of the Terminal output, or run `--version`.

## Verifying the download

`SHA256SUMS` contains the SHA-256 hash of `usage.py` for this release:

```
4366ee7479c5da2bab9fbeca199d9e34626814a9361ca41a260a5ad0567a03a5  usage.py
```

If the hash of your file doesn't match, don't run it. Download it again from this repo.

## Disclaimer

Not affiliated with Anthropic, OpenAI, Nous Research, CoinMarketCap or CoinGecko. Token figures are estimates from local logs. Prices are informational only and are not financial advice.
