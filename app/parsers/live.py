"""Parsers for live port data.

FOS:   switchshow (state, speed, port type, single login), nsshow (pWWN -> Port Index, NPIV logins),
       porterrshow (frames TX/RX and error counters).
NX-OS: show interface brief (state, speed, mode, VSAN), show flogi database (pWWN per interface),
       show interface counters brief (5 minute rate in MB/s and total frames).
"""
from __future__ import annotations

import re

from ..common import norm_wwn

_WWN = r"[0-9a-f]{2}(?::[0-9a-f]{2}){7}"
_SUF = {"k": 1e3, "m": 1e6, "g": 1e9, "t": 1e12}


def num(s: str) -> float | None:
    """porterrshow abbreviates large values: 1.2m, 3.4g."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmgt]?)", s.strip().lower())
    if not m:
        return None
    return float(m.group(1)) * _SUF.get(m.group(2), 1)


# ------------------------------------------------------------------ FOS
def fos_switchshow_ports(text: str) -> list[dict]:
    ports = []
    slot_layout = False
    for raw in text.replace("\r", "").split("\n"):
        if re.match(r"^\s*Index\s+Slot\s+Port", raw):
            slot_layout = True
            continue
        if slot_layout:
            m = re.match(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+([0-9a-f-]{6})\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*(.*)$", raw, re.I)
            if not m:
                continue
            idx, slot, port, addr, media, speed, state, proto, rest = m.groups()
            name = f"{slot}/{port}"
        else:
            m = re.match(r"^\s*(\d+)\s+(\d+)\s+([0-9a-f-]{6})\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*(.*)$", raw, re.I)
            if not m:
                continue
            idx, port, addr, media, speed, state, proto, rest = m.groups()
            name = port
        rest = rest.strip()
        first = rest.split()[0] if rest else ""
        ptype = first if first.endswith("-Port") else ""
        w = re.search(_WWN, rest, re.I)
        npiv = re.search(r"(\d+)\s+NPIV", rest)
        ports.append({
            "id": idx, "port": name, "state": state, "online": state.lower() == "online",
            "speed": speed.lstrip("N") if speed.upper().startswith("N") else speed, "type": ptype,
            "wwpns": [w.group(0).lower()] if (w and ptype == "F-Port") else [],
            "peer": w.group(0).lower() if (w and ptype not in ("F-Port", "")) else "",
            "note": (rest[len(ptype):].replace(w.group(0), "") if w else rest[len(ptype):]).strip() if ptype else rest,
            "npiv": int(npiv.group(1)) if npiv else 0, "vsan": None, "address": addr, "media": media,
            "admin_disabled": "disabled" in rest.lower(), "persistent_disabled": "persistent" in rest.lower(),
        })
    return ports


def fos_ns_ports(text: str) -> dict[str, list[str]]:
    """nsshow -> {port index: [pwwn, ...]} using the Port Index field."""
    out: dict[str, list[str]] = {}
    cur = None
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*N[LV]?\s+[0-9a-f]{6};\s*\d+;\s*(" + _WWN + r");", raw, re.I)
        if m:
            cur = m.group(1).lower()
            continue
        m = re.match(r"^\s*Port Index:\s*(\d+)", raw, re.I)
        if m and cur:
            out.setdefault(m.group(1), [])
            if cur not in out[m.group(1)]:
                out[m.group(1)].append(cur)
    return out


def fos_porterrshow(text: str) -> dict[str, dict]:
    out = {}
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*(\d+(?:/\d+)?):\s+(.*)$", raw)
        if not m:
            continue
        cols = m.group(2).split()
        if len(cols) < 13:
            continue
        v = [num(c) for c in cols]
        out[m.group(1)] = {"tx_frames": v[0], "rx_frames": v[1], "enc_in": v[2], "crc": v[3], "enc_out": v[8],
                           "disc_c3": v[9], "link_fail": v[10], "loss_sync": v[11], "loss_sig": v[12]}
    return out


def fos_portstats(text: str) -> dict:
    """portstatsshow -> exact counters. stat_wtx/wrx are 4-byte words."""
    v = {k: int(n) for k, n in re.findall(r"^\s*(stat_[wf][tr]x)\s+(\d+)", text, re.M)}
    out = {}
    if "stat_wtx" in v:
        out["tx_bytes"] = v["stat_wtx"] * 4
    if "stat_wrx" in v:
        out["rx_bytes"] = v["stat_wrx"] * 4
    out["tx_frames"], out["rx_frames"] = v.get("stat_ftx"), v.get("stat_frx")
    return out


_PCFG_ROWS = {
    "speed": "speed", "trunk port": "trunk", "locked g_port": "g_lock", "locked f_port": "f_lock",
    "disabled e_port": "e_disabled", "npiv capability": "npiv", "persistent disable": "pdisable",
    "locked e_port": "e_lock",
}


def fos_portcfgshow(text: str) -> dict[str, dict]:
    """portcfgshow table -> {port name: {speed, trunk, g_lock, f_lock, e_disabled, npiv, pdisable}}."""
    out: dict[str, dict] = {}
    ports: list[str] = []
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*Ports of Slot\s+(\d+)\s+(.*)$", raw)
        if m:
            slot = m.group(1)
            nums = m.group(2).split()
            ports = [n if slot == "0" else f"{slot}/{n}" for n in nums]
            for pn in ports:
                out.setdefault(pn, {})
            continue
        if not ports or not raw.strip() or raw.strip().startswith("-"):
            continue
        toks = raw.split()
        if len(toks) <= len(ports):
            continue
        vals = toks[-len(ports):]
        label = " ".join(toks[:-len(ports)]).strip().lower()
        key = _PCFG_ROWS.get(label)
        if not key:
            continue
        for pn, v in zip(ports, vals):
            if key == "speed":
                out[pn]["speed"] = "auto" if v.upper() == "AN" else v.upper().rstrip("G")
            else:
                out[pn][key] = v.upper() == "ON"
    return out


def fos_portnames(text: str) -> dict[str, str]:
    out = {}
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*port\s+(\d+(?:/\d+)?):\s*(.*?)\s*$", raw, re.I)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def fos_live(outputs: dict[str, str]) -> list[dict]:
    ports = fos_switchshow_ports(outputs.get("switchshow", ""))
    ns = fos_ns_ports(outputs.get("nsshow", ""))
    err = fos_porterrshow(outputs.get("porterrshow", ""))
    pcfg = fos_portcfgshow(outputs.get("portcfgshow", ""))
    names = fos_portnames(outputs.get("portname", ""))
    for p in ports:
        c = pcfg.get(p["port"], {})
        mode = "F" if c.get("f_lock") else "G" if c.get("g_lock") else "E" if c.get("e_lock") else \
            "noE" if c.get("e_disabled") else "auto"
        p["cfg"] = {"name": names.get(p["port"], ""), "enabled": not (p["admin_disabled"] or c.get("pdisable")),
                    "persistent": bool(c.get("pdisable")) or p["persistent_disabled"],
                    "speed": c.get("speed", "auto"), "mode": mode,
                    "npiv": c.get("npiv", True), "trunk": c.get("trunk", True)}
        if p["wwpns"]:
            p["note"] = ""
        for w in ns.get(p["id"], []):
            if w not in p["wwpns"]:
                p["wwpns"].append(w)
        p["npiv"] = max(p["npiv"], len(p["wwpns"]) - 1) if p["wwpns"] else p["npiv"]
        e = err.get(p["id"]) or err.get(p["port"]) or {}
        st = fos_portstats(outputs.get(f"portstatsshow {p['port']}", ""))
        if st.get("tx_bytes") is not None:
            p["tx"], p["rx"], p["unit"] = st["tx_bytes"], st.get("rx_bytes"), "bytes"
        else:  # porterrshow totals are rounded; good for a glance, not for rates
            p["tx"], p["rx"], p["unit"] = e.get("tx_frames"), e.get("rx_frames"), "frames"
        p["tx_rate"] = p["rx_rate"] = None  # computed by the browser between refreshes
        p["errors"] = {k: e[k] for k in ("crc", "enc_out", "disc_c3", "link_fail", "loss_sync", "loss_sig")
                       if e.get(k)}
    return ports


# ------------------------------------------------------------------ NX-OS
def nx_interface_brief(text: str) -> list[dict]:
    ports = []
    for raw in text.replace("\r", "").split("\n"):
        cols = raw.split()
        if not cols or not re.match(r"^(fc\d+/\d+|port-channel\s*\d+|vfc\S+)$", cols[0]):
            continue
        if len(cols) < 5:
            continue
        name, vsan, _amode, _atrunk, status = cols[:5]
        sfp = cols[5] if len(cols) > 5 else ""
        mode = cols[6] if len(cols) > 6 else ""
        speed = cols[7] if len(cols) > 7 else ""
        pc = cols[8] if len(cols) > 8 else ""
        ports.append({
            "id": name, "port": name, "state": status, "online": status.lower() in ("up", "trunking"),
            "speed": "" if speed in ("--", "") else f"{speed}G", "type": "" if mode == "--" else f"{mode}-Port",
            "vsan": int(vsan) if vsan.isdigit() else None, "wwpns": [], "peer": "",
            "note": f"port-channel {pc}" if pc not in ("", "--") else "", "npiv": 0,
            "media": "" if sfp == "--" else sfp, "address": "",
        })
    return ports


def nx_flogi(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*(fc\d+/\d+|port-channel\s*\d+|vfc\S+)\s+(\d+)\s+(0x[0-9a-f]+)\s+(" + _WWN + ")", raw, re.I)
        if m:
            out.setdefault(m.group(1), []).append(m.group(4).lower())
    return out


def nx_counters(text: str) -> dict[str, dict]:
    out = {}
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*(fc\d+/\d+|port-channel\s*\d+|vfc\S+)\s+(\d+(?:\.\d+)?)\s+(\d+)\s+(\d+(?:\.\d+)?)\s+(\d+)", raw)
        if m:
            out[m.group(1)] = {"rx_rate": float(m.group(2)), "rx": int(m.group(3)),
                               "tx_rate": float(m.group(4)), "tx": int(m.group(5))}
    return out


def nx_run_interfaces(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    cur = None
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^interface\s+(fc\d+/\d+|port-channel\s*\d+|vfc\S+)\s*$", raw)
        if m:
            cur = out.setdefault(m.group(1), {"speed": "auto", "mode": "auto", "name": "", "enabled": None,
                                              "trunk": "on"})
            continue
        if not raw.startswith((" ", "\t")):
            cur = None
            continue
        if cur is None:
            continue
        st = raw.strip()
        if (m := re.match(r"^switchport speed (\S+)", st)):
            v = m.group(1)
            cur["speed"] = "auto" if v.startswith("auto") else str(int(v) // 1000) if v.isdigit() else v
        elif (m := re.match(r"^switchport mode (\S+)", st)):
            cur["mode"] = m.group(1)
        elif (m := re.match(r"^switchport description (.*)$", st)):
            cur["name"] = m.group(1).strip()
        elif (m := re.match(r"^switchport trunk mode (\S+)", st)):
            cur["trunk"] = m.group(1)
        elif st == "no shutdown":
            cur["enabled"] = True
        elif st == "shutdown":
            cur["enabled"] = False
    return out


def nx_flogi_fcid(text: str) -> dict[str, str]:
    return {m.group(2).lower(): m.group(1) for m in
            re.finditer(r"^\s*\S+\s+\d+\s+(0x[0-9a-f]+)\s+(" + _WWN + ")", text, re.I | re.M)}


def nx_live(outputs: dict[str, str], vsan: int | None = None) -> list[dict]:
    ports = nx_interface_brief(outputs.get("show interface brief", ""))
    fl = nx_flogi(outputs.get("show flogi database", ""))
    ct = nx_counters(outputs.get("show interface counters brief", ""))
    rc = nx_run_interfaces(outputs.get("show running-config interface", ""))
    fcids = nx_flogi_fcid(outputs.get("show flogi database", ""))
    for p in ports:
        c = rc.get(p["id"], {"speed": "auto", "mode": "auto", "name": "", "enabled": None, "trunk": "on"})
        if c["enabled"] is None:
            c["enabled"] = p["online"] or "admin" not in p["state"].lower()
        c["vsan"] = p["vsan"]
        p["cfg"] = c
        p["fcids"] = {}
        p["wwpns"] = [norm_wwn(w) for w in fl.get(p["id"], [])]
        p["fcids"] = {w: fcids.get(w, "") for w in p["wwpns"]}
        p["npiv"] = max(0, len(p["wwpns"]) - 1)
        c = ct.get(p["id"], {})
        p["tx"], p["rx"] = c.get("tx"), c.get("rx")
        p["tx_rate"], p["rx_rate"] = c.get("tx_rate"), c.get("rx_rate")
        p["unit"] = "frames"
        p["errors"] = {}
    return ports


def build_live(platform: str, outputs: dict[str, str]) -> dict:
    ports = fos_live(outputs) if platform == "fos" else nx_live(outputs)
    return {"platform": platform, "ports": ports,
            "rate_source": "switch" if platform == "nxos" else "browser",
            "rate_unit": "MB/s"}
