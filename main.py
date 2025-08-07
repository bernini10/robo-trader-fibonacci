# main.py (Versão 12.1 - Alertas Momentum com SL/TP)

import pandas as pd
import numpy as np
from binance.client import Client
import sys
import time
import os
import telegram
from telegram import constants
import asyncio
from prometheus_client import start_http_server, Counter, Gauge, Info
from estrategias import analisar_momentum_pullback

# ... (Todas as funções de log, métricas e análise Fibonacci permanecem as mesmas ) ...
def log(mensagem):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {mensagem}", flush=True)

METRICAS_CICLOS_ANALISE = Counter('fib_ciclos_analise_total', 'Número total de ciclos de análise completados')
METRICAS_ALERTAS_ENVIADOS = Counter('fib_alertas_enviados_total', 'Número total de alertas enviados para o Telegram')
METRICAS_ERROS_CRITICOS = Counter('fib_erros_criticos_total', 'Número total de erros críticos no loop principal')
METRICAS_OPORTUNIDADES_ENCONTRADAS = Gauge('fib_oportunidades_imediatas_atuais', 'Número de oportunidades imediatas encontradas no último ciclo')
METRICAS_INFO_ALERTAS = Info('fib_alerta_detalhes', 'Detalhes do último alerta enviado')
METRICAS_PARES_MONITORADOS = Info('fib_pares_monitorados', 'Lista de pares sendo monitorados')

def obter_top_pares_por_liquidez(client, num_pares, pares_a_ignorar):
    log(f"Buscando os {num_pares} pares com maior liquidez (volume em USDT)...")
    try:
        all_tickers = pd.DataFrame(client.get_ticker())
        usdt_pairs = all_tickers[all_tickers.symbol.str.endswith('USDT')]
        usdt_pairs = usdt_pairs[~usdt_pairs.symbol.str.contains('UP|DOWN|BEAR|BULL')]
        usdt_pairs = usdt_pairs[~usdt_pairs.symbol.isin(pares_a_ignorar)]
        usdt_pairs['quoteVolume'] = pd.to_numeric(usdt_pairs['quoteVolume'])
        top_pairs = usdt_pairs.sort_values(by='quoteVolume', ascending=False)
        lista_pares = top_pairs.head(num_pares)['symbol'].tolist()
        log(f"Top {num_pares} pares encontrados para a estratégia Fibonacci.")
        METRICAS_PARES_MONITORADOS.info({'pares_fibonacci': ', '.join(lista_pares)})
        return lista_pares
    except Exception as e:
        log(f"Erro ao obter pares para Fibonacci: {e}")
        METRICAS_PARES_MONITORADOS.info({'pares_fibonacci': 'Erro ao obter lista'})
        return []

def obter_dados_historicos(client, symbol, timeframe, limit=300):
    try:
        klines = client.get_klines(symbol=symbol, interval=timeframe, limit=limit)
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col])
        return df
    except Exception:
        return pd.DataFrame()

def encontrar_topos_fundos(df, periodo):
    pivos = []
    indices = df.index
    for i in range(periodo, len(df) - periodo):
        janela_indices = indices[i-periodo : i+periodo+1]
        if df['low'][i] == df.loc[janela_indices, 'low'].min():
            pivos.append({'tipo': 'fundo', 'preco': df['low'][i], 'volume': df['volume'][i], 'indice': i})
        if df['high'][i] == df.loc[janela_indices, 'high'].max():
            pivos.append({'tipo': 'topo', 'preco': df['high'][i], 'volume': df['volume'][i], 'indice': i})
    if not pivos: return pd.DataFrame()
    df_pivos = pd.DataFrame(pivos)
    df_pivos = df_pivos.loc[df_pivos['tipo'].shift() != df_pivos['tipo']]
    return df_pivos.reset_index(drop=True)

def verificar_confluencia_elliott(pivos):
    if len(pivos) < 3:
        return None, "INSUFICIENTE"
    p3, p2, p1 = pivos.iloc[-1], pivos.iloc[-2], pivos.iloc[-3]
    if p1['tipo'] == 'fundo' and p2['tipo'] == 'topo' and p3['tipo'] == 'fundo':
        onda1_inicio, onda1_fim, onda2_fim = p1['preco'], p2['preco'], p3['preco']
        if onda1_fim > onda1_inicio and onda2_fim > onda1_inicio:
            retração = (onda1_fim - onda2_fim) / (onda1_fim - onda1_inicio)
            if 0.5 <= retração <= 0.786: return "COMPRA", "INÍCIO_ONDA_3"
    if p1['tipo'] == 'topo' and p2['tipo'] == 'fundo' and p3['tipo'] == 'topo':
        onda1_inicio, onda1_fim, onda2_fim = p1['preco'], p2['preco'], p3['preco']
        if onda1_fim < onda1_inicio and onda2_fim < onda1_inicio:
            retração = (onda2_fim - onda1_fim) / (onda1_inicio - onda1_fim)
            if 0.5 <= retração <= 0.786: return "VENDA", "INÍCIO_ONDA_3"
    return None, "SEM_PADRÃO"

