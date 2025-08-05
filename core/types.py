# core/types.py

from enum import Enum
from typing import Callable, List, Any, Dict, Optional
from datetime import datetime
from dataclasses import dataclass, field

# --------------------------------------------
# Candle‑Definition
# --------------------------------------------
@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    ema10: Optional[float] = None
    ema20: Optional[float] = None



# --------------------------------------------
# Phase‑Enum
# --------------------------------------------
class Phase(Enum):
    NEUTRAL           = "Neutral"
    BASE_SWITCH_BEAR  = "Base_Switch_Bear"
    SWITCH_BEAR       = "Switch_Bear"
    TREND_BEAR        = "Trend_Bear"
    BASE_BEAR         = "Base_Bear"
    SWITCH_BULL       = "Switch_Bull"
    BASE_SWITCH_BULL  = "Base_Switch_Bull"
    TREND_BULL        = "Trend_Bull"
    BASE_BULL         = "Base_Bull"

# --------------------------------------------
# ContextStore für Sonderlogik
# --------------------------------------------
class ContextStore(dict):
    """Speichert Sonderlogik-Daten wie Initial-Lows/Highs und Extremes."""
    pass

# --------------------------------------------
# PhaseRule für Transitionen
# --------------------------------------------
PhaseCondition = Callable[[Phase, List[Candle], ContextStore], bool]

class PhaseRule:
    def __init__(self, from_phase: Phase, to_phase: Phase, condition: PhaseCondition):
        self.from_phase = from_phase
        self.to_phase = to_phase
        self.condition = condition
