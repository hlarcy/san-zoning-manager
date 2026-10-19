import copy

import pytest
from fastapi.testclient import TestClient

from app import demo
from app.main import app
from app.model import build_model
from app.parsers import fos, nxos
from app.plan import make_plan
from app.ssh import PullError, _allowed, _run


@pytest.fixture
def fos_model():
    return build_model("fos", demo.FOS)


@pytest.fixture
def nx_model():
    return build_model("nxos", demo.NXOS)


# ---------------------------------------------------------------- parsing
def test_fos_cfgshow_parses_defined_only():
    db = fos.parse_zoning(demo.FOS["cfgshow"])
    assert db["format"] == "cfgshow"
    assert db["effective"] == "FAB_A_PROD"
    assert len(db["cfgs"]["FAB_A_PROD"]) == 8
    assert db["zones"]["z_SQL01_HP3PAR01"]["members"] == ["SQL01_P0", "SQL01_P1", "HP3PAR01_N0S1P2", "HP3PAR01_N1S1P2"]
    # the effective section's resolved WWNs must not overwrite the defined zone
    assert db["zones"]["z_BKP01_MSL6480"]["members"][0] == "BKP01_HBA0"


def test_fos_configupload_and_peer_zone():
    t = ("[Zoning]\ncfg.C:pz\nzone.pz:00:02:00:00:00:03:02:01;T1;T2;H1\n"
         "alias.T1:20:01:00:02:ac:02:51:9e\nalias.T2:21:01:00:02:ac:02:51:9e\nalias.H1:20:00:00:25:b5:a0:00:01\nenable:C\n")
    db = fos.parse_zoning(t)
    assert db["zones"]["pz"]["peer"]["principal"] == ["T1", "T2"]
    m = build_model("fos", {"paste": t})
    z = m["zones"][0]
    assert z["peer"] and [x["principal"] for x in z["members"]] == [True, True, False]


def test_fos_cfgshow_peer_zone_block():
    t = """Defined configuration:
 cfg:\tC\tpz1
 zone:\tpz1
\t\tProperty Member: 00:02:00:00:00:03:01:01
\t\tCreated by: User
\t\tPrincipal Member(s):
\t\t\t\tT1
\t\tPeer Member(s):
\t\t\t\tH1; H2
 alias:\tT1\t20:01:00:02:ac:02:51:9e
"""
    db = fos.parse_zoning(t)
    assert db["zones"]["pz1"]["peer"] == {"principal": ["T1"], "peers": ["H1", "H2"], "guessed": False}


def test_nsshow_roles():
    ns = fos.parse_ns(demo.FOS["nsshow"])
    assert ns["10:00:00:10:9b:4a:11:05"]["role"] == "initiator"
    assert ns["20:11:00:02:ac:01:2a:3b"]["role"] == "target"


def test_nxos_running_prefers_full_zone_database():
    r = nxos.parse_running(demo.NXOS["show running-config"])
    v = r["vsans"][10]
    assert len(v["zones"]["SZ_HP3PAR01"]) == 7  # full section, not the 2-member active copy
    assert v["active"] == "ZS_FABRIC_A" and v["smart"] and v["mode"] == "enhanced"
    assert r["da_mode"] == "enhanced" and r["vsan_names"][10] == "FABRIC_A"
    assert v["zones"]["Z_RCFC_HP3PAR01_HP3PAR02"][0]["tag"] == "both"


def test_fcns_roles():
    f = nxos.parse_fcns(demo.NXOS["show fcns database"])
    assert f[10]["20:24:00:02:ac:01:2a:3b"]["role"] == "both"
    assert f[20]["21:00:00:24:ff:6b:30:10"]["role"] == "initiator"


def test_groups(fos_model, nx_model):
    g = fos_model["groups"]
    assert g["20:11:00:02:ac:01:2a:3b"] == g["21:12:00:02:ac:01:2a:3b"] == "HP3PAR01"
    assert g["21:00:00:24:ff:7a:10:21"] == "SQL01"
    assert g["10:00:00:10:9b:4a:11:05"] == "esx05"  # unaliased: from NodeSymb
    assert "21:00:00:24:ff:6b:30:10" not in nx_model["devices"]  # VSAN 20 device hidden in VSAN 10


