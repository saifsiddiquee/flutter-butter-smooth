#!/usr/bin/env python3
"""
Flutter performance static audit.

Scans a Flutter project's Dart sources and platform config for patterns that the
official Flutter performance docs (docs.flutter.dev/perf) call out as costly.
Heuristic only: every hit should be read in context before changing code.

Usage:
    python3 perf_audit.py <project_dir> [--out report.md] [--json report.json]
                          [--include-tests] [--min-severity low|medium|high]

No third-party dependencies (Python 3.8+).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

SKIP_DIRS = {".dart_tool", "build", ".git", ".idea", ".vscode", "ios/Pods", ".fvm",
             ".pub-cache", "node_modules", ".symlinks", "Pods"}
GENERATED_SUFFIXES = (".g.dart", ".freezed.dart", ".gr.dart", ".config.dart",
                      ".mocks.dart", ".pb.dart", ".pbenum.dart", ".pbjson.dart")


@dataclass
class Finding:
    rule: str
    severity: str
    category: str
    file: str
    line: int
    title: str
    detail: str
    fix: str
    ref: str
    snippet: str = ""


@dataclass
class Report:
    project: str
    files_scanned: int = 0
    findings: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Source preprocessing
# --------------------------------------------------------------------------- #

def strip_comments_and_strings(src: str) -> str:
    """Blank out comments and string *contents* (keeping quotes and newlines) so
    regexes only see code. Line numbers and offsets are preserved."""
    out = list(src)
    i, n = 0, len(src)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
        elif c == "/" and nxt == "*":
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth += 1
                    j += 2
                elif src.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif c in ("'", '"'):
            raw = i > 0 and src[i - 1] == "r"
            triple = src[i:i + 3] in ("'''", '"""')
            q = src[i:i + 3] if triple else c
            j = i + len(q)
            while j < n:
                if not raw and src[j] == "\\":
                    j += 2
                    continue
                if src.startswith(q, j):
                    break
                if not triple and src[j] == "\n":
                    break
                j += 1
            blank(i + len(q), j)
            i = j + len(q)
        else:
            i += 1
    return "".join(out)


def match_close(code: str, open_idx: int, open_ch: str = "(", close_ch: str = ")") -> int:
    depth = 0
    for k in range(open_idx, len(code)):
        ch = code[k]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return k
    return len(code) - 1


def line_of(code: str, idx: int) -> int:
    return code.count("\n", 0, idx) + 1


def call_args(code: str, m: re.Match) -> tuple[int, int, str]:
    """Return (start, end, text) of the argument list for a call whose regex
    match ends right after the opening parenthesis."""
    open_idx = m.end() - 1
    close_idx = match_close(code, open_idx)
    return open_idx, close_idx, code[open_idx + 1:close_idx]


def top_level_named_args(args: str) -> set[str]:
    """Named args at depth 0 of an argument list."""
    names, depth, token = set(), 0, ""
    for ch in args:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if depth == 0:
            token += ch
            if ch == ",":
                token = ""
            mm = re.match(r"\s*(\w+)\s*:", token)
            if mm and ":" in token:
                names.add(mm.group(1))
    return names


def class_bodies(code: str):
    """Yield (name, header, body_start, body_end) for each class declaration."""
    for m in re.finditer(r"\bclass\s+(\w+)([^{;]*)\{", code):
        start = m.end() - 1
        end = match_close(code, start, "{", "}")
        yield m.group(1), m.group(2), start, end


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #

class Ctx:
    def __init__(self, rel: str, raw: str, code: str, report: Report):
        self.rel, self.raw, self.code, self.report = rel, raw, code, report
        self.raw_lines = raw.splitlines()

    def add(self, rule, severity, category, idx, title, detail, fix, ref):
        ln = line_of(self.code, idx)
        snippet = self.raw_lines[ln - 1].strip() if 0 < ln <= len(self.raw_lines) else ""
        self.report.findings.append(Finding(rule, severity, category, self.rel, ln, title,
                                            detail, fix, ref, snippet[:160]))


