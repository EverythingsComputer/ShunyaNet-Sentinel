import sys
import feedparser
import requests
import time
import json
import os
import random
import atexit
from datetime import datetime, timezone
from dateutil import parser as dateparser
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QLineEdit,
    QScrollArea, QComboBox, QInputDialog, QFileDialog, QSplitter, QDialog
)
from PySide6.QtCore import Qt, QMetaObject, Q_ARG, QTimer, QThread, Signal, QEvent, QEasingCurve, QPropertyAnimation, QObject, Property
from PySide6.QtGui import QIcon, QPixmap, QPainter, QFont, QColor, QFontMetrics, QPalette, QTransform, QFontDatabase
from concurrent.futures import ThreadPoolExecutor



# Determine directory where app should read/write user-editable files
if getattr(sys, 'frozen', False):
    # When frozen on macOS, sys.executable is:
    #   <app>/Contents/MacOS/<exe>
    exe_dir = os.path.dirname(sys.executable)  # .../Contents/MacOS
    app_bundle_dir = os.path.abspath(os.path.join(exe_dir, "..", ".."))  # .../MyApp.app
    APP_DIR = os.path.abspath(os.path.join(app_bundle_dir, ".."))  # parent folder containing the .app
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# --------------------------
# Helper to load bundled files
# --------------------------
def resource_path(relative_path):
    """Get the absolute path to a resource, works for dev and PyInstaller"""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)

# --------------------------
# Load all files in assets
# --------------------------
def load_assets():
    assets_dir = resource_path("assets")
    assets_files = {}

    for root, _, files in os.walk(assets_dir):
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, assets_dir)
            assets_files[rel_path] = full_path

    return assets_files

# --------------------------
# Determine writable profile path
# --------------------------
if getattr(sys, 'frozen', False):
    # Running as PyInstaller exe
    base_dir = os.path.dirname(sys.executable)
else:
    # Running as script
    base_dir = os.path.dirname(__file__)


# Load assets once
assets = load_assets()

# Example Default Feeds - this is replaced with the feeds in your .txt list, when it is loaded. NRC and FEMA lists
FEEDS = [
    "http://rss.cnn.com/rss/cnn_topstories.rss",
    "http://rss.cnn.com/rss/cnn_world.rss",
    "http://rss.cnn.com/rss/cnn_us.rss",
    "http://feeds.bbci.co.uk/news/rss.xml",
    "http://www.euronews.com/rss",
    "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml",
    "https://www.spc.noaa.gov/products/spcrss.xml",
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.atom"
]

#These some of these values are immediately overwritten by the app_state.json file, which offers improved default values
LMSTUDIO_URL = "YOUR URL HERE/v1/chat/completions" #app_state overwrites 
MAX_TOKENS = 3000 #app_state overwrites 
MAX_TOKENS_BULK = 4000 #app_state overwrites 
FETCH_INTERVAL = 600 #app_state overwrites 
ITEMS_PER_FEED = 50 #app_state overwrites 
MAX_TOPICS = 10
MAX_PROFILES = 20
ROLLING_FILE = os.path.join(APP_DIR, "rolling_rss.txt")
PROFILE_FILE = os.path.join(APP_DIR, "topic_profiles.json")
SLACK_WEBHOOK_URL = "YOUR SLACK WEBHOOK URL HERE" #app_state overwrites 
STATE_FILE = os.path.join(APP_DIR, "app_state.json")



# -------- Thread worker for background fetch/send --------
class FetchSendWorker(QThread):
    log_signal = Signal(str)
    reply_signal = Signal(str)
    history_signal = Signal(str)

    def __init__(self, app_instance):
        super().__init__()
        self.app_instance = app_instance

    def run(self):
        self.app_instance.fetch_and_send()


# Worker for analysis
class AnalysisWorker(QThread):
    log_signal = Signal(str)
    reply_signal = Signal(str)

    def __init__(self, app_instance):
        super().__init__()
        self.app_instance = app_instance

    def run(self):
        self.app_instance.perform_bulk_analysis_if_ready()


# -------- Green rain overlay (transparent, non-blocking) --------
class GreenRainOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        # Use cross-platform monospace font
        self.font = QFont("Courier New", 14)

    
        self.char_width = QFontMetrics(self.font).horizontalAdvance('M')
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_rain)
        self.timer.start(50)  # ~20 FPS

        self.columns = []
        self.init_columns()

    def init_columns(self):
        parent = self.parent() or self
        width = max(1, parent.width())
        self.char_width = QFontMetrics(self.font).horizontalAdvance('M') or 12
        cols = max(2, width // self.char_width)
        self.columns = []
        for i in range(cols):
            x = i * self.char_width
            y = random.randint(-800, 0)
            speed = random.randint(4, 14)
            length = random.randint(6, 20)
            chars = [self.random_char() for _ in range(length)]
            self.columns.append({
                "x": x,
                "y": y,
                "speed": speed,
                "length": length,
                "chars": chars,
            })

    def random_char(self):
        if random.random() < 0.25:
            return chr(random.randint(0x30A0, 0x30FF))
        else:
            return chr(random.randint(33, 126))

    def resizeEvent(self, event):
        self.init_columns()
        return super().resizeEvent(event)

    def update_rain(self):
        h = max(1, self.height())
        for col in self.columns:
            col["y"] += col["speed"]
            if random.random() < 0.2:
                col["chars"] = [self.random_char() if random.random() < 0.15 else c for c in col["chars"]]
            if col["y"] - col["length"] * QFontMetrics(self.font).height() > h:
                col["y"] = random.randint(-600, 0)
                col["speed"] = random.randint(4, 14)
                col["length"] = random.randint(6, 20)
                col["chars"] = [self.random_char() for _ in range(col["length"])]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font)
        for col in self.columns:
            x = col["x"]
            y = col["y"]
            length = col["length"]
            chars = col["chars"]
            head_color = QColor(180, 255, 150, 220)
            painter.setPen(head_color)
            head_char = chars[-1] if chars else self.random_char()
            painter.drawText(x, y, head_char)
            for i in range(length - 1):
                idx = (len(chars) - 2) - i
                if idx < 0:
                    break
                ch = chars[idx]
                alpha = int(220 * (1 - (i + 1) / (length + 1)))
                alpha = max(20, alpha)
                painter.setPen(QColor(0, 255, 65, alpha))
                char_y = y - (i + 1) * QFontMetrics(self.font).height()
                painter.drawText(x, char_y, ch)

