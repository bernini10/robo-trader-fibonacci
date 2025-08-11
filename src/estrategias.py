# src/estrategias.py (Versão 20.0 - Apenas API Bybit + DEBUG + TP Corrigido)

import pandas as pd
import pandas_ta as ta
from datetime import datetime
import time
from src.utils import logger

def obter_tickers_bybit(client):
    """Obtém todos os tickers da Bybit com dados de valorização 24h"""
    try:
        response = client.get_tickers(category="linear")
        if response['retCode'] == 0 and response['result']['list']:
            tickers = []
            for ticker in response['result']['list']:
                if ticker['symbol'].endswith('USDT'):
                    # Calcular valorização percentual
                    price_24h_pcnt = float(ticker.get('price24hPcnt', 0)) * 100
                    tickers.append({
                        'symbol': ticker['symbol'],
                        'priceChangePercent': price_24h_pcnt,
                        'volume': float(ticker.get('volume24h', 0)),
                        'quoteVolume': float(ticker.get('turnover24h', 0)),
                        'lastPrice': float(ticker.get('lastPrice', 0))
                    })
            logger.info(f"Obtidos {len(tickers)} tickers USDT da Bybit")
            return pd.DataFrame(tickers)
        else:
            logger.error(f"Erro ao obter tickers da Bybit: {response}")
            return pd.DataFrame()
    except Exception as e:
        logger.error(f"Erro ao buscar tickers da Bybit: {e}")
        return pd.DataFrame()

def obter_klines_bybit(client, symbol, interval='5', limit=20):
    """Obtém dados de klines da Bybit"""
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
            logger.debug(f"Erro ao obter klines para {symbol}: {response}")
            return pd.DataFrame()
    except Exception as e:
        logger.debug(f"Erro ao obter klines para {symbol}: {e}")
        return pd.DataFrame()

def analisar_momentum_pullback(bybit_client, rsi_limite=30, valorizacao_minima_percent=3.0):
    logger.info(f"--- Buscando Candidatos Momentum (RSI < {rsi_limite}) - APENAS BYBIT ---")
    try:
        # Obter todos os tickers da Bybit
        all_tickers = obter_tickers_bybit(bybit_client)
        if all_tickers.empty:
            logger.error("Nenhum ticker obtido da Bybit")
            return []

        # Filtrar apenas pares USDT (já filtrado na função obter_tickers_bybit)
        usdt_pairs = all_tickers.copy()
        
        # Remover pares alavancados e tokens especiais
        usdt_pairs = usdt_pairs[~usdt_pairs.symbol.str.contains('UP|DOWN|BEAR|BULL')]
        
        # Filtrar pares com valorização acima do mínimo
        top_performers = usdt_pairs[usdt_pairs.priceChangePercent > valorizacao_minima_percent]
        
        if top_performers.empty:
            logger.info("Nenhum par com valorização suficiente encontrado")
            return []

        logger.info(f"Analisando {len(top_performers)} pares com valorização > {valorizacao_minima_percent}%")

        sinais_pendentes = []
        for _, row in top_performers.iterrows():
            par = row['symbol']
            try:
                # Obter dados de 5 minutos da Bybit
                df_5m = obter_klines_bybit(bybit_client, par, interval='5', limit=20)
                if df_5m.empty:
                    continue
                
                # Verificar se temos dados suficientes
                if len(df_5m) < 15:
                    continue
                
                # Calcular RSI
                df_5m.ta.rsi(length=14, append=True)
                rsi_atual = df_5m['RSI_14'].iloc[-1]
                
                # DEBUG: Mostrar RSI de cada par analisado
                logger.info(f"🔍 {par}: RSI={rsi_atual:.2f}, Valorização={row['priceChangePercent']:.1f}%")

                if pd.notna(rsi_atual) and rsi_atual < rsi_limite:
                    logger.warning(f"🎯 CANDIDATO ENCONTRADO: {par} RSI={rsi_atual:.2f} < {rsi_limite}")
                    logger.info(f"CANDIDATO A SINAL ENCONTRADO: {par} está sobrevendido (RSI: {rsi_atual:.2f}). Adicionando ao monitoramento.")
                    sinais_pendentes.append({'par': par, 'strategy_name': 'Momentum_Crossover'})
                    
            except Exception as e:
                logger.debug(f"Erro ao analisar {par}: {e}")
                continue
            time.sleep(0.05)  # Reduzir pausa já que é uma API só
            
        logger.info(f"Estratégia Momentum: {len(sinais_pendentes)} candidatos encontrados")
        return sinais_pendentes
        
    except Exception as e:
        logger.error(f"ERRO ao buscar candidatos Momentum: {e}", exc_info=True)
        return []

