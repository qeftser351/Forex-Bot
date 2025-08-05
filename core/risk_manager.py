import MetaTrader5 as mt5
import math
from typing import List, Optional, Dict
from core.types import Candle
import time
from core.phase_manager import PhaseState


class RiskManager:
    """
    RiskManager: Beinhaltet Logik für Positionsgröße, Break-Even und Trailing-Stop.
    """
    def __init__(
        self,
        account_balance: float,
        max_risk_per_trade: float = 0.01
    ):
        self.account_balance = account_balance
        self.max_risk = max_risk_per_trade
        self.trailing_levels: Dict[int, int] = {}
        # optionale Attribute für externe Daten
        self.symbol: Optional[str] = None
        self.spread: Optional[float] = None
        self.tick_size: Optional[float] = None
        

    def _get_min_stop_distance(self, symbol: str, fallback_pips: float = 0.5) -> float:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Symbol-Info für {symbol} nicht verfügbar")

        # 1) stops_level vom Broker, wenn vorhanden
        stops = getattr(info, 'trade_stops_level', None) or getattr(info, 'stoplevel', None)
        if stops:
            # Broker gibt Mindestabstand in “Ticks” zurück
            return stops * info.point

        # 2) Fallback in Pips
        #    pip_size = 10 * point – funktioniert für 5- und 3-stellige Quoting-Paare
        pip_size = info.point * 10
        print(f"[WARN] stops_level für {symbol} nicht verfügbar, verwende Fallback {fallback_pips} pips → {fallback_pips * pip_size}")
        return fallback_pips * pip_size




    def get_pip_value(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Symbol-Info für {symbol} nicht verfügbar")
        contract_size = getattr(info, 'trade_contract_size', None) or getattr(info, 'contract_size', None)
        if contract_size is None:
            raise RuntimeError(f"Konnte contract size für {symbol} nicht bestimmen")
        return contract_size * info.point



    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        side: str,
        risk_amount=50.0
    ) -> float:
        """
        Universelle Positionsgrößenberechnung – riskiere exakt risk_amount der Kontowährung pro Trade,
        oder, wenn nicht gesetzt, (self.account_balance * self.max_risk).
        """
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            raise RuntimeError(f"Symbol {symbol} nicht gefunden")

        min_lot = symbol_info.volume_min
        max_lot = symbol_info.volume_max
        lot_step = symbol_info.volume_step
        digits = symbol_info.digits

        pip_size = 0.01 if 'JPY' in symbol or digits == 3 else 0.0001
        stop_loss_pips = abs(entry_price - stop_loss) / pip_size

        tick_value = symbol_info.trade_tick_value
        tick_size  = symbol_info.trade_tick_size

        if not tick_value or not tick_size:
            pip_value_per_lot = 10.0
        else:
            pip_value_per_lot = (tick_value / tick_size) * pip_size

        acc_info = mt5.account_info()
        account_currency = acc_info.currency if acc_info and hasattr(acc_info, "currency") else "USD"
        base = symbol[:3]
        quote = symbol[3:]
        if quote == "JPY" and account_currency == "USD":
            usd_jpy = None
            try:
                usd_jpy_info = mt5.symbol_info("USDJPY")
                usd_jpy_tick = mt5.symbol_info_tick("USDJPY")
                if usd_jpy_info and usd_jpy_tick:
                    usd_jpy = usd_jpy_tick.bid
            except Exception:
                pass
            if not usd_jpy:
                usd_jpy = 150.0
            pip_value_per_lot = 1000.0 / usd_jpy

        # ---- Korrektur: Dynamisches Risiko berechnen ----
        if risk_amount is None:
            risk_amount = self.account_balance * self.max_risk

        if stop_loss_pips == 0 or pip_value_per_lot == 0:
            return float(min_lot)

        lot = risk_amount / (stop_loss_pips * pip_value_per_lot)
        lot = max(min_lot, min(lot, max_lot))
        lot = math.floor(lot / lot_step) * lot_step
        lot = round(lot, 2)
        return float(lot)











    def is_go_candle_bullish(
        self,
        candle: Candle,
        entry_price: float
    ) -> bool:
        return candle.high >= entry_price and candle.close > entry_price


    def calculate_breakeven_price_buy(
        self,
        candles: List[Candle],
        entry_price: float,
        spread: float,
        entry_candle_timestamp: Optional[int] = None  # Timestamp aus FSM (last_confirmation_bullish["candle"].timestamp)
    ) -> Optional[float]:
        """
        Break-Even nach Buy-Stop-Entry mit klassischer Go-/Confirmation-Logik.
        """
        if entry_candle_timestamp is None:
            print("[WARN] calculate_breakeven_price_buy: Kein Entry-Timestamp übergeben!")
            return None

        # Suche Index der Entry-Kerze (= Go-Candle)
        try:
            entry_idx = next(i for i, c in enumerate(candles) if c.timestamp == entry_candle_timestamp)
        except StopIteration:
            print("[WARN] calculate_breakeven_price_buy: Entry-Candle nicht gefunden!")
            return None

        # Step 1: Suche Bestätigungskerze nach Go-Candle
        confirmation_idx = None
        for i in range(entry_idx + 1, len(candles)):
            c = candles[i]
            if c.close > candles[entry_idx].high and c.low > entry_price:
                confirmation_idx = i
                break
            # Wenn der Kurs zwischendurch das Entry erneut verletzt, Reset!
            if c.low <= entry_price:
                return None

        if confirmation_idx is None:
            return None  # Noch keine Bestätigung gefunden

        # Step 2: Break-Even erst nach Abschluss der nächsten Candle!
        be_idx = confirmation_idx + 1
        if be_idx >= len(candles):
            return None  # Es gibt noch keine fertige Candle nach der Confirmation

        # Die Candle nach Confirmation (BE setzen)
        c = candles[be_idx]
        # Optional: Invalidierung prüfen – wenn c.low <= entry_price, kein BE!
        if c.low <= entry_price:
            return None

        candidate_sl = entry_price - spread

        price_ref = mt5.symbol_info_tick(self.symbol).bid
        min_dist = self._get_min_stop_distance(self.symbol, fallback_pips=0.5)
        if candidate_sl >= price_ref - min_dist:
            candidate_sl = price_ref - min_dist

        tick = mt5.symbol_info(self.symbol).point
        decimals = int(-math.log10(tick))
        candidate_sl = math.floor(candidate_sl / tick) * tick
        candidate_sl = round(candidate_sl, decimals)

        return candidate_sl







    def calculate_breakeven_price_sell(
        self,
        candles: List[Candle],
        entry_price: float,
        spread: float,
        entry_candle_timestamp: Optional[int] = None  # Timestamp aus FSM (last_confirmation_bearish["candle"].timestamp)
    ) -> Optional[float]:
        """
        Break-Even nach Sell-Stop-Entry mit klassischer Go-/Confirmation-Logik.
        """
        if entry_candle_timestamp is None:
            print("[WARN] calculate_breakeven_price_sell: Kein Entry-Timestamp übergeben!")
            return None

        # Suche Index der Entry-Kerze (= Go-Candle)
        try:
            entry_idx = next(i for i, c in enumerate(candles) if c.timestamp == entry_candle_timestamp)
        except StopIteration:
            print("[WARN] calculate_breakeven_price_sell: Entry-Candle nicht gefunden!")
            return None

        # Step 1: Suche Bestätigungskerze nach Go-Candle (Confirmation)
        confirmation_idx = None
        for i in range(entry_idx + 1, len(candles)):
            c = candles[i]
            # Confirmation: close < go.low UND high < entry_price
            if c.close < candles[entry_idx].low and c.high < entry_price:
                confirmation_idx = i
                break
            # Invalidierung: Entry von oben verletzt
            if c.high >= entry_price:
                return None

        if confirmation_idx is None:
            return None  # Noch keine Confirmation gefunden

        # Step 2: Break-Even erst nach Abschluss der nächsten Candle!
        be_idx = confirmation_idx + 1
        if be_idx >= len(candles):
            return None  # Es gibt noch keine fertige Candle nach der Confirmation

        # Die Candle nach Confirmation (BE setzen)
        c = candles[be_idx]
        # Optional: Invalidierung prüfen – wenn c.high >= entry_price, kein BE!
        if c.high >= entry_price:
            return None

        candidate_sl = entry_price + spread

        price_ref = mt5.symbol_info_tick(self.symbol).ask
        min_dist = self._get_min_stop_distance(self.symbol, fallback_pips=0.5)
        if candidate_sl <= price_ref + min_dist:
            candidate_sl = price_ref + min_dist

        tick = mt5.symbol_info(self.symbol).point
        decimals = int(-math.log10(tick))
        candidate_sl = math.ceil(candidate_sl / tick) * tick
        candidate_sl = round(candidate_sl, decimals)

        return candidate_sl







    #Trailing ab 2RR schon auf 1RR - Test
    def trailing_step_buy(self, candles, entry_price, rr, current_sl, last_level, fsm_context=None):
        """
        Trailing für Buy: Ab bestätigtem Entry aus fsm_context (PhaseState). Nutzt Property-Access.
        """
        if fsm_context is not None and hasattr(fsm_context, "last_confirmation_bullish"):
            entry_conf = getattr(fsm_context, "last_confirmation_bullish", None)
            if not entry_conf or not entry_conf.valid:
                return None, last_level
            entry_candle = entry_conf.candle
            try:
                entry_idx = next(i for i, c in enumerate(candles) if c.timestamp == entry_candle.timestamp)
                candles = candles[entry_idx:]  # Nur ab Entry!
            except StopIteration:
                return None, last_level
        # --- Standard-Trailing-Logik ---
        max_high = max((c.high for c in candles), default=entry_price)
        level = int((max_high - entry_price) / rr)
        if level >= 2 and level > last_level:   # ab 2RR trailed man
            candidate = entry_price + (level - 1) * rr
            if candidate > current_sl:
                return candidate, level
        return None, last_level



    def trailing_step_sell(
        self,
        candles,
        entry_price,
        rr,
        current_sl,
        last_level,
        fsm_context  # -> PhaseState
    ):
        # Hole bestätigte Entry-Candle aus FSM (muss Property sein)
        entry_conf = getattr(fsm_context, "last_confirmation_bearish", None)
        if not entry_conf or not entry_conf.valid:
            return None, last_level

        entry_candle = entry_conf.candle


        try:
            entry_idx = next(i for i, c in enumerate(candles) if c.timestamp == entry_candle.timestamp)
        except StopIteration:
            return None, last_level

        # Alle Candles ab Entry (inklusive) für Trailing analysieren
        min_low = min((c.low for c in candles[entry_idx:]), default=entry_price)
        level = int((entry_price - min_low) / rr)
        if level >= 2 and level > last_level:
            candidate = entry_price - (level - 1) * rr
            if candidate < current_sl:
                return candidate, level
        return None, last_level



    def try_break_even(
        self,
        symbol: str,
        candles: List[Candle],
        side: str,
        entry_price: float,
        spread: float,
        current_sl: float,
        ticket: int,
        fsm_context: PhaseState
    ) -> Optional[float]:
        # 1) Context prüfen
        entry_conf = (
            getattr(fsm_context, "last_confirmation_bullish", None)
            if side == "buy"
            else getattr(fsm_context, "last_confirmation_bearish", None)
        )
        if not entry_conf or not entry_conf.valid:
            print("[DEBUG] Keine bestätigte Entry-Candle im Kontext. BE übersprungen.")
            return None
        entry_candle = entry_conf.candle

        try:
            entry_idx = next(i for i, c in enumerate(candles) if c.timestamp == entry_candle.timestamp)
        except StopIteration:
            print("[DEBUG] Entry-Candle nicht im aktuellen Buffer.")
            return None
        relevant_candles = candles[entry_idx:]

        # 2) Break-Even-Level bestimmen (angepasste Methoden ohne fsm_context)
        if side == 'buy':
            new_sl = self.calculate_breakeven_price_buy(relevant_candles, entry_price, spread)
        else:
            new_sl = self.calculate_breakeven_price_sell(relevant_candles, entry_price, spread)
        if new_sl is None or new_sl == current_sl:
            print(f"[DEBUG] Kein neues SL-Level berechnet oder unverändert: new_sl={new_sl}")
            return None

        # 3) Fallback auf Mindestabstand prüfen und runden (wie bisher)
        tick = mt5.symbol_info(symbol).point
        tick_data = mt5.symbol_info_tick(symbol)
        price_ref = tick_data.bid if side == 'buy' else tick_data.ask
        min_dist = self._get_min_stop_distance(symbol, fallback_pips=0.5)
        if abs(new_sl - price_ref) < min_dist:
            print(f"[WARN] SL {new_sl} zu nah am Markt! Fallback-Abstand {min_dist} verwenden.")
            new_sl = (price_ref - min_dist) if side == 'buy' else (price_ref + min_dist)
        decimals = int(-math.log10(tick))
        if side == 'buy':
            new_sl = math.ceil(new_sl / tick) * tick
        else:
            new_sl = math.floor(new_sl / tick) * tick
        new_sl = round(new_sl, decimals)
    
        # 4) Modify-Request bauen
        req: Dict = {
            "action":       mt5.TRADE_ACTION_SLTP,
            "symbol":       symbol,
            "position":     ticket,
            "sl":           new_sl,
            "tp":           0.0,
            "deviation":    20,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        print(f"[DEBUG] BE-Modify-Request: {req}")
    
        positions = mt5.positions_get()
        print(f"[DEBUG] Aktuelle offene Positionen: {[ (p.ticket, p.symbol, p.sl, p.type) for p in positions ]}")
        print(f"[DEBUG] Versuche SL zu ändern für Ticket={ticket} (sollte Position-Ticket sein!)")

        # 5) order_check
        print(f"[INFO] BE→order_check: ticket={ticket}, candidate_sl={new_sl}")
        chk = mt5.order_check(req)
        print(f"[DEBUG] order_check Objekt: {chk}")
        try:
            print(f"[DEBUG] order_check Felder: {vars(chk)}")
        except Exception:
            try:
                print(f"[DEBUG] order_check Felder: {chk.__dict__}")
            except Exception:
                print(f"[DEBUG] order_check Felder: nicht verfügbar")

        if not chk or chk.retcode not in (0, mt5.TRADE_RETCODE_DONE):
            print(
                f"[ERROR] BE order_check failed: retcode={getattr(chk,'retcode',None)}, "
                f"comment={getattr(chk,'comment',None)}"
            )
            return None

        # 6) order_send
        res = mt5.order_send(req)
        print(f"[INFO] BE→order_send retcode={getattr(res,'retcode',None)}, "
            f"order_ticket={getattr(res,'order',None)}, new_sl={new_sl}")
        if not (res and res.retcode in (0, mt5.TRADE_RETCODE_DONE)):
            print(f"[ERROR] BE order_send failed: retcode={getattr(res,'retcode',None)}, "
                f"comment={getattr(res,'comment',None)}")
            return None

        print(f"[INFO] Break-Even applied successfully, new_sl={new_sl}")
        return new_sl




    
    def try_trailing(
        self,
        symbol: str,
        candles: List[Candle],
        side: str,
        entry_price: float,
        initial_stop: float,
        current_sl: float,
        ticket: int
    ) -> Optional[float]:
        import math, time
        from datetime import datetime
        import MetaTrader5 as mt5

        print(f"[{datetime.now()}][DEBUG] try_trailing called for "
            f"symbol={symbol}, ticket={ticket}, side={side}, "
            f"entry={entry_price}, current_sl={current_sl}")

        # Symbol- und Info-Objekt prüfen
        if not mt5.symbol_select(symbol, True):
            print(f"[ERROR] Symbol {symbol} konnte nicht selektiert werden!")
            return None
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"[ERROR] Kann symbol_info für {symbol} nicht lesen.")
            return None

        tick_size = info.point
        level = getattr(info, "trade_stops_level", 0)
        min_dist = level * tick_size if level and level > 0 else self._get_min_stop_distance(symbol, fallback_pips=0.5)
        print(f"[DEBUG] min_dist für {symbol} = {min_dist}")

        rr = abs(entry_price - initial_stop)
        last_level = self.trailing_levels.get(ticket, 0)
        if side == 'buy':
            candidate, new_level = self.trailing_step_buy(candles, entry_price, rr, current_sl, last_level)
        else:
            candidate, new_level = self.trailing_step_sell(candles, entry_price, rr, current_sl, last_level)

        if candidate is None or candidate == current_sl:
            print(f"[DEBUG] Kein neues Trailing-SL berechnet (unchanged: {candidate})")
            return None

        tick_data = mt5.symbol_info_tick(symbol)
        price_ref = tick_data.bid if side == 'buy' else tick_data.ask

        if side == 'buy':
            candidate = min(candidate, price_ref - min_dist)
            if candidate <= current_sl:
                print(f"[WARN] Neuer SL ({candidate}) <= alter SL ({current_sl}) – kein Fortschritt! (buy)")
                return None
        else:
            candidate = max(candidate, price_ref + min_dist)
            if candidate >= current_sl:
                print(f"[WARN] Neuer SL ({candidate}) >= alter SL ({current_sl}) – kein Fortschritt! (sell)")
                return None

        if abs(candidate - price_ref) < min_dist:
            print(f"[WARN] Trailing-SL {candidate} zu nah am Markt! Setze hart auf price_ref ± min_dist.")
            candidate = (price_ref - min_dist) if side == 'buy' else (price_ref + min_dist)

        decimals = int(-math.log10(tick_size))
        if side == 'buy':
            candidate = math.ceil(candidate / tick_size) * tick_size
        else:
            candidate = math.floor(candidate / tick_size) * tick_size
        candidate = round(candidate, decimals)

        # Existiert die Position noch?
        positions = mt5.positions_get(symbol=symbol) or []
        if not any(p.ticket == ticket for p in positions):
            print("[WARN] Keine Position mit Ticket gefunden!")
            return None

        # --- MODIFY-REQUEST ---
        req = {
            "action":       mt5.TRADE_ACTION_SLTP,
            "symbol":       symbol,
            "position":     ticket,
            "sl":           candidate,
            "tp":           0.0,
            "deviation":    20,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        print(f"[DEBUG] TR-Modify-Request: {req}")

        chk = mt5.order_check(req)
        print(f"[DEBUG] order_check retcode={getattr(chk, 'retcode', None)}, comment={getattr(chk, 'comment', None)}")
        if not chk or chk.retcode not in (0, mt5.TRADE_RETCODE_DONE):
            print(f"[ERROR] Trailing order_check failed: retcode={getattr(chk, 'retcode', None)}, comment={getattr(chk, 'comment', None)}")
            return None

        res = mt5.order_send(req)
        print(f"[DEBUG] order_send retcode={getattr(res, 'retcode', None)}, comment={getattr(res, 'comment', None)}")
        if not res or res.retcode not in (0, mt5.TRADE_RETCODE_DONE):
            print(f"[ERROR] Trailing order_send failed: retcode={getattr(res, 'retcode', None)}, comment={getattr(res, 'comment', None)}")
            return None


        # Nach Modify: Verifizieren, ob MT5 den SL angepasst hat
        time.sleep(0.1)
        positions_after = mt5.positions_get(symbol=symbol) or []
        for p2 in positions_after:
            if p2.ticket == ticket:
                if abs(p2.sl - candidate) < 1e-9:
                    print(f"[INFO] Trailing erfolgreich in MT5: neuer SL={p2.sl}")
                    self.trailing_levels[ticket] = new_level
                    return candidate
                else:
                    print(f"[WARN] Trailing-Update in MT5 nicht umgesetzt (SL bleibt {p2.sl})")
                    # Retry genau einmal, kein Endlos-Loop
                    # --- Retry-Logik wie gehabt, aber maximal 1 Versuch ---
                    level2 = getattr(info, "trade_stops_level", 0)
                    if level2 and level2 > 0:
                        min_dist = level2 * info.point
                    else:
                        min_dist *= 2
                    price_ref = mt5.symbol_info_tick(symbol).bid if side == 'buy' else mt5.symbol_info_tick(symbol).ask

                    if side == 'buy':
                        candidate_retry = price_ref - min_dist
                        candidate_retry = math.ceil(candidate_retry / tick_size) * tick_size
                    else:
                        candidate_retry = price_ref + min_dist
                        candidate_retry = math.floor(candidate_retry / tick_size) * tick_size
                    candidate_retry = round(candidate_retry, decimals)

                    req_retry = {
                        "action":       mt5.TRADE_ACTION_SLTP,
                        "symbol":       symbol,
                        "position":     ticket,
                        "sl":           candidate_retry,
                        "tp":           0.0,
                        "deviation":    20,
                        "type_time":    mt5.ORDER_TIME_GTC,
                        "type_filling": mt5.ORDER_FILLING_IOC,
                    }
                    print(f"[DEBUG] RETRY-Modify-Request: {req_retry}")
                    chk2 = mt5.order_check(req_retry)
                    if chk2 and chk2.retcode == mt5.TRADE_RETCODE_DONE:
                        res2 = mt5.order_send(req_retry)
                        time.sleep(0.1)
                        positions2 = mt5.positions_get(symbol=symbol) or []
                        for p3 in positions2:
                            if p3.ticket == ticket and abs(p3.sl - candidate_retry) < 1e-9:
                                print(f"[INFO] Trailing-Retry erfolgreich: neuer SL={p3.sl}")
                                self.trailing_levels[ticket] = new_level
                                return candidate_retry
                    print(f"[ERROR] Auch Retry hat in MT5 versagt – SL bleibt {p2.sl}")
                    return None

        print("[WARN] Position nicht mehr vorhanden – kein Trailing möglich.")
        return None







    def normalize_lots(self, symbol: str, desired_lots: float) -> float:
        info = mt5.symbol_info(symbol)
        if not info:
            raise RuntimeError(f"Symbol {symbol} nicht gefunden")
        min_vol = info.volume_min
        max_vol = info.volume_max
        step_vol = info.volume_step
        # Clamp auf min/max und immer ABrunden
        lots = max(min_vol, math.floor(desired_lots / step_vol) * step_vol)
        return min(lots, max_vol)
