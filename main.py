#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import asyncio
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Set
import ccxt.async_support as ccxt
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import matplotlib.pyplot as plt
from io import BytesIO
import traceback
import random
import ssl
import certifi
import time
import json
import websockets
import aiohttp
import requests
import re
import hmac
import hashlib

# Импорт конфигурации
from config import (
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    UPDATE_INTERVAL,
    MIN_CONFIDENCE,
    TIMEFRAMES,
    REF_LINKS,
    FEATURES,
    INDICATOR_SETTINGS,
    INDICATOR_WEIGHTS,
    PUMP_DUMP_SETTINGS,
    IMBALANCE_SETTINGS,
    LIQUIDITY_SETTINGS,
    PAIRS_TO_SCAN,
    DISPLAY_SETTINGS
)

# Временно отключаем проверку SSL для теста
ssl._create_default_https_context = ssl._create_unverified_context

# Ручной расчет индикаторов
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_macd(series, fast=12, slow=26, signal=9):
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calculate_atr(high, low, close, period=14):
    high_low = high - low
    high_close = abs(high - close.shift())
    low_close = abs(low - close.shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    atr = true_range.rolling(window=period).mean()
    return atr

def calculate_bollinger_bands(series, period=20, std_dev=2):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return sma, upper, lower

def calculate_sma(series, period):
    return series.rolling(window=period).mean()

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    vwap = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
    return vwap

def detect_candle_patterns(df: pd.DataFrame) -> Dict:
    patterns = {}
    
    if len(df) < 2:
        return patterns
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    body = abs(last['close'] - last['open'])
    upper_wick = last['high'] - max(last['close'], last['open'])
    lower_wick = min(last['close'], last['open']) - last['low']
    
    if body > 0:
        if upper_wick > body * 2 and lower_wick < body * 0.3:
            patterns['shooting_star'] = 'медвежий'
        elif lower_wick > body * 2 and upper_wick < body * 0.3:
            patterns['hammer'] = 'бычий'
            patterns['pin_bar'] = 'бычий'
    
    if prev['close'] < prev['open'] and last['close'] > last['open']:
        if last['close'] > prev['open'] and last['open'] < prev['close']:
            patterns['engulfing'] = 'бычий'
    
    if body < (last['high'] - last['low']) * 0.1:
        patterns['doji'] = 'нейтральный'
    
    return patterns

# Загрузка конфигурации
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============== АНАЛИЗАТОР ДИВЕРГЕНЦИЙ ==============

class DivergenceAnalyzer:
    def __init__(self):
        self.lookback = 30
    
    def find_swings(self, df: pd.DataFrame, column: str = 'close', window: int = 5) -> Tuple[List, List]:
        highs = []
        lows = []
        for i in range(window, len(df) - window):
            if all(df[column].iloc[i] > df[column].iloc[j] 
                   for j in range(i - window, i + window + 1) if j != i):
                highs.append((i, df[column].iloc[i]))
            if all(df[column].iloc[i] < df[column].iloc[j] 
                   for j in range(i - window, i + window + 1) if j != i):
                lows.append((i, df[column].iloc[i]))
        return highs, lows
    
    def detect_rsi_divergence(self, df: pd.DataFrame, timeframe: str) -> Dict:
        result = {
            'bullish': False,
            'bearish': False,
            'strength': 0,
            'description': '',
            'timeframe': timeframe
        }
        price_highs, price_lows = self.find_swings(df, 'close')
        rsi_highs, rsi_lows = self.find_swings(df, 'rsi')
        
        if len(price_lows) >= 2 and len(rsi_lows) >= 2:
            last_price_low = price_lows[-1]
            prev_price_low = price_lows[-2]
            last_rsi_low = rsi_lows[-1]
            prev_rsi_low = rsi_lows[-2]
            if (last_price_low[1] < prev_price_low[1] and 
                last_rsi_low[1] > prev_rsi_low[1]):
                result['bullish'] = True
                result['strength'] = min(100, abs(last_price_low[1] - prev_price_low[1]) / prev_price_low[1] * 500)
                result['description'] = f"Бычья дивергенция RSI ({timeframe})"
        
        if len(price_highs) >= 2 and len(rsi_highs) >= 2:
            last_price_high = price_highs[-1]
            prev_price_high = price_highs[-2]
            last_rsi_high = rsi_highs[-1]
            prev_rsi_high = rsi_highs[-2]
            if (last_price_high[1] > prev_price_high[1] and 
                last_rsi_high[1] < prev_rsi_high[1]):
                result['bearish'] = True
                result['strength'] = min(100, abs(last_price_high[1] - prev_price_high[1]) / prev_price_high[1] * 500)
                result['description'] = f"Медвежья дивергенция RSI ({timeframe})"
        return result
    
    def detect_macd_divergence(self, df: pd.DataFrame, timeframe: str) -> Dict:
        result = {
            'bullish': False,
            'bearish': False,
            'strength': 0,
            'description': '',
            'timeframe': timeframe
        }
        if 'MACD_12_26_9' not in df.columns:
            return result
        price_highs, price_lows = self.find_swings(df, 'close')
        macd_highs, macd_lows = self.find_swings(df, 'MACD_12_26_9')
        
        if len(price_lows) >= 2 and len(macd_lows) >= 2:
            last_price_low = price_lows[-1]
            prev_price_low = price_lows[-2]
            last_macd_low = macd_lows[-1]
            prev_macd_low = macd_lows[-2]
            if (last_price_low[1] < prev_price_low[1] and 
                last_macd_low[1] > prev_macd_low[1]):
                result['bullish'] = True
                result['strength'] = min(100, abs(last_price_low[1] - prev_price_low[1]) / prev_price_low[1] * 500)
                result['description'] = f"Бычья дивергенция MACD ({timeframe})"
        
        if len(price_highs) >= 2 and len(macd_highs) >= 2:
            last_price_high = price_highs[-1]
            prev_price_high = price_highs[-2]
            last_macd_high = macd_highs[-1]
            prev_macd_high = macd_highs[-2]
            if (last_price_high[1] > prev_price_high[1] and 
                last_macd_high[1] < prev_macd_high[1]):
                result['bearish'] = True
                result['strength'] = min(100, abs(last_price_high[1] - prev_price_high[1]) / prev_price_high[1] * 500)
                result['description'] = f"Медвежья дивергенция MACD ({timeframe})"
        return result
    
    def analyze(self, df: pd.DataFrame, timeframe: str) -> Dict:
        rsi_div = self.detect_rsi_divergence(df, timeframe)
        macd_div = self.detect_macd_divergence(df, timeframe)
        
        result = {
            'has_divergence': rsi_div['bullish'] or rsi_div['bearish'] or macd_div['bullish'] or macd_div['bearish'],
            'bullish': rsi_div['bullish'] or macd_div['bullish'],
            'bearish': rsi_div['bearish'] or macd_div['bearish'],
            'strength': max(rsi_div.get('strength', 0), macd_div.get('strength', 0)),
            'signals': []
        }
        
        unique_signals = set()
        if rsi_div['bullish']:
            unique_signals.add(rsi_div['description'])
        if rsi_div['bearish']:
            unique_signals.add(rsi_div['description'])
        if macd_div['bullish']:
            unique_signals.add(macd_div['description'])
        if macd_div['bearish']:
            unique_signals.add(macd_div['description'])
        
        result['signals'] = list(unique_signals)
        return result


# ============== АНАЛИЗАТОР ИМБАЛАНСОВ ==============

class ImbalanceAnalyzer:
    """Анализатор дисбаланса спроса и предложения (имбалансов)"""
    
    def __init__(self, settings: Dict = None):
        self.settings = settings or IMBALANCE_SETTINGS
        self.threshold_buy = self.settings.get('threshold_buy', 0.3)
        self.threshold_sell = self.settings.get('threshold_sell', -0.3)
        self.stack_threshold = self.settings.get('stack_threshold', 3)
        self.lookback_bars = self.settings.get('lookback_bars', 20)
        self.weight_higher_tf = self.settings.get('weight_higher_tf', 1.5)
        
    def calculate_imbalance(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Расчет имбаланса на основе свечных данных
        """
        # Аппроксимация buy/sell volume (в реальности нужно из стакана)
        df['buy_volume'] = np.where(
            df['close'] > df['open'],
            df['volume'] * 0.7,
            df['volume'] * 0.3
        )
        
        df['sell_volume'] = df['volume'] - df['buy_volume']
        
        # Расчет имбаланса
        df['imbalance'] = (df['buy_volume'] - df['sell_volume']) / df['volume']
        
        # Нормализованный имбаланс от -1 до 1
        df['imbalance_strength'] = df['imbalance'].abs() * 100
        
        # Сглаженный имбаланс
        df['imbalance_sma'] = df['imbalance'].rolling(window=14).mean()
        
        # Определяем типы имбалансов
        df['imbalance_type'] = 'neutral'
        df.loc[df['imbalance'] > self.threshold_buy, 'imbalance_type'] = 'strong_buy'
        df.loc[df['imbalance'] > self.threshold_buy * 2, 'imbalance_type'] = 'extreme_buy'
        df.loc[df['imbalance'] < self.threshold_sell, 'imbalance_type'] = 'strong_sell'
        df.loc[df['imbalance'] < self.threshold_sell * 2, 'imbalance_type'] = 'extreme_sell'
        
        return df
    
    def detect_imbalance_stacks(self, df: pd.DataFrame) -> List[Dict]:
        """Обнаружение стеков имбалансов (несколько подряд)"""
        stacks = []
        
        for i in range(max(0, len(df) - self.lookback_bars), len(df)):
            window = df.iloc[max(0, i-self.lookback_bars):i]
            
            # Считаем подряд идущие имбалансы одного типа
            buy_streak = 0
            sell_streak = 0
            max_buy_streak = 0
            max_sell_streak = 0
            
            for _, row in window.iterrows():
                if row['imbalance'] > self.threshold_buy:
                    buy_streak += 1
                    sell_streak = 0
                    max_buy_streak = max(max_buy_streak, buy_streak)
                elif row['imbalance'] < self.threshold_sell:
                    sell_streak += 1
                    buy_streak = 0
                    max_sell_streak = max(max_sell_streak, sell_streak)
                else:
                    buy_streak = 0
                    sell_streak = 0
            
            if max_buy_streak >= self.stack_threshold:
                stacks.append({
                    'type': 'buy_stack',
                    'strength': max_buy_streak * 10,
                    'count': max_buy_streak,
                    'description': f"Стек бычьих имбалансов ({max_buy_streak} свечей подряд)"
                })
            
            if max_sell_streak >= self.stack_threshold:
                stacks.append({
                    'type': 'sell_stack',
                    'strength': max_sell_streak * 10,
                    'count': max_sell_streak,
                    'description': f"Стек медвежьих имбалансов ({max_sell_streak} свечей подряд)"
                })
        
        return stacks
    
    def analyze(self, dataframes: Dict[str, pd.DataFrame]) -> Dict:
        """Полный анализ имбалансов на всех таймфреймах"""
        result = {
            'has_imbalance': False,
            'signals': [],
            'strength': 0,
            'alignment': {}
        }
        
        for tf_name, df in dataframes.items():
            if df is None or df.empty:
                continue
            
            df_with_imb = self.calculate_imbalance(df)
            last = df_with_imb.iloc[-1]
            
            if abs(last['imbalance']) > self.threshold_buy:
                result['has_imbalance'] = True
                imb_type = "бычий" if last['imbalance'] > 0 else "медвежий"
                strength = abs(last['imbalance']) * 100
                
                # Бонус для старших таймфреймов
                if tf_name in ['daily', 'weekly']:
                    strength *= self.weight_higher_tf
                
                signal = {
                    'timeframe': tf_name,
                    'type': imb_type,
                    'strength': min(strength, 100),
                    'description': f"{imb_type.capitalize()} имбаланс на {tf_name}: {strength:.1f}%"
                }
                result['signals'].append(signal['description'])
                result['strength'] = max(result['strength'], signal['strength'])
                result['alignment'][tf_name] = imb_type
        
        # Проверяем согласованность
        if len(result['alignment']) >= 2:
            types = list(result['alignment'].values())
            if all(t == types[0] for t in types):
                result['has_imbalance'] = True
                result['strength'] = min(result['strength'] + 20, 100)
                result['signals'].append(f"✅ Согласованные имбалансы на всех ТФ: {types[0]}")
        
        # Стеки имбалансов на старших ТФ
        for tf_name in ['daily', 'weekly']:
            if tf_name in dataframes:
                stacks = self.detect_imbalance_stacks(dataframes[tf_name])
                for stack in stacks:
                    result['has_imbalance'] = True
                    result['signals'].append(stack['description'])
                    result['strength'] = max(result['strength'], stack['strength'])
        
        return result


# ============== АНАЛИЗАТОР ЛИКВИДНОСТИ ==============

class LiquidityAnalyzer:
    """Анализ зон ликвидности и сборов стоп-лоссов"""
    
    def __init__(self, settings: Dict = None):
        self.settings = settings or LIQUIDITY_SETTINGS
        self.lookback_bars = self.settings.get('lookback_bars', 100)
        self.sweep_threshold = self.settings.get('sweep_retrace_threshold', 1.0)
        self.consolidation_threshold = self.settings.get('consolidation_threshold', 0.5)
        self.zone_distance = self.settings.get('zone_distance', 1.0)
        self.swing_levels = {}
        self.liquidity_zones = {}
        self.sweep_history = {}
    
    def find_swing_levels(self, df: pd.DataFrame) -> Dict:
        """Поиск свинг-хаев и свинг-лоев"""
        recent = df.tail(self.lookback_bars)
        
        swing_highs = []
        swing_lows = []
        
        for i in range(10, len(recent) - 10):
            # Локальный максимум
            if (recent['high'].iloc[i] > recent['high'].iloc[i-5:i].max() and 
                recent['high'].iloc[i] > recent['high'].iloc[i+1:i+6].max()):
                swing_highs.append({
                    'price': recent['high'].iloc[i],
                    'time': recent.index[i],
                    'strength': self._calculate_strength(recent, i)
                })
            
            # Локальный минимум
            if (recent['low'].iloc[i] < recent['low'].iloc[i-5:i].min() and 
                recent['low'].iloc[i] < recent['low'].iloc[i+1:i+6].min()):
                swing_lows.append({
                    'price': recent['low'].iloc[i],
                    'time': recent.index[i],
                    'strength': self._calculate_strength(recent, i)
                })
        
        return {
            'swing_highs': swing_highs[-10:],
            'swing_lows': swing_lows[-10:],
            'current_price': recent['close'].iloc[-1]
        }
    
    def _calculate_strength(self, df: pd.DataFrame, index: int) -> int:
        """Расчет силы уровня"""
        strength = 30
        volume = df['volume'].iloc[index]
        avg_volume = df['volume'].rolling(20).mean().iloc[index]
        
        if volume > avg_volume * 1.5:
            strength += 20
        if volume > avg_volume * 2:
            strength += 30
        
        return min(strength, 100)
    
    def detect_sweeps(self, symbol: str, df: pd.DataFrame) -> List[Dict]:
        """Обнаружение сборов ликвидности"""
        sweeps = []
        levels = self.find_swing_levels(df)
        current_price = df['close'].iloc[-1]
        
        # Сбор ликвидности шортистов (пробой хая и возврат)
        for high in levels['swing_highs']:
            if high['price'] < current_price * 1.02:  # цена рядом с уровнем
                # Проверяем, был ли пробой
                penetrated = any(df['high'].iloc[-10:] > high['price'])
                retraced = current_price < high['price'] * 1.005
                
                if penetrated and retraced:
                    sweeps.append({
                        'type': 'bearish_sweep',
                        'level': high['price'],
                        'strength': high['strength'],
                        'description': f"Сбор ликвидности шортистов на {high['price']:.2f}"
                    })
        
        # Сбор ликвидности лонгистов (пробой лоя и возврат)
        for low in levels['swing_lows']:
            if low['price'] > current_price * 0.98:
                penetrated = any(df['low'].iloc[-10:] < low['price'])
                retraced = current_price > low['price'] * 0.995
                
                if penetrated and retraced:
                    sweeps.append({
                        'type': 'bullish_sweep',
                        'level': low['price'],
                        'strength': low['strength'],
                        'description': f"Сбор ликвидности лонгистов на {low['price']:.2f}"
                    })
        
        return sweeps
    
    def find_liquidity_zones(self, df: pd.DataFrame) -> List[Dict]:
        """Поиск зон накопленной ликвидности"""
        zones = []
        recent = df.tail(100)
        
        for i in range(20, len(recent) - 20):
            window = recent.iloc[i-20:i+20]
            price_range = window['high'].max() - window['low'].min()
            avg_range = window['atr'].mean() if 'atr' in window.columns else price_range
            
            # Зона консолидации
            if price_range < avg_range * self.consolidation_threshold:
                zone_price = window['close'].mean()
                touches = sum(1 for _, row in window.iterrows() 
                            if abs(row['close'] - zone_price) / zone_price < 0.005)
                
                zones.append({
                    'price': zone_price,
                    'touches': touches,
                    'strength': min(30 + touches * 10, 100),
                    'description': f"Зона накопления {zone_price:.2f} ({touches} касаний)"
                })
        
        return zones[-5:]
    
    def analyze(self, symbol: str, df: pd.DataFrame) -> Dict:
        """Полный анализ ликвидности"""
        result = {
            'has_signal': False,
            'signals': [],
            'strength': 0,
            'sweeps': [],
            'zones': []
        }
        
        # Сборы ликвидности
        sweeps = self.detect_sweeps(symbol, df)
        if sweeps:
            result['has_signal'] = True
            result['sweeps'] = sweeps
            for sweep in sweeps:
                result['signals'].append(sweep['description'])
                result['strength'] = max(result['strength'], sweep['strength'])
        
        # Зоны ликвидности
        zones = self.find_liquidity_zones(df)
        if zones:
            result['zones'] = zones
            current_price = df['close'].iloc[-1]
            
            for zone in zones:
                distance = abs(current_price - zone['price']) / zone['price'] * 100
                if distance < self.zone_distance:
                    result['has_signal'] = True
                    result['signals'].append(f"⚠️ Цена в зоне ликвидности {zone['price']:.2f}")
                    result['strength'] = max(result['strength'], zone['strength'])
        
        return result


# ============== БАЗОВЫЙ КЛАСС ДЛЯ БИРЖ ==============

class BaseExchangeFetcher:
    def __init__(self, name: str):
        self.name = name
        self.available_pairs = []
    
    async def fetch_all_pairs(self) -> List[str]:
        raise NotImplementedError
    
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        raise NotImplementedError
    
    async def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        return 0.0
    
    async def fetch_ticker(self, symbol: str) -> Dict:
        return {}
    
    async def fetch_contract_info(self, symbol: str) -> Dict:
        return {
            'max_leverage': 100,
            'min_amount': 5.0,
            'max_amount': 2_000_000,
            'contract_size': 0.001
        }
    
    async def fetch_open_interest(self, symbol: str) -> Optional[float]:
        return None
    
    async def fetch_liquidations(self, symbol: str) -> Dict:
        return {'long': 0, 'short': 0}
    
    async def fetch_order_book(self, symbol: str) -> Dict:
        return {'bid_ask_ratio': 1.0}
    
    async def get_price_with_source(self, symbol: str, http_price: float = None) -> Dict:
        return {
            'price': http_price,
            'source': 'http',
            'time': datetime.now().strftime('%H:%M:%S')
        }
    
    async def close(self):
        pass


# ============== BINGX FUTURES ==============

class BingxFetcher(BaseExchangeFetcher):
    def __init__(self):
        super().__init__("BingX")
        self.exchange = ccxt.bingx({
            'apiKey': os.getenv('BINGX_API_KEY'),
            'secret': os.getenv('BINGX_SECRET_KEY'),
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',
                'adjustForTimeDifference': True
            }
        })
        logger.info("✅ BingX Futures инициализирован")
    
    async def fetch_all_pairs(self) -> List[str]:
        try:
            markets = await self.exchange.load_markets()
            usdt_pairs = []
            for symbol, market in markets.items():
                if (market['quote'] == 'USDT' and 
                    market['active'] and 
                    market['type'] in ['swap', 'future']):
                    usdt_pairs.append(symbol)
            
            logger.info(f"📊 BingX Futures: загружено {len(usdt_pairs)} пар")
            return usdt_pairs
        except Exception as e:
            logger.error(f"❌ BingX ошибка: {e}")
            return []
    
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < 20:
                return None
            
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            logger.error(f"Ошибка BingX {symbol}: {e}")
            return None
    
    async def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        try:
            funding = await self.exchange.fetch_funding_rate(symbol)
            return funding['fundingRate'] if funding else 0.0
        except:
            return 0.0
    
    async def fetch_ticker(self, symbol: str) -> Dict:
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return {
                'volume_24h': ticker.get('quoteVolume'),
                'price_change_24h': ticker.get('percentage'),
                'last': ticker.get('last')
            }
        except:
            return {}
    
    async def fetch_contract_info(self, symbol: str) -> Dict:
        try:
            markets = await self.exchange.load_markets()
            market = markets.get(symbol, {})
            
            return {
                'max_leverage': market.get('limits', {}).get('leverage', {}).get('max', 100),
                'min_amount': market.get('limits', {}).get('amount', {}).get('min', 5.0),
                'max_amount': market.get('limits', {}).get('amount', {}).get('max', 2_000_000),
                'contract_size': market.get('contractSize', 0.001)
            }
        except:
            return super().fetch_contract_info(symbol)
    
    async def fetch_open_interest(self, symbol: str) -> Optional[float]:
        try:
            oi = await self.exchange.fetch_open_interest(symbol)
            return oi.get('openInterestAmount', 0)
        except:
            return None
    
    async def close(self):
        await self.exchange.close()


# ============== МУЛЬТИТАЙМФРЕЙМ АНАЛИЗАТОР ==============

class MultiTimeframeAnalyzer:
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df['rsi'] = calculate_rsi(df['close'], INDICATOR_SETTINGS['rsi_period'])
        
        macd_line, signal_line, hist = calculate_macd(
            df['close'], 
            INDICATOR_SETTINGS['macd_fast'],
            INDICATOR_SETTINGS['macd_slow'],
            INDICATOR_SETTINGS['macd_signal']
        )
        df['MACD_12_26_9'] = macd_line
        df['MACDs_12_26_9'] = signal_line
        df['MACDh_12_26_9'] = hist
        
        for period in INDICATOR_SETTINGS['ema_periods']:
            df[f'ema_{period}'] = calculate_ema(df['close'], period)
        
        df['sma_50'] = calculate_sma(df['close'], 50)
        df['sma_200'] = calculate_sma(df['close'], 200)
        
        sma, upper, lower = calculate_bollinger_bands(
            df['close'], 
            INDICATOR_SETTINGS['bollinger_period'],
            INDICATOR_SETTINGS['bollinger_std']
        )
        df['BBL_20_2.0'] = lower
        df['BBM_20_2.0'] = sma
        df['BBU_20_2.0'] = upper
        
        df['atr'] = calculate_atr(df['high'], df['low'], df['close'], INDICATOR_SETTINGS['atr_period'])
        df['volume_sma'] = calculate_sma(df['volume'], INDICATOR_SETTINGS['volume_sma_period'])
        df['volume_ratio'] = df['volume'] / df['volume_sma']
        
        if FEATURES['advanced']['vwap']:
            df['vwap'] = calculate_vwap(df)
        
        return df
    
    def analyze_timeframe_alignment(self, dataframes: Dict[str, pd.DataFrame]) -> Dict:
        alignment = {
            'trend_alignment': 0,
            'hourly_trend': None,
            'daily_trend': None,
            'weekly_trend': None,
            'signals': []
        }
        
        if 'hourly' in dataframes and not dataframes['hourly'].empty:
            df_h = dataframes['hourly'].iloc[-1]
            alignment['hourly_trend'] = 'ВОСХОДЯЩИЙ 📈' if df_h['ema_9'] > df_h['ema_21'] else 'НИСХОДЯЩИЙ 📉'
        
        if 'daily' in dataframes and not dataframes['daily'].empty:
            df_d = dataframes['daily'].iloc[-1]
            if df_d['close'] > df_d['ema_200']:
                alignment['daily_trend'] = 'ВОСХОДЯЩИЙ 📈'
                if df_d['ema_9'] > df_d['ema_21']:
                    alignment['signals'].append("Дневной тренд восходящий (выше EMA 200)")
            else:
                alignment['daily_trend'] = 'НИСХОДЯЩИЙ 📉'
                if df_d['ema_9'] < df_d['ema_21']:
                    alignment['signals'].append("Дневной тренд нисходящий (ниже EMA 200)")
        
        if 'weekly' in dataframes and not dataframes['weekly'].empty:
            df_w = dataframes['weekly'].iloc[-1]
            if df_w['close'] > df_w['ema_200']:
                alignment['weekly_trend'] = 'ВОСХОДЯЩИЙ 📈'
                alignment['signals'].append("НЕДЕЛЬНЫЙ ТРЕНД ВОСХОДЯЩИЙ (сильный сигнал)")
            else:
                alignment['weekly_trend'] = 'НИСХОДЯЩИЙ 📉'
                alignment['signals'].append("НЕДЕЛЬНЫЙ ТРЕНД НИСХОДЯЩИЙ (сильный сигнал)")
        
        trends = [t for t in [alignment['hourly_trend'], alignment['daily_trend'], alignment['weekly_trend']] if t]
        if trends:
            bullish = trends.count('ВОСХОДЯЩИЙ 📈')
            bearish = trends.count('НИСХОДЯЩИЙ 📉')
            alignment['trend_alignment'] = (max(bullish, bearish) / len(trends)) * 100
        return alignment
    
    def generate_signal(self, dataframes: Dict[str, pd.DataFrame], metadata: Dict, symbol: str, exchange: str) -> Optional[Dict]:
        if 'current' not in dataframes or dataframes['current'].empty:
            return None
        
        df = dataframes['current']
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        
        alignment = self.analyze_timeframe_alignment(dataframes)
        
        confidence = 50
        reasons = []
        direction = 'NEUTRAL'
        
        # RSI
        if pd.notna(last['rsi']):
            if last['rsi'] < INDICATOR_SETTINGS['rsi_oversold']:
                reasons.append(f"RSI перепродан ({last['rsi']:.1f})")
                confidence += INDICATOR_WEIGHTS['rsi']
            elif last['rsi'] > INDICATOR_SETTINGS['rsi_overbought']:
                reasons.append(f"RSI перекуплен ({last['rsi']:.1f})")
                confidence += INDICATOR_WEIGHTS['rsi']
        
        # MACD
        if pd.notna(last['MACD_12_26_9']) and pd.notna(last['MACDs_12_26_9']):
            if last['MACD_12_26_9'] > last['MACDs_12_26_9'] and prev['MACD_12_26_9'] <= prev['MACDs_12_26_9']:
                reasons.append("Бычье пересечение MACD")
                confidence += INDICATOR_WEIGHTS['macd']
            elif last['MACD_12_26_9'] < last['MACDs_12_26_9'] and prev['MACD_12_26_9'] >= prev['MACDs_12_26_9']:
                reasons.append("Медвежье пересечение MACD")
                confidence += INDICATOR_WEIGHTS['macd']
        
        # EMA
        if last['ema_9'] > last['ema_21'] and prev['ema_9'] <= prev['ema_21']:
            reasons.append("Бычье пересечение EMA (9/21)")
            confidence += INDICATOR_WEIGHTS['ema_cross']
        elif last['ema_9'] < last['ema_21'] and prev['ema_9'] >= prev['ema_21']:
            reasons.append("Медвежье пересечение EMA (9/21)")
            confidence += INDICATOR_WEIGHTS['ema_cross']
        
        # Объем
        if last['volume_ratio'] > 1.5:
            reasons.append(f"Объем x{last['volume_ratio']:.1f} от нормы")
            confidence += INDICATOR_WEIGHTS['volume']
        
        # VWAP
        if FEATURES['advanced']['vwap'] and 'vwap' in df.columns:
            if last['close'] > last['vwap']:
                reasons.append(f"Цена выше VWAP ({last['vwap']:.2f})")
                confidence += 10
            else:
                reasons.append(f"Цена ниже VWAP ({last['vwap']:.2f})")
                confidence += 10
        
        # Сигналы от старших таймфреймов
        for signal in alignment['signals']:
            reasons.append(f"📊 {signal}")
            if "НЕДЕЛЬНЫЙ" in signal:
                confidence += INDICATOR_WEIGHTS['weekly_trend']
            elif "Дневной" in signal:
                confidence += INDICATOR_WEIGHTS['daily_trend']
        
        # Согласованность трендов
        if alignment['trend_alignment'] > 70:
            reasons.append(f"✅ Тренды согласованы ({alignment['trend_alignment']:.0f}%)")
            confidence += INDICATOR_WEIGHTS['trend_alignment']
        
        # Фандинг
        funding = metadata.get('funding_rate')
        if funding is not None and funding != 0:
            funding_pct = funding * 100
            if funding > 0.001:
                reasons.append(f"💰 Позитивный фандинг ({funding_pct:.4f}%)")
                if confidence > 60:
                    direction = 'SHORT 📉'
            elif funding < -0.001:
                reasons.append(f"💰 Негативный фандинг ({funding_pct:.4f}%)")
                if confidence > 60:
                    direction = 'LONG 📈'
        
        # Изменение цены
        price_change = metadata.get('price_change_24h')
        if price_change and abs(price_change) > 5:
            if price_change > 5:
                reasons.append(f"📈 Рост за 24ч: +{price_change:.1f}%")
            elif price_change < -5:
                reasons.append(f"📉 Падение за 24ч: {price_change:.1f}%")
        
        # Определение направления
        bullish_keywords = ['перепродан', 'Бычье', 'восходящий', 'негативный фандинг', 'выше VWAP']
        bearish_keywords = ['перекуплен', 'Медвежье', 'нисходящий', 'позитивный фандинг', 'ниже VWAP']
        bullish = sum(1 for r in reasons if any(k in r for k in bullish_keywords))
        bearish = sum(1 for r in reasons if any(k in r for k in bearish_keywords))
        
        if bullish > bearish and confidence >= MIN_CONFIDENCE:
            if alignment['weekly_trend'] == 'ВОСХОДЯЩИЙ 📈':
                direction = 'Разворот LONG 📈'
                reasons.append("🔄 Подтверждение разворота недельным трендом")
            else:
                direction = 'LONG 📈'
        elif bearish > bullish and confidence >= MIN_CONFIDENCE:
            if alignment['weekly_trend'] == 'НИСХОДЯЩИЙ 📉':
                direction = 'Разворот SHORT 📉'
                reasons.append("🔄 Подтверждение разворота недельным трендом")
            else:
                direction = 'SHORT 📉'
        
        signal_strength = min(confidence + alignment['trend_alignment'] / 2, 100)
        atr = last['atr'] if pd.notna(last['atr']) else (last['high'] - last['low']) * 0.3
        current_price = last['close']
        targets = {}
        
        if direction != 'NEUTRAL':
            if 'LONG' in direction:
                targets['target_1'] = round(current_price + atr * 1.5, 2)
                targets['target_2'] = round(current_price + atr * 3.0, 2)
                targets['stop_loss'] = round(current_price - atr * 1.0, 2)
            else:
                targets['target_1'] = round(current_price - atr * 1.5, 2)
                targets['target_2'] = round(current_price - atr * 3.0, 2)
                targets['stop_loss'] = round(current_price + atr * 1.0, 2)
        
        # Мощность сигнала
        signal_power = "⚡️"
        if signal_strength >= 90:
            signal_power = "🔥🔥🔥 ОЧЕНЬ СИЛЬНЫЙ"
        elif signal_strength >= 80:
            signal_power = "🔥🔥 СИЛЬНЫЙ"
        elif signal_strength >= 70:
            signal_power = "🔥 СРЕДНИЙ"
        
        return {
            'symbol': symbol,
            'exchange': exchange,
            'price': current_price,
            'direction': direction,
            'signal_power': signal_power,
            'confidence': round(confidence, 1),
            'signal_strength': round(signal_strength, 1),
            'reasons': reasons[:8],
            'funding_rate': metadata.get('funding_rate', 0),
            'volume_24h': metadata.get('volume_24h', 0),
            'price_change_24h': metadata.get('price_change_24h', 0),
            'max_leverage': 100,
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'alignment': alignment,
            **targets
        }


# ============== ОСНОВНОЙ КЛАСС БОТА ==============

class MultiExchangeScannerBot:
    def __init__(self):
        self.fetchers = {}
        self.analyzer = MultiTimeframeAnalyzer()
        self.telegram_bot = Bot(token=TELEGRAM_TOKEN)
        self.last_signals = {}
        
        if FEATURES['exchanges'].get('bingx', {}).get('enabled', False):
            self.fetchers['BingX'] = BingxFetcher()
        
        if FEATURES['advanced']['divergence']:
            self.divergence = DivergenceAnalyzer()
            logger.info("✅ Анализатор дивергенций инициализирован")
        else:
            self.divergence = None
        
        if FEATURES['advanced']['imbalance']:
            self.imbalance = ImbalanceAnalyzer(IMBALANCE_SETTINGS)
            logger.info("✅ Анализатор имбалансов инициализирован")
        else:
            self.imbalance = None
        
        if FEATURES['advanced']['liquidity']:
            self.liquidity = LiquidityAnalyzer(LIQUIDITY_SETTINGS)
            logger.info("✅ Анализатор ликвидности инициализирован")
        else:
            self.liquidity = None
    
    def extract_coin(self, symbol: str) -> str:
        if ':USDT' in symbol:
            return symbol.split('/')[0]
        elif '/' in symbol:
            return symbol.split('/')[0]
        else:
            return symbol.replace('USDT', '')
    
    def format_compact(self, num: float) -> str:
        if num > 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num > 1_000:
            return f"{num/1_000:.1f}K"
        else:
            return f"{num:.0f}"
    
    def format_message(self, signal: Dict, price_source: Dict = None, 
                       contract_info: Dict = None, pump_percent: float = None) -> Tuple[str, InlineKeyboardMarkup]:
        
        dir_emoji = '🟢' if 'LONG' in signal['direction'] else '🔴' if 'SHORT' in signal['direction'] else '⚪'
        coin = self.extract_coin(signal['symbol'])
        price_formatted = f"{signal['price']:.2f}"
        
        pump_text = ""
        if pump_percent:
            pump_type = "PUMP" if pump_percent > 0 else "DUMP"
            pump_text = f"{pump_type} {pump_percent:+.1f}% "
        
        exchange_link = REF_LINKS.get(signal['exchange'], '#')
        
        # Параметры в одну строку с кликабельной биржей
        if DISPLAY_SETTINGS['show_exchange_link']:
            params_text = f"└ <a href='{exchange_link}'>{signal['exchange']}</a>"
        else:
            params_text = f"└ {signal['exchange']}"
        
        if contract_info:
            max_lev = contract_info.get('max_leverage', 100)
            min_amt = contract_info.get('min_amount', 5)
            max_amt = contract_info.get('max_amount', 2_000_000)
            params_text += f" {max_lev}x / {min_amt:.0f}$ / {self.format_compact(max_amt)}"
        
        if signal.get('volume_24h'):
            params_text += f" / {self.format_compact(signal['volume_24h'])}"
        
        if signal.get('funding_rate'):
            funding = signal['funding_rate'] * 100
            color = "🟢" if funding > 0 else "🔴" if funding < 0 else "⚪"
            params_text += f" / {color} {funding:.3f}%"
        
        lines = [
            f"{dir_emoji} `{coin}` {pump_text}{signal['signal_power']}",
            params_text + "\n",
            f"📊 *Направление:* {signal['direction']}",
            f"🕓 *Таймфрейм:* {TIMEFRAME}",
            f"💰 *Цена:* `{price_formatted}`\n",
            f"🎯 *Цели:* {signal.get('target_1', '')} | {signal.get('target_2', '')} | SL {signal.get('stop_loss', '')}\n"
        ]
        
        if signal['reasons']:
            lines.append(f"💡 *Причины:* {', '.join(signal['reasons'][:3])}")
        
        # Клавиатура
        keyboard = []
        
        if DISPLAY_SETTINGS['buttons']['copy']:
            keyboard.append([InlineKeyboardButton(f"📋 Скопировать {coin}", callback_data=f"copy_{coin}")])
        
        if DISPLAY_SETTINGS['buttons']['trade']:
            if signal['exchange'] in REF_LINKS:
                keyboard.append([InlineKeyboardButton(f"🚀 Торговать на {signal['exchange']}", url=REF_LINKS[signal['exchange']])])
        
        action_row = []
        if DISPLAY_SETTINGS['buttons']['refresh']:
            action_row.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{coin}"))
        if DISPLAY_SETTINGS['buttons']['details']:
            action_row.append(InlineKeyboardButton("📊 Детали", callback_data=f"details_{coin}"))
        
        if action_row:
            keyboard.append(action_row)
        
        return "\n".join(lines), InlineKeyboardMarkup(keyboard)
    
    async def get_detailed_analysis(self, fetcher, symbol: str, coin: str, signal_time: str = None) -> Tuple[str, InlineKeyboardMarkup]:
        try:
            dataframes = {}
            for tf_name, tf_value in TIMEFRAMES.items():
                limit = 500 if tf_name == 'current' else 200
                df = await fetcher.fetch_ohlcv(symbol, tf_value, limit)
                if df is not None and not df.empty:
                    df = self.analyzer.calculate_indicators(df)
                    dataframes[tf_name] = df
            
            if not dataframes:
                return f"❌ Нет данных для {coin}", None
            
            contract_info = await fetcher.fetch_contract_info(symbol)
            open_interest = await fetcher.fetch_open_interest(symbol)
            ticker = await fetcher.fetch_ticker(symbol)
            funding = await fetcher.fetch_funding_rate(symbol)
            
            df_current = dataframes['current']
            last = df_current.iloc[-1]
            
            lines = []
            lines.append(f"📊 *ДЕТАЛЬНЫЙ АНАЛИЗ {coin}*")
            if signal_time:
                lines.append(f"⏱️ Время сигнала: `{signal_time}`")
            lines.append(f"⏱️ Время обновления: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n")
            
            # Параметры контракта
            lines.append("⚡️ *ПАРАМЕТРЫ КОНТРАКТА:*")
            lines.append(f"└ Макс. плечо: `{contract_info.get('max_leverage', 100)}x`")
            lines.append(f"└ Мин. вход: `{contract_info.get('min_amount', 5):.2f} USDT`")
            lines.append(f"└ Макс. вход: `{self.format_compact(contract_info.get('max_amount', 2_000_000))} USDT`")
            
            # Торговые данные
            lines.append("\n📈 *ТОРГОВЫЕ ДАННЫЕ:*")
            if ticker:
                lines.append(f"└ Объем 24ч: `{self.format_compact(ticker.get('volume_24h', 0))} USDT`")
                if ticker.get('price_change_24h'):
                    change = ticker['price_change_24h']
                    emoji = "📈" if change > 0 else "📉"
                    lines.append(f"└ Изменение 24ч: {emoji} `{change:+.2f}%`")
            
            if open_interest:
                lines.append(f"└ Открытый интерес: `{self.format_compact(open_interest)} USDT`")
            
            if funding:
                funding_pct = funding * 100
                color = "🟢" if funding_pct > 0 else "🔴" if funding_pct < 0 else "⚪"
                lines.append(f"└ Ставка фондирования: `{color} {funding_pct:.4f}%`")
            
            # Технический анализ
            lines.append("\n📊 *ТЕХНИЧЕСКИЙ АНАЛИЗ:*")
            lines.append(f"└ RSI(14): `{last['rsi']:.1f}`")
            lines.append(f"└ MACD: `{last['MACD_12_26_9']:.2f}` | Сигнал: `{last['MACDs_12_26_9']:.2f}`")
            lines.append(f"└ EMA 9/21: `{last['ema_9']:.2f}` / `{last['ema_21']:.2f}`")
            lines.append(f"└ ATR: `{last['atr']:.2f}`")
            lines.append(f"└ Объемный коэффициент: `x{last['volume_ratio']:.2f}`")
            
            # Имбалансы
            if self.imbalance and FEATURES['advanced']['imbalance']:
                imb = self.imbalance.analyze(dataframes)
                if imb['has_imbalance']:
                    lines.append("\n⚖️ *ИМБАЛАНСЫ:*")
                    for sig in imb['signals'][:3]:
                        lines.append(f"└ {sig}")
            
            # Ликвидность
            if self.liquidity and FEATURES['advanced']['liquidity']:
                liq = self.liquidity.analyze(symbol, df_current)
                if liq['has_signal']:
                    lines.append("\n📊 *ЛИКВИДНОСТЬ:*")
                    for sig in liq['signals'][:3]:
                        lines.append(f"└ {sig}")
            
            # Дивергенции
            if self.divergence and FEATURES['advanced']['divergence']:
                div = self.divergence.analyze(df_current, '15m')
                if div['has_divergence']:
                    lines.append("\n🔄 *ДИВЕРГЕНЦИИ:*")
                    for d in div['signals']:
                        lines.append(f"└ {d}")
            
            # Клавиатура
            keyboard = [
                [InlineKeyboardButton("🔝 Вернуться к сигналу", callback_data=f"back_{coin}")],
                [InlineKeyboardButton("🔄 Обновить детали", callback_data=f"refresh_details_{coin}")]
            ]
            
            return "\n".join(lines), InlineKeyboardMarkup(keyboard)
            
        except Exception as e:
            logger.error(f"Ошибка детального анализа {symbol}: {e}")
            return f"❌ Ошибка анализа: {e}", None
    
    async def scan_exchange(self, name: str, fetcher: BaseExchangeFetcher) -> List[Dict]:
        logger.info(f"🔍 Сканирую {name}...")
        signals = []
        
        try:
            pairs = await fetcher.fetch_all_pairs()
            if not pairs:
                logger.warning(f"⚠️ {name}: нет пар для анализа")
                return []
            
            scan_count = min(PAIRS_TO_SCAN, len(pairs))
            logger.info(f"📊 {name}: анализирую {scan_count} пар из {len(pairs)}")
            
            for i, pair in enumerate(pairs[:PAIRS_TO_SCAN]):
                try:
                    dataframes = {}
                    for tf_name, tf_value in TIMEFRAMES.items():
                        limit = 200 if tf_name == 'current' else 100
                        df = await fetcher.fetch_ohlcv(pair, tf_value, limit)
                        if df is not None and not df.empty:
                            df = self.analyzer.calculate_indicators(df)
                            dataframes[tf_name] = df
                    
                    if not dataframes:
                        continue
                    
                    funding = await fetcher.fetch_funding_rate(pair)
                    ticker = await fetcher.fetch_ticker(pair)
                    
                    metadata = {
                        'funding_rate': funding,
                        'volume_24h': ticker.get('volume_24h'),
                        'price_change_24h': ticker.get('price_change_24h')
                    }
                    
                    signal = self.analyzer.generate_signal(dataframes, metadata, pair, name)
                    
                    if not signal:
                        continue
                    
                    # Добавляем дополнительные анализы
                    if self.imbalance and FEATURES['advanced']['imbalance']:
                        imb = self.imbalance.analyze(dataframes)
                        if imb['has_imbalance']:
                            signal['imbalance'] = imb
                            signal['confidence'] = min(signal['confidence'] + imb['strength'] / 10, 100)
                    
                    if self.liquidity and FEATURES['advanced']['liquidity']:
                        liq = self.liquidity.analyze(pair, dataframes['current'])
                        if liq['has_signal']:
                            signal['liquidity'] = liq
                            signal['confidence'] = min(signal['confidence'] + liq['strength'] / 10, 100)
                    
                    if signal['confidence'] >= MIN_CONFIDENCE:
                        signals.append(signal)
                        logger.info(f"✅ {name} {pair} - {signal['direction']} ({signal['confidence']}%)")
                    
                    if (i + 1) % 10 == 0:
                        logger.info(f"📊 Прогресс {name}: {i + 1}/{scan_count}")
                    
                    await asyncio.sleep(0.3)
                    
                except Exception as e:
                    logger.error(f"Ошибка анализа {name} {pair}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Ошибка сканирования {name}: {e}")
        
        return signals
    
    async def scan_all(self) -> List[Dict]:
        logger.info("="*50)
        logger.info("🚀 НАЧАЛО МУЛЬТИ-БИРЖЕВОГО СКАНИРОВАНИЯ")
        logger.info("="*50)
        
        all_signals = []
        
        for name, fetcher in self.fetchers.items():
            try:
                signals = await self.scan_exchange(name, fetcher)
                all_signals.extend(signals)
            except Exception as e:
                logger.error(f"❌ Критическая ошибка {name}: {e}")
                continue
        
        all_signals.sort(key=lambda x: x['signal_strength'], reverse=True)
        
        logger.info("="*50)
        logger.info(f"🎯 ВСЕГО НАЙДЕНО СИГНАЛОВ: {len(all_signals)}")
        logger.info("="*50)
        
        return all_signals
    
    async def send_signal(self, signal: Dict, pump_only: bool = False):
        if pump_only and not signal.get('pump_dump'):
            return
        
        coin = self.extract_coin(signal['symbol'])
        self.last_signals[coin] = {
            'symbol': signal['symbol'],
            'signal': signal,
            'time': datetime.now()
        }
        
        contract_info = None
        for fetcher in self.fetchers.values():
            if fetcher.name == signal['exchange']:
                contract_info = await fetcher.fetch_contract_info(signal['symbol'])
                break
        
        pump_percent = None
        if signal.get('pump_dump'):
            pump_percent = signal['pump_dump'][0]['change_percent'] if signal['pump_dump'] else None
        
        msg, keyboard = self.format_message(signal, None, contract_info, pump_percent)
        
        await self.telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode='HTML',
            reply_markup=keyboard
        )
        logger.info(f"✅ Отправлен сигнал: {signal['symbol']}")
    
    async def scheduled(self):
        logger.info(f"🕐 Запуск мульти-биржевого анализа")
        signals = await self.scan_all()
        
        if signals:
            for i, signal in enumerate(signals[:5]):
                await self.send_signal(signal, pump_only=False)
                if i < len(signals[:5]) - 1:
                    await asyncio.sleep(3)
        else:
            logger.info("❌ Сигналов не найдено")
    
    async def run(self):
        logger.info("🤖 Мульти-биржевой бот запущен")
        
        try:
            while True:
                await self.scheduled()
                logger.info(f"💤 Следующий анализ через {UPDATE_INTERVAL//60} мин")
                await asyncio.sleep(UPDATE_INTERVAL)
        except KeyboardInterrupt:
            logger.info("🛑 Бот остановлен")
        finally:
            for fetcher in self.fetchers.values():
                await fetcher.close()


# ============== TELEGRAM HANDLER ==============

class TelegramHandler:
    def __init__(self, bot: MultiExchangeScannerBot):
        self.bot = bot
        self.app = Application.builder().token(TELEGRAM_TOKEN).build()
        self.register()
    
    def register(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("scan", self.scan))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("help", self.help))
        self.app.add_handler(CommandHandler("pump", self.pump_only))
        self.app.add_handler(CallbackQueryHandler(self.button))
    
    async def start(self, update, context):
        active_exchanges = ", ".join(self.bot.fetchers.keys())
        await update.message.reply_text(
            f"🤖 *Мульти-биржевой фьючерсный сканер*\n\n"
            f"📊 Активные биржи: {active_exchanges}\n"
            f"📈 Анализ: RSI, MACD, EMA, VWAP\n"
            f"🔥 Имбалансы, ликвидность, дивергенции\n\n"
            f"Команды:\n"
            f"/scan - Сканировать\n"
            f"/pump - Только сигналы у уровней\n"
            f"/status - Статус\n"
            f"/help - Помощь",
            parse_mode='Markdown'
        )
    
    async def scan(self, update, context):
        msg = await update.message.reply_text("🔍 Сканирую все биржи...")
        signals = await self.bot.scan_all()
        if signals:
            await msg.edit_text(f"✅ Найдено {len(signals)} сигналов")
            for signal in signals[:5]:
                await self.bot.send_signal(signal, pump_only=False)
                await asyncio.sleep(2)
        else:
            await msg.edit_text("❌ Сигналов не найдено")
    
    async def pump_only(self, update, context):
        msg = await update.message.reply_text("🔍 Ищу сигналы у уровней...")
        signals = await self.bot.scan_all()
        pump_signals = [s for s in signals if s.get('pump_dump')]
        
        if pump_signals:
            await msg.edit_text(f"✅ Найдено {len(pump_signals)} сигналов")
            for signal in pump_signals[:5]:
                await self.bot.send_signal(signal, pump_only=True)
                await asyncio.sleep(2)
        else:
            await msg.edit_text("❌ Сигналов у уровней не найдено")
    
    async def status(self, update, context):
        text = "*📡 Статус:*\n\n"
        for name in self.bot.fetchers.keys():
            text += f"✅ {name} Futures: активен\n"
        
        text += f"\n📊 *Функции:*\n"
        text += f"✓ Дивергенции: {'вкл' if FEATURES['advanced']['divergence'] else 'выкл'}\n"
        text += f"✓ Имбалансы: {'вкл' if FEATURES['advanced']['imbalance'] else 'выкл'}\n"
        text += f"✓ Ликвидность: {'вкл' if FEATURES['advanced']['liquidity'] else 'выкл'}"
        
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def help(self, update, context):
        await update.message.reply_text(
            "*Помощь*\n\n"
            "📊 *Анализ:*\n"
            "• RSI, MACD, EMA, VWAP\n"
            "• Дивергенции\n"
            "• Имбалансы спроса/предложения\n"
            "• Ликвидность и сборы стопов\n\n"
            "⚙️ *Команды:*\n"
            "/scan - все сигналы\n"
            "/pump - только сигналы у уровней\n"
            "/status - состояние бота",
            parse_mode='Markdown'
        )
    
    async def button(self, update, context):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data.startswith("copy_"):
            coin = data.replace("copy_", "")
            await query.message.reply_text(f"`{coin}`", parse_mode='Markdown')
            await query.answer(f"✅ {coin} скопирован")
        
        elif data.startswith("refresh_"):
            coin = data.replace("refresh_", "")
            await query.edit_message_text(f"🔄 Обновляю сигнал по {coin}...")
            
            if coin in self.bot.last_signals:
                symbol = self.bot.last_signals[coin]['symbol']
                for fetcher in self.bot.fetchers.values():
                    signal = await self.bot.analyzer.generate_signal_from_fetcher(fetcher, symbol)
                    if signal:
                        self.bot.last_signals[coin]['signal'] = signal
                        self.bot.last_signals[coin]['time'] = datetime.now()
                        msg, keyboard = self.bot.format_message(signal)
                        await query.message.reply_text(
                            f"🔄 *ОБНОВЛЕННЫЙ СИГНАЛ*\n\n{msg}",
                            parse_mode='HTML',
                            reply_markup=keyboard
                        )
                        await query.message.delete()
                        break
            else:
                await query.edit_message_text(f"❌ Нет данных для {coin}")
        
        elif data.startswith("details_"):
            coin = data.replace("details_", "")
            await query.edit_message_text(f"📊 Загружаю детальный анализ по {coin}...")
            
            if coin in self.bot.last_signals:
                symbol = self.bot.last_signals[coin]['symbol']
                signal_time = self.bot.last_signals[coin]['time'].strftime('%Y-%m-%d %H:%M:%S')
                
                for fetcher in self.bot.fetchers.values():
                    detailed, keyboard = await self.bot.get_detailed_analysis(fetcher, symbol, coin, signal_time)
                    await query.message.reply_text(
                        detailed,
                        parse_mode='Markdown',
                        reply_markup=keyboard
                    )
                    
                    original = self.bot.last_signals[coin]['signal']
                    msg, orig_keyboard = self.bot.format_message(original)
                    await query.edit_message_text(msg, parse_mode='HTML', reply_markup=orig_keyboard)
                    break
            else:
                await query.edit_message_text(f"❌ Нет данных для {coin}")
        
        elif data.startswith("back_"):
            coin = data.replace("back_", "")
            if coin in self.bot.last_signals:
                signal = self.bot.last_signals[coin]['signal']
                msg, keyboard = self.bot.format_message(signal)
                await query.edit_message_text(msg, parse_mode='HTML', reply_markup=keyboard)
    
    def run(self):
        self.app.run_polling()


# ============== MAIN ==============

async def main():
    bot = MultiExchangeScannerBot()
    handler = TelegramHandler(bot)
    polling = asyncio.create_task(asyncio.to_thread(handler.run))
    
    try:
        await bot.run()
    finally:
        polling.cancel()

if __name__ == "__main__":
    asyncio.run(main())
