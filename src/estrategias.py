# estrategias.py (Versão 2.1 - Pullback com SL/TP baseados em ATR)

import pandas as pd
import pandas_ta as ta
from datetime import datetime
import time

def log(mensagem):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {mensagem}", flush=True)

def analisar_momentum_pullback(client, rsi_limite=28, num_top_performers=50, multiplicador_sl=1.5, multiplicador_tp=3.0):
    """
    Analisa os top performers de 24h e busca um pullback no RSI com alvos de SL/TP baseados em ATR.
    """
    log("--- Iniciando Estratégia: Momentum Pullback v2.1 (com ATR) ---")
    
    try:
        all_tickers = pd.DataFrame(client.get_ticker())
        usdt_pairs = all_tickers[all_tickers.symbol.str.endswith('USDT')]
        usdt_pairs = usdt_pairs[~usdt_pairs.symbol.str.contains('UP|DOWN|BEAR|BULL')]
        
        usdt_pairs['priceChangePercent'] = pd.to_numeric(usdt_pairs['priceChangePercent'])
        
        top_performers = usdt_pairs.sort_values(by='priceChangePercent', ascending=False).head(num_top_performers)
        
        if top_performers.empty:
            log("Momentum: Nenhum par com valorização positiva encontrado.")
            return []

        log(f"Momentum: Top {len(top_performers)} pares com maior valorização selecionados.")
        
        sinais = []
        for i, row in top_performers.iterrows():
            par = row['symbol']
            log(f"Progresso Momentum: {i+1}/{len(top_performers)} | Analisando {par} (+{row['priceChangePercent']:.2f}%)")

            idade_minima_dias = 7
            try:
                klines_idade = client.get_klines(symbol=par, interval='1d', limit=idade_minima_dias + 1)
                if len(klines_idade) < idade_minima_dias:
                    log(f"Momentum: Par {par} ignorado (lançado na última semana).")
                    continue
            except Exception:
                log(f"Momentum: Não foi possível verificar a idade de {par}, ignorando.")
                continue

            df_5m = pd.DataFrame(client.get_klines(symbol=par, interval='5m', limit=100))
            if df_5m.empty: continue
                
            df_5m.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
            for col in ['open', 'high', 'low', 'close']:
                df_5m[col] = pd.to_numeric(df_5m[col])
            
            # Calcular RSI e ATR
            df_5m.ta.rsi(length=14, append=True)
            df_5m.ta.atr(length=14, append=True)
            
            rsi_atual = df_5m['RSI_14'].iloc[-1]
            atr_atual = df_5m['ATRr_14'].iloc[-1]
            preco_atual = df_5m['close'].iloc[-1]
            
            if rsi_atual <= rsi_limite:
                log(f"SINAL MOMENTUM ENCONTRADO! {par} atingiu RSI de {rsi_atual:.2f}")
                
                # Calcular SL e TP com base no ATR
                stop_loss = preco_atual - (multiplicador_sl * atr_atual)
                take_profit = preco_atual + (multiplicador_tp * atr_atual)
                
                sinal = {
                    'par': par,
                    'preco_atual': preco_atual,
                    'rsi_atual': rsi_atual,
                    'variacao_24h': row['priceChangePercent'],
                    'stop_loss': stop_loss,
                    'take_profit': take_profit
                }
                sinais.append(sinal)
            time.sleep(0.1)

        return sinais

    except Exception as e:
        log(f"ERRO na estratégia Momentum Pullback: {e}")
        return []
