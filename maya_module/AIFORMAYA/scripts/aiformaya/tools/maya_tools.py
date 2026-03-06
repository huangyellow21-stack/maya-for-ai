# -*- coding: utf-8 -*-
from __future__ import absolute_import

import glob
import os
import re

import maya.cmds as cmds
import maya.mel as mel

try:
    basestring
except NameError:
    basestring = str

from .attributes import expand_attributes

try:
    from ..core.memory import EntityMemory
except ImportError:
    class EntityMemory(object):
        @classmethod
        def update_last_created(cls, entity_type, entity_name):
            pass
        @classmethod
        def get_last_created(cls): return {}
        @classmethod
        def get_recent_objects(cls): return []


class ToolError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message

class ConfirmationError(Exception):
    def __init__(self, action, target, options):
        Exception.__init__(self, "Confirmation Required")
        self.action = action
        self.target = target
        self.options = options


def tool_ask_user_confirmation(args):
    """
    Raise ConfirmationError so agent.py catches it and renders a UI confirmation card.
    This is intentionally not a 'real' tool — the agent intercepts the exception
    and returns a confirm-type payload to the front-end instead of executing.
    """
    action = args.get("action", u"\u64cd\u4f5c")
    target = args.get("target", u"\u76ee\u6807")
    options = args.get("options") or [u"\u786e\u5b9a", u"\u53d6\u6d88"]
    raise ConfirmationError(action=action, target=target, options=options)


def _ensure_selection():
    sel = cmds.ls(sl=True, fl=True) or []
    if not sel:
        raise ToolError("MAYA_NO_SELECTION", "没有选择")
    return sel


def _selection_mode_and_type(sel):
    # Very lightweight heuristic
    if not sel:
        return ("object", None)
    s0 = sel[0]
    if ".f[" in s0:
        return ("component", "face")
    if ".e[" in s0:
        return ("component", "edge")
    if ".vtx[" in s0:
        return ("component", "vertex")
    return ("object", None)


def _to_transforms_from_selection(sel):
    """
    Accept transforms or shapes; lift shapes to parent transforms; drop components.
    Dedup while preserving order.
    """
    out = []
    seen = set()
    for s in sel:
        if "." in s:
            raise ToolError("MAYA_INVALID_SELECTION", "该工具需要选择物体（transform），不能是组件选择")
        node = s
        ntype = cmds.nodeType(node)
        if ntype != "transform":
            # try parent transform
            parents = cmds.listRelatives(node, parent=True, fullPath=False) or []
            if parents:
                node = parents[0]
        if cmds.nodeType(node) != "transform":
            continue
        if node not in seen:
            out.append(node)
            seen.add(node)
    if not out:
        raise ToolError("MAYA_INVALID_SELECTION", "没有可用的 transform 目标")
    return out


def tool_list_selection(_args):
    sel = cmds.ls(sl=True, fl=True) or []
    mode, ctype = _selection_mode_and_type(sel)
    nodes = []
    comps = []
    for s in sel:
        if "." in s:
            comps.append(s)
        else:
            nodes.append(s)
    return {
        "nodes": nodes,
        "components": comps,
        "selection_mode": mode,
        "component_type": ctype,
    }


def tool_list_cameras(args):
    include_defaults = bool(args.get("include_defaults", False))
    only_renderable = bool(args.get("only_renderable", False))
    cam_shapes = cmds.ls(type="camera") or []
    default_names = set(["persp", "top", "front", "side"])
    out = []
    for shape in cam_shapes:
        parents = cmds.listRelatives(shape, parent=True, fullPath=False) or []
        if not parents:
            continue
        transform = parents[0]
        is_default = transform in default_names
        if not include_defaults and is_default:
            continue
        try:
            renderable = bool(cmds.getAttr(shape + ".renderable"))
        except Exception:
            renderable = False
        if only_renderable and not renderable:
            continue
        out.append(
            {
                "transform": transform,
                "shape": shape,
                "is_default": is_default,
                "renderable": renderable,
            }
        )
    return {"cameras": out, "count": len(out)}


# Default nodes that Maya creates in every new scene - we filter these out
_DEFAULT_CAMERAS    = {"persp", "perspShape", "top", "topShape", "front", "frontShape",
                       "side", "sideShape"}
_DEFAULT_NODES      = {"defaultLightSet", "defaultObjectSet", "initialShadingGroup",
                       "initialParticleSE", "lambert1", "particleCloud1",
                       "shaderGlow1", "defaultRenderLayer", "renderLayerManager",
                       "layerManager", "defaultLayer", "lightLinker1",
                       "strokeGlobals", "hardwareRenderGlobals",
                       "defaultHardwareRenderGlobals", "defaultRenderGlobals",
                       "defaultRenderQuality", "defaultResolution",
                       "renderGlobalsList1", "defaultTextureList1",
                       "ikSystem", "dynController1",
                       "time1", "sequenceManager1"}


def tool_scan_scene_summary(args):
    u"""
    Scans the entire scene and returns a structured, human-readable summary.
    Automatically:
      - Excludes all default Maya cameras (persp/top/front/side)
      - Excludes Maya system nodes
      - Detects rigged characters: joint hierarchies that own mesh children
      - Groups all scene content into categories
      - For large scenes (>30 objects per category) provides counts only
    """
    LARGE_SCENE_THRESHOLD = 30

    # ---- 1. Collect all transforms and filter defaults ----
    all_transforms = cmds.ls(type="transform", long=False) or []
    default_cam_transforms = _DEFAULT_CAMERAS
    # filter system nodes
    user_transforms = []
    for t in all_transforms:
        if t in default_cam_transforms:
            continue
        if t in _DEFAULT_NODES:
            continue
        # skip shapes disguised as transforms (shouldn't happen but safety check)
        if t.endswith("Shape"):
            continue
        user_transforms.append(t)

    # ---- 2. Categorize each transform ----
    mesh_objs      = []   # plain geometry
    rig_roots      = []   # joint hierarchy roots (characters / rigs)
    user_cameras   = []   # user-created cameras
    lights_list    = []   # light nodes
    groups         = []   # empty groups or organizers
    curves_list    = []   # nurbs curves (controls, paths)
    other_list     = []   # anything else

    seen = set()

    def _children_by_type(node, node_type):
        return cmds.listRelatives(node, children=True, type=node_type, fullPath=False) or []

    # Find joint roots (no joint parent = root joint)
    all_joints = cmds.ls(type="joint", long=False) or []
    joint_roots = set()
    for j in all_joints:
        parent = cmds.listRelatives(j, parent=True, type="joint", fullPath=False)
        if not parent:
            joint_roots.add(j)
            # walk up through transform parents to find the character's top group
            p = cmds.listRelatives(j, parent=True, fullPath=False)
            if p:
                joint_roots.add(p[0])

    for t in user_transforms:
        if t in seen:
            continue
        seen.add(t)

        # check camera
        cam_shapes = _children_by_type(t, "camera")
        if cam_shapes:
            try:
                renderable = cmds.getAttr(cam_shapes[0] + ".renderable")
            except Exception:
                renderable = False
            user_cameras.append({"name": t, "renderable": bool(renderable)})
            continue

        # check light
        light_shapes = cmds.listRelatives(t, children=True,
                                          type=["spotLight","pointLight","directionalLight",
                                                "areaLight","ambientLight","volumeLight"],
                                          fullPath=False) or []
        if light_shapes:
            lights_list.append(t)
            continue

        # check if it's a joint root or group containing a rig
        if t in joint_roots:
            # count meshes skinned to this rig
            desc_joints = cmds.listRelatives(t, allDescendents=True, type="joint", fullPath=False) or []
            desc_meshes = cmds.listRelatives(t, allDescendents=True, type="mesh", fullPath=False) or []
            # look for skinCluster connections
            has_skin = False
            for m in desc_meshes:
                hist = cmds.listHistory(m, type="skinCluster") or []
                if hist:
                    has_skin = True
                    break
            rig_roots.append({
                "name": t,
                "joint_count": len(desc_joints),
                "mesh_count": len(desc_meshes),
                "is_skinned": has_skin,
            })
            # mark all descendants as seen
            descs = cmds.listRelatives(t, allDescendents=True, fullPath=False) or []
            seen.update(descs)
            continue

        # check mesh
        mesh_shapes = _children_by_type(t, "mesh")
        if mesh_shapes:
            # get poly count
            try:
                poly_count = cmds.polyEvaluate(t, face=True) or 0
            except Exception:
                poly_count = 0
            mesh_objs.append({"name": t, "faces": poly_count})
            continue

        # check nurbs curve (control curve or path)
        curve_shapes = _children_by_type(t, "nurbsCurve")
        if curve_shapes:
            curves_list.append(t)
            continue

        # check if it's an empty group
        children = cmds.listRelatives(t, children=True, fullPath=False) or []
        if not children:
            # empty transform, skip (probably a group anchor)
            continue

        # non-empty group – descend is already covered via other transforms
        # but record named groups that act as organizers
        all_child_types = set()
        for c in children:
            shapes = cmds.listRelatives(c, shapes=True, fullPath=False) or []
            for s in shapes:
                all_child_types.add(cmds.nodeType(s) or "unknown")
        groups.append({"name": t, "children": len(children)})

    # ---- 3. Build summary ----
    summary = {}

    if rig_roots:
        if len(rig_roots) <= LARGE_SCENE_THRESHOLD:
            summary["rigged_characters"] = rig_roots
        else:
            summary["rigged_characters"] = {
                "count": len(rig_roots),
                "note": u"场景角色过多，仅显示数量"
            }

    if mesh_objs:
        if len(mesh_objs) <= LARGE_SCENE_THRESHOLD:
            summary["geometry"] = mesh_objs
        else:
            total_faces = sum(m["faces"] for m in mesh_objs)
            summary["geometry"] = {
                "count": len(mesh_objs),
                "total_faces": total_faces,
                "note": u"几何体过多，仅显示汇总"
            }

    if user_cameras:
        summary["user_cameras"] = user_cameras

    if lights_list:
        summary["lights"] = lights_list

    if curves_list:
        if len(curves_list) <= LARGE_SCENE_THRESHOLD:
            summary["nurbs_curves"] = curves_list
        else:
            summary["nurbs_curves"] = {"count": len(curves_list)}

    if groups:
        summary["organizer_groups"] = [g["name"] for g in groups]

    summary["total_user_objects"] = (
        len(rig_roots) + len(mesh_objs) + len(user_cameras) +
        len(lights_list) + len(curves_list) + len(groups)
    )

    return summary


def tool_select_by_name_pattern(args):

    pattern = args.get("pattern")
    if not isinstance(pattern, basestring) or not pattern.strip():
        raise ToolError("ARG_VALIDATION_FAILED", "pattern 不能为空")
    pattern = pattern.strip()
    type_filter = args.get("type_filter") or ""
    mode = args.get("mode") or "replace"
    t_map = {
        "transform": "transform",
        "camera": "camera",
        "joint": "joint",
        "mesh": "mesh",
    }
    type_name = t_map.get(str(type_filter).strip().lower(), None)
    nodes = cmds.ls(pattern, long=False) or []
    if type_name:
        filtered = []
        for n in nodes:
            if cmds.nodeType(n) == type_name:
                filtered.append(n)
        nodes = filtered
    if not nodes:
        if mode == "replace":
            cmds.select(clear=True)
        return {"selected": [], "count": 0}
    if mode == "add":
        cmds.select(nodes, add=True)
    elif mode == "remove":
        cmds.select(nodes, deselect=True)
    else:
        cmds.select(nodes, replace=True)
    sel = cmds.ls(sl=True, long=False) or []
    return {"selected": sel, "count": len(sel)}


def tool_select_connected_components(args):
    sel = _ensure_selection()
    mode, ctype = _selection_mode_and_type(sel)
    if mode != "component":
        raise ToolError("MAYA_INVALID_SELECTION", "需要选择 polygon 组件（面/边/点）")

    # Prefer face: if edge/vertex, convert to faces first.
    if ctype in ("edge", "vertex"):
        try:
            cmds.polySelectConstraint(disable=True)
        except Exception:
            pass
        # Convert selection to faces
        try:
            mel.eval("ConvertSelectionToFaces;")
        except Exception:
            # fallback: polyListComponentConversion
            faces = cmds.polyListComponentConversion(sel, toFace=True) or []
            cmds.select(faces, r=True)

    # Now in face mode; expand to connected shell
    try:
        mel.eval("PolySelectTraverse 1;")  # 1 often means shell/connected in Maya's traverse
    except Exception:
        # Fallback: try polySelectConstraint might not help; keep current selection
        pass

    # Collect selection stats per mesh
    faces = cmds.ls(sl=True, fl=True) or []
    total = 0
    per_mesh = {}
    for f in faces:
        if ".f[" not in f:
            continue
        total += 1
        shape = f.split(".f[", 1)[0]
        per_mesh[shape] = per_mesh.get(shape, 0) + 1
    if total > 200000:
        raise ToolError("MAYA_TOO_MANY_TARGETS", "选中的面过多（>%d），请缩小范围" % 200000)
    return {
        "component_type": "face",
        "total_selected_faces": total,
        "per_mesh": [{"shape": k, "selected_faces": per_mesh[k]} for k in sorted(per_mesh.keys())],
    }


def tool_grow_selection(args):
    sel = _ensure_selection()
    mode, ctype = _selection_mode_and_type(sel)
    if mode != "component":
        raise ToolError("MAYA_INVALID_SELECTION", "需要组件选择（面/边/点）")
    steps = int(args.get("steps", 1))
    if steps < 1 or steps > 5:
        raise ToolError("ARG_VALIDATION_FAILED", "steps 必须在 1..5")
    for _ in range(steps):
        try:
            mel.eval("GrowPolygonSelectionRegion;")
        except Exception:
            # if fail, stop
            break
    new_sel = cmds.ls(sl=True, fl=True) or []
    return {"selected_count": len(new_sel), "steps": steps}


_PATTERN_I_RE = re.compile(r"\{i(?::([^}]+))?\}")


def _format_name(pattern, i):
    m = _PATTERN_I_RE.search(pattern)
    if not m:
        raise ToolError("ARG_VALIDATION_FAILED", "pattern 必须包含 {i} 占位符，例如 prop_{i:03d}")
    fmt = m.group(1)
    if fmt:
        try:
            token = ("{0:" + fmt + "}").format(i)
        except Exception:
            raise ToolError("ARG_VALIDATION_FAILED", "pattern 的 {i:...} 格式不合法：%s" % fmt)
    else:
        token = str(i)
    return _PATTERN_I_RE.sub(token, pattern, count=1)


