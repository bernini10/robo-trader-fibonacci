# src/estrategias.py (Versão 18.0 - Geração de Sinais Pendentes)

import pandas as pd
import pandas_ta as ta
from datetime import datetime
import time
from src.utils import logger

def obter_pares_negociaveis_bybit(client):
    try:
        info = client.get_instruments_info(category="linear")
        if info['retCode'] == 0 and info['result']['list']:
            return {s['symbol'] for s in info['result']['list']}
        return set()
    except Exception as e:
        logger.error(f"Bybit: Erro ao buscar pares negociáveis: {e}")
        return set()

def analisar_momentum_pullback(binance_client, bybit_client, rsi_limite=30, valorizacao_minima_percent=3.0):
    logger.info(f"--- Buscando Candidatos Momentum (RSI < {rsi_limite}) ---")
    try:
        pares_bybit = obter_pares_negociaveis_bybit(bybit_client)
        if not pares_bybit: return []

        all_tickers_binance = pd.DataFrame(binance_client.get_ticker())
        usdt_pairs = all_tickers_binance[all_tickers_binance.symbol.str.endswith('USDT')]
        usdt_pairs = usdt_pairs[~usdt_pairs.symbol.str.contains('UP|DOWN|BEAR|BULL')]
        usdt_pairs = usdt_pairs[usdt_pairs.symbol.isin(pares_bybit)]
        
        usdt_pairs['priceChangePercent'] = pd.to_numeric(usdt_pairs['priceChangePercent'])
        top_performers = usdt_pairs[usdt_pairs.priceChangePercent > valorizacao_minima_percent]
        
        if top_performers.empty: return []

        sinais_pendentes = []
        for _, row in top_performers.iterrows():
            par = row['symbol']
            try:
                df_5m = pd.DataFrame(binance_client.get_klines(symbol=par, interval='5m', limit=20))
                if df_5m.empty: continue
                
                df_5m.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
                for col in ['open', 'high', 'low', 'close']: df_5m[col] = pd.to_numeric(df_5m[col])
                
                df_5m.ta.rsi(length=14, append=True)
                rsi_atual = df_5m['RSI_14'].iloc[-1]

                if rsi_atual < rsi_limite:
                    logger.info(f"CANDIDATO A SINAL ENCONTRADO: {par} está sobrevendido (RSI: {rsi_atual:.2f}). Adicionando ao monitoramento.")
                    sinais_pendentes.append({'par': par, 'strategy_name': 'Momentum_Crossover'})
            except Exception:
                continue
            time.sleep(0.1)
        return sinais_pendentes
    except Exception as e:
        logger.error(f"ERRO ao buscar candidatos Momentum: {e}", exc_info=True)
        return []

# A estratégia de Fibonacci permanece inalterada
def analisar_fibonacci(binance_client, bybit_client, num_pares_liquidez=100, timeframes=['1h', '4h'], confianca_minima=8):
    logger.info(f"--- Iniciando Estratégia: Fibonacci Retraction (Confiança Mínima: {confianca_minima}) ---")
    # ... (código da estratégia Fibonacci permanece exatamente o mesmo) ...
    try:
        pares_bybit = obter_pares_negociaveis_bybit(bybit_client)
        if not pares_bybit: return []
        all_tickers_binance = pd.DataFrame(binance_client.get_ticker())
        usdt_pairs = all_tickers_binance[all_tickers_binance.symbol.str.endswith('USDT')]
        usdt_pairs = usdt_pairs[~usdt_pairs.symbol.str.contains('UP|DOWN|BEAR|BULL')]
        usdt_pairs = usdt_pairs[usdt_pairs.symbol.isin(pares_bybit)]
        usdt_pairs['quoteVolume'] = pd.to_numeric(usdt_pairs['quoteVolume'])
        top_pares = usdt_pairs.sort_values(by='quoteVolume', ascending=False).head(num_pares_liquidez)
        sinais = []
        for _, row in top_pares.iterrows():
            par = row['symbol']
            for tf in timeframes:
                df = pd.DataFrame(binance_client.get_klines(symbol=par, interval=tf, limit=300))
                if df.empty: continue
                df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
                for col in ['open', 'high', 'low', 'close']: df[col] = pd.to_numeric(df[col])
                pivos = encontrar_topos_fundos(df, 10)
                if len(pivos) < 2: continue
                ultimo_pivo, penultimo_pivo = pivos.iloc[-1], pivos.iloc[-2]
                preco_atual = df['close'].iloc[-1]
                if penultimo_pivo['tipo'] == 'fundo' and ultimo_pivo['tipo'] == 'topo':
                    fundo, topo = penultimo_pivo['preco'], ultimo_pivo['preco']
                    diferenca = topo - fundo
                    if diferenca <= 0: continue
                    nivel_618 = topo - diferenca * 0.618
                    nivel_500 = topo - diferenca * 0.500
                    confianca = 0
                    for i in range(-10, 0):
                        if nivel_618 <= df['low'].iloc[i] <= nivel_500:
                            confianca += 1
                    if (nivel_618 <= preco_atual <= nivel_500) and (confianca >= confianca_minima):
                        stop_loss = fundo
                        take_profit = topo + (diferenca * 1.618)
                        sl_mode = "Fundo do Pivo"
                        if stop_loss <= 0 or take_profit <= 0 or stop_loss >= preco_atual:
                            logger.warning(f"SL/TP de Fibonacci inválido para {par}. Usando fallback.")
                            stop_loss = preco_atual * 0.98
                            take_profit = preco_atual * 1.04
                            sl_mode = "Fallback 2%"
                        logger.info(f"SINAL FIBONACCI VÁLIDO! {par} ({tf}) com Confiança: {confianca}, SL Mode: {sl_mode}")
                        sinais.append({'strategy_name': 'Fibonacci', 'par': par, 'preco_atual': preco_atual, 'stop_loss': stop_loss, 'take_profit': take_profit, 'confianca': f"{confianca} toques", 'sl_mode': sl_mode})
                time.sleep(0.1)
        return sinais
    except Exception as e:
        logger.error(f"ERRO na estratégia Fibonacci: {e}", exc_info=True)
        return []

def encontrar_topos_fundos(df, periodo):
    pivos = []
    for i in range(periodo, len(df) - periodo):
        janela = df.iloc[i-periodo:i+periodo+1]
        if df['low'].iloc[i] == janela['low'].min():
            pivos.append({'tipo': 'fundo', 'preco': df['low'].iloc[i], 'indice': i})
        if df['high'].iloc[i] == janela['high'].max():
            pivos.append({'tipo': 'topo', 'preco': df['high'].iloc[i], 'indice': i})
    if not pivos: return pd.DataFrame()
    df_pivos = pd.DataFrame(pivos).drop_duplicates(subset=['preco', 'tipo'], keep='first')
    return df_pivos[df_pivos['tipo'].shift() != df_pivos['tipo']].reset_index(drop=True)
