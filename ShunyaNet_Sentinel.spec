# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for ShunyaNet Sentinel.

Built by .github/workflows/release.yml (Windows, macOS .app, Linux), but it also
works locally from the project root:

    pip install pyinstaller pillow
    python packaging/make_icons.py
    pyinstaller --noconfirm ShunyaNet_Sentinel.spec

Layout notes that matter:
  * one-FOLDER bundle (COLLECT), never onefile — the app keeps its state next to
    itself and a onefile build re-extracts to a temp dir on every launch;
  * macOS gets a real BUNDLE (.app) target so it can be code sealed; hand-built
    app folders are what Gatekeeper reports as "damaged";
  * everything the user may edit ships inside the bundle, and the program also
    accepts a copy placed next to the executable (see shipped_path() in the
    source), so users can add their own feed lists without rebuilding.
"""
import os
import re
import sys

APP_NAME = "ShunyaNetSentinel"
DISPLAY_NAME = "ShunyaNet Sentinel"
BUNDLE_ID = "com.shunyanet.sentinel"

src_root = os.path.abspath(SPECPATH)


def _version():
    """Read APP_VERSION from the program so the bundle never disagrees with it."""
    path = os.path.join(src_root, "ShunyaNet_Sentinel.py")
    with open(path, "r", encoding="utf-8") as fh:
        m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
    if not m:
        raise SystemExit("could not read APP_VERSION from ShunyaNet_Sentinel.py")
    return m.group(1)


VERSION = _version()


def _strip_flag():
    """Drop debug symbols from the bundle (set SENTINEL_STRIP=0 to keep a
    symbolized build when debugging a crash)."""
    return os.environ.get("SENTINEL_STRIP", "1").strip() not in ("", "0")


def _icon(name):
    """Use the generated icon when packaging/make_icons.py has been run."""
    p = os.path.join(src_root, "packaging", "icons", name)
    return p if os.path.exists(p) else None


icon_win = _icon("appicon.ico")
icon_mac = _icon("AppIcon.icns")

datas = [
    (os.path.join(src_root, "Data Sources"), "Data Sources"),
    (os.path.join(src_root, "topic_profiles.default.json"), "."),
    (os.path.join(src_root, "sentinel_prompt.txt"), "."),
    (os.path.join(src_root, "assets"), "assets"),
    (os.path.join(src_root, "README.md"), "."),
    (os.path.join(src_root, "LICENSE"), "."),
]

a = Analysis(
    [os.path.join(src_root, "ShunyaNet_Sentinel.py")],
    pathex=[src_root],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "PyQt5", "PyQt6",
              "unittest",
              # Qt modules this app never imports; dropping them roughly halves
              # the download. The release smoke test boots the real bundle, so a
              # wrong exclusion fails CI instead of shipping broken.
              "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.QtDesigner",
              "PySide6.QtUiTools", "PySide6.QtHelp", "PySide6.QtSql",
              "PySide6.QtTest", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
              "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtGraphs",
              "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
              "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
              "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets",
              "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
              "PySide6.QtSerialBus", "PySide6.QtSensors", "PySide6.QtPositioning",
              "PySide6.QtLocation", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
              "PySide6.QtTextToSpeech", "PySide6.QtHttpServer",
              "PySide6.QtVirtualKeyboard", "PySide6.QtQuickTest",
              "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # one-folder: binaries/datas go to COLLECT
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=_strip_flag(),
    upx=False,                      # UPX packing breaks macOS code sealing
    console=False,                  # windowed app; --smoke-test writes SENTINEL_SMOKE_FILE
    # A windowed build normally reports a startup exception with a MODAL dialog -
    # invisible headless on CI, where it just hangs until the job times out. Send
    # it to stderr instead (the workflow redirects that into its build log).
    disable_windowed_traceback=True,
    icon=icon_win if sys.platform == "win32" else icon_mac,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=_strip_flag(),
    upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="%s.app" % APP_NAME,
        icon=icon_mac,
        bundle_identifier=BUNDLE_ID,
        info_plist={
            "CFBundleName": DISPLAY_NAME,
            "CFBundleDisplayName": DISPLAY_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleIdentifier": BUNDLE_ID,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright":
                "Apache-2.0 - ShunyaNet / EverythingsComputer",
            "NSPrincipalClass": "NSApplication",
        },
    )
