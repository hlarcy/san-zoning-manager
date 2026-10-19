"""Compare the pulled model with the edited model and render the commands that make the change.

Nothing here talks to a switch. Output is text for a person to review and paste.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .common import valid_name


def _idx(items):
    return {x["id"]: x for x in items}


def _mkey(m):
    return (m["kind"], m["ref"], m.get("tag") or "", bool(m.get("principal")))


class Diff:
    def __init__(self, o: dict, w: dict):
        self.o, self.w = o, w
        self.oa, self.wa = _idx(o["aliases"]), _idx(w["aliases"])
        self.oz, self.wz = _idx(o["zones"]), _idx(w["zones"])
        self.oc, self.wc = _idx(o["cfgs"]), _idx(w["cfgs"])
        self.changes: list[tuple[str, str]] = []
        self.warnings: list[str] = []

        # aliases
        self.a_new = [a for i, a in self.wa.items() if i not in self.oa]
        self.a_del = [a for i, a in self.oa.items() if i not in self.wa]
        self.a_ren, self.a_add, self.a_rm, self.a_repoint = [], [], [], []
        for i in self.oa.keys() & self.wa.keys():
            oa, wa = self.oa[i], self.wa[i]
            if oa["name"] != wa["name"]:
                self.a_ren.append((oa, wa))
            if oa["kind"] == "device-alias":
                if oa["wwpns"] != wa["wwpns"]:
                    self.a_repoint.append((oa, wa))
            else:
                self.a_add += [(wa, x) for x in wa["wwpns"] if x not in oa["wwpns"]]
                self.a_rm += [(wa, x) for x in oa["wwpns"] if x not in wa["wwpns"]]
        # zones
        self.z_new = [z for i, z in self.wz.items() if i not in self.oz]
        self.z_del = [z for i, z in self.oz.items() if i not in self.wz]
        self.z_ren, self.z_add, self.z_rm = [], {}, {}
        for i in self.oz.keys() & self.wz.keys():
            oz, wz = self.oz[i], self.wz[i]
            if oz["name"] != wz["name"]:
                self.z_ren.append((oz, wz))
            ok, wk = {_mkey(m) for m in oz["members"]}, {_mkey(m) for m in wz["members"]}
            adds = [m for m in wz["members"] if _mkey(m) not in ok]
            rms = [m for m in oz["members"] if _mkey(m) not in wk]
            if adds:
                self.z_add[i] = adds
            if rms:
                self.z_rm[i] = rms
        # cfgs / zonesets
        self.c_new = [c for i, c in self.wc.items() if i not in self.oc]
        self.c_del = [c for i, c in self.oc.items() if i not in self.wc]
        self.c_ren, self.c_add, self.c_rm = [], {}, {}
        for i in self.oc.keys() & self.wc.keys():
            oc, wc = self.oc[i], self.wc[i]
            if oc["name"] != wc["name"]:
                self.c_ren.append((oc, wc))
            a = [z for z in wc["zones"] if z not in oc["zones"]]
            r = [z for z in oc["zones"] if z not in wc["zones"]]
            if a:
                self.c_add[i] = a
            if r:
                self.c_rm[i] = r
        self.active_changed = o.get("active") != w.get("active")

    # names always resolve to the edited name when the object still exists
    def aname(self, aid):
        a = self.wa.get(aid) or self.oa.get(aid)
        return a["name"] if a else aid

    def zname(self, zid):
        z = self.wz.get(zid) or self.oz.get(zid)
        return z["name"] if z else zid

    def cname(self, cid):
        c = self.wc.get(cid) or self.oc.get(cid)
        return c["name"] if c else cid

    def label(self, m):
        if m["kind"] == "alias":
            return self.aname(m["ref"])
        return m["ref"]

    @property
    def empty(self):
        return not any([self.a_new, self.a_del, self.a_ren, self.a_add, self.a_rm, self.a_repoint, self.z_new,
                        self.z_del, self.z_ren, self.z_add, self.z_rm, self.c_new, self.c_del, self.c_ren,
                        self.c_add, self.c_rm, self.active_changed])

    def touches_active(self) -> bool:
        act = self.w.get("active")
        if not act:
            return False
        if self.active_changed or act in self.c_add or act in self.c_rm:
            return True
        active_z = set(self.wc.get(act, {}).get("zones", [])) | set(self.oc.get(act, {}).get("zones", []))
        if active_z & (set(self.z_add) | set(self.z_rm)):
            return True
        changed_alias = {a["id"] for a, _ in self.a_add} | {a["id"] for a, _ in self.a_rm} | \
                        {b["id"] for _, b in self.a_repoint}
        for zid in active_z:
            z = self.wz.get(zid)
            if z and any(m["kind"] == "alias" and m["ref"] in changed_alias for m in z["members"]):
                return True
        return False

    def describe(self):
        C = self.changes.append
        for oa, wa in self.a_ren:
            C(("Aliases", f"Rename {oa['name']} to {wa['name']}"))
        for a in self.a_new:
            C(("Aliases", f"Create {a['name']} ({', '.join(a['wwpns'])})"))
        for oa, wa in self.a_repoint:
            C(("Aliases", f"Point {wa['name']} at {', '.join(wa['wwpns'])} (was {', '.join(oa['wwpns'])})"))
        for a, x in self.a_add:
            C(("Aliases", f"Add {x} to {a['name']}"))
        for a, x in self.a_rm:
            C(("Aliases", f"Remove {x} from {a['name']}"))
        for a in self.a_del:
            C(("Aliases", f"Delete {a['name']}"))
        for oz, wz in self.z_ren:
            C(("Zones", f"Rename zone {oz['name']} to {wz['name']}"))
        for z in self.z_new:
            C(("Zones", f"Create zone {z['name']} with {', '.join(self.label(m) for m in z['members'])}"))
        for zid, ms in self.z_add.items():
            C(("Zones", f"Zone {self.zname(zid)}: add {', '.join(self.label(m) + (' ' + m['tag'] if m.get('tag') else '') for m in ms)}"))
        for zid, ms in self.z_rm.items():
            C(("Zones", f"Zone {self.zname(zid)}: remove {', '.join(self.label(m) + (' ' + m['tag'] if m.get('tag') else '') for m in ms)}"))
        for z in self.z_del:
            C(("Zones", f"Delete zone {z['name']}"))
        word = "config" if self.w["platform"] == "fos" else "zoneset"
        for oc, wc in self.c_ren:
            C(("Configs", f"Rename {word} {oc['name']} to {wc['name']}"))
        for c in self.c_new:
            C(("Configs", f"Create {word} {c['name']}"))
        for cid, zs in self.c_add.items():
            C(("Configs", f"{self.cname(cid)}: add {', '.join(self.zname(z) for z in zs)}"))
        for cid, zs in self.c_rm.items():
            C(("Configs", f"{self.cname(cid)}: remove {', '.join(self.zname(z) for z in zs)}"))
        for c in self.c_del:
            C(("Configs", f"Delete {word} {c['name']}"))
        if self.active_changed and self.w.get("active"):
            C(("Configs", f"Make {self.cname(self.w['active'])} the active {word}"))

    def validate(self):
        P = self.w["platform"]
        W = self.warnings.append
        seen = {}
        for a in self.w["aliases"]:
            if not valid_name(P, a["name"]):
                W(f'Alias name "{a["name"]}" is not valid on {"FOS" if P == "fos" else "NX-OS"}.')
            k = a["name"].lower()
            if k in seen:
                W(f'Alias name "{a["name"]}" is used twice.')
            seen[k] = 1
            if a["kind"] == "device-alias" and len(a["wwpns"]) != 1:
                W(f'Device-alias "{a["name"]}" must map to exactly one pWWN.')
            if not a["wwpns"] and not a.get("other"):
                W(f'Alias "{a["name"]}" has no WWPNs.')
        das = {}
        for a in self.w["aliases"]:
            if a["kind"] == "device-alias" and a["wwpns"]:
                w = a["wwpns"][0]
                if w in das:
                    W(f'pWWN {w} has two device-aliases ({das[w]} and {a["name"]}). MDS allows one.')
                das[w] = a["name"]
        zn = {}
        for z in self.w["zones"]:
            if not valid_name(P, z["name"]):
                W(f'Zone name "{z["name"]}" is not valid.')
            if z["name"].lower() in zn:
                W(f'Zone name "{z["name"]}" is used twice.')
            zn[z["name"].lower()] = 1
            if not z["members"]:
                W(f'Zone "{z["name"]}" has no members.')
            for m in z["members"]:
                if m["kind"] == "alias" and m["ref"] not in self.wa:
                    W(f'Zone "{z["name"]}" refers to a deleted alias.')


# ------------------------------------------------------------------ FOS
def _q(s):
    return f'"{s}"'


def render_fos(d: Diff) -> list[str]:
    L: list[str] = []
    mem = lambda ms: "; ".join(d.label(m) for m in ms)  # noqa: E731
    for oa, wa in d.a_ren:
        L.append(f"zoneobjectrename {_q(oa['name'])}, {_q(wa['name'])}")
    for oz, wz in d.z_ren:
        L.append(f"zoneobjectrename {_q(oz['name'])}, {_q(wz['name'])}")
    for oc, wc in d.c_ren:
        L.append(f"zoneobjectrename {_q(oc['name'])}, {_q(wc['name'])}")
    for a in d.a_new:
        L.append(f"alicreate {_q(a['name'])}, {_q('; '.join(a['wwpns']))}")
    for a, x in d.a_add:
        L.append(f"aliadd {_q(a['name'])}, {_q(x)}")
    for z in d.z_new:
        if z.get("peer"):
            pr = [m for m in z["members"] if m.get("principal")]
            pe = [m for m in z["members"] if not m.get("principal")]
            L.append(f"zone --create -peerzone {z['name']}" + (f" -principal {_q(mem(pr))}" if pr else "") +
                     (f" -members {_q(mem(pe))}" if pe else ""))
        else:
            L.append(f"zonecreate {_q(z['name'])}, {_q(mem(z['members']))}")
    for zid, ms in d.z_add.items():
        z = d.wz[zid]
        if z.get("peer"):
            pr = [m for m in ms if m.get("principal")]
            pe = [m for m in ms if not m.get("principal")]
            L.append(f"zone --add -peerzone {z['name']}" + (f" -principal {_q(mem(pr))}" if pr else "") +
                     (f" -members {_q(mem(pe))}" if pe else ""))
        else:
            L.append(f"zoneadd {_q(z['name'])}, {_q(mem(ms))}")
    for c in d.c_new:
        L.append(f"cfgcreate {_q(c['name'])}, {_q('; '.join(d.zname(z) for z in c['zones']))}")
    for cid, zs in d.c_add.items():
        L.append(f"cfgadd {_q(d.cname(cid))}, {_q('; '.join(d.zname(z) for z in zs))}")
    for zid, ms in d.z_rm.items():
        z = d.wz[zid]
        if z.get("peer"):
            pr = [m for m in ms if m.get("principal")]
            pe = [m for m in ms if not m.get("principal")]
            L.append(f"zone --remove -peerzone {z['name']}" + (f" -principal {_q(mem(pr))}" if pr else "") +
                     (f" -members {_q(mem(pe))}" if pe else ""))
        else:
            L.append(f"zoneremove {_q(z['name'])}, {_q(mem(ms))}")
    for cid, zs in d.c_rm.items():
        L.append(f"cfgremove {_q(d.cname(cid))}, {_q('; '.join(d.zname(z) for z in zs))}")
    for z in d.z_del:
        L.append(f"zonedelete {_q(z['name'])}")
    for a, x in d.a_rm:
        L.append(f"aliremove {_q(a['name'])}, {_q(x)}")
    for a in d.a_del:
        L.append(f"alidelete {_q(a['name'])}")
    for c in d.c_del:
        L.append(f"cfgdelete {_q(c['name'])}")
    if d.w.get("active") and d.touches_active():
        L.append(f"cfgenable {_q(d.cname(d.w['active']))}")
    elif L:
        L.append("cfgsave")
    return L


# ------------------------------------------------------------------ NX-OS
def render_nxos(d: Diff, comments: bool = True) -> list[str]:
    v = d.w["vsan"]
    L: list[str] = []
    c = (lambda s: L.append(f"! {s}")) if comments else (lambda s: None)

    def ref(m):
        if m["kind"] == "alias":
            a = d.wa.get(m["ref"]) or d.oa.get(m["ref"])
            kind = a["kind"] if a else "device-alias"
            r = f"{'fcalias' if kind == 'fcalias' else 'device-alias'} {d.aname(m['ref'])}"
        elif m["kind"] == "wwpn":
            r = f"pwwn {m['ref']}"
        else:
            r = m["ref"]
        return r + (f" {m['tag']}" if m.get("tag") else "")

    L.append("configure terminal")
    da_ren = [(o, w) for o, w in d.a_ren if o["kind"] == "device-alias"]
    da_new = [a for a in d.a_new if a["kind"] == "device-alias"]
    da_del = [a for a in d.a_del if a["kind"] == "device-alias"]
    fa_ren = [(o, w) for o, w in d.a_ren if o["kind"] == "fcalias"]
    fa_new = [a for a in d.a_new if a["kind"] == "fcalias"]
    fa_del = [a for a in d.a_del if a["kind"] == "fcalias"]
    if da_ren or da_new or d.a_repoint:
        c("Device-aliases")
        L.append("device-alias database")
        for o, w in da_ren:
            L.append(f"  device-alias rename {o['name']} {w['name']}")
        for o, w in d.a_repoint:
            L.append(f"  no device-alias name {w['name']}")
            L.append(f"  device-alias name {w['name']} pwwn {w['wwpns'][0]}")
        for a in da_new:
            L.append(f"  device-alias name {a['name']} pwwn {a['wwpns'][0]}")
        L.append("exit")
        L.append("device-alias commit")
    fa_mod = {}
    for a, x in d.a_add:
        if a["kind"] == "fcalias":
            fa_mod.setdefault(a["id"], []).append(f"  member pwwn {x}")
    for a, x in d.a_rm:
        if a["kind"] == "fcalias":
            fa_mod.setdefault(a["id"], []).append(f"  no member pwwn {x}")
    if fa_ren or fa_new or fa_mod:
        c("fcaliases")
        for o, w in fa_ren:
            L.append(f"fcalias rename {o['name']} {w['name']} vsan {v}")
        for a in fa_new:
            L.append(f"fcalias name {a['name']} vsan {v}")
            L += [f"  member pwwn {x}" for x in a["wwpns"]]
            L.append("exit")
        for aid, lines in fa_mod.items():
            L.append(f"fcalias name {d.aname(aid)} vsan {v}")
            L += lines
            L.append("exit")
    if d.z_ren or d.c_ren:
        c("Renames")
        for oz, wz in d.z_ren:
            L.append(f"zone rename {oz['name']} {wz['name']} vsan {v}")
        for oc, wc in d.c_ren:
            L.append(f"zoneset rename {oc['name']} {wc['name']} vsan {v}")
    if d.z_new or d.z_add or d.z_rm:
        c("Zones")
        for z in d.z_new:
            L.append(f"zone name {z['name']} vsan {v}")
            L += [f"  member {ref(m)}" for m in z["members"]]
            L.append("exit")
        for zid in list(dict.fromkeys(list(d.z_add) + list(d.z_rm))):
            L.append(f"zone name {d.zname(zid)} vsan {v}")
            L += [f"  member {ref(m)}" for m in d.z_add.get(zid, [])]
            L += [f"  no member {ref(m)}" for m in d.z_rm.get(zid, [])]
            L.append("exit")
    if d.c_new or d.c_add or d.c_rm:
        c("Zonesets")
        for cfg in d.c_new:
            L.append(f"zoneset name {cfg['name']} vsan {v}")
            L += [f"  member {d.zname(z)}" for z in cfg["zones"]]
            L.append("exit")
        for cid in list(dict.fromkeys(list(d.c_add) + list(d.c_rm))):
            L.append(f"zoneset name {d.cname(cid)} vsan {v}")
            L += [f"  member {d.zname(z)}" for z in d.c_add.get(cid, [])]
            L += [f"  no member {d.zname(z)}" for z in d.c_rm.get(cid, [])]
            L.append("exit")
    if d.z_del or fa_del or d.c_del:
        c("Deletions")
        for z in d.z_del:
            L.append(f"no zone name {z['name']} vsan {v}")
        for a in fa_del:
            L.append(f"no fcalias name {a['name']} vsan {v}")
        for cfg in d.c_del:
            L.append(f"no zoneset name {cfg['name']} vsan {v}")
    if da_del:
        c("Device-alias deletions (after zones stop using them)")
        L.append("device-alias database")
        L += [f"  no device-alias name {a['name']}" for a in da_del]
        L.append("exit")
        L.append("device-alias commit")
    zoning_changed = any([d.z_new, d.z_add, d.z_rm, d.z_del, d.z_ren, d.c_new, d.c_add, d.c_rm, d.c_ren,
                          d.c_del, d.active_changed, fa_new, fa_mod, fa_ren, fa_del])
    if d.w.get("active") and (d.touches_active() or d.active_changed):
        c("Activate")
        L.append(f"zoneset activate name {d.cname(d.w['active'])} vsan {v}")
    if d.w.get("zone_mode") == "enhanced" and zoning_changed:
        L.append(f"zone commit vsan {v}")
    L.append("end")
    L.append("copy running-config startup-config")
    return L


def make_plan(orig: dict, work: dict, comments: bool = True) -> dict:
    d = Diff(orig, work)
    d.validate()
    pch, pcfg, pexe, pwarn = port_changes(work["platform"], work.get("ports_pending") or {})
    warnings = d.warnings + pwarn
    if d.empty and not pch:
        return {"changes": [], "commands": "", "warnings": warnings, "count": 0}
    if not d.empty:
        d.describe()
    changes = d.changes + pch
    sw = work["switch"]
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if work["platform"] == "fos":
        body = render_fos(d) if not d.empty else []
        head = [f"# Brocade FOS change for {sw.get('name')} ({sw.get('host') or 'no IP'}) generated {when}",
                "# Review, then paste line by line. cfgenable, cfgsave and some port commands ask for confirmation (y)."] if comments else []
        # FOS shell does not accept comment lines; keep header separate.
        return {"changes": changes, "header": head, "commands": "\n".join(body + pcfg) + "\n", "warnings": warnings,
                "count": len(changes)}
    lines = render_nxos(d, comments) if not d.empty else ["configure terminal", "end", "copy running-config startup-config"]
    if pcfg or pexe:
        i = len(lines) - 2  # before "end"
        extra = (["! Ports"] if comments and pcfg else []) + pcfg
        lines = lines[:i] + extra + lines[i:]
        if pexe:
            j = lines.index("end") + 1
            lines = lines[:j] + pexe + lines[j:]
    head = [f"! Cisco MDS change for {sw.get('name')} ({sw.get('host') or 'no IP'}) VSAN {work['vsan']} generated {when}",
            "! Review before pasting. zoneset activate may ask for confirmation."] if comments else []
    return {"changes": changes, "header": [], "commands": "\n".join(head + lines) + "\n", "warnings": warnings,
            "count": len(changes)}


# ------------------------------------------------------------------ port changes
import re as _re  # noqa: E402

SPEEDS = ["auto", "1", "2", "4", "8", "10", "16", "32", "64"]
FOS_MODES = {"auto": "No lock (auto)", "F": "Locked F_Port", "G": "Locked G_Port", "noE": "E_Port disabled"}
NX_MODES = ["auto", "F", "E", "Fx", "NP", "SD"]


def _port_name_ok(platform, n):
    return bool(_re.fullmatch(r"[A-Za-z0-9 _.\-/:]{0,64}", n)) if platform == "fos" else \
        bool(_re.fullmatch(r"[^\"'\n]{0,80}", n))


def _sp(v):
    return "auto" if v == "auto" else f"{v}G"


def port_changes(platform: str, pending: dict) -> tuple[list, list, list, list]:
    """Returns (changes, config lines, exec lines, warnings). FOS has no config mode: everything is in config lines."""
    changes, cfg, exe, warns = [], [], [], []

    def natural(k):
        return [int(t) if t.isdigit() else t for t in _re.split(r"(\d+)", k)]

    for pid in sorted(pending, key=natural):
        e = pending[pid]
        P, cur, tgt = e["port"], e.get("current", {}), e.get("target", {})
        tgt = {k: v for k, v in tgt.items() if cur.get(k) != v}
        clear = bool(e.get("clear"))
        if not tgt and not clear:
            continue
        devs = len(e.get("wwpns", []))
        label = f"Port {P}" if platform == "fos" else P
        for k, v in tgt.items():
            if k == "name":
                changes.append(("Ports", f"{label}: {'name' if platform == 'fos' else 'description'} \"{cur.get('name', '')}\" to \"{v}\""))
                if not _port_name_ok(platform, v):
                    warns.append(f"{label}: name \"{v}\" has characters the switch may reject.")
            elif k == "enabled":
                changes.append(("Ports", f"{label}: {'enable' if v else 'disable'}"))
                if not v and devs:
                    warns.append(f"{label}: {devs} logged-in device{'s' if devs > 1 else ''} will lose this path when the port is disabled.")
            elif k == "persistent":
                changes.append(("Ports", f"{label}: persistent disable {'on' if v else 'off'}"))
            elif k == "speed":
                changes.append(("Ports", f"{label}: speed {_sp(cur.get('speed', 'auto'))} to {_sp(v)}"))
                if v not in SPEEDS:
                    warns.append(f"{label}: speed {v} is not a standard FC speed.")
            elif k == "mode":
                names = FOS_MODES if platform == "fos" else {m: m for m in NX_MODES}
                changes.append(("Ports", f"{label}: port type {names.get(cur.get('mode', 'auto'), cur.get('mode'))} to {names.get(v, v)}"))
            elif k == "vsan":
                changes.append(("Ports", f"{label}: VSAN {cur.get('vsan')} to {v}"))
                if not (isinstance(v, int) and 1 <= v <= 4094):
                    warns.append(f"{label}: VSAN must be 1 to 4094.")
            else:
                nice = {"trunk": "trunking" if platform == "fos" else "trunk mode", "npiv": "NPIV"}.get(k, k)
                fmt = lambda x: ("on" if x else "off") if isinstance(x, bool) else str(x)  # noqa: E731
                changes.append(("Ports", f"{label}: {nice} {fmt(cur.get(k))} to {fmt(v)}"))
        if clear:
            changes.append(("Ports", f"{label}: clear counters"))
        enabled_after = tgt.get("enabled", cur.get("enabled", True))
        bounce = enabled_after and cur.get("enabled", True) and any(k in tgt for k in ("speed", "mode", "trunk", "vsan"))
        if bounce and devs:
            warns.append(f"{label}: changing speed, type, trunking or VSAN resets the link; {devs} logged-in device{'s' if devs > 1 else ''} will briefly lose this path.")

        if platform == "fos":
            if "name" in tgt:
                cfg.append(f'portname {P} -n "{tgt["name"]}"')
            if "speed" in tgt:
                cfg.append(f"portcfgspeed {P} {0 if tgt['speed'] == 'auto' else tgt['speed']}")
            if "mode" in tgt:
                old = cur.get("mode", "auto")
                cfg += {"F": [f"portcfgfport {P} 0"], "G": [f"portcfggport {P} 0"], "noE": [f"portcfgeport {P} 1"]}.get(old, [])
                cfg += {"F": [f"portcfgfport {P} 1"], "G": [f"portcfggport {P} 1"], "noE": [f"portcfgeport {P} 0"]}.get(tgt["mode"], [])
            if "npiv" in tgt:
                cfg.append(f"portcfgnpivport --{'enable' if tgt['npiv'] else 'disable'} {P}")
            if "trunk" in tgt:
                cfg.append(f"portcfgtrunkport {P} {1 if tgt['trunk'] else 0}")
            was_p = cur.get("persistent", False)
            want_p = tgt.get("persistent", was_p)
            if "enabled" in tgt and tgt["enabled"]:
                cfg.append(f"portcfgpersistentenable {P}" if was_p else f"portenable {P}")
            elif "enabled" in tgt and not tgt["enabled"]:
                cfg.append(f"portcfgpersistentdisable {P}" if want_p else f"portdisable {P}")
            elif "persistent" in tgt and not enabled_after:
                cfg.append(f"portcfgpersistentdisable {P}" if want_p else f"portcfgpersistentenable {P}")
                if not want_p:
                    cfg.append(f"portdisable {P}")
            if clear:
                cfg.append(f"portstatsclear {P}")
        else:
            block = []
            if bounce:
                block.append("  shutdown")
            if "name" in tgt:
                block.append(f"  switchport description {tgt['name']}" if tgt["name"] else "  no switchport description")
            if "speed" in tgt:
                block.append("  switchport speed auto" if tgt["speed"] == "auto" else f"  switchport speed {int(tgt['speed']) * 1000}")
            if "mode" in tgt:
                block.append(f"  switchport mode {tgt['mode']}")
            if "trunk" in tgt:
                block.append(f"  switchport trunk mode {tgt['trunk']}")
            if "enabled" in tgt:
                block.append("  no shutdown" if tgt["enabled"] else "  shutdown")
            elif bounce and "vsan" not in tgt:
                block.append("  no shutdown")
            if block:
                cfg += [f"interface {P}"] + block + ["exit"]
            if "vsan" in tgt:
                cfg += ["vsan database", f"  vsan {tgt['vsan']} interface {P}", "exit"]
                if bounce:
                    cfg += [f"interface {P}", "  no shutdown", "exit"]
            if clear:
                exe.append(f"clear counters interface {P}")
    return changes, cfg, exe, warns
