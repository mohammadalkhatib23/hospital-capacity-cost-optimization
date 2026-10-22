## SARIMA forecast performance

SARIMA is fitted separately to occupied and available beds at each origin using all eligible data available up to that origin, and forecasts six months ahead. MAE is the mean absolute difference between actual and forecast beds, reported for months 1-3 and 4-6. Actuals are from the NHS England publication dated 8 October 2026.

| Origin | Forecast months | Occupied MAE H1-H3 (beds) | Occupied MAE H4-H6 (beds) | Available MAE H1-H3 (beds) | Available MAE H4-H6 (beds) |
| --- | --- | ---: | ---: | ---: | ---: |
| Feb 2025 | Mar 2025-Aug 2025 | 683.7 | 379.9 | 208.0 | 141.6 |
| Jun 2025 | Jul 2025-Dec 2025 | 380.2 | 802.1 | 123.6 | 578.0 |
| Aug 2025 | Sep 2025-Feb 2026 | 752.9 | 940.5 | 434.1 | 631.3 |
| Feb 2026 | Mar 2026-Aug 2026 | 366.4 | 792.6 | 686.0 | 149.9 |
| Mar 2026 | Apr 2026-Sep 2026 | 731.3 | 1,183.3 | 313.9 | 696.3 |

![SARIMA MAE, months 1-3 vs 4-6](figures/sarima_mae_early_vs_late.png)
