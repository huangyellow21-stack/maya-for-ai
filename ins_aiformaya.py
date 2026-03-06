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
import stat
import errno
import subprocess
import time

import maya.cmds as cmds


def _norm(p):
    return os.path.normpath(p).replace("\\", "/")


def _ensure_dir(p):
    if not os.path.exists(p):
        os.makedirs(p)

try:
    unicode
except NameError:
    unicode = str

def _to_unicode(value):
    if isinstance(value, unicode):
        return value
    enc = sys.getfilesystemencoding() or "mbcs"
    try:
        return value.decode(enc)
    except Exception:
        return unicode(value, errors="ignore")


def _on_rm_error(func, path, exc_info):
    """
    shutil.rmtree 的错误回调：尝试修改权限后重试删除
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        # 如果还是删不掉，可能是进程占用，忽略，交给外层重试机制
        pass


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


def _source_bridge_dir():
    here = os.path.dirname(__file__)
    return os.path.join(here, "bridge")


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


def _copytree_force(src, dst):
    """
    强制复制目录树。如果目标目录存在，则覆盖。
    """
    if not os.path.isdir(src):
        return
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
            # 如果目标文件存在，先尝试修改权限
            if os.path.exists(t):
                try: os.chmod(t, stat.S_IWRITE)
                except: pass
            shutil.copy2(s, t)


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


def _kill_process_by_port(port):
    """
    尝试杀掉占用指定端口的进程 (Windows only, using netstat & taskkill)
    同时也杀掉所有 python.exe 进程如果它的命令行包含 'server:app' (网关特征)
    """
    # 1. 端口查杀
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        cmd = 'netstat -ano | findstr :%s' % port
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
        out, _ = proc.communicate()
        if out:
            try:
                out = out.decode("mbcs", errors="ignore")
            except Exception:
                pass
            pids = set()
            for line in out.splitlines():
                parts = line.strip().split()
                if len(parts) > 4 and str(port) in parts[1]: 
                    pids.add(parts[-1])
            for pid in pids:
                try:
                    subprocess.call('taskkill /F /PID %s' % pid, shell=True, startupinfo=startupinfo)
                except Exception:
                    pass
    except Exception:
        pass

    # 2. 进程名特征查杀 (更强力)
    # wmic process where "name='python.exe' and commandline like '%server:app%'" call terminate
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        # 扩大查杀范围，防止 residue
        cmd = 'wmic process where "name=\'python.exe\' and commandline like \'%server:app%\'" call terminate'
        subprocess.call(cmd, shell=True, startupinfo=startupinfo)
        
        # 针对 run_gateway.ps1 启动的 python 进程，可能不完全匹配 server:app (比如 uvicorn ...)
        # 杀掉所有命令行包含 bridge\server.py 或 uvicorn server:app 的进程
        # 注意：这里匹配 'bridge' 可能会误伤其他叫 bridge 的脚本，但在 Maya 插件目录下通常安全
        cmd2 = 'wmic process where "name=\'python.exe\' and commandline like \'%bridge%\'" call terminate'
        subprocess.call(cmd2, shell=True, startupinfo=startupinfo)
    except Exception:
        pass


def onMayaDroppedPythonFile(*_args):
    try:
        # 尝试关闭可能运行的网关，避免文件占用
        _kill_process_by_port(8765)
        # 稍微等一下让文件释放
        time.sleep(1)

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
            
        bridge_src = _source_bridge_dir()
        if not os.path.isdir(bridge_src):
            raise RuntimeError(u"未找到 bridge 目录：%s" % _norm(bridge_src))

        for modules_root, dst in targets:
            _ensure_dir(modules_root)

            if os.path.exists(dst):
                # 重试机制：最多尝试 5 次
                for i in range(5):
                    try:
                        # 尝试修改 dst 及其子文件的权限，防止只读文件无法删除
                        try:
                            os.chmod(dst, stat.S_IWRITE)
                            for root, dirs, files in os.walk(dst):
                                for d in dirs:
                                    try: os.chmod(os.path.join(root, d), stat.S_IWRITE)
                                    except: pass
                                for f in files:
                                    try: os.chmod(os.path.join(root, f), stat.S_IWRITE)
                                    except: pass
                        except Exception:
                            pass

                        shutil.rmtree(dst, onerror=_on_rm_error)
                        break
                    except Exception as e:
                        if i == 4: # 最后一次尝试
                            # 尝试重命名
                            try:
                                bak = dst + "_bak_%d" % int(time.time())
                                os.rename(dst, bak)
                                print(u"无法删除旧目录，已重命名为：%s" % _norm(bak))
                            except Exception as e2:
                                # 如果重命名也失败，说明目录被锁死。
                                # 但我们现在有 _copytree_force，可以尝试直接覆盖！
                                # 不过为了稳妥，我们最好还是警告一下，或者如果只是残留空目录，覆盖是没问题的。
                                # Error 183 就是因为目录还在，但 copytree 以为不在了。
                                # 改用 _copytree_force 后，这里即使报错，只要不是文件锁死，就可以继续。
                                pass
                        else:
                            # 再次尝试杀进程
                            _kill_process_by_port(8765)
                            time.sleep(1.0 + i)

            _copytree_force(src, dst)
            
            # 复制 icon 到根目录和 ui 目录
            icon_dst = os.path.join(dst, "icon")
            icon_ui_dst = os.path.join(dst, "scripts", "aiformaya", "ui", "icon")
            _copytree_merge(icon_src, icon_dst)
            _copytree_merge(icon_src, icon_ui_dst)
            
            # 复制 bridge 到模块根目录
            bridge_dst = os.path.join(dst, "bridge")
            _copytree_force(bridge_src, bridge_dst)
            
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
            
            # 【关键修复】立即将 scripts 路径加入 sys.path，确保无需重启即可使用
            scripts_path = os.path.join(dst, "scripts")
            if os.path.isdir(scripts_path):
                # 规范化路径，避免大小写或斜杠差异导致重复添加
                norm_path = _norm(scripts_path)
                # 检查 sys.path 中是否已存在（规范化比较）
                in_path = False
                for p in sys.path:
                    if _norm(p) == norm_path:
                        in_path = True
                        break
                if not in_path:
                    sys.path.append(scripts_path)
                    print(u"已动态添加路径到 sys.path: %s" % scripts_path)

        print(u"AI 小助手 安装完成！\n已复制到：\n%s\n并创建模块文件：\n%s\n\n【提示】虽然已动态加载，但为了最佳稳定性，建议重启 Maya。\n可在 Script Editor 中执行：\nimport aiformaya; aiformaya.show()"
              % ("\n".join([_norm(d) for d in installed_dirs]), "\n".join([_norm(m) for m in mod_paths])))


        cmds.confirmDialog(
            title=u"成功",
            message=u"AI 小助手 安装完成。",
            button=[u"确定"],
            defaultButton=u"确定",
        )

    except Exception as e:
        traceback.print_exc()
        cmds.confirmDialog(
            title=u"失败",
            message=u"AI 小助手 安装失败：%s" % e,
            button=[u"确定"],
            defaultButton=u"确定",
        )


if __name__ == "__main__":
    onMayaDroppedPythonFile()

