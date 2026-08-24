# -*- coding: utf-8 -*-

from Autodesk.Revit.DB import *


def xyz_add(a, b):
    return XYZ(
        a.X + b.X,
        a.Y + b.Y,
        a.Z + b.Z
    )


def xyz_sub(a, b):
    return XYZ(
        a.X - b.X,
        a.Y - b.Y,
        a.Z - b.Z
    )


def get_element_anchor_point(el):
    if el is None:
        return None

    try:
        loc = el.Location

        if isinstance(loc, LocationPoint):
            return loc.Point
    except:
        pass

    try:
        bbox = el.get_BoundingBox(None)

        if bbox:
            return XYZ(
                (bbox.Min.X + bbox.Max.X) / 2.0,
                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                (bbox.Min.Z + bbox.Max.Z) / 2.0
            )
    except:
        pass

    return None