# src/main.py (Versão 21.0 - TP/SL Fixo para 5min + Timeframes Escalonados)

import asyncio
import telegram
from telegram import constants
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta

from src.config import settings
from src.utils import logger, log_trade
from src.bybit_executor import BybitExecutor
from src.estrategias import analisar_momentum_pullback, analisar_fibonacci

# === ESTRUTURAS DE DADOS GLOBAIS ===
sinais_pendentes_5m = {}
sinais_pendentes_15m = {}
sinais_pendentes_4h = {}
posicoes_momentum = {}
historico_operacoes = {}

class GestorDrawdown:
    def __init__(self, drawdown_maximo=0.15, perdas_consecutivas_max=5):
        self.drawdown_maximo = drawdown_maximo
        self.perdas_consecutivas_max = perdas_consecutivas_max
        self.saldo_inicial = None
        self.saldo_pico = None
        self.perdas_consecutivas = 0
        self.bot_pausado = False
        
    def atualizar_saldo(self, saldo_atual):
        if self.saldo_inicial is None:
            self.saldo_inicial = saldo_atual
            self.saldo_pico = saldo_atual
            
        if saldo_atual > self.saldo_pico:
            self.saldo_pico = saldo_atual
            self.perdas_consecutivas = 0
            
        drawdown_atual = (self.saldo_pico - saldo_atual) / self.saldo_pico
        
        if drawdown_atual > self.drawdown_maximo:
            self.bot_pausado = True
            return f"🚨 ALERTA CRÍTICO: Drawdown de {drawdown_atual*100:.1f}% excede limite de {self.drawdown_maximo*100:.1f}%! Bot pausado."
            
        return None
        
    def registrar_operacao(self, resultado):
        if resultado == "perda":
            self.perdas_consecutivas += 1
            if self.perdas_consecutivas >= self.perdas_consecutivas_max:
                self.bot_pausado = True
                return f"🚨 ALERTA: {self.perdas_consecutivas} perdas consecutivas! Bot pausado."
        else:
            self.perdas_consecutivas = 0
        return None
        
    def pode_operar(self):
        return not self.bot_pausado

gestor_drawdown = GestorDrawdown()

async def enviar_alerta_telegram(bot, chat_id, mensagem):
    try:
        await bot.send_message(chat_id=chat_id, text=mensagem, parse_mode=constants.ParseMode.MARKDOWN)
        logger.info(f"Mensagem enviada para Telegram: {mensagem[:100]}...")
    except Exception as e:
        logger.error(f"Falha ao enviar mensagem Telegram: {e}")

def obter_klines_bybit_para_rsi(client, symbol, interval='5', limit=50):
    """Função auxiliar para obter klines da Bybit para cálculo de RSI"""
    try:
        response = client.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        if response['retCode'] == 0 and response['result']['list']:
            klines = []
            for kline in response['result']['list']:
                klines.append([
                    int(kline[0]),      # timestamp
                    float(kline[1]),    # open
                    float(kline[2]),    # high
                    float(kline[3]),    # low
                    float(kline[4]),    # close
                    float(kline[5]),    # volume
                ])
            # Bybit retorna em ordem decrescente, precisamos inverter
            klines.reverse()
            return pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        else:
            return pd.DataFrame()
    except Exception as e:
        logger.debug(f"Erro ao obter klines para RSI {symbol}: {e}")
        return pd.DataFrame()

def verificar_reset_timeframe(par, primeira_operacao_timestamp):
    """Verifica se deve resetar o ciclo de timeframes"""
    agora = datetime.now()
    tempo_decorrido = agora - primeira_operacao_timestamp
    
    # Reset após 24h
    if tempo_decorrido > timedelta(hours=24):
        return True
        
    # TODO: Implementar verificação de nova alta superior
    return False

def promover_para_proximo_timeframe(par, timeframe_atual):
    """Move um par para o próximo timeframe na sequência"""
    if timeframe_atual == "5m":
        # Move de 5m para 15m
        if par in sinais_pendentes_5m:
            sinais_pendentes_15m[par] = sinais_pendentes_5m[par].copy()
            sinais_pendentes_15m[par]['timeframe'] = '15m'
            sinais_pendentes_15m[par]['promovido_em'] = datetime.now()
            del sinais_pendentes_5m[par]
            logger.info(f"Par {par} promovido de 5m para 15m")
            
    elif timeframe_atual == "15m":
        # Move de 15m para 4h
        if par in sinais_pendentes_15m:
            sinais_pendentes_4h[par] = sinais_pendentes_15m[par].copy()
            sinais_pendentes_4h[par]['timeframe'] = '4h'
            sinais_pendentes_4h[par]['promovido_em'] = datetime.now()
            del sinais_pendentes_15m[par]
            logger.info(f"Par {par} promovido de 15m para 4h")

