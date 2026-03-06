# -*- coding: utf-8 -*-
from __future__ import absolute_import

import logging

import threading
import re
from collections import OrderedDict

log = logging.getLogger("aiformaya")

# In-memory plan dictionary with max size
MAX_CACHE = 200
PLAN_CACHE = OrderedDict()
_cache_lock = threading.Lock()

def _normalize_text(text):
    text = (text or "").lower()
    return re.sub(r"\s+", "", text)
    
def get_cached_plan(text, intent=None):
    """
    Look up a previously verified plan using the normalized input text or intent logic hash.
    
    DISABLED: Cache keys don't include scene context hash, so cached plans
    can be reused in the wrong scene state (e.g. referencing objects that
    no longer exist). Will be re-enabled once scene hashing is added.
    """
    return None

def save_plan(text, plan, intent=None):
    """
    Save a generated plan to the cache.
    """
    if intent:
        import json
        intent_copy = dict(intent)
        intent_copy.pop("raw", None)
        key = json.dumps(intent_copy, sort_keys=True, ensure_ascii=False)
    else:
        key = _normalize_text(text)
        
    if not key or not plan:
        return
        
    log.info(u"Caching plan for '%s'", key)
    with _cache_lock:
        PLAN_CACHE[key] = plan
        
        # Enforce MAX_CACHE constraint
        if len(PLAN_CACHE) > MAX_CACHE:
            PLAN_CACHE.popitem(last=False)
