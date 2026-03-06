# -*- coding: utf-8 -*-
from __future__ import absolute_import

import logging

log = logging.getLogger("aiformaya")

try:
    from ..memory import EntityMemory
except ImportError:
    class EntityMemory(object):
        @classmethod
        def get_last_created(cls): return {}
        @classmethod
        def get_recent_objects(cls): return []


def resolve_scene_context(intent_targets, text, allow_implicit=True):
    """
    Priority Resolution:
    1. Explicit object name (e.g. if 'pSphere1' is in text and exists in scene)
    2. Selection (cmds.ls(sl=True))
    3. Last created (EntityMemory.get_last_created())
    4. Recent objects (EntityMemory.get_recent_objects())

    IMPORTANT: All maya.cmds calls MUST be wrapped in executeInMainThreadWithResult
    when called from a worker thread system.
    """
    context = {
        "selection": [],
        "last_created": {},
        "target_nodes": []  # The final resolved nodes for the action
    }

    # Gather live data safely - query cmds on the main thread
    def _query_scene():
        import maya.cmds as cmds
        return cmds.ls(sl=True) or [], cmds.ls(type="transform") or []

    live_sel = []
    all_transforms = []
    try:
        import maya.utils as maya_utils
        live_sel, all_transforms = maya_utils.executeInMainThreadWithResult(_query_scene)
    except Exception as e:
        log.debug("scene_context: could not query scene: %s", e)

    context["selection"] = live_sel

    last_created_dict = {}
    recent_objs = []
    try:
        last_created_dict = EntityMemory.get_last_created()
        recent_objs = EntityMemory.get_recent_objects()
    except Exception:
        pass
    context["last_created"] = last_created_dict

    resolved_nodes = []

    # 1. Explicit object name
    for t in all_transforms:
        if t in text:
            if t not in resolved_nodes:
                resolved_nodes.append(t)

    # 2. Selection (If user said "这个", "选择的", or just implicit with live selection)
    if not resolved_nodes and live_sel and (u"\u8fd9\u4e2a" in text or u"\u9009\u4e2d" in text or u"\u5f53\u524d" in text):
        resolved_nodes.extend(live_sel)

    # 3. Last Created (If user says "它", "刚才的", or no explicit target but we need one)
    if not resolved_nodes and (u"\u5b83" in text or u"\u521a\u624d" in text):
        if last_created_dict:
            first_val = list(last_created_dict.values())[0]
            if isinstance(first_val, list):
                resolved_nodes.extend(first_val)
            else:
                resolved_nodes.append(first_val)

    # Implicit fallback for targets
    if allow_implicit and not resolved_nodes and ("object" in intent_targets or "target" in intent_targets):
        if live_sel:
            resolved_nodes.extend(live_sel)
        elif recent_objs:
            resolved_nodes.append(recent_objs[-1])

    context["target_nodes"] = resolved_nodes
    return context
