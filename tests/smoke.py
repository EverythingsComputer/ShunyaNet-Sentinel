#!/usr/bin/env python3
"""Offscreen smoke: boot, settings dialog tabs, wizard flow, close."""
import os, sys, importlib.util

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
dlg._refresh_notify_summary()
print("notify summary:", dlg.test_notify_result.text())
dlg.reject()

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
