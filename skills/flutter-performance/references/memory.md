# Memory: leaks, bloat and disposal

Source: docs.flutter.dev/tools/devtools/memory, /perf/ui-performance.

## When to investigate
Crashes from out-of-memory, the app or device slowing down over time, the OS killing the app, or memory usage creeping up as the user navigates back and forth.

## Leak vs bloat
- **Leak**: memory grows progressively because unneeded objects stay reachable — e.g. a listener created on every screen visit and never removed. Even a small leak, repeated, eventually crashes.
- **Bloat**: using more memory than necessary — e.g. decoding images far larger than displayed, keeping streams open for the app's lifetime, caching everything forever.

Dart's GC frees only **unreachable** objects. Anything still referenced from a root (globals, statics, singletons, long-lived objects, closures held by them) is retained.

## Disposal checklist

Any object with `dispose()` must be disposed when its owner goes away. In `State`:

| Created in `initState` | Release in `dispose` |
|---|---|
| `AnimationController`, `TextEditingController`, `ScrollController`, `PageController`, `TabController`, `FocusNode`, `ValueNotifier`/`ChangeNotifier` you own | `.dispose()` |
| `StreamSubscription` | `.cancel()` |
| `Timer` / `Timer.periodic` | `.cancel()` |
| `StreamController` / sinks | `.close()` |
| `addListener(...)` on an external object | `removeListener(...)` |
| `WidgetsBinding.instance.addObserver(this)` | `removeObserver(this)` |
| Platform resources (video/camera/WebView/audio players) | their `dispose()` |

```dart
class _PlayerState extends State<Player> with SingleTickerProviderStateMixin {
  late final AnimationController _anim = AnimationController(vsync: this, duration: const Duration(milliseconds: 300));
  late final StreamSubscription<Position> _sub = positions.listen(_onPos);
  Timer? _poll;

  @override
  void dispose() {
    _poll?.cancel();
    _sub.cancel();
    _anim.dispose();
    super.dispose();          // always last
  }
}
```

Also: a running `AnimationController` keeps ticking (CPU + battery) — stop repeating animations when off-screen (`TickerMode`, `VisibilityDetector`, or pausing in `didChangeAppLifecycleState`).

Lints: `cancel_subscriptions`, `close_sinks` (see `assets/analysis_options_perf.yaml`). The `leak_tracker` / `leak_tracker_flutter_testing` packages can catch undisposed objects in widget tests.

## Closures retain what they capture

```dart
final handler = () => print(myHugeObject.name);
setHandler(handler);   // myHugeObject lives as long as handler is reachable
```

Capture only the small value you need (`final name = myHugeObject.name;`).

## BuildContext needs extra care

`BuildContext` is large and short-lived. Don't let it leak into long-lived places.

```dart
// BAD: if useHandler stores the closure long-term, the context (and its subtree) leaks
@override
Widget build(BuildContext context) {
  final handler = () => apply(Theme.of(context));
  useHandler(handler);
  ...
}

// GOOD: capture the long-lived, shared object instead
@override
Widget build(BuildContext context) {
  final theme = Theme.of(context);
  final handler = () => apply(theme);
  useHandler(handler);
  ...
}
```

Rules:
- Passing `context` into a closure is fine **only if the closure doesn't outlive the widget**.
- Never store a `BuildContext` in a `State` field or a singleton/service — the `State` can outlive the widget.
- After `await`, check `mounted` (or `context.mounted`) before using `context`.

## Images (largest source of bloat)
- Decode at display size: `cacheWidth`/`cacheHeight` or `ResizeImage` (decoded size = width × height × 4 bytes).
- Don't hold `ui.Image`/`Uint8List` byte buffers in long-lived state longer than needed; call `ui.Image.dispose()` on images you created manually.
- Limit `imageCache` if the app shows huge numbers of images: `PaintingBinding.instance.imageCache.maximumSizeBytes = 100 << 20;`.
- `RepaintBoundary` raster caches also consume GPU memory — use them sparingly.

## Other bloat sources
- Unbounded in-memory caches/maps/lists (add size limits / LRU).
- Keeping every page of paginated data forever; trim or use a database.
- `AutomaticKeepAliveClientMixin` / `PageView` keeping many heavy pages alive.
- Big `ListView(children:)` building all items (also a speed problem — see `ui-thread.md`).

## Investigating with DevTools Memory view (profile mode)
- **Chart**: Dart heap, native memory (decoded images, file buffers), raster cache, RSS, GC events over time. Steady upward staircase across repeated navigation = leak.
- **Profile Memory**: current allocation by class (export CSV).
- **Diff Snapshots**: snapshot → do the action (e.g. open and close a screen 10×) → snapshot → diff. Classes whose instance counts grow by ~10 are leaking; inspect their **retaining path** to find who holds them.
- **Trace Instances**: pick classes, exercise the feature, refresh — see which call stacks allocated them.
- *Retained size* (object + what it keeps alive) is what matters when hunting leaks, not shallow size.
