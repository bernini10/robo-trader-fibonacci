# src/bybit_executor.py (Versão 2.0 - MODO PRODUÇÃO)

from pybit.unified_trading import HTTP
from src.config import settings
from src.utils import logger, log_trade
import math

class BybitExecutor:
    def __init__(self):
        logger.info("Initializing Bybit Executor...")
        self.session = HTTP(
            api_key=settings.bybit_api_key,
            api_secret=settings.bybit_api_secret,
            testnet=False
        )
        self.leverage = str(settings.leverage)
        self.risk_percent = settings.risk_per_trade / 100
        logger.warning("Bybit Executor initialized in PRODUCTION MODE. REAL TRADES WILL BE EXECUTED.")

    def get_margin_balance(self):
        """Busca o saldo de margem (marginBalance) da conta de derivativos (Unified Trading)."""
        try:
            response = self.session.get_wallet_balance(accountType="UNIFIED")
            if response['retCode'] == 0:
                # Encontra a moeda USDT na lista de balanços
                for coin in response['result']['list']:
                    if coin['coin'] == 'USDT':
                        balance = float(coin['marginBalance'])
                        logger.info(f"Successfully fetched margin balance: {balance:.2f} USDT")
                        return balance
                logger.warning("USDT not found in wallet balance list.")
                return None
            else:
                logger.error(f"API Error fetching balance: {response['retMsg']}")
                return None
        except Exception as e:
            logger.error(f"Exception fetching margin balance: {e}", exc_info=True)
            return None

    def set_leverage_if_needed(self, symbol):
        """Verifica e ajusta a alavancagem somente se for diferente da desejada."""
        try:
            # Bybit requer que a alavancagem seja definida para cada par
            self.session.set_leverage(category="linear", symbol=symbol, buyLeverage=self.leverage, sellLeverage=self.leverage)
            logger.info(f"Leverage for {symbol} set to {self.leverage}x.")
            return True
        except Exception as e:
            # A API da Bybit retorna erro se a alavancagem já estiver no valor desejado.
            # Verificamos se a mensagem de erro confirma isso.
            if "leverage not modified" in str(e).lower():
                logger.info(f"Leverage for {symbol} is already set to {self.leverage}x. No changes needed.")
                return True
            else:
                logger.error(f"Failed to set leverage for {symbol}: {e}", exc_info=True)
                return False

    def place_order(self, signal):
        """Calcula o tamanho da posição e coloca uma ordem de mercado real."""
        logger.info(f"Processing signal for {signal['par']} in PRODUCTION MODE.")
        
        symbol = signal['par']
        
        if not self.set_leverage_if_needed(symbol):
            return
        
        balance = self.get_margin_balance()
        if balance is None or balance <= 0:
            logger.error("Cannot place order: Invalid or zero balance.")
            return

        risk_amount_usdt = balance * self.risk_percent
        entry_price = float(signal['preco_atual'])
        sl_price = float(signal['stop_loss'])
        
        if entry_price <= sl_price:
            logger.error(f"Invalid signal for {symbol}: Stop loss ({sl_price}) must be below entry price ({entry_price}) for a long position.")
            return

        sl_distance_percent = (entry_price - sl_price) / entry_price
        if sl_distance_percent <= 0:
             logger.error(f"Invalid stop loss distance for {symbol}. Cannot calculate position size.")
             return

        position_size_usdt = risk_amount_usdt / sl_distance_percent
        order_size_usdt = round(position_size_usdt, 2)

        if order_size_usdt < 1.0: # Validação de valor mínimo da Bybit
            logger.warning(f"Order for {symbol} skipped. Calculated order size ({order_size_usdt} USDT) is below the minimum of 1 USDT.")
            return

        sl_price_rounded = round(sl_price, 1)
        tp_price_rounded = round(float(signal['take_profit']), 1)

        logger.info(f"Placing REAL order for {symbol}:")
        logger.info(f"  - Size: {order_size_usdt:.2f} USDT")
        logger.info(f"  - Stop Loss: {sl_price_rounded}")
        logger.info(f"  - Take Profit: {tp_price_rounded}")

        try:
            response = self.session.place_order(
                category="linear",
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=str(order_size_usdt), # Qtd em valor de USDT
                isLeverage=1, # Indica que a qty é em USDT
                takeProfit=str(tp_price_rounded),
                stopLoss=str(sl_price_rounded),
                tpslMode="Full" # 'Full' para TP/SL na posição inteira, 'Partial' para parciais
            )

            if response['retCode'] == 0:
                order_id = response['result']['orderId']
                logger.info(f"SUCCESS: Order for {symbol} placed successfully. Order ID: {order_id}")
                # Aqui iniciaremos o monitoramento da ordem no futuro
            else:
                logger.error(f"API Error placing order for {symbol}: {response['retMsg']}")

        except Exception as e:
            logger.error(f"Exception placing order for {symbol}: {e}", exc_info=True)

