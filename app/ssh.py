"""Read-only SSH collection from Brocade FOS and Cisco MDS switches.

Only the fixed commands below can ever be sent. There is no code path that enters config mode,
and the _run() guard refuses anything that is not on the allowlist.
"""
from __future__ import annotations

import os
import re
import time

import paramiko

FOS_COMMANDS = ["version", "switchshow", "cfgshow", "nsshow", "nscamshow"]
NXOS_COMMANDS = ["show version", "show running-config", "show fcns database", "show zone status",
                 "show zoneset active"]
FOS_LIVE = ["switchshow", "nsshow", "porterrshow", "portcfgshow", "portname"]
NXOS_LIVE = ["show interface brief", "show flogi database", "show interface counters brief",
             "show running-config interface"]
DETECT = ["show version", "version"]


class PullError(Exception):
    pass


def _fos_wrap(cmd: str, fid: int | None) -> str:
    return f'fosexec --fid {int(fid)} -cmd "{cmd}"' if fid is not None else cmd


def _allowed(fid: int | None) -> frozenset[str]:
    fos = FOS_COMMANDS + FOS_LIVE
    return frozenset(DETECT + NXOS_COMMANDS + NXOS_LIVE + [_fos_wrap(c, fid) for c in fos] + fos)


# the only command with an argument: per-port byte counters, port number validated strictly
_PORTSTATS = re.compile(r'^(?:portstatsshow \d{1,4}(?:/\d{1,3})?|fosexec --fid \d{1,3} -cmd "portstatsshow \d{1,4}(?:/\d{1,3})?")$')


def _run(client: paramiko.SSHClient, cmd: str, allowed: frozenset[str], timeout: int) -> str:
    if cmd not in allowed and not _PORTSTATS.match(cmd):  # hard stop: never send anything else
        raise PullError(f"Refused to send non-allowlisted command: {cmd!r}")
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return out + (("\n" + err) if err.strip() else "")


def _host_key_policy(client: paramiko.SSHClient):
    strict = os.environ.get("STRICT_HOST_KEY_CHECKING", "0") == "1"
    kh = os.environ.get("KNOWN_HOSTS_FILE", "/data/known_hosts")
    if strict:
        if os.path.exists(kh):
            client.load_host_keys(kh)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())


def _connect(host: str, port: int, username: str, password: str, timeout: int) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    _host_key_policy(client)
    try:
        client.connect(host, port=port, username=username, password=password, timeout=timeout,
                       banner_timeout=timeout, auth_timeout=timeout, look_for_keys=False, allow_agent=False)
    except paramiko.AuthenticationException as e:
        raise PullError("Login failed. Check the username and password.") from e
    except paramiko.SSHException as e:
        raise PullError(f"SSH error: {e}") from e
    except OSError as e:
        raise PullError(f"Could not reach {host}:{port} ({e}).") from e
    return client


def _fingerprint(client) -> str:
    key = client.get_transport().get_remote_server_key()
    return f"{key.get_name()} {key.get_fingerprint().hex(':')}"


def pull(host: str, username: str, password: str, port: int = 22, platform: str = "auto",
         fid: int | None = None, timeout: int = 30) -> dict:
    client = _connect(host, port, username, password, timeout)
    log = []
    try:
        fingerprint = _fingerprint(client)
        allowed = _allowed(fid)
        outputs: dict[str, str] = {}
        if platform == "auto":
            t0 = time.time()
            sv = _run(client, "show version", allowed, timeout)
            log.append({"cmd": "show version", "bytes": len(sv), "ms": int((time.time() - t0) * 1000)})
            if "Cisco" in sv and ("NX-OS" in sv or "MDS" in sv or "Nexus" in sv):
                platform = "nxos"
                outputs["show version"] = sv
            else:
                t0 = time.time()
                v = _run(client, "version", allowed, timeout)
                log.append({"cmd": "version", "bytes": len(v), "ms": int((time.time() - t0) * 1000)})
                if "Fabric OS" not in v:
                    raise PullError("Could not tell whether this is FOS or NX-OS. Choose the platform and try again.")
                platform = "fos"
                outputs["version"] = v
        cmds = FOS_COMMANDS if platform == "fos" else NXOS_COMMANDS
        for c in cmds:
            if c in outputs:
                continue
            wire = _fos_wrap(c, fid) if platform == "fos" else c
            t0 = time.time()
            outputs[c] = _run(client, wire, allowed, timeout)
            log.append({"cmd": wire, "bytes": len(outputs[c]), "ms": int((time.time() - t0) * 1000)})
        return {"platform": platform, "outputs": outputs, "log": log, "fingerprint": fingerprint}
    finally:
        client.close()


def live(host: str, username: str, password: str, platform: str, port: int = 22, fid: int | None = None,
         timeout: int = 30) -> dict:
    """Collect port state, logins and counters. Read-only."""
    if platform not in ("fos", "nxos"):
        raise PullError("Platform must be fos or nxos for live data.")
    client = _connect(host, port, username, password, timeout)
    try:
        allowed = _allowed(fid)
        outputs = {}
        t0 = time.time()
        for c in (FOS_LIVE if platform == "fos" else NXOS_LIVE):
            wire = _fos_wrap(c, fid) if platform == "fos" else c
            outputs[c] = _run(client, wire, allowed, timeout)
        if platform == "fos":
            from .parsers.live import fos_switchshow_ports
            online = [p["port"] for p in fos_switchshow_ports(outputs["switchshow"]) if p["online"]][:192]
            for name in online:
                c = f"portstatsshow {name}"
                outputs[c] = _run(client, _fos_wrap(c, fid), allowed, timeout)
        return {"outputs": outputs, "ms": int((time.time() - t0) * 1000), "fingerprint": _fingerprint(client)}
    finally:
        client.close()
