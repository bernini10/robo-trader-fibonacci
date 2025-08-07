# src/utils.py (Versão 1.0)

import logging
from logging.handlers import RotatingFileHandler
import sys
import csv
from datetime import datetime

# --- Configuração do Logger Principal (Erros e Informações) ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = 'logs/error.log'

# Handler para rotacionar o arquivo de log quando ele atinge 5MB
file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
file_handler.setFormatter(log_formatter)

# Handler para imprimir logs no console (saída do Docker)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

# Obter o logger principal
logger = logging.getLogger('robot_logger')
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# --- Configuração do Logger de Trades (CSV) ---
trade_log_file = 'logs/trade_history.csv'
trade_log_header = [
    'timestamp_utc', 'strategy', 'pair', 'direction', 'entry_price', 
    'size_usdt', 'pnl_usdt', 'result', 'exit_price', 'close_reason'
]

# Garante que o cabeçalho exista no arquivo CSV
try:
    with open(trade_log_file, 'r') as f:
        pass
except FileNotFoundError:
    with open(trade_log_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(trade_log_header)

def log_trade(trade_data):
    """Registra uma operação completa no arquivo CSV de trades."""
    with open(trade_log_file, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=trade_log_header)
        # Preenche campos ausentes para garantir consistência
        for key in trade_log_header:
            trade_data.setdefault(key, 'N/A')
        trade_data['timestamp_utc'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        writer.writerow(trade_data)
    logger.info(f"TRADE LOGGED: {trade_data['pair']} - Result: {trade_data['result']}, PNL: {trade_data['pnl_usdt']} USDT")

