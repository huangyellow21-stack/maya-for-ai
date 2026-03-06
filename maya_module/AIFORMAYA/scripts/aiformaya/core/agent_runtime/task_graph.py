# -*- coding: utf-8 -*-
"""
AIFORMAYA v3.0  —  Task Graph Builder
Imposes sequence constraints on Capabilities for predictable execution ordering.
"""
import logging

log = logging.getLogger("aiformaya")

# Guaranteed Execution Order
ORDER = [
    "CREATE_OBJECT",
    "DUPLICATE_OBJECTS",
    "SCATTER_AROUND",
    "PLACE_ON_TOP",
    "RANDOM_SCATTER",
    "SURFACE_ATTACH",
    "CONSTRAINT_BIND",
    "ROTATE_ANIMATION",
    "ROLL_ANIMATION",
    "BOUNCE_ANIMATION",
    "CAMERA_LOOK",
    "FOLLOW_CAMERA",
    "SCENE_CLEANUP",
    "OBJECT_FRACTURE"
]


def build_task_graph(capabilities):
    """
    Given an unordered list of mapped capabilities, rebuilds them
    in a logical sequence to prevent runtime animation errors or context overlaps.
    """
    # Use list order preserving deduplication over sets which randomize
    unique_cap = []
    for c in capabilities:
        if c not in unique_cap:
            unique_cap.append(c)

    graph = []

    for step in ORDER:
        if step in unique_cap:
            graph.append(step)

    # Note any unrecognized capability mapped outside the normal list
    for step in unique_cap:
        if step not in graph:
            graph.append(step)
            
    log.debug("Task Graph -> Built Ordered Sequence: %s", graph)
    return graph
