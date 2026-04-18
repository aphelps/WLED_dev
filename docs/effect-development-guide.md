# Effect Development Guide

This guide covers how to add a new LED effect to WLED — either directly
in `FX.cpp` (for general-purpose effects) or via a usermod (for effects
tied to custom hardware or a specific build).

---

## 1. Overview

WLED effects are C++ functions called every frame (~16 ms) that write colors
to a segment of LEDs. Every effect needs three things:

| What | Purpose |
|------|---------|
| `uint16_t mode_my_effect(void)` | The effect function |
| PROGMEM descriptor string | Name, slider labels, flags, defaults shown in the UI |
| Registration call | Wires the function + descriptor into WLED's effect list |

The API for the effect function itself is identical regardless of which path
you take. Only the registration differs.

---

## 2. Choosing Where to Put Your Effect

| Path | Use when |
|------|---------|
| **FX.cpp** | Effect is general-purpose; no special hardware required; you want it available in all WLED builds |
| **Usermod** | Effect requires specific hardware (sensors, custom PCB), or you want to ship it without touching the core WLED source |

---

## 3. Path A — Adding an Effect to FX.cpp

### 3.1 Write the function

Add your function to `WLED/wled00/FX.cpp`:

```cpp
uint16_t mode_my_effect(void) {
  if (SEGLEN == 0) return FRAMETIME;
  // ... your logic ...
  return FRAMETIME;
}
static const char _data_FX_MODE_MY_EFFECT[] PROGMEM =
  "My Effect@Speed,Intensity;!,!;!;01;sx=128,ix=128";
```

### 3.2 Declare it in FX.h

In `WLED/wled00/FX.h`, add a `#define` for your effect ID in the effect ID block
and declare the function:

```cpp
#define FX_MODE_MY_EFFECT   <next available ID>

uint16_t mode_my_effect(void);
```

### 3.3 Register it

In the `WS2812FX::setupEffectData()` function in `FX.cpp`, add:

```cpp
addEffect(FX_MODE_MY_EFFECT, &mode_my_effect, _data_FX_MODE_MY_EFFECT);
```

---

## 4. Path B — Adding an Effect via a Usermod

Usermods register effects from their `setup()` method using ID `255`, which
tells WLED to assign the next available ID automatically. This keeps usermod
effects isolated from the core effect numbering.

### 4.1 Create a usermod (if you don't have one)

Create a directory under `WLED/usermods/my_mod/` with at minimum:

```
my_mod.h     # class declaration, inherits Usermod
my_mod.cpp   # implementation
```

Minimal `my_mod.h`:

```cpp
#pragma once
#include "wled.h"

class MyMod : public Usermod {
public:
  void setup() override;
  void loop() override {}
  uint16_t getId() override { return USERMOD_ID_MY_MOD; } // define in const.h
  static const char _name[];
};
```

Minimal `my_mod.cpp`:

```cpp
#include "my_mod.h"

uint16_t mode_my_effect(void) { /* ... */ return FRAMETIME; }
static const char _data_FX_MODE_MY_EFFECT[] PROGMEM = "My Effect@!;!;!;01";

void MyMod::setup() {
  strip.addEffect(255, &mode_my_effect, _data_FX_MODE_MY_EFFECT);
}

const char MyMod::_name[] PROGMEM = "MyMod";
```

### 4.2 Register the usermod

In `WLED/wled00/usermods_list.cpp`:

```cpp
#ifdef USERMOD_MY_MOD
  #include "../usermods/my_mod/my_mod.h"
#endif

// inside registerUsermods():
#ifdef USERMOD_MY_MOD
  usermods.add(new MyMod());
#endif
```

### 4.3 Enable it in your build environment

In `platformio.ini` (or `platformio_override.ini`):

```ini
[env:my_env]
extends = env:esp32dev
build_flags = ${env:esp32dev.build_flags} -D USERMOD_MY_MOD
```

---

