# src/bybit_executor.py (Versão 17.2 - Trava de Segurança de SL/TP)

from pybit.unified_trading import HTTP
from src.config import settings
from src.utils import logger, log_trade
import math

class BybitExecutor:
    def __init__(self):
        logger.info("Initializing Bybit Executor...")
        self.session = HTTP(api_key=settings.bybit_api_key, api_secret=settings.bybit_api_secret, testnet=False)
        self.leverage = float(settings.leverage)
        self.risk_percent = float(settings.risk_per_trade) / 100
        self.instrument_rules = {}
        logger.warning("Bybit Executor initialized in PRODUCTION MODE for Unified Trading Account (Cross Margin only).")

    def get_open_positions(self):
        try:
            response = self.session.get_positions(category="linear", settleCoin="USDT")
            if response['retCode'] == 0 and response['result']['list']:
                open_positions = [item['symbol'] for item in response['result']['list'] if float(item.get('size', 0)) > 0]
                if open_positions: logger.info(f"Current open positions: {open_positions}")
                return open_positions
            return []
        except Exception as e:
            logger.error(f"Exception fetching open positions: {e}", exc_info=True)
            return []

    def get_instrument_rules(self, symbol):
        if symbol in self.instrument_rules: return self.instrument_rules[symbol]
        try:
            response = self.session.get_instruments_info(category="linear", symbol=symbol)
            if response['retCode'] == 0 and response['result']['list']:
                rules = response['result']['list'][0]
                self.instrument_rules[symbol] = {'qtyStep': float(rules['lotSizeFilter']['qtyStep']), 'minOrderQty': float(rules['lotSizeFilter']['minOrderQty']), 'tickSize': float(rules['priceFilter']['tickSize'])}
                return self.instrument_rules[symbol]
            return None
        except Exception as e:
            logger.error(f"Exception fetching instrument rules for {symbol}: {e}", exc_info=True)
            return None

    def format_qty(self, qty, rules):
        qty_step = rules['qtyStep']
        decimals = 0
        if '.' in str(qty_step): decimals = len(str(qty_step).split('.')[1])
        adjusted_qty = math.floor(qty / qty_step) * qty_step
        return f"{adjusted_qty:.{decimals}f}"

    def format_price(self, price, rules):
        tick_size = rules['tickSize']
        decimals = 0
        if '.' in str(tick_size): decimals = len(str(tick_size).split('.')[1])
        adjusted_price = round(price / tick_size) * tick_size
        return f"{adjusted_price:.{decimals}f}"

    def get_margin_balance(self):
        try:
            response = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            if response['retCode'] == 0 and response['result']['list']:
                account_info = response['result']['list'][0]
                balance_str = account_info.get('totalMarginBalance') or account_info.get('walletBalance') or account_info.get('totalEquity')
                if balance_str: return float(balance_str)
            return None
        except Exception as e:
            logger.error(f"Exception fetching margin balance: {e}", exc_info=True)
            return None

    def set_leverage_if_needed(self, symbol):
        try:
            self.session.set_leverage(category="linear", symbol=symbol, buyLeverage=str(self.leverage), sellLeverage=str(self.leverage))
            return True
        except Exception as e:
            if "leverage not modified" in str(e).lower(): return True
            logger.error(f"Failed to set leverage for {symbol}: {e}", exc_info=True)
            return False

    def place_order(self, signal):
        symbol = signal['par']
        logger.info(f"Processing signal for {symbol} from strategy {signal['strategy_name']}.")
        
        rules = self.get_instrument_rules(symbol)
        if not rules: return f"❌ *ERRO DE ORDEM ({symbol}):* Não foi possível obter as regras do par."

        if not self.set_leverage_if_needed(symbol): return f"❌ *ERRO DE ORDEM ({symbol}):* Falha ao configurar alavancagem."
        
        balance = self.get_margin_balance()
        if balance is None or balance <= 1: return f"❌ *ERRO DE ORDEM ({symbol}):* Saldo insuficiente ou inválido ({balance} USDT)."

        entry_price = float(signal['preco_atual'])
        sl_price = float(signal['stop_loss'])
        tp_price = float(signal['take_profit'])
        
        order_cost = balance * self.risk_percent
        notional_value = order_cost * self.leverage
        qty_in_token = notional_value / entry_price
        
        formatted_qty = self.format_qty(qty_in_token, rules)
        if float(formatted_qty) < rules['minOrderQty']: return f"ℹ️ *ORDEM IGNORADA ({symbol}):* Qtd calculada ({formatted_qty}) abaixo da mínima ({rules['minOrderQty']})."

        sl_price_formatted = self.format_price(sl_price, rules)
        
        # --- TRAVA DE SEGURANÇA FINAL ---
        if not (float(sl_price_formatted) > 0 and float(sl_price_formatted) < entry_price):
            msg = f"❌ *ORDEM REJEITADA ({symbol}):* Stop Loss inválido após formatação.\nMotivo: SL calculado (`{sl_price_formatted}`) é zero ou maior que o preço de entrada (`{entry_price}`). A ordem não foi enviada."
            logger.error(msg)
            return msg

        order_params = {"category": "linear", "symbol": symbol, "side": "Buy", "orderType": "Market", "qty": formatted_qty, "stopLoss": sl_price_formatted}
        log_msg = f"Placing REAL order for {symbol}: Qty: {formatted_qty}, Margin: {order_cost:.2f}, SL: {sl_price_formatted}"

        if tp_price > 0:
            tp_price_formatted = self.format_price(tp_price, rules)
            order_params["takeProfit"] = tp_price_formatted
            order_params["tpslMode"] = "Full"
            log_msg += f", TP: {tp_price_formatted}"
        else:
            log_msg += " (Dynamic TP)"

        logger.info(log_msg)

        try:
            response = self.session.place_order(**order_params)
            if response['retCode'] == 0:
                order_id = response['result']['orderId']
                log_trade({'strategy': signal.get('strategy_name', 'N/A'), 'pair': symbol, 'direction': 'Long', 'entry_price': entry_price, 'size_usdt': f"{notional_value:.2f}", 'pnl_usdt': 'N/A', 'result': 'OPEN', 'exit_price': 'N/A', 'close_reason': 'N/A'})
                
                tp_text = f"`{self.format_price(tp_price, rules)}`" if tp_price > 0 else "*Dinâmico (RSI ≥ 70)*"
                msg = (f"✅ *ORDEM EXECUTADA: {symbol}*\n\n"
                       f"Direção: *Compra*\n"
                       f"Margem Usada: *{order_cost:.2f} USDT*\n"
                       f"Take Profit: {tp_text}\n"
                       f"Stop Loss: `{sl_price_formatted}`\n"
                       f"ID da Ordem: `{order_id}`")
                logger.info(f"SUCCESS: Order for {symbol} placed. ID: {order_id}")
                return msg
            else:
                logger.error(f"API Error placing order for {symbol}: {response['retMsg']}")
                return f"❌ *FALHA DE API ({symbol}):* A ordem não foi colocada.\nMotivo: `{response['retMsg']}`"
        except Exception as e:
            logger.error(f"Exception placing order for {symbol}: {e}", exc_info=True)
            return f"❌ *ERRO CRÍTICO ({symbol}):* `{str(e)}`"

    def close_position(self, symbol, side):
        try:
            response = self.session.get_positions(category="linear", symbol=symbol)
            if response['retCode'] == 0 and response['result']['list']:
                position_size = ""
                for pos in response['result']['list']:
                    if pos['symbol'] == symbol and pos['side'] == side:
                        position_size = pos['size']
                        break
                
                if position_size and float(position_size) > 0:
                    logger.info(f"Attempting to close {side} position for {symbol} of size {position_size}...")
                    close_side = "Sell" if side == "Buy" else "Buy"
                    
                    close_response = self.session.place_order(category="linear", symbol=symbol, side=close_side, orderType="Market", qty=position_size, reduceOnly=True)

                    if close_response['retCode'] == 0:
                        msg = f"✅ *POSIÇÃO FECHADA (TP Dinâmico): {symbol}*\nMotivo: RSI atingiu a zona de sobrecompra."
                        logger.info(f"SUCCESS: Position for {symbol} closed successfully.")
                        return msg
                    else:
                        logger.error(f"API Error closing position for {symbol}: {close_response['retMsg']}")
                        return f"❌ *FALHA AO FECHAR (TP Dinâmico): {symbol}*\n`{close_response['retMsg']}`"
            return None
        except Exception as e:
            logger.error(f"Exception closing position for {symbol}: {e}", exc_info=True)
            return f"❌ *ERRO CRÍTICO AO FECHAR (TP Dinâmico): {symbol}*\n`{str(e)}`"