def tool_rename_batch(args):
    sel = _ensure_selection()
    transforms = _to_transforms_from_selection(sel)
    if len(transforms) > 300:
        raise ToolError("MAYA_TOO_MANY_TARGETS", "目标过多（>%d），请缩小范围" % 300)

    pattern = args.get("pattern", "")
    if not isinstance(pattern, basestring) or not pattern.strip():
        raise ToolError("ARG_VALIDATION_FAILED", "pattern 不能为空，且必须包含 {i}")
    if _PATTERN_I_RE.search(pattern) is None:
        raise ToolError("ARG_VALIDATION_FAILED", "pattern 必须包含 {i} 占位符，例如 prop_{i:03d}")

    start = int(args.get("start", 1))
    on_conflict = args.get("on_conflict", "auto_increment")
    keep_namespace = bool(args.get("keep_namespace", True))

    renamed = []
    i = start
    for t in transforms:
        old = t
        # namespace handling
        ns = ""
        base = old
        if ":" in old:
            parts = old.split(":")
            ns = ":".join(parts[:-1]) + ":"
            base = parts[-1]
        target_i = i
        tries = 0
        while True:
            new_base = _format_name(pattern, target_i)
            new_name = (ns + new_base) if (keep_namespace and ns) else new_base
            exists = cmds.objExists(new_name)
            if not exists or new_name == old:
                break
            if on_conflict == "error":
                raise ToolError("NAME_CONFLICT", "重命名冲突：%s 已存在" % new_name)
            tries += 1
            if tries > 10000:
                raise ToolError("NAME_CONFLICT", "重命名冲突次数过多，请修改 pattern 或 start")
            target_i += 1

        try:
            actual = cmds.rename(old, new_name)
        except Exception as e:
            raise ToolError("MAYA_COMMAND_FAILED", "rename 失败：%s" % str(e))
        renamed.append({"old": old, "new": actual})
        i = target_i + 1

    return {
        "renamed": renamed,
        "count": len(renamed),
        "pattern": pattern,
        "start": start,
        "keep_namespace": keep_namespace,
        "on_conflict": on_conflict,
    }


def tool_set_key(args):
    sel = _ensure_selection()
    transforms = _to_transforms_from_selection(sel)
    if len(transforms) > 200:
        raise ToolError("MAYA_TOO_MANY_TARGETS", "目标过多（>%d），请缩小范围" % 200)

    time = args.get("time", None)
    attrs_tokens = args.get("attributes", None)
    try:
        attrs = expand_attributes(attrs_tokens) if attrs_tokens is not None else None
    except Exception as e:
        raise ToolError("ARG_VALIDATION_FAILED", "attributes 不合法：%s" % str(e))

    try:
        if time is not None:
            cmds.currentTime(time, edit=True)
        if attrs is None or len(attrs) == 0:
            cmds.setKeyframe(transforms)
            used_attrs = []
        else:
            # Expand short attrs to full plugs
            plugs = []
            for t in transforms:
                for a in attrs:
                    plugs.append("%s.%s" % (t, a))
            cmds.setKeyframe(plugs)
            used_attrs = attrs
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "setKey 失败：%s" % str(e))

    return {"time": float(cmds.currentTime(q=True)), "objects": transforms, "attributes": used_attrs, "count": len(transforms)}


def tool_delete_keys_range(args):
    sel = _ensure_selection()
    transforms = _to_transforms_from_selection(sel)
    if len(transforms) > 200:
        raise ToolError("MAYA_TOO_MANY_TARGETS", "目标过多（>%d），请缩小范围" % 200)

    if "start" not in args or "end" not in args:
        raise ToolError("ARG_VALIDATION_FAILED", "需要 start 与 end")
    start = float(args.get("start"))
    end = float(args.get("end"))
    if end < start:
        raise ToolError("ARG_VALIDATION_FAILED", "end 必须 >= start")
    if (end - start) > 10000:
        raise ToolError("ARG_VALIDATION_FAILED", "范围过大（end-start > 10000），请缩小范围")

    attrs_tokens = args.get("attributes", None)
    # Default TRS (transform) if not provided
    if attrs_tokens is None or (isinstance(attrs_tokens, (list, tuple)) and len(attrs_tokens) == 0):
        attrs_tokens = ["transform"]
    try:
        attrs = expand_attributes(attrs_tokens)
    except Exception as e:
        raise ToolError("ARG_VALIDATION_FAILED", "attributes 不合法：%s" % str(e))

    try:
        plugs = []
        for t in transforms:
            for a in attrs:
                plugs.append("%s.%s" % (t, a))
        cmds.cutKey(plugs, time=(start, end), option="keys")
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "delete keys 失败：%s" % str(e))

    return {"start": start, "end": end, "objects": transforms, "attributes": attrs, "count": len(transforms)}


def tool_shift_keys(args):
    sel = _ensure_selection()
    transforms = _to_transforms_from_selection(sel)
    if len(transforms) > 200:
        raise ToolError("MAYA_TOO_MANY_TARGETS", "目标过多（>%d），请缩小范围" % 200)

    if "start" not in args or "end" not in args:
        raise ToolError("ARG_VALIDATION_FAILED", "需要 start 与 end")
    start = float(args.get("start"))
    end = float(args.get("end"))
    offset = float(args.get("offset", 0.0))

    attrs_tokens = args.get("attributes", None)
    if attrs_tokens is None or (isinstance(attrs_tokens, (list, tuple)) and len(attrs_tokens) == 0):
        attrs_tokens = ["transform"]
    try:
        attrs = expand_attributes(attrs_tokens)
    except Exception as e:
        raise ToolError("ARG_VALIDATION_FAILED", "attributes 不合法：%s" % str(e))

    try:
        plugs = []
        for t in transforms:
            for a in attrs:
                plugs.append("%s.%s" % (t, a))
        cmds.keyframe(plugs, edit=True, time=(start, end), relative=True, timeChange=offset)
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "shift keys 失败：%s" % str(e))

    return {"start": start, "end": end, "offset": offset, "objects": transforms, "attributes": attrs, "count": len(transforms)}


def tool_euler_filter(args):
    sel = _ensure_selection()
    transforms = _to_transforms_from_selection(sel)
    if len(transforms) > 100:
        raise ToolError("MAYA_TOO_MANY_TARGETS", "目标过多（>%d），请缩小范围" % 100)

    tr = args.get("time_range", None)
    if tr is None:
        start = float(cmds.playbackOptions(q=True, min=True))
        end = float(cmds.playbackOptions(q=True, max=True))
    else:
        try:
            start = float(tr.get("start"))
            end = float(tr.get("end"))
        except Exception:
            raise ToolError("ARG_VALIDATION_FAILED", "time_range 需要 start/end")
        if end < start:
            raise ToolError("ARG_VALIDATION_FAILED", "time_range.end 必须 >= start")

    # Apply Euler filter on rotate channels
    try:
        plugs = []
        for t in transforms:
            for a in ("rx", "ry", "rz"):
                plugs.append("%s.%s" % (t, a))
        cmds.filterCurve(plugs, filter="euler", time=(start, end))
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "euler filter 失败：%s" % str(e))

    return {"processed_objects": transforms, "time_range": {"start": start, "end": end}, "count": len(transforms)}


def _apply_transform_args(node, args):
    """
    将 args 中的 translate/rotate/scale 应用到 node。
    在每个 create_* 工具创建并命名后调用。
    """
    translate = args.get("translate")
    rotate    = args.get("rotate")
    scale     = args.get("scale")
    try:
        if isinstance(translate, (list, tuple)) and len(translate) == 3:
            cmds.setAttr(
                node + ".translate",
                float(translate[0]), float(translate[1]), float(translate[2]),
                type="double3"
            )
        if isinstance(rotate, (list, tuple)) and len(rotate) == 3:
            cmds.setAttr(
                node + ".rotate",
                float(rotate[0]), float(rotate[1]), float(rotate[2]),
                type="double3"
            )
        if isinstance(scale, (list, tuple)) and len(scale) == 3:
            cmds.setAttr(
                node + ".scale",
                float(scale[0]), float(scale[1]), float(scale[2]),
                type="double3"
            )
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "应用变换失败：%s" % str(e))


def tool_create_cube(args):
    size = float(args.get("size", 1.0))
    w = float(args.get("width", size))
    h = float(args.get("height", size))
    d = float(args.get("depth", size))
    sx = int(args.get("subdiv_x", 1))
    sy = int(args.get("subdiv_y", 1))
    sz = int(args.get("subdiv_z", 1))
    name = args.get("name")

    try:
        res = cmds.polyCube(w=w, h=h, d=d, sx=sx, sy=sy, sz=sz)
        xform = res[0] if isinstance(res, (list, tuple)) and res else res
        actual = xform
        if isinstance(name, basestring) and name.strip():
            target = name.strip()
            if not cmds.objExists(target):
                actual = cmds.rename(xform, target)
            else:
                # 简单自增避免冲突
                i = 1
                while True:
                    cand = "%s_%02d" % (target, i)
                    if not cmds.objExists(cand):
                        actual = cmds.rename(xform, cand)
                        break
                    i += 1
        _apply_transform_args(actual, args)
        EntityMemory.update_last_created("cube", actual)
        return {
            "transform": actual,
            "width": w, "height": h, "depth": d,
            "subdiv": {"x": sx, "y": sy, "z": sz},
            "translate": args.get("translate"),
            "rotate":    args.get("rotate"),
        }
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "polyCube 失败：%s" % str(e))


def tool_create_sphere(args):
    radius = float(args.get("radius", 1.0))
    sx = int(args.get("subdiv_axis", 20))
    sy = int(args.get("subdiv_height", 20))
    name = args.get("name")
    try:
        res = cmds.polySphere(r=radius, sx=sx, sy=sy)
        xform = res[0] if isinstance(res, (list, tuple)) and res else res
        actual = xform
        if isinstance(name, basestring) and name.strip():
            target = name.strip()
            if not cmds.objExists(target):
                actual = cmds.rename(xform, target)
            else:
                i = 1
                while True:
                    cand = "%s_%02d" % (target, i)
                    if not cmds.objExists(cand):
                        actual = cmds.rename(xform, cand)
                        break
                    i += 1
        _apply_transform_args(actual, args)
        EntityMemory.update_last_created("sphere", actual)
        return {
            "transform": actual,
            "radius": radius,
            "subdiv": {"axis": sx, "height": sy},
            "translate": args.get("translate"),
            "rotate":    args.get("rotate"),
        }
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "polySphere 失败：%s" % str(e))


def tool_create_cylinder(args):
    radius = float(args.get("radius", 1.0))
    height = float(args.get("height", 2.0))
    sa = int(args.get("subdiv_axis", 20))
    sh = int(args.get("subdiv_height", 1))
    sc = int(args.get("subdiv_caps", 1))
    name = args.get("name")
    try:
        res = cmds.polyCylinder(r=radius, h=height, sa=sa, sh=sh, sc=sc)
        xform = res[0] if isinstance(res, (list, tuple)) and res else res
        actual = xform
        if isinstance(name, basestring) and name.strip():
            target = name.strip()
            if not cmds.objExists(target):
                actual = cmds.rename(xform, target)
            else:
                i = 1
                while True:
                    cand = "%s_%02d" % (target, i)
                    if not cmds.objExists(cand):
                        actual = cmds.rename(xform, cand)
                        break
                    i += 1
        _apply_transform_args(actual, args)
        EntityMemory.update_last_created("cylinder", actual)
        return {
            "transform": actual,
            "radius": radius, "height": height,
            "subdiv": {"axis": sa, "height": sh, "caps": sc},
            "translate": args.get("translate"),
            "rotate":    args.get("rotate"),
        }
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "polyCylinder 失败：%s" % str(e))


def tool_create_plane(args):
    w = float(args.get("width", 10.0))
    h = float(args.get("height", 10.0))
    sx = int(args.get("subdiv_x", 1))
    sy = int(args.get("subdiv_y", 1))
    name = args.get("name")
    try:
        res = cmds.polyPlane(w=w, h=h, sx=sx, sy=sy)
        xform = res[0] if isinstance(res, (list, tuple)) and res else res
        actual = xform
        if isinstance(name, basestring) and name.strip():
            target = name.strip()
            if not cmds.objExists(target):
                actual = cmds.rename(xform, target)
            else:
                i = 1
                while True:
                    cand = "%s_%02d" % (target, i)
                    if not cmds.objExists(cand):
                        actual = cmds.rename(xform, cand)
                        break
                    i += 1
        _apply_transform_args(actual, args)
        EntityMemory.update_last_created("plane", actual)
        return {
            "transform": actual,
            "width": w, "height": h,
            "subdiv": {"x": sx, "y": sy},
            "translate": args.get("translate"),
            "rotate":    args.get("rotate"),
        }
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "polyPlane 失败：%s" % str(e))

def tool_create_camera(args):
    name = args.get("name")
    focal_length = args.get("focal_length", None)
    near_clip = args.get("near_clip", None)
    far_clip = args.get("far_clip", None)
    translate = args.get("translate")   # [x, y, z] optional initial position
    rotate    = args.get("rotate")      # [rx, ry, rz] optional initial rotation
    try:
        res = cmds.camera()
        xform = res[0] if isinstance(res, (list, tuple)) and res else res
        shape = res[1] if isinstance(res, (list, tuple)) and len(res) > 1 else None
        actual = xform
        if isinstance(name, basestring) and name.strip():
            target = name.strip()
            if not cmds.objExists(target):
                actual = cmds.rename(xform, target)
            else:
                i = 1
                while True:
                    cand = "%s_%02d" % (target, i)
                    if not cmds.objExists(cand):
                        actual = cmds.rename(xform, cand)
                        break
                    i += 1
        if shape is None:
            shapes = cmds.listRelatives(actual, shapes=True, fullPath=False) or []
            shape = shapes[0] if shapes else None
        if shape:
            if focal_length is not None:
                cmds.setAttr(shape + ".focalLength", float(focal_length))
            if near_clip is not None:
                cmds.setAttr(shape + ".nearClipPlane", float(near_clip))
            if far_clip is not None:
                cmds.setAttr(shape + ".farClipPlane", float(far_clip))
        # Apply initial position/rotation if provided
        if translate and len(translate) == 3:
            cmds.setAttr(actual + ".translate",
                         float(translate[0]), float(translate[1]), float(translate[2]),
                         type="double3")
        if rotate and len(rotate) == 3:
            cmds.setAttr(actual + ".rotate",
                         float(rotate[0]), float(rotate[1]), float(rotate[2]),
                         type="double3")
        EntityMemory.update_last_created("camera", actual)
        return {
            "camera":       actual,
            "transform":    actual,
            "shape":        shape,
            "focal_length": focal_length,
            "near_clip":    near_clip,
            "far_clip":     far_clip,
            "translate":    translate,
            "rotate":       rotate,
        }
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "camera 失败：%s" % str(e))

