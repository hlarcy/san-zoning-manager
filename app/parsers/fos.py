"""Brocade FOS v8 parsers: cfgshow / zoneshow / alishow output, configupload [Zoning], nsshow / nscamshow."""
from __future__ import annotations

import re

_PEER_PROP = re.compile(r"^00:02:00:00:00:([0-9a-f]{2}):([0-9a-f]{2}):([0-9a-f]{2})$", re.I)


def _split(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;\s]+", s) if x.strip()]


def _peer(members: list[str]):
    m = _PEER_PROP.match(members[0]) if members else None
    if not m:
        return members, None
    rest = members[1:]
    n = int(m.group(2), 16)
    guessed = not (1 <= n < len(rest))
    if guessed:
        n = 1
    return rest, {"principal": rest[:n], "peers": rest[n:], "guessed": guessed}


def _db():
    return {"aliases": {}, "zones": {}, "cfgs": {}, "effective": None, "defzone": None, "format": None}


def _parse_upload(t: str, db: dict) -> None:
    eff = False
    for raw in t.split("\n"):
        line = raw.strip()
        if not line:
            continue
        h = re.match(r"^\[(.+)\]$", line)
        if h:
            eff = "effective" in h.group(1).lower()
            continue
        m = re.match(r"^(cfg|zone|alias)\.([^:]+):(.*)$", line)
        if m:
            kind, name, body = m.groups()
            mem = [x.strip() for x in body.split(";") if x.strip()]
            if eff:
                if kind == "cfg" and not db["effective"]:
                    db["effective"] = name
                continue
            if kind == "cfg":
                db["cfgs"].setdefault(name, mem)
            elif kind == "zone" and name not in db["zones"]:
                members, peer = _peer(mem)
                db["zones"][name] = {"members": members, "peer": peer}
            elif kind == "alias":
                db["aliases"].setdefault(name, mem)
            continue
        m = re.match(r"^enable:\s*(\S+)", line)
        if m:
            db["effective"] = m.group(1)
            continue
        m = re.match(r"^defzone:\s*(\S+)", line)
        if m:
            db["defzone"] = m.group(1)


def _parse_cfgshow(t: str, db: dict) -> None:
    sect = "defined"
    cur: dict | None = None
    pmode = None

    def flush():
        nonlocal cur
        if cur and sect == "defined" and cur["kind"] != "skip":
            k, n = cur["kind"], cur["name"]
            if k == "cfg":
                db["cfgs"].setdefault(n, cur["mem"])
            elif k == "alias":
                db["aliases"].setdefault(n, cur["mem"])
            elif k == "zone" and n not in db["zones"]:
                if cur["peer"]:
                    db["zones"][n] = {"members": cur["pr"] + cur["pe"],
                                      "peer": {"principal": cur["pr"], "peers": cur["pe"], "guessed": False}}
                else:
                    members, peer = _peer(cur["mem"])
                    db["zones"][n] = {"members": members, "peer": peer}
        cur = None

    for raw in t.split("\n"):
        s = raw.replace("\t", " ").strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("defined configuration"):
            flush(); sect = "defined"; continue
        if low.startswith("effective configuration"):
            flush(); sect = "effective"; continue
        if low.startswith("no effective configuration"):
            flush(); continue
        if re.search(r":\S*[>#]\s*\S*\s*$", s) and not re.match(r"^(cfg|zone|alias):", s):
            flush(); continue  # switch prompt
        m = re.match(r"^(cfg|zone|alias):\s*(\S+)\s*(.*)$", s)
        if m:
            flush()
            if sect == "effective":
                if m.group(1) == "cfg":
                    db["effective"] = m.group(2)
                cur = {"kind": "skip"}
                continue
            cur = {"kind": m.group(1), "name": m.group(2), "mem": _split(m.group(3)),
                   "pr": [], "pe": [], "peer": False}
            pmode = None
            continue
        if not cur or cur["kind"] == "skip":
            continue
        if low.startswith("property member:"):
            cur["peer"] = True; continue
        if low.startswith("created by:"):
            continue
        if low.startswith("principal member"):
            cur["peer"] = True; pmode = "pr"
            rest = s.split(":", 1)[1] if ":" in s else ""
            cur["pr"] += _split(rest)
            continue
        if low.startswith("peer member"):
            cur["peer"] = True; pmode = "pe"
            rest = s.split(":", 1)[1] if ":" in s else ""
            cur["pe"] += _split(rest)
            continue
        toks = _split(s)
        if cur["peer"] and pmode:
            cur[pmode] += toks
        else:
            cur["mem"] += toks
    flush()


def parse_zoning(text: str) -> dict:
    db = _db()
    t = text.replace("\r", "")
    if re.search(r"^\s*(cfg|zone|alias)\.[^:\s]+:", t, re.M):
        db["format"] = "configupload"
        _parse_upload(t, db)
    elif re.search(r"^\s*(cfg|zone|alias):\s*\S", t, re.M):
        db["format"] = "cfgshow"
        _parse_cfgshow(t, db)
    return db


def parse_ns(text: str) -> dict:
    """nsshow / nscamshow -> {wwpn: {role, fc4, symb, node}}"""
    out: dict[str, dict] = {}
    cur = None
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*N[LV]?\s+([0-9a-f]{6});\s*\d+;\s*([0-9a-f]{2}(?::[0-9a-f]{2}){7});", raw, re.I)
        if m:
            cur = m.group(2).lower()
            out.setdefault(cur, {"role": None, "fc4": "", "symb": "", "node": ""})
            continue
        if cur is None:
            continue
        s = raw.strip()
        m = re.match(r"^Device type:\s*(.*)$", s, re.I)
        if m:
            d = m.group(1).lower()
            if "initiator" in d and "target" in d:
                out[cur]["role"] = "both"
            elif "initiator" in d:
                out[cur]["role"] = "initiator"
            elif "target" in d:
                out[cur]["role"] = "target"
            continue
        m = re.match(r"^FC4s:\s*(.*)$", s, re.I)
        if m:
            out[cur]["fc4"] = m.group(1).strip(); continue
        m = re.match(r"^NodeSymb:\s*\[\d+\]\s*\"(.*)\"", s, re.I)
        if m:
            out[cur]["node"] = m.group(1); continue
        m = re.match(r"^PortSymb:\s*\[\d+\]\s*\"(.*)\"", s, re.I)
        if m:
            out[cur]["symb"] = m.group(1); continue
    return out


def parse_switchname(text: str) -> str:
    m = re.search(r"^switchName:\s*(\S+)", text, re.M)
    return m.group(1) if m else ""


def parse_version(text: str) -> str:
    m = re.search(r"Fabric OS:\s*(\S+)", text)
    return m.group(1) if m else ""
