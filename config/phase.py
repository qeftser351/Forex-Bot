from core.types import Candle, Phase, PhaseRule, ContextStore
from typing import List, Any
from datetime import datetime
import pandas as pd
from typing import Optional
from core.phase_state import PhaseState


def ensure_list_of_candles(values: Any) -> List[Candle]:
    # Pandas/numpy: force list
    if isinstance(values, (pd.Series, pd.DataFrame)):
        values = values.to_dict(orient="records") if hasattr(values, "to_dict") else list(values)
    elif hasattr(values, "__array__"):  # numpy array
        values = list(values)
    # Dict als Mapping: sortiere nach Keys (alte Logik)
    if isinstance(values, dict):
        values = [values[k] for k in sorted(values.keys())]
    # Konvertiere ALLES in Candle, keine dicts durchlassen!
    if isinstance(values, list):
        candles = []
        for item in values:
            if isinstance(item, Candle):
                candles.append(item)
            elif isinstance(item, dict):
                candles.append(Candle(**item))
            else:
                raise TypeError(f"Kann Element vom Typ {type(item)} nicht in Candle konvertieren")
        return candles
    raise TypeError(f"Kann Werte nicht in Liste/Candle-Liste wandeln: {type(values)}")

def get_candle_before(context: PhaseState, candle: Candle):
    # Hilfsfunktion: finde Kerze vor der übergebenen Kerze in context['last_candles']
    candles = context.last_candles
    try:
        idx = candles.index(candle)
        if idx > 0:
            return candles[idx - 1]
    except ValueError:
        return None
    return None




# —————— Konstanten und Hilfsfunktionen ——————
EMA_FAST_PERIOD = 10
EMA_SLOW_PERIOD = 20


