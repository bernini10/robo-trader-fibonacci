# src/main.py (Versão 13.0 - Arquitetura com Executor)

import asyncio
import telegram
from telegram import constants
from binance.client import Client

# Importações da nova estrutura
from src.config import settings
from src.utils import logger
from src.bybit_executor import BybitExecutor
from src.estrategias import analisar_momentum_pullback # Por enquanto, só esta estratégia será executada

async def enviar_alerta_telegram(bot, chat_id, mensagem):
    try:
        await bot.send_message(chat_id=chat_id, text=mensagem, parse_mode=constants.ParseMode.MARKDOWN)
        logger.info(f"Telegram message sent: \"{mensagem.splitlines()[0]}...\"")
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")

async def main_loop():
    logger.info("Initializing main loop...")
    
    # Inicializa os clientes
    binance_client = Client()
    bot = telegram.Bot(token=settings.telegram_token)
    executor = BybitExecutor() # Nosso executor de ordens (em modo simulação)

    await enviar_alerta_telegram(bot, settings.telegram_chat_id, "✅ *Robô Trader Iniciado (MODO PRODUÇÃO)*\nMonitorando o mercado para ordens reais.")

    while True:
        try:
            logger.info("--- Starting new analysis cycle ---")
            
            # 1. Análise de Momentum (única estratégia ativa para execução por enquanto)
            sinais_momentum = analisar_momentum_pullback(binance_client)
            
            if not sinais_momentum:
                logger.info("No actionable signals found in this cycle.")
            else:
                logger.info(f"Found {len(sinais_momentum)} signal(s). Processing with executor...")
                for sinal in sinais_momentum:
                    # 2. Envia o sinal para o executor
                    await enviar_alerta_telegram(bot, settings.telegram_chat_id, f"🚨 *Sinal Encontrado:* `{sinal['par']}`\n- RSI: {sinal['rsi_atual']:.2f}\n- Variação 24h: +{sinal['variacao_24h']:.2f}%\n\n*Iniciando simulação de ordem...*")
                    executor.place_order(sinal)
                    await asyncio.sleep(1) # Evita flood de API

            logger.info(f"Cycle finished. Waiting for 1 minute before next cycle.")
            await asyncio.sleep(60) # Roda a cada 1 minuto

        except Exception as e:
            logger.critical(f"CRITICAL ERROR in main loop: {e}", exc_info=True)
            await enviar_alerta_telegram(bot, settings.telegram_chat_id, f"🔥 *ERRO CRÍTICO NO ROBÔ* 🔥\n`{e}`\nO robô tentará reiniciar o ciclo em 1 minuto.")
            await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Robot shutdown requested. Exiting.")
