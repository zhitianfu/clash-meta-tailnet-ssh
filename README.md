# SSH out from an Android phone over a Tailscale tailnet, carried inside Clash Meta

Run a **Tailscale tailnet inside the Clash Meta Android app**, then SSH from Termux to a Linux host
and a macOS host. No Tailscale app. No open port. No path back into the phone.

Verified end to end on a rooted Android phone with Termux, against one Linux mini-PC and one MacBook.

---

## 1. Requirements this solves

- The phone must reach two machines that are **not on its LAN**.
- Only the tools already present: **Termux + Clash Meta**. No extra Android app.
- Clash Meta must keep running 24/7.
- The **phone initiates only**. No machine may open a session to the phone.
- **No port may be open to the internet** on any machine.
- Latency and throughput must stay close to a direct link.

## 2. Why this shape

| Constraint | Consequence |
|---|---|
| Android permits one active VPN at a time | A second VPN app cannot coexist with Clash Meta |
| Both peers sit behind NAT, no public endpoint | A rendezvous service is required |
| The Tailscale Linux binaries do not run in Termux (non-PIE static ELF; Android refuses `execve`) | The phone needs a tailnet client that Termux can run |
| Clash Meta's core is mihomo (MetacubeX) | Its `tailscale` outbound is exactly that client |

The core is built with `-tags=foss,with_gvisor,cmfa`. The `tailscale` outbound is guarded by
`//go:build with_gvisor && !no_tailscale`, so it is present in that build. Confirm it on the shipped
binary before you rely on it: look for the strings `auth-key`, `state-dir`, `control-url`, `tsnet`,
and the module `github.com/metacubex/tailscale`.

**Result:** one proxy line and one rule in the Clash Meta profile. No second core, and no wrapper
around the SSH client.

## 3. Architecture

```
 Android phone
 ┌────────────────────────────────────────────────┐
 │ Clash Meta  (system VPN, tun, 24/7)             │
 │   rules: IP-CIDR,100.64.0.0/10 -> TS-NET        │
 │   proxy: TS-NET, type: tailscale                │
 │          mihomo tsnet -> userspace WireGuard    │
 └───────────────┬────────────────────────────────┘
                 │  tailnet 100.64.0.0/10
        ┌────────┴─────────┐
        │                  │
   Linux host           macOS host
   OpenSSH + key        Tailscale SSH (identity auth)
   tag:linux            tag:mac
```

## 4. Phone side

### 4.1 Add the outbound to the Clash Meta profile

```yaml
proxies:
  - {name: TS-NET, type: tailscale, hostname: <PHONE_NODE_NAME>, auth-key: <AUTH_KEY>, control-url: https://controlplane.tailscale.com, state-dir: /data/data/<APP_PACKAGE>/files/clash/ts-node, udp: true}

rules:
  - IP-CIDR,100.64.0.0/10,TS-NET,no-resolve
  - DOMAIN-SUFFIX,ts.net,TS-NET
```

Put the two rules **above** the stock `IP-CIDR,100.64.0.0/10,DIRECT` line. Rules are first-match, so
that one line otherwise swallows all tailnet traffic.

### 4.2 Where the profile lives

- `/data/data/<APP_PACKAGE>/files/imported/<PROFILE_UUID>/config.yaml` — source of truth
- `/data/data/<APP_PACKAGE>/files/processing/config.yaml` — what the core actually loaded

Termux cannot list another app's data: its `/data/data` is a tmpfs that holds only its own entry.
Use `su -M` (Magisk mount master) to reach the real directory. Preserve owner and mode on every
write (`10254:10254`, mode `600`).

### 4.3 Validate before you write

Download the official mihomo release binary and parse the whole edited file:

```
mihomo -t -f config.yaml -d <scratch-dir>
```

Two traps:

- The scratch home must contain the geodata files, or `SAFE_PATHS` must list the app home. Otherwise
  the run tries to download the MMDB.
- `state-dir` must be a **lexical** subpath of the core home. `/data/data/<APP_PACKAGE>/files/clash`
  passes; `/data/user/0/<APP_PACKAGE>/...` fails with `path is not subpath of home directory`, even
  though both spellings reach the same directory.

### 4.4 Reload the core

The core reads the profile only when it starts:

```
am start-foreground-service -n <APP_PACKAGE>/com.github.kr328.clash.service.ClashService
```

In practice this restarted the core about half the time, and sometimes left it **down**. A
VpnService can never be raised from a shell — Android permits the app only. Budget one tap on the
app's Start switch or Quick Settings tile.

## 5. Tailnet side

### 5.1 Give the phone a tag

mihomo cannot attach a tag to its node. The tag must come from the **auth key**. Create a reusable,
pre-authorised, non-ephemeral key that carries a tag such as `tag:phone`.

### 5.2 Policy shape (verified)

