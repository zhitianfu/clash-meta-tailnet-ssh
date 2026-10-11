#!/usr/bin/env python3
"""Inject the mihomo `tailscale` outbound and the tailnet rules into a Clash Meta profile.

Idempotent. Run as ROOT on a rooted Android device, because the profile lives in app-private
storage. Every path and name comes from the environment, so nothing site-specific is baked in.

  APP_PACKAGE     e.g. com.github.metacubex.clash.meta
  PROFILE         path to the imported profile config.yaml  (required)
  PROCESSING      path to the processing config.yaml        (optional)
  AUTH_KEY_FILE   file that holds the auth key              (required)
  NODE_NAME       phone node name inside the tailnet        (default: phone)
  STATE_DIR       tsnet state dir                           (required; subpath of the core home)
  DRY=1           write the candidate to CANDIDATE instead of the profile, then stop, so the
                  result can be validated with `mihomo -t` before it goes live.

Never prints the auth key.
"""
import os
import re
import sys

PROFILE = os.environ.get("PROFILE", "")
PROCESSING = os.environ.get("PROCESSING", "")
KEYFILE = os.environ.get("AUTH_KEY_FILE", "")
CANDIDATE = os.environ.get("CANDIDATE", "/data/local/tmp/tailnet-candidate.yaml")
NODE = os.environ.get("NODE_NAME", "phone")
STATE_DIR = os.environ.get("STATE_DIR", "")
DRY = os.environ.get("DRY") == "1"

if not PROFILE or not STATE_DIR:
    sys.exit("ERROR: set PROFILE and STATE_DIR")

key = ""
if os.path.exists(KEYFILE):
    key = open(KEYFILE).read().strip()
if key and not key.startswith("tskey-"):
    sys.exit("ERROR: auth key has a bad prefix")
if not key:
    print("WARN: no auth key file; relying on a persisted state-dir")

proxy = ("  - {name: TS-NET, type: tailscale, hostname: %s%s, "
         "control-url: https://controlplane.tailscale.com, state-dir: %s, udp: true}"
         % (NODE, (", auth-key: " + key) if key else "", STATE_DIR))
rule = (" - IP-CIDR,100.64.0.0/10,TS-NET,no-resolve\n"
        " - DOMAIN-SUFFIX,ts.net,TS-NET\n")

src = open(PROFILE).read()
pat = re.compile(r'^\s*-\s*\{?name: TS-NET.*$', re.M)

if pat.search(src):
    # already present: rewrite the line, so a changed key or state-dir takes effect
    new = pat.sub(lambda m: proxy, src, count=1)
    have_rule = "IP-CIDR,100.64.0.0/10,TS-NET" in src
    if not have_rule:
        new = new.replace("rules:\n", "rules:\n" + rule, 1)
else:
    out, done_p, done_r = [], False, False
    for line in src.splitlines(keepends=True):
        if not done_p and line.startswith("proxies:"):
            out.append(line); out.append(proxy + "\n"); done_p = True; continue
        if not done_r and line.startswith("rules:"):
            out.append(line); out.append(rule); done_r = True; continue
        out.append(line)
    if not (done_p and done_r):
        sys.exit("ERROR: anchor line not found (proxies: / rules:)")
    new = "".join(out)

st = os.stat(PROFILE)
targets = [CANDIDATE] if DRY else [p for p in (PROFILE, PROCESSING) if p]
for path in targets:
    tmp = path + ".tsnew"
    with open(tmp, "w") as fh:
        fh.write(new)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, 0o600)
    except PermissionError:
        pass
    os.replace(tmp, path)
    print("wrote %s" % path)

print("OK. node=%s key_present=%s state_dir=%s" % (NODE, bool(key), STATE_DIR))
