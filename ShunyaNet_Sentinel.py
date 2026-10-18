#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ShunyaNet Sentinel v3.0 -- RSS / LLM event monitor with sequential memory

Cyberpunk green-on-black monitoring tool: polls RSS feeds, screens them with a
locally-hosted LLM (LM Studio / Ollama / llama.cpp / OpenAI), and delivers
alerts + periodic summary reports to the GUI, desktop popups, Slack, Discord,
Telegram, ntfy, or any generic webhook, and timestamped files. An experimental
sequential memory layer folds past summaries into a long-term memory file the
LLM consults on later runs.

UI
  * QMainWindow with dockable / floatable panels: "Targeting" (topics, sources,
    prompts) left; LATEST REPORT over TERMINAL LOG in the center splitter;
    filterable "Report Feed" cards right. Geometry, dock layout and splitter
    ratios persist; View -> Reset Layout forgets only the layout.
  * First run launches a 4-step setup wizard (Help -> Setup Wizard, Ctrl+W,
    to reopen): topics -> LLM connection (with live Test Connection) -> feed
    list -> confirm-and-launch. Settings is a tabbed dialog (Connection /
    Limits / Automation / Notifications / Memory & Output) where every field
    carries a tooltip and each page scrolls instead of clipping.
  * F1 matrix-rain screensaver (timer idles at zero cost when hidden),
    Ctrl+= / Ctrl+- UI scaling, keyboard shortcuts throughout (F2 lists them).

Alerting
  * Any combination of: Slack, Discord, Telegram bot, ntfy topic, generic JSON
    webhook, native desktop popups via the system tray. Settings ->
    Notifications has a Test button that uses the values as typed. Long
    reports are split to each platform's message limit.

Agent integration
  * Every saved report refreshes Report_Output/agent_handoff.json -- a small
    JSON index pointing at the newest alert/summary plus memory files, so
    external scripts and AI agents can follow Sentinel by polling one file
    (see docs/AGENT-INTEGRATION.md).

Robustness
  * All LLM / RSS work runs on daemon threads; closing the window never blocks
    on an in-flight request and callbacks always marshal back via Qt signals.
  * Prompts are filled by token replacement (stray "{" cannot crash them);
    feed summaries are HTML-stripped before hitting a prompt; report-feed text
    is HTML-escaped before rendering; report filenames never collide.
  * RSS feeds are fetched in parallel but serialized per host (polite to rate
    limiters), with per-source failure logging; the rolling file self-trims at
    a size cap; window state from a missing screen falls back gracefully;
    report output dirs saved on another OS are sanitized on load; loaded feed
    lists persist in app_state.json.
"""

import sys
import os
import re
import html
import json
import time
import random
import atexit
import shutil
import subprocess
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

APP_VERSION = "3.1.0"

import requests
import feedparser

from PySide6.QtCore import (
    Qt, QTimer, Signal, QByteArray, QEasingCurve, QPropertyAnimation,
    Property,
)
try:                       # QPointF lives in QtCore on all supported PySide6
    from PySide6.QtCore import QPointF
except ImportError:        # pragma: no cover - binding variant safety
    from PySide6.QtGui import QPointF
try:                       # QPolygonF moved between modules across versions
    from PySide6.QtGui import QPolygonF
except ImportError:        # pragma: no cover - binding variant safety
    from PySide6.QtCore import QPolygonF
from PySide6.QtGui import (
    QAction, QIcon, QPixmap, QPainter, QFont, QFontDatabase, QColor, QFontMetrics,
    QPen,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QTextBrowser, QPushButton, QToolButton,
    QComboBox, QCheckBox, QSpinBox, QSplitter, QScrollArea, QDockWidget, QDialog,
    QDialogButtonBox, QTabWidget, QFrame, QMessageBox, QStyle, QInputDialog,
    QFileDialog,
)

# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #

if getattr(sys, "frozen", False):
    exe_dir = os.path.dirname(sys.executable)
    app_bundle_dir = os.path.abspath(os.path.join(exe_dir, "..", ".."))
    APP_DIR = os.path.abspath(os.path.join(app_bundle_dir, ".."))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def load_assets():
    assets_dir = resource_path("assets")
    assets_files = {}
    if os.path.exists(assets_dir):
        for root, _, files in os.walk(assets_dir):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, assets_dir)
                assets_files[rel_path] = full_path
    return assets_files


assets = load_assets()

FEEDS = [
    "http://rss.cnn.com/rss/cnn_topstories.rss",
    "http://rss.cnn.com/rss/cnn_world.rss",
    "http://rss.cnn.com/rss/cnn_us.rss",
    "http://feeds.bbci.co.uk/news/rss.xml",
    "http://www.euronews.com/rss",
    "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml",
    "https://www.spc.noaa.gov/products/spcrss.xml",
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.atom",
]

FETCH_INTERVAL = 600
ITEMS_PER_FEED = 50
MAX_TOPICS = 10
MAX_PROFILES = 20
ROLLING_FILE = os.path.join(APP_DIR, "rolling_rss.txt")
PROFILE_FILE = os.path.join(APP_DIR, "topic_profiles.json")
SLACK_WEBHOOK_URL = ""
STATE_FILE = os.path.join(APP_DIR, "app_state.json")
SENTINEL_FILE = os.path.join(APP_DIR, ".sentinel_first_run")
DEFAULT_PROMPT_FILE = os.path.join(APP_DIR, "sentinel_prompt.txt")
DEFAULT_REPORT_OUTPUT_DIR = os.path.join(APP_DIR, "Report_Output")
MEMORY_SHORT_TERM_FILE = os.path.join(APP_DIR, "Memory_Short_Term.txt")
MEMORY_LONG_TERM_FILE = os.path.join(APP_DIR, "Memory_Long_Term.txt")
MEMORY_PROFILER_INTERVAL = 7200

MAX_FEED_ENTRIES = 30
MAX_SEEN_GUIDS = 8000
FEED_BODY_CHARS = 4000

DEFAULT_UI_FONT_PT = 10

# --------------------------------------------------------------------------- #
# Themes
#
# Every color the UI paints comes from the active palette dict. 'text' is the
# main foreground; 'accent' is the interactive highlight (selection, focused
# borders, selected tab); 'on_accent' is readable text drawn on accent fills.
# Cyber themes are monospace; Professional switches to the system UI font.
# --------------------------------------------------------------------------- #

THEMES = {
    "cyber_green": {
        "label": "Cyberpunk Green", "mono": True, "radius": 3,
        "text": "#00FF41", "accent": "#00FF41", "on_accent": "#000000",
        "dim": "#0a3d18", "bg": "#000000", "panel": "#000800",
        "card": "#010501", "btn": "#001a05", "hover": "#032b0c",
        "title": "#001503", "bar": "#000d02", "sep": "#0c2a12",
        "hint": "#35a357", "disabled": "#1f5c2e",
        "body": "#001900", "reply": "#001a03",
    },
    "cyber_amber": {
        "label": "Cyberpunk Amber", "mono": True, "radius": 3,
        "text": "#FFA000", "accent": "#FFB300", "on_accent": "#1a0e00",
        "dim": "#5c3a00", "bg": "#000000", "panel": "#0d0700",
        "card": "#080400", "btn": "#1f1200", "hover": "#33200a",
        "title": "#190e00", "bar": "#0d0700", "sep": "#3a2500",
        "hint": "#c98f4b", "disabled": "#6e5127",
        "body": "#1a0f00", "reply": "#170d00",
    },
    "cyber_ice": {
        "label": "Cyberpunk Blue", "mono": True, "radius": 3,
        "text": "#4FC3F7", "accent": "#29B6F6", "on_accent": "#001520",
        "dim": "#0d3b56", "bg": "#000000", "panel": "#00080f",
        "card": "#00050a", "btn": "#001524", "hover": "#062a40",
        "title": "#00101c", "bar": "#000d17", "sep": "#0c2a3e",
        "hint": "#4b9cc9", "disabled": "#275168",
        "body": "#00141f", "reply": "#00121c",
    },
    "mono_light_on_dark": {
        "label": "Mono \u2014 White on Black", "mono": True, "radius": 3,
        "text": "#FFFFFF", "accent": "#FFFFFF", "on_accent": "#000000",
        "dim": "#5a5a5a", "bg": "#000000", "panel": "#0a0a0a",
        "card": "#060606", "btn": "#141414", "hover": "#2b2b2b",
        "title": "#101010", "bar": "#0a0a0a", "sep": "#3a3a3a",
        "hint": "#9e9e9e", "disabled": "#4f4f4f",
        "body": "#111111", "reply": "#101010",
    },
    "mono_dark_on_light": {
        "label": "Mono \u2014 Black on White", "mono": True, "radius": 3,
        "text": "#101010", "accent": "#101010", "on_accent": "#FFFFFF",
        "dim": "#9e9e9e", "bg": "#FFFFFF", "panel": "#FFFFFF",
        "card": "#FAFAFA", "btn": "#F0F0F0", "hover": "#DCDCDC",
        "title": "#E8E8E8", "bar": "#F2F2F2", "sep": "#C4C4C4",
        "hint": "#5c5c5c", "disabled": "#a8a8a8",
        "body": "#F7F7F7", "reply": "#F7F7F7",
    },
    "professional": {
        "label": "Professional (Paper)", "mono": False, "radius": 6,
        "text": "#2B3440", "accent": "#3D5AFE", "on_accent": "#FFFFFF",
        "dim": "#C9C3B8", "bg": "#FAF7F1", "panel": "#FFFFFF",
        "card": "#FFFFFF", "btn": "#F2EFE8", "hover": "#E6E1D6",
        "title": "#EFEBE1", "bar": "#F2EFE8", "sep": "#DDD7CA",
        "hint": "#6E7683", "disabled": "#B4AFA5",
        "body": "#FCFAF5", "reply": "#FCFAF5",
    },
}
DEFAULT_THEME = "cyber_green"

# active palette (module-level so widget builders can read it)
PALETTE = dict(THEMES[DEFAULT_THEME])


def theme_font_family():
    """Font family the current theme wants."""
    if PALETTE.get("mono", True):
        return BASE_FONT_FAMILY
    try:
        return QApplication.font().family() or "Sans Serif"
    except Exception:
        return "Sans Serif"


def _pick_monospace_font():
    """First monospace font actually installed on this system."""
    try:
        try:
            families = set(QFontDatabase.supportedFamilies())   # Qt >= 6.10
        except AttributeError:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                families = set(QFontDatabase().families())      # older Qt
        for name in ("Cascadia Mono", "JetBrains Mono", "Fira Code", "Consolas",
                     "DejaVu Sans Mono", "Liberation Mono", "Menlo", "Courier New"):
            if name in families:
                return name
        fixed = QFontDatabase.systemFont(QFontDatabase.FixedFont).family()
        if fixed:
            return fixed
    except Exception:
        pass
    return "monospace"


BASE_FONT_FAMILY = "monospace"   # resolved for real once QApplication exists (main)

# --------------------------------------------------------------------------- #
# LLM providers. Every one speaks the OpenAI chat-completions wire format;
# the app always POSTs to <base_url>/chat/completions. Endpoints below were
# checked against each provider's official docs (OpenAI-compat pages).
#   key_required: whether the hint should push for an API key
#   base_url:     default/placeholder base URL ('' = user must supply)
# --------------------------------------------------------------------------- #

PROVIDERS = {
    "openai_compat": {
        "label": "OpenAI-compatible (LM Studio, llama.cpp, vLLM, unsloth, custom)",
        "base_url": "http://localhost:1234/v1",
        "model": "",
        "key_required": False,
        "hint": "Any server that speaks the OpenAI API: LM Studio (enable \u201cServe on "
                "Local Network\u201d), llama.cpp server, vLLM, unsloth servers, Ollama "
                "(http://localhost:11434/v1) and similar. Blank model uses the loaded "
                "model where supported. Local servers need no API key.",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_required": True,
        "hint": "OpenAI: paste your API key (sk-...). Default URL is fine unless you "
                "route through a proxy.",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-4-6",
        "key_required": True,
        "hint": "Anthropic Claude via its OpenAI-compatible endpoint. Create an API "
                "key at console.anthropic.com.",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.8-flash",
        "key_required": True,
        "hint": "Google AI Studio API key (aistudio.google.com). Uses Google's "
                "OpenAI-compatible endpoint.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4-6",
        "key_required": True,
        "hint": "OpenRouter (openrouter.ai): one key for many models; use the "
                "vendor/model id exactly as listed there.",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "key_required": True,
        "hint": "Groq (console.groq.com): very fast hosted open models.",
    },
    "mistral": {
        "label": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-large-latest",
        "key_required": True,
        "hint": "Mistral La Plateforme (console.mistral.ai).",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_required": True,
        "hint": "DeepSeek API (platform.deepseek.com). Base URL has no /v1 suffix.",
    },
    "together": {
        "label": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "key_required": True,
        "hint": "Together AI (api.together.ai): hosted open models; use the exact "
                "model id from their model list.",
    },
    "xai": {
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.7",
        "key_required": True,
        "hint": "xAI Grok (docs.x.ai).",
    },
}
PROVIDER_ORDER = list(PROVIDERS.keys())


def provider_label(key):
    return PROVIDERS.get(key, {}).get("label", key)


def chat_completions_url(provider, base_url):
    """Full request URL for a provider. All providers use the OpenAI wire
    format at <base>/chat/completions; empty base falls back to the default."""
    p = PROVIDERS.get(provider) or PROVIDERS["openai_compat"]
    base = (base_url or "").strip().rstrip("/") or p["base_url"].rstrip("/")
    return base + "/chat/completions"


# legacy provider names -> new keys (old saved state is migrated on load)
LEGACY_PROVIDER_MAP = {"lmstudio": "openai_compat", "llamacpp": "openai_compat",
                       "ollama": "openai_compat"}


DEFAULT_SETTINGS = {
    "LLM_PROVIDER": "lmstudio",
    "LLM_BASE_URL": "http://localhost:1234/v1",
    "LLM_MODEL": "",
    "LLM_API_KEY": "",
    "SLACK_WEBHOOK_URL": SLACK_WEBHOOK_URL,
    "DISCORD_WEBHOOK_URL": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "NTFY_TOPIC_URL": "",
    "GENERIC_WEBHOOK_URL": "",
    "NOTIFY_DESKTOP": "1",
    "NOTIFY_SUMMARIES": "1",
    "UI_THEME": DEFAULT_THEME,
    "MAX_INPUT_TOKENS": 4000,
    "MAX_OUTPUT_TOKENS": 1500,
    "MAX_INPUT_TOKENS_BULK": 20000,
    "MAX_OUTPUT_TOKENS_BULK": 5000,
    "FETCH_INTERVAL": FETCH_INTERVAL,
    "ITEMS_PER_FEED": ITEMS_PER_FEED,
    "USE_CHUNKED_MODE": "1",
    "CHUNK_TOKENS": 2000,   # was CHUNK_SIZE in characters; migrated on load
    "ALERTS_ENABLED": "1",
    "SUMMARIES_ENABLED": "1",
    "ANALYSIS_WINDOW": 3600,
    "BULK_ANALYSIS": "0",
    "REPORT_OUTPUT_DIR": DEFAULT_REPORT_OUTPUT_DIR,
    "MEMORY_PROFILER": "0",
    "MEMORY_LONG_TERM_MAX_TOKENS": 5000,
    "MEMORY_SHORT_TERM_CHUNK_TOKENS": 10000,
    "MEMORY_PROFILER_OUTPUT_TOKENS": 5000,
    "LLM_TIMEOUT": 900,
    "PROMPT_FILE": "",
}

INT_SETTINGS = (
    "MAX_INPUT_TOKENS", "MAX_OUTPUT_TOKENS", "MAX_INPUT_TOKENS_BULK",
    "MAX_OUTPUT_TOKENS_BULK", "FETCH_INTERVAL", "ITEMS_PER_FEED", "CHUNK_TOKENS",
    "ANALYSIS_WINDOW", "MEMORY_LONG_TERM_MAX_TOKENS",
    "MEMORY_SHORT_TERM_CHUNK_TOKENS", "MEMORY_PROFILER_OUTPUT_TOKENS",
    "LLM_TIMEOUT",
)

def build_style_sheet(p):
    """Full Qt stylesheet from a palette dict (see THEMES)."""
    return """
QWidget { background-color: %(bg)s; color: %(text)s; }
QMainWindow::separator { background: %(sep)s; width: 3px; height: 3px; }
QMainWindow::separator:hover { background: %(accent)s; }

QLabel { background: transparent; font-weight: bold; }
QLabel[role="hint"] { color: %(hint)s; font-weight: normal; }

QLineEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {
    background-color: %(panel)s; border: 1px solid %(dim)s;
    border-radius: %(radius)spx; padding: 3px; color: %(text)s;
    selection-background-color: %(accent)s; selection-color: %(on_accent)s;
}
QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus,
QSpinBox:focus { border: 1px solid %(accent)s; }
QComboBox::drop-down { border: 0; width: 16px; }
QComboBox QAbstractItemView { background-color: %(panel)s; color: %(text)s;
    border: 1px solid %(accent)s; selection-background-color: %(dim)s;
    selection-color: %(text)s; }

QPushButton, QToolButton { background-color: %(btn)s; color: %(text)s;
    border: 1px solid %(dim)s; border-radius: %(radius)spx; padding: 4px 8px; }
