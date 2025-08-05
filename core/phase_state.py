# core/phase_state.py
from core.confirmation import Confirmation

class PhaseState(dict):
    """
    Zentrales State/Context-Objekt für die Phasen-Statemachine.
    KEIN dict mehr für Bestätigungen/Schlüssel, sondern alles als Property!
    """

    def __init__(self):
        # super().__init__() kann bleiben, ist aber für dict-Zugriff irrelevant
        super().__init__()
        self.current_phase = None
        self._last_candle_ts = None
        self.prev_phase = None

        self.last_confirmation_bullish = Confirmation()
        self.last_confirmation_bearish = Confirmation()

        self.switch_bull_initial_low = None
        self.switch_bull_prev_higher_high = None
        self.switch_bear_initial_high = None
        self.switch_bear_prev_lower_low = None

        self.switch_bull_pivot_idx = None
        self.switch_bull_breakout_idx = None
        self.pivot_idx_bear = None
        self.breakdown_idx = None
        
    @property
    def last_candle_ts(self):
        return self._last_candle_ts

    @last_candle_ts.setter
    def last_candle_ts(self, value):
        self._last_candle_ts = value

    def reset(self):
        self.current_phase = None
        self.last_candles = []
        self.prev_phase = None
        self.last_confirmation_bullish.reset()
        self.last_confirmation_bearish.reset()
        self.switch_bull_initial_low = None
        self.switch_bull_prev_higher_high = None
        self.switch_bear_initial_high = None
        self.switch_bear_prev_lower_low = None
        self.switch_bull_pivot_idx = None
        self.switch_bull_breakout_idx = None
        self.pivot_idx_bear = None
        self.breakdown_idx = None

    def copy(self):
        from copy import deepcopy
        ps = PhaseState()
        ps.current_phase = self.current_phase
        ps.last_candles = deepcopy(self.last_candles)
        ps.prev_phase = self.prev_phase
        ps.last_confirmation_bullish = self.last_confirmation_bullish.copy()
        ps.last_confirmation_bearish = self.last_confirmation_bearish.copy()
        ps.switch_bull_initial_low = self.switch_bull_initial_low
        ps.switch_bull_prev_higher_high = self.switch_bull_prev_higher_high
        ps.switch_bear_initial_high = self.switch_bear_initial_high
        ps.switch_bear_prev_lower_low = self.switch_bear_prev_lower_low
        ps.switch_bull_pivot_idx = self.switch_bull_pivot_idx
        ps.switch_bull_breakout_idx = self.switch_bull_breakout_idx
        ps.pivot_idx_bear = self.pivot_idx_bear
        ps.breakdown_idx = self.breakdown_idx
        return ps

    def invalidate_bullish_confirmations(self):
        self.last_confirmation_bullish.reset()

    def invalidate_bearish_confirmations(self):
        self.last_confirmation_bearish.reset()
