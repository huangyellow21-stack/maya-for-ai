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
    
def get_cached_plan(text):
    """
    Look up a previously verified plan using the normalized input text.
    """
    key = _normalize_text(text)
    if not key:
        return None
        
    with _cache_lock:
        pl = PLAN_CACHE.get(key)
        if pl:
            log.info(u"Cache Hit for '%s'", key)
            # Move to end to represent recently used
            PLAN_CACHE.move_to_end(key)
            return pl
    return None

def save_plan(text, plan):
    """
    Save a generated plan to the cache.
    """
    key = _normalize_text(text)
    if not key or not plan:
        return
        
    log.info(u"Caching plan for '%s'", key)
    with _cache_lock:
        PLAN_CACHE[key] = plan
        
        # Enforce MAX_CACHE constraint
        if len(PLAN_CACHE) > MAX_CACHE:
            PLAN_CACHE.popitem(last=False)
