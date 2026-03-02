# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os


def _user_config_dir():
    # Maya on Windows usually has MAYA_APP_DIR, otherwise fall back to Documents\maya
    base = os.environ.get("MAYA_APP_DIR")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "Documents", "maya")
    d = os.path.join(base, "AIFORMAYA")
    if not os.path.isdir(d):
        try:
            os.makedirs(d)
        except Exception:
            pass
    return d


def config_path():
    return os.path.join(_user_config_dir(), "config.json")


DEFAULT_CONFIG = {
    "gateway_url": "http://127.0.0.1:8765",
    "provider": "deepseek",  # deepseek | gemini
    "model_deepseek": "deepseek-chat",
    "model_gemini": "gemini-1.5-flash",
    "temperature": 0.2,
    "mode": "edit",
}


def load_config():
    path = config_path()
    if not os.path.exists(path):
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if not raw:
            return dict(DEFAULT_CONFIG)
        cfg = json.loads(raw.decode("utf-8"))
        out = dict(DEFAULT_CONFIG)
        if isinstance(cfg, dict):
            out.update(cfg)
        return out
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    path = config_path()
    try:
        data = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
        with open(path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False

