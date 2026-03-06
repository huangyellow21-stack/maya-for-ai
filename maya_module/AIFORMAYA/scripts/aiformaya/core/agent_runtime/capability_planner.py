# -*- coding: utf-8 -*-

def plan_capabilities(intent):
    """
    Input:
    {
      "actions": ["create", "rotate", "scatter"],
      "count": 10,
      "targets": ["sphere", "target"],
      "relations": ["around"]
    }
    
    Output:
    ["CREATE_OBJECT", "DUPLICATE_OBJECTS", "SCATTER_AROUND", "ROTATE_ANIMATION"]
    """
    capabilities = []
    
    acts = intent.get("actions", [])
    tgs = intent.get("targets", [])
    rels = intent.get("relations", [])
    count = intent.get("count", 1)

    # 1. Creation & Duplication
    # Note: FX_EXPLOSION is checked later; if it fires, CREATE_OBJECT is suppressed
    if "create" in acts:
        capabilities.append("CREATE_OBJECT")
        if count > 1:
            capabilities.append("DUPLICATE_OBJECTS")
    elif "duplicate" in acts or count > 1:
        capabilities.append("DUPLICATE_OBJECTS")

    # 2. Relation (Spatial)
    if "around" in rels and "rotate" not in acts:
        capabilities.append("SCATTER_AROUND")
    if "on_top_of" in rels:
        capabilities.append("PLACE_ON_TOP")
    if "next_to" in rels:
        capabilities.append("PLACE_NEXT_TO")
    if "line_up" in rels:
        capabilities.append("LINE_UP")
    if "inside" in rels:
        capabilities.append("PLACE_INSIDE")
    if "scatter" in acts:
        capabilities.append("RANDOM_SCATTER")

    # 3. Animation
    if "rotate" in acts:
        if "around" in rels:
            capabilities.append("ORBIT_ANIMATION")
        else:
            capabilities.append("ROTATE_ANIMATION")
    if "bounce" in acts:
        capabilities.append("BOUNCE_ANIMATION")
    if "roll" in acts:
        capabilities.append("ROLL_ANIMATION")
    if "move" in acts or "attach" in rels:
        capabilities.append("SURFACE_ATTACH")

    # 4. Camera & View
    if "look_at" in acts or "follow" in acts:
        if "around" in rels:
            capabilities.append("CAMERA_LOOK")
        else:
            capabilities.append("FOLLOW_CAMERA")
    
    # 5. Physics / FX
    if "fracture" in acts:
        capabilities.append("OBJECT_FRACTURE")

    # 6. Constraints
    if "constraint" in acts:
        capabilities.append("CONSTRAINT_BIND")

    # 7. Scene edits
    if "delete" in acts:
        capabilities.append("SCENE_CLEANUP")

    # 8. High-level Presentation / FX Workflows
    # Turntable / Product showcase
    if "present" in acts or "turntable" in tgs:
        capabilities.append("PRODUCT_TURNTABLE")

    # Three-point lighting / FX lighting
    if "light" in acts or "three_point_lighting" in tgs or "light" in tgs:
        # Only if not a simple light-creation, i.e. it's about illuminating scene
        capabilities.append("FX_LIGHTING")

    # FX Explosion — always route to template; also fires from 'explode' action
    if "explode" in acts or any(t in tgs for t in ("explosion", "bomb", "fx")):
        capabilities.append("FX_EXPLOSION")

    # Deduplicate while preserving order
    seen = set()
    ordered_caps = []
    for cap in capabilities:
        if cap not in seen:
            ordered_caps.append(cap)
            seen.add(cap)

    # FX conflict guard: if FX_EXPLOSION is present, remove CREATE_OBJECT (prevent sphere fallback)
    if "FX_EXPLOSION" in ordered_caps and "CREATE_OBJECT" in ordered_caps:
        ordered_caps = [c for c in ordered_caps if c != "CREATE_OBJECT"]

    return ordered_caps