async def monitorar_tp_dinamico(executor, bot):
    """Monitora TP dinâmico apenas para posições de 15m e 4h"""
    if not posicoes_momentum:
        return
    
    # Filtrar apenas posições que NÃO são de 5m (que têm TP fixo)
    posicoes_tp_dinamico = {par: info for par, info in posicoes_momentum.items() 
                           if info.get('timeframe') != '5m'}
    
    if not posicoes_tp_dinamico:
        return
    
    logger.info(f"--- Monitorando {len(posicoes_tp_dinamico)} posições para TP Dinâmico (RSI >= 70) ---")
    
    for par, info in list(posicoes_tp_dinamico.items()):
        try:
            # Verificar se posição ainda existe na exchange
            posicoes_abertas = executor.get_open_positions()
            posicao_existe = any(pos == par for pos in posicoes_abertas)
            
            if not posicao_existe:
                logger.warning(f"Posição {par} não encontrada na exchange. Removendo do monitoramento.")
                del posicoes_momentum[par]
                continue
            
            # Obter dados de 5min para RSI usando Bybit
            df_5m = obter_klines_bybit_para_rsi(executor.session, par, interval='5', limit=20)
            if df_5m.empty or len(df_5m) < 15:
                continue
            
            # Calcular RSI
            try:
                df_5m.ta.rsi(length=14, append=True)
                rsi_atual = df_5m['RSI_14'].iloc[-1]
            except:
                continue
            
            logger.info(f"Monitorando TP para {par} ({info.get('timeframe', 'N/A')}): RSI atual é {rsi_atual:.2f}")
            
            # Verificar condição de TP dinâmico
            if pd.notna(rsi_atual) and rsi_atual >= 70:
                logger.warning(f"🎯 TP DINÂMICO ATIVADO PARA {par}! RSI: {rsi_atual:.2f}")
                
                resultado_fechamento = executor.close_position(par, "Buy")
                
                if resultado_fechamento and "sucesso" in resultado_fechamento.lower():
                    await enviar_alerta_telegram(bot, settings.telegram_chat_id, 
                        f"🎯 *TP DINÂMICO EXECUTADO*\n{resultado_fechamento}")
                    
                    # Promover para próximo timeframe após TP bem-sucedido
                    timeframe_atual = info.get('timeframe', '5m')
                    promover_para_proximo_timeframe(par, timeframe_atual)
                    
                    del posicoes_momentum[par]
                    logger.info(f"Posição {par} fechada com sucesso e promovida para próximo timeframe.")
                else:
                    logger.error(f"FALHA ao fechar posição {par}. Mantendo no monitoramento.")
                        
        except Exception as e:
            logger.error(f"Erro ao monitorar TP dinâmico para {par}: {e}")