ANIM_HINTS = re.compile(r"AnimatedBuilder|ListenableBuilder|ValueListenableBuilder|"
                        r"TweenAnimationBuilder|\.value\b|AnimationController|setState")


def rule_opacity(ctx: Ctx):
    for m in re.finditer(r"(?<![\w.])Opacity\s*\(", ctx.code):
        o, c, args = call_args(ctx.code, m)
        window = ctx.code[max(0, m.start() - 600):m.start()]
        animated = bool(re.search(r"AnimatedBuilder|ListenableBuilder|ValueListenableBuilder|"
                                  r"TweenAnimationBuilder|builder\s*:", window)) and \
            bool(re.search(r"opacity\s*:\s*[^,]*\b(value|animation|_\w*[Aa]nim\w*)\b", args))
        zero = re.search(r"opacity\s*:\s*0(\.0)?\s*[,)]", args + ")")
        if zero:
            ctx.add("opacity-zero", "low", "Speed", m.start(), "Opacity(opacity: 0) used to hide",
                    "A zero-opacity widget is still built, laid out and may be painted.",
                    "Use Visibility, Offstage, or don't build the widget.", "raster-thread.md#opacity")
        elif animated:
            ctx.add("opacity-animated", "high", "Speed", m.start(), "Opacity driven by an animation",
                    "Opacity re-composites (often via saveLayer) every frame of the animation.",
                    "Use FadeTransition(opacity: animation) or AnimatedOpacity.",
                    "raster-thread.md#opacity")
        else:
            ctx.add("opacity-widget", "medium", "Speed", m.start(), "Opacity widget",
                    "Opacity is expensive; for simple shapes/text/images a translucent color is cheaper.",
                    "Use a semi-transparent color, Image(color:, colorBlendMode: BlendMode.modulate), "
                    "FadeInImage, or apply opacity to leaf widgets only.", "raster-thread.md#opacity")


def rule_savelayer(ctx: Ctx):
    pats = [
        (r"Clip\.antiAliasWithSaveLayer", "high", "Clip.antiAliasWithSaveLayer triggers saveLayer",
         "Allocates an offscreen buffer and forces a GPU render-target switch.",
         "Use Clip.antiAlias or Clip.hardEdge, or borderRadius instead of clipping."),
        (r"\.saveLayer\s*\(", "medium", "Explicit canvas.saveLayer()",
         "saveLayer is one of the most expensive painting operations.",
         "Precompute overlapping translucent shapes, avoid overlaps, or apply effects per element."),
        (r"(?<![\w.])ShaderMask\s*\(", "medium", "ShaderMask (may call saveLayer)",
         "ShaderMask can trigger saveLayer.",
         "Confirm the effect is needed; cache it as an image if static."),
        (r"(?<![\w.])ColorFiltered\s*\(", "medium", "ColorFiltered (may call saveLayer)",
         "ColorFilter can trigger saveLayer.",
         "Pre-tint assets, use Image(color:, colorBlendMode:), or limit to small leaf widgets."),
        (r"(?<![\w.])BackdropFilter\s*\(", "medium", "BackdropFilter (blur)",
         "Backdrop blurs are expensive raster work, especially when repeated or animated.",
         "Limit to one area, avoid in list items and animations."),
    ]
    for pat, sev, title, detail, fix in pats:
        for m in re.finditer(pat, ctx.code):
            ctx.add("savelayer", sev, "Speed", m.start(), title, detail, fix,
                    "raster-thread.md#savelayer--the-most-expensive-call")


