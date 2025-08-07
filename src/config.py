# src/config.py (Versão 1.0)

import os
from src.utils import logger

class Config:
    def __init__(self):
        logger.info("Loading configuration from environment variables...")
        self.telegram_token = os.getenv('TELEGRAM_TOKEN')
        self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.bybit_api_key = os.getenv('BYBIT_API_KEY')
        self.bybit_api_secret = os.getenv('BYBIT_API_SECRET')
        
        try:
            self.risk_per_trade = float(os.getenv('RISK_PER_TRADE_PERCENT', '5.0'))
            self.leverage = int(os.getenv('LEVERAGE', '10'))
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid numeric configuration: {e}. Exiting.")
            raise SystemExit(f"Error: Invalid numeric configuration for risk or leverage.")

        self.validate()
        logger.info("Configuration loaded and validated successfully.")

    def validate(self):
        """Valida se todas as configurações essenciais estão presentes."""
        required_vars = {
            'Telegram Token': self.telegram_token,
            'Telegram Chat ID': self.telegram_chat_id,
            'Bybit API Key': self.bybit_api_key,
            'Bybit API Secret': self.bybit_api_secret
        }
        for name, var in required_vars.items():
            if not var:
                logger.error(f"Missing critical configuration: {name}. Exiting.")
                raise SystemExit(f"Error: {name} is not set in the .env file.")

# Instância global para ser importada por outros módulos
settings = Config()
