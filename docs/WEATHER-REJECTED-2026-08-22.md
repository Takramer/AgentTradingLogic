# Kalshi temperature contracts — measured and rejected

**Closed 2026-08-22.** Code deleted (`rhengine/weather.py`, `tests/test_weather.py`,
the capture log, `docs/FREE-APIS-2026-08-21.md`). This note is all that remains,
because the repo records rejections as carefully as results and the next person
to notice ~1.6M contracts/day on a forecastable underlying deserves the number
rather than the idea.

## Why it looked good

Seven cities, twelve 1 °F strikes each, resolving next day, ~1.6M contracts/24h.
A weather forecast is a direct estimate of the settled value — almost nothing
else on the venue has a computable fair value. Kalshi even publishes
`expiration_value` on every settled market, so ground truth is free and exact.

## Why it is not

The model was being scored against **the weather**. The right opponent is **the
price**. The ladder's bucket probabilities imply an expected maximum; scoring
that against `expiration_value` over 25 settled days at 18h lead:

| series | market bias | market sd | free calibrated model sd (a FLOOR) |
|---|---|---|---|
| KXHIGHNY | +0.50 °F | 1.59 °F | 1.30 °F |
| KXHIGHLAX | +0.08 | 1.30 | 0.89 |
| KXHIGHMIA | −0.21 | **0.98** | 1.05 |
| KXHIGHCHI | −0.24 | 1.44 | 1.56 |
| KXHIGHAUS | −0.10 | **0.97** | 0.89 |
| KXHIGHDEN | +0.11 | 1.57 | 0.90 |
| KXHIGHPHIL | −0.06 | 1.34 | 1.18 |

**The market is unbiased** — seven cities, biases centred on zero. At n=25 the
standard error on an sd is ≈0.15–0.22 °F, so only Denver clears two of them, and
the model column is a floor (its hindcast archive mixes lead times and includes
nowcast data). On two cities the market is already ahead. A 0.3 °F sd edge moves
2 °F-bucket probabilities a few points against a 1–3 ¢ spread and a fee peaking
at 1.75 ¢.

Trials logged, family `weather` (2).

## Four things worth carrying forward

1. **Score against the price, not against reality.** Three sessions were spent
   calibrating a forecast before anyone asked how good the market's forecast was.
   It took one function and 25 days of free data to answer.
2. **`expiration_value` on settled Kalshi markets is exact ground truth**, and
   hourly candlesticks reconstruct any past ladder. That pattern is reusable for
   any Kalshi series — it is how the BTC work is validated.
3. **A bare `except Exception` counted a NameError as missing data** 150 times
   and reported "0 usable days". Two plausible wrong fixes were tried before the
   exception was printed. Catch network errors, not everything.
4. **Provider disagreement beat modelled residual.** Three forecast providers
   spanned 0.2–6.7 °F on the same day while the modelled residual was 0.9–1.6 °F.
   A single model's ensemble spread is not uncertainty.
