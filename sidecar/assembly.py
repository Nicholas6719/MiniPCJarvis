"""Turning one OpenSCAD file into named parts he can zoom in on.

HIS PICTURE OF IT: *"Render me Iron Man's Mark 3 suit... I zoom in on the helmet
to see the helmet specs. I zoom in on the gauntlet to see the gauntlet specs."*
And, of a Spider-Man baseball: *"make his eyes smaller... make the lines on the
mask bigger."*

Both need the same thing, and it is not a better mesh: it is a model that KNOWS
WHAT ITS PIECES ARE CALLED. A single solid has no helmet in it to zoom into, and
no `eye_d` to shrink.

HOW A PART IS DECIDED, and why the model is not asked.

The model writes modules and calls them. We PARSE the result: a part is a
statement at the top level of the file that invokes a module defined in it.
`coil()` called from inside `coil_housing()` is a helper, not a part; a helper
promoted to a part would be six identical coils and no housing.

Asking the model to also emit a manifest of its own parts was the obvious
alternative and it is worse: it is a second thing to get wrong, it can disagree
with the code it describes, and there is no way to tell which of the two is
lying. The source is the only thing that actually builds.

THE DISPATCHER IS WRITTEN BY US, NOT BY THE MODEL. Rendering one part means
running OpenSCAD with `-D part="core"`, which needs a `part` variable and a
guard on every top-level call. That is mechanical, so it is generated from what
was parsed rather than requested in the prompt — the local model already has
three documented ways of mangling OpenSCAD it was asked to write, and this is
work it does not need to do.

A SIMPLE PART STAYS SIMPLE. One module, or none, means one part, and everything
behaves exactly as it did before. Nothing here forces structure onto a cube.
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger("jarvis.assembly")

# A ceiling on pieces, not a budget. It was 8, justified by "every part costs
# another OpenSCAD run" — measured at 0.5 s a part against the 25 s the language
# model takes to write the source, so that reasoning was worth nothing and a
# suit of armour has more than eight components. His instruction: "I would
# rather a render take 15-20 minutes and it's exactly what I want, than it take
# 5 minutes and we have to edit it 800 times."
#
# What this number now guards is COMPREHENSIBILITY — forty named pieces is not
# something anyone can work with by voice — and runaway output from the model.
MAX_PARTS = 24

# The variable the dispatcher switches on. Deliberately obscure enough that the
# model is unlikely to have used it for something of its own.
PART_VAR = "jarvis_part"

_MODULE_DEF = re.compile(r"\bmodule\s+([A-Za-z_]\w*)\s*\(", re.M)


def module_names(source: str) -> list[str]:
    """Every module the file defines, in the order it defines them."""
    return _MODULE_DEF.findall(source or "")


def _top_level_statements(source: str) -> list[tuple[int, int]]:
    """(start, end) of each statement outside every brace and every definition.

    Brace depth is the only thing that has to be tracked: a module definition is
    the one construct that opens a brace at depth 0, so skipping to its matching
    close skips the body without needing to understand what is in it. Strings
    and comments are stepped over so a `{` inside either cannot unbalance it.
    """
    spans: list[tuple[int, int]] = []
    i, n, start, depth = 0, len(source), 0, 0
    while i < n:
        c = source[i]
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i)
            i = n if j < 0 else j + 2
            continue
        if c == '"':
            j = i + 1
            while j < n and source[j] != '"':
                j += 2 if source[j] == "\\" else 1
            i = j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                # The end of a definition or of a braced top-level block.
                spans.append((start, i + 1))
                start = i + 1
        elif c == ";" and depth == 0:
            spans.append((start, i + 1))
            start = i + 1
        i += 1
    if start < n and source[start:].strip():
        spans.append((start, n))
    return spans


def _decomment(text: str) -> str:
    """`text` with every comment removed, so code can be asked what it calls.

    The model writes commented-out example calls — a hand-written version of
    the dispatcher we generate — and reading those as real calls made a
    statement claim to build a module it only mentioned.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find(chr(10), i)
            i = n if j < 0 else j
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i)
            i = n if j < 0 else j + 2
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:min(j + 1, n)])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _code_head(text: str) -> str:
    """`text` with any leading comments and blank lines removed.

    A module definition is not a part, and it does not always begin with the
    word: the model writes a comment block above each one, so testing the raw
    text read `// Iron Man\'s Arc Reactor` + `module arc_reactor() {...}` as a
    top-level CALL to arc_reactor. The file came back with two parts, both of
    them the same module, one of which was its own definition.
    """
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
        elif text.startswith("//", i):
            j = text.find(chr(10), i)
            i = n if j < 0 else j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i)
            i = n if j < 0 else j + 2
        else:
            break
    return text[i:]



