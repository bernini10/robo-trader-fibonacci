# src/main.py (Versão 18.2 - Correção de Duplicação com Verificação Dupla)

import asyncio
import telegram
from telegram import constants
from binance.client import Client
import pandas as pd
import pandas_ta as ta
from datetime import datetime

from src.config import settings
from src.utils import logger
from src.bybit_executor import BybitExecutor
from src.estrategias import analisar_momentum_pullback, analisar_fibonacci

sinais_pendentes = {}

async def enviar_alerta_telegram(bot, chat_id, mensagem):
    try:
        await bot.send_message(chat_id=chat_id, text=mensagem, parse_mode=constants.ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")

async def monitorar_sinais_pendentes(binance_client, executor, bot):
    if not sinais_pendentes:
        return

    logger.info(f"--- Monitorando {len(sinais_pendentes)} sinais pendentes para Crossover de RSI ---")
    # Obter a lista de posições mais recente ANTES de começar a iterar
    posicoes_atuais = executor.get_open_positions()

    for par, info in list(sinais_pendentes.items()):
        try:
            # Se uma posição já foi aberta para este par, remova-o dos pendentes e pule.
            if par in posicoes_atuais:
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

    await enviar_alerta_telegram(bot, settings.telegram_chat_id, "✅ *Robô Multi-Estratégia Iniciado (v18.2)*\n- Lógica Anti-Duplicação Reforçada.")

    while True:
        try:
            logger.info("--- Starting new analysis cycle ---")
            
            await monitorar_sinais_pendentes(binance_client, executor, bot)

            posicoes_abertas = executor.get_open_positions()
            
            candidatos_momentum = analisar_momentum_pullback(binance_client, executor.session)
            for candidato in candidatos_momentum:
                if candidato['par'] not in posicoes_abertas and candidato['par'] not in sinais_pendentes:
                    sinais_pendentes[candidato['par']] = {'timestamp': datetime.now()}

            sinais_fibonacci = analisar_fibonacci(binance_client, executor.session)
            if sinais_fibonacci:
                posicoes_abertas_fib = executor.get_open_positions() # Re-check for safety
                for sinal in sinais_fibonacci:
                    if sinal['par'] not in posicoes_abertas_fib:
                        resultado_ordem = executor.place_order(sinal)
                        if resultado_ordem:
                            await asyncio.sleep(2) # Dê tempo para a posição ser registrada
                            posicoes_abertas_fib = executor.get_open_positions()
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
