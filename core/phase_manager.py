# core/phase_manager.py
"""
State Machine für Phasen-Übergänge. Nutzt das zentrale PhaseState-Objekt.
"""
from typing import List
from config.phase import PHASE_RULES
from core.types import PhaseRule, Phase, Candle
from core.phase_state import PhaseState
from core.phase_state import Confirmation

class PhaseStateMachine:
    def __init__(self):
        self.rules: List[PhaseRule] = PHASE_RULES
        self.state: PhaseState = PhaseState()
        self.state.current_phase = Phase.NEUTRAL

    def replay_from_scratch(self, candles: List[Candle]) -> Phase:
        self.state.reset()
        self.state.current_phase = Phase.NEUTRAL
        for candle in candles:
            self.update_with_candle(candle)
        return self.state.current_phase






    def reset(self):
        self.state.reset()

    def update(self, candles: List[Candle]) -> Phase:
        print(f"[DEBUG] FSM state ID: {id(self.state)}")
        prev_phase = self.state.current_phase
        self.state.last_candles = candles

        for rule in self.rules:
            if rule.from_phase == prev_phase:
                condition_result = rule.condition(prev_phase, candles, self.state)
                print(f"[DEBUG] Prüfe Regel: {rule.from_phase.name} -> {rule.to_phase.name}, Bedingung: {condition_result}")
                if condition_result:
                    # Phase wechseln
                    new_phase = rule.to_phase
                    if new_phase == prev_phase:
                        # Kein Wechsel, Kontext bleibt erhalten
                        return prev_phase

                    print(f"[FSM] Phase Wechsel von {prev_phase.name} zu {new_phase.name}")
                    print(f"[DEBUG] Kontext vor Wechsel: {self.state}")

                    # Kontext-Löschungen je nach Phasenwechsel
                    
                    # Switch-Bull Kontext & Confirmation löschen, wenn Phase wechselt
                    if prev_phase == Phase.SWITCH_BULL and new_phase != Phase.SWITCH_BULL and new_phase != Phase.BASE_SWITCH_BULL:
                        self.state.switch_bull_initial_low = None
                        self.state.switch_bull_prev_higher_high = None
                        self.state.switch_bull_pivot_idx = None
                        self.state.switch_bull_breakout_idx = None
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None

                        
                    # Wenn Phase neu SWITCH_BULL wird, Base-Switch-Bull-Kontext zurücksetzen
                    if new_phase == Phase.SWITCH_BULL:
                        self.state.switch_bull_pivot_idx = None
                        self.state.switch_bull_breakout_idx = None
                        
                    # Wenn Phase neu SWITCH_BEAR wird, Base-Switch-Bear-Kontext zurücksetzen
                    if new_phase == Phase.SWITCH_BEAR:
                        self.state.pivot_idx_bear = None
                        self.state.breakdown_idx = None


                    # Switch-Bear Kontext & Confirmation löschen, wenn Phase wechselt
                    if prev_phase == Phase.SWITCH_BEAR and new_phase != Phase.SWITCH_BEAR and new_phase != Phase.BASE_SWITCH_BEAR:
                        self.state.switch_bear_initial_high = None
                        self.state.switch_bear_prev_lower_low = None
                        self.state.pivot_idx_bear = None
                        self.state.breakdown_idx = None
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None

                    
                    # Neu: Gegenseitigen Switch-Kontext beim Wechsel zu SWITCH_BULL oder SWITCH_BEAR löschen
                    if new_phase == Phase.SWITCH_BULL:
                        # SWITCH_BEAR Kontext löschen
                        self.state.switch_bear_initial_high = None
                        self.state.switch_bear_prev_lower_low = None
                        self.state.pivot_idx_bear = None
                        self.state.breakdown_idx = None
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None
                        # Base-Switch-Bull Kontext zurücksetzen (optional)
                        self.state.switch_bull_pivot_idx = None
                        self.state.switch_bull_breakout_idx = None

                    if new_phase == Phase.SWITCH_BEAR:
                        # SWITCH_BULL Kontext löschen
                        self.state.switch_bull_initial_low = None
                        self.state.switch_bull_prev_higher_high = None
                        self.state.switch_bull_pivot_idx = None
                        self.state.switch_bull_breakout_idx = None
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None
                        # Base-Switch-Bear Kontext zurücksetzen (optional)
                        self.state.pivot_idx_bear = None
                        self.state.breakdown_idx = None


                    # Confirmation Bearish löschen, wenn von bearischer Phase in nicht-bearische Phase gewechselt wird
                    bear_phases = {Phase.SWITCH_BEAR, Phase.BASE_SWITCH_BEAR, Phase.BASE_BEAR, Phase.TREND_BEAR}
                    if prev_phase in bear_phases and new_phase not in bear_phases:
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None

                    # Confirmation Bullish löschen, wenn von bullischer Phase in nicht-bullische Phase gewechselt wird
                    bull_phases = {Phase.SWITCH_BULL, Phase.BASE_SWITCH_BULL, Phase.BASE_BULL, Phase.TREND_BULL}
                    if prev_phase in bull_phases and new_phase not in bull_phases:
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None

                    # Wechsel von BASE_SWITCH_BULL / BASE_BULL zu TREND_BULL: Confirmation Bull löschen
                    if prev_phase in (Phase.BASE_SWITCH_BULL, Phase.BASE_BULL) and new_phase == Phase.TREND_BULL:
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None

                    # Wechsel von BASE_SWITCH_BEAR / BASE_BEAR zu TREND_BEAR: Confirmation Bear löschen
                    if prev_phase in (Phase.BASE_SWITCH_BEAR, Phase.BASE_BEAR) and new_phase == Phase.TREND_BEAR:
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None

                    # Rücksprung TREND_BULL -> BASE_BULL: Confirmation Bull aktivieren
                    if prev_phase == Phase.TREND_BULL and new_phase == Phase.BASE_BULL:
                        self.state.last_confirmation_bullish.valid = True
                        self.state.last_confirmation_bullish.candle = candles[-1]

                    # Rücksprung TREND_BEAR -> BASE_BEAR: Confirmation Bear aktivieren
                    if prev_phase == Phase.TREND_BEAR and new_phase == Phase.BASE_BEAR:
                        self.state.last_confirmation_bearish.valid = True
                        self.state.last_confirmation_bearish.candle = candles[-1]
                        
                    # --- Confirmation darf NUR im relevanten Kontext gesetzt sein! ---
                    # Bullish Confirmation nur in bullischen Phasen
                    if self.state.current_phase not in {Phase.SWITCH_BULL, Phase.BASE_SWITCH_BULL, Phase.BASE_BULL, Phase.TREND_BULL}:
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None

                    # Bearish Confirmation nur in bearischen Phasen
                    if self.state.current_phase not in {Phase.SWITCH_BEAR, Phase.BASE_SWITCH_BEAR, Phase.BASE_BEAR, Phase.TREND_BEAR}:
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None


                    # Phase im State setzen
                    self.state.current_phase = new_phase

                    print(f"[DEBUG] Kontext nach Wechsel: {self.state}")
                    return new_phase

        print(f"[DEBUG] Keine Regel zum Phasenwechsel gefunden, bleibe bei {prev_phase.name}")
        return prev_phase
    
    
    def update_with_candle(self, candle: Candle) -> Phase:
        print(f"[DEBUG] FSM state ID: {id(self.state)}")
        prev_phase = self.state.current_phase

        # Anhängen der neuen Kerze an den Puffer
        if self.state.last_candles is None:
            self.state.last_candles = []
        self.state.last_candles.append(candle)

        # Optional: Limitierung der Kerzenanzahl im Puffer (z.B. 100)
        MAX_CANDLES = 100
        if len(self.state.last_candles) > MAX_CANDLES:
            self.state.last_candles.pop(0)

        # Nun Regeln prüfen, identisch zu update()
        for rule in self.rules:
            if rule.from_phase == prev_phase:
                condition_result = rule.condition(prev_phase, self.state.last_candles, self.state)
                print(f"[DEBUG] Prüfe Regel: {rule.from_phase.name} -> {rule.to_phase.name}, Bedingung: {condition_result}")
                if condition_result:
                    new_phase = rule.to_phase
                    if new_phase == prev_phase:
                        return prev_phase

                    print(f"[FSM] Phase Wechsel von {prev_phase.name} zu {new_phase.name}")
                    print(f"[DEBUG] Kontext vor Wechsel: {self.state}")

                    # Kontext-Löschungen je nach Phasenwechsel
                    if prev_phase == Phase.SWITCH_BULL and new_phase not in (Phase.SWITCH_BULL, Phase.BASE_SWITCH_BULL):
                        self.state.switch_bull_initial_low = None
                        self.state.switch_bull_prev_higher_high = None
                        self.state.switch_bull_pivot_idx = None
                        self.state.switch_bull_breakout_idx = None
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None

                    if new_phase == Phase.SWITCH_BULL:
                        self.state.switch_bull_pivot_idx = None
                        self.state.switch_bull_breakout_idx = None

                    if new_phase == Phase.SWITCH_BEAR:
                        self.state.pivot_idx_bear = None
                        self.state.breakdown_idx = None

                    if prev_phase == Phase.SWITCH_BEAR and new_phase not in (Phase.SWITCH_BEAR, Phase.BASE_SWITCH_BEAR):
                        self.state.switch_bear_initial_high = None
                        self.state.switch_bear_prev_lower_low = None
                        self.state.pivot_idx_bear = None
                        self.state.breakdown_idx = None
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None

                    if new_phase == Phase.SWITCH_BULL:
                        self.state.switch_bear_initial_high = None
                        self.state.switch_bear_prev_lower_low = None
                        self.state.pivot_idx_bear = None
                        self.state.breakdown_idx = None
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None
                        self.state.switch_bull_pivot_idx = None
                        self.state.switch_bull_breakout_idx = None

                    if new_phase == Phase.SWITCH_BEAR:
                        self.state.switch_bull_initial_low = None
                        self.state.switch_bull_prev_higher_high = None
                        self.state.switch_bull_pivot_idx = None
                        self.state.switch_bull_breakout_idx = None
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None
                        self.state.pivot_idx_bear = None
                        self.state.breakdown_idx = None

                    bear_phases = {Phase.SWITCH_BEAR, Phase.BASE_SWITCH_BEAR, Phase.BASE_BEAR, Phase.TREND_BEAR}
                    if prev_phase in bear_phases and new_phase not in bear_phases:
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None

                    bull_phases = {Phase.SWITCH_BULL, Phase.BASE_SWITCH_BULL, Phase.BASE_BULL, Phase.TREND_BULL}
                    if prev_phase in bull_phases and new_phase not in bull_phases:
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None

                    if prev_phase in (Phase.BASE_SWITCH_BULL, Phase.BASE_BULL) and new_phase == Phase.TREND_BULL:
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None

                    if prev_phase in (Phase.BASE_SWITCH_BEAR, Phase.BASE_BEAR) and new_phase == Phase.TREND_BEAR:
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None

                    if prev_phase == Phase.TREND_BULL and new_phase == Phase.BASE_BULL:
                        self.state.last_confirmation_bullish.valid = True
                        self.state.last_confirmation_bullish.candle = candle

                    if prev_phase == Phase.TREND_BEAR and new_phase == Phase.BASE_BEAR:
                        self.state.last_confirmation_bearish.valid = True
                        self.state.last_confirmation_bearish.candle = candle

                    if new_phase not in {Phase.SWITCH_BULL, Phase.BASE_SWITCH_BULL, Phase.BASE_BULL, Phase.TREND_BULL}:
                        self.state.last_confirmation_bullish.valid = False
                        self.state.last_confirmation_bullish.candle = None

                    if new_phase not in {Phase.SWITCH_BEAR, Phase.BASE_SWITCH_BEAR, Phase.BASE_BEAR, Phase.TREND_BEAR}:
                        self.state.last_confirmation_bearish.valid = False
                        self.state.last_confirmation_bearish.candle = None

                    self.state.current_phase = new_phase
                    print(f"[DEBUG] Kontext nach Wechsel: {self.state}")
                    return new_phase

        print(f"[DEBUG] Keine Regel zum Phasenwechsel gefunden, bleibe bei {prev_phase.name}")
        return prev_phase












