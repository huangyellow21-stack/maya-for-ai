# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import sys
import traceback
import threading
import subprocess
import time
import functools
import json

import maya.cmds as cmds
try:
    import maya.OpenMaya as om
except ImportError:
    om = None

from ..core import config as cfgmod
from ..core.agent import run_chat, AgentError
from ..core.http_client import get_json
from ..core.memory import ChatPersistence
import json

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    import shiboken2
except Exception:
    QtCore = None
    QtGui = None
    QtWidgets = None
    shiboken2 = None


def _kill_process_by_port(port):
    """
    尝试杀掉占用指定端口的进程 (Windows only)
    同时也杀掉所有 python.exe 进程如果它的命令行包含 'server:app' (网关特征)
    """
    import subprocess
    
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE

    # 1. 端口查杀 (netstat)
    try:
        cmd = 'netstat -ano | findstr :%s' % port
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
        out, _ = proc.communicate()
        if out:
            try:
                out = out.decode("mbcs", errors="ignore")
            except Exception:
                pass
            pids = set()
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) > 4 and str(port) in parts[1]: 
                    pids.add(parts[-1])
            for pid in pids:
                try:
                    subprocess.call('taskkill /F /PID %s' % pid, shell=True, startupinfo=startupinfo)
                except Exception:
                    pass
    except Exception:
        pass

    # 2. 进程名特征查杀 (wmic)
    # 这一步对于杀掉"正在启动但还没监听端口"的进程非常重要
    try:
        # 杀掉 server:app (uvicorn)
        cmd = 'wmic process where "name=\'python.exe\' and commandline like \'%server:app%\'" call terminate'
        subprocess.call(cmd, shell=True, startupinfo=startupinfo)
        
        # 杀掉 bridge 相关 (启动脚本封装)
        cmd2 = 'wmic process where "name=\'python.exe\' and commandline like \'%bridge%\'" call terminate'
        subprocess.call(cmd2, shell=True, startupinfo=startupinfo)
    except Exception:
        pass


_EXIT_CALLBACK_ID = None

def _on_maya_exit(*args):
    """
    Maya 退出时的回调：清理网关进程
    """
    try:
        # 默认尝试清理 8765 端口
        _kill_process_by_port(8765)
    except:
        pass

def _register_exit_callback():
    """
    注册 Maya 退出回调，确保只注册一次
    """
    global _EXIT_CALLBACK_ID
    if _EXIT_CALLBACK_ID is None and om is not None:
        try:
            _EXIT_CALLBACK_ID = om.MSceneMessage.addCallback(
                om.MSceneMessage.kMayaExiting, 
                _on_maya_exit
            )
        except Exception:
            pass


class WorkerSignals(QtCore.QObject):
    chat_finished = QtCore.Signal(object, object)
    chat_error = QtCore.Signal(str)
    gateway_status = QtCore.Signal(str, str)


class ApiKeyDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(ApiKeyDialog, self).__init__(parent)
        self.setWindowTitle("API Key")
        self.setModal(True)
        self.key = None
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        info = QtWidgets.QLabel("请输入你的大模型 API Key 以继续使用")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.input = QtWidgets.QLineEdit()
        self.input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.input.setPlaceholderText("粘贴你的 API Key")
        self.input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.input)

        self.hintLabel = QtWidgets.QLabel("")
        self.hintLabel.setStyleSheet("color: #9FB1C7; font-size: 11px;")
        layout.addWidget(self.hintLabel)

        btns = QtWidgets.QHBoxLayout()
        save = QtWidgets.QPushButton("保存并继续")
        save.clicked.connect(self.accept)
        cancel = QtWidgets.QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(save)
        btns.addWidget(cancel)
        layout.addLayout(btns)

    def _on_text_changed(self, text):
        t = text.strip()
        if t.startswith("AIza"):
            self.hintLabel.setText("识别：可能是 Gemini Key")
        elif t.startswith("sk-"):
            self.hintLabel.setText("识别：可能是 OpenAI / DeepSeek Key")
        elif t:
            self.hintLabel.setText("识别：未知格式")
        else:
            self.hintLabel.setText("")

    def get_key(self):
        return self.input.text().strip()


WINDOW_OBJECT_NAME = "AIFORMAYA_Dock"
CONTROL_NAME = "AIFORMAYA_WorkspaceControl"


class AiformayaWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(AiformayaWidget, self).__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)

        self.cfg = cfgmod.load_config()
        self.history = []
        self._icon_root = self._resolve_icon_dir()
        self._last_provider = (self.cfg.get("provider") or "deepseek").strip().lower()

        self.signals = WorkerSignals()
        self.signals.chat_finished.connect(self.on_chat_finished)
        self.signals.chat_error.connect(self.on_chat_error)
        self.signals.gateway_status.connect(self.on_gateway_status)

        self._ai_placeholder_item = None
        self._gateway_running = False

        self._build_ui()
        self._load_cfg_to_ui()
        
        # Initial checks
        self._load_session_history()
        self._start_gateway_check_thread()
        self._refresh_key_status()
        self._check_first_run_key()

    def _load_session_history(self):
        try:
            ui_hist, ag_hist = ChatPersistence.load()
            self.history = ag_hist
            for item in ui_hist:
                role = item.get("role")
                content = item.get("content")
                if role and content:
                    self._add_chat_bubble(role, content, save=False)
        except Exception as e:
            print("Failed to load chat history:", e)

    def _save_session_history(self):
        # Gather right from UI
        ui_hist = []
        for i in range(self.chatList.count()):
            item = self.chatList.item(i)
            # We stored role and content in user role data
            role = item.data(QtCore.Qt.UserRole)
            content = item.data(QtCore.Qt.UserRole + 1)
            if role and content:
                ui_hist.append({"role": role, "content": content})
        ChatPersistence.save(ui_hist, self.history)

    def _check_first_run_key(self):
        # 强引导：如果当前 key 为空，自动弹窗
        provider = (self.cfg.get("provider") or "deepseek").strip().lower()
        key_field = "gemini_api_key" if provider == "gemini" else "deepseek_api_key"
        if not self.cfg.get(key_field):
            self._show_api_key_dialog()

    def _resolve_icon_dir(self):
        here = self._to_unicode(os.path.dirname(__file__))
        candidates = [
            os.path.abspath(os.path.join(here, u"icon")),
            os.path.abspath(os.path.join(here, u"..", u"icon")),
            os.path.abspath(os.path.join(here, u"..", u"..", u"icon")),
            os.path.abspath(os.path.join(here, u"..", u"..", u"..", u"icon")),
            os.path.abspath(os.path.join(here, u"..", u"..", u"..", u"..", u"icon")),
            os.path.abspath(os.path.join(here, u"..", u"..", u"..", u"..", u"..", u"icon")),
        ]
        for p in candidates:
            if os.path.isdir(p):
                return p
        return candidates[0]

    def _to_unicode(self, value):
        if isinstance(value, unicode):
            return value
        enc = sys.getfilesystemencoding() or "mbcs"
        try:
            return value.decode(enc)
        except Exception:
            return unicode(value, errors="ignore")

    def _normalize_icon_name(self, name):
        text = self._to_unicode(name).replace(" ", "").replace(u"（", "(").replace(u"）", ")")
        text = text.replace(u"－", "-").replace(u"—", "-").replace(u"‐", "-")
        return text.lower()

    def _find_icon_path(self, filename):
        icon_dir = self._to_unicode(self._icon_root)
        fname = self._to_unicode(filename)
        path = os.path.join(icon_dir, fname)
        if os.path.exists(path):
            return path
        try:
            want = self._normalize_icon_name(fname)
            for f in os.listdir(icon_dir):
                fu = self._to_unicode(f)
                if self._normalize_icon_name(fu) == want:
                    return os.path.join(icon_dir, fu)
        except Exception:
            return None
        return None

    def _load_pixmap(self, filename, size):
        path = self._find_icon_path(filename)
        if not path:
            return None
        pm = QtGui.QPixmap(path)
        if pm.isNull():
            return None
        if size:
            pm = pm.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        return pm

    def _set_icon(self, widget, filename, size):
        pm = self._load_pixmap(filename, size)
        if pm is None:
            return
        widget.setIcon(QtGui.QIcon(pm))
        widget.setIconSize(QtCore.QSize(size, size))

    def _set_label_icon(self, label, filename, size):
        pm = self._load_pixmap(filename, size)
        if pm is None:
            return
        label.setPixmap(pm)
        label.setFixedSize(size, size)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # TopBar
        topbar = QtWidgets.QFrame()
        topbar.setObjectName("TopBar")
        topbar_layout = QtWidgets.QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(12, 8, 12, 8)
        topbar_layout.setSpacing(8)
        self.logoLabel = QtWidgets.QLabel()
        self._set_label_icon(self.logoLabel, u"应用左上角 Logo.png", 24)
        topbar_layout.addWidget(self.logoLabel)
        self.titleLabel = QtWidgets.QLabel("AIFORMAYA")
        self.titleLabel.setObjectName("TitleLabel")
        topbar_layout.addWidget(self.titleLabel)
        topbar_layout.addStretch(1)
        self.gearBtn = QtWidgets.QPushButton("")
        self.gearBtn.setObjectName("IconButton")
        self._set_icon(self.gearBtn, u"顶部工具图标（右上角齿轮）.png", 16)
        self.gearBtn.setFixedSize(28, 28)
        topbar_layout.addWidget(self.gearBtn)
        layout.addWidget(topbar)

        # Scroll Area for Settings
        self.settingsScroll = QtWidgets.QScrollArea()
        self.settingsScroll.setWidgetResizable(True)
        self.settingsScroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.settingsScroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        settings_container = QtWidgets.QWidget()
        settings_container.setObjectName("SettingsContainer")
        settings_layout = QtWidgets.QVBoxLayout(settings_container)
        settings_layout.setContentsMargins(16, 16, 16, 16)
        settings_layout.setSpacing(12)

        # Gateway Card
        gateway_card = QtWidgets.QFrame()
        gateway_card.setObjectName("Card")
        gateway_layout = QtWidgets.QVBoxLayout(gateway_card)
        gateway_layout.setContentsMargins(16, 16, 16, 16)
        gateway_layout.setSpacing(10)
        gateway_header = QtWidgets.QHBoxLayout()
        gateway_icon = QtWidgets.QLabel()
        self._set_label_icon(gateway_icon, u"左侧分区图标（Gateway).png", 18)
        gateway_header.addWidget(gateway_icon)
        gateway_title = QtWidgets.QLabel("Gateway")
        gateway_title.setObjectName("SectionTitle")
        gateway_header.addWidget(gateway_title)

        # Gateway Status
        self.gatewayStatusLabel = QtWidgets.QLabel("Checking...")
        self.gatewayStatusLabel.setObjectName("StatusLabel")
        self.gatewayStatusLabel.setStyleSheet("color: #FFB020;")  # Warning color
        gateway_header.addStretch(1)
        gateway_header.addWidget(self.gatewayStatusLabel)

        gateway_layout.addLayout(gateway_header)
        gateway_row = QtWidgets.QHBoxLayout()
        gateway_label = QtWidgets.QLabel("URL")
        gateway_label.setObjectName("FieldLabel")
        gateway_row.addWidget(gateway_label)
        self.gatewayUrl = QtWidgets.QLineEdit()
        self.gatewayUrl.setPlaceholderText("http://127.0.0.1:8765")
        self.gatewayUrl.setFixedHeight(36)
        gateway_row.addWidget(self.gatewayUrl, 1)
        self.healthBtn = QtWidgets.QPushButton("Check")
        self.healthBtn.setObjectName("PrimaryButton")
        self.healthBtn.setFixedHeight(36)
        gateway_row.addWidget(self.healthBtn)
        
        self.restartGatewayBtn = QtWidgets.QPushButton("Restart")
        self.restartGatewayBtn.setObjectName("DangerButton")
        self.restartGatewayBtn.setFixedHeight(36)
        self.restartGatewayBtn.setFixedWidth(60)
        self.restartGatewayBtn.clicked.connect(self._on_restart_gateway_clicked)
        self.restartGatewayBtn.setVisible(False)
        gateway_row.addWidget(self.restartGatewayBtn)
        
        gateway_layout.addLayout(gateway_row)

        # Start Gateway Button
        self.startGatewayBtn = QtWidgets.QPushButton("Start Gateway")
        self.startGatewayBtn.setObjectName("SecondaryButton")
        self.startGatewayBtn.setFixedHeight(32)
        self.startGatewayBtn.clicked.connect(self._on_start_gateway_clicked)
        self.startGatewayBtn.setVisible(False)
        gateway_layout.addWidget(self.startGatewayBtn)

        settings_layout.addWidget(gateway_card)

        # Provider Card
        provider_card = QtWidgets.QFrame()
        provider_card.setObjectName("Card")
        provider_layout = QtWidgets.QVBoxLayout(provider_card)
        provider_layout.setContentsMargins(16, 16, 16, 16)
        provider_layout.setSpacing(10)
        provider_header = QtWidgets.QHBoxLayout()
        provider_icon = QtWidgets.QLabel()
        self._set_label_icon(provider_icon, u"左侧分区图标（Provider).png", 18)
        provider_header.addWidget(provider_icon)
        provider_title = QtWidgets.QLabel("Provider & Models")
        provider_title.setObjectName("SectionTitle")
        provider_header.addWidget(provider_title)

        # API Key Status
        self.keyStatusLabel = QtWidgets.QLabel("API Key: Checking...")
        self.keyStatusLabel.setObjectName("StatusLabel")
        self.keyStatusLabel.setStyleSheet("color: #FFB020;")
        provider_header.addStretch(1)
        provider_header.addWidget(self.keyStatusLabel)
        
        provider_layout.addLayout(provider_header)

        # Set Key Button Row
        key_row = QtWidgets.QHBoxLayout()
        self.setKeyBtn = QtWidgets.QPushButton("设置 API Key")
        self.setKeyBtn.setObjectName("SecondaryButton")
        self.setKeyBtn.setFixedHeight(24)
        self.setKeyBtn.clicked.connect(self._on_set_key_clicked)
        self.setKeyBtn.setVisible(False)
        key_row.addStretch(1)
        key_row.addWidget(self.setKeyBtn)
        provider_layout.addLayout(key_row)

        model_row = QtWidgets.QHBoxLayout()
        model_label = QtWidgets.QLabel("Model")
        model_label.setObjectName("FieldLabel")
        model_row.addWidget(model_label)
        self.provider = QtWidgets.QComboBox()
        self.provider.addItems(["deepseek", "gemini"])
        self.provider.setFixedHeight(36)
        model_row.addWidget(self.provider)
        self.modelInput = QtWidgets.QComboBox()
        self.modelInput.setEditable(True)
        self.modelInput.setFixedHeight(36)
        model_row.addWidget(self.modelInput, 1)
        provider_layout.addLayout(model_row)
        settings_layout.addWidget(provider_card)

        # Settings Card
        settings_card = QtWidgets.QFrame()
        settings_card.setObjectName("Card")
        settings_layout_inner = QtWidgets.QVBoxLayout(settings_card)
        settings_layout_inner.setContentsMargins(16, 16, 16, 16)
        settings_layout_inner.setSpacing(10)
        settings_header = QtWidgets.QHBoxLayout()
        settings_icon = QtWidgets.QLabel()
        self._set_label_icon(settings_icon, u"左侧分区图标（Settings).png", 18)
        settings_header.addWidget(settings_icon)
        settings_title = QtWidgets.QLabel("Settings")
        settings_title.setObjectName("SectionTitle")
        settings_header.addWidget(settings_title)
        settings_header.addStretch(1)
        settings_layout_inner.addLayout(settings_header)
        temp_row = QtWidgets.QHBoxLayout()
        temp_label = QtWidgets.QLabel("Temperature")
        temp_label.setObjectName("FieldLabel")
        temp_row.addWidget(temp_label)
        self.temperatureSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.temperatureSlider.setRange(0, 200)
        self.temperatureSlider.setFixedHeight(24)
        temp_row.addWidget(self.temperatureSlider, 1)
        self.temperatureValue = QtWidgets.QDoubleSpinBox()
        self.temperatureValue.setDecimals(2)
        self.temperatureValue.setRange(0.0, 2.0)
        self.temperatureValue.setSingleStep(0.05)
        self.temperatureValue.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.temperatureValue.setFixedHeight(28)
        self.temperatureValue.setFixedWidth(60)
        self.temperatureValue.setAlignment(QtCore.Qt.AlignCenter)
        temp_row.addWidget(self.temperatureValue)
        settings_layout_inner.addLayout(temp_row)
        mode_row = QtWidgets.QHBoxLayout()
        mode_label = QtWidgets.QLabel("Mode")
        mode_label.setObjectName("FieldLabel")
        mode_row.addWidget(mode_label)
        self.modeBox = QtWidgets.QComboBox()
        self.modeBox.addItems(["编辑模式", "问询模式"])
        self.modeBox.setFixedHeight(36)
        mode_row.addWidget(self.modeBox, 1)
        settings_layout_inner.addLayout(mode_row)
        btn_row = QtWidgets.QHBoxLayout()
        self.saveBtn = QtWidgets.QPushButton("Save Config")
        self.saveBtn.setObjectName("SecondaryButton")
        self.saveBtn.setFixedHeight(32)
        self._set_icon(self.saveBtn, u"按钮图标（Save Config）.png", 16)
        self.clearBtn = QtWidgets.QPushButton("Clear Chat")
        self.clearBtn.setObjectName("DangerButton")
        self.clearBtn.setFixedHeight(32)
        self._set_icon(self.clearBtn, u"按钮图标（Clear Chat）.png", 16)
        btn_row.addWidget(self.saveBtn)
        btn_row.addWidget(self.clearBtn)
        btn_row.addStretch(1)
        settings_layout_inner.addLayout(btn_row)
        settings_layout.addWidget(settings_card)

        self.settingsScroll.setWidget(settings_container)
        layout.addWidget(self.settingsScroll)

        # Chat Card
        chat_card = QtWidgets.QFrame()
        chat_card.setObjectName("Card")
        chat_layout = QtWidgets.QVBoxLayout(chat_card)
        chat_layout.setContentsMargins(16, 16, 16, 16)
        chat_layout.setSpacing(12)
        chat_header = QtWidgets.QHBoxLayout()
        chat_icon = QtWidgets.QLabel()
        self._set_label_icon(chat_icon, u"聊天气泡头像(AI 头像）.png", 18)
        chat_header.addWidget(chat_icon)
        chat_title = QtWidgets.QLabel("Chat")
        chat_title.setObjectName("SectionTitle")
        chat_header.addWidget(chat_title)
        chat_header.addStretch(1)
        chat_layout.addLayout(chat_header)
        self._userAvatar = self._load_pixmap(u"聊天气泡头像（用户).png", 32)
        self._aiAvatar = self._load_pixmap(u"聊天气泡头像(AI 头像）.png", 32)
        self.chatList = QtWidgets.QListWidget()
        self.chatList.setObjectName("ChatList")
        self.chatList.setSpacing(10)
        self.chatList.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.chatList.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.chatList.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        chat_layout.addWidget(self.chatList, 1)
        chat_layout.addSpacing(8)
        input_row = QtWidgets.QHBoxLayout()
        input_row.setSpacing(10)
        self.input = QtWidgets.QLineEdit()
        self.input.setObjectName("ChatInput")
        self.input.setPlaceholderText("在这里输入 Maya 或动画相关问题，或加 ! 提普通问题")
        self.input.setFixedHeight(36)
        input_row.addWidget(self.input, 1)
        self.sendBtn = QtWidgets.QPushButton("Send")
        self.sendBtn.setObjectName("PrimaryButton")
        self.sendBtn.setFixedHeight(36)
        self._set_icon(self.sendBtn, u"按钮图标（Send）.png", 16)
        input_row.addWidget(self.sendBtn)
        chat_layout.addLayout(input_row)
        layout.addWidget(chat_card, 1)

        self.setStyleSheet(
            """
QWidget#AIFORMAYA_Dock {
    background-color: #0B1630;
    color: #D6E2F2;
    font-family: "Segoe UI","Source Han Sans SC","Noto Sans SC","Microsoft YaHei","PingFang SC",sans-serif;
    font-size: 13px;
}
QWidget#SettingsContainer {
    background-color: #0B1630;
}
QFrame#TopBar {
    background-color: #0A1A2B;
    border: 1px solid #22324A;
    border-radius: 12px;
}
QFrame#Card {
    background-color: #121F33;
    border: 1px solid #22324A;
    border-radius: 12px;
}
QLabel#TitleLabel {
    color: #EAF2FF;
    font-size: 16px;
    font-weight: 600;
}
QLabel#SectionTitle {
    color: #EAF2FF;
    font-size: 14px;
    font-weight: 600;
}
QLabel#FieldLabel {
    color: #9FB1C7;
    min-width: 90px;
}
QLabel#StatusLabel {
    font-weight: 600;
    font-size: 12px;
}
QLineEdit, QComboBox, QPlainTextEdit, QDoubleSpinBox, QListWidget {
    background-color: #0E1A2A;
    border: 1px solid #22324A;
    border-radius: 10px;
    padding: 6px 10px;
    color: #D6E2F2;
}
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover, QDoubleSpinBox:hover, QListWidget:hover {
    background-color: #102036;
    border: 1px solid #2C4464;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QDoubleSpinBox:focus, QListWidget:focus {
    border: 1px solid #21C7B7;
}
QLineEdit::placeholder {
    color: #7F93AD;
}
QListWidget#ChatList {
    background-color: #0F1B2E;
    border-radius: 12px;
    padding: 10px;
}
QListWidget#ChatList::item {
    border: none;
    padding: 0px;
}
QLineEdit#ChatInput {
    background-color: #0F1B2E;
    border-radius: 10px;
}
QFrame#BubbleAI {
    background-color: #1A2A3F;
    border: 1px solid #22324A;
    border-radius: 14px;
}
QFrame#BubbleUser {
    background-color: #1A5F5A;
    border: 1px solid #1CBCAE;
    border-radius: 14px;
}
QFrame#BubbleSystem {
    background-color: #18233A;
    border: 1px solid #22324A;
    border-radius: 14px;
}
QLabel#BubbleText {
    color: #E6EEF7;
    background-color: transparent;
}
QPushButton#PrimaryButton {
    background-color: #21C7B7;
    color: #062028;
    border-radius: 8px;
    padding: 6px 12px;
}
QPushButton#PrimaryButton:hover {
    background-color: #27D6C8;
}
QPushButton#PrimaryButton:pressed {
    background-color: #17AFA1;
}
QPushButton#SecondaryButton {
    background-color: #0E1A2A;
    color: #D6E2F2;
    border: 1px solid #22324A;
    border-radius: 8px;
    padding: 6px 12px;
}
QPushButton#SecondaryButton:hover {
    background-color: #102036;
    border: 1px solid #2C4464;
}
QPushButton#SecondaryButton:pressed {
    background-color: #0C1625;
    border: 1px solid #1E2C41;
}
QPushButton#DangerButton {
    background-color: #2A1A22;
    color: #FFD6DE;
    border: 1px solid #4A2330;
    border-radius: 8px;
    padding: 6px 12px;
}
QPushButton#DangerButton:hover {
    background-color: #3A1F2A;
}
QPushButton#DangerButton:pressed {
    background-color: #24141B;
}
QPushButton#IconButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}
QPushButton#IconButton:hover {
    background-color: #102036;
    border: 1px solid #2C4464;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #22324A;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 14px;
    margin: -5px 0;
    background: #21C7B7;
    border-radius: 7px;
}
"""
        )

        self.saveBtn.clicked.connect(self.on_save)
        self.clearBtn.clicked.connect(self.on_clear)
        self.healthBtn.clicked.connect(self.on_health)
        self.sendBtn.clicked.connect(self.on_send)
        self.input.returnPressed.connect(self.on_send)
        self.provider.currentTextChanged.connect(self.on_provider_changed)
        self.modeBox.currentIndexChanged.connect(self.on_mode_changed)
        self.temperatureSlider.valueChanged.connect(self._on_temp_slider_changed)
        self.temperatureValue.valueChanged.connect(self._on_temp_value_changed)

    def _load_cfg_to_ui(self):
        self.gatewayUrl.setText(self.cfg.get("gateway_url", "http://127.0.0.1:8765"))
        self.provider.setCurrentText(self.cfg.get("provider", "deepseek"))
        if self.modelInput.count() == 0:
            self.modelInput.addItems(
                [self.cfg.get("model_deepseek", "deepseek-chat"), self.cfg.get("model_gemini", "gemini-1.5-flash")]
            )
        try:
            temp = float(self.cfg.get("temperature", 0.2))
        except Exception:
            temp = 0.2
        self.temperatureValue.setValue(temp)
        self.temperatureSlider.setValue(int(temp * 100))
        mode = str(self.cfg.get("mode", "edit")).strip().lower()
        if mode == "view":
            self.modeBox.setCurrentIndex(1)
        else:
            self.modeBox.setCurrentIndex(0)
        self._apply_provider_ui_state()

    def _ui_to_cfg(self):
        self.cfg["gateway_url"] = str(self.gatewayUrl.text()).strip()
        self.cfg["provider"] = str(self.provider.currentText()).strip()
        model_text = str(self.modelInput.currentText()).strip()
        provider = (self.cfg.get("provider") or "deepseek").strip().lower()
        if provider == "gemini":
            self.cfg["model_gemini"] = model_text
        else:
            self.cfg["model_deepseek"] = model_text
        self.cfg["temperature"] = float(self.temperatureValue.value())
        mode_text = str(self.modeBox.currentText())
        if "询" in mode_text:
            self.cfg["mode"] = "view"
        else:
            self.cfg["mode"] = "edit"

    def _add_chat_bubble(self, role, content, **kwargs):
        item = QtWidgets.QListWidgetItem(self.chatList)
        
        # Calculate explicit labels. Note: content might already have prefixes due to how `log()` is called.
        # So we just strip it if it already has it, and cleanly add our preferred HTML formatting.
        display_text = content
        if role == "user":
            if display_text.startswith("你："):
                display_text = display_text[2:].strip()
            # Remove any leading diamond or weird symbols sometimes sent by error
            if display_text.startswith(u"：") or display_text.startswith(u": ") or display_text.startswith(u"❖：") or display_text.startswith(u"❖: "):
                 display_text = display_text[2:].strip()
            display_text = u"<b>你：</b><br>" + display_text
        elif role == "ai":
            if display_text.startswith("AI："):
                 display_text = display_text[3:].strip()
            display_text = u"<b>AI：</b><br>" + display_text
        else:
            if display_text.startswith("系统："):
                 display_text = display_text[3:].strip()
            elif display_text.startswith("[系统]"):
                 display_text = display_text[4:].strip()
            display_text = u"<b>系统提示：</b><br>" + display_text

        # Format Markdown trivially to rich text
        display_text = display_text.replace("\n", "<br>")
        display_text = display_text.replace("```python", "<br><i>[Python Code]</i><br><code>").replace("```", "</code><br>")
        
        widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        bubble = QtWidgets.QFrame()
        if role == "user":
            bubble.setObjectName("BubbleUser")
        elif role == "ai":
            bubble.setObjectName("BubbleAI")
        else:
            bubble.setObjectName("BubbleSystem")
        bubble.setMaximumWidth(520)
        bubble_layout = QtWidgets.QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(12, 8, 12, 8)
        text_label = QtWidgets.QLabel(display_text)
        text_label.setObjectName("BubbleText")
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        bubble_layout.addWidget(text_label)

        avatar = QtWidgets.QLabel()
        avatar.setFixedSize(32, 32)
        if role == "user":
            if self._userAvatar:
                avatar.setPixmap(self._userAvatar)
            row.addStretch(1)
            row.addWidget(bubble)
            row.addWidget(avatar)
        else:
            if self._aiAvatar:
                avatar.setPixmap(self._aiAvatar)
            row.addWidget(avatar)
            row.addWidget(bubble)
            row.addStretch(1)

        item.setSizeHint(widget.sizeHint())
        item.setData(QtCore.Qt.UserRole, role)
        item.setData(QtCore.Qt.UserRole + 1, content)
        self.chatList.addItem(item)
        self.chatList.setItemWidget(item, widget)
        self.chatList.scrollToBottom()

        if kwargs.get("save", True):
            self._save_session_history()

    def log(self, text):
        role = "system"
        content = text
        
        # Try parse confirm JSON
        if content.startswith("{") and '"type": "confirm"' in content:
            try:
                data = json.loads(content)
                if data.get("type") == "confirm":
                    self._add_confirm_card(data)
                    return
            except Exception:
                pass

        if text.startswith("你："):
            role = "user"
            content = text[2:].strip()
        elif text.startswith("AI："):
            role = "ai"
            content = text[3:].strip()
        
        # Check for weird icon artifacts
        if content.startswith(u"：") or content.startswith(u"❖："):
             content = content[2:].strip()
             
        self._add_chat_bubble(role, content)

    def _add_confirm_card(self, data):
        action = data.get("action", "执行操作")
        options = data.get("options", [])
        tool = data.get("tool", "")
        
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(12, 8, 12, 8)
        
        bubble = QtWidgets.QFrame()
        bubble.setObjectName("BubbleSystem")
        bubble.setMaximumWidth(520)
        bubble_layout = QtWidgets.QVBoxLayout(bubble)
        
        title = QtWidgets.QLabel(u"系统提示：需要确认 %s" % action)
        title.setStyleSheet("font-weight: bold; color: #EAF2FF;")
        bubble_layout.addWidget(title)
        
        btn_layout = QtWidgets.QHBoxLayout()
        for opt in options:
            btn = QtWidgets.QPushButton(opt)
            btn.setObjectName("SecondaryButton")
            btn.setFixedHeight(30)
            def _make_callback(o=opt):
                def _cb():
                    # Send response back to chat
                    msg = u"我选择了: %s" % o
                    self._add_chat_bubble("user", msg)
                    self._send_text_to_agent(msg)
                    # Disable buttons to prevent multi-click
                    for i in range(btn_layout.count()):
                        w = btn_layout.itemAt(i).widget()
                        if w: w.setEnabled(False)
                return _cb
            btn.clicked.connect(_make_callback())
            btn_layout.addWidget(btn)
        
        btn_layout.addStretch(1)
        bubble_layout.addLayout(btn_layout)
        
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        avatar = QtWidgets.QLabel()
        avatar.setFixedSize(32, 32)
        if self._aiAvatar:
            avatar.setPixmap(self._aiAvatar)
        row.addWidget(avatar)
        row.addWidget(bubble)
        row.addStretch(1)
        layout.addLayout(row)
        
        item = QtWidgets.QListWidgetItem(self.chatList)
        item.setSizeHint(widget.sizeHint())
        
        # Save confirm JSON for history
        item.setData(QtCore.Qt.UserRole, "ai")
        item.setData(QtCore.Qt.UserRole + 1, json.dumps(data))
        
        self.chatList.addItem(item)
        self.chatList.setItemWidget(item, widget)
        self.chatList.scrollToBottom()
        self._save_session_history()

    def _send_text_to_agent(self, text):
        from functools import partial
        self.statusIndicator.setStyleSheet(self._base_style + "background-color: #21C7B7;")
        cb = partial(self._on_chat_finished)
        import threading
        t = threading.Thread(target=partial(self._chat_thread_func, text, self.history))
        t.daemon = True
        t.start()

    def on_save(self):
        self._ui_to_cfg()
        ok = cfgmod.save_config(self.cfg)
        self.log("[config] 保存%s" % ("成功" if ok else "失败"))

    def on_clear(self):
        self.history = []
        self.chatList.clear()
        ChatPersistence.clear()

    def _apply_provider_ui_state(self):
        p = str(self.provider.currentText()).strip().lower()
        if p == "gemini":
            model_value = self.cfg.get("model_gemini", "gemini-1.5-flash")
        else:
            model_value = self.cfg.get("model_deepseek", "deepseek-chat")
        self.modelInput.blockSignals(True)
        self.modelInput.setCurrentText(model_value)
        self.modelInput.blockSignals(False)
        self._refresh_key_status()

    def _refresh_key_status(self):
        provider = str(self.provider.currentText()).strip().lower()
        key_field = "gemini_api_key" if provider == "gemini" else "deepseek_api_key"
        key = self.cfg.get(key_field)
        
        if key:
            self.keyStatusLabel.setText("API Key: Configured")
            self.keyStatusLabel.setStyleSheet("color: #21C7B7;") # Green
            self.setKeyBtn.setVisible(False)
        else:
            self.keyStatusLabel.setText("API Key: Missing")
            self.keyStatusLabel.setStyleSheet("color: #FF5252;") # Red
            self.setKeyBtn.setVisible(True)

    def _on_set_key_clicked(self):
        self._show_api_key_dialog()

    def on_provider_changed(self, *_):
        current_model = str(self.modelInput.currentText()).strip()
        if self._last_provider == "gemini":
            self.cfg["model_gemini"] = current_model
        else:
            self.cfg["model_deepseek"] = current_model
        self._apply_provider_ui_state()
        self._last_provider = str(self.provider.currentText()).strip().lower()
        self._ui_to_cfg()
        cfgmod.save_config(self.cfg)

    def on_mode_changed(self, *_):
        self._ui_to_cfg()
        cfgmod.save_config(self.cfg)

    def _on_temp_slider_changed(self, value):
        self.temperatureValue.blockSignals(True)
        self.temperatureValue.setValue(float(value) / 100.0)
        self.temperatureValue.blockSignals(False)
        self._ui_to_cfg()
        cfgmod.save_config(self.cfg)

    def _on_temp_value_changed(self, value):
        self.temperatureSlider.blockSignals(True)
        self.temperatureSlider.setValue(int(float(value) * 100))
        self.temperatureSlider.blockSignals(False)
        self._ui_to_cfg()
        cfgmod.save_config(self.cfg)

    def on_health(self):
        self._ui_to_cfg()
        cfgmod.save_config(self.cfg)
        self.healthBtn.setEnabled(False)
        self.healthBtn.setText("Checking...")
        self.log("[系统] 正在检测网关连接...")
        t = threading.Thread(target=self._check_gateway_with_log)
        t.daemon = True
        t.start()

    def _check_gateway_with_log(self):
        url = (self.cfg.get("gateway_url") or "").rstrip("/") + "/health"
        try:
            self.signals.gateway_status.emit("Checking...", "color: #FFB020;")
            get_json(url, timeout_s=2)
            self.signals.gateway_status.emit("Connected", "color: #21C7B7;")
        except Exception as e:
            # 增加网络连通性检测
            is_online = False
            try:
                # 简单 ping 一下公网 (百度/谷歌/Cloudflare)
                # 使用 socket 连接检测比 ping 更快且跨平台
                import socket
                socket.create_connection(("www.baidu.com", 80), timeout=2)
                is_online = True
            except:
                pass
            
            if not is_online:
                self.signals.gateway_status.emit("Disconnected: No Internet", "color: #FF5252;")
            else:
                self.signals.gateway_status.emit("Disconnected", "color: #FF5252;")

    def _start_gateway_check_thread(self):
        t = threading.Thread(target=self._check_gateway_func)
        t.daemon = True
        t.start()

    def _check_gateway_func(self):
        url = (self.cfg.get("gateway_url") or "").rstrip("/") + "/health"
        try:
            self.signals.gateway_status.emit("Checking...", "color: #FFB020;")
            get_json(url, timeout_s=2)
            self.signals.gateway_status.emit("Connected", "color: #21C7B7;")
        except Exception as e:
            # 这里的异常会通过信号在 UI 线程处理
            self.signals.gateway_status.emit("Disconnected", "color: #FF5252;")

    def on_gateway_status(self, status, style):
        # 只有状态发生变化或者明确是 Checking... 才打印，避免轮询刷屏
        # 这里为了响应 Check 按钮，我们每次都打一下 Log 也不坏，但最好区分来源
        # 简单做：如果 status 是 Connected/Disconnected，我们在 Log 里提一句
        # 但要注意轮询线程也会触发这个。
        
        prev_status = self.gatewayStatusLabel.text()
        self.gatewayStatusLabel.setText(status)
        self.gatewayStatusLabel.setStyleSheet(style)
        
        self.healthBtn.setEnabled(True)
        self.healthBtn.setText("Check")
        
        if status == "Connected":
            self._gateway_running = True
            self.startGatewayBtn.setVisible(False)
            self.restartGatewayBtn.setVisible(True)
            if prev_status != "Connected":
                 self.log("[系统] 网关连接成功。")
        else:
            self._gateway_running = False
            self.restartGatewayBtn.setVisible(False)
            if status.startswith("Disconnected"):
                self.startGatewayBtn.setVisible(True)
                self.startGatewayBtn.setEnabled(True)
                self.startGatewayBtn.setText("Start Gateway")
                if prev_status != status:
                    self.log("[系统] 网关未连接。")
                    if "Bat missing" in status:
                         self.log("[系统] 错误：找不到启动脚本 (bridge/run_gateway.bat)。\n原因：插件安装目录不完整。\n请尝试重新下载源码包，不要只复制 maya_module。")
                    elif "No Internet" in status:
                         self.log("[系统] 错误：无法连接互联网。请检查网络设置。")
                    elif "Timeout" in status:
                         self.log("[系统] 错误：网关启动超时。可能是端口 (8765) 被占用，或者 Python 环境问题。")
                    elif ":" in status:
                        # 显示其他错误详情
                        self.log("[系统] 错误详情：%s" % status.split(":", 1)[1].strip())
            elif status == "Checking...":
                self.startGatewayBtn.setVisible(False)

    def _on_restart_gateway_clicked(self):
        # 强制重启：先尝试杀掉可能存在的进程，再启动
        self._on_start_gateway_clicked()

    def _on_start_gateway_clicked(self):
        # 1. 前置检查 API Key
        provider = (self.cfg.get("provider") or "deepseek").strip().lower()
        key_field = "gemini_api_key" if provider == "gemini" else "deepseek_api_key"
        if not self.cfg.get(key_field):
            self.log("[系统] 启动网关前请先配置 API Key。")
            if self._show_api_key_dialog():
                # 用户填了 Key，继续启动
                pass
            else:
                # 用户取消，终止启动
                self.log("[系统] 未配置 API Key，已取消启动网关。")
                return

        # 尝试杀掉可能存在的残留进程，确保启动环境干净
        try:
            gw_url = self.cfg.get("gateway_url", "http://127.0.0.1:8765")
            try:
                try: from urllib.parse import urlparse
                except ImportError: from urlparse import urlparse
                parsed = urlparse(gw_url)
                port = parsed.port or 8765
            except:
                port = 8765
            _kill_process_by_port(port)
        except Exception:
            pass

        self.startGatewayBtn.setEnabled(False)
        self.startGatewayBtn.setText("Starting...")
        self.log("[系统] 正在尝试启动网关... (首次启动可能需要1-2分钟安装依赖，请耐心等待)")
        
        t = threading.Thread(target=self._start_gateway_thread_func)
        t.daemon = True
        t.start()

    def _start_gateway_thread_func(self):
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            
            # 策略调整：
            # 1. 优先查找安装目录内的 bridge (modules/AIFORMAYA/bridge)
            # dock.py 位于 .../scripts/aiformaya/ui/dock.py
            # bridge 位于 .../bridge
            # 相对路径：../../../../bridge
            
            root_module = os.path.abspath(os.path.join(here, u"..", u"..", u"..", u".."))
            bat_path_module = os.path.join(root_module, "bridge", "run_gateway.bat")
            
            bat_path = None
            if os.path.exists(bat_path_module):
                bat_path = bat_path_module
            else:
                # 2. 如果没找到（旧版安装或开发环境），尝试向上查找源码包
                curr = here
                for i in range(6):
                    curr = os.path.dirname(curr)
                    check = os.path.join(curr, "bridge", "run_gateway.bat")
                    if os.path.exists(check):
                        bat_path = check
                        break
            
            if not bat_path:
                msg = "Disconnected: Bat missing"
                # Debug log
                self.signals.gateway_status.emit(msg + " Checked: %s" % str(bat_path_module), "color: #FF5252;")
                return

            work_dir = os.path.dirname(bat_path)
            # 使用 CREATE_NO_WINDOW (0x08000000) 隐藏黑框 (仅 Windows)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
            # 增加环境变量检查，确保 python 命令可用
            env = os.environ.copy()
            # 剥离 Maya 专有环境变量，避免干扰外部独立 Python3 环境
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            
            # 注入 API Key，避免后台启动时弹出交互式输入导致挂起
            if self.cfg.get("deepseek_api_key"):
                env["DEEPSEEK_API_KEY"] = str(self.cfg.get("deepseek_api_key"))
            if self.cfg.get("gemini_api_key"):
                env["GEMINI_API_KEY"] = str(self.cfg.get("gemini_api_key"))
                
            # 注入端口信息，确保网关监听端口与配置一致
            try:
                try:
                    from urllib.parse import urlparse
                except ImportError:
                    from urlparse import urlparse
                
                gw_url = self.cfg.get("gateway_url", "http://127.0.0.1:8765")
                parsed = urlparse(gw_url)
                if parsed.port:
                    env["GATEWAY_PORT"] = str(parsed.port)
            except Exception:
                pass
            
            # 使用 shell=False 避免 CMD 窗口闪烁问题，直接调用
            # 注意：使用 shell=True 配合 SW_HIDE 通常能隐藏窗口
            # 但如果 .bat 内部有 pause，会卡住。
            # run_gateway.bat 最后有 pause，这会导致静默启动卡死！
            # 我们需要修改 bat 或者用其他方式启动。
            # 由于不能改 bat（已发布），我们尝试用 powershell 直接启动 ps1
            # 或者，我们接受 bat 的 pause，但因为是后台进程，用户看不到，所以它会一直等...
            # 这是一个关键问题：run_gateway.bat 里的 pause 会阻塞进程结束，但对于 server 来说，
            # server 是在前台运行的，所以 bat 也是一直运行的，这没问题。
            # 问题是如果 server 启动失败，bat 会 pause，导致 python 进程不退出。
            
            # 使用 PIPE 捕获输出，以便分析错误
            proc = subprocess.Popen(
                [bat_path], 
                cwd=work_dir, 
                shell=True, 
                startupinfo=startupinfo, 
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # 轮询检查
            # 首次启动可能需要安装依赖 (venv, pip install)，耗时较长
            # 将超时时间延长到 120 秒
            max_retries = 120
            for i in range(max_retries):
                time.sleep(1)
                
                # 1. 快速失败检查：如果进程已退出，立即报错
                if proc.poll() is not None:
                    # 读取错误输出
                    try:
                        _, stderr_data = proc.communicate()
                        err_msg = stderr_data.decode("mbcs", "ignore") if stderr_data else "Process exited unexpectedly"
                    except:
                        err_msg = "Process exited unexpectedly"
                    
                    self.signals.gateway_status.emit("Disconnected: Startup Failed", "color: #FF5252;")
                    # 记录详细日志到聊天窗口，帮助排查
                    self.signals.chat_error.emit("网关启动失败，错误日志：\n" + err_msg)
                    return

                # 2. 正常健康检查
                # 每 5 秒 log 一次，让用户知道还在跑
                if i > 0 and i % 5 == 0:
                     self.signals.gateway_status.emit("Starting (%ds)..." % i, "color: #FFB020;")
                
                url = (self.cfg.get("gateway_url") or "").rstrip("/") + "/health"
                try:
                    get_json(url, timeout_s=1)
                    self.signals.gateway_status.emit("Connected", "color: #21C7B7;")
                    return
                except Exception:
                    pass

            self.signals.gateway_status.emit("Disconnected: Timeout", "color: #FF5252;")
            # 超时后尝试杀掉进程，避免僵尸
            try: proc.kill() 
            except: pass
            
        except Exception as e:
            self.signals.gateway_status.emit("Disconnected: %s" % str(e), "color: #FF5252;")

    def _show_api_key_dialog(self, provider=None):
        dlg = ApiKeyDialog(self)
        if dlg.exec_():
            key = dlg.get_key()
            if key:
                provider = str(self.provider.currentText()).strip().lower()
                if provider == "gemini":
                    self.cfg["gemini_api_key"] = key
                else:
                    self.cfg["deepseek_api_key"] = key
                cfgmod.save_config(self.cfg)
                self._refresh_key_status()
                return True
        return False

    def on_send(self):
        text = str(self.input.text()).strip()
        if not text:
            return
        self.input.setText("")

        force = False
        if text.startswith("!"):
            force = True
            text = text[1:].lstrip()

        # Gateway check
        if not self._gateway_running:
            self.log("[系统] 网关未连接，请点击上方 'Start Gateway' 按钮。")
            return

        self._ui_to_cfg()
        cfgmod.save_config(self.cfg)

        # API Key check
        provider = (self.cfg.get("provider") or "deepseek").strip().lower()
        key_field = "gemini_api_key" if provider == "gemini" else "deepseek_api_key"
        if not self.cfg.get(key_field):
            if not self._show_api_key_dialog():
                self.log("[系统] 未配置 API Key，无法发送消息。")
                return

        self.log("你：%s" % text)

        if not force and not self._is_maya_related(text):
            self.log("[local] 当前仅处理 Maya/动画相关问题。如需普通提问，请在前面加 !")
            return

        self._add_chat_bubble("ai", "...")
        self._ai_placeholder_item = self.chatList.item(self.chatList.count() - 1)

        self.sendBtn.setEnabled(False)
        self.input.setEnabled(False)

        import copy
        history_copy = copy.deepcopy(self.history)

        t = threading.Thread(target=functools.partial(self._chat_thread_func, text, history_copy))
        t.daemon = True
        t.start()

    def _chat_thread_func(self, text, history, config_copy=None):
        try:
            reply, new_history = run_chat(text, history_messages=history, max_turns=8)
            self.signals.chat_finished.emit(reply, new_history)
        except AgentError as e:
            self.signals.chat_error.emit(str(e))
        except Exception:
            self.signals.chat_error.emit(traceback.format_exc())

    def on_chat_finished(self, reply, new_history):
        self.history = new_history
        self.sendBtn.setEnabled(True)
        self.input.setEnabled(True)
        self.input.setFocus()

        if self._ai_placeholder_item:
            widget = self.chatList.itemWidget(self._ai_placeholder_item)
            if widget:
                label = widget.findChild(QtWidgets.QLabel, "BubbleText")
                if label:
                    label.setText(reply)
                    self._ai_placeholder_item.setSizeHint(widget.sizeHint())
            self._ai_placeholder_item = None
        else:
            self.log("AI：%s" % reply)
        self.chatList.scrollToBottom()

    def on_chat_error(self, error):
        self.sendBtn.setEnabled(True)
        self.input.setEnabled(True)
        self.input.setFocus()

        if self._ai_placeholder_item:
            widget = self.chatList.itemWidget(self._ai_placeholder_item)
            if widget:
                label = widget.findChild(QtWidgets.QLabel, "BubbleText")
                if label:
                    label.setText("[Error] " + error)
                    label.setStyleSheet("color: #FF5252;")
                    self._ai_placeholder_item.setSizeHint(widget.sizeHint())
            self._ai_placeholder_item = None
        else:
            self.log("[Error] %s" % error)
        self.chatList.scrollToBottom()

    def _is_maya_related(self, text):
        t = text.lower()
        kws = [
            "maya",
            "mesh",
            "joint",
            "camera",
            "ik",
            "fk",
            "create",
            "key",
            "keyframe",
            "graph editor",
            "timeline",
            "constraint",
            "anim",
            "动画",
            "场景",
            "模型",
            "物体",
            "节点",
            "关键帧",
            "摄像机",
            "镜头",
            "骨骼",
            "约束",
            "particle",
            "fx",
            "vfx",
        ]
        for k in kws:
            if k in t:
                return True
        if (
            u"创建" in text
            or u"立方体" in text
            or u"球" in text
            or u"帧" in text
            or u"粒子" in text
            or u"爆炸" in text
            or u"特效" in text
        ):
            return True
        name_tokens = ["pcube", "psphere", "locator", "ctrl", "cam"]
        for n in name_tokens:
            if n in t:
                return True
        return False


def _maya_main_window():
    try:
        import maya.OpenMayaUI as omui
        ptr = omui.MQtUtil.mainWindow()
        if ptr is None:
            return None
        return shiboken2.wrapInstance(long(ptr), QtWidgets.QWidget)
    except Exception:
        return None


def show():
    if QtWidgets is None:
        raise RuntimeError("PySide2 不可用，无法创建 UI")

    # Delete existing workspaceControl
    if cmds.workspaceControl(CONTROL_NAME, q=True, exists=True):
        cmds.deleteUI(CONTROL_NAME)

    cmds.workspaceControl(CONTROL_NAME, label="AIFORMAYA", floating=False, retain=False)

    # Get Qt pointer for workspaceControl
    import maya.OpenMayaUI as omui
    ptr = omui.MQtUtil.findControl(CONTROL_NAME)
    if ptr is None:
        ptr = omui.MQtUtil.findLayout(CONTROL_NAME)
    if ptr is None:
        ptr = omui.MQtUtil.findMenuItem(CONTROL_NAME)
    if ptr is None:
        raise RuntimeError("无法获取 workspaceControl 的 Qt 指针：%s" % CONTROL_NAME)
    qt_parent = shiboken2.wrapInstance(long(ptr), QtWidgets.QWidget)

    # Ensure a layout exists and add our widget
    lay = qt_parent.layout()
    if lay is None:
        lay = QtWidgets.QVBoxLayout(qt_parent)
        lay.setContentsMargins(0, 0, 0, 0)
    # Clean stale items if any
    while lay.count():
        item = lay.takeAt(0)
        w_old = item.widget()
        if w_old is not None:
            w_old.setParent(None)
    w = AiformayaWidget(parent=qt_parent)
    w.setMinimumSize(520, 600)
    w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
    lay.addWidget(w)
    w.show()

    # 注册退出清理回调
    _register_exit_callback()

    cmds.workspaceControl(CONTROL_NAME, e=True, restore=True)
    return w