# ---------------------------------------------------------------- planning
def test_no_change_no_commands(fos_model):
    p = make_plan(fos_model, copy.deepcopy(fos_model))
    assert p["count"] == 0 and p["commands"] == ""


def test_fos_rename_create_and_zone(fos_model):
    w = copy.deepcopy(fos_model)
    a = next(a for a in w["aliases"] if a["name"] == "ESX01_HBA0")
    a["name"] = "ESX01_P0"
    w["aliases"].append({"id": "n:1", "name": "ESX05_HBA0", "kind": "alias", "wwpns": ["10:00:00:10:9b:4a:11:05"]})
    w["zones"].append({"id": "n:2", "name": "z_ESX05_HP3PAR01", "peer": False, "members": [
        {"kind": "alias", "ref": "n:1", "tag": None, "principal": False},
        {"kind": "alias", "ref": "a:HP3PAR01_N0S1P1", "tag": None, "principal": False}]})
    next(c for c in w["cfgs"] if c["id"] == w["active"])["zones"].append("n:2")
    z = next(z for z in w["zones"] if z["name"] == "z_SQL01_HP3PAR02")
    z["members"] = [m for m in z["members"] if m["ref"] != "a:SQL01_P1"]
    cmds = make_plan(fos_model, w)["commands"].splitlines()
    assert cmds[0] == 'zoneobjectrename "ESX01_HBA0", "ESX01_P0"'
    assert 'alicreate "ESX05_HBA0", "10:00:00:10:9b:4a:11:05"' in cmds
    assert 'zonecreate "z_ESX05_HP3PAR01", "ESX05_HBA0; HP3PAR01_N0S1P1"' in cmds
    assert 'cfgadd "FAB_A_PROD", "z_ESX05_HP3PAR01"' in cmds
    assert 'zoneremove "z_SQL01_HP3PAR02", "SQL01_P1"' in cmds
    assert cmds[-1] == 'cfgenable "FAB_A_PROD"'
    assert cmds.index('alicreate "ESX05_HBA0", "10:00:00:10:9b:4a:11:05"') < cmds.index(
        'zonecreate "z_ESX05_HP3PAR01", "ESX05_HBA0; HP3PAR01_N0S1P1"')


def test_fos_rename_only_saves(fos_model):
    w = copy.deepcopy(fos_model)
    w["aliases"][0]["name"] = "BKP01_P0"
    cmds = make_plan(fos_model, w)["commands"].splitlines()
    assert cmds == ['zoneobjectrename "BKP01_HBA0", "BKP01_P0"', "cfgsave"]


def test_fos_delete_zone_order(fos_model):
    w = copy.deepcopy(fos_model)
    w["zones"] = [z for z in w["zones"] if z["id"] != "z:z_ESX04_HP3PAR01"]
    for c in w["cfgs"]:
        c["zones"] = [z for z in c["zones"] if z != "z:z_ESX04_HP3PAR01"]
    w["aliases"] = [a for a in w["aliases"] if a["id"] != "a:ESX04_HBA0"]
    cmds = make_plan(fos_model, w)["commands"].splitlines()
    assert cmds.index('cfgremove "FAB_A_PROD", "z_ESX04_HP3PAR01"') < cmds.index('zonedelete "z_ESX04_HP3PAR01"') \
        < cmds.index('alidelete "ESX04_HBA0"') < cmds.index('cfgenable "FAB_A_PROD"')


