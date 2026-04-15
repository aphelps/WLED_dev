---
name: flash
description: Build and upload ampworks firmware to the target WLED device, then confirm the running build matches the local git state.
disable-model-invocation: true
---

1. Get expected hash: `git -C WLED rev-parse --short HEAD` (append `+dirty` if `git status --porcelain` is non-empty)
2. Build & upload from `WLED/`:
   - No arg: `pio run -e ampworks -t upload`
   - With IP arg: `WLED_IP=<arg> pio run -e ampworks -t upload`
3. Poll until the device's `git` field matches the expected hash (up to 30s / 6 × 5s retries):
   ```bash
   curl -s http://192.168.1.55/json/info | python3 -c "
   import json,sys; d=json.load(sys.stdin)
   for k in ('ver','git','branch','built'): print(f'{k}: {d.get(k,\"(missing)\")}')
   "
   ```
4. Print confirmed fields on success; error on timeout.
