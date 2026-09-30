# Build & Commit Pro Tracker

A local dashboard for your AI coding tools. It reads the usage logs that **Claude Code**, **OpenAI Codex CLI**, **Hermes Agent**, **Gemini CLI**, **OpenCode** and **Aider** already keep on your machine, and turns them into one page. For every tool it shows:

- how many tokens you're using
- what that usage would cost at API prices
- how close you are to your limits, with desktop alerts before you hit them
- how that usage turns into **git commits**: how many, how many were AI-assisted, and the API cost per commit

The page also shows live **Claude service status** and a **crypto price ticker**, and there's an optional **menu bar meter** for macOS and Linux.

A single Python file. No dependencies, no accounts, no server. Your usage data never leaves your computer.

<!-- Add a screenshot here: ![Dashboard](screenshot.png) -->

## What it shows

| Section | What you get |
|---|---|
| **Stat cards** | Claude tokens in the last 5 hours and 7 days, **API value** of your usage over 30 days, cache hit rate, Codex 5h/weekly limit %, the other tools you use, **commits** in 30 days |
| **Usage chart** | **Tokens, cost or commits** (with/without AI) over **24h, 48h or 72h** (hourly) or **7, 14 or 30 days** (daily), per tool, with **limit hits** (dashed lines) and **Claude incidents** (red bands) marked |
| **Claude status** | Live component status and the last 30 days of incidents from status.claude.com |
| **When you work** | Weekday × hour heatmap of your token use, with your peak hour, busiest day and active days |
| **Recent sessions** | Your last 20 sessions across all tools. **Click one** for a request-by-request timeline, cache hit rate, cost, models and the Claude Code tools it used (edits, reads, terminal commands…) |
| **Models / projects** | Tokens and cost by model, with a **tip** when a big model is doing lots of small jobs; tokens, cost and commits by project |
| **Pace forecasts** | When you'll hit your Claude 5h limit at this hour's pace, where Codex's 5h and weekly limits will land by reset, this week vs your 3-week average, and a projected bar for the rest of today |
| **Price ticker** | Your choice of coins in your choice of currency, with 24h change and a 7-day sparkline; drag the slim track underneath, or use the mouse wheel, when they don't all fit |
| **⚙ Settings** | Switches for each tracker, git tracking, screenshot mode, crypto prices and the moving background, a coin picker, and CSV/JSON export (top-right of the page) |
| **Menu bar meter** *(optional)* | Claude and Codex % in your macOS or Linux menu bar; see [Menu bar meter](#menu-bar-meter-optional) |

**On limits:** Anthropic doesn't publish token limits for Claude subscriptions. So the 5-hour gauge compares against the usage level where you *actually* hit a limit, once that has happened at least once. Until then it compares against your busiest 5-hour window. Codex percentages come straight from Codex's own rate-limit reports.

Your Claude limits are shared across Claude Code **and** the Claude app and claude.ai, but only Claude Code leaves logs on your machine, so the dashboard can't see app usage. For the full picture, check *Settings → Usage* in Claude. The Claude 5h forecast and the 85% alert start once Claude Code has hit a limit at least once.

**On cost:** the dashboard prices every model call at the provider's public API rates. If you're on a subscription (Claude Pro/Max, ChatGPT Plus/Pro), that's not what you pay. It's what the same usage would cost you on the API, which shows how much value you're getting from the plan. Add your plan prices (see *Settings*) and the cost card shows the ratio. Hermes, Aider and OpenCode report their own cost figures, and those are used where present. A $0 figure (usually a subscription) is valued at list prices instead.

## Supported tools

| Tool | Where it reads from | Override |
|---|---|---|
| Claude Code | `~/.claude/projects/` (or `~/.config/claude/projects/`), plus Hermes' sandboxes under `~/.hermes/sandboxes/` | `HERMES_HOME` for the sandboxes |
| Codex CLI | `~/.codex/sessions/` | `CODEX_HOME` |
| Hermes Agent | `~/.hermes/state.db` | `HERMES_HOME` |
| Gemini CLI | `~/.gemini/tmp/*/chats/` | `GEMINI_DATA_DIR` (comma-separated) |
| OpenCode | `~/.local/share/opencode/opencode.db`, plus the older `storage/message/` files | `XDG_DATA_HOME` |
| Aider | `.aider.chat.history.md` in your project folders | the `aider_dirs` setting |

`~` means your home folder; on Windows that's `C:\Users\<you>`. Hermes Agent runs under WSL on Windows, so run the script inside WSL to include it.

**Hermes** keeps a running total for each chat, and chats can stay open for days (for example through its Telegram gateway). At each refresh the dashboard records how much each chat has grown, dated at its latest activity, so today's work shows up today even in a chat that started last week.

**Not trackable:** chats in the Claude, ChatGPT or Gemini apps and websites. They run on the providers' servers and leave nothing on your machine.

**Aider** writes its history into each project rather than one central place. By default the script looks up to four folders deep in `~/code`, `~/projects`, `~/dev`, `~/src`, `~/repos`, `~/Documents/GitHub` and `~/tools`. Add your own with `--set aider_dirs=~/work,~/code`.

**Cursor isn't supported.** It keeps usage on Cursor's servers, not on your machine. The only way to read it locally is to take Cursor's saved login token, and a tool like this shouldn't do that.

The dashboard keeps its own history in `~/.ai-usage/usage.db`. That means it keeps working after a tool deletes old logs; Claude Code, for example, removes logs after 30 days.

## Git activity

The dashboard also reads the git history of the projects your AI tools work in, so you can see how usage turns into commits:

- **Which repos:** any repo your AI tools were used in (it follows the working folders recorded by Claude Code, Codex, OpenCode, Gemini CLI and Aider), plus any you add with `--set 'git.extra_repos=["~/code/site"]'`
- **Which commits:** your own from the last 30 days (matched on the `user.email` set in each repo), across local branches, excluding merges. Turn *Only my commits* off in ⚙ Settings to include everyone's.
- **What counts as AI-assisted:**
  - a commit with a `Co-Authored-By:` line naming Claude, Codex, Gemini, OpenCode, Copilot or Cursor
  - a commit Aider made (author ends in `(aider)`)
  - a commit with Claude Code's *Generated with Claude Code* footer
  - one of your own commits made within 30 minutes of an AI tool working in that repo (or in a sandbox or chat that isn't tied to a repo). This covers commits you make by hand from what the AI prepared, which carry no trailer
- **What it shows:**
  - a *Commits · 30d* card (hover it for lines changed and API cost per commit)
  - a *Commits* view on the usage chart
  - a *Commits* column in *Projects*

Only numbers are kept (date, repo name, lines added and removed, files changed, which AI helped). Commit messages and code are never stored. The git commands are strictly read-only, and features a cloned repo could use to run programs (pagers, text-conversion and external diff drivers, fsmonitor, signature checks) are switched off. Turn git tracking off entirely with `--set git.enabled=false`.

## Requirements

- **Python 3.9 or newer.** Nothing to `pip install`.
- At least one of the tools above, used on this machine.
- A web browser.
- Optional:
  - `git` on your PATH, for commit tracking
  - [SwiftBar](https://swiftbar.app) or xbar (macOS), or Argos (Linux), for the menu bar meter
  - `notify-send` (Linux), for desktop alerts

## Install and run

### macOS

```bash
# 1. Python: macOS asks to install Xcode Command Line Tools the first time you run python3. Say yes.
python3 --version

# 2. Get the script
git clone https://github.com/18catalyst/BuildAndCommitProTracker.git ~/tools/BuildAndCommitProTracker
cd ~/tools/BuildAndCommitProTracker

# 3. (Optional) verify it matches the published checksum
shasum -a 256 -c SHA256SUMS

# 4. Run it. This builds the dashboard and opens it in your browser.
python3 usage.py

# 5. (Optional) refresh automatically in the background (uses launchd)
python3 usage.py --install
```

### Linux

```bash
# 1. Python is usually preinstalled. If not: sudo apt install python3  (or dnf / pacman)
python3 --version

# 2. Get the script
git clone https://github.com/18catalyst/BuildAndCommitProTracker.git ~/tools/BuildAndCommitProTracker
cd ~/tools/BuildAndCommitProTracker

# 3. (Optional) verify the checksum
sha256sum -c SHA256SUMS

# 4. Run it
python3 usage.py

# 5. (Optional) refresh in the background (adds a line to your user crontab; needs cron installed)
python3 usage.py --install
```

Desktop alerts on Linux use `notify-send` (package `libnotify-bin` on Debian/Ubuntu).

### Windows (PowerShell)

```powershell
# 1. Install Python from python.org or the Microsoft Store, then check it:
py --version

# 2. Get the script
git clone https://github.com/18catalyst/BuildAndCommitProTracker.git "$HOME\tools\BuildAndCommitProTracker"
cd "$HOME\tools\BuildAndCommitProTracker"

# 3. (Optional) verify the checksum. Compare the output with SHA256SUMS.
Get-FileHash .\usage.py -Algorithm SHA256

# 4. Run it
py usage.py

# 5. (Optional) refresh in the background (creates a Task Scheduler task)
py usage.py --install
```

No git? Download `usage.py` from this repo, using the **Raw** button then *Save as*, and run it the same way from the folder you saved it to.

## Upgrading

```bash
cd ~/tools/BuildAndCommitProTracker && git pull
python3 usage.py --install      # only if you use auto-refresh
```

Cloned it back when it was called *Claude & Codex Token Tracker*? Your clone keeps working: GitHub redirects the old address, and `git pull` picks up the rename. If you downloaded the file by hand, replace `usage.py` with the new one. Your history, settings and keys live in `~/.ai-usage/` and carry over. The version number is shown top-right on the page, or run `python3 usage.py --version`.

## Opening it again

```bash
python3 usage.py          # refresh the data and open the dashboard (Windows: py usage.py)
```

Or open the last saved copy directly: `~/.ai-usage/dashboard.html` (Windows: `%USERPROFILE%\.ai-usage\dashboard.html`). If you use the menu bar meter, its *Open dashboard* item does the same.

With `--install` turned on, the data refreshes in the background every 15 minutes by default. An open dashboard tab reloads itself on the same schedule. Status and prices update live while the page is open.

## Alerts

During each refresh, the script sends a desktop notification:

- when you **hit a usage limit** (Claude Code or Codex), with the reset time when it's known
- when Claude usage passes **85% of your usual limit** in a 5-hour window. This needs at least one past limit hit to learn where your limit sits.
- when Codex's 5-hour or weekly limit passes **85%**
- when a **new Claude incident** starts

Each alert fires once, not on every refresh. Alerts work best with `--install`, so checks keep happening in the background. Test that notifications reach you:

```bash
python3 usage.py --test-alert
```

On macOS the first notification may ask for permission; allow it under *System Settings → Notifications*. Change thresholds or turn alerts off in *Settings* below.

## Menu bar meter (optional)

Not needed for anything else: the dashboard, alerts and background refresh all work without it. If you'd like your limits at a glance, it puts Claude and Codex % in the menu bar with a dropdown of key numbers, pace warnings, *Open dashboard* and *Refresh now*. The text turns red at 85%; when you've had no Claude Code use in 5 hours it shows your 7-day total instead.

It needs a small free menu bar app:

- **macOS:** [SwiftBar](https://swiftbar.app) (or xbar)
  ```bash
  brew install --cask swiftbar     # or download it from swiftbar.app
  ```
  Open SwiftBar once and choose a plugin folder, then:
  ```bash
  python3 usage.py --install-menubar
  ```
- **Linux (GNOME):** the Argos extension. Create `~/.config/argos`, then run the same command.
- **Windows:** not available.

If the command can't find your plugin folder, pass it: `python3 usage.py --install-menubar ~/Documents/SwiftBar`. The meter re-reads your data every 2 minutes; keep `--install` on so the data itself stays fresh. To remove it, delete `build-commit-pro.2m.sh` from the plugin folder.

## Settings

### On the page

Click **⚙ Settings** (top-right). You can:

- turn individual trackers on or off (hidden tools disappear from every card, chart and table)
- turn commit tracking on or off, and switch between *only my commits* and everyone's
- **Screenshot mode:** replaces every project and repo name with *Project A*, *Project B*… so you can share the dashboard safely (names are swapped, not blurred)
- **Export** the last 30 days as a daily CSV or a full JSON file (names hidden too if screenshot mode is on)
- show or hide crypto prices
- stop the moving background
- pick which coins appear in the ticker, from about 24 popular coins

Changes apply instantly and are remembered **in that browser**. The page is a local file, so it can't change your settings file itself. Instead, the panel shows a ready-made command; run it once to make your choices the defaults in every browser.

### In the settings file

Settings live in `~/.ai-usage/config.json`. Change them from the command line:

```bash
python3 usage.py --settings                       # show everything and the file path
python3 usage.py --set currency=GBP
python3 usage.py --set coins=BTC,ETH,SOL,DOGE
python3 usage.py --set 'plans={"Claude Code": 20, "Codex": 20}'
python3 usage.py --unset coins                    # back to the default
```

| Setting | Default | What it does |
|---|---|---|
| `currency` | `USD` | Currency for the price ticker (USD, GBP, EUR, JPY, …) |
| `coins` | `SOL,BTC,ETH,XMR,BNB,XRP,LINK` | Coins shown in the ticker. Common symbols just work; for others, give the CoinGecko id: `WIF:dogwifcoin`. The popular coins in the page's picker are always fetched too, in the same single request |
| `show_prices` | `true` | `false` hides the ticker and stops price requests |
| `screenshot_mode` | `false` | Start with project and repo names hidden (also applies to `--export`) |
| `hidden_tools` | none | Tools hidden on the dashboard by default, e.g. `--set 'hidden_tools=["Hermes"]'` (use `"Git"` to hide commits) |
| `git.enabled` | `true` | Read git history at all |
| `git.author` | `me` | Default commit view: `me` (your `user.email` in each repo) or `all` |
| `git.extra_repos` | none | Extra repos to include, e.g. `--set 'git.extra_repos=["~/code/site"]'` |
| `refresh_minutes` | `15` | Background refresh and page reload interval. Run `--install` again after changing it |
| `alerts.enabled` | `true` | Turn all alerts on or off |
| `alerts.claude_5h_percent` | `85` | Claude 5-hour alert threshold (% of your estimated limit) |
| `alerts.codex_percent` | `85` | Codex 5-hour/weekly alert threshold |
| `alerts.limit_hits` | `true` | Alert when you hit a limit |
| `alerts.incidents` | `true` | Alert on new Claude incidents |
| `plans` | none | Monthly plan cost in USD per tool, e.g. `{"Claude Code": 100}`. The cost card compares against it |
| `prices` | none | Your own model prices, USD per 1M tokens: `--set 'prices.hermes-4=[0.2,0.6,0,0.02]'` (input, output, cache write, cache read) |
| `theme.accent` | `#ff6a13` | Accent colour: buttons, heatmap, and Claude's colour unless you set one |
| `theme.edge` | `rgba(129,140,248,.38)` | Panel border colour |
| `theme.panel_opacity` | `0.05` | `0` fully see-through, `1` solid |
| `theme.animate_background` | `true` | `false` keeps the ASCII background still |
| `theme.colors` | none | Per-tool colours, e.g. `--set 'theme.colors={"Codex": "#a3e635"}'` |
| `aider_dirs` | see *Supported tools* | Folders to search for Aider history files |

**Model prices** are built in for current and recent Claude, OpenAI and Gemini models (list prices as of September 2026). If a model has no price, the Terminal output says so. Its cost shows as `—` until you add one under `prices`.

## Crypto prices (optional keys)

Prices try **CoinMarketCap** first if you've saved a key, then **CoinGecko**. Both have free tiers:

- **CoinMarketCap:** sign up at [coinmarketcap.com/api](https://coinmarketcap.com/api/) (Basic plan, free) and copy your key from *API Keys*. Each refresh uses 1 credit, so refreshing every 15 minutes uses about 2,900 of the free 15,000 credits a month.
- **CoinGecko:** create a free *Demo* key at [coingecko.com/en/developers/dashboard](https://www.coingecko.com/en/developers/dashboard).

```bash
python3 usage.py --cmc-key YOUR_KEY
# or
python3 usage.py --coingecko-key YOUR_KEY
```

Keys are stored only on your machine, in `~/.ai-usage/config.json` (on macOS and Linux, readable only by you). They are never written into the dashboard page.

## All commands

| Command | What it does |
|---|---|
| `usage.py` | Collect, rebuild and open the dashboard |
| `usage.py --no-open` | Collect and rebuild without opening a browser |
| `usage.py --install` | Auto-refresh in the background (launchd / cron / Task Scheduler) |
| `usage.py --uninstall` | Remove the auto-refresh |
| `usage.py --settings` | Show current settings |
| `usage.py --set KEY=VALUE` | Change a setting (repeatable; nested keys use dots) |
| `usage.py --unset KEY` | Reset a setting to its default |
| `usage.py --cmc-key KEY` | Save a CoinMarketCap API key |
| `usage.py --coingecko-key KEY` | Save a CoinGecko demo API key |
| `usage.py --test-alert` | Send a test desktop notification |
| `usage.py --export [FOLDER]` | Write every request and commit to CSV, plus a JSON summary |
| `usage.py --install-menubar [FOLDER]` | Optional: add the menu bar meter to SwiftBar / xbar / Argos |
| `usage.py --menubar` | Print the menu bar output (used by the menu bar plugin) |
| `usage.py --offline` | Skip the status and price requests |
| `usage.py --inspect-hermes` | Show the Hermes database layout (for troubleshooting) |
| `usage.py --version` | Print the version |

If you move `usage.py` after running `--install`, run `--install` again from the new location.

## Privacy

- The only network requests are to `status.claude.com` (service status), `pro-api.coinmarketcap.com` / `api.coingecko.com` (prices), and Google Fonts and cdnjs (page fonts and the chart library). Use `--offline` to skip status and prices, or `--set show_prices=false` to drop prices for good.
- Your token counts, project names, session history and commit counts stay in `~/.ai-usage/` on your machine. The script only reads other tools' files and your repos; it never changes them.
- **Before sharing a screenshot,** turn on *Screenshot mode* in ⚙ Settings, which swaps project and repo names for *Project A*, *Project B*…

## Troubleshooting

- **`python3: command not found` (Windows):** use `py` instead of `python3`.
- **Prices say "unavailable":** run `python3 usage.py --no-open` and read the lines starting with `!`. `HTTP 401/403` means the price source wants a key; see *Crypto prices* above.
- **A tool doesn't appear:** it only shows once its log folder has data from *this* machine. Web and app sessions (ChatGPT, claude.ai, Gemini web) aren't stored locally.
- **Aider doesn't appear:** add the folder that holds your projects: `--set aider_dirs=~/work`.
- **No commits showing:** the repo must be one your AI tools ran in, or listed in `git.extra_repos`. Check `git config user.email` in that repo matches the email on your commits, or turn off *Only my commits* in ⚙ Settings.
- **Hermes shows no usage:** run `python3 usage.py --inspect-hermes` and open an issue with the output. Models Hermes is configured with but that have no price show as `—`; add a price with `--set prices.MODEL=[...]`.
- **Menu bar meter says it can't find a plugin folder:** open SwiftBar once and pick a folder, or pass one: `--install-menubar ~/Documents/SwiftBar`. If it's installed but not visible, your menu bar may be full; macOS hides items that don't fit.
- **No notifications:** run `--test-alert`. On macOS check *System Settings → Notifications* for Script Editor; on Linux install `notify-send`. Alerts are always printed in the Terminal output too.
- **Which version am I running?** It's shown top-right on the page and at the start of the Terminal output, or run `--version`.

## Verifying the download

`SHA256SUMS` contains the SHA-256 hash of `usage.py` for this release:

```
646ddc7b09c2f59b06287951dc33ecf95117df520ae53704a68de67fa3bccd58  usage.py
```

If the hash of your file doesn't match, don't run it. Download it again from this repo.

## Disclaimer

Not affiliated with Anthropic, OpenAI, Google, Nous Research, Aider, OpenCode, CoinMarketCap or CoinGecko. Token and cost figures are estimates from local logs and public list prices, not bills. Prices are informational only and are not financial advice.