def rule_lists(ctx: Ctx):
    for m in re.finditer(r"(?<![\w.])(ListView|GridView(?:\.count|\.extent)?)\s*\(", ctx.code):
        o, c, args = call_args(ctx.code, m)
        named = top_level_named_args(args)
        if "children" in named:
            dynamic = bool(re.search(r"\.map\s*\(|List\.generate|\bfor\s*\(|\.\.\.", args))
            ctx.add("non-lazy-list", "high" if dynamic else "low", "Speed", m.start(),
                    f"{m.group(1)} with an eager children list",
                    "All children are built up front, including off-screen ones."
                    + (" Children are generated from data, so the list may be long." if dynamic else
                       " Fine if the list is short and fixed."),
                    f"Use {m.group(1).split('.')[0]}.builder (with itemExtent/prototypeItem when uniform).",
                    "ui-thread.md#lists-and-grids-be-lazy")
        if re.search(r"shrinkWrap\s*:\s*true", args):
            ctx.add("shrinkwrap", "medium", "Speed", m.start(), f"{m.group(1)} with shrinkWrap: true",
                    "shrinkWrap lays out every child to measure the list, defeating laziness.",
                    "Combine nested scrollables into one CustomScrollView with slivers.",
                    "ui-thread.md#lists-and-grids-be-lazy")
    for m in re.finditer(r"(?<![\w.])SingleChildScrollView\s*\(", ctx.code):
        o, c, args = call_args(ctx.code, m)
        if re.search(r"\b(Column|Row|Wrap)\s*\(", args) and \
                re.search(r"\.map\s*\(|List\.generate|\bfor\s*\(", args):
            ctx.add("scrollview-column", "high", "Speed", m.start(),
                    "SingleChildScrollView + Column built from data",
                    "Builds and lays out every item even when off screen.",
                    "Use ListView.builder / SliverList.builder.", "ui-thread.md#lists-and-grids-be-lazy")


def rule_intrinsics(ctx: Ctx):
    for m in re.finditer(r"(?<![\w.])(IntrinsicHeight|IntrinsicWidth|IntrinsicColumnWidth)\s*\(", ctx.code):
        ctx.add("intrinsic", "medium", "Speed", m.start(), f"{m.group(1)} forces an intrinsic layout pass",
                "Children are measured twice; inside lists/grids every cell is queried.",
                "Give fixed sizes (SizedBox, itemExtent, mainAxisExtent) or size relative to an anchor.",
                "ui-thread.md#avoid-intrinsic-layout-passes")


WIDGET_BASES = re.compile(r"extends\s+(StatelessWidget|StatefulWidget|Widget|InheritedWidget|"
                          r"ProxyWidget|RenderObjectWidget|LeafRenderObjectWidget|"
                          r"SingleChildRenderObjectWidget|MultiChildRenderObjectWidget|HookWidget|"
                          r"ConsumerWidget|ConsumerStatefulWidget)\b")


def rule_operator_eq(ctx: Ctx):
    for name, header, s, e in class_bodies(ctx.code):
        if WIDGET_BASES.search(header):
            m = re.search(r"operator\s*==", ctx.code[s:e])
            if m:
                ctx.add("widget-operator-eq", "high", "Speed", s + m.start(),
                        f"operator == overridden on widget {name}",
                        "Causes O(N^2) behavior and de-optimizes == calls app-wide.",
                        "Remove it; cache the widget instance (const/final field) instead.",
                        "ui-thread.md#never-override-operator--on-widgets")


DISPOSABLES = {
    "AnimationController": "dispose", "TextEditingController": "dispose",
    "ScrollController": "dispose", "PageController": "dispose", "TabController": "dispose",
    "FocusNode": "dispose", "ValueNotifier": "dispose", "TransformationController": "dispose",
    "StreamSubscription": "cancel", "Timer": "cancel", "StreamController": "close",
    "VideoPlayerController": "dispose", "WebViewController": None,
}