QPushButton:hover, QToolButton:hover { background-color: %(hover)s; border-color: %(accent)s; }
QPushButton:pressed, QToolButton:pressed { background-color: %(accent)s; color: %(on_accent)s; }
QPushButton:checked, QToolButton:checked { background-color: %(dim)s; border-color: %(accent)s; }
QPushButton:disabled, QToolButton:disabled { color: %(disabled)s; border-color: %(sep)s; }
QToolButton[flat="true"] { border: 0; background: transparent; }

QCheckBox { spacing: 6px; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid %(dim)s;
    background: %(panel)s; }
QCheckBox::indicator:checked { background-color: %(accent)s; border-color: %(accent)s; }

QTabWidget::pane { border: 1px solid %(dim)s; }
QTabBar::tab { background: %(bar)s; color: %(hint)s; padding: 5px 12px;
    border: 1px solid %(dim)s; }
QTabBar::tab:selected { color: %(on_accent)s; background: %(accent)s; }

QDockWidget { color: %(text)s; font-weight: bold; }
QDockWidget::title { background: %(title)s; padding: 4px 6px;
    border-bottom: 1px solid %(dim)s; }
QDockWidget::close-button, QDockWidget::float-button {
    border: 1px solid %(dim)s; background: %(bar)s; padding: 1px; }

QMenuBar { background: %(bar)s; border-bottom: 1px solid %(dim)s; }
QMenuBar::item:selected { background: %(accent)s; color: %(on_accent)s; }
QMenu { background: %(panel)s; border: 1px solid %(dim)s; color: %(text)s; }
QMenu::item:selected { background: %(accent)s; color: %(on_accent)s; }

QToolBar { background: %(bar)s; border: 0; border-bottom: 1px solid %(dim)s;
    spacing: 4px; padding: 3px; }
QStatusBar { background: %(bar)s; border-top: 1px solid %(dim)s; color: %(text)s; }
QStatusBar::item { border: 0; }

QScrollBar:vertical { background: %(panel)s; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: %(dim)s; min-height: 22px; }
QScrollBar::handle:vertical:hover { background: %(accent)s; }
QScrollBar:horizontal { background: %(panel)s; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: %(dim)s; min-width: 22px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }

QSplitter::handle { background: %(sep)s; }
QSplitter::handle:hover { background: %(accent)s; }
QFrame[role="card"] { border: 1px solid %(dim)s; border-radius: %(radius)spx;
    background: %(card)s; }

QToolTip { background-color: %(title)s; color: %(text)s;
    border: 1px solid %(accent)s; padding: 4px; }
QStatusBar QLabel { color: %(hint)s; }
QProgressBar { background: %(panel)s; border: 1px solid %(dim)s;
    text-align: center; color: %(text)s; }
QProgressBar::chunk { background-color: %(dim)s; }

QMessageBox, QDialog { background-color: %(bg)s; }

QLabel#bigHeader { color: %(accent)s; font-size: 15pt; letter-spacing: 2px; }
QLabel#sectionHeader { color: %(accent)s; letter-spacing: 1px; }
QTextBrowser#entryBody { background-color: %(body)s; border: 0; padding: 4px; }
QTextEdit#replyBox { background-color: %(reply)s; border: 1px solid %(dim)s; }
QTextEdit#logView { background-color: %(bg)s; border: 1px solid %(dim)s; }
""" % p


STYLE_SHEET = build_style_sheet(PALETTE)


def _make_icon(kind):
    """Theme-aware 16px vector glyph drawn in the active palette colour.
    OS 'standard icons' embed their own colours (a baked-green checkmark etc.)
    and clash with non-default themes, so every toolbar glyph is our own."""
    px = QPixmap(16, 16)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    col = QColor(PALETTE["accent"])
    pen = QPen(col)
    pen.setWidthF(1.6)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if kind == "fetch":                       # down arrow into tray
        p.drawLine(8, 2, 8, 10)
        p.drawPolyline(QPolygonF([QPointF(4.5, 6.5), QPointF(8, 10.5), QPointF(11.5, 6.5)]))
        p.drawLine(3, 13, 13, 13)
    elif kind == "analyze":                   # play triangle
        tri = QPolygonF([QPointF(4, 2.5), QPointF(4, 13.5), QPointF(13.5, 8)])
        p.setBrush(col)
        p.drawPolygon(tri)
    elif kind == "auto":                      # radar / eye-ish circle + dot
        p.drawEllipse(QPointF(8, 8), 5.6, 5.6)
        p.setBrush(col)
        p.drawEllipse(QPointF(8, 8), 2.0, 2.0)
    elif kind == "settings":                  # gear (simple 8-tooth)
        import math
        outer = QPolygonF()
        for i in range(16):
            ang = math.pi * 2 * i / 16
            r = 7.0 if i % 2 == 0 else 5.0
            outer.append(QPointF(8 + r * math.cos(ang), 8 + r * math.sin(ang)))
        p.setBrush(col)
        p.drawPolygon(outer)
        p.setBrush(QColor(PALETTE["btn"]))
        p.drawEllipse(QPointF(8, 8), 2.6, 2.6)
    elif kind == "rain":                      # three falling strokes
        for x0 in (4.0, 8.0, 12.0):
            p.drawLine(QPointF(x0, 3), QPointF(x0 - 2, 13))
    elif kind == "folder":                    # open folder
        p.drawPolygon(QPolygonF([QPointF(2, 4.5), QPointF(6.5, 4.5), QPointF(8, 6.5),
                                 QPointF(14, 6.5), QPointF(14, 13), QPointF(2, 13)]))
    p.end()
    return QIcon(px)


def hint_label(text):
    lbl = QLabel(text)
    lbl.setProperty("role", "hint")
    lbl.setWordWrap(True)
    return lbl


def section_header(text):
    """Big glowing section header used by the wizard and welcome screen."""
    lbl = QLabel(text)
    lbl.setObjectName("bigHeader")
    return lbl


def header_label(text):
    lbl = QLabel(text)
    lbl.setObjectName("sectionHeader")
    return lbl


def make_card(title, margins=(8, 6, 8, 8)):
    """A thin framed box with a title -- visual grouping without extra chrome."""
    frame = QFrame()
    frame.setProperty("role", "card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(*margins)
    lay.setSpacing(5)
    if title:
        lay.addWidget(header_label(title))
    return frame, lay


def fmt_mmss(seconds):
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return "--:--"
    return "%02d:%02d" % (seconds // 60, seconds % 60)


PROMPT_MARKER_RE = re.compile(r"^#>\s*([A-Za-z_]+)\s*$", re.MULTILINE)


def parse_prompt_sections(text):
    """Split one prompt file into named sections marked by '#> NAME' lines.

    Sections used by Sentinel: ALERT (screening instructions for every fetch),
    SUMMARY (periodic trend-report instructions), MEMORY (long-term-memory
    policy + the {LONG_TERM_MEMORY} token; only appended to SUMMARY when the
    memory profiler is on). A file with no markers serves both modes whole.
    """
    text = text or ""
    matches = list(PROMPT_MARKER_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return {"ALERT": stripped, "SUMMARY": stripped}
    sections = {}
    for i, m in enumerate(matches):
        name = m.group(1).upper()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[m.end():end].strip()
    return sections


def render_prompt(template, mapping):
    """Token replacement -- immune to stray braces inside prompt files."""
    out = template or ""
    for key, value in mapping.items():
        out = out.replace("{" + key + "}", value or "")
    return out


# --------------------------------------------------------------------------- #
# Cosmetic widgets (same behaviour as before)
# --------------------------------------------------------------------------- #

class GreenRainOverlay(QWidget):
    """Matrix-rain screensaver overlay. The animation timer only runs while
    the overlay is visible, so an idle app costs nothing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.font = QFont(BASE_FONT_FAMILY, 14)
        self.char_width = QFontMetrics(self.font).horizontalAdvance("M")
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.update_rain)
        self.columns = []
        self.init_columns()

    def showEvent(self, event):
        self.timer.start()
        return super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        return super().hideEvent(event)

    def init_columns(self):
        parent = self.parent() or self
        width = max(1, parent.width())
        self.char_width = QFontMetrics(self.font).horizontalAdvance("M") or 12
        cols = max(2, width // self.char_width)
        self.columns = []
        for i in range(cols):
            x = i * self.char_width
            y = random.randint(-800, 0)
            speed = random.randint(4, 14)
            length = random.randint(6, 20)
            chars = [self.random_char() for _ in range(length)]
            self.columns.append({"x": x, "y": y, "speed": speed,
                                 "length": length, "chars": chars})

    def random_char(self):
        if random.random() < 0.25:
            return chr(random.randint(0x30A0, 0x30FF))
        return chr(random.randint(33, 126))

    def resizeEvent(self, event):
        self.init_columns()
        return super().resizeEvent(event)

    def update_rain(self):
        if not self.isVisible():
            return
        h = max(1, self.height())
        line_h = QFontMetrics(self.font).height()
        for col in self.columns:
            col["y"] += col["speed"]
            if random.random() < 0.2:
                col["chars"] = [self.random_char() if random.random() < 0.15 else c
                                for c in col["chars"]]
            if col["y"] - col["length"] * line_h > h:
                col["y"] = random.randint(-600, 0)
                col["speed"] = random.randint(4, 14)
                col["length"] = random.randint(6, 20)
                col["chars"] = [self.random_char() for _ in range(col["length"])]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font)
        line_h = QFontMetrics(self.font).height()
        for col in self.columns:
            x, y, length, chars = col["x"], col["y"], col["length"], col["chars"]
            head = QColor(PALETTE["accent"]); head.setAlpha(230)
            painter.setPen(head)
            head_char = chars[-1] if chars else self.random_char()
            painter.drawText(x, y, head_char)
            for i in range(length - 1):
                idx = (len(chars) - 2) - i
                if idx < 0:
                    break
                alpha = max(20, int(220 * (1 - (i + 1) / (length + 1))))
                trail = QColor(PALETTE["accent"]); trail.setAlpha(alpha)
                painter.setPen(trail)
                painter.drawText(x, y - (i + 1) * line_h, chars[idx])