# Confirmation-Candle Logik
def is_confirmation_bullish(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    candles = ensure_list_of_candles(candles_input)
    if len(candles) < 2:
        print("[DEBUG] is_confirmation_bullish: Zu wenige Kerzen")
        return False

    if prev_phase not in (Phase.SWITCH_BULL, Phase.TREND_BULL):
        print(f"[DEBUG] is_confirmation_bullish: Falsche Phase {prev_phase}")
        return False

    conf = context.last_confirmation_bullish
    if conf and conf.valid:
        print("[DEBUG] is_confirmation_bullish: Schon bestätigt")
        return False

    prev = candles[-2]
    curr = candles[-1]
    print(f"[DEBUG] Prüfe Kerzen: prev_high={prev.high}, curr_high={curr.high}, curr_close={curr.close}")

    # Symmetrische Bedingung zur Bearish-Version:
    if curr.high > prev.high:
        print("[DEBUG] curr.high > prev.high -> kein bullish confirmation")
        return False

    ema_fast = getattr(curr, "ema10", None)
    ema_slow = getattr(curr, "ema20", None)
    if ema_fast is None or ema_slow is None:
        print("[DEBUG] EMA Werte fehlen")
        return False

    dist_fast = abs(curr.close - ema_fast)
    dist_slow = abs(curr.close - ema_slow)
    
    # Prüfe, ob Schlusskurs unter beiden EMAs liegt - dann kein bullish confirmation
    if curr.close < ema_fast and curr.close < ema_slow:
        print("[DEBUG] curr.close unter beiden EMAs -> kein bullish confirmation")
        return False

    print("[DEBUG] is_confirmation_bullish: Bestätigung erkannt")
    return True







def is_confirmation_bearish(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    candles = ensure_list_of_candles(candles_input)
    if len(candles) < 2:
        print("[DEBUG] is_confirmation_bearish: Zu wenige Kerzen")
        return False
    if prev_phase not in (Phase.SWITCH_BEAR, Phase.TREND_BEAR):
        print(f"[DEBUG] is_confirmation_bearish: Falsche Phase {prev_phase}")
        return False

    conf = context.last_confirmation_bearish
    if conf and conf.valid:
        print("[DEBUG] is_confirmation_bearish: Schon bestätigt")
        return False

    prev = candles[-2]
    curr = candles[-1]
    print(f"[DEBUG] Prüfe Kerzen: prev_low={prev.low}, curr_low={curr.low}, curr_close={curr.close}")

    if curr.low < prev.low:
        print("[DEBUG] curr.low < prev.low -> kein bearish confirmation")
        return False

    ema_fast = getattr(curr, "ema10", None)
    ema_slow = getattr(curr, "ema20", None)
    if ema_fast is None or ema_slow is None:
        print("[DEBUG] EMA Werte fehlen")
        return False

    dist_fast = abs(curr.close - ema_fast)
    dist_slow = abs(curr.close - ema_slow)

    # Prüfe, ob Schlusskurs über beiden EMAs liegt - dann kein bearish confirmation
    if curr.close > ema_fast and curr.close > ema_slow:
        print("[DEBUG] curr.close über beiden EMAs -> kein bearish confirmation")
        return False

    print("[DEBUG] is_confirmation_bearish: Bestätigung erkannt")
    return True







# 1. Switch_Bull
def is_switch_bull(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    print(f"[DEBUG] Kontext-Typ: {type(context)}")
    print(f"[DEBUG] Kontext-ID: {id(context)}")
    print(f"[DEBUG Bswitch bull] Prev={prev_phase}, Candles={len(candles_input)}")
    candles = ensure_list_of_candles(candles_input)

    allowed = {
        Phase.BASE_SWITCH_BEAR,
        Phase.SWITCH_BEAR,
        Phase.TREND_BEAR,
        Phase.BASE_BEAR
    }
    if prev_phase not in allowed or len(candles) < 2:
        return False

    # --- Kontext-Check: Wenn Werte schon gesetzt, niemals erneut setzen ---
    if (
        context.switch_bull_initial_low is not None and
        context.switch_bull_prev_higher_high is not None
    ):
        print("[DEBUG] Kontextwerte für switch_bull sind bereits gesetzt, kein Phasenwechsel")
        return False

    curr_idx = len(candles) - 1
    prev_idx = curr_idx - 1

    prev = candles[prev_idx]
    curr = candles[curr_idx]

    ema_fast = getattr(curr, "ema10", None)
    ema_slow = getattr(curr, "ema20", None)
    prev_ema_fast = getattr(prev, "ema10", None)
    prev_ema_slow = getattr(prev, "ema20", None)
    if None in (ema_fast, ema_slow, prev_ema_fast, prev_ema_slow):
        return False

    breakout = (
        (curr.close > ema_fast and curr.close > ema_slow) or
        (prev.close > ema_fast and curr.close > ema_slow) or
        (prev.close > ema_slow and curr.close > ema_fast)
    )
    if not breakout:
        return False

    # --- Initial Low suchen ---
    initial_low_idx = None
    for i in range(curr_idx, 0, -1):
        if candles[i].low < candles[i-1].low:
            initial_low_idx = i
            break

    if initial_low_idx is None:
        return False

    initial_low = candles[initial_low_idx].low

    prev_higher_high = None
    peak_high = candles[initial_low_idx].high
    for i in range(initial_low_idx - 1, -1, -1):
        if candles[i].high > peak_high:
            prev_higher_high = candles[i].high
            break

    if prev_higher_high is None:
        return False

    # --- Jetzt Kontext-Extrema einmalig setzen ---
    context.switch_bull_initial_low = initial_low
    context.switch_bull_prev_higher_high = prev_higher_high

    print(f"[DEBUG] switch_bull context gesetzt: initial_low={initial_low}, prev_higher_high={prev_higher_high}")

    return True






# 2. Switch_Bear
def is_switch_bear(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    print(f"[DEBUG] Kontext-Typ: {type(context)}")
    print(f"[DEBUG] Kontext-ID: {id(context)}")
    print(f"[DEBUG Bswitch bear] Prev={prev_phase}, Candles={len(candles_input)}")
    candles = ensure_list_of_candles(candles_input)

    allowed = {
        Phase.BASE_SWITCH_BULL,
        Phase.SWITCH_BULL,
        Phase.TREND_BULL,
        Phase.BASE_BULL
    }
    if prev_phase not in allowed or len(candles) < 2:
        return False

    # --- Kontext-Check: Wenn Werte schon gesetzt, niemals erneut setzen ---
    if (
        context.switch_bear_initial_high is not None and
        context.switch_bear_prev_lower_low is not None
    ):
        return False

    curr_idx = len(candles) - 1
    prev_idx = curr_idx - 1

    prev = candles[prev_idx]
    curr = candles[curr_idx]

    ema_fast = getattr(curr, "ema10", None)
    ema_slow = getattr(curr, "ema20", None)
    prev_ema_fast = getattr(prev, "ema10", None)
    prev_ema_slow = getattr(prev, "ema20", None)
    if None in (ema_fast, ema_slow, prev_ema_fast, prev_ema_slow):
        return False

    breakout = (
        (curr.close < ema_fast and curr.close < ema_slow) or
        (prev.close < ema_fast and curr.close < ema_slow) or
        (prev.close < ema_slow and curr.close < ema_fast)
    )
    if not breakout:
        return False

    # --- Initial High suchen ---
    initial_high_idx = None
    for i in range(curr_idx, 0, -1):
        if candles[i].high > candles[i-1].high:
            initial_high_idx = i
            break

    if initial_high_idx is None:
        return False

    initial_high = candles[initial_high_idx].high

    prev_lower_low = None
    peak_low = candles[initial_high_idx].low
    for i in range(initial_high_idx - 1, -1, -1):
        if candles[i].low < peak_low:
            prev_lower_low = candles[i].low
            break

    if prev_lower_low is None:
        return False

    # --- Jetzt Kontext-Extrema einmalig setzen ---
    context.switch_bear_initial_high = initial_high
    context.switch_bear_prev_lower_low = prev_lower_low

    print(f"[DEBUG] switch_bear context gesetzt: initial_high={initial_high}, prev_lower_low={prev_lower_low}")

    return True





# 3. Trend_Bull

def check_transition_to_trend_bull(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    candles = ensure_list_of_candles(candles_input)
    if prev_phase not in {Phase.BASE_BULL, Phase.BASE_SWITCH_BULL}:
        return False

    confirm = context.last_confirmation_bullish
    if not (confirm and confirm.candle):
        return False
    confirm_candle = confirm.candle


    prev_candle = get_candle_before(context, confirm_candle)
    if not prev_candle:
        return False

    curr_candle = candles[-1]

    if curr_candle.close > prev_candle.high:
        context.current_phase = Phase.TREND_BULL
        # Confirmation löschen
        context.last_confirmation_bullish.valid = False
        context.last_confirmation_bullish.candle = None
        return True
    return False






# 4. Trend_Bear
def check_transition_to_trend_bear(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    candles = ensure_list_of_candles(candles_input)
    if prev_phase not in {Phase.BASE_BEAR, Phase.BASE_SWITCH_BEAR}:
        return False

    confirm = context.last_confirmation_bearish
    if not (confirm and confirm.candle):
        return False
    confirm_candle = confirm.candle


    prev_candle = get_candle_before(context, confirm_candle)
    if not prev_candle:
        return False

    curr_candle = candles[-1]

    if curr_candle.close < prev_candle.low:
        context.current_phase = Phase.TREND_BEAR
        context.last_confirmation_bearish.valid = False
        context.last_confirmation_bearish.candle = None
        return True
    return False






# 5. Base_Bull
def check_transition_to_base_bull(prev_phase: Phase, candles: List[Candle], context: PhaseState) -> bool:
    if prev_phase != Phase.TREND_BULL:
        return False

    if not is_confirmation_bullish(prev_phase, candles, context):
        return False

    current_candle = candles[-1]
    context.last_confirmation_bullish.valid = True
    context.last_confirmation_bullish.candle = current_candle
    print(f"[DEBUG] Confirmation Bullish in TREND_BULL erkannt: {current_candle}")
    return True




# 6. Base_Bear
def check_transition_to_base_bear(prev_phase: Phase, candles: List[Candle], context: PhaseState) -> bool:
    if prev_phase != Phase.TREND_BEAR:
        return False

    current_candle = candles[-1]
    if not is_confirmation_bearish(prev_phase, candles, context):
        return False

    context.current_phase = Phase.BASE_BEAR
    context.last_confirmation_bearish.valid = True
    context.last_confirmation_bearish.candle = current_candle
    return True











# 7. Base_Switch_Bull
def is_base_switch_bull(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    candles = ensure_list_of_candles(candles_input)
    if prev_phase != Phase.SWITCH_BULL:
        return False

    initial_low = context.switch_bull_initial_low
    prev_higher = context.switch_bull_prev_higher_high
    if initial_low is None or prev_higher is None:
        return False

    # Merke Pivot-Index für diesen Switch im Context
    pivot_idx = context.switch_bull_pivot_idx
    if pivot_idx is None:
        pivot_idx = next((i for i, c in enumerate(candles) if c.low == initial_low), None)
        if pivot_idx is None:
            return False
        context.switch_bull_pivot_idx = pivot_idx

    # Merke Breakout-Index im Context (nur einmal suchen!)
    breakout_idx = context.switch_bull_breakout_idx
    if breakout_idx is None:
        for i in range(pivot_idx + 1, len(candles)):
            if candles[i].close > prev_higher:
                breakout_idx = i
                context.switch_bull_breakout_idx = breakout_idx
                break
        if breakout_idx is None:
            print("[DEBUG] Kein Breakout gefunden – Abbruch in is_base_switch_bull.")
            return False

    print(f"[DEBUG] BREAKOUT gefunden: breakout_idx={breakout_idx}, candle={candles[breakout_idx]}")

    start_confirmation = breakout_idx + 1
    if start_confirmation >= len(candles):
        return False

    print(f"[DEBUG] Prüfe Confirmation Bullish: breakout_idx={breakout_idx}, start={start_confirmation}, end={len(candles)}")

    # Confirmation Candle bullish suchen und bei Treffer valid setzen
    for j in range(start_confirmation, len(candles)):
        subcandles = candles[:j + 1]
        if is_confirmation_bullish(prev_phase, subcandles, context):
            context.last_confirmation_bullish.valid = True
            context.last_confirmation_bullish.candle = candles[j]
            print(f"[DEBUG] Confirmation Bullish gesetzt: idx={j}, candle={candles[j]}")
            return True

    return False






# 8. Base_Switch_Bear
def is_base_switch_bear(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    candles = ensure_list_of_candles(candles_input)
    if prev_phase != Phase.SWITCH_BEAR:
        return False

    initial_high = context.switch_bear_initial_high
    prev_lower = context.switch_bear_prev_lower_low
    if initial_high is None or prev_lower is None:
        return False

    # Pivots einmalig speichern
    pivot_idx = context.pivot_idx_bear
    if pivot_idx is None:
        pivot_idx = next((i for i, c in enumerate(candles) if c.high == initial_high), None)
        if pivot_idx is None:
            return False
        context.pivot_idx_bear = pivot_idx

    # Breakdown einmalig suchen und merken
    breakdown_idx = context.breakdown_idx
    if breakdown_idx is None:
        for i in range(pivot_idx + 1, len(candles)):
            if candles[i].close < prev_lower:
                breakdown_idx = i
                context.breakdown_idx = breakdown_idx
                break
        if breakdown_idx is None:
            print("[DEBUG] Kein Breakdown gefunden – Abbruch in is_base_switch_bear.")
            return False
        
    print(f"[DEBUG] BREAKDOWN gefunden: breakdown_idx={breakdown_idx}, candle={candles[breakdown_idx]}")

    start_confirmation = breakdown_idx + 1
    if start_confirmation >= len(candles):
        return False
    
    print(f"[DEBUG] Prüfe Confirmation Bearish: breakdown_idx={breakdown_idx}, start={start_confirmation}, end={len(candles)}")

    # Confirmation Candle bearish suchen und bei Treffer valid setzen
    for j in range(start_confirmation, len(candles)):
        subcandles = candles[:j + 1]
        if is_confirmation_bearish(prev_phase, subcandles, context):
            context.last_confirmation_bearish.valid = True
            context.last_confirmation_bearish.candle = candles[j]
            print(f"[DEBUG] Confirmation Bearish gesetzt: idx={j}, candle={candles[j]}")
            return True
    return False








# 9. Neutral zu switch_bull
def neutral_to_switch_bull(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    candles = ensure_list_of_candles(candles_input)

    if prev_phase != Phase.NEUTRAL or len(candles) < 3:
        return False

    curr_idx = len(candles) - 1
    prev_idx = curr_idx - 1

    prev = candles[prev_idx]
    curr = candles[curr_idx]

    ema_fast = getattr(curr, "ema10", None)
    ema_slow = getattr(curr, "ema20", None)
    prev_ema_fast = getattr(prev, "ema10", None)
    prev_ema_slow = getattr(prev, "ema20", None)
    if None in (ema_fast, ema_slow, prev_ema_fast, prev_ema_slow):
        return False

    breakout = False
    if curr.close > ema_fast and curr.close > ema_slow:
        breakout = True
    elif prev.close > ema_fast and curr.close > ema_slow:
        breakout = True
    elif prev.close > ema_slow and curr.close > ema_fast:
        breakout = True

    if not breakout:
        return False

    initial_low_idx = None
    for i in range(curr_idx - 1, 0, -1):
        if candles[i].low > candles[i - 1].low:
            initial_low_idx = i - 1
            break

    if initial_low_idx is None:
        return False

    initial_low = candles[initial_low_idx].low

    prev_higher_high = None
    for i in range(initial_low_idx - 1, -1, -1):
        if candles[i].high > initial_low:
            prev_higher_high = candles[i].high
            break

    if prev_higher_high is None:
        return False

    context.switch_bull_initial_low = initial_low
    context.switch_bull_prev_higher_high = prev_higher_high
    return True



# 10. Neutral zu switch_bear
def neutral_to_switch_bear(prev_phase: Phase, candles_input: Any, context: PhaseState) -> bool:
    candles = ensure_list_of_candles(candles_input)

    if prev_phase != Phase.NEUTRAL or len(candles) < 3:
        return False

    curr_idx = len(candles) - 1
    prev_idx = curr_idx - 1

    prev = candles[prev_idx]
    curr = candles[curr_idx]

    ema_fast = getattr(curr, "ema10", None)
    ema_slow = getattr(curr, "ema20", None)
    prev_ema_fast = getattr(prev, "ema10", None)
    prev_ema_slow = getattr(prev, "ema20", None)
    if None in (ema_fast, ema_slow, prev_ema_fast, prev_ema_slow):
        return False

    breakout = False
    if curr.close < ema_fast and curr.close < ema_slow:
        breakout = True
    elif prev.close < ema_fast and curr.close < ema_slow:
        breakout = True
    elif prev.close < ema_slow and curr.close < ema_fast:
        breakout = True

    if not breakout:
        return False

    initial_high_idx = None
    for i in range(curr_idx - 1, 0, -1):
        if candles[i].high < candles[i - 1].high:
            initial_high_idx = i - 1
            break

    if initial_high_idx is None:
        return False

    initial_high = candles[initial_high_idx].high

    prev_lower_low = None
    peak_low = candles[initial_high_idx].low
    for i in range(initial_high_idx - 1, -1, -1):
        if candles[i].low < peak_low:
            prev_lower_low = candles[i].low
            break

    if prev_lower_low is None:
        return False

    context.switch_bear_initial_high = initial_high
    context.switch_bear_prev_lower_low = prev_lower_low
    return True



# Liste aller Phasen-Regeln für den Wechsel von Phase nach Phase
PHASE_RULES: List[PhaseRule] = [
    PhaseRule(Phase.NEUTRAL, Phase.SWITCH_BULL, neutral_to_switch_bull),
    PhaseRule(Phase.NEUTRAL, Phase.SWITCH_BEAR, neutral_to_switch_bear),

    PhaseRule(Phase.BASE_SWITCH_BEAR, Phase.SWITCH_BULL, is_switch_bull),
    PhaseRule(Phase.SWITCH_BEAR, Phase.SWITCH_BULL, is_switch_bull),
    PhaseRule(Phase.TREND_BEAR, Phase.SWITCH_BULL, is_switch_bull),
    PhaseRule(Phase.BASE_BEAR, Phase.SWITCH_BULL, is_switch_bull),

    PhaseRule(Phase.BASE_SWITCH_BULL, Phase.SWITCH_BEAR, is_switch_bear),
    PhaseRule(Phase.SWITCH_BULL, Phase.SWITCH_BEAR, is_switch_bear),
    PhaseRule(Phase.TREND_BULL, Phase.SWITCH_BEAR, is_switch_bear),
    PhaseRule(Phase.BASE_BULL, Phase.SWITCH_BEAR, is_switch_bear),

    PhaseRule(Phase.BASE_SWITCH_BULL, Phase.TREND_BULL, check_transition_to_trend_bull),
    PhaseRule(Phase.BASE_BULL, Phase.TREND_BULL, check_transition_to_trend_bull),

    PhaseRule(Phase.BASE_SWITCH_BEAR, Phase.TREND_BEAR, check_transition_to_trend_bear),
    PhaseRule(Phase.BASE_BEAR, Phase.TREND_BEAR, check_transition_to_trend_bear),

    PhaseRule(Phase.TREND_BULL, Phase.BASE_BULL, check_transition_to_base_bull),
    PhaseRule(Phase.TREND_BEAR, Phase.BASE_BEAR, check_transition_to_base_bear),

    PhaseRule(Phase.SWITCH_BULL, Phase.BASE_SWITCH_BULL, is_base_switch_bull),
    PhaseRule(Phase.SWITCH_BEAR, Phase.BASE_SWITCH_BEAR, is_base_switch_bear)
]
