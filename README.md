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

## Optimisation problem

Planning gap:

```math
d_t = \frac{O_t}{0.90} - A_t
```

Point planning:

```math
\begin{aligned}
\min_{x,\,a,\,r,\,s,\,u}\quad & \sum_{t=1}^{6}\left(500\,x_t + 550\,a_t + 300\,r_t + 5000\,s_t\right)\\
\text{s.t.}\quad & x_t + s_t - u_t = d_t, && t = 1,\dots,6\\
& x_t = x_{t-1} + a_t - r_t, && t = 1,\dots,6\\
& 0 \le x_t \le 15000, && t = 1,\dots,6\\
& 0 \le a_t \le 5000,\quad 0 \le r_t \le 5000, && t = 1,\dots,6\\
& s_t \ge 0,\quad u_t \ge 0, && t = 1,\dots,6\\
& x_0 = 0
\end{aligned}
```

SAA (N equally weighted scenarios $j$):

```math
\begin{aligned}
\min_{x,\,a,\,r,\,s,\,u}\quad & \sum_{t=1}^{6}\left(500\,x_t + 550\,a_t + 300\,r_t\right) + \frac{5000}{N}\sum_{j=1}^{N}\sum_{t=1}^{6} s_{j,t}\\
\text{s.t.}\quad & x_t + s_{j,t} - u_{j,t} = d_{j,t}, && j = 1,\dots,N,\; t = 1,\dots,6\\
& x_t = x_{t-1} + a_t - r_t, && t = 1,\dots,6\\
& 0 \le x_t \le 15000,\quad 0 \le a_t \le 5000,\quad 0 \le r_t \le 5000, && t = 1,\dots,6\\
& s_{j,t} \ge 0,\quad u_{j,t} \ge 0, && j = 1,\dots,N,\; t = 1,\dots,6\\
& x_0 = 0
\end{aligned}
```

Variables:

- $x_t$: extra capacity active in month $t$ (beds)
- $a_t$: extra capacity added in month $t$ (beds)
- $r_t$: extra capacity removed in month $t$ (beds)
- $s_t$, $s_{j,t}$: shortfall against the planning requirement (beds)
- $u_t$, $u_{j,t}$: capacity above the planning requirement (beds)
- $O_t$, $A_t$: occupied and available beds (forecast inputs)
- $d_t$, $d_{j,t}$: planning gap (point forecast, or scenario $j$)
