"""Fictional sample switch output so the UI can be tried without a switch."""

FOS = {
    "version": "Kernel:     4.14.240\nFabric OS:  v8.2.3c\nMade on:    Tue Mar 12 2024\n",
    "switchshow": "switchName:\tDEMO_FOS_A\nswitchType:\t162.0\nswitchState:\tOnline\nswitchDomain:\t1\n",
    "cfgshow": """Defined configuration:
 cfg:	FAB_A_PROD
		z_ESX01_HP3PAR01; z_ESX02_HP3PAR01; z_ESX03_HP3PAR01;
		z_ESX04_HP3PAR01; z_SQL01_HP3PAR01; z_SQL01_HP3PAR02;
		z_BKP01_MSL6480; z_RCFC_HP3PAR01_HP3PAR02
 cfg:	FAB_A_OLD
		z_ESX01_HP3PAR01
 zone:	z_BKP01_MSL6480
		BKP01_HBA0; MSL6480_LTO8_D1; MSL6480_LTO8_D2
 zone:	z_ESX01_HP3PAR01
		ESX01_HBA0; HP3PAR01_N0S1P1; HP3PAR01_N1S1P1
 zone:	z_ESX02_HP3PAR01
		ESX02_HBA0; HP3PAR01_N0S1P1; HP3PAR01_N1S1P1
 zone:	z_ESX03_HP3PAR01
		ESX03_HBA0; HP3PAR01_N0S1P1; HP3PAR01_N1S1P1
 zone:	z_ESX04_HP3PAR01
		ESX04_HBA0; HP3PAR01_N0S1P1; HP3PAR01_N1S1P1
 zone:	z_RCFC_HP3PAR01_HP3PAR02
		HP3PAR01_N0S2P4; HP3PAR02_N0S2P4
 zone:	z_SQL01_HP3PAR01
		SQL01_P0; SQL01_P1; HP3PAR01_N0S1P2; HP3PAR01_N1S1P2
 zone:	z_SQL01_HP3PAR02
		SQL01_P0; SQL01_P1; HP3PAR02_N0S1P1; HP3PAR02_N1S1P1
 alias:	BKP01_HBA0
		21:00:00:24:ff:6b:30:10
 alias:	ESX01_HBA0
		10:00:00:10:9b:4a:11:01
 alias:	ESX02_HBA0
		10:00:00:10:9b:4a:11:02
 alias:	ESX03_HBA0
		10:00:00:10:9b:4a:11:03
 alias:	ESX04_HBA0
		10:00:00:10:9b:4a:11:04
 alias:	HP3PAR01_N0S1P1
		20:11:00:02:ac:01:2a:3b
 alias:	HP3PAR01_N0S1P2
		20:12:00:02:ac:01:2a:3b
 alias:	HP3PAR01_N0S2P4
		20:24:00:02:ac:01:2a:3b
 alias:	HP3PAR01_N1S1P1
		21:11:00:02:ac:01:2a:3b
 alias:	HP3PAR01_N1S1P2
		21:12:00:02:ac:01:2a:3b
 alias:	HP3PAR02_N0S1P1
		20:11:00:02:ac:01:4c:7d
 alias:	HP3PAR02_N0S2P4
		20:24:00:02:ac:01:4c:7d
 alias:	HP3PAR02_N1S1P1
		21:11:00:02:ac:01:4c:7d
 alias:	MSL6480_LTO8_D1
		50:01:43:80:28:ce:a1:01
 alias:	MSL6480_LTO8_D2
		50:01:43:80:28:ce:a1:05
 alias:	SQL01_P0
		21:00:00:24:ff:7a:10:20
 alias:	SQL01_P1
		21:00:00:24:ff:7a:10:21

Effective configuration:
 cfg:	FAB_A_PROD
 zone:	z_BKP01_MSL6480
		21:00:00:24:ff:6b:30:10
		50:01:43:80:28:ce:a1:01
""",
    "nsshow": "",
    "nscamshow": "",
}


def _ns(entries):
    out = ["{", " Type Pid    COS     PortName                NodeName                 TTL(sec)"]
    for i, (w, role, node) in enumerate(entries):
        out.append(f" N    01{i:02x}00;      3;{w};20:00:{w[6:]}; na")
        out.append("    FC4s: FCP")
        out.append(f'    NodeSymb: [{len(node)}] "{node}"')
        out.append(f"    Device type: Physical {role}")
        out.append(f"    Port Index: {i}")
    out.append(f"The Local Name Server has {len(entries)} entries }}")
    return "\n".join(out)


