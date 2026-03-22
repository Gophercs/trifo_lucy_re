---
id: TASK-003
title: Trace APK map rendering code path
status: open
priority: medium
depends_on: []
estimated_effort: medium
skills: [java, android, reverse-engineering]
requires_robot: false
---

# Trace APK Map Rendering Code Path

## Context

The Trifo Home Android app (`com.trifo.home`, v2.6.3) receives map data from
the robot via MQTT. The transport layer is understood: Base64 decode then LZ4
decompress yields a protobuf message (same structure as OGM files). But the
app must also decode the grid data (protobuf field 8) to render the map on
screen — meaning the decoder exists in the APK somewhere.

Known code paths:
- `h3.h0.b()` — does Base64 decode + LZ4 decompress (transport layer)
- `BlowerParamBean` class — blower config (not directly relevant but shows
  code structure)
- The app is obfuscated (ProGuard), so class/method names are mangled

## Objective

Find the Java/Kotlin code in the decompiled APK that:
1. Takes the LZ4-decompressed protobuf
2. Extracts the grid data blob (field 8)
3. Decodes it into a renderable bitmap/grid

Even partial traces are valuable — knowing which class handles map rendering
narrows the search significantly.

## Inputs

- APK file: `Trifo Home_2.6.3_APKPure.apk` (in project root)
- Decompiled output: `apk_extract/` directory
- Known entry point: `h3.h0.b()` for transport layer
- `docs/ogm_format_research.md` — section 11 covers existing APK analysis

## Deliverable

Identified class/method names with explanation of the decode flow. Code
snippets showing the grid data transformation. Even identifying "this class
receives the protobuf and passes grid data to X" would be a meaningful step.
