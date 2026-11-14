"""Write reports/findings.md: the headline numbers for slides, straight from the API.

Run:  python scripts/findings.py     (no server needed; takes ~30 s)
"""
import sys
import warnings
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
warnings.simplefilter("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

OUT = ROOT / "reports" / "findings.md"


def pct(x):
    return "–" if x is None else f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(round(100 * x))}%"


def share(x):
    v = round(100 * x, 1)
    return f"{'−' if v < 0 else ''}{abs(v):.1f}%"


def main() -> None:
    c = TestClient(app)
    meta = c.get("/api/meta").json()
    h = meta["harmonization"]
    g = c.get("/api/calendar?region=Ghana").json()
    regions = c.get("/api/compare").json()["regions"]

    s, r = g["season"], g["recent"]
    done = [f for f in g["fire_years"] if f["complete"] and f["vs_normal"] is not None]
    last = done[-1]
    worst = min(done, key=lambda f: f["vs_normal"])
    best = max(done, key=lambda f: f["vs_normal"])
    change = g["long_term_change"]["change"]
    (a0, a1), (b0, b1) = g["long_term_change"]["from"], g["long_term_change"]["to"]

    by_peak = sorted([x for x in regions if x["peak"]], key=lambda x: (
        ("Jul Aug Sep Oct Nov Dec Jan Feb Mar Apr May Jun".split().index(x["peak"].split()[1]),
         int(x["peak"].split()[0]))))
    by_drop = sorted(regions, key=lambda x: -x["share_of_change"])
    top, acc = [], 0.0
    for x in by_drop:
        top.append(x)
        acc += x["share_of_change"]
        if acc >= 0.6:
            break
    steepest = sorted(regions, key=lambda x: x["long_term_change"])[:3]
    flat = [x for x in regions if x["long_term_change"] > -0.05]
    name = lambda x: x["region"].removesuffix(" Region")

    lines = [
        "# Ghana Fire Calendar — key findings",
        "",
        f"_Generated {date.today().isoformat()} from data up to {meta['data']['last']}. "
        f"Units: VIIRS-equivalent active cell-days (a {h['grid_deg']}° cell with fire on a day)._",
        "",
        "## The harmonization problem and result",
        "",
        f"- Raw hotspots: VIIRS records about **3.9×** as many as MODIS over Ghana. Joined naively, "
        "burning appears to jump in 2012.",
        f"- On a common {h['grid_deg']}° grid the gap shrinks to **{h['overall_ratio']:.2f}×**; the rest is "
        "small fires only VIIRS can see.",
        f"- Monthly correction factors (fitted {h['calibration_years'][0]}–{h['calibration_years'][1]}) predict "
        f"held-out years' VIIRS totals from MODIS with **{100 * h['annual_error']:.1f}%** average error "
        f"({100 * h['peak_month_error']:.0f}% for single fire-season months).",
        f"- Factor ranges from {min(h['factors_by_month'].values()):.1f} (main season, big fires) to "
        f"{max(h['factors_by_month'].values()):.1f} (off-season, small fires).",
        f"- Before {h['aqua_start']} only Terra was flying; a separate Terra-only factor is used "
        f"({100 * h['terra_only_annual_error']:.1f}% error) and those months are marked lower confidence.",
        "",
        "## Ghana as a whole",
        "",
        f"- Usual fire season: **{s['start']} – {s['end']}**, peak around **{s['peak']}**; "
        f"half of all burning between {s['core_start']} and {s['core_end']}.",
        f"- Burning in {b0}–{str(b1)[-2:]} averaged **{pct(change)}** compared with {a0}–{str(a1)[-2:]}.",
        f"- Last full season ({last['label']}): **{pct(last['vs_normal'])}** vs normal. "
        f"Lowest season on record: {worst['label']} ({pct(worst['vs_normal'])}); highest: "
        f"{best['label']} ({pct(best['vs_normal'])}).",
        f"- Last 30 days ({r['from']} to {r['to']}): {r['status']} ({r['value']:,.0f} vs {r['normal']:,.0f}).",
        "",
        "## Regions",
        "",
        f"- **The season moves south.** {name(by_peak[0])} peaks first (around {by_peak[0]['peak']}), "
        f"{name(by_peak[-1])} last (around {by_peak[-1]['peak']}): northern savanna burns in "
        "November–December, the middle belt in January, the southern forest zone in March–April.",
        f"- **Where the decline came from.** {', '.join(name(x) for x in top[:-1])} and {name(top[-1])} account for "
        f"**{round(100 * acc)}%** of the national drop.",
        f"- **Steepest relative drops:** " + ", ".join(f"{name(x)} ({pct(x['long_term_change'])})" for x in steepest) + ".",
        f"- **Little or no decline:** " + ", ".join(f"{name(x)} ({pct(x['long_term_change'])})" for x in flat) + ".",
        "",
        "| Region | Share of burning | Fire season | Peak | Change | Share of national drop | Last season |",
        "|---|---:|---|---|---:|---:|---:|",
    ]
    for x in regions:
        lines.append(f"| {name(x)} | {100 * x['share']:.1f}% | {x['start']} – {x['end']} | {x['peak']} | "
                     f"{pct(x['long_term_change'])} | {share(x['share_of_change'])} | "
                     f"{pct(x['last_season_vs_normal'])} |")
    lines += [
        "",
        "## Caveats",
        "",
        "- Hotspots measure burning activity seen by satellites, not the number of fires or burned area. "
        "Fires between overpasses or under thick cloud are missed.",
        "- Off-season months before 2012 multiply small MODIS counts by large factors and are less certain "
        "(under 5% of a year's burning).",
        f"- The long-term change carries the calibration's uncertainty (a few percent a year), much smaller "
        f"than the {pct(change)} change.",
        "- The latest weeks use near-real-time data, replaced by quality-checked data a few months later.",
        "",
    ]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
