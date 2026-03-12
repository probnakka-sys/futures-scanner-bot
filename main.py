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
    DISPLAY_SETTINGS
)

# Временно отключаем проверку SSL для теста
ssl._create_default_https_context = ssl._create_unverified_context

# Запасной список популярных фьючерсных пар
FALLBACK_PAIRS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOGE/USDT', 'DOT/USDT', 'LINK/USDT', 'MATIC/USDT',
    'AVAX/USDT', 'UNI/USDT', 'ATOM/USDT', 'LTC/USDT', 'BCH/USDT',
    'ALGO/USDT', 'NEAR/USDT', 'FIL/USDT', 'APT/USDT', 'ARB/USDT',
    'AAVE/USDT', 'COMP/USDT', 'MKR/USDT', 'SNX/USDT', 'YFI/USDT',
    'CRV/USDT', 'BAL/USDT', '1INCH/USDT', 'OP/USDT', 'IMX/USDT',
    'AXS/USDT', 'SAND/USDT', 'MANA/USDT', 'GALA/USDT', 'ENJ/USDT',
    'FET/USDT', 'AGIX/USDT', 'OCEAN/USDT', 'GRT/USDT', 'BAND/USDT'
]

# Ручной расчет индикаторов
def calculate_rsi(series, period=14):
    """Расчет RSI"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_ema(series, period):
    """Расчет EMA"""
    return series.ewm(span=period, adjust=False).mean()

def calculate_macd(series, fast=12, slow=26, signal=9):
    """Расчет MACD"""
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calculate_atr(high, low, close, period=14):
    """Расчет ATR"""
    high_low = high - low
    high_close = abs(high - close.shift())
    low_close = abs(low - close.shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    atr = true_range.rolling(window=period).mean()
    return atr

def calculate_bollinger_bands(series, period=20, std_dev=2):
    """Расчет полос Боллинджера"""
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    return sma, upper, lower

def calculate_sma(series, period):
    """Расчет SMA"""
    return series.rolling(window=period).mean()

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Расчет VWAP (Volume Weighted Average Price)"""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    vwap = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
    return vwap

def detect_candle_patterns(df: pd.DataFrame) -> Dict:
    """Обнаружение свечных паттернов с указанием таймфрейма"""
    patterns = {}
    
    if len(df) < 2:
        return patterns
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # Пинбар (длинная тень)
    body = abs(last['close'] - last['open'])
    upper_wick = last['high'] - max(last['close'], last['open'])
    lower_wick = min(last['close'], last['open']) - last['low']
    
    if body > 0:
        if upper_wick > body * 2 and lower_wick < body * 0.3:
            patterns['shooting_star'] = 'медвежий'
        elif lower_wick > body * 2 and upper_wick < body * 0.3:
            patterns['hammer'] = 'бычий'
            patterns['pin_bar'] = 'бычий'
    
    # Поглощение
    if prev['close'] < prev['open'] and last['close'] > last['open']:
        if last['close'] > prev['open'] and last['open'] < prev['close']:
            patterns['engulfing'] = 'бычий'
    
    # Дожи
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


# ============== WEBSOCKET МОНИТОР ==============

