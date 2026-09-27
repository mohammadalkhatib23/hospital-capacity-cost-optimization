# NHS frozen forecast-to-capacity planning

Read `nhs_frozen_three_month_uncertainty_optimization.ipynb`. It is the only
notebook in this repository; saved execution outputs are included. Cells 45-47
show the three simple total-cost comparisons.

| Origin | Frozen planning months |
| --- | --- |
| August 2025 | September-November 2025 |
| November 2025 | December 2025-February 2026 |
| February 2026 | March-May 2026 |

Three policies: latest-value persistence with deterministic optimization; locked
Random Forest available-bed and Extra Trees occupied-bed points with deterministic
optimization; the same ML points with 13 equally weighted, centered historical
paired error paths and expected-cost stochastic optimization. All three monthly
decisions stay frozen within each block. Each policy carries its own capacity
state into the next block. No CQR or CVaR is used in this experiment.

The series has 66 monthly national all-acute aggregate observations, not daily,
provider-level or patient-level records. G&A means General and Acute beds,
excluding maternity and mental health. Only occupied and available beds enter
planning; the threshold requirement is occupied / 0.90.

## View or run

Viewing saved outputs requires no execution. For a fresh run, first obtain the
workbook as described in `DATA_PROVENANCE.md` and place it in the repository root.
Use Python 3.10 in an isolated environment:

```sh
python -m venv .venv
# Activate .venv using the command appropriate to your operating system.
python -m pip install -r requirements.txt
python -m ipykernel install --user --name nhs-capacity --display-name "NHS capacity"
```

Open the notebook in VS Code or another Jupyter client using that kernel, with
the repository root as working directory. Run all cells. Alternatively run
`python nhs_frozen_three_month_uncertainty_optimization.py` from the root to
regenerate files (this does not update notebook inline outputs).
PuLP requires a working CBC solver; the import check reports local availability.
Do not run broad CLI workflows in the imported legacy modules.

## Reproduction boundary

This is a frozen-input experiment, not a rerun of the original model-selection
study. Evaluation forecasts are LOADED from the included 54-row forecast table.
The notebook refits 26 development estimators, constructs 13 residual paths,
then solves nine optimization problems and evaluates actual outcomes. It checks
against the included deterministic reference tables and 42 archived development
predictions. The two-row model-selection table records earlier locked choices;
it does not reproduce their selection. No prior trial notebooks are included.

`forecasting_evaluation.py` defines data cleaning, lag/calendar features, tree
estimators and recursive feature rows. `frozen_uncertainty_experiment.py` defines
development recursion, scenarios and policy wrappers. `optimization_evaluation.py`
defines the solvers and realized evaluator. `optimization_revision.py`,
`uncertainty_evaluation.py` and `deep_forecasting_evaluation.py` are retained because
the unchanged import chain needs them, not because deep learning or conformal
methods run here. Optional Darts/Torch imports are guarded; do not install them
for this notebook. Uncalled legacy entry points require artifacts not included.

The included regression JSON is HISTORICAL evidence read by cell 42. Packaging
does not rerun those tests or supply the old trial test fixtures. Any test-pass
text in saved output is historical, not proof of a new execution.
See `REPRODUCTION_REVIEW.md` for exactly what was checked and what remains blocked.

## Interpretation

These costs and cost reductions are illustrative modeled quantities, NOT NHS
cash savings. Cost coefficients, physical limits and the 90% threshold are
unvalidated. Uncertainty improves on ML point planning in August and February
but loses in November; it does not win every comparison. Thirteen overlapping
historical paths and nine already-inspected evaluation months support only an
exploratory comparison. Publication-time availability, data revisions and the
boundary between extra and published baseline capacity remain unresolved.

The workbook is not committed. The owner has authorized this baseline public
release. That authorization does not resolve the documented data-reuse and code
licensing questions; see `DATA_PROVENANCE.md`. Fresh end-to-end reproduction
has not been verified.
