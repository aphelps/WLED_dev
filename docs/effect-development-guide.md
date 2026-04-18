# Effect Development Guide

This guide covers how to add a new LED effect to the AMPWorks usermod
(`WLED/usermods/ampworks/ampworks.cpp`).

---

## 1. Overview

WLED effects are C++ functions called every frame (~16 ms) that write colors
to a segment of LEDs.  Custom effects in this repo live in `ampworks.cpp` and
are registered at startup so WLED treats them exactly like built-in effects.

The three things every effect needs:

| What | Where |
|------|-------|
| Function `uint16_t mode_my_effect(void)` | `ampworks.cpp` |
| PROGMEM descriptor string | `ampworks.cpp` (immediately after the function) |
| `strip.addEffect(...)` call | `AMPWorks::setup()` at the bottom of `ampworks.cpp` |

---

## 2. Anatomy of an Effect

```cpp
uint16_t mode_my_effect(void) {
  if (SEGLEN == 0) return FRAMETIME;   // guard — always include this

  // ... your logic here ...

  return FRAMETIME;   // tell WLED to call again next frame
}

static const char _data_FX_MODE_MY_EFFECT[] PROGMEM =
  "My Effect@Speed,Intensity,C1,C2;Color1,Color2;Pal;01;sx=128,ix=128,c1=0,c2=0";
```

Register it in `AMPWorks::setup()`:

```cpp
strip.addEffect(255, &mode_my_effect, _data_FX_MODE_MY_EFFECT);
```

The first argument (`255`) tells WLED to assign the next available effect ID
automatically.

---

## 3. PROGMEM Descriptor Format

```
"Name@p1,p2,p3,p4,p5;c1,c2,c3;palette;flags;defaults"
```