async def monitorar_sinais_timeframe(executor, bot, sinais_dict, timeframe, posicoes_abertas):
    """Monitora sinais de um timeframe específico usando apenas Bybit"""
    if not sinais_dict:
        return
        
    logger.info(f"--- Monitorando {len(sinais_dict)} sinais pendentes em {timeframe} ---")
    
    sinais_para_processar = list(sinais_dict.items())
    
    for par, info in sinais_para_processar:
        try:
            # Verificar se já existe posição
            if par in posicoes_abertas:
                logger.info(f"Sinal {par} ({timeframe}) removido - posição já existe.")
                del sinais_dict[par]
                continue
            
            # Verificar timeout baseado no timeframe
            timeout_map = {'5m': 900, '15m': 1800, '4h': 7200}  # 15min, 30min, 2h
            timeout = timeout_map.get(timeframe, 900)
            
            if (datetime.now() - info['timestamp']).total_seconds() > timeout:
                logger.info(f"Sinal {par} ({timeframe}) expirou após {timeout}s.")
                del sinais_dict[par]
                continue
            
            # Verificar reset de 24h
            if 'primeira_operacao' in info:
                if verificar_reset_timeframe(par, info['primeira_operacao']):
                    logger.info(f"Reset de 24h para {par}. Removendo de todos os timeframes.")
                    # Remover de todos os timeframes
                    sinais_pendentes_5m.pop(par, None)
                    sinais_pendentes_15m.pop(par, None)
                    sinais_pendentes_4h.pop(par, None)
                    continue
                
            # Mapear timeframe para intervalo da Bybit
            interval_map = {'5m': '5', '15m': '15', '4h': '240'}
            interval = interval_map.get(timeframe, '5')
            
            # Obter dados do timeframe apropriado da Bybit
            df = obter_klines_bybit_para_rsi(executor.session, par, interval=interval, limit=50)
            if df.empty or len(df) < 15:
                continue
            
            # Calcular RSI
            try:
                df.ta.rsi(length=14, append=True)
                rsi_atual = df['RSI_14'].iloc[-1]
                rsi_anterior = df['RSI_14'].iloc[-2]
            except:
                continue
            
            logger.info(f"Monitorando {par} ({timeframe}): RSI {rsi_anterior:.2f} → {rsi_atual:.2f}")
            
            # Verificar crossover (saída de sobrevenda)
            crossover_confirmado = (
                pd.notna(rsi_anterior) and pd.notna(rsi_atual) and
                rsi_anterior <= 30 and
                rsi_atual > 30 and
                rsi_atual > rsi_anterior
            )
            
            if crossover_confirmado:
                logger.warning(f"🚀 CROSSOVER CONFIRMADO PARA {par} ({timeframe})! RSI: {rsi_anterior:.2f} → {rsi_atual:.2f}")
                
                if not gestor_drawdown.pode_operar():
                    logger.warning("Bot pausado pelo gestor de drawdown. Operação cancelada.")
                    continue
                
                preco_atual = df['close'].iloc[-1]
                
                # AJUSTE: TP/SL específico por timeframe
                if timeframe == '5m':
                    # PRIMEIRO CICLO: TP fixo 5% e SL fixo 2.5%
                    take_profit = preco_atual * 1.05  # +5%
                    stop_loss = preco_atual * 0.975   # -2.5%
                    sl_mode = f'TP Fixo 5% / SL Fixo 2.5% ({timeframe})'
                    
                    sinal_final = {
                        'strategy_name': f'Momentum_Crossover_{timeframe}', 
                        'par': par, 
                        'preco_atual': preco_atual, 
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,  # TP fixo para 5m
                        'sl_mode': sl_mode,
                        'timeframe': timeframe
                    }
                    
                else:
                    # OUTROS CICLOS: TP dinâmico (RSI >= 70) e SL baseado em ATR
                    try:
                        df.ta.atr(length=14, append=True)
                        atr = df['ATR_14'].iloc[-1]
                        stop_loss = preco_atual - (atr * 2) if pd.notna(atr) else preco_atual * 0.98
                    except:
                        stop_loss = preco_atual * 0.98
                    
                    sinal_final = {
                        'strategy_name': f'Momentum_Crossover_{timeframe}', 
                        'par': par, 
                        'preco_atual': preco_atual, 
                        'stop_loss': stop_loss,
                        'sl_mode': f'ATR 2x ({timeframe})',
                        'timeframe': timeframe,
                        'take_profit': 0  # TP dinâmico para 15m e 4h
                    }
                
                resultado_ordem = executor.place_order(sinal_final)
                
                if resultado_ordem and "✅" in resultado_ordem:
                    # Registrar posição para monitoramento
                    posicoes_momentum[par] = {
                        'timestamp': datetime.now(),
                        'preco_entrada': preco_atual,
                        'timeframe': timeframe,
                        'tp_tipo': 'fixo' if timeframe == '5m' else 'dinamico'
                    }
                    
                    # Registrar no histórico
                    if par not in historico_operacoes:
                        historico_operacoes[par] = {
                            'primeira_operacao': datetime.now(),
                            'operacoes': []
                        }
                    
                    historico_operacoes[par]['operacoes'].append({
                        'timestamp': datetime.now(),
                        'timeframe': timeframe,
                        'preco': preco_atual
                    })
                    
                    del sinais_dict[par]
                    
                    cabecalho = f"*[Estratégia: {sinal_final['strategy_name']}]*\n"
                    if sinal_final.get('sl_mode'): 
                        cabecalho += f"Método SL/TP: *{sinal_final['sl_mode']}*\n"
                    alerta_final = cabecalho + resultado_ordem
                    await enviar_alerta_telegram(bot, settings.telegram_chat_id, alerta_final)
                    
                    logger.info(f"Ordem executada para {par} ({timeframe})")
                else:
                    logger.error(f"FALHA na execução da ordem para {par} ({timeframe}).")
                        
        except Exception as e:
            logger.error(f"Erro ao monitorar sinal {par} ({timeframe}): {e}")

