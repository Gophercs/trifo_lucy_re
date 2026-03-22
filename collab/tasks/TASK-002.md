---
id: TASK-002
title: Map blowerMode percentage values to named presets
status: open
priority: medium
depends_on: []
estimated_effort: small
skills: [testing, documentation]
requires_robot: true
---

# Map blowerMode Percentage Values to Named Presets

## Context

The `blowerMode:N` smart speaker command accepts values 0-100 and directly
controls suction motor power as a percentage. The Trifo app exposes 4 named
presets (Off, Low/Quiet, Normal, High/Max) but we don't know which percentage
values correspond to each preset.

We know:
- The robot reports `"blower": "23"` in status (likely a preset ID, not %)
- `blowerMode:0` turns off suction
- `blowerMode:100` is maximum (very loud)
- The motor pitch maps roughly chromatically from G# at 20% to F at 100%
- There's a perceptual pitch leap between 30% and 40%

## Objective

Determine the exact percentage values for each of the 4 app presets. This
likely requires either:
- Firmware analysis (find the preset→percentage mapping in the binary)
- Live testing with the app (set each preset via app, read back the actual
  blowerMode value from MQTT traffic)

## Inputs

- `docs/09_mqtt_commands.md` — command reference
- `docs/ogm_format_research.md` section on software analysis may have relevant
  decompilation pointers
- The status field `"blower": "23"` is a preset ID, not a percentage

## Deliverable

A simple mapping table: `{off: N, low: N, normal: N, high: N}`
