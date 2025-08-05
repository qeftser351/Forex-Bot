from typing import List, Optional, Dict
from core.types import Candle, Phase
from core.phase_manager import PhaseStateMachine
from config.phase import (
    is_confirmation_bullish,
    is_confirmation_bearish
)
from datetime import datetime, time
import pytz
import math
from config.phase import EMA_FAST_PERIOD, EMA_SLOW_PERIOD
import pandas as pd
from config.phase import ensure_list_of_candles

# Fallback für Mindestabstand, falls Broker keine trade_stops_level liefert
default_stop_level_points = 10


class ConfigEntryLogic:
    def __init__(self, phase_machine: PhaseStateMachine, spread: float):
        self.pm = phase_machine
        self.spread = spread
        self.default_stop_level_points = default_stop_level_points

    def _min_dist(self, stop_level: float, tick_size: float) -> float:
        """
        Berechnet den minimalen Preisabstand (in Preis, nicht in Pips!) für Stop-Orders.
        Gibt mindestens die Tickgröße zurück, selbst wenn stop_level=0.
        """
        level = stop_level if stop_level and stop_level > 0 else self.default_stop_level_points
        return max(level * tick_size, tick_size)

    #Einstigeszeiten definieren
    def _is_within_allowed_time(self) -> bool:
        berlin = pytz.timezone('Europe/Berlin')
        now = datetime.now(berlin).timetz()
        allowed = [
            (time(8, 0), time(12, 00)),
            (time(13, 0), time(15, 0)),
            (time(15, 00), time(20, 0)),
        ]
        return any(start <= now <= end for start, end in allowed)

    def check_buy_stop(self, candles: List[Candle], current_ask: float, stop_level: float, tick_size: float) -> Optional[Dict[str, float]]:
        if not self._is_within_allowed_time():
            print("[INFO] Buy-Stop übersprungen – außerhalb des Zeitfensters.")
            return None

        phase = self.pm.current_phase
        ctx = self.pm.state

        if phase != Phase.BASE_SWITCH_BULL:
            return None


        # Immer zu Candle-Objekten konvertieren
        candles = ensure_list_of_candles(candles)

        # Bestimme die gültige Confirmation
        confirm_candle_ctx = ctx.last_confirmation_bullish
        confirm_candle = confirm_candle_ctx.candle if confirm_candle_ctx and confirm_candle_ctx.valid else None


        if not confirm_candle:
            return None

        # Finde die Confirm-Candle im aktuellen Buffer (Vergleich nach timestamp!)
        conf_idx = next((i for i, c in enumerate(candles) if hasattr(confirm_candle, 'timestamp') and c.timestamp == confirm_candle.timestamp), None)
        if conf_idx is None:
            return None

        if not is_confirmation_bullish(phase, candles, ctx):
            return None

        # Verwende ausschließlich Switch-Kontext für Extrema (keine base_bull_prev_higher_high!)
        prev_higher = ctx.switch_bull_prev_higher_high
        initial_low = ctx.switch_bull_initial_low
        if prev_higher is None or initial_low is None:
            return None

        prev = candles[-2]
        min_dist = max(self._min_dist(stop_level, tick_size), tick_size)
        desired_entry = prev.high + self.spread

        if desired_entry - current_ask < min_dist:
            entry_price = current_ask + min_dist + tick_size
        else:
            entry_price = desired_entry

        curr = candles[-1]
        ema_fast = getattr(curr, "ema10", None)
        ema_slow = getattr(curr, "ema20", None)
        if ema_fast is None or ema_slow is None:
            return None

        curr_close = curr.close
        dist_fast = abs(curr_close - ema_fast)
        dist_slow = abs(curr_close - ema_slow)
        ema_ref = ema_fast if dist_fast > dist_slow else ema_slow

        stop_loss = ema_ref - 2 * self.spread
        min_broker_dist = min_dist

        if entry_price < current_ask + min_broker_dist:
            entry_price = current_ask + min_broker_dist + tick_size
        if abs(entry_price - stop_loss) < min_broker_dist:
            stop_loss = entry_price - min_broker_dist

        entry_price = round(entry_price / tick_size) * tick_size
        stop_loss = round(stop_loss / tick_size) * tick_size

        if stop_loss >= entry_price:
            print(f"[ERROR] SL >= Entry nach Adjustierung! SL={stop_loss}, Entry={entry_price}")
            return None

        print(
            f"[DEBUG] check_buy_stop: prev.high={prev.high}, spread={self.spread}, "
            f"stop_loss={stop_loss}, entry_price={entry_price}, ask={current_ask}, "
            f"min_broker_dist={min_broker_dist}"
        )

        return {
            "side": "buy",
            "entry_price": entry_price,
            "stop_loss": stop_loss
        }



    def check_sell_stop(
        self,
        candles: List[Candle],
        current_bid: float,
        stop_level: float,
        tick_size: float
    ) -> Optional[Dict[str, float]]:
        if not self._is_within_allowed_time():
            print("[INFO] Sell-Stop übersprungen – außerhalb des Zeitfensters.")
            return None

        phase = self.pm.current_phase
        ctx = self.pm.state

        if phase != Phase.BASE_SWITCH_BEAR:
            return None

        # Immer zu Candle-Objekten konvertieren
        candles = ensure_list_of_candles(candles)

        # Bestimme die gültige Confirmation
        confirm_candle_ctx = ctx.last_confirmation_bearish
        confirm_candle = confirm_candle_ctx.candle if confirm_candle_ctx and confirm_candle_ctx.valid else None


        if not confirm_candle:
            return None

        # Finde die Confirm-Candle im aktuellen Buffer (Vergleich nach timestamp!)
        conf_idx = next((i for i, c in enumerate(candles) if hasattr(confirm_candle, 'timestamp') and c.timestamp == confirm_candle.timestamp), None)
        if conf_idx is None:
            return None

        if not is_confirmation_bearish(phase, candles, ctx):
            return None

        # Verwende ausschließlich Switch-Kontext für Extrema (keine base_bear_prev_lower_low!)
        prev_lower = ctx.switch_bear_prev_lower_low
        initial_high = ctx.switch_bear_initial_high
        if prev_lower is None or initial_high is None:
            return None

        prev = candles[-2]
        min_dist = max(self._min_dist(stop_level, tick_size), tick_size)
        desired_entry = prev.low - self.spread

        if current_bid - desired_entry < min_dist:
            entry_price = current_bid - min_dist - tick_size
        else:
            entry_price = desired_entry

        curr = candles[-1]
        ema_fast = getattr(curr, "ema10", None)
        ema_slow = getattr(curr, "ema20", None)
        if ema_fast is None or ema_slow is None:
            return None

        curr_close = curr.close
        dist_fast = abs(curr_close - ema_fast)
        dist_slow = abs(curr_close - ema_slow)
        ema_ref = ema_fast if dist_fast > dist_slow else ema_slow

        # Stop-Loss 2× Spread über weitestem EMA
        stop_loss = ema_ref + 2 * self.spread

        min_broker_dist = min_dist

        if current_bid - entry_price < min_broker_dist:
            entry_price = current_bid - min_broker_dist - tick_size
        if abs(stop_loss - entry_price) < min_broker_dist:
            stop_loss = entry_price + min_broker_dist

        entry_price = round(entry_price / tick_size) * tick_size
        stop_loss = round(stop_loss / tick_size) * tick_size

        if stop_loss <= entry_price:
            print(f"[ERROR] SL <= Entry nach Adjustierung! SL={stop_loss}, Entry={entry_price}")
            return None

        print(
            f"[DEBUG] check_sell_stop: prev.low={prev.low}, spread={self.spread}, "
            f"stop_loss={stop_loss}, entry_price={entry_price}, bid={current_bid}, "
            f"min_broker_dist={min_broker_dist}"
        )

        return {
            "side": "sell",
            "entry_price": entry_price,
            "stop_loss": stop_loss
        }