```json
{
  "tagOwners": {
    "tag:phone": ["autogroup:admin"],
    "tag:linux": ["autogroup:admin"],
    "tag:mac":   ["autogroup:admin"]
  },
  "grants": [
    {"src": ["tag:linux"], "dst": ["tag:mac"],   "ip": ["tcp:22"]},
    {"src": ["tag:mac"],   "dst": ["tag:linux"], "ip": ["tcp:22"]},
    {"src": ["tag:phone"], "dst": ["tag:linux"], "ip": ["tcp:22"]},
    {"src": ["tag:phone"], "dst": ["tag:mac"],   "ip": ["tcp:22"]}
  ],
  "ssh": [
    {"action": "accept",
     "src": ["tag:linux", "tag:mac", "tag:phone"],
     "dst": ["tag:linux", "tag:mac"],
     "users": ["autogroup:nonroot"]}
  ]
}
```

Two rule sets do the work: `grants` open the packet path, and the `ssh` rule authorises Tailscale
SSH. **`dst` never contains `tag:phone`**, so nothing can open a session to the phone.

## 6. Destination side

### Linux host

- Plain OpenSSH, public key only, `AllowUsers` limited to the tailnet range.
- **A host that runs Tailscale SSH hijacks port 22 for the whole tailnet.** The tailnet policy cannot
  switch that off; only the host can: `tailscale set --ssh=false`.
- Tell the two apart by reading the raw banner through the tunnel:
  `SSH-2.0-Tailscale` = intercepted, `SSH-2.0-OpenSSH_*` = the real sshd.

### macOS host

- **No Remote Login is required.** Tailscale SSH serves the session and authorises it by tailnet
  identity.
- This needs the phone's tag in the `ssh` rule's `src`. Without it the host answers
  `tailnet policy does not permit you to SSH to this node`.

## 7. The surprise worth writing down

**Tailscale SSH needs no client certificate on the phone.** A plain `ssh` from the phone reaches a
Tailscale SSH host, and the session reports:

```
Authenticated to <TAILNET_IP> ([<TAILNET_IP>]:22) using "none".
```

Tailscale SSH authorises by tailnet identity, so the policy is the only gate. The first attempt
failed with `tailnet policy does not permit you to SSH to this node` for one reason only: the `ssh`
rule listed other tags as sources, but not the phone's tag. Adding the tag fixed the macOS path with
**no change on the macOS host at all**.

## 8. Verification

```
phone -> linux host : <LINUX_HOSTNAME> / <LINUX_USER>
phone -> mac host   : <MAC_HOSTNAME> / <MAC_USER>
phone -> mac host   : /dev/ttys001        (interactive shell)
```

One-way check, run from the Linux host:

```
$ ssh <PHONE_TAILNET_IP> true
ssh: connect to host <PHONE_TAILNET_IP> port 22: Connection timed out
```

The phone listens on nothing:

```
$ su -M -c 'netstat -tlnp | grep ":22 "'
(no output)
```

## 9. Security properties

- The phone opens **no listener**. `type: tailscale` in mihomo is an outbound; tsnet starts no SSH
  server.
- No policy rule or grant points at the phone. The phone is a source only.
- The Linux host accepts public keys only, from the tailnet range.
- No port is open to the internet on any machine.
- Residual: the tailnet control plane sees the node, and Tailscale SSH on the macOS host grants a
  shell to whatever identities the policy allows. Keep the `ssh` rule narrow, and disable key expiry
  only on the node that must stay unattended.

## 10. Performance

Measured over a 2.4 GHz Wi-Fi link, 20 MB per transfer:

| Path | Time | Rate |
|---|---|---|
| macOS host, over the tunnel | 10.9 s | 1.84 MB/s |
| Linux host, over the tunnel | 10.3 s | 1.93 MB/s |
| Linux host, raw LAN, no tunnel | 11.4 s | 1.75 MB/s |

The tunnel matched the raw LAN, so the phone-side userspace stack was **not** the limit. The Wi-Fi
link was (25 Mbps, physical rate). Expect much higher rates on 5 GHz or mobile data.

## 11. Runbook

1. Change the profile, validate it with `mihomo -t`, then reload the core.
2. **An in-app profile Update erases these lines.** Keep `scripts/apply-tailnet-block.py` and re-run
   it, then toggle the VPN.
3. Exclude Clash Meta and Termux from battery optimisation, or the 24/7 requirement fails.
4. tsnet state trap: `Authkey is set; but state is <X>. Ignoring authkey`. A state directory left
   mid-login never uses the key again. Point `state-dir` at a **fresh** folder to recover. A fresh
   folder does use the key.

## Files

- `clash-meta/tailnet-block.yaml` — the two lines to add, as a fragment.
- `scripts/apply-tailnet-block.py` — idempotent injector. Takes every path and name from the
  environment, so nothing site-specific is baked in.

## Licence

MIT. See `LICENSE`.
