# UI thread: build cost, rebuilds, lists and layout

Source: docs.flutter.dev/perf/best-practices, /perf/rendering-performance.

Use this when the **UI (bottom) graph** shows red bars, or the CPU profiler shows time in `build`, `layout`, or your own Dart code.

## Contents
- Keep build() cheap
- Scope rebuilds narrowly
- const constructors
- Widgets, not helper methods
- Animation builders: pass the static child
- Never override operator == on widgets
- Lists and grids: be lazy
- Avoid intrinsic layout passes
- StringBuffer
- Other UI-thread costs

## Keep build() cheap

`build()` can run whenever an ancestor rebuilds, often many times per second during animations. Anything expensive inside it is multiplied.

Move out of `build()`:
- Parsing, sorting, filtering, formatting large collections → compute once when data changes (in `initState`, `didUpdateWidget`, a view model, or a memoized getter).
- Creating controllers, `Future`s, `Stream`s → create in `initState`, not `build` (a `FutureBuilder(future: fetch())` refetches on every rebuild).
- Regex compilation, `DateFormat`/`NumberFormat` construction → make them `static final`.

```dart
// BAD: refetches + re-sorts on every rebuild
Widget build(BuildContext context) {
  return FutureBuilder(
    future: api.fetchItems(),
    builder: (context, snap) {
      final sorted = [...?snap.data]..sort((a, b) => a.name.compareTo(b.name));
      return ItemList(items: sorted);
    },
  );
}

// GOOD
late final Future<List<Item>> _items = api.fetchItems().then(
  (items) => items..sort((a, b) => a.name.compareTo(b.name)),
);

Widget build(BuildContext context) {
  return FutureBuilder(
    future: _items,
    builder: (context, snap) => ItemList(items: snap.data ?? const []),
  );
}
```

## Scope rebuilds narrowly

When `setState()` is called, **every descendant of that State rebuilds**. Calling it high in the tree for a change that affects a small part rebuilds far more than needed.

- Split large widgets into smaller ones based on **how they change**, not just on encapsulation.
- Move the state (and the `setState`) down into the smallest widget that owns the changing UI.
- Prefer targeted listenables: `ValueListenableBuilder`, `ListenableBuilder`, or a state-management selector (`context.select`, `Selector`, `ref.watch(provider.select(...))`, `BlocSelector`) so only dependents rebuild.
- `MediaQuery.of(context)` / `Theme.of(context)` register dependencies on the whole object; prefer the narrow accessors like `MediaQuery.sizeOf(context)`, `MediaQuery.paddingOf(context)` so unrelated changes (e.g. keyboard insets) don't rebuild you.

```dart
// BAD: whole page rebuilds every tick of a countdown
class _CheckoutPageState extends State<CheckoutPage> {
  int secondsLeft = 60;
  // Timer calls setState every second -> entire page (form, list, images) rebuilds
}

// GOOD: only the countdown text rebuilds
class CountdownText extends StatefulWidget { ... } // owns its Timer + setState
```

## const constructors

Rebuild traversal stops when Flutter re-encounters **the same widget instance** as last frame. `const` widgets are canonicalized — the same instance every time — so their subtree is skipped.

- Mark widget constructors `const` and instantiate them with `const` wherever arguments are compile-time constants.
- Enable lints `prefer_const_constructors`, `prefer_const_constructors_in_immutables`, `prefer_const_literals_to_create_immutables` (see `assets/analysis_options_perf.yaml`), then `dart fix --apply`.

## Widgets, not helper methods

To create reusable pieces of UI, prefer a `StatelessWidget` over a function/method that returns a `Widget`.

Why: a `Widget _buildHeader()` call is re-executed whenever the parent rebuilds and has no element of its own, so Flutter can't skip it, it can't be `const`, it can't rebuild independently, and it shares the parent's `BuildContext`. A separate widget class gets its own element, can be `const`, and can rebuild alone.

```dart
// BAD
Widget _buildHeader() => Padding(padding: const EdgeInsets.all(16), child: Text(title));

// GOOD
class _Header extends StatelessWidget {
  const _Header({required this.title});
  final String title;
  @override
  Widget build(BuildContext context) =>
      Padding(padding: const EdgeInsets.all(16), child: Text(title));
}
```

## Animation builders: pass the static child

