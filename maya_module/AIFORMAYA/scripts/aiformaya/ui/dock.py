# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import sys
import traceback

import maya.cmds as cmds

from ..core import config as cfgmod
from ..core.agent import run_chat, AgentError
from ..core.http_client import get_json

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    import shiboken2
except Exception:
    QtCore = None
    QtGui = None
    QtWidgets = None
    shiboken2 = None


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

        self._build_ui()
        self._load_cfg_to_ui()

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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

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
        gateway_header.addStretch(1)
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
        gateway_layout.addLayout(gateway_row)
        layout.addWidget(gateway_card)

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
        provider_header.addStretch(1)
        provider_layout.addLayout(provider_header)
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
        layout.addWidget(provider_card)

        settings_card = QtWidgets.QFrame()
        settings_card.setObjectName("Card")
        settings_layout = QtWidgets.QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(16, 16, 16, 16)
        settings_layout.setSpacing(10)
        settings_header = QtWidgets.QHBoxLayout()
        settings_icon = QtWidgets.QLabel()
        self._set_label_icon(settings_icon, u"左侧分区图标（Settings).png", 18)
        settings_header.addWidget(settings_icon)
        settings_title = QtWidgets.QLabel("Settings")
        settings_title.setObjectName("SectionTitle")
        settings_header.addWidget(settings_title)
        settings_header.addStretch(1)
        settings_layout.addLayout(settings_header)
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
        settings_layout.addLayout(temp_row)
        mode_row = QtWidgets.QHBoxLayout()
        mode_label = QtWidgets.QLabel("Mode")
        mode_label.setObjectName("FieldLabel")
        mode_row.addWidget(mode_label)
        self.modeBox = QtWidgets.QComboBox()
        self.modeBox.addItems(["编辑模式", "问询模式"])
        self.modeBox.setFixedHeight(36)
        mode_row.addWidget(self.modeBox, 1)
        settings_layout.addLayout(mode_row)
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
        settings_layout.addLayout(btn_row)
        layout.addWidget(settings_card)

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

    def _add_chat_bubble(self, role, content):
        item = QtWidgets.QListWidgetItem(self.chatList)
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
        text_label = QtWidgets.QLabel(content)
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
        self.chatList.addItem(item)
        self.chatList.setItemWidget(item, widget)
        self.chatList.scrollToBottom()

    def log(self, text):
        role = "system"
        content = text
        if text.startswith("你："):
            role = "user"
            content = text[2:].strip()
        elif text.startswith("AI："):
            role = "ai"
            content = text[3:].strip()
        self._add_chat_bubble(role, content)

    def on_save(self):
        self._ui_to_cfg()
        ok = cfgmod.save_config(self.cfg)
        self.log("[config] 保存%s" % ("成功" if ok else "失败"))

    def on_clear(self):
        self.history = []
        self.chatList.clear()

    def _apply_provider_ui_state(self):
        p = str(self.provider.currentText()).strip().lower()
        if p == "gemini":
            model_value = self.cfg.get("model_gemini", "gemini-1.5-flash")
        else:
            model_value = self.cfg.get("model_deepseek", "deepseek-chat")
        self.modelInput.blockSignals(True)
        self.modelInput.setCurrentText(model_value)
        self.modelInput.blockSignals(False)

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
        url = (self.cfg.get("gateway_url") or "").rstrip("/") + "/health"
        try:
            data = get_json(url, timeout_s=10)
            self.log("[health] %s %s" % (url, str(data)))
        except Exception as e:
            self.log("[health] 请求失败：%s" % str(e))

    def on_send(self):
        text = str(self.input.text()).strip()
        if not text:
            return
        self.input.setText("")

        force = False
        if text.startswith("!"):
            force = True
            text = text[1:].lstrip()

        self._ui_to_cfg()
        cfgmod.save_config(self.cfg)

        self.log("你：%s" % text)

        if not force and not self._is_maya_related(text):
            self.log("[local] 当前仅处理 Maya/动画相关问题。如需普通提问，请在前面加 !")
            return

        try:
            reply, new_history = run_chat(text, history_messages=self.history, max_turns=8)
            self.history = new_history
            self.log("AI：%s" % reply)
        except AgentError as e:
            self.log("[error] %s" % str(e))
        except Exception:
            self.log("[error] 未知异常：\n%s" % traceback.format_exc())

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
    w.setMinimumSize(480, 360)
    w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
    lay.addWidget(w)
    w.show()

    cmds.workspaceControl(CONTROL_NAME, e=True, restore=True)
    return w