class WebSocketMonitor:
    """Мониторинг цен в реальном времени через WebSocket MEXC (фьючерсы)"""
    
    def __init__(self, callback_function=None):
        self.callback = callback_function
        self.ws_connection = None
        self.running = False
        self.subscribed_symbols = set()
        self.latest_prices = {}
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.lock = asyncio.Lock()
        self.message_count = 0
        self.last_minute_count = 0
        self.last_minute_time = time.time()
        self.ping_task = None
        self.MAX_SUBSCRIPTIONS = 30
        
    async def connect(self):
    """Подключение к WebSocket MEXC (фьючерсы)"""
    uri = "wss://wbs-api.mexc.com/ws"
    
    try:
        logger.info(f"🔌 Подключаюсь к WebSocket MEXC Futures: {uri}")
        # Убираем extra_headers, так как он не поддерживается
        self.ws_connection = await websockets.connect(
            uri, 
            ping_interval=None,
            ping_timeout=None,
            max_size=2**20,
            compression=None
            # extra_headers убран!
        )
        self.running = True
        self.reconnect_attempts = 0
        logger.info("✅ WebSocket Futures подключен")
        
        asyncio.create_task(self._listen())
        self.ping_task = asyncio.create_task(self._ping_loop())
        asyncio.create_task(self._stats_logger())
        
        if self.subscribed_symbols:
            await self._resubscribe_all()
            
    except Exception as e:
        logger.error(f"❌ Ошибка подключения WebSocket: {e}")
        self.running = False
        await self._handle_disconnect()
    
    async def _ping_loop(self):
        """Отправка PING каждые 20 секунд для поддержания соединения"""
        while self.running:
            try:
                await asyncio.sleep(20)
                if self.ws_connection and self.running:
                    ping_msg = {"method": "PING"}
                    await self.ws_connection.send(json.dumps(ping_msg))
                    logger.debug("📤 PING отправлен")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки PING: {e}")
                break
    
    async def _stats_logger(self):
        """Логирование статистики WebSocket"""
        while self.running:
            await asyncio.sleep(60)
            current_time = time.time()
            messages_per_min = self.message_count - self.last_minute_count
            self.last_minute_count = self.message_count
            self.last_minute_time = current_time
            sub_count = len(self.subscribed_symbols)
            logger.info(f"📊 WebSocket: {messages_per_min} сообщ/мин, {sub_count}/{self.MAX_SUBSCRIPTIONS} подписок")
    
    async def _listen(self):
        """Прослушивание входящих сообщений"""
        while self.running:
            try:
                message = await asyncio.wait_for(self.ws_connection.recv(), timeout=30)
                self.message_count += 1
                await self._process_message(message)
                
            except asyncio.TimeoutError:
                try:
                    await self.ws_connection.ping()
                except:
                    await self._handle_disconnect()
                    break
                    
            except websockets.exceptions.ConnectionClosed:
                logger.warning("⚠️ WebSocket соединение закрыто")
                await self._handle_disconnect()
                break
                
            except Exception as e:
                logger.error(f"❌ Ошибка при получении сообщения: {e}")
                await asyncio.sleep(1)
    
    async def _process_message(self, message: str):
        """Обработка входящего сообщения"""
        try:
            if isinstance(message, bytes):
                logger.debug("📦 Получены бинарные данные (protobuf)")
                return
            
            data = json.loads(message)
            
            if data.get('msg') == 'PONG':
                logger.debug("📥 PONG получен")
                return
            
            if 'id' in data and 'msg' in data:
                if 'SUBSCRIPTION' in data['msg']:
                    logger.info(f"✅ Подписка подтверждена: {data.get('c', '')}")
                return
            
            # Обработка данных о сделках на фьючерсах
            if 'd' in data and 'deals' in data['d']:
                channel = data.get('c', '')
                if '@' in channel:
                    parts = channel.split('@')
                    if len(parts) >= 3:
                        symbol_raw = parts[-1]
                        if symbol_raw.endswith('USDT'):
                            base = symbol_raw.replace('USDT', '')
                            symbol = f"{base}/USDT"
                            
                            deals = data['d']['deals']
                            if deals:
                                latest_deal = deals[-1]
                                price = float(latest_deal.get('p', 0))
                                timestamp = latest_deal.get('t', int(time.time() * 1000))
                                
                                async with self.lock:
                                    self.latest_prices[symbol] = {
                                        'price': price,
                                        'timestamp': timestamp,
                                        'time': datetime.now().strftime('%H:%M:%S.%f')[:-3]
                                    }
                                
                                if self.callback:
                                    await self.callback(symbol, price, timestamp)
            
            # Обработка мини-тикера фьючерсов
            elif 'd' in data and 's' in data['d']:
                channel = data.get('c', '')
                if 'miniTicker' in channel:
                    ticker_data = data['d']
                    symbol_raw = ticker_data.get('s', '')
                    if symbol_raw.endswith('USDT'):
                        base = symbol_raw.replace('USDT', '')
                        symbol = f"{base}/USDT"
                        price = float(ticker_data.get('p', 0))
                        timestamp = ticker_data.get('t', int(time.time() * 1000))
                        
                        async with self.lock:
                            self.latest_prices[symbol] = {
                                'price': price,
                                'timestamp': timestamp,
                                'time': datetime.now().strftime('%H:%M:%S.%f')[:-3]
                            }
                        
                        if self.callback:
                            await self.callback(symbol, price, timestamp)
                
        except json.JSONDecodeError:
            logger.error(f"❌ Ошибка парсинга JSON: {message[:100]}")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения: {e}")
    
    async def subscribe(self, symbol: str) -> bool:
        """Подписка на обновления цены (фьючерсные каналы)"""
        try:
            if not self.ws_connection or not self.running:
                logger.warning(f"⚠️ WebSocket не подключен, невозможно подписаться на {symbol}")
                return False
            
            if len(self.subscribed_symbols) >= self.MAX_SUBSCRIPTIONS:
                logger.warning(f"⚠️ Достигнут лимит подписок ({self.MAX_SUBSCRIPTIONS})")
                return False
            
            symbol_raw = symbol.replace('/', '').upper()
            
            # Фьючерсные каналы
            channels = [
                f"contract@public.deals.v3.api@{symbol_raw}",
                f"contract@public.miniTicker.v3.api@{symbol_raw}",
                f"contract@public.ticker.v3.api@{symbol_raw}"
            ]
            
            async with self.lock:
                for channel in channels:
                    subscribe_msg = {
                        "method": "SUBSCRIPTION",
                        "params": [channel]
                    }
                    await self.ws_connection.send(json.dumps(subscribe_msg))
                    logger.info(f"✅ WebSocket подписка на {symbol} через {channel}")
                
                self.subscribed_symbols.add(symbol)
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка подписки на {symbol}: {e}")
            return False
    
    async def _resubscribe_all(self):
        """Переподписка на все символы после переподключения"""
        if self.subscribed_symbols:
            logger.info(f"🔄 Переподписываюсь на {len(self.subscribed_symbols)} символов...")
            symbols_list = list(self.subscribed_symbols)
            for i in range(0, len(symbols_list), self.MAX_SUBSCRIPTIONS):
                batch = symbols_list[i:i+self.MAX_SUBSCRIPTIONS]
                for symbol in batch:
                    await self.subscribe(symbol)
                    await asyncio.sleep(0.1)
                await asyncio.sleep(1)
    
    async def _handle_disconnect(self):
        """Обработка отключения с автоматическим переподключением"""
        self.running = False
        if self.ping_task:
            self.ping_task.cancel()
        
        if self.ws_connection:
            await self.ws_connection.close()
        
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            wait_time = 2 ** self.reconnect_attempts
            logger.info(f"🔄 Попытка переподключения {self.reconnect_attempts}/{self.max_reconnect_attempts} через {wait_time}с...")
            await asyncio.sleep(wait_time)
            await self.connect()
        else:
            logger.error("❌ Превышено количество попыток переподключения")
    
    async def get_latest_price(self, symbol: str) -> Optional[Dict]:
        """Получение последней цены с информацией об источнике"""
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
        """Остановка WebSocket"""
        self.running = False
        if self.ping_task:
            self.ping_task.cancel()
        if self.ws_connection:
            await self.ws_connection.close()
        logger.info("🛑 WebSocket монитор остановлен")


