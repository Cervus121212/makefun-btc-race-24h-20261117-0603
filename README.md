# SMA Anchor Dip Rider

A cbBTC (CBBTC/USDC) trading bot for the make.fun competition
`btc-race-24h-20261117-0603` (race a BTC trading bot live for 24 hours).
It speaks `agent-fun/bars-replay/v1` on stdin/stdout (see `main.py`).

## Idea

The dev window is a 24-hour chop around $64.5k (+0.5% net) with several
1-2% swings. Momentum loses everywhere on it: every Donchian breakout
variant and every z-score variant ends negative. The deep dips, though,
all bounce: five separate events push the close 80+ bps below the
90-bar simple moving average, and every one recovers at least 99 bps
within four hours. So the bot does nothing most of the day and buys
only a deep dip measured against that slow anchor, then sells once the
price has climbed back past the anchor, with a trailing stop, a hard
stop and a time cap as rails.

## Rules

- **Anchor:** the simple moving average of the last 90 closes, frozen
  at its entry-time value so the exit target does not drift up.
- **Entry:** the close is at least **80 bps below the anchor**. One
  position, full cash. No uptick requirement: the dip itself is the
  signal, and waiting for a turn cost the third trade on this window.
- **Exit** (whichever comes first, decided on each bar's close, filled
  at the next open): the close reaches the **anchor + 75 bps**, the
  close retraces **150 bps** from the post-entry peak, the close is
  **400 bps** below the entry cost, or **300 bars** have passed.
- **Cooldown:** 5 bars after an exit before a new entry.
- **Fallback:** from bar 1080 on, if the bot has never held a position,
  the entry trigger relaxes to **60 bps** below the anchor, so a quiet
  window still produces at least one trade. This never fires on the dev
  window.
- Every order caps slippage at 200 bps from the decision close.

## Dev-window grade (local replay, exact harness fill math)

Graded locally with a faithful re-implementation of the kit's replay
(`sim/bars/engine.ts` fill math: 4 bps fee, 2 bps slippage,
constant-product impact against each bar's open depth, flatten at the
last close). The simulator was itself verified by replaying the two
previous race entries on their own kits and reproducing their recorded
dev grades exactly (+$35.54 and +$7.86). This bot was run twice; both
runs produced byte-identical replies.

| Metric | Value |
|---|---|
| Net PnL | **+$31.99** |
| Return | +3.20% |
| Trades | 6 (3 round trips) |
| Max drawdown | 0.75% |
| Rejected orders | 0 |

Trade log (order decided on batch → filled at the next bar's open):
buy seq 370 @ $64,040.46 → sell seq 510 @ $65,012.82 (+$14.37);
buy seq 573 @ $64,167.42 → sell seq 864 @ $65,061.09 (+$13.30);
buy seq 1100 @ $64,515.81 → sell seq 1400 @ $64,838.69 (+$4.32). All three
round trips are positive; the trailing stop and hard stop never bind
on the dev window.

## Robustness

Neighbouring parameter sets are all positive with the same trade count:
anchor 75-105 bars, entry 70-80 bps, exit target 50-100 bps above the
anchor, hold cap 270-330 bars (e.g. hold 270: +$30.95, hold 300:
+$31.99, hold 330: +$30.01). The result is a plateau, not a single
tuned point. The one sharp edge is the entry trigger: at 90 bps it
never fires on this window (the deepest dip is 87 bps below the
anchor), which is what the bar-1080 fallback is for.

## Files

- `main.py` — the bot (Python 3.13, standard library only).
- `vendor/makefun_bars.py` — byte-identical copy of the kit's Python
  SDK shim (`sdk/makefun_bars.py`).

## Determinism

The bot reads only the bars it is sent. No clock, no environment, no
randomness, no network, no files. Two runs on the same batches produce
byte-identical orders.

## License

MIT (see `LICENSE`).