def tool_set_translate(args):
    sel = _ensure_selection()
    transforms = _to_transforms_from_selection(sel)
    if len(transforms) > 200:
        raise ToolError("MAYA_TOO_MANY_TARGETS", "目标过多（>%d），请缩小范围" % 200)

    # Args
    has_x = "x" in args
    has_y = "y" in args
    has_z = "z" in args
    if not (has_x or has_y or has_z):
        raise ToolError("ARG_VALIDATION_FAILED", "需要至少提供 x/y/z 之一")
    mode = args.get("mode", "absolute")  # absolute | relative
    if mode not in ("absolute", "relative"):
        raise ToolError("ARG_VALIDATION_FAILED", "mode 必须是 absolute 或 relative")
    time = args.get("time", None)
    set_key = bool(args.get("set_key", False))

    try:
        if time is not None:
            cmds.currentTime(time, edit=True)
        changed_attrs = []
        for t in transforms:
            # current values
            cur_tx = cmds.getAttr(t + ".tx")
            cur_ty = cmds.getAttr(t + ".ty")
            cur_tz = cmds.getAttr(t + ".tz")
            tx = cur_tx
            ty = cur_ty
            tz = cur_tz
            if has_x:
                vx = float(args.get("x"))
                tx = (cur_tx + vx) if mode == "relative" else vx
            if has_y:
                vy = float(args.get("y"))
                ty = (cur_ty + vy) if mode == "relative" else vy
            if has_z:
                vz = float(args.get("z"))
                tz = (cur_tz + vz) if mode == "relative" else vz
            if tx != cur_tx:
                cmds.setAttr(t + ".tx", tx)
                changed_attrs.append(t + ".tx")
            if ty != cur_ty:
                cmds.setAttr(t + ".ty", ty)
                changed_attrs.append(t + ".ty")
            if tz != cur_tz:
                cmds.setAttr(t + ".tz", tz)
                changed_attrs.append(t + ".tz")
        if set_key and changed_attrs:
            cmds.setKeyframe(changed_attrs)
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "set translate 失败：%s" % str(e))

    return {
        "objects": transforms,
        "mode": mode,
        "time": float(cmds.currentTime(q=True)),
        "changed": changed_attrs,
    }

def _safe_unique_name(base):
    if not cmds.objExists(base):
        return base
    i = 1
    while True:
        cand = "%s_%02d" % (base, i)
        if not cmds.objExists(cand):
            return cand
        i += 1

def tool_create_and_animate_translate_x(args):
    """
    对已存在的 target 物体设置 translateX 关键帧动画。
    不创建任何新几何体。若 target 不存在则报错。

    支持两种参数约定：
      新约定（规划器优先）：target, start_value, end_value, start_time, end_time
      旧约定（向后兼容）：  target/name, end_x/delta_x, start_time, end_time
    """
    # ── 1. 解析 target ──
    target = args.get("target") or args.get("name")
    if not target or not isinstance(target, basestring) or not target.strip():
        raise ToolError("ARG_VALIDATION_FAILED", "需要 target（物体名称）")
    target = target.strip()
    if not cmds.objExists(target):
        raise ToolError("MAYA_INVALID_TARGET",
                        "目标物体不存在：%s — 请先创建它，再添加动画" % target)

    # ── 2. 解析时间 ──
    start_time = float(args.get("start_time", 1))
    end_time_arg = args.get("end_time")
    duration = args.get("duration")
    if end_time_arg is not None:
        end_time = float(end_time_arg)
    elif duration is not None:
        end_time = start_time + float(duration)
    else:
        end_time = start_time + 47.0
    if end_time < start_time:
        raise ToolError("ARG_VALIDATION_FAILED", "end_time 必须 >= start_time")

    # ── 3. 解析起止值 ──
    # 新约定: start_value / end_value
    # 旧约定: end_x / delta_x（相对当前 tx）
    has_start_value = "start_value" in args
    has_end_value   = "end_value"   in args
    has_end_x       = "end_x"       in args
    has_delta_x     = "delta_x"     in args

    try:
        cur_tx = cmds.getAttr(target + ".tx")
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "无法读取 %s.tx: %s" % (target, e))

    if has_start_value:
        start_tx = float(args["start_value"])
    else:
        start_tx = cur_tx          # 以当前位置为起点

    if has_end_value:
        end_tx = float(args["end_value"])
    elif has_end_x:
        end_tx = float(args["end_x"])
    elif has_delta_x:
        end_tx = start_tx + float(args["delta_x"])
    else:
        end_tx = start_tx + 10.0  # 默认向右 10 单位

    # ── 4. 设置关键帧（不改动任何几何） ──
    try:
        # 清理已有 tx 关键帧（防止覆盖混乱）
        try:
            cmds.cutKey(target, attribute="tx")
        except Exception:
            pass

        cmds.currentTime(start_time, edit=True)
        cmds.setAttr(target + ".tx", start_tx)
        cmds.setKeyframe(target, attribute="tx", time=start_time, value=start_tx)

        cmds.currentTime(end_time, edit=True)
        cmds.setAttr(target + ".tx", end_tx)
        cmds.setKeyframe(target, attribute="tx", time=end_time, value=end_tx)

        # 线性 tangent → 匹匀平移速度不变（防止 ease-in/out 造成视觉不均匀）
        cmds.keyTangent(target, attribute="tx",
                        time=(start_time, start_time),
                        inTangentType="linear", outTangentType="linear")
        cmds.keyTangent(target, attribute="tx",
                        time=(end_time, end_time),
                        inTangentType="linear", outTangentType="linear")
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "设置 translateX 关键帧失败：%s" % str(e))

    return {
        "target":      target,
        "attribute":   "translateX",
        "start_value": start_tx,
        "end_value":   end_tx,
        "start_time":  start_time,
        "end_time":    end_time,
    }



def tool_create_bouncing_ball(args):
    radius = float(args.get("radius", 1.0))
    name = args.get("name") or "bouncingBall"
    start_time = float(args.get("start_time", 1))
    end_time_arg = args.get("end_time")
    duration = args.get("duration", 60)
    if end_time_arg is not None:
        end_time = float(end_time_arg)
    else:
        end_time = float(start_time + float(duration) - 1)
    if end_time <= start_time:
        raise ToolError("ARG_VALIDATION_FAILED", "end_time 必须 > start_time")
    bounces = int(args.get("bounces", 3))
    if bounces < 1:
        bounces = 1
    height = float(args.get("height", 10.0))
    decay = float(args.get("decay", 0.6))
    ground_y = float(args.get("ground_y", 0.0))

    try:
        res = cmds.polySphere(r=radius, sx=20, sy=20)
        xform = res[0] if isinstance(res, (list, tuple)) and res else res
        actual = xform
        if isinstance(name, basestring) and name.strip():
            target = name.strip()
            if not cmds.objExists(target):
                actual = cmds.rename(xform, target)
            else:
                actual = cmds.rename(xform, _safe_unique_name(target))
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "polySphere 失败：%s" % str(e))

    try:
        cmds.setAttr(actual + ".ty", ground_y)
    except Exception:
        pass

    frames_total = end_time - start_time
    segment = frames_total / float(bounces)

    try:
        cmds.cutKey(actual, attribute="ty")
    except Exception:
        pass

    keys = []
    for i in range(bounces):
        seg_start = start_time + segment * i
        seg_end = seg_start + segment
        peak_time = (seg_start + seg_end) * 0.5
        amp = height * (decay ** i)
        keys.append((seg_start, ground_y))
        keys.append((peak_time, ground_y + amp))
        keys.append((seg_end, ground_y))

    for t, v in keys:
        cmds.setKeyframe(actual, attribute="ty", time=t, value=v)

    return {
        "transform": actual,
        "radius": radius,
        "start_time": start_time,
        "end_time": end_time,
        "bounces": bounces,
        "height": height,
        "decay": decay,
        "ground_y": ground_y,
    }


def tool_create_loop_rotate(args):
    target = args.get("target")
    if isinstance(target, basestring) and target.strip():
        transforms = [target.strip()]
        if not cmds.objExists(transforms[0]):
            raise ToolError("MAYA_INVALID_TARGET", "目标不存在：%s" % transforms[0])
    else:
        sel = _ensure_selection()
        transforms = _to_transforms_from_selection(sel)
        if len(transforms) != 1:
            raise ToolError("AMBIGUOUS_TARGET", "需要唯一的 transform。请只选择一个，或在参数提供 target。")
    axis_token = str(args.get("axis", "y")).strip().lower()
    axis_map = {"x": "rx", "y": "ry", "z": "rz"}
    axis = axis_map.get(axis_token, "ry")
    start_time = float(args.get("start_time", 1))
    end_time_arg = args.get("end_time")
    duration = args.get("duration")
    if end_time_arg is not None:
        end_time = float(end_time_arg)
    elif duration is not None:
        end_time = float(start_time + float(duration))
    else:
        end_time = float(start_time + 47.0)
    if end_time <= start_time:
        raise ToolError("ARG_VALIDATION_FAILED", "end_time 必须 > start_time")
    rotations = float(args.get("rotations", 1.0))
    t = transforms[0]
    try:
        cmds.currentTime(start_time, edit=True)
        start_val = cmds.getAttr("%s.%s" % (t, axis))
        cmds.setKeyframe(t, attribute=axis, time=start_time, value=start_val)
        cmds.currentTime(end_time, edit=True)
        end_val = start_val + 360.0 * rotations
        cmds.setAttr("%s.%s" % (t, axis), end_val)
        cmds.setKeyframe(t, attribute=axis, time=end_time, value=end_val)
        # 线性 tangent → 旋转匹配匹匀平移，视觉上不会有加速/减速是象
        cmds.keyTangent(t, attribute=axis,
                        time=(start_time, start_time),
                        inTangentType="linear", outTangentType="linear")
        cmds.keyTangent(t, attribute=axis,
                        time=(end_time, end_time),
                        inTangentType="linear", outTangentType="linear")
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "create loop rotate 失败：%s" % str(e))
    return {
        "target": t,
        "axis": axis,
        "start_time": start_time,
        "end_time": end_time,
        "rotations": rotations,
    }


def tool_create_ping_pong_translate(args):
    target = args.get("target")
    if isinstance(target, basestring) and target.strip():
        transforms = [target.strip()]
        if not cmds.objExists(transforms[0]):
            raise ToolError("MAYA_INVALID_TARGET", "目标不存在：%s" % transforms[0])
    else:
        sel = _ensure_selection()
        transforms = _to_transforms_from_selection(sel)
        if len(transforms) != 1:
            raise ToolError("AMBIGUOUS_TARGET", "需要唯一的 transform。请只选择一个，或在参数提供 target。")
    axis_token = str(args.get("axis", "x")).strip().lower()
    axis_map = {"x": "tx", "y": "ty", "z": "tz"}
    axis = axis_map.get(axis_token, "tx")
    start_time = float(args.get("start_time", 1))
    end_time_arg = args.get("end_time")
    duration = args.get("duration")
    if end_time_arg is not None:
        end_time = float(end_time_arg)
    elif duration is not None:
        end_time = float(start_time + float(duration))
    else:
        end_time = float(start_time + 47.0)
    if end_time <= start_time:
        raise ToolError("ARG_VALIDATION_FAILED", "end_time 必须 > start_time")
    distance = float(args.get("distance", 10.0))
    cycles = int(args.get("cycles", 2))
    if cycles < 1:
        cycles = 1
    t = transforms[0]
    plug = "%s.%s" % (t, axis)
    try:
        cmds.currentTime(start_time, edit=True)
        base = cmds.getAttr(plug)
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "查询初始平移失败：%s" % str(e))
    frames_total = end_time - start_time
    segment = frames_total / float(cycles)
    try:
        cmds.cutKey(t, attribute=axis)
    except Exception:
        pass
    keys = []
    keys.append((start_time, base))
    for i in range(cycles):
        sign = 1.0 if (i % 2 == 0) else -1.0
        target_val = base + sign * distance
        t_time = start_time + segment * (i + 1)
        keys.append((t_time, target_val))
    try:
        for tt, vv in keys:
            cmds.setKeyframe(t, attribute=axis, time=tt, value=vv)
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "create ping pong translate 失败：%s" % str(e))
    return {
        "target": t,
        "axis": axis,
        "start_time": start_time,
        "end_time": end_time,
        "distance": distance,
        "cycles": cycles,
    }


def tool_retime_keys(args):
    """
    将指定目标在某个时间点的关键帧移动到另一个时间点。
    - target: 目标 transform 名称；若缺省则使用当前选择，且需要唯一 transform。
    - from_time/to_time: 必填，源与目标时间。
    - attributes: 可选，支持组名/短名；也接受 translateX/rotateY 等会被映射为 tx/ry。
    """
    target = args.get("target")
    if isinstance(target, basestring) and target.strip():
        transforms = [target.strip()]
        if not cmds.objExists(transforms[0]):
            raise ToolError("MAYA_INVALID_TARGET", "目标不存在：%s" % transforms[0])
    else:
        sel = _ensure_selection()
        transforms = _to_transforms_from_selection(sel)
        if len(transforms) != 1:
            raise ToolError("AMBIGUOUS_TARGET", "需要唯一的 transform。请只选择一个，或在参数提供 target。")

    if "from_time" not in args or "to_time" not in args:
        raise ToolError("ARG_VALIDATION_FAILED", "需要 from_time 与 to_time")
    from_t = float(args.get("from_time"))
    to_t = float(args.get("to_time"))

    attr_tokens = args.get("attributes")
    # 兼容 translateX/rotateY/scaleZ 这类写法
    if isinstance(attr_tokens, (list, tuple)):
        mapped = []
        map_long = {
            "translatex": "tx", "translatey": "ty", "translatez": "tz",
            "rotatex": "rx", "rotatey": "ry", "rotatez": "rz",
            "scalex": "sx", "scaley": "sy", "scalez": "sz",
        }
        for t in attr_tokens:
            if isinstance(t, basestring):
                low = t.strip().lower()
                mapped.append(map_long.get(low, t))
        attr_tokens = mapped
    try:
        attrs = expand_attributes(attr_tokens) if attr_tokens is not None else ["tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"]
    except Exception as e:
        raise ToolError("ARG_VALIDATION_FAILED", "attributes 不合法：%s" % str(e))

    try:
        plugs = []
        for t in transforms:
            for a in attrs:
                plugs.append("%s.%s" % (t, a))
        cmds.keyframe(plugs, edit=True, time=(from_t, from_t), timeChange=to_t)
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "retime 失败：%s" % str(e))

    return {"target": transforms[0], "from_time": from_t, "to_time": to_t, "attributes": attrs}


