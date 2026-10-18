# Tests

Isolated end-to-end tests for ShunyaNet Sentinel. They copy the app into a
temporary scratch directory (no test ever touches your settings, memory, or
report files), run it offscreen with `QT_QPA_PLATFORM=offscreen`, and drive
the real pipeline against local mock servers (LLM, RSS, Slack/Discord/
Telegram/ntfy/generic webhook receivers).

Run from the repo root with any Python 3.10+ that has PySide6/feedparser/
requests available (or `venv-linux/bin/python` on Linux):

    python3 tests/cli_run.py tests/smoke.py      # boot, dialogs, wizard, close
    python3 tests/cli_run.py tests/e2e.py        # full alert->notify->summary->memory pipeline
    python3 tests/cli_run.py tests/restart.py    # persistence + legacy-state migration

Each prints `... ALL PASS` / `... OK` and exits 0 on success. The scratch dir
path is printed for inspection afterwards.

- `smoke.py`  — window boots, Settings tabs build (incl. Notifications), wizard
  flow reaches "Launch First Fetch", clean close.
- `e2e.py`    — mock RSS feeds -> alert pass -> report file + agent_handoff.json
  -> every notifier channel receives the message with correct payload shapes ->
  dedupe on a second fetch -> summary pass clears the rolling file and feeds
  memory -> memory profiler folds short-term into long-term -> notifier failure
  path is logged, not raised -> feed list persistence round-trip.
- `restart.py`— virgin-run defaults, custom feeds survive restart, legacy
  app_state.json (SUMMARY_ENABLED/BULK_ANALYSIS) migrates to SUMMARIES_ENABLED.
