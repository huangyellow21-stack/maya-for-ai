# -*- coding: utf-8 -*-
from .spatial_reasoning import calculate_spatial_offsets

def _fmt_target(t, plan_vars):
    if not t: return ""
    return "{%s}" % t if (t in plan_vars or t.startswith("{")) else t

def generate_plan(intent, resolved_tools, scene_context, semantic):
    """
    Translates ordered resolved tools + intent + context + semantic objects into the final Plan JSON 
    used by `execute_plan`. Incorporates explicit `save_as` tracking.
    """
    
    plan = {
        "steps": []
    }
    plan_vars = set()
    
    act_count = intent.get("count", 1)
    relations = intent.get("relations", [])
    targets = intent.get("targets", [])
    scene_nodes = scene_context.get("target_nodes", [])

    semantic_subject = semantic.get("subject")
    semantic_env = semantic.get("environment")
    semantic_camera = semantic.get("camera")

    # --- Capability Merging Pre-processing ---
    all_caps = [r["capability"] for r in resolved_tools]
    
    # If BOUNCE_ANIMATION exists, skip sphere creation
    skip_create_sphere = "BOUNCE_ANIMATION" in all_caps
    
    # If ORBIT_ANIMATION exists and there's a camera target, skip camera creation
    skip_create_camera = "ORBIT_ANIMATION" in all_caps and semantic_camera
    # ----------------------------------------

    subject_object = None
    environment_object = None
    camera_object = None
    
    target_var = None
    if scene_nodes:
        # User defined an existing node as the relationship target
        target_var = scene_nodes[0]
        # Treat scene target as subject initially if no new objects are created
        if not subject_object:
            subject_object = target_var
        if not environment_object:
            environment_object = target_var
    
    var_counter = 1

    for res in resolved_tools:
        cap = res["capability"]
        tool = res["tool"]

        if cap == "CREATE_OBJECT":
            # Determine type from tool name if possible, or fallback to intent targets
            obj_type = "sphere"
            tool_suffix = tool.replace("maya.create_", "")
            if tool_suffix in ["sphere", "cube", "cylinder", "plane", "camera", "light"]:
                obj_type = tool_suffix
            else:
                for tg in ["sphere", "cube", "cylinder", "plane", "camera", "light"]:
                    if tg in targets:
                        obj_type = tg
                        break
                        
            # --- Skip Merged Creations ---
            if obj_type == "sphere" and skip_create_sphere:
                continue
            if obj_type == "camera" and skip_create_camera:
                continue
            # ---------------------------
        
            current_var = "%s_%d" % (obj_type, var_counter)
            step = {
                "tool": tool,
                "args": {"name": current_var},
                "save_as": current_var
            }
            plan["steps"].append(step)
            plan_vars.add(current_var)
            
            # Categorize the created object
            if obj_type == "plane":
                if environment_object is None:
                    environment_object = current_var
            elif obj_type == "camera":
                camera_object = current_var
            elif obj_type in ["sphere", "cube", "cylinder", "mesh"]:
                if subject_object is None or subject_object == target_var:
                    subject_object = current_var
            else:
                if subject_object is None:
                    subject_object = current_var
            
            var_counter += 1

        elif cap == "DUPLICATE_OBJECTS":
            copies_var = "copies_%d" % var_counter
            code = "import maya.cmds as cmds\n"
            code += "sel = cmds.ls(sl=True)\n"
            code += "if not sel and cmds.objExists('%s'):\n" % (subject_object if subject_object else "")
            code += "    sel = ['%s']\n" % (subject_object if subject_object else "")
            code += "if sel:\n"
            code += "    copies = []\n"
            code += "    for i in range(%d):\n" % (act_count - 1)
            code += "        dupes = cmds.duplicate(sel[0])\n"
            code += "        copies.extend(dupes)\n"
            code += "    result = {'created': copies}\n"
            
            step = {
                "tool": "maya.execute_python_code",
                "args": {"code": code},
                "save_as": copies_var
            }
            plan["steps"].append(step)
            plan_vars.add(copies_var)
            var_counter += 1

        elif cap in ["SCATTER_AROUND", "PLACE_ON_TOP", "PLACE_NEXT_TO", "RANDOM_SCATTER"]:
            # Inject spatial math
            rel = "around"
            if cap == "PLACE_ON_TOP": rel = "on_top_of"
            elif cap == "PLACE_NEXT_TO": rel = "next_to"
            elif cap == "RANDOM_SCATTER": rel = "scatter"

            # Determine scatter target (e.g. environment object if placing on top of plane)
            spatial_target = target_var
            if rel == "on_top_of" and environment_object:
                spatial_target = environment_object
            elif rel == "scatter" and environment_object:
                spatial_target = environment_object

            spatial_code = calculate_spatial_offsets(rel, spatial_target, act_count)
            func_name = "scatter_around"
            if rel == "on_top_of": func_name = "place_on_top"
            if rel == "next_to": func_name = "place_next_to"
            if rel == "scatter": func_name = "random_scatter"

            # Execute caller
            import json
            safe_spatial_target = json.dumps(spatial_target if spatial_target else "")
            caller_code = "\ntarget = variables.get('%s', [%s])\n" % (spatial_target, safe_spatial_target)
            caller_code += "if isinstance(target, list) and target: target = target[0]\n"
            caller_code += "elif isinstance(target, list): target = ''\n"
            
            # The items to scatter are either the newly created variables, or active selection
            safe_subject_object = json.dumps(subject_object if subject_object else "")
            items_ref = "[%s]" % safe_subject_object
            if act_count > 1:
                # Merge base object with copies
                items_ref = "[%s] + (variables.get('copies_%d', []) if 'variables' in globals() else [])" % (safe_subject_object, var_counter - 1)

            caller_code += "items = %s\n" % items_ref
            caller_code += "if items:\n"
            caller_code += "    %s(target, items)\n" % func_name

            code = spatial_code + caller_code
            
            step = {
                "tool": "maya.execute_python_code",
                "args": {"code": code}
            }
            plan["steps"].append(step)

        elif cap == "BOUNCE_ANIMATION":
            target = subject_object or target_var
            if target:
                step = {
                    "tool": "maya.add_bounce_animation",
                    "args": {"target": _fmt_target(target, plan_vars)}
                }
                plan["steps"].append(step)
            else:
                bounce_var = "bounce_%d" % var_counter
                step = {
                    "tool": "maya.create_bouncing_ball",
                    "args": {"name": bounce_var},
                    "save_as": bounce_var
                }
                plan["steps"].append(step)
                plan_vars.add(bounce_var)
                subject_object = bounce_var
                var_counter += 1

        elif cap == "ROTATE_ANIMATION":
            target = subject_object or target_var
            args = {}
            if target:
                args["target"] = _fmt_target(target, plan_vars)
            step = {
                "tool": "maya.create_loop_rotate",
                "args": args
            }
            plan["steps"].append(step)

        elif cap == "ORBIT_ANIMATION":
            orbit_target = subject_object or target_var or ""
            args = {"frames": 120}
            if orbit_target:
                args["target"] = _fmt_target(orbit_target, plan_vars)
            step = {
                "tool": "maya.create_turntable",
                "args": args,
                "save_as": "turntable_cam_%d" % var_counter
            }
            plan["steps"].append(step)
            plan_vars.add("turntable_cam_%d" % var_counter)
            camera_object = "turntable_cam_%d" % var_counter
            var_counter += 1

        elif cap == "ROLL_ANIMATION":
            roll_target = subject_object or target_var or ""
            roll_code = """import maya.cmds as cmds
target = '{target}'
if not cmds.objExists(target):
    sel = cmds.ls(sl=True)
    target = sel[0] if sel else None
if target:
    bb = cmds.xform(target, q=True, ws=True, bb=True)
    radius = (bb[4] - bb[1]) / 2.0 if bb else 1.0
    distance = 20.0
    start_frame = 1
    end_frame = 120
    cmds.setKeyframe(target, attribute='tx', t=start_frame, v=cmds.getAttr(target+'.tx'))
    cmds.setKeyframe(target, attribute='rz', t=start_frame, v=0)
    import math
    rot = -(distance / (2 * math.pi * radius)) * 360
    end_tx = cmds.getAttr(target+'.tx') + distance
    cmds.setKeyframe(target, attribute='tx', t=end_frame, v=end_tx)
    cmds.setKeyframe(target, attribute='rz', t=end_frame, v=rot)
""".format(target=roll_target)
            step = {
                "tool": "maya.execute_python_code",
                "args": {"code": roll_code}
            }
            plan["steps"].append(step)

        elif cap == "SURFACE_ATTACH":
            # Very basic attach logic via python for mock
            target_obj = subject_object if subject_object else "selection"
            env_obj = environment_object if environment_object else "plane"

            code = "import maya.cmds as cmds\n"
            code += "targets = cmds.ls(sl=True) or ['%s']\n" % target_obj
            code += "if targets and cmds.objExists(targets[0]):\n"
            code += "    if cmds.objExists('%s'):\n" % env_obj
            code += "        cmds.geometryConstraint('%s', targets[0])\n" % env_obj

            step = {
                "tool": "maya.execute_python_code",
                "args": {"code": code}
            }
            plan["steps"].append(step)

        elif cap == "CAMERA_LOOK" or cap == "FOLLOW_CAMERA":
            # Skip if turntable already handles it
            if "ORBIT_ANIMATION" in all_caps:
                continue
            
            args = {}
            if camera_object:
                args["camera"] = _fmt_target(camera_object, plan_vars)
            
            target = subject_object or target_var
            if target:
                args["target"] = _fmt_target(target, plan_vars)
                
            step = {
                "tool": "maya.camera_look_at",
                "args": args
            }
            plan["steps"].append(step)

        elif cap == "SCENE_CLEANUP":
            step = {
                "tool": "maya.delete_selected",
                "args": {}
            }
            plan["steps"].append(step)

        elif cap == "PRODUCT_TURNTABLE":
            # Skip if ORBIT_ANIMATION already added a turntable
            if "ORBIT_ANIMATION" in all_caps:
                continue
            turntable_tgt = subject_object or target_var or ""
            step = {
                "tool": "maya.create_turntable",
                "args": {
                    "target": turntable_tgt,
                    "frames": 120,
                },
                "save_as": "turntable_cam_%d" % var_counter
            }
            plan["steps"].append(step)
            plan_vars.add("turntable_cam_%d" % var_counter)
            camera_object = "turntable_cam_%d" % var_counter
            var_counter += 1

        elif cap == "FX_LIGHTING":
            lighting_tgt = subject_object or target_var or ""
            step = {
                "tool": "maya.create_three_point_lighting",
                "args": {
                    "target": lighting_tgt,
                    "intensity": 1.0,
                }
            }
            plan["steps"].append(step)

        elif cap == "FX_EXPLOSION":
            step = {
                "tool": "maya.import_bomb_asset",
                "args": {
                    "namespace": "Bomb",
                }
            }
            plan["steps"].append(step)

    return plan
