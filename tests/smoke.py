#!/usr/bin/env python3
"""Offscreen smoke: boot, settings dialog tabs, wizard flow, close."""
import os, sys, time, importlib.util

os.environ["QT_QPA_PLATFORM"] = "offscreen"
APP = os.environ.get("SENTINEL_APP",
                     os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ShunyaNet_Sentinel.py"))
spec = importlib.util.spec_from_file_location("appmod", APP)
mod = importlib.util.module_from_spec(spec)
sys.modules["appmod"] = mod
spec.loader.exec_module(mod)

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
QDialog.exec = lambda self, *a: QDialog.open(self)   # un-modallize
# C++ static helpers bypass the Python exec patch; stub them to capture text
_captured_info = {}
QMessageBox.information = staticmethod(
    lambda *a, **k: _captured_info.update({"args": a}) or QMessageBox.Ok)

app = QApplication(sys.argv)
win = mod.SentinelWindow()
win.show()
app.processEvents()
print("BOOT OK; feeds:", len(win.feeds))

dlg = mod.SettingsDialog(win)
app.processEvents()
tabs = dlg._tabs
print("settings tabs:", [tabs.tabText(i) for i in range(tabs.count())])
assert " Appearance " in [tabs.tabText(i) for i in range(tabs.count())]
dlg._refresh_notify_summary()
print("notify summary:", dlg.test_notify_result.text())

# provider combo must carry every registry entry as data, incl. custom compat
prov = dlg.fields["LLM_PROVIDER"]
keys = [prov.itemData(i) for i in range(prov.count())]
assert "openai_compat" in keys and len(keys) == len(mod.PROVIDERS), keys
dlg.reject()

# --- theme engine: apply every theme, verify palette+sheet swap, then revert ---
before = dict(mod.PALETTE)
for k in mod.THEMES:
    win.apply_theme(k, persist=False)
    assert mod.PALETTE["accent"] == mod.THEMES[k]["accent"], k
    sheet = mod.QApplication.instance().styleSheet().lower()
    assert mod.THEMES[k]["accent"].lower() in sheet, k
print("themes apply OK:", list(mod.THEMES))
win.apply_theme(mod.DEFAULT_THEME, persist=False)

# --- fetch-thread regression: start_fetch_thread must free its slot on finish ---
win.settings["LLM_PROVIDER"] = "openai_compat"
win.settings["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"   # instant refuse
win.feeds = ["http://127.0.0.1:1/x.rss"]                 # unreachable feed
win.start_fetch_thread()
deadline = time.time() + 30
while win._fetch_worker is not None and time.time() < deadline:
    app.processEvents(); time.sleep(0.05)
assert win._fetch_worker is None, "fetch worker slot never freed (freeze regression)"
win.start_fetch_thread()   # second start must be accepted now
deadline = time.time() + 30
while win._fetch_worker is not None and time.time() < deadline:
    app.processEvents(); time.sleep(0.05)
assert win._fetch_worker is None, "second fetch slot never freed"
print("FETCH THREAD SLOT FREEING OK")

wiz = mod.SetupWizard(win)
app.processEvents()
for _ in range(mod.SetupWizard.STEPS):
    wiz._next()
    app.processEvents()
print("wizard launched flag:", wiz.launched)
win.show_agent_access()   # static QMessageBox exec is un-modalled above
print("AGENT DIALOG OK")
print("WIZARD OK")

win.close()
app.processEvents()
print("CLOSE OK")
