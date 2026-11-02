# Raster thread: painting cost, saveLayer, opacity, clipping, images, Impeller

Source: docs.flutter.dev/perf/best-practices, /perf/ui-performance, /perf/impeller, /perf/rendering-performance.

Use this when the **raster (top) graph** shows red while the UI graph is fine — the layer tree is cheap to build but expensive to draw.

## Contents
- saveLayer — the most expensive call
- Opacity
- Clipping
- Shadows and blurs
- RepaintBoundary and the raster cache
- Images
- Impeller

## saveLayer — the most expensive call

`saveLayer()` allocates an **offscreen buffer** and drawing into it can force a **render target switch**. GPUs want to stream like a firehose; switching targets interrupts that, and on mobile GPUs it's especially disruptive.

Your code may never call it directly — widgets and packages call it for you.

**Known triggers**
- `Clip.antiAliasWithSaveLayer` (as any `clipBehavior`)
- `ShaderMask`
- `ColorFilter` / `ColorFiltered`
- `Opacity` over a group of overlapping children
- `Chip` when `disabledColorAlpha != 0xff`
- `Text` with an `overflowShader`
- `BackdropFilter` (blur) — similarly costly

**Detect**: DevTools Performance view → enable **checkerboard offscreen layers** (`PerformanceOverlayLayer.checkerboardOffscreenLayers`); `saveLayer` also appears as a timeline event.

**Ask for each call**: Does the app need this effect? Can it be eliminated? Can it apply to one element instead of a group?

**Fix**
- Overlapping semi-transparent shapes that never change → precompute the composited result once (e.g. an image or a single pre-blended color) and draw that.
- Refactor painting so shapes don't overlap.
- Apply opacity/filters to **individual leaf widgets** rather than a parent high in the tree.
- If a package you don't own calls it excessively, ask the maintainer, swap the package, or write your own.
- Truly dynamic overlapping translucent shapes (e.g. server-driven) may genuinely need it — that's okay.

## Opacity

`Opacity` is expensive (often a layer/`saveLayer`). Especially avoid it **inside animations**.

| Instead of | Use |
|---|---|
| `Opacity` animated via setState/AnimatedBuilder | `FadeTransition(opacity: animation)` or `AnimatedOpacity` |
| `Opacity` around simple shape/text | Draw with a semi-transparent color: `color: Colors.black.withValues(alpha: 0.5)` / `TextStyle(color: c.withValues(alpha: .5))` (works when parts don't overlap) |
| `Opacity` around an image | `Image(color: Color.fromRGBO(255,255,255,0.5), colorBlendMode: BlendMode.modulate)` or `Image(opacity: const AlwaysStoppedAnimation(0.5))` (the `opacity` parameter also accepts a running `Animation`) |
| Fade-in image after load | `FadeInImage` (uses the GPU fragment shader for gradual opacity) |
| `Opacity(opacity: 0)` to hide | `Visibility`, `Offstage`, or just don't build it |

```dart
// BAD
AnimatedBuilder(
  animation: _ctrl,
  builder: (_, __) => Opacity(opacity: _ctrl.value, child: const Hero()),
);
// GOOD
FadeTransition(opacity: _ctrl, child: const Hero());
```

## Clipping

Clipping doesn't call `saveLayer` unless you request `Clip.antiAliasWithSaveLayer`, so it's cheaper than `Opacity` — but still costly. Clipping is off by default (`Clip.none`); only enable it where needed.

- Rounded rectangles: use `borderRadius` on `BoxDecoration`/`Material`/`Card`/`ClipRRect` alternatives instead of a clip where possible.
- Prefer `Clip.hardEdge` (fastest) or `Clip.antiAlias` over `Clip.antiAliasWithSaveLayer`.
- **Never clip inside an animation.** Pre-clip the image (or the static content) before animating it.
- For rounded images, an alternative is overlaying opaque corners onto a square instead of clipping.

## Shadows and blurs

Shadows (`BoxShadow`, `elevation`, `PhysicalModel`) and `BackdropFilter` can be expensive, especially many of them or high in the tree, or animated. Limit count, avoid large blur radii on many list items, and avoid animating them; consider pre-rendered shadow images for repeated static cards.

## RepaintBoundary and the raster cache

`RepaintBoundary` isolates a subtree into its own layer so it can be cached/reused instead of repainted.

Use when:
- A **static but complex** subtree is faded, rotated, translated or otherwise transformed.
- A small, frequently repainting widget (spinner, ticking clock, video) sits next to a large static area — wrap the frequently repainting part so it doesn't repaint the rest.

Don't sprinkle it everywhere: cache entries are **expensive to construct and consume a lot of GPU memory**. Verify benefit with DevTools (highlight repaints; raster cache stats in the Memory view).

`CustomPainter`: implement `shouldRepaint` correctly (return `false` when inputs are unchanged), and pass a `repaint:` `Listenable` instead of rebuilding the widget each tick.

## Images

Rendering a texture from an image file is one of the most expensive operations: fetch compressed bytes from storage → decode into RAM → upload to GPU memory.

- **Decode at display size**: `Image.network(url, cacheWidth: w, cacheHeight: h)` or `ResizeImage(provider, width: w)`. Use logical size × `MediaQuery.devicePixelRatioOf(context)`. A 4000×3000 photo shown at 100×100 otherwise costs ~48 MB decoded.
- Serve appropriately sized images from the backend/CDN; use compressed formats.
- **Precache** images needed on the next screen: `precacheImage(AssetImage('...'), context)` in `didChangeDependencies` or before navigation — removes pop-in and first-frame decode cost.
- Use placeholders (`FadeInImage`, `cached_network_image` placeholders) for perceived performance.
- Tune the global cache if needed: `PaintingBinding.instance.imageCache.maximumSizeBytes`.
- Prefer vector/`Icon` fonts for simple glyphs instead of many raster assets.

## Impeller

Impeller precompiles a smaller, simpler set of shaders **at engine build time**, so they never compile at runtime — eliminating first-run animation jank caused by shader compilation. It also builds pipeline state objects up front and caches explicitly, giving predictable frame times.

Status (Flutter 3.47):

| Platform | Impeller |
|---|---|
| iOS | Only renderer (no Skia option) |
| Android | Default on API 29+; falls back to OpenGL on older/non-Vulkan devices automatically |
| macOS / Linux / Windows | Default as of 3.47 (opt-out to be removed later) |
| Web | Uses Skia (CanvasKit/Skwasm) |

If an app sees jank **only the first time** an animation runs, check that Impeller hasn't been disabled:
- Android: look for `<meta-data android:name="io.flutter.embedding.android.EnableImpeller" android:value="false" />` in `AndroidManifest.xml` — remove it unless there's a known fidelity issue.
- macOS: `FLTEnableImpeller` = false in `Info.plist`.
- Linux: `fl_dart_project_set_enable_impeller(project, FALSE)` in `linux/runner/my_application.cc`.
- Windows: `project.set_impeller_switch(flutter::ImpellerSwitch::Disabled)` in `windows/runner/main.cpp`.
- CLI flag `--no-enable-impeller` disables it for a debug run (useful for A/B comparing).

If Impeller itself shows performance or fidelity problems, file an issue prefixed `[Impeller]` with device/chip info, a recording, and an exported performance trace.

Custom fragment shaders: keep them simple (see Impeller's "Guidance for writing efficient shaders" in the Flutter repo).
