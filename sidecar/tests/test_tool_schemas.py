"""Every tool schema must survive the model's own chat template.

2026-09-08, and it cost a whole release. gpt-oss's Jinja template renders the
tool list like this:

    {%- if param_spec.type == "array" -%}
        {%- if param_spec['items'] -%}

`param_spec['items']` on a dict with no "items" key does not come back
undefined - it comes back as the dict's own .items METHOD, and minja refuses
it: "Function is not a bool value". llama-server answers **500 to the entire
request**, and the sidecar's stderr goes to DEVNULL, so all anyone sees is

    httpx.HTTPStatusError: Server error '500 Internal Server Error'

One array parameter missing its `items` therefore breaks every tool-using
turn in the app. On 2026-09-08 it broke 28 turns, failed bargein_e2e twice
(the reply it was supposed to interrupt never streamed a word), slowed
clarify_e2e past its budget, and answered "I hit a problem with that" to him
at 17:55.

Run: python tests/test_tool_schemas.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "schema.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def walk(schema, path, out):
    """Every subschema in a JSON-Schema fragment, with a readable path."""
    if not isinstance(schema, dict):
        return
    out.append((path, schema))
    for key in ("properties", "$defs", "definitions"):
        for k, v in (schema.get(key) or {}).items():
            walk(v, f"{path}.{k}", out)
    for key in ("items", "additionalProperties"):
        v = schema.get(key)
        if isinstance(v, dict):
            walk(v, f"{path}[{key}]", out)
    for key in ("anyOf", "oneOf", "allOf"):
        for i, v in enumerate(schema.get(key) or []):
            walk(v, f"{path}.{key}[{i}]", out)


def main() -> int:
    # the same set main.py registers - test_evolution_wiring keeps that honest
    from tools import (biometric, browser_tools, builtin, camera_tools, documents,
                       fabrication, office, image_tools,
                       file_tools, handoff, health, holo_tools, input_tools, location,
                       market_tools, memory_tools, news_tools, projects, render_tools,
                       model_tools, task_tools, uia, vision_analyze, vision_tools,
                       weather, workspace_tools, web_tools, windows_tools)
    import market_intel
    for m in (builtin, memory_tools, windows_tools, web_tools, task_tools,
              vision_tools, browser_tools, handoff, file_tools, weather,
              camera_tools, market_tools, news_tools, input_tools, uia,
              projects, location, health, vision_analyze, biometric, fabrication,
              holo_tools, render_tools, model_tools, documents, office, image_tools,
              workspace_tools, market_intel):
        m.register_all()
    from tools.registry import registry

    tools = list(registry._tools.values())
    print(f"\n-- {len(tools)} tools --")
    check("there are tools to check", len(tools) > 50, len(tools))

    bad_array, bad_type, bad_obj = [], [], []
    for t in tools:
        params = getattr(t, "parameters", None) or {}
        nodes = []
        walk(params, t.name, nodes)
        for path, s in nodes:
            ty = s.get("type")
            if ty == "array" and not isinstance(s.get("items"), dict):
                bad_array.append(f"{path} (items={s.get('items')!r})")
            # the same trap one level out: a bare "object" with no properties
            # renders nothing useful and invites the model to guess
            if ty == "object" and path != t.name and "properties" not in s:
                bad_obj.append(path)
            if ty is not None and not isinstance(ty, str):
                bad_type.append(f"{path} (type={ty!r})")

    print("\n-- an array must say what is IN it --")
    check("every array parameter has an `items` object", not bad_array,
          "; ".join(bad_array[:6]))
    check("...and every declared type is a plain string", not bad_type,
          "; ".join(bad_type[:6]))
    if bad_obj:
        print(f"  note: {len(bad_obj)} object schema(s) without properties "
              f"(allowed, but the model is guessing): {'; '.join(bad_obj[:4])}")

    # WHY THERE IS NO LIVE CHECK HERE. Rendering the real template needs
    # llama-server, and a gate must never depend on a running service or the
    # network. The rule above is the whole lesson the 500 taught;
    # scratchpad/probe_template.py drives the live server when one is up.

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
