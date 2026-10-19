"""Turn parsed switch output into one platform-neutral model the UI edits.

Model shape (all JSON):
  platform: "fos" | "nxos"
  switch:   {name, host, version}
  vsan, vsans, vsan_names, smart, zone_mode, da_mode
  aliases:  [{id, name, kind: alias|device-alias|fcalias, wwpns: [...]}]
  zones:    [{id, name, members: [{kind: alias|wwpn|other, ref, tag, principal}], peer: bool}]
  cfgs:     [{id, name, zones: [zone ids]}]   (FOS cfgs or NX-OS zonesets)
  active:   cfg id or None
  devices:  {wwpn: {role, role_src, vendor, online, node}}
  groups:   {wwpn: host or SAN name}
  notes:    [str]
IDs are stable across edits so renames can be told apart from delete + create.
"""
from __future__ import annotations

import re
from collections import Counter

from .common import name_role, name_stem, norm_wwn, vendor_of
from .parsers import fos, nxos

_PAR = re.compile(r"^2[0-9a-f]:[0-9a-f]{2}:00:02:ac:")


def detect_platform(text: str) -> str | None:
    if "Fabric OS" in text or "cfgshow" in text or "Defined configuration" in text:
        return "fos"
    if "device-alias" in text or ("zone name" in text and " vsan " in text) or "NX-OS" in text:
        return "nxos"
    if fos.parse_zoning(text)["format"]:
        return "fos"
    return None


def build_model(platform: str, texts: dict[str, str], vsan: int | None = None, host: str = "") -> dict:
    model = _from_fos(texts) if platform == "fos" else _from_nxos(texts, vsan)
    model["switch"]["host"] = host
    _annotate(model)
    return model


# ---------------------------------------------------------------- FOS
def _from_fos(texts: dict[str, str]) -> dict:
    zoning = texts.get("cfgshow") or texts.get("paste") or ""
    db = fos.parse_zoning(zoning)
    ns_text = "\n".join(texts.get(k, "") for k in ("nsshow", "nscamshow", "paste"))
    ns = fos.parse_ns(ns_text)
    notes: list[str] = []
    aliases = []
    ci = {}
    for name, mem in db["aliases"].items():
        ws, other = [], []
        for m in mem:
            w = norm_wwn(m)
            (ws if w else other).append(w or m)
        if other:
            notes.append(f'Alias "{name}" has non-WWPN members ({", ".join(other)}); they are kept but not editable here.')
        aliases.append({"id": f"a:{name}", "name": name, "kind": "alias", "wwpns": list(dict.fromkeys(ws)),
                        "other": other})
        ci[name.lower()] = name

    def member(tok: str, principal: bool = False) -> dict:
        a = tok if tok in db["aliases"] else ci.get(tok.lower())
        if a:
            return {"kind": "alias", "ref": f"a:{a}", "tag": None, "principal": principal}
        w = norm_wwn(tok)
        if w:
            return {"kind": "wwpn", "ref": w, "tag": None, "principal": principal}
        return {"kind": "other", "ref": tok, "tag": None, "principal": principal}

    zones = []
    for name, z in db["zones"].items():
        if z["peer"]:
            pr = set(z["peer"]["principal"])
            mems = [member(t, t in pr) for t in z["members"]]
        else:
            mems = [member(t) for t in z["members"]]
        zones.append({"id": f"z:{name}", "name": name, "members": mems, "peer": bool(z["peer"])})
        for m in mems:
            if m["kind"] == "other":
                notes.append(f'Zone "{name}" has member "{m["ref"]}" (Domain,Port or undefined alias).')
    cfgs = []
    for name, zs in db["cfgs"].items():
        missing = [z for z in zs if z not in db["zones"]]
        if missing:
            notes.append(f'Config "{name}" lists undefined zones: {", ".join(missing)}.')
        cfgs.append({"id": f"c:{name}", "name": name, "zones": [f"z:{z}" for z in zs if z in db["zones"]]})
    active = f"c:{db['effective']}" if db["effective"] in db["cfgs"] else None
    return {
        "platform": "fos",
        "switch": {"name": fos.parse_switchname(texts.get("switchshow", "")) or "FOS switch",
                   "version": fos.parse_version(texts.get("version", "")), "host": ""},
        "vsan": None, "vsans": [], "vsan_names": {}, "smart": False, "zone_mode": "basic", "da_mode": "basic",
        "defzone": db["defzone"], "aliases": aliases, "zones": zones, "cfgs": cfgs, "active": active,
        "_ns": {w: v for w, v in ns.items()}, "notes": notes,
    }


