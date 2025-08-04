# main.py (Versão 9.6 - FINAL COM ALERTAS EM %)

import pandas as pd
import numpy as np
from binance.client import Client
import sys
import time
import os
import telegram
from telegram import constants
import asyncio
from prometheus_client import start_http_server, Counter, Gauge

# --- FUNÇÃO DE LOGGING ---
def log(mensagem ):
    """Imprime uma mensagem com data e hora, forçando a exibição imediata."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {mensagem}", flush=True)

# --- CONFIGURAÇÃO DAS MÉTRICAS (DEFINIDAS GLOBALMENTE) ---
METRICAS_CICLOS_ANALISE = Counter('fib_ciclos_analise_total', 'Número total de ciclos de análise completados')
METRICAS_ALERTAS_ENVIADOS = Counter('fib_alertas_enviados_total', 'Número total de alertas enviados para o Telegram')
METRICAS_ERROS_CRITICOS = Counter('fib_erros_criticos_total', 'Número total de erros críticos no loop principal')
METRICAS_OPORTUNIDADES_ENCONTRADAS = Gauge('fib_oportunidades_imediatas_atuais', 'Número de oportunidades imediatas encontradas no último ciclo')

# --- FUNÇÕES DE ANÁLISE ---
def obter_top_pares_por_volume(client, num_pares, pares_a_ignorar):
    log(f"Buscando os {num_pares} pares com maior volume...")
    try:
        all_tickers = pd.DataFrame(client.get_ticker())
        usdt_pairs = all_tickers[all_tickers.symbol.str.endswith('USDT')]
        usdt_pairs = usdt_pairs[~usdt_pairs.symbol.str.contains('UP|DOWN|BEAR|BULL')]
        usdt_pairs = usdt_pairs[~usdt_pairs.symbol.isin(pares_a_ignorar)]
        usdt_pairs['volume'] = pd.to_numeric(usdt_pairs['volume'])
        top_pairs = usdt_pairs.sort_values(by='volume', ascending=False)
        log(f"Top {num_pares} pares encontrados.")
        return top_pairs.head(num_pares)['symbol'].tolist()
    except Exception as e:
        log(f"Erro ao obter pares: {e}")
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
    for i in range(periodo, len(df) - periodo):
        is_low = df['low'][i] == df['low'][i-periodo:i+periodo+1].min()
        is_high = df['high'][i] == df['high'][i-periodo:i+periodo+1].max()
        if is_low: pivos.append({'tipo': 'fundo', 'preco': df['low'][i], 'volume': df['volume'][i]})
        if is_high: pivos.append({'tipo': 'topo', 'preco': df['high'][i], 'volume': df['volume'][i]})
    if not pivos: return pd.DataFrame()
    return pd.DataFrame(pivos).drop_duplicates(subset=['tipo'], keep='last')

def calcular_chance(nivel_fib, volume_pivo, volume_medio, timeframe):
    score = 0
    if nivel_fib == '0.618': score += 2
    elif nivel_fib == '0.500': score += 1
    if volume_pivo > volume_medio * 1.5: score += 1
    if timeframe == '4h': score += 1
    chance_map = {0: 25, 1: 45, 2: 65, 3: 85, 4: 95}
    return chance_map.get(score, 25)

def formatar_preco(preco):
    return f"{preco:.8f}" if preco < 0.01 else f"{preco:.4f}"

def analisar_fibonacci(df, pivos, timeframe, stop_loss_buffer):
    if len(pivos) < 2: return None
    ultimo_pivo, penultimo_pivo = pivos.iloc[-1], pivos.iloc[-2]
    preco_atual = df['close'].iloc[-1]
    volume_medio = df['volume'].iloc[-50:].mean()
    resultado = {}
    if penultimo_pivo['tipo'] == 'fundo' and ultimo_pivo['tipo'] == 'topo':
        fundo, topo = penultimo_pivo['preco'], ultimo_pivo['preco']
        diferenca = topo - fundo
        if diferenca == 0: return None
        resultado.update({'tipo': 'COMPRA', 'volume_pivo': penultimo_pivo['volume'], 'sl': fundo * (1 - stop_loss_buffer), 'tp1': topo, 'tp2': topo + (diferenca * 1.618)})
        niveis = {'0.382': topo - diferenca * 0.382, '0.500': topo - diferenca * 0.500, '0.618': topo - diferenca * 0.618}
    elif penultimo_pivo['tipo'] == 'topo' and ultimo_pivo['tipo'] == 'fundo':
        topo, fundo = penultimo_pivo['preco'], ultimo_pivo['preco']
        diferenca = topo - fundo
        if diferenca == 0: return None
        resultado.update({'tipo': 'VENDA', 'volume_pivo': penultimo_pivo['volume'], 'sl': topo * (1 + stop_loss_buffer), 'tp1': fundo, 'tp2': fundo - (diferenca * 1.618)})
        niveis = {'0.382': fundo + diferenca * 0.382, '0.500': fundo + diferenca * 0.500, '0.618': fundo + diferenca * 0.618}
    else:
        return None
    for nivel, valor_nivel in niveis.items():
        distancia = abs(preco_atual - valor_nivel) / valor_nivel
        if distancia <= 0.015:
            oportunidade = resultado.copy()
            oportunidade.update({'nivel_fib': nivel, 'preco_alvo': valor_nivel, 'preco_atual': preco_atual, 'distancia_%': distancia * 100, 'status': 'IMEDIATA', 'chance_%': calcular_chance(nivel, oportunidade['volume_pivo'], volume_medio, timeframe)})
            return oportunidade
    return None

def executar_analise_completa(client, params):
    log("Iniciando novo ciclo de análise de mercado...")
    pares = obter_top_pares_por_volume(client, params['num_pares'], params['pares_a_ignorar'])
    if not pares: return pd.DataFrame()
    total_operacoes = len(pares) * len(params['timeframes'])
    log(f"Analisando {len(pares)} pares em {len(params['timeframes'])} timeframes. Total de {total_operacoes} operações...")
    oportunidades = []
    for i, par in enumerate(pares):
        for tf in params['timeframes']:
            log(f"Progresso: {(i*len(params['timeframes']) + params['timeframes'].index(tf) + 1)}/{total_operacoes} | Analisando {par} ({tf})")
            df = obter_dados_historicos(client, par, tf)
            if df.empty or len(df) < params['periodo_zigzag'] * 2: continue
            pivos = encontrar_topos_fundos(df, params['periodo_zigzag'])
            if pivos.empty: continue
            resultado = analisar_fibonacci(df, pivos, tf, params['stop_loss_buffer'])
            if resultado:
                oportunidades.append({'par': par, 'timeframe': tf, **resultado})
            time.sleep(0.2)
    log("Varredura de pares concluída.")
    if not oportunidades: return pd.DataFrame()
    log("Calculando scores e confluências...")
    df = pd.DataFrame(oportunidades)
    confluencia = df.groupby('par')['par'].transform('count')
    df['chance_%'] += (confluencia - 1) * 5
    df['chance_%'] = df['chance_%'].clip(upper=99)
    df_final = df[(df['status'] == 'IMEDIATA') & (df['chance_%'] >= params['chance_minima'])].sort_values(by='chance_%', ascending=False)
    return df_final

# --- FUNÇÕES PRINCIPAIS DO ROBÔ ---
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
    
    params = {
        'num_pares': 50, 'timeframes': ['15m', '1h', '4h'], 'periodo_zigzag': 10,
        'pares_a_ignorar': ['USDCUSDT', 'FDUSDUSDT', 'TUSDUSDT', 'USDPUSDT', 'BUSDUSDT'],
        'stop_loss_buffer': 0.01, 'chance_minima': 85, 'intervalo_minutos': 5
    }

    client = Client()
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    alertas_enviados = set()

    try:
        start_http_server(8000 )
        log("Servidor de métricas Prometheus iniciado na porta 8000.")
    except Exception as e:
        log(f"ERRO FATAL ao iniciar servidor de métricas: {e}")
        sys.exit(1)

    await enviar_alerta_telegram(bot, TELEGRAM_CHAT_ID, "✅ *Robô Analisador de Fibonacci Iniciado*\nEstou monitorando o mercado para você.")
    
    try:
        while True:
            try:
                novas_oportunidades = executar_analise_completa(client, params)
                METRICAS_CICLOS_ANALISE.inc()
                
                num_oportunidades = len(novas_oportunidades) if not novas_oportunidades.empty else 0
                METRICAS_OPORTUNIDADES_ENCONTRADAS.set(num_oportunidades)

                if num_oportunidades > 0:
                    log(f"ENCONTRADO! {num_oportunidades} sinal(is) de alta probabilidade.")
                    for _, row in novas_oportunidades.iterrows():
                        alerta_id = f"{row['par']}-{row['timeframe']}-{row['tipo']}-{row['nivel_fib']}"
                        if alerta_id not in alertas_enviados:
                            METRICAS_ALERTAS_ENVIADOS.inc()
                            
                            # --- Bloco de Cálculo e Formatação da Mensagem ---
                            preco_entrada = row['preco_alvo']
                            sl_percent = abs((row['sl'] - preco_entrada) / preco_entrada) * 100
                            tp1_percent = abs((row['tp1'] - preco_entrada) / preco_entrada) * 100
                            tp2_percent = abs((row['tp2'] - preco_entrada) / preco_entrada) * 100

                            mensagem = (
                                f"🚨 *ALERTA DE ENTRADA IMEDIATA* 🚨\n\n"
                                f"*{row['par']} ({row['timeframe']})*\n"
                                f"*{row['tipo']}* com chance de *{row['chance_%']:.0f}%*\n\n"
                                f"📈 *Entrada:* Perto de `{formatar_preco(preco_entrada)}` (Fibo {row['nivel_fib']})\n"
                                f"🎯 *Take Profit 1:* `{formatar_preco(row['tp1'])}` (+{tp1_percent:.2f}%)\n"
                                f"🎯 *Take Profit 2:* `{formatar_preco(row['tp2'])}` (+{tp2_percent:.2f}%)\n"
                                f"🛑 *Stop Loss:* `{formatar_preco(row['sl'])}` (-{sl_percent:.2f}%)"
                            )
                            # --- Fim do Bloco ---

                            await enviar_alerta_telegram(bot, TELEGRAM_CHAT_ID, mensagem)
                            alertas_enviados.add(alerta_id)
                else:
                    log(f"Nenhum sinal de alta probabilidade encontrado.")
                
                if len(alertas_enviados) > 200:
                    alertas_enviados.clear()
                
                log(f"Análise concluída. Próxima verificação em {params['intervalo_minutos']} minutos.")
                await asyncio.sleep(params['intervalo_minutos'] * 60)

            except Exception as e:
                METRICAS_ERROS_CRITICOS.inc()
                log(f"ERRO CRÍTICO no loop principal: {e}")
                await enviar_alerta_telegram(bot, TELEGRAM_CHAT_ID, f"⚠️ *Ocorreu um erro crítico no robô:*\n`{e}`\n\nVou tentar novamente em 1 minuto.")
                await asyncio.sleep(60)
    finally:
        await enviar_alerta_telegram(bot, TELEGRAM_CHAT_ID, "🛑 *Robô Analisador de Fibonacci Parado*\nO monitoramento foi encerrado.")
        log("Robô encerrado. Mensagem de parada enviada.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Robô interrompido pelo usuário (Ctrl+C).")