def rule_dispose(ctx: Ctx):
    for name, header, s, e in class_bodies(ctx.code):
        if not re.search(r"extends\s+State\s*<|with\s+.*ChangeNotifier|extends\s+ChangeNotifier|"
                         r"extends\s+\w*(Cubit|Bloc|Controller|ViewModel|Notifier)\b", header):
            continue
        body = ctx.code[s:e]
        decl = re.compile(r"(?:late\s+)?(?:final\s+)?(" + "|".join(DISPOSABLES) +
                          r")(?:<[^>;=]*>)?\??\s+(\w+)\s*[;=]")
        for m in decl.finditer(body):
            typ, var = m.group(1), m.group(2)
            method = DISPOSABLES[typ]
            if method is None:
                continue
            released = re.search(rf"\b{re.escape(var)}\s*(\?|!)?\s*\.\.?\s*{method}\s*\(", body) or \
                re.search(rf"\b{re.escape(var)}\s*\?\.\s*{method}\s*\(", body)
            if not released:
                past = {"dispose": "disposed", "cancel": "cancelled", "close": "closed"}[method]
                ctx.add("not-disposed", "high", "Memory", s + m.start(),
                        f"{typ} '{var}' is never {past} in {name}",
                        "Undisposed controllers/subscriptions leak memory and may keep tickers/streams running.",
                        f"Call {var}.{method}() in dispose() (before super.dispose()).",
                        "memory.md#disposal-checklist")


def rule_context_fields(ctx: Ctx):
    for m in re.finditer(r"^\s*(?:late\s+)?(?:final\s+)?BuildContext\??\s+\w+\s*;", ctx.code, re.M):
        ctx.add("context-field", "high", "Memory", m.start(), "BuildContext stored in a field",
                "BuildContext is large and short-lived; storing it can leak the widget subtree.",
                "Read what you need (Theme.of(context), Navigator.of(context)) when needed; check mounted after await.",
                "memory.md#buildcontext-needs-extra-care")


def rule_helper_methods(ctx: Ctx):
    hits = [m for m in re.finditer(r"^\s*(?:static\s+)?Widget\s+(_?\w+)\s*\(", ctx.code, re.M)
            if m.group(1) not in ("build",)]
    if len(hits) >= 2:
        m = hits[0]
        names = ", ".join(h.group(1) for h in hits[:6]) + ("…" if len(hits) > 6 else "")
        ctx.add("widget-helper-methods", "low", "Speed", m.start(),
                f"{len(hits)} methods return Widget ({names})",
                "Helper methods re-run on every parent rebuild and can't be const or rebuild independently.",
                "Extract frequently rebuilt or reusable pieces into StatelessWidget classes with const constructors.",
                "ui-thread.md#widgets-not-helper-methods")


def rule_builder_child(ctx: Ctx):
    for m in re.finditer(r"(?<![\w.])(AnimatedBuilder|ListenableBuilder|ValueListenableBuilder|"
                         r"TweenAnimationBuilder)\s*(?:<[^>]*>)?\s*\(", ctx.code):
        o, c, args = call_args(ctx.code, m)
        if "child" in top_level_named_args(args):
            continue
        bm = re.search(r"builder\s*:\s*\(([^)]*)\)", args)
        if not bm:
            continue
        builder_body = args[bm.end():]
        nested_widgets = len(re.findall(r"\b[A-Z]\w*\s*\(", builder_body))
        if nested_widgets >= 4:
            ctx.add("builder-no-child", "medium", "Speed", m.start(),
                    f"{m.group(1)} rebuilds its whole subtree every tick",
                    f"The builder creates ~{nested_widgets} widgets per frame and no static child is passed.",
                    "Move the parts that don't depend on the animation/value into the `child:` parameter "
                    "and use the builder's child argument; or use FadeTransition/SlideTransition/etc.",
                    "ui-thread.md#animation-builders-pass-the-static-child")


def rule_future_in_build(ctx: Ctx):
    for m in re.finditer(r"(?<![\w.])(FutureBuilder|StreamBuilder)\s*(?:<[^>]*>)?\s*\(", ctx.code):
        o, c, args = call_args(ctx.code, m)
        key = "future" if m.group(1) == "FutureBuilder" else "stream"
        fm = re.search(rf"\b{key}\s*:\s*([\w.]+(?:<[^>]*>)?)\s*\(", args)
        if fm:
            ctx.add("future-in-build", "high", "Speed", m.start(),
                    f"{m.group(1)} creates its {key} inside build()",
                    f"A new {key} is created on every rebuild, restarting the work (refetch/re-subscribe).",
                    f"Create the {key} once in initState/a controller and pass the stored field.",
                    "ui-thread.md#keep-build-cheap")