# ---------------------------------------------------------------- NX-OS
def _from_nxos(texts: dict[str, str], vsan: int | None) -> dict:
    rc = texts.get("show running-config") or texts.get("paste") or ""
    run = nxos.parse_running(rc)
    fcns = nxos.parse_fcns(texts.get("show fcns database", "") or texts.get("paste", ""))
    status = nxos.parse_zone_status(texts.get("show zone status", ""))
    act = nxos.parse_zoneset_active(texts.get("show zoneset active", ""))
    ver, devname = nxos.parse_version(texts.get("show version", ""))
    vsans = sorted(set(run["vsans"]) | set(fcns))
    if vsan is None or vsan not in vsans:
        with_zones = sorted(run["vsans"], key=lambda v: -len(run["vsans"][v]["zones"]))
        vsan = with_zones[0] if with_zones else (vsans[0] if vsans else 1)
    v = run["vsans"].get(vsan) or {"zones": {}, "zonesets": {}, "fcaliases": {}, "active": None, "smart": False,
                                    "mode": "basic"}
    st = status.get(vsan, {})
    notes: list[str] = []
    aliases = [{"id": f"a:{n}", "name": n, "kind": "device-alias", "wwpns": [w]} for n, w in run["da"].items()]
    aliases += [{"id": f"f:{n}", "name": n, "kind": "fcalias", "wwpns": list(dict.fromkeys(ws))}
                for n, ws in v["fcaliases"].items()]

    def member(m: dict) -> dict:
        if m["kind"] == "device-alias":
            if m["ref"] in run["da"]:
                return {"kind": "alias", "ref": f"a:{m['ref']}", "tag": m["tag"], "principal": False}
            return {"kind": "other", "ref": f"device-alias {m['ref']}", "tag": m["tag"], "principal": False}
        if m["kind"] == "fcalias":
            if m["ref"] in v["fcaliases"]:
                return {"kind": "alias", "ref": f"f:{m['ref']}", "tag": m["tag"], "principal": False}
            return {"kind": "other", "ref": f"fcalias {m['ref']}", "tag": m["tag"], "principal": False}
        return {"kind": m["kind"], "ref": m["ref"], "tag": m["tag"], "principal": False}

    zones = []
    for n, mems in v["zones"].items():
        zm = [member(m) for m in mems]
        zones.append({"id": f"z:{n}", "name": n, "members": zm, "peer": False})
        for m in zm:
            if m["kind"] == "other":
                notes.append(f'Zone "{n}" has member "{m["ref"]}", which this tool cannot edit.')
    cfgs = [{"id": f"c:{n}", "name": n, "zones": [f"z:{z}" for z in zs if z in v["zones"]]}
            for n, zs in v["zonesets"].items()]
    active_name = act.get(vsan) or v["active"]
    if active_name and active_name not in v["zonesets"]:
        cfgs.append({"id": f"c:{active_name}", "name": active_name, "zones": []})
        notes.append(f'Active zoneset "{active_name}" is not in the full zone database.')
    ns = {w: {"role": d["role"], "node": "", "vendor": d["vendor"]} for w, d in fcns.get(vsan, {}).items()}
    return {
        "platform": "nxos",
        "switch": {"name": run["switchname"] or devname or "MDS switch", "version": ver or run["version"], "host": ""},
        "vsan": vsan, "vsans": vsans, "vsan_names": {str(k): x for k, x in run["vsan_names"].items()},
        "smart": st.get("smart", v["smart"]), "zone_mode": st.get("mode", v["mode"]), "da_mode": run["da_mode"],
        "defzone": None, "aliases": aliases, "zones": zones, "cfgs": cfgs,
        "active": f"c:{active_name}" if active_name else None, "_ns": ns, "notes": notes,
    }


