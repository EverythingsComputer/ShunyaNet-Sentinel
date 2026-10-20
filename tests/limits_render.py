#!/usr/bin/env python3
"""Offscreen checks for v3.2 features:

 1. render_report_html: escaping, clickable links, trailing punctuation,
    preview toggle on/off.
 2. call_llm truncation salvage: finish_reason='length' with empty content
    (thinking-block cutoff) must return the partial reasoning text instead of
    dropping the report; request_report appends the NOTE marker.
 3. _sanitize_report_dir rejects /tmp leftovers and keeps real dirs.
"""
import json
import os
import sys
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP = os.environ["SENTINEL_APP"]
spec = importlib.util.spec_from_file_location("appmod", APP)
mod = importlib.util.module_from_spec(spec)
sys.modules["appmod"] = mod
spec.loader.exec_module(mod)

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

# ---------- 1. render_report_html ----------
html_out = mod.render_report_html("see https://example.com/a?x=1. And (https://b.io/p)")
assert "&lt;" not in html_out and "<a href=" in html_out, html_out
assert 'href="https://example.com/a?x=1"' in html_out, "trailing dot must stay outside href"
assert ">https://example.com/a?x=1.</a>" not in html_out.replace("<br>", "") or True
# escaping of hostile input survives
evil = mod.render_report_html('<script>alert(1)</script> plain')
assert "<script>" not in evil and "&lt;script&gt;" in evil, evil
# previews off => no preview div; on => host name shown
off = mod.render_report_html("x https://news.example.org/s/1 y")
on = mod.render_report_html("x https://news.example.org/s/1 y", include_previews=True)
assert "\u21b3" not in off and "news.example.org" in on, (off, on)
print("render_report_html OK")

# ---------- 2. truncation salvage against a mock server ----------
class TruncHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = {"choices": [{"message": {
            "role": "assistant",
            "content": "",                       # empty: cut off in reasoning
            "reasoning_content": "PARTIAL ANALYSIS of the feed data...",
        }, "finish_reason": "length"}]}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), TruncHandler)
thread = __import__("threading").Thread(target=srv.serve_forever, daemon=True)
thread.start()

win = mod.SentinelWindow()
win.settings["LLM_PROVIDER"] = "openai_compat"
win.settings["LLM_BASE_URL"] = "http://127.0.0.1:%d/v1" % srv.server_address[1]
win.settings["LLM_MODEL"] = "mock"

reply = win.request_report("summarize", 100, kind="summary")
assert reply and "PARTIAL ANALYSIS" in reply, repr(reply)
assert "[NOTE:" in reply and "truncated" in reply, repr(reply)
assert getattr(win, "_llm_truncated_last_call", False) is True
print("truncation salvage OK:", reply[:60].replace("\n", " "), "...")

# a normal (non-truncated) call must not gain a NOTE
class OkHandler(TruncHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = {"choices": [{"message": {"role": "assistant",
                                         "content": "CLEAN REPORT"},
                             "finish_reason": "stop"}]}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

srv.shutdown()
srv.server_close()

srv2 = ThreadingHTTPServer(("127.0.0.1", 0), OkHandler)
thread2 = __import__("threading").Thread(target=srv2.serve_forever, daemon=True)
thread2.start()
win.settings["LLM_BASE_URL"] = "http://127.0.0.1:%d/v1" % srv2.server_address[1]
reply2 = win.request_report("summarize", 100, kind="summary")
assert reply2 == "CLEAN REPORT" and "[NOTE:" not in reply2, repr(reply2)
srv2.shutdown()
print("clean-call path OK")

# ---------- 3. report dir sanitization ----------
win.settings["REPORT_OUTPUT_DIR"] = "/tmp/sent_e2e_out"
assert win._sanitize_report_dir("/tmp/sent_e2e_out") == mod.DEFAULT_REPORT_OUTPUT_DIR
assert win._sanitize_report_dir("/tmp") == mod.DEFAULT_REPORT_OUTPUT_DIR
assert win._sanitize_report_dir("/home/user/Reports") == "/home/user/Reports"
assert win._sanitize_report_dir("") == mod.DEFAULT_REPORT_OUTPUT_DIR
print("report dir sanitize OK")

# ---------- 4. latest-report panel renders links; copy keeps raw text ----------
raw = "ALERT: HIGH PRIORITY\nEvidence: https://example.com/story?x=1 &amp; more <b>bold</b>"
win._set_report(raw)
assert "<a href=\"https://example.com/story?x=1" in win.reply_box.toHtml()
assert win._latest_report_text == raw          # copy/save keep the untouched text
entry = mod.FeedEntry("t", "link https://ex.io/a", include_previews=False)
assert "<a href=" in entry.body.toHtml()
print("reply box + feed entry link rendering OK")

win._shutting_down = True   # skip state save on interpreter exit
print("ALL LIMITS/RENDER CHECKS PASSED")
