# -*- coding: utf-8 -*-
from __future__ import absolute_import
import re

def analyze_task(user_text):
    """
    Classify the user request into:
    - SCRIPT_TASK
    - MULTI_STEP_TASK
    - BATCH_TASK
    - SIMPLE_TOOL
    """
    text = user_text.lower()
    
    # 1. Check script
    script_kws = [u"脚本", u"代码", u"循环", "python", "script", "for"]
    if any(k in user_text for k in script_kws) or "for " in text:
        return "SCRIPT_TASK"
        
    # 2. Check multi step
    multi_kws = [u"然后", u"再", u"接着", u"并", u"之后", u"同时", "then", "and", "after", "next"]
    # Chinese requires exact match, English can be matched in lower text
    for kw in multi_kws:
        if all(ord(c) < 128 for c in kw):
            # English: ensure word boundary
            if re.search(r'\b' + kw + r'\b', text):
                return "MULTI_STEP_TASK"
        else:
            if kw in user_text:
                return "MULTI_STEP_TASK"
                
    # 3. Check batch: must contain a number greater than 1 AND a valid creation/generation word
    text_nums = re.sub(u'[一二三四五六七八九十百千万两]', '1', user_text)
    # Convert '十' manually or roughly just rely on keywords. Let's just find all numbers
    # To avoid "创建一个" triggering batch:
    found_nums = [int(n) for n in re.findall(r'\d+', text_nums)]
    is_plural = any(n > 1 for n in found_nums)
    
    if is_plural or u"批量" in user_text or u"多个" in user_text:
        create_kws = [u"创建", u"生成", u"做一个", u"来个", "create", "make", "add", "spawn"]
        if any(kw in user_text.lower() or kw in user_text for kw in create_kws):
            return "BATCH_TASK"
                
    return "SIMPLE_TOOL"