def test_nxos_smart_changes(nx_model):
    w = copy.deepcopy(nx_model)
    next(a for a in w["aliases"] if a["name"] == "HP3PAR01_N0S1P1")["name"] = "SAN01_N0S1P1"
    w["aliases"].append({"id": "n:1", "name": "ESX04_HBA0", "kind": "device-alias", "wwpns": ["10:00:00:10:9b:4a:11:04"]})
    z = next(z for z in w["zones"] if z["name"] == "SZ_HP3PAR01")
    z["members"].append({"kind": "alias", "ref": "n:1", "tag": "initiator", "principal": False})
    z["members"] = [m for m in z["members"] if m["ref"] != "a:SQL01_P1"]
    z["name"] = "SZ_SAN01"
    cmds = make_plan(nx_model, w)["commands"].splitlines()
    body = [c for c in cmds if not c.startswith("!")]
    assert body[:6] == ["configure terminal", "device-alias database",
                        "  device-alias rename HP3PAR01_N0S1P1 SAN01_N0S1P1",
                        "  device-alias name ESX04_HBA0 pwwn 10:00:00:10:9b:4a:11:04", "exit", "device-alias commit"]
    assert "zone rename SZ_HP3PAR01 SZ_SAN01 vsan 10" in body
    i = body.index("zone name SZ_SAN01 vsan 10")
    assert body[i + 1:i + 4] == ["  member device-alias ESX04_HBA0 initiator",
                                 "  no member device-alias SQL01_P1 initiator", "exit"]
    assert body[-4:] == ["zoneset activate name ZS_FABRIC_A vsan 10", "zone commit vsan 10", "end",
                         "copy running-config startup-config"]


def test_nxos_delete_alias_after_zone(nx_model):
    w = copy.deepcopy(nx_model)
    z = next(z for z in w["zones"] if z["name"] == "SZ_PRIMERA01")
    z["members"] = [m for m in z["members"] if m["ref"] != "a:UCS_ESXB02_VHBA_A"]
    w["aliases"] = [a for a in w["aliases"] if a["id"] != "a:UCS_ESXB02_VHBA_A"]
    body = [c for c in make_plan(nx_model, w)["commands"].splitlines() if not c.startswith("!")]
    assert body.index("  no member device-alias UCS_ESXB02_VHBA_A initiator") < body.index(
        "  no device-alias name UCS_ESXB02_VHBA_A")


def test_validation_warnings(nx_model):
    w = copy.deepcopy(nx_model)
    w["aliases"].append({"id": "n:9", "name": "9bad", "kind": "device-alias", "wwpns": ["20:11:00:02:ac:01:2a:3b"]})
    warns = make_plan(nx_model, w)["warnings"]
    assert any("not valid" in x for x in warns) and any("two device-aliases" in x for x in warns)


# ---------------------------------------------------------------- safety
def test_ssh_allowlist_refuses_config_commands():
    allowed = _allowed(None)
    for bad in ["configure terminal", "cfgenable X", "zone name x vsan 1", "show version; reload"]:
        with pytest.raises(PullError):
            _run(None, bad, allowed, 5)
    assert 'fosexec --fid 128 -cmd "cfgshow"' in _allowed(128)


def test_api_demo_and_plan():
    c = TestClient(app)
    assert c.get("/api/health").json()["push"] is False
    d = c.get("/api/demo/fos").json()["model"]
    w = copy.deepcopy(d)
    w["aliases"][0]["name"] = "BKP01_P0"
    p = c.post("/api/plan", json={"original": d, "working": w}).json()
    assert p["count"] == 1 and "zoneobjectrename" in p["commands"]
    r = c.post("/api/parse", json={"texts": {"paste": demo.NXOS["show running-config"]}, "vsan": 20}).json()
    assert r["model"]["platform"] == "nxos" and r["model"]["vsan"] == 20
    assert c.get("/").status_code in (200, 404)


# ---------------------------------------------------------------- live data
from app.parsers import live as lv  # noqa: E402


