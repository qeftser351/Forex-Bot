from typing import List, Optional
from config.phase import Candle
import math
import MetaTrader5 as mt5

class RiskManager:
    """
    RiskManager: Position sizing, stop-loss, breakeven und Trailing-Stop.
    """
    def __init__(self, account_balance: float, max_risk_per_trade: float = 0.01):
        self.account_balance = account_balance
        self.max_risk = max_risk_per_trade
        self.trailing_levels = {}  # Neu: zur Nachverfolgung pro Ticket

    def calculate_position_size(self, symbol: str, entry_price: float, stop_price: float, side: str) -> float:
        """
        Berechnet die Positionsgröße basierend auf Risiko und Stop-Abstand, inkl. Normierung auf Broker-Volumen.
        """
        risk_amount = self.account_balance * self.max_risk
        stop_distance = abs(entry_price - stop_price)
        if stop_distance < 1e-6:
            raise ValueError("Stop-Loss-Abstand zu klein!")
        raw_lots = risk_amount / stop_distance
        return self.normalize_lots(symbol, raw_lots)

    def normalize_lots(self, symbol: str, desired_lots: float) -> float:
        info = mt5.symbol_info(symbol)
        if not info:
            raise RuntimeError(f"Symbol {symbol} nicht gefunden")
        min_vol, max_vol, step_vol = info.volume_min, info.volume_max, info.volume_step
        lots = max(min_vol, step_vol * round(desired_lots / step_vol))
        return min(lots, max_vol)

    # Staticmethod, kein self nötig
    @staticmethod
    def is_go_candle_bullish(candle: Candle, entry_price: float) -> bool:
        return candle.low <= entry_price and candle.close > entry_price

    @staticmethod
    def is_go_candle_bearish(candle: Candle, entry_price: float) -> bool:
        return candle.high >= entry_price and candle.close < entry_price

    def calculate_breakeven_price_buy(
        self,
        candles: List[Candle],
        entry_price: float,
        spread: float
    ) -> Optional[float]:
        go_candle = None
        go_index = -1
        for idx, c in enumerate(candles):
            if self.is_go_candle_bullish(c, entry_price):
                go_candle = c
                go_index = idx
                break
        if go_candle is None:
            return None
        for c in candles[go_index + 1:]:
            if c.low > entry_price and c.close > go_candle.high:
                return entry_price - spread
            if self.is_go_candle_bullish(c, entry_price):
                go_candle = c
        return None

    def calculate_breakeven_price_sell(
        self,
        candles: List[Candle],
        entry_price: float,
        spread: float
    ) -> Optional[float]:
        go_candle = None
        go_index = -1
        for idx, c in enumerate(candles):
            if self.is_go_candle_bearish(c, entry_price):
                go_candle = c
                go_index = idx
                break
        if go_candle is None:
            return None
        for c in candles[go_index + 1:]:
            if c.high < entry_price and c.close < go_candle.low:
                return entry_price + spread
            if self.is_go_candle_bearish(c, entry_price):
                go_candle = c
        return None

    def trailing_step_buy(self, entry_price: float, rr: float, level: int) -> float:
        return entry_price + (level - 2) * rr

    def trailing_step_sell(self, entry_price: float, rr: float, level: int) -> float:
        return entry_price - (level - 2) * rr

