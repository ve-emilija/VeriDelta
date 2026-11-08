import json
import sys

sys.path.insert(0, "/home/claude/veridelta/src")
from veridelta import graphrag_pipeline as gp

GRAPH_DIR = "/tmp/claude-0/-home-claude/87ad8046-c88e-5d67-a80e-7c09fa627593/scratchpad/ot_graph_check3"
bind_index = json.load(open(f"{GRAPH_DIR}/bind_index.json"))
assoc = json.load(open(f"{GRAPH_DIR}/association_edges.json"))

HTML = "/tmp/claude-0/-home-claude/87ad8046-c88e-5d67-a80e-7c09fa627593/scratchpad/gui/veridelta.html"

with open(HTML, encoding="utf-8") as f:
    lines = f.readlines()

PREFIX = "const DATA = "
line = lines[168]
assert line.startswith(PREFIX) and line.rstrip().endswith("];")
data = json.loads(line[len(PREFIX):].rstrip()[:-1])

for d in data:
    bind_at_risk = [{"file": b, "category": gp.bind_category(b)[1]} for b in gp.get_bind_at_risk(d["ip"], bind_index)]
    associations = assoc.get(d["changed"], [])
    d["bind_at_risk"] = bind_at_risk
    d["associations"] = associations

new_line = PREFIX + json.dumps(data, separators=(",", ": ")) + ";\n"
lines[168] = new_line

with open(HTML, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("done. sample:", json.dumps(data[1]["bind_at_risk"]), data[1]["associations"])