def test_fos_switchshow_ports_both_layouts():
    single = """Index Port Address  Media Speed   State       Proto
==================================================
   0   0   010000   id    N16      Online      FC  F-Port  10:00:00:10:9b:4a:11:01
   1   1   010100   id    N16      No_Light    FC
   2   2   010200   id    N32      Online      FC  F-Port  1 N Port + 2 NPIV public
   3   3   010300   id    N32      Online      FC  E-Port  10:00:88:94:71:aa:bb:01 "SW_B" (downstream)
"""
    p = lv.fos_switchshow_ports(single)
    assert [x["online"] for x in p] == [True, False, True, True]
    assert p[0]["wwpns"] == ["10:00:00:10:9b:4a:11:01"] and p[0]["speed"] == "16"
    assert p[2]["npiv"] == 2 and p[2]["wwpns"] == []
    assert p[3]["type"] == "E-Port" and p[3]["wwpns"] == [] and p[3]["peer"] == "10:00:88:94:71:aa:bb:01"
    chassis = """Index Slot Port Address Media  Speed  State     Proto
=======================================================
  64    2    0   014000   id    N32    Online    FC  F-Port  21:00:00:24:ff:7a:10:20
"""
    c = lv.fos_switchshow_ports(chassis)
    assert c[0]["port"] == "2/0" and c[0]["id"] == "64"


def test_fos_live_merges_npiv_and_bytes():
    out = {
        "switchshow": "   2   2   010200   id    N32      Online      FC  F-Port  1 N Port + 1 NPIV public\n",
        "nsshow": (" N    010200;      3;20:00:00:25:b5:a0:00:01;20:00:00:25:b5:a0:00:00; na\n    Port Index: 2\n"
                   " N    010201;      3;20:00:00:25:b5:a0:00:02;20:00:00:25:b5:a0:00:00; na\n    Port Index: 2\n"),
        "porterrshow": "  2:   1.2g   3.4g    0      5      0      0      0      0      7      0      0      0      0      0      0\n",
        "portstatsshow 2": "stat_wtx            \t1000        4-byte words transmitted\nstat_wrx            \t2500        4-byte words received\n",
    }
    p = lv.fos_live(out)[0]
    assert p["wwpns"] == ["20:00:00:25:b5:a0:00:01", "20:00:00:25:b5:a0:00:02"]
    assert p["unit"] == "bytes" and p["tx"] == 4000 and p["rx"] == 10000
    assert p["errors"] == {"crc": 5.0, "enc_out": 7.0}
    assert lv.num("1.2g") == 1.2e9 and lv.num("870") == 870


def test_nxos_live():
    from app import demo
    ports = lv.build_live("nxos", demo.nxos_live())["ports"]
    p1 = ports[0]
    assert p1["port"] == "fc1/1" and p1["online"] and p1["vsan"] == 10 and p1["speed"] == "16G"
    assert p1["wwpns"] == ["10:00:00:10:9b:4a:11:01"] and p1["tx_rate"] > 0 and p1["tx"] > 0
    assert ports[23]["type"] == "TE-Port" and ports[23]["note"] == "port-channel 1"
    assert not ports[20]["online"]


def test_live_allowlist():
    allowed = _allowed(None)
    ok = MagicRun()
    for good in ["portstatsshow 12", "portstatsshow 3/14", 'fosexec --fid 5 -cmd "portstatsshow 2"', "porterrshow",
                 "show flogi database"]:
        _run(ok, good, allowed, 5)
    for bad in ["portstatsshow 1; cfgclear", "portstatsshow 1 && reboot", "portstatsshow x", "portstatsreset 1",
                "show interface brief | include x"]:
        with pytest.raises(PullError):
            _run(ok, bad, allowed, 5)


class MagicRun:
    """Stands in for an SSH client; records commands that pass the guard."""
    def __init__(self):
        self.sent = []

    def exec_command(self, cmd, timeout=None):
        import io
        self.sent.append(cmd)
        b = io.BytesIO(b"")
        return None, b, io.BytesIO(b"")


def test_api_demo_live():
    c = TestClient(app)
    for plat in ("fos", "nxos"):
        d = c.get(f"/api/demo-live/{plat}").json()
        assert d["platform"] == plat and len(d["ports"]) == 24 and d["at"] > 0
        assert any(p["wwpns"] for p in d["ports"])


# ---------------------------------------------------------------- port configuration
def test_portcfgshow_and_names():
    from app import demo
    ports = {p["port"]: p for p in lv.build_live("fos", demo.fos_live())["ports"]}
    assert ports["0"]["cfg"]["mode"] == "F" and ports["0"]["cfg"]["name"] == "ESX01"
    assert ports["4"]["cfg"]["speed"] == "16" and ports["5"]["cfg"]["mode"] == "noE"
    assert ports["20"]["cfg"]["persistent"] and not ports["20"]["cfg"]["enabled"]


