---
name: check-device
description: Query the WLED device at 192.168.1.55 and report its running firmware version, git commit, branch, build time, uptime, and free heap. Warns if git field is absent (pre-build-info firmware).
---

```bash
curl -s http://192.168.1.55/json/info | python3 -c "
import json, sys
d = json.load(sys.stdin)
for k in ('ver','git','branch','built','uptime','freeheap'):
    v = d.get(k)
    if v is not None: print(f'{k}: {v}')
if 'git' not in d: print('WARNING: no git field — firmware predates build-info script')
"
```