# -------- Custom scalable QLabel --------
class ScalableLabel(QLabel):
    def __init__(self, pixmap):
        super().__init__()
        self._scale = 1.0
        self._original_pix = pixmap
        self.setAlignment(Qt.AlignCenter)
        self.updatePixmap()

    def updatePixmap(self):
        size = self._original_pix.size()
        new_w = int(size.width() * self._scale)
        new_h = int(size.height() * self._scale)
        scaled_pix = self._original_pix.scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        canvas = QPixmap(self.size())
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        x = (canvas.width() - scaled_pix.width()) // 2
        y = (canvas.height() - scaled_pix.height()) // 2
        painter.drawPixmap(x, y, scaled_pix)
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


class RSSApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ShunyaNet Sentinel")
        self.seen_guids = set()
        self.profiles = {}
        self.feeds = FEEDS.copy()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.active_threads = []
        self._shutting_down = False

        # ================================================================
        # SETTINGS DICTIONARY WITH DEFAULTS
        # ================================================================
        self.settings = {
            "LMSTUDIO_URL": LMSTUDIO_URL,
            "SLACK_WEBHOOK_URL": SLACK_WEBHOOK_URL,
            "MAX_TOKENS": MAX_TOKENS,
            "MAX_TOKENS_BULK": MAX_TOKENS_BULK,
            "FETCH_INTERVAL": FETCH_INTERVAL,
            "ITEMS_PER_FEED": ITEMS_PER_FEED,
            "USE_CHUNKED_MODE": "1",
            "CHUNK_SIZE": 4000,
            "WRITE_TO_FILE": "1",
            "ANALYSIS_WINDOW": 3600,
            "BULK_ANALYSIS": "0"
        }

        self.settings_fields = {}  # for pop-up editing
        self.rolling_file_start_time = time.time()


        # ================================================================
        # PROMPT HANDLING
        # ================================================================
        self.prompt_file = "default_prompt.txt"
        self.base_prompt = ""

        # Load default prompt if available
        if os.path.exists(self.prompt_file):
            with open(self.prompt_file, "r", encoding="utf-8") as f:
                self.base_prompt = f.read()
        else:
            self.base_prompt = "No prompt loaded."


        # ================================================================
        # MAIN SPLITTER
        # ================================================================
        main_splitter = QSplitter(Qt.Horizontal)

        # Create main layout for the window
        main_layout = QVBoxLayout(self)
        self.setLayout(main_layout)

        # Middle layout does not exist yet, so add splitter directly to main layout
        main_layout.addWidget(main_splitter)

        # ================================================================
        # LEFT PANEL — topics, profiles, sources, settings
        # ================================================================
        left_widget = QWidget()
        left_panel = QVBoxLayout(left_widget)
        left_panel.setSpacing(20)
        left_panel.setContentsMargins(6, 6, 6, 6)
        main_splitter.addWidget(left_widget)
        
        # --- MATRIX RAIN OVERLAY ---
        self.green_overlay = GreenRainOverlay(self)
        self.green_overlay.resize(self.width(), self.height())
        self.green_overlay.hide()
        self.installEventFilter(self)

        # Topic Profile Manager
        left_panel.addWidget(QLabel("TOPIC PROFILE MANAGER:"))
        self.profile_select = QComboBox()
        left_panel.addWidget(self.profile_select)

        profile_buttons_layout = QHBoxLayout()
        profile_buttons_layout.setSpacing(3)
        self.save_profile_btn = QPushButton("Save")
        self.load_profile_btn = QPushButton("Load")
        self.delete_profile_btn = QPushButton("Delete")
        profile_buttons_layout.addWidget(self.save_profile_btn)
        profile_buttons_layout.addWidget(self.load_profile_btn)
        profile_buttons_layout.addWidget(self.delete_profile_btn)
        left_panel.addLayout(profile_buttons_layout)

        # Topics
        left_panel.addWidget(QLabel("TOPIC LIST:"))
        self.topics_container = QVBoxLayout()
        self.topics_container.setSpacing(2)
        self.topic_entries = []
        topic_scroll = QScrollArea()
        topic_widget = QWidget()
        topic_widget.setLayout(self.topics_container)
        topic_scroll.setWidget(topic_widget)
        topic_scroll.setWidgetResizable(True)
        topic_scroll.setFixedHeight(180)
        left_panel.addWidget(topic_scroll)

        self.add_topic_button = QPushButton("Add Topic")
        self.add_topic_button.clicked.connect(lambda: self.add_topic_field(""))
        left_panel.addWidget(self.add_topic_button)

        for t in ["Venezuela", "regional or national air traffic disruption", "transcontinental internet outage"]:
            self.add_topic_field(t)

        # Fetch button
        self.send_button = QPushButton("Fetch / Send")
        self.send_button.clicked.connect(self.start_fetch_thread)
        left_panel.addWidget(self.send_button)

        # Additional Settings
        self.additional_settings_btn = QPushButton("Additional Settings")
        self.additional_settings_btn.clicked.connect(self.open_settings_window)
        left_panel.addWidget(self.additional_settings_btn)

        # Data source manager
        left_panel.addWidget(QLabel("DATA SOURCE MANAGER:"))
        data_buttons_layout = QHBoxLayout()
        data_buttons_layout.setSpacing(3)
        self.load_sources_btn = QPushButton("Load Data Source File")
        self.load_sources_btn.clicked.connect(self.load_data_sources)
        data_buttons_layout.addWidget(self.load_sources_btn)
        left_panel.addLayout(data_buttons_layout)

        # Prompt Loader
        self.load_prompt_btn = QPushButton("Load Prompt File")
        self.load_prompt_btn.clicked.connect(self.load_prompt_file)
        left_panel.addWidget(self.load_prompt_btn)


        # ---------------- LOGO BUTTON ----------------
        logo_btn = QPushButton()
        logo_btn.setFixedSize(200, 200)
        logo_btn.setFlat(True)
        logo_btn.setFocusPolicy(Qt.NoFocus)
        logo_btn.setStyleSheet("""
            QPushButton {
                border: none;
                background-color: #000000;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        logo_btn.clicked.connect(self.start_fetch_thread)

        # Center the label inside the button
        logo_layout = QVBoxLayout(logo_btn)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setAlignment(Qt.AlignCenter)

        file_path = resource_path(os.path.join("assets", "logo_icon.png"))
        if os.path.exists(file_path):
            pix = QPixmap(file_path)
            if not pix.isNull():
                pix = pix.scaled(175, 175, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                logo_label = ScalableLabel(pix)
                logo_label.setStyleSheet("border: none;")
                logo_label.setAlignment(Qt.AlignCenter)
        else:
            logo_label = QLabel("No image found")
            logo_label.setAlignment(Qt.AlignCenter)
            logo_label.setStyleSheet("border: none;")

        logo_layout.addWidget(logo_label)

        # ---------------- CENTER BUTTON IN LEFT PANEL ----------------
        # Create a container widget to center the button
        center_container = QWidget()
        center_layout = QVBoxLayout(center_container)
        center_layout.addStretch(1)
        center_layout.addWidget(logo_btn, 0, Qt.AlignCenter)
        center_layout.addStretch(1)

        # Add container to left panel (fills available space)
        left_panel.addWidget(center_container)


        # -------- Animation helper --------
        def animate_label(scale):
            anim = QPropertyAnimation(logo_label, b"scale")
            anim.setDuration(150)
            anim.setEndValue(scale)
            anim.setEasingCurve(QEasingCurve.InOutQuad)
            anim.start()
            logo_label._anim = anim  # keep reference to avoid garbage collection

        def enterEvent(event):
            animate_label(1.1)
            return QPushButton.enterEvent(logo_btn, event)

        def leaveEvent(event):
            animate_label(1.0)
            return QPushButton.leaveEvent(logo_btn, event)

        def mousePressEvent(event):
            animate_label(0.95)
            return QPushButton.mousePressEvent(logo_btn, event)

        def mouseReleaseEvent(event):
            animate_label(1.1)
            return QPushButton.mouseReleaseEvent(logo_btn, event)

        logo_btn.enterEvent = enterEvent
        logo_btn.leaveEvent = leaveEvent
        logo_btn.mousePressEvent = mousePressEvent
        logo_btn.mouseReleaseEvent = mouseReleaseEvent



        # ================================================================
        # MIDDLE PANEL — Logs + Model Reply
        # ================================================================
        middle_widget = QWidget()
        middle_panel = QVBoxLayout(middle_widget)
        middle_panel.setSpacing(4)
        middle_panel.setContentsMargins(6, 6, 6, 6)
        self.middle_layout = middle_panel  # assign attribute
        main_splitter.addWidget(middle_widget)

        self.middle_layout.addWidget(QLabel("TERMINAL LOGS:"))
        self.log = QTextEdit(readOnly=True)
        # Optional: keep logs dark as well
        log_palette = self.log.palette()
        log_palette.setColor(QPalette.Base, QColor(0, 0, 0))        # black background
        log_palette.setColor(QPalette.Text, QColor(0, 255, 65))     # bright green text
        self.log.setPalette(log_palette)
        self.middle_layout.addWidget(self.log)

        self.middle_layout.addWidget(QLabel("LATEST REPORT:"))
        self.reply_box = QTextEdit(readOnly=True)
        # Apply palette so it doesn't turn white
        reply_palette = self.reply_box.palette()
        reply_palette.setColor(QPalette.Base, QColor(0, 25, 0))     # dark green background
        reply_palette.setColor(QPalette.Text, QColor(0, 255, 65))   # bright green text
        self.reply_box.setPalette(reply_palette)
        self.middle_layout.addWidget(self.reply_box)

                # rain toggle button
        self.rain_toggle = QPushButton("TOGGLE SCREEN SAVER")
        self.rain_toggle.setCheckable(True)
        self.rain_toggle.setChecked(False)
        self.rain_toggle.clicked.connect(self.toggle_rain)
        self.middle_layout.addWidget(self.rain_toggle, 0, Qt.AlignBottom)

        # Load profiles AFTER log exists
        self.load_profiles()
        self.update_profile_combobox()
        self.save_profile_btn.clicked.connect(self.save_current_profile)
        self.load_profile_btn.clicked.connect(self.load_selected_profile)
        self.delete_profile_btn.clicked.connect(self.delete_selected_profile)

        # ================================================================
        # RIGHT PANEL — Prior Replies
        # ================================================================
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 6, 6, 6)
        right_layout.setSpacing(4)
        main_splitter.addWidget(right_widget)

        right_layout.addWidget(QLabel("REPORT FEED:"))
        self.history_scroll = QScrollArea()
        self.history_widget = QWidget()
        self.history_layout = QVBoxLayout()
        self.history_widget.setLayout(self.history_layout)
        self.history_scroll.setWidget(self.history_widget)
        self.history_scroll.setWidgetResizable(True)
        right_layout.addWidget(self.history_scroll)


        #=======================================================
        # RESUME APP STATE
        #======================================================
        self.load_app_state()


        # ================================================================
        # TIMERS — use defaults from self.settings
        # ================================================================
        self.auto_fetch_timer = QTimer()
        self.auto_fetch_timer.timeout.connect(self.start_fetch_thread)
        self.auto_fetch_timer.start(self.settings["FETCH_INTERVAL"] * 1000)

        self.bulk_timer = QTimer()
        self.bulk_timer.setInterval(self.settings["ANALYSIS_WINDOW"] * 1000)
        self.bulk_timer.timeout.connect(self.perform_bulk_analysis_if_ready)
        self.bulk_timer.start()

        # Default splitter sizes
        main_splitter.setSizes([250, 450, 350])



    # ================================================================
    # SETTINGS POP-UP
    # ================================================================
    def open_settings_window(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Additional Settings")
        dlg.setMinimumWidth(400)

        layout = QVBoxLayout(dlg)
        self.settings_fields.clear()  # clear previous fields

        # Create editable fields
        for name, value in self.settings.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(name + ":"))
            edit = QLineEdit(str(value))
            row.addWidget(edit)
            layout.addLayout(row)
            self.settings_fields[name] = edit

        # Save and Close button
        close_btn = QPushButton("Save and Close")
        def save_and_close():
            for name, edit in self.settings_fields.items():
                val = edit.text()
                if name in ["FETCH_INTERVAL", "ANALYSIS_WINDOW", "MAX_TOKENS", "MAX_TOKENS_BULK", "ITEMS_PER_FEED", "CHUNK_SIZE"]:
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                self.settings[name] = val

            # Update timers only if they exist
            if hasattr(self, "auto_fetch_timer"):
                self.auto_fetch_timer.setInterval(self.settings["FETCH_INTERVAL"] * 1000)
            if hasattr(self, "bulk_timer"):
                self.bulk_timer.setInterval(self.settings["ANALYSIS_WINDOW"] * 1000)

            dlg.close()


        close_btn.clicked.connect(save_and_close)
        layout.addWidget(close_btn)
        dlg.exec()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and hasattr(self, "green_overlay"):
            self.green_overlay.resize(self.size())
        return super().eventFilter(obj, event)

    def toggle_rain(self):
        if self.rain_toggle.isChecked():
            self.green_overlay.show()
        else:
            self.green_overlay.hide()


    # ---------- Fetch thread starter ----------

    def start_fetch_thread(self):
        if self._shutting_down:
            return
        worker = FetchSendWorker(self)
        self.active_threads.append(worker)
        worker.log_signal.connect(self.thread_safe_log)
        worker.reply_signal.connect(self.thread_safe_reply)
        worker.history_signal.connect(self.add_to_history)
        worker.finished.connect(lambda: self.active_threads.remove(worker) if worker in self.active_threads else None)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    # ------------- Bulk Analysis Starter --------
    def start_bulk_analysis(self):
        if self.get_setting("BULK_ANALYSIS", int) != 1:
            return
        if self._shutting_down:
            return
        worker = AnalysisWorker(self)
        self.active_threads.append(worker)
        worker.log_signal.connect(self.thread_safe_log)
        worker.reply_signal.connect(self.thread_safe_reply)
        worker.finished.connect(lambda: self.active_threads.remove(worker) if worker in self.active_threads else None)
        worker.finished.connect(worker.deleteLater)
        worker.start()


    # ---------- Settings ----------
    def add_setting_field(self, name, default_value):
        hbox = QHBoxLayout()
        label = QLabel(name + ":")
        edit = QLineEdit(str(default_value))
        hbox.addWidget(label)
        hbox.addWidget(edit)
        self.settings_fields_layout.addLayout(hbox)  # use the layout we created
        self.settings_fields[name] = edit


    def toggle_additional_settings(self, checked):
        self.settings_container.setVisible(checked)

    def get_setting(self, name, type_cast=str):
        # If settings field does not yet exist (startup), fall back to the stored settings dict
        if name not in self.settings_fields:
            return type_cast(self.settings.get(name))

        # Otherwise read from UI field
        try:
            return type_cast(self.settings_fields[name].text())
        except Exception:
            return type_cast(self.settings.get(name))


    def update_timer_interval(self):
        interval = self.get_setting("FETCH_INTERVAL", int)
        if interval and interval > 0:
            self.auto_fetch_timer.start(interval * 1000)
            self.thread_safe_log(f"Auto-fetch interval updated to {interval} seconds.")

    def update_analysis_timer_interval(self):
        """
        Update the bulk analysis QTimer interval to whatever the user sets
        in ANALYSIS_WINDOW (seconds). Uses the bulk_timer QTimer created
        in __init__.
        """
        interval = self.get_setting("ANALYSIS_WINDOW", int)
        if interval and interval > 0:
            # Update the existing timer's interval (no start/stop drift)
            self.bulk_timer.setInterval(interval * 1000)
            self.thread_safe_log(f"Bulk analysis interval updated to {interval} seconds.")


    # ---------- Logging ----------
    def thread_safe_log(self, msg: str):
        if self._shutting_down:
            return
        try:
            QMetaObject.invokeMethod(self.log, "append", Qt.QueuedConnection, Q_ARG(str, msg))
            print(msg)
        except Exception as e:
            print(f"Log error: {e}")

    def thread_safe_reply(self, msg: str):
        if self._shutting_down:
            return
        try:
            QMetaObject.invokeMethod(self.reply_box, "setPlainText", Qt.QueuedConnection, Q_ARG(str, msg))
        except Exception as e:
            print(f"Reply error: {e}")

    # ---------- History ----------
    def add_to_history(self, text: str):
        if self._shutting_down:
            return
        try:
            while self.history_layout.count() >= 25:
                item = self.history_layout.itemAt(0)
                widget = item.widget()
                if widget:
                    widget.setParent(None)

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry_text = f"<b>{timestamp}</b><br>{text.replace(chr(10), '<br>')}"
            entry_label = QLabel(entry_text)
            entry_label.setWordWrap(True)

            entry_label.setStyleSheet(
                "background-color: #001900; border: 1px solid #00FF41; color: #00FF41; padding: 5px;"
            )

            self.history_layout.addWidget(entry_label)
            self.history_scroll.verticalScrollBar().setValue(self.history_scroll.verticalScrollBar().maximum())
        except Exception as e:
            print(f"History error: {e}")

    # ---------- Topics ----------
    def add_topic_field(self, default_text=""):
        if len(self.topic_entries) >= MAX_TOPICS:
            self.thread_safe_log(f"Maximum of {MAX_TOPICS} topics reached.")
            return
        hbox = QHBoxLayout()
        line = QLineEdit(default_text)
        hbox.addWidget(line)
        remove_btn = QPushButton("Remove")
        remove_btn.setFixedWidth(70)
        hbox.addWidget(remove_btn)
        self.topics_container.addLayout(hbox)
        remove_btn.clicked.connect(lambda: self.remove_topic_field(hbox, line))
        self.topic_entries.append(line)

    def remove_topic_field(self, hbox, line):
        for i in reversed(range(hbox.count())):
            widget = hbox.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        if line in self.topic_entries:
            self.topic_entries.remove(line)
        self.topics_container.removeItem(hbox)

    def get_topics_list(self):
        return [t.text().strip() for t in self.topic_entries if t.text().strip()]

    def get_topics_string(self):
        topics = self.get_topics_list()
        return ", ".join(topics) if topics else "No topics defined"

    # ---------- Data sources ----------
    def load_data_sources(self):
        try:
            file_path, _ = QFileDialog.getOpenFileName(self, "Select Data Source File", "", "Text Files (*.txt);;JSON Files (*.json)")
            if not file_path:
                return
            if file_path.endswith(".txt"):
                with open(file_path, "r") as f:
                    self.feeds = [line.strip() for line in f.readlines() if line.strip() and not line.startswith("#")]
            elif file_path.endswith(".json"):
                with open(file_path, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.feeds = [url.strip() for url in data if url.strip() and not url.strip().startswith("#")]
                else:
                    self.thread_safe_log("JSON must contain a list of URLs.")
                    return
            self.thread_safe_log(f"{len(self.feeds)} data sources loaded.")
        except Exception as e:
            self.thread_safe_log(f"Failed to load data sources: {e}")


    def load_prompt_file(self):
        try:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Select Prompt File", "", "Text Files (*.txt)"
            )
            if not file_path:
                return
            with open(file_path, "r", encoding="utf-8") as f:
                self.base_prompt = f.read()
            self.thread_safe_log(f"Loaded prompt file: {file_path}")
        except Exception as e:
            self.thread_safe_log(f"Failed to load prompt: {e}")


    #--------- Append RSS Results to File ----------
    def append_to_rolling_file(self, text):
        try:
            with open(ROLLING_FILE, "a", encoding="utf-8") as f:
                f.write(text + "\n\n")
            self.thread_safe_log(f"Appended {len(text)} chars to {ROLLING_FILE}")
        except Exception as e:
            self.thread_safe_log(f"Failed to write to rolling file: {e}")


    # ---------- Profiles ----------
    def load_profiles(self):
        """Load profiles from the JSON file."""
        try:
            if os.path.exists(PROFILE_FILE):
                with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                    self.profiles = json.load(f)
                self.thread_safe_log(f"Loaded {len(self.profiles)} saved profiles.")
            else:
                self.profiles = {}
        except Exception as e:
            self.thread_safe_log(f"Failed to load profiles, starting fresh. Error: {e}")
            self.profiles = {}

    def save_profiles(self):
        """Save profiles to the JSON file."""
        try:
            with open(PROFILE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.profiles, f, indent=2)
            self.thread_safe_log(f"Profiles saved to {PROFILE_FILE}.")
        except Exception as e:
            self.thread_safe_log(f"Failed to save profiles. Error: {e}")

    def update_profile_combobox(self):
        """Update the profile dropdown."""
        try:
            self.profile_select.clear()
            for name in self.profiles.keys():
                self.profile_select.addItem(name)
        except Exception as e:
            print(f"Profile combobox error: {e}")

    def save_current_profile(self):
        """Prompt for a profile name and save current topics."""
        if len(self.profiles) >= MAX_PROFILES:
            self.thread_safe_log(f"Cannot save more than {MAX_PROFILES} profiles.")
            return
        try:
            name, ok = QInputDialog.getText(self, "Profile Name", "Enter profile name:")
            if not ok or not name.strip():
                return
            name = name.strip()
            self.profiles[name] = self.get_topics_list()  # Assumes this method exists
            self.save_profiles()
            self.update_profile_combobox()
            self.thread_safe_log(f"Profile '{name}' saved.")
        except Exception as e:
            self.thread_safe_log(f"Failed to save profile: {e}")

    def load_selected_profile(self):
        """Load the currently selected profile into the UI."""
        try:
            name = self.profile_select.currentText()
            if name not in self.profiles:
                self.thread_safe_log("No profile selected.")
                return

            # Clear current topic widgets
            for i in reversed(range(self.topics_container.count())):
                item = self.topics_container.itemAt(i)
                if item.layout():
                    hbox = item.layout()
                    for j in reversed(range(hbox.count())):
                        widget = hbox.itemAt(j).widget()
                        if widget:
                            widget.setParent(None)
                    self.topics_container.removeItem(hbox)

            # Clear the internal list
            self.topic_entries.clear()

            # Load new topics
            for topic in self.profiles[name]:
                self.add_topic_field(topic)  # Assumes this method exists

            self.thread_safe_log(f"Profile '{name}' loaded with topics: {', '.join(self.profiles[name])}")
        except Exception as e:
            self.thread_safe_log(f"Failed to load profile: {e}")

    def delete_selected_profile(self):
        """Delete the currently selected profile."""
        try:
            name = self.profile_select.currentText()
            if name in self.profiles:
                del self.profiles[name]
                self.save_profiles()
                self.update_profile_combobox()
                self.thread_safe_log(f"Profile '{name}' deleted.")
        except Exception as e:
            self.thread_safe_log(f"Failed to delete profile: {e}")

    # ---------- RSS ----------
    def fetch_feed(self, url):
        try:
            headers = {"User-Agent": "Python RSS Client"}
            if "reddit.com" in url:
                headers["User-Agent"] = (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/119.0.0.0 Safari/537.36"
                )
            timeout = 20 if "fema.gov" in url else 10
            r = requests.get(url, headers=headers, timeout=timeout)
            r.raise_for_status()
            return feedparser.parse(r.content)
        except Exception as e:
            self.thread_safe_log(f"Error fetching {url}: {e}")
            return None

    def fetch_rss_latest(self):
        items = []
        for url in self.feeds:
            feed = self.fetch_feed(url)
            if not feed:
                continue
            self.thread_safe_log(f"Checking {url}, {len(feed.entries)} entries found")
            for entry in feed.entries[:self.get_setting("ITEMS_PER_FEED", int)]:
                guid = getattr(entry, "id", None) or getattr(entry, "link", None)
                if guid in self.seen_guids:
                    continue
                self.seen_guids.add(guid)
                pub_date = getattr(entry, "published", None) or getattr(entry, "updated", None)
                if pub_date:
                    try:
                        pub_dt = dateparser.parse(pub_date)
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        if (now - pub_dt).total_seconds() > 86400:  # older than 24h
                            continue
                    except Exception:
                        pub_date = "(Invalid date)"
                else:
                    pub_date = "(No date)"
                title = getattr(entry, "title", "(No title)")
                link = getattr(entry, "link", "")
                summary = getattr(entry, "summary", "(No summary)")
                items.append(f"Title: {title}\nPublished: {pub_date}\nSummary: {summary}\nLink: {link}")
        self.thread_safe_log(f"Collected {len(items)} items")
        return "\n\n".join(items)

    # ---------- LMStudio ----------
    def truncate_tokens(self, text):
        return text[: self.get_setting("MAX_TOKENS", int) * 4]

    # ---------- Slack ----------
    def send_slack_notification(self, message):
        try:
            webhook_url = self.get_setting("SLACK_WEBHOOK_URL")
            if not webhook_url:
                self.thread_safe_log("Slack Webhook URL is empty, skipping notification.")
                return
            resp = requests.post(webhook_url, json={"text": message}, timeout=10)
            if resp.status_code != 200:
                self.thread_safe_log(f"Slack error: {resp.status_code}, {resp.text}")
        except Exception as e:
            self.thread_safe_log(f"Slack exception: {e}")

    # ---------- LMStudio ----------
    def fetch_and_send(self):
        try:
            if self._shutting_down:
                return
            self.thread_safe_log("[ShunyaNet Sentinel] Fetching RSS...")
            text_block = self.fetch_rss_latest()
            if not text_block:
                self.thread_safe_log("No new items.")
                return

            # Optional: write the raw pull to a rolling file
            if self.get_setting("WRITE_TO_FILE", int) == 1:
                self.append_to_rolling_file(text_block)
                if not hasattr(self, "rolling_file_start_time"):
                    self.rolling_file_start_time = time.time()

            # Now perform bulk trend analysis
            self.perform_bulk_analysis_if_ready()

            # Truncate input so we never send millions of characters
            text_block = self.truncate_tokens(text_block)
            topics_str = self.get_topics_string()
            self.thread_safe_log(f"Topics sent to LLM: {topics_str}")

            # Check chunk settings
            use_chunked = self.get_setting("USE_CHUNKED_MODE", int) == 1
            chunk_size = self.get_setting("CHUNK_SIZE", int) or 4000
            chunks = [text_block] if not use_chunked else [text_block[i:i + chunk_size] for i in range(0, len(text_block), chunk_size)]
            self.thread_safe_log(f"{len(chunks)} chunk(s) prepared for LMStudio.")

            for idx, chunk in enumerate(chunks):
                prompt_text = self.base_prompt.format(CHUNK=chunk, TOPICS=topics_str)
                self.thread_safe_log(f"Sending chunk {idx + 1}/{len(chunks)} ({len(chunk)} chars)...")

                resp = requests.post(
                    self.get_setting("LMSTUDIO_URL"),
                    json={"model": "your_model_name",
                        "messages": [{"role": "user", "content": prompt_text}],
                        "max_tokens": self.get_setting("MAX_TOKENS", int)},
                    timeout=900
                )

                if resp.status_code != 200:
                    self.thread_safe_log(f"LMStudio returned HTTP {resp.status_code} for chunk {idx + 1}")
                    continue

                chunk_reply = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if chunk_reply:
                    self.thread_safe_reply(chunk_reply)
                    self.send_slack_notification(chunk_reply)
                    QMetaObject.invokeMethod(
                        self,
                        "add_to_history",
                        Qt.QueuedConnection,
                        Q_ARG(str, chunk_reply)
                    )
        except Exception as e:
            self.thread_safe_log(f"Error sending: {e}")

    def perform_bulk_analysis_if_ready(self):
        try:
            if self._shutting_down:
                return None  # disabled

            # MUST DEFINE THESE FIRST
            now = time.time()
            window = self.get_setting("ANALYSIS_WINDOW", int)

            self.thread_safe_log(
                f"[Analysis Check] window={window}s elapsed={int(now - self.rolling_file_start_time)}s"
            )

            # Not enough time has passed
            if now - self.rolling_file_start_time < window:
                return None

            # Proceed with analysis
            file_path = ROLLING_FILE
            if not os.path.exists(file_path):
                self.rolling_file_start_time = now
                return None

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    full_text = f.read().strip()
            except Exception as e:
                self.thread_safe_log(f"Failed reading rolling file: {e}")
                self.rolling_file_start_time = now
                return None

            if not full_text:
                self.thread_safe_log("Rolling file empty, skipping analysis.")
                self.rolling_file_start_time = now
                return None

            # Truncate to avoid token explosion
            max_chars = self.get_setting("MAX_TOKENS_BULK", int) * 4
            full_text = full_text[:max_chars]

            self.thread_safe_log("Performing bulk analysis over rolling file...")

            prompt = (
                "You are analyzing an accumulation of RSS and social media text. "
                "Produce a structured trend report summarizing major themes, "
                "emerging risks, and patterns based solely on matching or linked (yet disparate) information in the data feed. "
                "If there are no patterns that can be pieced together, don't create fake connections just to provide an answer. Do not quote the input.\n\n"
                "INPUT DATA:\n" + full_text
            )

            try:
                resp = requests.post(
                    self.get_setting("LMSTUDIO_URL"),
                    json={"model":"your_model_name",
                        "messages":[{"role":"user","content":prompt}],
                        "max_tokens": self.get_setting("MAX_TOKENS_BULK", int)},
                    timeout=900
                )
                if resp.status_code == 200:
                    reply = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    if reply:
                        self.thread_safe_reply(reply)
                        self.send_slack_notification(reply)
                        QMetaObject.invokeMethod(
                            self,
                            "add_to_history",
                            Qt.QueuedConnection,
                            Q_ARG(str, reply)
                        )
            except Exception as e:
                self.thread_safe_log(f"Error during bulk analysis: {e}")

            # Reset and restart the window
            try:
                open(file_path, "w").close()
                self.thread_safe_log("Rolling file cleared after bulk analysis.")
            except:
                pass

            self.rolling_file_start_time = now
        except Exception as e:
            self.thread_safe_log(f"Error in bulk analysis: {e}")

    # ==================
    # SAVE APP STATE AND RESUME STATE
    #====================

    def save_app_state(self):
        try:
            state = {
                "active_profile": self.profile_select.currentText(),
                "topics": self.get_topics_list(),
                "settings": self.settings,
                "data_source_file": getattr(self, "current_data_source_file", ""),
                "prompt_file": getattr(self, "prompt_file", "")
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            self.thread_safe_log("App state saved.")
        except Exception as e:
            self.thread_safe_log(f"Failed to save app state: {e}")
    
    def load_app_state(self):
        try:
            if not os.path.exists(STATE_FILE):
                return
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            # Restore settings
            self.settings.update(state.get("settings", {}))
            # Restore prompt file
            prompt_file = state.get("prompt_file", "")
            if prompt_file and os.path.exists(prompt_file):
                self.prompt_file = prompt_file
                with open(prompt_file, "r", encoding="utf-8") as pf:
                    self.base_prompt = pf.read()
            # Restore topics
            self.topic_entries.clear()
            for i in reversed(range(self.topics_container.count())):
                item = self.topics_container.itemAt(i)
                if item.layout():
                    for j in reversed(range(item.layout().count())):
                        widget = item.layout().itemAt(j).widget()
                        if widget:
                            widget.setParent(None)
                    self.topics_container.removeItem(item.layout())
            for topic in state.get("topics", []):
                self.add_topic_field(topic)
            # Restore active profile selection
            active_profile = state.get("active_profile", "")
            index = self.profile_select.findText(active_profile)
            if index >= 0:
                self.profile_select.setCurrentIndex(index)
            # Restore data source file
            self.current_data_source_file = state.get("data_source_file", "")
            self.thread_safe_log("App state loaded.")
        except Exception as e:
            self.thread_safe_log(f"Failed to load app state: {e}")

    def closeEvent(self, event):
        """
        Robust shutdown sequence for macOS:
        - set a shutting_down flag
        - replace UI callbacks with no-ops
        - disconnect worker signals
        - stop & delete timers
        - politely request threads to stop, wait (longer)
        - shutdown executor (blocking)
        - drain Qt event loop a few times
        - save state and allow normal close
        """
        # 0) Mark shutting down (if worker code checks this, they can exit early)
        try:
            self._shutting_down = True
        except Exception:
            pass

        # 1) Replace UI-updating helpers with no-ops immediately
        try:
            old_log = self.thread_safe_log
            old_reply = self.thread_safe_reply
            old_history = self.add_to_history
            
            def noop(*args, **kwargs):
                pass
                
            self.thread_safe_log = noop
            self.thread_safe_reply = noop
            self.add_to_history = noop
        except Exception:
            pass

        # 2) Disconnect signals on active QThreads (best-effort)
        try:
            for thread in list(getattr(self, "active_threads", []) or []):
                try:
                    for sig_name in ("log_signal", "reply_signal", "history_signal"):
                        sig = getattr(thread, sig_name, None)
                        if sig is not None:
                            try:
                                sig.disconnect()
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception:
            pass

        # 3) Stop timers and remove them (avoid firing while closing)
        try:
            if hasattr(self, "auto_fetch_timer") and self.auto_fetch_timer is not None:
                try:
                    self.auto_fetch_timer.stop()
                except Exception:
                    pass
                try:
                    self.auto_fetch_timer.deleteLater()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if hasattr(self, "bulk_timer") and self.bulk_timer is not None:
                try:
                    self.bulk_timer.stop()
                except Exception:
                    pass
                try:
                    self.bulk_timer.deleteLater()
                except Exception:
                    pass
        except Exception:
            pass

        # 4) Ask QThreads to stop (requestInterruption + quit) and wait *longer*
        try:
            for thread in list(getattr(self, "active_threads", []) or []):
                try:
                    try:
                        thread.requestInterruption()
                    except Exception:
                        pass
                    try:
                        thread.quit()
                    except Exception:
                        pass
                    # Wait up to 5 seconds per thread for a clean stop
                    try:
                        thread.wait(timeout=5000)
                    except TypeError:
                        try:
                            thread.wait(5000)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # 5) Shutdown executor (blocking). This waits for executing futures to finish.
        try:
            # prefer to block briefly to allow clean completion
            try:
                self.executor.shutdown(wait=True, timeout=5)
            except TypeError:
                # older Python doesn't accept timeout kw
                try:
                    self.executor.shutdown(wait=True)
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            pass

        # 6) Block signals on text widgets in case any stray Qt events are posted
        try:
            if hasattr(self, "log") and isinstance(self.log, QTextEdit):
                try:
                    self.log.blockSignals(True)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if hasattr(self, "reply_box") and isinstance(self.reply_box, QTextEdit):
                try:
                    self.reply_box.blockSignals(True)
                except Exception:
                    pass
        except Exception:
            pass

        # 7) Drain the event queue several times to let pending events run (callbacks are no-ops)
        try:
            for _ in range(8):
                QApplication.processEvents()
                # tiny pause to let other threads/qt-posted events interleave (non-blocking)
                time.sleep(0.01)
        except Exception:
            pass

        # 8) Save app state last so state write succeeds with UI callbacks disabled
        try:
            self.save_app_state()
        except Exception:
            pass

        # 9) Allow Qt to clean up objects normally (no sys.exit / QApplication.quit here)
        try:
            self.setAttribute(Qt.WA_DeleteOnClose, True)
            self.deleteLater()
        except Exception:
            pass

        # 10) Finally accept the close and return to event loop so exec() exits cleanly
        event.accept()

# ---------- Global Qt cleanup (prevents PySide6/Shiboken crash on exit) ----------

def clean_qt_shutdown():
    try:
        app = QApplication.instance()
        if app is not None:
            # Close windows first
            try:
                app.closeAllWindows()
            except Exception:
                pass

            # Quit the Qt event loop
            try:
                app.quit()
            except Exception:
                pass

            # Schedule deletion of QApplication
            try:
                app.deleteLater()
            except Exception:
                pass
    except Exception:
        pass

atexit.register(clean_qt_shutdown)

# ---------- Run ----------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    monospace_family = QFontDatabase.systemFont(QFontDatabase.FixedFont).family()

    # Global Green-style stylesheet
    app.setStyleSheet(f"""
        QWidget {{
            background-color: #000000;
            color: #00FF41;
            font-family: "Courier New";
            font-size: 14px;
        }}

        QLabel {{
            font-weight: bold;
            color: #00FF41;
        }}

        QLineEdit, QTextEdit, QComboBox {{
            background-color: #000000;
            color: #00FF41;
            border: 1px solid #00FF41;
            border-radius: 4px;
            padding: 4px;
            selection-background-color: #007700;
            selection-color: #00FF41;
        }}

        QScrollArea {{
            background-color: #000000;
            border: 1px solid #003300;
        }}

        QPushButton {{
            background-color: #001900;
            color: #00FF41;
            border: 1px solid #00FF41;
            border-radius: 4px;
            padding: 6px;
        }}

        QPushButton:hover {{
            background-color: #003300;
        }}

        QPushButton:pressed {{
            background-color: #005500;
        }}

        QComboBox QAbstractItemView {{
            background-color: #000000;
            border: 1px solid #00FF41;
            color: #00FF41;
            selection-background-color: #003300;
        }}

        QScrollBar:vertical {{
            background: #000;
            width: 14px;
            margin: 0px;
        }}

        QScrollBar::handle:vertical {{
            background: #003300;
            min-height: 20px;
            border-radius: 6px;
        }}

        QScrollBar::handle:vertical:hover {{
            background: #005500;
        }}

        QScrollBar::add-line, QScrollBar::sub-line {{
            height: 0px;
        }}
    """)

    icon_path = resource_path(os.path.join("assets", "app_icon.png"))
    app.setWindowIcon(QIcon(icon_path))

    w = RSSApp()
    w.resize(1050, 800)
    w.show()

    exit_code = app.exec()

    # ==============================
    # HARD Qt cleanup (macOS-safe)
    # ==============================

    try:
        # 1. Close & delete all top-level widgets (destroys layouts safely)
        for widget in QApplication.topLevelWidgets():
            try:
                widget.close()
                widget.deleteLater()
            except Exception:
                pass

        # 2. Drain pending Qt events (CRITICAL)
        QApplication.processEvents()

        # 3. Quit and destroy QApplication explicitly
        app.quit()
        app.deleteLater()

        QApplication.processEvents()

    except Exception:
        pass

    sys.exit(exit_code)
