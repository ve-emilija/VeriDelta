import csv, os, re, sys, random, collections, json
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(HERE))
import networkx as nx
from veridelta import type_annotator as ta
S=os.environ.get("VD_SOURCES", os.path.join(ROOT,"sources")+os.sep)
nodes={}
for repo in ("opentitan","caliptra-rtl"):
    G=nx.DiGraph()
    for f in ta.collect_files(S+repo,False):
        for p,c in ta.extract_class_edges(ta._read(f)): G.add_edge(p,c)
    ta.annotate_graph(G); nodes[repo]=G
C=collections.defaultdict(lambda:{"label":None,"date":None,"cls":set(),"files":[]})
for r in csv.DictReader(open(os.path.join(ROOT,"data","corpus","corpus_files.csv"))):
    d=C[(r["repo"],r["hash"])]; d["label"]=r["commit_label"]; d["date"]=r["author_date"]; d["files"].append(r["path"])
    base=os.path.basename(r["path"]); stem=re.sub(r"\.svh?$","",base)
    if base.endswith((".sv",".svh")) and stem in nodes[r["repo"]] and nodes[r["repo"]].nodes[stem]["type"]!="OTHER":
        d["cls"].add(stem)
V=sorted([(k,d) for k,d in C.items() if d["label"] in("verification","both") and d["cls"]],key=lambda x:x[1]["date"])
cut=int(len(V)*0.8); test=V[cut:]
rng=random.Random(42); order=test[:]; rng.shuffle(order)
sample=order[:400]; check=sample[:20]
msg={}
for r in csv.DictReader(open(os.path.join(ROOT,"data","corpus","commits.csv"),encoding="utf-8",errors="ignore")): msg[(r["repo"],r["hash"])]=(r["subject"],r["body"])
mine=set()   # (earlier pilot: commits reviewed by hand before this protocol; not part of the repository)
tk={k for k,_ in test}
print("test commits",len(test),"| your 49 reviewed commits in test set:",len(mine&tk),"| in the 400:",len(mine&{k for k,_ in sample}))
print("label mix of the 400:",collections.Counter(d["label"] for _,d in sample))
print("types of changed classes in the 400:",collections.Counter(nodes[k[0]].nodes[c]["type"] for k,d in sample for c in d["cls"]))
print("pairs in the 400:",sum(len(d["cls"]) for _,d in sample))
json.dump([{"repo":k[0],"hash":k[1],"date":d["date"],"label":d["label"],"classes":sorted(d["cls"]),"files":d["files"]} for k,d in sample],open(os.path.join(ROOT,"data","sample400_pin.json"),"w"),indent=1)
# hand-check sheet
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
wb=openpyxl.Workbook(); ws=wb.active; ws.title="Label 20 commits"
hdr=["#","Repo","Commit","Date","Commit message","Changed UVM classes (type)","Changed files (up to 12)","Category 1","Category 2 (optional)","Note"]
ws.append(hdr)
for i,(k,d) in enumerate(check,1):
    s,b=msg.get(k,("",""))
    cl=", ".join(f"{c} ({nodes[k[0]].nodes[c]['type']})" for c in sorted(d["cls"]))
    fl="\n".join(d["files"][:12])+(f"\n... (+{len(d['files'])-12} more)" if len(d["files"])>12 else "")
    ws.append([i,k[0],k[1][:8],d["date"][:10],(s+"\n\n"+b.strip())[:1500],cl,fl,"","",""])
widths=[4,12,10,11,60,40,60,22,22,30]
for j,w in enumerate(widths): ws.column_dimensions[chr(65+j)].width=w
for row in ws.iter_rows(min_row=1):
    for c in row: c.alignment=Alignment(wrap_text=True,vertical="top")
for c in ws[1]: c.font=Font(bold=True,color="FFFFFF"); c.fill=PatternFill("solid",fgColor="305496")
ws.freeze_panes="A2"
cats=["Interface Drift","Sequence Invalidation","Checker Desync","RAL Desync","Coverage Regression","None of these"]
dv=DataValidation(type="list",formula1='"'+",".join(cats)+'"',allow_blank=True); ws.add_data_validation(dv); dv.add(f"H2:I{len(check)+1}")
g=wb.create_sheet("Category definitions")
g.append(["Category","What it means"])
for a,b in [("Interface Drift","Signal layouts, widths or protocol parameters change, invalidating virtual interface handles or transaction field assumptions in agents, drivers and monitors."),
("Sequence Invalidation","Stimulus generation assumptions become invalid, so sequences produce illegal or incomplete stimulus."),
("Checker Desync","Scoreboard reference models diverge from the updated behavior."),
("RAL Desync","The register abstraction layer drifts from the actual register map after register additions, removals or address changes."),
("Coverage Regression","Coverage collection points become unreachable or insufficient after structural changes."),
("None of these","The change is a pure refactor, rename, comment or tooling change that would not cause any of the failures above.")]: g.append([a,b])
g.append([]); g.append(["Question for each commit","Looking at the commit, which failure category (one or two) could it cause in the components that depend on the changed classes? Please label from the commit itself, without looking at anything else."])
g.column_dimensions["A"].width=26; g.column_dimensions["B"].width=110
for row in g.iter_rows():
    for c in row: c.alignment=Alignment(wrap_text=True,vertical="top")
for c in g[1]: c.font=Font(bold=True)
wb.save(os.path.join(ROOT,"data","evaluation","hand_check_20.xlsx"))
print("sheet saved")
