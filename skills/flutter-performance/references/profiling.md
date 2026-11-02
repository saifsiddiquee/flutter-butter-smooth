# Profiling and measuring Flutter performance

Source: docs.flutter.dev/perf/ui-performance, /perf/metrics, /perf/web-performance, /tools/devtools.

## Contents
- Ground rules
- Running in profile mode
- The performance overlay
- Flutter's threads
- DevTools views to use
- Custom tracing
- Metrics to track (and how to read them in code)
- Benchmarks with integration tests
- Web

## Ground rules

1. **Physical device, profile mode.** Debug builds are JIT-compiled (JIT pauses cause jank) and run expensive asserts; simulators/emulators have different hardware where some operations are faster and some slower. Neither is representative of release.
2. **Test on the slowest device your users will reasonably use.** Fine on a flagship ≠ fine for users.
3. **Measure before and after.** Every optimization claim should be backed by numbers from the same interaction on the same device.

## Running in profile mode

```bash
flutter run --profile
```

- VS Code: in `launch.json` set `"flutterMode": "profile"` on the Flutter configuration (revert when done).
- Android Studio / IntelliJ: **Run > Flutter Run main.dart in Profile Mode**.

Then open DevTools (the URL is printed by `flutter run`, or launch from the IDE).

## The performance overlay

Toggle with:
- DevTools → Performance view → **Performance Overlay** button
- `P` key in the `flutter run` terminal
- Programmatically: `MaterialApp(showPerformanceOverlay: true, ...)`

Reading it:
- Two graphs, each showing the last 300 frames. **Top = raster thread ("GPU")**, **bottom = UI thread**.
- White horizontal lines are 16 ms increments. Crossing one means < 60 Hz.
- A **red vertical bar** = a frame that missed its deadline.
  - Red in UI graph → Dart code too expensive.
  - Red in raster graph → scene too complex to render.
  - Red in both → **start with the UI thread**.
- The graph only updates when the app paints; idle = flat.

## Flutter's threads

| Thread | Runs | In overlay? | Notes |
|---|---|---|---|
| Platform | Platform main thread, plugin code | No | |
| UI | All Dart code (yours + framework); builds the layer tree | Bottom graph | **Never block it** |
| Raster | Takes layer tree and draws via GPU (Impeller/Skia) | Top graph | Runs on CPU; slow = something your Dart code asked for |
| I/O | Expensive I/O work | No | |

You can't touch the raster thread directly, but what you build on the UI thread determines its cost.

## DevTools views to use

- **Performance view**: frame chart (UI vs raster per frame), timeline events, *Track widget builds*, *Track layouts* (shows `$runtimeType intrinsics` events), *Track paints*, checkerboard offscreen layers (`saveLayer`) and raster cache images toggles, export/import traces.
- **CPU Profiler**: flame chart / call tree for expensive Dart functions.
- **Flutter Inspector**: widget tree, **Slow Animations** (5x) to isolate animation problems, highlight repaints.
- **Memory view**: see `memory.md`.
- **App Size tool**: see `app-size-and-startup.md`.
- **IDE widget rebuild stats**: Android Studio/IntelliJ Flutter Performance window → *Show widget rebuild information* — counts rebuilds per widget per frame, flags frames > 16 ms.

## Custom tracing

```dart
import 'dart:developer';

Timeline.startSync('parseFeed');
try {
  parseFeed(raw);
} finally {
  Timeline.finishSync();
}

// Async work spanning frames:
final task = TimelineTask()..start('loadProfile');
await loadProfile();
task.finish();
```

Events appear in the DevTools timeline (and in Chrome DevTools for web).

## Metrics to track

| Metric | How to get it |
|---|---|
| Startup time to first frame | Time until `WidgetsBinding.instance.firstFrameRasterized` completes |
| Per-frame build / raster / total time | `FrameTiming.buildDuration`, `.rasterDuration`, `.totalSpan` |
| Frame time statistics | Track **average, 90th percentile, 99th percentile, worst** for both build and raster |
| CPU/GPU usage | Trace events (good approximation of energy use) |
| Release size | `--analyze-size` / store consoles (see `app-size-and-startup.md`) |

Collecting frame timings in-app (useful for field telemetry or quick checks in profile mode):

```dart
void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final sw = Stopwatch()..start();
  WidgetsBinding.instance.firstFrameRasterized.then((_) {
    debugPrint('Time to first frame: ${sw.elapsedMilliseconds} ms');
  });
  SchedulerBinding.instance.addTimingsCallback((List<FrameTiming> timings) {
    for (final t in timings) {
      final build = t.buildDuration.inMicroseconds / 1000;
      final raster = t.rasterDuration.inMicroseconds / 1000;
      if (build + raster > 16) {
        debugPrint('Slow frame: build=${build}ms raster=${raster}ms');
      }
    }
  });
  runApp(const MyApp());
}
```

## Benchmarks with integration tests

Automate jank/regression detection with `integration_test` + `flutter_driver` timeline summaries:

```dart
// integration_test/scroll_perf_test.dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:my_app/main.dart' as app;

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('scroll feed', (tester) async {
    app.main();
    await tester.pumpAndSettle();
    final list = find.byType(Scrollable).first;
    await binding.traceAction(() async {
      await tester.fling(list, const Offset(0, -2000), 3000);
      await tester.pumpAndSettle();
    }, reportKey: 'scrolling_timeline');
  });
}
```

```dart
// test_driver/perf_driver.dart
import 'package:flutter_driver/flutter_driver.dart' as driver;
import 'package:integration_test/integration_test_driver.dart';

Future<void> main() {
  return integrationDriver(responseDataCallback: (data) async {
    if (data != null) {
      final timeline = driver.Timeline.fromJson(
          data['scrolling_timeline'] as Map<String, dynamic>);
      final summary = driver.TimelineSummary.summarize(timeline);
      await summary.writeTimelineToFile('scrolling_timeline',
          pretty: true, includeSummary: true);
    }
  });
}
```

```bash
flutter drive --driver=test_driver/perf_driver.dart \
  --target=integration_test/scroll_perf_test.dart --profile
```

The summary JSON includes average/percentile/worst frame build and rasterizer times — compare across commits.

## Web

- Flutter DevTools **cannot connect** to a web app in profile mode. Use the **Chrome DevTools Performance panel** (Flutter ≥ 3.14 emits timeline events there).
- Steps: (optional) enable tracing flags in `main()` → `flutter run -d chrome --profile` → Chrome DevTools → Performance → Record.
- Tracing flags (set to `true` in `main()` before `runApp`):
  - `debugProfileBuildsEnabled` — event per widget build
  - `debugProfileBuildsEnabledUserWidgets` — event per user-created widget build
  - `debugProfileLayoutsEnabled` — event per RenderObject layout
  - `debugProfilePaintsEnabled` — event per RenderObject paint
- Also usable on web: `dart:developer` `Timeline` / `TimelineTask`.
- Web has no isolates; `compute()` runs on the main thread there.
- Load speed: tree shaking, deferred loading (`deferred as` imports produce separate `.js` chunks), image placeholders/precaching. Consider building with `--wasm` where supported.
