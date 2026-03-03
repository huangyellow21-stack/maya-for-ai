# -*- coding: utf-8 -*-
from __future__ import absolute_import

try:
    basestring
except NameError:
    basestring = str


GROUP_MAP = {
    "translate": ["tx", "ty", "tz"],
    "rotate": ["rx", "ry", "rz"],
    "scale": ["sx", "sy", "sz"],
    "transform": ["tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"],
}

SHORT_ALLOWED = set(GROUP_MAP["transform"])


def expand_attributes(tokens):
    """
    tokens:
      - None / [] : caller decides default behavior
      - ["translate","tx","ry"] : expands & validates
    """
    if tokens is None:
        return None
    if not isinstance(tokens, (list, tuple)):
        raise ValueError("attributes must be an array")
    if len(tokens) == 0:
        return []

    out = []
    seen = set()
    for t in tokens:
        if not isinstance(t, basestring):
            raise ValueError("attributes items must be string")
        key = t.strip()
        if not key:
            continue
        low = key.lower()
        if low in GROUP_MAP:
            for a in GROUP_MAP[low]:
                if a not in seen:
                    out.append(a)
                    seen.add(a)
            continue
        if low in SHORT_ALLOWED:
            if low not in seen:
                out.append(low)
                seen.add(low)
            continue
        raise ValueError("unsupported attribute token: %s" % key)
    return out

