import json, collections, sys
d = json.load(open(r"C:\Users\nicho\Documents\spy.json"))
frames = d["shared"]["frames"]
prof = d["profiles"][0]
leaf_counts = collections.Counter()
any_counts = collections.Counter()
for stack, w in zip(prof["samples"], prof["weights"]):
    if not stack:
        continue
    leaf = frames[stack[-1]]
    leaf_counts[f"{leaf.get('name')}  [{str(leaf.get('file','')).split(chr(92))[-1]}:{leaf.get('line')}]"] += w
    for fi in set(stack):
        f = frames[fi]
        any_counts[f"{f.get('name')}"] += w
tot = sum(leaf_counts.values()) or 1
print("== hot leaves")
for name, c in leaf_counts.most_common(10):
    print(f"{100*c/tot:5.1f}%  {name}")
print("\n== functions present anywhere in stack")
for name, c in any_counts.most_common(14):
    print(f"{100*c/tot:5.1f}%  {name}")
