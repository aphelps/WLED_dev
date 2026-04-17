# Adjust Sparkle Effect for Smooth Fading

## Goal
Make the HMTL Sparkle effect fade pixels smoothly in and out over several frames
instead of snapping them instantly to a new color or background.

## Subtasks
- [x] Check SEGENV.data allocation pattern in FX.cpp
- [x] Replace instant setPixelColor calls with target array + per-frame blendPixelColor
- [x] Add c2 "Fade Speed" parameter to PROGMEM descriptor
- [x] Build with `pio run -e ampworks` and confirm clean compile
- [ ] Test on device: verify smooth fade-in and fade-out visually

## Notes
- Uses `uint32_t targets[SEGLEN]` in SEGENV.data (4 bytes/pixel)
- Each cycle tick: dice roll assigns new target colors
- Each frame: `blendPixelColor(i, targets[i], fade_step)` advances toward target
- fade_step = SEGMENT.custom2 (1–255); default c2=32 ≈ smooth ~8-frame fade
- Existing speed/intensity/c1/Color-2 semantics unchanged