FOS["nsshow"] = _ns([
    ("10:00:00:10:9b:4a:11:01", "Initiator", "esx01.arc.local"),
    ("10:00:00:10:9b:4a:11:02", "Initiator", "esx02.arc.local"),
    ("10:00:00:10:9b:4a:11:03", "Initiator", "esx03.arc.local"),
    ("10:00:00:10:9b:4a:11:04", "Initiator", "esx04.arc.local"),
    ("10:00:00:10:9b:4a:11:05", "Initiator", "esx05.arc.local"),
    ("21:00:00:24:ff:7a:10:20", "Initiator", "sql01.arc.local"),
    ("21:00:00:24:ff:7a:10:21", "Initiator", "sql01.arc.local"),
    ("21:00:00:24:ff:6b:30:10", "Initiator", "bkp01.arc.local"),
    ("20:11:00:02:ac:01:2a:3b", "Target", "HPE 3PAR 8450"),
    ("21:11:00:02:ac:01:2a:3b", "Target", "HPE 3PAR 8450"),
    ("20:12:00:02:ac:01:2a:3b", "Target", "HPE 3PAR 8450"),
    ("21:12:00:02:ac:01:2a:3b", "Target", "HPE 3PAR 8450"),
    ("20:24:00:02:ac:01:2a:3b", "Target", "HPE 3PAR 8450"),
    ("20:11:00:02:ac:01:4c:7d", "Target", "HPE 3PAR 8450"),
    ("21:11:00:02:ac:01:4c:7d", "Target", "HPE 3PAR 8450"),
    ("20:24:00:02:ac:01:4c:7d", "Target", "HPE 3PAR 8450"),
    ("50:01:43:80:28:ce:a1:01", "Target", "HPE MSL6480"),
    ("50:01:43:80:28:ce:a1:05", "Target", "HPE MSL6480"),
])

