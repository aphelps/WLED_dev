---
name: check-device
description: Query a WLED device and report its running firmware version, git commit, branch, build time, uptime and free heap. Takes a host/IP argument or WLED_IP; warns if the git field is absent (firmware predating the build-info script).
---

Target the device by argument, falling back to `WLED_IP`, then mDNS. There is deliberately no
default address — a hardcoded one is how this ends up querying the wrong device, or nothing at
all, on someone else's network.

```bash
DEV="${1:-${WLED_IP:-wled.local}}"
```

If the address is unknown, discover it — WLED advertises over mDNS:

```bash
dns-sd -B _wled._tcp         # macOS  (Ctrl-C to stop)
avahi-browse -rt _wled._tcp  # Linux
```

Then:

```bash
curl -s -m 8 "http://$DEV/json/info" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for k in ('name','ver','arch','git','branch','built','uptime','freeheap','ip'):
    v = d.get(k)
    if v is not None: print(f'{k}: {v}')
print('leds:', d.get('leds', {}).get('count'))
if 'git' not in d: print('WARNING: no git field — firmware predates the build-info script')
"
```

`git` / `branch` / `built` are injected by `WLED/tools/set_build_info.py`. Stock WLED builds do not
carry them, so their absence means "not our firmware" — not "device broken".

To tell a healthy device from one that is boot-looping, read `uptime` twice ~20s apart and check it
climbs. A looping device answers any single poll perfectly happily.