def test_nx_running_interface():
    from app import demo
    ports = {p["port"]: p for p in lv.build_live("nxos", demo.nxos_live())["ports"]}
    assert ports["fc1/2"]["cfg"]["speed"] == "16" and ports["fc1/2"]["cfg"]["trunk"] == "off"
    assert ports["fc1/22"]["cfg"]["enabled"] is False and ports["fc1/24"]["cfg"]["mode"] == "E"


def _with_ports(model, pending):
    w = copy.deepcopy(model)
    w["ports_pending"] = pending
    return w


def test_fos_port_commands(fos_model):
    cur = {"name": "ESX01", "enabled": True, "persistent": False, "speed": "auto", "mode": "noE", "npiv": True, "trunk": True}
    pend = {
        "0": {"port": "0", "current": cur, "target": {"name": "ESX01_HBA0", "speed": "16", "mode": "F", "trunk": False},
              "clear": True, "wwpns": ["10:00:00:10:9b:4a:11:01"]},
        "7": {"port": "7", "current": cur, "target": {"enabled": False, "persistent": True}, "clear": False, "wwpns": []},
        "20": {"port": "20", "current": {**cur, "enabled": False, "persistent": True}, "target": {"enabled": True, "persistent": False},
               "clear": False, "wwpns": []},
    }
    p = make_plan(fos_model, _with_ports(fos_model, pend))
    cmds = p["commands"].splitlines()
    assert cmds == ['portname 0 -n "ESX01_HBA0"', "portcfgspeed 0 16", "portcfgeport 0 1", "portcfgfport 0 1",
                    "portcfgtrunkport 0 0", "portstatsclear 0", "portcfgpersistentdisable 7", "portcfgpersistentenable 20"]
    assert any("briefly lose this path" in w for w in p["warnings"])
    assert p["count"] == 9


def test_nxos_port_commands(nx_model):
    cur = {"name": "", "enabled": True, "speed": "auto", "mode": "F", "trunk": "on", "vsan": 10}
    pend = {
        "fc1/3": {"port": "fc1/3", "current": cur, "target": {"name": "ESX03 HBA0", "speed": "32", "trunk": "off"},
                  "clear": True, "wwpns": ["x"]},
        "fc1/9": {"port": "fc1/9", "current": cur, "target": {"vsan": 20}, "clear": False, "wwpns": []},
        "fc1/22": {"port": "fc1/22", "current": {**cur, "enabled": False}, "target": {"enabled": True}, "clear": False, "wwpns": []},
    }
    body = [c for c in make_plan(nx_model, _with_ports(nx_model, pend))["commands"].splitlines() if not c.startswith("!")]
    assert body[0] == "configure terminal"
    i = body.index("interface fc1/3")
    assert body[i:i + 7] == ["interface fc1/3", "  shutdown", "  switchport description ESX03 HBA0", "  switchport speed 32000",
                             "  switchport trunk mode off", "  no shutdown", "exit"]
    j = body.index("  vsan 20 interface fc1/9")
    assert body[j - 1] == "vsan database" and body[j + 1] == "exit"
    assert body[body.index("interface fc1/22") + 1] == "  no shutdown"
    k = body.index("end")
    assert body[k + 1] == "clear counters interface fc1/3" and body[-1] == "copy running-config startup-config"


def test_ports_with_zoning_share_one_session(nx_model):
    w = _with_ports(nx_model, {"fc1/1": {"port": "fc1/1", "current": {"enabled": True}, "target": {"enabled": False},
                                         "clear": False, "wwpns": []}})
    next(a for a in w["aliases"] if a["name"] == "ESX02_HBA0")["name"] = "ESX02_P0"
    body = [c for c in make_plan(nx_model, w)["commands"].splitlines() if not c.startswith("!")]
    assert body.count("configure terminal") == 1 and body.count("end") == 1
    assert body.index("interface fc1/1") < body.index("end")
