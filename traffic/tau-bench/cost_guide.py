"""Regenerate the cost guide: cost-guide.svg and the cost tables in README.md.

    python3 cost_guide.py

Numbers come from tau_traffic.py's cost estimate (MODELS prices, TOKEN_PROFILES) and
from MEASURED below. After real runs: put the average cost per conversation from the
runs' summary.json files in MEASURED (and adjust TOKEN_PROFILES in tau_traffic.py if
estimates and measurements drift apart), then rerun this script.
"""

import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import dotenv  # noqa: F401  (tau_traffic imports it; not needed here)
except ImportError:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *a, **k: None)
import tau_traffic as tt  # noqa: E402

# Real average cost per conversation with the default models
# (gpt-4o-mini agent, gpt-4.1-mini customer): domain -> ($ per conversation, conversations).
MEASURED = {
    "retail": (0.0088, 8),
    "banking_knowledge": (0.0086, 3),
}
MEASURED_ON = "Nov 8, 2026"

AGENT, CUSTOMER = tt.DEFAULTS["agent_model"], tt.DEFAULTS["customer_model"]


def estimate(domain, agent=AGENT, customer=CUSTOMER, num=1):
    return tt.estimate_cost(SimpleNamespace(domain=domain, agent_model=agent,
                                            customer_model=customer, num=num))


def per_conversation(domain):
    """(cost, measured?) for the default models: measured if we have it, else estimate."""
    if domain in MEASURED:
        return MEASURED[domain][0], True
    return estimate(domain), False


def money(x):
    return f"${x:,.2f}" if x >= 0.995 else f"${x:.2f}" if x >= 0.1 else f"${x:.3f}"


# --------------------------------------------------------------------------- tables


def tables():
    domains = list(tt.DOMAINS)
    out = []
    out.append(f"**Per conversation** (simulated customer: {CUSTOMER}):\n")
    head = "| Domain | " + " | ".join(f"agent {m}" for m in tt.MODELS) + " | Measured |"
    out += [head, "|" + "---|" * (len(tt.MODELS) + 2)]
    for d in domains:
        cells = [money(estimate(d, agent=m)) for m in tt.MODELS]
        meas = (f"{money(MEASURED[d][0])} ({MEASURED[d][1]} conversations, {AGENT})"
                if d in MEASURED else "not yet")
        out.append(f"| {d} | " + " | ".join(cells) + f" | {meas} |")
    out.append("")
    out.append(f"**Batches** (default models; measured where available):\n")
    sizes = (10, 50, 100, 500)
    out += ["| Domain | " + " | ".join(f"{n} conversations" for n in sizes) + " |",
            "|" + "---|" * (len(sizes) + 1)]
    for d in domains:
        c, measured = per_conversation(d)
        out.append(f"| {d}{'' if measured else ' (estimate)'} | "
                   + " | ".join(money(c * n) for n in sizes) + " |")
    out.append("")
    out.append("**Spread over time, per day** (default models):\n")
    rates = (("one every 30 min", 48), ("one every 10 min", 144), ("one every 2 min", 720))
    out += ["| Domain | " + " | ".join(f"{r} ({n}/day)" for r, n in rates) + " |",
            "|" + "---|" * (len(rates) + 1)]
    for d in domains:
        c, measured = per_conversation(d)
        out.append(f"| {d}{'' if measured else ' (estimate)'} | "
                   + " | ".join(money(c * n) for _, n in rates) + " |")
    return "\n".join(out)


# --------------------------------------------------------------------------- chart

SVG_STYLE = """
  .surface { fill: #fcfcfb; } .title { fill: #0b0b0b; } .sub, .cat { fill: #52514e; }
  .tick { fill: #898781; } .grid { stroke: #e1e0d9; } .base { stroke: #c3c2b7; }
  .measured { fill: #2a78d6; } .estimate { fill: #86b6ef; } .val { fill: #0b0b0b; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; } .title { fill: #f0efec; } .sub, .cat { fill: #c3c2b7; }
    .grid { stroke: #2c2c2a; } .base { stroke: #383835; }
    .measured { fill: #3987e5; } .estimate { fill: #1c5cab; } .val { fill: #f0efec; }
  }
  text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
"""


