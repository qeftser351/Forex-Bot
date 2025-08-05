# main.py
"""
Bootstrapping: Initialisiert MetaTrader5, DataHandler, Strategie und startet das Event-Loop.
"""
import os
import threading
import signal
import sys
from dotenv import load_dotenv
import MetaTrader5 as mt5
from data_handler import DataHandler
from strategy import TradingStrategy
from config.timeframes import K, B, E  # MT5-Integer-Konstanten
from typing import Dict

def start_periodic_summary(strategies: Dict[str, TradingStrategy], interval: int = 30):
    """
    Ruft strat.controller.print_summary() für jede Strategie alle `interval` Sekunden auf.
    """
    def _report():
        for strat in strategies.values():
            strat.controller.print_summary()
        t = threading.Timer(interval, _report)
        t.daemon = True
        t.start()

    t0 = threading.Timer(interval, _report)
    t0.daemon = True
    t0.start()

def shutdown(signum, frame):
    """Signal-Handler für Strg+C oder SIGTERM."""
    print("\nStop signal received, shutting down …")
    try:
        handler.stop()
    except Exception:
        pass
    try:
        mt5.shutdown()
    except Exception:
        pass
    sys.exit(0)

if __name__ == '__main__':
    # SIGINT (Strg+C) und SIGTERM abfangen
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Umgebungsvariablen laden
    load_dotenv()

    # MT5-Zugangsdaten einlesen
    try:
        MT5_LOGIN    = int(os.environ['MT5_LOGIN'])
        MT5_PASSWORD = os.environ['MT5_PASSWORD']
        MT5_SERVER   = os.environ['MT5_SERVER']
    except KeyError as e:
        raise RuntimeError(f"Umgebungsvariable {e.args[0]} fehlt")

    # MT5 initialisieren
    if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
        raise RuntimeError(f"MT5 Init-Fehler: {mt5.last_error()}")
    print("MT5: verbunden")

    # DataHandler erstellen
    handler = DataHandler(mt5)

    # Symbol und Kontostand
    SYMBOLS = ['GBPUSD.r', 'EURUSD.r', 'AUDUSD.r' , 'USDCAD.r', 'GBPJPY.r', 'USDJPY.r', 'EURJPY.r']
    account = mt5.account_info()
    if account is None:
        raise RuntimeError("MT5: Konto-Info nicht abrufbar")
    INITIAL_BALANCE = account.balance

    # 1) Parameter pro Symbol einlesen
    symbol_params = {}
    for sym in SYMBOLS:
        if not mt5.symbol_select(sym, True):
            raise RuntimeError(f"Symbol {sym} nicht verfügbar")
        info = mt5.symbol_info(sym)
        symbol_params[sym] = {
            'tick_size': info.point,
            'spread':    info.spread * info.point
        }

    # 2) Für jedes Symbol eine eigene Strategie anlegen, initialisieren und abonnieren
    strategies: Dict[str, TradingStrategy] = {}
    for sym in SYMBOLS:
        params = symbol_params[sym]
        strat = TradingStrategy(
            symbol=sym,
            data_handler=handler,
            account_balance=INITIAL_BALANCE,
            tick_size=params['tick_size'],
            spread=params['spread']
        )

        strat.controller.initialize()
        strat.start()
        strategies[sym] = strat


    # 3) Periodische Zusammenfassung aller Strategien
    start_periodic_summary(strategies, interval=15)

    # 4) Event-Loop starten (blockierend)
    handler.run()
