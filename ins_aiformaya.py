# -*- coding: utf-8 -*-
"""
拖入 Maya 窗口即可安装 AIFORMAYA 模块。

逻辑：
1. 以本脚本所在目录为根，查找 ./maya_module/AIFORMAYA 作为源目录
2. 复制到 Maya userAppDir 下的 modules/AIFORMAYA
3. 如目标已存在，询问是否覆盖
"""

import os
import sys
import shutil
import traceback

import maya.cmds as cmds


def _norm(p):
    return os.path.normpath(p).replace("\\", "/")


def _ensure_dir(p):
    if not os.path.exists(p):
        os.makedirs(p)

def _to_unicode(value):
    if isinstance(value, unicode):
        return value
    enc = sys.getfilesystemencoding() or "mbcs"
    try:
        return value.decode(enc)
    except Exception:
        return unicode(value, errors="ignore")


def _source_module_dir():
    """
    源模块目录：通常为 <当前脚本所在目录>/maya_module/AIFORMAYA
    """
    here = os.path.dirname(__file__)
    src = os.path.join(here, "maya_module", "AIFORMAYA")
    return src

def _source_icon_dir():
    here = os.path.dirname(__file__)
    return os.path.join(here, "icon")


def _target_module_dirs():
    """
    目标模块目录：
    1. <userAppDir>/modules/AIFORMAYA        （全局）
    2. <userAppDir>/<version>/modules/AIFORMAYA （针对当前 Maya 版本，例如 2020）
    """
    ua = cmds.internalVar(userAppDir=True)
    version = str(cmds.about(version=True) or "").strip() or "2020"

    roots = []
    roots.append(os.path.join(ua, "modules"))
    roots.append(os.path.join(ua, version, "modules"))

    # 去重
    seen = set()
    out = []
    for r in roots:
        nr = os.path.normpath(r)
        if nr in seen:
            continue
        seen.add(nr)
        dst = os.path.join(nr, "AIFORMAYA")
        out.append((nr, dst))
    return out


def _copytree(src, dst):
    """
    Python2 自带 shutil.copytree，简单包一层，带目录创建。
    """
    _ensure_dir(os.path.dirname(dst))
    shutil.copytree(src, dst)

def _copytree_merge(src, dst):
    if not os.path.isdir(src):
        raise RuntimeError(u"图标源目录不存在：%s" % _norm(src))
    src_u = _to_unicode(src)
    dst_u = _to_unicode(dst)
    _ensure_dir(dst_u)
    for base, dirs, files in os.walk(src_u):
        base_u = _to_unicode(base)
        rel = os.path.relpath(base_u, src_u)
        target_dir = dst_u if rel == "." else os.path.join(dst_u, rel)
        _ensure_dir(target_dir)
        for f in files:
            fu = _to_unicode(f)
            s = os.path.join(base_u, fu)
            t = os.path.join(target_dir, fu)
            shutil.copy2(s, t)

def _clean_pyc(root):
    for base, dirs, files in os.walk(root):
        for d in list(dirs):
            if d == "__pycache__":
                p = os.path.join(base, d)
                try:
                    shutil.rmtree(p)
                except Exception:
                    pass
        for f in files:
            if f.endswith(".pyc"):
                p = os.path.join(base, f)
                try:
                    os.remove(p)
                except Exception:
                    pass


def onMayaDroppedPythonFile(*_args):
    try:
        src = _source_module_dir()
        if not os.path.isdir(src):
            raise RuntimeError(u"未找到源模块目录：%s\n请确保本脚本旁边有 maya_module/AIFORMAYA 目录。" % _norm(src))

        targets = _target_module_dirs()

        # 为简化体验：自动覆盖同名 AIFORMAYA（不再多次弹对话框）
        installed_dirs = []
        mod_paths = []
        icon_src = _source_icon_dir()
        if not os.path.isdir(icon_src):
            raise RuntimeError(u"未找到图标目录：%s" % _norm(icon_src))
        for modules_root, dst in targets:
            _ensure_dir(modules_root)

            if os.path.exists(dst):
                try:
                    shutil.rmtree(dst)
                except Exception as e:
                    raise RuntimeError(u"删除旧目录失败：%s" % e)

            _copytree(src, dst)
            icon_dst = os.path.join(dst, "icon")
            icon_ui_dst = os.path.join(dst, "scripts", "aiformaya", "ui", "icon")
            _copytree_merge(icon_src, icon_dst)
            _copytree_merge(icon_src, icon_ui_dst)
            _clean_pyc(dst)
            installed_dirs.append(dst)

            # 在各自 modules 根目录写入/覆盖 AIFORMAYA.mod，使用相对路径 ./AIFORMAYA
            mod_path = os.path.join(modules_root, "AIFORMAYA.mod")
            try:
                with open(mod_path, "w") as f:
                    f.write("+ AIFORMAYA 0.1 ./AIFORMAYA\n")
                mod_paths.append(mod_path)
            except Exception as e:
                raise RuntimeError(u"写入 AIFORMAYA.mod 失败：%s" % e)

        cmds.confirmDialog(
            title=u"成功",
            message=(
                u"AIFORMAYA 安装完成！\n已复制到：\n%s\n并创建模块文件：\n%s\n\n"
                u"重启 Maya 后可在 Script Editor 中执行：\nimport aiformaya; aiformaya.show()"
            )
            % ("\n".join([_norm(d) for d in installed_dirs]), "\n".join([_norm(m) for m in mod_paths])),
            button=[u"确定"],
            defaultButton=u"确定",
        )

    except Exception as e:
        traceback.print_exc()
        cmds.error(u"AIFORMAYA 安装失败：%s" % e)


if __name__ == "__main__":
    onMayaDroppedPythonFile()