NXOS = {
    "show version": "Cisco Nexus Operating System (NX-OS) Software\nMDS 9396T 32G\n  system:    version 9.3(2a)\n  Device name: DEMO-MDS-A\n",
    "show running-config": """!Command: show running-config
version 9.3(2a)
switchname DEMO-MDS-A
vsan database
  vsan 10 name "FABRIC_A"
  vsan 20 name "BACKUP_A"
device-alias mode enhanced
device-alias database
  device-alias name ESX01_HBA0 pwwn 10:00:00:10:9b:4a:11:01
  device-alias name ESX02_HBA0 pwwn 10:00:00:10:9b:4a:11:02
  device-alias name ESX03_HBA0 pwwn 10:00:00:10:9b:4a:11:03
  device-alias name SQL01_P0 pwwn 21:00:00:24:ff:7a:10:20
  device-alias name SQL01_P1 pwwn 21:00:00:24:ff:7a:10:21
  device-alias name UCS_ESXB01_VHBA_A pwwn 20:00:00:25:b5:a0:00:01
  device-alias name UCS_ESXB02_VHBA_A pwwn 20:00:00:25:b5:a0:00:02
  device-alias name HP3PAR01_N0S1P1 pwwn 20:11:00:02:ac:01:2a:3b
  device-alias name HP3PAR01_N1S1P1 pwwn 21:11:00:02:ac:01:2a:3b
  device-alias name HP3PAR01_N0S2P4 pwwn 20:24:00:02:ac:01:2a:3b
  device-alias name HP3PAR02_N0S2P4 pwwn 20:24:00:02:ac:01:4c:7d
  device-alias name PRIMERA01_N0S0P1 pwwn 20:01:00:02:ac:02:51:9e
  device-alias name PRIMERA01_N1S0P1 pwwn 21:01:00:02:ac:02:51:9e
  device-alias name BKP01_HBA0 pwwn 21:00:00:24:ff:6b:30:10
  device-alias name MSL6480_LTO8_D1 pwwn 50:01:43:80:28:ce:a1:01

device-alias commit
zone smart-zoning enable vsan 10
zone mode enhanced vsan 10
zone mode enhanced vsan 20
!Active Zone Database Section for vsan 10
zone name SZ_HP3PAR01 vsan 10
    member device-alias ESX01_HBA0 initiator
    member device-alias HP3PAR01_N0S1P1 target
zoneset name ZS_FABRIC_A vsan 10
    member SZ_HP3PAR01
zoneset activate name ZS_FABRIC_A vsan 10
do clear zone database vsan 10
!Full Zone Database Section for vsan 10
zone name SZ_HP3PAR01 vsan 10
    member device-alias ESX01_HBA0 initiator
    member device-alias ESX02_HBA0 initiator
    member device-alias ESX03_HBA0 initiator
    member device-alias SQL01_P0 initiator
    member device-alias SQL01_P1 initiator
    member device-alias HP3PAR01_N0S1P1 target
    member device-alias HP3PAR01_N1S1P1 target
zone name SZ_PRIMERA01 vsan 10
    member device-alias UCS_ESXB01_VHBA_A initiator
    member device-alias UCS_ESXB02_VHBA_A initiator
    member device-alias PRIMERA01_N0S0P1 target
    member device-alias PRIMERA01_N1S0P1 target
zone name Z_RCFC_HP3PAR01_HP3PAR02 vsan 10
    member device-alias HP3PAR01_N0S2P4 both
    member device-alias HP3PAR02_N0S2P4 both
zoneset name ZS_FABRIC_A vsan 10
    member SZ_HP3PAR01
    member SZ_PRIMERA01
    member Z_RCFC_HP3PAR01_HP3PAR02
!Full Zone Database Section for vsan 20
zone name Z_BKP01_MSL6480 vsan 20
    member device-alias BKP01_HBA0
    member device-alias MSL6480_LTO8_D1
zoneset name ZS_BACKUP_A vsan 20
    member Z_BKP01_MSL6480
zoneset activate name ZS_BACKUP_A vsan 20
""",
    "show fcns database": """
VSAN 10:
--------------------------------------------------------------------------
FCID        TYPE  PWWN                    (VENDOR)        FC4-TYPE:FEATURE
--------------------------------------------------------------------------
0x0a0000    N     10:00:00:10:9b:4a:11:01 (Emulex)        scsi-fcp:init
0x0a0100    N     10:00:00:10:9b:4a:11:02 (Emulex)        scsi-fcp:init
0x0a0200    N     10:00:00:10:9b:4a:11:03 (Emulex)        scsi-fcp:init
0x0a0300    N     10:00:00:10:9b:4a:11:04 (Emulex)        scsi-fcp:init
0x0a0400    N     21:00:00:24:ff:7a:10:20 (Qlogic)        scsi-fcp:init
0x0a0500    N     21:00:00:24:ff:7a:10:21 (Qlogic)        scsi-fcp:init
0x0a0600    N     20:00:00:25:b5:a0:00:01 (Cisco)         scsi-fcp:init
0x0a0700    N     20:00:00:25:b5:a0:00:02 (Cisco)         scsi-fcp:init
0x0a0800    N     20:11:00:02:ac:01:2a:3b (3PARdata)      scsi-fcp:target
0x0a0900    N     21:11:00:02:ac:01:2a:3b (3PARdata)      scsi-fcp:target
0x0a0a00    N     20:24:00:02:ac:01:2a:3b (3PARdata)      scsi-fcp:both
0x0a0b00    N     20:24:00:02:ac:01:4c:7d (3PARdata)      scsi-fcp:both
0x0a0c00    N     20:01:00:02:ac:02:51:9e (3PARdata)      scsi-fcp:target
0x0a0d00    N     21:01:00:02:ac:02:51:9e (3PARdata)      scsi-fcp:target

Total number of entries = 14

VSAN 20:
--------------------------------------------------------------------------
FCID        TYPE  PWWN                    (VENDOR)        FC4-TYPE:FEATURE
--------------------------------------------------------------------------
0x140000    N     21:00:00:24:ff:6b:30:10 (Qlogic)        scsi-fcp:init
0x140100    N     50:01:43:80:28:ce:a1:01 (HP)            scsi-fcp:target
""",
    "show zone status": """VSAN: 10 default-zone: deny distribute: full Interop: default
    mode: enhanced merge-control: allow
    session: none
    hard-zoning: enabled broadcast: unsupported
    smart-zoning: enabled
VSAN: 20 default-zone: deny distribute: full Interop: default
    mode: enhanced merge-control: allow
    smart-zoning: disabled
""",
    "show zoneset active": "zoneset name ZS_FABRIC_A vsan 10\n  zone name SZ_HP3PAR01 vsan 10\nzoneset name ZS_BACKUP_A vsan 20\n",
}


