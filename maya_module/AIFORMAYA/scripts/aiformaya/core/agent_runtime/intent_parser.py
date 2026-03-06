# -*- coding: utf-8 -*-
from __future__ import absolute_import
import re


class IntentParser(object):

    """
    Robust Natural Language Parser for Maya AI Agent Runtime.

    Returns:
    {
        "actions": [],
        "count": 1,
        "targets": [],
        "relations": [],
        "raw": text
    }
    """

    # ----------------------------
    # ACTION KEYWORDS
    # ----------------------------
    ACTION_MAP = {

        # create
        u"\u521b\u5efa": "create",
        u"\u751f\u6210": "create",
        u"\u505a": "create",
        u"\u52a0": "create",
        u"\u653e": "create",
        u"\u653e\u7f6e": "create",
        u"\u6446": "create",

        "create": "create",
        "make": "create",
        "spawn": "create",
        "add": "create",
        "place": "create",
        "put": "create",

        # duplicate
        u"\u590d\u5236": "duplicate",
        u"\u514b\u9686": "duplicate",

        "duplicate": "duplicate",
        "clone": "duplicate",

        # rotate
        u"\u65cb\u8f6c": "rotate",
        u"\u8f6c": "rotate",
        u"\u7ed5": "rotate",

        "rotate": "rotate",
        "spin": "rotate",

        # bounce
        u"\u5f39\u8df3": "bounce",
        u"\u8df3": "bounce",

        "bounce": "bounce",

        # scatter
        u"\u6563\u5e03": "scatter",
        u"\u968f\u673a": "scatter",
        u"\u968f\u673a\u6446": "scatter",
        u"\u968f\u673a\u653e": "scatter",
        u"\u968f\u673a\u5206\u5e03": "scatter",
        u"\u94fa\u6ee1": "scatter",

        "scatter": "scatter",
        "spread": "scatter",

        # look
        u"\u770b": "look_at",
        u"\u770b\u5411": "look_at",
        u"\u5bf9\u51c6": "look_at",

        "look": "look_at",
        "aim": "look_at",

        # roll
        u"\u6eda": "roll",
        u"\u6eda\u52a8": "roll",

        "roll": "roll",

        # move
        u"\u79fb\u52a8": "move",
        u"\u631a": "move",

        "move": "move",

        # fracture
        u"\u788e": "fracture",
        u"\u6253\u788e": "fracture",
        u"\u7834\u788e": "fracture",

        "fracture": "fracture",

        # delete
        u"\u5220\u9664": "delete",
        u"\u6e05\u7a7a": "delete",

        "delete": "delete",
    }

    # ----------------------------
    # OBJECT KEYWORDS
    # ----------------------------
    OBJECT_MAP = {

        # sphere
        u"\u5c0f\u7403": "sphere",
        u"\u5706\u7403": "sphere",
        u"\u7403": "sphere",

        "sphere": "sphere",
        "ball": "sphere",

        # cube
        u"\u7acb\u65b9\u4f53": "cube",
        u"\u65b9\u5757": "cube",
        u"\u76d2\u5b50": "cube",

        "cube": "cube",
        "box": "cube",

        # cylinder
        u"\u5706\u67f1": "cylinder",
        u"\u67f1\u5b50": "cylinder",

        "cylinder": "cylinder",

        # plane  (longer keywords first so sorted() picks them up before "\u9762")
        u"\u5730\u9762": "plane",
        u"\u5e73\u9762": "plane",
        u"\u5730\u677f": "plane",
        u"\u5e73\u53f0": "plane",
        u"\u684c\u5b50": "plane",
        u"\u684c\u9762": "plane",
        u"\u9762": "plane",

        "ground": "plane",
        "floor": "plane",
        "table": "plane",
        "plane": "plane",

        # camera
        u"\u6444\u50cf\u673a": "camera",
        u"\u76f8\u673a": "camera",
        u"\u955c\u5934": "camera",

        "camera": "camera",

        # light
        u"\u706f\u5149": "light",
        u"\u5149\u6e90": "light",
        u"\u706f": "light",

        "light": "light",

        # generic
        u"\u7269\u4f53": "object",
        u"\u5bf9\u8c61": "object",
        u"\u4e1c\u897f": "object",

        "object": "object",

        u"\u76ee\u6807": "target",
        "target": "target",
    }

    # ----------------------------
    # RELATIONS
    # ----------------------------
    RELATION_MAP = {

        # around
        u"\u56f4\u7ed5": "around",
        u"\u56f4\u7740": "around",
        u"\u73af\u7ed5": "around",

        "around": "around",

        # on top
        u"\u4e0a\u9762": "on_top_of",
        u"\u9876\u4e0a": "on_top_of",
        u"\u653e\u4e0a": "on_top_of",
        u"\u6446\u5728": "on_top_of",
        u"\u653e\u5728": "on_top_of",

        "on_top": "on_top_of",
        "on": "on_top_of",

        # next to
        u"\u65c1\u8fb9": "next_to",
        u"\u8fb9\u4e0a": "next_to",

        "next_to": "next_to",
        "beside": "next_to",

        # line
        u"\u6392\u6210\u4e00\u6392": "line_up",
        u"\u6392\u4e00\u6392": "line_up",
        u"\u6392\u961f": "line_up",

        "line_up": "line_up",

        # inside
        u"\u91cc\u9762": "inside",

        "inside": "inside",
    }

    # ----------------------------
    # NUMBER WORDS
    # ----------------------------
    CHINESE_NUMS = {
        u"\u4e00": 1,
        u"\u4e8c": 2,
        u"\u4e24": 2,
        u"\u4e09": 3,
        u"\u56db": 4,
        u"\u4e94": 5,
        u"\u516d": 6,
        u"\u4e03": 7,
        u"\u516b": 8,
        u"\u4e5d": 9,
        u"\u5341": 10,
        u"\u767e": 100,

        u"\u591a\u4e2a": 5,
        u"\u5f88\u591a": 10,
        u"\u4e00\u5806": 10,
        u"\u4e00\u6392": 5,
    }

    # ----------------------------
    # PARSER
    # ----------------------------
    @classmethod
    def parse(cls, text):

        intent = {
            "actions": [],
            "count": 1,
            "targets": [],
            "relations": [],
            "raw": text,
        }

        if not text:
            return intent

        lower = text.lower()

        # ----------------------------
        # ACTION DETECTION
        # ----------------------------
        for kw in sorted(cls.ACTION_MAP.keys(), key=len, reverse=True):
            if kw in text or kw in lower:
                act = cls.ACTION_MAP[kw]
                if act not in intent["actions"]:
                    intent["actions"].append(act)

        # ----------------------------
        # OBJECT DETECTION
        # ----------------------------
        for kw in sorted(cls.OBJECT_MAP.keys(), key=len, reverse=True):
            if kw in text or kw in lower:
                obj = cls.OBJECT_MAP[kw]
                if obj not in intent["targets"]:
                    intent["targets"].append(obj)

        # ----------------------------
        # RELATION DETECTION
        # ----------------------------
        for kw in sorted(cls.RELATION_MAP.keys(), key=len, reverse=True):
            if kw in text or kw in lower:
                rel = cls.RELATION_MAP[kw]
                if rel not in intent["relations"]:
                    intent["relations"].append(rel)

        # special rule: \u5728XXX\u4e0a  (e.g. "\u5728\u5730\u9762\u4e0a", "\u5728\u684c\u5b50\u4e0a")
        if u"\u5728" in text and u"\u4e0a" in text:
            if "on_top_of" not in intent["relations"]:
                intent["relations"].append("on_top_of")

        # ----------------------------
        # COUNT DETECTION
        # ----------------------------
        digit_match = re.search(r'(\d+)', text)

        if digit_match:
            intent["count"] = int(digit_match.group(1))
        else:
            # Check Chinese number words (longer first for "\u591a\u4e2a" etc.)
            for kw in sorted(cls.CHINESE_NUMS.keys(), key=len, reverse=True):
                if kw in text:
                    intent["count"] = cls.CHINESE_NUMS[kw]
                    break

        return intent


def parse_intent(text):
    """Module-level convenience function."""
    return IntentParser.parse(text)