def rule_globalkey_in_build(ctx: Ctx):
    for bm in re.finditer(r"Widget\s+build\s*\(\s*BuildContext\s+\w+\s*\)\s*\{", ctx.code):
        s = bm.end() - 1
        e = match_close(ctx.code, s, "{", "}")
        for m in re.finditer(r"GlobalKey\s*(?:<[^>]*>)?\s*\(", ctx.code[s:e]):
            ctx.add("globalkey-in-build", "medium", "Speed", s + m.start(), "GlobalKey created inside build()",
                    "A new GlobalKey each build forces the subtree to be torn down and recreated.",
                    "Create the key once as a State field.", "ui-thread.md#other-ui-thread-costs")


def rule_json(ctx: Ctx):
    if re.search(r"Isolate\.run|compute\s*\(|Isolate\.spawn", ctx.code):
        return
    for m in re.finditer(r"\bjsonDecode\s*\(|\bjson\.decode\s*\(", ctx.code):
        ctx.add("json-main-isolate", "low", "Speed", m.start(), "JSON decoded on the main isolate",
                "Large payloads decoded synchronously can exceed a frame and cause jank.",
                "For large/variable-size payloads use Isolate.run (or compute() if targeting web).",
                "concurrency.md#short-lived-isolaterun")
        break


def rule_images(ctx: Ctx):
    for m in re.finditer(r"(?<![\w.])Image\.(network|file|asset|memory)\s*\(", ctx.code):
        o, c, args = call_args(ctx.code, m)
        if not re.search(r"cacheWidth|cacheHeight", args):
            ctx.add("image-decode-size", "low", "Memory", m.start(),
                    f"Image.{m.group(1)} without cacheWidth/cacheHeight",
                    "Images are decoded at full resolution (width x height x 4 bytes) regardless of display size.",
                    "Pass cacheWidth/cacheHeight (logical size x devicePixelRatio) or wrap the provider in ResizeImage.",
                    "raster-thread.md#images")


def rule_mediaquery(ctx: Ctx):
    for m in re.finditer(r"MediaQuery\.of\s*\(\s*\w+\s*\)\s*\.\s*(size|padding|viewInsets|"
                         r"devicePixelRatio|orientation|textScaler|platformBrightness)", ctx.code):
        acc = {"size": "sizeOf", "padding": "paddingOf", "viewInsets": "viewInsetsOf",
               "devicePixelRatio": "devicePixelRatioOf", "orientation": "orientationOf",
               "textScaler": "textScalerOf", "platformBrightness": "platformBrightnessOf"}[m.group(1)]
        ctx.add("mediaquery-of", "low", "Speed", m.start(), f"MediaQuery.of(context).{m.group(1)}",
                "Depends on the whole MediaQueryData, so any change (e.g. keyboard insets) rebuilds this widget.",
                f"Use MediaQuery.{acc}(context).", "ui-thread.md#scope-rebuilds-narrowly")


def rule_should_repaint(ctx: Ctx):
    for m in re.finditer(r"bool\s+shouldRepaint\s*\([^)]*\)\s*(=>\s*true|\{\s*return\s+true\s*;)",
                         ctx.code):
        ctx.add("should-repaint-true", "medium", "Speed", m.start(), "CustomPainter.shouldRepaint always true",
                "The painter repaints on every rebuild even when nothing changed.",
                "Compare old and new inputs; drive animation via the `repaint:` Listenable.",
                "raster-thread.md#repaintboundary-and-the-raster-cache")


