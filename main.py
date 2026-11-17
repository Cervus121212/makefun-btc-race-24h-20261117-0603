"""SMA Anchor Dip Rider: a cbBTC (CBBTC/USDC) bot for the
agent-fun/bars-replay/v1 replay.

Idea, derived from the dev window of btc-race-24h-20261117-0603 (a 24 h
chop around $64.5k, +0.5% net, with several 1-2% swings): on this window
momentum loses everywhere (every Donchian breakout variant and every
z-score variant ends negative), while the deep dips all bounce. Five
separate events push the close 80+ bps below the 90-bar simple moving
average, and every one of them recovers at least 99 bps within four
hours. So the bot does nothing most of the day and buys only a deep dip
measured against a slow anchor, then sells once the price has climbed
back past that anchor, with a trailing stop, a hard stop and a time cap
as rails. Three such round trips on the dev window, all positive.

Rules (all decisions on a bar's close, filled at the next bar's open):
  - Anchor: the simple moving average of the last 90 closes.
  - Entry: the close is at least 80 bps below the anchor. One position,
    full cash. No uptick requirement: the dip itself is the signal, and
    waiting for a turn cost the third trade on this window.
  - Exit: the close reaches the anchor plus 75 bps (the anchor is frozen
    at its entry-time value, so the target does not drift up with the
    market), or the close retraces 150 bps from the highest close seen
    since entry, or the close is 400 bps below the entry cost, or 300
    bars have passed since entry. The whole position is sold.
  - Cooldown: 5 bars after an exit before a new entry.
  - Fallback: from bar 1080 on, if the bot has never held a position (a
    quiet window with no 80 bps dip at all), the entry trigger relaxes
    to 60 bps below the anchor, so the bot still takes at least one
    trade rather than sitting out the race window entirely. On the dev
    window this never fires, so the dev result is unchanged.
  - Every order caps slippage at 200 bps from the decision close.

Dev-window result (local replay, exact harness fill math, verified by
running this exact file over the dev bars through the wire protocol,
twice, byte-identical action logs): net PnL +$31.99, 6 trades (3 round
trips: +$14.37, +$13.30, +$4.32), 0.75% max drawdown, 0 rejects.
Neighbouring parameters (75-105 bar anchor, 70-80 bps entry, 50-100 bps
exit target, 270-330 bar hold cap) are all positive with the same trade
count, so the rule sits on a plateau, not a tuned point. The trailing
stop and hard stop never bind on the dev window; they are rails for the
race window.

Determinism: the bot reads only the bars it is sent (closes, the
account's cash and positions). No clock, no environment, no random
numbers, no files. The same batches always give the same orders.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

# --- strategy parameters (all in bars / basis points) -----------------
ANCHOR_BARS = 90            # window for the slow moving average
ENTRY_BPS = 80              # close this far below the anchor to buy
EXIT_BPS = 75               # exit: close this far above the entry-time anchor
TRAIL_BPS = 150             # exit: close retraces this far from the post-entry peak
STOP_BPS = 400              # exit: close this far below the entry cost
MAX_HOLD_BARS = 300         # exit: bars held, at most
COOLDOWN_BARS = 5           # bars after an exit before a new entry
MAX_SLIPPAGE_BPS = 200      # cap on every order, measured from the decision close

FALLBACK_BAR = 1080         # from this batch on, relax the trigger if never traded
FALLBACK_ENTRY_BPS = 60     # relaxed trigger, only while never having held

MIN_NOTIONAL = 1_000_000    # 1 USDC, in quote units


class Rider:
    """Per-run state. All of it is rebuilt from the batches alone."""

    def __init__(self):
        self.closes = []          # int closes, latest last
        self.anchor = 0           # SMA of the last ANCHOR_BARS closes, frozen at entry
        self.entry_price = None  # int price units of the current position's cost basis
        self.entry_bar = None    # batch seq at which the buy was decided
        self.peak = None         # highest int close seen since entry
        self.last_exit_bar = -10 ** 9
        self.ever_held = False

    def entry_cost_price(self, state):
        """Average cost per unit of the held position, in price units.

        value(qty, price) = qty * price // 10**7, so the price that
        values the position at its cost is cost * 10**7 // qty.
        """
        held = state.positions.get(SYMBOL)
        if held is None or held.qty <= 0:
            return None
        return held.cost * 10 ** 7 // held.qty

    def on_batch(self, state, batch):
        if state.warming_up:
            return []
        bar = None
        for b in batch["bars"]:
            if b["symbol"] == SYMBOL:
                bar = b
                break
        if bar is None:
            return []  # no bar for CBBTC this minute: nothing to decide on

        close = int(bar["c"])
        self.closes.append(close)
        seq = state.seq

        held = state.positions.get(SYMBOL)
        if held is not None and held.qty > 0:
            self.ever_held = True
            if self.entry_price is None:
                # The position appeared with this batch's fills. The
                # entry bar and the peak stay as set when the buy was
                # decided, so the hold cap and the trail are measured
                # from the decision bar, exactly as tuned.
                self.entry_price = self.entry_cost_price(state)
            if self.peak is None:
                self.peak = close
            if close > self.peak:
                self.peak = close
            # --- exit checks -------------------------------------------
            entry = self.entry_price
            if entry is not None:
                target = self.anchor * (10_000 + EXIT_BPS)
                reached = close * 10_000 >= target
                retrace = close * 10_000 <= self.peak * (10_000 - TRAIL_BPS)
                stopped = close * 10_000 <= entry * (10_000 - STOP_BPS)
                timed_out = (self.entry_bar is not None
                             and seq - self.entry_bar >= MAX_HOLD_BARS)
                if reached or retrace or stopped or timed_out:
                    self.last_exit_bar = seq
                    self.entry_price = None
                    self.entry_bar = None
                    self.peak = None
                    return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                             "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        # Flat: reset any stale entry bookkeeping and look for an entry.
        self.entry_price = None
        self.entry_bar = None
        self.peak = None
        if seq - self.last_exit_bar < COOLDOWN_BARS:
            return []
        if len(self.closes) < ANCHOR_BARS + 1:
            return []

        # The anchor is the SMA of the last ANCHOR_BARS closes. It is
        # recomputed on every flat bar and frozen once a position opens,
        # so the exit target does not drift with the market.
        self.anchor = sum(self.closes[-ANCHOR_BARS:]) // ANCHOR_BARS

        trigger = ENTRY_BPS
        if seq >= FALLBACK_BAR and not self.ever_held:
            trigger = FALLBACK_ENTRY_BPS
        if close * 10_000 >= self.anchor * (10_000 - trigger):
            return []  # not deep enough below the anchor yet

        if state.cash < MIN_NOTIONAL:
            return []
        self.entry_bar = seq
        self.peak = close
        self.ever_held = True
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": state.cash,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]


def main():
    rider = Rider()
    makefun_bars.run(rider.on_batch)


if __name__ == "__main__":
    main()