`AnimatedBuilder` (and `ListenableBuilder`, `ValueListenableBuilder`, `TweenAnimationBuilder`) call `builder` every tick. Anything built inside that doesn't depend on the animation is rebuilt every frame. Build it once and pass it as `child` — this is the `TransitionBuilder` pattern the framework's own `SlideTransition` etc. use.

```dart
// BAD: the whole card rebuilds 60–120 times per second
AnimatedBuilder(
  animation: _controller,
  builder: (context, _) => Transform.rotate(
    angle: _controller.value * 2 * pi,
    child: const ExpensiveCard(),   // still re-traversed via builder
  ),
);

// GOOD
AnimatedBuilder(
  animation: _controller,
  child: const ExpensiveCard(),     // built once
  builder: (context, child) => Transform.rotate(
    angle: _controller.value * 2 * pi,
    child: child,
  ),
);

// BETTER when a transition widget exists: RotationTransition, FadeTransition,
// SlideTransition, ScaleTransition, SizeTransition — no builder at all.
RotationTransition(turns: _controller, child: const ExpensiveCard());
```

## Never override operator == on widgets

It seems like it would skip rebuilds, but it causes **O(N²)** behavior and, because even one override makes `==` calls non-static, it degrades performance across the board. Only exception: leaf widgets (no children) whose property comparison is much cheaper than rebuilding and that rarely change — and even then caching the widget instance (e.g. `const` or a `final` field) is preferable.

## Lists and grids: be lazy

Building a large list of children eagerly is one of the two most common pitfalls (the other is over-rebuilding).

- Use builder constructors so only visible (plus cache extent) items are built: `ListView.builder`, `ListView.separated`, `GridView.builder`, `SliverList(delegate: SliverChildBuilderDelegate(...))` / `SliverList.builder`, `SliverGrid.builder`.
- Avoid `Column`/`ListView(children: [...])`/`GridView.count(children:)` when most children are off screen.
- Avoid `SingleChildScrollView(child: Column(children: items.map(...).toList()))` for long data — it builds and lays out everything.
- Give fixed extents when items are uniform: `itemExtent` or `prototypeItem` — scrolling and jump-to-index skip measuring children.
- Avoid `shrinkWrap: true` on long lists, and nested scrollables with `shrinkWrap` + `NeverScrollableScrollPhysics`; combine into one `CustomScrollView` with slivers instead.
- Paginate network data (load one page at a time as the user nears the end).
- Use `Key`s (e.g. `ValueKey(item.id)`) on list items that can reorder so elements and state are reused rather than rebuilt.

## Avoid intrinsic layout passes

Flutter tries to lay out in one pass ("constraints go down, sizes go up, parent sets position"). An **intrinsic pass** happens when a widget asks children for their preferred size before laying them out — e.g. to make all cells as big as the biggest. In a grid this asks **every** cell (not just visible ones) and then lays them all out again.

Common triggers: `IntrinsicHeight`, `IntrinsicWidth`, `Table` with `IntrinsicColumnWidth`, some `shrinkWrap` usages, baseline alignment in large lists.

Detect: DevTools Performance → **Track layouts**; intrinsic events appear as `"$runtimeType intrinsics"`.

Fix:
- Give cells a fixed size up front (`SizedBox`, `itemExtent`, `SliverGridDelegateWithFixedCrossAxisCount(mainAxisExtent: ...)`).
- Or choose an "anchor" cell and size others relative to it via a custom `RenderObject` that lays out the anchor first.
- For "make siblings equal height" in a short `Row`, `IntrinsicHeight` is acceptable; it's the large/repeated case that hurts.

## StringBuffer

`+` creates a new `String` each time. In loops, use `StringBuffer` and call `toString()` once.

```dart
final sb = StringBuffer();
for (final line in lines) {
  sb..write(line.label)..write(': ')..writeln(line.value);
}
final text = sb.toString();
```

## Other UI-thread costs

- **Large synchronous work** (JSON decode of big payloads, image processing, crypto, big list filtering) belongs in an isolate — see `concurrency.md`.
- **Debug-only work** (`print` in hot paths, assertions-heavy validation) — guard with `kDebugMode` or remove; `debugPrint` is throttled but still costs.
- **Deep widget trees from wrapper widgets** — prefer `ColoredBox`/`DecoratedBox`/`SizedBox`/`Padding` over a `Container` that only sets one property (lints `use_colored_box`, `use_decorated_box`, `sized_box_for_whitespace`, `avoid_unnecessary_containers`).
- **GlobalKey reparenting** is expensive; don't create `GlobalKey`s in `build()`.
