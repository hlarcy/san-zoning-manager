/* SAN Zoning Manager front end. All edits stay in the browser; the server only reads switches and renders commands. */
(() => {
"use strict";

/* ------------------------------------------------------------------ utils */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const clone = o => JSON.parse(JSON.stringify(o));
const uid = () => "n:" + Math.random().toString(36).slice(2, 10);
const WWN = /^([0-9a-f]{2}:){7}[0-9a-f]{2}$/;
function normWWN(s){ s = String(s).trim().toLowerCase(); if (/^[0-9a-f]{16}$/.test(s)) s = s.match(/../g).join(":"); return WWN.test(s) ? s : null; }
const ROLE = {initiator:"Initiator", target:"Target", both:"Both", unknown:"Unknown"};
const byName = (a, b) => a.localeCompare(b, undefined, {numeric:true, sensitivity:"base"});

async function api(path, body){
  const r = await fetch(path, body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  let j = {}; try { j = await r.json(); } catch {}
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : (j.detail ? JSON.stringify(j.detail) : r.statusText));
  return j;
}
function toast(html, kind){
  const t = document.createElement("div"); t.className = "toast" + (kind ? " " + kind : ""); t.innerHTML = html;
  $("#toasts").appendChild(t); setTimeout(() => t.remove(), kind === "warn" ? 6000 : 3000);
}

/* ------------------------------------------------------------------ state */
const S = { orig:null, work:null, raw:null, meta:null, view:"connect", selHost:null, selSan:null, q:{}, undo:[], plan:null, comments:true, busy:false,
  cred:null, /* SSH credentials for live refresh: memory only, never saved */ live:null };
const LS = "zm.workspace.v1";
function save(){ try { localStorage.setItem(LS, JSON.stringify({orig:S.orig, work:S.work, raw:S.raw, meta:S.meta, view:S.view, at:Date.now()})); } catch {} }
function restore(){
  try { const j = JSON.parse(localStorage.getItem(LS) || "null"); if (j && j.orig && j.work){ Object.assign(S, {orig:j.orig, work:j.work, raw:j.raw, meta:j.meta, view: j.view && j.view !== "connect" ? j.view : "hosts"}); return j.at; } } catch {}
  return null;
}
function load(res, view){
  S.orig = res.model; S.work = clone(res.model); S.raw = res.raw; S.meta = {log:res.log || [], fingerprint:res.fingerprint || "", at:Date.now()};
  S.undo = []; S.selHost = null; S.selSan = null; S.plan = null; S.view = view || "hosts";
  if (!S.live || S.live.host !== (res.model.switch.host || "") || S.live.platform !== res.model.platform) S.live = newLive(res.model);
  if (S.cred && (S.cred.host !== res.model.switch.host)) S.cred = null;
  save(); refreshPlan(); render();
}
function change(label, fn){
  const before = clone(S.work);
  let msg;
  try { msg = fn(S.work); } catch (e) { toast(esc(e.message), "warn"); S.work = before; return false; }
  if (msg && msg.error){ toast(esc(msg.error), "warn"); S.work = before; return false; }
  S.undo.push({label, model:before}); if (S.undo.length > 200) S.undo.shift();
  save(); refreshPlan(); render();
  toast(`${esc(label)}${msg && msg.note ? `<br><span class="muted">${esc(msg.note)}</span>` : ""}`, msg && msg.warn ? "warn" : "");
  return true;
}
let planT;
function refreshPlan(){
  clearTimeout(planT);
  planT = setTimeout(async () => {
    if (!S.orig) return;
    try { S.plan = await api("/api/plan", {original:S.orig, working:S.work, comments:S.comments}); }
    catch (e) { S.plan = {changes:[], commands:"", warnings:["Could not build commands: " + e.message], count:0}; }
    renderNav(); if (S.view === "changes") render();
  }, 120);
}

/* ------------------------------------------------------------------ derived view of a model */
function derive(m){
  const al = new Map(m.aliases.map(a => [a.id, a]));
  const byW = new Map();
  m.aliases.forEach(a => a.wwpns.forEach(w => { if (!byW.has(w)) byW.set(w, []); byW.get(w).push(a); }));
  const zones = new Map(m.zones.map(z => [z.id, z]));
  const active = m.cfgs.find(c => c.id === m.active) || null;
  const activeZ = active ? active.zones.map(id => zones.get(id)).filter(Boolean) : [];
  const role = w => (m.devices[w] && m.devices[w].role) || "unknown";
  const memW = mem => mem.kind === "alias" ? ((al.get(mem.ref) || {}).wwpns || []) : mem.kind === "wwpn" ? [mem.ref] : [];
  const label = w => { const a = byW.get(w); return a && a.length ? a[0].name : w; };
  const isT = r => r === "target" || r === "both";
  const hosts = new Map(), sans = new Map();
  for (const w of Object.keys(m.devices)){
    const g = m.groups[w] || "Unassigned";
    const tgt = isT(role(w));
    const map = tgt ? sans : hosts;
    if (!map.has(g)) map.set(g, []); map.get(g).push(w);
  }
  const hostOf = w => isT(role(w)) ? null : (m.groups[w] || "Unassigned");
  const sanOf = w => isT(role(w)) ? (m.groups[w] || "Unassigned") : null;
  const smart = m.platform === "nxos" && m.smart;
  const allowed = (ta, tb) => !smart || !((ta === "initiator" && tb === "initiator") || (ta === "target" && tb === "target"));
  // access: host -> san -> Set(zone names)
  const access = new Map();
  const add = (h, s, z) => { if (!access.has(h)) access.set(h, new Map()); const x = access.get(h); if (!x.has(s)) x.set(s, new Set()); x.get(s).add(z); };
  for (const z of activeZ){
    const ex = [];
    z.members.forEach(mm => memW(mm).forEach(w => ex.push({w, tag: mm.tag || role(w)})));
    for (let i = 0; i < ex.length; i++) for (let j = 0; j < ex.length; j++){
      if (i === j) continue;
      const a = ex[i], b = ex[j];
      const h = hostOf(a.w), s = sanOf(b.w);
      if (h && s && allowed(a.tag, b.tag)) add(h, s, z.name);
    }
  }
  const zonesOf = w => m.zones.filter(z => z.members.some(mm => memW(mm).includes(w)));
  return {m, al, byW, zones, active, activeZ, role, memW, label, hosts, sans, hostOf, sanOf, access, zonesOf, smart};
}

/* ------------------------------------------------------------------ naming */
const isFOS = () => S.work.platform === "fos";
function validName(n){ return isFOS() ? /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(n) : /^[A-Za-z][A-Za-z0-9_\-$^]{0,63}$/.test(n); }
function cleanName(n){ let s = String(n).replace(isFOS() ? /[^A-Za-z0-9_]/g : /[^A-Za-z0-9_\-$^]/g, "_"); if (!/^[A-Za-z]/.test(s)) s = "Z_" + s; return s.slice(0, 64); }
const nameRule = () => isFOS() ? "Start with a letter; letters, digits and underscore; up to 64." : "Start with a letter; letters, digits, _ - $ ^; up to 64.";
function aliasNameTaken(m, n, exceptId){ return m.aliases.some(a => a.id !== exceptId && a.name.toLowerCase() === n.toLowerCase() && (a.kind !== "fcalias" || true)); }
function zoneNameTaken(m, n, exceptId){ return m.zones.some(z => z.id !== exceptId && z.name.toLowerCase() === n.toLowerCase()); }
function uniqueZoneName(m, base){ let n = cleanName(base), b = n, k = 2; while (zoneNameTaken(m, n)) n = cleanName(`${b}_${k++}`); return n; }

/* ------------------------------------------------------------------ operations on the working model */
function refFor(m, w){
  const D = derive(m);
  const as = (D.byW.get(w) || []);
  const pick = (m.platform === "nxos" ? as.find(a => a.kind === "device-alias") : null) || as.find(a => a.wwpns.length === 1) || null;
  return pick ? {kind:"alias", ref:pick.id} : {kind:"wwpn", ref:w};
}
function zoneHas(m, z, w){ const D = derive(m); return z.members.some(mm => D.memW(mm).includes(w)); }
function addToZone(m, z, w, tag){
  if (zoneHas(m, z, w)) return;
  const r = refFor(m, w);
  z.members.push({...r, tag: (m.platform === "nxos" && m.smart) ? tag : null, principal: z.peer ? tag === "target" : false});
}
function removeFromZone(m, z, ws){
  const D = derive(m); const set = new Set(ws); let blocked = null;
  z.members = z.members.filter(mm => {
    const mw = D.memW(mm);
    if (!mw.some(w => set.has(w))) return true;
    if (mw.every(w => set.has(w))) return false;
    blocked = D.al.get(mm.ref)?.name || mm.ref; return true;
  });
  return blocked;
}
function deleteZone(m, zid){ m.zones = m.zones.filter(z => z.id !== zid); m.cfgs.forEach(c => c.zones = c.zones.filter(x => x !== zid)); }
function activeCfg(m){ return m.cfgs.find(c => c.id === m.active) || null; }

function opSetAccess(m, host, san, on){
  const D = derive(m);
  const inits = (D.hosts.get(host) || []);
  const allT = (D.sans.get(san) || []).filter(w => D.role(w) === "target" || D.role(w) === "both");
  // host-facing ports: SAN ports already zoned with at least one initiator (skips replication-only ports)
  const isInit = w => !(D.role(w) === "target" || D.role(w) === "both");
  const facing = new Set();
  for (const z of D.activeZ){ const zw = z.members.flatMap(mm => D.memW(mm)); if (zw.some(isInit)) zw.forEach(w => { if (allT.includes(w)) facing.add(w); }); }
  const tgts = on && facing.size ? allT.filter(w => facing.has(w)) : allT;
  if (!inits.length) return {error:`${host} has no initiators.`};
  if (!tgts.length) return {error:`${san} has no target ports.`};
  const cfg = activeCfg(m);
  if (!cfg) return {error:"There is no active config or zoneset to change."};
  const tset = new Set(tgts);
  const zoneTargets = z => [...new Set(z.members.flatMap(mm => D.memW(mm)))].filter(w => D.role(w) === "target" || D.role(w) === "both");
  if (on){
    if (D.smart){
      const cands = D.activeZ.filter(z => { const t = zoneTargets(z); return t.length && t.every(w => tset.has(w)); })
        .sort((a, b) => (b.name === `SZ_${san}`) - (a.name === `SZ_${san}`) || zoneTargets(b).length - zoneTargets(a).length);
      const z = cands[0] ? m.zones.find(x => x.id === cands[0].id) : null;
      if (z){ inits.forEach(w => addToZone(m, z, w, "initiator")); return {note:`Added to smart zone ${z.name}.`}; }
      const nz = {id:uid(), name:uniqueZoneName(m, `SZ_${san}`), peer:false, members:[]};
      tgts.forEach(w => addToZone(m, nz, w, "target")); inits.forEach(w => addToZone(m, nz, w, "initiator"));
      m.zones.push(nz); cfg.zones.push(nz.id);
      return {note:`Created smart zone ${nz.name} with all ${san} ports.`};
    }
    const nz = {id:uid(), name:uniqueZoneName(m, `${isFOS() ? "z" : "Z"}_${host}_${san}`), peer:false, members:[]};
    inits.forEach(w => addToZone(m, nz, w, "initiator")); tgts.forEach(w => addToZone(m, nz, w, "target"));
    m.zones.push(nz); cfg.zones.push(nz.id);
    return {note:`Created zone ${nz.name}.`};
  }
  // off
  const iset = new Set(inits); const notes = []; let warn = false;
  for (const zr of D.activeZ){
    const z = m.zones.find(x => x.id === zr.id);
    const zw = [...new Set(z.members.flatMap(mm => D.memW(mm)))];
    if (!zw.some(w => iset.has(w)) || !zw.some(w => tset.has(w))) continue;
    const zt = zw.filter(w => D.role(w) === "target" || D.role(w) === "both");
    const others = zw.filter(w => !iset.has(w) && !(D.role(w) === "target" || D.role(w) === "both"));
    if (zt.every(w => tset.has(w))){
      if (!others.length){
        const used = m.cfgs.filter(c => c.id !== cfg.id && c.zones.includes(z.id)).length;
        if (used){ cfg.zones = cfg.zones.filter(x => x !== z.id); notes.push(`Removed ${z.name} from ${cfg.name} (still in another config)`); }
        else { deleteZone(m, z.id); notes.push(`Deleted zone ${z.name}`); }
      } else {
        const b = removeFromZone(m, z, inits);
        if (b){ warn = true; notes.push(`Alias ${b} in ${z.name} also covers other devices; edit it on the Zones page`); }
        else notes.push(`Removed ${host} from ${z.name}`);
      }
    } else if (!others.length){
      const b = removeFromZone(m, z, tgts);
      notes.push(b ? `Alias ${b} in ${z.name} covers ports of another SAN; edit on the Zones page` : `Removed ${san} ports from ${z.name}`);
      if (b) warn = true;
    } else { warn = true; notes.push(`${z.name} mixes several hosts and SANs; edit it on the Zones page`); }
  }
  return {note:notes.join(". ") + ".", warn};
}

function opRenameGroup(m, kind, old, nw, opts){
  const D = derive(m);
  const ws = (kind === "host" ? D.hosts : D.sans).get(old) || [];
  const other = kind === "host" ? D.hosts : D.sans;
  if (other.has(nw) && nw !== old) return {error:`A ${kind === "host" ? "host" : "SAN"} called ${nw} already exists. Use Move to merge.`};
  ws.forEach(w => m.groups[w] = nw);
  let ra = 0, rz = 0;
  if (opts.aliases){
    const set = new Set(ws);
    for (const a of m.aliases){
      if (!a.wwpns.some(w => set.has(w)) || !a.name.startsWith(old)) continue;
      const n = cleanName(nw + a.name.slice(old.length));
      if (aliasNameTaken(m, n, a.id)) return {error:`Alias ${n} already exists.`};
      a.name = n; ra++;
    }
  }
  if (opts.zones){
    const rx = new RegExp(`(^|_)${old.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=_|$)`, "g");
    for (const z of m.zones){
      if (!rx.test(z.name)) continue; rx.lastIndex = 0;
      const n = cleanName(z.name.replace(rx, (_, p) => p + nw)); rx.lastIndex = 0;
      if (n !== z.name){ if (zoneNameTaken(m, n, z.id)) return {error:`Zone ${n} already exists.`}; z.name = n; rz++; }
    }
  }
  return {note:`${ra} alias${ra === 1 ? "" : "es"} and ${rz} zone${rz === 1 ? "" : "s"} renamed.`};
}

function opCreateAlias(m, {name, wwpns, role, group}){
  if (!validName(name)) return {error:`Alias name: ${nameRule()}`};
  if (aliasNameTaken(m, name)) return {error:`Alias ${name} already exists.`};
  if (!wwpns.length) return {error:"Enter at least one WWPN."};
  const kind = m.platform === "fos" ? "alias" : "device-alias";
  if (kind === "device-alias"){
    if (wwpns.length !== 1) return {error:"A device-alias maps to exactly one WWPN."};
    const clash = m.aliases.find(a => a.kind === "device-alias" && a.wwpns[0] === wwpns[0]);
    if (clash) return {error:`${wwpns[0]} already has device-alias ${clash.name}. Rename that one instead.`};
  }
  m.aliases.push({id:uid(), name, kind, wwpns});
  for (const w of wwpns){
    if (!m.devices[w]) m.devices[w] = {role, role_src:"Manual", vendor:"", online:false, node:""};
    else if (role && role !== m.devices[w].role && m.devices[w].role === "unknown"){ m.devices[w].role = role; m.devices[w].role_src = "Manual"; }
    if (group) m.groups[w] = group;
  }
}
function opDeleteAlias(m, id){
  const a = m.aliases.find(x => x.id === id); if (!a) return;
  const notes = [];
  for (const z of [...m.zones]){
    const before = z.members.length;
    z.members = z.members.filter(mm => !(mm.kind === "alias" && mm.ref === id));
    if (z.members.length !== before){
      if (!z.members.length){ deleteZone(m, z.id); notes.push(`deleted empty zone ${z.name}`); }
      else notes.push(`removed from ${z.name}`);
    }
  }
  m.aliases = m.aliases.filter(x => x.id !== id);
  return notes.length ? {note: notes.join(", ")} : undefined;
}

/* ------------------------------------------------------------------ dialog */
function ask({title, html, ok = "Save", onOk, wide}){
  const d = $("#dlg");
  $("#dlgTitle").textContent = title; $("#dlgBody").innerHTML = html; $("#dlgErr").textContent = ""; $("#dlgOk").textContent = ok;
  d.style.width = wide ? "min(760px, calc(100vw - 32px))" : "";
  const form = $("#dlgForm");
  const submit = e => {
    e.preventDefault();
    const err = onOk(form);
    if (err){ $("#dlgErr").textContent = err; return; }
    close();
  };
  const close = () => { form.removeEventListener("submit", submit); d.close(); };
  form.addEventListener("submit", submit);
  $("#dlgCancel").onclick = close;
  d.addEventListener("close", () => form.removeEventListener("submit", submit), {once:true});
  d.showModal();
  const f = d.querySelector("input:not([type=hidden]):not([type=checkbox]),select,textarea"); if (f) f.focus();
}
const val = (f, n) => (f.elements[n] ? f.elements[n].value : "").trim();

/* ------------------------------------------------------------------ chips */
function roleChip(text, role, title, extra = ""){ return `<span class="chip r-${role}" title="${esc(title || "")}">${esc(text)}${extra}</span>`; }
function online(w){ const d = S.work.devices[w]; return `<span class="dotst${d && d.online ? " on" : ""}" title="${d && d.online ? "Logged in to the fabric" : "Not logged in"}"></span>`; }

/* ------------------------------------------------------------------ header + nav */
function renderCtx(){
  const c = $("#ctx");
  if (!S.work){ c.innerHTML = ""; return; }
  const m = S.work;
  const vs = m.platform === "nxos" && m.vsans && m.vsans.length > 1
    ? `<select id="vsanSel" aria-label="VSAN">${m.vsans.map(v => `<option value="${v}"${v === m.vsan ? " selected" : ""}>VSAN ${v}${m.vsan_names[v] ? " " + esc(m.vsan_names[v]) : ""}</option>`).join("")}</select>`
    : (m.platform === "nxos" ? `<span class="chip-ctx">VSAN ${m.vsan}</span>` : "");
  c.innerHTML = `<span class="sw">${esc(m.switch.name)}</span><span class="muted" style="color:#D9D3DC">${esc(m.switch.host || "")}</span>
    <span class="chip-ctx">${m.platform === "fos" ? "Brocade FOS " + esc(m.switch.version || "") : "Cisco MDS NX-OS " + esc(m.switch.version || "")}</span>${vs}
    ${m.platform === "nxos" ? `<span class="chip-ctx">${m.smart ? "Smart zoning" : "Basic zoning"}, ${esc(m.zone_mode)}</span>` : ""}
    ${m.demo ? '<span class="chip-ctx demo">Demo data</span>' : ""}`;
  const vsel = $("#vsanSel");
  if (vsel) vsel.onchange = async () => {
    if (S.plan && S.plan.count && !confirmInline()) { vsel.value = m.vsan; return; }
    try {
      const res = m.demo ? await api(`/api/demo/nxos?vsan=${vsel.value}`) : await api("/api/parse", {platform:"nxos", texts:S.raw, vsan:+vsel.value, host:m.switch.host});
      load({...res, log:S.meta.log, fingerprint:S.meta.fingerprint}, S.view);
      toast(`Switched to VSAN ${vsel.value}`);
    } catch (e) { toast(esc(e.message), "warn"); }
  };
}
function confirmInline(){ toast("You have unsaved changes in this VSAN. Discard them on the Changes page first.", "warn"); return false; }

const NAV = [
  {id:"connect", label:"Switch", grp:"Source"},
  {id:"hosts", label:"Hosts", grp:"Manage", need:true},
  {id:"sans", label:"SANs", need:true},
  {id:"access", label:"Access matrix", need:true},
  {id:"aliases", label:"Aliases", grp:"Configuration", need:true},
  {id:"zones", label:"Zones", need:true},
  {id:"live", label:"Live data", grp:"Monitor", need:true},
  {id:"changes", label:"Changes", grp:"Output", need:true},
];
function renderNav(){
  const D = S.work ? derive(S.work) : null;
  const n = {hosts: D && D.hosts.size, sans: D && D.sans.size, aliases: S.work && S.work.aliases.length, zones: S.work && S.work.zones.length};
  $("#nav").innerHTML = NAV.map(x => `${x.grp ? `<div class="grp">${x.grp}</div>` : ""}<button class="nav" data-act="nav" data-v="${x.id}"${S.view === x.id ? ' aria-current="page"' : ""}${x.need && !S.work ? " disabled" : ""}>${x.label}${x.id === "changes" ? (S.plan && S.plan.count ? `<span class="badge">${S.plan.count}</span>` : "") : (n[x.id] != null ? `<span class="n">${n[x.id]}</span>` : "")}</button>`).join("");
}

/* ------------------------------------------------------------------ views */
function render(){
  renderCtx(); renderNav();
  const v = S.work ? S.view : "connect";
  ({connect:vConnect, hosts:vHosts, sans:vSans, access:vAccess, aliases:vAliases, zones:vZones, live:vLive, changes:vChanges})[v]();
}

function vConnect(){
  const m = S.work;
  $("#main").innerHTML = `
  <h1>Pull a zoning config<span class="dot">.</span></h1>
  <p class="lede">Connect to a Brocade FOS or Cisco MDS switch over SSH. This tool only runs read-only show commands, keeps your edits in this browser, and gives you the commands to paste yourself.</p>
  ${m ? `<div class="note">Working on <b>${esc(m.switch.name)}</b> ${m.switch.host ? "(" + esc(m.switch.host) + ")" : ""} with ${S.plan && S.plan.count ? `<b>${S.plan.count} pending change${S.plan.count > 1 ? "s" : ""}</b>` : "no pending changes"}. Pulling again replaces this workspace. <button class="link" data-act="nav" data-v="hosts">Continue editing</button></div>` : ""}
  <div class="cols">
    <form class="panel" id="pullForm" autocomplete="off">
      <h2>Pull from a switch</h2>
      <p class="sub">Credentials are used for this pull only and are not stored.</p>
      <div class="form">
        <div class="field"><label for="pHost">Management IP or hostname</label><input type="text" id="pHost" name="host" required value="${esc(m && !m.demo ? m.switch.host : "")}" placeholder="10.0.10.21"></div>
        <div class="field"><label for="pPort">SSH port</label><input type="number" id="pPort" name="port" value="22" min="1" max="65535"></div>
        <div class="field"><label for="pUser">Username</label><input type="text" id="pUser" name="username" required autocomplete="username"></div>
        <div class="field"><label for="pPass">Password</label><input type="password" id="pPass" name="password" required autocomplete="current-password"></div>
        <div class="field"><label for="pPlat">Platform</label><select id="pPlat" name="platform"><option value="auto">Detect automatically</option><option value="fos">Brocade FOS</option><option value="nxos">Cisco MDS NX-OS</option></select></div>
        <div class="field"><label for="pVsan">VSAN (MDS)</label><input type="number" id="pVsan" name="vsan" min="1" max="4093" placeholder="Busiest VSAN"></div>
        <div class="field"><label for="pFid">Fabric ID (FOS virtual fabrics)</label><input type="number" id="pFid" name="fid" min="1" max="128" placeholder="Leave blank if not used"></div>
        <div class="field" style="justify-content:flex-end"><button class="btn primary" type="submit" id="pullBtn">Pull config (read-only)</button></div>
      </div>
      <details class="more"><summary>Commands this sends</summary><ul class="arrows mono"><li>FOS: version, switchshow, cfgshow, nsshow, nscamshow</li><li>NX-OS: show version, show running-config, show fcns database, show zone status, show zoneset active</li></ul></details>
      ${S.meta && S.meta.log && S.meta.log.length && m && !m.demo ? `<details class="more"><summary>Last pull log</summary><ul class="arrows mono">${S.meta.log.map(l => `<li>${esc(l.cmd)}: ${l.bytes} bytes, ${l.ms} ms</li>`).join("")}</ul>${S.meta.fingerprint ? `<p class="muted mono">Host key ${esc(S.meta.fingerprint)}</p>` : ""}</details>` : ""}
    </form>
    <div class="panel">
      <h2>Import from text</h2>
      <p class="sub">Paste or load <span class="mono">cfgshow</span>, a <span class="mono">configupload</span> file, or MDS <span class="mono">show running-config</span>.</p>
      <div class="field" style="margin-bottom:10px"><textarea id="impText" rows="9" aria-label="Config text" placeholder="Paste here"></textarea></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <select id="impPlat" aria-label="Platform"><option value="auto">Detect</option><option value="fos">Brocade FOS</option><option value="nxos">Cisco MDS</option></select>
        <input type="file" id="impFile" hidden><button class="btn" type="button" data-act="impFile">Choose file</button>
        <button class="btn primary" type="button" data-act="import">Import</button>
      </div>
      <h3 style="margin-top:22px">Try it without a switch</h3>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" data-act="demo" data-v="fos">Load FOS demo</button><button class="btn" data-act="demo" data-v="nxos">Load MDS demo</button></div>
    </div>
  </div>`;
  $("#pullForm").addEventListener("submit", async e => {
    e.preventDefault();
    const f = e.target; const b = $("#pullBtn"); b.disabled = true; b.innerHTML = '<span class="spin"></span> Pulling';
    try {
      const body = {host:val(f, "host"), port:+val(f, "port") || 22, username:val(f, "username"), password:f.elements.password.value, platform:val(f, "platform"),
        vsan: val(f, "vsan") ? +val(f, "vsan") : null, fid: val(f, "fid") ? +val(f, "fid") : null};
      const res = await api("/api/pull", body);
      f.elements.password.value = "";
      load(res, "hosts");
      S.cred = {host:body.host, port:body.port, username:body.username, password:body.password, platform:res.model.platform, fid:body.fid}; // memory only
      toast(`Pulled <span class="b">${esc(res.model.switch.name)}</span>: ${res.model.aliases.length} aliases, ${res.model.zones.length} zones`);
      if (res.model.notes && res.model.notes.length) toast(esc(res.model.notes[0]), "warn");
    } catch (err) { toast(esc(err.message), "warn"); b.disabled = false; b.textContent = "Pull config (read-only)"; }
  });
  $("#impFile").addEventListener("change", e => { const fl = e.target.files[0]; if (!fl) return; const r = new FileReader(); r.onload = () => { $("#impText").value = r.result; }; r.readAsText(fl); });
}

/* ---------- hosts */
function listPane(kind, D){
  const map = kind === "host" ? D.hosts : D.sans;
  const q = (S.q[kind] || "").toLowerCase();
  const names = [...map.keys()].sort(byName).filter(n => !q || n.toLowerCase().includes(q) || map.get(n).some(w => w.includes(q) || D.label(w).toLowerCase().includes(q)));
  const sel = kind === "host" ? S.selHost : S.selSan;
  const reach = n => kind === "host" ? [...(D.access.get(n) || new Map()).keys()] : [...D.access].filter(([, s]) => s.has(n)).map(([h]) => h);
  return `<div class="list"><div class="lh"><input type="search" id="q-${kind}" placeholder="Search ${kind === "host" ? "hosts" : "SANs"}, aliases, WWPNs" value="${esc(S.q[kind] || "")}" aria-label="Search"></div>
    <div class="items" role="listbox">${names.map(n => { const r = reach(n); const on = map.get(n).filter(w => S.work.devices[w]?.online).length;
      return `<button class="item" role="option" data-act="sel-${kind}" data-v="${esc(n)}" aria-selected="${n === sel}"><span class="nm">${esc(n)}</span><span class="ct">${map.get(n).length} ${kind === "host" ? "port" : "port"}${map.get(n).length > 1 ? "s" : ""}</span><span class="mt">${on}/${map.get(n).length} logged in · ${kind === "host" ? (r.length ? "reaches " + esc(r.sort(byName).join(", ")) : "no SAN access") : (r.length + " host" + (r.length === 1 ? "" : "s"))}</span></button>`; }).join("") || '<div class="item"><span class="mt">No matches</span></div>'}</div></div>`;
}
function bindSearch(kind){ const i = $(`#q-${kind}`); if (!i) return; i.addEventListener("input", () => { S.q[kind] = i.value; const p = i.selectionStart; render(); const n = $(`#q-${kind}`); n.focus(); n.setSelectionRange(p, p); }); }

function portTable(D, ws, kind){
  return `<div class="tbl"><table><thead><tr><th>Alias</th><th>WWPN</th><th>Vendor</th><th>Role</th><th></th></tr></thead><tbody>${ws.sort((a, b) => byName(D.label(a), D.label(b))).map(w => {
    const as = D.byW.get(w) || []; const d = S.work.devices[w] || {};
    return `<tr><td>${as.length ? as.map(a => `<div><b>${esc(a.name)}</b>${a.wwpns.length > 1 ? ` <span class="sub">(${a.wwpns.length} WWPNs)</span>` : ""}</div>`).join("") : '<span class="sub">No alias</span>'}</td>
      <td class="mono">${online(w)}${esc(w)}${d.node ? `<div class="sub">${esc(d.node)}</div>` : ""}</td><td>${esc(d.vendor || "")}</td>
      <td>${roleChip(ROLE[D.role(w)], D.role(w), d.role_src)}</td>
      <td class="act">${as.length ? `<button class="btn sm ghost" data-act="aliasRename" data-v="${esc(as[0].id)}">Rename</button>` : `<button class="btn sm" data-act="aliasForW" data-v="${esc(w)}">Create alias</button>`}
        <button class="btn sm ghost" data-act="movePort" data-v="${esc(w)}" data-k="${kind}">Move</button>
        ${as.length ? `<button class="btn sm ghost danger" data-act="aliasDelete" data-v="${esc(as[0].id)}">Delete alias</button>` : ""}</td></tr>`;
  }).join("")}</tbody></table></div>`;
}

function vHosts(){
  const D = derive(S.work), O = derive(S.orig);
  if (!S.selHost || !D.hosts.has(S.selHost)) S.selHost = [...D.hosts.keys()].sort(byName)[0] || null;
  const h = S.selHost;
  let det = '<div class="empty"><h3>No hosts</h3>Add an alias for an initiator on the Aliases page.</div>';
  if (h){
    const ws = D.hosts.get(h); const acc = D.access.get(h) || new Map(); const oacc = O.access.get(h) || new Map();
    const sans = [...D.sans.keys()].sort(byName);
    const zones = [...new Set(ws.flatMap(w => D.zonesOf(w).map(z => z.name)))];
    det = `<div class="title"><h2>${esc(h)}</h2><button class="btn sm" data-act="groupRename" data-k="host" data-v="${esc(h)}">Rename host</button></div>
      <div class="muted">${ws.length} initiator${ws.length > 1 ? "s" : ""} · ${acc.size ? "reaches " + esc([...acc.keys()].sort(byName).join(", ")) : "no SAN access"}</div>
      <div class="sec"><div class="sec-h"><h3>Initiators</h3><span class="sp"></span><button class="btn sm primary" data-act="addPort" data-k="host" data-v="${esc(h)}">Add initiator</button></div>${portTable(D, ws, "host")}</div>
      <div class="sec"><div class="sec-h"><h3>SAN access</h3><span class="muted">Turn a SAN on or off for every initiator of this host.</span></div>
        <div class="acc">${sans.map(s => { const on = acc.has(s), was = oacc.has(s);
          return `<div class="row${on !== was ? " chg" : ""}"><label class="tg"><input type="checkbox" data-act="access" data-h="${esc(h)}" data-s="${esc(s)}"${on ? " checked" : ""} aria-label="${esc(h)} access to ${esc(s)}"><span></span></label>
            <span class="nm">${esc(s)}</span><span class="muted">${D.sans.get(s).length} ports</span>${on !== was ? `<span class="tag chg">${on ? "Added" : "Removed"}</span>` : ""}
            <span class="via">${on ? "via " + esc([...acc.get(s)].join(", ")) : ""}</span></div>`; }).join("") || '<div class="muted">No SANs found.</div>'}</div></div>
      <div class="sec"><h3>Zones</h3><div class="chips">${zones.map(z => `<span class="chip r-both">${esc(z)}</span>`).join("") || '<span class="muted">Not in any zone</span>'}</div></div>`;
  }
  $("#main").innerHTML = `<div class="head"><h1>Hosts<span class="dot">.</span></h1></div><div class="md">${listPane("host", D)}<section class="detail">${det}</section></div>`;
  bindSearch("host");
}

function vSans(){
  const D = derive(S.work), O = derive(S.orig);
  if (!S.selSan || !D.sans.has(S.selSan)) S.selSan = [...D.sans.keys()].sort(byName)[0] || null;
  const s = S.selSan;
  let det = '<div class="empty"><h3>No SANs</h3>No target ports were found.</div>';
  if (s){
    const ws = D.sans.get(s);
    const hosts = [...D.access].filter(([, x]) => x.has(s)).map(([hh]) => hh).sort(byName);
    const ohosts = new Set([...O.access].filter(([, x]) => x.has(s)).map(([hh]) => hh));
    const removed = [...ohosts].filter(x => !hosts.includes(x));
    det = `<div class="title"><h2>${esc(s)}</h2><button class="btn sm" data-act="groupRename" data-k="san" data-v="${esc(s)}">Rename SAN</button></div>
      <div class="muted">${ws.length} target port${ws.length > 1 ? "s" : ""} · ${hosts.length} host${hosts.length === 1 ? "" : "s"} with access</div>
      <div class="sec"><div class="sec-h"><h3>Target ports</h3><span class="sp"></span><button class="btn sm primary" data-act="addPort" data-k="san" data-v="${esc(s)}">Add target port</button></div>${portTable(D, ws, "san")}</div>
      <div class="sec"><div class="sec-h"><h3>Hosts with access</h3><span class="sp"></span><button class="btn sm primary" data-act="grant" data-v="${esc(s)}">Grant hosts access</button></div>
        <div class="acc">${hosts.map(hh => `<div class="row${ohosts.has(hh) ? "" : " chg"}"><span class="nm">${esc(hh)}</span><span class="muted">${(D.hosts.get(hh) || []).length} initiators</span>${ohosts.has(hh) ? "" : '<span class="tag chg">Added</span>'}<span class="via">via ${esc([...D.access.get(hh).get(s)].join(", "))}</span><button class="btn sm ghost danger" data-act="revoke" data-h="${esc(hh)}" data-s="${esc(s)}">Remove</button></div>`).join("")}
        ${removed.map(hh => `<div class="row chg"><span class="nm">${esc(hh)}</span><span class="tag chg">Removed</span><span class="via"></span></div>`).join("")}
        ${!hosts.length && !removed.length ? '<div class="muted">No hosts can reach this SAN.</div>' : ""}</div></div>`;
  }
  $("#main").innerHTML = `<div class="head"><h1>SANs<span class="dot">.</span></h1></div><div class="md">${listPane("san", D)}<section class="detail">${det}</section></div>`;
  bindSearch("san");
}

function vAccess(){
  const D = derive(S.work), O = derive(S.orig);
  const hosts = [...D.hosts.keys()].sort(byName), sans = [...D.sans.keys()].sort(byName);
  const q = (S.q.mx || "").toLowerCase();
  const hs = hosts.filter(h => !q || h.toLowerCase().includes(q));
  $("#main").innerHTML = `<div class="head"><h1>Access matrix<span class="dot">.</span></h1><span class="sp"></span><input type="search" id="q-mx" placeholder="Filter hosts" value="${esc(S.q.mx || "")}" aria-label="Filter hosts"></div>
  <p class="lede">Tick a box to let a host reach a SAN; untick to remove it. ${D.smart ? "Hosts are added to the SAN's smart zone, or a new SZ_ zone is created." : isFOS() ? "A zone named z_HOST_SAN is created for new access." : "A zone named Z_HOST_SAN is created for new access."} Highlighted cells differ from the switch.</p>
  ${hosts.length && sans.length ? `<div class="mx"><table><thead><tr><th>Host</th>${sans.map(s => `<th class="rot"><div>${esc(s)}</div></th>`).join("")}</tr></thead><tbody>
  ${hs.map(h => `<tr><td class="h">${esc(h)}</td>${sans.map(s => { const on = !!(D.access.get(h) && D.access.get(h).has(s)); const was = !!(O.access.get(h) && O.access.get(h).has(s));
    return `<td class="c${on !== was ? " chg" : ""}"><input type="checkbox" data-act="access" data-h="${esc(h)}" data-s="${esc(s)}"${on ? " checked" : ""} aria-label="${esc(h)} to ${esc(s)}" title="${on ? "via " + esc([...D.access.get(h).get(s)].join(", ")) : ""}"></td>`; }).join("")}</tr>`).join("")}
  </tbody></table></div>` : '<div class="empty">Need at least one host and one SAN.</div>'}`;
  const i = $("#q-mx"); i.addEventListener("input", () => { S.q.mx = i.value; const p = i.selectionStart; render(); const n = $("#q-mx"); n.focus(); n.setSelectionRange(p, p); });
}

function vAliases(){
  const D = derive(S.work);
  const q = (S.q.al || "").toLowerCase();
  const origIds = new Map(S.orig.aliases.map(a => [a.id, a]));
  const rows = S.work.aliases.filter(a => !q || a.name.toLowerCase().includes(q) || a.wwpns.some(w => w.includes(q)) || a.wwpns.some(w => (S.work.groups[w] || "").toLowerCase().includes(q)))
    .sort((a, b) => byName(a.name, b.name));
  const unal = Object.entries(S.work.devices).filter(([w, d]) => d.online && !D.byW.has(w));
  $("#main").innerHTML = `<div class="head"><h1>Aliases<span class="dot">.</span></h1><span class="sp"></span><input type="search" id="q-al" placeholder="Search name, WWPN or group" value="${esc(S.q.al || "")}" aria-label="Search aliases"><button class="btn primary" data-act="aliasNew">New alias</button></div>
  <p class="lede">${isFOS() ? "FOS aliases can hold several WWPNs." : `Device-aliases are fabric-wide (mode ${esc(S.work.da_mode)}) and map to one pWWN; fcaliases belong to VSAN ${S.work.vsan}.`}</p>
  ${unal.length ? `<div class="note warn"><b>${unal.length} logged-in device${unal.length > 1 ? "s have" : " has"} no alias.</b><div class="tbl" style="margin-top:8px"><table><thead><tr><th>WWPN</th><th>Role</th><th>Vendor</th><th>Group</th><th></th></tr></thead><tbody>${unal.map(([w, d]) => `<tr><td class="mono">${esc(w)}${d.node ? `<div class="sub">${esc(d.node)}</div>` : ""}</td><td>${roleChip(ROLE[d.role], d.role, d.role_src)}</td><td>${esc(d.vendor)}</td><td>${esc(S.work.groups[w] || "")}</td><td class="act"><button class="btn sm primary" data-act="aliasForW" data-v="${esc(w)}">Create alias</button></td></tr>`).join("")}</tbody></table></div></div>` : ""}
  <div class="tbl"><table><thead><tr><th>Name</th><th>Type</th><th>WWPN</th><th>Role</th><th>Host or SAN</th><th>Zones</th><th></th></tr></thead><tbody>
  ${rows.map(a => { const o = origIds.get(a.id); const st = !o ? "New" : (o.name !== a.name ? "Renamed" : (o.wwpns.join() !== a.wwpns.join() ? "Changed" : ""));
    const zc = S.work.zones.filter(z => z.members.some(mm => mm.kind === "alias" && mm.ref === a.id)).length;
    return `<tr><td><b>${esc(a.name)}</b> ${st ? `<span class="tag chg">${st}</span>` : ""}${o && o.name !== a.name ? `<div class="sub">was ${esc(o.name)}</div>` : ""}</td>
    <td>${esc(a.kind)}</td><td class="mono">${a.wwpns.map(w => `<div>${online(w)}${esc(w)}</div>`).join("") || '<span class="sub">none</span>'}</td>
    <td>${a.wwpns.map(w => roleChip(ROLE[D.role(w)], D.role(w), (S.work.devices[w] || {}).role_src)).join(" ")}</td>
    <td>${esc([...new Set(a.wwpns.map(w => S.work.groups[w] || ""))].join(", "))}</td><td class="mono">${zc}</td>
    <td class="act"><button class="btn sm ghost" data-act="aliasEdit" data-v="${esc(a.id)}">Edit</button><button class="btn sm ghost" data-act="aliasRename" data-v="${esc(a.id)}">Rename</button><button class="btn sm ghost danger" data-act="aliasDelete" data-v="${esc(a.id)}">Delete</button></td></tr>`; }).join("") || '<tr><td colspan="7" class="sub">No aliases</td></tr>'}
  </tbody></table></div>`;
  const i = $("#q-al"); i.addEventListener("input", () => { S.q.al = i.value; const p = i.selectionStart; render(); const n = $("#q-al"); n.focus(); n.setSelectionRange(p, p); });
}

function memberChip(D, z, mm, idx){
  const ws = D.memW(mm);
  const roles = [...new Set(ws.map(D.role))];
  const role = mm.kind === "other" ? "other" : (mm.tag || (roles.length === 1 ? roles[0] : "unknown"));
  const name = mm.kind === "alias" ? (D.al.get(mm.ref)?.name || "?") : mm.ref;
  const tagSel = D.smart && mm.kind !== "other" ? `<select class="tagsel" data-act="tag" data-z="${esc(z.id)}" data-i="${idx}" aria-label="Smart zoning type">${["", "initiator", "target", "both"].map(t => `<option value="${t}"${(mm.tag || "") === t ? " selected" : ""}>${t || "auto"}</option>`).join("")}</select>` : "";
  const pr = z.peer ? `<span class="t">${mm.principal ? "principal" : "peer"}</span>` : "";
  return `<span class="chip r-${role}" title="${esc(ws.join(", "))}">${esc(name)}${pr}${tagSel}<button class="x" data-act="zrm" data-z="${esc(z.id)}" data-i="${idx}" aria-label="Remove ${esc(name)}">×</button></span>`;
}
function vZones(){
  const D = derive(S.work);
  const origZ = new Map(S.orig.zones.map(z => [z.id, z]));
  const cfg = D.active;
  const q = (S.q.zn || "").toLowerCase();
  const match = z => !q || z.name.toLowerCase().includes(q) || z.members.some(mm => (mm.kind === "alias" ? (D.al.get(mm.ref)?.name || "") : mm.ref).toLowerCase().includes(q));
  const inActive = new Set(cfg ? cfg.zones : []);
  const card = z => { const o = origZ.get(z.id); const chg = !o || JSON.stringify(o.members) !== JSON.stringify(z.members) || o.name !== z.name;
    return `<div class="zcard${chg ? " chg" : ""}"><div class="zh"><b>${esc(z.name)}</b>${z.peer ? '<span class="tag">Peer zone</span>' : ""}${!o ? '<span class="tag chg">New</span>' : o.name !== z.name ? `<span class="tag chg">was ${esc(o.name)}</span>` : chg ? '<span class="tag chg">Changed</span>' : ""}
      <span class="sp"></span><button class="btn sm ghost" data-act="zrename" data-v="${esc(z.id)}">Rename</button>
      <button class="btn sm ghost" data-act="zcfg" data-v="${esc(z.id)}">${inActive.has(z.id) ? "Remove from config" : "Add to active config"}</button>
      <button class="btn sm ghost danger" data-act="zdel" data-v="${esc(z.id)}">Delete</button></div>
      <div class="chips">${z.members.map((mm, i) => memberChip(D, z, mm, i)).join("") || '<span class="muted">No members</span>'}</div>
      <div class="add"><input type="text" list="memList" placeholder="Add member: alias name or WWPN" data-addfor="${esc(z.id)}" aria-label="Add member to ${esc(z.name)}"><button class="btn sm" data-act="zadd" data-v="${esc(z.id)}">Add</button></div></div>`; };
  const act = S.work.zones.filter(z => inActive.has(z.id) && match(z));
  const rest = S.work.zones.filter(z => !inActive.has(z.id) && match(z));
  $("#main").innerHTML = `<div class="head"><h1>Zones<span class="dot">.</span></h1><span class="sp"></span><input type="search" id="q-zn" placeholder="Search zones or members" value="${esc(S.q.zn || "")}" aria-label="Search zones"><button class="btn primary" data-act="znew">New zone</button></div>
  <p class="lede">${cfg ? `Active ${isFOS() ? "config" : "zoneset"} <span class="tag act">${esc(cfg.name)}</span> holds ${cfg.zones.length} zones.` : "No active config found."} ${D.smart ? "Smart zoning is on; set each member's type or leave it on auto." : ""}</p>
  <datalist id="memList">${S.work.aliases.map(a => `<option value="${esc(a.name)}">`).join("")}</datalist>
  ${act.map(card).join("") || '<div class="empty">No zones match.</div>'}
  ${rest.length ? `<details class="more"${q ? " open" : ""}><summary>${rest.length} zone${rest.length > 1 ? "s" : ""} not in the active ${isFOS() ? "config" : "zoneset"}</summary><div style="margin-top:10px">${rest.map(card).join("")}</div></details>` : ""}`;
  const i = $("#q-zn"); i.addEventListener("input", () => { S.q.zn = i.value; const p = i.selectionStart; render(); const n = $("#q-zn"); n.focus(); n.setSelectionRange(p, p); });
  document.querySelectorAll("[data-addfor]").forEach(inp => inp.addEventListener("keydown", e => { if (e.key === "Enter"){ e.preventDefault(); ACT.zadd({dataset:{v:inp.dataset.addfor}}); } }));
}

/* ---------- live data */
function newLive(m){ return {host:(m && m.switch.host) || "", platform:m && m.platform, data:null, prev:null, hist:{}, busy:false, auto:0, timer:null, active:true, q:"", sort:"port", err:"", sel:new Set()}; }
const natural = (a, b) => String(a).localeCompare(String(b), undefined, {numeric:true});
function fmtBytes(n){ if (n == null) return "–"; const u = ["B","KB","MB","GB","TB","PB"]; let i = 0; while (n >= 1000 && i < u.length - 1){ n /= 1000; i++; } return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`; }
function fmtCount(n){ if (n == null) return "–"; const u = ["","k","M","G","T"]; let i = 0; while (n >= 1000 && i < u.length - 1){ n /= 1000; i++; } return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)}${u[i]}`; }
const fmtRate = r => r == null ? "–" : r >= 100 ? r.toFixed(0) : r.toFixed(1);
function lineRate(speed){ const g = parseFloat(String(speed).replace(/[^0-9.]/g, "")); return g ? g * 100 : null; } // FC payload ~100 MB/s per Gbit
function spark(h){
  if (!h || h.length < 2) return '<span class="muted">–</span>';
  const W = 84, H = 22, max = Math.max(1, ...h.map(x => Math.max(x.tx || 0, x.rx || 0)));
  const pts = k => h.map((x, i) => `${(i / (h.length - 1) * (W - 2) + 1).toFixed(1)},${(H - 2 - (x[k] || 0) / max * (H - 4)).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true"><polyline class="srx" points="${pts("rx")}"/><polyline class="stx" points="${pts("tx")}"/></svg>`;
}
async function liveRefresh(){
  const L = S.live; if (!L || L.busy) return;
  const m = S.work;
  if (!m.demo && !S.cred){ render(); return; }
  L.busy = true; L.err = ""; if (S.view === "live") render();
  try {
    const d = m.demo ? await api(`/api/demo-live/${m.platform}`) : await api("/api/live", {...S.cred, platform:m.platform});
    const prev = L.data; const dt = prev ? (d.at - prev.at) / 1000 : 0;
    const pmap = new Map((prev ? prev.ports : []).map(p => [p.id, p]));
    for (const p of d.ports){
      if (d.rate_source === "browser"){
        const q = pmap.get(p.id);
        const r = (a, b) => (q && dt > 0 && a != null && b != null && a >= b && p.unit === "bytes" && q.unit === "bytes") ? (a - b) / dt / 1e6 : null;
        p.tx_rate = q ? r(p.tx, q.tx) : null; p.rx_rate = q ? r(p.rx, q.rx) : null;
      }
      if (p.tx_rate != null || p.rx_rate != null){ const h = (L.hist[p.id] = L.hist[p.id] || []); h.push({tx:p.tx_rate || 0, rx:p.rx_rate || 0}); if (h.length > 30) h.shift(); }
    }
    L.prev = prev; L.data = d;
  } catch (e) { L.err = e.message; toast(esc(e.message), "warn"); }
  L.busy = false;
  if (S.view === "live") render();
}
function setAuto(sec){
  const L = S.live; clearInterval(L.timer); L.auto = sec; L.timer = null;
  if (sec) L.timer = setInterval(() => { if (S.view === "live" && !document.hidden) liveRefresh(); }, sec * 1000);
}
function vLive(){
  const m = S.work; const L = S.live || (S.live = newLive(m)); const D = derive(m);
  const needCred = !m.demo && !S.cred;
  const d = L.data;
  const devChip = w => { const r = D.role(w); const g = m.groups[w]; const nm = D.label(w);
    return `<button class="chip r-${r} chipbtn" data-act="goGroup" data-w="${esc(w)}" title="${esc(w)}${g ? " · " + esc(g) : ""}">${esc(nm === w ? "No alias" : nm)}${g && g !== "Unassigned" ? `<span class="t">${esc(g)}</span>` : ""}</button>`; };
  let body = "";
  if (needCred){
    body = `<form class="panel" id="liveForm" autocomplete="off" style="max-width:640px"><h2>Connect for live data</h2>
      <p class="sub">Live data needs a fresh read-only SSH session each refresh. Credentials stay in this browser tab's memory and are gone when you reload or close it.</p>
      <div class="form"><div class="field"><label for="lHost">Switch</label><input type="text" id="lHost" name="host" required value="${esc(m.switch.host || "")}"></div>
      <div class="field"><label for="lPort">SSH port</label><input type="number" id="lPort" name="port" value="22"></div>
      <div class="field"><label for="lUser">Username</label><input type="text" id="lUser" name="username" required autocomplete="username"></div>
      <div class="field"><label for="lPass">Password</label><input type="password" id="lPass" name="password" required autocomplete="current-password"></div>
      ${m.platform === "fos" ? '<div class="field"><label for="lFid">Fabric ID (optional)</label><input type="number" id="lFid" name="fid"></div>' : ""}
      <div class="field" style="justify-content:flex-end"><button class="btn primary" type="submit">Connect and refresh</button></div></div></form>`;
  } else if (!d){
    body = `<div class="empty">${L.busy ? '<span class="spin"></span> Reading ports from ' + esc(m.switch.name) : `<h3>No live data yet</h3>Press Refresh to read port state and counters from ${esc(m.switch.name)}.`}</div>`;
  } else {
    const q = L.q.toLowerCase();
    let ports = d.ports.filter(p => (!L.active || p.online) && (!q || p.port.toLowerCase().includes(q) || p.wwpns.some(w => w.includes(q) || D.label(w).toLowerCase().includes(q) || (m.groups[w] || "").toLowerCase().includes(q)) || (p.note || "").toLowerCase().includes(q)));
    const busy = p => (p.tx_rate || 0) + (p.rx_rate || 0);
    const errs = p => Object.values(p.errors || {}).reduce((a, b) => a + (b || 0), 0);
    ports.sort(L.sort === "busy" ? (a, b) => busy(b) - busy(a) : L.sort === "errors" ? (a, b) => errs(b) - errs(a) || natural(a.port, b.port) : (a, b) => natural(a.port, b.port));
    const on = d.ports.filter(p => p.online).length;
    const devs = d.ports.reduce((a, p) => a + p.wwpns.length, 0);
    const tx = d.ports.reduce((a, p) => a + (p.tx_rate || 0), 0), rx = d.ports.reduce((a, p) => a + (p.rx_rate || 0), 0);
    const ep = d.ports.filter(p => errs(p) > 0).length;
    const rateNote = d.rate_source === "switch" ? "Rates are the 5-minute average reported by the switch." : (L.prev ? `Rates are measured over the last ${Math.round((d.at - L.prev.at) / 1000)} s.` : "Rates appear after the next refresh.");
    body = `<div class="stats">
      <div class="stat"><div class="k">Ports online</div><div class="v">${on}<span class="muted" style="font-size:14px;font-weight:600"> / ${d.ports.length}</span></div></div>
      <div class="stat"><div class="k">Devices logged in</div><div class="v">${devs}</div></div>
      <div class="stat key"><div class="k">Total TX MB/s</div><div class="v">${L.prev || d.rate_source === "switch" ? fmtRate(tx) : "–"}</div></div>
      <div class="stat key"><div class="k">Total RX MB/s</div><div class="v">${L.prev || d.rate_source === "switch" ? fmtRate(rx) : "–"}</div></div>
      <div class="stat"><div class="k">Ports with errors</div><div class="v"${ep ? ' style="color:var(--red)"' : ""}>${ep}</div></div></div>
      <p class="muted" style="margin:-8px 0 12px;font-size:12.5px">${rateNote} Totals are ${d.platform === "nxos" ? "frames" : "bytes"} since the counters were last cleared.${d.platform === "fos" && d.ports.some(p => p.online && p.unit === "frames") ? " Ports showing fr had no byte counters, so only rounded frame totals are shown." : ""} The bar under RX shows roughly how busy the link is against its speed.</p>
      ${selBar(d)}<div class="tbl"><table class="live"><thead><tr><th class="selc"><input type="checkbox" id="selAll" aria-label="Select all shown ports"${ports.length && ports.every(p => L.sel.has(p.id)) ? " checked" : ""}></th><th>Port</th><th>State</th><th>Speed</th><th>Type</th>${m.platform === "nxos" ? "<th>VSAN</th>" : ""}<th>Connected pWWNs</th><th class="num">TX MB/s</th><th class="num">RX MB/s</th><th>Trend</th><th class="num">Total TX / RX</th><th>Errors</th><th></th></tr></thead><tbody>
      ${ports.map(p => { const lr = lineRate(p.speed); const pct = lr ? Math.min(100, (busy(p) / 2) / lr * 100) : 0; const e = errs(p);
        const pend = (m.ports_pending || {})[p.id];
        return `<tr class="${p.online ? "" : "off"}${pend ? " pend" : ""}" data-pid="${esc(p.id)}"><td class="selc"><input type="checkbox" data-act="psel" data-v="${esc(p.id)}"${L.sel.has(p.id) ? " checked" : ""} aria-label="Select port ${esc(p.port)}"></td>
        <td class="mono"><button class="portbtn" data-act="pedit" data-v="${esc(p.id)}" data-pid="${esc(p.id)}" aria-describedby="pcard"><b>${esc(p.port)}</b></button>${p.cfg && p.cfg.name ? `<div class="sub pname">${esc(p.cfg.name)}</div>` : ""}${pend ? '<div><span class="tag chg">Pending</span></div>' : ""}</td>
        <td class="nw"><span class="dotst${p.online ? " on" : ""}"></span>${esc(p.state)}</td><td class="mono nw">${esc(p.speed || "")}</td><td class="nw">${esc(p.type || "")}</td>${m.platform === "nxos" ? `<td class="mono">${p.vsan ?? ""}</td>` : ""}
        <td><div class="chips">${p.wwpns.map(devChip).join("")}${p.npiv && p.wwpns.length > 1 ? `<span class="tag">NPIV ${p.wwpns.length}</span>` : ""}${!p.wwpns.length && (p.peer || p.note) ? `<span class="muted">${esc([p.peer, p.note].filter(Boolean).join(" "))}</span>` : ""}</div>${p.wwpns.length ? `<div class="sub mono">${p.wwpns.map(esc).join(", ")}</div>` : ""}</td>
        <td class="num mono">${fmtRate(p.tx_rate)}</td><td class="num mono">${fmtRate(p.rx_rate)}${lr && (p.tx_rate != null || p.rx_rate != null) ? `<div class="util" title="About ${pct.toFixed(0)}% of ${esc(p.speed)} line rate"><span style="width:${pct.toFixed(1)}%"></span></div>` : ""}</td>
        <td>${spark(L.hist[p.id])}</td>
        <td class="num mono">${p.unit === "bytes" ? `${fmtBytes(p.tx)} / ${fmtBytes(p.rx)}` : `${fmtCount(p.tx)} / ${fmtCount(p.rx)} fr`}</td>
        <td>${e ? Object.entries(p.errors).filter(([, v]) => v).map(([k, v]) => `<span class="tag err">${esc(k.replace("_", " "))} ${fmtCount(v)}</span>`).join(" ") : '<span class="muted">0</span>'}</td>
        <td class="act"><button class="btn sm ghost" data-act="pedit" data-v="${esc(p.id)}">Configure</button></td></tr>`; }).join("") || `<tr><td colspan="13" class="sub">No ports match.</td></tr>`}
      </tbody></table></div>`;
  }
  const upd = d ? new Date(d.at).toLocaleTimeString("en-GB") : "";
  $("#main").innerHTML = `<div class="head"><h1>Live data<span class="dot">.</span></h1><span class="sp"></span>
    ${needCred ? "" : `<input type="search" id="q-live" placeholder="Port, pWWN, alias or host" value="${esc(L.q)}" aria-label="Filter ports">
    <label class="checkline"><input type="checkbox" id="liveActive"${L.active ? " checked" : ""}> Active ports only</label>
    <select id="liveSort" aria-label="Sort"><option value="port"${L.sort === "port" ? " selected" : ""}>Sort by port</option><option value="busy"${L.sort === "busy" ? " selected" : ""}>Busiest first</option><option value="errors"${L.sort === "errors" ? " selected" : ""}>Errors first</option></select>
    <select id="liveAuto" aria-label="Auto refresh"><option value="0">Auto refresh off</option>${[15, 30, 60].map(s => `<option value="${s}"${L.auto === s ? " selected" : ""}>Every ${s} s</option>`).join("")}</select>
    <button class="btn primary" data-act="liveRefresh"${L.busy ? " disabled" : ""}>${L.busy ? '<span class="spin"></span> Refreshing' : "Refresh"}</button>`}</div>
    <p class="lede">Port state, the pWWNs logged in on each port and TX/RX traffic for ${esc(m.switch.name)}, read with show commands only.${upd ? ` Last updated <b>${upd}</b>${d.ms ? ` (took ${(d.ms / 1000).toFixed(1)} s)` : ""}.` : ""}${S.cred && !m.demo ? ' <button class="link" data-act="liveForget">Forget credentials</button>' : ""}</p>
    ${L.err ? `<div class="note err">${esc(L.err)}</div>` : ""}${body}`;
  const f = $("#liveForm");
  if (f) f.addEventListener("submit", e => { e.preventDefault();
    S.cred = {host:val(f, "host"), port:+val(f, "port") || 22, username:val(f, "username"), password:f.elements.password.value, platform:m.platform, fid: f.elements.fid && val(f, "fid") ? +val(f, "fid") : null};
    liveRefresh(); });
  const qi = $("#q-live"); if (qi) qi.addEventListener("input", () => { L.q = qi.value; const p = qi.selectionStart; render(); const n = $("#q-live"); n.focus(); n.setSelectionRange(p, p); });
  const la = $("#liveActive"); if (la) la.onchange = () => { L.active = la.checked; render(); };
  const ls = $("#liveSort"); if (ls) ls.onchange = () => { L.sort = ls.value; render(); };
  const au = $("#liveAuto"); if (au) au.onchange = () => { setAuto(+au.value); if (+au.value) liveRefresh(); };
  const sa = $("#selAll"); if (sa) sa.onchange = () => { const shown = [...document.querySelectorAll("input[data-act=psel]")].map(x => x.dataset.v); shown.forEach(id => sa.checked ? L.sel.add(id) : L.sel.delete(id)); render(); };
  bindPortHover();
  if (!needCred && !d && !L.busy && !L.err) liveRefresh();
}

/* ---------- port configuration (staged into the change plan, never pushed) */
const FOS_MODES = [["auto", "No lock (auto)"], ["F", "Locked F_Port (hosts and storage)"], ["G", "Locked G_Port"], ["noE", "E_Port disabled"]];
const NX_MODES = [["auto", "auto"], ["F", "F (fabric port)"], ["E", "E (ISL)"], ["Fx", "Fx"], ["NP", "NP (NPV uplink)"], ["SD", "SD (SPAN)"]];
const SPEEDS = [["auto", "Auto-negotiate"], ["4", "4G"], ["8", "8G"], ["16", "16G"], ["32", "32G"], ["64", "64G"]];
const modeLabel = (plat, v) => ((plat === "fos" ? FOS_MODES : NX_MODES).find(x => x[0] === v) || [v, v])[1];
function effCfg(m, p){ const pe = (m.ports_pending || {})[p.id]; return {...(p.cfg || {}), ...(pe ? pe.target : {})}; }
function stagePort(m, p, upd, clear){
  m.ports_pending = m.ports_pending || {};
  const ex = m.ports_pending[p.id] || {port:p.port, current:clone(p.cfg || {}), target:{}, clear:false, wwpns:p.wwpns};
  ex.wwpns = p.wwpns;
  Object.assign(ex.target, upd);
  for (const k of Object.keys(ex.target)) if (String(ex.current[k]) === String(ex.target[k])) delete ex.target[k];
  if (clear !== undefined) ex.clear = clear;
  if (!Object.keys(ex.target).length && !ex.clear) delete m.ports_pending[p.id]; else m.ports_pending[p.id] = ex;
}
function selBar(d){
  const L = S.live, m = S.work; const n = L.sel.size;
  const pend = Object.keys(m.ports_pending || {}).length;
  return `${pend ? `<div class="note warn"><b>${pend} port${pend > 1 ? "s have" : " has"} pending changes.</b> They are added to the commands on the <button class="link" data-act="nav" data-v="changes">Changes</button> page. Nothing is sent to the switch.</div>` : ""}
  ${n ? `<div class="bulkbar"><b>${n} port${n > 1 ? "s" : ""} selected</b><span class="sp"></span>
    <button class="btn sm primary" data-act="pbulk">Configure selected</button><button class="btn sm" data-act="pquick" data-v="enable">Enable</button><button class="btn sm" data-act="pquick" data-v="disable">Disable</button>
    <button class="btn sm" data-act="pquick" data-v="clear">Clear counters</button><button class="btn sm ghost" data-act="pclearsel">Clear selection</button></div>` : ""}`;
}
function portDialog(ids){
  const m = S.work, L = S.live, d = L.data; const fos = m.platform === "fos";
  const ports = ids.map(id => d.ports.find(p => p.id === id)).filter(Boolean); if (!ports.length) return;
  const bulk = ports.length > 1, p0 = ports[0], e0 = effCfg(m, p0);
  const sel = (name, label, opts, cur, hint) => `<div class="field"><label for="pf-${name}">${label}</label><select id="pf-${name}" name="${name}">${bulk ? '<option value="">No change</option>' : ""}${opts.map(([v, t]) => `<option value="${esc(v)}"${!bulk && String(cur) === String(v) ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
  const stateOpts = fos ? [["on", "Enabled"], ["off", "Disabled"], ["poff", "Disabled (persistent)"]] : [["on", "Enabled (no shutdown)"], ["off", "Shut down"]];
  const stateCur = e0.enabled === false ? (fos && e0.persistent ? "poff" : "off") : "on";
  const devs = ports.reduce((a, p) => a + p.wwpns.length, 0);
  const pend = !bulk && (m.ports_pending || {})[p0.id];
  const speedHint = !bulk && p0.speed ? `Running at ${esc(p0.speed)}${/G$/.test(p0.speed) ? "" : "G"} now.` : "";
  const html = `${devs ? `<div class="note warn" style="margin:0">${devs} logged-in device${devs > 1 ? "s use" : " uses"} ${bulk ? "these ports" : "this port"}. Disabling, or changing speed, type, trunking or VSAN, interrupts ${devs > 1 ? "their paths" : "its path"}.</div>` : ""}
    <div class="form">
      ${bulk ? `<div class="field full"><label>Ports</label><div class="mono">${ports.map(p => esc(p.port)).join(", ")}</div></div>` : `<div class="field full"><label for="pf-name">${fos ? "Port name" : "Description"}</label><input type="text" id="pf-name" name="name" maxlength="${fos ? 64 : 80}" value="${esc(e0.name || "")}" placeholder="${fos ? "ESX01_HBA0" : "ESX01 HBA0"}"></div>`}
      ${sel("state", "State", stateOpts, stateCur)}
      ${sel("speed", "Speed", SPEEDS, e0.speed || "auto", speedHint)}
      ${sel("mode", fos ? "Port type lock" : "Port mode", fos ? FOS_MODES : NX_MODES, e0.mode || "auto", fos ? "Lock host and storage ports as F_Port; leave ISLs on auto." : "")}
      ${fos ? sel("npiv", "NPIV", [["1", "On"], ["0", "Off"]], e0.npiv === false ? "0" : "1") + sel("trunk", "Trunking", [["1", "On"], ["0", "Off"]], e0.trunk === false ? "0" : "1")
            : sel("trunk", "Trunk mode", [["on", "on"], ["off", "off"], ["auto", "auto"]], e0.trunk || "on", "F ports to hosts are usually off.") +
              `<div class="field"><label for="pf-vsan">VSAN</label><input type="number" id="pf-vsan" name="vsan" min="1" max="4094" list="vsanList" value="${bulk ? "" : esc(e0.vsan ?? "")}" placeholder="${bulk ? "No change" : ""}"><datalist id="vsanList">${(m.vsans || []).map(v => `<option value="${v}">${esc(m.vsan_names[v] || "")}</option>`).join("")}</datalist></div>`}
      <label class="checkline full"><input type="checkbox" name="clear"${pend && pend.clear ? " checked" : ""}> Clear the port's counters</label>
    </div>
    ${pend ? `<p class="muted" style="margin:0">This port already has pending changes. <button type="button" class="link" data-act="prevert" data-v="${esc(p0.id)}">Revert them</button></p>` : ""}`;
  ask({title: bulk ? `Configure ${ports.length} ports` : `Configure ${fos ? "port " : ""}${p0.port}`, ok:"Stage changes", wide:true, html,
    onOk: f => {
      const v = n => f.elements[n] ? f.elements[n].value : "";
      const upd = {};
      if (!bulk){ const nm = v("name").trim(); if (fos ? !/^[A-Za-z0-9 _.\-/:]{0,64}$/.test(nm) : /["'\n]/.test(nm)) return fos ? "Port names can use letters, digits, space and _ . - / :" : "Descriptions cannot contain quotes."; upd.name = nm; }
      const st = v("state"); if (st){ upd.enabled = st === "on"; if (fos) upd.persistent = st === "poff"; }
      if (v("speed")) upd.speed = v("speed");
      if (v("mode")) upd.mode = v("mode");
      if (fos){ if (v("npiv")) upd.npiv = v("npiv") === "1"; if (v("trunk")) upd.trunk = v("trunk") === "1"; }
      else { if (v("trunk")) upd.trunk = v("trunk"); if (v("vsan")){ const n = +v("vsan"); if (!(n >= 1 && n <= 4094)) return "VSAN must be 1 to 4094."; upd.vsan = n; } }
      const clear = f.elements.clear.checked;
      change(bulk ? `Staged changes for ${ports.length} ports` : `Staged changes for ${fos ? "port " : ""}${p0.port}`, mm => { ports.forEach(p => stagePort(mm, p, {...upd}, clear)); return {note:"Review them on the Changes page."}; });
    }});
}
let hoverT = null;
function pcardEl(){ let c = $("#pcard"); if (!c){ c = document.createElement("div"); c.id = "pcard"; c.className = "pcard"; c.setAttribute("role", "tooltip"); c.hidden = true; document.body.appendChild(c); } return c; }
function hideCard(){ clearTimeout(hoverT); const c = $("#pcard"); if (c) c.hidden = true; }
function portCard(p){
  const m = S.work, D = derive(m), fos = m.platform === "fos", L = S.live;
  const c = p.cfg || {}; const pe = (m.ports_pending || {})[p.id];
  const lr = lineRate(p.speed); const busy = (p.tx_rate || 0) + (p.rx_rate || 0);
  const kv = (k, v) => v === undefined || v === null || v === "" ? "" : `<dt>${k}</dt><dd>${v}</dd>`;
  const onoff = b => b === undefined ? "" : b ? "On" : "Off";
  const devs = p.wwpns.map(w => { const dv = m.devices[w] || {}; const r = D.role(w); const al = D.label(w); const g = m.groups[w];
    const zc = D.zonesOf(w).length;
    return `<li><span class="chip r-${r}">${esc(al === w ? "No alias" : al)}</span> <span class="mono">${esc(w)}</span><div class="muted">${[ROLE[r], g && g !== "Unassigned" ? g : "", dv.vendor, dv.node, p.fcids && p.fcids[w] ? "FCID " + p.fcids[w] : "", `${zc} zone${zc === 1 ? "" : "s"}`].filter(Boolean).map(esc).join(" · ")}</div></li>`; }).join("");
  const errs = Object.entries(p.errors || {}).filter(([, v]) => v);
  return `<div class="pc-h"><b class="mono">${fos ? "Port " : ""}${esc(p.port)}</b>${c.name ? `<span>${esc(c.name)}</span>` : ""}<span class="sp"></span><span class="dotst${p.online ? " on" : ""}"></span>${esc(p.state)}</div>
    <dl class="pc-kv">
      ${kv("Type", esc(p.type || "none"))}${kv("Speed", `${esc(p.speed || "–")}${/G$/.test(p.speed) || !p.speed ? "" : "G"} <span class="muted">(set: ${esc(c.speed === "auto" ? "auto" : (c.speed || "?") + "G")})</span>`)}
      ${kv(fos ? "Lock" : "Mode", esc(modeLabel(m.platform, c.mode || "auto")))}${!fos ? kv("VSAN", esc(p.vsan ?? "")) : ""}
      ${fos ? kv("Index", esc(p.id)) + kv("Address", esc(p.address || "")) : ""}${kv("Media", esc(p.media || ""))}
      ${fos ? kv("NPIV", onoff(c.npiv)) + kv("Trunking", onoff(c.trunk)) + kv("Persistent disable", c.persistent ? "On" : "") : kv("Trunk mode", esc(c.trunk || ""))}
      ${p.peer ? kv("Neighbour", `<span class="mono">${esc(p.peer)}</span> ${esc(p.note || "")}`) : p.note ? kv("Note", esc(p.note)) : ""}
    </dl>
    <div class="pc-s"><div class="k">Connected (${p.wwpns.length})</div>${devs ? `<ul class="pc-dev">${devs}</ul>` : '<div class="muted">Nothing logged in</div>'}</div>
    <div class="pc-s pc-traffic"><div><div class="k">TX</div><b>${fmtRate(p.tx_rate)}</b> MB/s</div><div><div class="k">RX</div><b>${fmtRate(p.rx_rate)}</b> MB/s</div><div><div class="k">Load</div><b>${lr && (p.tx_rate != null) ? ((busy / 2) / lr * 100).toFixed(0) + "%" : "–"}</b></div><div><div class="k">Total</div>${p.unit === "bytes" ? `${fmtBytes(p.tx)} / ${fmtBytes(p.rx)}` : `${fmtCount(p.tx)} / ${fmtCount(p.rx)} fr`}</div></div>
    ${L.hist[p.id] && L.hist[p.id].length > 1 ? `<div class="pc-s">${spark(L.hist[p.id])}</div>` : ""}
    ${errs.length ? `<div class="pc-s"><div class="k">Errors</div>${errs.map(([k, v]) => `<span class="tag err">${esc(k.replace("_", " "))} ${fmtCount(v)}</span>`).join(" ")}</div>` : ""}
    ${pe ? `<div class="pc-s pc-pend"><div class="k">Pending</div>${Object.entries(pe.target).map(([k, v]) => `<div>${esc(k)}: ${esc(String(pe.current[k] ?? ""))} → <b>${esc(String(v))}</b></div>`).join("")}${pe.clear ? "<div>clear counters</div>" : ""}</div>` : ""}
    <div class="pc-foot muted">Click the port to configure it</div>`;
}
function showCard(pid, anchor){
  const L = S.live; if (!L || !L.data || !anchor) return;
  const p = L.data.ports.find(x => x.id === pid); if (!p) return;
  const c = pcardEl(); c.innerHTML = portCard(p); c.hidden = false;
  const r = anchor.getBoundingClientRect(); const cw = c.offsetWidth, ch = c.offsetHeight;
  let x = r.right + 14, y = r.top - 8;
  if (x + cw > window.innerWidth - 12) x = Math.max(12, r.left - cw - 14);
  if (y + ch > window.innerHeight - 12) y = Math.max(12, window.innerHeight - ch - 12);
  c.style.left = x + "px"; c.style.top = y + "px";
}
function bindPortHover(){
  const tb = document.querySelector("table.live tbody"); if (!tb) return;
  tb.addEventListener("mouseover", e => { const tr = e.target.closest("tr[data-pid]"); if (!tr) return; clearTimeout(hoverT); hoverT = setTimeout(() => showCard(tr.dataset.pid, tr.querySelector(".portbtn")), 250); });
  tb.addEventListener("mouseout", e => { if (!e.relatedTarget || !tb.contains(e.relatedTarget)) hideCard(); });
  tb.querySelectorAll(".portbtn").forEach(b => { b.addEventListener("focus", () => showCard(b.dataset.pid, b)); b.addEventListener("blur", hideCard); });
}
document.addEventListener("keydown", e => { if (e.key === "Escape") hideCard(); });
window.addEventListener("scroll", hideCard, true);

function hl(line){
  const e = esc(line);
  if (/^\s*[!#]/.test(line)) return `<span class="cm">${e}</span>`;
  return e.replace(/^(\s*)(no |)(\S+(?: \S+)?)/, (m0, sp, no, cmd) => `${sp}${no ? `<span class="wn">no </span>` : ""}<span class="kw">${cmd}</span>`);
}
function vChanges(){
  const p = S.plan;
  const has = p && p.count;
  const plat = S.work.platform;
  const fname = `${S.work.switch.name || "switch"}-${new Date().toISOString().slice(0, 10)}-zoning.txt`;
  $("#main").innerHTML = `<div class="head"><h1>Changes<span class="dot">.</span></h1><span class="sp"></span>
    <button class="btn" data-act="undo"${S.undo.length ? "" : " disabled"}>Undo${S.undo.length ? ": " + esc(S.undo[S.undo.length - 1].label) : ""}</button>
    <button class="btn danger" data-act="discard"${has ? "" : " disabled"}>Discard all</button></div>
  <div class="note"><b>Nothing has been sent to ${esc(S.work.switch.name)}.</b> Review the commands, then paste them into an SSH session yourself. Pull the switch again afterwards to confirm.</div>
  ${!p ? '<div class="empty"><span class="spin"></span> Building commands</div>' : !has ? '<div class="empty"><h3>No changes yet</h3>Rename aliases, change host access or edit zones, and the commands appear here.</div>' : `
  ${p.warnings && p.warnings.length ? `<div class="note err"><b>Check before running</b><ul class="arrows">${p.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
  <div class="cols">
    <section><h3>${p.count} change${p.count > 1 ? "s" : ""}</h3><ul class="chlist">${p.changes.map(([s, t]) => `<li><span class="s">${esc(s)}</span><span>${esc(t)}</span></li>`).join("")}</ul></section>
    <section style="min-width:0"><div class="sec-h"><h3>Commands for ${plat === "fos" ? "Brocade FOS" : "Cisco MDS NX-OS"}</h3><span class="sp"></span>
      <label class="checkline"><input type="checkbox" id="optCom"${S.comments ? " checked" : ""}> Comments</label>
      <button class="btn sm" data-act="copy">Copy</button><button class="btn sm primary" data-act="download" data-v="${esc(fname)}">Download .txt</button></div>
      ${plat === "fos" && p.header && p.header.length ? `<p class="muted" style="margin:0 0 6px;font-size:12px">${esc(p.header[1] || "")}</p>` : ""}
      <pre class="cmds" id="cmds">${p.commands.trimEnd().split("\n").map(hl).join("\n")}</pre></section>
  </div>`}`;
  const oc = $("#optCom"); if (oc) oc.onchange = () => { S.comments = oc.checked; refreshPlan(); };
}

/* ------------------------------------------------------------------ actions */
const D_ = () => derive(S.work);
function groupOptions(kind, current){
  const D = D_(); const names = [...(kind === "san" ? D.sans : D.hosts).keys()].sort(byName);
  return names.map(n => `<option value="${esc(n)}"${n === current ? " selected" : ""}>${esc(n)}</option>`).join("");
}
function aliasDialog({title, name = "", wwpns = "", role = "initiator", group = "", lockRole = false}){
  const multi = isFOS();
  ask({title, ok:"Create alias", html:`<div class="form">
    <div class="field full"><label for="aName">Alias name</label><input type="text" id="aName" name="name" value="${esc(name)}" required><span class="hint">${nameRule()}</span></div>
    <div class="field full"><label for="aW">WWPN${multi ? "s" : ""}</label><input type="text" id="aW" name="wwpns" class="mono" value="${esc(wwpns)}" placeholder="10:00:00:10:9b:4a:11:05" list="wList">${multi ? '<span class="hint">Separate several with ; or commas.</span>' : ""}
      <datalist id="wList">${Object.entries(S.work.devices).filter(([w]) => !D_().byW.has(w)).map(([w, d]) => `<option value="${w}">${esc(ROLE[d.role])} ${esc(d.node || "")}</option>`).join("")}</datalist></div>
    <div class="field"><label for="aRole">Role</label><select id="aRole" name="role"${lockRole ? " disabled" : ""}>${["initiator", "target", "both"].map(r => `<option value="${r}"${r === role ? " selected" : ""}>${ROLE[r]}</option>`).join("")}</select></div>
    <div class="field"><label for="aGrp">Host or SAN</label><input type="text" id="aGrp" name="group" value="${esc(group)}" list="gList" placeholder="ESX05 or HP3PAR01"><datalist id="gList">${groupOptions("host")}${groupOptions("san")}</datalist></div></div>`,
    onOk: f => {
      const n = val(f, "name"); const raw = val(f, "wwpns").split(/[;,\s]+/).filter(Boolean);
      const ws = raw.map(normWWN); if (ws.some(x => !x) || !ws.length) return "Enter valid WWPNs like 10:00:00:10:9b:4a:11:05.";
      const r = lockRole ? role : val(f, "role"); const g = val(f, "group") || n;
      let err = null;
      change(`Created alias ${n}`, m => { const x = opCreateAlias(m, {name:n, wwpns:[...new Set(ws)], role:r, group:g}); if (x && x.error){ err = x.error; return x; } });
      return err;
    }});
}
const ACT = {
  nav: el => { S.view = el.dataset.v; save(); render(); $("#main").focus(); },
  liveRefresh: () => liveRefresh(),
  pedit: el => { hideCard(); portDialog([el.dataset.v]); },
  pbulk: () => { hideCard(); portDialog([...S.live.sel]); },
  pclearsel: () => { S.live.sel.clear(); render(); },
  prevert: el => { $("#dlg").close(); change(`Reverted pending changes on ${el.dataset.v}`, m => { delete (m.ports_pending || {})[el.dataset.v]; }); },
  pquick: el => { const L = S.live; const ports = [...L.sel].map(id => L.data.ports.find(p => p.id === id)).filter(Boolean); if (!ports.length) return;
    const what = el.dataset.v; const fos = S.work.platform === "fos";
    change(`Staged ${what === "clear" ? "counter clear" : what} for ${ports.length} port${ports.length > 1 ? "s" : ""}`, m => {
      ports.forEach(p => what === "clear" ? stagePort(m, p, {}, true) : stagePort(m, p, fos ? {enabled: what === "enable", persistent: false} : {enabled: what === "enable"}));
      return {note:"Review them on the Changes page."}; }); },
  liveForget: () => { S.cred = null; if (S.live) setAuto(0); render(); toast("Credentials cleared from this tab"); },
  goGroup: el => { const w = el.dataset.w; const D = D_(); const g = S.work.groups[w]; if (!g) return;
    if (D.sanOf(w)){ S.selSan = g; S.view = "sans"; } else { S.selHost = g; S.view = "hosts"; } save(); render(); },
  "sel-host": el => { S.selHost = el.dataset.v; render(); },
  "sel-san": el => { S.selSan = el.dataset.v; render(); },
  demo: async el => { try { load(await api(`/api/demo/${el.dataset.v}`), "hosts"); toast("Loaded demo data"); } catch (e) { toast(esc(e.message), "warn"); } },
  impFile: () => $("#impFile").click(),
  import: async () => {
    const t = $("#impText").value; if (!t.trim()) { toast("Paste some config first.", "warn"); return; }
    try { const res = await api("/api/parse", {platform:$("#impPlat").value, texts:{paste:t}}); load({...res, log:[{cmd:"(imported text)", bytes:t.length, ms:0}]}, "hosts"); toast(`Imported ${res.model.aliases.length} aliases, ${res.model.zones.length} zones`); }
    catch (e) { toast(esc(e.message), "warn"); }
  },
  access: el => {
    const h = el.dataset.h, s = el.dataset.s, on = el.checked;
    if (!change(`${on ? "Granted" : "Removed"} ${h} access to ${s}`, m => opSetAccess(m, h, s, on))) el.checked = !on;
  },
  revoke: el => change(`Removed ${el.dataset.h} access to ${el.dataset.s}`, m => opSetAccess(m, el.dataset.h, el.dataset.s, false)),
  grant: el => {
    const s = el.dataset.v; const D = D_();
    const cand = [...D.hosts.keys()].filter(h => !(D.access.get(h) && D.access.get(h).has(s))).sort(byName);
    if (!cand.length){ toast("Every host already reaches this SAN."); return; }
    ask({title:`Grant access to ${s}`, ok:"Grant access", html:`<div class="field"><label>Hosts</label><div class="picklist">${cand.map(h => `<label><input type="checkbox" name="h" value="${esc(h)}"> ${esc(h)} <span class="muted">(${D.hosts.get(h).length} initiators)</span></label>`).join("")}</div></div>`,
      onOk: f => { const hs = [...f.querySelectorAll("input[name=h]:checked")].map(x => x.value); if (!hs.length) return "Pick at least one host.";
        let err = null; change(`Granted ${hs.length} host${hs.length > 1 ? "s" : ""} access to ${s}`, m => { const notes = []; for (const h of hs){ const r = opSetAccess(m, h, s, true); if (r && r.error){ err = r.error; return r; } if (r && r.note) notes.push(r.note); } return {note:[...new Set(notes)].join(" ")}; }); return err; }});
  },
  groupRename: el => {
    const kind = el.dataset.k, old = el.dataset.v;
    ask({title:`Rename ${kind === "host" ? "host" : "SAN"} ${old}`, ok:"Rename", html:`<div class="field"><label for="gN">New name</label><input type="text" id="gN" name="n" value="${esc(old)}" required></div>
      <label class="checkline"><input type="checkbox" name="ra" checked> Rename aliases that start with ${esc(old)} (for example ${esc(old)}_${kind === "host" ? "HBA0" : "N0S1P1"})</label>
      <label class="checkline"><input type="checkbox" name="rz" checked> Rename zones whose name contains ${esc(old)}</label>`,
      onOk: f => { const n = val(f, "n"); if (!n) return "Enter a name."; if (n === old) return null; if (!/^[A-Za-z][A-Za-z0-9_\-]*$/.test(n)) return "Use letters, digits, _ and -, starting with a letter.";
        let err = null; change(`Renamed ${old} to ${n}`, m => { const r = opRenameGroup(m, kind, old, n, {aliases:f.elements.ra.checked, zones:f.elements.rz.checked}); if (r && r.error) err = r.error; return r; });
        if (!err){ if (kind === "host") S.selHost = n; else S.selSan = n; render(); } return err; }});
  },
  addPort: el => {
    const kind = el.dataset.k, g = el.dataset.v; const D = D_();
    const n = ((kind === "host" ? D.hosts : D.sans).get(g) || []).length;
    aliasDialog({title:`Add ${kind === "host" ? "initiator to " : "target port to "}${g}`, name:`${g}_${kind === "host" ? "HBA" : "P"}${n}`, role: kind === "host" ? "initiator" : "target", group:g});
  },
  aliasNew: () => aliasDialog({title:"New alias"}),
  aliasForW: el => { const w = el.dataset.v; const d = S.work.devices[w] || {}; const g = S.work.groups[w] && S.work.groups[w] !== "Unassigned" ? S.work.groups[w] : (d.node || "").split(".")[0];
    const base = cleanName((g || "DEV").toUpperCase()); aliasDialog({title:`Alias for ${w}`, name:`${base}_${d.role === "target" ? "P" : "HBA"}0`, wwpns:w, role:d.role === "unknown" ? "initiator" : d.role, group:g ? g.toUpperCase() : ""}); },
  aliasRename: el => { const a = S.work.aliases.find(x => x.id === el.dataset.v); if (!a) return;
    ask({title:`Rename alias ${a.name}`, ok:"Rename", html:`<div class="field"><label for="rn">New name</label><input type="text" id="rn" name="n" value="${esc(a.name)}" required><span class="hint">${nameRule()} Zones that use it keep working.</span></div>`,
      onOk: f => { const n = val(f, "n"); if (n === a.name) return null; if (!validName(n)) return nameRule(); if (aliasNameTaken(S.work, n, a.id)) return `${n} already exists.`;
        change(`Renamed ${a.name} to ${n}`, m => { m.aliases.find(x => x.id === a.id).name = n; }); }}); },
  aliasEdit: el => { const a = S.work.aliases.find(x => x.id === el.dataset.v); if (!a) return; const D = D_();
    const w0 = a.wwpns[0] || ""; const role = D.role(w0);
    ask({title:`Edit ${a.name}`, html:`<div class="form"><div class="field full"><label for="eW">WWPN${a.kind === "device-alias" ? "" : "s"}</label><input type="text" id="eW" name="w" class="mono" value="${esc(a.wwpns.join("; "))}"></div>
      <div class="field"><label for="eR">Role</label><select id="eR" name="r">${["initiator", "target", "both", "unknown"].map(r => `<option value="${r}"${r === role ? " selected" : ""}>${ROLE[r]}</option>`).join("")}</select><span class="hint">Used for grouping and new smart zone members.</span></div>
      <div class="field"><label for="eG">Host or SAN</label><input type="text" id="eG" name="g" value="${esc(S.work.groups[w0] || "")}" list="gList2"><datalist id="gList2">${groupOptions("host")}${groupOptions("san")}</datalist></div></div>`,
      onOk: f => { const ws = val(f, "w").split(/[;,\s]+/).filter(Boolean).map(normWWN); if (!ws.length || ws.some(x => !x)) return "Enter valid WWPNs.";
        if (a.kind === "device-alias" && ws.length !== 1) return "A device-alias maps to exactly one WWPN.";
        const r = val(f, "r"), g = val(f, "g");
        change(`Updated ${a.name}`, m => { const x = m.aliases.find(y => y.id === a.id); x.wwpns = [...new Set(ws)];
          x.wwpns.forEach(w => { if (!m.devices[w]) m.devices[w] = {role:r, role_src:"Manual", vendor:"", online:false, node:""}; if (m.devices[w].role !== r){ m.devices[w].role = r; m.devices[w].role_src = "Manual"; } if (g) m.groups[w] = g; }); }); }}); },
  aliasDelete: el => { const a = S.work.aliases.find(x => x.id === el.dataset.v); if (!a) return;
    const zs = S.work.zones.filter(z => z.members.some(mm => mm.kind === "alias" && mm.ref === a.id)).map(z => z.name);
    ask({title:`Delete alias ${a.name}?`, ok:"Delete", html:`<p>${zs.length ? `It is a member of ${zs.length} zone${zs.length > 1 ? "s" : ""}: <b>${esc(zs.join(", "))}</b>. It will be removed from them, and zones left empty are deleted.` : "It is not used in any zone."}</p>`,
      onOk: () => { change(`Deleted alias ${a.name}`, m => opDeleteAlias(m, a.id)); }}); },
  movePort: el => { const w = el.dataset.v, kind = el.dataset.k; const cur = S.work.groups[w] || "";
    ask({title:`Move ${D_().label(w)}`, ok:"Move", html:`<div class="field"><label for="mv">${kind === "host" ? "Host" : "SAN"}</label><input type="text" id="mv" name="g" value="${esc(cur)}" list="mvList" required><datalist id="mvList">${groupOptions(kind)}</datalist><span class="hint">Pick an existing name or type a new one. Grouping is kept in this tool; it does not change the switch.</span></div>`,
      onOk: f => { const g = val(f, "g"); if (!g) return "Enter a name."; change(`Moved ${D_().label(w)} to ${g}`, m => { m.groups[w] = g; }); }}); },
  tag: el => { const z = el.dataset.z, i = +el.dataset.i, t = el.value || null; change("Changed member type", m => { m.zones.find(x => x.id === z).members[i].tag = t; }); },
  zrm: el => { const zid = el.dataset.z, i = +el.dataset.i; const z = S.work.zones.find(x => x.id === zid); const D = D_(); const mm = z.members[i];
    const nm = mm.kind === "alias" ? D.al.get(mm.ref)?.name : mm.ref;
    change(`Removed ${nm} from ${z.name}`, m => { const zz = m.zones.find(x => x.id === zid); zz.members.splice(i, 1); }); },
  zadd: el => { const zid = el.dataset.v; const inp = document.querySelector(`[data-addfor="${CSS.escape(zid)}"]`); const v = inp ? inp.value.trim() : ""; if (!v) return;
    const a = S.work.aliases.find(x => x.name.toLowerCase() === v.toLowerCase()); const w = normWWN(v);
    if (!a && !w){ toast(`${esc(v)} is not an alias or a WWPN.`, "warn"); return; }
    change(`Added ${a ? a.name : w} to zone`, m => { const z = m.zones.find(x => x.id === zid); const D = derive(m);
      const ws = a ? a.wwpns : [w]; if (z.members.some(mm => a ? (mm.kind === "alias" && mm.ref === a.id) : (mm.kind === "wwpn" && mm.ref === w))) return {error:"Already a member."};
      const r = D.role(ws[0]); const tag = D.smart ? (r === "unknown" ? null : r) : null;
      z.members.push({kind: a ? "alias" : "wwpn", ref: a ? a.id : w, tag, principal: z.peer ? r === "target" : false}); }); },
  zrename: el => { const z = S.work.zones.find(x => x.id === el.dataset.v);
    ask({title:`Rename zone ${z.name}`, ok:"Rename", html:`<div class="field"><label for="zn">New name</label><input type="text" id="zn" name="n" value="${esc(z.name)}" required><span class="hint">${nameRule()}</span></div>`,
      onOk: f => { const n = val(f, "n"); if (n === z.name) return null; if (!validName(n)) return nameRule(); if (zoneNameTaken(S.work, n, z.id)) return `${n} already exists.`;
        change(`Renamed zone ${z.name} to ${n}`, m => { m.zones.find(x => x.id === z.id).name = n; }); }}); },
  zdel: el => { const z = S.work.zones.find(x => x.id === el.dataset.v);
    ask({title:`Delete zone ${z.name}?`, ok:"Delete", html:`<p>The zone is removed from every config and deleted. Its ${z.members.length} members lose the access it gave them.</p>`,
      onOk: () => { change(`Deleted zone ${z.name}`, m => deleteZone(m, z.id)); }}); },
  zcfg: el => { const zid = el.dataset.v; const cfg = activeCfg(S.work); if (!cfg){ toast("No active config.", "warn"); return; } const z = S.work.zones.find(x => x.id === zid);
    const inC = cfg.zones.includes(zid);
    change(`${inC ? "Removed" : "Added"} ${z.name} ${inC ? "from" : "to"} ${cfg.name}`, m => { const c = activeCfg(m); c.zones = inC ? c.zones.filter(x => x !== zid) : [...c.zones, zid]; }); },
  znew: () => { const D = D_();
    const opt = (title, map) => [...map.keys()].sort(byName).map(g => `<div><b class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.06em">${esc(title)} ${esc(g)}</b>${map.get(g).map(w => `<label><input type="checkbox" name="w" value="${w}"> ${esc(D.label(w))} <span class="muted mono">${w}</span></label>`).join("")}</div>`).join("");
    ask({title:"New zone", ok:"Create zone", wide:true, html:`<div class="field"><label for="nz">Zone name</label><input type="text" id="nz" name="n" required placeholder="${D.smart ? "SZ_ARRAY01" : isFOS() ? "z_HOST_ARRAY" : "Z_HOST_ARRAY"}"><span class="hint">${nameRule()}</span></div>
      <div class="field"><label>Members</label><div class="picklist">${opt("Host", D.hosts)}${opt("SAN", D.sans)}</div></div>
      <label class="checkline"><input type="checkbox" name="act" checked> Add to the active ${isFOS() ? "config" : "zoneset"}</label>`,
      onOk: f => { const n = val(f, "n"); if (!validName(n)) return nameRule(); if (zoneNameTaken(S.work, n)) return `${n} already exists.`;
        const ws = [...f.querySelectorAll("input[name=w]:checked")].map(x => x.value); if (!ws.length) return "Pick at least one member.";
        change(`Created zone ${n}`, m => { const z = {id:uid(), name:n, peer:false, members:[]}; const DD = derive(m);
          ws.forEach(w => addToZone(m, z, w, DD.role(w) === "unknown" ? null : DD.role(w))); m.zones.push(z); if (f.elements.act.checked && activeCfg(m)) activeCfg(m).zones.push(z.id); }); }}); },
  undo: () => { const u = S.undo.pop(); if (!u) return; S.work = u.model; save(); refreshPlan(); render(); toast(`Undid: ${esc(u.label)}`); },
  discard: () => ask({title:"Discard all changes?", ok:"Discard", html:"<p>Your edits are thrown away and the workspace goes back to what was pulled from the switch.</p>",
    onOk: () => { S.undo.push({label:"Discard all", model:clone(S.work)}); S.work = clone(S.orig); save(); refreshPlan(); render(); toast("Changes discarded"); }}),
  copy: async () => { const t = S.plan ? S.plan.commands : ""; try { await navigator.clipboard.writeText(t); toast("Commands copied"); } catch { const r = document.createRange(); r.selectNodeContents($("#cmds")); const s = getSelection(); s.removeAllRanges(); s.addRange(r); toast("Selected. Press Ctrl+C to copy."); } },
  download: el => { const blob = new Blob([S.plan.commands], {type:"text/plain"}); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = el.dataset.v; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); },
};
document.addEventListener("click", e => {
  const el = e.target.closest("[data-act]"); if (!el || el.disabled) return;
  const a = el.dataset.act;
  if (a === "access" || a === "tag" || a === "psel") return; // handled on change
  if (ACT[a]){ e.preventDefault(); ACT[a](el); }
});
document.addEventListener("change", e => { const el = e.target.closest("[data-act]"); if (!el) return; if (el.dataset.act === "access" || el.dataset.act === "tag") ACT[el.dataset.act](el);
  if (el.dataset.act === "psel"){ const L = S.live; el.checked ? L.sel.add(el.dataset.v) : L.sel.delete(el.dataset.v); render(); } });

/* ------------------------------------------------------------------ boot */
const at = restore();
render();
if (at){ refreshPlan(); toast(`Restored your workspace from ${new Date(at).toLocaleString("en-GB")}`); }
})();
