# -*- coding: utf-8 -*-

def calculate_spatial_offsets(relation, target_details, act_count):
    """
    Returns a Maya python snippet string that calculates positions based on 
    precise bounding box (BBox), pivot, and offset mathematical logic.
    """
    code_lines = []

    # Target details assume we have:
    # tg = 'pCube1'
    code_lines.append("import maya.cmds as cmds")
    code_lines.append("import math")
    
    if relation == "around":
        radius_expr = "max([bb[3]-bb[0], bb[4]-bb[1], bb[5]-bb[2]]) * 1.5 if bb else 3.0"
        code_lines.append("""
def scatter_around(target_mesh, items):
    pos = cmds.xform(target_mesh, q=True, ws=True, rp=True) if target_mesh else [0,0,0]
    bb = cmds.xform(target_mesh, q=True, ws=True, bb=True) if target_mesh else None
    radius = %s
    count = len(items)
    for i, item in enumerate(items):
        angle = (2 * math.pi / count) * i
        x = pos[0] + radius * math.cos(angle)
        z = pos[2] + radius * math.sin(angle)
        cmds.xform(item, ws=True, t=(x, pos[1], z))
        # Look at center
        cmds.viewPlace(item, lookAt=pos) if cmds.objectType(item) in ['camera', 'transform'] else None
        """ % radius_expr)
        
    elif relation == "on_top_of":
        code_lines.append("""
def place_on_top(target_mesh, items):
    bb = cmds.xform(target_mesh, q=True, ws=True, bb=True) if target_mesh else [0,0,0,0,0,0]
    pos = cmds.xform(target_mesh, q=True, ws=True, rp=True) if target_mesh else [0,0,0]
    top_y = bb[4]  # Y max
    for item in items:
        ib = cmds.xform(item, q=True, ws=True, bb=True)
        # offset item so its bottom touches top_y
        offset_y = item_h = (ib[4] - ib[1]) / 2.0 if ib else 0.5
        cmds.xform(item, ws=True, t=(pos[0], top_y + offset_y, pos[2]))
        """)

    elif relation == "next_to":
        code_lines.append("""
def place_next_to(target_mesh, items):
    bb = cmds.xform(target_mesh, q=True, ws=True, bb=True) if target_mesh else [0,0,0,0,0,0]
    pos = cmds.xform(target_mesh, q=True, ws=True, rp=True) if target_mesh else [0,0,0]
    right_x = bb[3]  # X max
    for i, item in enumerate(items):
        ib = cmds.xform(item, q=True, ws=True, bb=True)
        width_offset = (ib[3] - ib[0]) if ib else 1.0
        cmds.xform(item, ws=True, t=(right_x + (i+1)*width_offset, pos[1], pos[2]))
        """)

    elif relation == "scatter":
        code_lines.append("""
def random_scatter(target_mesh, items):
    import random
    import maya.cmds as cmds
    bb = cmds.xform(target_mesh, q=True, ws=True, bb=True) if target_mesh else [-5,-5,-5,5,5,5]
    for item in items:
        # Calculate random X and Z within bounds
        x = random.uniform(bb[0], bb[3])
        z = random.uniform(bb[2], bb[5])
        
        # Calculate object radius to prevent intersection
        ib = cmds.xform(item, q=True, ws=True, bb=True)
        radius = (ib[4] - ib[1]) / 2.0 if ib else 0.5
        
        # Snap Y to the top of the ground bbox + radius
        y = bb[4] + radius
        
        cmds.xform(item, ws=True, t=(x,y,z))
        """)
        
    # Return raw text of the helper functions
    return "\n".join(code_lines)