def analisar_fibonacci(bybit_client, num_pares_liquidez=100, timeframes=['60', '240'], confianca_minima=8):
    logger.info(f"--- Iniciando Estratégia: Fibonacci Retraction (Confiança Mínima: {confianca_minima}) - APENAS BYBIT ---")
    try:
        # Obter todos os tickers da Bybit
        all_tickers = obter_tickers_bybit(bybit_client)
        if all_tickers.empty:
            logger.error("Nenhum ticker obtido da Bybit")
            return []

        # Filtrar apenas pares USDT e remover alavancados
        usdt_pairs = all_tickers.copy()
        usdt_pairs = usdt_pairs[~usdt_pairs.symbol.str.contains('UP|DOWN|BEAR|BULL')]
        
        # Selecionar os pares com maior liquidez (quoteVolume)
        top_pares = usdt_pairs.sort_values(by='quoteVolume', ascending=False).head(num_pares_liquidez)
        
        logger.info(f"Analisando {len(top_pares)} pares com maior liquidez")
        
        sinais = []
        for _, row in top_pares.iterrows():
            par = row['symbol']
            for tf in timeframes:
                try:
                    # Obter dados históricos da Bybit
                    limit = 300 if tf == '240' else 200  # 240 = 4h, 60 = 1h
                    df = obter_klines_bybit(bybit_client, par, interval=tf, limit=limit)
                    if df.empty or len(df) < 50:
                        continue
                    
                    # Encontrar pivôs
                    pivos = encontrar_topos_fundos(df, 10)
                    if len(pivos) < 2:
                        continue
                        
                    ultimo_pivo, penultimo_pivo = pivos.iloc[-1], pivos.iloc[-2]
                    preco_atual = df['close'].iloc[-1]
                    
                    # Verificar padrão fundo -> topo
                    if penultimo_pivo['tipo'] == 'fundo' and ultimo_pivo['tipo'] == 'topo':
                        fundo, topo = penultimo_pivo['preco'], ultimo_pivo['preco']
                        diferenca = topo - fundo
                        if diferenca <= 0:
                            continue
                            
                        # Calcular níveis de Fibonacci
                        nivel_618 = topo - diferenca * 0.618
                        nivel_500 = topo - diferenca * 0.500
                        
                        # Contar toques na zona
                        confianca = 0
                        for i in range(-10, 0):
                            if i < -len(df):
                                continue
                            if nivel_618 <= df['low'].iloc[i] <= nivel_500:
                                confianca += 1
                        
                        # Verificar se está na Golden Zone e tem confiança suficiente
                        if (nivel_618 <= preco_atual <= nivel_500) and (confianca >= confianca_minima):
                            stop_loss = fundo
                            
                            # CORREÇÃO: Take Profit usando 61.8% em vez de 161.8%
                            extensao_fib = diferenca * 0.618
                            take_profit = preco_atual + extensao_fib
                            
                            sl_mode = "Fundo do Pivô + Fib 61.8%"
                            
                            # Validar SL/TP
                            if stop_loss <= 0 or take_profit <= 0 or stop_loss >= preco_atual:
                                logger.warning(f"SL/TP de Fibonacci inválido para {par}. Usando fallback.")
                                stop_loss = preco_atual * 0.98
                                take_profit = preco_atual * 1.04
                                sl_mode = "Fallback 2%"
                            
                            logger.info(f"SINAL FIBONACCI VÁLIDO! {par} ({tf}min) com Confiança: {confianca}, SL Mode: {sl_mode}")
                            
                            sinais.append({
                                'strategy_name': 'Fibonacci',
                                'par': par,
                                'preco_atual': preco_atual,
                                'stop_loss': stop_loss,
                                'take_profit': take_profit,
                                'confianca': f"{confianca} toques",
                                'sl_mode': sl_mode
                            })
                            
                except Exception as e:
                    logger.debug(f"Erro na estratégia Fibonacci para {par} ({tf}min): {e}")
                    continue
                time.sleep(0.05)
        
        logger.info(f"Estratégia Fibonacci: {len(sinais)} sinais encontrados")
        return sinais
        
    except Exception as e:
        logger.error(f"ERRO na estratégia Fibonacci: {e}", exc_info=True)
        return []

def encontrar_topos_fundos(df, periodo):
    """Encontra topos e fundos no DataFrame"""
    pivos = []
    for i in range(periodo, len(df) - periodo):
        janela = df.iloc[i-periodo:i+periodo+1]
        
        # Verificar se é um fundo (mínimo local)
        if df['low'].iloc[i] == janela['low'].min():
            pivos.append({'tipo': 'fundo', 'preco': df['low'].iloc[i], 'indice': i})
            
        # Verificar se é um topo (máximo local)
        if df['high'].iloc[i] == janela['high'].max():
            pivos.append({'tipo': 'topo', 'preco': df['high'].iloc[i], 'indice': i})
    
    if not pivos:
        return pd.DataFrame()
    
    # Converter para DataFrame e remover duplicatas
    df_pivos = pd.DataFrame(pivos).drop_duplicates(subset=['preco', 'tipo'], keep='first')
    
    # Filtrar para alternar entre topos e fundos
    df_pivos = df_pivos[df_pivos['tipo'].shift() != df_pivos['tipo']].reset_index(drop=True)
    
    return df_pivos