async def monitorar_tp_dinamico(executor, bot):
    """Monitora TP dinâmico apenas para posições de 15m e 4h"""
    if not posicoes_momentum:
        return
    
    # Filtrar apenas posições que NÃO são de 5m (que têm TP fixo)
    posicoes_tp_dinamico = {par: info for par, info in posicoes_momentum.items() 
                           if info.get('timeframe') != '5m'}
    
    if not posicoes_tp_dinamico:
        return
    
    logger.info(f"--- Monitorando {len(posicoes_tp_dinamico)} posições para TP Dinâmico (RSI >= 70) ---")
    
    for par, info in list(posicoes_tp_dinamico.items()):
        try:
            # Verificar se posição ainda existe na exchange
            posicoes_abertas = executor.get_open_positions()
            posicao_existe = any(pos == par for pos in posicoes_abertas)
            
            if not posicao_existe:
                logger.warning(f"Posição {par} não encontrada na exchange. Removendo do monitoramento.")
                del posicoes_momentum[par]
                continue
            
            # Obter dados de 5min para RSI usando Bybit
            df_5m = obter_klines_bybit_para_rsi(executor.session, par, interval='5', limit=20)
            if df_5m.empty or len(df_5m) < 15:
                continue
            
            # Calcular RSI
            try:
                df_5m.ta.rsi(length=14, append=True)
                rsi_atual = df_5m['RSI_14'].iloc[-1]
            except:
                continue
            
            logger.info(f"Monitorando TP para {par} ({info.get('timeframe', 'N/A')}): RSI atual é {rsi_atual:.2f}")
            
            # Verificar condição de TP dinâmico
            if pd.notna(rsi_atual) and rsi_atual >= 70:
                logger.warning(f"🎯 TP DINÂMICO ATIVADO PARA {par}! RSI: {rsi_atual:.2f}")
                
                resultado_fechamento = executor.close_position(par, "Buy")
                
                if resultado_fechamento and "sucesso" in resultado_fechamento.lower():
                    await enviar_alerta_telegram(bot, settings.telegram_chat_id, 
                        f"🎯 *TP DINÂMICO EXECUTADO*\n{resultado_fechamento}")
                    
                    # Promover para próximo timeframe após TP bem-sucedido
                    timeframe_atual = info.get('timeframe', '5m')
                    promover_para_proximo_timeframe(par, timeframe_atual)
                    
                    del posicoes_momentum[par]
                    logger.info(f"Posição {par} fechada com sucesso e promovida para próximo timeframe.")
                else:
                    logger.error(f"FALHA ao fechar posição {par}. Mantendo no monitoramento.")
                        
        except Exception as e:
            logger.error(f"Erro ao monitorar TP dinâmico para {par}: {e}")

