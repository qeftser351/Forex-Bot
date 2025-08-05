import MetaTrader5 as mt5
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime
from core.phase_manager import PhaseStateMachine
from core.types import Candle, Phase
from core.entry_manager import EntryLogicManager
from core.risk_manager import RiskManager
import logging
import csv
import os
import math
import pytz
from config.phase import is_confirmation_bearish, is_confirmation_bullish
from config.timeframes import (
    can_k_to_b,
    can_b_to_e,
    should_switch_back_to_k,
    should_switch_back_to_b,
    BASE_PHASES,
    K, B, E,
    get_direction,
)


logger = logging.getLogger(__name__)

EMA_FAST_PERIOD = 10
EMA_SLOW_PERIOD = 20
EMA_SOURCE      = 'close'
SMOOTH_TYPE     = 'None'
SMOOTH_LENGTH   = 14
SMOOTH_STDDEV   = 2
TIMEFRAMES = [K, B, E]

def setup_csv(path='summary_log.csv'):
    exists = os.path.isfile(path)
    csvfile = open(path, mode='a', newline='')
    writer = csv.writer(csvfile, delimiter=';')
    if not exists:
        writer.writerow(['timestamp', 'symbol', '1h', '15m', '1m', 'entry', 'sl', 'rr'])
    return csvfile, writer

csvfile, summary_writer = setup_csv()

def _serialize_state(state):
    def serialize(val):
        if isinstance(val, dict):
            return {k: serialize(v) for k, v in val.items()}
        if isinstance(val, list):
            return [serialize(x) for x in val]
        if hasattr(val, "timestamp") and hasattr(val, "open"):
            return {
                "ts": val.timestamp,
                "o": val.open,
                "h": val.high,
                "l": val.low,
                "c": val.close,
            }
        return val
    return {k: serialize(getattr(state, k)) for k in vars(state)}