class ScalableLabel(QLabel):
    def __init__(self, pixmap):
        super().__init__()
        self._scale = 1.0
        self._original_pix = pixmap
        self.setAlignment(Qt.AlignCenter)
        self.updatePixmap()

    def updatePixmap(self):
        w = max(1, self.width())
        h = max(1, self.height())
        canvas = QPixmap(w, h)
        canvas.fill(Qt.transparent)
        size = self._original_pix.size()
        new_w = max(1, int(size.width() * self._scale))
        new_h = max(1, int(size.height() * self._scale))
        scaled_pix = self._original_pix.scaled(new_w, new_h, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation)
        # If the scaled image is bigger than the label, centre-draw anyway;
        # painting clips to the canvas instead of crashing on an empty pixmap.
        painter = QPainter(canvas)
        painter.drawPixmap((w - scaled_pix.width()) // 2,
                           (h - scaled_pix.height()) // 2, scaled_pix)
        painter.end()
        self.setPixmap(canvas)

    def resizeEvent(self, event):
        self.updatePixmap()
        super().resizeEvent(event)

    def getScale(self):
        return self._scale

    def setScale(self, s):
        self._scale = s
        self.updatePixmap()

    scale = Property(float, getScale, setScale)


class FeedEntry(QFrame):
    """One collapsible item in the Report Feed dock."""

    def __init__(self, title, body_text, parent=None):
        super().__init__(parent)
        self._body_text = body_text or ""
        self.setProperty("role", "card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header_row = QWidget()
        hlay = QHBoxLayout(header_row)
        hlay.setContentsMargins(4, 2, 4, 2)
        hlay.setSpacing(6)

        self.toggle_btn = QToolButton()
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setAutoRaise(True)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setToolTip("Expand / collapse this report")
        self.toggle_btn.setText("\u25b8")
        hlay.addWidget(self.toggle_btn)

        self.cap = QLabel(title)
        self.cap.setObjectName("sectionHeader")
        hlay.addWidget(self.cap, 1)
        lay.addWidget(header_row)

        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        self.body.setReadOnly(True)
        # Escape before embedding in HTML: report text and titles can contain
        # <, > or & characters which used to corrupt the rendered entry.
        safe_title = html.escape(title)
        safe_body = html.escape(self._body_text).replace("\n", "<br>")
        self.body.setHtml("<b>%s</b><hr>%s" % (safe_title, safe_body))
        self.body.setObjectName("entryBody")
        self.body.setMinimumHeight(120)
        self.body.setVisible(False)
        lay.addWidget(self.body)

        self.toggle_btn.clicked.connect(self._on_toggle)

    def _on_toggle(self):
        open_it = self.toggle_btn.isChecked()
        self.toggle_btn.setText("\u25be" if open_it else "\u25b8")
        self.body.setVisible(open_it)

    def set_expanded(self, open_it):
        self.toggle_btn.setChecked(bool(open_it))
        self._on_toggle()

    def matches(self, needle):
        if not needle:
            self.setVisible(True)
            return
        self.setVisible(needle in self._body_text.lower() or needle in self.cap.text().lower())


# --------------------------------------------------------------------------- #
# Workers  (all long HTTP / LLM work happens here, never on the GUI thread)
# --------------------------------------------------------------------------- #

class _TaskThread(threading.Thread):
    """Runs fn() in a daemon thread. Completion is reported by emitting a Qt
    signal, which lands on the GUI thread's event loop — worker callbacks can
    therefore never touch widgets from the wrong thread. Being daemon threads,
    closing the window never waits on (or kills mid-call) a long LLM request,
    and no 'QThread destroyed while running' errors are possible."""

    def __init__(self, fn, finished_signal=None):
        super().__init__(daemon=True)
        self._fn = fn
        self._finished_signal = finished_signal

    def run(self):
        try:
            self._fn()
        except Exception as exc:                      # noqa: BLE001
            print("Task error: %s" % exc)
        if self._finished_signal is not None:
            try:
                self._finished_signal.emit()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Settings dialog  (tabbed, resizable, replaces the old single-wall form)
# --------------------------------------------------------------------------- #

class SettingsDialog(QDialog):
    """Tabbed settings. Fields write nothing until 'Save & Apply'; the
    provider combo swaps example URLs so newcomers see what goes where."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle("System Settings")
        self.setModal(True)
        self.setMinimumSize(620, 460)
        self.resize(760, 600)

        self.fields = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        self._tabs = tabs

        # ---- Connection ----------------------------------------------------
        conn = QWidget()
        form = QFormLayout(conn)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        prov = QComboBox()
        for _k in PROVIDER_ORDER:
            prov.addItem(PROVIDERS[_k]["label"], _k)
        cur_prov = str(self.app.settings.get("LLM_PROVIDER", "openai_compat"))
        cur_prov = LEGACY_PROVIDER_MAP.get(cur_prov, cur_prov)
        idx = prov.findData(cur_prov)
        if idx >= 0:
            prov.setCurrentIndex(idx)
        prov.setToolTip("Where your language model runs. Local servers (LM Studio, "
                        "llama.cpp, Ollama, vLLM) all use the OpenAI-compatible "
                        "entry; hosted providers need an API key.")
        self.fields["LLM_PROVIDER"] = prov
        form.addRow("Provider:", prov)

        self.url_edit = self._line("LLM_BASE_URL")
        self.model_edit = self._line("LLM_MODEL")
        form.addRow("Server URL:", self.url_edit)
        form.addRow("Model name:", self.model_edit)

        key_row = QHBoxLayout()
        self.key_edit = self._line("LLM_API_KEY")
        self.key_edit.setEchoMode(QLineEdit.Password)
        show_key = QToolButton()
        show_key.setText("show")
        show_key.setCheckable(True)
        show_key.setToolTip("Show / hide the key while typing")
        show_key.toggled.connect(lambda on: self.key_edit.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(show_key)
        key_wrap = QWidget()
        key_wrap.setLayout(key_row)
        form.addRow("API key:", key_wrap)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setToolTip("Sends a tiny request to the LLM server to confirm "
                                 "URL, model and key are correct.")
        self.test_btn.clicked.connect(self._test_connection)
        self.test_result = hint_label("")
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_result, 1)
        test_wrap = QWidget()
        test_wrap.setLayout(test_row)
        form.addRow("", test_wrap)

        conn_hint = hint_label("")
        form.addRow("", conn_hint)
        self._conn_hint = conn_hint
        prov.currentIndexChanged.connect(lambda: self._provider_changed(prov.currentData()))
        self._provider_changed(prov.currentData())
        self._add_tab(tabs, conn, " Connection ")

        # ---- Appearance ----------------------------------------------------
        app_tab = QWidget()
        aform = QFormLayout(app_tab)
        aform.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        theme_combo = QComboBox()
        for _k in THEMES:
            theme_combo.addItem(THEMES[_k]["label"], _k)
        cur_theme = str(self.app.settings.get("UI_THEME", DEFAULT_THEME))
        idx = theme_combo.findData(cur_theme)
        theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        theme_combo.setToolTip("Colour theme. Changes apply instantly so you can "
                               "preview; Cancel restores the old one.")
        self.fields["UI_THEME"] = theme_combo
        aform.addRow("Theme:", theme_combo)
        # live preview: apply instantly (no persist); Cancel restores on reject
        self._theme_at_open = cur_theme if idx >= 0 else DEFAULT_THEME
        theme_combo.currentIndexChanged.connect(
            lambda: self.app.apply_theme(theme_combo.currentData(), persist=False))
        aform.addRow(hint_label("Ctrl+= / Ctrl+- zoom every widget; Ctrl+0 resets. "
                                "\u201cProfessional\u201d switches to the system UI font."))
        self._add_tab(tabs, app_tab, " Appearance ")

        # ---- Limits --------------------------------------------------------
        limits = QWidget()
        lform = QFormLayout(limits)
        lform.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._add_row(lform, "Max input tokens:", "MAX_INPUT_TOKENS", 1000, 2000000,
                      "How much RSS text goes to the LLM per pull. Keep it below your "
                      "model's context window (1 token is roughly 4 characters).")
        self._add_row(lform, "Max reply length:", "MAX_OUTPUT_TOKENS", 16, 500000,
                      "Cap on the LLM's alert reply. This is a ceiling, not a target.")
        self._add_row(lform, "Bulk report input:", "MAX_INPUT_TOKENS_BULK", 1000, 2000000,
                      "Token cap for accumulated feeds sent during bulk trend analysis.")
        self._add_row(lform, "Bulk report length:", "MAX_OUTPUT_TOKENS_BULK", 16, 500000,
                      "Cap on the bulk trend report itself.")
        self._add_row(lform, "Chunk size (tokens):", "CHUNK_TOKENS", 200, 500000,
                      "When chunking is on, alert input is split into pieces this many "
                      "TOKENS long.")
        self._add_row(lform, "Items per feed:", "ITEMS_PER_FEED", 1, 500,
                      "Maximum new entries taken from each feed per cycle. "
                      "Already-seen items are skipped automatically.")
        self._add_check(lform, "USE_CHUNKED_MODE", "Split large pulls into chunks",
                        "Prevents overflowing the model's context, but the same event "
                        "may be reported in two chunks.")
        self._add_row(lform, "LLM request timeout (s):", "LLM_TIMEOUT", 30, 7200,
                      "How long to wait for the LLM to answer one request before "
                      "giving up. Big models on small GPUs may need 1800+.")
        lform.addRow(hint_label("Rule of thumb: 1 token \u2248 4 characters. If the LLM "
                                "complains about context length, lower these."))
        self._add_tab(tabs, limits, " Limits ")

        # ---- Automation ----------------------------------------------------
        auto = QWidget()
        aform = QFormLayout(auto)
        aform.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._add_row(aform, "Auto-fetch every (seconds):", "FETCH_INTERVAL", 30, 86400,
                      "Time between automatic RSS pulls + analysis. Must be longer than "
                      "your LLM takes to answer, or a backlog builds up. "
                      "600 = 10 min, 1800 = 30 min.")
        aform.addRow(hint_label("MODE 1 \u2014 INSTANT ALERTS"))
        self._add_check(aform, "ALERTS_ENABLED", "Alerts  \u2014 check each fetch for urgent matches",
                        "Every pull is sent to the LLM; anything that crosses the alert "
                        "threshold appears in the report feed and Slack right away.")
        aform.addRow(hint_label("MODE 2 \u2014 PERIODIC SUMMARY REPORTS"))
        self._add_check(aform, "SUMMARIES_ENABLED", "Summaries  \u2014 one trend report per window",
                        "New items keep accumulating either way; while this is on, every "
                        "'Summary window' the LLM turns them into one trend report. The "
                        "status bar counts down to the next one.")
        self._add_row(aform, "Summary window (seconds):", "ANALYSIS_WINDOW", 60, 86400,
                      "How far back each summary report reaches. "
                      "3600 = 1 h, 21600 = 6 h.")
        aform.addRow(hint_label("'Analyze Now' (Ctrl+B) always runs a summary on demand, "
                                "even with Summaries switched off. New here? Leave the "
                                "defaults and just set your topics."))
        self._add_tab(tabs, auto, " Automation ")

        # ---- Memory & output ----------------------------------------------
        mem = QWidget()
        mform = QFormLayout(mem)
        mform.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.dir_edit = QLineEdit(str(self.app.settings.get("REPORT_OUTPUT_DIR", "")))
        self.dir_edit.setToolTip("Folder where alert / summary .txt reports are saved.")
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.dir_edit, 1)
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse_dir)
        open_btn = QPushButton("Open")
        open_btn.setToolTip("Show the report folder in your file manager")
        open_btn.clicked.connect(self._open_dir)
        dir_row.addWidget(browse)
        dir_row.addWidget(open_btn)
        dir_wrap = QWidget()
        dir_wrap.setLayout(dir_row)
        mform.addRow("Report folder:", dir_wrap)

        self._add_check(mform, "MEMORY_PROFILER", "Sequential long-term memory",
                        "Sentinel folds past summaries into a running memory file the LLM "
                        "consults on later reports. Experimental \u2014 off by default.")
        self._add_row(mform, "Long-term memory cap (tokens):", "MEMORY_LONG_TERM_MAX_TOKENS",
                      500, 400000, "How much consolidated memory is replayed to the LLM.")
        self._add_row(mform, "Memory fold chunk (tokens):", "MEMORY_SHORT_TERM_CHUNK_TOKENS",
                      500, 400000, "Short-term entries are folded into long-term memory in "
                      "pieces this large.")
        self._add_row(mform, "Memory rewrite reply cap:", "MEMORY_PROFILER_OUTPUT_TOKENS",
                      256, 100000, "Max length of each memory-rewrite reply from the LLM.")
        mform.addRow(hint_label("Memory files live beside the app: %s / %s"
                                % (os.path.basename(MEMORY_LONG_TERM_FILE),
                                   os.path.basename(MEMORY_SHORT_TERM_FILE))))
        self._add_tab(tabs, mem, " Memory && Output ")

        # ---- Notifications -------------------------------------------------
        notif = QWidget()
        nform = QFormLayout(notif)
        nform.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        nform.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        def nline(key, placeholder, tip):
            edit = self._line(key)
            edit.setPlaceholderText(placeholder)
            edit.setToolTip(tip)
            return edit

        nline("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/...  (optional)",
              "Slack incoming webhook. Setup: create an app at api.slack.com, add the "
              "\u201cIncoming Webhooks\u201d feature, copy the URL. Leave blank to skip.")
        nform.addRow("Slack webhook:", self.fields["SLACK_WEBHOOK_URL"])

        nline("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/...",
              "Discord channel webhook: Server Settings \u2192 Integrations \u2192 "
              "Webhooks \u2192 New Webhook \u2192 copy URL. Leave blank to skip.")
        nform.addRow("Discord webhook:", self.fields["DISCORD_WEBHOOK_URL"])

        tk = nline("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF...",
                   "Telegram bot token from @BotFather (/newbot). Pair with the chat ID "
                   "below. Leave blank to skip.")
        tk.setEchoMode(QLineEdit.Password)
        nform.addRow("Telegram bot token:", tk)
        nline("TELEGRAM_CHAT_ID", "e.g. 123456789 or @yourchannel",
              "Chat ID your bot sends to. Fastest way: message your bot, then open "
              "https://api.telegram.org/bot<TOKEN>/getUpdates and read chat.id.")
        nform.addRow("Telegram chat ID:", self.fields["TELEGRAM_CHAT_ID"])

        nline("NTFY_TOPIC_URL", "https://ntfy.sh/your-secret-topic",
              "ntfy keeps phones notified with no account: install the ntfy app and "
              "subscribe to a hard-to-guess topic name, then paste its URL here. "
              "Self-hosted servers work too.")
        nform.addRow("ntfy topic URL:", self.fields["NTFY_TOPIC_URL"])

        nline("GENERIC_WEBHOOK_URL", "https://anything.example/hook",
              "POSTs JSON {title, text, type, source, timestamp} to any endpoint \u2014 "
              "useful for Home Assistant, custom scripts, or agent integrations.")
        nform.addRow("Generic webhook:", self.fields["GENERIC_WEBHOOK_URL"])

        self._add_check(nform, "NOTIFY_DESKTOP",
                        "Desktop popup notifications (via system tray)",
                        "Shows a native pop-up when the system tray is available. "
                        "Alerts always notify here while this is on.")
        self._add_check(nform, "NOTIFY_SUMMARIES",
                        "Send periodic summary reports to the channels above",
                        "Off = only instant alerts are pushed; summaries stay in the "
                        "app and report folder. On = both go out.")

        test_row2 = QHBoxLayout()
        self.test_notify_btn = QPushButton("Send Test Notification")
        self.test_notify_btn.setToolTip(
            "Sends a short test message to every channel configured above \u2014 using "
            "the values currently in this dialog, before you even save.")
        self.test_notify_btn.clicked.connect(self._test_notifications)
        self.test_notify_result = hint_label("No channels configured yet \u2014 alerts "
                                             "stay in the app.")
        test_row2.addWidget(self.test_notify_btn)
        test_row2.addWidget(self.test_notify_result, 1)
        twrap2 = QWidget()
        twrap2.setLayout(test_row2)
        nform.addRow("", twrap2)

        self._fields_notifier_keys = ["SLACK_WEBHOOK_URL", "DISCORD_WEBHOOK_URL",
                                      "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                                      "NTFY_TOPIC_URL", "GENERIC_WEBHOOK_URL"]
        for k in self._fields_notifier_keys:
            w = self.fields[k]
            if isinstance(w, QLineEdit):
                w.textChanged.connect(self._refresh_notify_summary)
        self._add_tab(tabs, notif, " Notifications ")
        self._refresh_notify_summary()

        # ---- Buttons -------------------------------------------------------
        bar = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bar.button(QDialogButtonBox.Save).setText("Save && Apply")
        bar.button(QDialogButtonBox.Cancel).setText("Cancel")
        restore = QPushButton("Restore Defaults")
        restore.clicked.connect(self._restore_defaults)
        bar.layout().insertWidget(0, restore)
        bar.accepted.connect(self._commit)
        bar.rejected.connect(self.reject)
        root.addWidget(bar)

    # -- widget factories ----------------------------------------------------
    def _add_tab(self, tabs, inner, title):
        """Put a form page into its own scroll area (prevents clipping when
        the dialog is smaller than the form) and register the tab."""
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tabs.addTab(scroll, title)

    def _line(self, key):
        edit = QLineEdit(str(self.app.settings.get(key, "")))
        self.fields[key] = edit
        return edit

    def _spin(self, key, lo, hi):
        spin = QSpinBox()
        spin.setRange(lo, min(hi, 2000000000))
        try:
            spin.setValue(int(self.app.settings.get(key, lo)))
        except (TypeError, ValueError):
            spin.setValue(lo)
        self.fields[key] = spin
        return spin

    def _check(self, key, text):
        box = QCheckBox(text)
        try:
            box.setChecked(int(self.app.settings.get(key, 0)) == 1)
        except (TypeError, ValueError):
            box.setChecked(False)
        self.fields[key] = box
        return box

    def _add_row(self, form, label, key, lo, hi, tooltip):
        spin = self._spin(key, lo, hi)
        spin.setToolTip(tooltip)
        lbl = QLabel(label)
        lbl.setToolTip(tooltip)
        form.addRow(lbl, spin)
        return spin

    def _add_check(self, form, key, text, tooltip):
        box = self._check(key, text)
        box.setToolTip(tooltip)
        form.addRow(box)
        return box

    # -- provider guidance ----------------------------------------------------
    def _provider_changed(self, provider):
        """Refresh URL/model placeholders + guidance from the provider registry.
        `provider` here is the combo's DATA value (a PROVIDERS key)."""
        p = PROVIDERS.get(provider) or PROVIDERS["openai_compat"]
        self.url_edit.setPlaceholderText(p["base_url"])
        self.model_edit.setPlaceholderText(p["model"] or "blank uses the loaded model")
        hint = p["hint"]
        if not p["key_required"]:
            hint += " Leave the API key blank for local servers."
        self._conn_hint.setText(hint)

    def _test_connection(self):
        """Fire a 1-token request at the *dialog's current* values."""
        self.test_btn.setEnabled(False)
        self.test_result.setText("testing\u2026")
        provider = self.fields["LLM_PROVIDER"].currentData()
        base_url = self.url_edit.text().strip().rstrip("/")
        model = self.model_edit.text().strip()
        api_key = self.key_edit.text().strip()
        url = chat_completions_url(provider, base_url)

        def work():
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = "Bearer %s" % api_key
            payload = {"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
                if resp.status_code == 200:
                    return True, "Connected \u2014 server responded OK."
                return False, "HTTP %s: %s" % (resp.status_code, resp.text[:200])
            except Exception as exc:
                return False, "%s" % exc

        def done(result):
            self.test_btn.setEnabled(True)
            ok, msg = result
            self.test_result.setText(("\u2714 " if ok else "\u2718 ") + msg)

        self.app.run_background(work, done)

    # -- notification test ----------------------------------------------------
    def _snapshot_notifier_settings(self):
        snap = dict(self.app.settings)
        for key in getattr(self, "_fields_notifier_keys", []):
            w = self.fields.get(key)
            if isinstance(w, QLineEdit):
                snap[key] = w.text().strip()
        for key in ("NOTIFY_DESKTOP", "NOTIFY_SUMMARIES"):
            w = self.fields.get(key)
            if isinstance(w, QCheckBox):
                snap[key] = "1" if w.isChecked() else "0"
        return snap

    def _refresh_notify_summary(self, *_):
        chans = configured_channels(self._snapshot_notifier_settings())
        if hasattr(self, "test_notify_result"):
            self.test_notify_result.setText(
                ("Channels: " + ", ".join(chans)) if chans
                else "No channels configured yet \u2014 alerts stay in the app.")

    def _test_notifications(self):
        snap = self._snapshot_notifier_settings()
        chans = [c for c in configured_channels(snap) if c != "Desktop"]
        if not chans:
            self.test_notify_result.setText("\u2718 Nothing to test: fill at least one "
                                            "channel above.")
            return
        self.test_notify_btn.setEnabled(False)
        self.test_notify_result.setText("sending test message to %s\u2026"
                                        % ", ".join(chans))

        def work():
            return dispatch_notifications(snap,
                                          "Test notification from ShunyaNet Sentinel.",
                                          "TEST")

        def done(results):
            self.test_notify_btn.setEnabled(True)
            lines = []
            for channel, ok, msg in (results or []):
                lines.append("%s %s%s" % ("\u2714" if ok else "\u2718", channel,
                                          "" if ok else ": " + msg))
            self.test_notify_result.setText("  ".join(lines))

        self.app.run_background(work, done)

    # -- actions -------------------------------------------------------------
    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Report Output Folder",
                                             self.dir_edit.text() or DEFAULT_REPORT_OUTPUT_DIR)
        if d:
            self.dir_edit.setText(d)

    def _open_dir(self):
        d = self.dir_edit.text().strip() or DEFAULT_REPORT_OUTPUT_DIR
        try:
            os.makedirs(d, exist_ok=True)
            open_path(d)
        except Exception as exc:
            QMessageBox.warning(self, "Open folder", str(exc))

    def _restore_defaults(self):
        for key, widget in self.fields.items():
            default = DEFAULT_SETTINGS.get(key)
            if isinstance(widget, QSpinBox):
                widget.setValue(int(default))
            elif isinstance(widget, QCheckBox):
                widget.setChecked(int(default) == 1)
            elif isinstance(widget, QComboBox):
                widget.setCurrentText(str(default))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(default))
        self.dir_edit.setText(DEFAULT_SETTINGS["REPORT_OUTPUT_DIR"])

    def reject(self):
        # undo any live theme preview before closing
        try:
            if getattr(self, "_theme_at_open", None):
                self.app.apply_theme(self._theme_at_open, persist=False)
        except Exception:
            pass
        super().reject()

    def _commit(self):
        for key, widget in self.fields.items():
            if isinstance(widget, QSpinBox):
                self.app.settings[key] = int(widget.value())
            elif isinstance(widget, QCheckBox):
                self.app.settings[key] = "1" if widget.isChecked() else "0"
            elif isinstance(widget, QComboBox):
                data = widget.currentData()
                self.app.settings[key] = (data if data is not None
                                          else widget.currentText())
            elif isinstance(widget, QLineEdit):
                self.app.settings[key] = widget.text()
        self.app.settings["REPORT_OUTPUT_DIR"] = (self.dir_edit.text().strip()
                                                  or DEFAULT_REPORT_OUTPUT_DIR)
        self.app.apply_settings()
        self.accept()


def open_path(path):
    """Open a folder in the OS file manager without going through a shell."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            launcher = shutil.which("xdg-open") or shutil.which("gio")
            if not launcher:
                return False
            if os.path.basename(launcher) == "gio":
                subprocess.Popen([launcher, "open", path])
            else:
                subprocess.Popen([launcher, path],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Notifiers  (pure functions of a settings dict -> per-channel results)
#
# Channels are configured by filling their URL/token fields in Settings; every
# one that is filled gets the message. All senders truncate to their platform
# limit, so long reports never bounce off an API.
# --------------------------------------------------------------------------- #

def _split_message(text, limit):
    """Split text into <=limit chunks on paragraph boundaries where possible."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text.strip() else []
    parts, current = [], ""
    for para in text.split("\n\n"):
        while len(para) > limit:                 # one huge paragraph
            parts.append(para[:limit])
            para = para[limit:]
        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) > limit and current:
            parts.append(current)
            current = para
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return parts


def _post(url, payload=None, headers=None, data=None, timeout=10):
    """One POST; returns (ok, message)."""
    try:
        resp = requests.post(url, json=payload, headers=headers, data=data,
                             timeout=timeout)
        if resp.status_code in (200, 204):
            return True, "ok"
        return False, "HTTP %s: %s" % (resp.status_code, resp.text[:160])
    except Exception as exc:
        return False, "%s" % exc


def _first_line(text, limit=90):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return "Sentinel report"


