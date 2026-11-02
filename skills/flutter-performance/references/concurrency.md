# Concurrency: moving heavy work off the UI isolate

Source: docs.flutter.dev/perf/isolates.

All Dart code runs in isolates — like threads, but with **no shared memory**; they talk only by messages. A Flutter app does everything on the **main isolate** by default, which also processes "paint frame" events 60 (or 120) times per second. Any single task longer than the gap between frames delays those events → jank, or an unresponsive app.

**The one hard rule:** use an isolate when a computation is causing jank because it takes longer than a frame gap. Don't move trivial work — spawning and copying have overhead.

## Common candidates
- Parsing/decoding large JSON or data files
- Reading large result sets from a local database
- Processing/compressing images, audio, video; media conversion
- Filtering/sorting large lists or walking file systems
- Async work with FFI
- Push notification processing

Note: `await` alone does **not** move work off the main isolate — an `async` function still runs its synchronous parts on the UI thread.

## Short-lived: Isolate.run

Spawns an isolate, runs the callback, returns the result, exits.

```dart
import 'dart:convert';
import 'dart:isolate';

Future<List<Photo>> getPhotos() async {
  final String jsonString = await rootBundle.loadString('assets/photos.json'); // main isolate
  return Isolate.run<List<Photo>>(() {
    final data = jsonDecode(jsonString) as List<Object?>;
    return data.cast<Map<String, Object?>>().map(Photo.fromJson).toList();
  });
}
```

`Isolate.run` and `compute` return results via `Isolate.exit`, which **transfers** the result without copying — so returning large results is cheap.

**Targeting web too?** Web has no isolates. Use `compute(fn, message)` from `package:flutter/foundation.dart`: it runs on the main thread on web and in a new isolate elsewhere (on mobile/desktop it is equivalent to `Isolate.run(() => fn(message))`). `fn` must be a top-level or static function.

```dart
final photos = await compute(parsePhotos, jsonString);

List<Photo> parsePhotos(String s) =>
    (jsonDecode(s) as List).cast<Map<String, Object?>>().map(Photo.fromJson).toList();
```

## Long-lived workers

If the same computation runs repeatedly (e.g. per scroll page, per incoming socket message), spawning each time adds overhead. Keep a background worker alive:
- `Isolate.spawn()` + `ReceivePort` / `SendPort` for two-way messaging (pattern in dart.dev/language/concurrency).
- Or a package such as `worker_manager` / `isolate_agents`.
- Many isolates: `IsolateNameServer` to look up ports by name.

Message cost: messages are **copied** between isolates except immutable objects (e.g. `String`, unmodifiable bytes), which are passed by reference. Keep messages small; send `TransferableTypedData` for big byte buffers.

## Plugins inside isolates

Background isolates can call platform plugins via `BackgroundIsolateBinaryMessenger`:

```dart
void main() {
  final token = RootIsolateToken.instance!;
  Isolate.spawn(_worker, token);
}

Future<void> _worker(RootIsolateToken token) async {
  BackgroundIsolateBinaryMessenger.ensureInitialized(token);
  final prefs = await SharedPreferences.getInstance(); // now works
}
```

## Limitations
- Isolates don't share globals: a mutated global in a spawned isolate doesn't change the main isolate's copy.
- No `rootBundle` and no `dart:ui`/widget work in spawned isolates — load assets on the main isolate and pass the bytes/string in.
- Background isolates can send to and get replies from the host platform, but **cannot receive unsolicited messages** (e.g. no long-lived Firestore listeners in a background isolate; one-off queries are fine).
- Web: no isolates (see `compute` above).