def tool_retime_range(args):
    """
    将目标在 [from_start, from_end] 范围内的关键帧压缩/拉伸并移动到 [to_start, to_end]。
    - target: 目标 transform 名称；若缺省则使用当前选择，且需要唯一 transform。
    - from_start/from_end/to_start/to_end: 必填，源与目标时间范围。
    - attributes: 可选，支持组名/短名；也接受 translateX/rotateY 等。
    """
    target = args.get("target")
    if isinstance(target, basestring) and target.strip():
        transforms = [target.strip()]
        if not cmds.objExists(transforms[0]):
            raise ToolError("MAYA_INVALID_TARGET", "目标不存在：%s" % transforms[0])
    else:
        sel = _ensure_selection()
        transforms = _to_transforms_from_selection(sel)
        if len(transforms) != 1:
            raise ToolError("AMBIGUOUS_TARGET", "需要唯一的 transform。请只选择一个，或在参数提供 target。")

    # 目标区间至少需要 to_start/to_end；from_start/from_end 若缺省则自动从关键帧范围推断
    for key in ("to_start", "to_end"):
        if key not in args:
            raise ToolError("ARG_VALIDATION_FAILED", "需要 %s" % key)
    to_start = float(args.get("to_start"))
    to_end = float(args.get("to_end"))
    if to_end <= to_start:
        raise ToolError("ARG_VALIDATION_FAILED", "to_end 必须 > to_start")

    attr_tokens = args.get("attributes")
    # 兼容 translateX/rotateY/scaleZ 这类写法
    if isinstance(attr_tokens, (list, tuple)):
        mapped = []
        map_long = {
            "translatex": "tx", "translatey": "ty", "translatez": "tz",
            "rotatex": "rx", "rotatey": "ry", "rotatez": "rz",
            "scalex": "sx", "scaley": "sy", "scalez": "sz",
        }
        for t in attr_tokens:
            if isinstance(t, basestring):
                low = t.strip().lower()
                mapped.append(map_long.get(low, t))
        attr_tokens = mapped
    try:
        attrs = expand_attributes(attr_tokens) if attr_tokens is not None else ["tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"]
    except Exception as e:
        raise ToolError("ARG_VALIDATION_FAILED", "attributes 不合法：%s" % str(e))

    # 先构建 plugs，便于后续查询关键帧与编辑
    plugs = []
    for t in transforms:
        for a in attrs:
            plugs.append("%s.%s" % (t, a))

    # 自动推断源区间：若未显式提供 from_start/from_end，则使用当前关键帧最小/最大时间
    if "from_start" in args and "from_end" in args:
        from_start = float(args.get("from_start"))
        from_end = float(args.get("from_end"))
    else:
        try:
            times = cmds.keyframe(plugs, query=True, timeChange=True)
        except Exception as e:
            raise ToolError("MAYA_COMMAND_FAILED", "查询关键帧失败：%s" % str(e))
        if not times:
            raise ToolError("MAYA_NO_KEYS", "目标在选定属性上没有关键帧")
        # cmds.keyframe 可能返回单个 float 或列表
        if not isinstance(times, (list, tuple)):
            times = [times]
        from_start = float(min(times))
        from_end = float(max(times))

    if from_end <= from_start:
        raise ToolError("ARG_VALIDATION_FAILED", "from_end 必须 > from_start")

    scale = (to_end - to_start) / (from_end - from_start)
    offset = to_start - from_start

    # 更稳妥的实现：逐通道复制/删除关键帧，避免一次性 timeScale/timeChange 带来的兼容性问题
    try:
        for plug in plugs:
            times = cmds.keyframe(plug, query=True, timeChange=True)
            if not times:
                continue
            if not isinstance(times, (list, tuple)):
                times = [times]
            # 只处理指定源区间内的关键帧
            src_times = [float(t) for t in times if from_start <= float(t) <= from_end]
            if not src_times:
                continue

            # 提前查询每个源时间点的数值
            values = []
            for t in src_times:
                v = cmds.keyframe(plug, query=True, eval=True, time=(t, t))
                if isinstance(v, (list, tuple)):
                    v = v[0]
                values.append(float(v))

            # 删除旧关键帧
            cmds.cutKey(plug, time=(from_start, from_end), option="keys")

            # 写入新的关键帧
            for t, v in zip(src_times, values):
                new_t = to_start + (t - from_start) * scale
                cmds.setKeyframe(plug, time=new_t, value=v)
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "retime range 失败：%s" % str(e))

    return {
        "target": transforms[0],
        "from_start": from_start,
        "from_end": from_end,
        "to_start": to_start,
        "to_end": to_end,
        "attributes": attrs,
        "scale": scale,
        "offset": offset,
    }


def tool_copy_animation(args):
    source = args.get("source")
    if not isinstance(source, basestring) or not source.strip():
        raise ToolError("ARG_VALIDATION_FAILED", "需要 source")
    source = source.strip()
    if not cmds.objExists(source):
        raise ToolError("MAYA_INVALID_TARGET", "源不存在：%s" % source)
    targets = args.get("targets") or []
    if isinstance(targets, basestring):
        targets = [targets]
    targets = [t for t in targets if isinstance(t, basestring) and t.strip()]
    if not targets:
        raise ToolError("ARG_VALIDATION_FAILED", "需要至少一个 target")
    for t in targets:
        if not cmds.objExists(t):
            raise ToolError("MAYA_INVALID_TARGET", "目标不存在：%s" % t)

    start = args.get("start")
    end = args.get("end")
    attrs_tokens = args.get("attributes", None)
    if attrs_tokens is None or (isinstance(attrs_tokens, (list, tuple)) and len(attrs_tokens) == 0):
        attrs_tokens = ["transform"]
    try:
        attrs = expand_attributes(attrs_tokens)
    except Exception as e:
        raise ToolError("ARG_VALIDATION_FAILED", "attributes 不合法：%s" % str(e))

    if start is None or end is None:
        plugs = []
        for a in attrs:
            plugs.append("%s.%s" % (source, a))
        times = cmds.keyframe(plugs, query=True, timeChange=True)
        if not times:
            raise ToolError("MAYA_NO_KEYS", "源对象在选定属性上没有关键帧")
        if not isinstance(times, (list, tuple)):
            times = [times]
        start = float(min(times))
        end = float(max(times))
    start = float(start)
    end = float(end)
    if end < start:
        raise ToolError("ARG_VALIDATION_FAILED", "end 必须 >= start")

    offset = float(args.get("time_offset", 0.0))

    try:
        cmds.copyKey(source, time=(start, end), attribute=attrs)
        for t in targets:
            cmds.pasteKey(t, option="replaceCompletely", copies=1, timeOffset=offset)
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "copy animation 失败：%s" % str(e))

    return {"source": source, "targets": targets, "start": start, "end": end, "attributes": attrs, "time_offset": offset}


def tool_list_animated_nodes(args):
    time_start = args.get("time_start")
    time_end = args.get("time_end")
    type_filter = str(args.get("type_filter", "all")).strip().lower()
    attrs_tokens = args.get("attributes", None)
    if attrs_tokens is not None and isinstance(attrs_tokens, (list, tuple)) and len(attrs_tokens) == 0:
        attrs_tokens = None
    if attrs_tokens is None:
        attrs = None
    else:
        try:
            attrs = expand_attributes(attrs_tokens)
        except Exception as e:
            raise ToolError("ARG_VALIDATION_FAILED", "attributes 不合法：%s" % str(e))

    if type_filter == "transform":
        nodes = cmds.ls(type="transform") or []
    elif type_filter == "camera":
        nodes = cmds.ls(type="camera") or []
        tmp = []
        for s in nodes:
            parents = cmds.listRelatives(s, parent=True, fullPath=False) or []
            if parents:
                tmp.append(parents[0])
        nodes = list(set(tmp))
    elif type_filter == "joint":
        nodes = cmds.ls(type="joint") or []
    else:
        nodes = cmds.ls(type="transform") or []

    out = []
    for n in nodes:
        plugs = []
        if attrs is None:
            times = cmds.keyframe(n, query=True, timeChange=True)
        else:
            for a in attrs:
                plugs.append("%s.%s" % (n, a))
            times = cmds.keyframe(plugs, query=True, timeChange=True)
        if not times:
            continue
        if not isinstance(times, (list, tuple)):
            times = [times]
        t_min = float(min(times))
        t_max = float(max(times))
        if time_start is not None and t_max < float(time_start):
            continue
        if time_end is not None and t_min > float(time_end):
            continue
        out.append(
            {
                "name": n,
                "type": cmds.nodeType(n),
                "time_min": t_min,
                "time_max": t_max,
            }
        )

    return {"nodes": out, "count": len(out)}


def _normalize_path(path):
    if not isinstance(path, basestring):
        return ""
    p = os.path.expandvars(os.path.expanduser(path.strip()))
    return p.replace("\\", "/")


def _resolve_bomb_asset_path(args):
    path = args.get("path") if isinstance(args, dict) else None
    if isinstance(path, basestring) and path.strip():
        p = _normalize_path(path)
        if os.path.exists(p):
            return p
        raise ToolError("ASSET_NOT_FOUND", "Bomb 资产未找到：%s" % p)

    env_path = os.environ.get("AIFORMAYA_BOMB_ASSET_PATH")
    if env_path:
        p = _normalize_path(env_path)
        if os.path.exists(p):
            return p

    asset_root = os.environ.get("AIFORMAYA_ASSET_ROOT")
    if asset_root:
        p = _normalize_path(os.path.join(asset_root, "Bomb.ma"))
        if os.path.exists(p):
            return p

    maya_location = os.environ.get("MAYA_LOCATION")
    if maya_location:
        p = _normalize_path(os.path.join(maya_location, "Examples", "FX", "Effects_Assets", "Bomb.ma"))
        if os.path.exists(p):
            return p

    candidates = []
    candidates.extend(glob.glob("C:/Program Files/Autodesk/Maya*/Examples/FX/Effects_Assets/Bomb.ma"))
    candidates.extend(glob.glob("C:/Program Files/Maya*/Examples/FX/Effects_Assets/Bomb.ma"))
    for c in candidates:
        p = _normalize_path(c)
        if os.path.exists(p):
            return p

    default_path = "C:/Program Files/Autodesk/Maya2020/Examples/FX/Effects_Assets/Bomb.ma"
    if os.path.exists(default_path):
        return _normalize_path(default_path)

    raise ToolError("ASSET_NOT_FOUND", "未找到 Bomb 资产文件，请设置 AIFORMAYA_BOMB_ASSET_PATH 或 AIFORMAYA_ASSET_ROOT，或传入 path")


def _mel_escape(path):
    return _normalize_path(path).replace('"', '\\"')


def tool_import_bomb_asset(args):
    path = _resolve_bomb_asset_path(args)
    namespace = args.get("namespace") if isinstance(args, dict) else None
    if not isinstance(namespace, basestring) or not namespace.strip():
        namespace = "Bomb"
    ns = _mel_escape(namespace)
    p = _mel_escape(path)
    try:
        mel.eval('performFileSilentImportAction "%s";' % p)
        mel.eval('file -import -type "mayaAscii" -ignoreVersion -ra true -mergeNamespacesOnClash false -namespace "%s" -options "v=0;" -pr -importTimeRange "combine" "%s";' % (ns, p))
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "导入 Bomb 资产失败：%s" % str(e))
    return {"path": path, "namespace": namespace}


def tool_create_three_point_lighting(args):
    """创建三点基础布光"""
    target = args.get("target") or ""
    base_intensity = float(args.get("intensity", 1.0))
    import maya.cmds as cmds
    try:
        # 智能判定物体包围盒
        bbox = None
        if target and cmds.objExists(target):
            bbox = cmds.exactWorldBoundingBox(target)
        else:
            sel = cmds.ls(sl=True) or []
            if sel:
                bbox = cmds.exactWorldBoundingBox(sel)
                
        if bbox:
            cx = (bbox[0] + bbox[3]) / 2.0
            cy = (bbox[1] + bbox[4]) / 2.0
            cz = (bbox[2] + bbox[5]) / 2.0
            max_dim = max(bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2])
            if max_dim < 0.1: max_dim = 10.0
        else:
            cx, cy, cz = 0.0, 0.0, 0.0
            max_dim = 20.0
            
        dist = max_dim * 1.5
        intensity = base_intensity
        
        # Key Light
        key = cmds.directionalLight(name="Key_Light", intensity=intensity * 1.2)
        key_transform = cmds.listRelatives(key, parent=True)[0]
        cmds.setAttr(key_transform + ".translate", cx + dist, cy + dist, cz + dist)
        cmds.setAttr(key_transform + ".rotate", -45, 45, 0)
        cmds.setAttr(key + ".useDepthMapShadows", 1)
        
        # Fill Light
        fill = cmds.directionalLight(name="Fill_Light", intensity=intensity * 0.5)
        fill_transform = cmds.listRelatives(fill, parent=True)[0]
        cmds.setAttr(fill_transform + ".translate", cx - dist, cy + dist*0.5, cz + dist)
        cmds.setAttr(fill_transform + ".rotate", -20, -45, 0)
        
        # Back Light
        back = cmds.directionalLight(name="Back_Light", intensity=intensity * 1.5)
        back_transform = cmds.listRelatives(back, parent=True)[0]
        cmds.setAttr(back_transform + ".translate", cx, cy + dist*1.5, cz - dist*1.5)
        cmds.setAttr(back_transform + ".rotate", -45, 180, 0)
        
        # Group them
        group = cmds.group([key_transform, fill_transform, back_transform], name="ThreePointLighting_Grp")
        
        # If target, aim lights at target
        aim_target = target if (target and cmds.objExists(target)) else (cmds.ls(sl=True)[0] if cmds.ls(sl=True) else None)
        if aim_target:
            cmds.aimConstraint(aim_target, key_transform, aimVector=(0,0,-1))
            cmds.aimConstraint(aim_target, fill_transform, aimVector=(0,0,-1))
            cmds.aimConstraint(aim_target, back_transform, aimVector=(0,0,-1))
            
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "创建三点布光失败：%s" % str(e))
        
    return {
        "summary": "根据场景尺寸（包围盒）自动计算了灯光放置距离并完成了打光，主光倍增为 %s。" % round(intensity * 1.2, 2),
        "group": group, 
        "lights": [key_transform, fill_transform, back_transform]
    }

