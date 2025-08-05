import MetaTrader5 as mt5
from config.timeframes import K, B, E
from core.tf_manager import MultiTimeframeController
from core.phase_manager import Candle


def max_lots(symbol: str, risk_per_trade: float = 0.01) -> float:
    """
    Berechnet, wie viele Lots basierend auf risk_per_trade-Prozent der freien Margin
    für das gegebene Symbol maximal platziert werden können.
    """
    account = mt5.account_info()
    if account is None:
        raise RuntimeError("MT5: Konto-Info nicht abrufbar")
    free = account.margin_free
    # Berechne Margin-Bedarf für 1 Lot
    tick = mt5.symbol_info_tick(symbol)
    price = tick.ask if tick.ask else tick.bid
    margin_per_lot = mt5.order_calc_margin(
        mt5.ORDER_TYPE_BUY_STOP,
        symbol,
        1.0,
        price
    )
    # erlaubte Lots durch free margin * risk_per_trade
    allowed = (free * risk_per_trade) / margin_per_lot
    # runden auf 2 Dezimalstellen
    return round(allowed, 2)


class TradingStrategy:
    """
    Orchestriert den MultiTimeframeController und verbindet ihn mit dem DataHandler.
    """
    def __init__(
        self,
        symbol: str,
        data_handler,
        account_balance: float,
        tick_size: float,
        spread: float
    ):
        """
        Initialisiert die Trading-Strategie mit Symbol, DataHandler und Parametern.
        """
        self.symbol = symbol
        self.controller = MultiTimeframeController(
            symbol=symbol,
            data_handler=data_handler,
            account_balance=account_balance,
            tick_size=tick_size,
            spread=spread
        )

    def start(self) -> None:
        """
        Starte die Strategie: initialisiere Controller und abonniere Candle-Updates auf allen Timeframes.
        """
        # Erstinitialisierung
        self.controller.initialize()

        # Abonniere neue Kerzen (Candles) für alle Timeframes
        for tf in (K, B, E):
            self.controller.data.subscribe(
                symbol=self.symbol,
                timeframe=tf,
                callback=lambda raw_candle, tf=tf: self._on_candle(tf, raw_candle)
            )

    def _on_candle(self, timeframe: int, raw_candle) -> None:
        print(f"[CHECK] Type: {type(raw_candle)}, Content: {raw_candle}")

        """
        Callback für jede neue abgeschlossene Kerze.
        Erwartet raw_candle als Candle-Objekt, nicht als Tupel.
        """
        # raw_candle ist bereits ein Candle-Objekt, also keine Entpackung
        c = raw_candle

        # Optional: Falls du trotzdem sicher sein willst, kannst du auch Attribute extrahieren:
        # timestamp = c.timestamp
        # open_ = c.open
        # high = c.high
        # low = c.low
        # close = c.close
        # volume = c.volume

        # Weiterleiten an Controller
        self.controller.on_new_candle(timeframe, c)