def calcular_chance(nivel_fib, volume_pivo, volume_medio, timeframe, contexto_elliott):
    score = 0
    if nivel_fib == '0.618': score += 2
    elif nivel_fib == '0.500': score += 1
    if volume_pivo > volume_medio * 1.5: score += 1
    if timeframe == '4h': score += 1
    if contexto_elliott == "INÍCIO_ONDA_3": score += 3
    chance_map = {0:20, 1:35, 2:50, 3:65, 4:75, 5:85, 6:95, 7:99}
    return chance_map.get(score, 20)

def formatar_preco(preco):
    return f"{preco:.8f}" if preco < 0.01 else f"{preco:.4f}"

def analisar_fibonacci(df, pivos, timeframe, stop_loss_buffer, contexto_elliott):
    if len(pivos) < 2: return None
    ultimo_pivo, penultimo_pivo = pivos.iloc[-1], pivos.iloc[-2]
    preco_atual = df['close'].iloc[-1]
    volume_medio = df['volume'].iloc[-50:].mean()
    resultado = {}
    tipo_operacao = None
    if penultimo_pivo['tipo'] == 'fundo' and ultimo_pivo['tipo'] == 'topo':
        tipo_operacao = 'COMPRA'
        fundo, topo = penultimo_pivo['preco'], ultimo_pivo['preco']
        diferenca = topo - fundo
        if diferenca == 0: return None
        resultado.update({'tipo': tipo_operacao, 'volume_pivo': penultimo_pivo['volume'], 'sl': fundo * (1 - stop_loss_buffer), 'tp1': topo, 'tp2': topo + (diferenca * 1.618)})
        niveis = {'0.382': topo - diferenca * 0.382, '0.500': topo - diferenca * 0.500, '0.618': topo - diferenca * 0.618}
    elif penultimo_pivo['tipo'] == 'topo' and ultimo_pivo['tipo'] == 'fundo':
        tipo_operacao = 'VENDA'
        topo, fundo = penultimo_pivo['preco'], ultimo_pivo['preco']
        diferenca = topo - fundo
        if diferenca == 0: return None
        resultado.update({'tipo': tipo_operacao, 'volume_pivo': penultimo_pivo['volume'], 'sl': topo * (1 + stop_loss_buffer), 'tp1': fundo, 'tp2': fundo - (diferenca * 1.618)})
        niveis = {'0.382': fundo + diferenca * 0.382, '0.500': fundo + diferenca * 0.500, '0.618': fundo + diferenca * 0.618}
    else:
        return None
    for nivel, valor_nivel in niveis.items():
        distancia = abs(preco_atual - valor_nivel) / valor_nivel
        if distancia <= 0.015:
            chance = calcular_chance(nivel, resultado['volume_pivo'], volume_medio, timeframe, contexto_elliott if tipo_operacao == contexto_elliott[0] else "SEM_PADRÃO")
            oportunidade = resultado.copy()
            oportunidade.update({'nivel_fib': nivel, 'preco_alvo': valor_nivel, 'preco_atual': preco_atual, 'distancia_%': distancia * 100, 'status': 'IMEDIATA', 'chance_%': chance, 'contexto_elliott': contexto_elliott[1] if tipo_operacao == contexto_elliott[0] else "N/A"})
            return oportunidade
    return None

async def executar_analise_fib_elliott(client, params):
    log("--- Iniciando Estratégia: Fibonacci + Elliott (Top 100 Liquidez) ---")
    pares = obter_top_pares_por_liquidez(client, params['num_pares'], params['pares_a_ignorar'])
    if not pares: return pd.DataFrame()
    total_operacoes = len(pares) * len(params['timeframes'])
    log(f"Analisando {len(pares)} pares em {len(params['timeframes'])} timeframes. Total de {total_operacoes} operações...")
    oportunidades = []
    for i, par in enumerate(pares):
        for tf in params['timeframes']:
            log(f"Progresso Fib: {(i*len(params['timeframes']) + params['timeframes'].index(tf) + 1)}/{total_operacoes} | Analisando {par} ({tf})")
            df = obter_dados_historicos(client, par, tf)
            if df.empty or len(df) < params['periodo_zigzag'] * 2: continue
            pivos = encontrar_topos_fundos(df, params['periodo_zigzag'])
            if pivos.empty: continue
            tipo_elliott, contexto_elliott = verificar_confluencia_elliott(pivos)
            resultado = analisar_fibonacci(df, pivos, tf, params['stop_loss_buffer'], (tipo_elliott, contexto_elliott))
            if resultado:
                oportunidades.append({'par': par, 'timeframe': tf, **resultado})
            await asyncio.sleep(0.1)
    log("Varredura Fibonacci concluída.")
    if not oportunidades: return pd.DataFrame()
    log("Calculando scores e confluências Fibonacci...")
    df = pd.DataFrame(oportunidades)
    confluencia = df.groupby('par')['par'].transform('count')
    df['chance_%'] += (confluencia - 1) * 5
    df['chance_%'] = df['chance_%'].clip(upper=99)
    df_final = df[(df['status'] == 'IMEDIATA') & (df['chance_%'] >= params['chance_minima'])].sort_values(by='chance_%', ascending=False)
    return df_final

