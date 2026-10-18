#!/usr/bin/env python3
"""Restart behaviors: feeds round-trip, legacy setting migration, virgin first run."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP = os.environ["SENTINEL_APP"]
DIR = os.environ["SENTINEL_DIR"]

from PySide6.QtWidgets import QApplication, QDialog
QDialog.exec = lambda self, *a: QDialog.open(self)

import importlib.util


def load_mod():
    spec = importlib.util.spec_from_file_location("appmod2", APP)
    m = importlib.util.module_from_spec(spec)
    sys.modules["appmod2"] = m
    spec.loader.exec_module(m)
    return m


app = QApplication.instance() or QApplication(sys.argv)

# --- 1. virgin run: no state file -> quickstart feeds, first-run flag set after wizard
mod = load_mod()
win = mod.SentinelWindow()
win.show()
app.processEvents()
assert win.is_first_run is True
n_default = len(win.feeds)
print("virgin feeds:", n_default)
win.settings["LLM_BASE_URL"] = "http://seed.test:9/v1"
win.save_app_state()
win.close()
app.processEvents()

# --- 2. restart with custom feeds saved
mod = load_mod()
win = mod.SentinelWindow()
win.show()
app.processEvents()
assert win.is_first_run is False, "first-run flag wrong after state exists"
assert len(win.feeds) == n_default and win.settings["LLM_BASE_URL"] == "http://seed.test:9/v1"
custom = ["https://feeds.example/a.xml", "https://feeds.example/b.xml"]
win.feeds = custom
win.save_app_state()
win.close()
app.processEvents()

mod = load_mod()
win = mod.SentinelWindow()
win.show()
app.processEvents()
assert win.feeds == custom, "feed persistence failed: %s" % win.feeds[:3]
print("feeds persisted OK")
win.close()
app.processEvents()

# --- 3. legacy state migration (SUMMARY_ENABLED + BULK_ANALYSIS -> SUMMARIES_ENABLED)
legacy = {
    "active_profile": None,
    "topic_profile": {"overall_topic": "Legacy test", "subtopics": "a\nb"},
    "settings": {
        "LLM_PROVIDER": "ollama",
        "SUMMARY_ENABLED": "0",
        "BULK_ANALYSIS": "1",
    },
}
with open(os.path.join(DIR, "app_state.json"), "w") as fh:
    json.dump(legacy, fh)

mod = load_mod()
win = mod.SentinelWindow()
win.show()
app.processEvents()
assert win.get_setting("SUMMARIES_ENABLED", int) == 1, "legacy OR-migration failed"
assert win.overall_topic_edit.toPlainText() == "Legacy test"
# old WRITE_TO_FILE key must not crash anything and must be dropped from defaults
assert "WRITE_TO_FILE" not in mod.DEFAULT_SETTINGS
print("legacy migration OK")
win.close()
app.processEvents()

print("RESTART TESTS ALL PASS")