async def monitorar_sinais_timeframe(executor, bot, sinais_dict, timeframe, posicoes_abertas):
    """Monitora sinais de um timeframe específico usando apenas Bybit"""
    if not sinais_dict:
        return
        
    logger.info(f"--- Monitorando {len(sinais_dict)} sinais pendentes em {timeframe} ---")
    
    sinais_para_processar = list(sinais_dict.items())
    
    for par, info in sinais_para_processar:
        try:
            # Verificar se já existe posição
            if par in posicoes_abertas:
                logger.info(f"Sinal {par} ({timeframe}) removido - posição já existe.")
                del sinais_dict[par]
                continue
            
            # Verificar timeout baseado no timeframe
            timeout_map = {'5m': 900, '15m': 1800, '4h': 7200}  # 15min, 30min, 2h
            timeout = timeout_map.get(timeframe, 900)
            
            if (datetime.now() - info['timestamp']).total_seconds() > timeout:
                logger.info(f"Sinal {par} ({timeframe}) expirou após {timeout}s.")
                del sinais_dict[par]
                continue
            
            # Verificar reset de 24h
            if 'primeira_operacao' in info:
                if verificar_reset_timeframe(par, info['primeira_operacao']):
                    logger.info(f"Reset de 24h para {par}. Removendo de todos os timeframes.")
                    # Remover de todos os timeframes
                    sinais_pendentes_5m.pop(par, None)
                    sinais_pendentes_15m.pop(par, None)
                    sinais_pendentes_4h.pop(par, None)
                    continue
                
            # Mapear timeframe para intervalo da Bybit
            interval_map = {'5m': '5', '15m': '15', '4h': '240'}
            interval = interval_map.get(timeframe, '5')
            
            # Obter dados do timeframe apropriado da Bybit
            df = obter_klines_bybit_para_rsi(executor.session, par, interval=interval, limit=50)
            if df.empty or len(df) < 15:
                continue
            
            # Calcular RSI
            try:
                df.ta.rsi(length=14, append=True)
                rsi_atual = df['RSI_14'].iloc[-1]
                rsi_anterior = df['RSI_14'].iloc[-2]
            except:
                continue
            
            logger.info(f"Monitorando {par} ({timeframe}): RSI {rsi_anterior:.2f} → {rsi_atual:.2f}")
            
            # Verificar crossover (saída de sobrevenda)
            crossover_confirmado = (
                pd.notna(rsi_anterior) and pd.notna(rsi_atual) and
                rsi_anterior <= 30 and
                rsi_atual > 30 and
                rsi_atual > rsi_anterior
            )
            
            if crossover_confirmado:
                logger.warning(f"🚀 CROSSOVER CONFIRMADO PARA {par} ({timeframe})! RSI: {rsi_anterior:.2f} → {rsi_atual:.2f}")
                
                if not gestor_drawdown.pode_operar():
                    logger.warning("Bot pausado pelo gestor de drawdown. Operação cancelada.")
                    continue
                
                preco_atual = df['close'].iloc[-1]
                
                # AJUSTE: TP/SL específico por timeframe
                if timeframe == '5m':
                    # PRIMEIRO CICLO: TP fixo 5% e SL fixo 2.5%
                    take_profit = preco_atual * 1.05  # +5%
                    stop_loss = preco_atual * 0.975   # -2.5%
                    sl_mode = f'TP Fixo 5% / SL Fixo 2.5% ({timeframe})'
                    
                    sinal_final = {
                        'strategy_name': f'Momentum_Crossover_{timeframe}', 
                        'par': par, 
                        'preco_atual': preco_atual, 
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,  # TP fixo para 5m
                        'sl_mode': sl_mode,
                        'timeframe': timeframe
                    }
                    
                else:
                    # OUTROS CICLOS: TP dinâmico (RSI >= 70) e SL baseado em ATR
                    try:
                        df.ta.atr(length=14, append=True)
                        atr = df['ATR_14'].iloc[-1]
                        stop_loss = preco_atual - (atr * 2) if pd.notna(atr) else preco_atual * 0.98
                    except:
                        stop_loss = preco_atual * 0.98
                    
                    sinal_final = {
                        'strategy_name': f'Momentum_Crossover_{timeframe}', 
                        'par': par, 
                        'preco_atual': preco_atual, 
                        'stop_loss': stop_loss,
                        'sl_mode': f'ATR 2x ({timeframe})',
                        'timeframe': timeframe,
                        'take_profit': 0  # TP dinâmico para 15m e 4h
                    }
                
                resultado_ordem = executor.place_order(sinal_final)
                
                if resultado_ordem and "✅" in resultado_ordem:
                    # Registrar posição para monitoramento
                    posicoes_momentum[par] = {
                        'timestamp': datetime.now(),
                        'preco_entrada': preco_atual,
                        'timeframe': timeframe,
                        'tp_tipo': 'fixo' if timeframe == '5m' else 'dinamico'
                    }
                    
                    # Registrar no histórico
                    if par not in historico_operacoes:
                        historico_operacoes[par] = {
                            'primeira_operacao': datetime.now(),
                            'operacoes': []
                        }
                    
                    historico_operacoes[par]['operacoes'].append({
                        'timestamp': datetime.now(),
                        'timeframe': timeframe,
                        'preco': preco_atual
                    })
                    
                    del sinais_dict[par]
                    
                    cabecalho = f"*[Estratégia: {sinal_final['strategy_name']}]*\n"
                    if sinal_final.get('sl_mode'): 
                        cabecalho += f"Método SL/TP: *{sinal_final['sl_mode']}*\n"
                    alerta_final = cabecalho + resultado_ordem
                    await enviar_alerta_telegram(bot, settings.telegram_chat_id, alerta_final)
                    
                    logger.info(f"Ordem executada para {par} ({timeframe})")
                else:
                    logger.error(f"FALHA na execução da ordem para {par} ({timeframe}).")
                        
        except Exception as e:
            logger.error(f"Erro ao monitorar sinal {par} ({timeframe}): {e}")

