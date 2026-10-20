---
name: sentinel-monitor
description: Read ShunyaNet Sentinel alerts/summaries from its agent_handoff.json and act on new reports. Use when asked what Sentinel has seen, to forward or correlate its alerts, or to watch it on a schedule.
---

# ShunyaNet Sentinel watcher

Sentinel (a local RSS+LLM monitor) writes every alert and summary report to
disk and refreshes an index file. Read the index, open new reports, act.

## Setup (once)

- `HANDOFF` = absolute path to `agent_handoff.json`, shown in the Sentinel app
  under Help → Agent / Script Integration (default:
  `<Sentinel folder>/Report_Output/agent_handoff.json`).

## Watch loop

1. Read `$HANDOFF` (JSON). Note `latest_report_mtime` and
   `latest_report_type`.
2. If `latest_report_mtime` differs from the last value you recorded, read the
   file at `latest_report_path` — that is the full newest report.
3. Classify by first line: `ALERT: HIGH PRIORITY` = urgent;
   `SUMMARY REPORT` = periodic trend digest.
4. Do what the user asked: summarize, forward to a channel, cross-check the
   evidence links listed in the report, append to a timeline note, etc.
5. Record the mtime you processed so the next run skips old reports.

## Rules

- Read-only. Never write or delete Sentinel's files; Sentinel owns them.
- A report ending with `[NOTE: ... hit the configured max output length ...]`
  was cut off at that limit — the content before the marker is valid, just
  possibly mid-sentence. Mention the truncation when relaying the report.
- `Memory_Long_Term.txt` (path in the handoff JSON) is Sentinel's rolling
  situation picture — useful context, but it can be stale: never treat it as
  current news.
- If `agent_handoff.json` is missing, Sentinel has not produced a report yet;
  say so instead of guessing.