# ---------------------------------------------------------------- live demo (counters grow with time)
import re as _re  # noqa: E402
import math as _math  # noqa: E402
import time as _time  # noqa: E402


def _fmt(n: float) -> str:
    for suf, div in (("g", 1e9), ("m", 1e6), ("k", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{suf}"
    return str(int(n))


def fos_live() -> dict:
    t = _time.time() - 1_790_000_000  # roughly weeks of uptime
    wwpns = _re.findall(r";\s*([0-9a-f:]{23});20:", FOS["nsshow"])
    lines = ["switchName:\tDEMO_FOS_A", "switchState:\tOnline", "",
             "Index Port Address  Media Speed   State       Proto", "=" * 50]
    err = ["          frames      enc    crc    crc    too    too    bad    enc   disc   link   loss   loss   frjt   fbsy  c3timeout    pcs    uncor",
           "       tx     rx      in    err    g_eof  shrt   long   eof     out   c3    fail    sync   sig                    tx    rx     err    err"]
    for i in range(24):
        if i < len(wwpns):
            spd = "N32" if wwpns[i].startswith("2") and ":00:02:ac:" in wwpns[i] else "N16"
            lines.append(f"  {i:3d} {i:3d}   01{i:02x}00   id    {spd}      Online      FC  F-Port  {wwpns[i]}")
            rate = 2000 + (i * 7919) % 40000
            tx, rx = 4e9 + i * 1e8 + t * rate, 6e9 + i * 2e8 + t * rate * 1.6
            crc = 3 if i == 6 else 0
            err.append(f"  {i:2d}:   {_fmt(tx):>6} {_fmt(rx):>6}  0      {crc}      0      0      0      0      {12 if i == 6 else 0}      0      0      0      0      0      0      0      0      0      0")
        elif i == 23:
            lines.append(f"  {i:3d} {i:3d}   01{i:02x}00   id    N32      Online      FC  E-Port  10:00:88:94:71:aa:bb:01 \"DEMO_FOS_B\" (downstream)")
            err.append(f"  {i:2d}:   {_fmt(9e9 + t * 50000):>6} {_fmt(8e9 + t * 45000):>6}  0      0      0      0      0      0      0      0      0      0      0      0      0      0      0      0      0")
        else:
            lines.append(f"  {i:3d} {i:3d}   01{i:02x}00   --    N32      No_Module   FC" + ("  Disabled (Persistent)" if i == 20 else ""))
            err.append(f"  {i:2d}:      0      0      0      0      0      0      0      0      0      0      0      0      0      0      0      0      0      0      0")
    out = {"switchshow": "\n".join(lines), "nsshow": FOS["nsshow"], "porterrshow": "\n".join(err)}
    names = _re.findall(r'NodeSymb: \[\d+\] "([^"]+)"', FOS["nsshow"])
    out["portname"] = "\n".join(f"port {i}: {names[i].split('.')[0].upper() if i < len(names) else ''}" for i in range(24))
    pc = []
    for blk in (range(0, 16), range(16, 24)):
        nums = list(blk)
        hdr = "Ports of Slot 0   " + "  ".join(f"{n:>2}" for n in nums)
        def row(label, f):
            return f"{label:<20}" + "  ".join(f"{f(n):>2}" for n in nums)
        pc += [hdr, "-----------------+" + "---+" * len(nums),
               row("Speed", lambda n: "16G" if n == 4 else "AN"),
               row("Trunk Port", lambda n: "ON"),
               row("Locked G_Port", lambda n: ".."),
               row("Disabled E_Port", lambda n: "ON" if n < 8 else ".."),
               row("Locked F_Port", lambda n: "ON" if n in (0, 1) else ".."),
               row("NPIV capability", lambda n: "ON"),
               row("Persistent Disable", lambda n: "ON" if n == 20 else ".."), ""]
    out["portcfgshow"] = "\n".join(pc)
    for i in list(range(len(wwpns))) + [23]:
        bps = (40 + (i * 37) % 400) * 1e6  # bytes/s, with a slow wobble so refreshes differ
        wob = _math.sin(t / 20 + i) * bps * 10
        wtx, wrx = int((5e11 + t * bps * 0.7 + wob) / 4), int((7e11 + t * bps + wob) / 4)
        out[f"portstatsshow {i}"] = (f"stat_wtx            \t{wtx}        4-byte words transmitted\n"
                                     f"stat_wrx            \t{wrx}        4-byte words received\n"
                                     f"stat_ftx            \t{wtx // 520}        Frames transmitted\n"
                                     f"stat_frx            \t{wrx // 520}        Frames received\n")
    return out


def nxos_live() -> dict:
    t = _time.time() - 1_790_000_000
    rows = _re.findall(r"^(0x[0-9a-f]{6})\s+N\s+([0-9a-f:]{23})", NXOS["show fcns database"], _re.M)
    vs = []
    vsan = 10
    for line in NXOS["show fcns database"].split("\n"):
        m = _re.match(r"^VSAN (\d+):", line)
        if m:
            vsan = int(m.group(1))
        m = _re.match(r"^(0x[0-9a-f]{6})\s+N\s+([0-9a-f:]{23})", line)
        if m:
            vs.append((m.group(1), m.group(2), vsan))
    brief = ["-------------------------------------------------------------------------------",
             "Interface  Vsan   Admin  Admin   Status          SFP    Oper  Oper   Port",
             "                  Mode   Trunk                          Mode  Speed  Channel",
             "                         Mode                                 (Gbps)",
             "-------------------------------------------------------------------------------"]
    flogi = ["--------------------------------------------------------------------------------",
             "INTERFACE        VSAN    FCID           PORT NAME               NODE NAME",
             "--------------------------------------------------------------------------------"]
    cnt = ["-------------------------------------------------------------------------------",
           "Interface        Input (rate is 5 min avg)      Output (rate is 5 min avg)",
           "                 -----------------------------  -----------------------------",
           "                 Rate     Total                 Rate     Total",
           "                 MB/s     Frames                MB/s     Frames",
           "-------------------------------------------------------------------------------"]
    for i in range(1, 25):
        name = f"fc1/{i}"
        if i <= len(vs):
            fcid, w, v = vs[i - 1]
            spd = 32 if ":00:02:ac:" in w else 16
            brief.append(f"{name:<11}{v:<7}auto   on      up               swl    F       {spd}    --")
            flogi.append(f"{name:<17}{v:<8}{fcid}  {w} 20:00:{w[6:]}")
            r_in = round(5 + (i * 37) % 180 + (t % 60) / 10, 1)
            r_out = round(3 + (i * 53) % 140 + (t % 45) / 10, 1)
            cnt.append(f"{name:<17}{r_in:<9}{int(1e9 + i * 1e7 + t * 900):<22}{r_out:<9}{int(8e8 + i * 1e7 + t * 700)}")
        elif i == 24:
            brief.append(f"{name:<11}{'1':<7}auto   on      trunking         swl    TE      32    1")
            cnt.append(f"{name:<17}{'410.2':<9}{int(5e10 + t * 30000):<22}{'388.7':<9}{int(4.8e10 + t * 28000)}")
        else:
            brief.append(f"{name:<11}{'1':<7}auto   on      notConnected     swl    --      --    --")
            cnt.append(f"{name:<17}{'0':<9}{'0':<22}{'0':<9}0")
    del rows
    rc = []
    for i in range(1, 25):
        rc.append(f"interface fc1/{i}")
        if i <= len(vs):
            rc.append(f"  switchport description {vs[i - 1][1][-5:].replace(':', '')}_link")
            rc += ["  switchport speed 16000"] if i == 2 else []
            rc += ["  switchport mode F", "  switchport trunk mode off", "  no shutdown"]
        elif i == 24:
            rc += ["  switchport description ISL_to_DEMO-MDS-B", "  switchport mode E", "  channel-group 1 force", "  no shutdown"]
        elif i == 22:
            rc += ["  shutdown"]
        else:
            rc += ["  switchport mode F", "  no shutdown"]
    return {"show interface brief": "\n".join(brief), "show flogi database": "\n".join(flogi),
            "show interface counters brief": "\n".join(cnt), "show running-config interface": "\n".join(rc)}