def tool_create_turntable(args):
    """创建环绕展示摄像机"""
    target = args.get("target") or ""
    frames = int(args.get("frames", 120))
    import maya.cmds as cmds
    try:
        # 智能判定物体包围盒
        bbox = None
        sel_target = None
        if target and cmds.objExists(target):
            bbox = cmds.exactWorldBoundingBox(target)
            sel_target = target
        else:
            sel = cmds.ls(sl=True) or []
            if sel:
                bbox = cmds.exactWorldBoundingBox(sel)
                sel_target = sel[0]
                
        if bbox:
            cx = (bbox[0] + bbox[3]) / 2.0
            cy = (bbox[1] + bbox[4]) / 2.0
            cz = (bbox[2] + bbox[5]) / 2.0
            max_dim = max(bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2])
            if max_dim < 0.1: max_dim = 10.0
            # 自适应距离和高度
            calc_distance = max_dim * 2.2
            calc_height = (bbox[4] - bbox[1]) * 0.35 + cy
        else:
            cx, cy, cz = 0.0, 0.0, 0.0
            calc_distance = 20.0
            calc_height = 5.0
            
        dist = float(args.get("distance", calc_distance))

        loc_name = "Turntable_Center" if not sel_target else "Turntable_Center_" + sel_target
        loc = cmds.spaceLocator(name=loc_name)[0]
        cmds.xform(loc, translation=[cx, calc_height, cz], worldSpace=True)
            
        cam, cam_shape = cmds.camera(name="Turntable_Camera")
        cmds.setAttr(cam + ".translateZ", dist)
        
        # Group the camera into the locator
        cmds.parent(cam, loc)
        
        # Keyframe the locator to spin 360 degrees
        start_time = cmds.playbackOptions(query=True, minTime=True)
        end_time = start_time + frames
        
        cmds.setKeyframe(loc, attribute="rotateY", t=start_time, v=0)
        cmds.setKeyframe(loc, attribute="rotateY", t=end_time, v=360)
        
        # Make it linear
        cmds.selectKey(loc, attribute="rotateY")
        cmds.keyTangent(inTangentType="linear", outTangentType="linear")
        cmds.playbackOptions(animationStartTime=start_time, animationEndTime=end_time, maxTime=end_time)
        
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "创建环绕摄像机失败：%s" % str(e))
        
    # v2.2: update memory
    try:
        EntityMemory.update_last_created("camera", cam)
    except Exception:
        pass

    return {
        "summary": "根据对象尺寸智能设置了摄像机旋转半径 %s 和目标高度 %s" % (round(dist, 1), round(calc_height, 1)),
        "camera": cam,
        "center_locator": loc,
        "frames": frames
    }


def tool_cleanup_scene(args):
    """清理选中物体的历史、冻结变换、居中枢轴"""
    import maya.cmds as cmds
    import maya.mel as mel
    
    sel = cmds.ls(sl=True, type="transform") or []
    if not sel:
        # Prompt user contextually if nothing selected
        from .maya_tools import ConfirmationError
        raise ConfirmationError(
            action="清理整个场景",
            target="所有物体",
            options=["是，清理全部", "取消"]
        )
        
    cmds.undoInfo(openChunk=True)
    try:
        # 1. Delete Non-Deformer History
        # mel.eval('BakeNonDefHistory') -> but python safe is better
        # Actually bakePartialHistory is safer in python
        for obj in sel:
            cmds.bakePartialHistory(obj, prePostDeformers=True)
            
        # 2. Freeze Transforms
        cmds.makeIdentity(sel, apply=True, t=1, r=1, s=1, n=0, pn=1)
        
        # 3. Center Pivot
        cmds.xform(sel, centerPivots=True)
    except Exception as e:
        cmds.undoInfo(closeChunk=True)
        raise ToolError("MAYA_COMMAND_FAILED", "清理场景历史失败：%s" % str(e))
        
    cmds.undoInfo(closeChunk=True)
    return {"summary": "成功清理了 %d 个物体的历史并重置了变换" % len(sel), "count": len(sel)}


def tool_group_and_center(args):
    """打组并居中枢轴"""
    import maya.cmds as cmds
    group_name = args.get("group_name") or "Auto_Group"
    
    sel = cmds.ls(sl=True) or []
    if not sel:
        raise ToolError("MAYA_SELECTION_REQUIRED", "请先选择要打组的物体")
        
    cmds.undoInfo(openChunk=True)
    try:
        grp = cmds.group(sel, name=group_name)
        cmds.xform(grp, centerPivots=True)
    except Exception as e:
        cmds.undoInfo(closeChunk=True)
        raise ToolError("MAYA_COMMAND_FAILED", "打组失败：%s" % str(e))
        
    cmds.undoInfo(closeChunk=True)
    return {"summary": "成功将 %d 个物体打组为 %s，并居中了枢轴" % (len(sel), grp), "group": grp}


def tool_randomize_transforms(args):
    """随机变换选中物体"""
    import maya.cmds as cmds
    import random
    
    sel = cmds.ls(sl=True, type="transform") or []
    if not sel:
        raise ToolError("MAYA_SELECTION_REQUIRED", "请先在 Maya 中选中要随机打散的物体")
        
    t_min = args.get("translate_min", [0,0,0])
    t_max = args.get("translate_max", [0,0,0])
    r_min = args.get("rotate_min", [0,0,0])
    r_max = args.get("rotate_max", [0,0,0])
    s_min = args.get("scale_min", [1,1,1])
    s_max = args.get("scale_max", [1,1,1])
    uniform_scale = args.get("uniform_scale", True)
    
    cmds.undoInfo(openChunk=True)
    try:
        for obj in sel:
            tx = random.uniform(t_min[0], t_max[0])
            ty = random.uniform(t_min[1], t_max[1])
            tz = random.uniform(t_min[2], t_max[2])
            
            rx = random.uniform(r_min[0], r_max[0])
            ry = random.uniform(r_min[1], r_max[1])
            rz = random.uniform(r_min[2], r_max[2])
            
            cmds.xform(obj, relative=True, translation=[tx, ty, tz])
            cmds.xform(obj, relative=True, rotation=[rx, ry, rz])
            
            if uniform_scale:
                s = random.uniform(s_min[0], s_max[0])
                cmds.setAttr(obj + ".scale", s, s, s)
            else:
                sx = random.uniform(s_min[0], s_max[0])
                sy = random.uniform(s_min[1], s_max[1])
                sz = random.uniform(s_min[2], s_max[2])
                cmds.setAttr(obj + ".scale", sx, sy, sz)
    except Exception as e:
        cmds.undoInfo(closeChunk=True)
        raise ToolError("MAYA_COMMAND_FAILED", "随机变换失败：%s" % str(e))
        
    cmds.undoInfo(closeChunk=True)
    return {"summary": "成功随机打散了 %d 个选中物体" % len(sel)}


def tool_assign_color_materials(args):
    """快速创建并赋予纯色材质"""
    import maya.cmds as cmds
    import random
    
    sel = cmds.ls(sl=True) or []
    if not sel:
        raise ToolError("MAYA_SELECTION_REQUIRED", "请先在 Maya 中选中要赋予材质的物体")
        
    color = args.get("color")
    random_colors = args.get("random_colors", False)
    
    cmds.undoInfo(openChunk=True)
    try:
        assigned_mats = []
        for obj in sel:
            # Create a simple lambert
            mat = cmds.shadingNode("lambert", asShader=True)
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=mat + "SG")
            cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader", force=True)
            
            if random_colors:
                c = [random.random(), random.random(), random.random()]
            elif color and len(color) == 3:
                # Clamp safely
                c = [max(0, min(1, float(color[0]))), max(0, min(1, float(color[1]))), max(0, min(1, float(color[2])))]
            else:
                c = [0.8, 0.8, 0.8] # Default gray
                
            cmds.setAttr(mat + ".color", c[0], c[1], c[2], type="double3")
            
            cmds.sets(obj, forceElement=sg)
            assigned_mats.append(mat)
    except Exception as e:
        cmds.undoInfo(closeChunk=True)
        raise ToolError("MAYA_COMMAND_FAILED", "赋予材质失败：%s" % str(e))
        
    cmds.undoInfo(closeChunk=True)
    return {"summary": "成功为 %d 个物体创建并赋予了材质" % len(sel), "materials": assigned_mats}


def _get_target_and_source(args, allow_fallback=True):
    source = args.get("source")
    target = args.get("target")

    if not source or not target:
        if allow_fallback:
            sel = cmds.ls(sl=True, fl=True) or []
            if len(sel) != 2:
                raise ToolError("MAYA_SELECTION_REQUIRED", "需要选中 2 个 transform（source 与 target）")
            for s in sel:
                if "." in s:
                    raise ToolError("MAYA_INVALID_SELECTION", "该工具需要选择物体（transform），不能是组件选择")
            transforms = _to_transforms_from_selection(sel)
            if len(transforms) != 2:
                raise ToolError("MAYA_SELECTION_REQUIRED", "需要选中 2 个 transform（source 与 target）")
            source = source or transforms[0]
            target = target or transforms[1]
        else:
             raise ToolError("MAYA_SELECTION_REQUIRED", "需要提供 source 和 target")

    if not isinstance(source, basestring) or not source.strip():
        raise ToolError("MAYA_INVALID_TARGET", "source 必须是字符串")
    if not isinstance(target, basestring) or not target.strip():
        raise ToolError("MAYA_INVALID_TARGET", "target 必须是字符串")

    source = source.strip()
    target = target.strip()

    if not cmds.objExists(source):
        raise ToolError("MAYA_INVALID_TARGET", "source 不存在: %s" % source)
    if not cmds.objExists(target):
         raise ToolError("MAYA_INVALID_TARGET", "target 不存在: %s" % target)

    if cmds.nodeType(source) != "transform":
         parents = cmds.listRelatives(source, parent=True, fullPath=False) or []
         if parents and cmds.nodeType(parents[0]) == "transform":
             source = parents[0]
         else:
             raise ToolError("MAYA_INVALID_SELECTION", "source 必须是 transform")

    if cmds.nodeType(target) != "transform":
        parents = cmds.listRelatives(target, parent=True, fullPath=False) or []
        if parents and cmds.nodeType(parents[0]) == "transform":
            target = parents[0]
        else:
            raise ToolError("MAYA_INVALID_SELECTION", "target 必须是 transform")

    return source, target

def tool_match_transform(args):
    source, target = _get_target_and_source(args)
    do_t = bool(args.get("translate", True))
    do_r = bool(args.get("rotate", True))
    do_s = bool(args.get("scale", False))
    space = args.get("space", "world")
    set_key = bool(args.get("set_key", False))
    time_val = args.get("time")

    if space not in ("world", "object"):
        space = "world"

    try:
        if time_val is not None:
             cmds.currentTime(float(time_val), edit=True)

        before = {
            "t": cmds.xform(source, q=True, ws=(space=="world"), os=(space=="object"), t=True) or [0,0,0],
            "r": cmds.xform(source, q=True, ws=(space=="world"), os=(space=="object"), ro=True) or [0,0,0],
            "s": cmds.xform(source, q=True, ws=(space=="world"), os=(space=="object"), s=True) or [1,1,1]
        }
        
        target_t = cmds.xform(target, q=True, ws=(space=="world"), os=(space=="object"), t=True) or [0,0,0]
        target_r = cmds.xform(target, q=True, ws=(space=="world"), os=(space=="object"), ro=True) or [0,0,0]
        target_s = cmds.xform(target, q=True, ws=(space=="world"), os=(space=="object"), s=True) or [1,1,1]

        if do_t:
             cmds.xform(source, ws=(space=="world"), os=(space=="object"), t=target_t)
        if do_r:
             cmds.xform(source, ws=(space=="world"), os=(space=="object"), ro=target_r)
        if do_s:
             try:
                 cmds.setAttr(source + ".sx", target_s[0])
                 cmds.setAttr(source + ".sy", target_s[1])
                 cmds.setAttr(source + ".sz", target_s[2])
             except Exception:
                 pass

        after = {
            "t": cmds.xform(source, q=True, ws=(space=="world"), os=(space=="object"), t=True) or [0,0,0],
            "r": cmds.xform(source, q=True, ws=(space=="world"), os=(space=="object"), ro=True) or [0,0,0],
            "s": cmds.xform(source, q=True, ws=(space=="world"), os=(space=="object"), s=True) or [1,1,1]
        }

        if set_key:
            plugs = []
            if do_t: plugs += [source+".tx", source+".ty", source+".tz"]
            if do_r: plugs += [source+".rx", source+".ry", source+".rz"]
            if do_s: plugs += [source+".sx", source+".sy", source+".sz"]
            if plugs:
                 cmds.setKeyframe(plugs)

        return {
             "source": source,
             "target": target,
             "applied": {"translate": do_t, "rotate": do_r, "scale": do_s},
             "space": space,
             "time": float(cmds.currentTime(q=True)),
             "set_key": set_key,
             "before": before,
             "after": after
        }
    except Exception as e:
         raise ToolError("MAYA_COMMAND_FAILED", "match_transform 失败：%s" % str(e))

def _axis_to_vector(axis_str):
    mapping = {
        "x": [1,0,0], "-x": [-1,0,0],
        "y": [0,1,0], "-y": [0,-1,0],
        "z": [0,0,1], "-z": [0,0,-1]
    }
    return mapping.get(str(axis_str).lower(), [0,0,1])

def tool_aim_at_target(args):
    source, target = _get_target_and_source(args)

    aim_axis = args.get("aim_axis", "z")
    up_axis = args.get("up_axis", "y")
    world_up_type = args.get("world_up_type", "scene")
    world_up_object = args.get("world_up_object")
    maintain_offset = bool(args.get("maintain_offset", False))
    create_constraint = bool(args.get("create_constraint", True))
    delete_constraint_after = bool(args.get("delete_constraint_after", False))
    set_key = bool(args.get("set_key", False))
    time_val = args.get("time")

    if world_up_type == "object":
        if not world_up_object or not cmds.objExists(world_up_object):
             raise ToolError("ARG_VALIDATION_FAILED", "world_up_type=object 但未提供有效的 world_up_object")

    aim_vec = _axis_to_vector(aim_axis)
    up_vec = _axis_to_vector(up_axis)

    try:
        if time_val is not None:
             cmds.currentTime(float(time_val), edit=True)

        kwargs = {
            "aimVector": aim_vec,
            "upVector": up_vec,
            "worldUpType": "scene" if world_up_type == "scene" else "object",
            "mo": maintain_offset
        }
        if world_up_type == "object":
             kwargs["worldUpObject"] = world_up_object

        res = cmds.aimConstraint(target, source, **kwargs)
        constraint_name = res[0] if isinstance(res, (list, tuple)) and res else res

        mode = "persistent"
        deleted = False

        if delete_constraint_after:
             cmds.currentTime(cmds.currentTime(q=True), e=True)
             current_ro = cmds.xform(source, q=True, ws=True, ro=True) or [0,0,0]
             cmds.delete(constraint_name)
             deleted = True
             mode = "one_shot"
             try:
                 cmds.xform(source, ws=True, ro=current_ro)
             except Exception:
                 pass

        if set_key:
            cmds.setKeyframe([source+".rx", source+".ry", source+".rz"])

        return {
            "source": source,
            "target": target,
            "aim_axis": aim_axis,
            "up_axis": up_axis,
            "world_up_type": world_up_type,
            "maintain_offset": maintain_offset,
            "constraint": {
                "created": create_constraint,
                "name": constraint_name,
                "deleted": deleted
            },
            "mode": mode,
            "set_key": set_key,
            "time": float(cmds.currentTime(q=True))
        }

    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "aim_at_target 失败：%s" % str(e))

