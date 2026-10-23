# Agent / Script Integration

ShunyaNet Sentinel is a GUI app, but everything it produces is also written to
plain files designed to be read by scripts and AI agents (Hermes, Claude Code,
cron jobs, Home Assistant automations, ...). No API keys, no server — just the
local filesystem.

## The handoff index (start here)

`<Report_Output_DIR>/agent_handoff.json` — refreshed atomically after every
saved alert or summary:

```json
{
  "app": "ShunyaNet Sentinel",
  "version": "3.3.1",
  "updated": "2026-10-18T03:44:33",
  "latest_report_type": "ALERT",
  "latest_report_path": "/path/to/Report_Output/ALERT_0344_18-10-26_1.txt",
  "latest_report_mtime": 1792000000.0,
  "report_dir": "/path/to/Report_Output",
  "memory_long_term": "/path/to/Memory_Long_Term.txt",
  "memory_short_term": "/path/to/Memory_Short_Term.txt",
  "rolling_feed": "/path/to/rolling_rss.txt"
}
```

Poll this one file. When `updated` (or `latest_report_mtime`) changes, open
`latest_report_path` — that is the newest report in full.

## File map

All paths are next to `ShunyaNet_Sentinel.py` unless noted.

| File | Contents | Lifetime |
|------|----------|----------|
| `Report_Output/ALERT_HHMM_DD-MM-YY_N.txt` | One instant alert as returned by the LLM (headline, matched signal, confidence, evidence links) | Permanent until you delete |
| `Report_Output/SUMMARY_HHMM_DD-MM-YY_N.txt` | One periodic trend report | Permanent until you delete |
| `Report_Output/agent_handoff.json` | Index above | Overwritten each report |
| `Memory_Long_Term.txt` | Consolidated long-term memory (LLM-maintained narrative of the monitoring situation) | Rewritten by the profiler while memory is enabled |
| `Memory_Short_Term.txt` | Recent report excerpts awaiting consolidation | Emptied after each profiler fold |
| `rolling_rss.txt` | Raw collected feed entries since the last summary | Cleared after each summary; self-trims at 16 MB |
| `app_state.json` | App settings incl. topics, feeds, provider config (contains secrets — do not commit) | Overwritten on change |

## Recommended agent loop

1. Read `agent_handoff.json` (every N minutes via cron, or watch the file).
2. If `latest_report_mtime` is newer than last seen: read the report file.
3. Act per your skill's policy — e.g. forward to another channel, cross-check
   the evidence links, log a timeline entry, wake a human.
4. Alert files begin with `ALERT: HIGH PRIORITY`; summary files with
   `SUMMARY REPORT`. Use that prefix as a cheap classifier.

## Push instead of polling

For push-style integration set **Settings → Notifications → Generic webhook**:
Sentinel POSTs JSON to any URL:

```json
{
  "title": "SENTINEL ALERT",
  "text": "<full report text>",
  "type": "alert",
  "source": "ShunyaNet Sentinel",
  "timestamp": "2026-10-18T03:44:33"
}
```

That shape drops straight into Home Assistant (`webhook` trigger), ntfy with a
JSON template, or a small listener that wakes an agent.

## Writing an agent skill (e.g. Hermes)

A skill for an agent needs only:

- the absolute path of `agent_handoff.json` (shown in Help → Agent / Script
  Integration inside the app),
- the rule "compare `latest_report_mtime`, read new reports once",
- whatever domain response you want (notify, correlate, archive).

Keep the skill read-only against Sentinel's files; Sentinel owns writes.

## Stability notes

- Report filenames never overwrite: `_N` suffix increments within a minute.
- `agent_handoff.json` is written via temp-file + rename, so readers never see
  a half-written file.
- The JSON schema above is additive: fields may appear in future versions,
  existing ones will not change meaning.
