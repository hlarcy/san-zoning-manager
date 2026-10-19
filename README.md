# SAN Zoning Manager

A small web app for managing Brocade FOS and Cisco MDS zoning. It pulls the zoning config from a switch over SSH, lets you edit hosts, SANs, aliases and zones in the browser, and gives you the commands to paste yourself.

It never pushes config. The SSH code can only send a fixed list of show commands, and refuses anything else.

## Run it

```bash
docker compose up -d --build
```

Open http://localhost:8080. To try it without a switch, use **Load FOS demo** or **Load MDS demo** on the Switch page.

The container must be able to reach the switch management IPs on TCP 22.

## What it reads

| Platform | Commands sent |
|---|---|
| Brocade FOS v8 | `version`, `switchshow`, `cfgshow`, `nsshow`, `nscamshow` |
| Cisco MDS NX-OS | `show version`, `show running-config`, `show fcns database`, `show zone status`, `show zoneset active` |

The **Live data** page sends these when you press Refresh (or on the auto-refresh timer):

| Platform | Commands sent |
|---|---|
| Brocade FOS v8 | `switchshow`, `nsshow`, `porterrshow`, `portcfgshow`, `portname`, then `portstatsshow <port>` for each online port |
| Cisco MDS NX-OS | `show interface brief`, `show flogi database`, `show interface counters brief`, `show running-config interface` |

`portstatsshow` is the only command that takes an argument. The port number is checked against a strict pattern before it is sent.

For FOS with virtual fabrics, enter the Fabric ID and each command is wrapped in `fosexec --fid N -cmd "..."`.

An account with read-only (user or network-operator) rights is enough. Credentials are used for one pull and are not stored.

You can also paste or upload `cfgshow`, a `configupload` file, or MDS `show running-config` on the Switch page.

## What you can do

- **Hosts:** see each host's initiators grouped together, rename the host (and the aliases and zones that carry its name), add initiators, move ports between hosts, and turn SAN access on or off.
- **SANs:** see each array's target ports grouped together, rename the SAN (and its port aliases and zones), add target ports, and grant or remove host access.
- **Access matrix:** hosts against SANs as tick boxes, with changed cells highlighted.
- **Aliases:** create, rename, edit, delete. Logged-in devices that have no alias are listed with a one-click Create alias.
- **Zones:** add or remove members, set smart zoning member types, rename, delete, create, and add to or remove from the active config.
- **Live data:** every port's state, speed and type, the pWWNs logged in on it (with alias and host or SAN), TX and RX MB/s, a trend line over the last 30 refreshes, total traffic and error counters. You can filter, sort by busiest or errors, refresh manually or every 15, 30 or 60 seconds, and click a device to jump to its host or SAN.
  - MDS rates are the switch's own 5-minute averages.
  - FOS rates are measured between two refreshes from the `portstatsshow` byte counters, so the first refresh shows totals only.
  - Credentials for refresh are held in the browser tab's memory only, and are cleared on reload or with "Forget credentials".
- **Ports (on the Live data page):** hover over or tab to a port to see its details: type, speed (running and configured), lock or mode, VSAN, index and address, media, NPIV, trunking, every logged-in device with alias, host or SAN, vendor, node name and FCID, traffic, load, errors and any pending change.
  - Click a port, or **Configure**, to stage changes: name or description, enable, disable or persistent disable, speed, port type lock (FOS) or port mode (MDS), NPIV and trunking (FOS), trunk mode and VSAN (MDS), and clear counters.
  - Tick several ports to configure them together, or to enable, disable or clear counters in one go.
  - Staged port changes are highlighted and added to the Changes page with the zoning commands. They are never sent to the switch.
- **Changes:** a plain list of what changed, the commands, copy and download, undo and discard.

Your workspace is kept in the browser's local storage, so a refresh does not lose work.

## How access changes become zones

- **MDS with smart zoning:** the host's initiators are added to the SAN's smart zone (the zone whose targets are all that SAN's ports). If there isn't one, `SZ_<SAN>` is created and added to the active zoneset.
- **FOS, or MDS without smart zoning:** a zone `z_<HOST>_<SAN>` (or `Z_<HOST>_<SAN>`) is created with the host's initiators and the SAN's ports.
- New access uses the SAN's host-facing ports (ports already zoned to at least one host), so replication-only ports are left out.
- Removing access takes the host out of zones that point only at that SAN. Zones left without initiators are deleted. A zone that mixes several hosts and SANs is not changed automatically; you're told to edit it on the Zones page.

## Hosts and SANs

Grouping is worked out on each pull:

- **SANs:** by array serial number in the WWPN (3PAR, Primera, Alletra 9000), otherwise by the alias name stem (`HP3PAR01_N0S1P1` becomes `HP3PAR01`).
- **Hosts:** by the alias name stem (`ESX01_HBA0` becomes `ESX01`), or the name server node name for devices with no alias.
- **Roles:** come from the name server first (FOS `Device type`, MDS `fcns` FC4 features), then smart zoning tags, then the WWPN vendor, then the alias name.

Moving a port between groups only changes the view in this tool. Renaming a group can rename the aliases and zones too, and those renames do produce commands.

## Commands it generates

**FOS:**

1. `zoneobjectrename`
2. `alicreate` and `aliadd`
3. `zonecreate` and `zoneadd`
4. `cfgadd`
5. `zoneremove` and `cfgremove`
6. `zonedelete`, then `aliremove` and `alidelete`

It finishes with `cfgenable` when the effective config is affected, otherwise `cfgsave`. Peer zones use `zone --add/--remove -peerzone`.

**NX-OS:**

1. `device-alias rename` and new device-aliases, then `device-alias commit`
2. fcalias changes, then `zone rename` and `zoneset rename`
3. Zone member adds and removes, with smart zoning types
4. Zoneset membership
5. Deletions, then device-alias deletions (after zones stop using them)

It finishes with `zoneset activate`, `zone commit` in enhanced mode, and `copy running-config startup-config`.

**Port changes:**

- **FOS:** `portname -n`, `portcfgspeed`, `portcfgfport`, `portcfggport` and `portcfgeport` locks, `portcfgnpivport`, `portcfgtrunkport`, `portenable` and `portdisable` (or the persistent versions), and `portstatsclear`.
- **NX-OS:** an `interface` block with description, speed, mode, trunk mode and shutdown or no shutdown, plus `vsan database` membership. If the port stays up, it is shut and re-enabled around the speed, mode, trunk or VSAN change. `clear counters interface` runs after `end`.
- **Warnings:** you're warned when a change will interrupt a logged-in device.

Always review the output, run it in a change window, and pull the switch again afterwards to confirm.

## Security

- **Localhost only:** the published port is bound to 127.0.0.1 by default.
- **Locked-down container:** it runs as a non-root user with a read-only root filesystem.
- **No credential storage:** credentials are never logged or saved.
- **Host keys:** the SSH host key fingerprint is shown after each pull. To refuse unknown switches, put their keys in `./data/known_hosts` and set `STRICT_HOST_KEY_CHECKING: "1"`.
- **No API docs:** the API docs endpoints are switched off.
- **Offline-only mode:** set `ZM_DISABLE_PULL=1` to switch off the SSH pull, leaving only paste and upload.

## Development

```bash
pip install -r requirements.txt pytest httpx
pytest -q
uvicorn app.main:app --reload --port 8080
```