# ---------------------------------------------------------------- roles and groups
def _annotate(model: dict) -> None:
    ns = model.pop("_ns", {})
    al = {a["id"]: a for a in model["aliases"]}
    by_w: dict[str, list[str]] = {}
    for a in model["aliases"]:
        for w in a["wwpns"]:
            by_w.setdefault(w, []).append(a["name"])
    devs: dict[str, dict] = {}

    def dev(w):
        return devs.setdefault(w, {"role": "unknown", "role_src": "", "vendor": "", "online": False, "node": ""})

    def mem_ws(m):
        if m["kind"] == "alias":
            return al[m["ref"]]["wwpns"] if m["ref"] in al else []
        return [m["ref"]] if m["kind"] == "wwpn" else []

    # device-aliases are fabric-wide; on NX-OS only show devices seen in this VSAN
    if model["platform"] == "fos":
        for w in by_w:
            dev(w)
    else:
        for a in model["aliases"]:
            if a["kind"] == "fcalias":
                for w in a["wwpns"]:
                    dev(w)
    for z in model["zones"]:
        for m in z["members"]:
            for w in mem_ws(m):
                dev(w)
    for w, d in ns.items():
        dev(w).update(online=True, node=d.get("node", ""))

    tags: dict[str, set] = {}
    for z in model["zones"]:
        for m in z["members"]:
            if m.get("tag") in ("initiator", "target", "both"):
                for w in mem_ws(m):
                    tags.setdefault(w, set()).add(m["tag"])

    for w, d in devs.items():
        vname, vrole = vendor_of(w)
        d["vendor"] = vname or (ns.get(w, {}).get("vendor") or "")
        if ns.get(w, {}).get("role"):
            d["role"], d["role_src"] = ns[w]["role"], "Name server"
        elif len(tags.get(w, ())) == 1:
            d["role"], d["role_src"] = next(iter(tags[w])), "Zone tag"
        elif vrole:
            d["role"], d["role_src"] = vrole, "WWPN vendor"
        else:
            roles = {r for r in (name_role(n) for n in by_w.get(w, [])) if r}
            if len(roles) == 1:
                d["role"], d["role_src"] = roles.pop(), "Alias name"

    # peer zones: principals are usually targets
    for z in model["zones"]:
        if not z["peer"]:
            continue
        for m in z["members"]:
            for w in mem_ws(m):
                if devs[w]["role"] == "unknown":
                    devs[w]["role"] = "target" if m["principal"] else "initiator"
                    devs[w]["role_src"] = "Peer zone"
    # propagate inside zones
    for _ in range(8):
        changed = False
        for z in model["zones"]:
            ws = list(dict.fromkeys(w for m in z["members"] for w in mem_ws(m)))
            unk = [w for w in ws if devs[w]["role"] == "unknown"]
            if not unk:
                continue
            i = sum(devs[w]["role"] == "initiator" for w in ws)
            t = sum(devs[w]["role"] == "target" for w in ws)
            to = "target" if i and not t else "initiator" if t and not i else None
            if to:
                for w in unk:
                    devs[w]["role"], devs[w]["role_src"] = to, "Inferred from zone"
                changed = True
        if not changed:
            break

    # groups: hosts by alias stem or node name, SANs by array serial or alias stem
    groups: dict[str, str] = {}
    par: dict[str, list[str]] = {}
    for w, d in devs.items():
        names = by_w.get(w, [])
        if d["role"] in ("target", "both"):
            if _PAR.match(w):
                par.setdefault(w[15:], []).append(w)
                continue
            groups[w] = name_stem(names[0]) if names else (d["vendor"].split(" ")[0] or "SAN") + "_" + w[-8:].replace(":", "")
        else:
            if names:
                groups[w] = name_stem(names[0])
            elif d["node"]:
                groups[w] = d["node"].split(".")[0].split(" ")[0] or "Unassigned"
            else:
                groups[w] = "Unassigned"
    for serial, ws in par.items():
        c = Counter(name_stem(n) for w in ws for n in by_w.get(w, []))
        nm = c.most_common(1)[0][0] if c else "HPE3PAR_" + serial.replace(":", "").upper()
        for w in ws:
            groups[w] = nm
    model["devices"] = devs
    model["groups"] = groups
