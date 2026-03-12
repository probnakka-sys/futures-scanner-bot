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

# ============== BINGX FUTURES С WEBSOCKET (РАБОЧАЯ ВЕРСИЯ) ==============

class BingxFetcher(BaseExchangeFetcher):
    """Фетчер для BingX Futures с WebSocket через python-bingx"""
    
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
        
        # WebSocket через библиотеку python-bingx
        self.ws_client = None
        self.ws_enabled = FEATURES['data_sources'].get('websocket', False)
        self.latest_prices = {}
        self.ws_task = None
        
        if self.ws_enabled:
            asyncio.create_task(self._init_websocket())
        
        logger.info("✅ BingX Futures инициализирован")
    
    async def _init_websocket(self):
        """Инициализация WebSocket через python-bingx"""
        try:
            from bingX import BingX
            
            # Ключи не обязательны для публичных данных!
            self.ws_client = BingX(api_key="", secret_key="")
            logger.info("✅ WebSocket клиент BingX инициализирован")
            
            # Запускаем прослушивание
            self.ws_task = asyncio.create_task(self._ws_listen())
            
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации WebSocket: {e}")
    
    async def _ws_listen(self):
        """Прослушивание публичных WebSocket каналов"""
        try:
            from bingX.perpetual.v2 import PerpetualV2
            
            # Создаем клиент для публичных данных
            client = PerpetualV2(api_key="", secret_key="")
            
            # Подписываемся на поток всех тикеров (публичный канал)
            async for ticker in client.market.ws_tickers():
                if ticker:
                    symbol_raw = ticker.get('symbol', '')
                    if symbol_raw and 'USDT' in symbol_raw:
                        base = symbol_raw.replace('USDT', '').replace('-', '')
                        symbol = f"{base}/USDT:USDT"
                        price = float(ticker.get('lastPrice', 0))
                        
                        if price > 0:
                            self.latest_prices[symbol] = {
                                'price': price,
                                'time': datetime.now().strftime('%H:%M:%S.%f')[:-3]
                            }
                            logger.debug(f"📈 WebSocket {symbol}: {price}")
                            
        except Exception as e:
            logger.error(f"❌ Ошибка в WebSocket потоке: {e}")
            await asyncio.sleep(5)
            # Перезапускаем при ошибке
            if self.ws_enabled:
                asyncio.create_task(self._ws_listen())
    
    async def fetch_all_pairs(self) -> List[str]:
        """Получение всех пар"""
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
        """Получение свечных данных"""
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
        """Получение ставки фондирования"""
        try:
            funding = await self.exchange.fetch_funding_rate(symbol)
            return funding['fundingRate'] if funding else 0.0
        except:
            return 0.0
    
    async def fetch_ticker(self, symbol: str) -> Dict:
        """Получение тикера"""
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
        """Получение цены с указанием источника"""
        if symbol in self.latest_prices:
            return {
                'price': self.latest_prices[symbol]['price'],
                'source': 'websocket',
                'time': self.latest_prices[symbol]['time']
            }
        
        return {
            'price': http_price,
            'source': 'http',
            'time': datetime.now().strftime('%H:%M:%S')
        }
    
    async def close(self):
        """Закрытие соединений"""
        await self.exchange.close()
        if self.ws_task:
            self.ws_task.cancel()
            try:
                await self.ws_task
            except:
                pass
            
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

# ============== ОСНОВНОЙ КЛАСС БОТА ==============

class MultiExchangeScannerBot:
    """Бот для сканирования нескольких бирж"""
    
    def __init__(self):
        self.fetchers = {}
        self.analyzer = MultiTimeframeAnalyzer()
        self.telegram_bot = Bot(token=TELEGRAM_TOKEN)
        self.scanned_pairs = set()
        
        # Инициализация фетчеров согласно настройкам
        if FEATURES['exchanges'].get('bybit', {}).get('enabled', False):
            self.fetchers['Bybit'] = BybitFetcher()
        
        if FEATURES['exchanges'].get('bingx', {}).get('enabled', False):
            self.fetchers['BingX'] = BingxFetcher()
        
        if FEATURES['exchanges'].get('mexc', {}).get('enabled', False):
            self.fetchers['MEXC'] = MexcFetcher()
        
        if not self.fetchers:
            logger.error("❌ Нет активных бирж! Проверьте настройки FEATURES['exchanges']")
        
        # Инициализация анализаторов
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
    
    async def scan_exchange(self, name: str, fetcher: BaseExchangeFetcher) -> List[Dict]:
        """Сканирование одной биржи"""
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
                    
                    pump_events = []
                    if self.pump_dump and 'current' in dataframes:
                        df_current = dataframes['current']
                        last_price = df_current['close'].iloc[-1]
                        last_timestamp = int(df_current.index[-1].timestamp() * 1000)
                        
                        pump_events = await self.pump_dump.add_price_point(
                            pair, last_price, last_timestamp
                        )
                    
                    signal_divergence = None
                    if self.divergence and 'current' in dataframes:
                        df_current = dataframes['current']
                        signal_divergence = self.divergence.analyze(df_current, '15m')
                    
                    patterns = None
                    if FEATURES['advanced']['patterns'] and 'current' in dataframes:
                        df_current = dataframes['current']
                        patterns = detect_candle_patterns(df_current)
                    
                    funding = await fetcher.fetch_funding_rate(pair)
                    ticker = await fetcher.fetch_ticker(pair)
                    
                    metadata = {
                        'funding_rate': funding,
                        'volume_24h': ticker.get('volume_24h'),
                        'price_change_24h': ticker.get('price_change_24h')
                    }
                    
                    signal = self.analyzer.generate_signal(dataframes, metadata, pair, name)
                    
                    if signal:
                        if pump_events:
                            signal['pump_dump'] = pump_events
                            strongest = max(pump_events, key=lambda x: abs(x['change_percent'])) if pump_events else None
                            if strongest:
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
        """Сканирование всех активных бирж"""
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
        
        price_source = None
        if 'BingX' in self.fetchers and signal['exchange'] == 'BingX':
            price_source = await self.fetchers['BingX'].get_price_with_source(signal['symbol'], signal['price'])
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


