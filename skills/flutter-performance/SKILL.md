---
name: flutter-performance
description: Audit and optimize any Flutter app for maximum performance — smooth 60/120 FPS (16.6 ms / 8.3 ms frame budgets), low memory, small download size, fast startup and low battery drain. Use this whenever the user mentions Flutter or Dart performance, jank, stutter, dropped frames, slow scrolling, laggy animations, rebuilds, high memory, leaks, out-of-memory crashes, large APK/IPA/AAB size, slow startup, battery or heat, DevTools profiling, Impeller, isolates, or asks to "optimize", "speed up" or "review" a Flutter codebase — even if they don't say the word "performance".
---

# Flutter Performance

Based on the official Flutter performance docs (docs.flutter.dev/perf, Flutter 3.47). Flutter apps are fast by default; most real-world slowness comes from a small set of avoidable pitfalls. This skill finds those pitfalls, fixes them, and proves the fix with measurements.

The four performance categories (they match Flutter's GitHub labels) organize everything below:

| Category | Symptom | Where to look |
|---|---|---|
| **Speed** | Jank, dropped frames, slow first animation | `references/ui-thread.md`, `references/raster-thread.md` |
| **Memory** | Growth over time, OOM crashes, device slowdown | `references/memory.md` |
| **App size** | Large download/install size | `references/app-size-and-startup.md` |
| **Energy** | Battery drain, heat | Frame time and CPU/GPU usage — every speed fix is also an energy fix |

## Frame budget — the number everything is judged against

Flutter builds a frame on the **UI thread** (your Dart code) and rasterizes it on the **raster thread** (Impeller/Skia). Both must finish in time:

| Display | Total budget | Rule of thumb per thread |
|---|---|---|
| 60 Hz | ~16.6 ms | build ≤ 8 ms + raster ≤ 8 ms if latency matters |
| 120 Hz | ~8.3 ms | build ≤ 4 ms + raster ≤ 4 ms |

Going well under budget still matters: it doesn't change what users see, but it **reduces battery use and heat** and leaves headroom for the slowest device you support.

## Workflow

Follow these steps in order. Skipping measurement is the most common mistake — optimizing by guesswork wastes effort and can make code worse.

### 1. Understand the target
Find out (from the user or the code): which platforms (Android/iOS/web/desktop), what the complaint is (which screen, which interaction), and the lowest-end device they support. If the user just wants a general review, go to step 2 and treat the audit as the starting point.

### 2. Static audit (always do this when you have the code)
Run the bundled scanner from the project root:

```bash
python3 <skill-dir>/scripts/perf_audit.py <path-to-flutter-project> --out perf_audit.md
```

It flags known anti-patterns (Opacity in animations, `saveLayer` triggers, non-lazy lists, intrinsic layouts, undisposed controllers, `operator ==` on widgets, helper methods returning widgets, unsized image decoding, heavy JSON on the main isolate, and more), each with file:line and a fix. It is heuristic: **read each hit in context before changing code** — some are fine (e.g. a 5-item `ListView(children:)` is not a problem).

Also check `analysis_options.yaml`; suggest merging `assets/analysis_options_perf.yaml` so the analyzer catches const and disposal issues continuously.

### 3. Measure (when the user can run the app)
Measurements must come from **profile mode on a physical device** — debug mode is JIT-compiled with expensive asserts and emulators have different hardware, so their numbers are misleading. Give the user exact commands from `references/profiling.md`:

```bash
flutter run --profile          # then press P for the performance overlay, or open DevTools
```

Read the overlay: top graph = raster thread, bottom = UI thread, white lines = 16 ms steps, red bars = missed frames.

### 4. Diagnose by thread
- **UI graph red** (even if raster is red too) → Dart code is too expensive. Start here. → `references/ui-thread.md` (rebuilds, build cost, lists, layout) and `references/concurrency.md` (heavy work to isolates).
- **Only raster graph red** → scene is too expensive to draw. → `references/raster-thread.md` (saveLayer, opacity, clips, shadows, RepaintBoundary, images, Impeller).
- **Jank only on first run of an animation** → shader compilation; confirm Impeller is on. → `references/raster-thread.md#impeller`.
- **Memory climbs / OOM** → `references/memory.md`.
- **Big binary / slow download / slow startup** → `references/app-size-and-startup.md`.
- **Web app** → `references/profiling.md#web` (Chrome DevTools; Flutter DevTools can't attach to web profile builds).

### 5. Fix — highest impact first
Prioritize by (frames affected × cost per frame). Typical order:
1. Work that runs every frame during animation/scroll (rebuilding subtrees on each tick, `Opacity`/clip inside animations, non-lazy long lists).
2. Long synchronous work on the main isolate (JSON parsing, image processing, big sorts/filters) → `Isolate.run`/`compute`.
3. `saveLayer` triggers and over-broad opacity/clip/shadow high in the tree.
4. Leaks (undisposed controllers, subscriptions, closures holding `BuildContext`).
5. Memory bloat (images decoded far larger than displayed).
6. App size (split debug info, assets, deferred components).

Make one category of change at a time and keep diffs reviewable. Preserve behavior and visuals exactly unless the user agrees to a visual trade-off (e.g. replacing a clip with `borderRadius`).

### 6. Verify
Re-profile the same interaction in profile mode and compare: average / 90th / 99th percentile / worst frame build and raster time (see `references/profiling.md`). For size, compare `--analyze-size` JSON files in DevTools. Report before → after numbers when the user can supply them; otherwise state clearly which improvements are expected but unmeasured.

## Top rules (quick reference)

These cover the majority of real issues. Details and code are in the references.

**Rebuilds & build cost (UI thread)**
- Keep `build()` cheap and free of repeated work — it can run on every ancestor rebuild.
- Push `setState` down to the smallest subtree that changes; split big widgets by *how they change*.
- Use `const` constructors so identical subtrees short-circuit rebuilds.
- Extract reusable UI into `StatelessWidget` classes, not methods that return `Widget`.
- In `AnimatedBuilder`/`*Transition`/`ValueListenableBuilder`, pass the static subtree as `child`.
- Never override `operator ==` on widgets (O(N²) and de-optimizes calls globally).
- Use `StringBuffer` for building strings in loops.

**Lists & layout**
- Use lazy builders (`ListView.builder`, `GridView.builder`, slivers) for anything long or unbounded; avoid `Column`/`ListView(children:)` with mostly off-screen children.
- Avoid intrinsic passes (`IntrinsicHeight`/`IntrinsicWidth`, `shrinkWrap` lists in lists); give cells fixed sizes (`itemExtent`, `prototypeItem`).

**Painting (raster thread)**
- Avoid `Opacity`, especially animated — use `AnimatedOpacity`, `FadeTransition`, `FadeInImage`, or a semi-transparent color.
- Avoid `saveLayer` triggers: `Clip.antiAliasWithSaveLayer`, `ShaderMask`, `ColorFilter`, group opacity over overlapping children.
- Prefer `borderRadius` over clipping; never clip inside an animation — pre-clip instead.
- Use `RepaintBoundary` around static-but-complex content that's faded/rotated/moved, not everywhere (raster cache costs GPU memory).
- Make sure Impeller is enabled (default on iOS, Android API 29+, and desktop as of 3.47).

**Concurrency**
- Anything that can exceed a frame gap (large JSON, DB reads, image/audio/video processing, big filters) → `Isolate.run` (or `compute` if you also target web).

**Memory**
- `dispose()` every controller, `cancel()` every subscription/timer, `close()` every sink.
- Never store `BuildContext` in long-lived closures or `State` fields; extract what you need (e.g. `Theme.of(context)`) first.
- Decode images at display size (`cacheWidth`/`cacheHeight`, `ResizeImage`).

**Size**
- Release with `--split-debug-info` (plus `--obfuscate` if desired); measure with `--analyze-size`; compress/remove assets; defer rarely used features.

## Output format

When asked to audit or optimize, deliver:

```markdown
# Flutter Performance Report — <app name>

## Summary
<2–4 sentences: biggest risks, expected impact, what was measured vs inferred>

## Findings (highest impact first)
### 1. <Title> — <Speed|Memory|Size|Energy> · <High|Medium|Low>
- **Where:** `lib/path/file.dart:123`
- **Why it's slow:** <mechanism, tied to UI vs raster thread>
- **Fix:** <concrete change, with a short before/after code snippet>

## Changes applied
<list of edits, or "none — recommendations only">

## How to verify
<exact profile-mode commands and which metric should improve>
```

When editing code directly, keep changes minimal and explain each in one line.

## Reference files

Read only what the diagnosis points to:

- `references/profiling.md` — profile mode, physical devices, performance overlay, DevTools views, tracing, frame metrics, web profiling, integration-test benchmarks.
- `references/ui-thread.md` — build cost, rebuild scoping, const, widgets vs functions, animation builders, lists, intrinsics, StringBuffer, `operator ==`.
- `references/raster-thread.md` — saveLayer, opacity, clipping, shadows, RepaintBoundary, image caching, Impeller.
- `references/concurrency.md` — `Isolate.run`, `compute`, long-lived workers, plugins in isolates, limitations.
- `references/memory.md` — leaks vs bloat, disposal, closures and BuildContext, images, DevTools Memory view.
- `references/app-size-and-startup.md` — measuring true size, `--analyze-size`, size reduction, deferred components, startup time.
- `scripts/perf_audit.py` — static scanner (Python 3, no dependencies).
- `assets/analysis_options_perf.yaml` — lint set that catches perf issues at edit time.
