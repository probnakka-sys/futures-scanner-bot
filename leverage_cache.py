#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import asyncio
import logging
from typing import Dict, Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

try:
    from bingx_leverages import BingXClient
    BINGX_LEVERAGES_AVAILABLE = True
except ImportError:
    logger.warning("⚠️ bingx-leverages не установлен, будет использоваться fallback логика")
    BINGX_LEVERAGES_AVAILABLE = False

class LeverageCache:
    """Кэширование реальных данных о плечах с BingX"""
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.cache = {}
        self.tier_cache = {}
        self.cache_ttl = 3600  # 1 час
        self.client = None
        
        if BINGX_LEVERAGES_AVAILABLE and api_key and api_secret:
            try:
                self.client = BingXClient(api_key=api_key, api_secret=api_secret)
                logger.info("✅ BingX Leverages клиент инициализирован")
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации BingX Leverages: {e}")
    
    async def get_leverage(self, symbol: str) -> int:
        """Получение реального максимального плеча для монеты"""
        # Проверяем кэш
        if symbol in self.cache:
            leverage, timestamp = self.cache[symbol]
            if datetime.now() - timestamp < timedelta(seconds=self.cache_ttl):
                return leverage
        
        # Если библиотека недоступна - используем fallback
        if not self.client or not BINGX_LEVERAGES_AVAILABLE:
            return self._fallback_leverage(symbol)
        
        try:
            # Конвертируем символ в формат BingX (BTC/USDT:USDT -> BTC-USDT)
            bingx_symbol = symbol.replace('/', '-').split(':')[0]
            
            # Получаем реальные tiers через библиотеку
            tiers = await asyncio.to_thread(
                self.client.discover_leverage_tiers,
                bingx_symbol
            )
            
            if tiers and len(tiers) > 0:
                # Берем максимальное плечо из tiers
                max_leverage = max(t['leverage'] for t in tiers)
                
                # Сохраняем в кэш
                self.cache[symbol] = (max_leverage, datetime.now())
                self.tier_cache[symbol] = (tiers, datetime.now())
                
                logger.info(f"📊 Реальное плечо для {symbol}: {max_leverage}x")
                return max_leverage
            else:
                return self._fallback_leverage(symbol)
                
        except Exception as e:
            logger.error(f"Ошибка получения плеча для {symbol}: {e}")
            return self._fallback_leverage(symbol)
    
    def _fallback_leverage(self, symbol: str) -> int:
        """Fallback логика на основе типа монеты"""
        coin = symbol.split('/')[0].upper()
        
        if coin in ['BTC', 'ETH']:
            return 125
        elif coin in ['BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'DOT', 'LINK', 'MATIC', 'AVAX']:
            return 75
        elif coin in ['SHIB', 'PEPE', 'DOGS', 'NOT', 'BONK', 'WIF']:
            return 50
        else:
            return 50
    
    async def get_position_limits(self, symbol: str) -> Dict:
        """Получение информации о лимитах позиций из tiers"""
        if symbol in self.tier_cache:
            tiers, timestamp = self.tier_cache[symbol]
            if datetime.now() - timestamp < timedelta(seconds=self.cache_ttl):
                return self._extract_limits(tiers)
        
        if not self.client or not BINGX_LEVERAGES_AVAILABLE:
            return {}
        
        try:
            bingx_symbol = symbol.replace('/', '-').split(':')[0]
            tiers = await asyncio.to_thread(
                self.client.discover_leverage_tiers,
                bingx_symbol
            )
            
            if tiers:
                self.tier_cache[symbol] = (tiers, datetime.now())
                return self._extract_limits(tiers)
        except Exception as e:
            logger.error(f"Ошибка получения tiers для {symbol}: {e}")
        
        return {}
    
    def _extract_limits(self, tiers: List[Dict]) -> Dict:
        """Извлечение лимитов из tiers"""
        if not tiers:
            return {}
        
        limits = {
            'max_leverage': max(t['leverage'] for t in tiers),
            'min_position': min(t.get('min_position_val', 5) for t in tiers),
            'max_position': max(t.get('max_position_val', 2_000_000) for t in tiers),
            'tiers': tiers
        }
        return limits
