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

# Загрузка конфигурации
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
UPDATE_INTERVAL = int(os.getenv('UPDATE_INTERVAL', 900))
MIN_CONFIDENCE = int(os.getenv('MIN_CONFIDENCE', 65))
TIMEFRAME = os.getenv('TIMEFRAME', '15m')

# Реферальные ссылки
REF_LINKS = {
    'MEXC': os.getenv('MEXC_REF_LINK', 'https://www.mexc.com'),
    'Bybit': os.getenv('BYBIT_REF_LINK', 'https://www.bybit.com'),
    'BingX': os.getenv('BINGX_REF_LINK', 'https://bingx.com')
}

# Таймфреймы
TIMEFRAMES = {
    'current': TIMEFRAME,
    'hourly': '1h',
    'daily': '1d',
    'weekly': '1w'
}

# ============== ЗАПАСНОЙ СПИСОК ==============
FALLBACK_PAIRS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOGE/USDT', 'DOT/USDT', 'LINK/USDT', 'MATIC/USDT',
    'AVAX/USDT', 'UNI/USDT', 'ATOM/USDT', 'LTC/USDT', 'BCH/USDT',
    'ALGO/USDT', 'NEAR/USDT', 'FIL/USDT', 'APT/USDT', 'ARB/USDT',
    'AAVE/USDT', 'COMP/USDT', 'MKR/USDT', 'SNX/USDT', 'YFI/USDT',
    'CRV/USDT', 'BAL/USDT', '1INCH/USDT', 'OP/USDT', 'IMX/USDT',
    'AXS/USDT', 'SAND/USDT', 'MANA/USDT', 'GALA/USDT', 'ENJ/USDT',
    'FET/USDT', 'AGIX/USDT', 'OCEAN/USDT', 'GRT/USDT', 'BAND/USDT',
    'ATOM/USDT', 'OSMO/USDT', 'DOT/USDT', 'KSM/USDT', 'NEAR/USDT',
    'SOL/USDT', 'RAY/USDT', 'AVAX/USDT', 'JOE/USDT', 'BNB/USDT',
    'CAKE/USDT', 'BAKE/USDT', 'XVS/USDT', 'ALPACA/USDT', 'TWT/USDT',
    'ICP/USDT', 'RNDR/USDT', 'STX/USDT', 'FLOW/USDT', 'MINA/USDT',
    'EGLD/USDT', 'KDA/USDT', 'HNT/USDT', 'ANKR/USDT', 'ZIL/USDT',
    'IOST/USDT', 'IOTX/USDT', 'VET/USDT', 'THETA/USDT', 'TFUEL/USDT',
    'ROSE/USDT', 'CELR/USDT', 'CKB/USDT', 'ONE/USDT', 'HARMONY/USDT'
]