def parts_in(source: str) -> list[dict]:
    """The named parts this source builds, in assembly order.

    Each is {"name", "start", "end", "text"} — the whole top-level statement,
    so `translate([0,0,12]) core();` keeps its placement and the part lands
    where it belongs in the assembly rather than at the origin.
    """
    src = source or ""
    known = set(module_names(src))
    if not known:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for a, b in _top_level_statements(src):
        text = src[a:b]
        stripped = text.strip()
        # A DEFINITION IS NOT A PART, and it does not always start with the word.
        # The model writes a comment block above each module, so testing the raw
        # text read a comment block followed by `module arc_reactor() {...}`
        # as a top-level CALL to arc_reactor — so the file came back with
        # two parts, both of them the same module, one of which was its
        # own definition. Comments come off first.
        if not stripped or _code_head(stripped).startswith("module"):
            continue
        # The module this statement builds: the first known name it calls.
        called = [m for m in re.findall(r"\b([A-Za-z_]\w*)\s*\(",
                                        _decomment(text)) if m in known]
        if not called:
            continue                      # an assignment, or a bare primitive
        name = called[0]
        if name in seen:
            # The same module placed twice — "left_arm" and "right_arm" are one
            # part called twice, and numbering them is more honest than
            # silently dropping the second.
            k = 2
            while f"{name}_{k}" in seen:
                k += 1
            name = f"{name}_{k}"
        seen.add(name)
        out.append({"name": name, "start": a, "end": b, "text": stripped})
    return _unwrap_master(src, out, known)


