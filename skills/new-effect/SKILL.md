---
name: new-effect
description: Cheat-sheet for adding a new LED effect to usermods/ampworks/ampworks.cpp — skeleton, PROGMEM descriptor syntax, registration, audio data, and MPR121 touch access.
user-invocable: false
---

## Skeleton

```cpp
uint16_t mode_my_effect(void) {
  if (SEGLEN == 0) return FRAMETIME;
  // SEGMENT.speed / .intensity / .custom1 / .custom2 / .custom3 (uint8_t sliders)
  // SEGCOLOR(0..2), SEGMENT.color_from_palette(), SEGMENT.fill(), SEGMENT.setPixelColor()
  // SEGENV.step (frame counter), SEGENV.aux0/aux1 (persistent state), SEGENV.call (call #)
  // strip.now (ms timestamp), FRAMETIME (~16ms)
  return FRAMETIME;
}
static const char _data_FX_MODE_MY_EFFECT[] PROGMEM =
  "My Effect@Speed,Intensity,C1;Color1,Color2,Color3;Palette;01;sx=128,ix=128,c1=0";
```

## PROGMEM descriptor format
`"Name@p1,p2,p3;c1,c2,c3;pal;flags;defaults"`
- params/colors/pal: omit trailing defaults; use `!` for "automatic"
- flags: `01` = 1D; `02` = 2D; `08` = volume reactive; `10` = freq reactive
- defaults: `sx=`,`ix=`,`c1=`,`c2=`,`c3=`,`pal=` (0–255)

## Registration (in `AMPWorks::setup()`)
```cpp
strip.addEffect(255, &mode_my_effect, _data_FX_MODE_MY_EFFECT);
```

## Audio data
```cpp
um_data_t *um_data;
if (!UsermodManager::getUMData(&um_data, USERMOD_ID_AUDIOREACTIVE))
  um_data = simulateSound(SEGMENT.soundSim);
float volumeSmth  = *(float*)   um_data->u_data[0];
int16_t volumeRaw = *(int16_t*) um_data->u_data[1];
```

## MPR121 touch data
```cpp
#ifdef USERMOD_MPR121
UsermodMPR121 *mpr = (UsermodMPR121*) UsermodManager::lookup(USERMOD_ID_MPR121);
if (mpr && mpr->isSensorFound()) {
  if (mpr->touched(3)) { /* electrode 3 touched */ }
  uint16_t prox = mpr->getFiltered(MPR121::PROX_SENSOR); // 0–~800
}
#endif
```
