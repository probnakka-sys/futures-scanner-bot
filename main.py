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
    PAIRS_TO_SCAN,
    DISPLAY_SETTINGS,
    WEBSOCKET_SETTINGS
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


# ============== WEBSOCKET МЕНЕДЖЕР ДЛЯ BINGX ==============

class BingXWebSocketManager:
    """Менеджер WebSocket для получения цен в реальном времени с BingX"""
    
    def __init__(self, callback_function=None):
        self.callback = callback_function
        self.ws_connection = None
        self.running = False
        self.subscribed_symbols = set()
        self.latest_prices = {}
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = WEBSOCKET_SETTINGS.get('max_retries', 5)
        self.lock = asyncio.Lock()
        self.message_count = 0
        self.ping_task = None
        self.listen_key = None
        self.ping_interval = WEBSOCKET_SETTINGS.get('ping_interval', 30)
        
    async def connect(self):
        """Подключение к WebSocket BingX через правильный эндпоинт"""
        try:
            uri = "wss://open-api-swap.bingx.com/swap-market"
            
            logger.info(f"🔌 Подключаюсь к WebSocket BingX: {uri}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0',
                'Connection': 'Upgrade',
                'Upgrade': 'websocket'
            }
            
            self.ws_connection = await websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=20,
                max_size=2**20,
                extra_headers=headers
            )
            self.running = True
            self.reconnect_attempts = 0
            logger.info("✅ WebSocket BingX подключен")
            
            await self._send_ping()
            
            asyncio.create_task(self._listen())
            self.ping_task = asyncio.create_task(self._ping_loop())
            
            if self.subscribed_symbols:
                await self._resubscribe_all()
                
        except Exception as e:
            logger.error(f"❌ Ошибка подключения WebSocket BingX: {e}")
            self.running = False
            await self._handle_disconnect()
    
    async def _send_ping(self):
        try:
            if self.ws_connection and self.running:
                ping_msg = json.dumps({"ping": int(time.time() * 1000)})
                await self.ws_connection.send(ping_msg)
                logger.debug("📤 Ping отправлен")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки ping: {e}")
    
    async def _ping_loop(self):
        while self.running:
            try:
                await asyncio.sleep(self.ping_interval)
                await self._send_ping()
            except Exception as e:
                logger.error(f"❌ Ошибка в ping loop: {e}")
                break
    
    async def _listen(self):
        while self.running:
            try:
                message = await asyncio.wait_for(self.ws_connection.recv(), timeout=30)
                self.message_count += 1
                await self._process_message(message)
                
            except asyncio.TimeoutError:
                try:
                    await self._send_ping()
                except:
                    await self._handle_disconnect()
                    break
                    
            except websockets.exceptions.ConnectionClosed:
                logger.warning("⚠️ WebSocket BingX соединение закрыто")
                await self._handle_disconnect()
                break
                
            except Exception as e:
                logger.error(f"❌ Ошибка при получении сообщения: {e}")
                await asyncio.sleep(1)
    
    async def _process_message(self, message: str):
        try:
            data = json.loads(message)
            
            if 'pong' in data:
                logger.debug("📥 Pong получен")
                return
            
            if 'data' in data:
                ticker_data = data['data']
                if 's' in ticker_data and 'c' in ticker_data:
                    symbol_raw = ticker_data['s']
                    base = symbol_raw.split('-')[0]
                    symbol = f"{base}/USDT:USDT"
                    price = float(ticker_data['c'])
                    timestamp = int(time.time() * 1000)
                    
                    async with self.lock:
                        self.latest_prices[symbol] = {
                            'price': price,
                            'timestamp': timestamp,
                            'time': datetime.now().strftime('%H:%M:%S.%f')[:-3]
                        }
                    
                    if self.callback:
                        await self.callback(symbol, price, timestamp)
                        
        except json.JSONDecodeError:
            logger.debug(f"📨 Получено сообщение: {message[:100]}")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения: {e}")
    
    async def subscribe(self, symbol: str) -> bool:
        try:
            if not self.ws_connection or not self.running:
                logger.warning(f"⚠️ WebSocket не подключен, невозможно подписаться на {symbol}")
                return False
            
            if len(self.subscribed_symbols) >= WEBSOCKET_SETTINGS.get('subscription_limit', 100):
                logger.warning(f"⚠️ Достигнут лимит подписок")
                return False
            
            symbol_raw = symbol.replace('/', '-').replace(':USDT', '')
            
            subscribe_msg = {
                "id": int(time.time() * 1000),
                "reqType": "sub",
                "dataType": f"{symbol_raw}@ticker"
            }
            
            async with self.lock:
                await self.ws_connection.send(json.dumps(subscribe_msg))
                self.subscribed_symbols.add(symbol)
                logger.info(f"✅ WebSocket подписка на {symbol}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка подписки на {symbol}: {e}")
            return False
    
    async def _resubscribe_all(self):
        if self.subscribed_symbols:
            logger.info(f"🔄 Переподписываюсь на {len(self.subscribed_symbols)} символов...")
            for symbol in self.subscribed_symbols.copy():
                await self.subscribe(symbol)
                await asyncio.sleep(0.1)
    
    async def _handle_disconnect(self):
        self.running = False
        if self.ping_task:
            self.ping_task.cancel()
        
        if self.ws_connection:
            await self.ws_connection.close()
        
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            wait_time = min(2 ** self.reconnect_attempts, 30)
            logger.info(f"🔄 Попытка переподключения BingX {self.reconnect_attempts}/{self.max_reconnect_attempts} через {wait_time}с...")
            await asyncio.sleep(wait_time)
            await self.connect()
        else:
            logger.error("❌ Превышено количество попыток переподключения BingX")
    
    async def get_latest_price(self, symbol: str) -> Optional[Dict]:
        async with self.lock:
            data = self.latest_prices.get(symbol)
            if data:
                return {
                    'price': data['price'],
                    'source': 'websocket',
                    'time': data['time']
                }
        return None
    
    async def stop(self):
        self.running = False
        if self.ping_task:
            self.ping_task.cancel()
        if self.ws_connection:
            await self.ws_connection.close()
        logger.info("🛑 WebSocket BingX остановлен")