# ============== ПАМП-ДАМП АНАЛИЗАТОР ==============

class PumpDumpAnalyzer:
    """
    Анализатор пампа и дампа.
    Отслеживает резкие ценовые движения.
    """
    
    def __init__(self, settings: Dict = None):
        self.settings = settings or PUMP_DUMP_SETTINGS
        self.pump_threshold = self.settings.get('threshold', 7.0)
        self.dump_threshold = -self.pump_threshold
        self.time_windows = self.settings.get('time_windows', [1, 3, 5, 15])
        self.max_history_minutes = self.settings.get('history_minutes', 30)
        self.price_history = {}  # {symbol: [(timestamp, price)]}
        self.last_events = {}     # для предотвращения дублей
        
    async def add_price_point(self, symbol: str, price: float, timestamp: int):
        """Добавление новой цены в историю"""
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        
        current_time = timestamp / 1000  # переводим в секунды
        self.price_history[symbol].append((current_time, price))
        
        # Очищаем старые записи
        cutoff_time = current_time - (self.max_history_minutes * 60)
        self.price_history[symbol] = [
            (t, p) for t, p in self.price_history[symbol] if t > cutoff_time
        ]
        
        # Анализируем на наличие событий
        return await self.analyze_symbol(symbol)
    
    def calculate_change(self, symbol: str, minutes: int) -> Optional[Dict]:
        """Расчет изменения цены за указанное количество минут"""
        if symbol not in self.price_history or len(self.price_history[symbol]) < 2:
            return None
        
        history = self.price_history[symbol]
        current_time = history[-1][0]
        current_price = history[-1][1]
        
        # Ищем цену minutes назад
        target_time = current_time - (minutes * 60)
        closest_point = None
        min_time_diff = float('inf')
        
        for t, p in history:
            time_diff = abs(t - target_time)
            if time_diff < min_time_diff:
                min_time_diff = time_diff
                closest_point = (t, p)
        
        if closest_point is None or min_time_diff > 60:  # если расхождение больше минуты
            return None
        
        past_price = closest_point[1]
        time_span = current_time - closest_point[0]  # фактический промежуток в секундах
        
        if time_span < 30:  # слишком маленький промежуток
            return None
        
        change_percent = ((current_price - past_price) / past_price) * 100
        
        # Определение направления
        direction = None
        if change_percent >= self.pump_threshold:
            direction = 'PUMP'
        elif change_percent <= self.dump_threshold:
            direction = 'DUMP'
        
        if direction is None:
            return None
        
        return {
            'symbol': symbol,
            'type': direction,
            'change_percent': round(change_percent, 2),
            'time_window': minutes,
            'actual_time': round(time_span / 60, 1),  # в минутах
            'start_price': round(past_price, 8),
            'end_price': round(current_price, 8),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    async def analyze_symbol(self, symbol: str) -> List[Dict]:
        """Анализ символа по всем временным окнам"""
        events = []
        
        for minutes in self.time_windows:
            event = self.calculate_change(symbol, minutes)
            if event:
                event_key = f"{symbol}_{minutes}_{event['type']}"
                last_time = self.last_events.get(event_key, 0)
                
                if time.time() - last_time > 300:
                    events.append(event)
                    self.last_events[event_key] = time.time()
        
        return events


# ============== АНАЛИЗАТОР ДИВЕРГЕНЦИЙ ==============

class DivergenceAnalyzer:
    """
    Анализатор дивергенций RSI и MACD.
    Отслеживает расхождения между ценой и индикаторами.
    """
    
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
        
        if rsi_div['bullish']:
            result['signals'].append(rsi_div['description'])
        if rsi_div['bearish']:
            result['signals'].append(rsi_div['description'])
        if macd_div['bullish']:
            result['signals'].append(macd_div['description'])
        if macd_div['bearish']:
            result['signals'].append(macd_div['description'])
        
        return result


class FuturesDataFetcher:
    """Класс для получения данных с бирж (фьючерсы MEXC)"""
    
    def __init__(self):
        self.exchanges = {}
        self.available_pairs = {}
        self.session = None
        self.websocket = None
        logger.info("✅ MEXC Futures будет работать через новое API")
        
        if FEATURES['data_sources']['websocket']:
            asyncio.create_task(self._init_websocket())
        
        if FEATURES['exchanges']['bybit']:
            logger.info("✅ Bybit подключен (в разработке)")
        else:
            logger.warning("⚠️ Bybit временно отключен")
            
        if FEATURES['exchanges']['bingx']:
            logger.info("✅ BingX подключен (в разработке)")
        else:
            logger.warning("⚠️ BingX временно отключен")
    
    async def _init_websocket(self):
        """Инициализация WebSocket"""
        self.websocket = WebSocketMonitor()
        await self.websocket.connect()
    
    async def check_is_futures(self, symbol: str) -> bool:
        """Проверка, является ли пара фьючерсной"""
        try:
            symbol_raw = symbol.replace('/', '').upper()
            url = f"https://api.mexc.com/api/v1/contract/detail/{symbol_raw}"
            
            response = await asyncio.to_thread(requests.get, url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('code') == 200 and data.get('data') is not None
            
            return False
        except:
            return False
    
    async def fetch_all_pairs(self, exchange_name: str) -> List[str]:
    """Получение всех доступных фьючерсных пар с MEXC"""
    if exchange_name != 'MEXC':
        return []
    
    try:
        # Используем альтернативный эндпоинт для получения списка контрактов
        url = "https://api.mexc.com/api/v1/contract/detail"
        logger.info(f"🔍 Загружаю список всех фьючерсных пар с MEXC...")
        
        headers = {
            'User-Agent': 'Mozilla/5.0'
        }
        
        response = await asyncio.to_thread(requests.get, url, timeout=10, headers=headers)
        
        if response.status_code != 200:
            logger.error(f"❌ MEXC: HTTP {response.status_code}")
            # Если не работает, используем запасной список популярных фьючерсов
            fallback_futures = [
                'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
                'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'LINK/USDT', 'MATIC/USDT',
                'DOT/USDT', 'UNI/USDT', 'ATOM/USDT', 'LTC/USDT', 'BCH/USDT',
                'ALGO/USDT', 'NEAR/USDT', 'FIL/USDT', 'APT/USDT', 'ARB/USDT',
                'OP/USDT', 'INJ/USDT', 'TIA/USDT', 'WIF/USDT', 'PEPE/USDT',
                'BONK/USDT', 'SHIB/USDT', 'AAVE/USDT', 'CRV/USDT', 'SNX/USDT'
            ]
            logger.info(f"📊 Использую запасной список из {len(fallback_futures)} фьючерсных пар")
            return fallback_futures
        
        data = response.json()
        
        all_pairs = []
        if isinstance(data, dict) and data.get('code') == 200 and data.get('data'):
            contracts = data['data']
            for contract in contracts:
                if isinstance(contract, dict):
                    symbol = contract.get('symbol')
                    if symbol and 'USDT' in symbol:
                        # Конвертируем из BTCUSDT в BTC/USDT
                        base = symbol.replace('USDT', '')
                        formatted = f"{base}/USDT"
                        all_pairs.append(formatted)
                        
                        if self.websocket and FEATURES['data_sources']['websocket']:
                            await self.websocket.subscribe(formatted)
        
        if all_pairs:
            logger.info(f"📊 MEXC Futures: загружено {len(all_pairs)} пар")
            return all_pairs
        else:
            # Если API вернуло пустой список, используем запасной
            fallback_futures = [
                'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
                'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'LINK/USDT', 'MATIC/USDT',
                'DOT/USDT', 'UNI/USDT', 'ATOM/USDT', 'LTC/USDT', 'BCH/USDT',
                'ALGO/USDT', 'NEAR/USDT', 'FIL/USDT', 'APT/USDT', 'ARB/USDT',
                'OP/USDT', 'INJ/USDT', 'TIA/USDT', 'WIF/USDT', 'PEPE/USDT',
                'BONK/USDT', 'SHIB/USDT', 'AAVE/USDT', 'CRV/USDT', 'SNX/USDT'
            ]
            logger.info(f"📊 Использую запасной список из {len(fallback_futures)} фьючерсных пар")
            return fallback_futures
            
    except Exception as e:
        logger.error(f"❌ MEXC: ошибка загрузки пар - {e}")
        # В случае ошибки используем запасной список
        fallback_futures = [
            'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
            'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'LINK/USDT', 'MATIC/USDT',
            'DOT/USDT', 'UNI/USDT', 'ATOM/USDT', 'LTC/USDT', 'BCH/USDT',
            'ALGO/USDT', 'NEAR/USDT', 'FIL/USDT', 'APT/USDT', 'ARB/USDT',
            'OP/USDT', 'INJ/USDT', 'TIA/USDT', 'WIF/USDT', 'PEPE/USDT',
            'BONK/USDT', 'SHIB/USDT', 'AAVE/USDT', 'CRV/USDT', 'SNX/USDT'
        ]
        logger.info(f"📊 Использую запасной список из {len(fallback_futures)} фьючерсных пар")
        return fallback_futures
    
    async def fetch_ohlcv(self, exchange_name: str, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """Получение свечных данных (фьючерсы)"""
        if exchange_name != 'MEXC':
            return None
        
        try:
            symbol_raw = symbol.replace('/', '').upper()
            
            interval_map = {
                '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
                '1h': '1h', '4h': '4h', '1d': '1d', '1w': '1w'
            }
            interval = interval_map.get(timeframe, '15m')
            
            # Эндпоинт для фьючерсных свечей
            url = f"https://api.mexc.com/api/v1/contract/kline/{symbol_raw}?interval={interval}&limit={limit}"
            
            response = await asyncio.to_thread(requests.get, url, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"❌ MEXC Futures: HTTP {response.status_code} для {symbol} {timeframe}")
                return None
            
            data = response.json()
            
            if data.get('code') != 200 or not data.get('data'):
                return None
            
            klines = data['data']
            if len(klines) < 20:
                return None
            
            rows = []
            for kline in klines:
                # Формат фьючерсных свечей: [timestamp, open, high, low, close, volume]
                rows.append([kline[0], float(kline[1]), float(kline[2]), 
                            float(kline[3]), float(kline[4]), float(kline[5])])
            
            df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            return df
            
        except Exception as e:
            logger.error(f"Ошибка загрузки {symbol} {timeframe} с MEXC: {e}")
            return None
    
    async def fetch_funding_rate(self, exchange_name: str, symbol: str) -> Optional[float]:
        """Получение ставки фондирования с MEXC Futures"""
        try:
            symbol_raw = symbol.replace('/', '').upper()
            url = f"https://api.mexc.com/api/v1/contract/funding_rate/{symbol_raw}"
            
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0'
            }
            
            response = await asyncio.to_thread(requests.get, url, timeout=5, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('code') == 200 and data.get('data') is not None:
                    return float(data['data'])
            
            return 0.0
            
        except Exception as e:
            logger.debug(f"Ошибка получения фандинга для {symbol}: {e}")
            return 0.0
    
    async def fetch_ticker(self, exchange_name: str, symbol: str) -> Dict:
        """Получение тикера (фьючерсы)"""
        if exchange_name != 'MEXC':
            return {}
        
        try:
            symbol_raw = symbol.replace('/', '').upper()
            url = f"https://api.mexc.com/api/v1/contract/ticker/{symbol_raw}"
            
            response = await asyncio.to_thread(requests.get, url, timeout=5)
            
            if response.status_code != 200:
                return {}
            
            data = response.json()
            
            if data.get('code') == 200 and data.get('data'):
                ticker = data['data']
                return {
                    'volume_24h': float(ticker.get('volume', 0)),
                    'price_change_24h': float(ticker.get('changePercent', 0)),
                    'last': float(ticker.get('lastPrice', 0))
                }
            return {}
        except:
            return {}
    
    async def get_price_with_source(self, symbol: str, http_price: float = None) -> Dict:
        """Получение цены с указанием источника"""
        if self.websocket and FEATURES['data_sources']['websocket']:
            ws_price = await self.websocket.get_latest_price(symbol)
            if ws_price:
                return ws_price
        
        return {
            'price': http_price,
            'source': 'http',
            'time': datetime.now().strftime('%H:%M:%S')
        }
    
    async def close_all(self):
        """Закрытие соединений."""
        if self.websocket:
            await self.websocket.stop()


class MultiTimeframeAnalyzer:
    """Анализатор"""
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Расчет индикаторов"""
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
        """Генерация сигнала"""
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
                reasons.append(f"Цена выше VWAP ({last['vwap']:.4f})")
                confidence += 10
            else:
                reasons.append(f"Цена ниже VWAP ({last['vwap']:.4f})")
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
                targets['target_1'] = round(current_price + atr * 1.5, 8)
                targets['target_2'] = round(current_price + atr * 3.0, 8)
                targets['stop_loss'] = round(current_price - atr * 1.0, 8)
            else:
                targets['target_1'] = round(current_price - atr * 1.5, 8)
                targets['target_2'] = round(current_price - atr * 3.0, 8)
                targets['stop_loss'] = round(current_price + atr * 1.0, 8)
        
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
            'confidence': confidence,
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


class FuturesScannerBot:
    """Основной класс бота"""
    
    def __init__(self):
        self.fetcher = FuturesDataFetcher()
        self.analyzer = MultiTimeframeAnalyzer()
        self.telegram_bot = Bot(token=TELEGRAM_TOKEN)
        self.scanned_pairs = set()
        
        if FEATURES['advanced']['pump_dump']:
            self.pump_dump = PumpDumpAnalyzer(PUMP_DUMP_SETTINGS)
            logger.info("✅ Памп-дамп анализатор инициализирован")
        else:
            self.pump_dump = None
        
        if FEATURES['advanced']['divergence']:
            self.divergence = DivergenceAnalyzer()
            logger.info("✅ Анализатор дивергенций инициализирован")
        else:
            self.divergence = None
    
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
        
        price_display = f"`{signal['price']:.8f}`"
        if DISPLAY_SETTINGS['show_price_source'] and price_source:
            source_text = "(w)" if price_source['source'] == 'websocket' else "(h)"
            price_display = f"`{signal['price']:.8f}` {source_text}"
        
        lines = [
            f"{emoji} *СИГНАЛ {signal['exchange']}*",
            f"└ `{signal['symbol']}` {signal['signal_power']}\n",
            f"📊 *Направление:* {signal['direction']}",
            f"🎯 *Текущая цена:* {price_display}\n"
        ]
        
        if pump_only:
            if signal.get('pump_dump'):
                lines.append(f"⚡ *Импульсный анализ:*")
                for event in signal['pump_dump']:
                    emoji_pump = "🚀" if event['type'] == 'PUMP' else "📉"
                    lines.append(
                        f"└ {emoji_pump} {event['time_window']}мин: "
                        f"{event['change_percent']:+.2f}%"
                    )
                lines.append("")
        else:
            if DISPLAY_SETTINGS['show_divergence'] and signal.get('divergence') and signal['divergence']['has_divergence']:
                lines.append(f"📊 *Дивергенции:*")
                for div_signal in signal['divergence']['signals']:
                    lines.append(f"└ {div_signal}")
                lines.append("")
            
            if DISPLAY_SETTINGS['show_pump_dump'] and signal.get('pump_dump'):
                lines.append(f"⚡ *Импульсный анализ:*")
                for event in signal['pump_dump']:
                    emoji_pump = "🚀" if event['type'] == 'PUMP' else "📉"
                    lines.append(
                        f"└ {emoji_pump} {event['time_window']}мин: "
                        f"{event['change_percent']:+.2f}%"
                    )
                lines.append("")
            
            if DISPLAY_SETTINGS['show_patterns'] and signal.get('patterns'):
                patterns = signal['patterns']
                active_patterns = [k for k, v in patterns.items() if v]
                if active_patterns:
                    lines.append(f"🕯 *Свечные паттерны (15m):*")
                    for pattern in active_patterns:
                        direction = patterns[pattern]
                        emoji_pattern = "🟢" if direction in ['бычий', True] else "🔴" if direction in ['медвежий', False] else "⚪"
                        pattern_names = {
                            'hammer': 'Молот',
                            'shooting_star': 'Падающая звезда',
                            'pin_bar': 'Пинбар',
                            'engulfing': 'Поглощение',
                            'doji': 'Дожи'
                        }
                        name = pattern_names.get(pattern, pattern)
                        lines.append(f"└ {emoji_pattern} {name} ({direction})")
                    lines.append("")
        
        if 'target_1' in signal:
            lines.extend([
                f"🎯 *Цели:*",
                f"└ Цель 1: `{signal['target_1']:.8f}`",
                f"└ Цель 2: `{signal['target_2']:.8f}`",
                f"└ Стоп-лосс: `{signal['stop_loss']:.8f}`\n"
            ])
        
        lines.append(f"⚡️ *Параметры:*")
        
        if DISPLAY_SETTINGS['show_volume']:
            volume = self.format_volume(signal.get('volume_24h', 0))
            lines.append(f"└ Объем 24ч: `{volume}`")
        
        if DISPLAY_SETTINGS['show_funding']:
            funding = self.format_funding(signal.get('funding_rate', 0))
            lines.append(f"└ Фандинг: `{funding}`")
        
        lines.append("")
        
        lines.extend([
            f"🔥 *Уверенность:* {signal['confidence']:.1f}%",
            f"⚡️ *Сила сигнала:* {signal['signal_strength']}/100\n"
        ])
        
        if DISPLAY_SETTINGS['show_alignment'] and signal.get('alignment'):
            align = signal['alignment']
            lines.append(f"📊 *Старшие таймфреймы:*")
            if align.get('hourly_trend'):
                lines.append(f"└ 1ч: {align['hourly_trend']}")
            if align.get('daily_trend'):
                lines.append(f"└ 1д: {align['daily_trend']}")
            if align.get('weekly_trend'):
                lines.append(f"└ 1н: {align['weekly_trend']}")
            lines.append("")
        
        if signal['reasons']:
            lines.append(f"💡 *Причины:*")
            for reason in signal['reasons']:
                lines.append(f"• {reason}")
            lines.append("")
        
        lines.append(f"⏱️ *Время:* {signal['time']}")
        
        keyboard = []
        
        if DISPLAY_SETTINGS['buttons']['copy']:
            coin = signal['symbol'].replace('/USDT', '')
            keyboard.append([InlineKeyboardButton(f"📋 Скопировать {coin}", callback_data=f"copy_{coin}")])
        
        if DISPLAY_SETTINGS['buttons']['trade']:
            exch = signal['exchange']
            if exch in REF_LINKS:
                keyboard.append([InlineKeyboardButton(f"🚀 Торговать на {exch}", url=REF_LINKS[exch])])
        
        action_row = []
        if DISPLAY_SETTINGS['buttons']['refresh']:
            action_row.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{signal['symbol']}"))
        if DISPLAY_SETTINGS['buttons']['details']:
            action_row.append(InlineKeyboardButton("📊 Детали", callback_data=f"details_{signal['symbol']}"))
        
        if action_row:
            keyboard.append(action_row)
        
        return "\n".join(lines), InlineKeyboardMarkup(keyboard)
    
    async def scan_all(self) -> List[Dict]:
        logger.info("="*50)
        logger.info("🚀 НАЧАЛО СКАНИРОВАНИЯ ФЬЮЧЕРСОВ")
        logger.info("="*50)
        
        all_signals = []
        
        try:
            pairs = await self.fetcher.fetch_all_pairs('MEXC')
            if pairs:
                logger.info(f"📊 MEXC Futures: анализирую {min(PAIRS_TO_SCAN, len(pairs))} пар из {len(pairs)}")
                
                for i, pair in enumerate(pairs[:PAIRS_TO_SCAN]):
                    try:
                        # Проверяем, что это фьючерс
                        is_futures = await self.fetcher.check_is_futures(pair)
                        if not is_futures:
                            logger.debug(f"⏭️ {pair} не является фьючерсом, пропускаем")
                            continue
                        
                        dataframes = {}
                        for tf_name, tf_value in TIMEFRAMES.items():
                            limit = 200 if tf_name == 'current' else 100
                            df = await self.fetcher.fetch_ohlcv('MEXC', pair, tf_value, limit)
                            if df is not None and not df.empty:
                                df = self.analyzer.calculate_indicators(df)
                                dataframes[tf_name] = df
                        
                        if not dataframes:
                            continue
                        
                        pump_events = []
                        if self.pump_dump and 'current' in dataframes:
                            df_current = dataframes['current']
                            last_price = df_current['close'].iloc[-1]
                            last_timestamp = int(df_current.index[-1].timestamp() * 1000)
                            
                            pump_events = await self.pump_dump.add_price_point(
                                pair, last_price, last_timestamp
                            )
                            
                            if pump_events:
                                logger.info(f"📊 Обнаружен памп/дамп для {pair}: {pump_events}")
                        
                        signal_divergence = None
                        if self.divergence and 'current' in dataframes:
                            df_current = dataframes['current']
                            signal_divergence = self.divergence.analyze(df_current, '15m')
                            
                            if signal_divergence and signal_divergence['has_divergence']:
                                logger.info(f"📊 Обнаружена дивергенция для {pair}: {signal_divergence['signals']}")
                        
                        patterns = None
                        if FEATURES['advanced']['patterns'] and 'current' in dataframes:
                            df_current = dataframes['current']
                            patterns = detect_candle_patterns(df_current)
                        
                        funding = await self.fetcher.fetch_funding_rate('MEXC', pair)
                        ticker = await self.fetcher.fetch_ticker('MEXC', pair)
                        
                        metadata = {
                            'funding_rate': funding,
                            'volume_24h': ticker.get('volume_24h'),
                            'price_change_24h': ticker.get('price_change_24h')
                        }
                        
                        signal = self.analyzer.generate_signal(dataframes, metadata, pair, 'MEXC')
                        
                        if signal:
                            if pump_events:
                                signal['pump_dump'] = pump_events
                                if len(pump_events) > 0:
                                    strongest = max(pump_events, key=lambda x: abs(x['change_percent']))
                                    if abs(strongest['change_percent']) >= 10:
                                        signal['confidence'] = min(signal['confidence'] + 15, 100)
                                    elif abs(strongest['change_percent']) >= 7:
                                        signal['confidence'] = min(signal['confidence'] + 10, 100)
                            
                            if signal_divergence and signal_divergence['has_divergence']:
                                signal['divergence'] = signal_divergence
                                signal['confidence'] = min(signal['confidence'] + signal_divergence['strength'] / 2, 100)
                                if signal_divergence['bullish']:
                                    signal['reasons'].append(f"📊 Бычья дивергенция (сила {signal_divergence['strength']:.0f}%)")
                                elif signal_divergence['bearish']:
                                    signal['reasons'].append(f"📊 Медвежья дивергенция (сила {signal_divergence['strength']:.0f}%)")
                            
                            if patterns:
                                signal['patterns'] = patterns
                                if patterns.get('engulfing'):
                                    signal['reasons'].append("📊 Паттерн поглощения (15m)")
                                    signal['confidence'] = min(signal['confidence'] + 15, 100)
                                if patterns.get('pin_bar'):
                                    signal['reasons'].append("📊 Пинбар (15m)")
                                    signal['confidence'] = min(signal['confidence'] + 10, 100)
                                if patterns.get('doji'):
                                    signal['reasons'].append("📊 Дожи (15m)")
                                    signal['confidence'] = min(signal['confidence'] + 5, 100)
                            
                            signal['confidence'] = round(signal['confidence'], 1)
                        
                        if signal and signal['confidence'] >= MIN_CONFIDENCE:
                            all_signals.append(signal)
                            logger.info(f"✅ {pair} - {signal['direction']} ({signal['confidence']}%)")
                        
                        if (i + 1) % 10 == 0:
                            logger.info(f"📊 Прогресс MEXC: {i + 1}/{min(PAIRS_TO_SCAN, len(pairs))}")
                        
                        await asyncio.sleep(0.3)
                        
                    except Exception as e:
                        logger.error(f"Ошибка анализа {pair}: {e}")
                        continue
        except Exception as e:
            logger.error(f"Ошибка сканирования MEXC: {e}")
        
        all_signals.sort(key=lambda x: x['signal_strength'], reverse=True)
        
        logger.info("="*50)
        logger.info(f"🎯 ВСЕГО НАЙДЕНО СИГНАЛОВ: {len(all_signals)}")
        logger.info("="*50)
        
        return all_signals
    
    async def send_signal(self, signal: Dict, price_source: Dict = None, pump_only: bool = False):
        if pump_only and not signal.get('pump_dump'):
            logger.debug(f"Сигнал {signal['symbol']} пропущен (нет памп-дамп)")
            return
        
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
        logger.info(f"🕐 Запуск анализа")
        
        signals = await self.scan_all()
        
        if signals:
            for i, signal in enumerate(signals[:5]):
                price_source = await self.fetcher.get_price_with_source(signal['symbol'], signal['price'])
                await self.send_signal(signal, price_source, pump_only=False)
                if i < len(signals[:5]) - 1:
                    await asyncio.sleep(3)
        else:
            logger.info("❌ Сигналов не найдено")
    
    async def run(self):
        logger.info("🤖 Бот запущен (Фьючерсный режим)")
        
        try:
            while True:
                await self.scheduled()
                logger.info(f"💤 Следующий анализ через {UPDATE_INTERVAL//60} мин")
                await asyncio.sleep(UPDATE_INTERVAL)
        except KeyboardInterrupt:
            logger.info("🛑 Бот остановлен пользователем")
        except Exception as e:
            logger.error(f"💥 Критическая ошибка: {e}")
            traceback.print_exc()
        finally:
            await self.fetcher.close_all()


class TelegramHandler:
    def __init__(self, bot: FuturesScannerBot):
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
        await update.message.reply_text(
            "🤖 *Фьючерсный сканер PRO*\n\n"
            "📊 Анализирую MEXC Futures\n"
            "📈 Мультитаймфрейм (15m/1h/1d/1w)\n"
            "🔥 Памп-дамп анализ (7%+)\n"
            "📈 Дивергенции RSI/MACD\n"
            "📊 VWAP институциональный уровень\n"
            "🕯 Свечные паттерны\n"
            "⚡ WebSocket реального времени (w)\n\n"
            "Команды:\n"
            "/scan - Сканировать\n"
            "/pump - Только памп-дамп сигналы\n"
            "/status - Статус\n"
            "/help - Помощь",
            parse_mode='Markdown'
        )
    
    async def scan(self, update, context):
        msg = await update.message.reply_text("🔍 Сканирую...")
        signals = await self.bot.scan_all()
        if signals:
            await msg.edit_text(f"✅ Найдено {len(signals)} сигналов")
            for signal in signals[:5]:
                price_source = await self.bot.fetcher.get_price_with_source(signal['symbol'], signal['price'])
                await self.bot.send_signal(signal, price_source, pump_only=False)
                await asyncio.sleep(2)
        else:
            await msg.edit_text("❌ Сигналов не найдено")
    
    async def pump_only(self, update, context):
        msg = await update.message.reply_text("🔍 Ищу памп-дамп сигналы...")
        signals = await self.bot.scan_all()
        pump_signals = [s for s in signals if s.get('pump_dump') and len(s['pump_dump']) > 0]
        
        if pump_signals:
            await msg.edit_text(f"✅ Найдено {len(pump_signals)} памп-дамп сигналов")
            for signal in pump_signals[:5]:
                price_source = await self.bot.fetcher.get_price_with_source(signal['symbol'], signal['price'])
                await self.bot.send_signal(signal, price_source, pump_only=True)
                await asyncio.sleep(2)
        else:
            await msg.edit_text("❌ Памп-дамп сигналов не найдено")
    
    async def status(self, update, context):
        text = "*📡 Статус:*\n\n"
        text += f"✅ MEXC Futures: активен\n"
        
        if self.bot.fetcher.websocket and self.bot.fetcher.websocket.running:
            ws_stats = f" ({len(self.bot.fetcher.websocket.subscribed_symbols)} подписок, {self.bot.fetcher.websocket.message_count} сообщ.)"
            text += f"✅ WebSocket: активен{ws_stats}\n"
        else:
            text += f"❌ WebSocket: отключен\n"
        
        text += f"\n📊 *Функции:*\n"
        text += f"✓ Памп-дамп: {'вкл' if FEATURES['advanced']['pump_dump'] else 'выкл'}\n"
        text += f"✓ Дивергенции: {'вкл' if FEATURES['advanced']['divergence'] else 'выкл'}\n"
        text += f"✓ VWAP: {'вкл' if FEATURES['advanced']['vwap'] else 'выкл'}\n"
        text += f"✓ Паттерны: {'вкл' if FEATURES['advanced']['patterns'] else 'выкл'}\n"
        text += f"✓ WebSocket: {'вкл' if FEATURES['data_sources']['websocket'] else 'выкл'}"
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def help(self, update, context):
        await update.message.reply_text(
            "*Помощь*\n\n"
            "📊 *Анализ:*\n"
            "• RSI, MACD, EMA\n"
            "• Объемы, VWAP\n"
            "• Дивергенции\n"
            "• Памп-дамп (>7%)\n"
            "• Свечные паттерны\n"
            "• WebSocket (w) реальное время\n\n"
            "⚙️ *Команды:*\n"
            "/scan - все сигналы\n"
            "/pump - только памп-дамп\n"
            "/status - состояние бота\n\n"
            "⚙️ *Кнопки:*\n"
            "🔄 Обновить - обновить сигнал\n"
            "📊 Детали - подробный анализ\n\n"
            "⚙️ Настройки в config.py",
            parse_mode='Markdown'
        )
    
    async def button(self, update, context):
        query = update.callback_query
        await query.answer()
        
        if query.data.startswith("copy_"):
            coin = query.data.replace("copy_", "")
            await query.message.reply_text(f"`{coin}`", parse_mode='Markdown')
        
        elif query.data.startswith("refresh_"):
            symbol = query.data.replace("refresh_", "")
            await query.edit_message_text(f"🔄 Обновляю {symbol}...")
            await asyncio.sleep(1)
            await query.edit_message_text(f"✅ {symbol} обновлен")
        
        elif query.data.startswith("details_"):
            symbol = query.data.replace("details_", "")
            await query.edit_message_text(f"📊 Детальный анализ для {symbol} скоро будет доступен")
    
    def run(self):
        self.app.run_polling()


async def main():
    bot = FuturesScannerBot()
    handler = TelegramHandler(bot)
    
    polling = asyncio.create_task(asyncio.to_thread(handler.run))
    
    try:
        await bot.run()
    finally:
        polling.cancel()


if __name__ == "__main__":
    asyncio.run(main())

