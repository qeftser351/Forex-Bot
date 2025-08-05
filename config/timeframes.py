import MetaTrader5 as mt5
from config.phase import Phase
from core.types import Phase

# Zeiteinheiten (MT5-Integer-Konstanten)
K = mt5.TIMEFRAME_H1  # Kontext-Zeiteinheit
B = mt5.TIMEFRAME_M15  # Bestätigungs-Zeiteinheit
E = mt5.TIMEFRAME_M1  # Einstiegs-Zeiteinheit

TIMEFRAMES = [K, B, E]

# Historische Bars zum Initialisieren in jeder TF
HISTORY_LIMIT: dict[int, int] = {
    K: 100,
    B: 100,
    E: 100,
}

# Als Base-Phase gelten alle Baseline-Zustände
BASE_PHASES: set[Phase] = {
    Phase.BASE_BULL,
    Phase.BASE_SWITCH_BULL,
    Phase.BASE_BEAR,
    Phase.BASE_SWITCH_BEAR,
}

def get_direction(phase: Phase) -> str:
    """Gibt die Grundrichtung einer Phase zurück: 'bull' oder 'bear'."""
    name = phase.name.lower() if phase is not None else ''
    if 'bull' in name:
        return 'bull'
    elif 'bear' in name:
        return 'bear'
    return ''

def can_k_to_b(phase: Phase) -> bool:
    """Wechsel von K nach B nur, wenn K in Base- oder Trendphase ist."""
    return phase in (
        Phase.BASE_SWITCH_BULL, Phase.BASE_SWITCH_BEAR,
        Phase.BASE_BULL, Phase.BASE_BEAR,
        Phase.TREND_BULL, Phase.TREND_BEAR
    )

def can_b_to_e(phase_k: Phase, phase_b: Phase) -> bool:
    """
    Wechsel von B nach E nur, wenn:
    - K in Base- oder Trendphase,
    - B in Base-Phase,
    - und beide dieselbe Richtung haben.
    """
    return (
        phase_k in (Phase.BASE_BULL, Phase.BASE_BEAR, Phase.BASE_SWITCH_BULL, Phase.BASE_SWITCH_BEAR, Phase.TREND_BULL, Phase.TREND_BEAR)
        and
        phase_b in (Phase.BASE_BULL, Phase.BASE_BEAR, Phase.BASE_SWITCH_BULL, Phase.BASE_SWITCH_BEAR)
        and
        get_direction(phase_b) == get_direction(phase_k)
    )

def should_switch_back_to_k(phase_k: Phase, entered_direction: str, current_b_dir: str) -> bool:
    """
    Rücksprung von B nach K wenn:
    - K-Richtung nicht mit entered_direction übereinstimmt
    - oder B-Richtung nicht mit K-Richtung übereinstimmt
    """
    if not entered_direction:
        return False
    return get_direction(phase_k) != entered_direction or current_b_dir != get_direction(phase_k)

def should_switch_back_to_b(phase_b: Phase, entered_direction: str, current_k_dir: str) -> bool:
    """
    Rücksprung von E nach B wenn:
    - B-Richtung nicht mit entered_direction übereinstimmt
    - oder K-Richtung nicht mit B-Richtung übereinstimmt
    - oder B keine Base-Phase mehr ist
    """
    if not entered_direction:
        return False
    if phase_b not in BASE_PHASES:
        return True
    return get_direction(phase_b) != entered_direction or current_k_dir != get_direction(phase_b)

def get_history_limit(tf: int) -> int:
    """Anzahl der Bars, die beim TF-Wechsel für Initialisierung geladen werden sollen."""
    return HISTORY_LIMIT.get(tf, 100)