def _get_drivers_and_driven(args):
    drivers = args.get("drivers")
    driven = args.get("driven")
    
    if isinstance(drivers, basestring):
         drivers = [drivers]
         
    if not drivers: drivers = []
    
    if not drivers and not driven:
         sel = cmds.ls(sl=True, fl=True) or []
         if len(sel) < 2:
             raise ToolError("MAYA_SELECTION_REQUIRED", "需要选择至少 2 个物体（前面的为驱动，最后一个为被驱动）")
         for s in sel:
             if "." in s:
                 raise ToolError("MAYA_INVALID_SELECTION", "约束工具必须选择 transform，不能有组件")
         
         sel_trans = _to_transforms_from_selection(sel)
         if len(sel_trans) < 2:
             raise ToolError("MAYA_SELECTION_REQUIRED", "需要选择至少 2 个 transform")
             
         driven = sel_trans[-1]
         drivers = sel_trans[:-1]
    
    elif driven and not drivers:
         sel = cmds.ls(sl=True, fl=True) or []
         if not sel:
              raise ToolError("MAYA_SELECTION_REQUIRED", "提供了 driven 但没有 drivers，请在场景中选择 drivers")
         sel_trans = []
         for s in sel:
            if "." in s: continue
            try:
                sel_trans += _to_transforms_from_selection([s])
            except Exception: pass
            
         drivers = [item for item in sel_trans if item != driven]
         if not drivers:
              raise ToolError("MAYA_SELECTION_REQUIRED", "提供了 driven 但选择集中没有其他有效 transform 做 drivers")
    
    elif drivers and not driven:
         sel = cmds.ls(sl=True, fl=True) or []
         if not sel:
             raise ToolError("MAYA_SELECTION_REQUIRED", "提供了 drivers 但没有 driven，请在场景中最后选择 driven")
         sel_trans = []
         for s in sel:
            if "." in s: continue
            try:
                sel_trans += _to_transforms_from_selection([s])
            except Exception: pass
         
         if sel_trans:
             driven = sel_trans[-1]
         else:
             raise ToolError("MAYA_SELECTION_REQUIRED", "提供了 drivers 但没有 driven，且无法从选择集中提取 driven")

    if not drivers or not driven:
         raise ToolError("MAYA_SELECTION_REQUIRED", "无法解析有效的 drivers 和 driven")
         
    if len(drivers) > 10:
         raise ToolError("MAYA_TOO_MANY_TARGETS", "驱动物体 drivers 数量过多（>10），暂不支持")
         
    for n in drivers + [driven]:
         if not cmds.objExists(n):
              raise ToolError("MAYA_INVALID_TARGET", "目标不存在: %s" % n)
         if cmds.nodeType(n) != "transform":
              parents = cmds.listRelatives(n, parent=True, fullPath=False) or []
              if not parents or cmds.nodeType(parents[0]) != "transform":
                  raise ToolError("MAYA_INVALID_SELECTION", "约束目标必须是 transform: %s" % n)

    return drivers, driven

def _do_constraint(constraint_func, args):
    drivers, driven = _get_drivers_and_driven(args)
    maintain_offset = bool(args.get("maintain_offset", True))
    weight = float(args.get("weight", 1.0))
    skip_axes = args.get("skip_axes") or []
    delete_constraint_after = bool(args.get("delete_constraint_after", False))
    set_key = bool(args.get("set_key", False))
    time_val = args.get("time")
    
    if not isinstance(skip_axes, (list, tuple)): skip_axes = [skip_axes]
    skip_axes = [str(x).lower() for x in skip_axes if x in ('x','y','z')]

    try:
        if time_val is not None:
             cmds.currentTime(float(time_val), edit=True)
             
        kwargs = {
            "mo": maintain_offset,
            "weight": weight
        }
        if skip_axes:
             kwargs["skip"] = skip_axes
             
        cmd_args = drivers + [driven]
        res = constraint_func(*cmd_args, **kwargs)
        constraint_name = res[0] if isinstance(res, (list, tuple)) and res else res
        
        deleted = False
        if delete_constraint_after:
             cmds.currentTime(cmds.currentTime(q=True), e=True)
             cur_t = cmds.xform(driven, q=True, ws=True, t=True) or [0,0,0]
             cur_r = cmds.xform(driven, q=True, ws=True, ro=True) or [0,0,0]
             cmds.delete(constraint_name)
             deleted = True
             try:
                 cmds.xform(driven, ws=True, t=cur_t, ro=cur_r)
             except Exception:
                 pass
                 
        if set_key:
             plugs = []
             if constraint_func == cmds.pointConstraint or constraint_func == cmds.parentConstraint:
                 plugs += [driven+".tx", driven+".ty", driven+".tz"]
             if constraint_func == cmds.orientConstraint or constraint_func == cmds.parentConstraint:
                 plugs += [driven+".rx", driven+".ry", driven+".rz"]
             if plugs:
                 cmds.setKeyframe(plugs)
                 
        return {
            "drivers": drivers,
            "driven": driven,
            "constraint_name": constraint_name,
            "deleted": deleted,
            "maintain_offset": maintain_offset,
            "skip_axes": skip_axes,
            "time": float(cmds.currentTime(q=True)),
            "set_key": set_key
        }
    except Exception as e:
         raise ToolError("MAYA_COMMAND_FAILED", "constraint 失败：%s" % str(e))

def tool_point_constraint(args):
    return _do_constraint(cmds.pointConstraint, args)
    
def tool_orient_constraint(args):
    return _do_constraint(cmds.orientConstraint, args)
    
def tool_parent_constraint(args):
    return _do_constraint(cmds.parentConstraint, args)

def tool_create_object_and_camera_and_aim(args):
    object_type = args.get("object_type", "sphere").lower()
    object_name = args.get("object_name")
    camera_name = args.get("camera_name")
    distance_multiplier = float(args.get("distance_multiplier", 4.0))
    min_distance = float(args.get("min_distance", 10.0))
    place_direction = args.get("place_direction", "-z").lower()
    create_persistent_constraint = bool(args.get("create_persistent_constraint", True))
    camera_forward_axis = args.get("camera_forward_axis", "-z").lower()

    try:
        if object_type == "cube":
            obj = cmds.polyCube(name=object_name)[0] if object_name else cmds.polyCube()[0]
        elif object_type == "cylinder":
            obj = cmds.polyCylinder(name=object_name)[0] if object_name else cmds.polyCylinder()[0]
        else:
            obj = cmds.polySphere(name=object_name)[0] if object_name else cmds.polySphere()[0]
            object_type = "sphere"
        
        if camera_name:
            cam, cam_shape = cmds.camera(name=camera_name)
        else:
            cam, cam_shape = cmds.camera()

        bbox = cmds.exactWorldBoundingBox(obj)
        radius_est = max(bbox[3]-bbox[0], bbox[4]-bbox[1], bbox[5]-bbox[2]) / 2.0
        distance = max(radius_est * distance_multiplier, min_distance)
        
        obj_pos = cmds.xform(obj, q=True, ws=True, t=True)
        cam_pos = list(obj_pos)

        dir_map = {
            "+x": [1,0,0], "-x": [-1,0,0],
            "+y": [0,1,0], "-y": [0,-1,0],
            "+z": [0,0,1], "-z": [0,0,-1],
        }
        vec = dir_map.get(place_direction, [0,0,-1])
        cam_pos[0] += vec[0] * distance
        cam_pos[1] += vec[1] * distance
        cam_pos[2] += vec[2] * distance

        cmds.xform(cam, ws=True, t=cam_pos)

        # To aim the camera AT the object, its LOCAL forward axis must point to the target.
        # Maya's default camera forward is -Z.
        axis_map = {
            "+x": [1,0,0], "-x": [-1,0,0],
            "+y": [0,1,0], "-y": [0,-1,0],
            "+z": [0,0,1], "-z": [0,0,-1]
        }
        aim_vec = axis_map.get(camera_forward_axis, [0, 0, -1])
        
        # Calculate up vector to prevent flipping. Default scene up is +Y.
        up_vec = [0, 1, 0]
        if abs(aim_vec[1]) == 1.0: # If aiming straight up or down
            up_vec = [0, 0, -1] if aim_vec[1] > 0 else [0, 0, 1]

        # aimConstraint syntax: aimVector defines WHICH local axis points at the target.
        constraint = cmds.aimConstraint(
            obj, cam, 
            aimVector=aim_vec, 
            upVector=up_vec, 
            worldUpType="vector", 
            worldUpVector=[0, 1, 0],
            maintainOffset=False
        )[0]

        if not create_persistent_constraint:
            cmds.delete(constraint)
            constraint_result = {"type": "aim", "name": constraint, "persistent": False}
        else:
            constraint_result = {"type": "aim", "name": constraint, "persistent": True}

        EntityMemory.update_last_created(object_type, obj)
        EntityMemory.update_last_created("camera", cam)

        return {
            "object": obj,
            "object_type": object_type,
            "camera_transform": cam,
            "camera_shape": cam_shape,
            "camera_position": cam_pos,
            "object_position": obj_pos,
            "distance_used": distance,
            "constraint": constraint_result
        }
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "create_object_and_camera_and_aim 失败：%s" % str(e))

