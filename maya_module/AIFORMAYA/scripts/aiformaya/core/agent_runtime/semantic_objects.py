# -*- coding: utf-8 -*-
"""
AIFORMAYA v3.0  —  Semantic Object Resolver
Evaluates the parsed targets and assigns explicit roles (subject, environment, camera).
"""
import logging

log = logging.getLogger("aiformaya")


def resolve_semantic_objects(intent):
    """
    Parses intent targets and assigns semantic roles for procedural stability.
    Returns:
        dict: {"subject": str or None, "environment": str or None, "camera": str or None}
    """
    targets = intent.get("targets", [])

    subject = None
    environment = None
    camera = None

    for t in targets:
        # Canonical type mapping
        if t in ["sphere", "\u7403", "\u5c0f\u7403", "cube", "\u65b9\u5757", "\u7acb\u65b9\u4f53", "cylinder", "\u5706\u67f1", "mesh", "\u7269\u4f53", "\u6a21\u578b", "object"]:
            if not subject:
                # We normalize the subject output to a standard maya node type if possible, or just the word
                if t in ["sphere", "\u7403", "\u5c0f\u7403"]:
                    subject = "sphere"
                elif t in ["cube", "\u65b9\u5757", "\u7acb\u65b9\u4f53"]:
                    subject = "cube"
                elif t in ["cylinder", "\u5706\u67f1"]:
                    subject = "cylinder"
                elif t in ["mesh", "\u7269\u4f53", "\u6a21\u578b", "object"]:
                    subject = "mesh"
                else:
                    subject = t

        elif t in ["plane", "ground", "\u5730\u9762", "\u5730\u677f", "\u5e73\u9762"]:
            environment = "plane"

        elif t in ["camera", "\u6444\u50cf\u673a", "\u955c\u5934", "cam"]:
            camera = "camera"

    semantic = {
        "subject": subject,
        "environment": environment,
        "camera": camera
    }
    
    log.debug("Semantic Object Resolver -> %s", semantic)
    return semantic
