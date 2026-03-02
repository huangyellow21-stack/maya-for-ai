# -*- coding: utf-8 -*-
from __future__ import absolute_import

import glob
import os
import re

import maya.cmds as cmds
import maya.mel as mel

from .attributes import expand_attributes


class ToolError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


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
        return {"transform": actual, "width": w, "height": h, "depth": d, "subdiv": {"x": sx, "y": sy, "z": sz}}
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
        return {"transform": actual, "radius": radius, "subdiv": {"axis": sx, "height": sy}}
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
        return {
            "transform": actual,
            "radius": radius,
            "height": height,
            "subdiv": {"axis": sa, "height": sh, "caps": sc},
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
        return {"transform": actual, "width": w, "height": h, "subdiv": {"x": sx, "y": sy}}
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "polyPlane 失败：%s" % str(e))

def tool_create_camera(args):
    name = args.get("name")
    focal_length = args.get("focal_length", None)
    near_clip = args.get("near_clip", None)
    far_clip = args.get("far_clip", None)
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
        return {"transform": actual, "shape": shape, "focal_length": focal_length, "near_clip": near_clip, "far_clip": far_clip}
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
    一次性完成：创建立方体 -> 在 start_time 打 translate 关键帧 ->
              在 end_time 将 X 设为 end_x（或按 delta_x 相对移动）并打关键帧。
    不依赖当前选择，直接对新建 transform 操作。
    """
    size = float(args.get("size", 1.0))
    w = float(args.get("width", size))
    h = float(args.get("height", size))
    d = float(args.get("depth", size))
    name = args.get("name")
    start_time = float(args.get("start_time", 1))
    end_time_arg = args.get("end_time")
    duration = args.get("duration")
    if end_time_arg is not None:
        end_time = float(end_time_arg)
    elif duration is not None:
        end_time = float(start_time + float(duration))
    else:
        end_time = float(start_time + 47.0)
    has_end_x = "end_x" in args
    has_delta_x = "delta_x" in args
    if not has_end_x and not has_delta_x:
        try:
            dx = float(args.get("delta_x", 10.0))
        except Exception:
            dx = 10.0
        args["delta_x"] = dx
        has_delta_x = True
    if end_time < start_time:
        raise ToolError("ARG_VALIDATION_FAILED", "end_time 必须 >= start_time")

    try:
        actual = None
        if isinstance(name, basestring) and name.strip() and cmds.objExists(name.strip()):
            # 目标已存在：复用该对象，而不是再创建一个（更“幂等”）
            actual = name.strip()
            # 调整几何尺寸：优先修改 polyCube 历史；若无历史则使用缩放近似
            try:
                shapes = cmds.listRelatives(actual, shapes=True) or []
                hist = []
                for s in shapes:
                    hist += cmds.listHistory(s) or []
                pc = None
                for n in hist:
                    if cmds.nodeType(n) == "polyCube":
                        pc = n
                        break
                if pc:
                    try:
                        cmds.setAttr(pc + ".w", w)
                        cmds.setAttr(pc + ".h", h)
                        cmds.setAttr(pc + ".d", d)
                    except Exception:
                        pass
                else:
                    # 近似处理：对 transform 直接缩放
                    cmds.setAttr(actual + ".sx", w)
                    cmds.setAttr(actual + ".sy", h)
                    cmds.setAttr(actual + ".sz", d)
            except Exception:
                pass
        else:
            # 正常创建新立方体
            res = cmds.polyCube(w=w, h=h, d=d, sx=1, sy=1, sz=1)
            xform = res[0] if isinstance(res, (list, tuple)) and res else res
            actual = xform
            if isinstance(name, basestring) and name.strip():
                target = name.strip()
                if not cmds.objExists(target):
                    actual = cmds.rename(xform, target)
                else:
                    actual = cmds.rename(xform, _safe_unique_name(target))

        # start key
        cmds.currentTime(start_time, edit=True)
        cmds.setKeyframe([actual + ".tx", actual + ".ty", actual + ".tz"])

        # end key
        cmds.currentTime(end_time, edit=True)
        cur_tx = cmds.getAttr(actual + ".tx")
        if has_end_x:
            target_x = float(args.get("end_x"))
        else:
            target_x = float(cur_tx + float(args.get("delta_x")))
        cmds.setAttr(actual + ".tx", target_x)
        cmds.setKeyframe(actual + ".tx")
    except Exception as e:
        raise ToolError("MAYA_COMMAND_FAILED", "create and animate 失败：%s" % str(e))

    return {
        "transform": actual,
        "size": {"w": w, "h": h, "d": d},
        "start_time": start_time,
        "end_time": end_time,
        "end_x": target_x,
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


TOOLS = [
    {
        "name": "maya.list_tools",
        "description": "返回可用工具的清单（名称与描述）。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "handler": lambda _args: [{"name": t["name"], "description": t["description"]} for t in TOOLS[1:]],
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
        "name": "maya.select_by_name_pattern",
        "description": "按名称通配符选择对象，可按类型过滤。",
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
        "description": "导入 Bomb 爆炸资产（自动识别路径，可传入自定义 path）。",
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
]


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

