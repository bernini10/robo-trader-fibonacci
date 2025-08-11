# src/bybit_executor.py (Versão 21.0 - TP/SL sem interferência de alavancagem)

import time
from pybit.unified_trading import HTTP
from src.config import settings
from src.utils import logger

class BybitExecutor:
    def __init__(self):
        logger.info("Initializing Bybit Executor...")
        self.session = HTTP(
            api_key=settings.bybit_api_key, 
            api_secret=settings.bybit_api_secret, 
            testnet=False
        )
        self.risk_per_trade = settings.risk_per_trade / 100
        self.leverage = settings.leverage
        logger.warning(f"Bybit Executor initialized in PRODUCTION MODE for Unified Trading Account (Cross Margin only).")

    def get_margin_balance(self):
        """Obtém o saldo da margem unificada"""
        try:
            response = self.session.get_wallet_balance(accountType="UNIFIED")
            if response['retCode'] == 0:
                for coin in response['result']['list'][0]['coin']:
                    if coin['coin'] == 'USDT':
                        return float(coin['walletBalance'])
            return None
        except Exception as e:
            logger.error(f"Erro ao obter saldo: {e}")
            return None

    def get_open_positions(self):
        """Obtém posições abertas"""
        try:
            response = self.session.get_positions(category="linear", settleCoin="USDT")
            if response['retCode'] == 0:
                positions = []
                for pos in response['result']['list']:
                    if float(pos['size']) > 0:
                        positions.append(pos['symbol'])
                logger.info(f"Current open positions: {positions}")
                return positions
            return []
        except Exception as e:
            logger.error(f"Erro ao obter posições: {e}")
            return []

    def place_order(self, sinal):
        """Executa ordem com TP/SL corretos (sem interferência de alavancagem)"""
        try:
            par = sinal['par']
            preco_atual = sinal['preco_atual']
            
            # Obter saldo disponível
            saldo = self.get_margin_balance()
            if not saldo:
                return "❌ ERRO: Não foi possível obter saldo da conta."
            
            # Calcular quantidade baseada no risco
            valor_risco = saldo * self.risk_per_trade
            
            # Calcular stop loss distance para determinar quantidade
            if 'stop_loss' in sinal and sinal['stop_loss'] > 0:
                stop_distance = abs(preco_atual - sinal['stop_loss'])
                if stop_distance > 0:
                    # Quantidade baseada no risco real (sem alavancagem)
                    quantidade_base = valor_risco / stop_distance
                    # Aplicar alavancagem apenas na quantidade
                    quantidade = quantidade_base * self.leverage
                else:
                    quantidade = (valor_risco * self.leverage) / preco_atual
            else:
                quantidade = (valor_risco * self.leverage) / preco_atual
            
            # Arredondar quantidade para precisão adequada
            if quantidade < 1:
                quantidade = round(quantidade, 3)
            else:
                quantidade = round(quantidade, 1)
            
            if quantidade <= 0:
                return "❌ ERRO: Quantidade calculada inválida."
            
            # Definir alavancagem para o par
            try:
                self.session.set_leverage(
                    category="linear",
                    symbol=par,
                    buyLeverage=str(self.leverage),
                    sellLeverage=str(self.leverage)
                )
            except Exception as e:
                logger.warning(f"Aviso ao definir alavancagem para {par}: {e}")
            
            # CORREÇÃO: TP/SL sem interferência da alavancagem
            # Os preços já vêm calculados corretamente do main.py
            take_profit_price = None
            stop_loss_price = None
            
            if 'take_profit' in sinal and sinal['take_profit'] > 0:
                # TP já calculado corretamente (5% = preco_atual * 1.05)
                take_profit_price = round(sinal['take_profit'], 6)
            
            if 'stop_loss' in sinal and sinal['stop_loss'] > 0:
                # SL já calculado corretamente (2.5% = preco_atual * 0.975)
                stop_loss_price = round(sinal['stop_loss'], 6)
            
            # Executar ordem principal
            order_response = self.session.place_order(
                category="linear",
                symbol=par,
                side="Buy",
                orderType="Market",
                qty=str(quantidade),
                timeInForce="IOC"
            )
            
            if order_response['retCode'] != 0:
                return f"❌ ERRO na ordem principal: {order_response['retMsg']}"
            
            order_id = order_response['result']['orderId']
            
            # Aguardar execução da ordem principal
            time.sleep(2)
            
            # Verificar se a ordem foi executada
            order_status = self.session.get_open_orders(category="linear", symbol=par)
            if order_status['retCode'] == 0:
                open_orders = [o for o in order_status['result']['list'] if o['orderId'] == order_id]
                if open_orders:
                    return f"❌ ERRO: Ordem principal não foi executada completamente."
            
            # Configurar TP/SL se especificados
            tp_sl_results = []
            
            if take_profit_price:
                try:
                    tp_response = self.session.place_order(
                        category="linear",
                        symbol=par,
                        side="Sell",
                        orderType="Limit",
                        qty=str(quantidade),
                        price=str(take_profit_price),
                        timeInForce="GTC",
                        reduceOnly=True
                    )
                    if tp_response['retCode'] == 0:
                        tp_sl_results.append(f"TP: {take_profit_price}")
                    else:
                        logger.warning(f"Falha ao definir TP: {tp_response['retMsg']}")
                except Exception as e:
                    logger.warning(f"Erro ao definir TP: {e}")
            
            if stop_loss_price:
                try:
                    sl_response = self.session.place_order(
                        category="linear",
                        symbol=par,
                        side="Sell",
                        orderType="StopMarket",
                        qty=str(quantidade),
                        stopPrice=str(stop_loss_price),
                        timeInForce="GTC",
                        reduceOnly=True
                    )
                    if sl_response['retCode'] == 0:
                        tp_sl_results.append(f"SL: {stop_loss_price}")
                    else:
                        logger.warning(f"Falha ao definir SL: {sl_response['retMsg']}")
                except Exception as e:
                    logger.warning(f"Erro ao definir SL: {e}")
            
            # Resultado final
            resultado = f"✅ *ORDEM EXECUTADA*\n"
            resultado += f"Par: *{par}*\n"
            resultado += f"Quantidade: *{quantidade}*\n"
            resultado += f"Preço: *{preco_atual:.6f}*\n"
            resultado += f"Alavancagem: *{self.leverage}x*\n"
            
            if tp_sl_results:
                resultado += f"TP/SL: *{' | '.join(tp_sl_results)}*\n"
            
            # Calcular valor da posição
            valor_posicao = quantidade * preco_atual
            resultado += f"Valor da Posição: *${valor_posicao:.2f}*"
            
            logger.info(f"Ordem executada com sucesso: {par}")
            return resultado
            
        except Exception as e:
            logger.error(f"Erro ao executar ordem: {e}")
            return f"❌ ERRO na execução: {str(e)}"

    def close_position(self, symbol, side):
        """Fecha uma posição específica"""
        try:
            # Obter informações da posição
            positions = self.session.get_positions(category="linear", symbol=symbol)
            if positions['retCode'] != 0:
                return f"❌ Erro ao obter posição: {positions['retMsg']}"
            
            position_info = None
            for pos in positions['result']['list']:
                if pos['symbol'] == symbol and float(pos['size']) > 0:
                    position_info = pos
                    break
            
            if not position_info:
                return f"❌ Posição não encontrada para {symbol}"
            
            quantidade = position_info['size']
            
            # Cancelar ordens TP/SL pendentes
            try:
                self.session.cancel_all_orders(category="linear", symbol=symbol)
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Aviso ao cancelar ordens pendentes: {e}")
            
            # Fechar posição
            close_response = self.session.place_order(
                category="linear",
                symbol=symbol,
                side="Sell",  # Sempre Sell para fechar posição Buy
                orderType="Market",
                qty=quantidade,
                timeInForce="IOC",
                reduceOnly=True
            )
            
            if close_response['retCode'] == 0:
                return f"✅ Posição {symbol} fechada com sucesso. Quantidade: {quantidade}"
            else:
                return f"❌ Erro ao fechar posição: {close_response['retMsg']}"
                
        except Exception as e:
            logger.error(f"Erro ao fechar posição {symbol}: {e}")
            return f"❌ Erro ao fechar posição: {str(e)}"

    def get_position_info(self, symbol):
        """Obtém informações detalhadas de uma posição"""
        try:
            response = self.session.get_positions(category="linear", symbol=symbol)
            if response['retCode'] == 0:
                for pos in response['result']['list']:
                    if pos['symbol'] == symbol and float(pos['size']) > 0:
                        return {
                            'symbol': pos['symbol'],
                            'size': float(pos['size']),
                            'side': pos['side'],
                            'avgPrice': float(pos['avgPrice']),
                            'markPrice': float(pos['markPrice']),
                            'unrealisedPnl': float(pos['unrealisedPnl']),
                            'percentage': float(pos['unrealisedPnl']) / float(pos['positionValue']) * 100 if float(pos['positionValue']) > 0 else 0
                        }
            return None
        except Exception as e:
            logger.error(f"Erro ao obter informações da posição {symbol}: {e}")
            return None
