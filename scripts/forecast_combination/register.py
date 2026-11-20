"""Freeze the design and populations before constructing or scoring Combo."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/forecast_combination"
ROLL = ROOT / "outputs/fair_comparison_rolling"
MONTHLY = ROOT / "outputs/monthly_origins"
VALUE = ROOT / "outputs/optimisation_value"
METHODS = ["Mean-24", "Persistence", "Seasonal naive", "SARIMA-24", "ExtraTrees-24"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def register(data_dir):
    if any((OUT / name).exists() for name in ("config.json", "config.sha256", "plans.csv", "costs.csv")):
        raise ValueError("Registration already exists or planning has begun.")
    review = OUT / "logic_review.md"
    assert "The design can proceed." in review.read_text(encoding="utf-8")
    manifests = [ROLL / "config.sha256", ROLL / "registration.sha256", ROLL / "plans.sha256",
                 MONTHLY / "data.sha256", VALUE / "config.sha256", VALUE / "rule_plans.sha256"]
    for manifest in manifests:
        for line in manifest.read_text().splitlines():
            expected, name = line.split(maxsplit=1)
            assert digest(manifest.parent / name) == expected, name
    rolling = json.loads((ROLL / "config.json").read_text(encoding="utf-8"))
    source = json.loads((data_dir / "SOURCE.json").read_text(encoding="utf-8"))
    assert digest(data_dir / source["file"]) == source["sha256"] == rolling["data"]["sha256"]
    p2 = rolling["origins"]
    formed = ["2025-09", "2026-03"]
    partial = ["2025-10", "2026-01"]
    p1 = [o for o in p2 if o not in formed]
    p3 = [o for o in p2 if "2025-04" <= o <= "2026-04"]
    assert sorted(p2) == p2 and [len(set(s)) for s in (p1, p2, p3)] == [23, 25, 11]
    assert p2[0] == "2024-02" and p2[-1] == "2026-04" and set(p2).isdisjoint(partial)
    populations = dict(P1=p1, P2=p2, P3=p3, P3_partial=partial, P3_all=sorted(p3 + partial))
    files = [*manifests, ROLL / "config.json", MONTHLY / "availability.csv",
             VALUE / "publication_input_hashes.json", ROLL / "report_source_hashes.json"]
    config = dict(
        baseline_commit="523311131936408f038f2b14e05871530a9737c6",
        part_a_sha256=digest(review), data=source, data_end="2026-10",
        source_hashes={p.relative_to(ROOT).as_posix(): digest(p) for p in files},
        methods=METHODS, populations=populations, idea_formed_at=formed,
        subsets={p: {str(r): origins[r::6] for r in range(6)}
                 for p, origins in populations.items() if p in ("P1", "P2", "P3")},
        forecast=dict(window_months=24, horizon_months=6, occupancy_target=0.90,
                      Combo="Equal-weight occupied and available forecasts, then occupied / 0.90 - available.",
                      secondary="Combo-median: median of the available gap forecasts. Descriptive point forecast, Rule and LP only.",
                      partial="Mean of the four available methods and their same-date raw errors. Report individually outside all population rankings."),
        bank="One paired occupied/available error path per common backtest. Average across members on exactly the same keys. "
             "Every path ends by the planning origin. Keep the registered shared backtest exclusions. No centring, resampling or clipping.",
        costs=dict(running=500, opening=550, closing=300, shortage=5000, capacity=15000, ramp=5000,
                   initial=0, terminal_charge=0, validated=False),
        endpoints={
            "E1": "Mean Protected cost minus same-origin hindsight and Combo wins/ties/losses against each individual Protected plan.",
            "E2": "Same comparison for LP.",
            "E3": "Maximum and empirical 90th percentile of LP and Protected excess (linear interpolation).",
            "E4": "Six-month mean gap error (actual minus point), then mean absolute value across origins, sample SD (ddof=1) and min/max.",
            "E5": "Coverage of the 0.9 scenario quantile: actual <= quantile overall and by horizon. Reuse the registered finite-bank order statistic. "
                  "Report observed counts, target 0.90, individual benchmarks and margins.",
            "E6": "Savings from Seasonal naive LP to Combo LP plus savings from Combo LP to Combo Protected. Equal origin weights.",
        },
        hypotheses={
            "H1": "Combo Protected strictly lowest mean excess among the five individuals and Combo.",
            "H2": "Combo Protected worst excess strictly lower than each individual Protected worst excess.",
            "H3": "Combo mean absolute six-month signed error strictly lower than every individual's.",
            "populations": ["P1", "P3"],
            "clear_winner": "H1, lowest mean Protected excess in at least four of six chronological every-sixth-origin subsets, "
                            "and more wins than losses against every individual. Lowest subset means include exact ties.",
        },
        conventions=dict(tie_band="1% of same-origin, same-penalty hindsight for pairwise wins/ties/losses",
                         sensitivity_penalties=[2500, 10000], indicative=10000,
                         sensitivity="Descriptive E1 on complete five-method populations only, using same-penalty saved comparators and hindsight.",
                         score_rtol=1e-6, optimisation_tolerance="1e-6 times own Rule cost",
                         no_refit=True, price_units="illustrative model units",
                         shared_direction="Compare unanimous misses with majority and mixed cases using signs of each method's six-month mean error.",
                         concentration="On P1, sort Combo Protected minus individual Protected by origin difference. Use cumulative sums "
                                       "and remove one/two strongest contributors to check dominance and sign reversals.",
                         bank_spread="For each P1 origin and horizon take empirical 90th minus 10th percentile of raw gap errors "
                                     "(linear interpolation), then average widths equally over origins."),
        gates=["Part A before registration", "config SHA-256 before planning", "plans SHA-256 before scoring",
               "Phase 2 checks and toy pytest", "Part B sanity checks before publication; stop on failure"],
    )
    (OUT / "config.json").write_bytes((json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode())
    sha = digest(OUT / "config.json")
    (OUT / "config.sha256").write_bytes(f"{sha}  config.json\n".encode())
    print(sha)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    register(parser.parse_args().data_dir)
