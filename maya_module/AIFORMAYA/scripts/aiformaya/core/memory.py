# -*- coding: utf-8 -*-
"""
AIFORMAYA v2.0 — Entity Memory (upgraded)
Tracks: last_created, last_selected, last_camera, recent_objects
"""
import os
import json
import time

MEMORY_FILE = os.path.expanduser("~/.aiformaya_memory.json")
CHAT_FILE = os.path.expanduser("~/.aiformaya_chat.json")

MAX_CHAT_MESSAGES = 100
MAX_MEMORY_ENTITIES = 20


def _load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class EntityMemory(object):

    @classmethod
    def load(cls):
        return _load_json(MEMORY_FILE)

    @classmethod
    def save(cls, data):
        _save_json(MEMORY_FILE, data)

    # ── Create ──
    @classmethod
    def update_last_created(cls, entity_type, entity_name):
        data = cls.load()
        if "last_created" not in data:
            data["last_created"] = {}
        data["last_created"][entity_type] = entity_name
        cls._add_to_recent(data, entity_name)
        cls.save(data)

    # ── Selection ──
    @classmethod
    def update_last_selected(cls, name):
        if not name:
            return
        data = cls.load()
        data["last_selected"] = name
        cls._add_to_recent(data, name)
        cls.save(data)

    # ── Camera ──
    @classmethod
    def update_last_camera(cls, name):
        if not name:
            return
        data = cls.load()
        data["last_camera"] = name
        cls.save(data)

    # ── Recent objects (list) ──
    @classmethod
    def update_recent_objects(cls, names):
        if not names:
            return
        data = cls.load()
        for n in names:
            cls._add_to_recent(data, n)
        cls.save(data)

    # ── Last action ──
    @classmethod
    def update_last_action(cls, action):
        """Record the most recently executed tool name."""
        if not action:
            return
        data = cls.load()
        data["last_action"] = action
        cls.save(data)

    @staticmethod
    def _add_to_recent(data, name):
        if not name:
            return
        if "recent_objects" not in data:
            data["recent_objects"] = []
        lst = data["recent_objects"]
        if name in lst:
            lst.remove(name)
        lst.append(name)
        if len(lst) > MAX_MEMORY_ENTITIES:
            data["recent_objects"] = lst[-MAX_MEMORY_ENTITIES:]

    # ── Summary for prompt injection ──
    @classmethod
    def get_summary(cls):
        data = cls.load()
        last = data.get("last_created", {})
        recent = data.get("recent_objects", [])
        last_sel = data.get("last_selected", "")
        last_cam = data.get("last_camera", "")
        last_action = data.get("last_action", "")

        if not last and not recent and not last_sel and not last_cam:
            return ""

        lines = [u"\u300a\u6700\u8fd1\u4e0a\u4e0b\u6587\u300b"]  # 《最近上下文》
        if last_action:
            lines.append(u"\u4e0a\u4e00\u6b65操作: %s" % last_action)
        if last_sel:
            lines.append(u"\u6700\u65b0\u9009\u4e2d\u5bf9\u8c61: %s" % last_sel)
        if last_cam:
            lines.append(u"\u6700\u65b0\u6444\u50cf\u673a: %s" % last_cam)
        for t, n in list(last.items())[:4]:
            lines.append(u"\u6700\u65b0\u521b\u5efa (%s): %s" % (t, n))
        if recent:
            lines.append(u"\u6700\u8fd1\u5bf9\u8c61: %s" % u", ".join(recent[-6:]))

        return u"\n".join(lines)


class ChatPersistence(object):
    @classmethod
    def load(cls):
        data = _load_json(CHAT_FILE)
        return data.get("ui_history", []), data.get("agent_history", [])

    @classmethod
    def save(cls, ui_history, agent_history):
        ui_hist = ui_history[-MAX_CHAT_MESSAGES:]
        ag_hist = agent_history[-MAX_CHAT_MESSAGES:]
        data = {
            "saved_at": time.time(),
            "ui_history": ui_hist,
            "agent_history": ag_hist,
        }
        _save_json(CHAT_FILE, data)

    @classmethod
    def clear(cls):
        _save_json(CHAT_FILE, {})