# ============== БАЗОВЫЙ КЛАСС ДЛЯ БИРЖ ==============

class BaseExchangeFetcher:
    """Базовый класс для всех бирж"""
    
    def __init__(self, name: str):
        self.name = name
        self.available_pairs = []
    
    async def fetch_all_pairs(self) -> List[str]:
        """Получение списка всех пар"""
        raise NotImplementedError
    
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """Получение свечных данных"""
        raise NotImplementedError
    
    async def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        """Получение ставки фондирования"""
        return 0.0
    
    async def fetch_ticker(self, symbol: str) -> Dict:
        """Получение тикера"""
        return {}
    
    async def get_price_with_source(self, symbol: str, http_price: float = None) -> Dict:
        """Получение цены с источником"""
        return {
            'price': http_price,
            'source': 'http',
            'time': datetime.now().strftime('%H:%M:%S')
        }
    
    async def close(self):
        """Закрытие соединений"""
        pass


# ============== BYBIT FUTURES (отключен) ==============

class BybitFetcher(BaseExchangeFetcher):
    """Фетчер для Bybit Futures (временно отключен из-за блокировки)"""
    
    def __init__(self):
        super().__init__("Bybit")
        logger.warning("⚠️ Bybit временно отключен из-за геоблокировки")
    
    async def fetch_all_pairs(self) -> List[str]:
        return []
    
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        return None


# ============== BINGX FUTURES С WEBSOCKET ==============

class BingxFetcher(BaseExchangeFetcher):
    """Фетчер для BingX Futures с WebSocket поддержкой"""
    
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
        
        self.websocket = None
        self.ws_enabled = FEATURES['data_sources'].get('websocket', False)
        
        if self.ws_enabled:
            asyncio.create_task(self._init_websocket())
        
        logger.info("✅ BingX Futures инициализирован")
    
    async def _init_websocket(self):
        self.websocket = BingXWebSocketManager()
        await self.websocket.connect()
    
    async def fetch_all_pairs(self) -> List[str]:
        try:
            markets = await self.exchange.load_markets()
            usdt_pairs = []
            for symbol, market in markets.items():
                if (market['quote'] == 'USDT' and 
                    market['active'] and 
                    market['type'] in ['swap', 'future']):
                    usdt_pairs.append(symbol)
                    
                    if self.websocket and self.ws_enabled:
                        await self.websocket.subscribe(symbol)
            
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
    
    async def get_price_with_source(self, symbol: str, http_price: float = None) -> Dict:
        if self.websocket and self.ws_enabled:
            ws_price = await self.websocket.get_latest_price(symbol)
            if ws_price:
                return ws_price
        
        return {
            'price': http_price,
            'source': 'http',
            'time': datetime.now().strftime('%H:%M:%S')
        }
    
    async def close(self):
        await self.exchange.close()
        if self.websocket:
            await self.websocket.stop()


