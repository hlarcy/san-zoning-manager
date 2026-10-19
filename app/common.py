"""Shared helpers: WWPN handling, vendor hints, naming rules."""
from __future__ import annotations

import re

WWN_RE = re.compile(r"^([0-9a-f]{2}:){7}[0-9a-f]{2}$")
FOS_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
NX_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-$^]{0,63}$")


def norm_wwn(s: str) -> str | None:
    s = str(s).strip().lower()
    if re.fullmatch(r"[0-9a-f]{16}", s):
        s = ":".join(s[i:i + 2] for i in range(0, 16, 2))
    return s if WWN_RE.match(s) else None


# (regex on lower-case colon WWPN, vendor label, role hint or None)
OUI = [
    (r"^2[0-9a-f]:[0-9a-f]{2}:00:02:ac", "HPE 3PAR / Primera / Alletra 9000", "target"),
    (r"^2[0-9a-f]:70:00:c0:ff", "HPE MSA", "target"),
    (r"^50:0a:09", "NetApp", "target"),
    (r"^20:[0-9a-f]{2}:00:a0:98", "NetApp ONTAP LIF", "target"),
    (r"^52:4a:93", "Pure Storage", "target"),
    (r"^50:06:01:6", "Dell EMC VNX / Unity", "target"),
    (r"^50:00:09:7", "Dell EMC VMAX / PowerMax", "target"),
    (r"^50:00:d3:1", "Dell Compellent / SC", "target"),
    (r"^50:05:07:68", "IBM SVC / FlashSystem", "target"),
    (r"^50:05:07:63", "IBM DS8000", "target"),
    (r"^50:06:0e:80", "Hitachi VSP / HPE XP", "target"),
    (r"^56:c9:ce:9", "HPE Nimble / Alletra 6000", "target"),
    (r"^10:00:00:00:c9", "Emulex HBA", "initiator"),
    (r"^10:00:00:90:fa", "Emulex HBA", "initiator"),
    (r"^10:00:00:10:9b", "Emulex / Broadcom HBA", "initiator"),
    (r"^10:00:00:62:0b", "Broadcom HBA", "initiator"),
    (r"^21:00:00:24:ff", "QLogic / Marvell HBA", "initiator"),
    (r"^21:00:00:0e:1e", "QLogic / Marvell HBA", "initiator"),
    (r"^21:00:00:1b:32", "QLogic HBA", "initiator"),
    (r"^21:00:f4:e9:d4", "QLogic / Marvell HBA", "initiator"),
    (r"^2[0-9a-f]:00:00:25:b5", "Cisco UCS vHBA", "initiator"),
    (r"^50:06:0b:00:00:c2", "HPE Virtual Connect", "initiator"),
    (r"^50:01:43:80", "HPE (HBA or tape)", None),
]
_OUI = [(re.compile(p), n, r) for p, n, r in OUI]

NAME_T = re.compile(
    r"3par|primera|alletra|nimble|netapp|ontap|pure|flasharray|vnx|unity|vmax|powermax|pmax|powerstore|"
    r"xtremio|compellent|storwize|flashsystem|v[57]000|hitachi|storeonce|datadomain|quantum|scalar|tape|"
    r"\blto\d*\b|\b(msa\d*|eva\d*|fas\d*|aff\w*|svc\d*|ds8\w*|xiv\w*|vsp\w*|xp\d+|msl\w*|dd\d+|array\w*|"
    r"stor\w*|ctrl\w*|ctl\d*|sp[ab]|tgt\w*|target\w*)\b"
)
NAME_I = re.compile(
    r"\b(hba\d*|esx\w*|vmh\w*|host\w*|srv\w*|server\w*|hyperv\w*|hv\d+\w*|win\w*|lnx\w*|linux\w*|aix\w*|"
    r"blade\w*|bl\d+\w*|dl\d+\w*|sy\d+\w*|synergy\w*|ucs\w*|vhba\w*|sql\w*|ora\w*|exch\w*|vdi\w*|bkp\w*|"
    r"backup\w*|init\w*)\b"
)


def vendor_of(w: str) -> tuple[str, str | None]:
    for rx, name, role in _OUI:
        if rx.match(w):
            return name, role
    return "", None


def name_role(n: str) -> str | None:
    s = re.sub(r"[_\-.$^]+", " ", n.lower())
    t, i = bool(NAME_T.search(s)), bool(NAME_I.search(s))
    if t and not i:
        return "target"
    if i and not t:
        return "initiator"
    return None


def name_stem(n: str) -> str:
    """ESX01_HBA0 -> ESX01, HP3PAR01_N0S1P1 -> HP3PAR01, UCS_ESXB01_VHBA_A -> UCS_ESXB01."""
    seg = [x for x in re.split(r"[_\-.]+", n) if x]
    if not seg:
        return n
    if not re.search(r"\d", seg[0]) and len(seg) > 1:
        return f"{seg[0]}_{seg[1]}"
    return seg[0]


def valid_name(platform: str, n: str) -> bool:
    return bool((FOS_NAME_RE if platform == "fos" else NX_NAME_RE).match(n))
