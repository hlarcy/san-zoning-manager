"""Cisco MDS NX-OS parsers: show running-config, show fcns database, show zone status, show zoneset active."""
from __future__ import annotations

import re

from ..common import norm_wwn

_TAG = r"(?:\s+(initiator|target|both))?\s*$"


def _vsan():
    return {"zones": {}, "zonesets": {}, "fcaliases": {}, "active": None, "smart": False, "mode": "basic",
            "a_zones": {}, "a_zonesets": {}}


def parse_member(text: str) -> dict:
    s = text.strip()
    m = re.match(r"^member\s+device-alias\s+(\S+)" + _TAG, s)
    if m:
        return {"kind": "device-alias", "ref": m.group(1), "tag": m.group(2)}
    m = re.match(r"^member\s+pwwn\s+(\S+)" + _TAG, s)
    if m and norm_wwn(m.group(1)):
        return {"kind": "wwpn", "ref": norm_wwn(m.group(1)), "tag": m.group(2)}
    m = re.match(r"^member\s+fcalias\s+(\S+)" + _TAG, s)
    if m:
        return {"kind": "fcalias", "ref": m.group(1), "tag": m.group(2)}
    return {"kind": "other", "ref": s[len("member "):] if s.startswith("member ") else s, "tag": None}


def parse_running(text: str) -> dict:
    out = {"switchname": "", "version": "", "da": {}, "da_mode": "basic", "vsans": {}, "vsan_names": {}}
    ctx = None
    sect = "full"
    V = lambda v: out["vsans"].setdefault(int(v), _vsan())  # noqa: E731
    for raw in text.replace("\r", "").split("\n"):
        if not raw.strip():
            continue
        st = raw.strip()
        if st.startswith("!"):
            low = st.lower()
            if "active zone database section" in low:
                sect = "active"
            elif "full zone database section" in low:
                sect = "full"
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent == 0:
            ctx = None
            m = re.match(r"^switchname\s+(\S+)", st)
            if m:
                out["switchname"] = m.group(1); continue
            m = re.match(r"^version\s+(\S+)", st)
            if m:
                out["version"] = m.group(1); continue
            if st == "device-alias database":
                ctx = ("da",); continue
            if st == "device-alias mode enhanced":
                out["da_mode"] = "enhanced"; continue
            if st == "vsan database":
                ctx = ("vsandb",); continue
            m = re.match(r"^fcalias name (\S+) vsan (\d+)$", st)
            if m:
                V(m.group(2))["fcaliases"].setdefault(m.group(1), [])
                ctx = ("fcalias", int(m.group(2)), m.group(1)); continue
            m = re.match(r"^zone name (\S+) vsan (\d+)$", st)
            if m:
                key = "zones" if sect == "full" else "a_zones"
                V(m.group(2))[key][m.group(1)] = []
                ctx = ("zone", int(m.group(2)), m.group(1), key); continue
            m = re.match(r"^zoneset name (\S+) vsan (\d+)$", st)
            if m:
                key = "zonesets" if sect == "full" else "a_zonesets"
                V(m.group(2))[key][m.group(1)] = []
                ctx = ("zoneset", int(m.group(2)), m.group(1), key); continue
            m = re.match(r"^zoneset activate name (\S+) vsan (\d+)$", st)
            if m:
                V(m.group(2))["active"] = m.group(1); continue
            m = re.match(r"^zone smart-zoning enable vsan (\d+)$", st)
            if m:
                V(m.group(1))["smart"] = True; continue
            m = re.match(r"^zone mode enhanced vsan (\d+)$", st)
            if m:
                V(m.group(1))["mode"] = "enhanced"; continue
            continue
        if not ctx:
            continue
        if ctx[0] == "da":
            m = re.match(r"^device-alias name (\S+) pwwn (\S+)", st)
            if m and norm_wwn(m.group(2)):
                out["da"][m.group(1)] = norm_wwn(m.group(2))
        elif ctx[0] == "vsandb":
            m = re.match(r'^vsan (\d+)(?: name "?([^"]+?)"?)?(?:\s|$)', st)
            if m:
                V(m.group(1))
                if m.group(2):
                    out["vsan_names"][int(m.group(1))] = m.group(2)
        elif ctx[0] == "fcalias":
            m = re.match(r"^member pwwn (\S+)", st)
            if m and norm_wwn(m.group(1)):
                V(ctx[1])["fcaliases"][ctx[2]].append(norm_wwn(m.group(1)))
        elif ctx[0] == "zone" and st.startswith("member"):
            V(ctx[1])[ctx[3]][ctx[2]].append(parse_member(st))
        elif ctx[0] == "zoneset":
            m = re.match(r"^member (\S+)$", st)
            if m:
                V(ctx[1])[ctx[3]][ctx[2]].append(m.group(1))
    for v in out["vsans"].values():
        if not v["zones"] and v["a_zones"]:
            v["zones"] = v["a_zones"]
        if not v["zonesets"] and v["a_zonesets"]:
            v["zonesets"] = v["a_zonesets"]
        del v["a_zones"], v["a_zonesets"]
    return out


def parse_fcns(text: str) -> dict:
    """show fcns database -> {vsan: {wwpn: {role, vendor, fc4}}}"""
    out: dict[int, dict] = {}
    vsan = 0
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*VSAN\s+(\d+):", raw)
        if m:
            vsan = int(m.group(1)); continue
        m = re.match(r"^\s*(0x[0-9a-f]{6})\s+(\S+)\s+([0-9a-f]{2}(?::[0-9a-f]{2}){7})\s*(?:\(([^)]*)\))?\s*(.*)$",
                     raw, re.I)
        if not m:
            continue
        feat = (m.group(5) or "").lower()
        role = None
        if ("init" in feat and "target" in feat) or "both" in feat:
            role = "both"
        elif "init" in feat:
            role = "initiator"
        elif "target" in feat:
            role = "target"
        out.setdefault(vsan, {})[m.group(3).lower()] = {"role": role, "vendor": m.group(4) or "", "fc4": feat}
    return out


def parse_zone_status(text: str) -> dict:
    out: dict[int, dict] = {}
    cur = None
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*VSAN:\s*(\d+)", raw)
        if m:
            cur = out.setdefault(int(m.group(1)), {}); continue
        if cur is None:
            continue
        m = re.search(r"\bmode:\s*(basic|enhanced)", raw)
        if m and "merge" not in raw:
            cur["mode"] = m.group(1)
        m = re.search(r"smart-zoning:\s*(enabled|disabled)", raw)
        if m:
            cur["smart"] = m.group(1) == "enabled"
    return out


def parse_zoneset_active(text: str) -> dict:
    return {int(v): n for n, v in re.findall(r"^zoneset name (\S+) vsan (\d+)", text, re.M)}


def parse_version(text: str) -> tuple[str, str]:
    ver = re.search(r"(?:system|NXOS):\s+version\s+(\S+)", text)
    name = re.search(r"Device name:\s*(\S+)", text)
    return (ver.group(1) if ver else ""), (name.group(1) if name else "")