def send_slack(url, text):
    ok_all = True
    msgs = []
    for part in _split_message(text, 3500) or ["(empty report)"]:
        ok, msg = _post(url, {"text": part})
        if not ok:
            msgs.append(msg)
        ok_all = ok_all and ok
    return ok_all, ("; ".join(msgs) if msgs else "ok")


def send_discord(url, text):
    ok_all = True
    msgs = []
    for part in _split_message(text, 1900) or ["(empty report)"]:
        ok, msg = _post(url, {"content": part})
        if not ok:
            msgs.append(msg)
        ok_all = ok_all and ok
    return ok_all, ("; ".join(msgs) if msgs else "ok")


def send_telegram(bot_token, chat_id, text):
    url = "https://api.telegram.org/bot%s/sendMessage" % bot_token.strip("/")
    sent_any = True
    msgs = []
    for part in _split_message(text, 4000) or ["(empty report)"]:
        ok, msg = _post(url, {"chat_id": chat_id, "text": part})
        if not ok:
            msgs.append(msg)
        sent_any = sent_any and ok
    return sent_any, ("; ".join(msgs) if msgs else "ok")


def send_ntfy(topic_url, text, title=None, priority="default", tags=None):
    headers = {}
    if title:
        headers["Title"] = title[:120]
    if priority and priority != "default":
        headers["Priority"] = priority
    if tags:
        headers["Tags"] = tags
    return _post(topic_url, data=text.encode("utf-8"),
                 headers=headers or None)


