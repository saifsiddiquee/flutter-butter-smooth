# flutter-performance

A Claude skill that audits and optimizes Flutter apps: jank and dropped frames, rebuilds, memory leaks, app size, startup time and battery use. It follows the official Flutter performance docs (docs.flutter.dev/perf).

## What it does

- Runs a bundled static scanner (`perf_audit.py`) that flags known Flutter anti-patterns with file, line and fix.
- Walks through a measure-first workflow: profile mode on a physical device, then diagnose by UI thread or raster thread.
- Provides focused references on UI thread, raster thread, concurrency, memory, app size and profiling.
- Produces a structured performance report.

The skill activates when you mention Flutter or Dart performance, jank, slow scrolling, memory, app size, startup, or ask to optimize a Flutter codebase.

## Install

### As a plugin

```
/plugin marketplace add saifsiddiquee/flutter-butter-smooth
/plugin install flutter-performance@flutter-performance-marketplace
```

### Manually (user-level skill)

```bash
git clone https://github.com/saifsiddiquee/flutter-butter-smooth.git
cp -R flutter-butter-smooth/skills/flutter-performance ~/.claude/skills/
```

Start a new Claude Code session afterwards.

## Use the scanner directly

Python 3.8+, no dependencies:

```bash
python3 skills/flutter-performance/scripts/perf_audit.py <flutter_project> \
  [--out report.md] [--json report.json] [--include-tests] [--min-severity low|medium|high]
```

The scanner is heuristic. Read each finding in context before changing code.

## Optional lint set

Merge `skills/flutter-performance/assets/analysis_options_perf.yaml` into your project's `analysis_options.yaml` so the analyzer catches const and disposal issues as you edit.

## Layout

```
.claude-plugin/        plugin and marketplace manifests
skills/flutter-performance/
  SKILL.md             entry point and workflow
  references/          deep dives, loaded on demand
  scripts/perf_audit.py
  assets/analysis_options_perf.yaml
```

## License

MIT, see [LICENSE](LICENSE).
