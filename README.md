<img width="1642" height="946" alt="ShunyaNetSentinelAppGraphicV1" src="https://github.com/user-attachments/assets/f1348720-4d36-4bfa-b8d6-ea92bdfd0a9f" />

# ShunyaNet Sentinel (v3.0 — UPDATED: 10/18/2026)

ShunyaNet Sentinel is a lightweight, cyberpunk-themed program that ingests RSS feeds (breaking news, social media, government alerts), sends them to an LLM for analysis, and delivers alerts and summary reports to the GUI and your notification channels at regular intervals.

The project uses an LLM hosted locally on the same machine or network (e.g., with Tailscale) via **LM Studio**, **Ollama**, or **llama.cpp** — or the **OpenAI API**. Alerting works over **Slack, Discord, Telegram, ntfy, any generic webhook, and desktop popups** (use as many at once as you want).

Compatible with current versions of Linux, macOS, and Windows.

The quality of reporting depends on your prompt, context size, RSS feeds, and LLM. Turn off "thinking" features if your model has them. Models that have performed well:
- GPT OSS 20b (thinking LOW in LM Studio)
- Hermes 4 70b
- Gemma 3 27b it abliterated (mlabonne's version)
- Qwen3 32b

*Strongly suggest macOS or Linux. See Known Issues at the end.*

------------------------------------------------------------------------

# High-Level Workflow

1. Enter topics of interest (the Setup Wizard walks you through this on first launch)
2. Provide a list of RSS feeds (bundled lists included, one URL per line)
3. Sentinel pulls feeds at your configured interval
4. Feed content goes to your LLM server
5. The LLM screens for alerts and, periodically, writes trend reports
6. Alerts/summaries land in the GUI **and** every notification channel you configured (phone push via Slack/Discord/Telegram/ntfy)
7. Every report is also saved as a plain text file — including an `agent_handoff.json` index so scripts and AI agents can consume Sentinel's output without touching the GUI

------------------------------------------------------------------------

# Core Capabilities

## RSS Feed Monitoring
- Periodic polling, fetched in parallel but serialized per-host (polite to rate limiters like Reddit)
- Deduplication by entry GUID; per-source failure logging
- Handles slow or malformed feeds without blocking the UI

## LLM-Based Analysis
- Works with any OpenAI-compatible `/v1/chat/completions` endpoint: LM Studio, Ollama, llama.cpp, OpenAI
- One prompt file drives both modes via `#> ALERT` / `#> SUMMARY` / `#> MEMORY` sections
- Automatic chunking when pulls exceed your token budget

## Alerting
- **Slack** incoming webhook
- **Discord** channel webhook
- **Telegram** bot (token + chat ID)
- **ntfy** topic URL (free, no-account phone push; self-hostable)
- **Generic webhook** (POSTs JSON — Home Assistant, scripts, anything)
- **Desktop popups** via the system tray
- Per-channel **Test button** in Settings → Notifications using the values you just typed, before saving
- Toggle whether periodic summaries also go out, or only instant alerts

## Local & Self-Hosted
Other than polling RSS feeds and sending your notifications (both optional), all information stays on hardware you control.

## Agent / Script Integration
Sentinel writes stable, machine-readable artifacts beside every report: `Report_Output/agent_handoff.json` points at the newest alert/summary file plus the memory files. Any local agent can poll that one JSON file to follow what Sentinel is seeing. See [docs/AGENT-INTEGRATION.md](docs/AGENT-INTEGRATION.md) for the full contract, and Help → Agent / Script Integration inside the app.

------------------------------------------------------------------------

# Requirements

- Python 3.10+
- Dependencies (see `requirements.txt`): PySide6, feedparser, requests

LLM hosted via:
- LM Studio (recommended)
- Ollama
- llama.cpp server
- OpenAI API key

------------------------------------------------------------------------

# Installation

## 1. Clone (or download the ZIP) and enter the folder

    git clone https://github.com/EverythingsComputer/ShunyaNet-Sentinel.git
    cd ShunyaNet-Sentinel

## 2. (Strongly recommended) Create a virtual environment

macOS / Linux:

    python3 -m venv venv
    source venv/bin/activate

Windows (PowerShell):

    python -m venv venv
    venv\Scripts\Activate.ps1

## 3. Install requirements

    pip install -r requirements.txt

## 4. Run

macOS / Linux: `python3 ShunyaNet_Sentinel.py`
Windows: `python ShunyaNet_Sentinel.py`

------------------------------------------------------------------------

# Quick Start

On first launch, Sentinel walks you through setup with a guided **Setup Wizard** (reopen anytime with `Ctrl+W` or Help → Setup Wizard):

1. **Topics** – your overall topic and signals (one per line). Autosaves as you type.
2. **Model connection** – pick LM Studio / Ollama / llama.cpp / OpenAI; the wizard shows exactly what to enter, with a **Test Connection** button so you know it works before relying on it.
3. **Feeds** – one click loads the bundled Quick-Start (short) or World News (long) list.
4. **Confirm** – review and launch your first fetch (the button actually fetches).

Manual setup (skipping the wizard):

1. Enter topics in the Targeting panel.
2. File → Bundled Source Lists → Quick-Start List (or load your own `.txt`, one RSS URL per line — your loaded list is remembered across restarts now).
3. Run → Settings (`Ctrl+,`) → Connection tab: choose provider, set the Server URL (LM Studio example: `http://localhost:1234/v1`), press **Test Connection**, Save & Apply.
4. Load a model in LM Studio and turn on "Serve on Local Network" in Server Settings. Tips:
    - In the Load Model window, turn on "Manually choose model load parameters"
    - Set Max Concurrent Predictions to 1 (for now)
    - Turn off or minimize thinking, if available
    - A solid moderate choice: gpt-oss-20b, thinking "low"
5. Click the cat! (...or hit Fetch / Send with `Ctrl+Enter`, or wait for the auto-fetch countdown in the status bar)

To push alerts to your phone: Settings → Notifications → fill one channel (Slack/Discord/Telegram/ntfy), press **Send Test Notification**, Save & Apply. Setup hints for each service are in that tab's tooltips.

Every setting has a hover tooltip; Help (`F2`) lists keyboard shortcuts. Matrix-rain screensaver: `F1`.

------------------------------------------------------------------------

# Full Instructions & Config

1. **Topics** — enter topics in the Targeting panel (autosaves). Save named sets as Profiles to switch watch-lists quickly. Your loaded feed list is part of app state and persists across restarts.

2. **Prompts: one file does it all (`sentinel_prompt.txt`).** It loads automatically and contains marked sections:
   - `#> ALERT` — instructions sent with every fetch (instant alert screening)
   - `#> SUMMARY` — instructions for periodic trend reports
   - `#> MEMORY` — long-term-memory policy; Sentinel appends it to the summary prompt **only while the memory profiler is enabled**

   A prompt file with no `#>` markers is used whole for both modes. File → Load Prompt File swaps files (it reports which sections it detects). Tokens `{TOPICS}`, `{CHUNK}` and `{LONG_TERM_MEMORY}` are filled in at send time. Tweaking the wording can substantially improve signal quality.

3. **Data sources** — "Load Data Sources…" takes a `.txt` with one RSS URL per line (`#` comments allowed) or a JSON list. Bundled lists:
   - `Default_test-v1.txt` — short, varied; keeps the first pull quick so you can diagnose your setup
   - `Default_long-v1.txt` — longer world/breaking-news focus
   - `India_regional_example-v1.txt`, `Iran_List_V1.txt` — region/topic examples

   Reddit URLs work by adding `.rss`; Bluesky profiles work as `https://bsky.app/profile/NAME/RSS`. Tools like RSS-Bridge convert sites without feeds (e.g., Telegram). **Randomize your list order** so rate-limiting sites don't get hit in bursts (Sentinel also serializes requests per host automatically).

4. **Settings** (`Ctrl+,`) — five tabs, every field with a tooltip:

| Tab | Key settings |
|-----|--------------|
| **Connection** | Provider, Server URL, Model name, API key, Test Connection |
| **Limits** | Max input/output tokens (1 token ≈ 4 characters), chunk size (in CHARACTERS), items per feed, LLM timeout |
| **Automation** | Auto-fetch interval; Mode 1: instant Alerts; Mode 2: periodic Summaries with analysis window |
| **Notifications** | Slack / Discord / Telegram / ntfy / generic webhook; desktop popups; whether summaries are pushed too; Send Test Notification |
| **Memory & Output** | Report folder, sequential long-term memory profiler and its caps |

Defaults work for most setups. Notable values: `FETCH_INTERVAL` (default 600 s — must exceed your LLM's processing time or a backlog builds), `MAX_INPUT_TOKENS` (keep below your model's context window; enable chunking if you exceed it).

##### Provider cheat sheet
| | lmstudio | ollama | llamacpp | openai |
|---|---|---|---|---|
| URL | `http://localhost:1234/v1` | `http://localhost:11434/v1` | its `/v1` endpoint | ignored |
| Model | blank = loaded model | e.g. `llama3.1` | your `--alias` | e.g. `gpt-4o-mini` |
| API key | blank | blank | if you set one | `sk-...` |

5. **Reports & agent files** — every alert/summary is written to the Report folder (`REPORT_OUTPUT_DIR`, default `Report_Output/`) with timestamped names, and `agent_handoff.json` is refreshed to point at the newest artifacts. File → Open Report Folder, or Help → Agent / Script Integration for paths.

6. **Done — click the cat!** Keep it simple on the first run: defaults + short feed list, confirm end-to-end, then tune context, feeds, and prompt.

------------------------------------------------------------------------

# Tips, Tricks, and Known Issues

Issues/Warnings:
- Poor configuration can overload your LLM or hardware. Make sure your LLM replies faster than your RSS pull interval.
- LM Studio's "parallel" predictions can cause timeouts/backlogs with this app — keep Max Concurrent Predictions at 1 until you've tested otherwise.
- A slow or dead URL slows the feed pull; watch the Terminal Log occasionally and cull failures (the log is timestamped now).
- Earlier versions had spontaneous quits on Windows 11 after hours of running. The threading model was rewritten in v3.0 (daemon worker threads, no blocking joins on close) which removes the known causes; if you still see it on an old build, upgrade or run under Linux.

Suggestions:
- The first fetch is a stress test: maximum feed volume, likely stale items — good for checking plumbing and measuring your slowest LLM pass.
- Thinking models sometimes produce malformed replies or loop. Turn thinking off (or LOW) first.
- Some sites rate-limit RSS pulls; Sentinel serializes per-host requests, but randomizing list order helps further.

------------------------------------------------------------------------

# Status

Beta (v3.0)

------------------------------------------------------------------------

# License

Apache 2.0. Attribution: ShunyaNet / EverythingsComputer. **No warranty**; provided "as is".

### Third-party libraries
- **PySide6** — LGPL
- **feedparser** — BSD
- **requests** — Apache 2.0

------------------------------------------------------------------------

# Name Origin

My cat.