class FuturesDataFetcher:
    """Класс для получения данных с бирж. Использует синхронные запросы для надежности."""
    
    def __init__(self):
        self.exchanges = {}
        self.available_pairs = {}
        self.session = None
        logger.info("✅ MEXC будет работать через прямое API (синхронные запросы)")
        logger.warning("⚠️ Bybit временно отключен")
        logger.warning("⚠️ BingX временно отключен")
    
    async def fetch_all_pairs(self, exchange_name: str) -> List[str]:
        """Получение всех доступных пар. Работает только для MEXC."""
        if exchange_name != 'MEXC':
            return []
        
        try:
            import requests
            url = "https://api.mexc.com/api/v3/exchangeInfo"
            logger.info(f"🔍 Загружаю список всех пар с MEXC...")
            
            # Используем синхронный requests в отдельном потоке
            response = await asyncio.to_thread(requests.get, url, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"❌ MEXC: HTTP {response.status_code}")
                return []
            
            data = response.json()
            
            # Парсим ответ и собираем все USDT пары
            all_pairs = []
            for symbol_info in data.get('symbols', []):
                if symbol_info.get('quoteAsset') == 'USDT' and symbol_info.get('status') == '1':
                    base = symbol_info.get('baseAsset')
                    quote = symbol_info.get('quoteAsset')
                    if base and quote:
                        all_pairs.append(f"{base}/{quote}")
            
            logger.info(f"📊 MEXC: загружено {len(all_pairs)} USDT пар")
            return all_pairs
            
        except Exception as e:
            logger.error(f"❌ MEXC: ошибка загрузки пар - {e}")
            return []
    
    async def fetch_ohlcv(self, exchange_name: str, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """Получение свечных данных через синхронные запросы."""
        if exchange_name != 'MEXC':
            return None
        
        try:
            import requests
            # Конвертируем символ
            symbol_raw = symbol.replace('/', '')
            
            # Маппинг таймфреймов
            interval_map = {
                '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
                '1h': '1h', '4h': '4h', '1d': '1d', '1w': '1w'
            }
            interval = interval_map.get(timeframe, '15m')
            
            url = f"https://api.mexc.com/api/v3/klines?symbol={symbol_raw}&interval={interval}&limit={limit}"
            
            # Выполняем запрос в отдельном потоке
            response = await asyncio.to_thread(requests.get, url, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"❌ MEXC: HTTP {response.status_code} для {symbol} {timeframe}")
                return None
            
            data = response.json()
            
            if not data or len(data) < 20:
                logger.warning(f"⚠️ MEXC: недостаточно данных для {symbol} {timeframe}")
                return None
            
            # Преобразуем ответ в DataFrame
            rows = []
            for item in data:
                rows.append([item[0], float(item[1]), float(item[2]), float(item[3]), float(item[4]), float(item[5])])
            
            df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            logger.info(f"✅ MEXC: загружено {len(df)} свечей для {symbol} {timeframe}")
            return df
            
        except Exception as e:
            logger.error(f"Ошибка загрузки {symbol} {timeframe} с MEXC: {e}")
            return None
    
    async def fetch_funding_rate(self, exchange_name: str, symbol: str) -> Optional[float]:
        """Получение ставки фондирования."""
        return 0.0
    
    async def fetch_ticker(self, exchange_name: str, symbol: str) -> Dict:
        """Получение тикера."""
        if exchange_name != 'MEXC':
            return {}
        
        try:
            import requests
            symbol_raw = symbol.replace('/', '')
            url = f"https://api.mexc.com/api/v3/ticker/24hr?symbol={symbol_raw}"
            
            response = await asyncio.to_thread(requests.get, url, timeout=5)
            
            if response.status_code != 200:
                return {}
            
            data = response.json()
            
            return {
                'volume_24h': float(data.get('quoteVolume', 0)),
                'price_change_24h': float(data.get('priceChangePercent', 0)),
                'last': float(data.get('lastPrice', 0))
            }
        except:
            return {}
    
    async def close_all(self):
        """Закрытие соединений."""
        pass

class MultiTimeframeAnalyzer:
    """Анализатор"""
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Расчет индикаторов"""
        df['rsi'] = calculate_rsi(df['close'], 14)
        
        macd_line, signal_line, hist = calculate_macd(df['close'])
        df['MACD_12_26_9'] = macd_line
        df['MACDs_12_26_9'] = signal_line
        df['MACDh_12_26_9'] = hist
        
        df['ema_9'] = calculate_ema(df['close'], 9)
        df['ema_21'] = calculate_ema(df['close'], 21)
        df['ema_50'] = calculate_ema(df['close'], 50)
        df['ema_200'] = calculate_ema(df['close'], 200)
        
        df['sma_50'] = calculate_sma(df['close'], 50)
        df['sma_200'] = calculate_sma(df['close'], 200)
        
        sma, upper, lower = calculate_bollinger_bands(df['close'])
        df['BBL_20_2.0'] = lower
        df['BBM_20_2.0'] = sma
        df['BBU_20_2.0'] = upper
        
        df['atr'] = calculate_atr(df['high'], df['low'], df['close'])
        
        df['volume_sma'] = calculate_sma(df['volume'], 20)
        df['volume_ratio'] = df['volume'] / df['volume_sma']
        
        return df
    
    def analyze_timeframe_alignment(self, dataframes: Dict[str, pd.DataFrame]) -> Dict:
        """Анализ согласованности трендов"""
        alignment = {
            'trend_alignment': 0,
            'hourly_trend': None,
            'daily_trend': None,
            'weekly_trend': None,
            'signals': []
        }
        
        if 'hourly' in dataframes and not dataframes['hourly'].empty:
            df_h = dataframes['hourly'].iloc[-1]
            alignment['hourly_trend'] = 'BULLISH' if df_h['ema_9'] > df_h['ema_21'] else 'BEARISH'
        
        if 'daily' in dataframes and not dataframes['daily'].empty:
            df_d = dataframes['daily'].iloc[-1]
            if df_d['close'] > df_d['ema_200']:
                alignment['daily_trend'] = 'BULLISH'
                if df_d['ema_9'] > df_d['ema_21']:
                    alignment['signals'].append("Дневной тренд восходящий (выше EMA 200)")
            else:
                alignment['daily_trend'] = 'BEARISH'
                if df_d['ema_9'] < df_d['ema_21']:
                    alignment['signals'].append("Дневной тренд нисходящий (ниже EMA 200)")
        
        if 'weekly' in dataframes and not dataframes['weekly'].empty:
            df_w = dataframes['weekly'].iloc[-1]
            if df_w['close'] > df_w['ema_200']:
                alignment['weekly_trend'] = 'BULLISH'
                alignment['signals'].append("НЕДЕЛЬНЫЙ ТРЕНД ВОСХОДЯЩИЙ (сильный сигнал)")
            else:
                alignment['weekly_trend'] = 'BEARISH'
                alignment['signals'].append("НЕДЕЛЬНЫЙ ТРЕНД НИСХОДЯЩИЙ (сильный сигнал)")
        
        trends = [t for t in [alignment['hourly_trend'], alignment['daily_trend'], alignment['weekly_trend']] if t]
        if trends:
            bullish = trends.count('BULLISH')
            bearish = trends.count('BEARISH')
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
            if last['rsi'] < 30:
                reasons.append(f"RSI перепродан ({last['rsi']:.1f})")
                confidence += 10
            elif last['rsi'] > 70:
                reasons.append(f"RSI перекуплен ({last['rsi']:.1f})")
                confidence += 10
        
        # MACD
        if pd.notna(last['MACD_12_26_9']) and pd.notna(last['MACDs_12_26_9']):
            if last['MACD_12_26_9'] > last['MACDs_12_26_9'] and prev['MACD_12_26_9'] <= prev['MACDs_12_26_9']:
                reasons.append("Бычье пересечение MACD")
                confidence += 15
            elif last['MACD_12_26_9'] < last['MACDs_12_26_9'] and prev['MACD_12_26_9'] >= prev['MACDs_12_26_9']:
                reasons.append("Медвежье пересечение MACD")
                confidence += 15
        
        # EMA
        if last['ema_9'] > last['ema_21'] and prev['ema_9'] <= prev['ema_21']:
            reasons.append("Бычье пересечение EMA (9/21)")
            confidence += 15
        elif last['ema_9'] < last['ema_21'] and prev['ema_9'] >= prev['ema_21']:
            reasons.append("Медвежье пересечение EMA (9/21)")
            confidence += 15
        
        # Объем
        if last['volume_ratio'] > 1.5:
            reasons.append(f"Объем x{last['volume_ratio']:.1f} от нормы")
            confidence += 10
        
        # Сигналы от старших таймфреймов
        for signal in alignment['signals']:
            reasons.append(f"📊 {signal}")
            if "НЕДЕЛЬНЫЙ" in signal:
                confidence += 25
            elif "Дневной" in signal:
                confidence += 15
        
        # Согласованность трендов
        if alignment['trend_alignment'] > 70:
            reasons.append(f"✅ Тренды согласованы ({alignment['trend_alignment']:.0f}%)")
            confidence += 10
        
        # Фандинг
        funding = metadata.get('funding_rate')
        if funding is not None:
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
        bullish_keywords = ['перепродан', 'Бычье', 'восходящий', 'негативный фандинг']
        bearish_keywords = ['перекуплен', 'Медвежье', 'нисходящий', 'позитивный фандинг']
        
        bullish = sum(1 for r in reasons if any(k in r for k in bullish_keywords))
        bearish = sum(1 for r in reasons if any(k in r for k in bearish_keywords))
        
        if bullish > bearish and confidence >= MIN_CONFIDENCE:
            if alignment['weekly_trend'] == 'BULLISH':
                direction = 'Разворот LONG 📈'
                reasons.append("🔄 Подтверждение разворота недельным трендом")
            else:
                direction = 'LONG 📈'
        elif bearish > bullish and confidence >= MIN_CONFIDENCE:
            if alignment['weekly_trend'] == 'BEARISH':
                direction = 'Разворот SHORT 📉'
                reasons.append("🔄 Подтверждение разворота недельным трендом")
            else:
                direction = 'SHORT 📉'
        
        # Сила сигнала
        signal_strength = min(confidence + alignment['trend_alignment'] / 2, 100)
        
        # Цели
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
            'confidence': min(confidence, 100),
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
    
    def format_funding(self, rate: float) -> str:
        if rate is None:
            return "N/A"
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
    
    def format_message(self, signal: Dict) -> Tuple[str, InlineKeyboardMarkup]:
        emoji_map = {
            'LONG 📈': '🟢',
            'SHORT 📉': '🔴',
            'Разворот LONG 📈': '🟢🔄',
            'Разворот SHORT 📉': '🔴🔄',
            'NEUTRAL': '⚪'
        }
        emoji = emoji_map.get(signal['direction'], '🤖')
        
        lines = [
            f"{emoji} *СИГНАЛ {signal['exchange']}*",
            f"└ `{signal['symbol']}` {signal['signal_power']}\n",
            f"📊 *Направление:* {signal['direction']}",
            f"🎯 *Текущая цена:* `{signal['price']:.8f}`\n"
        ]
        
        if 'target_1' in signal:
            lines.extend([
                f"🎯 *Цели:*",
                f"└ Цель 1: `{signal['target_1']:.8f}`",
                f"└ Цель 2: `{signal['target_2']:.8f}`",
                f"└ Стоп-лосс: `{signal['stop_loss']:.8f}`\n"
            ])
        
        funding = self.format_funding(signal.get('funding_rate', 0))
        volume = self.format_volume(signal.get('volume_24h', 0))
        
        lines.extend([
            f"⚡ *Параметры:*",
            f"└ Объем 24ч: `{volume}`",
            f"└ Фандинг: `{funding}`\n",
            f"🔥 *Уверенность:* {signal['confidence']}%",
            f"⚡️ *Сила сигнала:* {signal['signal_strength']}/100\n"
        ])
        
        if signal.get('alignment'):
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
        
        # Клавиатура
        keyboard = []
        coin = signal['symbol'].replace('/USDT', '')
        keyboard.append([InlineKeyboardButton(f"📋 Скопировать {coin}", callback_data=f"copy_{coin}")])
        
        exch = signal['exchange']
        if exch in REF_LINKS:
            keyboard.append([InlineKeyboardButton(f"🚀 Торговать на {exch}", url=REF_LINKS[exch])])
        
        keyboard.append([
            InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh"),
            InlineKeyboardButton("📊 Детали", callback_data=f"details")
        ])
        
        return "\n".join(lines), InlineKeyboardMarkup(keyboard)
    
    async def scan_all(self) -> List[Dict]:
        """Сканирование всех бирж"""
        logger.info("="*50)
        logger.info("🚀 НАЧАЛО СКАНИРОВАНИЯ")
        logger.info("="*50)
        
        # ==== ТЕСТОВОЕ СКАНИРОВАНИЕ BTC/USDT ====
        test_pair = 'BTC/USDT'
        logger.info(f"🧪 Тестовое сканирование {test_pair} на MEXC...")
        
        # Получаем данные по всем таймфреймам
        test_dataframes = {}
        for tf_name, tf_value in TIMEFRAMES.items():
            limit = 200 if tf_name == 'current' else 100
            df = await self.fetcher.fetch_ohlcv('MEXC', test_pair, tf_value, limit)
            if df is not None and not df.empty:
                logger.info(f"✅ Загружено {len(df)} свечей для {tf_name} ({tf_value})")
                df = self.analyzer.calculate_indicators(df)
                test_dataframes[tf_name] = df
            else:
                logger.error(f"❌ Не удалось загрузить данные для {tf_name} ({tf_value})")
        
        if test_dataframes:
            # Получаем метаданные
            funding = await self.fetcher.fetch_funding_rate('MEXC', test_pair)
            ticker = await self.fetcher.fetch_ticker('MEXC', test_pair)
            
            metadata = {
                'funding_rate': funding,
                'volume_24h': ticker.get('volume_24h'),
                'price_change_24h': ticker.get('price_change_24h')
            }
            
            # Генерируем тестовый сигнал
            test_signal = self.analyzer.generate_signal(test_dataframes, metadata, test_pair, 'MEXC')
            if test_signal:
                logger.info(f"✅ ТЕСТ: Сгенерирован сигнал с уверенностью {test_signal['confidence']}%")
                if test_signal['confidence'] >= MIN_CONFIDENCE:
                    logger.info(f"🔥 ТЕСТ: Сигнал достиг порога {MIN_CONFIDENCE}%")
                    logger.info(f"📊 Направление: {test_signal['direction']}")
                    logger.info(f"📊 Причины: {test_signal['reasons']}")
                    await self.send_signal(test_signal)
                else:
                    logger.warning(f"⚠️ ТЕСТ: Уверенность {test_signal['confidence']}% ниже порога {MIN_CONFIDENCE}%")
            else:
                logger.warning("⚠️ ТЕСТ: Сигнал не сгенерирован (вернул None)")
        else:
            logger.error("❌ ТЕСТ: Нет данных для анализа")
        # ========================================
        
        all_signals = []
        
        # Сканируем только MEXC (Bybit и BingX отключены)
        try:
            pairs = await self.fetcher.fetch_all_pairs('MEXC')
            if pairs:
                logger.info(f"📊 MEXC: анализирую {min(50, len(pairs))} пар из {len(pairs)}")
                
                for i, pair in enumerate(pairs[:300]):  # Анализируем первые 50 пар
                    try:
                        # Получаем данные по всем таймфреймам
                        dataframes = {}
                        for tf_name, tf_value in TIMEFRAMES.items():
                            limit = 200 if tf_name == 'current' else 100
                            df = await self.fetcher.fetch_ohlcv('MEXC', pair, tf_value, limit)
                            if df is not None and not df.empty:
                                df = self.analyzer.calculate_indicators(df)
                                dataframes[tf_name] = df
                        
                        if not dataframes:
                            continue
                        
                        # Получаем метаданные
                        funding = await self.fetcher.fetch_funding_rate('MEXC', pair)
                        ticker = await self.fetcher.fetch_ticker('MEXC', pair)
                        
                        metadata = {
                            'funding_rate': funding,
                            'volume_24h': ticker.get('volume_24h'),
                            'price_change_24h': ticker.get('price_change_24h')
                        }
                        
                        # Генерируем сигнал
                        signal = self.analyzer.generate_signal(dataframes, metadata, pair, 'MEXC')
                        
                        if signal and signal['confidence'] >= MIN_CONFIDENCE:
                            all_signals.append(signal)
                            logger.info(f"✅ {pair} - {signal['direction']} ({signal['confidence']}%)")
                        
                        # Прогресс
                        if (i + 1) % 10 == 0:
                            logger.info(f"📊 Прогресс MEXC: {i + 1}/{min(50, len(pairs))}")
                        
                        await asyncio.sleep(0.3)  # Задержка между запросами
                        
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
    
    async def send_signal(self, signal: Dict):
        """Отправка сигнала"""
        msg, keyboard = self.format_message(signal)
        await self.telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        logger.info(f"✅ Отправлен сигнал: {signal['symbol']}")
    
    async def scheduled(self):
        """Плановый анализ"""
        logger.info(f"🕐 Запуск анализа")
        
        signals = await self.scan_all()
        
        if signals:
            for i, signal in enumerate(signals[:5]):  # Отправляем только 5 лучших
                await self.send_signal(signal)
                if i < len(signals[:5]) - 1:  # Если это не последний сигнал
                    await asyncio.sleep(3)  # Увеличиваем паузу до 3 секунд
        else:
            logger.info("❌ Сигналов не найдено")
    
    async def run(self):
        """Запуск"""
        logger.info("🤖 Бот запущен")
        
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
    """Обработчик команд"""
    
    def __init__(self, bot: FuturesScannerBot):
        self.bot = bot
        self.app = Application.builder().token(TELEGRAM_TOKEN).build()
        self.register()
    
    def register(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("scan", self.scan))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("help", self.help))
        self.app.add_handler(CallbackQueryHandler(self.button))
    
    async def start(self, update, context):
        await update.message.reply_text(
            "🤖 *Фьючерсный сканер*\n\n"
            "📊 Анализирую MEXC, Bybit, BingX\n"
            "📈 Мультитаймфрейм (15m/1h/1d/1w)\n"
            "🎯 Цели и стоп-лосс\n\n"
            "Команды:\n"
            "/scan - Сканировать\n"
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
                await self.bot.send_signal(signal)
                await asyncio.sleep(2)
        else:
            await msg.edit_text("❌ Сигналов не найдено")
    
    async def status(self, update, context):
        text = "*📡 Статус:*\n\n"
        for name in self.bot.fetcher.exchanges.keys():
            status = "✅" if await self.bot.fetcher.test_connection(name) else "❌"
            text += f"{status} {name}\n"
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def help(self, update, context):
        await update.message.reply_text(
            "*Помощь*\n\n"
            "Бот анализирует фьючерсные пары\n"
            "При проблемах проверьте интернет",
            parse_mode='Markdown'
        )
    
    async def button(self, update, context):
        query = update.callback_query
        await query.answer()
        if query.data.startswith("copy_"):
            coin = query.data.replace("copy_", "")
            await query.message.reply_text(f"`{coin}`", parse_mode='Markdown')
    
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