TOOLS = [
    {
        "name": "maya.match_transform",
        "description": "将一个 transform 的 translate/rotate/scale（可选）对齐到另一个 transform。",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": { "type": "string", "description": "要被对齐的物体（缺省使用选择第1个）" },
                "target": { "type": "string", "description": "对齐参照物体（缺省使用选择第2个）" },
                "translate": { "type": "boolean", "default": True },
                "rotate": { "type": "boolean", "default": True },
                "scale": { "type": "boolean", "default": False },
                "space": { "type": "string", "enum": ["world", "object"], "default": "world" },
                "set_key": { "type": "boolean", "default": False },
                "time": { "type": "number", "description": "可选；先切到该帧再执行" }
            },
            "required": []
        },
        "handler": tool_match_transform,
    },
    {
        "name": "maya.aim_at_target",
        "description": "让 source 朝向 target，可选择创建 aimConstraint 持续跟随，或对准一次后删除约束。",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": { "type": "string" },
                "target": { "type": "string" },
                "aim_axis": { "type": "string", "enum": ["x","y","z","-x","-y","-z"], "default": "z" },
                "up_axis":  { "type": "string", "enum": ["x","y","z","-x","-y","-z"], "default": "y" },
                "world_up_type": { "type": "string", "enum": ["scene","object"], "default": "scene" },
                "world_up_object": { "type": "string", "description": "world_up_type=object 时必填" },
                "maintain_offset": { "type": "boolean", "default": False },
                "create_constraint": { "type": "boolean", "default": True },
                "delete_constraint_after": { "type": "boolean", "default": False },
                "set_key": { "type": "boolean", "default": False },
                "time": { "type": "number" }
            },
            "required": []
        },
        "handler": tool_aim_at_target,
    },
    {
        "name": "maya.point_constraint",
        "description": "创建点约束。",
        "input_schema": {
            "type": "object",
            "properties": {
                "drivers": { "type": ["array","string"], "items": { "type": "string" }, "description": "驱动物体；缺省使用选择中除最后一个外的全部" },
                "driven":  { "type": "string", "description": "被驱动物体；缺省使用选择最后一个" },
                "maintain_offset": { "type": "boolean", "default": True },
                "weight": { "type": "number", "default": 1.0 },
                "skip_axes": { "type": "array", "items": { "type": "string", "enum": ["x","y","z"] } },
                "delete_constraint_after": { "type": "boolean", "default": False },
                "time": { "type": "number" },
                "set_key": { "type": "boolean", "default": False }
            },
            "required": []
        },
        "handler": tool_point_constraint,
    },
    {
        "name": "maya.orient_constraint",
        "description": "创建方向约束。",
        "input_schema": {
            "type": "object",
            "properties": {
                "drivers": { "type": ["array","string"], "items": { "type": "string" }, "description": "驱动物体；缺省使用选择中除最后一个外的全部" },
                "driven":  { "type": "string", "description": "被驱动物体；缺省使用选择最后一个" },
                "maintain_offset": { "type": "boolean", "default": True },
                "weight": { "type": "number", "default": 1.0 },
                "skip_axes": { "type": "array", "items": { "type": "string", "enum": ["x","y","z"] } },
                "delete_constraint_after": { "type": "boolean", "default": False },
                "time": { "type": "number" },
                "set_key": { "type": "boolean", "default": False }
            },
            "required": []
        },
        "handler": tool_orient_constraint,
    },
    {
        "name": "maya.parent_constraint",
        "description": "创建父约束。",
        "input_schema": {
            "type": "object",
            "properties": {
                "drivers": { "type": ["array","string"], "items": { "type": "string" }, "description": "驱动物体；缺省使用选择中除最后一个外的全部" },
                "driven":  { "type": "string", "description": "被驱动物体；缺省使用选择最后一个" },
                "maintain_offset": { "type": "boolean", "default": True },
                "weight": { "type": "number", "default": 1.0 },
                "skip_axes": { "type": "array", "items": { "type": "string", "enum": ["x","y","z"] } },
                "delete_constraint_after": { "type": "boolean", "default": False },
                "time": { "type": "number" },
                "set_key": { "type": "boolean", "default": False }
            },
            "required": []
        },
        "handler": tool_parent_constraint,
    },
    {
        "name": "maya.list_tools",
        "description": "返回可用工具的清单（名称与描述）。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "handler": lambda _args: [{"name": t["name"], "description": t["description"]} for t in TOOLS],
    },
    {
        "name": "maya.list_selection",
        "description": "列出当前选中的节点与组件信息。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "handler": tool_list_selection,
    },
    {
        "name": "maya.list_cameras",
        "description": "列出场景中的摄像机，可选是否包含默认摄像机或仅渲染摄像机。",
        "input_schema": {
            "type": "object",
            "properties": {
                "include_defaults": {"type": "boolean", "default": False},
                "only_renderable": {"type": "boolean", "default": False},
            },
            "required": [],
        },
        "handler": tool_list_cameras,
    },
    {
        "name": "maya.scan_scene_summary",
        "description": (
            u"扫描整个场景并返回结构化的场景摘要。"
            u"自动过滤 Maya 默认相机（persp/top/front/side）和系统节点，"
            u"识别绑定角色（含 joint 层级和皮肤权重）、几何体、用户相机、灯光、曲线。"
            u"大型场景返回数量汇总，小型场景返回详细列表。"
            u"当用户询问\u300c场景中有什么\u300d或\u300c场景内容\u300d时，优先使用此工具。"
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "handler": tool_scan_scene_summary,
    },
    {
        "name": "maya.select_by_name_pattern",
        "description": u"\u6309\u540d\u79f0\u901a\u914d\u7b26\u9009\u62e9\u5bf9\u8c61\uff0c\u53ef\u6309\u7c7b\u578b\u8fc7\u6ee4\u3002",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "type_filter": {
                    "type": "string",
                    "enum": ["transform", "camera", "joint", "mesh", "all"],
                    "default": "all",
                },
                "mode": {
                    "type": "string",
                    "enum": ["replace", "add", "remove"],
                    "default": "replace",
                },
            },
            "required": ["pattern"],
        },
        "handler": tool_select_by_name_pattern,
    },
    {
        "name": "maya.select_connected_components",
        "description": "对当前 polygon 组件选择执行连通壳（shell）扩展，结果为面选择。超过 200000 面会拒绝。",
        "input_schema": {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["shell"], "default": "shell"}, "prefer": {"type": "string", "enum": ["face"], "default": "face"}},
            "required": [],
        },
        "handler": tool_select_connected_components,
    },
    {
        "name": "maya.grow_selection",
        "description": "组件选择向外增长 steps（1-5）。",
        "input_schema": {"type": "object", "properties": {"steps": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1}}, "required": []},
        "handler": tool_grow_selection,
    },
    {
        "name": "maya.rename_batch",
        "description": "批量重命名选中的 transform。pattern 必须包含 {i}，可用 {i:03d} 补零。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "minLength": 1},
                "start": {"type": "integer", "minimum": 0, "default": 1},
                "scope": {"type": "string", "enum": ["selected"], "default": "selected"},
                "target_type": {"type": "string", "enum": ["transform"], "default": "transform"},
                "on_conflict": {"type": "string", "enum": ["error", "auto_increment"], "default": "auto_increment"},
                "keep_namespace": {"type": "boolean", "default": True},
            },
            "required": ["pattern"],
        },
        "handler": tool_rename_batch,
    },
    {
        "name": "maya.set_key",
        "description": "对选中 transform 打关键帧。attributes 支持组名 translate/rotate/scale/transform 与短名 tx/ry 等；不填则 keyable。",
        "input_schema": {"type": "object", "properties": {"time": {"type": "number"}, "attributes": {"type": "array", "items": {"type": "string"}}}, "required": []},
        "handler": tool_set_key,
    },
    {
        "name": "maya.delete_keys_range",
        "description": "删除选中 transform 在时间范围内的关键帧。不填 attributes 默认仅 TRS（transform）。",
        "input_schema": {
            "type": "object",
            "properties": {"start": {"type": "number"}, "end": {"type": "number"}, "attributes": {"type": "array", "items": {"type": "string"}}},
            "required": ["start", "end"],
        },
        "handler": tool_delete_keys_range,
    },
    {
        "name": "maya.shift_keys",
        "description": "将选中对象在时间范围内的关键帧整体平移 offset 帧。",
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "number"},
                "end": {"type": "number"},
                "offset": {"type": "number"},
                "attributes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["start", "end", "offset"],
        },
        "handler": tool_shift_keys,
    },
    {
        "name": "maya.euler_filter",
        "description": "对选中 transform 的旋转曲线做 Euler Filter。默认 playback range。",
        "input_schema": {
            "type": "object",
            "properties": {"time_range": {"type": "object", "properties": {"start": {"type": "number"}, "end": {"type": "number"}}, "required": ["start", "end"]}},
            "required": [],
        },
        "handler": tool_euler_filter,
    },
    {
        "name": "maya.create_camera",
        "description": "创建摄像机，可设置焦距与裁剪面。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "focal_length": {"type": "number"},
                "near_clip": {"type": "number"},
                "far_clip": {"type": "number"},
            },
            "required": [],
        },
        "handler": tool_create_camera,
    },
    {
        "name": "maya.create_cube",
        "description": "创建一个多边形立方体，可指定尺寸与细分。",
        "input_schema": {
            "type": "object",
            "properties": {
                "size": {"type": "number", "default": 1.0},
                "width": {"type": "number"},
                "height": {"type": "number"},
                "depth": {"type": "number"},
                "subdiv_x": {"type": "integer", "minimum": 1, "default": 1},
                "subdiv_y": {"type": "integer", "minimum": 1, "default": 1},
                "subdiv_z": {"type": "integer", "minimum": 1, "default": 1},
                "name": {"type": "string"},
            },
            "required": [],
        },
        "handler": tool_create_cube,
    },
    {
        "name": "maya.create_polygon_cube",
        "description": "创建一个多边形立方体（别名）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "size": {"type": "number", "default": 1.0},
                "width": {"type": "number"},
                "height": {"type": "number"},
                "depth": {"type": "number"},
                "subdiv_x": {"type": "integer", "minimum": 1, "default": 1},
                "subdiv_y": {"type": "integer", "minimum": 1, "default": 1},
                "subdiv_z": {"type": "integer", "minimum": 1, "default": 1},
                "name": {"type": "string"},
            },
            "required": [],
        },
        "handler": tool_create_cube,
    },
    {
        "name": "maya.create_sphere",
        "description": "创建一个多边形球体，可指定半径与细分。",
        "input_schema": {
            "type": "object",
            "properties": {
                "radius": {"type": "number", "default": 1.0},
                "subdiv_axis": {"type": "integer", "minimum": 3, "default": 20},
                "subdiv_height": {"type": "integer", "minimum": 2, "default": 20},
                "name": {"type": "string"},
            },
            "required": [],
        },
        "handler": tool_create_sphere,
    },
    {
        "name": "maya.create_cylinder",
        "description": "创建一个多边形圆柱体，可指定半径、高度与细分。",
        "input_schema": {
            "type": "object",
            "properties": {
                "radius": {"type": "number", "default": 1.0},
                "height": {"type": "number", "default": 2.0},
                "subdiv_axis": {"type": "integer", "minimum": 3, "default": 20},
                "subdiv_height": {"type": "integer", "minimum": 1, "default": 1},
                "subdiv_caps": {"type": "integer", "minimum": 1, "default": 1},
                "name": {"type": "string"},
            },
            "required": [],
        },
        "handler": tool_create_cylinder,
    },
    {
        "name": "maya.create_plane",
        "description": "创建一个多边形平面，可指定宽高与细分。",
        "input_schema": {
            "type": "object",
            "properties": {
                "width": {"type": "number", "default": 10.0},
                "height": {"type": "number", "default": 10.0},
                "subdiv_x": {"type": "integer", "minimum": 1, "default": 1},
                "subdiv_y": {"type": "integer", "minimum": 1, "default": 1},
                "name": {"type": "string"},
            },
            "required": [],
        },
        "handler": tool_create_plane,
    },
    {
        "name": "maya.set_translate",
        "description": "设置选中 transform 的平移。支持 absolute/relative，可选在该帧打关键帧。",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
                "mode": {"type": "string", "enum": ["absolute", "relative"], "default": "absolute"},
                "time": {"type": "number"},
                "set_key": {"type": "boolean", "default": False}
            },
            "required": []
        },
        "handler": tool_set_translate,
    },
    {
        "name": "maya.create_and_animate_translate_x",
        "description": "一次性完成：创建立方体并在 start_time/end_time 上为 translate/tx 打关键帧（end_x 或 delta_x）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "size": {"type": "number", "default": 1.0},
                "width": {"type": "number"},
                "height": {"type": "number"},
                "depth": {"type": "number"},
                "start_time": {"type": "number"},
                "end_time": {"type": "number"},
                "duration": {"type": "number"},
                "end_x": {"type": "number"},
                "delta_x": {"type": "number"}
            },
            "required": ["start_time"]
        },
        "handler": tool_create_and_animate_translate_x,
    },
    {
        "name": "maya.create_loop_rotate",
        "description": "为一个物体在时间范围内创建绕某轴的旋转动画（常用于“绕 Y 轴转一圈/转几圈”）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "axis": {"type": "string", "enum": ["x", "y", "z"], "default": "y"},
                "start_time": {"type": "number"},
                "end_time": {"type": "number"},
                "duration": {"type": "number"},
                "rotations": {"type": "number", "default": 1.0}
            },
            "required": []
        },
        "handler": tool_create_loop_rotate,
    },
    {
        "name": "maya.create_ping_pong_translate",
        "description": "为一个物体在时间范围内创建沿某轴的往返平移动画（左右来回/上下往返）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "axis": {"type": "string", "enum": ["x", "y", "z"], "default": "x"},
                "start_time": {"type": "number"},
                "end_time": {"type": "number"},
                "duration": {"type": "number"},
                "distance": {"type": "number", "default": 10.0},
                "cycles": {"type": "integer", "minimum": 1, "default": 2}
            },
            "required": []
        },
        "handler": tool_create_ping_pong_translate,
    },
    {
        "name": "maya.retime_keys",
        "description": "将目标在 from_time 的关键帧移动到 to_time。未指定 target 时需要唯一选择。",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "from_time": {"type": "number"},
                "to_time": {"type": "number"},
                "attributes": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["from_time", "to_time"]
        },
        "handler": tool_retime_keys,
    },
    {
        "name": "maya.retime_range",
        "description": "将目标在 [from_start, from_end] 范围内的关键帧压缩/移动到 [to_start, to_end]。",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "from_start": {"type": "number"},
                "from_end": {"type": "number"},
                "to_start": {"type": "number"},
                "to_end": {"type": "number"},
                "attributes": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["from_start", "from_end", "to_start", "to_end"]
        },
        "handler": tool_retime_range,
    },
    {
        "name": "maya.create_bouncing_ball",
        "description": "创建一个球体并在给定时间范围内生成衰减高度的小球弹跳动画（ty）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "radius": {"type": "number", "default": 1.0},
                "start_time": {"type": "number"},
                "end_time": {"type": "number"},
                "duration": {"type": "number"},
                "bounces": {"type": "integer", "minimum": 1, "default": 3},
                "height": {"type": "number", "default": 10.0},
                "decay": {"type": "number", "default": 0.6},
                "ground_y": {"type": "number", "default": 0.0}
            },
            "required": []
        },
        "handler": tool_create_bouncing_ball,
    },
    {
        "name": "maya.copy_animation",
        "description": "复制源对象在时间范围内的关键帧到目标对象，可选时间偏移与属性。",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "targets": {"type": "array", "items": {"type": "string"}},
                "start": {"type": "number"},
                "end": {"type": "number"},
                "time_offset": {"type": "number", "default": 0},
                "attributes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["source", "targets"],
        },
        "handler": tool_copy_animation,
    },
    {
        "name": "maya.list_animated_nodes",
        "description": "列出在时间范围内有关键帧的节点，可按类型与属性过滤。",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_start": {"type": "number"},
                "time_end": {"type": "number"},
                "type_filter": {
                    "type": "string",
                    "enum": ["transform", "camera", "joint", "all"],
                    "default": "all",
                },
                "attributes": {"type": "array", "items": {"type": "string"}},
            },
            "required": [],
        },
        "handler": tool_list_animated_nodes,
    },
    {
        "name": "maya.import_bomb_asset",
        "description": "导入或创建 Bomb 爆炸资产（特效）、炸弹。如果用户让你“创建爆炸”、“生成爆炸”或制作爆炸效果，请务必直接调用此工具，不要自己写代码。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "namespace": {"type": "string", "default": "Bomb"}
            },
            "required": []
        },
        "handler": tool_import_bomb_asset,
    },
    {
        "name": "maya.create_three_point_lighting",
        "description": "创建标准的三点布光（主光、辅光、背光/轮廓光）系统。当用户请求打光、布光、照亮场景时调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "被照亮的目标物体名称（可选，会自动根据选中物体包围盒智能计算）"},
                "intensity": {"type": "number", "default": 1.0, "description": "光照强度倍增基数（可选）"}
            },
            "required": []
        },
        "handler": tool_create_three_point_lighting,
    },
    {
        "name": "maya.create_turntable",
        "description": "创建一个自动环绕目标旋转 360 度的展示摄像机（Turntable Camera）。当用户请求创建展示平台、环绕镜头、围绕物体旋转的摄像机时调用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "要环绕的目标物体名称（若空则绕选中物体，自动计算bbox半径）"},
                "frames": {"type": "number", "default": 120, "description": "旋转一圈所需的动画帧数"},
                "distance": {"type": "number", "description": "摄像机距离目标的距离半径（非必要不要填，系统会根据物体智能计算）"}
            },
            "required": []
        },
        "handler": tool_create_turntable,
    },
    {
        "name": "maya.create_object_and_camera_and_aim",
        "description": "高层级复合工具：创建一个指定类型的物体（小球、立方体、圆柱体等）和一个摄像机，并将摄像机放置在物体外合适的距离，然后使用 aimConstraint 让摄像机看向它。适用于所有『创建某物体和摄像机并看向它』这类需求。",
        "input_schema": {
            "type": "object",
            "properties": {
                "object_type": {
                    "type": "string", 
                    "enum": ["sphere", "cube", "cylinder"],
                    "default": "sphere"
                },
                "object_name": {"type": "string"},
                "camera_name": {"type": "string"},
                "distance_multiplier": {"type": "number", "default": 4.0},
                "min_distance": {"type": "number", "default": 10.0},
                "place_direction": {
                    "type": "string",
                    "enum": ["+z", "-z", "+x", "-x", "+y", "-y"],
                    "default": "-z"
                },
                "create_persistent_constraint": {"type": "boolean", "default": True},
                "camera_forward_axis": {
                    "type": "string",
                    "enum": ["+z", "-z"],
                    "default": "-z",
                    "description": "项目约定：摄像机朝向轴，默认-z看向目标"
                }
            },
            "required": []
        },
        "handler": tool_create_object_and_camera_and_aim,
    },
    {
        "name": "maya.add_camera_jitter",
        "description": "为摄像机添加抖动动画。必须指定抖动类型，如果没有指定，会提示用户选择。",
        "input_schema": {
            "type": "object",
            "properties": {
                "camera": {"type": "string"},
                "jitter_type": {
                    "type": "string",
                    "description": "如果不确定，请不要传此参数，系统会弹窗询问用户。",
                    "enum": ["Handheld", "Vibration", "Earthquake"]
                }
            },
            "required": ["camera"]
        },
        # We use a lambda here because `tool_add_camera_jitter` is defined AFTER this list in the file.
        # Python resolves lambdas at runtime, preventing a NameError during module initialization.
        "handler": lambda args: tool_add_camera_jitter(args)
    },
    {
        "name": "maya.ask_user_confirmation",
        "description": "如果用户的请求含糊不清需要澄清，或者非常危险（如删除场景、写入文件），使用此工具弹出 UI 卡片选项框。参数 options 为一个字符串数组，展示给用户按键。",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "即将执行的动作标题，比如'删除场景'"},
                "target": {"type": "string", "description": "影响的目标，比如'整个工程'"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "提供给用户的选项按钮文字数组，如 ['确定', '取消']"
                }
            },
            "required": ["action", "target", "options"]
        },
        "handler": tool_ask_user_confirmation,
    },
    {
        "name": "maya.cleanup_scene",
        "description": "一键清理选中物体的构建历史（烘焙变形器之外的构造记录）、冻结坐标变换（清零SRT偏移）、并居中枢轴。如果没有选中，它会弹窗询问是否清理全部。当你收到优化场景、清理历史、清零坐标、居中坐标轴的指令时调用。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "handler": tool_cleanup_scene,
    },
    {
        "name": "maya.group_and_center",
        "description": "打组并居中枢轴。将用户当前选中的零散物体自动打组成一个新的层级组，并且组节点的中心点会自动对齐到这堆物体的绝对中心。",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_name": {"type": "string", "default": "Auto_Group", "description": "新打的组的名称"}
            },
            "required": []
        },
        "handler": tool_group_and_center,
    },
    {
        "name": "maya.randomize_transforms",
        "description": "散布 / 随机变换。对选中的一组物体随机偏移位置、旋转、缩放。用来快速制作满地散落物体、凌乱的石头/树木极其好用。",
        "input_schema": {
            "type": "object",
            "properties": {
                "translate_min": {"type": "array", "items": {"type": "number"}, "description": "[xmin, ymin, zmin] 可选"},
                "translate_max": {"type": "array", "items": {"type": "number"}, "description": "[xmax, ymax, zmax] 可选"},
                "rotate_min": {"type": "array", "items": {"type": "number"}, "description": "[xmin, ymin, zmin] 旋转下限"},
                "rotate_max": {"type": "array", "items": {"type": "number"}, "description": "[xmax, ymax, zmax] 旋转上限"},
                "scale_min": {"type": "array", "items": {"type": "number"}, "description": "[sx, sy, sz] 或直接通过 uniform_scale 控制单轴统一缩放"},
                "scale_max": {"type": "array", "items": {"type": "number"}, "description": "[sx, sy, sz]"},
                "uniform_scale": {"type": "boolean", "default": True, "description": "是否等比缩放"}
            },
            "required": []
        },
        "handler": tool_randomize_transforms,
    },
    {
        "name": "maya.assign_color_materials",
        "description": "快速纯色涂装工具。能够瞬间为选定的一个或一批模型分别创建带颜色的 Lambert 材质并赋予它们。解决手补材质繁琐且容易报错的痛点。",
        "input_schema": {
            "type": "object",
            "properties": {
                "color": {"type": "array", "items": {"type": "number"}, "description": "RGB 数组 [R, G, B] 每项目0-1。如果为空则随机"},
                "random_colors": {"type": "boolean", "default": False, "description": "如果为 true，则给选中的每个物体赋予完全独立随机色彩的材质"}
            },
            "required": []
        },
        "handler": tool_assign_color_materials,
    },
    {
        "name": "maya.execute_python_code",
        "description": "在 Maya 环境中动态执行任何符合规范的 Python 代码。这是你最强大的工具！当预设工具无法满足需求时（例如：“建10个小球”、“把选中物体全部重命名”等批量/循环操作），你必须自己编写合乎逻辑的 Maya Python 代码并使用这个工具一次性执行。切忌为了批量操作而人工循环调用其他单步工具！",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "包含合法 Maya Python 命令的多行脚本代码。例如：\nimport maya.cmds as cmds\nfor i in range(10):\n    cmds.polyCube(n='myCube_%d' % i)\n"
                }
            },
            "required": ["code"]
        },
        "handler": lambda args: tool_execute_python_code(args)
    },
    # ── v2.1 新增工具 ────────────────────────────────────────────────
    {
        "name": "maya.camera_look_at",
        "description": (
            "对摄像机创建 aimConstraint，使其持续朝向目标物体（跟随动画全程，not 静帧）。"
            "若只需一次性对齐而不跟随，请使用 maya.aim_at_target 并传入 delete_constraint_after=true。"
            "camera 和 target 都必须已在场景中存在。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "camera": {"type": "string", "description": "摄像机 transform 名称，例如 'track_cam'"},
                "target": {"type": "string", "description": "持续跟随的目标物体名称，例如 'rolling_ball'"}
            },
            "required": ["camera", "target"]
        },
        "handler": lambda args: _tool_camera_look_at(args),
    },
    {
        "name": "maya.camera_frame_selection",
        "description": "让摄像机框选/聚焦当前选中物体（等同于按 F 键）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "camera": {"type": "string", "description": "摄像机名称（可选），留空则使用当前活动摄像机"}
            },
            "required": []
        },
        "handler": lambda args: _tool_camera_frame_selection(args),
    },
    {
        "name": "maya.duplicate_objects",
        "description": "复制当前选中的物体，返回新创建节点列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "smart_transform": {"type": "boolean", "default": False, "description": "是否使用 smartTransform 复制方式"}
            },
            "required": []
        },
        "handler": lambda args: _tool_duplicate_objects(args),
    },
    {
        "name": "maya.delete_selected",
        "description": "删除当前场景中选中的对象。破坏性操作，调用前应经过 ask_user_confirmation 确认。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "handler": lambda args: _tool_delete_selected(args),
    },
    {
        "name": "maya.freeze_transforms",
        "description": "对当前选中对象冻结变换（位移/旋转/缩放归零）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "translate": {"type": "boolean", "default": True},
                "rotate":    {"type": "boolean", "default": True},
                "scale":     {"type": "boolean", "default": True}
            },
            "required": []
        },
        "handler": lambda args: _tool_freeze_transforms(args),
    },
    {
        "name": "maya.center_pivot",
        "description": "将当前选中对象的轴心居中（等同于 Modify > Center Pivot）。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        },
        "handler": lambda args: _tool_center_pivot(args),
    },
    {
        "name": "maya.parent_objects",
        "description": "建立父子层级：将 child 物体设为 parent 物体的子节点。",
        "input_schema": {
            "type": "object",
            "properties": {
                "child":  {"type": "string", "description": "子节点名称"},
                "parent": {"type": "string", "description": "父节点名称"}
            },
            "required": ["child", "parent"]
        },
        "handler": lambda args: _tool_parent_objects(args),
    },
]

