# App size and startup

Source: docs.flutter.dev/perf/app-size, /perf/deferred-components, /perf/metrics, /deployment/obfuscate.

## Measuring size correctly

- **Debug builds are not representative** — they carry hot-reload and debugging overhead.
- A plain release build (`flutter build apk`, `flutter build ios`) is the *upload* package; stores split it per device (DPI, CPU architecture), so it isn't the user's download size either.

**Closest real numbers**
- Android: `flutter build appbundle` → upload the `.aab` in Google Play Console → **Android vitals → App size** (download and install sizes; computed for an XXXHDPI arm64-v8a device).
- iOS: `flutter build ipa --export-method development` → `open build/ios/archive/*.xcarchive` → Distribute App → App Thinning: *all compatible device variants* → Strip Swift symbols → export. Read `App Thinning Size Report.txt`. Exact numbers come from App Store Connect.

**Breakdown (what is taking the space)**

```bash
flutter build apk --analyze-size        # or appbundle | ios | macos | linux | windows
```

Prints a per-category summary (assets, native code, Flutter libs, Dart code by package) and writes `*-code-size-analysis_*.json`. Open DevTools → **App Size tool** → load the JSON for a tree/treemap down to function level, or load two JSONs to **diff** before/after. (On iOS this measures a `.app`, not an IPA — use for relative comparisons.)

## Reducing size

1. **Split debug info** (can dramatically reduce code size) — keep the symbols to de-obfuscate crash stack traces:
   ```bash
   flutter build appbundle --obfuscate --split-debug-info=build/symbols
   flutter build ipa      --obfuscate --split-debug-info=build/symbols
   ```
   (Symbolize later with `flutter symbolize -i <stack> -d build/symbols/app.android-arm64.symbols`.)
2. **Assets**: remove unused ones; compress PNG/JPEG (or convert to WebP); don't ship multiple resolutions you don't need; prefer vector icons; subset or drop unused font weights.
3. **Dependencies**: minimize resources imported from libraries; review the per-package breakdown in `--analyze-size` and replace or drop heavy packages used for small features.
4. **Platform-specific code is tree-shaken**: guard platform-only code with `Platform.isWindows` etc. (`dart:io`) — the compiler drops unreachable branches for the target platform. (Use `kIsWeb` for web checks.)
5. **Android**: ship an App Bundle (`.aab`) so Play delivers per-ABI/per-density splits; if you must ship APKs, `flutter build apk --split-per-abi`. Enable R8 shrinking for the native side (on by default in release).
6. **Deferred components** for large, rarely used features (below).

## Deferred components (Android & web)

Download code + assets **only when the user needs them**, using Dart deferred imports.

```dart
import 'package:my_app/heavy_editor.dart' deferred as editor;

late final Future<void> _loaded = editor.loadLibrary(); // start early to hide latency

FutureBuilder<void>(
  future: _loaded,
  builder: (context, snap) => snap.connectionState == ConnectionState.done
      ? (snap.hasError ? Text('Error: ${snap.error}') : editor.HeavyEditor())
      : const CircularProgressIndicator(),
);
```

- **Web**: each deferred library becomes a separate `.js` chunk — no extra setup; great for initial load time.
- **Android**: uses Play Feature Delivery dynamic feature modules. Setup: add Play Core dependency, set `android:name="io.flutter.embedding.android.FlutterPlayStoreSplitApplication"` on `<application>`, add a `deferred-components:` section under `flutter:` in `pubspec.yaml`, then run `flutter build appbundle` and follow the validator (it generates files into `build/android_deferred_components_setup_files`). Commit `deferred_components_loading_units.yaml`. Test locally with `bundletool ... --local-testing`.
- Only active in profile/release; debug treats deferred imports as normal.
- Common mistake: one non-`deferred` import of the library anywhere pulls it back into the base unit. Check the loading-units YAML.
- Must still upload the whole app as one AAB; no partial updates.

## Startup time

Metric: time until `WidgetsBinding.instance.firstFrameRasterized` completes (see `profiling.md`). Measure in profile/release on a real device.

Practices:
- Keep `main()` minimal: only `await` what the first frame truly needs. Initialize analytics, remote config, ads, non-critical SDKs **after** the first frame (`WidgetsBinding.instance.addPostFrameCallback`) or lazily on first use.
- Run independent async init in parallel (`Future.wait([...])`) instead of sequential awaits.
- Show a lightweight first screen quickly; load data after.
- Avoid large synchronous work (JSON, DB migrations) before `runApp`; push it to an isolate.
- Use native splash/launch screens (Android splash, iOS launch storyboard) so the OS shows something instantly.
- Precache only the images needed for the first screen.
- Defer heavy feature code (deferred components on web/Android).
- Add-to-app: pre-warm and cache a `FlutterEngine` so the Flutter screen opens instantly (see docs "Loading sequence and performance").