def log_summary_to_csv(symbol, phase_1h, phase_15m, phase_1m, entry=None, sl=None, rr=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary_writer.writerow([
        now, symbol, phase_1h, phase_15m, phase_1m,
        entry if entry else "", sl if sl else "", rr if rr else ""
    ])
    csvfile.flush()

class MultiTimeframeController:
    def __init__(
        self,
        symbol: str,
        data_handler,
        account_balance: float,
        tick_size: float,
        spread: float
    ):
        self.symbol = symbol
        self.data = data_handler
        self.spread = spread
        self.tick_size = tick_size
        self.entry_timestamps: Dict[int, float] = {}

        self.active_tf: Optional[int] = None
        self.phase_history = {}

        self.risk_mgr = RiskManager(account_balance)
        self.risk_mgr.symbol = symbol
        self.risk_mgr.spread = spread
        self.risk_mgr.tick_size = tick_size

        self.break_even_applied: Dict[int, bool] = {}
        self.machines = {tf: PhaseStateMachine() for tf in TIMEFRAMES}
        self.entry_mgr = EntryLogicManager(self.machines[E], spread)

        self.phases = {tf: None for tf in TIMEFRAMES}
        self.open_ticket: Optional[int] = None
        self.entry_price: Optional[float] = None
        self.initial_stop: Optional[float] = None
        self.current_sl: Optional[float] = None
        self.side: Optional[str] = None

        self.switch_data = {tf: {} for tf in TIMEFRAMES}
        self.entered_direction: Optional[str] = None
        self.last_update_ts = {tf: None for tf in TIMEFRAMES}
        self.processed_entry_candles = set()


    def initialize(self) -> None:
        print("[INIT] initialize() wurde gestartet")

        # 1. Alte Orders & Positionen löschen (unverändert)
        for o in self.data.mt5.orders_get(magic=234000) or []:
            print(f"[INIT] Lösche alte Pending-Order: {o.ticket}")
            self.data.cancel_order(o.ticket)
        for p in self.data.mt5.positions_get() or []:
            if p.magic == 234000:
                print(f"[INIT] Schließe alte Position: {p.ticket}")
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": p.ticket,
                    "symbol": p.symbol,
                    "volume": p.volume,
                    "type": mt5.ORDER_TYPE_BUY if p.type == mt5.ORDER_TYPE_SELL else mt5.ORDER_TYPE_SELL,
                    "price": mt5.symbol_info_tick(p.symbol).bid if p.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(p.symbol).ask,
                    "deviation": 20,
                    "magic": 234000,
                    "comment": "Bot-Startup Cleanup"
                }
                self.data.mt5.order_send(req)

        # 2. Interner State resetten (unverändert)
        self.open_ticket = None
        self.entry_price = None
        self.initial_stop = None
        self.current_sl = None
        self.side = None
        self.entered_direction = None
        self.processed_entry_candles.clear()

        # 3. FSMs initialisieren – Historie laden und FSM resetten (ohne Kontext-Manipulation)
        for tf in TIMEFRAMES:
            self.data.refresh_history(self.symbol, tf)
            hist = self.data.histories[self.symbol][tf]

            fsm = self.machines[tf]

            # Zustand mit Historie neu aufbauen
            phase = fsm.replay_from_scratch(hist)
    
            print(f"[INIT] TF={tf}, Phase nach finalem Update: {phase.name}")
            print(f"[INIT DEBUG] FSM Context für TF={tf}: {fsm.state}")

            # Letzte Phase merken
            fsm.state.prev_phase = phase
            fsm.state.last_candles = hist
            fsm.current_phase = phase
            self.phases[tf] = phase

            # Fix: last_candle_ts auf letzte Kerze setzen (oder None, falls keine Kerzen)
            if hist:
                fsm.state.last_candle_ts = hist[-1].timestamp
            else:
                fsm.state.last_candle_ts = None


        # 4. Switch-Daten übernehmen (unverändert)
        for tf in TIMEFRAMES:
            fsm = self.machines[tf]
            phase = self.phases[tf]
            if phase in (Phase.SWITCH_BULL, Phase.BASE_SWITCH_BULL):
                self.switch_data[tf] = {
                    'initial_extreme': fsm.state.switch_bull_initial_low,
                    'previous_extreme': fsm.state.switch_bull_prev_higher_high,
                }
            elif phase in (Phase.SWITCH_BEAR, Phase.BASE_SWITCH_BEAR):
                self.switch_data[tf] = {
                    'initial_extreme': fsm.state.switch_bear_initial_high,
                    'previous_extreme': fsm.state.switch_bear_prev_lower_low,
                }
            else:
                self.switch_data[tf] = {}

        # 5. Übernehme offene Orders/Positionen (unverändert)
        for order in self.data.mt5.orders_get(magic=234000) or []:
            self.open_ticket = order.ticket
            self.entry_price = order.price_open
            self.initial_stop = order.sl
            self.current_sl = order.sl
            self.side = 'buy' if order.type == mt5.ORDER_TYPE_BUY_STOP else 'sell'
            order_time = getattr(order, 'time_setup', None)
            if order_time:
                buf_e = self.data.histories[self.symbol][E]
                ts = next((c.timestamp for c in buf_e if c.timestamp >= order_time), None)
                if ts:
                    self.entry_timestamps[order.ticket] = ts
        for p in self.data.mt5.positions_get(symbol=self.symbol) or []:
            if p.magic == 234000:
                entry_ts = datetime.fromtimestamp(p.time, tz=pytz.UTC).replace(tzinfo=None)
                self.entry_timestamps[p.ticket] = entry_ts
                self.open_ticket = p.ticket
                self.entry_price = p.price_open
                self.initial_stop = p.sl
                self.current_sl = p.sl
                self.side = 'buy' if p.type == mt5.POSITION_TYPE_BUY else 'sell'
                self.break_even_applied[p.ticket] = False
                self.risk_mgr.trailing_levels[p.ticket] = 0
                print(f"[INIT] Übernehme Position {p.ticket}: Entry-Time={entry_ts}, Price={self.entry_price}, SL={self.initial_stop}")

        print(f"[INIT DEBUG] entry_timestamps nach initialize: {self.entry_timestamps}")
        print(f"[INIT DEBUG] entry_price={self.entry_price}, initial_stop={self.initial_stop}")
        
        # Setze entered_direction initial anhand K- und B-Phase
        k_phase = self.phases.get(K)
        b_phase = self.phases.get(B)

        if k_phase and b_phase:
            if 'BULL' in k_phase.name and 'BULL' in b_phase.name:
                self.entered_direction = 'bull'
            elif 'BEAR' in k_phase.name and 'BEAR' in b_phase.name:
                self.entered_direction = 'bear'
            else:
                self.entered_direction = None
        else:
            self.entered_direction = None

        print(f"[INIT] entered_direction initial gesetzt auf: {self.entered_direction}")

        # K->B->E Durchbounce: Nur überschreiben, wenn entered_direction None ist
        if self.entered_direction is None:
            if can_k_to_b(self.phases[K]):
                self.entered_direction = 'bull' if 'BULL' in self.phases[K].name else 'bear'
                print(f"[DEBUG] entered_direction durch K->B gesetzt: {self.entered_direction}")
                self._record_switch(K, self.data.histories[self.symbol][K], 'low' if self.entered_direction == 'bull' else 'high')
                self._switch_to(B)
                if can_b_to_e(self.phases[K], self.phases[B]):
                    self.entered_direction = 'bull' if 'BULL' in self.phases[B].name else 'bear'
                    print(f"[DEBUG] entered_direction durch B->E gesetzt: {self.entered_direction}")
                    self._record_switch(B, self.data.histories[self.symbol][B], 'low' if self.entered_direction == 'bull' else 'high')
                    self._switch_to(E)
            elif can_b_to_e(self.phases[K], self.phases[B]):
                self.entered_direction = 'bull' if 'BULL' in self.phases[B].name else 'bear'
                print(f"[DEBUG] entered_direction durch B->E gesetzt (ohne K->B): {self.entered_direction}")
                self._record_switch(B, self.data.histories[self.symbol][B], 'low' if self.entered_direction == 'bull' else 'high')
                self._switch_to(E)


        self._sync_ticket_state_with_mt5()
        self.sync_active_tf_with_phases()











    def get_active_position_ticket(self):
        pos = [p for p in self.data.mt5.positions_get(symbol=self.symbol) if p.magic == 234000]
        if pos:
            return pos[0].ticket
        return None
    
    
    def sync_active_tf_with_phases(self):
        # Importiere am Anfang der Datei falls nicht schon geschehen:
        # from config.timeframes import can_k_to_b, can_b_to_e, K, B, E

        k_phase = self.phases[K]
        b_phase = self.phases[B]

        # 1. Wenn Wechsel von B nach E laut Strategie erlaubt ist → aktiver TF = E
        if can_b_to_e(k_phase, b_phase):
            self.active_tf = E
        # 2. Wenn Wechsel von K nach B laut Strategie erlaubt ist → aktiver TF = B
        elif can_k_to_b(k_phase):
            self.active_tf = B
        # 3. Ansonsten im K-TF bleiben
        else:
            self.active_tf = K




    def on_new_candle(self, tf: int, candle: Candle) -> None:
        if self.last_update_ts[tf] == candle.timestamp:
            return
        self.last_update_ts[tf] = candle.timestamp

        for tf_upd in TIMEFRAMES:
            # NUR für den tatsächlich betroffenen TF (der bei on_new_candle übergeben wurde)
            buf = self.data.histories[self.symbol][tf]
            last_candle = buf[-1] if buf else None
            fsm = self.machines[tf]
            ctx = fsm.state

            if last_candle and ctx.last_candle_ts != last_candle.timestamp:
                print(f"[DEBUG] FSM-Update für TF={tf}: last_candle_ts alt={ctx.last_candle_ts}, neu={last_candle.timestamp}")
                new_phase = fsm.update_with_candle(last_candle)
                fsm.state.prev_phase = new_phase
                fsm.state.last_candle_ts = last_candle.timestamp
                self.phases[tf] = new_phase

                # Switch-Daten aktualisieren (property, nicht dict)
                if new_phase in (Phase.BASE_SWITCH_BULL, Phase.SWITCH_BULL):
                    self.switch_data[tf] = {
                        'initial_extreme': fsm.state.switch_bull_initial_low,
                        'previous_extreme': fsm.state.switch_bull_prev_higher_high
                    }
                elif new_phase in (Phase.BASE_SWITCH_BEAR, Phase.SWITCH_BEAR):
                    self.switch_data[tf] = {
                        'initial_extreme': fsm.state.switch_bear_initial_high,
                        'previous_extreme': fsm.state.switch_bear_prev_lower_low
                    }
                else:
                    self.switch_data[tf] = {}



                # Sofortiger TF-Wechsel-Check (direkt nach FSM-Update)
                if tf_upd == K and self.active_tf == K and can_k_to_b(self.phases[K]):
                    self.entered_direction = 'bull' if 'BULL' in self.phases[K].name else 'bear'
                    print(f"[DEBUG] entered_direction aktuell: {self.entered_direction}")
                    extreme = 'low' if self.entered_direction == 'bull' else 'high'
                    self._record_switch(K, buf, extreme)
                    self._switch_to(B)
                    if can_b_to_e(self.phases[K], self.phases[B]):
                        self.entered_direction = 'bull' if 'BULL' in self.phases[B].name else 'bear'
                        extreme = 'low' if self.entered_direction == 'bull' else 'high'
                        self._record_switch(B, self.data.histories[self.symbol][B], extreme)
                        self._switch_to(E)
                    return

                if tf_upd == B and self.active_tf == B and can_b_to_e(self.phases[K], self.phases[B]):
                    self.entered_direction = 'bull' if 'BULL' in self.phases[B].name else 'bear'
                    extreme = 'low' if self.entered_direction == 'bull' else 'high'
                    self._record_switch(B, buf, extreme)
                    self._switch_to(E)
                    return

        # --- Pending-Order-Protection ---
        if tf == E:
            phase = self.phases.get(E)
            if phase == Phase.SWITCH_BEAR:
                for order in self.data.mt5.orders_get(symbol=self.symbol) or []:
                    if order.magic != 234000:
                        continue
                    if order.type == mt5.ORDER_TYPE_BUY_STOP:
                        self.data.cancel_order(order.ticket)
                        if self.open_ticket == order.ticket:
                            self.open_ticket = None
            elif phase == Phase.SWITCH_BULL:
                for order in self.data.mt5.orders_get(symbol=self.symbol) or []:
                    if order.magic != 234000:
                        continue
                    if order.type == mt5.ORDER_TYPE_SELL_STOP:
                        self.data.cancel_order(order.ticket)
                        if self.open_ticket == order.ticket:
                            self.open_ticket = None
        
        
        BASE_PHASES = (
            Phase.BASE_BULL, Phase.BASE_BEAR, 
            Phase.BASE_SWITCH_BULL, Phase.BASE_SWITCH_BEAR
        )

        if tf in (B, E) and self.entered_direction is not None:
            k_phase = self.phases[K]
            b_phase = self.phases[B]
            current_k_dir = get_direction(k_phase)
            current_b_dir = get_direction(b_phase)

            if tf == B:
                if should_switch_back_to_k(k_phase, self.entered_direction, current_b_dir):
                    print(f"[INFO] Rücksprung zu K aufgrund von Richtungswechsel oder Phase")
                    self.entered_direction = None
                    self._switch_to(K)
                    return

            elif tf == E:
                if self._has_active_trade():
                    print("[INFO] Aktiver Trade vorhanden – bleibe im E-Timeframe!")
                else:
                    if should_switch_back_to_b(b_phase, self.entered_direction, current_k_dir):
                        if b_phase not in BASE_PHASES:
                            print(f"[INFO] B ({b_phase.name}) keine Base-Phase mehr – Rücksprung zu B")
                            self._switch_to(B)
                        else:
                            print(f"[INFO] Rücksprung zu B oder K aufgrund von Richtungswechsel oder Phase")
                            # Prüfe ob Rücksprung zu K nötig
                            if current_b_dir != current_k_dir:
                                self._switch_to(K)
                            else:
                                self._switch_to(B)
                        return






        
        
        # --- Break-Even und Trailing im E-Timeframe ---
        if tf == E:
            phase = self.phases.get(E)
            buf = self.data.histories[self.symbol][E]

            # 1) Break-Even und Trailing für jede aktive Bot-Position
            positions = [
                p for p in self.data.mt5.positions_get(symbol=self.symbol) or []
                if p.magic == 234000
            ]
            for p in positions:
                ticket   = p.ticket
                entry_ts = self.entry_timestamps.get(ticket)
                if entry_ts is None:
                    print(f"[WARN] Kein Entry-Timestamp für Ticket {ticket}")
                    continue
                buf_e = [c for c in buf if c.timestamp >= entry_ts]
                print(f"[DEBUG] buf_e length für Ticket {ticket}: {len(buf_e)}")

                # Break-Even
                new_sl = self.risk_mgr.try_break_even(
                    candles=buf_e,
                    side=self.side,
                    entry_price=self.entry_price,
                    spread=self.spread,
                    current_sl=self.current_sl,
                    symbol=self.symbol,
                    ticket=ticket
                )
                if new_sl is not None:
                    self.current_sl = new_sl
                    self.break_even_applied[ticket] = True
                    print(f"[BE] Break-Even aktiviert: new_sl={new_sl} for Ticket={ticket}")

                # Trailing
                new_sl = self.risk_mgr.try_trailing(
                    candles=buf_e,
                    symbol=self.symbol,
                    side=self.side,
                    entry_price=self.entry_price,
                    initial_stop=self.initial_stop,
                    current_sl=self.current_sl,
                    ticket=ticket
                )
                if new_sl is not None:
                    self.current_sl = new_sl
                    print(f"[TR] Trailing Stop applied: new_sl={new_sl} for Ticket={ticket}")

            # 2) Einstieg in E (Entry-Phase)
            entry_key = (self.symbol, candle.timestamp)
            if entry_key in self.processed_entry_candles:
                print(f"[ORDER-SKIP] Entry für {entry_key} bereits verarbeitet.")
                return

            # Keine offene Position, keine offene Order
            open_pos = self.data.mt5.positions_get(symbol=self.symbol) or []
            open_ord = self.data.mt5.orders_get(symbol=self.symbol) or []
            active = any(p.magic == 234000 for p in open_pos) or any(o.magic == 234000 for o in open_ord)
            if active:
                print(f"[ORDER-SKIP] Bereits Order/Position für {self.symbol} offen.")
                return

            symbol_info = mt5.symbol_info(self.symbol)
            tick_data   = mt5.symbol_info_tick(self.symbol)
            tick_size   = symbol_info.point
            stop_level  = getattr(symbol_info, 'trade_stops_level', 0)
            ask         = tick_data.ask
            bid         = tick_data.bid

            entry = None
            if self.entered_direction == 'bull' and phase == Phase.BASE_SWITCH_BULL:
                if (
                    self.phases[K] in (Phase.BASE_BULL, Phase.BASE_SWITCH_BULL)
                    and self.phases[B] in (Phase.BASE_BULL, Phase.BASE_SWITCH_BULL)
                    and get_direction(self.phases[K]) == get_direction(self.phases[B]) == 'bull'
                ):
                    entry = self.entry_mgr.check_buy_stop(buf, ask, stop_level, tick_size)
                else:
                    print(f"[BLOCK] Buy-Stop NICHT erlaubt: K={self.phases[K]}, B={self.phases[B]}, dir={get_direction(self.phases[K])}")
            elif self.entered_direction == 'bear' and phase == Phase.BASE_SWITCH_BEAR:
                if (
                    self.phases[K] in (Phase.BASE_BEAR, Phase.BASE_SWITCH_BEAR)
                    and self.phases[B] in (Phase.BASE_BEAR, Phase.BASE_SWITCH_BEAR)
                    and get_direction(self.phases[K]) == get_direction(self.phases[B]) == 'bear'
                ):
                    entry = self.entry_mgr.check_sell_stop(buf, bid, stop_level, tick_size)
                else:
                    print(f"[BLOCK] Sell-Stop NICHT erlaubt: K={self.phases[K]}, B={self.phases[B]}, dir={get_direction(self.phases[K])}")

            if entry:
                self._open_new_trade(entry)



        # --- Cleanup nach Trade-Close für alle nicht mehr aktiven Tickets ---
        aktive_tickets = set(
            p.ticket for p in self.data.mt5.positions_get(symbol=self.symbol) or []
            if p.magic == 234000
        )
        for ticket in list(self.entry_timestamps.keys()):
            if ticket not in aktive_tickets:
                self.entry_timestamps.pop(ticket, None)
                self.break_even_applied.pop(ticket, None)
                self.risk_mgr.trailing_levels.pop(ticket, None)

        # _pending_to_position konsistent säubern (nur falls vorhanden)
        if hasattr(self.data, "_pending_to_position") and self.symbol in self.data._pending_to_position:
            for ticket in list(self.data._pending_to_position[self.symbol].keys()):
                if ticket not in aktive_tickets:
                    self.data._pending_to_position[self.symbol].pop(ticket, None)
            if not self.data._pending_to_position[self.symbol]:
                self.data._pending_to_position.pop(self.symbol)

        self._sync_ticket_state_with_mt5()

        
                    
    def _sync_ticket_state_with_mt5(self):
        """Synchronisiere State-Flags mit echten offenen Tickets."""
        aktive_tickets = set(
            p.ticket for p in self.data.mt5.positions_get(symbol=self.symbol) or []
            if p.magic == 234000
        )
        # BreakEven/Trailing: Fehlt → anlegen, nicht mehr offen → löschen
        for p in aktive_tickets:
            if p not in self.break_even_applied:
                self.break_even_applied[p] = False
            if p not in self.risk_mgr.trailing_levels:
                self.risk_mgr.trailing_levels[p] = 0
        for t in list(self.break_even_applied.keys()):
            if t not in aktive_tickets:
                self.break_even_applied.pop(t, None)
        for t in list(self.risk_mgr.trailing_levels.keys()):
            if t not in aktive_tickets:
                self.risk_mgr.trailing_levels.pop(t, None)
        for t in list(self.entry_timestamps.keys()):
            if t not in aktive_tickets:
                self.entry_timestamps.pop(t, None)


    def _open_new_trade(self, entry: Dict[str, float]) -> None:
        # DEDUPLICATION: Keine Order/Position mehrfach
        open_orders = self.data.mt5.orders_get(symbol=self.symbol) or []
        if any(getattr(o, "magic", None) == 234000 for o in open_orders):
            print("[ORDER-BLOCKED] Bereits Pending-Order vorhanden – keine neue Order platzieren.")
            return
        open_positions = self.data.mt5.positions_get(symbol=self.symbol) or []
        if any(getattr(p, "magic", None) == 234000 for p in open_positions):
            print("[ORDER-BLOCKED] Bereits Position vorhanden – keine neue Order platzieren.")
            return

        symbol_info = self.data.mt5.symbol_info(self.symbol)
        tick = symbol_info.point

        entry_price = math.floor(entry['entry_price'] / tick) * tick
        stop_loss   = math.floor(entry['stop_loss']   / tick) * tick

        self.side = entry['side']
        self.entry_price = entry_price
        entry_ts = self.data.histories[self.symbol][E][-1].timestamp
        self.initial_stop = stop_loss

        size = self.risk_mgr.calculate_position_size(
            self.symbol, entry_price, stop_loss, self.side
        )

        print(f"[DEBUG] _open_new_trade: side={self.side}, entry_price={entry_price}, stop_loss={stop_loss}, size={size}, tick={tick}")

        res = self.data.place_order(
            symbol=self.symbol,
            side=self.side,
            price=entry_price,
            size=size,
            stop_loss=stop_loss
        )

        if res and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
            self.open_ticket = res.order
            self.current_sl = stop_loss
            self.entry_timestamps[self.open_ticket] = entry_ts
            rr_pips = abs(entry_price - stop_loss) / tick
            print(f"[INFO] Trade eröffnet (Ticket={self.open_ticket}): 1RR = {rr_pips:.1f} Pips")
            self.data.modify_order(
                symbol=self.symbol, ticket=self.open_ticket, new_sl=stop_loss
            )





    def _switch_to(self, tf: int) -> None:
        print(f"[SWITCH] Wechsle auf TF={tf} ({self.symbol})")

        # Historie NICHT neu laden, da im Live-Betrieb parallel aktualisiert
        hist = self.data.histories[self.symbol].get(tf, [])

        if not hist or len(hist) < 3:
            print(f"[ERROR] Nicht genügend Candles ({len(hist) if hist else 0}) für TF {tf}, kein Zustandwechsel!")
            return

        # FSM bleibt unverändert, kein Replay; nur Phasenstatus übernehmen
        phase = self.phases.get(tf)
        print(f"[SWITCH] Phase aktuell für TF={tf}: {phase.name if phase else 'N/A'}")

        # Setze last_candle_ts auf letzte Kerze der Historie
        fsm = self.machines[tf]
        fsm.state.last_candle_ts = hist[-1].timestamp if hist else None
        self.last_update_ts[tf] = hist[-1].timestamp if hist else None

        # Synchronisiere aktiven Timeframe
        self.sync_active_tf_with_phases()

        if tf == K:
            self._bounce_from_k_if_needed()



    def _bounce_from_k_if_needed(self):
        k_phase = self.phases[K]
        if k_phase in (
            Phase.BASE_SWITCH_BULL, Phase.BASE_BULL,
            Phase.BASE_SWITCH_BEAR, Phase.BASE_BEAR,
            Phase.TREND_BULL, Phase.TREND_BEAR
        ):
            self.entered_direction = 'bull' if 'BULL' in k_phase.name else 'bear'
            extreme = 'low' if self.entered_direction == 'bull' else 'high'
            self._record_switch(K, self.data.histories[self.symbol][K], extreme)
            self._switch_to(B)
            print(f"[INFO] Sofortiger Bounce: K ({k_phase.name}) → zurück nach B!")

    def _record_switch(self, tf: int, buf: List[Candle], extreme: str) -> None:
        ctx = self.machines[tf].state
        if extreme == 'low':
            initial = ctx.switch_bull_initial_low
            prev    = ctx.switch_bull_prev_higher_high
        else:
            initial = ctx.switch_bear_initial_high
            prev    = ctx.switch_bear_prev_lower_low
        self.switch_data[tf] = {'initial_extreme': initial, 'previous_extreme': prev}


    def _has_active_trade(self) -> bool:
        open_pos = [
            p for p in self.data.mt5.positions_get(symbol=self.symbol) or []
            if p.magic == 234000
        ]
        open_ord = [
            o for o in self.data.mt5.orders_get(symbol=self.symbol) or []
            if o.magic == 234000
        ]
        return bool(open_pos or open_ord)


    
    

    def print_summary(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] {self.symbol} summary:")
        for tf in TIMEFRAMES:
            lbl = {K: '1h', B: '15m', E: '1m'}[tf]
            phase = self.phases.get(tf)
            line = f"  {lbl}: {phase.name if phase else 'N/A'}"
            history = self.data.histories[self.symbol].get(tf)
            if history and len(history) >= 20:
                last_candle = history[-1]
                ema_10 = getattr(last_candle, "ema10", None)
                ema_20 = getattr(last_candle, "ema20", None)
                if ema_10 is not None and ema_20 is not None:
                    line += f" | last_candle: {last_candle.timestamp} | EMA10={ema_10:.5f} EMA20={ema_20:.5f}"
                else:
                    line += " | EMA10/20: n/a"
            else:
                line += " | EMA10/20: n/a"

            # Hier nach der Schleife
            print(f"  entered_direction: {self.entered_direction}")

            ctx = self.machines[tf].state

            # Bestätigung nur ausgeben, wenn valid == True
            if phase in (Phase.SWITCH_BULL, Phase.BASE_SWITCH_BULL, Phase.BASE_BULL):
                conf = ctx.last_confirmation_bullish
                if conf and conf.valid and conf.candle:
                    c = conf.candle
                    line += f" | conf_bullish: {c.timestamp}, o={c.open}, h={c.high}, l={c.low}, c={c.close}"
                # Ausgabe der Kontextwerte für SWITCH_BULL
                if phase == Phase.SWITCH_BULL:
                    line += f" | initial_low={ctx.switch_bull_initial_low}, prev_higher_high={ctx.switch_bull_prev_higher_high}"

            if phase in (Phase.SWITCH_BEAR, Phase.BASE_SWITCH_BEAR, Phase.BASE_BEAR):
                conf = ctx.last_confirmation_bearish
                if conf and conf.valid and conf.candle:
                    c = conf.candle
                    line += f" | conf_bearish: {c.timestamp}, o={c.open}, h={c.high}, l={c.low}, c={c.close}"
                # Ausgabe der Kontextwerte für SWITCH_BEAR
                if phase == Phase.SWITCH_BEAR:
                    line += f" | initial_high={ctx.switch_bear_initial_high}, prev_lower_low={ctx.switch_bear_prev_lower_low}"



            # Order/Position Info auf E
            if tf == E and self.open_ticket is not None:
                has_order = any(
                    o.ticket == self.open_ticket
                    for o in self.data.mt5.orders_get(symbol=self.symbol) or []
                    if o.magic == 234000
                )
                has_pos = any(
                    p.ticket == self.open_ticket
                    for p in self.data.mt5.positions_get(symbol=self.symbol) or []
                    if p.magic == 234000
                )
                if has_order or has_pos:
                    rr = (abs(self.entry_price - self.initial_stop) if self.entry_price and self.initial_stop else None)
                    line += f" | entry={self.entry_price:.5f}, sl={self.initial_stop:.5f}"
                    if rr is not None:
                        line += f", 1RR={rr:.5f}"
                else:
                    self.open_ticket = None
                    self.entry_price = None
                    self.initial_stop = None
                    self.current_sl = None
                    self.side = None

            # Farb-/Fettlogik
            if tf == self.active_tf:
                if tf == B:
                    color = "\033[1m\033[34m"
                elif tf == K:
                    color = "\033[1m\033[33m"
                elif tf == E:
                    color = "\033[1m\033[32m"
                else:
                    color = ""
                line = f"{color}{line}\033[0m"
            print(line)
        
        # NEU: Ausgabe der letzten 10 Kerzen für E mit EMA10/20
        #b_history = self.data.histories[self.symbol].get(B, [])
        #print(f"\n[{now}] Letzte 10 Kerzen für TF=E (1m) mit EMA10 und EMA20:")
        #for c in b_history[-10:]:
            #ema10 = getattr(c, "ema10", None)
            #ema20 = getattr(c, "ema20", None)
            #print(f"  {c.timestamp} | O={c.open:.5f} H={c.high:.5f} L={c.low:.5f} C={c.close:.5f} | EMA10={ema10 if ema10 is not None else 'n/a'} EMA20={ema20 if ema20 is not None else 'n/a'}")






    def stop(self) -> None:
        self.data.stop()