## 5. Anatomy of an Effect Function

```cpp
uint16_t mode_my_effect(void) {
  if (SEGLEN == 0) return FRAMETIME;   // always guard against zero-length segments

  // Initialization — runs only on the first call after the effect is selected
  if (SEGENV.call == 0) {
    SEGMENT.fill(BLACK);
  }

  // ... per-frame rendering logic ...

  return FRAMETIME;   // request next frame; return a ms value to delay instead
}
```

---

## 6. PROGMEM Descriptor Format

```
"Name@p1,p2,p3,p4,p5;c1,c2,c3;palette;flags;defaults"
```

| Field | Meaning |
|-------|---------|
| `Name` | Displayed in the UI |
| `@p1…p5` | Slider labels: Speed, Intensity, C1, C2, C3 (omit trailing ones you don't use) |
| `;c1,c2,c3` | Color slot labels; use `!` for "automatic from palette" |
| `;palette` | Palette slot label; use `!` for automatic |
| `;flags` | `01` = 1D strip, `02` = 2D matrix, `08` = volume reactive, `10` = freq reactive |
| `;defaults` | Initial slider values: `sx=`, `ix=`, `c1=`, `c2=`, `c3=`, `pal=` (0–255) |

Examples:

```cpp
// Speed + Intensity + one custom slider; two color slots; 1D; custom default
"AMP AI@!,!,Spacing;!,!;!;01;c1=3"

// Four sliders; two color slots; palette; 1D
"HMTL Sparkle@Rate,Sparkle,BG Reset,Fade;!,!;!;01;sx=128,ix=50,c1=20,c2=32"
```

Omit trailing sections you don't need. `!` means "inherit from the segment's
automatic setting."

---

## 7. Parameters Available Inside an Effect

```cpp
SEGLEN              // number of pixels in the segment (uint16_t)
SEGMENT.speed       // Speed slider   0–255 (uint8_t)
SEGMENT.intensity   // Intensity slider 0–255
SEGMENT.custom1     // C1 slider 0–255
SEGMENT.custom2     // C2 slider 0–255
SEGMENT.custom3     // C3 slider 0–255 (5-bit; values 0–31 scaled to 0–255 in UI)
SEGCOLOR(0)         // Color slot 0 as uint32_t (0xWWRRGGBB)
SEGCOLOR(1)         // Color slot 1
SEGCOLOR(2)         // Color slot 2
```

`map8(value, lo, hi)` linearly maps a 0–255 value to [lo, hi]:

```cpp
uint8_t points = map8(SEGMENT.custom1, 1, 10);  // slider → 1..10 points
```

---

## 8. Writing Pixels

```cpp
SEGMENT.setPixelColor(i, 0xFF0000);        // set pixel i to red
SEGMENT.setPixelColor(i, 255, 0, 0);       // same, from R/G/B components
SEGMENT.fill(SEGCOLOR(1));                 // fill entire segment
SEGMENT.blendPixelColor(i, target, 32);    // blend toward target (0=none, 255=snap)
SEGMENT.fade_out(220);                     // dim all pixels (220 ≈ slight fade)
SEGMENT.blur(16);                          // blur neighboring pixels
```

---

## 9. Color Utilities

```cpp
uint32_t c = SEGMENT.color_from_palette(pos, true, false, 255); // palette lookup (0–255)
uint32_t c = SEGMENT.color_wheel(pos);                          // HSV hue wheel
uint32_t b = color_blend(c1, c2, amt);                          // blend (0→c1, 255→c2)

uint8_t  r  = random8();          // 0–255
uint8_t  rn = random8(100);       // 0–99
uint16_t rl = random16(SEGLEN);   // 0–(SEGLEN-1)
```

---

## 10. Frame Timing

`FRAMETIME` (~16 ms) is the standard return value. `strip.now` is a millisecond
timestamp. Use the **cycle-tick pattern** to run logic at a rate decoupled from
the frame rate:

```cpp
uint32_t cycleTime = 10 + (255 - SEGMENT.speed) * 2; // ms per tick
uint32_t it = strip.now / cycleTime;
if (it != SEGENV.step) {
  SEGENV.step = it;
  // runs once per cycle (e.g. pick new targets) ...
}
// rendering runs every frame regardless
return FRAMETIME;
```

---

## 11. Per-Frame State (`SEGENV`)

| Field | Type | Purpose |
|-------|------|---------|
| `SEGENV.call` | `uint32_t` | Frame counter; 0 on first call — use for initialization |
| `SEGENV.step` | `uint32_t` | General-purpose; conventional use: cycle-tick ID |
| `SEGENV.aux0` | `uint16_t` | Small persistent value (position, index, etc.) |
| `SEGENV.aux1` | `uint16_t` | Second small persistent value |

---

## 12. Persistent Scratch Memory (`SEGENV.data`)

For per-pixel arrays or larger structs:

```cpp
unsigned dataSize = sizeof(uint32_t) * SEGLEN;
if (!SEGENV.allocateData(dataSize)) {
  SEGMENT.fill(SEGCOLOR(1));
  return FRAMETIME; // graceful fallback
}
uint32_t* targets = reinterpret_cast<uint32_t*>(SEGENV.data);
if (SEGENV.call == 0) {
  for (uint16_t i = 0; i < SEGLEN; i++) targets[i] = SEGCOLOR(1);
}
```

Or using a struct:

```cpp
struct MyData { uint8_t count; uint16_t positions[MAX_POINTS]; };
if (!SEGENV.allocateData(sizeof(MyData))) return FRAMETIME;
MyData* data = reinterpret_cast<MyData*>(SEGENV.data);
if (SEGENV.call == 0) { data->count = 0; }
```

`allocateData` returns `false` when the heap is too fragmented — always handle it.

---

## 13. Audio-Reactive Effects

```cpp
um_data_t *um_data = nullptr;
if (!UsermodManager::getUMData(&um_data, USERMOD_ID_AUDIOREACTIVE)) {
  um_data = simulateSound(SEGMENT.soundSim); // fallback to simulated audio
}
if (!um_data) return FRAMETIME;

float   volumeSmth = *(float*)   um_data->u_data[0]; // smoothed 0..~1
int16_t volumeRaw  = *(int16_t*) um_data->u_data[1]; // raw 0..~255

uint8_t bri = (uint8_t)constrain(volumeSmth * 255.0f, 0, 255);
```

Add flag `08` in the descriptor to mark the effect as volume-reactive.

---

## 14. Cross-Usermod Data (Touch Sensors, etc.)

Usermods can expose data to effects via `UsermodManager::lookup()`. Example
for any usermod that implements a `touched(n)` interface:

```cpp
// Inside a usermod effect — look up another usermod by its ID
MyTouchMod *mod = (MyTouchMod*) UsermodManager::lookup(USERMOD_ID_MY_TOUCH);
if (mod && mod->isSensorFound()) {
  if (mod->touched(0)) { /* electrode 0 active */ }
}
```

This pattern (lookup + typed cast + inline wrappers) avoids the `um_data_t`
bitmask scheme and is preferred for complex sensor types.

---

## 15. Where to Look for More

| Resource | What's there |
|----------|-------------|
| `WLED/wled00/FX.cpp` | 100+ built-in effects — best source of real patterns for timers, palettes, 2D, audio, etc. |
| `WLED/wled00/FX.h` | Full `Segment` class API: `setPixelColor`, `fill`, `blur`, `fade_out`, all color utilities |
| `WLED/wled00/wled.h` | Global constants, feature flags, `WS2812FX` class |
| `WLED/wled00/const.h` | Compile-time constants (max buses, effect counts, etc.) |
| `WLED/wled00/usermods_list.cpp` | How existing usermods are registered |
| `WLED/usermods/` | Reference usermod implementations |
