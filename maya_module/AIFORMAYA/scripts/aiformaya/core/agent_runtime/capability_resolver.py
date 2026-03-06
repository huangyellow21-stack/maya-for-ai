# -*- coding: utf-8 -*-

# Map core capabilities to arrays of tools (primary, fallback)
CAPABILITY_TOOL_MAP = {
    "CREATE_OBJECT": {
        "sphere": ["maya.create_sphere"],
        "cube": ["maya.create_cube"],
        "cylinder": ["maya.create_cylinder"],
        "plane": ["maya.create_plane"],
        "camera": ["maya.create_camera"],
        "light": ["maya.create_three_point_lighting"]
    },
    "DUPLICATE_OBJECTS": ["maya.execute_python_code", "maya.duplicate_objects"],
    "SCATTER_AROUND": ["maya.execute_python_code"],
    "PLACE_ON_TOP": ["maya.execute_python_code"],
    "PLACE_NEXT_TO": ["maya.execute_python_code"],
    "LINE_UP": ["maya.execute_python_code"],
    "PLACE_INSIDE": ["maya.execute_python_code"],
    "RANDOM_SCATTER": ["maya.randomize_transforms", "maya.execute_python_code"],
    "ROTATE_ANIMATION": ["maya.create_loop_rotate", "maya.execute_python_code"],
    "ORBIT_ANIMATION": ["maya.execute_python_code"], # complex orbit needs custom code
    "BOUNCE_ANIMATION": ["maya.create_bouncing_ball", "maya.execute_python_code"],
    "CAMERA_LOOK": ["maya.camera_look_at", "maya.execute_python_code"],
    "CONSTRAINT_BIND": ["maya.execute_python_code"],
    "OBJECT_FRACTURE": ["maya.execute_python_code"], # Intentionally missing plugins
    "SCENE_CLEANUP": ["maya.delete_selected", "maya.cleanup_scene"],
    "ROLL_ANIMATION": ["maya.create_loop_rotate", "maya.execute_python_code"],
    "SURFACE_ATTACH": ["maya.execute_python_code"],
    "FOLLOW_CAMERA": ["maya.camera_look_at", "maya.execute_python_code"]
}

def resolve_capabilities(capabilities, targets, available_tools_schema):
    """
    Returns an ordered list of matched tools for the planned capabilities.
    If a capability matches no accessible tools, returns a suggestion wrapper.
    """
    available_tool_names = set(t["name"] for t in available_tools_schema)
    resolved_tools = []
    unsupported_suggestions = []

    # Gather ALL creation targets (support multi-type creation like "plane + spheres")
    creation_targets = []
    for tg in ["plane", "sphere", "cube", "cylinder", "camera", "light"]:
        if tg in targets:
            creation_targets.append(tg)
    if not creation_targets:
        creation_targets = ["sphere"]  # default

    for cap in capabilities:
        tools_for_cap = []
        if cap == "CREATE_OBJECT":
            # Resolve ALL creation targets, not just the first one
            for ct in creation_targets:
                ct_tools = CAPABILITY_TOOL_MAP[cap].get(ct, [])
                for t_name in ct_tools:
                    if t_name in available_tool_names:
                        resolved_tools.append({
                            "capability": cap,
                            "tool": t_name,
                        })
                        break
            continue  # Already handled
        else:
            tools_for_cap = CAPABILITY_TOOL_MAP.get(cap, [])

        matched_tool = None
        for t_name in tools_for_cap:
            if t_name in available_tool_names:
                matched_tool = t_name
                break
            elif t_name == "maya.execute_python_code" and "maya.execute_python_code" in available_tool_names:
                matched_tool = t_name
                break

        if matched_tool:
            resolved_tools.append({
                "capability": cap,
                "tool": matched_tool
            })
        else:
            # Generate fallback suggestions
            if cap == "OBJECT_FRACTURE":
                unsupported_suggestions.append(u"当前未检测到打碎(Fracture)相关的工具或插件。建议您安装 Voronoi Fracture 或利用 Bullet 建立物理动力学解算。")
            else:
                unsupported_suggestions.append(u"当前系统缺失处理 '%s' 操作的专门能力。" % cap)

    return resolved_tools, unsupported_suggestions
