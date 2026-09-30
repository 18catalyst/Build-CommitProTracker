# Changelog

## v2.8

- **Easier-to-read title:** bold monospace instead of the pixel font, with a soft dark halo so the moving background no longer cuts through the letters. The ampersand picks up your accent colour.

## v2.7

- **Renamed to Build & Commit Pro Tracker** (previously *Claude & Codex Token Tracker*), now that it covers six AI tools and your git commits. The script (`usage.py`), your data folder (`~/.ai-usage`) and the background refresh are unchanged, so upgrading keeps your history and settings.

## v2.6

- **Git activity:** commits from the repos your AI tools work in, found automatically from each tool's working folder.
  - Commits count as AI-assisted when they have a Co-Authored-By line from Claude, Codex, Gemini, OpenCode, Copilot or Cursor, Aider's `(aider)` author tag, or Claude Code's footer.
  - New *Commits · 30d* card, a *Commits* chart view (with vs without AI), a commits column in *Projects*, and API cost per commit.
- **Settings panel** gains a *Git* section: commit tracking on/off and *Only my commits* vs everyone's.
- **New settings:** `git.enabled`, `git.author`, `git.extra_repos`.
- **Security:** git runs read-only with pagers, text-conversion/external diff drivers, fsmonitor and signature checks disabled, so a cloned repo's config can't run programs.
- **Project names** now come from the actual folder or repo name (e.g. `foundwell` rather than `Projects-foundwell`), so a project and its repo line up.
- **The *Claude status* stat card was removed** (the header and the status panel already show it), to keep the stats on one row.
- **The crypto ticker has a scroll track** when the coins don't fit: always visible (macOS hides normal scrollbars), draggable and clickable, and the mouse wheel and arrow keys scroll it too.
- **Piping the output** (e.g. into `head`) no longer prints a traceback.

## v2.5

- **⚙ Settings panel on the page:**
  - switch each tracker, crypto prices and the moving background on or off
  - pick ticker coins from about 24 popular coins

  Choices are saved in the browser, and the panel gives you a one-line command to make them your defaults.
- **New `hidden_tools` setting** for tools you never want to see.
- **The *When you work* heatmap now fills its panel**, with a summary line: peak hour, busiest hours, busiest day, active days.
- **The ticker keeps the settings button and version visible** on narrow screens; the coins scroll instead.

## v2.4

**New tools**
- Gemini CLI, OpenCode (new SQLite and older file layouts) and Aider are now tracked alongside Claude Code, Codex and Hermes.
- Tools you don't use are hidden automatically.

**Cost estimates**
- Every call is priced at the provider's public API rates (Claude, OpenAI and Gemini models, September 2026 list prices).
- New *API value · 30d* card; set your plan prices to see how much value you're getting.
- The chart switches between **Tokens** and **Cost**. Sessions, models and projects show cost too.
- Add or override model prices with `--set prices.MODEL=[in,out,cache_write,cache_read]`.

**Alerts**
- Desktop notifications on macOS, Windows and Linux: limit hits, nearing your Claude 5-hour limit, Codex over threshold, new Claude incidents.
- Each alert fires once. Try it with `--test-alert`.

**Settings**
- `--settings`, `--set key=value` and `--unset key` for currency, coins, refresh interval, alert thresholds, plan prices, model prices, theme colours, panel opacity, background animation and Aider folders.
- `--install` uses your `refresh_minutes`.

**Layout and fixes**
- Codex 5h and weekly limits share one card; extra tools share an *Other tools* card, so everything stays on one row.
- Projects now cover every tool, not just Claude Code.
- Tiny coin prices (e.g. PEPE) keep their significant digits.
- The CoinGecko key is no longer embedded in the dashboard page.
- Project names are safely escaped when embedded in the page.
- Read-only access to other tools' databases falls back to a temporary copy if the app has them locked.

## v2.3

First public release: Claude Code, Codex and Hermes usage, Claude status and incidents, limit-hit markers, heatmap, sessions, models, crypto ticker, auto-refresh on macOS/Linux/Windows.
