# -*- coding: utf-8 -*-
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
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_json(path, data):
    try:
        with open(path, "w") as f:
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

    @classmethod
    def update_last_created(cls, entity_type, entity_name):
        data = cls.load()
        if "last_created" not in data:
            data["last_created"] = {}
        data["last_created"][entity_type] = entity_name
        
        if "recent_entities" not in data:
            data["recent_entities"] = []
        
        # Add to recent and deduplicate, keeping order
        if entity_name in data["recent_entities"]:
            data["recent_entities"].remove(entity_name)
        data["recent_entities"].append(entity_name)
        
        # Cap size
        if len(data["recent_entities"]) > MAX_MEMORY_ENTITIES:
            data["recent_entities"] = data["recent_entities"][-MAX_MEMORY_ENTITIES:]
            
        cls.save(data)

    @classmethod
    def get_summary(cls):
        data = cls.load()
        last = data.get("last_created", {})
        recent = data.get("recent_entities", [])
        
        if not last and not recent:
            return ""
            
        lines = ["【最近上下文（参考用）】"]
        if "camera" in last:
            lines.append("最新摄像机: %s" % last["camera"])
        if "sphere" in last:
            lines.append("最新球体: %s" % last["sphere"])
        if "cube" in last:
            lines.append("最新立方体: %s" % last["cube"])
            
        if recent:
            lines.append("最近操作过的对象: %s" % ", ".join(recent[-5:]))
            
        return "\n".join(lines)

class ChatPersistence(object):
    @classmethod
    def load(cls):
        data = _load_json(CHAT_FILE)
        return data.get("ui_history", []), data.get("agent_history", [])

    @classmethod
    def save(cls, ui_history, agent_history):
        # Cap histories to save space
        ui_hist = ui_history[-MAX_CHAT_MESSAGES:] if len(ui_history) > MAX_CHAT_MESSAGES else ui_history
        # For agent history, ensure we don't truncate exactly in the middle of a tool call / result pair
        # A simple cap is enough if we only persist complete user/assistant pairs
        ag_hist = agent_history[-MAX_CHAT_MESSAGES:] if len(agent_history) > MAX_CHAT_MESSAGES else agent_history
        
        data = {
            "saved_at": time.time(),
            "ui_history": ui_hist,
            "agent_history": ag_hist
        }
        _save_json(CHAT_FILE, data)
    
    @classmethod
    def clear(cls):
        _save_json(CHAT_FILE, {})