| Field | Meaning |
|-------|---------|
| `Name` | Displayed in the UI |
| `@p1…p5` | Slider labels: Speed, Intensity, C1, C2, C3 (omit trailing ones you don't use) |
| `;c1,c2,c3` | Color slot labels; use `!` for "automatic from palette" |
| `;palette` | Palette slot label; use `!` for automatic |
| `;flags` | `01` = 1D strip; `02` = 2D matrix; `08` = volume reactive; `10` = freq reactive |
| `;defaults` | Initial slider values: `sx=`, `ix=`, `c1=`, `c2=`, `c3=`, `pal=` (0–255) |

**Examples from this repo:**

```cpp
// Two sliders (Speed + Intensity), one color slot, palette, 1D, defaults
"AMP AI@!,!,Spacing;!,!;!;01;c1=3"

// Four sliders, two color slots, palette, 1D
"HMTL Sparkle@Rate,Sparkle,BG Reset,Fade;!,!;!;01;sx=128,ix=50,c1=20,c2=32"
```

Omit trailing sections you don't need.  `!` means "inherit from the segment's
automatic setting."

---

## 4. Parameters Available Inside an Effect

All effect parameters come through macros and the `SEGMENT` object:

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

`map8(value, lo, hi)` linearly maps a 0–255 value to the [lo, hi] range —
useful for converting sliders to meaningful units:

```cpp
uint8_t points = map8(SEGMENT.custom1, 1, 10);  // slider → 1..10 points
```

---

## 5. Writing Pixels

```cpp
// Set pixel i to a packed 0xWWRRGGBB color
SEGMENT.setPixelColor(i, 0xFF0000);         // red

// Set from separate R, G, B components
SEGMENT.setPixelColor(i, 255, 0, 0);

// Fill the entire segment with one color
SEGMENT.fill(SEGCOLOR(1));

// Blend pixel i toward a target color (blend=0 → no change; blend=255 → snap)
SEGMENT.blendPixelColor(i, targetColor, 32);

// Dim all pixels toward black (r=0 → full black; r=255 → no dimming)
SEGMENT.fade_out(220);

// Blur neighboring pixels together (smooths sharp edges)
SEGMENT.blur(16);
```

---

## 6. Color Utilities

```cpp
// Draw color at position pos (0–255) along the active palette
uint32_t c = SEGMENT.color_from_palette(pos, /*mapping=*/true, /*wrap=*/false, 255);

// Map position 0–255 around the color wheel (HSV hue)
uint32_t c = SEGMENT.color_wheel(pos);

// Blend two packed colors (amt=0 → c1; amt=255 → c2)
uint32_t blended = color_blend(c1, c2, amt);

// Fast random numbers
uint8_t  r8  = random8();        // 0–255
uint8_t  r8n = random8(100);     // 0–99
uint16_t r16 = random16(SEGLEN); // 0–(SEGLEN-1)
```

---

## 7. Frame Timing

`FRAMETIME` (~16 ms) is the standard return value — it tells WLED to call
your effect again next frame.

`strip.now` is a millisecond timestamp.  The **cycle-tick pattern** runs logic
at a rate independent of the frame rate:

```cpp
uint32_t cycleTime = 10 + (255 - SEGMENT.speed) * 2; // ms per tick
uint32_t it = strip.now / cycleTime;
if (it != SEGENV.step) {
  SEGENV.step = it;
  // ... this runs once per cycle, not once per frame ...
}
// ... rendering runs every frame regardless ...
return FRAMETIME;
```

Higher `SEGMENT.speed` → smaller `cycleTime` → faster ticks.

---

## 8. Per-Frame State

WLED provides several persistent fields on `SEGENV` that survive across frames:

| Field | Type | Purpose |
|-------|------|---------|
| `SEGENV.call` | `uint32_t` | Frame counter; 0 on first call — use for initialization |
| `SEGENV.step` | `uint32_t` | General-purpose counter; conventional use: cycle-tick ID |
| `SEGENV.aux0` | `uint16_t` | Small persistent value (position, index, etc.) |
| `SEGENV.aux1` | `uint16_t` | Second small persistent value |

Use `SEGENV.call == 0` to initialize state on first entry:

```cpp
if (SEGENV.call == 0) {
  SEGMENT.fill(SEGCOLOR(1)); // fill with background color
  SEGENV.aux0 = 0;
}
```

---

## 9. Persistent Scratch Memory (`SEGENV.data`)

For larger per-pixel arrays or structs, allocate a scratch buffer:

```cpp
// Allocate once; safe to call every frame — re-uses existing allocation
unsigned dataSize = sizeof(uint32_t) * SEGLEN;
if (!SEGENV.allocateData(dataSize)) {
  SEGMENT.fill(SEGCOLOR(1));
  return FRAMETIME; // graceful fallback if allocation fails
}
uint32_t* targets = reinterpret_cast<uint32_t*>(SEGENV.data);
```

You can also use a struct:

```cpp
struct MyData {
  uint8_t  count;
  uint16_t positions[MAX_POINTS];
};
if (!SEGENV.allocateData(sizeof(MyData))) return FRAMETIME;
MyData* data = reinterpret_cast<MyData*>(SEGENV.data);
if (SEGENV.call == 0) { data->count = 0; } // initialize on first frame
```

`allocateData` returns `false` if the heap is too fragmented — always handle
this case.

---

## 10. Audio-Reactive Effects

```cpp
um_data_t *um_data = nullptr;
if (!UsermodManager::getUMData(&um_data, USERMOD_ID_AUDIOREACTIVE)) {
  um_data = simulateSound(SEGMENT.soundSim); // fall back to simulated audio
}
if (!um_data) return FRAMETIME;

float   volumeSmth = *(float*)   um_data->u_data[0]; // smoothed volume 0..~1
int16_t volumeRaw  = *(int16_t*) um_data->u_data[1]; // raw volume 0..~255
```

`volumeSmth` is a float in roughly the 0–1 range (sometimes slightly above).
Scale it to 0–255 before using as brightness:

```cpp
uint8_t bri = (uint8_t)constrain(volumeSmth * 255.0f, 0, 255);
```

Add flag `08` to the descriptor to mark the effect as volume-reactive so WLED
can show it in filtered lists.

---

## 11. MPR121 Touch-Reactive Effects

```cpp
#ifdef USERMOD_MPR121
UsermodMPR121 *mpr = (UsermodMPR121*) UsermodManager::lookup(USERMOD_ID_MPR121);
if (mpr && mpr->isSensorFound()) {

  if (mpr->touched(3)) {
    // electrode 3 is currently being touched
  }

  // Raw filtered reading for proximity sensor (~0 when clear, ~800 when hand nearby)
  uint16_t prox = mpr->getFiltered(MPR121::PROX_SENSOR);
}
#endif
```

Electrodes 0–11 are the touch pads; `MPR121::PROX_SENSOR` (electrode 12) gives
proximity without requiring direct contact.  The `#ifdef` guard is required —
the MPR121 usermod is only compiled in the `ampworks` PlatformIO environment.

---

## 12. Where to Look for More

| Resource | What's there |
|----------|-------------|
| `WLED/wled00/FX.cpp` | 100+ built-in effects — the best source of working patterns for timers, palettes, 2D, audio, etc. |
| `WLED/wled00/FX.h` | Full `Segment` class API: all `setPixelColor`, `fill`, `blur`, `fade_out`, color utility declarations |
| `WLED/wled00/wled.h` | Global constants, feature flags, `WS2812FX` class |
| `WLED/wled00/const.h` | Compile-time constants (max buses, effect counts, etc.) |
| `usermods/ampworks/ampworks.cpp` | All existing custom effects — reference implementations for audio, touch, SEGENV.data, cycle-ticks |
| `usermods/mpr121/usermod_mpr121.h` | Full MPR121 API |
| `.claude/skills/new-effect/SKILL.md` | Quick-reference cheat-sheet for Claude Code |
