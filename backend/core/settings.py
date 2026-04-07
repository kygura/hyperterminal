import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class HyperliquidConfig(BaseModel):
    api_url: str = Field(default="https://api.hyperliquid.xyz")
    ws_url: str = Field(default="wss://api.hyperliquid.xyz/ws")
    agent_wallet_address: Optional[str] = None
    agent_private_key: Optional[str] = None
    vault_address: Optional[str] = None

class Settings(BaseModel):
    # Configuration sections
    hyperliquid: HyperliquidConfig = Field(default_factory=HyperliquidConfig)
    
    # Paths
    log_level: str = "INFO"
    log_file: str = "./logs/trading.log"
    database_url: str = "sqlite:///./data/trading.db"
    
    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'

    @classmethod
    def load(cls) -> 'Settings':
        """Load settings from environment variables"""
        settings = cls()
        
        # Load from environment
        settings.hyperliquid.api_url = os.getenv('HYPERLIQUID_API_URL', settings.hyperliquid.api_url)
        settings.hyperliquid.agent_private_key = os.getenv('AGENT_WALLET_SECRET_KEY')
        settings.hyperliquid.vault_address = os.getenv('VAULT_WALLET')
        
        settings.log_level = os.getenv('LOG_LEVEL', settings.log_level)
        settings.log_file = os.getenv('LOG_FILE', settings.log_file)
        settings.database_url = os.getenv('DATABASE_URL', settings.database_url)
        
        return settings

# Global settings instance
settings = Settings.load()