def _module_body(source: str, name: str) -> tuple[int, int] | None:
    """(start, end) of the braces belonging to `module name(...)`."""
    m = re.search(r"\bmodule\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    if not m:
        return None
    depth, i, n = 1, m.end(), len(source)
    while i < n and depth:
        c = source[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return (m.end(), i - 1) if depth == 0 else None


def _unwrap_master(source: str, parts: list[dict], known: set) -> list[dict]:
    """Read through a single module that just assembles the others.

    The model's habit, and the shape most OpenSCAD in the world has: define the
    pieces, define one module that calls them all, call that. One top-level
    call means one part, and the components he asked to zoom into are sitting
    one level down.

    Only a BARE master call is unwrapped. `translate([0,0,5]) arc_reactor();`
    places the whole assembly, and dropping that transform would move every
    part.
    """
    if len(parts) != 1:
        return parts
    only = parts[0]
    bare = _decomment(only["text"]).strip().rstrip(";").strip()
    if bare != f"{only['name']}()":
        return parts                       # not a bare call; it carries a transform
    span = _module_body(source, only["name"])
    if not span:
        return parts
    body = source[span[0]:span[1]]
    inner: list[dict] = []
    seen: set = set()
    for a, b in _top_level_statements(body):
        text = body[a:b].strip()
        if not text or _code_head(text).startswith("module"):
            continue
        called = [m for m in re.findall(r"\b([A-Za-z_]\w*)\s*\(",
                                        _decomment(text))
                  if m in known and m != only["name"]]
        if not called:
            continue
        name = called[0]
        if name in seen:
            k = 2
            while f"{name}_{k}" in seen:
                k += 1
            name = f"{name}_{k}"
        seen.add(name)
        inner.append({"name": name, "start": span[0] + a, "end": span[0] + b,
                      "text": text, "nested": True})
    # Two or more real components, or it was a wrapper around one shape and
    # there is nothing to take apart.
    return inner if len(inner) >= 2 else parts


def with_dispatcher(source: str, parts: list[dict]) -> str:
    """The same source, able to render one named part at a time.

    Every top-level part call is wrapped in a guard on `jarvis_part`, which
    OpenSCAD's `-D` overrides from the command line. Rendering the whole thing
    is the default, so the file still builds by hand exactly as written.
    """
    if not parts:
        return source
    # When the parts came from INSIDE a master module, the spans point into that
    # module's body — cutting them out would gut the module while its own
    # top-level call still stood. Keep the file whole and drop the master call
    # instead, or every component renders twice.
    #
    # ASKED, NOT INFERRED. This was `any(p["start"] < last_top_level_start)`,
    # which is true of an ordinary assembly as well — every part but the last
    # begins before the last statement does — so it took the nested path for
    # every file and each part rendered the whole model.
    inside = bool(parts and parts[0].get("nested"))
    if inside:
        keep, tail = [_without_master_call(source, parts)], ""
    else:
        keep = []
        last = 0
        for p in parts:
            keep.append(source[last:p["start"]])
            last = p["end"]
        tail = source[last:]

    lines = ['\n// -- rendered one part at a time; "all" builds the assembly',
             f'{PART_VAR} = "all";']
    for p in parts:
        # BRACES, AND THE STATEMENT UNTOUCHED. Collapsing it onto one line let a
        # `// comment` above the call swallow the call, and an `if` with an
        # empty body then captured the NEXT part's line as its own. Two parts
        # lost, no error anywhere.
        lines.append(f'if ({PART_VAR} == "all" || {PART_VAR} == "{p["name"]}") '
                     f'{{\n{p["text"]}\n}}')
    return "".join(keep) + tail.rstrip() + "\n" + "\n".join(lines) + "\n"


def _last_top_level_start(source: str) -> int:
    """Where the final top-level statement begins."""
    spans = _top_level_statements(source)
    return spans[-1][0] if spans else len(source)


def _without_master_call(source: str, parts: list[dict]) -> str:
    """The file with the wrapper's own top-level call removed."""
    known = set(module_names(source))
    inner = {p["name"].rsplit("_", 1)[0] for p in parts}
    out = source
    for a, b in reversed(_top_level_statements(source)):
        text = source[a:b].strip()
        if not text or _code_head(text).startswith("module"):
            continue
        called = [m for m in re.findall(r"\b([A-Za-z_]\w*)\s*\(",
                                        _decomment(text)) if m in known]
        if called and called[0] not in inner:
            out = out[:a] + out[b:]
    return out.rstrip()


def manifest_path(stl_path: str) -> str:
    """Where the part list for a model lives."""
    base = str(stl_path)
    for ext in (".stl", ".obj"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    return base + ".parts.json"


def write_manifest(stl_path: str, parts: list[dict]) -> str:
    """Record the parts beside the model, so projecting it later needs no rerun."""
    p = manifest_path(stl_path)
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"parts": parts}, fh, indent=1)
        return p
    except OSError:
        log.warning("could not write the part manifest for %s", stl_path,
                    exc_info=True)
        return ""


def read_manifest(stl_path: str) -> list[tuple[str, str]]:
    """[(name, file), ...] for a model that has parts, or [] for one that does not.

    A part whose file has gone is dropped HERE rather than further in, so the
    caller sees a shorter list instead of a missing file at render time.
    """
    p = manifest_path(stl_path)
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as fh:
            got = json.load(fh) or {}
    except (OSError, ValueError):
        log.warning("could not read the part manifest %s", p, exc_info=True)
        return []
    out = []
    for entry in got.get("parts") or []:
        name, path = entry.get("name"), entry.get("stl")
        if name and path and os.path.exists(path):
            out.append((str(name), str(path)))
    return out

# Where `with_dispatcher` starts writing. Everything from here down is ours.
_MARK = "// -- rendered one part at a time"
_GUARD = re.compile(
    r'^if \(' + PART_VAR + r' == "all" \|\| ' + PART_VAR
    + r' == "[^"]+"\) \{\n(.*?)\n\}$',
    re.M | re.S)


def strip_dispatcher(source: str) -> str:
    """The source as the model wrote it, with our scaffolding taken back off.

    Handing the dispatcher to a language model for editing asks it to reproduce
    a machine-generated guard per part, verbatim, on every change — tokens spent
    on boilerplate it did not write, and a guard it mangles still renders,
    because the conditions fall back to "all". It should only ever see its own
    code.
    """
    src = source or ""
    if _MARK not in src:
        return src
    head, tail = src.split(_MARK, 1)
    # Put the guarded statements back as plain top-level calls, in order.
    calls = [m.group(1).strip() for m in _GUARD.finditer(tail)]
    body = "\n".join(calls)
    return (head.rstrip() + "\n\n" + body + "\n") if body else head


def clear_manifest(stl_path: str) -> None:
    """Forget a model's parts, and delete the files that described them.

    Called when a rebuild produced no assembly. Leaving them would let
    `read_manifest` keep serving pieces of a model that no longer exists.
    """
    p = manifest_path(stl_path)
    stale = []
    try:
        with open(p, encoding="utf-8") as fh:
            stale = [e.get("stl") for e in (json.load(fh) or {}).get("parts") or []]
    except (OSError, ValueError):
        pass
    for f in [p] + [x for x in stale if x]:
        try:
            os.remove(f)
        except OSError:
            pass