def chart(batch=100):
    domains = list(tt.DOMAINS)
    rows = [(d, *per_conversation(d)) for d in domains]
    W, left, right, top, row_h, bar_h = 760, 150, 250, 78, 40, 22
    plot_w = W - left - right
    H = top + row_h * len(rows) + 46
    top_value = max(c * batch for _, c, _ in rows)
    step = 0.25 if top_value <= 1.5 else 0.5 if top_value <= 3 else 1.0
    axis_max = step * (int(top_value / step) + 1)
    x = lambda v: left + plot_w * v / axis_max  # noqa: E731

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" '
         f'height="{H}" role="img" aria-labelledby="t d">',
         f"<style>{SVG_STYLE}</style>",
         f'<title id="t">What {batch} conversations cost, by domain</title>',
         '<desc id="d">' + "; ".join(
             f"{d}: {money(c * batch)} ({'measured' if m else 'estimate'})"
             for d, c, m in rows) + "</desc>",
         f'<rect class="surface" width="{W}" height="{H}" rx="8"/>',
         f'<text class="title" x="24" y="32" font-size="17" font-weight="600">'
         f"What {batch} conversations cost, by domain</text>",
         f'<text class="sub" x="24" y="54" font-size="12.5">Default models: {AGENT} agent, '
         f"{CUSTOMER} simulated customer · bold bars = measured, faded bars = estimate</text>"]
    # gridlines and ticks
    v = 0.0
    while v <= axis_max + 1e-9:
        gx = x(v)
        p.append(f'<line class="grid" x1="{gx:.1f}" y1="{top - 6}" x2="{gx:.1f}" '
                 f'y2="{top + row_h * len(rows)}" stroke-width="1"/>')
        p.append(f'<text class="tick" x="{gx:.1f}" y="{top + row_h * len(rows) + 18}" '
                 f'font-size="11.5" text-anchor="middle">${v:.2f}</text>')
        v += step
    for i, (d, c, measured) in enumerate(rows):
        y = top + i * row_h + (row_h - bar_h) / 2
        w = max(x(c * batch) - left, 3)
        r = min(4, w / 2)
        # bar anchored at the baseline (square left), 4px rounded data end (right)
        p.append(f'<path class="{"measured" if measured else "estimate"}" '
                 f'd="M{left},{y} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{bar_h - 2 * r} '
                 f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z">'
                 f"<title>{d}: {money(c * batch)} per {batch} conversations "
                 f"({money(c)} each, {'measured' if measured else 'estimate'})</title></path>")
        p.append(f'<text class="cat" x="{left - 12}" y="{y + bar_h / 2 + 4.5:.1f}" '
                 f'font-size="13" text-anchor="end">{d}</text>')
        note = (f"measured, {MEASURED[d][1]} conversations" if measured else "estimate")
        p.append(f'<text x="{x(c * batch) + 8:.1f}" y="{y + bar_h / 2 + 4.5:.1f}" font-size="13">'
                 f'<tspan class="val" font-weight="600">{money(c * batch)}</tspan>'
                 f'<tspan class="sub"> · {note}</tspan></text>')
    p.append(f'<line class="base" x1="{left}" y1="{top - 6}" x2="{left}" '
             f'y2="{top + row_h * len(rows)}" stroke-width="1.5"/>')
    p.append(f'<text class="tick" x="24" y="{H - 12}" font-size="11">Measured {MEASURED_ON}. '
             f"Real runs vary: a single conversation cost from about half to double the average."
             f"</text>")
    p.append("</svg>")
    return "\n".join(p)


def main():
    (HERE / "cost-guide.svg").write_text(chart() + "\n")
    readme = HERE / "README.md"
    text = readme.read_text()
    start, end = "<!-- cost-tables:start -->", "<!-- cost-tables:end -->"
    if start in text and end in text:
        text = re.sub(re.escape(start) + r".*?" + re.escape(end),
                      lambda _: f"{start}\n{tables()}\n{end}", text, flags=re.S)
        readme.write_text(text)
        print("Updated cost-guide.svg and the tables in README.md")
    else:
        print("Updated cost-guide.svg (README.md has no cost-tables markers)")


if __name__ == "__main__":
    main()