def send_generic_webhook(url, title, text, report_type):
    payload = {
        "title": title,
        "text": text,
        "type": (report_type or "report").lower(),
        "source": "ShunyaNet Sentinel",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    return _post(url, payload)


def configured_channels(settings):
    """Human-readable list of the notifier channels that are switched on."""
    chans = []
    if (settings.get("SLACK_WEBHOOK_URL") or "").strip():
        chans.append("Slack")
    if (settings.get("DISCORD_WEBHOOK_URL") or "").strip():
        chans.append("Discord")
    if ((settings.get("TELEGRAM_BOT_TOKEN") or "").strip()
            and (settings.get("TELEGRAM_CHAT_ID") or "").strip()):
        chans.append("Telegram")
    if (settings.get("NTFY_TOPIC_URL") or "").strip():
        chans.append("ntfy")
    if (settings.get("GENERIC_WEBHOOK_URL") or "").strip():
        chans.append("Webhook")
    try:
        if int(settings.get("NOTIFY_DESKTOP", 1) or 0) == 1:
            chans.append("Desktop")
    except (TypeError, ValueError):
        pass
    return chans


def dispatch_notifications(settings, text, report_type="ALERT"):
    """Send `text` to every configured channel. Returns [(channel, ok, msg)].

    Desktop popups are handled by the GUI (signal) and skipped here; this
    covers the network channels only. Safe to call from any thread.
    """
    results = []
    title = "SENTINEL %s" % (report_type or "REPORT").upper()
    body = "%s\n\n%s" % (title, text)

    url = (settings.get("SLACK_WEBHOOK_URL") or "").strip()
    if url and "YOUR SLACK WEBHOOK" not in url:
        ok, msg = send_slack(url, body)
        results.append(("Slack", ok, msg))

    url = (settings.get("DISCORD_WEBHOOK_URL") or "").strip()
    if url:
        ok, msg = send_discord(url, body)
        results.append(("Discord", ok, msg))

    token = (settings.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (settings.get("TELEGRAM_CHAT_ID") or "").strip()
    if token and chat:
        ok, msg = send_telegram(token, chat, body)
        results.append(("Telegram", ok, msg))

    url = (settings.get("NTFY_TOPIC_URL") or "").strip()
    if url:
        ok, msg = send_ntfy(url, text, title=title,
                            priority="high" if report_type == "ALERT" else "default",
                            tags="rotating_light" if report_type == "ALERT" else None)
        results.append(("ntfy", ok, msg))

    url = (settings.get("GENERIC_WEBHOOK_URL") or "").strip()
    if url:
        ok, msg = send_generic_webhook(url, title, text, report_type)
        results.append(("Webhook", ok, msg))

    return results


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
# First-run setup wizard  (4 short steps; everything stays editable later)
# --------------------------------------------------------------------------- #

class SetupWizard(QDialog):
    """A calm guided path from blank slate to first fetch."""

    STEPS = 4

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle("Welcome to ShunyaNet Sentinel")
        self.setModal(True)
        self.setMinimumSize(640, 500)
        self.resize(720, 560)
        self.step = 0
        self.launched = False       # True only when the final page was accepted
        # plain-dict value store: wizard pages are destroyed/rebuilt on
        # navigation, so nothing may be read from a previous page's widgets
        self._data = {
            "overall": app.overall_topic_edit.toPlainText().strip(),
            "subs": app.subtopics_edit.toPlainText().strip(),
            "provider": str(app.settings.get("LLM_PROVIDER", "lmstudio")),
            "url": str(app.settings.get("LLM_BASE_URL", "")),
            "model": str(app.settings.get("LLM_MODEL", "")),
            "key": str(app.settings.get("LLM_API_KEY", "")),
        }

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        self.title_lbl = section_header("")
        head.addWidget(self.title_lbl)
        head.addStretch(1)
        self.progress_lbl = hint_label("")
        head.addWidget(self.progress_lbl)
        root.addLayout(head)

        self.sub_lbl = hint_label("")
        root.addWidget(self.sub_lbl)

        page_host = QWidget()
        self.page_layout = QVBoxLayout(page_host)
        self.page_layout.setContentsMargins(0, 6, 0, 6)
        root.addWidget(page_host, 1)

        btns = QHBoxLayout()
        self.skip_btn = QPushButton("Skip setup")
        self.skip_btn.setToolTip("Close the wizard; everything can be configured later")
        self.skip_btn.clicked.connect(self.accept)
        self.back_btn = QPushButton("< Back")
        self.back_btn.clicked.connect(self._back)
        self.next_btn = QPushButton("Next >")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._next)
        btns.addWidget(self.skip_btn)
        btns.addStretch(1)
        btns.addWidget(self.back_btn)
        btns.addWidget(self.next_btn)
        root.addLayout(btns)

        self._render()

    # ---------------------------------------------------------------- pages
    def _clear_page(self):
        # recursive: pages contain nested layouts, not just widgets
        def drain(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.layout():
                    drain(item.layout())
                elif item.widget():
                    w = item.widget()
                    w.setParent(None)
                    w.deleteLater()
                # spacer items carry nothing to clean up
        drain(self.page_layout)

    def _render(self):
        self._clear_page()
        self.back_btn.setVisible(self.step > 0)
        self.next_btn.setText("Launch First Fetch" if self.step == self.STEPS - 1
                              else "Next >")
        self.progress_lbl.setText("step %d of %d" % (self.step + 1, self.STEPS))
        getattr(self, "_page_%d" % self.step)()

    def _page_0(self):
        self.title_lbl.setText("WHAT TO WATCH")
        self.sub_lbl.setText(
            "Sentinel pulls news feeds and asks your LLM whether anything matches "
            "these topics. Describe what matters to you.")
        form = QVBoxLayout()
        form.setSpacing(6)
        form.addWidget(QLabel("Overall topic"))
        self.w_overall = QTextEdit()
        self.w_overall.setFixedHeight(54)
        self.w_overall.setPlaceholderText("e.g. Severe weather in the US Gulf Coast")
        self.w_overall.setPlainText(self._data.get("overall", ""))
        form.addWidget(self.w_overall)
        form.addWidget(QLabel("Signals / sub-topics (one per line)"))
        self.w_subs = QTextEdit()
        self.w_subs.setPlaceholderText(
            "tornado watch issued\npower grid failure\nflash flood emergency warning")
        self.w_subs.setPlainText(self._data.get("subs", ""))
        form.addWidget(self.w_subs, 1)
        self.page_layout.addLayout(form, 1)

    def _page_1(self):
        self.title_lbl.setText("WHERE YOUR MODEL LIVES")
        self.sub_lbl.setText(
            "Sentinel talks to a language model you run yourself (or OpenAI). "
            "Pick the server you have open; fine-tune later in Settings.")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.w_prov = QComboBox()
        for _k in PROVIDER_ORDER:
            self.w_prov.addItem(PROVIDERS[_k]["label"], _k)
        cur = LEGACY_PROVIDER_MAP.get(self._data.get("provider", "openai_compat"),
                                      self._data.get("provider", "openai_compat"))
        idx = self.w_prov.findData(cur)
        if idx >= 0:
            self.w_prov.setCurrentIndex(idx)
        form.addRow("Provider:", self.w_prov)
        self.w_url = QLineEdit(self._data.get("url", ""))
        form.addRow("Server URL:", self.w_url)
        self.w_model = QLineEdit(self._data.get("model", ""))
        form.addRow("Model name:", self.w_model)
        self.w_key = QLineEdit(self._data.get("key", ""))
        self.w_key.setEchoMode(QLineEdit.Password)
        form.addRow("API key:", self.w_key)
        self.w_hint = hint_label("")
        form.addRow("", self.w_hint)
        self.w_prov.currentTextChanged.connect(self._prov_hint)
        self._prov_hint(self.w_prov.currentText())

        row = QHBoxLayout()
        self.w_test_btn = QPushButton("Test Connection")
        self.w_test_result = hint_label("")
        self.w_test_btn.clicked.connect(self._test)
        row.addWidget(self.w_test_btn)
        row.addWidget(self.w_test_result, 1)
        wrap = QWidget()
        wrap.setLayout(row)
        form.addRow("", wrap)
        self.page_layout.addLayout(form)
        self.page_layout.addStretch(1)

    def _prov_hint(self, *_a):
        provider = self.w_prov.currentData()
        p = PROVIDERS.get(provider) or PROVIDERS["openai_compat"]
        self.w_url.setPlaceholderText(p["base_url"])
        self.w_model.setPlaceholderText(p["model"] or "blank uses the loaded model")
        hint = p["hint"]
        if not p["key_required"]:
            hint += " Leave the API key blank for local servers."
        self.w_hint.setText(hint)

    def _test(self):
        provider = self.w_prov.currentData()
        base_url = self.w_url.text().strip().rstrip("/")
        model = self.w_model.text().strip()
        api_key = self.w_key.text().strip()
        url = chat_completions_url(provider, base_url)

        def work():
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = "Bearer %s" % api_key
            payload = {"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
                if resp.status_code == 200:
                    return True, "Connected \u2014 server responded OK."
                return False, "HTTP %s: %s" % (resp.status_code, resp.text[:160])
            except Exception as exc:
                return False, "%s" % exc

        def done(result):
            ok, msg = result
            self.w_test_result.setText(("\u2714 " if ok else "\u2718 ") + msg)

        self.app.run_background(work, done)

    def _page_2(self):
        self.title_lbl.setText("FEEDS TO PULL")
        self.sub_lbl.setText(
            "A short test list keeps the first pull quick. Load bigger or custom "
            "lists anytime from File \u2192 Data Sources.")
        lay = QVBoxLayout()
        lay.setSpacing(8)
        self.w_src_short = QPushButton("Quick-Start List  (short, good for a first run)")
        self.w_src_short.clicked.connect(lambda: self._pick_source("Default_test-v1.txt"))
        self.w_src_long = QPushButton("World News List  (longer, breaking-news focus)")
        self.w_src_long.clicked.connect(lambda: self._pick_source("Default_long-v1.txt"))
        self.w_src_own = QPushButton("Use My Own File\u2026")
        self.w_src_own.clicked.connect(self._pick_own)
        lay.addWidget(self.w_src_short)
        lay.addWidget(self.w_src_long)
        lay.addWidget(self.w_src_own)
        self.w_src_result = hint_label("")
        lay.addWidget(self.w_src_result)
        lay.addStretch(1)
        self.page_layout.addLayout(lay)

    def _pick_source(self, name):
        if self.app.load_bundled_sources(name, quiet=True):
            self.w_src_result.setText("Loaded %d sources from %s."
                                      % (len(self.app.feeds), name))

    def _pick_own(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Data Source File", "",
                                              "Text Files (*.txt);;JSON Files (*.json)")
        if path:
            self.app.load_sources_from_file(path)
            self.w_src_result.setText("Loaded %d sources from %s."
                                      % (len(self.app.feeds), os.path.basename(path)))

    def _page_3(self):
        self.title_lbl.setText("READY")
        self.sub_lbl.setText("Launch keeps these defaults; change anything later.")
        grid = QVBoxLayout()
        grid.setSpacing(6)
        d = self._data
        chans = configured_channels(self.app.settings)
        cadence = "auto-fetch every %ss" % self.app.settings.get("FETCH_INTERVAL", 600)
        if str(self.app.settings.get("SUMMARIES_ENABLED", "1")) == "1":
            cadence += (" \u00b7 summary reports every %ss"
                        % self.app.settings.get("ANALYSIS_WINDOW", 3600))
        else:
            cadence += " \u00b7 summary reports manual (Ctrl+B)"
        lines = [
            "Topics:   %s" % (d.get("overall", "") or "(none yet)"),
            "Signals:  %d line(s)" % len([l for l in
                                          d.get("subs", "").splitlines() if l.strip()]),
            "LLM:      %s  (%s)" % (d.get("provider", "?"),
                                    d.get("url", "") or "default URL"),
            "Feeds:    %d source(s) armed" % len(self.app.feeds),
            "Alerts:   %s" % (", ".join(chans) if chans else "GUI only"),
            "Cadence:  %s" % cadence,
        ]
        for line in lines:
            grid.addWidget(QLabel(line))
        tip = hint_label(
            "\nTip: the first fetch pulls a full batch of feed history \u2014 it is the "
            "slowest one. Watch the Terminal Log while it works.")
        grid.addWidget(tip)
        grid.addStretch(1)
        self.page_layout.addLayout(grid)

    # ---------------------------------------------------------------- nav
    def _capture_page(self):
        """Stash the current page's values before its widgets go away."""
        if self.step == 0:
            self._data["overall"] = self.w_overall.toPlainText().strip()
            self._data["subs"] = self.w_subs.toPlainText().strip()
        elif self.step == 1:
            self._data["provider"] = self.w_prov.currentData()
            self._data["url"] = self.w_url.text().strip()
            self._data["model"] = self.w_model.text().strip()
            self._data["key"] = self.w_key.text().strip()

    def _apply(self):
        prof = (self._data.get("provider")
                or self.app.settings.get("LLM_PROVIDER", "openai_compat"))
        prof = LEGACY_PROVIDER_MAP.get(prof, prof)
        self.app.settings["LLM_PROVIDER"] = prof
        default_url = PROVIDERS.get(prof, PROVIDERS["openai_compat"])["base_url"]
        typed_url = self._data.get("url", "")
        self.app.settings["LLM_BASE_URL"] = typed_url or default_url
        self.app.settings["LLM_MODEL"] = self._data.get("model", "")
        self.app.settings["LLM_API_KEY"] = self._data.get("key", "")
        overall = self._data.get("overall", "")
        subs = self._data.get("subs", "")
        if overall or subs:
            self.app.overall_topic_edit.setPlainText(overall)
            self.app.subtopics_edit.setPlainText(subs)

    def _next(self):
        self._capture_page()
        if self.step == self.STEPS - 1:
            self._apply()
            self.launched = True
            self.accept()
            return
        self.step += 1
        self._render()

    def _back(self):
        self._capture_page()
        if self.step > 0:
            self.step -= 1
            self._render()


# --------------------------------------------------------------------------- #

class SentinelWindow(QMainWindow):
    """Signals marshal worker output back onto the GUI thread."""

    sig_log = Signal(str)
    sig_report = Signal(str)
    sig_feed = Signal(str)
    sig_request_bulk = Signal()
    sig_bg_result = Signal(object, object)   # (callback_id, result)
    sig_notify_desktop = Signal(str, str)    # (title, body)
    sig_fetch_done = Signal()
    sig_analysis_done = Signal()
    sig_memory_done = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ShunyaNet Sentinel v%s" % APP_VERSION)
        self.setMinimumSize(760, 480)
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowNestedDocks
                            | QMainWindow.AllowTabbedDocks)
        self.setCorner(Qt.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        icon_path = resource_path(os.path.join("assets", "app_icon.png"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # first run == no app_state.json yet (used to trigger the wizard)
        self.is_first_run = not os.path.exists(STATE_FILE)

        # ---- state ---------------------------------------------------------
        self.seen_guids = set()
        self._seen_order = []
        self._state_has_feeds = False   # set when app_state.json supplies a list
        self.profiles = {}
        self.feeds = FEEDS.copy()
        self.settings = dict(DEFAULT_SETTINGS)
        self.active_threads = []          # running _TaskThread objects (diagnostics)
        self._bg_callbacks = {}           # id -> GUI-thread callback
        self._bg_counter = 0
        self._worker_lock = threading.Lock()
        self._shutting_down = False
        self._fetch_worker = None
        self._analysis_worker = None
        self._memory_worker = None

        self.rolling_file_start_time = time.time()
        self.memory_profiler_start_time = time.time()
        # ONE prompt file drives both modes; '#> ALERT' / '#> SUMMARY' /
        # '#> MEMORY' sections are extracted at load time. Legacy two-file
        # setups (default_prompt.txt + default_summary_prompt.txt) are still
        # honoured when the unified file is absent.
        self.prompt_sections = {}
        self.prompt_file = ""
        self._load_unified_prompt()
        self.ui_font_pt = DEFAULT_UI_FONT_PT
        self.next_fetch_ts = time.time() + int(self.settings["FETCH_INTERVAL"])
        self.next_bulk_ts = time.time() + int(self.settings["ANALYSIS_WINDOW"])

        # debounced autosave when the user edits topics (created before the UI
        # because the targeting dock wires into it while building)
        self.topic_save_timer = QTimer(self)
        self.topic_save_timer.setSingleShot(True)
        self.topic_save_timer.setInterval(1500)
        self.topic_save_timer.timeout.connect(self.save_app_state)

        self.sig_log.connect(self._append_log)
        self.sig_report.connect(self._set_report)
        self.sig_feed.connect(self.add_to_history)
        self.sig_bg_result.connect(self._on_bg_result)
        self.sig_notify_desktop.connect(self._show_desktop_notification)
        self.sig_fetch_done.connect(self._on_fetch_finished)
        self.sig_analysis_done.connect(self._on_analysis_finished)
        self.sig_memory_done.connect(self._on_memory_finished)
        # note: no force here -- a fetch may only trigger bulk analysis when
        # the toggle is on AND the analysis window has elapsed (checked in
        # perform_bulk_analysis_if_ready)
        self.sig_request_bulk.connect(lambda: self.start_bulk_analysis(force=False))

        # ---- UI ------------------------------------------------------------
        self._build_actions()
        self._build_central()
        self._build_targeting_dock()
        self._build_feed_dock()
        self._build_menus_and_toolbar()
        self._build_status_bar()

        self.green_overlay = GreenRainOverlay(self)
        self.green_overlay.resize(self.size())
        self.green_overlay.hide()

        # ---- data + timers -------------------------------------------------
        self.load_profiles()
        self.update_profile_combobox()
        self.load_app_state()
        # re-resolve now that a saved PROMPT_FILE choice may exist
        if str(self.settings.get("PROMPT_FILE", "") or "").strip():
            self._load_unified_prompt()
        self._load_default_data_sources_if_empty()

        self.auto_fetch_timer = QTimer(self)
        self.auto_fetch_timer.timeout.connect(self.start_fetch_thread)
        self.bulk_timer = QTimer(self)
        self.bulk_timer.timeout.connect(self.start_bulk_analysis)
        self.memory_timer = QTimer(self)
        self.memory_timer.setInterval(MEMORY_PROFILER_INTERVAL * 1000)
        self.memory_timer.timeout.connect(self.start_memory_profiler)
        self.status_tick = QTimer(self)
        self.status_tick.setInterval(1000)
        self.status_tick.timeout.connect(self._update_status_bar)

        if self.ui_font_pt != DEFAULT_UI_FONT_PT:
            font = QApplication.font()
            font.setFamily(theme_font_family())
            font.setPointSize(self.ui_font_pt)
            QApplication.setFont(font)

        saved_theme = str(self.settings.get("UI_THEME", DEFAULT_THEME))
        if saved_theme in THEMES and saved_theme != DEFAULT_THEME:
            self.apply_theme(saved_theme, persist=False)
        self.apply_settings()
        self.act_auto.setChecked(True)
        self.status_tick.start()

        self._update_source_label()
        self._refresh_prompt_labels()
        self._update_profile_label()
        self.restore_ui_state()
        self._ensure_tray()      # desktop popups + click-to-restore, if available
        if not self.prompt_sections.get("ALERT") and not self.prompt_sections.get("SUMMARY"):
            self._append_log("WARNING: no prompt file found (expected sentinel_prompt.txt "
                             "next to the app). Alerts would send an empty prompt \u2014 "
                             "load one via File \u2192 Load Prompt File.")
        self._append_log("ShunyaNet Sentinel ready. %d sources armed." % len(self.feeds))
        if self.is_first_run:
            # let the window paint first, then guide the newcomer
            QTimer.singleShot(350, self._maybe_open_wizard)

    def _maybe_open_wizard(self):
        self.show_wizard()
        try:
            with open(SENTINEL_FILE, "w", encoding="utf-8") as fh:
                fh.write("first-run completed %s\n" % datetime.now().isoformat())
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Generic background call (result is marshalled back to the GUI thread)
    # ------------------------------------------------------------------ #
    def run_background(self, fn, callback):
        """Run fn() in a worker thread; call callback(result) on the GUI thread.

        The result travels via sig_bg_result, so callbacks always execute on
        the GUI thread no matter which thread produced them."""
        if self._shutting_down:
            return
        self._bg_counter += 1
        cid = self._bg_counter
        self._bg_callbacks[cid] = callback

        def work():
            try:
                result = fn()
            except Exception as exc:                  # noqa: BLE001
                result = (False, "error: %s" % exc)
            if not self._shutting_down:
                self.sig_bg_result.emit(cid, result)

        _TaskThread(work).start()

    def _on_bg_result(self, cid, result):
        callback = self._bg_callbacks.pop(cid, None)
        if callback is not None and not self._shutting_down:
            callback(result)

    # ================================================================== #
    # UI construction
    # ================================================================== #

    def _build_actions(self):
        style = self.style()

        def std_icon(*names):
            """First standard pixmap that exists in this Qt version (or None)."""
            for name in names:
                pixmap = getattr(QStyle.StandardPixmap, name, None)
                if pixmap is not None:
                    return pixmap
            return None

        self._themed_actions = {}

        def make(text, slot=None, shortcut=None, checkable=False, icon=None, themed=None):
            action = QAction(text, self)
            if themed:
                self._themed_actions[themed] = action
            if callable(slot):
                action.triggered.connect(slot)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                action.setStatusTip("%s  [%s]" % (text, QKeySequence(shortcut).toString(
                    QKeySequence.NativeText)))
            else:
                action.setStatusTip(text)
            action.setCheckable(checkable)
            if isinstance(icon, QIcon):
                action.setIcon(icon)
            elif icon is not None:
                action.setIcon(style.standardIcon(icon))
            self.addAction(action)
            return action

        self.act_fetch = make("Fetch / Send Now", self.start_fetch_thread, "Ctrl+Return",
                              icon=_make_icon("fetch"), themed="fetch")
        self.act_analyze = make("Analyze Now", self.analyze_now, "Ctrl+B",
                                icon=_make_icon("analyze"), themed="analyze")
        self.act_auto = make("Auto-Fetch Enabled", None, checkable=True,
                             icon=_make_icon("auto"), themed="auto")
        self.act_auto.setChecked(True)
        self.act_auto.toggled.connect(self._toggle_auto_fetch)
        self.act_settings = make("Settings\u2026", self.open_settings_window, "Ctrl+,",
                                 icon=_make_icon("settings"), themed="settings")

        self.act_sources = make("Load Data Sources\u2026", self.load_data_sources, "Ctrl+O")
        self.act_quickstart_sources = make(
            "Quick-Start Sources (short test list)",
            lambda: self.load_bundled_sources("Default_test-v1.txt"))
        self.act_long_sources = make(
            "World News Sources (long list)",
            lambda: self.load_bundled_sources("Default_long-v1.txt"))
        self.act_prompt = make("Load Prompt File\u2026", self.load_prompt_file)
        self.act_open_reports = make("Open Report Folder", self.open_report_folder)
        self.act_quit = make("Quit", self.close, QKeySequence.Quit)

        self.act_new_profile = make("Save Profile As\u2026", self.save_current_profile, "Ctrl+Shift+S")
        self.act_load_profile = make("Load Selected Profile", self.load_selected_profile)
        self.act_del_profile = make("Delete Selected Profile", self.delete_selected_profile)

        self.act_copy_report = make("Copy Latest Report", self.copy_latest_report, "Ctrl+Shift+C")
        self.act_clear_log = make("Clear Terminal Log", lambda: self.log.clear(), "Ctrl+K")
        self.act_clear_feed = make("Clear Report Feed", self.clear_history)

        self.act_rain = make("Screen Saver Rain", None, "F1", checkable=True,
                             icon=_make_icon("rain"), themed="rain")
        self.act_rain.toggled.connect(self.toggle_rain)

        self.act_zoom_in = make("Increase UI Scale", lambda: self.zoom_ui(1), "Ctrl+=")
        self.act_zoom_out = make("Decrease UI Scale", lambda: self.zoom_ui(-1), "Ctrl+-")
        self.act_zoom_reset = make("Reset UI Scale", lambda: self.zoom_ui(0))

        self.act_wizard = make("Setup Wizard\u2026", self.show_wizard, "Ctrl+W",
                               icon=std_icon("SP_FileDialogContentsView",
                                             "SP_DialogHelpButton"))
        self.act_help = make("Keyboard && Usage Help", self.show_help, "F2",
                             icon=std_icon("SP_MessageBoxQuestion"))
        self.act_about = make("About ShunyaNet Sentinel", self.show_about)

    def _build_central(self):
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.setOpaqueResize(True)
        self.center_splitter = splitter

        # ---- latest report -------------------------------------------------
        report_card, rlay = make_card("LATEST REPORT")
        rbar = QHBoxLayout()
        rbar.setSpacing(4)
        rbar.addStretch(1)
        copy_btn = QToolButton()
        copy_btn.setText("Copy")
        copy_btn.setToolTip("Copy the latest report to the clipboard (Ctrl+Shift+C)")
        copy_btn.clicked.connect(self.copy_latest_report)
        save_btn = QToolButton()
        save_btn.setText("Save\u2026")
        save_btn.clicked.connect(self.save_latest_report)
        rbar.addWidget(copy_btn)
        rbar.addWidget(save_btn)
        rlay.addLayout(rbar)

        self.reply_box = QTextEdit(readOnly=True)
        self.reply_box.setLineWrapMode(QTextEdit.WidgetWidth)
        self.reply_box.setObjectName("replyBox")
        self.reply_box.setPlaceholderText(
            "AWAITING SIGNAL \u2014 set topics on the left, point Sentinel at your LLM "
            "(Run \u2192 Settings), then Fetch / Send (Ctrl+Enter).")
        rlay.addWidget(self.reply_box, 1)
        splitter.addWidget(report_card)

        # ---- terminal log --------------------------------------------------
        log_card, llay = make_card("TERMINAL LOG")
        lbar = QHBoxLayout()
        lbar.setSpacing(4)
        lbar.addStretch(1)
        clear_btn = QToolButton()
        clear_btn.setText("Clear")
        clear_btn.setToolTip("Clear the terminal log (Ctrl+K)")
        lbar.addWidget(clear_btn)
        llay.addLayout(lbar)

        self.log = QTextEdit(readOnly=True)
        self.log.setLineWrapMode(QTextEdit.NoWrap)
        self.log.document().setMaximumBlockCount(5000)   # bounded memory on long runs
        self.log.setObjectName("logView")
        self.log.setPlaceholderText("Runtime chatter, feed errors and LLM status.")
        llay.addWidget(self.log, 1)
        clear_btn.clicked.connect(self.log.clear)
        splitter.addWidget(log_card)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([560, 300])
        self.setCentralWidget(splitter)

    def _build_targeting_dock(self):
        dock = QDockWidget("Targeting", self)
        dock.setObjectName("dock_targeting")
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        features = (QDockWidget.DockWidgetFeature.DockWidgetClosable
                    | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                    | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        dock.setFeatures(features)

        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(10)

        # --- topics card (the heart of the app: first and biggest) ---------
        topic_card, tl = make_card("WHAT TO WATCH")
        tl.addWidget(QLabel("Overall topic"))
        self.overall_topic_edit = QTextEdit()
        self.overall_topic_edit.setFixedHeight(56)
        self.overall_topic_edit.setPlaceholderText("e.g. Severe weather in the US Gulf Coast")
        self.overall_topic_edit.setToolTip("One sentence describing what you are "
                                           "monitoring. Saved automatically.")
        tl.addWidget(self.overall_topic_edit)
        tl.addWidget(QLabel("Signals / sub-topics  (one per line)"))
        self.subtopics_edit = QTextEdit()
        self.subtopics_edit.setMinimumHeight(120)
        self.subtopics_edit.setPlaceholderText(
            "tornado watch issued\npower grid failure\nflash flood emergency warning")
        self.subtopics_edit.setToolTip("Each line is a signal the LLM screens the feeds "
                                       "for. These are guidance, not exact keyword "
                                       "matches. Saved automatically.")
        tl.addWidget(self.subtopics_edit, 1)
        topic_row = QHBoxLayout()
        topic_row.setSpacing(4)
        save_p = QPushButton("Save As Profile\u2026")
        save_p.setToolTip("Give this topic set a name so you can switch back to it "
                          "later (Ctrl+Shift+S)")
        save_p.clicked.connect(self.save_current_profile)
        load_p = QPushButton("Load Profile")
        load_p.setToolTip("Replace the topics above with the profile selected below")
        load_p.clicked.connect(self.load_selected_profile)
        del_p = QPushButton("Delete")
        del_p.setToolTip("Remove the profile selected below")
        del_p.clicked.connect(self.delete_selected_profile)
        topic_row.addWidget(save_p)
        topic_row.addWidget(load_p)
        topic_row.addWidget(del_p)
        tl.addLayout(topic_row)
        self.profile_select = QComboBox()
        self.profile_select.setToolTip("Saved topic profiles (topic_profiles.json). "
                                       "Pick one, then press Load Profile.")
        self.profile_select.currentTextChanged.connect(self._update_profile_label)
        tl.addWidget(self.profile_select)
        self.profile_hint = hint_label("Topics autosave with the app. Profiles let you "
                                       "keep several watch-lists.")
        tl.addWidget(self.profile_hint)
        vbox.addWidget(topic_card, 1)

        # --- inputs card ----------------------------------------------------
        io_card, iol = make_card("DATA & PROMPTS")
        src_btn = QPushButton("Load Data Sources\u2026")
        src_btn.setToolTip("A .txt file with one RSS feed URL per line (Ctrl+O). "
                           "Bundled lists: File menu.")
        src_btn.clicked.connect(self.load_data_sources)
        iol.addWidget(src_btn)
        self.src_label = hint_label("%d sources" % len(self.feeds))
        iol.addWidget(self.src_label)
        ap = QPushButton("Load Prompt File\u2026")
        ap.setToolTip("ONE .txt file drives both modes. Mark its parts with:\n"
                      "  #> ALERT    (screening instructions, every fetch)\n"
                      "  #> SUMMARY  (periodic trend report)\n"
                      "  #> MEMORY   (memory policy; used only while the memory\n"
                      "              profiler is enabled in Settings)\n"
                      "Tokens {TOPICS}, {CHUNK}, {LONG_TERM_MEMORY} fill in at send time.\n"
                      "A file with no markers is used whole for both modes.")
        ap.clicked.connect(self.load_prompt_file)
        iol.addWidget(ap)
        self.prompt_label = hint_label("Prompt: default")
        iol.addWidget(self.prompt_label)
        vbox.addWidget(io_card)

        # --- logo / big fetch ----------------------------------------------
        logo_btn = QPushButton()
        logo_btn.setFixedSize(120, 120)
        logo_btn.setFlat(True)
        logo_btn.setToolTip("FETCH / SEND NOW \u2014 pull the feeds and screen them for "
                            "alerts (Ctrl+Enter)")
        logo_btn.setStyleSheet("QPushButton { border: none; background-color: transparent; }")
        logo_btn.clicked.connect(self.start_fetch_thread)
        logo_layout = QVBoxLayout(logo_btn)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setAlignment(Qt.AlignCenter)

        file_path = resource_path(os.path.join("assets", "logo_icon.png"))
        if os.path.exists(file_path):
            pix = QPixmap(file_path)
            if not pix.isNull():
                pix = pix.scaled(104, 104, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                logo_label = ScalableLabel(pix)
                logo_label.setStyleSheet("border: none;")
            else:
                logo_label = QLabel("logo unavailable")
                logo_label.setAlignment(Qt.AlignCenter)
        else:
            logo_label = QLabel("[ SHUNYA NET ]")
            logo_label.setAlignment(Qt.AlignCenter)
        logo_layout.addWidget(logo_label)

        def animate_label(scale):
            anim = QPropertyAnimation(logo_label, b"scale")
            anim.setDuration(150)
            anim.setEndValue(scale)
            anim.setEasingCurve(QEasingCurve.InOutQuad)
            anim.start()
            logo_label._anim = anim

        logo_btn.enterEvent = lambda e: (animate_label(1.1), QPushButton.enterEvent(logo_btn, e))
        logo_btn.leaveEvent = lambda e: (animate_label(1.0), QPushButton.leaveEvent(logo_btn, e))
        logo_btn.mousePressEvent = lambda e: (animate_label(0.95), QPushButton.mousePressEvent(logo_btn, e))
        logo_btn.mouseReleaseEvent = lambda e: (animate_label(1.1), QPushButton.mouseReleaseEvent(logo_btn, e))

        center_container = QWidget()
        center_layout = QVBoxLayout(center_container)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.addStretch(1)
        center_layout.addWidget(logo_btn, 0, Qt.AlignCenter)
        center_layout.addStretch(1)
        vbox.addWidget(center_container)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumWidth(240)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dock.setWidget(scroll)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.targeting_dock = dock

        # autosave when the user edits topics (debounced)
        self.overall_topic_edit.textChanged.connect(self.topic_save_timer.start)
        self.subtopics_edit.textChanged.connect(self.topic_save_timer.start)

    def _build_feed_dock(self):
        dock = QDockWidget("Report Feed", self)
        dock.setObjectName("dock_feed")
        dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea)
        features = (QDockWidget.DockWidgetFeature.DockWidgetClosable
                    | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                    | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        dock.setFeatures(features)

        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(6)

        self.feed_filter = QLineEdit()
        self.feed_filter.setPlaceholderText("Filter feed\u2026  (type to narrow reports)")
        self.feed_filter.setClearButtonEnabled(True)
        self.feed_filter.textChanged.connect(self._filter_history)
        vbox.addWidget(self.feed_filter)

        meta = QHBoxLayout()
        self.feed_count_label = hint_label("0 entries")
        meta.addWidget(self.feed_count_label, 1)
        expand_all = QToolButton()
        expand_all.setText("Expand all")
        expand_all.clicked.connect(lambda: self._expand_all_history(True))
        collapse_all = QToolButton()
        collapse_all.setText("Collapse")
        collapse_all.clicked.connect(lambda: self._expand_all_history(False))
        meta.addWidget(expand_all)
        meta.addWidget(collapse_all)
        vbox.addLayout(meta)

        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_widget = QWidget()
        self.history_layout = QVBoxLayout(self.history_widget)
        self.history_layout.setContentsMargins(0, 0, 4, 0)
        self.history_layout.setSpacing(6)
        self.history_layout.addStretch(1)
        self.history_scroll.setWidget(self.history_widget)
        vbox.addWidget(self.history_scroll, 1)

        self.feed_placeholder = hint_label(
            "No reports yet.\n\nAlerts and summaries appear here as cards you can "
            "expand, filter and search.")
        self.feed_placeholder.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.history_layout.insertWidget(0, self.feed_placeholder)

        dock.setWidget(inner)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.feed_dock = dock
        self.feed_entries = []

    def _build_menus_and_toolbar(self):
        mb = self.menuBar()

        m_file = mb.addMenu("&File")
        m_file.addAction(self.act_sources)
        src_menu = m_file.addMenu("Bundled Source Lists")
        src_menu.addAction(self.act_quickstart_sources)
        src_menu.addAction(self.act_long_sources)
        m_file.addSeparator()
        m_file.addAction(self.act_prompt)
        m_file.addSeparator()
        m_file.addAction(self.act_open_reports)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_run = mb.addMenu("&Run")
        m_run.addAction(self.act_fetch)
        m_run.addAction(self.act_analyze)
        m_run.addAction(self.act_auto)
        m_run.addSeparator()
        m_run.addAction(self.act_settings)

        m_profiles = mb.addMenu("&Profiles")
        m_profiles.addAction(self.act_new_profile)
        m_profiles.addAction(self.act_load_profile)
        m_profiles.addAction(self.act_del_profile)

        self.m_view = mb.addMenu("&View")
        self.m_view.addAction(self.act_rain)
        self.m_view.addSeparator()
        self.m_view.addAction(self.act_zoom_in)
        self.m_view.addAction(self.act_zoom_out)
        self.m_view.addAction(self.act_zoom_reset)
        self.m_view.addSeparator()
        for dock in (self.targeting_dock, self.feed_dock):
            self.m_view.addAction(dock.toggleViewAction())
        self.m_view.addSeparator()
        reset_layout = QAction("Reset Layout", self)
        reset_layout.setStatusTip("Restore default panels, sizes and window geometry")
        reset_layout.triggered.connect(self.reset_ui_state)
        self.m_view.addAction(reset_layout)

        m_help = mb.addMenu("&Help")
        m_help.addAction(self.act_wizard)
        m_help.addAction(self.act_help)
        agent_act = QAction("Agent / Script Integration\u2026", self)
        agent_act.setStatusTip("Show the files external agents can read")
        agent_act.triggered.connect(self.show_agent_access)
        m_help.addAction(agent_act)
        m_help.addAction(self.act_about)

        tb = self.addToolBar("Main")
        tb.setObjectName("toolbar_main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for action in (self.act_fetch, self.act_analyze, self.act_auto,
                       self.act_settings, self.act_rain):
            tb.addAction(action)

    def _build_status_bar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(True)
        pad = "padding: 0 8px; color: #35a357;"
        self.status_run = QLabel(" idle ")
        self.status_fetch = QLabel(" next fetch --:-- ")
        self.status_bulk = QLabel(" next analysis --:-- ")
        self.status_sources = QLabel(" %d sources " % len(self.feeds))
        self.status_mem = QLabel(" memory off ")
        for widget in (self.status_run, self.status_fetch, self.status_bulk,
                       self.status_mem, self.status_sources):
            widget.setStyleSheet(pad)
            sb.addWidget(widget)
        hint = QLabel(" F2 help   Ctrl+Enter fetch ")
        hint.setStyleSheet(pad)
        sb.addPermanentWidget(hint)

    # ================================================================== #
    # Small UI helpers
    # ================================================================== #

    def _append_log(self, msg):
        if self._shutting_down:
            return
        line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        self.log.append(line)
        print(line)

    def _set_report(self, msg):
        if self._shutting_down:
            return
        self.reply_box.setPlainText(msg or "")

    def thread_safe_log(self, msg):
        """Safe from worker threads."""
        if not self._shutting_down:
            self.sig_log.emit(str(msg))

    def thread_safe_reply(self, msg):
        if not self._shutting_down:
            self.sig_report.emit(str(msg))

    def copy_latest_report(self):
        text = self.reply_box.toPlainText().strip()
        if not text:
            self.statusBar().showMessage("No report to copy yet.", 3000)
            return
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("Latest report copied to clipboard.", 3000)

    def save_latest_report(self):
        text = self.reply_box.toPlainText()
        if not text:
            self.statusBar().showMessage("No report to save yet.", 3000)
            return
        suggested = os.path.join(self.get_setting("REPORT_OUTPUT_DIR") or DEFAULT_REPORT_OUTPUT_DIR,
                                 "REPORT_%s.txt" % datetime.now().strftime("%H%M%S_%d-%m-%y"))
        path, _ = QFileDialog.getSaveFileName(self, "Save Report", suggested, "Text Files (*.txt)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                self.statusBar().showMessage("Saved %s" % os.path.basename(path), 4000)
            except Exception as exc:
                QMessageBox.warning(self, "Save failed", str(exc))

    def open_report_folder(self):
        out_dir = self.get_setting("REPORT_OUTPUT_DIR") or DEFAULT_REPORT_OUTPUT_DIR
        try:
            os.makedirs(out_dir, exist_ok=True)
            open_path(out_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Open folder", str(exc))

    def zoom_ui(self, direction):
        if direction == 0:
            self.ui_font_pt = DEFAULT_UI_FONT_PT
        else:
            self.ui_font_pt = max(7, min(20, self.ui_font_pt + direction))
        font = QApplication.font()
        font.setFamily(theme_font_family())
        font.setPointSize(self.ui_font_pt)
        QApplication.setFont(font)
        self.save_app_state()
        self.statusBar().showMessage("UI scale: %d pt" % self.ui_font_pt, 2500)

    def toggle_rain(self, checked=None):
        if checked is None:
            checked = self.act_rain.isChecked()
        if checked:
            self.green_overlay.setGeometry(0, 0, self.width(), self.height())
            self.green_overlay.init_columns()
            self.green_overlay.show()
            self.green_overlay.raise_()
        else:
            self.green_overlay.hide()

    def resizeEvent(self, event):
        if hasattr(self, "green_overlay"):
            self.green_overlay.setGeometry(0, 0, self.width(), self.height())
        return super().resizeEvent(event)

    def _toggle_auto_fetch(self, checked):
        if not hasattr(self, "auto_fetch_timer"):
            return
        if checked:
            self.next_fetch_ts = time.time() + int(self.get_setting("FETCH_INTERVAL", int))
            self.auto_fetch_timer.start(int(self.get_setting("FETCH_INTERVAL", int)) * 1000)
            self.thread_safe_log("Auto-fetch enabled.")
        else:
            self.auto_fetch_timer.stop()
            self.next_fetch_ts = 0
            self.thread_safe_log("Auto-fetch paused (manual fetch still available).")

    def _update_status_bar(self):
        busy = []
        if self._fetch_worker:
            busy.append("fetch")
        if self._analysis_worker:
            busy.append("analysis")
        if self._memory_worker:
            busy.append("memory")
        if busy:
            self.status_run.setText(" busy: %s " % "/".join(busy))
        else:
            self.status_run.setText(" idle ")

        if not hasattr(self, "auto_fetch_timer"):
            return
        auto_on = self.act_auto.isChecked()
        self.status_fetch.setText(" next fetch %s " % (
            fmt_mmss(self.next_fetch_ts - time.time()) if auto_on else "paused"))

        bulk_enabled = self.get_setting("SUMMARIES_ENABLED", int) == 1
        self.status_bulk.setText(" next analysis %s " % (
            fmt_mmss(self.next_bulk_ts - time.time()) if bulk_enabled else "manual"))

        self.status_mem.setText(" memory %s " % (
            "on" if self.get_setting("MEMORY_PROFILER", int) == 1 else "off"))
        self.status_sources.setText(" %d sources " % len(self.feeds))

    def _update_source_label(self):
        if hasattr(self, "src_label"):
            self.src_label.setText("%d data sources armed" % len(self.feeds))

    def _refresh_prompt_labels(self):
        if not hasattr(self, "prompt_label"):
            return
        name = os.path.basename(self.prompt_file) if self.prompt_file else \
            "(legacy default prompts)"
        sections = ", ".join(sorted(k for k, v in self.prompt_sections.items() if v)) \
            or "whole-file"
        self.prompt_label.setText("Prompt: %s\nSections: %s" % (name, sections))

    def _update_profile_label(self):
        if not hasattr(self, "profile_hint"):
            return
        name = self.profile_select.currentText()
        self.profile_hint.setText("Active profile: %s" % (name or "none"))

    def show_help(self):
        QMessageBox.information(
            self, "Shortcuts & Navigation",
            "Ctrl+Enter      Fetch / Send now\n"
            "Ctrl+B          Analyze rolling file now (summary report)\n"
            "Ctrl+,          Settings\n"
            "Ctrl+W          Re-open the setup wizard\n"
            "F1              Toggle matrix rain screensaver\n"
            "Ctrl+K          Clear terminal log\n"
            "Ctrl+Shift+C    Copy latest report\n"
            "Ctrl+= / Ctrl+- UI font scale\n"
            "Ctrl+O          Load data sources\n"
            "F2              This help\n\n"
            "Panels: every side panel can be dragged out to float, tabbed behind "
            "another, or closed from the View menu. Drag the divider between the "
            "report and the log to rebalance them; the layout is remembered.")

    def show_agent_access(self):
        """Explain, in one place, every file an external agent can read."""
        out_dir = self.get_setting("REPORT_OUTPUT_DIR") or DEFAULT_REPORT_OUTPUT_DIR
        handoff = os.path.join(out_dir, self.AGENT_HANDOFF_NAME)
        lines = [
            "ShunyaNet Sentinel writes plain files you (or an AI agent such as",
            "Hermes) can read without touching the GUI:",
            "",
            "  %s" % handoff,
            "      JSON index of the newest report + memory files. Poll this.",
            "  %s/" % out_dir,
            "      ALERT_*.txt / SUMMARY_*.txt reports, newest per timestamp.",
            "  %s" % MEMORY_LONG_TERM_FILE,
            "      Consolidated long-term memory (when profiler is on).",
            "  %s" % MEMORY_SHORT_TERM_FILE,
            "      Recent report excerpts awaiting consolidation.",
            "",
            "Set up an agent skill to read agent_handoff.json, open the latest",
            "report, and act on it (summarize, forward, cross-check feeds).",
        ]
        QMessageBox.information(self, "Agent / Script Integration", "\n".join(lines))

    def show_about(self):
        QMessageBox.about(
            self, "ShunyaNet Sentinel",
            "ShunyaNet Sentinel v%s\n\nRSS / event feed triage with a local LLM and "
            "sequential short/long-term memory.\n\nAlert channels: Slack, Discord, "
            "Telegram, ntfy, generic webhook, desktop popups.\n\nApp data folder: %s"
            % (APP_VERSION, APP_DIR))

    def apply_theme(self, theme_key=None, persist=True):
        """Switch the whole UI to a palette from THEMES (live preview safe)."""
        global PALETTE
        if theme_key not in THEMES:
            theme_key = DEFAULT_THEME
        PALETTE = dict(THEMES[theme_key])
        self.settings["UI_THEME"] = theme_key
        qapp = QApplication.instance()
        qapp.setStyleSheet(build_style_sheet(PALETTE))
        font = qapp.font()
        font.setFamily(theme_font_family())
        if PALETTE.get("mono", True):
            font.setStyleHint(QFont.Monospace)
        font.setPointSize(self.ui_font_pt)
        qapp.setFont(font)
        # toolbar glyphs are drawn in the accent colour -> redraw for new theme
        for kind, act in getattr(self, "_themed_actions", {}).items():
            try:
                act.setIcon(_make_icon(kind))
            except Exception:
                pass
        if persist:
            self.save_app_state()

    def apply_settings(self):
        """Push settings dict into timers / labels. Called after any change."""
        for key in INT_SETTINGS:
            try:
                self.settings[key] = int(self.settings.get(key, DEFAULT_SETTINGS[key]))
            except (TypeError, ValueError):
                self.settings[key] = DEFAULT_SETTINGS[key]

        if not hasattr(self, "auto_fetch_timer"):
            return

        interval = max(30, int(self.settings["FETCH_INTERVAL"]))
        window = max(60, int(self.settings["ANALYSIS_WINDOW"]))
        self.auto_fetch_timer.setInterval(interval * 1000)
        self.bulk_timer.setInterval(window * 1000)
        self.memory_timer.setInterval(MEMORY_PROFILER_INTERVAL * 1000)

        if self.act_auto.isChecked():
            self.next_fetch_ts = time.time() + interval
            self.auto_fetch_timer.start(interval * 1000)
        else:
            self.auto_fetch_timer.stop()
            self.next_fetch_ts = 0

        if self.get_setting("SUMMARIES_ENABLED", int) == 1:
            elapsed = time.time() - self.rolling_file_start_time
            self.bulk_timer.start(window * 1000)
            self.next_bulk_ts = time.time() + max(1, window - elapsed)
        else:
            self.bulk_timer.stop()
            self.next_bulk_ts = 0

        if self.get_setting("MEMORY_PROFILER", int) == 1:
            self.memory_profiler_start_time = time.time()
            self.memory_timer.start()
        else:
            self.memory_timer.stop()

        self._update_status_bar()
        self.save_app_state()

    # ================================================================== #
    # Worker orchestration
    # ================================================================== #

    def _spawn_task(self, kind, fn):
        """Start a named task thread; returns True if started."""
        with self._worker_lock:
            if self._shutting_down or getattr(self, "_%s_worker" % kind) is not None:
                return False
            done_sig = getattr(self, "sig_%s_done" % kind, None)
            task = _TaskThread(fn, finished_signal=done_sig)
            setattr(self, "_%s_worker" % kind, task)
            self.active_threads.append(task)
        task.start()
        return True

    def _finish_task(self, kind):
        with self._worker_lock:
            task = getattr(self, "_%s_worker" % kind, None)
            setattr(self, "_%s_worker" % kind, None)
            if task in self.active_threads:
                self.active_threads.remove(task)
        return task

    def start_fetch_thread(self):
        if self._shutting_down:
            return
        if self._fetch_worker is not None:
            self.statusBar().showMessage("A fetch is already running.", 3000)
            return
        if not self._spawn_task("fetch", self.fetch_and_send):
            return
        self.act_fetch.setEnabled(False)
        self.next_fetch_ts = time.time() + int(self.get_setting("FETCH_INTERVAL", int))
        self.status_run.setText(" busy: fetch ")

    def _on_fetch_finished(self):
        self._finish_task("fetch")
        self.act_fetch.setEnabled(not self._shutting_down)
        self._update_status_bar()

    def start_bulk_analysis(self, force=False):
        if self._shutting_down or self._analysis_worker is not None:
            return
        if not force and self.get_setting("SUMMARIES_ENABLED", int) != 1:
            return
        if not self._spawn_task("analysis",
                                lambda: self.perform_bulk_analysis_if_ready(force=force)):
            return
        self.act_analyze.setEnabled(False)

    def analyze_now(self):
        if self._analysis_worker is not None:
            self.statusBar().showMessage("Analysis already running.", 3000)
            return
        self.start_bulk_analysis(force=True)

    def _on_analysis_finished(self):
        self._finish_task("analysis")
        self.act_analyze.setEnabled(not self._shutting_down)
        self._update_status_bar()

    def _maybe_run_bulk_after_fetch(self):
        """Called from the fetch thread; hops to the GUI thread then analyzes.

        Only a suggestion: it still requires the Summaries toggle and an
        elapsed ANALYSIS_WINDOW, so reports land on the configured cadence
        rather than after every single pull."""
        if self.get_setting("SUMMARIES_ENABLED", int) != 1:
            return
        window = self.get_setting("ANALYSIS_WINDOW", int)
        if time.time() - self.rolling_file_start_time < window:
            return
        self.sig_request_bulk.emit()

    def start_memory_profiler(self):
        if self._shutting_down or self._memory_worker is not None:
            return
        self._spawn_task("memory", self.process_memory_profiler_if_ready)

    def _on_memory_finished(self):
        self._finish_task("memory")
        self._update_status_bar()

    # ================================================================== #
    # Settings access
    # ================================================================== #

    def open_settings_window(self):
        dlg = SettingsDialog(self)
        if dlg.exec():   # accepted -> persist everything incl. previewed theme
            self.save_app_state()

    # legacy setting names folded into simpler ones (v3); old app_state.json
    # files keep working through these aliases
    _SETTING_ALIASES = {
        "SUMMARY_ENABLED": "SUMMARIES_ENABLED",
        "BULK_ANALYSIS": "SUMMARIES_ENABLED",
    }

    def get_setting(self, name, type_cast=str):
        name = self._SETTING_ALIASES.get(name, name)
        value = self.settings.get(name, DEFAULT_SETTINGS.get(name))
        try:
            return type_cast(value)
        except (TypeError, ValueError):
            default = DEFAULT_SETTINGS.get(name, 0 if type_cast is int else "")
            try:
                return type_cast(default)
            except (TypeError, ValueError):
                return default

    # ================================================================== #
    # Report feed (history)
    # ================================================================== #

    def add_to_history(self, text):
        if self._shutting_down or not text:
            return
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry = FeedEntry(timestamp, text[:FEED_BODY_CHARS])
            idx = self.history_layout.count() - 1   # insert before trailing stretch
            self.history_layout.insertWidget(max(0, idx), entry)
            self.feed_entries.append(entry)

            while len(self.feed_entries) > MAX_FEED_ENTRIES:
                oldest = self.feed_entries.pop(0)
                self.history_layout.removeWidget(oldest)
                oldest.deleteLater()

            for previous in self.feed_entries[:-1]:
                previous.set_expanded(False)
            entry.set_expanded(True)
            self.feed_count_label.setText("%d entries" % len(self.feed_entries))
            self.feed_placeholder.setVisible(not self.feed_entries)
            if self.feed_filter.text().strip():
                entry.matches(self.feed_filter.text().strip().lower())

            scrollbar = self.history_scroll.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        except Exception as exc:
            print("History error: %s" % exc)

    def clear_history(self):
        for entry in self.feed_entries:
            self.history_layout.removeWidget(entry)
            entry.deleteLater()
        self.feed_entries = []
        self.feed_count_label.setText("0 entries")
        self.feed_filter.clear()
        self.feed_placeholder.setVisible(True)

    def _filter_history(self, text):
        needle = (text or "").strip().lower()
        for entry in self.feed_entries:
            entry.matches(needle)

    def _expand_all_history(self, open_it):
        for entry in self.feed_entries:
            entry.set_expanded(open_it)

    # ================================================================== #
    # Topic profiles
    # ================================================================== #

    def get_topic_profile(self):
        return {
            "overall_topic": self.overall_topic_edit.toPlainText().strip(),
            "subtopics": self.subtopics_edit.toPlainText().strip(),
        }

    def set_topic_profile(self, profile):
        overall, sub = "", ""
        if isinstance(profile, dict):
            overall = profile.get("overall_topic", "") or ""
            sub = profile.get("subtopics", "") or ""
        elif isinstance(profile, list):
            cleaned = [str(i).strip() for i in profile if str(i).strip()]
            if cleaned:
                overall, sub = cleaned[0], "\n".join(cleaned[1:])
        elif isinstance(profile, str):
            overall = profile.strip()
        self.overall_topic_edit.setPlainText(overall)
        self.subtopics_edit.setPlainText(sub)
        self._update_profile_label()

    def get_topics_string(self):
        p = self.get_topic_profile()
        return ("OVERALL TOPIC:\n%s\n\nSUB-TOPICS / SIGNALS:\n%s"
                % (p["overall_topic"] or "None", p["subtopics"] or "None"))

    def load_profiles(self):
        try:
            if os.path.exists(PROFILE_FILE):
                with open(PROFILE_FILE, "r", encoding="utf-8") as fh:
                    self.profiles = json.load(fh)
        except Exception as exc:
            self.profiles = {}
            self.thread_safe_log("Profile load failed: %s" % exc)

    def save_profiles(self):
        try:
            with open(PROFILE_FILE, "w", encoding="utf-8") as fh:
                json.dump(self.profiles, fh, indent=2)
        except Exception as exc:
            self.thread_safe_log("Profile save failed: %s" % exc)

    def update_profile_combobox(self):
        current = self.profile_select.currentText()
        self.profile_select.blockSignals(True)
        self.profile_select.clear()
        for name in self.profiles.keys():
            self.profile_select.addItem(name)
        if current in self.profiles:
            self.profile_select.setCurrentText(current)
        self.profile_select.blockSignals(False)
        self._update_profile_label()

    def save_current_profile(self):
        name, ok = QInputDialog.getText(self, "Profile Name", "Enter profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        self.profiles[name] = self.get_topic_profile()
        self.save_profiles()
        self.update_profile_combobox()
        self.profile_select.setCurrentText(name)
        self.statusBar().showMessage("Profile '%s' saved." % name, 4000)

    def load_selected_profile(self):
        name = self.profile_select.currentText()
        if name in self.profiles:
            self.set_topic_profile(self.profiles[name])
            self.statusBar().showMessage("Profile '%s' loaded." % name, 4000)
        else:
            self.statusBar().showMessage("No profile selected.", 3000)

    def delete_selected_profile(self):
        name = self.profile_select.currentText()
        if name in self.profiles:
            reply = QMessageBox.question(self, "Delete Profile",
                                         "Delete profile '%s'?" % name,
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            del self.profiles[name]
            self.save_profiles()
            self.update_profile_combobox()

    # ================================================================== #
    # Files: sources, prompts, memory, reports
    # ================================================================== #

    def load_data_sources(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Data Source File", "",
                                              "Text Files (*.txt);;JSON Files (*.json)")
        if not path:
            return
        count = self.load_sources_from_file(path)
        if count == 0:
            QMessageBox.warning(self, "No sources loaded",
                                "That file contained no usable feed URLs.\n"
                                "Expected: one URL per line (.txt) or a JSON list.")

    def load_sources_from_file(self, path):
        """Load RSS URLs from a .txt (one per line) or .json (list) file."""
        try:
            if path.lower().endswith(".json"):
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                urls = [str(u).strip() for u in data if str(u).strip()] \
                    if isinstance(data, list) else []
            else:
                with open(path, "r", encoding="utf-8") as fh:
                    urls = [line.strip() for line in fh
                            if line.strip() and not line.strip().startswith("#")]
            if urls:
                self.feeds = urls
                self._update_source_label()
                self.save_app_state()
                self.thread_safe_log("%d sources loaded from %s"
                                     % (len(self.feeds), path))
            return len(urls)
        except Exception as exc:
            self.thread_safe_log("Source load failed: %s" % exc)
            return 0

    def load_bundled_sources(self, filename, quiet=False):
        """Load one of the lists shipped in 'Data Sources/'."""
        path = os.path.join(APP_DIR, "Data Sources", filename)
        if not os.path.exists(path):
            if not quiet:
                QMessageBox.warning(self, "Not found",
                                    "Bundled list not found:\n%s" % path)
            return False
        count = self.load_sources_from_file(path)
        if not quiet:
            self.statusBar().showMessage("%d sources loaded from %s"
                                         % (count, filename), 5000)
        return count > 0

    def _load_default_data_sources_if_empty(self):
        """Arm the short quick-start list on a virgin install, or when an older
        state file predates feed persistence (so nobody silently gets the 8
        hardcoded demo URLs instead of the bundled test list)."""
        if not self._state_has_feeds:
            path = os.path.join(APP_DIR, "Data Sources", "Default_test-v1.txt")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    urls = [line.strip() for line in fh
                            if line.strip() and not line.strip().startswith("#")]
                if urls:
                    self.feeds = urls

    def show_wizard(self):
        dlg = SetupWizard(self)
        dlg.exec()
        # wizard may have changed settings/topics/sources
        self.apply_settings()
        self._update_source_label()
        self.save_app_state()
        if getattr(dlg, "launched", False):
            # final page said "Launch First Fetch" — deliver on it
            QTimer.singleShot(250, self.start_fetch_thread)

    def _load_unified_prompt(self):
        """Pick the prompt file (saved choice > unified default > legacy pair)."""
        saved = str(self.settings.get("PROMPT_FILE", "") or "").strip()
        if saved and os.path.exists(saved):
            self.set_prompt_file(saved)
            return
        if os.path.exists(DEFAULT_PROMPT_FILE):
            self.set_prompt_file(DEFAULT_PROMPT_FILE)
            return
        # legacy fallback: two separate files stitched into one section map
        alert_txt = self.load_prompt_text(
            os.path.join(APP_DIR, "default_prompt.txt"), "")
        summary_txt = self.load_prompt_text(
            os.path.join(APP_DIR, "default_summary_prompt.txt"), "")
        self.prompt_file = ""
        self.prompt_sections = {"ALERT": alert_txt, "SUMMARY": summary_txt}

    def set_prompt_file(self, path):
        self.prompt_file = path
        text = self.load_prompt_text(path, "")
        self.prompt_sections = parse_prompt_sections(text)
        if not self.prompt_sections.get("ALERT") and not self.prompt_sections.get("SUMMARY"):
            self.prompt_sections = {"ALERT": text, "SUMMARY": text}
        self._refresh_prompt_labels()

    def get_alert_prompt(self):
        return self.prompt_sections.get("ALERT", "") or \
            self.prompt_sections.get("SUMMARY", "")

    def get_summary_prompt(self):
        """Summary instructions; the MEMORY section is appended only while the
        memory profiler is enabled, so one file serves both configurations."""
        base = self.prompt_sections.get("SUMMARY", "") or \
            self.prompt_sections.get("ALERT", "")
        if self.get_setting("MEMORY_PROFILER", int) == 1:
            mem = self.prompt_sections.get("MEMORY", "")
            if mem and "{LONG_TERM_MEMORY}" not in base:
                base = (base + "\n\n" + mem).strip()
        return base

    def load_prompt_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Prompt File", "",
            "Text Files (*.txt);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception as exc:
            QMessageBox.warning(self, "Load failed", str(exc))
            return
        sections = parse_prompt_sections(text)
        names = ", ".join(sorted(sections.keys())) if sections else "whole-file"
        reply = QMessageBox.question(
            self, "Use this prompt file?",
            "Detected sections: %s\n\n%s\n\nUse it for both alerts and summaries?"
            % (names, path),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return
        self.set_prompt_file(path)
        self.settings["PROMPT_FILE"] = path
        self.save_app_state()
        self.thread_safe_log("Prompt file set: %s (sections: %s)"
                             % (path, names))

    def load_prompt_text(self, path, fallback=""):
        try:
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    return fh.read()
        except Exception:
            pass
        return fallback



    ROLLING_FILE_MAX_BYTES = 16 * 1024 * 1024     # hard cap on disk usage

    def append_to_rolling_file(self, text):
        try:
            with open(ROLLING_FILE, "a", encoding="utf-8") as fh:
                fh.write(text + "\n\n")
            # keep the file bounded when bulk analysis is off or rarely run;
            # only the newest content matters for trend reports
            if os.path.getsize(ROLLING_FILE) > self.ROLLING_FILE_MAX_BYTES:
                with open(ROLLING_FILE, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(-self.ROLLING_FILE_MAX_BYTES // 2, os.SEEK_END)
                    tail = fh.read()
                cut = tail.find("\n\nTitle:")    # resume on an entry boundary
                if cut != -1:
                    tail = tail[cut + 2:]
                with open(ROLLING_FILE, "w", encoding="utf-8") as fh:
                    fh.write(tail)
                self.thread_safe_log("Rolling file trimmed to stay under its size cap.")
        except Exception as exc:
            self.thread_safe_log("Rolling file write failed: %s" % exc)

    def append_to_memory_short_term(self, text):
        try:
            with open(MEMORY_SHORT_TERM_FILE, "a", encoding="utf-8") as fh:
                fh.write("[%s]\n%s\n\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
        except Exception as exc:
            self.thread_safe_log("Short term memory write failed: %s" % exc)

    def load_long_term_memory(self):
        try:
            if os.path.exists(MEMORY_LONG_TERM_FILE):
                with open(MEMORY_LONG_TERM_FILE, "r", encoding="utf-8") as fh:
                    return fh.read().strip()
            return ""
        except Exception as exc:
            self.thread_safe_log("Failed to load long-term memory: %s" % exc)
            return ""

    def truncate_to_token_limit(self, text, max_tokens):
        return (text or "")[: int(max_tokens) * 4]

    def split_short_term_memory_chunks(self, text, max_tokens):
        max_chars = int(max_tokens) * 4
        text = (text or "").strip()
        if not text:
            return []
        entries = [p.strip() for p in re.split(
            r"(?=^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]\s*$)", text, flags=re.MULTILINE) if p.strip()]
        if not entries:
            entries = [text]
        chunks, current_parts, current_chars = [], [], 0

        def flush():
            nonlocal current_parts, current_chars
            if current_parts:
                chunks.append("\n\n".join(current_parts).strip())
            current_parts, current_chars = [], 0

        for entry in entries:
            e_len = len(entry)
            if e_len <= max_chars:
                if current_parts and current_chars + 2 + e_len > max_chars:
                    flush()
                current_parts.append(entry)
                current_chars += (2 if current_chars else 0) + e_len
            else:
                flush()
                for offset in range(0, e_len, max_chars):
                    piece = entry[offset:offset + max_chars].strip()
                    if piece:
                        chunks.append(piece)
        flush()
        return chunks

    def process_memory_profiler_if_ready(self):
        try:
            if self._shutting_down or self.get_setting("MEMORY_PROFILER", int) != 1:
                return None
            now = time.time()
            if now - self.memory_profiler_start_time < MEMORY_PROFILER_INTERVAL:
                return None

            st_text, lt_text = "", ""
            if os.path.exists(MEMORY_SHORT_TERM_FILE):
                with open(MEMORY_SHORT_TERM_FILE, "r", encoding="utf-8") as fh:
                    st_text = fh.read().strip()
            if not st_text:
                self.memory_profiler_start_time = now
                return None
            if os.path.exists(MEMORY_LONG_TERM_FILE):
                with open(MEMORY_LONG_TERM_FILE, "r", encoding="utf-8") as fh:
                    lt_text = fh.read().strip()

            lt_lim = self.get_setting("MEMORY_LONG_TERM_MAX_TOKENS", int)
            st_chunk_lim = self.get_setting("MEMORY_SHORT_TERM_CHUNK_TOKENS", int)
            out_lim = self.get_setting("MEMORY_PROFILER_OUTPUT_TOKENS", int)

            working_memory = self.truncate_to_token_limit(lt_text, lt_lim)
            chunks = self.split_short_term_memory_chunks(st_text, st_chunk_lim)
            self.thread_safe_log("Memory profiler: %d chunk(s) to fold in." % len(chunks))

            for idx, chunk in enumerate(chunks, 1):
                prompt = ("Update long-term memory using these short-term entries. "
                          "Return only updated text.\n\nLT MEMORY:\n%s\n\nCHUNK:\n%s"
                          % (working_memory or "(empty)", chunk))
                reply = self.call_llm(prompt, out_lim)
                if not reply:
                    return None
                working_memory = self.truncate_to_token_limit(reply.strip(), lt_lim)

            with open(MEMORY_LONG_TERM_FILE + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(working_memory)
            os.replace(MEMORY_LONG_TERM_FILE + ".tmp", MEMORY_LONG_TERM_FILE)
            open(MEMORY_SHORT_TERM_FILE, "w", encoding="utf-8").close()
            self.memory_profiler_start_time = now
            self.thread_safe_log("Memory profiler: long-term memory updated.")
        except Exception as exc:
            self.thread_safe_log("Profiler error: %s" % exc)

    # the handoff index is the stable contract for external agents/scripts:
    # one JSON file in the report folder pointing at the newest artifacts.
    AGENT_HANDOFF_NAME = "agent_handoff.json"

    def _write_agent_handoff(self, fpath, report_type):
        """Refresh agent_handoff.json after every saved report so a local AI
        agent or script can poll one small file instead of parsing folders."""
        try:
            out_dir = os.path.dirname(fpath)
            handoff = {
                "app": "ShunyaNet Sentinel",
                "version": APP_VERSION,
                "updated": datetime.now().isoformat(timespec="seconds"),
                "latest_report_type": report_type,
                "latest_report_path": fpath,
                "latest_report_mtime": os.path.getmtime(fpath),
                "report_dir": out_dir,
                "memory_long_term": MEMORY_LONG_TERM_FILE
                if os.path.exists(MEMORY_LONG_TERM_FILE) else None,
                "memory_short_term": MEMORY_SHORT_TERM_FILE
                if os.path.exists(MEMORY_SHORT_TERM_FILE) else None,
                "rolling_feed": ROLLING_FILE
                if os.path.exists(ROLLING_FILE) else None,
            }
            tmp = os.path.join(out_dir, self.AGENT_HANDOFF_NAME + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(handoff, fh, indent=2)
            os.replace(tmp, os.path.join(out_dir, self.AGENT_HANDOFF_NAME))
        except Exception as exc:
            self.thread_safe_log("Handoff index write failed: %s" % exc)

    def write_report_output(self, text, report_type="REPORT", add_to_memory=False):
        try:
            out_dir = self.get_setting("REPORT_OUTPUT_DIR") or DEFAULT_REPORT_OUTPUT_DIR
            os.makedirs(out_dir, exist_ok=True)
            base = "%s_%s" % (report_type, datetime.now().strftime("%H%M_%d-%m-%y"))
            n = 1
            while True:
                fpath = os.path.join(out_dir, "%s_%d.txt" % (base, n))
                if not os.path.exists(fpath):
                    break
                n += 1
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(text)
            self._write_agent_handoff(fpath, report_type)
            if add_to_memory and self.get_setting("MEMORY_PROFILER", int) == 1:
                self.append_to_memory_short_term(text)
        except Exception as exc:
            self.thread_safe_log("Output write failed: %s" % exc)

    # ================================================================== #
    # LLM / RSS pipeline (unchanged logic)
    # ================================================================== #

    def call_llm(self, prompt, max_tokens):
        provider = self.get_setting("LLM_PROVIDER")
        base_url = self.get_setting("LLM_BASE_URL").rstrip("/")
        model = self.get_setting("LLM_MODEL")
        api_key = self.get_setting("LLM_API_KEY")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer %s" % api_key
        payload = {"model": model,
                   "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": max_tokens}
        try:
            url = chat_completions_url(provider, base_url)
            timeout = self.get_setting("LLM_TIMEOUT", int) or 900
            last_exc = None
            for attempt in (1, 2):
                try:
                    resp = requests.post(url, headers=headers, json=payload,
                                         timeout=timeout)
                    break
                except requests.exceptions.RequestException as exc:
                    last_exc = exc
                    if attempt == 1 and not self._shutting_down:
                        self.thread_safe_log("LLM connection issue, retrying once: %s" % exc)
                        time.sleep(3)
            else:
                raise last_exc
            if resp.status_code != 200:
                self.thread_safe_log("LLM error %s: %s"
                                     % (resp.status_code, resp.text[:400]))
                return None
            data = resp.json()
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                self.thread_safe_log("LLM reply had unexpected shape: %s"
                                     % str(data)[:300])
                return None
        except Exception as exc:
            self.thread_safe_log("LLM call failed: %s" % exc)
            return None

    @staticmethod
    def _clean_summary(entry):
        """Strip HTML from a feed entry summary (feeds often embed markup)."""
        raw = getattr(entry, "summary", "") or ""
        if "<" in raw and ">" in raw:
            raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
            raw = re.sub(r"</p>", "\n", raw, flags=re.I)
            raw = re.sub(r"<[^>]+>", "", raw)
            raw = html.unescape(raw)
            raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
        return raw or "N/A"

    def fetch_feed(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ShunyaNetSentinel/1.0"}
            r = requests.get(url, headers=headers, timeout=20)
            parsed = feedparser.parse(r.content)
            if getattr(parsed, "bozo", 0) and not getattr(parsed, "entries", None):
                return None
            return parsed
        except Exception:
            return None

    def _remember_guid(self, guid):
        self.seen_guids.add(guid)
        self._seen_order.append(guid)
        while len(self._seen_order) > MAX_SEEN_GUIDS:
            self.seen_guids.discard(self._seen_order.pop(0))

    def fetch_rss_latest(self):
        """Pull all feeds in parallel (bounded), newest-first within each feed.

        Requests to the SAME host are serialized with a per-host lock: sites
        like reddit rate-limit aggressively and return 429 when hit by several
        threads at once, while cross-host traffic still runs in parallel."""
        items = []
        limit = self.get_setting("ITEMS_PER_FEED", int)
        urls = list(self.feeds)
        # shuffle so one rate-limiting site (reddit etc.) is not always hit first
        random.shuffle(urls)
        host_locks = {}
        locks_guard = threading.Lock()

        def fetch_one(url):
            try:
                host = url.split("/")[2]
            except IndexError:
                host = url
            with locks_guard:
                lock = host_locks.get(host)
                if lock is None:
                    lock = threading.Lock()
                    host_locks[host] = lock
            with lock:
                return self.fetch_feed(url)

        unreachable = 0
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(urls)))) as pool:
            results = list(pool.map(lambda u: (u, fetch_one(u)), urls))
        for url, feed in results:
            if not feed:
                unreachable += 1
                self.thread_safe_log("Source unreachable: %s" % url)
                continue
            for entry in getattr(feed, "entries", [])[:limit]:
                guid = getattr(entry, "id", None) or getattr(entry, "link", None)
                if guid and guid in self.seen_guids:
                    continue
                if guid:
                    self._remember_guid(guid)
                items.append("Title: %s\nPublished: %s\nSummary: %s\nLink: %s"
                             % (getattr(entry, "title", "N/A"),
                                getattr(entry, "published", None) or "N/A",
                                self._clean_summary(entry),
                                getattr(entry, "link", "")))
        if unreachable:
            self.thread_safe_log("%d of %d sources unreachable this cycle."
                                 % (unreachable, len(urls)))
        return "\n\n".join(items)

    def truncate_tokens(self, text, bulk=False):
        lim = self.get_setting("MAX_INPUT_TOKENS_BULK" if bulk else "MAX_INPUT_TOKENS", int)
        return text[: lim * 4]

    def is_no_alert_response(self, text):
        norm = (text or "").upper()
        return "ALERT: NO ALERT" in norm or "REPORT: *** NO HIT" in norm

    def _show_desktop_notification(self, title, body):
        """Native OS notification via the tray icon (best effort)."""
        tray = getattr(self, "_tray", None)
        if tray is None:
            return
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            tray.showMessage(title, body[:200], QSystemTrayIcon.Information, 8000)
        except Exception:
            pass

    def _ensure_tray(self):
        """Create the system tray icon once, if the platform has one."""
        if getattr(self, "_tray", None) is not None:
            return self._tray
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return None
            icon_path = resource_path(os.path.join("assets", "app_icon.png"))
            icon = QIcon(icon_path) if os.path.exists(icon_path) else self.windowIcon()
            tray = QSystemTrayIcon(icon, self)
            tray.setToolTip("ShunyaNet Sentinel")
            tray.activated.connect(self._tray_activated)
            tray.show()
            self._tray = tray
            return tray
        except Exception:
            return None

    def _tray_activated(self, reason):
        from PySide6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.showNormal()
                self.raise_()
                self.activateWindow()

    def publish_output(self, text, report_type, send_to_slack=True, add_to_memory=False):
        if not text:
            return
        self.thread_safe_reply(text)
        if send_to_slack:
            want_summaries = self.get_setting("NOTIFY_SUMMARIES", int) == 1
            if report_type != "SUMMARY" or want_summaries:
                settings_snapshot = dict(self.settings)

                def work():
                    return dispatch_notifications(settings_snapshot, text, report_type)

                def done(results):
                    for channel, ok, msg in (results or []):
                        if ok:
                            self.thread_safe_log("Notified %s." % channel)
                        else:
                            self.thread_safe_log("%s notify failed: %s" % (channel, msg))

                self.run_background(work, done)
                # desktop popup (skipped when only summaries are being muted and
                # this is a summary; alerts always pop up while the toggle is on)
                if self.get_setting("NOTIFY_DESKTOP", int) == 1:
                    title = "SENTINEL %s" % (report_type or "REPORT").upper()
                    self.sig_notify_desktop.emit(title, _first_line(text))
        self.write_report_output(text, report_type, add_to_memory)
        self.sig_feed.emit(text)

    def fetch_and_send(self):
        try:
            if self._shutting_down:
                return
            self.thread_safe_log("Fetching %d sources\u2026" % len(self.feeds))
            text = self.fetch_rss_latest()
            if not text:
                self.thread_safe_log("No new items this cycle.")
                return
            self.thread_safe_log("%d new item(s) collected." % text.count("Title: "))

            # collection is unconditional (bounded, cheap): it keeps the raw
            # material available for 'Analyze Now' even when automatic
            # summary reports are switched off. The toggle governs reports.
            self.append_to_rolling_file(text)
            self._maybe_run_bulk_after_fetch()

            if self.get_setting("ALERTS_ENABLED", int) != 1:
                return

            alert_input = self.truncate_tokens(text, False)
            use_chunked = self.get_setting("USE_CHUNKED_MODE", int) == 1
            c_tokens = self.get_setting("CHUNK_TOKENS", int) or 2000
            c_size = c_tokens * 4          # token -> char budget (same rule as trims)
            chunks = ([alert_input] if not use_chunked
                      else [alert_input[i:i + c_size] for i in range(0, len(alert_input), c_size)])
            lt_mem = self.load_long_term_memory()
            hits = 0
            for chunk in chunks:
                prompt = render_prompt(self.get_alert_prompt(), {
                    "CHUNK": chunk,
                    "TOPICS": self.get_topics_string(),
                    "LONG_TERM_MEMORY": lt_mem,
                })
                reply = self.call_llm(prompt, self.get_setting("MAX_OUTPUT_TOKENS", int))
                if reply and not self.is_no_alert_response(reply):
                    hits += 1
                    self.publish_output(reply, "ALERT")
            self.thread_safe_log("Alert pass done: %d hit(s) across %d chunk(s)."
                                 % (hits, len(chunks)))
        except Exception as exc:
            self.thread_safe_log("Fetch error: %s" % exc)

    def perform_bulk_analysis_if_ready(self, force=False):
        try:
            if self._shutting_down:
                return None
            now = time.time()
            window = self.get_setting("ANALYSIS_WINDOW", int)
            # 'force' (Analyze Now) bypasses the window check; automatic runs
            # only proceed once ANALYSIS_WINDOW has elapsed.
            if not force and now - self.rolling_file_start_time < window:
                return None
            if not os.path.exists(ROLLING_FILE):
                return None
            with open(ROLLING_FILE, "r", encoding="utf-8") as fh:
                full_text = fh.read().strip()
            if not full_text:
                self.rolling_file_start_time = now
                return None

            self.thread_safe_log("Bulk analysis: %d chars in rolling file." % len(full_text))
            prompt = render_prompt(self.get_summary_prompt(), {
                "CHUNK": self.truncate_tokens(full_text, True),
                "TOPICS": self.get_topics_string(),
                "LONG_TERM_MEMORY": self.load_long_term_memory(),
            })
            reply = self.call_llm(prompt, self.get_setting("MAX_OUTPUT_TOKENS_BULK", int))
            if reply:
                self.publish_output(reply, "SUMMARY", add_to_memory=True)
                open(ROLLING_FILE, "w", encoding="utf-8").close()
            self.rolling_file_start_time = now
            self.next_bulk_ts = now + window
        except Exception as exc:
            self.thread_safe_log("Bulk error: %s" % exc)

    # ================================================================== #
    # Persistence (settings, topics, window layout)
    # ================================================================== #

    def save_app_state(self):
        try:
            state = {
                "active_profile": self.profile_select.currentText(),
                "topic_profile": self.get_topic_profile(),
                "settings": self.settings,
                "feeds": self.feeds,
                "ui": self._capture_ui_state(),
            }
            with open(STATE_FILE, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
        except Exception as exc:
            print("State save failed: %s" % exc)

    def _capture_ui_state(self):
        ui = {}
        try:
            geo = self.saveGeometry()
            ui["geometry"] = bytes(geo.toBase64()).decode("ascii")
        except Exception:
            pass
        try:
            state = self.saveState()
            ui["window_state"] = bytes(state.toBase64()).decode("ascii")
        except Exception:
            pass
        try:
            ui["center_splitter"] = list(map(int, self.center_splitter.sizes()))
        except Exception:
            pass
        ui["ui_font_pt"] = self.ui_font_pt
        return ui

    def _sanitize_report_dir(self, path_str):
        """A report dir saved on another OS is meaningless here; fall back."""
        path_str = (path_str or "").strip()
        if not path_str:
            return DEFAULT_REPORT_OUTPUT_DIR
        looks_windows = bool(re.match(r"^[A-Za-z]:[\\/]", path_str)) or "\\" in path_str
        if os.name != "nt" and looks_windows:
            return DEFAULT_REPORT_OUTPUT_DIR
        if os.name == "nt" and path_str.startswith("/") and not looks_windows:
            return DEFAULT_REPORT_OUTPUT_DIR
        return path_str

    @staticmethod
    def _migrate_settings(settings):
        """Bring an old app_state.json settings dict up to the current model."""
        changed = False
        prov = settings.get("LLM_PROVIDER")
        if prov in LEGACY_PROVIDER_MAP:
            settings["LLM_PROVIDER"] = LEGACY_PROVIDER_MAP[prov]
            changed = True
        if "CHUNK_SIZE" in settings:
            # character-based chunking -> token-based (same 4 chars/token rule)
            try:
                settings["CHUNK_TOKENS"] = max(200, int(settings["CHUNK_SIZE"]) // 4)
            except (TypeError, ValueError):
                pass
            del settings["CHUNK_SIZE"]
            changed = True
        return changed

    def load_app_state(self):
        try:
            if not os.path.exists(STATE_FILE):
                return
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            saved = dict(DEFAULT_SETTINGS)
            raw = state.get("settings", {}) or {}
            saved.update(raw)
            self._migrate_settings(saved)
            # v3 merged SUMMARY_ENABLED + BULK_ANALYSIS into SUMMARIES_ENABLED;
            # honour either legacy key from older state files (on = either was on)
            if "SUMMARIES_ENABLED" not in raw:
                legacy_on = any(str(raw.get(k, "")) == "1"
                                for k in ("SUMMARY_ENABLED", "BULK_ANALYSIS"))
                saved["SUMMARIES_ENABLED"] = "1" if legacy_on else "0"
            self.settings = saved
            self.settings["REPORT_OUTPUT_DIR"] = self._sanitize_report_dir(
                saved.get("REPORT_OUTPUT_DIR", ""))
            self.set_topic_profile(state.get("topic_profile"))
            saved_feeds = state.get("feeds")
            if isinstance(saved_feeds, list):
                cleaned = [str(u).strip() for u in saved_feeds if str(u).strip()]
                if cleaned:
                    self.feeds = cleaned
                    self._state_has_feeds = True
            name = state.get("active_profile")
            if name in self.profiles:
                self.profile_select.setCurrentText(name)
            self.ui_font_pt = int(state.get("ui", {}).get("ui_font_pt", DEFAULT_UI_FONT_PT))
        except Exception as exc:
            print("State load failed: %s" % exc)

    def _default_window_rect(self):
        """A sane startup size that always fits the primary screen."""
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            w = min(1320, int(screen.width() * 0.85))
            h = min(840, int(screen.height() * 0.85))
            return screen.x() + (screen.width() - w) // 2, \
                   screen.y() + (screen.height() - h) // 2, w, h
        except Exception:
            return 0, 0, 1320, 840

    def _apply_default_geometry(self):
        x, y, w, h = self._default_window_rect()
        self.resize(w, h)
        self.move(x, y)

    def _visible_on_some_screen(self, rect):
        try:
            for screen in QApplication.screens():
                if screen.availableGeometry().intersects(rect):
                    return True
        except Exception:
            return True
        return False

    def restore_ui_state(self):
        try:
            if not os.path.exists(STATE_FILE):
                self._apply_default_geometry()
                return
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                ui = (json.load(fh) or {}).get("ui", {})
            geo = ui.get("geometry")
            restored = False
            if geo:
                restored = bool(self.restoreGeometry(
                    QByteArray.fromBase64(geo.encode("ascii"))))
            if not restored or not self._visible_on_some_screen(self.frameGeometry()):
                # saved layout referenced a screen that no longer exists
                self._apply_default_geometry()
            win_state = ui.get("window_state")
            if win_state:
                self.restoreState(QByteArray.fromBase64(win_state.encode("ascii")))
            sizes = ui.get("center_splitter")
            if sizes and len(sizes) == 2:
                self.center_splitter.setSizes([int(x) for x in sizes])
        except Exception as exc:
            print("UI state restore failed: %s" % exc)
            self._apply_default_geometry()

    def reset_ui_state(self):
        """Forget the saved WINDOW LAYOUT only; settings and topics are kept."""
        try:
            state = {}
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as fh:
                    state = json.load(fh) or {}
            state["ui"] = {"ui_font_pt": DEFAULT_UI_FONT_PT}
            with open(STATE_FILE, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
        except Exception:
            pass
        self._apply_default_geometry()
        self.targeting_dock.show()
        self.feed_dock.show()
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.targeting_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.feed_dock)
        self.center_splitter.setSizes([560, 300])
        self.zoom_ui(0)
        self.statusBar().showMessage("Layout reset. Your settings and topics were kept.",
                                     6000)

    def closeEvent(self, event):
        # Daemon task threads are deliberately NOT joined here: a fetch can sit
        # in an LLM request for minutes, and blocking window close on it is
        # exactly what produced the old "app hangs / ghost error on quit"
        # reports. _shutting_down makes every worker path exit at its next
        # checkpoint instead, and all signals are guarded.
        self._shutting_down = True
        try:
            self.status_tick.stop()
            self.auto_fetch_timer.stop()
            self.bulk_timer.stop()
            self.memory_timer.stop()
            tray = getattr(self, "_tray", None)
            if tray is not None:
                tray.hide()
            self.save_app_state()
        except Exception:
            pass
        event.accept()


def clean_qt_shutdown():
    try:
        app = QApplication.instance()
        if app:
            app.quit()
    except Exception:
        pass


atexit.register(clean_qt_shutdown)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ShunyaNet Sentinel")
    app.setOrganizationName("ShunyaNet")

    # resolve a monospace font that actually exists on this OS
    global BASE_FONT_FAMILY
    BASE_FONT_FAMILY = _pick_monospace_font()

    font = QFont(BASE_FONT_FAMILY, DEFAULT_UI_FONT_PT)
    font.setStyleHint(QFont.Monospace)
    app.setFont(font)
    app.setStyleSheet(STYLE_SHEET)

    window = SentinelWindow()
    window.show()
    exit_code = app.exec()
    # clean teardown on every platform (was the source of close-time tracebacks)
    try:
        window.deleteLater()
    except Exception:
        pass
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