def rule_string_concat(ctx: Ctx):
    for loop in re.finditer(r"\b(for|while)\s*\(", ctx.code):
        o = loop.end() - 1
        c = match_close(ctx.code, o)
        brace = ctx.code.find("{", c, c + 5)
        if brace == -1:
            continue
        e = match_close(ctx.code, brace, "{", "}")
        m = re.search(r"\b\w+\s*\+=\s*['\"]|\b(\w+)\s*=\s*\1\s*\+\s*['\"]", ctx.code[brace:e])
        if m:
            ctx.add("string-concat-loop", "low", "Speed", brace + m.start(), "String concatenation in a loop",
                    "Each + creates a new String.", "Use StringBuffer and call toString() once.",
                    "ui-thread.md#stringbuffer")


DART_RULES = [rule_opacity, rule_savelayer, rule_lists, rule_intrinsics, rule_operator_eq,
              rule_dispose, rule_context_fields, rule_helper_methods, rule_builder_child,
              rule_future_in_build, rule_globalkey_in_build, rule_json, rule_images,
              rule_mediaquery, rule_should_repaint, rule_string_concat]


# --------------------------------------------------------------------------- #
# Project-level checks
# --------------------------------------------------------------------------- #

def project_checks(root: Path, report: Report):
    def add(rule, sev, cat, path, title, detail, fix, ref, line=1):
        report.findings.append(Finding(rule, sev, cat, str(path.relative_to(root)), line, title,
                                       detail, fix, ref))

    impeller_files = [
        (root / "android/app/src/main/AndroidManifest.xml",
         r"EnableImpeller\"\s+android:value=\"false\"|EnableImpeller\"[^>]*value=\"false\""),
        (root / "macos/Runner/Info.plist", r"FLTEnableImpeller</key>\s*<false\s*/>"),
        (root / "ios/Runner/Info.plist", r"FLTEnableImpeller</key>\s*<false\s*/>"),
        (root / "linux/runner/my_application.cc", r"set_enable_impeller\s*\([^)]*FALSE"),
        (root / "windows/runner/main.cpp", r"ImpellerSwitch::Disabled"),
    ]
    for path, pat in impeller_files:
        if path.exists():
            text = path.read_text(errors="ignore")
            m = re.search(pat, text, re.S)
            if m:
                add("impeller-disabled", "high", "Speed", path, "Impeller is disabled",
                    "Without Impeller, shaders may compile at runtime, causing first-run animation jank.",
                    "Remove the opt-out unless there is a documented fidelity bug; file [Impeller] issues instead.",
                    "raster-thread.md#impeller", text.count("\n", 0, m.start()) + 1)

    ao = root / "analysis_options.yaml"
    wanted = ["prefer_const_constructors", "cancel_subscriptions", "close_sinks"]
    text = ao.read_text(errors="ignore") if ao.exists() else ""
    missing = [w for w in wanted if w not in text]
    if missing:
        add("lints", "low", "Speed", ao, "Performance lints not enabled: " + ", ".join(missing),
            "The analyzer can catch missing const and leaked subscriptions/sinks as you type.",
            "Merge assets/analysis_options_perf.yaml, then run `dart fix --apply`.",
            "ui-thread.md#const-constructors")

    # Large raster assets
    big = []
    for d in ("assets", "images", "res", "lib/assets"):
        p = root / d
        if p.is_dir():
            for f in p.rglob("*"):
                if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp") and f.is_file():
                    size = f.stat().st_size
                    if size > 300 * 1024:
                        big.append((size, f))
    if big:
        big.sort(reverse=True)
        listing = "; ".join(f"{f.relative_to(root)} ({s // 1024} KB)" for s, f in big[:8])
        add("large-assets", "medium", "Size", big[0][1], f"{len(big)} raster asset(s) over 300 KB",
            "Large images inflate download size and memory when decoded. " + listing,
            "Compress (WebP/optimized PNG/JPEG), resize to the max displayed size, or load on demand.",
            "app-size-and-startup.md#reducing-size")

    fonts = [f for f in root.rglob("*") if f.suffix.lower() in (".ttf", ".otf")
             and not any(part in SKIP_DIRS for part in f.parts)]
    if len(fonts) > 6:
        add("many-fonts", "low", "Size", fonts[0], f"{len(fonts)} font files bundled",
            "Each weight/style adds to the app size.", "Drop unused weights or subset fonts.",
            "app-size-and-startup.md#reducing-size")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def iter_dart_files(root: Path, include_tests: bool):
    dirs = ["lib"] + (["test", "integration_test"] if include_tests else [])
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".")]
            for fn in filenames:
                if fn.endswith(".dart") and not fn.endswith(GENERATED_SUFFIXES):
                    yield Path(dirpath) / fn