async def main_loop():
    logger.info("🚀 Inicializando loop principal com timeframes escalonados - APENAS BYBIT...")
    
    executor = BybitExecutor()
    bot = telegram.Bot(token=settings.telegram_token)
    
    # Enviar mensagem de inicialização
    await enviar_alerta_telegram(bot, settings.telegram_chat_id, 
        "🤖 *BOT INICIADO - BYBIT ONLY*\nSistema de timeframes escalonados ativo\n5m (TP Fixo 5%) → 15m (TP Dinâmico) → 4h (TP Dinâmico)")
    
    while True:
        try:
            # Verificar saldo e drawdown
            try:
                saldo_atual = executor.get_margin_balance()
                if saldo_atual:
                    alerta_drawdown = gestor_drawdown.atualizar_saldo(saldo_atual)
                    if alerta_drawdown:
                        await enviar_alerta_telegram(bot, settings.telegram_chat_id, alerta_drawdown)
            except Exception as e:
                logger.debug(f"Erro ao verificar saldo: {e}")
            
            if not gestor_drawdown.pode_operar():
                logger.warning("Bot pausado pelo gestor de drawdown.")
                await asyncio.sleep(300)  # Aguardar 5 minutos
                continue
            
            # Obter posições abertas
            posicoes_abertas = executor.get_open_positions()
            
            # Monitorar TP dinâmico (apenas para 15m e 4h)
            await monitorar_tp_dinamico(executor, bot)
            
            # Monitorar sinais pendentes em todos os timeframes
            await monitorar_sinais_timeframe(executor, bot, 
                sinais_pendentes_5m, '5m', posicoes_abertas)
            await monitorar_sinais_timeframe(executor, bot, 
                sinais_pendentes_15m, '15m', posicoes_abertas)
            await monitorar_sinais_timeframe(executor, bot, 
                sinais_pendentes_4h, '4h', posicoes_abertas)
            
            # Buscar novos candidatos (apenas para 5m - início do ciclo)
            try:
                novos_sinais_momentum = analisar_momentum_pullback(executor.session, rsi_limite=30, valorizacao_minima_percent=3.0)
                for sinal in novos_sinais_momentum:
                    par = sinal['par']
                    if (par not in sinais_pendentes_5m and 
                        par not in sinais_pendentes_15m and 
                        par not in sinais_pendentes_4h and 
                        par not in posicoes_abertas):
                        
                        sinais_pendentes_5m[par] = {
                            'timestamp': datetime.now(),
                            'strategy_name': 'Momentum_Crossover_5m',
                            'timeframe': '5m'
                        }
                        logger.info(f"🔍 NOVO CANDIDATO: {par} sobrevendido em 5m. Aguardando crossover.")
                        
            except Exception as e:
                logger.error(f"Erro ao buscar candidatos Momentum: {e}")
            
            # Executar estratégia Fibonacci (independente dos timeframes escalonados)
            try:
                novos_sinais_fibonacci = analisar_fibonacci(executor.session, num_pares_liquidez=100, timeframes=['60', '240'], confianca_minima=8)
                for sinal in novos_sinais_fibonacci:
                    par = sinal['par']
                    if par not in posicoes_abertas:
                        resultado_ordem = executor.place_order(sinal)
                        if resultado_ordem and "✅" in resultado_ordem:
                            cabecalho = f"*[Estratégia: {sinal['strategy_name']}]*\n"
                            if sinal.get('confianca'): 
                                cabecalho += f"Confiança: *{sinal['confianca']}*\n"
                            if sinal.get('sl_mode'): 
                                cabecalho += f"Método SL/TP: *{sinal['sl_mode']}*\n"
                            alerta_final = cabecalho + resultado_ordem
                            await enviar_alerta_telegram(bot, settings.telegram_chat_id, alerta_final)
                            
            except Exception as e:
                logger.error(f"Erro ao executar estratégia Fibonacci: {e}")
            
            logger.info(f"📊 Status: 5m({len(sinais_pendentes_5m)}) | 15m({len(sinais_pendentes_15m)}) | 4h({len(sinais_pendentes_4h)}) | Posições({len(posicoes_momentum)})")
            await asyncio.sleep(60)
            
        except Exception as e:
            logger.error(f"Erro no loop principal: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Robot shutdown requested. Exiting.")
