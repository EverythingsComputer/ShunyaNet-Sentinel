#!/usr/bin/env python3
"""E2E: mock RSS + mock LLM + mock notifiers through the real pipeline.

Checks:
 1. fetch -> alert published to GUI report, feed dock, report file
 2. Slack/Discord/Telegram/ntfy/generic-webhook receivers all got the alert
 3. second fetch with same feeds -> deduped (no new LLM calls)
 4. Analyze Now -> summary published, rolling file cleared, memory short-term appended
 5. memory profiler folds short-term -> long-term via mock LLM
 6. notifier failure path logged, not raised
"""
import json
import os
import sys
import threading
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP = os.environ["SENTINEL_APP"]
DIR = os.environ["SENTINEL_DIR"]

# ---------------- mock servers ----------------
LLM_CALLS = []
NOTIF_HITS = {"slack": [], "discord": [], "telegram": [], "ntfy": [], "generic": [], "fail": []}


class LLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        LLM_CALLS.append(body)
        prompt = body.get("messages", [{}])[0].get("content", "")
        if "Update long-term memory" in prompt:
            content = "MOCK CONSOLIDATED MEMORY"
        elif "summary-report layer" in prompt:
            content = "SUMMARY REPORT\nOverall Topic: mock\nKey Developments:\n- mock trend (Link: null)"
        elif "immediate-alert screening" in prompt:
            content = "ALERT: HIGH PRIORITY\nHeadline: Mock escalation detected\nMatched Signal: test\nConfidence: High"
        else:
            content = "ALERT: NO ALERT"
        resp = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *a):
        pass


class NotifHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0)))
        path = self.path
        if "/slack" in path:
            NOTIF_HITS["slack"].append(json.loads(raw or b"{}"))
        elif "/discord" in path:
            NOTIF_HITS["discord"].append(json.loads(raw or b"{}"))
        elif "/telegram" in path:
            NOTIF_HITS["telegram"].append(json.loads(raw or b"{}"))
        elif "/ntfy" in path:
            NOTIF_HITS["ntfy"].append((dict(self.headers), raw.decode("utf-8", "replace")))
        elif "/generic" in path:
            NOTIF_HITS["generic"].append(json.loads(raw or b"{}"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


class FailHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        NOTIF_HITS["fail"].append(True)
        self.send_response(500)
        self.end_headers()
        self.wfile.write(b"boom")

    def log_message(self, *a):
        pass


def serve(handler, port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


serve(LLMHandler, 8931)
serve(NotifHandler, 8932)
serve(FailHandler, 8933)

# ---------------- mock RSS feeds (file-based via requests patch is messy;
# instead point the app at a local HTTP server serving RSS) ----------------
RSS_XML = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Mock escalation item %d</title><link>https://example.test/item%d</link>
<pubDate>Sun, 18 Oct 2026 00:00:00 GMT</pubDate>
<description>Something notable happened near the monitored region.</description></item>
</channel></rss>"""


class RSSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        n = abs(hash(self.path)) % 1000
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        body = (RSS_XML % (n, n)).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


serve(RSSHandler, 8934)

# ---------------- app under test ----------------
spec = importlib.util.spec_from_file_location("appmod", APP)
mod = importlib.util.module_from_spec(spec)
sys.modules["appmod"] = mod
spec.loader.exec_module(mod)

# redirect only Telegram API traffic to the local mock (everything else passes)
_real_post = mod.requests.post


def _patched_post(url, *a, **k):
    if "api.telegram.org" in url:
        url = "http://127.0.0.1:8932/telegram"
    return _real_post(url, *a, **k)


mod.requests.post = _patched_post

from PySide6.QtWidgets import QApplication, QDialog
QDialog.exec = lambda self, *a: QDialog.open(self)

app = QApplication(sys.argv)
win = mod.SentinelWindow()
win.show()

# configure for the mocks
win.settings.update({
    "LLM_PROVIDER": "lmstudio",
    "LLM_BASE_URL": "http://127.0.0.1:8931/v1",
    "SLACK_WEBHOOK_URL": "http://127.0.0.1:8932/slack",
    "DISCORD_WEBHOOK_URL": "http://127.0.0.1:8932/discord",
    "TELEGRAM_BOT_TOKEN": "mocktoken",
    "TELEGRAM_CHAT_ID": "42",
    "NTFY_TOPIC_URL": "http://127.0.0.1:8932/ntfy",
    "GENERIC_WEBHOOK_URL": "http://127.0.0.1:8932/generic",
    "MEMORY_PROFILER": "1",
    "ALERTS_ENABLED": "1",
    "SUMMARIES_ENABLED": "1",
    "REPORT_OUTPUT_DIR": os.path.join(DIR, "out_reports"),
})
win.feeds = ["http://127.0.0.1:8934/f%d" % i for i in range(5)]

import time


def pump(seconds=2.0):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


# --- 1. alert pass (synchronous body, then pump for notifier callbacks)
win.fetch_and_send()
pump(3.0)

report_text = win.reply_box.toPlainText()
assert "Mock escalation" in report_text, "alert not shown in report box: %r" % report_text[:80]
assert len(win.feed_entries) >= 1, "no feed cards"
out_files = os.listdir(os.path.join(DIR, "out_reports"))
alerts = [f for f in out_files if f.startswith("ALERT_")]
assert alerts, "no ALERT file written: %s" % out_files
handoff = json.load(open(os.path.join(DIR, "out_reports", "agent_handoff.json")))
assert handoff["latest_report_type"] == "ALERT"
assert os.path.exists(handoff["latest_report_path"]), "handoff points nowhere"

# --- 2. every notifier channel received something
assert NOTIF_HITS["slack"], "slack got nothing"
assert NOTIF_HITS["discord"], "discord got nothing"
assert NOTIF_HITS["telegram"], "telegram got nothing"
assert NOTIF_HITS["ntfy"], "ntfy got nothing"
assert NOTIF_HITS["generic"], "generic got nothing"
tg = NOTIF_HITS["telegram"][0]
assert tg.get("chat_id") == "42", "telegram chat_id wrong: %s" % tg
ntfy_headers, ntfy_body = NOTIF_HITS["ntfy"][0]
assert ntfy_headers.get("Priority") == "high", "ntfy alert priority missing"
gen = NOTIF_HITS["generic"][0]
assert gen.get("type") == "alert" and gen.get("source"), "generic payload shape: %s" % gen

# --- 3. dedupe: second fetch, same feeds -> no new items -> no LLM alert call
calls_before = len(LLM_CALLS)
win.fetch_and_send()
pump(1.0)
assert len(LLM_CALLS) == calls_before, "dedup failed: %d extra LLM calls" % (len(LLM_CALLS) - calls_before)

# --- 4. summary pass via Analyze Now body
rolling = os.path.join(DIR, "rolling_rss.txt")
assert os.path.getsize(rolling) > 0, "rolling file empty after fetches"
win.perform_bulk_analysis_if_ready(force=True)
pump(2.5)
summaries = [f for f in os.listdir(os.path.join(DIR, "out_reports")) if f.startswith("SUMMARY_")]
assert summaries, "no SUMMARY file"
assert open(rolling).read().strip() == "", "rolling file not cleared after summary"
mem_st = open(os.path.join(DIR, "Memory_Short_Term.txt")).read()
assert "mock trend" in mem_st, "summary not appended to short-term memory"

# --- 5. memory profiler folds into long-term
win.memory_profiler_start_time = 0
win.process_memory_profiler_if_ready()
pump(1.0)
lt = open(os.path.join(DIR, "Memory_Long_Term.txt")).read()
assert "MOCK CONSOLIDATED MEMORY" in lt, "long-term memory not written: %r" % lt[:80]
assert open(os.path.join(DIR, "Memory_Short_Term.txt")).read().strip() == "", "short-term not cleared"

# --- 6. notifier failure path: no exception, logged only
win.settings["SLACK_WEBHOOK_URL"] = "http://127.0.0.1:8933/slack"
results = mod.dispatch_notifications(dict(win.settings), "failure probe", "ALERT")
failed = [r for r in results if r[0] == "Slack"]
assert failed and failed[0][1] is False, "slack failure not detected: %s" % results
assert NOTIF_HITS["fail"], "fail server never hit"

# --- 7. feed persistence round-trip
win.feeds = ["http://127.0.0.1:8934/persisted"]
win.save_app_state()
state = json.load(open(os.path.join(DIR, "app_state.json")))
assert state.get("feeds") == ["http://127.0.0.1:8934/persisted"], "feeds not saved"

print("E2E ALL PASS (%d LLM calls)" % len(LLM_CALLS))
win.close()
app.processEvents()
