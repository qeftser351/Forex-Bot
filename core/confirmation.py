# core/confirmation.py
from core.types import Candle

class Confirmation:
    """
    Container für Entry-Bestätigung (statt dict).
    Zugriff nur über Attribute (.valid, .candle).
    """
    def __init__(self, valid: bool = False, candle: Candle = None):
        self.valid = valid
        self.candle = candle

    def reset(self):
        self.valid = False
        self.candle = None

    def copy(self):
        from copy import deepcopy
        return Confirmation(self.valid, deepcopy(self.candle))

    def __repr__(self):
        return f"Confirmation(valid={self.valid}, candle={self.candle})"