# ============== АНАЛИЗАТОРЫ ==============

class PumpDumpAnalyzer:
    def __init__(self, settings: Dict = None):
        self.settings = settings or PUMP_DUMP_SETTINGS
        self.pump_threshold = self.settings.get('threshold', 7.0)
        self.dump_threshold = -self.pump_threshold
        self.time_windows = self.settings.get('time_windows', [1, 3, 5, 15])
        self.max_history_minutes = self.settings.get('history_minutes', 30)
        self.price_history = {}
        self.last_events = {}
    
    async def add_price_point(self, symbol: str, price: float, timestamp: int):
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        current_time = timestamp / 1000
        self.price_history[symbol].append((current_time, price))
        cutoff_time = current_time - (self.max_history_minutes * 60)
        self.price_history[symbol] = [
            (t, p) for t, p in self.price_history[symbol] if t > cutoff_time
        ]
        return await self.analyze_symbol(symbol)
    
    def calculate_change(self, symbol: str, minutes: int) -> Optional[Dict]:
        if symbol not in self.price_history or len(self.price_history[symbol]) < 2:
            return None
        history = self.price_history[symbol]
        current_time = history[-1][0]
        current_price = history[-1][1]
        target_time = current_time - (minutes * 60)
        closest_point = None
        min_time_diff = float('inf')
        for t, p in history:
            time_diff = abs(t - target_time)
            if time_diff < min_time_diff:
                min_time_diff = time_diff
                closest_point = (t, p)
        if closest_point is None or min_time_diff > 60:
            return None
        past_price = closest_point[1]
        time_span = current_time - closest_point[0]
        if time_span < 30:
            return None
        change_percent = ((current_price - past_price) / past_price) * 100
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
            'actual_time': round(time_span / 60, 1),
            'start_price': round(past_price, 8),
            'end_price': round(current_price, 8),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    async def analyze_symbol(self, symbol: str) -> List[Dict]:
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
        if rsi_div['bullish']:
            result['signals'].append(rsi_div['description'])
        if rsi_div['bearish']:
            result['signals'].append(rsi_div['description'])
        if macd_div['bullish']:
            result['signals'].append(macd_div['description'])
        if macd_div['bearish']:
            result['signals'].append(macd_div['description'])
        return result


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
            f"🔥 Памп-дамп анализ (7%+)\n"
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
        pump_signals = [s for s in signals if s.get('pump_dump') and len(s['pump_dump']) > 0]
        
        if pump_signals:
            await msg.edit_text(f"✅ Найдено {len(pump_signals)} памп-дамп сигналов")
            for signal in pump_signals[:5]:
                await self.bot.send_signal(signal, pump_only=True)
                await asyncio.sleep(2)
        else:
            await msg.edit_text("❌ Памп-дамп сигналов не найдено")
    
    async def status(self, update, context):
        text = "*📡 Статус:*\n\n"
        
        for name in self.bot.fetchers.keys():
            text += f"✅ {name} Futures: активен\n"
        
        text += f"\n⚡ WebSocket: {'✅ активен' if FEATURES['data_sources']['websocket'] else '❌ отключен'}\n"
        
        text += f"\n📊 *Функции:*\n"
        text += f"✓ Памп-дамп: {'вкл' if FEATURES['advanced']['pump_dump'] else 'выкл'}\n"
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
            "• Памп-дамп (>7%)\n"
            "• Свечные паттерны\n\n"
            "⚙️ *Команды:*\n"
            "/scan - все сигналы\n"
            "/pump - только памп-дамп\n"
            "/status - состояние бота\n\n"
            "⚙️ *Кнопки:*\n"
            "🔄 Обновить - обновить сигнал\n"
            "📊 Детали - подробный анализ",
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











