# -*- coding: utf-8 -*-

from Autodesk.Revit.DB import *


def get_first_reference(tag):
    try:
        refs = tag.GetTaggedReferences()

        if refs and len(refs) > 0:
            return refs[0]
    except:
        pass

    return None


def get_leader_end_condition(tag):
    try:
        if not tag.HasLeader:
            return "no_leader"
    except:
        pass

    try:
        cond_str = str(tag.LeaderEndCondition)

        if "Free" in cond_str:
            return "free"

        if "Attached" in cond_str:
            return "attached"
    except:
        pass

    try:
        ref = get_first_reference(tag)
        pt = tag.GetLeaderEnd(ref)

        if pt is not None:
            return "free"
    except:
        pass

    return "attached"


def set_leader_end_condition(tag, mode):
    try:
        if mode == "no_leader":
            tag.HasLeader = False
            return True

        tag.HasLeader = True

        if mode == "free":
            tag.LeaderEndCondition = LeaderEndCondition.Free
            return True

        if mode == "attached":
            tag.LeaderEndCondition = LeaderEndCondition.Attached
            return True

    except:
        pass

    return False


def safe_get_leader_end(tag, ref):
    try:
        if ref:
            return tag.GetLeaderEnd(ref)
    except:
        pass

    return None


def safe_set_leader_end(tag, ref, pt):
    try:
        if ref and pt:
            tag.SetLeaderEnd(ref, pt)
            return True
    except:
        pass

    return False


def safe_get_leader_elbow(tag, ref):
    try:
        if ref:
            return tag.GetLeaderElbow(ref)
    except:
        pass

    return None


def safe_set_leader_elbow(tag, ref, pt):
    try:
        if ref and pt:
            tag.SetLeaderElbow(ref, pt)
            return True
    except:
        pass

    return False