async def executar_analise_momentum(client):
    return analisar_momentum_pullback(client)

async def enviar_alerta_telegram(bot, chat_id, mensagem):
    try:
        log(f"Tentando enviar mensagem para o Telegram: \"{mensagem.splitlines()[0]}...\"")
        await bot.send_message(chat_id=chat_id, text=mensagem, parse_mode=constants.ParseMode.MARKDOWN)
        log("Mensagem enviada com sucesso.")
    except Exception as e:
        log(f"ERRO ao enviar alerta para o Telegram: {e}")

async def main():
    log("Iniciando o robô...")
    try:
        log("Lendo as credenciais do ambiente...")
        TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
        TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
        log("Credenciais lidas com sucesso.")
    except KeyError:
        log("ERRO FATAL: Variáveis de ambiente não definidas.")
        sys.exit(1)
    
    params = {'num_pares': 100, 'timeframes': ['15m', '1h', '4h'], 'periodo_zigzag': 15, 'pares_a_ignorar': ['USDCUSDT', 'FDUSDUSDT', 'TUSDUSDT', 'USDPUSDT', 'BUSDUSDT'], 'stop_loss_buffer': 0.01, 'chance_minima': 85, 'intervalo_minutos': 5}
    
    client = Client()
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    alertas_enviados_fib = set()
    alertas_enviados_momentum = set()

    try:
        start_http_server(8000 )
        log("Servidor de métricas Prometheus iniciado na porta 8000.")
    except Exception as e:
        log(f"ERRO FATAL ao iniciar servidor de métricas: {e}")
        sys.exit(1)
    
    await enviar_alerta_telegram(bot, TELEGRAM_CHAT_ID, "✅ *Robô Multi-Estratégia Iniciado*\nEstou monitorando o mercado para você.")
    
    try:
        while True:
            try:
                log("Iniciando ciclo de análises em PARALELO...")
                
                resultados = await asyncio.gather(
                    executar_analise_fib_elliott(client, params),
                    executar_analise_momentum(client)
                )
                
                oportunidades_fib = resultados[0]
                sinais_momentum = resultados[1]

                num_oportunidades = len(oportunidades_fib) if not oportunidades_fib.empty else 0
                METRICAS_OPORTUNIDADES_ENCONTRADAS.set(num_oportunidades + len(sinais_momentum))
                
                if num_oportunidades > 0:
                    log(f"FIB: ENCONTRADO! {num_oportunidades} sinal(is) de alta probabilidade.")
                    for _, row in oportunidades_fib.iterrows():
                        alerta_id = f"FIB-{row['par']}-{row['timeframe']}-{row['tipo']}"
                        if alerta_id not in alertas_enviados_fib:
                            METRICAS_ALERTAS_ENVIADOS.inc()
                            preco_entrada = row['preco_alvo']
                            sl_percent = abs((row['sl'] - preco_entrada) / preco_entrada) * 100
                            tp1_percent = abs((row['tp1'] - preco_entrada) / preco_entrada) * 100
                            tp2_percent = abs((row['tp2'] - preco_entrada) / preco_entrada) * 100
                            METRICAS_INFO_ALERTAS.info({'estrategia': 'Fibonacci', 'par': row['par'], 'timeframe': row['timeframe'], 'tipo': row['tipo'], 'chance': f"{row['chance_%']:.0f}%", 'entrada': formatar_preco(preco_entrada), 'sl': f"{formatar_preco(row['sl'])} (-{sl_percent:.2f}%)", 'tp1': f"{formatar_preco(row['tp1'])} (+{tp1_percent:.2f}%)", 'tp2': f"{formatar_preco(row['tp2'])} (+{tp2_percent:.2f}%)", 'contexto': row['contexto_elliott']})
                            header = "🚨 *ALERTA DE ENTRADA IMEDIATA* 🚨"
                            confluencia_info = ""
                            if row['contexto_elliott'] == "INÍCIO_ONDA_3":
                                header = "💎 *SINAL DE ELITE: FIBONACCI + ELLIOTT* 💎"
                                confluencia_info = "\n*Confluência:* Potencial início de Onda 3."
                            mensagem = (f"{header}\n\n" f"*{row['par']} ({row['timeframe']})*\n" f"*{row['tipo']}* com chance de *{row['chance_%']:.0f}%*{confluencia_info}\n\n" f"📈 *Entrada:* Perto de `{formatar_preco(preco_entrada)}` (Fibo {row['nivel_fib']})\n" f"🎯 *Take Profit 1:* `{formatar_preco(row['tp1'])}` (+{tp1_percent:.2f}%)\n" f"🎯 *Take Profit 2:* `{formatar_preco(row['tp2'])}` (+{tp2_percent:.2f}%)\n" f"🛑 *Stop Loss:* `{formatar_preco(row['sl'])}` (-{sl_percent:.2f}%)")
                            await enviar_alerta_telegram(bot, TELEGRAM_CHAT_ID, mensagem)
                            alertas_enviados_fib.add(alerta_id)
                
                # AJUSTE PARA PROCESSAR OS NOVOS SINAIS DE MOMENTUM COM SL/TP
                if sinais_momentum:
                    log(f"MOMENTUM: ENCONTRADO! {len(sinais_momentum)} sinal(is) de pullback.")
                    for sinal in sinais_momentum:
                        alerta_id = f"MOMENTUM-{sinal['par']}"
                        if alerta_id not in alertas_enviados_momentum:
                            METRICAS_ALERTAS_ENVIADOS.inc()
                            preco_entrada = sinal['preco_atual']
                            sl_percent = abs((sinal['stop_loss'] - preco_entrada) / preco_entrada) * 100
                            tp_percent = abs((sinal['take_profit'] - preco_entrada) / preco_entrada) * 100
                            METRICAS_INFO_ALERTAS.info({'estrategia': 'Momentum', 'par': sinal['par'], 'preco_atual': formatar_preco(preco_entrada), 'rsi_atual': f"{sinal['rsi_atual']:.2f}", 'variacao_24h': f"{sinal['variacao_24h']:.2f}%", 'sl': f"{formatar_preco(sinal['stop_loss'])} (-{sl_percent:.2f}%)", 'tp': f"{formatar_preco(sinal['take_profit'])} (+{tp_percent:.2f}%)"})
                            mensagem = (f"📈 *ALERTA DE MOMENTUM PULLBACK (COM ALVOS)* 📈\n\n"
                                        f"*{sinal['par']}* (+{sinal['variacao_24h']:.2f}% em 24h) está em pullback.\n\n"
                                        f"RSI(14) em 5m: *{sinal['rsi_atual']:.2f}* (Gatilho de Sobrevenda)\n\n"
                                        f"📈 *Entrada:* Perto de `{formatar_preco(preco_entrada)}`\n"
                                        f"🎯 *Take Profit:* `{formatar_preco(sinal['take_profit'])}` (+{tp_percent:.2f}%)\n"
                                        f"🛑 *Stop Loss:* `{formatar_preco(sinal['stop_loss'])}` (-{sl_percent:.2f}%)")
                            await enviar_alerta_telegram(bot, TELEGRAM_CHAT_ID, mensagem)
                            alertas_enviados_momentum.add(alerta_id)

                METRICAS_CICLOS_ANALISE.inc()
                if len(alertas_enviados_fib) > 200: alertas_enviados_fib.clear()
                if len(alertas_enviados_momentum) > 200: alertas_enviados_momentum.clear()
                
                log(f"Análise concluída. Próxima verificação em {params['intervalo_minutos']} minutos.")
                await asyncio.sleep(params['intervalo_minutos'] * 60)

            except Exception as e:
                METRICAS_ERROS_CRITICOS.inc()
                log(f"ERRO CRÍTICO no loop principal: {e}")
                await enviar_alerta_telegram(bot, TELEGRAM_CHAT_ID, f"⚠️ *Ocorreu um erro crítico no robô:*\n`{e}`\n\nVou tentar novamente em 1 minuto.")
                await asyncio.sleep(60)
    finally:
        await enviar_alerta_telegram(bot, TELEGRAM_CHAT_ID, "🛑 *Robô Multi-Estratégia Parado*\nO monitoramento foi encerrado.")
        log("Robô encerrado. Mensagem de parada enviada.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Robô interrompido pelo usuário (Ctrl+C).")
