import time
from datetime import datetime
import MetaTrader5 as mt5
import math
from typing import Dict, List, Callable, Optional
from types import SimpleNamespace
from core.types import Candle
from config.timeframes import get_history_limit
import pytz
import numpy as np

def calc_ema(values, span):
    if len(values) < span:
        return None
    weights = np.exp(np.linspace(-1., 0., span))
    weights /= weights.sum()
    return float(np.dot(values[-span:], weights))

class DataHandler:
    TIMEFRAME_MAP: Dict[int, str] = {
        mt5.TIMEFRAME_M1:  '1m',
        mt5.TIMEFRAME_M15: '15m',
        mt5.TIMEFRAME_H1:  '1h',
    }

    def __init__(self, mt5_module):
        self.mt5 = mt5_module
        self.histories: Dict[str, Dict[int, List[Candle]]] = {}
        self.subscribers: Dict[str, Dict[int, List[Callable]]] = {}
        self._running = False
        self.open_ticket: Optional[int] = None
        self._pending_to_position: Dict[str, Dict[int, int]] = {}
        self.last_history_ts: Dict[str, Dict[int, Optional[datetime]]] = {}

    def fetch_history(self, symbol: str, timeframe: int, limit: int) -> List[Candle]:
        rates = self.mt5.copy_rates_from_pos(symbol, timeframe, 0, limit)
        candles = []
        for r in rates:
            ts_utc = datetime.fromtimestamp(r['time'], tz=pytz.UTC).replace(tzinfo=None)
            candles.append(Candle(
                timestamp=ts_utc,
                open=r['open'],
                high=r['high'],
                low=r['low'],
                close=r['close'],
                volume=(r['tick_volume'] if 'tick_volume' in r.dtype.names else 0)
            ))
        self.histories.setdefault(symbol, {})[timeframe] = candles.copy()
        self.last_history_ts.setdefault(symbol, {})[timeframe] = candles[-1].timestamp if candles else None
        # EMAs berechnen
        for i, c in enumerate(candles):
            closes10 = [x.close for x in candles[max(0, i-9):i+1]]
            c.ema10 = calc_ema(closes10, 10) if len(closes10) == 10 else None
            closes20 = [x.close for x in candles[max(0, i-19):i+1]]
            c.ema20 = calc_ema(closes20, 20) if len(closes20) == 20 else None
        return candles

    def refresh_history(self, symbol, tf):
        hist = self.fetch_history(symbol, tf, get_history_limit(tf))
        self.histories[symbol][tf] = hist[:-1]  # Bis vorletzte Kerze

    def append_and_get(self, symbol: str, timeframe: int, candle: Candle) -> List[Candle]:
        if candle.timestamp.tzinfo is not None:
            candle_ts = candle.timestamp.astimezone(pytz.UTC).replace(tzinfo=None)
        else:
            candle_ts = candle.timestamp

        if timeframe not in self.histories.get(symbol, {}):
            limit = get_history_limit(timeframe)
            self.fetch_history(symbol, timeframe, limit)

        buf = self.histories[symbol][timeframe]
        last_hist_ts = self.last_history_ts.get(symbol, {}).get(timeframe)

        # Keine Duplikate (History-Reload)
        if last_hist_ts and candle_ts == last_hist_ts:
            print(f"[PATCH] Candle {candle_ts} wurde schon aus History geladen, wird NICHT erneut angehängt.")
            self.last_history_ts[symbol][timeframe] = None
            return buf

        # Neues Candle
        new_candle = Candle(
            timestamp=candle_ts,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume
        )
        buf.append(new_candle)
        limit = get_history_limit(timeframe)
        self.histories[symbol][timeframe] = buf[-limit:]
        self.last_history_ts[symbol][timeframe] = candle_ts
        # EMAs für das neue Candle
        buf = self.histories[symbol][timeframe]
        if len(buf) >= 10:
            buf[-1].ema10 = calc_ema([c.close for c in buf[-10:]], 10)
        else:
            buf[-1].ema10 = None
        if len(buf) >= 20:
            buf[-1].ema20 = calc_ema([c.close for c in buf[-20:]], 20)
        else:
            buf[-1].ema20 = None
        return self.histories[symbol][timeframe]

    def subscribe(self, symbol: str, timeframe: int, callback: Callable):
        if timeframe not in self.TIMEFRAME_MAP:
            raise RuntimeError(f"Unsupported timeframe: {timeframe}")
        subs = self.subscribers.setdefault(symbol, {})
        if timeframe not in subs:
            self.fetch_history(symbol, timeframe, get_history_limit(timeframe))
            subs[timeframe] = []
        if callback not in subs[timeframe]:
            subs[timeframe].append(callback)
            print(f"[DEBUG] Subscriber registriert: Symbol={symbol}, TF={timeframe}, CB={callback}")
        else:
            print(f"[DEBUG] Subscriber bereits vorhanden: Symbol={symbol}, TF={timeframe}, CB={callback}")

    def get_symbol_info(self, symbol: str) -> SimpleNamespace:
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Symbol {symbol} nicht verfügbar")
        tick = self.mt5.symbol_info_tick(symbol)
        return SimpleNamespace(
            bid=tick.bid,
            ask=tick.ask,
            stop_level=info.trade_stops_level,
            point=info.point
        )


    def place_order(
        self,
        symbol: str,
        side: str,
        price: float,
        size: float,
        stop_loss: float
    ):
        ok = self.mt5.symbol_select(symbol, True)
        print(f"[DEBUG] symbol_select('{symbol}') → {ok}")

        info = self.mt5.symbol_info(symbol)
        if info is None:
            print(f"[ERROR] symbol_info({symbol}) konnte nicht abgerufen werden!")
            return None
        tick = info.point
        digits = info.digits
        stop_level = getattr(info, 'trade_stops_level', 0) or 10
        min_dist = stop_level * tick

        tick_data = self.mt5.symbol_info_tick(symbol)
        ask = getattr(tick_data, 'ask', None)
        bid = getattr(tick_data, 'bid', None)

        # Preis auf Tickgröße und Mindestabstand prüfen/setzen
        if side == 'buy':
            entry_type = self.mt5.ORDER_TYPE_BUY_STOP
            price = max(price, (ask or 0) + min_dist)
            price = round(price / tick) * tick
            if stop_loss is not None:
                stop_loss = min(stop_loss, price - min_dist)
                stop_loss = round(stop_loss / tick) * tick
        else:
            entry_type = self.mt5.ORDER_TYPE_SELL_STOP
            price = min(price, (bid or 0) - min_dist)
            price = round(price / tick) * tick
            if stop_loss is not None:
                stop_loss = max(stop_loss, price + min_dist)
                stop_loss = round(stop_loss / tick) * tick

        # Mindestabstand-Fehler (MetaTrader lehnt sonst sowieso ab)
        if entry_type == self.mt5.ORDER_TYPE_BUY_STOP and ask is not None and price < ask + min_dist:
            print(f"[ERROR] Buy-Stop zu nah am Ask: Preis={price}, Ask={ask}, min_dist={min_dist}")
            return None
        if entry_type == self.mt5.ORDER_TYPE_SELL_STOP and bid is not None and price > bid - min_dist:
            print(f"[ERROR] Sell-Stop zu nah am Bid: Preis={price}, Bid={bid}, min_dist={min_dist}")
            return None

        # Lotgröße clampen und runden
        min_vol, max_vol, step_vol = info.volume_min, info.volume_max, info.volume_step
        adj_size = max(min(size, max_vol), min_vol)
        adj_size = math.floor(adj_size / step_vol) * step_vol
        adj_size = round(adj_size, 2)

        print(f"[INFO] {symbol}: min_lot={min_vol}, max_lot={max_vol}, lot_step={step_vol}")
        print(f"[DEBUG] stop_level={stop_level}, tick={tick}, min_dist={min_dist}, digits={digits}")
        print(f"[DEBUG] Order-Request: Symbol={symbol}, Typ={'BUY_STOP' if side == 'buy' else 'SELL_STOP'}, Volumen={adj_size}, SL={stop_loss}, Preis={price}")
        print(f"[DEBUG] Bid={bid}, Ask={ask}")

        # Margin Check
        account = self.mt5.account_info()
        margin_req = self.mt5.order_calc_margin(entry_type, symbol, adj_size, price)
        if margin_req > account.margin_free:
            print(f"[ERROR] Nicht genug Margin für {adj_size} Lots auf {symbol}")
            return None

        req = {
            'action':       self.mt5.TRADE_ACTION_PENDING,
            'symbol':       symbol,
            'volume':       adj_size,
            'type':         entry_type,
            'price':        price,
            'sl':           stop_loss,
            'deviation':    10,
            'magic':        234000,
            'comment':      'EMA Edge Bot',
            'type_time':    self.mt5.ORDER_TIME_GTC,
            'type_filling': self.mt5.ORDER_FILLING_RETURN,
        }

        res = self.mt5.order_send(req)
        print(f"[INFO] place_order retcode={getattr(res,'retcode',None)}, ticket={getattr(res,'order',None)}")
        return res

    def cancel_order(self, ticket: int):
        order_list = self.mt5.orders_get(ticket=ticket)
        symbol = None
        if order_list and len(order_list) > 0:
            symbol = order_list[0].symbol
        else:
            # Notfalls Symbol aus Positionen (falls Order gefillt)
            for pos in self.mt5.positions_get() or []:
                if pos.ticket == ticket:
                    symbol = pos.symbol
                    break

        if symbol:
            if not self.mt5.symbol_select(symbol, True):
                print(f"[ERROR] Symbol {symbol} konnte nicht geladen werden!")
                return None

        req = {
            'action':       self.mt5.TRADE_ACTION_REMOVE,
            'order':        ticket,
            'type_time':    self.mt5.ORDER_TIME_GTC,
            'type_filling': self.mt5.ORDER_FILLING_RETURN,
        }
        res = self.mt5.order_send(req)
        print(f"[INFO] cancel_order retcode={getattr(res,'retcode',None)}, ticket={ticket}")
        return res


    # Go-Candle Detection (stateless helpers)
    def is_go_candle_bullish(candle: Candle, entry_price: float) -> bool:
        return candle.low <= entry_price and candle.close > entry_price

    def is_go_candle_bearish(candle: Candle, entry_price: float) -> bool:
        return candle.high >= entry_price and candle.close < entry_price

    def modify_order(
        self,
        symbol: str,
        ticket: int,
        new_sl: float,
        new_tp: float = 0.0
    ):
        """
        Setzt SL/TP für eine bereits gefillte Position (nicht Pending-Order).
        Ticket kann Pending- oder Position-Ticket sein, wird intern gemappt.
        """
        # 1) Symbol sicher auswählen
        if not self.mt5.symbol_select(symbol, True):
            print(f"[ERROR] Symbol {symbol} konnte nicht selektiert werden!")
            return None

        # 2) Mapping von Pending-Order zu Position (so eindeutig wie möglich)
        position_ticket = None

        # a) Direkt übergebenes Ticket als Position suchen
        for pos in self.mt5.positions_get(symbol=symbol) or []:
            if pos.magic == 234000 and pos.ticket == ticket:
                position_ticket = ticket
                break

        # b) Mapping nutzen
        if position_ticket is None:
            position_ticket = self._pending_to_position.get(symbol, {}).get(ticket)

        # c) Fallback: irgendeine offene Bot-Position nehmen und Mapping aktualisieren
        if position_ticket is None:
            for pos in self.mt5.positions_get(symbol=symbol) or []:
                if pos.magic == 234000:
                    position_ticket = pos.ticket
                    self._pending_to_position.setdefault(symbol, {})[ticket] = pos.ticket
                    break

        if position_ticket is None:
            print(f"[ERROR] Keine Bot-Position gefunden für Ticket={ticket} ({symbol})")
            return None

        # 3) Request zusammenbauen
        req = {
            "action":       self.mt5.TRADE_ACTION_SLTP,
            "symbol":       symbol,
            "position":     position_ticket,
            "sl":           new_sl,
            "tp":           new_tp,
            "deviation":    20,
            "type_time":    self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        print(f"[DEBUG] Modify-Request: {req}")

        # 4) order_check (MetaTrader-Validierung)
        chk = self.mt5.order_check(req)
        print(f"[INFO] order_check → retcode={getattr(chk,'retcode',None)}, comment={getattr(chk,'comment',None)}")
        if not chk or chk.retcode != self.mt5.TRADE_RETCODE_DONE:
            print(f"[ERROR] order_check fehlgeschlagen: {getattr(chk,'comment',None)}")
            return None

        # 5) order_send (SL/TP wird gesetzt)
        res = self.mt5.order_send(req)
        print(f"[INFO] order_send → retcode={getattr(res,'retcode',None)}, order={getattr(res,'order',None)}")
        if not res or res.retcode != self.mt5.TRADE_RETCODE_DONE:
            print(f"[ERROR] order_send fehlgeschlagen: {getattr(res,'comment',None)}")
            return None

        print(f"[INFO] SL/TP erfolgreich geändert für Position {position_ticket}: SL={new_sl}, TP={new_tp}")
        return res




    def run(self):
        # Letzter Candle-Timestamp pro Symbol/TF merken
        last_times = {
            sym: {tf: buf[-1].timestamp for tf, buf in tfs.items() if buf}
            for sym, tfs in self.histories.items()
        }
        self._running = True
        print("[DATAHANDLER] Starte Run-Loop... (Ctrl+C zum Stop)")
        while self._running:
            for symbol, tfs in self.subscribers.items():
                for tf_const, callbacks in tfs.items():
                    try:
                        rates = self.mt5.copy_rates_from_pos(symbol, tf_const, 0, 2)
                        if rates is None or len(rates) < 2:
                            continue
                        closed = rates[-2]
                        ts = datetime.fromtimestamp(closed['time'], tz=pytz.UTC).replace(tzinfo=None)
                        last_ts = last_times.setdefault(symbol, {}).get(tf_const)
                        if last_ts is not None and ts <= last_ts:
                            continue
                        last_times[symbol][tf_const] = ts

                        candle = Candle(
                            timestamp=ts,
                            open=closed['open'],
                            high=closed['high'],
                            low=closed['low'],
                            close=closed['close'],
                            volume=(closed['tick_volume'] if 'tick_volume' in closed.dtype.names else 0)
                        )
                        # In History puffern
                        limit = get_history_limit(tf_const)
                        buf = self.histories.setdefault(symbol, {}).setdefault(tf_const, [])
                        buf.append(candle)
                        buf[:] = buf[-limit:]
                        # EMA updaten
                        if len(buf) >= 10:
                            buf[-1].ema10 = calc_ema([c.close for c in buf[-10:]], 10)
                        else:
                            buf[-1].ema10 = None
                        if len(buf) >= 20:
                            buf[-1].ema20 = calc_ema([c.close for c in buf[-20:]], 20)
                        else:
                            buf[-1].ema20 = None

                        print(f"[DEBUG] Sende Candle an Subscriber: TF={tf_const}, Symbol={symbol}, TS={candle.timestamp}")
                        for cb in callbacks:
                            try:
                                cb(candle)
                            except Exception as e:
                                print(f"[ERROR] Callback-Fehler: {e} ({cb})")
                    except Exception as e:
                        print(f"[ERROR] Exception in DataHandler.run für {symbol}/{tf_const}: {e}")

            time.sleep(1)  # Nur 1 Sekunde Pause, damit alle TF regelmäßig gescannt werden

    def stop(self):
        self._running = False