# ── v2.1 新工具实现 ────────────────────────────────────────────────────────

def _tool_camera_look_at(args):
    """
    为摄像机创建持久 aimConstraint，使其全程跟随 target（不删除约束）。
    若需一次性对齐，请改用 tool_aim_at_target(delete_constraint_after=True)。
    """
    camera = args.get("camera", "")
    target = args.get("target", "")
    if not camera or not cmds.objExists(camera):
        raise ToolError("PARAM", u"摄像机不存在: %s" % camera)
    if not target or not cmds.objExists(target):
        raise ToolError("PARAM", u"目标对象不存在: %s" % target)
    # 清除已有 aimConstraint（用 listConnections 更可靠，因为 constraint 可能不是直接子节点）
    existing = cmds.listConnections(camera, type="aimConstraint") or []
    if existing:
        existing = list(set(existing))  # 去重
        try:
            cmds.delete(existing)
        except Exception:
            pass
    # 建立持续 aimConstraint
    # Maya 摄像机默认沿本地 -Z 轴朝前 → aimVector=(0,0,-1)
    # worldUpType="vector" + worldUpVector=(0,1,0) 防止镜头翻转
    con = cmds.aimConstraint(
        target, camera,
        aimVector=(0, 0, -1),
        upVector=(0, 1, 0),
        worldUpType="vector",
        worldUpVector=(0, 1, 0),
        maintainOffset=False,
        weight=1.0,
    )
    constraint_name = con[0] if isinstance(con, (list, tuple)) and con else con
    return {
        "camera":      camera,
        "target":      target,
        "constraint":  constraint_name,
        "persistent":  True,
        "aim_vector":  [0, 0, -1],
        "up_vector":   [0, 1, 0],
        "message":     u"摄像机 %s 已持续跟随 %s（aimConstraint: %s, aimVec=-Z）" % (camera, target, constraint_name),
    }

def _tool_camera_frame_selection(args):
    camera = args.get("camera") or ""
    sel = cmds.ls(sl=True) or []
    if not sel:
        raise ToolError("MAYA_NO_SELECTION", u"没有选中任何物体，请先选择要框选的对象")
    if camera and cmds.objExists(camera):
        cmds.viewFit(camera, fitFactor=1.0)
    else:
        cmds.viewFit(fitFactor=1.0)
    return {"camera": camera or "active",
            "selected": sel,
            "message": u"已框选 %d 个物体到摄像机视图" % len(sel)}

def _tool_duplicate_objects(args):
    smart = args.get("smart_transform", False)
    sel = cmds.ls(sl=True) or []
    if not sel:
        raise ToolError("MAYA_NO_SELECTION", u"请先选择要复制的物体")
    new_nodes = cmds.duplicate(smartTransform=smart) or []
    return {"created": new_nodes,
            "message": u"已复制 %d 个物体" % len(new_nodes)}

def _tool_delete_selected(args):
    sel = cmds.ls(sl=True, fl=True) or []
    if not sel:
        raise ToolError("MAYA_NO_SELECTION", u"没有选中任何对象")
    cmds.delete(sel)
    return {"deleted": sel,
            "message": u"已删除 %d 个对象" % len(sel)}

def _tool_freeze_transforms(args):
    t = args.get("translate", True)
    r = args.get("rotate", True)
    s = args.get("scale", True)
    sel = cmds.ls(sl=True) or []
    if not sel:
        raise ToolError("MAYA_NO_SELECTION", u"没有选中任何对象")
    cmds.makeIdentity(apply=True, t=int(t), r=int(r), s=int(s), n=0)
    return {"objects": sel,
            "message": u"已冻结变换（t=%s, r=%s, s=%s）" % (t, r, s)}

def _tool_center_pivot(args):
    sel = cmds.ls(sl=True) or []
    if not sel:
        raise ToolError("MAYA_NO_SELECTION", u"没有选中任何对象")
    cmds.xform(centerPivots=True)
    return {"objects": sel,
            "message": u"已将 %d 个对象的轴心居中" % len(sel)}

def _tool_parent_objects(args):
    child  = args.get("child", "")
    parent = args.get("parent", "")
    if not child or not cmds.objExists(child):
        raise ToolError("PARAM", u"子节点不存在: %s" % child)
    if not parent or not cmds.objExists(parent):
        raise ToolError("PARAM", u"父节点不存在: %s" % parent)
    cmds.parent(child, parent)
    return {"child": child, "parent": parent,
            "message": u"已将 %s 设为 %s 的子节点" % (child, parent)}

def tool_add_camera_jitter(args):
    camera = args.get("camera")
    jitter_type = args.get("jitter_type")
    
    if not camera or not cmds.objExists(camera):
        raise ToolError("PARAM", "目标摄像机不存在: %s" % camera)
        
    if not jitter_type:
        # LLM didn't pick a type, throw confirmation error
        raise ConfirmationError(
            action="为摄像机添加抖动动画",
            target=camera,
            options=["Handheld", "Vibration", "Earthquake"]
        )
        
    # Mock implementation of actually adding jitter
    return {"status": "success", "message": u"已应用 %s 抖动到 %s" % (jitter_type, camera)}


def tool_ask_user_confirmation(args):
    action = args.get("action", "未知破坏性操作")
    target = args.get("target", "整个场景")
    options = args.get("options", ["确认执行", "取消并另存为"])
    
    raise ConfirmationError(action=action, target=target, options=options)


def tool_execute_python_code(args):
    code = args.get("code")
    if not code:
        raise ToolError("PARAM", "未提供要执行的 Python 代码 (code 参数为空)")

    # v2.2 安全过滤：禁止导入危险模块
    _BLOCKED = ["import os", "import sys", "import subprocess",
                "__import__", "os.system", "os.popen", "subprocess."]
    for blocked in _BLOCKED:
        if blocked in code:
            raise ToolError("SECURITY",
                u"禁止导入系统模块或调用系统命令：请勿使用 '%s'。"
                u"需要媒体操作请直接使用 cmds。" % blocked)

    # Create a clean namespace but inject maya.cmds and maya.mel
    import maya.cmds as cmds
    import maya.mel as mel
    import sys
    import cStringIO
    import traceback
    
    # Simple output capture
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    capture_out = cStringIO.StringIO()
    capture_err = cStringIO.StringIO()
    sys.stdout = capture_out
    sys.stderr = capture_err
    
    namespace = {"cmds": cmds, "mel": mel}
    
    success = False
    try:
        # Avoid indentation issues if the LLM provided unindented blocks
        import textwrap
        code = textwrap.dedent(code).strip()
        exec(code, namespace)
        success = True
    except Exception as e:
        traceback.print_exc()
        success = False
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        
    stdout_val = capture_out.getvalue()
    stderr_val = capture_err.getvalue()
    
    if not success:
        err_msg = u"Python 脚本执行报错:\n%s\n标准输出:\n%s" % (
            stderr_val.decode('utf-8', 'ignore') if isinstance(stderr_val, str) else stderr_val,
            stdout_val.decode('utf-8', 'ignore') if isinstance(stdout_val, str) else stdout_val
        )
        raise ToolError("EXECUTION_FAILED", err_msg)
        
    s_out = stdout_val.decode('utf-8', 'ignore') if isinstance(stdout_val, str) else stdout_val
    return {
        "success": True, 
        "stdout": s_out,
        "message": u"代码已成功在 Maya 内部执行。",
        "summary": u"执行了一段自定义 Python 脚本。\n%s" % (u"输出: " + s_out.strip() if s_out.strip() else u"（无打印输出）")
    }


def tools_schema():
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in TOOLS]


def call_tool(name, arguments):
    for t in TOOLS:
        if t["name"] == name:
            try:
                return {"ok": True, "result": t["handler"](arguments)}
            except ToolError as e:
                return {"ok": False, "error": {"code": e.code, "message": e.message}}
            except Exception as e:
                return {"ok": False, "error": {"code": "MAYA_COMMAND_FAILED", "message": str(e)}}
    return {"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": "未知工具：%s" % name}}

