# scripts

Fleet-level tools that operate on WLED devices over the network, rather than on the firmware
source. Plain Python 3, standard library only — nothing to install.

| Script | For |
|---|---|
| `wled_sync.py` | Put every WLED device on the network onto the same effect, colour and palette, phase-aligned so they animate together. |

Related: `skills/wled-scan/` finds devices and tables their configuration (`wleds`); this reuses its
discovery rather than reimplementing it.

---

## `wled_sync.py` — one look across the whole installation

Sweeps the local subnet, finds every WLED device, and applies the same state to all of them. The
point is the *whole* installation matching, so it also aligns animation phase — without that, ten
devices on "Blink" each blink on their own schedule and the room looks broken rather than
synchronised.

### Running it

Always start with `--dry-run`. It discovers and resolves everything, prints exactly what it would
send, and changes nothing:

```bash
scripts/wled_sync.py --effect Hiphotic --palette 'Ocean' --dry-run
```

Then for real:

```bash
scripts/wled_sync.py --effect Hiphotic --palette 'Ocean'
scripts/wled_sync.py --effect Blink --color '#ff0000'      # colour-driven palette needed, see below
scripts/wled_sync.py --host 192.168.1.39 --host 192.168.1.55 --effect Solid
```

Names are matched case-insensitively against what each device reports, not against a hard-coded
table — effect and palette *numbers* differ between firmware builds, so a number that is "Hiphotic"
on one device is something else on another. That is why this takes names.

Devices are discovered on the local subnet by default. `--tailscale` additionally sweeps Tailscale
peers; it is off by default because that is a different trust boundary. `--subnet` and `--host`
override discovery.

### Things that will surprise you

- **A palette can silently override `--color`.** Most palettes supply their own colours and the
  effect ignores the segment colour entirely. Pass a colour-driven palette (`Default`, `Color 1`,
  `Colors 1&2`, `Color Gradient`, `Colors Only`) or your `--color` does nothing. The tool warns
  when it spots this combination rather than leaving you to wonder.
- **`--speed` and `--intensity` are deliberately not set by default.** They are usually tuned per
  installation, so overwriting them across the fleet destroys local adjustment. Set them explicitly
  if you want them uniform.
- **Out-of-range speed/intensity is rejected up front.** WLED does not clamp: a value above 255
  becomes 0, which freezes the animation on every device at once.
- **Phase alignment is the default**; `--no-phase` turns it off, and devices then visibly drift
  apart within seconds.

### Options

| Flag | Default | Notes |
|---|---|---|
| `--effect` | — | Effect name, matched case-insensitively. |
| `--palette` | — | Palette name. |
| `--color` / `--colour` | — | `#RRGGBB` or `r,g,b`. Needs a colour-driven palette to be visible. |
| `--speed` | unset | 0-255. Left alone unless given. |
| `--intensity` | unset | 0-255. Same. |
| `--dry-run` | off | Resolve and print; change nothing. |
| `--no-phase` | off | Skip phase alignment. |
| `--tailscale` | off | Also sweep Tailscale peers. |
| `--subnet CIDR` | — | Explicit range instead of the local subnet (repeatable). |
| `--host HOST` | — | Target specific hosts instead of sweeping (repeatable). |
| `--timeout` | 2.0 | Per-request seconds for reads and writes. |
| `--discovery-timeout` | 1.0 | Per-probe seconds while sweeping; short because most addresses are empty. |
| `--workers` | 16 | Concurrent devices to apply to. |
| `--discovery-workers` | 64 | Concurrent probes while sweeping. |

Exit status is `0` only when every device found was updated; any failure exits `1`, so it is safe
to use in a script.

---

## Tests

```bash
make test-sync      # just these
make test           # everything in the repo
```

Pure logic — no network. The assertions worth knowing about cover the ordering rule (transitivity
and antisymmetry across the full `uint32` range, and convergence under all six arrival orders of
three commands) and that the broadcast-suppression flag is present on every request, since without
it a synced device re-broadcasts to the fleet and undoes the run.
