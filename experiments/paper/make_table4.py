import json, os, matplotlib
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.family"] = "Liberation Serif"
R = json.load(open(os.path.join(ROOT, "results", "run1", "results.json")))["results"]
SYS = [("graphrag", "VeriDelta"), ("llm_no_history", "LLM, no history"), ("structural_prior", "Structural prior"), ("llm_no_graph", "LLM, no graph")]
CATS = ["Interface Drift", "Sequence Invalidation", "Checker Desync", "RAL Desync", "Coverage Regression"]
HDR = ["IF", "SI", "CD", "RD", "CR"]
def row(s):
    m = R["filtered"]["metrics"][s]; u = R["unfiltered"]["metrics"][s]
    return [m[c]["f1"] for c in CATS] + [m["micro"]["precision"], m["micro"]["recall"], m["micro"]["f1"], u["micro"]["f1"]]
rows = {s: row(s) for s, _ in SYS}
sup = [R["filtered"]["metrics"]["graphrag"][c]["support"] for c in CATS]
best = [max(rows[s][j] for s, _ in SYS) for j in range(9)]
W, FS = 3.45, 6.6
xl, xr = 0.03, W - 0.03
x0n = 1.00; cw = (xr - x0n) / 9
cen = [x0n + cw * (j + 0.5) for j in range(9)]          # 5 categories | P R F1 | F1*
RH = 0.19
H = 0.12 + RH * (2 + 1 + len(SYS)) + 0.02
fig = plt.figure(figsize=(W, H), facecolor="white"); ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
y = H - 0.06
def hline(yy, lw=0.5, a=xl, b=xr): ax.plot([a, b], [yy, yy], color="black", lw=lw, solid_capstyle="butt")
hline(y, 0.9)
ym = y - RH / 2
ax.text((x0n + x0n + 5 * cw) / 2, ym, "F1 per category", fontsize=FS, style="italic", ha="center", va="center")
ax.text((x0n + 5 * cw + x0n + 8 * cw) / 2, ym, "Micro, restricted", fontsize=FS-0.3, style="italic", ha="center", va="center")
ax.text(cen[8], ym, "Unrestr.", fontsize=FS - 0.3, style="italic", ha="center", va="center")
hline(y - RH * 0.97, 0.4, x0n + 0.04, x0n + 5 * cw - 0.04); hline(y - RH * 0.97, 0.4, x0n + 5 * cw + 0.04, x0n + 8 * cw - 0.04); hline(y - RH * 0.97, 0.4, x0n + 8 * cw + 0.04, xr - 0.02)
y -= RH; ym = y - RH / 2
ax.text(xl + 0.04, ym, "System", fontsize=FS, fontweight="bold", ha="left", va="center")
for j, t in enumerate(HDR + ["P", "R", "F1", "F1"]):
    ax.text(cen[j], ym, t, fontsize=FS, fontweight="bold", ha="center", va="center")
y -= RH; hline(y, 0.5); ym = y - RH / 2
ax.text(xl + 0.04, ym, "Support (points)", fontsize=FS, style="italic", ha="left", va="center")
for j, n in enumerate(sup):
    ax.text(cen[j], ym, f"{n:,}", fontsize=FS, style="italic", ha="center", va="center")
ax.text(cen[6], ym, "1,673", fontsize=FS, style="italic", ha="center", va="center")
ax.text(cen[8], ym, "2,055", fontsize=FS, style="italic", ha="center", va="center")
y -= RH; hline(y, 0.5)
for s, name in SYS:
    ym = y - RH / 2
    ax.text(xl + 0.04, ym, name, fontsize=FS, ha="left", va="center")
    for j, v in enumerate(rows[s]):
        ax.text(cen[j], ym, f"{v:.2f}", fontsize=FS, ha="center", va="center", fontweight="bold" if abs(v - best[j]) < 1e-9 else "normal")
    y -= RH
hline(y, 0.9)
fig.savefig(os.path.join(ROOT, "figures", "table4_results.png"), dpi=600, facecolor="white")
fig.savefig(os.path.join(ROOT, "figures", "table4_results.svg"), facecolor="white"); fig.savefig(os.path.join(ROOT, "figures", "table4_results.pdf"), facecolor="white")
for s, name in SYS: print(name, [f"{v:.2f}" for v in rows[s]])