# ============== MEXC FUTURES (отключен) ==============

class MexcFetcher(BaseExchangeFetcher):
    """Фетчер для MEXC Futures (временно отключен)"""
    
    def __init__(self):
        super().__init__("MEXC")
        logger.warning("⚠️ MEXC временно отключен")
    
    async def fetch_all_pairs(self) -> List[str]:
        return []
    
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        return None


# ============== ПРОФЕССИОНАЛЬНЫЙ АНАЛИЗАТОР УРОВНЕЙ ==============

class LevelAnalyzer:
    """Анализатор рыночных уровней"""
    
    def __init__(self):
        self.levels_history = {}
        self.price_history = {}
        self.breakout_confirmed = {}
        self.last_signals = {}
        
    async def add_price(self, symbol: str, price: float, volume: float, timestamp: int):
        if symbol not in self.price_history:
            self.price_history[symbol] = []
            
        self.price_history[symbol].append((timestamp, price, volume))
        
        cutoff = timestamp - (24 * 60 * 60 * 1000)
        self.price_history[symbol] = [
            x for x in self.price_history[symbol] if x[0] > cutoff
        ]
        
        events = await self._check_levels(symbol, price, volume, timestamp)
        return events
    
    async def _check_levels(self, symbol: str, price: float, volume: float, timestamp: int) -> List[Dict]:
        if symbol not in self.levels_history:
            self.levels_history[symbol] = {}
        
        last_signal = self.last_signals.get(symbol, 0)
        if timestamp - last_signal < 300000:
            return []
        
        events = []
        coin = symbol.split('/')[0]
        
        test_levels = {
            round(price * 1.02, -2): {'strength': 30, 'type': 'resistance'},
            round(price * 0.98, -2): {'strength': 30, 'type': 'support'},
            round(price * 1.05, -2): {'strength': 25, 'type': 'resistance'},
            round(price * 0.95, -2): {'strength': 25, 'type': 'support'}
        }
        
        for level_price, level_info in test_levels.items():
            distance = abs(price - level_price) / level_price * 100
            
            if distance <= 0.5:
                event = await self._handle_level_touch(
                    symbol, coin, price, volume, timestamp, level_price, level_info
                )
                if event:
                    events.append(event)
                    
            elif 1.0 <= distance <= 3.0:
                event = await self._check_breakout(
                    symbol, coin, price, timestamp, level_price, level_info
                )
                if event:
                    events.append(event)
        
        if events:
            self.last_signals[symbol] = timestamp
            
        return events
    
    async def _handle_level_touch(self, symbol: str, coin: str, price: float, volume: float, 
                                  timestamp: int, level_price: float, level_info: Dict) -> Optional[Dict]:
        
        if level_price not in self.levels_history[symbol]:
            self.levels_history[symbol][level_price] = {
                'touches': [],
                'strength': level_info['strength'],
                'type': level_info['type']
            }
        
        level_data = self.levels_history[symbol][level_price]
        
        level_data['touches'].append({
            'timestamp': timestamp,
            'price': price,
            'volume': volume
        })
        
        level_data['touches'] = level_data['touches'][-5:]
        
        return await self._analyze_level_interaction(
            symbol, coin, price, volume, timestamp, level_price, level_data
        )
    
    async def _analyze_level_interaction(self, symbol: str, coin: str, price: float, volume: float,
                                        timestamp: int, level_price: float, level_data: Dict) -> Optional[Dict]:
        
        touches = level_data['touches']
        strength = level_data['strength']
        level_type = level_data['type']
        
        event = {
            'symbol': symbol,
            'coin': coin,
            'level_price': level_price,
            'current_price': price,
            'strength': strength,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        if len(touches) == 1:
            event['type'] = 'LEVEL_TOUCH'
            event['direction'] = 'NEUTRAL'
            event['confidence'] = strength
            event['description'] = f"Касание уровня {level_price:.2f}"
            return event
            
        elif len(touches) >= 2:
            if level_type == 'support':
                event['type'] = 'STRONG_SUPPORT'
                event['direction'] = 'LONG'
                event['confidence'] = min(strength + 20 + (len(touches) * 5), 95)
                event['description'] = f"Сильная поддержка {level_price:.2f} ({len(touches)} касаний)"
            else:
                event['type'] = 'STRONG_RESISTANCE'
                event['direction'] = 'SHORT'
                event['confidence'] = min(strength + 20 + (len(touches) * 5), 95)
                event['description'] = f"Сильное сопротивление {level_price:.2f} ({len(touches)} касаний)"
            return event
        
        return None
    
    async def _check_breakout(self, symbol: str, coin: str, price: float, timestamp: int,
                             level_price: float, level_info: Dict) -> Optional[Dict]:
        
        if symbol not in self.breakout_confirmed:
            self.breakout_confirmed[symbol] = {}
        
        if level_info['type'] == 'resistance' and price > level_price * 1.01:
            event = {
                'symbol': symbol,
                'coin': coin,
                'type': 'BREAKOUT',
                'direction': 'LONG',
                'level_price': level_price,
                'current_price': price,
                'strength': level_info['strength'] + 30,
                'confidence': level_info['strength'] + 30,
                'description': f"Пробой сопротивления {level_price:.2f}",
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            self.breakout_confirmed[symbol][level_price] = timestamp
            return event
            
        elif level_info['type'] == 'support' and price < level_price * 0.99:
            event = {
                'symbol': symbol,
                'coin': coin,
                'type': 'BREAKOUT',
                'direction': 'SHORT',
                'level_price': level_price,
                'current_price': price,
                'strength': level_info['strength'] + 30,
                'confidence': level_info['strength'] + 30,
                'description': f"Пробой поддержки {level_price:.2f}",
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            self.breakout_confirmed[symbol][level_price] = timestamp
            return event
        
        return None
    
    def get_cooldown(self) -> int:
        return 300


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
        
        if pd.notna(last['rsi']):
            if last['rsi'] < INDICATOR_SETTINGS['rsi_oversold']:
                reasons.append(f"RSI перепродан ({last['rsi']:.1f})")
                confidence += INDICATOR_WEIGHTS['rsi']
            elif last['rsi'] > INDICATOR_SETTINGS['rsi_overbought']:
                reasons.append(f"RSI перекуплен ({last['rsi']:.1f})")
                confidence += INDICATOR_WEIGHTS['rsi']
        
        if pd.notna(last['MACD_12_26_9']) and pd.notna(last['MACDs_12_26_9']):
            if last['MACD_12_26_9'] > last['MACDs_12_26_9'] and prev['MACD_12_26_9'] <= prev['MACDs_12_26_9']:
                reasons.append("Бычье пересечение MACD")
                confidence += INDICATOR_WEIGHTS['macd']
            elif last['MACD_12_26_9'] < last['MACDs_12_26_9'] and prev['MACD_12_26_9'] >= prev['MACDs_12_26_9']:
                reasons.append("Медвежье пересечение MACD")
                confidence += INDICATOR_WEIGHTS['macd']
        
        if last['ema_9'] > last['ema_21'] and prev['ema_9'] <= prev['ema_21']:
            reasons.append("Бычье пересечение EMA (9/21)")
            confidence += INDICATOR_WEIGHTS['ema_cross']
        elif last['ema_9'] < last['ema_21'] and prev['ema_9'] >= prev['ema_21']:
            reasons.append("Медвежье пересечение EMA (9/21)")
            confidence += INDICATOR_WEIGHTS['ema_cross']
        
        if last['volume_ratio'] > 1.5:
            reasons.append(f"Объем x{last['volume_ratio']:.1f} от нормы")
            confidence += INDICATOR_WEIGHTS['volume']
        
        if FEATURES['advanced']['vwap'] and 'vwap' in df.columns:
            if last['close'] > last['vwap']:
                reasons.append(f"Цена выше VWAP ({last['vwap']:.2f})")
                confidence += 10
            else:
                reasons.append(f"Цена ниже VWAP ({last['vwap']:.2f})")
                confidence += 10
        
        for signal in alignment['signals']:
            reasons.append(f"📊 {signal}")
            if "НЕДЕЛЬНЫЙ" in signal:
                confidence += INDICATOR_WEIGHTS['weekly_trend']
            elif "Дневной" in signal:
                confidence += INDICATOR_WEIGHTS['daily_trend']
        
        if alignment['trend_alignment'] > 70:
            reasons.append(f"✅ Тренды согласованы ({alignment['trend_alignment']:.0f}%)")
            confidence += INDICATOR_WEIGHTS['trend_alignment']
        
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
        
        price_change = metadata.get('price_change_24h')
        if price_change and abs(price_change) > 5:
            if price_change > 5:
                reasons.append(f"📈 Рост за 24ч: +{price_change:.1f}%")
            elif price_change < -5:
                reasons.append(f"📉 Падение за 24ч: {price_change:.1f}%")
        
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
    """Бот для сканирования нескольких бирж"""
    
    def __init__(self):
        self.fetchers = {}
        self.analyzer = MultiTimeframeAnalyzer()
        self.telegram_bot = Bot(token=TELEGRAM_TOKEN)
        self.scanned_pairs = set()
        self.last_signals = {}  # Для хранения последних сигналов по монетам
        
        # Инициализация фетчеров согласно настройкам
        if FEATURES['exchanges'].get('bingx', {}).get('enabled', False):
            self.fetchers['BingX'] = BingxFetcher()
        
        # Инициализация анализаторов
        if FEATURES['advanced']['pump_dump']:
            self.level_analyzer = LevelAnalyzer()
            logger.info("✅ Анализатор уровней инициализирован")
        else:
            self.level_analyzer = None
        
        if FEATURES['advanced']['divergence']:
            self.divergence = DivergenceAnalyzer()
            logger.info("✅ Анализатор дивергенций инициализирован")
        else:
            self.divergence = None
    
    def extract_coin(self, symbol: str) -> str:
        """Извлечение чистой монеты из символа"""
        if ':USDT' in symbol:
            return symbol.split('/')[0]
        elif '/' in symbol:
            return symbol.split('/')[0]
        else:
            return symbol.replace('USDT', '')
    
    def format_funding(self, rate: float) -> str:
        if rate is None or rate == 0:
            return "Нет данных"
        color = "🟢" if rate > 0 else "🔴" if rate < 0 else "⚪"
        return f"{color} {rate*100:.4f}%"
    
    def format_volume(self, volume: float) -> str:
        if volume is None:
            return "N/A"
        if volume > 1_000_000:
            return f"${volume/1_000_000:.1f}M"
        elif volume > 1_000:
            return f"${volume/1_000:.1f}K"
        else:
            return f"${volume:.1f}"
    
    def format_message(self, signal: Dict, price_source: Dict = None, pump_only: bool = False) -> Tuple[str, InlineKeyboardMarkup]:
        emoji_map = {
            'LONG 📈': '🟢',
            'SHORT 📉': '🔴',
            'Разворот LONG 📈': '🟢🔄',
            'Разворот SHORT 📉': '🔴🔄',
            'NEUTRAL': '⚪'
        }
        emoji = emoji_map.get(signal['direction'], '🤖')
        
        coin = self.extract_coin(signal['symbol'])
        
        # Форматируем цену (сокращаем до 2 знаков)
        price_formatted = f"{signal['price']:.2f}"
        
        price_display = f"`{price_formatted}`"
        if DISPLAY_SETTINGS['show_price_source'] and price_source:
            source_text = "(w)" if price_source['source'] == 'websocket' else "(h)"
            price_display = f"`{price_formatted}` {source_text}"
        
        # Компактный формат сигнала
        lines = [
            f"{emoji} *СИГНАЛ {signal['exchange']}*",
            f"└ `{coin}` {signal['signal_power']}\n",
            f"📊 *Направление:* {signal['direction']}",
            f"🎯 *Цена:* {price_display}\n"
        ]
        
        # Цели одной строкой
        if 'target_1' in signal:
            lines.append(f"🎯 *Цели:* {signal['target_1']} | {signal['target_2']} | SL {signal['stop_loss']}\n")
        
        # Импульсный анализ (если есть)
        if signal.get('level_events'):
            event = signal['level_events'][0]  # Берем первое событие
            move_emoji = "🚀" if event.get('change_percent', 0) > 0 else "📉"
            change = event.get('change_percent', 0)
            lines.append(f"⚡ {move_emoji} {abs(change):.1f}% | {event['description']}\n")
        
        # Причины (кратко)
        if signal['reasons']:
            lines.append(f"💡 *Причины:* {', '.join(signal['reasons'][:3])}")
        
        lines.append(f"\n⏱️ {datetime.now().strftime('%d.%m %H:%M')}")
        
        # Клавиатура
        keyboard = []
        
        if DISPLAY_SETTINGS['buttons']['copy']:
            keyboard.append([InlineKeyboardButton(f"📋 Скопировать {coin}", callback_data=f"copy_{coin}")])
        
        if DISPLAY_SETTINGS['buttons']['trade']:
            exch = signal['exchange']
            if exch in REF_LINKS:
                keyboard.append([InlineKeyboardButton(f"🚀 Торговать на {exch}", url=REF_LINKS[exch])])
        
        action_row = []
        if DISPLAY_SETTINGS['buttons']['refresh']:
            action_row.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{coin}"))
        if DISPLAY_SETTINGS['buttons']['details']:
            action_row.append(InlineKeyboardButton("📊 Детали", callback_data=f"details_{coin}"))
        
        if action_row:
            keyboard.append(action_row)
        
        return "\n".join(lines), InlineKeyboardMarkup(keyboard)
    
    async def refresh_signal(self, fetcher, symbol: str, coin: str) -> Optional[Dict]:
        """Обновление сигнала по монете"""
        try:
            dataframes = {}
            for tf_name, tf_value in TIMEFRAMES.items():
                limit = 200 if tf_name == 'current' else 100
                df = await fetcher.fetch_ohlcv(symbol, tf_value, limit)
                if df is not None and not df.empty:
                    df = self.analyzer.calculate_indicators(df)
                    dataframes[tf_name] = df
            
            if not dataframes:
                return None
            
            funding = await fetcher.fetch_funding_rate(symbol)
            ticker = await fetcher.fetch_ticker(symbol)
            
            metadata = {
                'funding_rate': funding,
                'volume_24h': ticker.get('volume_24h'),
                'price_change_24h': ticker.get('price_change_24h')
            }
            
            signal = self.analyzer.generate_signal(dataframes, metadata, symbol, "BingX")
            
            if signal and self.level_analyzer and 'current' in dataframes:
                df = dataframes['current']
                price = df['close'].iloc[-1]
                timestamp = int(df.index[-1].timestamp() * 1000)
                volume = df['volume'].iloc[-1]
                
                events = await self.level_analyzer.add_price(symbol, price, volume, timestamp)
                if events:
                    signal['level_events'] = events
            
            return signal
        except Exception as e:
            logger.error(f"Ошибка обновления сигнала {symbol}: {e}")
            return None
    
    async def get_detailed_analysis(self, fetcher, symbol: str, coin: str) -> str:
        """Получение детального анализа по монете"""
        try:
            dataframes = {}
            for tf_name, tf_value in TIMEFRAMES.items():
                limit = 200 if tf_name == 'current' else 100
                df = await fetcher.fetch_ohlcv(symbol, tf_value, limit)
                if df is not None and not df.empty:
                    df = self.analyzer.calculate_indicators(df)
                    dataframes[tf_name] = df
            
            if not dataframes:
                return f"❌ Нет данных для {coin}"
            
            df_current = dataframes['current']
            last = df_current.iloc[-1]
            
            lines = []
            lines.append(f"📊 *ДЕТАЛЬНЫЙ АНАЛИЗ {coin}*")
            lines.append(f"⏱️ Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
            
            # Технические индикаторы
            lines.append("📈 *ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ (15m):*")
            rsi_status = "ПЕРЕПРОДАН" if last['rsi'] < 30 else "ПЕРЕКУПЛЕН" if last['rsi'] > 70 else "НЕЙТРАЛЬНЫЙ"
            lines.append(f"└ RSI(14): {last['rsi']:.1f} ({rsi_status})")
            
            macd_status = "Бычье" if last['MACD_12_26_9'] > last['MACDs_12_26_9'] else "Медвежье"
            lines.append(f"└ MACD: {macd_status} пересечение")
            
            ema_status = "Бычье" if last['ema_9'] > last['ema_21'] else "Медвежье"
            lines.append(f"└ EMA 9/21: {last['ema_9']:.2f} / {last['ema_21']:.2f} ({ema_status})")
            
            bb_position = "Нижняя" if last['close'] < last['BBM_20_2.0'] else "Верхняя" if last['close'] > last['BBM_20_2.0'] else "Средняя"
            lines.append(f"└ Bollinger: {bb_position} полоса")
            lines.append(f"└ ATR: {last['atr']:.2f}\n")
            
            # Старшие таймфреймы
            lines.append("📊 *СТАРШИЕ ТАЙМФРЕЙМЫ:*")
            if 'hourly' in dataframes:
                df_h = dataframes['hourly'].iloc[-1]
                trend_h = "ВОСХОДЯЩИЙ 📈" if df_h['ema_9'] > df_h['ema_21'] else "НИСХОДЯЩИЙ 📉"
                lines.append(f"└ 1h: {trend_h}")
            if 'daily' in dataframes:
                df_d = dataframes['daily'].iloc[-1]
                trend_d = "ВОСХОДЯЩИЙ 📈" if df_d['close'] > df_d['ema_200'] else "НИСХОДЯЩИЙ 📉"
                ema200_status = "выше EMA 200" if df_d['close'] > df_d['ema_200'] else "ниже EMA 200"
                lines.append(f"└ 1d: {trend_d} ({ema200_status})")
            if 'weekly' in dataframes:
                df_w = dataframes['weekly'].iloc[-1]
                trend_w = "ВОСХОДЯЩИЙ 📈" if df_w['close'] > df_w['ema_200'] else "НИСХОДЯЩИЙ 📉"
                lines.append(f"└ 1w: {trend_w}\n")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Ошибка детального анализа {symbol}: {e}")
            return f"❌ Ошибка анализа: {e}"
    
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
                    
                    if signal and self.level_analyzer and 'current' in dataframes:
                        df = dataframes['current']
                        price = df['close'].iloc[-1]
                        timestamp = int(df.index[-1].timestamp() * 1000)
                        volume = df['volume'].iloc[-1]
                        
                        events = await self.level_analyzer.add_price(pair, price, volume, timestamp)
                        if events:
                            signal['level_events'] = events
                    
                    if signal and signal['confidence'] >= MIN_CONFIDENCE:
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
        
        # Сохраняем сигнал для кнопок
        coin = self.extract_coin(signal['symbol'])
        self.last_signals[coin] = {
            'symbol': signal['symbol'],
            'signal': signal,
            'time': datetime.now()
        }
        
        price_source = None
        if 'BingX' in self.fetchers and signal['exchange'] == 'BingX':
            price_source = await self.fetchers['BingX'].get_price_with_source(signal['symbol'], signal['price'])
            if price_source['source'] == 'websocket':
                signal['price'] = price_source['price']
        
        msg, keyboard = self.format_message(signal, price_source, pump_only)
        await self.telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        signal_type = "ПАМП/ДАМП" if pump_only else ""
        logger.info(f"✅ Отправлен сигнал {signal_type}: {signal['symbol']}")
    
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
        ws_status = "✅ Включен" if FEATURES['data_sources']['websocket'] else "❌ Отключен"
        await update.message.reply_text(
            f"🤖 *Мульти-биржевой фьючерсный сканер*\n\n"
            f"📊 Активные биржи: {active_exchanges}\n"
            f"⚡ WebSocket: {ws_status}\n"
            f"📈 Мультитаймфрейм (15m/1h/1d/1w)\n"
            f"🔥 Анализ уровней\n"
            f"📈 Дивергенции RSI/MACD\n"
            f"📊 VWAP институциональный уровень\n"
            f"🕯 Свечные паттерны\n\n"
            f"Команды:\n"
            f"/scan - Сканировать\n"
            f"/pump - Только памп-дамп сигналы\n"
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
        msg = await update.message.reply_text("🔍 Ищу памп-дамп сигналы...")
        signals = await self.bot.scan_all()
        pump_signals = [s for s in signals if s.get('level_events')]
        
        if pump_signals:
            await msg.edit_text(f"✅ Найдено {len(pump_signals)} сигналов у уровней")
            for signal in pump_signals[:5]:
                await self.bot.send_signal(signal, pump_only=True)
                await asyncio.sleep(2)
        else:
            await msg.edit_text("❌ Сигналов у уровней не найдено")
    
    async def status(self, update, context):
        text = "*📡 Статус:*\n\n"
        
        for name in self.bot.fetchers.keys():
            text += f"✅ {name} Futures: активен\n"
        
        text += f"\n⚡ WebSocket: {'✅ активен' if FEATURES['data_sources']['websocket'] else '❌ отключен'}\n"
        
        text += f"\n📊 *Функции:*\n"
        text += f"✓ Анализ уровней: {'вкл' if FEATURES['advanced']['pump_dump'] else 'выкл'}\n"
        text += f"✓ Дивергенции: {'вкл' if FEATURES['advanced']['divergence'] else 'выкл'}\n"
        text += f"✓ VWAP: {'вкл' if FEATURES['advanced']['vwap'] else 'выкл'}\n"
        text += f"✓ Паттерны: {'вкл' if FEATURES['advanced']['patterns'] else 'выкл'}"
        
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def help(self, update, context):
        await update.message.reply_text(
            "*Помощь*\n\n"
            "📊 *Анализ:*\n"
            "• RSI, MACD, EMA\n"
            "• Объемы, VWAP\n"
            "• Дивергенции\n"
            "• Анализ уровней (поддержка/сопротивление)\n"
            "• Свечные паттерны\n\n"
            "⚙️ *Команды:*\n"
            "/scan - все сигналы\n"
            "/pump - только сигналы у уровней\n"
            "/status - состояние бота\n\n"
            "⚙️ *Кнопки:*\n"
            "🔄 Обновить - обновить сигнал\n"
            "📊 Детали - подробный анализ",
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
            
            # Ищем сохраненный сигнал
            if coin in self.bot.last_signals:
                symbol = self.bot.last_signals[coin]['symbol']
                
                for name, fetcher in self.bot.fetchers.items():
                    if name == "BingX":
                        signal = await self.bot.refresh_signal(fetcher, symbol, coin)
                        if signal:
                            price_source = await fetcher.get_price_with_source(symbol, signal['price'])
                            msg, keyboard = self.bot.format_message(signal, price_source, pump_only=False)
                            await query.message.reply_text(
                                f"🔄 *ОБНОВЛЕННЫЙ СИГНАЛ*\n\n{msg}",
                                parse_mode='Markdown',
                                reply_markup=keyboard
                            )
                            await query.message.delete()
                        else:
                            await query.edit_message_text(f"❌ Не удалось обновить сигнал для {coin}")
                        break
            else:
                await query.edit_message_text(f"❌ Нет данных для {coin}")
        
        elif data.startswith("details_"):
            coin = data.replace("details_", "")
            
            await query.edit_message_text(f"📊 Загружаю детальный анализ по {coin}...")
            
            if coin in self.bot.last_signals:
                symbol = self.bot.last_signals[coin]['symbol']
                
                for name, fetcher in self.bot.fetchers.items():
                    if name == "BingX":
                        detailed = await self.bot.get_detailed_analysis(fetcher, symbol, coin)
                        
                        keyboard = [[InlineKeyboardButton("🔝 Вернуться к сигналу", callback_data=f"back_{coin}")]]
                        
                        await query.message.reply_text(
                            detailed,
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        # Восстанавливаем исходное сообщение
                        original = self.bot.last_signals[coin]['signal']
                        price_source = await fetcher.get_price_with_source(symbol, original['price'])
                        msg, orig_keyboard = self.bot.format_message(original, price_source, pump_only=False)
                        await query.edit_message_text(msg, parse_mode='Markdown', reply_markup=orig_keyboard)
                        break
            else:
                await query.edit_message_text(f"❌ Нет данных для {coin}")
        
        elif data.startswith("back_"):
            coin = data.replace("back_", "")
            
            if coin in self.bot.last_signals:
                signal = self.bot.last_signals[coin]['signal']
                for name, fetcher in self.bot.fetchers.items():
                    if name == "BingX":
                        price_source = await fetcher.get_price_with_source(signal['symbol'], signal['price'])
                        msg, keyboard = self.bot.format_message(signal, price_source, pump_only=False)
                        await query.edit_message_text(msg, parse_mode='Markdown', reply_markup=keyboard)
                        break
    
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