def scan(root: Path, include_tests: bool) -> Report:
    report = Report(project=str(root))
    for path in iter_dart_files(root, include_tests):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        code = strip_comments_and_strings(raw)
        ctx = Ctx(str(path.relative_to(root)), raw, code, report)
        for rule in DART_RULES:
            try:
                rule(ctx)
            except Exception as exc:  # keep scanning other rules/files
                print(f"warning: {rule.__name__} failed on {path}: {exc}", file=sys.stderr)
        report.files_scanned += 1
    project_checks(root, report)
    report.findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.category, f.file, f.line))
    return report


def to_markdown(report: Report) -> str:
    fs = report.findings
    counts = {s: sum(1 for f in fs if f.severity == s) for s in SEVERITY_ORDER}
    by_rule: dict[str, int] = {}
    for f in fs:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
    out = [
        "# Flutter performance audit",
        "",
        f"Project: `{report.project}`  ",
        f"Dart files scanned: {report.files_scanned}  ",
        f"Findings: **{counts['high']} high**, {counts['medium']} medium, {counts['low']} low",
        "",
        "> Heuristic static scan based on docs.flutter.dev/perf. Confirm each finding in context and "
        "with profile-mode measurements before changing code. References point to files in this skill's "
        "`references/` folder.",
        "",
        "## Findings by rule",
        "",
        "| Rule | Count |",
        "|---|---|",
    ]
    out += [f"| `{r}` | {n} |" for r, n in sorted(by_rule.items(), key=lambda kv: -kv[1])]
    for sev in ("high", "medium", "low"):
        group = [f for f in fs if f.severity == sev]
        if not group:
            continue
        out += ["", f"## {sev.capitalize()} severity", ""]
        for f in group:
            out += [
                f"### {f.title}",
                f"- **Where:** `{f.file}:{f.line}` · {f.category} · rule `{f.rule}`",
            ]
            if f.snippet:
                out.append(f"- **Code:** `{f.snippet.replace('`', '')}`")
            out += [f"- **Why:** {f.detail}", f"- **Fix:** {f.fix}", f"- **Ref:** `references/{f.ref}`", ""]
    if not fs:
        out += ["", "No findings. Next step: profile on a physical device in profile mode."]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="Path to the Flutter project root (contains pubspec.yaml)")
    ap.add_argument("--out", help="Write markdown report to this file (default: stdout)")
    ap.add_argument("--json", help="Also write JSON findings to this file")
    ap.add_argument("--include-tests", action="store_true", help="Also scan test/ and integration_test/")
    ap.add_argument("--min-severity", choices=list(SEVERITY_ORDER), default="low")
    args = ap.parse_args(argv)

    root = Path(args.project).resolve()
    if not (root / "pubspec.yaml").exists():
        print(f"warning: no pubspec.yaml in {root}; scanning anyway", file=sys.stderr)

    report = scan(root, args.include_tests)
    limit = SEVERITY_ORDER[args.min_severity]
    report.findings = [f for f in report.findings if SEVERITY_ORDER[f.severity] <= limit]

    md = to_markdown(report)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"Wrote {args.out} ({len(report.findings)} findings, {report.files_scanned} files)")
    else:
        print(md)
    if args.json:
        Path(args.json).write_text(json.dumps({"project": report.project,
                                               "files_scanned": report.files_scanned,
                                               "findings": [asdict(f) for f in report.findings]},
                                              indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
