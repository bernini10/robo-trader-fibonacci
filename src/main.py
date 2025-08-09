# src/main.py (Versão 19.0 - Correção Crítica de TP Dinâmico e Gestão de Saldo)

import asyncio
import telegram
from telegram import constants
from binance.client import Client
import pandas as pd
import pandas_ta as ta
from datetime import datetime

from src.config import settings
from src.utils import logger, log_trade
from src.bybit_executor import BybitExecutor
from src.estrategias import analisar_momentum_pullback, analisar_fibonacci

sinais_pendentes = {}
posicoes_momentum = {} # Novo: Dicionário para rastrear posições que precisam de TP dinâmico

async def enviar_alerta_telegram(bot, chat_id, mensagem):
    try:
        await bot.send_message(chat_id=chat_id, text=mensagem, parse_mode=constants.ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")

# --- NOVA FUNÇÃO CRÍTICA ---
async def monitorar_tp_dinamico(binance_client, executor, bot):
    if not posicoes_momentum:
        return

    logger.info(f"--- Monitorando {len(posicoes_momentum)} posições para TP Dinâmico (RSI >= 70) ---")
    # Itera sobre uma cópia para poder modificar o dicionário original
    for par, info in list(posicoes_momentum.items()):
        try:
            df_5m = pd.DataFrame(binance_client.get_klines(symbol=par, interval='5m', limit=20))
            if df_5m.empty:
                continue
            
            df_5m.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
            for col in ['open', 'high', 'low', 'close']: df_5m[col] = pd.to_numeric(df_5m[col])
            
            df_5m.ta.rsi(length=14, append=True)
            rsi_atual = df_5m['RSI_14'].iloc[-1]
            logger.info(f"Monitorando TP para {par}: RSI atual é {rsi_atual:.2f}")

            if rsi_atual >= 70:
                logger.warning(f"GATILHO DE TAKE PROFIT DINÂMICO PARA {par}! RSI({rsi_atual:.2f}) >= 70. Fechando posição.")
                resultado_fechamento = executor.close_position(par, "Buy") # Assumindo que são posições de compra
                if resultado_fechamento:
                    await enviar_alerta_telegram(bot, settings.telegram_chat_id, resultado_fechamento)
                
                # Remove da lista de monitoramento após a tentativa de fechamento
                del posicoes_momentum[par]

        except Exception as e:
            logger.error(f"Erro ao monitorar TP dinâmico para {par}: {e}")
            # Remove em caso de erro para não ficar em loop
            if par in posicoes_momentum:
                del posicoes_momentum[par]

async def monitorar_sinais_pendentes(binance_client, executor, bot, posicoes_abertas):
    # ... (código desta função permanece o mesmo)
    if not sinais_pendentes:
        return
    logger.info(f"--- Monitorando {len(sinais_pendentes)} sinais pendentes para Crossover de RSI ---")
    for par, info in list(sinais_pendentes.items()):
        try:
            if par in posicoes_abertas:
                logger.info(f"Sinal pendente para {par} removido, pois a posição já existe.")
                del sinais_pendentes[par]
                continue
            if (datetime.now() - info['timestamp']).total_seconds() > 900:
                logger.info(f"Sinal pendente para {par} expirou. Removendo.")
                del sinais_pendentes[par]
                continue
            df_5m = pd.DataFrame(binance_client.get_klines(symbol=par, interval='5m', limit=100))
            if df_5m.empty: continue
            df_5m.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
            for col in ['open', 'high', 'low', 'close']: df_5m[col] = pd.to_numeric(df_5m[col])
            df_5m.ta.rsi(length=14, append=True)
            df_5m.ta.atr(length=14, append=True)
            rsi_atual = df_5m['RSI_14'].iloc[-1]
            logger.info(f"Monitorando {par}: RSI atual é {rsi_atual:.2f}")
            if rsi_atual > 30:
                logger.warning(f"GATILHO DE ENTRADA POR CROSSOVER DE RSI PARA {par}! RSI({rsi_atual:.2f}) > 30. Preparando ordem.")
                preco_atual = df_5m['close'].iloc[-1]
                atr_atual = df_5m['ATRr_14'].iloc[-1]
                stop_loss = preco_atual * 0.98
                sl_mode = "Fallback 2%"
                if pd.notna(atr_atual) and atr_atual > 0:
                    sl_candidato = preco_atual - (1.5 * atr_atual)
                    if sl_candidato > 0 and sl_candidato < preco_atual:
                        stop_loss = sl_candidato
                        sl_mode = "ATR (TP Dinâmico RSI)"
                sinal_final = {'strategy_name': 'Momentum Crossover', 'par': par, 'preco_atual': preco_atual, 'stop_loss': stop_loss, 'take_profit': 0, 'sl_mode': sl_mode}
                resultado_ordem = executor.place_order(sinal_final)
                if resultado_ordem and "✅" in resultado_ordem:
                    posicoes_momentum[par] = True # Adiciona à lista de monitoramento de TP
                if resultado_ordem:
                    cabecalho = f"*[Estratégia: {sinal_final['strategy_name']}]*\n"
                    if sinal_final.get('sl_mode'): cabecalho += f"Método SL/TP: *{sinal_final['sl_mode']}*\n"
                    alerta_final = cabecalho + resultado_ordem
                    await enviar_alerta_telegram(bot, settings.telegram_chat_id, alerta_final)
                del sinais_pendentes[par]
        except Exception as e:
            logger.error(f"Erro ao monitorar sinal pendente {par}: {e}")
            if par in sinais_pendentes:
                del sinais_pendentes[par]

async def main_loop():
    logger.info("Initializing main loop...")
    binance_client = Client()
    bot = telegram.Bot(token=settings.telegram_token)
    executor = BybitExecutor()
    await enviar_alerta_telegram(bot, settings.telegram_chat_id, "✅ *Robô Multi-Estratégia Iniciado (v19.0)*\n- Monitoramento de TP Dinâmico ATIVADO.")
    while True:
        try:
            logger.info("--- Starting new analysis cycle ---")
            posicoes_abertas = executor.get_open_positions()
            
            # Limpa posições que não estão mais abertas da nossa lista de monitoramento
            for par in list(posicoes_momentum.keys()):
                if par not in posicoes_abertas:
                    del posicoes_momentum[par]

            # 1. Monitorar posições abertas para fechar no TP de RSI 70
            await monitorar_tp_dinamico(binance_client, executor, bot)
            
            # 2. Monitorar sinais pendentes para abrir novas posições
            await monitorar_sinais_pendentes(binance_client, executor, bot, posicoes_abertas)

            # 3. Buscar novos candidatos para a lista de espera
            candidatos_momentum = analisar_momentum_pullback(binance_client, executor.session)
            for candidato in candidatos_momentum:
                if candidato['par'] not in posicoes_abertas and candidato['par'] not in sinais_pendentes:
                    sinais_pendentes[candidato['par']] = {'timestamp': datetime.now()}

            # 4. Executar a estratégia de Fibonacci
            sinais_fibonacci = analisar_fibonacci(binance_client, executor.session)
            if sinais_fibonacci:
                posicoes_abertas_fib = executor.get_open_positions()
                for sinal in sinais_fibonacci:
                    if sinal['par'] not in posicoes_abertas_fib:
                        resultado_ordem = executor.place_order(sinal)
                        if resultado_ordem and "✅" in resultado_ordem:
                            await asyncio.sleep(2) # Pausa para evitar erro de saldo
                        if resultado_ordem:
                            cabecalho = f"*[Estratégia: {sinal['strategy_name']}]*\n"
                            if sinal.get('confianca'): cabecalho += f"Confiança: *{sinal['confianca']}*\n"
                            if sinal.get('sl_mode'): cabecalho += f"Método SL/TP: *{sinal['sl_mode']}*\n"
                            alerta_final = cabecalho + resultado_ordem
                            await enviar_alerta_telegram(bot, settings.telegram_chat_id, alerta_final)
                        await asyncio.sleep(1)

            logger.info(f"Cycle finished. Waiting for 1 minute before next cycle.")
            await asyncio.sleep(60)
        except Exception as e:
            logger.critical(f"CRITICAL ERROR in main loop: {e}", exc_info=True)
            await enviar_alerta_telegram(bot, settings.telegram_chat_id, f"🔥 *ERRO CRÍTICO NO ROBÔ* 🔥\n`{e}`\nO robô tentará reiniciar o ciclo em 1 minuto.")
            await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Robot shutdown requested. Exiting.")
