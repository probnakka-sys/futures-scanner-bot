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
# Telegram
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import RetryAfter, TimedOut
import time
import json
import aiohttp
import random
from io import BytesIO
# Для продвинутых структур данных
import heapq
from collections import deque

# Графики
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

# Импорт конфигурации
from config import (
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    PUMP_CHAT_ID,
    STATS_CHAT_ID,
    ACCUMULATION_CHAT_ID,
    UPDATE_INTERVAL,
    PUMP_SCAN_INTERVAL,
    MIN_CONFIDENCE,
    TIMEFRAMES,
    REF_LINKS,
    FEATURES,
    INDICATOR_SETTINGS,
    INDICATOR_WEIGHTS,
    PUMP_DUMP_SETTINGS,
    PUMP_SCAN_SETTINGS,
    IMBALANCE_SETTINGS,
    LIQUIDITY_SETTINGS,
    SMC_SETTINGS,
    FRACTAL_SETTINGS,
    PAIRS_TO_SCAN,
    DISPLAY_SETTINGS,
    FIBONACCI_SETTINGS,
    VOLUME_PROFILE_SETTINGS,
    STATS_SETTINGS,
    ACCUMULATION_SETTINGS,
    ATR_SETTINGS,
    PERFORMANCE_SETTINGS,
    VOLUME_ANALYSIS_SETTINGS,
    DISPERSION_ANALYSIS_SETTINGS,
    LEVEL_ANALYSIS_SETTINGS,      
    LEVEL_STRENGTH_SETTINGS,
    SNIPER_ENTRY_SETTINGS,
    SCAN_MODE,
    SMART_REPEAT_SETTINGS,
    PUMP_DUMP_FILTER,
    WEBSOCKET_ANALYSIS_SETTINGS,
    FAKEOUT_SETTINGS,
    TIMEFRAME_WEIGHTS,
    EMA_TOUCH_SETTINGS,
    BREAKOUT_CONFIRMATION_SETTINGS,
    MINOR_TF_SETTINGS,      
    EXCHANGES,              
    PROXY_SETTINGS,
    FIBONACCI_ADVANCED_SETTINGS,      # ← ДОБАВИТЬ
    SIGNAL_FORMAT_SETTINGS,            # ← ДОБАВИТЬ
    FIB_HISTORY_SETTINGS,              # ← ДОБАВИТЬ
)

# from config import BREAKOUT_CONFIRMATION_SETTINGS

# Импорт системы статистики
from signal_stats import SignalStatistics

# Импорт настроек объемов и дисперсии
from config import VOLUME_ANALYSIS_SETTINGS, DISPERSION_ANALYSIS_SETTINGS

# Импорт снайперские точки входа
from config import SNIPER_ENTRY_SETTINGS

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==============

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

# ============== КЭШИРОВАНИЕ ДАННЫХ ==============

class CacheManager:
    """Кэширование данных для уменьшения количества запросов к бирже"""
    
    def __init__(self, ttl=60):
        self.cache = {}
        self.ttl = ttl
        logger.info(f"✅ CacheManager инициализирован (TTL: {ttl} сек)")
    
    def get(self, key: str) -> Optional[any]:
        """Получение данных из кэша"""
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return data
            else:
                del self.cache[key]
        return None
    
    def set(self, key: str, data: any):
        """Сохранение данных в кэш"""
        self.cache[key] = (data, time.time())
    
    def clear(self):
        """Очистка кэша"""
        self.cache.clear()
        logger.info("🧹 Кэш очищен")

# ============== ИСТОРИЯ ПОДХОДОВ К УРОВНЯМ ФИБОНАЧЧИ ==============

class FibHistoryTracker:
    """
    Хранит историю подходов к уровням Фибоначчи на разных таймфреймах
    """
    
    def __init__(self):
        self.history = {}  # key: f"{symbol}_{tf}_{level}"
        self.ttl = FIB_HISTORY_SETTINGS.get('ttl_seconds', 2592000)
        self.max_approaches = FIB_HISTORY_SETTINGS.get('max_approaches', 10)
        logger.info("✅ FibHistoryTracker инициализирован")
    
    def _make_key(self, symbol: str, tf: str, level: float) -> str:
        """Создание ключа для истории"""
        return f"{symbol}_{tf}_{level:.3f}"
    
    def add_approach(self, symbol: str, tf: str, level: float, price: float):
        """
        Добавление подхода к уровню
        """
        key = self._make_key(symbol, tf, level)
        now = datetime.now().timestamp()
        
        if key not in self.history:
            self.history[key] = {
                'symbol': symbol,
                'tf': tf,
                'level': level,
                'price': price,
                'approaches': 1,
                'first_approach': now,
                'last_approach': now,
                'is_broken': False,
                'broken_at': None
            }
            logger.debug(f"📊 Фибо {tf} {level:.3f} для {symbol}: первый подход")
        else:
            # Проверяем, не устарел ли уровень
            if now - self.history[key]['last_approach'] > self.ttl:
                # Устарел — сбрасываем
                self.history[key]['approaches'] = 1
                self.history[key]['first_approach'] = now
                logger.debug(f"📊 Фибо {tf} {level:.3f} для {symbol}: сброс (устарел)")
            else:
                self.history[key]['approaches'] = min(self.history[key]['approaches'] + 1, self.max_approaches)
                logger.debug(f"📊 Фибо {tf} {level:.3f} для {symbol}: подход #{self.history[key]['approaches']}")
            
            self.history[key]['last_approach'] = now
            self.history[key]['price'] = price
    
    def get_approach_count(self, symbol: str, tf: str, level: float) -> int:
        """
        Получение количества подходов к уровню
        """
        key = self._make_key(symbol, tf, level)
        if key not in self.history:
            return 0
        
        # Проверяем на устаревание
        now = datetime.now().timestamp()
        if now - self.history[key]['last_approach'] > self.ttl:
            del self.history[key]
            return 0
        
        return self.history[key]['approaches']
    
    def mark_broken(self, symbol: str, tf: str, level: float, price: float):
        """
        Отметить уровень как пробитый
        """
        key = self._make_key(symbol, tf, level)
        if key in self.history:
            self.history[key]['is_broken'] = True
            self.history[key]['broken_at'] = datetime.now().timestamp()
            self.history[key]['broken_price'] = price
            logger.debug(f"📊 Фибо {tf} {level:.3f} для {symbol}: ПРОБИТ")
    
    def get_strength_text(self, approaches: int) -> str:
        """
        Получить текстовое описание силы уровня по количеству подходов
        """
        if approaches == 0:
            return ""
        elif approaches == 1:
            return "1-й подход (экстремально сильный)"
        elif approaches == 2:
            return "2-й подход (очень сильный)"
        elif approaches == 3:
            return "3-й подход (сильный)"
        else:
            return f"{approaches}-й подход (ослаблен)"
    
    def cleanup(self):
        """Очистка устаревших записей"""
        now = datetime.now().timestamp()
        to_delete = []
        for key, data in self.history.items():
            if now - data['last_approach'] > self.ttl:
                to_delete.append(key)
        for key in to_delete:
            del self.history[key]
        if to_delete:
            logger.info(f"🧹 FibHistoryTracker: удалено {len(to_delete)} устаревших записей")

# ============== АНАЛИЗАТОР НАКОПЛЕНИЯ ==============

class AccumulationAnalyzer:
    """
    Анализатор фаз накопления/распределения
    Находит моменты, когда крупные игроки собирают позицию перед импульсом
    """
    
    def __init__(self, settings: Dict = None):
        self.settings = settings or ACCUMULATION_SETTINGS
        self.ad_threshold = self.settings.get('ad_threshold', 2.0)
        self.volume_spike_threshold = self.settings.get('volume_spike_threshold', 2.0)
        self.range_width_threshold = self.settings.get('range_width_threshold', 5.0)
        self.min_signals = self.settings.get('min_signals', 2)
        self.lookback = self.settings.get('lookback_period', 50)
    
    def calculate_ad_line(self, df: pd.DataFrame) -> pd.Series:
        """Расчет линии накопления/распределения (A/D)"""
        high_low = df['high'] - df['low']
        high_low = high_low.replace(0, 0.001)
        
        money_flow_multiplier = ((df['close'] - df['low']) - (df['high'] - df['close'])) / high_low
        money_flow_volume = money_flow_multiplier * df['volume']
        return money_flow_volume.cumsum()
    
    def detect_ad_divergence(self, df: pd.DataFrame) -> Dict:
        """Поиск дивергенции между ценой и A/D линией"""
        df = df.copy()
        df['ad_line'] = self.calculate_ad_line(df)
        
        recent = df.tail(20)
        
        price_lows = recent['close'].rolling(5).min()
        ad_lows = recent['ad_line'].rolling(5).min()
        
        price_trend = price_lows.is_monotonic_decreasing
        ad_trend = ad_lows.is_monotonic_increasing
        
        if price_trend and ad_trend:
            strength = min(80, abs(price_lows.iloc[-1] - price_lows.iloc[0]) / price_lows.iloc[0] * 500)
            return {
                'accumulation': True,
                'strength': strength,
                'description': f"📈 Дивергенция: цена падает, A/D растет (сила {strength:.0f}%)"
            }
        
        price_highs = recent['close'].rolling(5).max()
        ad_highs = recent['ad_line'].rolling(5).max()
        
        price_trend_up = price_highs.is_monotonic_increasing
        ad_trend_down = ad_highs.is_monotonic_decreasing
        
        if price_trend_up and ad_trend_down:
            strength = min(80, abs(price_highs.iloc[-1] - price_highs.iloc[0]) / price_highs.iloc[0] * 500)
            return {
                'accumulation': True,
                'distribution': True,
                'strength': strength,
                'description': f"📉 Распределение: цена растет, A/D падает (сила {strength:.0f}%)"
            }
        
        return {'accumulation': False}
    
    def detect_volume_spikes_in_range(self, df: pd.DataFrame) -> Dict:
        """Поиск всплесков объема внутри консолидации"""
        recent = df.tail(self.lookback)
        
        range_high = recent['high'].max()
        range_low = recent['low'].min()
        current_price = df['close'].iloc[-1]
        
        range_width = (range_high - range_low) / range_low * 100
        
        if current_price <= range_high and current_price >= range_low and range_width < self.range_width_threshold:
            avg_volume = recent['volume'].mean()
            last_volume = recent['volume'].iloc[-5:].mean()
            volume_ratio = last_volume / avg_volume if avg_volume > 0 else 1
            
            if volume_ratio > self.volume_spike_threshold:
                strength = min(90, volume_ratio * 30)
                return {
                    'accumulation': True,
                    'strength': strength,
                    'description': f"📊 Аномальный объем x{volume_ratio:.1f} в консолидации (сила {strength:.0f}%)"
                }
        
        return {'accumulation': False}
    
    def detect_silent_accumulation(self, df: pd.DataFrame) -> Dict:
        """Поиск тихой аккумуляции"""
        recent = df.tail(30)
        
        price_range = (recent['high'].max() - recent['low'].min()) / recent['close'].mean() * 100
        
        if price_range < 3:
            volume_sma_5 = recent['volume'].tail(5).mean()
            volume_sma_20 = recent['volume'].mean()
            volume_ratio = volume_sma_5 / volume_sma_20 if volume_sma_20 > 0 else 1
            
            lows_increasing = recent['low'].tail(10).is_monotonic_increasing
            
            signals = 0
            reasons = []
            
            if volume_ratio > 1.3:
                signals += 1
                reasons.append(f"объем +{(volume_ratio-1)*100:.0f}%")
            
            if lows_increasing:
                signals += 1
                reasons.append("минимумы растут")
            
            if signals >= 1:
                strength = signals * 35
                return {
                    'accumulation': True,
                    'strength': strength,
                    'description': f"📦 Тихая аккумуляция: {', '.join(reasons)} (сила {strength:.0f}%)"
                }
        
        return {'accumulation': False}
    
    def calculate_potential(self, df: pd.DataFrame, dataframes: Dict[str, pd.DataFrame]) -> Dict:
        """
        Расчет потенциала роста до ближайшей сильной зоны на старших ТФ
        """
        current_price = df['close'].iloc[-1]
        potential = {
            'has_potential': False,
            'target_price': None,
            'target_pct': 0,
            'target_level': '',
            'timeframe': '',
            'reasons': []
        }
        
        # Анализируем старшие таймфреймы
        target_tfs = ['hourly', 'daily', 'weekly', 'monthly']
        
        for tf_name in target_tfs:
            if tf_name not in dataframes or dataframes[tf_name] is None:
                continue
            
            tf_df = dataframes[tf_name]
            
            # Ищем ближайшие сильные уровни
            levels = self._find_strong_levels(tf_df)
            
            for level in levels:
                level_price = level['price']
                level_type = level['type']
                
                # Для LONG ищем уровень сопротивления выше цены
                if level_price > current_price:
                    distance = ((level_price - current_price) / current_price) * 100
                    
                    # Если расстояние разумное (не больше 50%)
                    if distance < 50:
                        if not potential['target_price'] or level_price < potential['target_price']:
                            potential['has_potential'] = True
                            potential['target_price'] = level_price
                            potential['target_pct'] = round(distance, 2)
                            potential['target_level'] = f"{level_type} на {tf_name}"
                            potential['timeframe'] = tf_name
                            potential['reasons'].append(
                                f"🎯 До {level_type} на {tf_name}: +{distance:.2f}%"
                            )
                
                # Для SHORT ищем уровень поддержки ниже цены
                elif level_price < current_price:
                    distance = ((current_price - level_price) / current_price) * 100
                    
                    if distance < 50:
                        if not potential['target_price'] or level_price > potential['target_price']:
                            potential['has_potential'] = True
                            potential['target_price'] = level_price
                            potential['target_pct'] = round(distance, 2)
                            potential['target_level'] = f"{level_type} на {tf_name}"
                            potential['timeframe'] = tf_name
                            potential['reasons'].append(
                                f"🎯 До {level_type} на {tf_name}: -{distance:.2f}%"
                            )
        
        return potential
    
    def _find_strong_levels(self, df: pd.DataFrame) -> List[Dict]:
        """Поиск сильных уровней на таймфрейме"""
        levels = []
        
        # EMA уровни
        if 'ema_50' in df.columns:
            levels.append({
                'price': df['ema_50'].iloc[-1],
                'type': 'EMA 50',
                'strength': 70
            })
        if 'ema_200' in df.columns:
            levels.append({
                'price': df['ema_200'].iloc[-1],
                'type': 'EMA 200',
                'strength': 90
            })
        
        # Локальные экстремумы
        recent = df.tail(50)
        swing_high = recent['high'].max()
        swing_low = recent['low'].min()
        
        levels.append({
            'price': swing_high,
            'type': 'Локальный максимум',
            'strength': 60
        })
        levels.append({
            'price': swing_low,
            'type': 'Локальный минимум',
            'strength': 60
        })
        
        return levels
    
    def analyze(self, df: pd.DataFrame) -> Dict:
        """Полный анализ накопления"""
        result = {
            'has_accumulation': False,
            'signals': [],
            'strength': 0,
            'direction': None
        }
        
        ad_div = self.detect_ad_divergence(df)
        if ad_div.get('accumulation'):
            result['has_accumulation'] = True
            result['signals'].append(ad_div['description'])
            result['strength'] = max(result['strength'], ad_div.get('strength', 0))
            if ad_div.get('distribution'):
                result['direction'] = 'SHORT'
            else:
                result['direction'] = 'LONG'
        
        volume_spike = self.detect_volume_spikes_in_range(df)
        if volume_spike.get('accumulation'):
            result['has_accumulation'] = True
            result['signals'].append(volume_spike['description'])
            result['strength'] = max(result['strength'], volume_spike.get('strength', 0))
        
        silent = self.detect_silent_accumulation(df)
        if silent.get('accumulation'):
            result['has_accumulation'] = True
            result['signals'].append(silent['description'])
            result['strength'] = max(result['strength'], silent.get('strength', 0))
        
        if result['has_accumulation'] and not result['direction']:
            if 'vwap' in df.columns:
                if df['close'].iloc[-1] > df['vwap'].iloc[-1]:
                    result['direction'] = 'LONG'
                else:
                    result['direction'] = 'SHORT'
        
        return result

# ============== АНАЛИЗАТОР ТРЕНДОВЫХ ЛИНИЙ ==============

class TrendLineAnalyzer:
    """Анализ наклонных уровней поддержки/сопротивления"""
    
    def find_trend_lines(self, df: pd.DataFrame, touch_count: int = 3) -> List[Dict]:
        """
        Поиск наклонных уровней с несколькими касаниями
        """
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        
        trend_lines = []
        
        # Поиск нисходящей линии сопротивления (соединяем максимумы)
        for i in range(len(highs) - 20, len(highs) - 5):
            for j in range(i + 5, len(highs)):
                # Пробуем провести линию через две точки
                x1, y1 = i, highs[i]
                x2, y2 = j, highs[j]
                
                # Наклон должен быть отрицательным (нисходящий тренд)
                slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0
                if slope >= 0:
                    continue
                
                # Считаем касания
                touches = 0
                touch_points = []
                
                for k in range(j, len(highs)):
                    # Расчет ожидаемого значения на линии
                    expected = y1 + slope * (k - i)
                    # Проверяем, касается ли свеча линии
                    if abs(highs[k] - expected) / expected < 0.003:  # допуск 0.3%
                        touches += 1
                        touch_points.append(k)
                
                if touches >= touch_count:
                    # Проверяем, пробита ли линия сейчас
                    last_price = closes[-1]
                    last_expected = y1 + slope * (len(highs)-1 - i)
                    is_broken = last_price > last_expected * 1.01  # пробой на 1%
                    
                    trend_lines.append({
                        'type': 'resistance',
                        'slope': slope,
                        'touches': touches,
                        'touch_points': touch_points,
                        'current_level': last_expected,
                        'is_broken': is_broken,
                        'strength': min(100, touches * 25)  # сила от количества касаний
                    })
        
        # Поиск восходящей линии поддержки (соединяем минимумы)
        for i in range(len(lows) - 20, len(lows) - 5):
            for j in range(i + 5, len(lows)):
                # Пробуем провести линию через две точки
                x1, y1 = i, lows[i]
                x2, y2 = j, lows[j]
                
                # Наклон должен быть положительным (восходящий тренд)
                slope = (y2 - y1) / (x2 - x1) if x2 != x1 else 0
                if slope <= 0:
                    continue
                
                # Считаем касания
                touches = 0
                touch_points = []
                
                for k in range(j, len(lows)):
                    # Расчет ожидаемого значения на линии
                    expected = y1 + slope * (k - i)
                    # Проверяем, касается ли свеча линии
                    if abs(lows[k] - expected) / expected < 0.003:  # допуск 0.3%
                        touches += 1
                        touch_points.append(k)
                
                if touches >= touch_count:
                    # Проверяем, пробита ли линия сейчас
                    last_price = closes[-1]
                    last_expected = y1 + slope * (len(lows)-1 - i)
                    is_broken = last_price < last_expected * 0.99  # пробой на 1% вниз
                    
                    trend_lines.append({
                        'type': 'support',
                        'slope': slope,
                        'touches': touches,
                        'touch_points': touch_points,
                        'current_level': last_expected,
                        'is_broken': is_broken,
                        'strength': min(100, touches * 25)
                    })
        
        return trend_lines[-5:]  # последние 5 линий

        # Пробой наклонного уровня
    def check_approaching_trendline(self, df: pd.DataFrame, current_price: float, touch_count: int = 3, threshold: float = 0.5) -> List[Dict]:
        """
        Проверка приближения цены к трендовой линии (до пробоя)
        threshold: процент от цены, при котором считаем "приближение" (0.5% по умолчанию)
        """
        warnings = []
        
        # Находим все трендовые линии
        trend_lines = self.find_trend_lines(df, touch_count)
        
        for line in trend_lines:
            if line['is_broken']:
                continue  # уже пробита - не интересно
                
            current_level = line['current_level']
            
            # Для линии сопротивления (цена под линией)
            if line['type'] == 'resistance' and current_price < current_level:
                distance_to_line = ((current_level - current_price) / current_price) * 100
                
                # Если цена приблизилась к линии на threshold%
                if distance_to_line <= threshold:
                    warnings.append({
                        'type': 'resistance',
                        'level': current_level,
                        'distance': distance_to_line,
                        'touches': line['touches'],
                        'message': f"⚠️ Цена приближается к наклонному сопротивлению ({distance_to_line:.1f}% до пробоя)"
                    })
            
            # Для линии поддержки (цена над линией)
            elif line['type'] == 'support' and current_price > current_level:
                distance_to_line = ((current_price - current_level) / current_price) * 100
                
                if distance_to_line <= threshold:
                    warnings.append({
                        'type': 'support',
                        'level': current_level,
                        'distance': distance_to_line,
                        'touches': line['touches'],
                        'message': f"⚠️ Цена приближается к наклонной поддержке ({distance_to_line:.1f}% до пробоя)"
                    })
        
        return warnings    

# ============== ОТСЛЕЖИВАНИЕ ПРОБОЕВ ==============

class BreakoutTracker:
    """Отслеживание пробоев с комбинированным подтверждением"""
    
    def __init__(self):
        self.potential_breakouts = {}  # отслеживаем потенциальные пробои
        self.confirmed_breakouts = set()
    
    def check_breakout_confirmation(self, symbol: str, tf: str, df: pd.DataFrame, line: Dict, current_price: float, 
                                   required_candles: int = 3, required_percent: float = 0.5,
                                   volume_confirmation: float = 2.0, confirmation_mode: str = 'any_two') -> Optional[Dict]:
        """
        Комбинированная проверка закрепления пробоя
        - required_candles: сколько свечей нужно
        - required_percent: на сколько процентов нужно закрепиться
        - volume_confirmation: какой объем нужен для подтверждения (x от среднего)
        - confirmation_mode: 'any_two', 'all', 'any_one'
        """
        key = f"{symbol}_{tf}_{id(line)}"
        
        # Определяем направление пробоя
        if line['type'] == 'resistance':
            is_broken = current_price > line['current_level']
            confirmation_price = line['current_level'] * (1 + required_percent/100)
            direction = "вверх"
        else:
            is_broken = current_price < line['current_level']
            confirmation_price = line['current_level'] * (1 - required_percent/100)
            direction = "вниз"
        
        # Если пробоя нет
        if not is_broken:
            if key in self.potential_breakouts:
                del self.potential_breakouts[key]
            return None
        
        # Есть пробой
        if key not in self.potential_breakouts:
            # Первый раз видим пробой
            avg_volume = df['volume'].rolling(20).mean().iloc[-1] if len(df) > 20 else df['volume'].mean()
            current_volume = df['volume'].iloc[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
            
            self.potential_breakouts[key] = {
                'line': line,
                'first_cross_time': datetime.now(),
                'first_cross_price': current_price,
                'direction': direction,
                'confirmations': {
                    'candles': 1,
                    'price': current_price >= confirmation_price if direction == 'вверх' else current_price <= confirmation_price,
                    'volume': volume_ratio >= volume_confirmation
                },
                'max_price': current_price,
                'min_price': current_price,
                'tf': tf,
                'volume_ratio': volume_ratio
            }
            return None
        
        # Уже отслеживаем этот пробой
        tracker = self.potential_breakouts[key]
        
        # Обновляем максимум/минимум
        tracker['max_price'] = max(tracker['max_price'], current_price)
        tracker['min_price'] = min(tracker['min_price'], current_price)
        
        # Обновляем подтверждения
        tracker['confirmations']['candles'] += 1
        
        if direction == 'вверх':
            tracker['confirmations']['price'] = current_price >= confirmation_price
        else:
            tracker['confirmations']['price'] = current_price <= confirmation_price
        
        # Считаем количество выполненных условий
        conditions_met = 0
        if tracker['confirmations']['candles'] >= required_candles:
            conditions_met += 1
        if tracker['confirmations']['price']:
            conditions_met += 1
        if tracker['confirmations']['volume']:
            conditions_met += 1
        
        # Проверяем по выбранному режиму
        should_confirm = False
        if confirmation_mode == 'all':
            should_confirm = conditions_met == 3
        elif confirmation_mode == 'any_two':
            should_confirm = conditions_met >= 2
        elif confirmation_mode == 'any_one':
            should_confirm = conditions_met >= 1
        
        if should_confirm:
            # Пробой подтвержден!
            self.confirmed_breakouts.add(key)
            
            # Рассчитываем размер движения
            if direction == 'вверх':
                move_percent = ((tracker['max_price'] - line['current_level']) / line['current_level']) * 100
            else:
                move_percent = ((line['current_level'] - tracker['min_price']) / line['current_level']) * 100
            
            result = {
                'line': line,
                'tf': tf,
                'direction': direction,
                'touches': line['touches'],
                'breakout_price': tracker['first_cross_price'],
                'current_price': current_price,
                'move_percent': move_percent,
                'confirmations': tracker['confirmations'],
                'message': (f"✅ ПРОБОЙ {direction} на {tf} ПОДТВЕРЖДЕН! "
                           f"(свечей: {tracker['confirmations']['candles']}/{required_candles}, "
                           f"закрепление: {'✅' if tracker['confirmations']['price'] else '❌'}, "
                           f"объем: x{tracker['volume_ratio']:.1f})")
            }
            
            del self.potential_breakouts[key]
            return result
        
        return None

# ============== ДЕТЕКТОР ЛОЖНЫХ ПРОБОЕВ  ==============

class FakeoutDetector:
    """Детектор ложных пробоев (fakeouts)"""
    
    def __init__(self):
        self.potential_fakeouts = {}  # отслеживаем подозрительные пробои
        self.confirmed_fakeouts = set()  # подтвержденные ложные пробои
    
    def check_fakeout(self, symbol: str, tf: str, df: pd.DataFrame, line: Dict, current_price: float,
                     breakout_distance: float = None,  # теперь берется из конфига
                     retrace_threshold: float = None,
                     confirmation_candles: int = None) -> Optional[Dict]:
        """
        Проверка на ложный пробой с настройками из конфига
        """
        from config import FAKEOUT_SETTINGS
        
        # Берем настройки из конфига, если не переданы явно
        if breakout_distance is None:
            breakout_distance = FAKEOUT_SETTINGS.get('breakout_distance', 2.0)
        if retrace_threshold is None:
            retrace_threshold = FAKEOUT_SETTINGS.get('retrace_threshold', 70) / 100
        if confirmation_candles is None:
            confirmation_candles = FAKEOUT_SETTINGS.get('confirmation_candles', 2)
        
        key = f"{symbol}_{tf}_{id(line)}"
        
        # Определяем тип линии
        if line['type'] == 'resistance':
            is_breakout = current_price > line['current_level']
            breakout_direction = "вверх"
        else:
            is_breakout = current_price < line['current_level']
            breakout_direction = "вниз"
        
        # Если пробоя нет - ничего не делаем
        if not is_breakout:
            if key in self.potential_fakeouts:
                del self.potential_fakeouts[key]
            return None
        
        # Есть пробой
        if key not in self.potential_fakeouts:
            breakout_price = current_price
            breakout_size = abs((current_price - line['current_level']) / line['current_level'] * 100)
            
            # Проверяем, достаточно ли большой пробой
            if breakout_size >= breakout_distance:
                self.potential_fakeouts[key] = {
                    'line': line,
                    'breakout_price': breakout_price,
                    'breakout_size': breakout_size,
                    'breakout_time': datetime.now(),
                    'max_price': current_price,
                    'min_price': current_price,
                    'direction': breakout_direction,
                    'candles_after': 0,
                    'tf': tf
                }
                logger.info(f"  🔍 Отслеживаю потенциальный пробой {breakout_direction} на {tf} ({breakout_size:.1f}%)")
            return None
        
        # Уже отслеживаем этот пробой
        tracker = self.potential_fakeouts[key]
        tracker['candles_after'] += 1
        
        # Обновляем максимум/минимум
        if current_price > tracker['max_price']:
            tracker['max_price'] = current_price
        if current_price < tracker['min_price']:
            tracker['min_price'] = current_price
        
        # Ждем нужное количество свечей для подтверждения
        if tracker['candles_after'] < confirmation_candles:
            return None
        
        # Анализируем, был ли это ложный пробой
        if line['type'] == 'resistance':
            max_price = tracker['max_price']
            current_retrace = ((max_price - current_price) / (max_price - line['current_level'])) * 100
            
            if current_retrace >= retrace_threshold * 100:
                fakeout = {
                    'type': 'fakeout',
                    'direction': 'вверх',
                    'line': line,
                    'breakout_price': tracker['breakout_price'],
                    'max_price': max_price,
                    'current_price': current_price,
                    'breakout_size': tracker['breakout_size'],
                    'retrace_percent': current_retrace,
                    'touches': line['touches'],
                    'tf': tf,
                    'message': (f"🚨 ЛОЖНЫЙ ПРОБОЙ {breakout_direction} на {tf}! "
                               f"Цена вернулась на {current_retrace:.0f}% от пробоя")
                }
                
                self.confirmed_fakeouts.add(key)
                del self.potential_fakeouts[key]
                return fakeout
        else:
            min_price = tracker['min_price']
            current_retrace = ((current_price - min_price) / (line['current_level'] - min_price)) * 100
            
            if current_retrace >= retrace_threshold * 100:
                fakeout = {
                    'type': 'fakeout',
                    'direction': 'вниз',
                    'line': line,
                    'breakout_price': tracker['breakout_price'],
                    'min_price': min_price,
                    'current_price': current_price,
                    'breakout_size': tracker['breakout_size'],
                    'retrace_percent': current_retrace,
                    'touches': line['touches'],
                    'tf': tf,
                    'message': (f"🚨 ЛОЖНЫЙ ПРОБОЙ {breakout_direction} на {tf}! "
                               f"Цена вернулась на {current_retrace:.0f}% от пробоя")
                }
                
                self.confirmed_fakeouts.add(key)
                del self.potential_fakeouts[key]
                return fakeout
        
        return None

# ============== ГЕНЕРАТОР ГРАФИКОВ ==============

class ChartGenerator:
    """Генератор графиков для сигналов"""
    
    def __init__(self):
        self.figsize = (12, 6)
        self.dpi = 100
        self.style = 'dark_background'
        
    def create_chart(self, df: pd.DataFrame, signal: Dict, coin: str, timeframe: str = '15m') -> BytesIO:
        """Создание графика с ценой, индикаторами и целями"""

        import matplotlib.font_manager as fm
        # Подавляем предупреждения о шрифтах
        import warnings
        warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib')

        plt.style.use(self.style)
        
        plot_df = df.tail(100).copy()
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=self.figsize, 
                                    gridspec_kw={'height_ratios': [3, 1]})
        
        # ===== ВЕРХНИЙ ГРАФИК =====
        ax1.plot(plot_df.index, plot_df['close'], 
                color='white', linewidth=2, label='Цена')
        
        # EMA линии - ВСЕ ДОЛЖНЫ БЫТЬ В ЛЕГЕНДЕ
        if 'ema_9' in plot_df.columns:
            ax1.plot(plot_df.index, plot_df['ema_9'], 
                    color='#00ff88', linewidth=1.5, alpha=0.7, label='EMA 9')
        if 'ema_21' in plot_df.columns:
            ax1.plot(plot_df.index, plot_df['ema_21'], 
                    color='#ff8800', linewidth=1.5, alpha=0.7, label='EMA 21')
        if 'ema_50' in plot_df.columns:
            ax1.plot(plot_df.index, plot_df['ema_50'], 
                    color='#8888ff', linewidth=1, alpha=0.5, label='EMA 50')
        if 'ema_200' in plot_df.columns:
            ax1.plot(plot_df.index, plot_df['ema_200'], 
                    color='#ff4444', linewidth=1, alpha=0.5, label='EMA 200')
        
        # Bollinger Bands - ДОЛЖНЫ БЫТЬ В ЛЕГЕНДЕ
        if 'BBL_20_2.0' in plot_df.columns and 'BBU_20_2.0' in plot_df.columns:
            ax1.fill_between(plot_df.index, 
                            plot_df['BBL_20_2.0'], 
                            plot_df['BBU_20_2.0'],
                            alpha=0.2, color='gray', label='Bollinger Bands')
        
        # Текущая цена
        current_price = signal['price']
        ax1.axhline(y=current_price, color='#ffff00', # ← ЖЕЛТЫЙ
                linestyle='--', linewidth=2.0, alpha=0.9, # потолще и ярче
                label=f'Текущая: {current_price:.4f}')
        
        # Цели
        if signal.get('target_1'):
            ax1.axhline(y=signal['target_1'], color='#ffaa00', 
                    linestyle='--', linewidth=1.5, alpha=0.8,
                    label=f'Цель 1: {signal["target_1"]}')
        if signal.get('target_2'):
            ax1.axhline(y=signal['target_2'], color='#00ff00', # ← ЗЕЛЕНЫЙ 
                    linestyle='--', linewidth=1.5, alpha=0.8,
                    label=f'Цель 2: {signal["target_2"]}')
        if signal.get('stop_loss'):
            ax1.axhline(y=signal['stop_loss'], color='#ff0000', 
                    linestyle='--', linewidth=1.5, alpha=0.8,
                    label=f'Стоп: {signal["stop_loss"]}')
        
        # ===== FVG ЗОНЫ - ТОЛЬКО 2 БЛИЖАЙШИЕ =====
        if 'fvg_zones' in signal and signal['fvg_zones']:
            # Берем ТОЛЬКО 2 ближайшие зоны
            fvg_to_show = signal['fvg_zones'][:2]
            logger.info(f"  🎨 Рисую {len(fvg_to_show)} FVG зон на графике")
            
            for zone in fvg_to_show:
                # Определяем цвет в зависимости от типа
                color = '#00ff00' if zone['type'] == 'bullish' else '#ff0000'
                alpha = 0.2
                
                # Рисуем зону
                ax1.axhspan(zone['min'], zone['max'], 
                        alpha=alpha, color=color, linewidth=0)
                
                # Добавляем границы зоны
                ax1.axhline(y=zone['min'], color=color, linestyle=':', 
                        linewidth=1, alpha=0.5)
                ax1.axhline(y=zone['max'], color=color, linestyle=':', 
                        linewidth=1, alpha=0.5)
                
                # Добавляем метку с таймфреймом и размером
                mid_price = (zone['min'] + zone['max']) / 2
                label = f"FVG {zone.get('tf_short', '?')} ({zone.get('size', 0):.1f}%)"
                ax1.text(plot_df.index[-1], mid_price, label, 
                        color='white',  # ← только это поменяли с color=color на color='white'
                        fontsize=8,     # ← оставляем как было
                        alpha=0.8,      # ← оставляем как было
                        verticalalignment='center',
                        horizontalalignment='right',
                        bbox=dict(boxstyle="round,pad=0.2", 
                                facecolor='black', 
                                alpha=0.5))  # ← оставляем как было

        # Добавляем отрисовку зон дисперсии
        if 'dispersion_zones' in signal and signal['dispersion_zones']:
            for zone in signal['dispersion_zones'][:3]:
                ax1.axhspan(zone['min'], zone['max'], 
                        alpha=0.2, color='orange', linewidth=0)
                
                # Добавляем метку
                mid_price = (zone['min'] + zone['max']) / 2
                ax1.text(plot_df.index[-1], mid_price, 
                        f"📊 ДИСПЕРСИЯ {zone['strength']:.0f}%", 
                        color='orange', fontsize=7, alpha=0.7,
                        verticalalignment='center',
                        horizontalalignment='right')
        
        # Заголовок
        ax1.set_title(f'{coin} - {signal["direction"]} (TF: {timeframe}, уверенность {signal["confidence"]}%)', 
                    fontsize=14, fontweight='bold', color='white')
        ax1.set_ylabel('Price (USDT)', color='white')
        ax1.legend(loc='upper left', fontsize=8, facecolor='#222222')
        ax1.grid(True, alpha=0.2, linestyle='--')
        ax1.tick_params(colors='white')
        ax1.set_facecolor('#111111')
        
        # ===== НИЖНИЙ ГРАФИК =====
        if 'rsi' in plot_df.columns:
            ax2.plot(plot_df.index, plot_df['rsi'], 
                    color='purple', linewidth=2, label='RSI 14')
            ax2.axhline(y=70, color='red', linestyle='--', alpha=0.5)
            ax2.axhline(y=30, color='green', linestyle='--', alpha=0.5)
            ax2.fill_between(plot_df.index, 30, 70, alpha=0.1, color='gray')
            
            if pd.notna(plot_df['rsi'].iloc[-1]):
                current_rsi = plot_df['rsi'].iloc[-1]
                ax2.scatter(plot_df.index[-1], current_rsi, 
                        color='yellow', s=50, zorder=5)
        
        ax2.set_ylabel('RSI', color='white')
        ax2.set_xlabel('Time', color='white')
        ax2.set_ylim(0, 100)
        ax2.grid(True, alpha=0.2, linestyle='--')
        ax2.tick_params(colors='white')
        ax2.set_facecolor('#111111')
        ax2.legend(loc='upper left', fontsize=8, facecolor='#222222')
        
        # Форматирование времени
        for ax in [ax1, ax2]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
        
        plt.tight_layout()
        
        buf = BytesIO()
        plt.savefig(buf, format='PNG', dpi=self.dpi, 
                bbox_inches='tight', facecolor='#111111')
        buf.seek(0)
        plt.close()
        
        return buf

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
        if 'rsi' not in df.columns:
            return result
            
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

# ============== SMART MONEY АНАЛИЗАТОР ==============

class SmartMoneyAnalyzer:
    def __init__(self, settings: Dict = None):
        self.settings = settings or SMC_SETTINGS
        self.order_blocks = []
        self.fair_value_gaps = []
        
    def find_order_blocks(self, df: pd.DataFrame) -> List[Dict]:
        order_blocks = []
        for i in range(10, len(df) - 5):
            price_move_forward = (df['close'].iloc[i+2] - df['close'].iloc[i]) / df['close'].iloc[i] * 100
            
            if abs(price_move_forward) > 2.0:
                if price_move_forward > 0:
                    for j in range(i, max(0, i-5), -1):
                        if df['close'].iloc[j] < df['open'].iloc[j]:
                            strength = min(100, abs(price_move_forward) * 15)
                            order_blocks.append({
                                'type': 'bullish',
                                'price_min': df['low'].iloc[j],
                                'price_max': df['high'].iloc[j],
                                'strength': strength,
                                'description': f"Бычий ордер-блок ({strength:.0f}%)"
                            })
                            break
                elif price_move_forward < 0:
                    for j in range(i, max(0, i-5), -1):
                        if df['close'].iloc[j] > df['open'].iloc[j]:
                            strength = min(100, abs(price_move_forward) * 15)
                            order_blocks.append({
                                'type': 'bearish',
                                'price_min': df['low'].iloc[j],
                                'price_max': df['high'].iloc[j],
                                'strength': strength,
                                'description': f"Медвежий ордер-блок ({strength:.0f}%)"
                            })
                            break
        return order_blocks[-20:]
    
    def find_fair_value_gaps(self, df: pd.DataFrame) -> List[Dict]:
        fvg_list = []
        for i in range(1, len(df) - 1):
            candle1 = df.iloc[i-1]
            candle2 = df.iloc[i]
            candle3 = df.iloc[i+1]
            
            if candle3['low'] > candle1['high']:
                gap_size = (candle3['low'] - candle1['high']) / candle1['high'] * 100
                fvg_list.append({
                    'type': 'bullish',
                    'price_min': candle1['high'],
                    'price_max': candle3['low'],
                    'size': gap_size,
                    'strength': min(100, gap_size * 20),
                    'description': f"Бычий FVG ({gap_size:.2f}%)"
                })
            elif candle3['high'] < candle1['low']:
                gap_size = (candle1['low'] - candle3['high']) / candle3['high'] * 100
                fvg_list.append({
                    'type': 'bearish',
                    'price_min': candle3['high'],
                    'price_max': candle1['low'],
                    'size': gap_size,
                    'strength': min(100, gap_size * 20),
                    'description': f"Медвежий FVG ({gap_size:.2f}%)"
                })
        return fvg_list[-10:]
    
    def analyze(self, df: pd.DataFrame, current_price: float) -> Dict:
        result = {
            'has_signal': False,
            'signals': [],
            'strength': 0
        }
        
        order_blocks = self.find_order_blocks(df)
        for ob in order_blocks:
            if ob['price_min'] <= current_price <= ob['price_max']:
                result['has_signal'] = True
                result['signals'].append(ob['description'])
                result['strength'] = max(result['strength'], ob['strength'])
        
        fvg_list = self.find_fair_value_gaps(df)
        for fvg in fvg_list:
            if fvg['price_min'] <= current_price <= fvg['price_max']:
                result['has_signal'] = True
                result['signals'].append(f"📐 FVG: {fvg['description']}")
                result['strength'] = max(result['strength'], fvg['strength'])
        
        return result

# ============== ФРАКТАЛЬНЫЙ АНАЛИЗАТОР ==============

class FractalAnalyzer:
    def __init__(self, settings: Dict = None):
        self.settings = settings or FRACTAL_SETTINGS
        self.window = self.settings.get('window', 5)
        
    def analyze(self, df: pd.DataFrame) -> Dict:
        fractal_up = 0
        fractal_down = 0
        
        for i in range(self.window, len(df) - self.window):
            if all(df['high'].iloc[i] > df['high'].iloc[j] 
                   for j in range(i - self.window, i + self.window + 1) if j != i):
                fractal_up += 1
            if all(df['low'].iloc[i] < df['low'].iloc[j] 
                   for j in range(i - self.window, i + self.window + 1) if j != i):
                fractal_down += 1
        
        result = {
            'has_fractal': False,
            'signals': [],
            'strength': 0
        }
        
        total = fractal_up + fractal_down
        if total > 0:
            if fractal_up > fractal_down * 2:
                result['has_fractal'] = True
                result['signals'].append(f"Преобладание бычьих фракталов ({fractal_up}/{fractal_down})")
                result['strength'] = 70
            elif fractal_down > fractal_up * 2:
                result['has_fractal'] = True
                result['signals'].append(f"Преобладание медвежьих фракталов ({fractal_down}/{fractal_up})")
                result['strength'] = 70
        
        return result

# ============== СБОРЩИК ВСЕХ УРОВНЕЙ ==============

class Level:
    """Универсальный класс для любого уровня"""
    
    def __init__(self, level_type: str, price: float, strength: int, tf: str, 
                 source: str, touches: int = 0, is_dynamic: bool = False):
        self.level_type = level_type  # 'horizontal', 'trendline', 'fvg', 'fib', 'ema'
        self.price = price
        self.strength = strength  # 0-100
        self.tf = tf  # '15m', '1h', '4h', '1d', '1w', '1M'
        self.source = source  # описание
        self.touches = touches  # сколько раз касались
        self.is_dynamic = is_dynamic  # динамический уровень (EMA) или статический
        self.min_price = price if level_type != 'fvg' else None
        self.max_price = price if level_type != 'fvg' else None
        self.is_broken = False
        self.breakout_time = None
        self.breakout_price = None

class LevelCollector:
    """Сбор всех сильных уровней со всех таймфреймов"""
    
    def __init__(self):
        self.levels = []
    
    def collect_levels(self, dataframes: Dict[str, pd.DataFrame], current_price: float) -> List[Level]:
        """Сбор уровней со всех таймфреймов"""
        all_levels = []
        
        # Приоритет таймфреймов (старшие важнее)
        tf_priority = ['monthly', 'weekly', 'daily', 'four_hourly', 'hourly', 'current']
        tf_weights = {'monthly': 4.0, 'weekly': 3.5, 'daily': 3.0, 
                     'four_hourly': 2.5, 'hourly': 2.0, 'current': 1.0}
        
        for tf in tf_priority:
            if tf not in dataframes or dataframes[tf] is None:
                continue
            
            df = dataframes[tf]
            tf_weight = tf_weights.get(tf, 1.0)
            
            # 1. Горизонтальные уровни (локальные максимумы/минимумы)
            levels_h = self._find_horizontal_levels(df, tf, tf_weight)
            all_levels.extend(levels_h)
            
            # 2. EMA уровни (EMA 200, EMA 50)
            levels_ema = self._find_ema_levels(df, tf, tf_weight)
            all_levels.extend(levels_ema)
            
            # 3. FVG зоны (из вашего FVG анализа)
            if 'fvg_analysis' in dataframes[tf]:
                levels_fvg = self._convert_fvg_to_levels(dataframes[tf]['fvg_analysis'], tf, tf_weight)
                all_levels.extend(levels_fvg)
            
            # 4. Уровни Фибоначчи
            if 'fib_analysis' in dataframes[tf]:
                levels_fib = self._convert_fib_to_levels(dataframes[tf]['fib_analysis'], tf, tf_weight)
                all_levels.extend(levels_fib)
        
        # Сортируем по расстоянию до цены
        for level in all_levels:
            if level.min_price <= current_price <= level.max_price:
                level.distance = 0
            elif level.min_price > current_price:
                level.distance = (level.min_price - current_price) / current_price
            else:
                level.distance = (current_price - level.max_price) / current_price
        
        all_levels.sort(key=lambda x: x.distance)
        
        return all_levels[:20]  # топ-20 ближайших уровней
    
    def _find_horizontal_levels(self, df: pd.DataFrame, tf: str, weight: float) -> List[Level]:
        """Поиск горизонтальных уровней"""
        levels = []
        window = 20
        
        # Ищем локальные максимумы (сопротивление)
        for i in range(window, len(df) - window):
            if df['high'].iloc[i] == max(df['high'].iloc[i-window:i+window]):
                price = df['high'].iloc[i]
                strength = self.calculate_level_strength('horizontal', tf, 0, size=0)
                
                # Считаем касания
                touches = sum(1 for j in range(i, len(df)) 
                            if abs(df['high'].iloc[j] - price) / price < 0.003)
                
                # Пересчитываем силу с учетом касаний
                strength = self.calculate_level_strength('horizontal', tf, touches, size=0)
                
                level = Level(
                    level_type='horizontal_resistance',
                    price=price,
                    strength=strength,
                    tf=tf,
                    source=f"Локальный максимум ({tf})",
                    touches=touches
                )
                level.min_price = price * 0.995
                level.max_price = price * 1.005
                levels.append(level)
        
        # Ищем локальные минимумы (поддержка)
        for i in range(window, len(df) - window):
            if df['low'].iloc[i] == min(df['low'].iloc[i-window:i+window]):
                price = df['low'].iloc[i]
                strength = self.calculate_level_strength('horizontal', tf, 0, size=0)
                
                touches = sum(1 for j in range(i, len(df)) 
                            if abs(df['low'].iloc[j] - price) / price < 0.003)
                
                strength = self.calculate_level_strength('horizontal', tf, touches, size=0)
                
                level = Level(
                    level_type='horizontal_support',
                    price=price,
                    strength=strength,
                    tf=tf,
                    source=f"Локальный минимум ({tf})",
                    touches=touches
                )
                level.min_price = price * 0.995
                level.max_price = price * 1.005
                levels.append(level)
        
        return levels
    
    def _find_ema_levels(self, df: pd.DataFrame, tf: str, weight: float) -> List[Level]:
        """Поиск EMA уровней"""
        levels = []
        
        for period, color, importance in [(200, '#ff4444', 3.0), (50, '#8888ff', 2.0)]:
            ema_col = f'ema_{period}'
            if ema_col not in df.columns:
                continue
            
            current_ema = df[ema_col].iloc[-1]
            
            # Считаем касания EMA с защитой от выхода за границы
            touches = 0
            df_len = len(df)
            start_idx = max(0, df_len - 50)  # последние 50 свечей
            
            for i in range(start_idx, df_len):
                try:
                    close_price = df['close'].iloc[i]
                    ema_value = df[ema_col].iloc[i]
                    
                    if pd.isna(close_price) or pd.isna(ema_value) or ema_value == 0:
                        continue
                    
                    if abs(close_price - ema_value) / ema_value < 0.003:
                        touches += 1
                except IndexError:
                    continue
            
            strength = self.calculate_level_strength(f'ema_{period}', tf, touches, size=0)
            
            level = Level(
                level_type=f'ema_{period}',
                price=current_ema,
                strength=strength,
                tf=tf,
                source=f"EMA {period} ({tf})",
                touches=touches,
                is_dynamic=True
            )
            level.min_price = current_ema * 0.99
            level.max_price = current_ema * 1.01
            levels.append(level)
        
        return levels    
    
    def calculate_level_strength(self, level_type: str, tf: str, touches: int, **kwargs) -> int:
        """Расчет силы уровня по настройкам"""
        
        weights = LEVEL_ANALYSIS_SETTINGS['weights']
        
        if level_type == 'horizontal' or level_type in ['horizontal_resistance', 'horizontal_support']:
            base = weights['horizontal']['base']
            per_touch = weights['horizontal']['per_touch']
            tf_mult = weights['horizontal']['tf_multiplier'].get(tf, 1.0)
            
            strength = (base + touches * per_touch) * tf_mult
            
        elif level_type == 'fvg' or 'fvg' in level_type:
            base = weights['fvg']['base']
            size_mult = weights['fvg']['size_multiplier']
            tf_mult = weights['fvg']['tf_multiplier'].get(tf, 1.0)
            
            strength = (base + kwargs.get('size', 0) * size_mult) * tf_mult
            
        elif 'ema' in level_type:
            if '200' in level_type:
                base = weights['ema']['ema_200']
            else:
                base = weights['ema']['ema_50']
            tf_mult = weights['ema']['tf_multiplier'].get(tf, 1.0)
            
            strength = (base + touches * 2) * tf_mult  # +2 за каждое касание EMA
            
        else:
            # По умолчанию
            strength = 50 * tf_multiplier.get(tf, 1.0)
        
        return min(100, int(strength))

    def find_confluence_levels(self, all_levels: List[Level], current_price: float, tolerance: float = 0.5) -> List[Dict]:
        """
        Поиск уровней, которые совпадают на разных таймфреймах
        tolerance: допустимое отклонение в процентах (0.5%)
        """
        confluence_zones = []
        
        # Группируем уровни по цене (с допуском)
        for level in all_levels:
            if level.distance > 20:  # не учитываем слишком далекие
                continue
                
            # Ищем другие уровни в этой же ценовой зоне
            matching_levels = []
            for other in all_levels:
                if other == level:
                    continue
                # Проверяем, совпадают ли ценовые зоны
                price_diff = abs(level.price - other.price) / level.price * 100
                if price_diff <= tolerance:
                    matching_levels.append(other)
            
            if matching_levels:
                # Собираем все таймфреймы, где есть уровни
                tfs = [level.tf] + [l.tf for l in matching_levels]
                unique_tfs = list(set(tfs))
                
                # Считаем силу конвергенции
                strength = len(unique_tfs) * 20  # базовый вес за каждый ТФ
                for l in [level] + matching_levels:
                    strength += l.strength / 10
                
                # Определяем тип (поддержка или сопротивление)
                if level.price < current_price:
                    zone_type = "поддержка"
                    direction = "LONG"
                else:
                    zone_type = "сопротивление"
                    direction = "SHORT"
                
                confluence_zones.append({
                    'price': level.price,
                    'zone_type': zone_type,
                    'direction': direction,
                    'timeframes': unique_tfs,
                    'strength': min(100, strength),
                    'levels': [level] + matching_levels,
                    'distance': level.distance,
                    'source': f"Конвергенция на {', '.join(unique_tfs)}"
                })
        
        # Сортируем по силе и расстоянию
        confluence_zones.sort(key=lambda x: (-x['strength'], x['distance']))
        return confluence_zones[:5]  # топ-5 сильных зон

    def calculate_level_strength_score(self, level: Dict, df: pd.DataFrame, last: pd.Series, alignment: Dict) -> Dict:
        """
        Оценка силы уровня и вероятности разворота/пробоя
        """
        result = {
            'strength': 0,           # 0-100
            'probability': 0,        # 0-100 (вероятность разворота)
            'direction': None,
            'signals': [],
            'action': 'наблюдение'    # 'разворот', 'пробой', 'ложный_пробой'
        }
        
        # ===== 1. КОНВЕРГЕНЦИЯ ТАЙМФРЕЙМОВ =====
        if 'timeframes' in level:
            tf_count = len(level['timeframes'])
        else:
            tf_count = 1
        
        if tf_count >= 4:
            result['strength'] += 40
            result['signals'].append(f"🔥 СУПЕР-КОНВЕРГЕНЦИЯ: {tf_count} ТФ")
            result['probability'] += 30
        elif tf_count >= 3:
            result['strength'] += 30
            result['signals'].append(f"⭐ Сильная конвергенция: {tf_count} ТФ")
            result['probability'] += 20
        elif tf_count >= 2:
            result['strength'] += 20
            result['signals'].append(f"📊 Конвергенция: {tf_count} ТФ")
            result['probability'] += 10
        
        # ===== 2. КАСАНИЯ УРОВНЯ =====
        touches = level.get('touches', 1)
        if touches >= 7:
            result['strength'] += 25
            result['signals'].append(f"🎯 Уровень тестирован {touches} раз (очень сильный)")
            result['probability'] += 20
        elif touches >= 5:
            result['strength'] += 20
            result['signals'].append(f"✅ Уровень тестирован {touches} раз")
            result['probability'] += 15
        elif touches >= 3:
            result['strength'] += 12
            result['signals'].append(f"📌 Уровень тестирован {touches} раза")
            result['probability'] += 8
        
        # ===== 3. ОБЪЕМ НА ПОДХОДЕ =====
        volume_ratio = last.get('volume_ratio', 1.0)
        if volume_ratio > 3.0:
            result['strength'] += 25
            result['signals'].append(f"🔥 Аномальный объем x{volume_ratio:.1f}")
            result['probability'] += 15
        elif volume_ratio > 2.0:
            result['strength'] += 18
            result['signals'].append(f"⚡ Высокий объем x{volume_ratio:.1f}")
            result['probability'] += 10
        elif volume_ratio > 1.5:
            result['strength'] += 10
            result['signals'].append(f"📊 Повышенный объем x{volume_ratio:.1f}")
            result['probability'] += 5
        
        # ===== 4. RSI ЭКСТРЕМУМ =====
        rsi = last.get('rsi', 50)
        if rsi > 85:
            result['strength'] += 15
            result['signals'].append(f"🔴 RSI сильно перекуплен ({rsi:.1f})")
            result['probability'] += 15
        elif rsi > 75:
            result['strength'] += 10
            result['signals'].append(f"🟡 RSI перекуплен ({rsi:.1f})")
            result['probability'] += 8
        elif rsi < 15:
            result['strength'] += 15
            result['signals'].append(f"🟢 RSI сильно перепродан ({rsi:.1f})")
            result['probability'] += 15
        elif rsi < 25:
            result['strength'] += 10
            result['signals'].append(f"📉 RSI перепродан ({rsi:.1f})")
            result['probability'] += 8
        
        # ===== 5. ТРЕНД =====
        weekly_trend = alignment.get('weekly_trend', '')
        daily_trend = alignment.get('daily_trend', '')
        zone_type = level.get('zone_type', '')
        
        if zone_type == 'поддержка':
            if weekly_trend == 'ВОСХОДЯЩИЙ' or daily_trend == 'ВОСХОДЯЩИЙ':
                result['strength'] += 20
                result['signals'].append("📈 Тренд вверх (поддержка усилена)")
                result['probability'] += 15
                result['direction'] = 'LONG'
            else:
                result['direction'] = 'LONG' if result['probability'] > 50 else 'NEUTRAL'
        else:  # сопротивление
            if weekly_trend == 'НИСХОДЯЩИЙ' or daily_trend == 'НИСХОДЯЩИЙ':
                result['strength'] += 20
                result['signals'].append("📉 Тренд вниз (сопротивление усилено)")
                result['probability'] += 15
                result['direction'] = 'SHORT'
            else:
                result['direction'] = 'SHORT' if result['probability'] > 50 else 'NEUTRAL'
        
        # ===== 6. ИМПУЛЬС ПОДХОДА =====
        distance = level.get('distance', 100)
        if distance < 1.0:
            result['strength'] += 15
            result['signals'].append(f"⚡ Цена у уровня ({distance:.1f}%)")
            result['probability'] += 20
        elif distance < 2.0:
            result['strength'] += 10
            result['signals'].append(f"🎯 Цена близко к уровню ({distance:.1f}%)")
            result['probability'] += 10
        
        # ===== 7. ОПРЕДЕЛЕНИЕ ДЕЙСТВИЯ =====
        result['strength'] = min(100, result['strength'])
        result['probability'] = min(100, result['probability'])
        
        if result['probability'] >= 70:
            result['action'] = 'разворот'
        elif result['probability'] >= 50:
            result['action'] = 'вероятный_разворот'
        elif result['probability'] >= 30:
            result['action'] = 'наблюдение'
        else:
            result['action'] = 'возможный_пробой'
        
        return result

# ============== УНИВЕРСАЛЬНЫЙ ДЕТЕКТОР ПРОБОЕВ ==============

class UniversalBreakoutDetector:
    """Детектор пробоев для любых уровней"""
    
    def __init__(self):
        self.tracked_breakouts = {}  # отслеживаем все пробои
        self.fakeouts = set()  # ложные пробои
    
    def analyze_level(self, level: Level, current_price: float, 
                     df: pd.DataFrame, required_candles: int = 3) -> Dict:
        """Анализ одного уровня"""
        
        key = f"{level.tf}_{level.level_type}_{level.price}"
        
        # Определяем статус пробоя
        if level.level_type in ['horizontal_resistance', 'trendline_resistance', 'fib_resistance']:
            is_breakout = current_price > level.max_price
            breakout_direction = "вверх"
            target_direction = "LONG"
        else:
            is_breakout = current_price < level.min_price
            breakout_direction = "вниз"
            target_direction = "SHORT"
        
        # Нет пробоя
        if not is_breakout:
            if key in self.tracked_breakouts:
                # Проверяем, не был ли это ложный пробой
                tracker = self.tracked_breakouts[key]
                if tracker['status'] == 'potential' and tracker['max_price'] > level.max_price * 1.01:
                    # Был пробой, но цена вернулась - ЭТО ЛОЖНЫЙ!
                    self.fakeouts.add(key)
                    return {
                        'type': 'fakeout',
                        'level': level,
                        'direction': breakout_direction,
                        'target': target_direction,
                        'message': f"🚨 ЛОЖНЫЙ ПРОБОЙ {breakout_direction} на {level.tf}!",
                        'confidence': level.strength * 1.5
                    }
                del self.tracked_breakouts[key]
            return None
        
        # Есть пробой
        if key not in self.tracked_breakouts:
            # Первый раз видим пробой
            self.tracked_breakouts[key] = {
                'level': level,
                'breakout_price': current_price,
                'max_price': current_price,
                'min_price': current_price,
                'time': datetime.now(),
                'candles': 1,
                'status': 'potential'
            }
            return {
                'type': 'breakout_alert',
                'level': level,
                'direction': breakout_direction,
                'message': f"⚠️ ПРОБОЙ {breakout_direction} на {level.tf}! (ожидание подтверждения)",
                'confidence': level.strength
            }
        
        # Отслеживаем пробой
        tracker = self.tracked_breakouts[key]
        tracker['candles'] += 1
        tracker['max_price'] = max(tracker['max_price'], current_price)
        tracker['min_price'] = min(tracker['min_price'], current_price)
        
        # Проверяем подтверждение
        if tracker['candles'] >= required_candles:
            # Проверяем размер движения
            if breakout_direction == "вверх":
                move_percent = ((tracker['max_price'] - level.max_price) / level.max_price) * 100
            else:
                move_percent = ((level.min_price - tracker['min_price']) / level.min_price) * 100
            
            if move_percent >= 1.0:  # подтвержденный пробой минимум на 1%
                del self.tracked_breakouts[key]
                return {
                    'type': 'confirmed_breakout',
                    'level': level,
                    'direction': breakout_direction,
                    'target': target_direction,
                    'move_percent': move_percent,
                    'message': f"✅ ПРОБОЙ {breakout_direction} на {level.tf} ПОДТВЕРЖДЕН! (+{move_percent:.1f}%)",
                    'confidence': level.strength * 1.3
                }
        
        return None

# ============== АНАЛИЗАТОР ФИБОНАЧЧИ ==============

class FibonacciAnalyzer:
    """
    Анализ уровней Фибоначчи с учетом:
    - Коррекций (0.236, 0.382, 0.5, 0.618, 0.786, 0.86)
    - Расширений (0.18, 0.27, 0.618)
    - Правила 3 свечей для точки B
    - Автоматический перевод терминов на русский язык
    """
    
    def __init__(self, settings: Dict = None):
        self.settings = settings or FIBONACCI_SETTINGS
        self.retracement_levels = self.settings.get('retracement_levels', 
                                                    [0.236, 0.382, 0.5, 0.618, 0.786, 0.86])
        self.extension_levels = self.settings.get('extension_levels', 
                                                  [0.18, 0.27, 0.618])
        self.lookback_candles = self.settings.get('lookback_candles', 3)
        self.min_distance = self.settings.get('min_distance_pct', 0.5)
        
        # Словарь для перевода таймфреймов
        self.tf_translation = {
            'monthly': 'месячный',
            'weekly': 'недельный',
            'daily': 'дневной',
            'hourly': 'часовой',
            'current': 'текущий'
        }
        
        # Словарь для перевода направления
        self.dir_translation = {
            'support': 'поддержка',
            'resistance': 'сопротивление'
        }
    
    def find_swing_low(self, df: pd.DataFrame, window: int = 5) -> Optional[int]:
        """Поиск локального минимума (точка A для бычьего движения)"""
        for i in range(window, len(df) - window):
            if (df['low'].iloc[i] < df['low'].iloc[i-1] and 
                df['low'].iloc[i] < df['low'].iloc[i-2] and
                df['low'].iloc[i] < df['low'].iloc[i+1] and
                df['low'].iloc[i] < df['low'].iloc[i+2]):
                return i
        return None
    
    def find_swing_high(self, df: pd.DataFrame, window: int = 5) -> Optional[int]:
        """Поиск локального максимума (точка A для медвежьего движения)"""
        for i in range(window, len(df) - window):
            if (df['high'].iloc[i] > df['high'].iloc[i-1] and 
                df['high'].iloc[i] > df['high'].iloc[i-2] and
                df['high'].iloc[i] > df['high'].iloc[i+1] and
                df['high'].iloc[i] > df['high'].iloc[i+2]):
                return i
        return None
    
    def find_point_b(self, df: pd.DataFrame, start_idx: int, is_bullish: bool) -> Optional[Dict]:
        """Поиск точки B как максимума/минимума следующих N свечей"""
        if start_idx + self.lookback_candles >= len(df):
            return None
        
        if is_bullish:
            max_price = float('-inf')
            max_idx = start_idx
            for i in range(1, self.lookback_candles + 1):
                idx = start_idx + i
                if df['high'].iloc[idx] > max_price:
                    max_price = df['high'].iloc[idx]
                    max_idx = idx
            return {'price': max_price, 'index': max_idx, 'type': 'bullish'}
        else:
            min_price = float('inf')
            min_idx = start_idx
            for i in range(1, self.lookback_candles + 1):
                idx = start_idx + i
                if df['low'].iloc[idx] < min_price:
                    min_price = df['low'].iloc[idx]
                    min_idx = idx
            return {'price': min_price, 'index': min_idx, 'type': 'bearish'}
    
    def calculate_fib_levels(self, point_a: float, point_b: float) -> Dict:
        """Расчет всех уровней Фибоначчи"""
        diff = point_b - point_a
        levels = {}
        
        for level in self.retracement_levels:
            price = point_b - diff * level
            levels[f'{level:.3f}'] = {
                'price': round(price, 2),
                'type': 'retracement',
                'level': level,
                'description': f'{level*100:.1f}%'
            }
        
        for level in self.extension_levels:
            price = point_b + diff * level
            levels[f'-{level:.3f}'] = {
                'price': round(price, 2),
                'type': 'extension',
                'level': -level,
                'description': f'-{level*100:.1f}%'
            }
        
        return levels
    
    def check_price_reaction(self, current_price: float, levels: Dict) -> List[Dict]:
        """Проверка реакции цены на уровни Фибоначчи"""
        reactions = []
        
        for key, level_data in levels.items():
            level_price = level_data['price']
            distance = abs(current_price - level_price) / current_price * 100
            
            if distance < self.min_distance:
                strength = 50
                if level_data['level'] == 0.618:
                    strength = 90
                elif level_data['level'] == 0.5:
                    strength = 80
                elif level_data['level'] == 0.236:
                    strength = 70
                elif level_data['level'] == 0.786:
                    strength = 85
                elif level_data['level'] == -0.618:
                    strength = 95
                
                direction = 'support' if current_price < level_price else 'resistance'
                
                # Переводим направление на русский
                direction_ru = self.dir_translation.get(direction, direction)
                
                reactions.append({
                    'level': key,
                    'price': level_price,
                    'strength': strength,
                    'description': f"{level_data['description']} ({direction_ru})"
                })
        
        return reactions
    
    def analyze(self, df: pd.DataFrame, timeframe: str = 'current') -> Dict:
        """Анализ Фибоначчи на таймфрейме"""
        result = {'has_signal': False, 'signals': [], 'strength': 0, 'levels': {}, 'timeframe': timeframe}
        
        # Бычье движение
        low_idx = self.find_swing_low(df)
        if low_idx:
            point_a = df['low'].iloc[low_idx]
            point_b_data = self.find_point_b(df, low_idx, True)
            if point_b_data:
                levels = self.calculate_fib_levels(point_a, point_b_data['price'])
                reactions = self.check_price_reaction(df['close'].iloc[-1], levels)
                for r in reactions:
                    result['has_signal'] = True
                    
                    # Переводим таймфрейм на русский
                    tf_ru = self.tf_translation.get(timeframe, timeframe)
                    
                    result['signals'].append(f"Фибо {tf_ru}: {r['description']}")
                    result['strength'] = max(result['strength'], r['strength'])
                    result['levels'] = levels
        
        # Медвежье движение
        high_idx = self.find_swing_high(df)
        if high_idx:
            point_a = df['high'].iloc[high_idx]
            point_b_data = self.find_point_b(df, high_idx, False)
            if point_b_data:
                levels = self.calculate_fib_levels(point_a, point_b_data['price'])
                reactions = self.check_price_reaction(df['close'].iloc[-1], levels)
                for r in reactions:
                    result['has_signal'] = True
                    
                    # Переводим таймфрейм на русский
                    tf_ru = self.tf_translation.get(timeframe, timeframe)
                    
                    result['signals'].append(f"Фибо {tf_ru}: {r['description']}")
                    result['strength'] = max(result['strength'], r['strength'])
                    result['levels'] = levels
        
        return result
    
    def analyze_multi_timeframe(self, dataframes: Dict[str, pd.DataFrame]) -> Dict:
        """Мультитаймфреймовый анализ Фибоначчи с переводом на русский"""
        result = {'has_confluence': False, 'signals': [], 'strength': 0, 'levels': {}}
        
        # Приоритет таймфреймов (от большего к меньшему)
        tf_priority = ['monthly', 'weekly', 'daily', 'hourly', 'current']
        
        for tf_name in tf_priority:
            if tf_name not in dataframes or dataframes[tf_name] is None:
                continue
            
            df = dataframes[tf_name]
            tf_result = self.analyze(df, tf_name)
            
            if tf_result['has_signal']:
                # Вес для старших таймфреймов
                weight = 1.0
                if tf_name == 'monthly':
                    weight = 3.0
                elif tf_name == 'weekly':
                    weight = 2.5
                elif tf_name == 'daily':
                    weight = 2.0
                elif tf_name == 'hourly':
                    weight = 1.5
                
                # Добавляем сигналы с уже переведенными таймфреймами
                result['signals'].extend(tf_result['signals'])
                result['strength'] += tf_result['strength'] * weight
                result['levels'][tf_name] = tf_result['levels']
                result['has_confluence'] = True
        
        if result['strength'] > 100:
            result['strength'] = 100
        
        return result

# ============== АНАЛИЗАТОР VOLUME PROFILE ==============

class VolumeProfileAnalyzer:
    """Анализ Volume Profile для определения ключевых уровней"""
    
    def __init__(self, settings: Dict = None):
        self.settings = settings or VOLUME_PROFILE_SETTINGS
        self.lookback = self.settings.get('lookback_bars', 100)
        self.va_pct = self.settings.get('value_area_pct', 70)
    
    def calculate_volume_profile(self, df: pd.DataFrame) -> Dict:
        """Расчет Volume Profile"""
        if df is None or len(df) < 20:
            return {}
        
        recent = df.tail(self.lookback).copy()
        price_precision = 6 if recent['close'].max() < 0.1 else 4 if recent['close'].max() < 10 else 2
        
        volume_by_price = {}
        for _, row in recent.iterrows():
            price_step = 10 ** (-price_precision)
            price_levels = np.arange(round(row['low'], price_precision), round(row['high'], price_precision) + price_step, price_step)
            vol_per_level = row['volume'] / len(price_levels) if len(price_levels) > 0 else 0
            for price in price_levels:
                price_key = round(price, price_precision)
                volume_by_price[price_key] = volume_by_price.get(price_key, 0) + vol_per_level
        
        if not volume_by_price:
            return {}
        
        sorted_prices = sorted(volume_by_price.keys())
        volumes = [volume_by_price[p] for p in sorted_prices]
        max_vol_idx = np.argmax(volumes)
        poc_price = sorted_prices[max_vol_idx]
        
        total_volume = sum(volumes)
        price_vol_pairs = list(zip(sorted_prices, volumes))
        price_vol_pairs.sort(key=lambda x: x[1], reverse=True)
        
        cum_vol, value_area_prices = 0, []
        for price, vol in price_vol_pairs:
            cum_vol += vol
            value_area_prices.append(price)
            if cum_vol >= total_volume * self.va_pct / 100:
                break
        
        val, vah = min(value_area_prices), max(value_area_prices)
        avg_volume = total_volume / len(volumes)
        hvn_threshold = avg_volume * self.settings.get('min_hvn_strength', 2.0)
        hvn_levels = [p for p, v in zip(sorted_prices, volumes) if v > hvn_threshold]
        
        return {
            'poc': poc_price,
            'val': val,
            'vah': vah,
            'hvn': hvn_levels[:5],
            'total_volume': total_volume
        }
    
    def check_price_reaction(self, current_price: float, vp_data: Dict) -> List[Dict]:
        """Проверка реакции цены на уровни Volume Profile"""
        reactions, distance = [], self.settings.get('confluence_distance', 0.5)
        if not vp_data:
            return reactions
        
        poc_dist = abs(current_price - vp_data['poc']) / current_price * 100
        if poc_dist < distance:
            reactions.append({'level': 'POC', 'strength': 90, 'description': f"Цена у POC ({vp_data['poc']:.2f})"})
        
        val_dist = abs(current_price - vp_data['val']) / current_price * 100
        if val_dist < distance:
            reactions.append({'level': 'VAL', 'strength': 75, 'description': f"Цена у VAL ({vp_data['val']:.2f})"})
        
        vah_dist = abs(current_price - vp_data['vah']) / current_price * 100
        if vah_dist < distance:
            reactions.append({'level': 'VAH', 'strength': 75, 'description': f"Цена у VAH ({vp_data['vah']:.2f})"})
        
        for hvn in vp_data['hvn'][:1]:
            hvn_dist = abs(current_price - hvn) / current_price * 100
            if hvn_dist < distance:
                reactions.append({'level': 'HVN', 'strength': 80, 'description': f"Цена в зоне HVN ({hvn:.2f})"})
                break
        
        return reactions
    
    def analyze_multi_timeframe(self, dataframes: Dict[str, pd.DataFrame]) -> Dict:
        """Мультитаймфреймовый анализ Volume Profile"""
        result = {'has_confluence': False, 'signals': [], 'strength': 0, 'levels': {}}
        target_tfs = self.settings.get('timeframes', ['daily', 'weekly', 'monthly'])
        
        for tf_name in target_tfs:
            if tf_name not in dataframes or dataframes[tf_name] is None:
                continue
            
            df = dataframes[tf_name]
            vp_data = self.calculate_volume_profile(df)
            if vp_data:
                reactions = self.check_price_reaction(df['close'].iloc[-1], vp_data)
                weight = 3.0 if tf_name == 'monthly' else 2.5 if tf_name == 'weekly' else 2.0 if tf_name == 'daily' else 1.0
                
                for r in reactions:
                    result['has_confluence'] = True
                    result['signals'].append(f"📊 {tf_name}: {r['description']}")
                    result['strength'] += r['strength'] * weight
                    result['levels'][tf_name] = vp_data
        
        if result['strength'] > 100:
            result['strength'] = 100
        
        return result

# ============== АНАЛИЗАТОР ИМБАЛАНСОВ ==============

class ImbalanceAnalyzer:
    def __init__(self, settings: Dict = None):
        self.settings = settings or IMBALANCE_SETTINGS
        self.threshold_buy = self.settings.get('threshold_buy', 0.3)
        self.threshold_sell = self.settings.get('threshold_sell', -0.3)
        
    def analyze(self, dataframes: Dict[str, pd.DataFrame]) -> Dict:
        result = {
            'has_imbalance': False,
            'signals': [],
            'strength': 0
        }
        
        for tf_name, df in dataframes.items():
            if df is None or df.empty:
                continue
            
            df['buy_volume'] = np.where(
                df['close'] > df['open'],
                df['volume'] * 0.7,
                df['volume'] * 0.3
            )
            df['sell_volume'] = df['volume'] - df['buy_volume']
            df['imbalance'] = (df['buy_volume'] - df['sell_volume']) / df['volume']
            
            last_imbalance = df['imbalance'].iloc[-1]
            
            if last_imbalance > self.threshold_buy:
                result['has_imbalance'] = True
                result['signals'].append(f"Бычий имбаланс на {tf_name}")
                result['strength'] = max(result['strength'], abs(last_imbalance) * 100)
            elif last_imbalance < self.threshold_sell:
                result['has_imbalance'] = True
                result['signals'].append(f"Медвежий имбаланс на {tf_name}")
                result['strength'] = max(result['strength'], abs(last_imbalance) * 100)
        
        return result

# ============== АНАЛИЗАТОР ЛИКВИДНОСТИ ==============

class LiquidityAnalyzer:
    def __init__(self, settings: Dict = None):
        self.settings = settings or LIQUIDITY_SETTINGS
    
    def analyze(self, symbol: str, df: pd.DataFrame) -> Dict:
        result = {
            'has_signal': False,
            'signals': [],
            'strength': 0
        }
        return result

# ============== БАЗОВЫЙ КЛАСС ДЛЯ БИРЖ ==============

class BaseExchangeFetcher:
    def __init__(self, name: str):
        self.name = name
    
    async def fetch_all_pairs(self) -> List[str]:
        return []
    
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> Optional[pd.DataFrame]:
        return None
    
    async def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        return 0.0
    
    async def fetch_ticker(self, symbol: str) -> Dict:
        return {}
    
    async def fetch_contract_info(self, symbol: str) -> Dict:
        return {
            'max_leverage': 100,
            'min_amount': 5.0,
            'max_amount': 2_000_000
        }
    
    async def fetch_open_interest(self, symbol: str) -> Optional[float]:
        return None
    
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
        
        # Инициализация кэша плеч
        try:
            from leverage_cache import LeverageCache
            self.leverage_cache = LeverageCache(
                os.getenv('BINGX_API_KEY'),
                os.getenv('BINGX_SECRET_KEY')
            )
            logger.info("✅ BingX Leverages клиент инициализирован")
        except Exception as e:
            logger.warning(f"⚠️ LeverageCache не инициализирован: {e}")
            self.leverage_cache = None
        
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
            
            logger.info(f"📊 BingX Futures: загружено {len(usdt_pairs)} фьючерсных пар")
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
            if "404" not in str(e):
                logger.error(f"Ошибка BingX {symbol}: {e}")
            return None
    
    async def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        try:
            funding = await self.exchange.fetch_funding_rate(symbol)
            if funding and 'fundingRate' in funding:
                return funding['fundingRate']
            return 0.0
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
        """Получение информации о контракте с защитой от некорректных данных"""
        try:
            markets = await self.exchange.load_markets()
            market = markets.get(symbol, {})
            limits = market.get('limits', {})
            
            # Получаем реальное плечо из кэша если доступно
            max_leverage = 100
            if self.leverage_cache:
                try:
                    max_leverage = await self.leverage_cache.get_leverage(symbol)
                except Exception as e:
                    logger.debug(f"Ошибка получения плеча из кэша для {symbol}: {e}")
            
            # Если плечо некорректное - определяем по монете
            if max_leverage > 200 or max_leverage < 1:
                coin = symbol.split('/')[0].upper()
                if coin in ['BTC', 'ETH']:
                    max_leverage = 125
                elif coin in ['BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'DOT', 'LINK']:
                    max_leverage = 75
                elif coin in ['SHIB', 'PEPE', 'DOGS', 'NOT', 'BONK', 'WIF']:
                    max_leverage = 50
                else:
                    max_leverage = 50
            
            # Минимальная сумма входа - защита от дурака
            min_amount = 5.0
            if limits.get('amount'):
                raw_min = limits['amount'].get('min', 5.0)
                # Если минималка слишком большая (>500$) - игнорируем
                if raw_min < 500:
                    min_amount = raw_min
                else:
                    logger.debug(f"Слишком большая минималка {raw_min} для {symbol}, использую 5$")
            
            # Для мемкоинов иногда минималка выше
            coin = symbol.split('/')[0].upper()
            if coin in ['SHIB', 'PEPE', 'DOGS', 'BONK'] and min_amount < 10:
                min_amount = 10.0
            
            # Максимальная сумма
            max_amount = 2_000_000
            if limits.get('amount') and limits['amount'].get('max'):
                raw_max = limits['amount'].get('max', 2_000_000)
                # Ограничиваем разумными пределами
                if raw_max < 50_000_000:
                    max_amount = raw_max
            
            # Получаем лимиты позиций из tiers если доступно
            if self.leverage_cache:
                try:
                    position_limits = await self.leverage_cache.get_position_limits(symbol)
                    if position_limits.get('max_position'):
                        max_amount = min(position_limits['max_position'], max_amount)
                except Exception as e:
                    logger.debug(f"Ошибка получения position limits для {symbol}: {e}")
            
            return {
                'max_leverage': max_leverage,
                'min_amount': round(min_amount, 2),
                'max_amount': int(max_amount),
                'has_data': True
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения контракта {symbol}: {e}")
            
            # Fallback значения с защитой
            coin = symbol.split('/')[0].upper()
            
            if coin in ['BTC', 'ETH']:
                max_leverage = 125
            elif coin in ['BNB', 'SOL', 'XRP', 'ADA']:
                max_leverage = 75
            else:
                max_leverage = 50
            
            return {
                'max_leverage': max_leverage,
                'min_amount': 5.0,
                'max_amount': 2_000_000,
                'has_data': False
            }
    
    async def fetch_open_interest(self, symbol: str) -> Optional[float]:
        try:
            oi = await self.exchange.fetch_open_interest(symbol)
            return oi.get('openInterestAmount', 0)
        except:
            return None
    
    async def close(self):
        await self.exchange.close()

# ============== ДЛЯ ПОДДЕРЖКИ BYBIT, MEXC ==============
class MultiExchangeFetcher:
    """Универсальный фетчер для всех бирж"""
    
    def __init__(self, exchange_id: str, api_key: str = None, api_secret: str = None):
        self.exchange_id = exchange_id
        self.name = exchange_id.capitalize()
        
        exchange_class = getattr(ccxt, exchange_id)
        config = {'enableRateLimit': True, 'options': {'adjustForTimeDifference': True}}
        
        if api_key and api_secret:
            config['apiKey'] = api_key
            config['secret'] = api_secret
        
        self.exchange = exchange_class(config)
        
        if exchange_id == 'mexc':
            self.exchange.options['defaultType'] = 'future'
        elif exchange_id == 'bybit':
            self.exchange.options['defaultType'] = 'linear'
    
    async def fetch_all_pairs(self) -> List[str]:
        await self.exchange.load_markets()
        usdt_pairs = []
        for symbol, market in self.exchange.markets.items():
            if market['quote'] == 'USDT' and market['active'] and market['type'] in ['swap', 'future', 'linear']:
                usdt_pairs.append(symbol)
        return usdt_pairs
    
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
            return None
    
    async def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        try:
            funding = await self.exchange.fetch_funding_rate(symbol)
            return funding.get('fundingRate', 0.0) if funding else 0.0
        except:
            return 0.0
    
    async def fetch_ticker(self, symbol: str) -> Dict:
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return {'volume_24h': ticker.get('quoteVolume'), 'price_change_24h': ticker.get('percentage')}
        except:
            return {}
    
    async def fetch_contract_info(self, symbol: str) -> Dict:
        return {'max_leverage': 100, 'min_amount': 5.0, 'max_amount': 2_000_000}
    
    async def close(self):
        await self.exchange.close()

# ============== МУЛЬТИТАЙМФРЕЙМ АНАЛИЗАТОР ==============

class MultiTimeframeAnalyzer:
    def __init__(self):
        self.divergence = DivergenceAnalyzer() if FEATURES['advanced']['divergence'] else None
        self.smc = SmartMoneyAnalyzer(SMC_SETTINGS) if FEATURES['advanced']['smart_money'] else None
        self.fractal = FractalAnalyzer(FRACTAL_SETTINGS) if FEATURES['advanced']['fractals'] else None
        self.fibonacci = None
        self.volume_profile = None
        self.accumulation = None
        self.imbalance = ImbalanceAnalyzer(IMBALANCE_SETTINGS) if FEATURES['advanced']['imbalance'] else None
        self.breakout_tracker = BreakoutTracker()
        # Инициализация истории Фибоначчи
        self.fib_history = FibHistoryTracker()
        logger.info("✅ FibHistoryTracker добавлен в MultiTimeframeAnalyzer")

        # Словарь для перевода таймфреймов
        self.tf_translation = {
            'monthly': 'месячный',
            'weekly': 'недельный',
            'daily': 'дневной',
            'hourly': 'часовой',
            'current': 'текущий'
        }
    
    def set_fibonacci(self, fib_analyzer):
        self.fibonacci = fib_analyzer
    
    def set_volume_profile(self, vp_analyzer):
        self.volume_profile = vp_analyzer
    
    def set_accumulation(self, acc_analyzer):
        self.accumulation = acc_analyzer
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Расчет всех технических индикаторов"""
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
        """Анализ согласованности трендов на разных таймфреймах"""
        alignment = {
            'trend_alignment': 0,
            'signals': [],
            'trends': {},
            'current_trend': None,
            'hourly_trend': None,
            'four_hourly_trend': None,
            'daily_trend': None,
            'weekly_trend': None,
            'monthly_trend': None,
        }
        
        # Словарь для перевода
        tf_names = {
            '1m': '1м',
            '3m': '3м',
            '5m': '5м',
            'current': '15м',
            '30m': '30м',
            'hourly': '1ч',
            'four_hourly': '4ч',
            'daily': '1д',
            'weekly': '1н',
            'monthly': '1м'
        }
        
        # Собираем все таймфреймы в правильном порядке
        tf_order = ['1m', '3m', '5m', 'current', '30m', 'hourly', 'four_hourly', 'daily', 'weekly', 'monthly']
        
        trends_list = []
        minor_bullish = 0
        minor_bearish = 0
        
        for tf_name in tf_order:
            if tf_name not in dataframes or dataframes[tf_name] is None or dataframes[tf_name].empty:
                continue
            
            df = dataframes[tf_name]
            last = df.iloc[-1]
            
            # Определяем тренд по EMA 9 и 21
            if pd.notna(last.get('ema_9')) and pd.notna(last.get('ema_21')):
                trend = 'ВОСХОДЯЩИЙ' if last['ema_9'] > last['ema_21'] else 'НИСХОДЯЩИЙ'
                alignment['trends'][tf_name] = trend
                
                # Сохраняем в отдельные поля для совместимости
                if tf_name == 'current':
                    alignment['current_trend'] = trend
                elif tf_name == 'hourly':
                    alignment['hourly_trend'] = trend
                elif tf_name == 'four_hourly':
                    alignment['four_hourly_trend'] = trend
                elif tf_name == 'daily':
                    alignment['daily_trend'] = trend
                elif tf_name == 'weekly':
                    alignment['weekly_trend'] = trend
                elif tf_name == 'monthly':
                    alignment['monthly_trend'] = trend
                
                # Для младших ТФ (1м, 3м, 5м) — считаем для группировки
                if tf_name in ['1m', '3m', '5m']:
                    if trend == 'ВОСХОДЯЩИЙ':
                        minor_bullish += 1
                    else:
                        minor_bearish += 1
                
                # Для остальных ТФ — добавляем в согласованность
                elif tf_name not in ['1m', '3m', '5m']:
                    trends_list.append(trend)
                    
                    # Добавляем сигнал для сильных трендов
                    if tf_name in ['weekly', 'monthly'] and last['ema_9'] > last['ema_200']:
                        alignment['signals'].append(f"{tf_names[tf_name]} ТРЕНД ВОСХОДЯЩИЙ (выше EMA 200)")
                    elif tf_name in ['weekly', 'monthly'] and last['ema_9'] < last['ema_200']:
                        alignment['signals'].append(f"{tf_names[tf_name]} ТРЕНД НИСХОДЯЩИЙ (ниже EMA 200)")
        
        # Добавляем группировку младших ТФ в причины
        if minor_bullish > 0 or minor_bearish > 0:
            if minor_bullish > minor_bearish:
                minor_text = "1м, 3м, 5м тренд восходящий (подтверждение)"
            else:
                minor_text = "1м, 3м, 5м тренд нисходящий (подтверждение)"
            alignment['signals'].append(minor_text)
        
        # Считаем согласованность (только для основных ТФ)
        if trends_list:
            bullish = trends_list.count('ВОСХОДЯЩИЙ')
            bearish = trends_list.count('НИСХОДЯЩИЙ')
            alignment['trend_alignment'] = (max(bullish, bearish) / len(trends_list)) * 100
            
            if alignment['trend_alignment'] >= 80 and len(trends_list) >= 3:
                direction = "бычий" if bullish > bearish else "медвежий"
                alignment['signals'].append(f"Тренды согласованы: {alignment['trend_alignment']:.0f}% ({direction}, {len(trends_list)} ТФ)")
        
        return alignment
    
    def analyze_fvg_multi_timeframe(self, dataframes: Dict[str, pd.DataFrame], current_price: float) -> Dict:
        """
        Анализ FVG на всех таймфреймах с фильтрацией по расстоянию
        """
        result = {'has_fvg': False, 'signals': [], 'strength': 0, 'zones': []}
        all_zones = []
        
        # Максимальное расстояние для отображения в причинах (например, 15%)
        MAX_DISTANCE_PCT = 15.0
        """
        Анализ FVG на всех таймфреймах с фильтрацией для графика
        """
        result = {'has_fvg': False, 'signals': [], 'strength': 0, 'zones': []}
        all_zones = []  # временный список всех найденных зон
        
        # Приоритет таймфреймов
        tf_priority = ['monthly', 'weekly', 'daily', 'four_hourly', 'hourly', 'current']
        
        # Словари для форматирования
        tf_short = {
            'monthly': '1м',
            'weekly': '1н',
            'daily': '1д',
            'four_hourly': '4ч',
            'hourly': '1ч',
            'current': '15м'
        }
        
        tf_weights = {
            'monthly': 4.0,
            'weekly': 3.5,
            'daily': 2.5,
            'four_hourly': 2.0,
            'hourly': 1.5,
            'current': 1.0
        }
        
        tf_names_ru = {
            'monthly': 'месячный',
            'weekly': 'недельный',
            'daily': 'дневной',
            'four_hourly': '4-часовой',
            'hourly': 'часовой',
            'current': '15-минутный'
        }
        
        dir_emoji = {
            'bullish': '📈',
            'bearish': '📉'
        }
        
        # Анализируем каждый таймфрейм
        for tf_name in tf_priority:
            if tf_name not in dataframes or dataframes[tf_name] is None:
                continue
            
            df = dataframes[tf_name]

            if df is None or df.empty or len(df) < 20:
                logger.info(f"    ⏭️ {tf_name}: недостаточно данных ({len(df) if df is not None else 0} свечей)")
                continue
            
            # Создаем временный SMC анализатор для этого ТФ
            smc_temp = SmartMoneyAnalyzer(SMC_SETTINGS)
            fvg_list = smc_temp.find_fair_value_gaps(df)
            
            logger.info(f"    🔍 {tf_name}: найдено {len(fvg_list)} FVG кандидатов")
            
            for fvg in fvg_list:
                # Проверяем, не закрыта ли зона
                if self._is_fvg_closed(df, fvg):
                    logger.info(f"    ⏭️ {tf_name} FVG пропущен (закрыт)")
                    continue
                
                # Проверяем, находится ли текущая цена в зоне FVG
                in_zone = (fvg['price_min'] <= current_price <= fvg['price_max'])
                
                # Рассчитываем расстояние до зоны
                if in_zone:
                    distance_pct = 0
                    distance_text = "в зоне"
                    zone_type = "тест"
                elif current_price < fvg['price_min']:
                    distance_pct = ((fvg['price_min'] - current_price) / current_price) * 100
                    distance_text = f"выше на {distance_pct:.1f}%"
                    zone_type = "сопротивление сверху"
                else:  # current_price > fvg['price_max']
                    distance_pct = ((current_price - fvg['price_max']) / current_price) * 100
                    distance_text = f"ниже на {distance_pct:.1f}%"
                    zone_type = "поддержка снизу"
                
                # Логируем найденный FVG
                logger.info(f"    ✅ {tf_name} FVG: {fvg['price_min']:.6f}-{fvg['price_max']:.6f}, {distance_text}")
                
                # Форматируем цены зоны
                if fvg['price_min'] < 0.001:
                    zone_str = f"{fvg['price_min']:.6f}-{fvg['price_max']:.6f}"
                elif fvg['price_min'] < 0.1:
                    zone_str = f"{fvg['price_min']:.4f}-{fvg['price_max']:.4f}"
                else:
                    zone_str = f"{fvg['price_min']:.2f}-{fvg['price_max']:.2f}"
                
                # Формируем сигнал
                size_pct = fvg.get('size', 0)
                tf_ru = tf_names_ru.get(tf_name, tf_name)
                direction = "бычий" if fvg['type'] == 'bullish' else "медвежий"
                
                signal_text = (f"FVG {tf_short[tf_name]}: {zone_str} "
                            f"(размер {size_pct:.2f}% {dir_emoji[fvg['type']]} {zone_type}, {distance_text})")
                
                result['has_fvg'] = True
                result['signals'].append(signal_text)
                
                # Сохраняем зону для графиков и анализа
                all_zones.append({
                    'tf': tf_name,
                    'tf_short': tf_short[tf_name],
                    'tf_ru': tf_ru,
                    'min': fvg['price_min'],
                    'max': fvg['price_max'],
                    'type': fvg['type'],
                    'dir_emoji': dir_emoji[fvg['type']],
                    'size': size_pct,
                    'distance_pct': distance_pct,
                    'in_zone': in_zone,
                    'zone_type': zone_type,
                    'distance_text': distance_text,
                    'weight': tf_weights.get(tf_name, 1.0),
                    'strength': fvg['strength']
                })
                
                # Увеличиваем силу с весом таймфрейма
                result['strength'] += fvg['strength'] * tf_weights.get(tf_name, 1.0)

        # ===== ВСТАВЬТЕ ЭТОТ БЛОК ЗДЕСЬ =====
        # После сбора всех зон, перед фильтрацией
        logger.info(f"  📊 Всего найдено FVG: {len(all_zones)}")
        for zone in all_zones:
            # Добавляем расстояние для сортировки если еще нет
            if 'sort_distance' not in zone:
                if zone['in_zone']:
                    zone['sort_distance'] = 0
                elif zone['min'] > current_price:
                    zone['sort_distance'] = (zone['min'] - current_price) / current_price
                else:
                    zone['sort_distance'] = (current_price - zone['max']) / current_price
            
            logger.info(f"    FVG {zone['tf']}: {zone['min']:.6f}-{zone['max']:.6f}, расстояние {zone['sort_distance']*100:.1f}%")
        # ===== КОНЕЦ БЛОКА =====
        
        # ===== ФИЛЬТРАЦИЯ ПО РАССТОЯНИЮ ДЛЯ ПРИЧИН =====
        MAX_DISTANCE_PCT = 15.0
        DISTANCE_THRESHOLDS = {
            'monthly': 50.0,
            'weekly': 30.0,
            'daily': 20.0,
            'four_hourly': 15.0,
            'hourly': 10.0,
            'current': 5.0
        }

        filtered_zones = []
        for zone in all_zones:
            threshold = DISTANCE_THRESHOLDS.get(zone['tf'], MAX_DISTANCE_PCT)
            
            if zone['distance_pct'] > threshold:
                logger.info(f"    ⏭️ FVG {zone['tf']} пропущен - слишком далеко ({zone['distance_pct']:.1f}% > {threshold}%)")
                continue
            
            filtered_zones.append(zone)
            
            # Добавляем в причины
            signal_text = (f"FVG {zone['tf_short']}: {zone['min']:.4f}-{zone['max']:.4f} "
                        f"(размер {zone['size']:.2f}% {zone['type']}, {zone['distance_text']})")
            result['signals'].append(signal_text)
            result['strength'] += zone['strength'] * zone['weight']

        logger.info(f"  📊 Добавлено {len(result['signals'])} FVG в причины (из {len(all_zones)} найденных)")

        # ===== ФИЛЬТРАЦИЯ ДЛЯ ГРАФИКА: ТОЛЬКО БЛИЖАЙШИЕ =====
        if filtered_zones:
            # Добавляем расстояние для сортировки
            zones_with_distance = []
            for zone in filtered_zones:
                if zone['in_zone']:
                    zone['sort_distance'] = 0
                elif zone['min'] > current_price:
                    zone['sort_distance'] = (zone['min'] - current_price) / current_price
                else:
                    zone['sort_distance'] = (current_price - zone['max']) / current_price
                
                zones_with_distance.append(zone)
            
            # Сортируем по расстоянию
            zones_with_distance.sort(key=lambda z: z['sort_distance'])
            
            # Берем ТОЛЬКО 2 ближайшие зоны для графика
            result['zones'] = zones_with_distance[:2]
            
            logger.info(f"  🎨 Для графика отобрано {len(result['zones'])} ближайших FVG из {len(filtered_zones)}")
        
        # Ограничиваем силу 100%
        if result['strength'] > 100:
            result['strength'] = 100
        
        return result

    def _is_fvg_closed(self, df: pd.DataFrame, fvg: Dict) -> bool:
        """
        Проверка, закрыта ли зона FVG (БОЛЕЕ МЯГКАЯ ВЕРСИЯ)
        FVG считается закрытым, только если цена ПОЛНОСТЬЮ прошла через зону
        и закрепилась с другой стороны на 2+ свечах
        """
        last_idx = len(df) - 1
        start_idx = max(0, last_idx - 100)  # увеличили до 100 свечей
        
        close_count = 0
        for i in range(start_idx, last_idx):
            candle = df.iloc[i]
            
            if fvg['type'] == 'bullish':
                # Бычий FVG закрывается при падении НИЖЕ зоны
                if candle['close'] < fvg['price_min']:
                    close_count += 1
                    if close_count >= 2:  # нужно 2 подтверждения
                        return True
                else:
                    close_count = 0  # сбрасываем, если вышли из зоны
            else:
                # Медвежий FVG закрывается при росте ВЫШЕ зоны
                if candle['close'] > fvg['price_max']:
                    close_count += 1
                    if close_count >= 2:
                        return True
                else:
                    close_count = 0
        
        return False

    def analyze_ema_touch(self, df: pd.DataFrame, last: pd.Series) -> Dict:
        """Анализ касаний цены EMA уровней"""
        result = {'touches': [], 'signals': []}
        
        # Защита от пустых данных
        if df is None or df.empty or last is None:
            return result
        
        for period in [9, 14, 21, 28, 50, 100]:
            ema_col = f'ema_{period}'
            if ema_col not in df.columns:
                continue
            
            ema_value = last.get(ema_col)
            if pd.isna(ema_value):
                continue
            
            price = last.get('close')
            if pd.isna(price):
                continue
            
            distance = abs(price - ema_value) / price * 100
            
            if distance < 0.5:
                result['touches'].append(period)
                
                if price > ema_value:
                    result['signals'].append(f"📊 Цена у EMA {period} (поддержка)")
                else:
                    result['signals'].append(f"📊 Цена у EMA {period} (сопротивление)")
        
        return result

    def analyze_ema_touch_multi_timeframe(self, dataframes: Dict[str, pd.DataFrame], current_price: float) -> Dict:
        """
        Анализ касаний EMA на всех таймфреймах с настройками из конфига
        """
        from config import EMA_TOUCH_SETTINGS, TIMEFRAME_WEIGHTS
        
        result = {'touches': [], 'signals': [], 'strength': 0}
        
        if not EMA_TOUCH_SETTINGS.get('enabled', True):
            return result
        
        periods = EMA_TOUCH_SETTINGS.get('periods', [9, 14, 21, 28, 50, 100])
        distance_threshold = EMA_TOUCH_SETTINGS.get('distance_threshold', 0.5)
        max_signals = EMA_TOUCH_SETTINGS.get('max_signals', 3)
        weights = EMA_TOUCH_SETTINGS.get('weights', {})
        
        # Словарь для перевода таймфреймов
        tf_short = {
            '1m': '1м',
            '3m': '3м',
            '5m': '5м',
            'current': '15м',
            '30m': '30м',
            'hourly': '1ч',
            'four_hourly': '4ч',
            'daily': '1д',
            'weekly': '1н',
            'monthly': '1м'
        }
        
        # Приоритет таймфреймов (от старших к младшим)
        tf_order = ['monthly', 'weekly', 'daily', 'four_hourly', 'hourly', '30m', 'current', '5m', '3m', '1m']
        
        for tf_name in tf_order:
            if tf_name not in dataframes or dataframes[tf_name] is None:
                continue
            
            df = dataframes[tf_name]
            if df.empty:
                continue
            
            last = df.iloc[-1]
            tf_short_name = tf_short.get(tf_name, tf_name)
            weight = weights.get(tf_name, TIMEFRAME_WEIGHTS.get(tf_name, 5))
            
            for period in periods:
                ema_col = f'ema_{period}'
                if ema_col not in df.columns:
                    continue
                
                ema_value = last[ema_col]
                price = last['close']
                distance = abs(price - ema_value) / price * 100
                
                if distance < distance_threshold:
                    if price > ema_value:
                        touch_type = "поддержка"
                        direction_bias = "LONG"
                    else:
                        touch_type = "сопротивление"
                        direction_bias = "SHORT"
                    
                    # Сила касания (чем больше период, тем сильнее)
                    strength = int(weight * (period / 50))
                    
                    result['touches'].append({
                        'tf': tf_short_name,
                        'period': period,
                        'type': touch_type,
                        'distance': distance,
                        'strength': strength,
                        'price': price,
                        'ema': ema_value
                    })
                    
                    signal_text = f"📊 Цена у EMA {period} на {tf_short_name} ({touch_type}, {distance:.1f}%)"
                    result['signals'].append(signal_text)
                    result['strength'] += strength
                    
                    logger.info(f"    📊 {tf_name} EMA {period}: {touch_type} (дистанция {distance:.2f}%)")
        
        # Ограничиваем количество сигналов в причинах
        if len(result['signals']) > max_signals:
            result['signals'] = result['signals'][:max_signals]
        
        # Ограничиваем силу
        if result['strength'] > 100:
            result['strength'] = 100
        
        return result
    
    def calculate_volume_spike(self, df: pd.DataFrame) -> Dict:
        """Поиск свечей с аномальным объемом"""
        from config import VOLUME_ANALYSIS_SETTINGS
        
        settings = VOLUME_ANALYSIS_SETTINGS['spike_detector']
        if not settings['enabled']:
            return {'spike': False}
        
        lookback = settings['lookback']
        threshold = settings['threshold']
        
        if len(df) < lookback + 1:
            return {'spike': False}
        
        last_volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].iloc[-lookback-1:-1].mean()
        ratio = last_volume / avg_volume if avg_volume > 0 else 1
        
        if ratio > threshold:
            return {
                'spike': True,
                'ratio': ratio,
                'price': df['close'].iloc[-1],
                'direction': 'UP' if df['close'].iloc[-1] > df['open'].iloc[-1] else 'DOWN'
            }
        return {'spike': False}

    def calculate_volume_dispersion(self, df: pd.DataFrame, hours: int = 2) -> Dict:
        """Расчет дисперсии объема за указанный период"""
        from config import VOLUME_ANALYSIS_SETTINGS
        
        settings = VOLUME_ANALYSIS_SETTINGS['volume_dispersion']
        if not settings['enabled']:
            return {'dispersion': 1.0, 'interpretation': ''}
        
        # 4 свечи в час для 15м таймфрейма
        periods = hours * 4
        if len(df) < periods:
            periods = len(df)
        
        period_df = df.tail(periods)
        
        # Дисперсия объема
        volume_std = period_df['volume'].std()
        volume_mean = period_df['volume'].mean()
        volume_dispersion = volume_std / volume_mean if volume_mean > 0 else 1.0
        
        # Интерпретация
        high_threshold = settings['high_threshold']
        low_threshold = settings['low_threshold']
        
        if volume_dispersion > high_threshold:
            interpretation = f"🔥 Высокая дисперсия объема x{volume_dispersion:.1f}"
        elif volume_dispersion < low_threshold:
            interpretation = f"📊 Низкая дисперсия объема (накопление)"
        else:
            interpretation = f"📈 Средняя дисперсия объема x{volume_dispersion:.1f}"
        
        return {
            'dispersion': volume_dispersion,
            'interpretation': interpretation
        }

    def calculate_price_dispersion(self, df: pd.DataFrame, hours: int = 2) -> Dict:
        """Расчет ценовой дисперсии за указанный период"""
        from config import DISPERSION_ANALYSIS_SETTINGS
        
        if not DISPERSION_ANALYSIS_SETTINGS['enabled']:
            return {'dispersion': 0, 'interpretation': '', 'zones': []}
        
        # 4 свечи в час для 15м таймфрейма
        periods = hours * 4
        if len(df) < periods:
            periods = len(df)
        
        period_df = df.tail(periods)
        
        # Ценовая дисперсия
        price_std = period_df['close'].std()
        price_mean = period_df['close'].mean()
        price_dispersion = (price_std / price_mean) * 100 if price_mean > 0 else 0
        
        # Поиск зон высокой дисперсии
        zones = []
        if DISPERSION_ANALYSIS_SETTINGS['show_zones_on_chart']:
            window = 10  # 10 свечей для анализа
            for i in range(0, len(period_df) - window, window):
                window_df = period_df.iloc[i:i+window]
                window_std = window_df['close'].std()
                window_mean = window_df['close'].mean()
                window_disp = (window_std / window_mean) * 100 if window_mean > 0 else 0
                
                if window_disp > DISPERSION_ANALYSIS_SETTINGS['thresholds']['high']:
                    zones.append({
                        'min': window_df['low'].min(),
                        'max': window_df['high'].max(),
                        'strength': window_disp,
                        'start': window_df.index[0],
                        'end': window_df.index[-1]
                    })
        
        # Интерпретация
        high_threshold = DISPERSION_ANALYSIS_SETTINGS['thresholds']['high']
        low_threshold = DISPERSION_ANALYSIS_SETTINGS['thresholds']['low']
        
        if price_dispersion > high_threshold:
            interpretation = f"🔥 ВЫСОКАЯ ДИСПЕРСИЯ ({price_dispersion:.1f}%)"
        elif price_dispersion < low_threshold:
            interpretation = f"📊 НИЗКАЯ ДИСПЕРСИЯ ({price_dispersion:.1f}%)"
        else:
            interpretation = f"📈 СРЕДНЯЯ ДИСПЕРСИЯ ({price_dispersion:.1f}%)"
        
        return {
            'dispersion': price_dispersion,
            'interpretation': interpretation,
            'zones': zones[:DISPERSION_ANALYSIS_SETTINGS['max_zones']]
        }

    def find_sniper_entry(self, levels: List[Dict], current_price: float, df: pd.DataFrame) -> Optional[Dict]:
        """
        Поиск снайперской точки входа (лимитный ордер на уровне)
        """
        from config import SNIPER_ENTRY_SETTINGS
        
        if not SNIPER_ENTRY_SETTINGS['enabled']:
            return None
        
        for level in levels:
            # Пропускаем слабые уровни
            if level.get('strength', 0) < SNIPER_ENTRY_SETTINGS['long']['min_strength']:
                continue
            
            # LONG: покупка на поддержке
            if level['zone_type'] == 'поддержка':
                distance = ((current_price - level['price']) / current_price) * 100
                
                if distance <= SNIPER_ENTRY_SETTINGS['long']['distance_threshold']:
                    # Проверяем подтверждение
                    if SNIPER_ENTRY_SETTINGS['long']['confirmation_volume']:
                        volume_ratio = df['volume_ratio'].iloc[-1] if 'volume_ratio' in df.columns else 1.0
                        if volume_ratio < SNIPER_ENTRY_SETTINGS['long']['confirmation_volume']:
                            continue
                    
                    if SNIPER_ENTRY_SETTINGS['long']['confirmation_rsi']:
                        rsi = df['rsi'].iloc[-1] if 'rsi' in df.columns else 50
                        if rsi > SNIPER_ENTRY_SETTINGS['long']['confirmation_rsi']:
                            continue
                    
                    # Расчет цен для лимитного ордера
                    entry_price = level['price'] * (1 + SNIPER_ENTRY_SETTINGS['order']['price_offset'] / 100)
                    stop_loss = level['price'] * (1 - SNIPER_ENTRY_SETTINGS['order']['stop_loss_offset'] / 100)
                    take_profit = entry_price * (1 + SNIPER_ENTRY_SETTINGS['order']['take_profit'] / 100)
                    
                    return {
                        'type': 'LONG',
                        'level_price': level['price'],
                        'entry_price': entry_price,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'strength': level.get('strength', 50),
                        'message': f"🎯 Снайперский вход LONG: лимит {entry_price:.4f} (уровень {level['price']:.4f})"
                    }
            
            # SHORT: продажа на сопротивлении
            elif level['zone_type'] == 'сопротивление':
                distance = ((level['price'] - current_price) / current_price) * 100
                
                if distance <= SNIPER_ENTRY_SETTINGS['short']['distance_threshold']:
                    # Проверяем подтверждение
                    if SNIPER_ENTRY_SETTINGS['short']['confirmation_volume']:
                        volume_ratio = df['volume_ratio'].iloc[-1] if 'volume_ratio' in df.columns else 1.0
                        if volume_ratio < SNIPER_ENTRY_SETTINGS['short']['confirmation_volume']:
                            continue
                    
                    if SNIPER_ENTRY_SETTINGS['short']['confirmation_rsi']:
                        rsi = df['rsi'].iloc[-1] if 'rsi' in df.columns else 50
                        if rsi < SNIPER_ENTRY_SETTINGS['short']['confirmation_rsi']:
                            continue
                    
                    # Расчет цен для лимитного ордера
                    entry_price = level['price'] * (1 - SNIPER_ENTRY_SETTINGS['order']['price_offset'] / 100)
                    stop_loss = level['price'] * (1 + SNIPER_ENTRY_SETTINGS['order']['stop_loss_offset'] / 100)
                    take_profit = entry_price * (1 - SNIPER_ENTRY_SETTINGS['order']['take_profit'] / 100)
                    
                    return {
                        'type': 'SHORT',
                        'level_price': level['price'],
                        'entry_price': entry_price,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'strength': level.get('strength', 50),
                        'message': f"🎯 Снайперский вход SHORT: лимит {entry_price:.4f} (уровень {level['price']:.4f})"
                    }
        
        return None

    def generate_signal(self, dataframes: Dict[str, pd.DataFrame], metadata: Dict, symbol: str, exchange: str) -> Optional[Dict]:
        """
        Генерация торгового сигнала на основе всех индикаторов
        """
        logger.info(f"🔄 generate_signal начал работу для {symbol}")

        global BREAKOUT_CONFIRMATION_SETTINGS
        
        if 'current' not in dataframes or dataframes['current'].empty:
            logger.warning(f"⚠️ Нет current данных для {symbol}")
            return None
        
        df = dataframes['current']
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        
        logger.info(f"  📊 {symbol} - Цена: {last['close']}, RSI: {last['rsi'] if pd.notna(last['rsi']) else 'N/A'}")
        
        alignment = self.analyze_timeframe_alignment(dataframes)
        logger.info(f"  📊 {symbol} - Согласованность трендов: {alignment['trend_alignment']}%")
        
        confidence = 50
        reasons = []
        direction = 'NEUTRAL'
        signal_type = 'regular'
        
        # ===== RSI =====
        if pd.notna(last['rsi']):
            if last['rsi'] < INDICATOR_SETTINGS['rsi_oversold']:
                reasons.append(f"RSI перепродан ({last['rsi']:.1f})")
                confidence += INDICATOR_WEIGHTS['rsi']
            elif last['rsi'] > INDICATOR_SETTINGS['rsi_overbought']:
                reasons.append(f"RSI перекуплен ({last['rsi']:.1f})")
                confidence += INDICATOR_WEIGHTS['rsi']
        
        # ===== MACD =====
        if pd.notna(last['MACD_12_26_9']) and pd.notna(last['MACDs_12_26_9']):
            if last['MACD_12_26_9'] > last['MACDs_12_26_9'] and prev['MACD_12_26_9'] <= prev['MACDs_12_26_9']:
                reasons.append("Бычье пересечение MACD")
                confidence += INDICATOR_WEIGHTS['macd']
            elif last['MACD_12_26_9'] < last['MACDs_12_26_9'] and prev['MACD_12_26_9'] >= prev['MACDs_12_26_9']:
                reasons.append("Медвежье пересечение MACD")
                confidence += INDICATOR_WEIGHTS['macd']
        
        # ===== EMA =====
        if last['ema_9'] > last['ema_21'] and prev['ema_9'] <= prev['ema_21']:
            reasons.append("Бычье пересечение EMA (9/21)")
            confidence += INDICATOR_WEIGHTS['ema_cross_current']
        elif last['ema_9'] < last['ema_21'] and prev['ema_9'] >= prev['ema_21']:
            reasons.append("Медвежье пересечение EMA (9/21)")
            confidence += INDICATOR_WEIGHTS['ema_cross_current']
        
        # ===== ОБЪЕМ =====
        if last['volume_ratio'] > 1.5:
            reasons.append(f"Объем x{last['volume_ratio']:.1f} от нормы")
            confidence += INDICATOR_WEIGHTS['volume']
        
        # ===== VWAP =====
        if FEATURES['advanced']['vwap'] and 'vwap' in df.columns:
            vwap_value = last['vwap']
            price = last['close']
            
            # Умное форматирование в зависимости от размера числа
            if vwap_value < 0.0001:
                vwap_formatted = f"{vwap_value:.8f}".rstrip('0').rstrip('.')
            elif vwap_value < 0.001:
                vwap_formatted = f"{vwap_value:.6f}".rstrip('0').rstrip('.')
            elif vwap_value < 0.01:
                vwap_formatted = f"{vwap_value:.5f}".rstrip('0').rstrip('.')
            elif vwap_value < 0.1:
                vwap_formatted = f"{vwap_value:.4f}".rstrip('0').rstrip('.')
            elif vwap_value < 1:
                vwap_formatted = f"{vwap_value:.3f}".rstrip('0').rstrip('.')
            else:
                vwap_formatted = f"{vwap_value:.2f}"
            
            reasons.append(f"Цена {'выше' if price > vwap_value else 'ниже'} VWAP ({vwap_formatted})")
            confidence += 10
        
        # ===== СИГНАЛЫ ОТ СТАРШИХ ТАЙМФРЕЙМОВ =====
        for signal in alignment['signals']:
            reasons.append(signal)
            if "НЕДЕЛЬНЫЙ" in signal or "МЕСЯЧНЫЙ" in signal:
                confidence += INDICATOR_WEIGHTS['weekly_trend']
            elif "Дневной" in signal:
                confidence += INDICATOR_WEIGHTS['daily_trend']

        # ===== СОГЛАСОВАННОСТЬ ТРЕНДОВ =====
        if alignment['trend_alignment'] > 70:
            confidence += INDICATOR_WEIGHTS['trend_alignment']
        
        # ===== АНАЛИЗ ФИБОНАЧЧИ =====
        fib_analysis = None
        if self.fibonacci and FEATURES['advanced']['fibonacci']:
            logger.info(f"  🔍 {symbol} - Начинаю анализ Фибоначчи")
            fib_analysis = self.fibonacci.analyze_multi_timeframe(dataframes)
            if fib_analysis['has_confluence']:
                for signal in fib_analysis['signals']:
                    reasons.append(signal)
                confidence += fib_analysis['strength'] / 5
                logger.info(f"  ✅ {symbol} - Фибоначчи: найдено {len(fib_analysis['signals'])} сигналов")
        
        # ===== АНАЛИЗ НАКОПЛЕНИЯ =====
        accumulation_analysis = None
        if self.accumulation and FEATURES['advanced']['accumulation']:
            logger.info(f"  🔍 {symbol} - Начинаю анализ накопления")
            accumulation_analysis = self.accumulation.analyze(df)
            
            if accumulation_analysis.get('has_accumulation'):
                for signal in accumulation_analysis['signals']:
                    reasons.append(f"📦 {signal}")
                confidence += accumulation_analysis.get('strength', 0) / 5
                signal_type = 'accumulation'
                
                # Расчет потенциала
                potential = self.accumulation.calculate_potential(df, dataframes)
                if potential['has_potential']:
                    accumulation_analysis['potential'] = potential
                    for reason in potential['reasons']:
                        reasons.append(reason)
                    logger.info(f"  📈 {symbol} - Потенциал: {potential['target_pct']}%")
                
                if accumulation_analysis.get('direction'):
                    direction = accumulation_analysis['direction']
                logger.info(f"  ✅ {symbol} - Накопление: найдено {len(accumulation_analysis['signals'])} сигналов")
        
        # ===== АНАЛИЗ ТРЕНДОВЫХ ЛИНИЙ =====
        trendline_breakout = False
        trendline_warnings = []
        breakout_level = None  # ← ДОБАВИТЬ

        if FEATURES['advanced']['patterns']:
            logger.info(f"  🔍 {symbol} - Анализ трендовых линий")
            trend_analyzer = TrendLineAnalyzer()
            current_tf = TIMEFRAMES.get('current', '15m')
            
            # 1. Ищем уже случившиеся пробои
            trend_lines = trend_analyzer.find_trend_lines(df, touch_count=3)
            
            best_line = None
            max_touches = 0
            for line in trend_lines:
                if line['is_broken'] and line['touches'] > max_touches:
                    max_touches = line['touches']
                    best_line = line
                    breakout_level = line['current_level']  # ← ЗАПОМИНАЕМ
            
            if best_line:
                reasons.append(f"📈 Пробой наклонного сопротивления на {current_tf} ({best_line['touches']} касаний)")
                confidence += 20
                trendline_breakout = True
                signal_type = 'breakout'
                logger.info(f"  ✅ {symbol} - Обнаружен пробой тренда с {best_line['touches']} касаниями на {current_tf}")
                
                # ===== ДЕТЕКТОР ЛОЖНОГО ПРОБОЯ =====
                if breakout_level and last['close'] < breakout_level * 1.01:  # цена вернулась к уровню
                    reasons.append("⚠️ Цена вернулась к уровню после пробоя - возможен ложный пробой")
                    confidence -= 30
                    # Меняем направление на противоположное
                    if 'LONG' in direction or direction == 'LONG':
                        direction = 'SHORT 📉 (ловушка быков)'
                    logger.info(f"  ⚠️ {symbol} - Обнаружен потенциальный ложный пробой!")
        
        # ===== ПОДТВЕРЖДЕНИЕ ПРОБОЕВ =====
        if FEATURES['advanced']['patterns'] and BREAKOUT_CONFIRMATION_SETTINGS['enabled']:
            logger.info(f"  🔍 {symbol} - Проверка подтвержденных пробоев")
            
            from config import BREAKOUT_CONFIRMATION_SETTINGS
            
            current_tf = TIMEFRAMES.get('current', '15m')
            trend_analyzer = TrendLineAnalyzer()
            trend_lines = trend_analyzer.find_trend_lines(df, touch_count=3)
            
            for line in trend_lines:
                confirmed = self.breakout_tracker.check_breakout_confirmation(
                    symbol, current_tf, df, line, last['close'],
                    required_candles=BREAKOUT_CONFIRMATION_SETTINGS['required_candles'],
                    required_percent=BREAKOUT_CONFIRMATION_SETTINGS['required_percent'],
                    volume_confirmation=BREAKOUT_CONFIRMATION_SETTINGS['volume_confirmation'],
                    confirmation_mode=BREAKOUT_CONFIRMATION_SETTINGS['confirmation_mode']
                )
                
                if confirmed:
                    reasons.append(f"✅ {confirmed['message']}")
                    confidence += 30
                    signal_type = 'confirmed_breakout'
                    
                    # Определяем направление
                    if confirmed['direction'] == 'вверх':
                        direction = 'LONG 📈 (подтвержденный пробой)'
                    else:
                        direction = 'SHORT 📉 (подтвержденный пробой)'
                    
                    logger.info(f"  ✅ {symbol} - Подтвержденный пробой! +30 confidence")
                    break  # берем первый подтвержденный пробой

        # ===== АНАЛИЗ КОНВЕРГЕНЦИИ УРОВНЕЙ =====
        if FEATURES['advanced']['patterns']:
            logger.info(f"  🔍 {symbol} - Анализ конвергенции уровней")
            level_collector = LevelCollector()
            
            # Собираем все уровни
            all_levels = level_collector.collect_levels(dataframes, last['close'])
            
            # Ищем совпадающие уровни
            confluence_zones = level_collector.find_confluence_levels(all_levels, last['close'], tolerance=0.5)
            
            for zone in confluence_zones:
                reasons.append(f"🎯 {zone['source']}: {zone['zone_type']} на {zone['price']:.4f} (сила {zone['strength']:.0f}%)")
                
                if zone['direction'] == 'LONG' and direction != 'LONG':
                    # Сильный уровень поддержки снизу
                    confidence += zone['strength'] / 2
                    if zone['distance'] < 5:
                        reasons.append(f"⚠️ Близкая сильная поддержка ({zone['distance']:.1f}%)")
                elif zone['direction'] == 'SHORT' and direction != 'SHORT':
                    # Сильный уровень сопротивления сверху
                    confidence += zone['strength'] / 2
                    if zone['distance'] < 5:
                        reasons.append(f"⚠️ Близкое сильное сопротивление ({zone['distance']:.1f}%)")

        # ===== АНАЛИЗ КОНВЕРГЕНЦИИ И СИЛЫ УРОВНЕЙ =====
        if FEATURES['advanced']['patterns']:
            logger.info(f"  🔍 {symbol} - Анализ силы уровней")
            level_collector = LevelCollector()
            
            # Собираем все уровни
            all_levels = level_collector.collect_levels(dataframes, last['close'])
            
            # Ищем совпадающие уровни (конвергенцию)
            confluence_zones = level_collector.find_confluence_levels(all_levels, last['close'], tolerance=0.5)
            
            # Анализируем каждый сильный уровень
            for zone in confluence_zones[:3]:  # топ-3 уровня
                # Оценка силы уровня
                strength_score = level_collector.calculate_level_strength_score(
                    zone, df, last, alignment
                )
                
                # Добавляем в причины
                if strength_score['strength'] >= 70:
                    reasons.append(f"🔥 {zone['source']}: {zone['zone_type']} на {zone['price']:.4f}")
                else:
                    reasons.append(f"⭐ {zone['source']}: {zone['zone_type']} на {zone['price']:.4f}")
                
                for signal in strength_score['signals'][:3]:
                    reasons.append(f"   {signal}")
                
                reasons.append(f"   📊 Вероятность разворота: {strength_score['probability']:.0f}%")
                
                # Корректируем направление
                if strength_score['action'] in ['разворот', 'вероятный_разворот']:
                    if zone['direction'] == 'LONG':
                        if direction != 'LONG':
                            old_dir = direction
                            direction = 'LONG 📈 (разворот от сильного уровня)'
                            confidence += strength_score['strength'] / 3
                            reasons.append(f"🔄 Смена направления: {old_dir} → LONG (сильный уровень)")
                    elif zone['direction'] == 'SHORT':
                        if direction != 'SHORT':
                            old_dir = direction
                            direction = 'SHORT 📉 (разворот от сильного уровня)'
                            confidence += strength_score['strength'] / 3
                            reasons.append(f"🔄 Смена направления: {old_dir} → SHORT (сильный уровень)")
                
                # Если вероятность пробоя выше
                elif strength_score['action'] == 'возможный_пробой':
                    reasons.append(f"⚠️ Возможен пробой уровня (вероятность разворота {strength_score['probability']:.0f}%)")
                    confidence -= 10

                # ===== СНАЙПЕРСКИЕ ТОЧКИ ВХОДА =====
                if SNIPER_ENTRY_SETTINGS['enabled'] and confluence_zones:
                    sniper = self.find_sniper_entry(confluence_zones, last['close'], df)
                    if sniper:
                        reasons.append(sniper['message'])
                        confidence += sniper['strength'] / 5
                        
                        # Добавляем в результат для отображения
                        if 'sniper_entry' not in locals():
                            # Проверяем, что signal является словарем
                            if isinstance(signal, dict):
                                signal['sniper_entry'] = sniper
                            else:
                                logger.warning(f"⚠️ signal не является словарем, тип: {type(signal)}")
                        logger.info(f"  🎯 {symbol} - Найдена снайперская точка входа: {sniper['type']} по {sniper['entry_price']:.4f}")

         # ===== АНАЛИЗ НАКОПЛЕНИЯ ПОСЛЕ ПРОБОЯ =====
                if breakout_level and (last['close'] > breakout_level * 0.99 and last['close'] < breakout_level * 1.01):
                    # Цена тестирует уровень после пробоя
                    if last['volume_ratio'] > 1.5:
                        reasons.append("📊 Накопление на пробитом уровне")
                        confidence += 15  # добавляем уверенности
                        logger.info(f"  📊 {symbol} - Накопление на пробитом уровне (объем x{last['volume_ratio']:.1f})")
                    else:
                        reasons.append("⚠️ Слабое накопление - возможен ложный пробой")
                        confidence -= 20
                        logger.info(f"  ⚠️ {symbol} - Слабое накопление на пробитом уровне")
        
        # ===== FVG МУЛЬТИТАЙМФРЕЙМОВЫЙ АНАЛИЗ =====
        fvg_analysis = {'has_fvg': False, 'signals': [], 'zones': []}
        if FEATURES['advanced']['smart_money']:
            logger.info(f"  🔍 {symbol} - Анализ FVG на всех таймфреймах")
            fvg_analysis = self.analyze_fvg_multi_timeframe(dataframes, last['close'])
            if fvg_analysis['has_fvg']:
                for signal_text in fvg_analysis['signals']:
                    reasons.append(signal_text)
                confidence += fvg_analysis['strength'] / 5
                logger.info(f"  ✅ {symbol} - Найдено FVG: {len(fvg_analysis['signals'])} на разных ТФ")
        
        # ===== АНАЛИЗ КАСАНИЙ EMA =====
        ema_touch = self.analyze_ema_touch(df, last)
        for signal in ema_touch['signals']:
            reasons.append(signal)
            confidence += 5  # небольшой бонус
        
        # ===== АНАЛИЗ КАСАНИЙ EMA НА ВСЕХ ТАЙМФРЕЙМАХ =====
        ema_touch_analysis = self.analyze_ema_touch_multi_timeframe(dataframes, last['close'])
        if ema_touch_analysis['signals']:
            for signal in ema_touch_analysis['signals']:
                reasons.append(signal)
            confidence += ema_touch_analysis['strength'] / 10
            logger.info(f"  ✅ {symbol} - Найдено {len(ema_touch_analysis['signals'])} касаний EMA")        
        
        # ===== АНАЛИЗ ОБЪЕМОВ =====
        if VOLUME_ANALYSIS_SETTINGS['enabled']:
            logger.info(f"  🔍 {symbol} - Анализ объемов")
            
            # 1. Детектор аномальных свечей
            volume_spike = self.calculate_volume_spike(df)
            if volume_spike['spike']:
                reasons.append(f"🔥 Аномальный объем x{volume_spike['ratio']:.1f}")
                confidence += VOLUME_ANALYSIS_SETTINGS['spike_detector']['weight']
                logger.info(f"  🔥 {symbol} - Объемный всплеск x{volume_spike['ratio']:.1f}")
            
            # 2. Дисперсия объема
            vol_dispersion = self.calculate_volume_dispersion(df, hours=2)
            if vol_dispersion['dispersion'] != 1.0:
                reasons.append(vol_dispersion['interpretation'])
                if vol_dispersion['dispersion'] > VOLUME_ANALYSIS_SETTINGS['volume_dispersion']['high_threshold']:
                    confidence += VOLUME_ANALYSIS_SETTINGS['volume_dispersion']['weight']
            
            # 3. Имбаланс buy/sell (если включен)
            if VOLUME_ANALYSIS_SETTINGS['imbalance']['enabled'] and self.imbalance:
                imbalance_result = self.imbalance.analyze(dataframes)
                if imbalance_result['has_imbalance']:
                    for signal in imbalance_result['signals']:
                        reasons.append(signal)
                    confidence += VOLUME_ANALYSIS_SETTINGS['imbalance']['weight']
                    logger.info(f"  📊 {symbol} - Имбаланс: {len(imbalance_result['signals'])} сигналов")

        # ===== АНАЛИЗ ДИСПЕРСИИ =====
        if DISPERSION_ANALYSIS_SETTINGS['enabled']:
            logger.info(f"  🔍 {symbol} - Анализ дисперсии")
            
            # Анализируем за разные периоды
            dispersion_zones = []
            for hours, name in [(1, 'час'), (2, 'часа'), (4, 'часа')]:
                dispersion = self.calculate_price_dispersion(df, hours=hours)
                if dispersion['dispersion'] > 0:
                    reasons.append(f"📊 Дисперсия за {name}: {dispersion['interpretation']}")
                    dispersion_zones = dispersion.get('zones', [])
                    
                    # Влияние на уверенность
                    if dispersion['dispersion'] > DISPERSION_ANALYSIS_SETTINGS['thresholds']['high']:
                        confidence += DISPERSION_ANALYSIS_SETTINGS['weights']['high']
                    elif dispersion['dispersion'] < DISPERSION_ANALYSIS_SETTINGS['thresholds']['low']:
                        confidence += DISPERSION_ANALYSIS_SETTINGS['weights']['low']
                    else:
                        confidence += DISPERSION_ANALYSIS_SETTINGS['weights']['medium']
                    break  # берем только один период для простоты
        
        # ===== ФАНДИНГ =====
        funding = metadata.get('funding_rate')
        if funding is not None and funding != 0:
            funding_pct = funding * 100
            if funding > 0.001:
                reasons.append(f"Позитивный фандинг ({funding_pct:.4f}%)")
            elif funding < -0.001:
                reasons.append(f"Негативный фандинг ({funding_pct:.4f}%)")
        
        # Подсчет медвежьих/бычьих сигналов для DUMP/PUMP
        bearish_score = 0
        bullish_score = 0
        
        if 'Медвежье пересечение MACD' in str(reasons):
            bearish_score += 30
        if 'Бычье пересечение MACD' in str(reasons):
            bullish_score += 30
            
        if 'Цена ниже VWAP' in str(reasons):
            bearish_score += 20
        if 'Цена выше VWAP' in str(reasons):
            bullish_score += 20
            
        if 'НЕДЕЛЬНЫЙ ТРЕНД НИСХОДЯЩИЙ' in str(reasons):
            bearish_score += 40
        if 'НЕДЕЛЬНЫЙ ТРЕНД ВОСХОДЯЩИЙ' in str(reasons):
            bullish_score += 40
        
        logger.info(f"  📊 {symbol} - Бычий счет: {bullish_score}, Медвежий счет: {bearish_score}")

        # ===== ОПРЕДЕЛЕНИЕ БАЗОВОГО НАПРАВЛЕНИЯ =====
        bullish_keywords = ['перепродан', 'Бычье', 'восходящий', 'негативный фандинг', 'выше VWAP', 'пробой']
        bearish_keywords = ['перекуплен', 'Медвежье', 'нисходящий', 'позитивный фандинг', 'ниже VWAP']
        
        bullish = sum(1 for r in reasons if any(k in r for k in bullish_keywords))
        bearish = sum(1 for r in reasons if any(k in r for k in bearish_keywords))
        
        # Базовое направление от индикаторов
        base_direction = 'NEUTRAL'
        if accumulation_analysis and accumulation_analysis.get('direction') and accumulation_analysis.get('has_accumulation'):
            base_direction = accumulation_analysis['direction']
        if trendline_breakout:
            if alignment['weekly_trend'] == 'НИСХОДЯЩИЙ' and last['rsi'] > 70:
                # Пробой вверх при медвежьем тренде и перекупленности = ЛОВУШКА!
                base_direction = 'SHORT 📉 (ловушка быков)'
                reasons.append("⚠️ Пробой вверх при медвежьем тренде - возможен ложный пробой")
                confidence -= 20
            else:
                base_direction = 'LONG'
        elif bullish > bearish and confidence >= MIN_CONFIDENCE:
            if alignment['weekly_trend'] == 'ВОСХОДЯЩИЙ':
                base_direction = 'Разворот LONG'
            else:
                base_direction = 'LONG'
        elif bearish > bullish and confidence >= MIN_CONFIDENCE:
            if alignment['weekly_trend'] == 'НИСХОДЯЩИЙ':
                base_direction = 'Разворот SHORT'
            else:
                base_direction = 'SHORT'
        
        direction = base_direction
        logger.info(f"  🎯 [1] Направление после базового определения: {direction}")
        
        # ===== АНАЛИЗ FVG ДЛЯ КОРРЕКЦИИ УВЕРЕННОСТИ =====
        if fvg_analysis['has_fvg'] and 'zones' in fvg_analysis:
            current_price = last['close']
            fvg_above = 0
            fvg_below = 0
            fvg_in_zone = 0
            
            for zone in fvg_analysis['zones']:
                if zone['in_zone']:
                    fvg_in_zone += 1
                    reasons.append(f"🎯 Цена в FVG {zone['tf_short']}: {zone['min']:.4f}-{zone['max']:.4f}")
                    confidence += 20
                    
                    if trendline_breakout:
                        confidence += 15
                        reasons.append(f"✅ Пробой из FVG зоны {zone['tf_short']}")
                    
                elif zone['min'] > current_price:
                    fvg_above += 1
                    if direction == 'LONG':
                        target_str = f"{zone['min']:.6f}" if zone['min'] < 0.001 else f"{zone['min']:.4f}"
                        reasons.append(f"🎯 Цель: FVG {zone['tf_short']} {target_str} (+{zone['distance_pct']:.1f}%)")
                else:
                    fvg_below += 1
                    if direction == 'SHORT':
                        target_str = f"{zone['max']:.6f}" if zone['max'] < 0.001 else f"{zone['max']:.4f}"
                        reasons.append(f"🎯 Цель: FVG {zone['tf_short']} {target_str} (-{zone['distance_pct']:.1f}%)")
            
            # ===== НОВАЯ ЛОГИКА СМЕНЫ НАПРАВЛЕНИЯ =====
    
            # Если мы в LONG, но сверху 3+ зон FVG - сильное сопротивление
            if direction == 'LONG' and fvg_above >= 3:
                old_direction = direction
                direction = 'SHORT 📉 (сильное сопротивление FVG)'
                logger.info(f"  🎯 [2] Смена направления в FVG: {old_direction} → {direction}")
                reasons.append(f"🔻 Смена направления: {old_direction} → SHORT (FVG сверху)")
                confidence += 25
                logger.info(f"  🔄 Смена направления из-за {fvg_above} FVG сверху")
            
            # Если мы в SHORT, но снизу 3+ зон FVG - сильная поддержка
            elif direction == 'SHORT' and fvg_below >= 3:
                old_direction = direction
                direction = 'LONG 📈 (сильная поддержка FVG)'
                logger.info(f"  🎯 [2] Смена направления в FVG: {old_direction} → {direction}")
                reasons.append(f"🔺 Смена направления: {old_direction} → LONG (FVG снизу)")
                confidence += 25
                logger.info(f"  🔄 Смена направления из-за {fvg_below} FVG снизу")
            
            # Если цена в зоне FVG - это сильный уровень
            if fvg_in_zone > 0:
                if direction == 'LONG':
                    reasons.append(f"✅ Подтверждение LONG - цена в зоне FVG")
                    confidence += 15
                else:
                    reasons.append(f"✅ Подтверждение SHORT - цена в зоне FVG")
                    confidence += 15
            
            # ===== КОНЕЦ НОВОГО БЛОКА =====                                                    

            # Склонение для "зона/зоны/зон"
            if direction == 'LONG' and fvg_above > 0:
                zone_word = "зона" if fvg_above == 1 else "зоны" if fvg_above <= 4 else "зон"
                reasons.append(f"⚠️ {fvg_above} {zone_word} FVG сверху (сопротивление)")
                confidence -= fvg_above * 3
            
            if direction == 'SHORT' and fvg_below > 0:
                zone_word = "зона" if fvg_below == 1 else "зоны" if fvg_below <= 4 else "зон"
                reasons.append(f"⚠️ {fvg_below} {zone_word} FVG снизу (поддержка)")
                confidence -= fvg_below * 3
        
        logger.info(f"  🎯 [4] Направление после FVG анализа: {direction}")

        logger.info(f"  📊 {symbol} - Направление: {direction}, Уверенность: {confidence}")
        
        logger.info(f"  🎯 [5] Направление перед проверкой NEUTRAL: {direction}")

        if direction == 'NEUTRAL':
            logger.info(f"⏭️ NEUTRAL сигнал для {symbol}")
            return None
        
        logger.info(f"  🎯 НАПРАВЛЕНИЕ ПЕРЕД РАСЧЕТОМ ЦЕЛЕЙ: {direction}")

        # ===== РАСЧЕТ ЦЕЛЕЙ ПО ATR =====
        atr = last['atr'] if pd.notna(last['atr']) else (last['high'] - last['low']) * 0.3
        current_price = last['close']
        targets = {}
        
        if 'LONG' in direction:
            targets['target_1'] = current_price + atr * ATR_SETTINGS['long_target_1_mult']
            targets['target_2'] = current_price + atr * ATR_SETTINGS['long_target_2_mult']
            targets['stop_loss'] = current_price - atr * ATR_SETTINGS['long_stop_loss_mult']
        else:
            targets['target_1'] = current_price - atr * ATR_SETTINGS['short_target_1_mult']
            targets['target_2'] = current_price - atr * ATR_SETTINGS['short_target_2_mult']
            targets['stop_loss'] = current_price + atr * ATR_SETTINGS['short_stop_loss_mult']
        
        logger.info(f"  🎯 НАПРАВЛЕНИЕ ПОСЛЕ РАСЧЕТА ЦЕЛЕЙ: {direction}")

        # Округление целей
        for key in ['target_1', 'target_2', 'stop_loss']:
            if current_price < 0.0001:
                targets[key] = round(targets[key], 8)
            elif current_price < 0.001:
                targets[key] = round(targets[key], 6)
            elif current_price < 0.01:
                targets[key] = round(targets[key], 5)
            elif current_price < 0.1:
                targets[key] = round(targets[key], 4)
            elif current_price < 1:
                targets[key] = round(targets[key], 3)
            else:
                targets[key] = round(targets[key], 2)
        
        logger.info(f"  📈 {symbol} - ATR: {atr}, Цели: {targets}")

        # ===== РАСЧЕТ ЗОН ДОП.ВХОДА =====
        entry_zones = []
        current_tf = TIMEFRAMES.get('current', '15m')
        
        # Получаем данные текущего ТФ для расчета зон
        if 'current' in dataframes and dataframes['current'] is not None:
            df_current = dataframes['current']
            
            if 'LONG' in direction:
                # Для LONG — ищем локальные минимумы
                # Зона 1: минимум за 20 свечей
                zone1 = df_current['low'].tail(20).min()
                # Зона 2: минимум за 50 свечей
                zone2 = df_current['low'].tail(50).min()
                entry_zones = [zone1, zone2]
            else:
                # Для SHORT — ищем локальные максимумы
                # Зона 1: максимум за 20 свечей
                zone1 = df_current['high'].tail(20).max()
                # Зона 2: максимум за 50 свечей
                zone2 = df_current['high'].tail(50).max()
                entry_zones = [zone1, zone2]
        
        # Форматируем зоны для отображения
        formatted_zones = []
        for zone in entry_zones:
            if current_price < 0.0001:
                formatted_zones.append(f"{zone:.8f}".rstrip('0').rstrip('.'))
            elif current_price < 0.001:
                formatted_zones.append(f"{zone:.6f}".rstrip('0').rstrip('.'))
            elif current_price < 0.01:
                formatted_zones.append(f"{zone:.5f}".rstrip('0').rstrip('.'))
            elif current_price < 0.1:
                formatted_zones.append(f"{zone:.4f}".rstrip('0').rstrip('.'))
            elif current_price < 1:
                formatted_zones.append(f"{zone:.3f}".rstrip('0').rstrip('.'))
            else:
                formatted_zones.append(f"{zone:.2f}".rstrip('0').rstrip('.'))
        
        # ===== ФОРМИРОВАНИЕ РЕЗУЛЬТАТА =====
        result = {
            'symbol': symbol,
            'exchange': exchange,
            'price': current_price,
            'direction': direction,
            'atr': atr,
            'signal_type': signal_type,
            'signal_power': self._get_power_text(confidence),
            'confidence': round(confidence, 1),
            'signal_strength': round((confidence + alignment['trend_alignment']) / 2, 1),
            'reasons': reasons[:8],
            'funding_rate': metadata.get('funding_rate', 0),
            'volume_24h': metadata.get('volume_24h', 0),
            'price_change_24h': metadata.get('price_change_24h', 0),
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'alignment': alignment,
            'bearish_score': bearish_score,  # ← ДОБАВИТЬ
            'bullish_score': bullish_score,  # ← ДОБАВИТЬ (опционально)
            'entry_zones': formatted_zones,
            **targets
        }
        
        # Добавляем зоны FVG для графика
        if fvg_analysis['has_fvg'] and 'zones' in fvg_analysis:
            result['fvg_zones'] = fvg_analysis['zones']
            logger.info(f"  🎨 Добавлено {len(fvg_analysis['zones'])} FVG зон для графика")
        
        # Добавляем зоны дисперсии для графика
        if DISPERSION_ANALYSIS_SETTINGS['enabled'] and 'dispersion_zones' in locals() and dispersion_zones:
            result['dispersion_zones'] = dispersion_zones
            logger.info(f"  🎨 Добавлено {len(dispersion_zones)} зон дисперсии для графика")

        if fib_analysis:
            result['fibonacci'] = fib_analysis
        if accumulation_analysis:
            result['accumulation'] = accumulation_analysis
        
        logger.info(f"✅ generate_signal успешно завершен для {symbol}")
        return result
    
    def _get_power_text(self, confidence: float) -> str:
        """Определение текста силы сигнала по уверенности"""
        if confidence >= 85:
            return "🔥🔥🔥 ОЧЕНЬ СИЛЬНЫЙ"
        elif confidence >= 70:
            return "🔥🔥 СИЛЬНЫЙ"
        elif confidence >= 55:
            return "🔥 СРЕДНИЙ"
        elif confidence >= 40:
            return "📊 СЛАБЫЙ"
        else:
            return "👀 НАБЛЮДЕНИЕ"

# ============== БЫСТРЫЙ ПАМП-СКАНЕР ==============

class FastPumpScanner:
    def __init__(self, fetcher: BaseExchangeFetcher, settings: Dict = None, analyzer=None, telegram_bot=None, chart_generator=None):
        self.fetcher = fetcher
        self.settings = settings or PUMP_SCAN_SETTINGS
        self.analyzer = analyzer
        self.telegram_bot = telegram_bot  # ✅ Добавляем telegram_bot
        self.chart_generator = chart_generator  # ✅ Добавляем chart_generator
        self.threshold = self.settings.get('threshold', 3.0)
        self.instant_threshold = self.settings.get('instant_threshold', 1.0)  # Снижено до 1%
        self.shitcoin_instant_threshold = self.settings.get('shitcoin_instant_threshold', 0.8)  # Для щиткоинов 0.8%
        self.shitcoin_volume_threshold = self.settings.get('shitcoin_volume_threshold', 1_000_000)  # 1M$ = порог щиткоина
        self.timeframes = self.settings.get('timeframes', ['1m', '3m', '5m', '15m', '30m'])
        self.max_pairs = self.settings.get('max_pairs_to_scan', 600)
        self.websocket_top_pairs = self.settings.get('websocket_top_pairs', 100)
        self.last_pump_signals = {}
        self.cache = CacheManager(ttl=30)
        self.ws_signals_sent = set()  # отслеживаем отправленные через WebSocket сигналы
        # self.batch_size = PUMP_SCAN_SETTINGS.get('batch_size', 100)
        # self.delay_between_batches = PUMP_SCAN_SETTINGS.get('delay_between_batches', 0.1)
        
        # WebSocket менеджер
        try:
            from websocket_manager import BingXWebSocketManager
            self.ws_manager = BingXWebSocketManager(
                os.getenv('BINGX_API_KEY'),
                os.getenv('BINGX_SECRET_KEY')
            )
            self.websocket_available = True
            logger.info("✅ WebSocket менеджер инициализирован")
        except ImportError as e:
            logger.warning(f"⚠️ WebSocketManager не инициализирован: {e}")
            self.ws_manager = None
            self.websocket_available = False
        
        self.batch_size = PERFORMANCE_SETTINGS.get('pump_batch_size', 50)
        self.delay_between_batches = PERFORMANCE_SETTINGS.get('delay_between_batches', 0.5)
        self.websocket_reconnect_delay = self.settings.get('websocket_reconnect_delay', 5)
        
        # Очередь для быстрых сигналов
        self.instant_signals_queue = asyncio.Queue()
        
        logger.info(f"✅ FastPumpScanner инициализирован (WebSocket: {self.websocket_available})")
        logger.info(f"   Пороги: мейджоры {self.instant_threshold}%, щиткоины {self.shitcoin_instant_threshold}%")
    
    async def start_websocket_monitoring(self, symbols: List[str]):
        """
        Запуск WebSocket мониторинга с приоритетом на щиткоины
        """
        if not self.websocket_available or not self.ws_manager:
            logger.info("WebSocket мониторинг недоступен, используется REST API")
            return
        
        # Получаем щиткоины с малым объемом
        shitcoins = await self._get_volatile_shitcoins(symbols)
        
        # Берем топ-5 мейджоров для контроля
        majors = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'BNB/USDT:USDT', 'SOL/USDT:USDT', 'XRP/USDT:USDT']
        
        # Объединяем: сначала все щиткоины, потом мейджоры
        all_priority = shitcoins + [m for m in majors if m not in shitcoins]
        
        # Ограничиваем до 100 пар
        priority_symbols = all_priority[:self.websocket_top_pairs]
        
        shitcoin_count = sum(1 for s in priority_symbols if s in shitcoins)
        major_count = len(priority_symbols) - shitcoin_count
        
        logger.info(f"🎯 WebSocket мониторинг: {len(priority_symbols)} пар")
        logger.info(f"   - Щиткоины: {shitcoin_count} (объем < {self.shitcoin_volume_threshold/1_000_000:.1f}M$)")
        logger.info(f"   - Мейджоры: {major_count}")

        # Запускаем WebSocket с callback
        await self.ws_manager.connect_ticker_stream(
            priority_symbols,
            self.handle_instant_signal
        )
        
        # Запускаем обработчик очереди
        asyncio.create_task(self.process_instant_signals())
        logger.info(f"📡 WebSocket мониторинг запущен")
    
    async def _get_volatile_shitcoins(self, all_symbols: List[str]) -> List[str]:
        """
        Определение самых волатильных щиткоинов на основе объема
        """
        shitcoins = []
        volumes = []
        
        logger.info("🔍 Сканирую щиткоины...")
        
        # Черный список мейджоров
        blacklist = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'DOT', 'LINK', 'MATIC', 'AVAX', 'UNI', 'SHIB']
        
        # Проверяем объемы (берем первые 300 пар для скорости)
        for symbol in all_symbols[:300]:
            try:
                coin = symbol.split('/')[0].upper()
                
                # Пропускаем мейджоры
                if coin in blacklist:
                    continue
                
                ticker = await self.fetcher.fetch_ticker(symbol)
                volume = ticker.get('volume_24h', 0)
                
                # Если объем меньше порога - это щиткоин
                if volume < self.shitcoin_volume_threshold:
                    shitcoins.append(symbol)
                    volumes.append((symbol, volume))
            except Exception as e:
                continue
        
        # Сортируем по объему (от самых маленьких - самых волатильных)
        volumes.sort(key=lambda x: x[1])
        top_shitcoins = [s for s, v in volumes[:150]]  # берем 150 самых маленьких
        
        logger.info(f"🎯 Найдено {len(top_shitcoins)} щиткоинов с объемом < {self.shitcoin_volume_threshold/1_000_000:.1f}M$")
        return top_shitcoins
    
    async def handle_instant_signal(self, signal_type: str, symbol: str, price: float, movement: Dict):
        """
        Обработка мгновенного сигнала от WebSocket с гибкими настройками
        """
        try:
            from config import PUMP_MECHANISM_SETTINGS
            
            mode = PUMP_MECHANISM_SETTINGS.get('mode', 'new_only')
            
            # Определяем, щиткоин или нет
            is_shitcoin = False
            try:
                ticker = await self.fetcher.fetch_ticker(symbol)
                volume = ticker.get('volume_24h', 1_000_000)
                if volume < self.shitcoin_volume_threshold:
                    is_shitcoin = True
            except:
                pass
            
            # ===== НОВЫЙ МЕХАНИЗМ (уже проверил в _check_instant_movement) =====
            new_passed = True  # новый механизм уже проверил движение
            
            # ===== СТАРЫЙ МЕХАНИЗМ (проверяем порог) =====
            old_passed = False
            if mode in ['old_only', 'both']:
                if is_shitcoin:
                    threshold = self.settings.get('shitcoin_instant_threshold', 1.5)
                else:
                    threshold = self.settings.get('instant_threshold', 2.0)
                
                if abs(movement['change_percent']) >= threshold:
                    old_passed = True
            
            # ===== ЛОГИКА ОТПРАВКИ =====
            should_send = False
            
            if mode == 'new_only':
                should_send = new_passed
            elif mode == 'old_only':
                should_send = old_passed
            elif mode == 'both':
                both_settings = PUMP_MECHANISM_SETTINGS.get('both_settings', {})
                if both_settings.get('require_both', False):
                    should_send = new_passed and old_passed  # нужны оба
                else:
                    should_send = new_passed or old_passed  # достаточно одного
            
            if should_send:
                logger.info(f"⚡ {symbol}: {movement['change_percent']:+.1f}% за {movement['time_window']:.1f} сек")
                
                await self.instant_signals_queue.put({
                    'symbol': symbol,
                    'price': price,
                    'movement': movement,
                    'is_shitcoin': is_shitcoin,
                    'time': datetime.now()
                })
            else:
                logger.debug(f"⏭️ {symbol}: движение {movement['change_percent']:.1f}% не прошло фильтр")
                
        except Exception as e:
            logger.error(f"Ошибка обработки мгновенного сигнала: {e}")
    
    async def process_instant_signals(self):
        """
        Обработка очереди мгновенных сигналов
        """
        while True:
            try:
                signal_data = await self.instant_signals_queue.get()
                
                # Отправляем быстрый предварительный сигнал
                await self.send_flash_signal(signal_data)
                
                # Запускаем полный анализ в фоне
                asyncio.create_task(self.confirm_signal(signal_data))
                
            except Exception as e:
                logger.error(f"Ошибка обработки очереди: {e}")
                await asyncio.sleep(1)
    
    async def send_flash_signal(self, signal_data: Dict):
        """
        Отправка быстрого предварительного сигнала (0-2 секунды)
        """
        symbol = signal_data['symbol']

        # ✅ Запоминаем, что сигнал отправлен
        if not hasattr(self, 'ws_signals_sent'):
            self.ws_signals_sent = set()
        
        self.ws_signals_sent.add(symbol)
        # Через 60 секунд удаляем из памяти
        asyncio.create_task(self._remove_from_ws_cache(symbol, 60))

        movement = signal_data['movement']
        coin = symbol.split('/')[0].replace('USDT', '')
        is_shitcoin = signal_data.get('is_shitcoin', False)
        
        # Эмодзи для щиткоинов - ⚡, для мейджоров - 🚀
        if is_shitcoin:
            direction_emoji = "⚡"
            coin_type = " [ЩИТКОИН]"
        else:
            direction_emoji = "🚀" if movement['change_percent'] > 0 else "📉"
            coin_type = ""
        
        msg = (
            f"{direction_emoji} <code>{coin}</code>{coin_type} {movement['change_percent']:+.1f}% за {movement['time_window']:.1f} сек\n"
            f"⏳ Полный анализ через 3-5 секунд...\n"
            f"💰 Цена: {signal_data['price']:.6f}"
        )
        
        # Создаем простую клавиатуру
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"📋 Копировать {coin}", callback_data=f"copy_{coin}")
        ]])
        
        # Отправляем в памп-группу
        try:
            await self.fetcher.telegram_bot.send_message(
                chat_id=PUMP_CHAT_ID,
                text=msg,
                parse_mode='HTML',
                reply_markup=keyboard
            )
            logger.info(f"⚡ Отправлен мгновенный сигнал для {symbol} (щиткоин: {is_shitcoin})")
        except Exception as e:
            logger.error(f"Ошибка отправки мгновенного сигнала: {e}")
    
    async def _remove_from_ws_cache(self, symbol: str, delay: int):
        """Удаление символа из кэша WebSocket сигналов"""
        await asyncio.sleep(delay)
        if hasattr(self, 'ws_signals_sent') and symbol in self.ws_signals_sent:
            self.ws_signals_sent.remove(symbol)
            logger.info(f"🗑️ {symbol} удален из кэша WebSocket сигналов")

    async def confirm_signal(self, signal_data: Dict):
        """
        Подтверждение сигнала полным анализом
        """
        symbol = signal_data['symbol']
        
        # Ждем немного для накопления данных
        await asyncio.sleep(3)
        
        try:
            # Загружаем данные для полного анализа
            dataframes = {}
            for tf_name, tf_value in TIMEFRAMES.items():
                limit_tf = 200 if tf_name == 'current' else 100
                df_tf = await self.fetcher.fetch_ohlcv(symbol, tf_value, limit_tf)
                if df_tf is not None and not df_tf.empty:
                    df_tf = self.analyzer.calculate_indicators(df_tf)
                    dataframes[tf_name] = df_tf
            
            if not dataframes:
                logger.warning(f"⚠️ Нет данных для подтверждения {symbol}")
                return
            
            # Получаем метаданные
            funding = await self.fetcher.fetch_funding_rate(symbol)
            ticker = await self.fetcher.fetch_ticker(symbol)
            
            metadata = {
                'funding_rate': funding,
                'volume_24h': ticker.get('volume_24h'),
                'price_change_24h': ticker.get('percentage')
            }
            
            # Генерируем полный сигнал
            signal = self.analyzer.generate_signal(dataframes, metadata, symbol, self.fetcher.name)
            
            if signal and 'NEUTRAL' not in signal['direction']:
                # Добавляем информацию о быстром движении
                signal['pump_dump'] = [{
                    'change_percent': signal_data['movement']['change_percent'],
                    'time_window': signal_data['movement']['time_window'],
                    'start_price': signal_data['movement']['start_price'],
                    'end_price': signal_data['movement']['end_price']
                }]
                
                if signal_data['movement']['change_percent'] > 0:
                    signal['signal_type'] = "PUMP"
                else:
                    signal['signal_type'] = "DUMP"
                
                # Убедимся, что funding_rate не потерялся
                signal['funding_rate'] = funding
                
                # Отправляем подтвержденный сигнал
                contract_info = await self.fetcher.fetch_contract_info(symbol)
                msg, keyboard = self.format_pump_message(signal, contract_info)
                
                # Отправляем подтвержденный сигнал
                try:
                    await self.fetcher.telegram_bot.send_message(
                        chat_id=PUMP_CHAT_ID,
                        text=f"✅ ПОДТВЕРЖДЕНО\n\n{msg}",
                        parse_mode='HTML',
                        reply_markup=keyboard
                    )
                    logger.info(f"✅ Подтвержден сигнал для {symbol}")
                except Exception as e:
                    logger.error(f"Ошибка отправки подтвержденного сигнала: {e}")
                
        except Exception as e:
            logger.error(f"❌ Ошибка подтверждения сигнала {symbol}: {e}")
    
    async def scan_pair(self, pair: str) -> Optional[Dict]:
        """
        Сканирование одной пары (для параллельного вызова REST API)
        """
        try:
            # Проверяем кэш
            cache_key = f"{pair}_pump"
            cached = self.cache.get(cache_key)
            if cached:
                return cached
            
            for tf in self.timeframes:
                limit = 20
                df = await self.fetcher.fetch_ohlcv(pair, tf, limit=limit)
                
                if df is None or len(df) < 10:
                    continue
                
                bars_ago = 1
                minutes = self._timeframe_to_minutes(tf)
                
                start_price = df['close'].iloc[-bars_ago-1]
                current_price = df['close'].iloc[-1]
                change_percent = (current_price - start_price) / start_price * 100
                
                if abs(change_percent) >= self.threshold:
                    signal_key = f"{pair}_{tf}"
                    last_time = self.last_pump_signals.get(signal_key)
                    
                    if last_time and (datetime.now() - last_time).total_seconds() < (self.settings.get('cooldown_minutes', 10) * 60):
                        continue
                    
                    if self.analyzer:
                        dataframes = {}
                        for tf_name, tf_value in TIMEFRAMES.items():
                            limit_tf = 200 if tf_name == 'current' else 100
                            df_tf = await self.fetcher.fetch_ohlcv(pair, tf_value, limit_tf)
                            if df_tf is not None and not df_tf.empty:
                                df_tf = self.analyzer.calculate_indicators(df_tf)
                                dataframes[tf_name] = df_tf
                        
                        if dataframes:
                            funding = await self.fetcher.fetch_funding_rate(pair)
                            ticker = await self.fetcher.fetch_ticker(pair)
                            
                            metadata = {
                                'funding_rate': funding,
                                'volume_24h': ticker.get('volume_24h'),
                                'price_change_24h': ticker.get('percentage')
                            }
                            
                            signal = self.analyzer.generate_signal(dataframes, metadata, pair, self.fetcher.name)
                            
                            if signal and 'NEUTRAL' not in signal['direction']:
                                signal['pump_dump'] = [{
                                    'change_percent': change_percent,
                                    'time_window': minutes,
                                    'start_price': start_price,
                                    'end_price': current_price
                                }]
                                
                                if change_percent > 0:
                                    signal['signal_type'] = "PUMP"
                                else:
                                    signal['signal_type'] = "DUMP"
                                
                                signal['funding_rate'] = funding
                                
                                # ✅ ОТПРАВЛЯЕМ СИГНАЛ С ГРАФИКОМ!
                                try:
                                    contract_info = await self.fetcher.fetch_contract_info(pair)
                                    msg, keyboard = self.format_pump_message(signal, contract_info)
                                    
                                    # Загружаем данные для графика
                                    df = await self.fetcher.fetch_ohlcv(pair, TIMEFRAMES.get('current', '15m'), limit=200)
                                    
                                    coin = pair.split('/')[0].replace('USDT', '')
                                    
                                    if df is not None and not df.empty:
                                        df = self.analyzer.calculate_indicators(df)
                                        chart_buf = self.chart_generator.create_chart(df, signal, coin, TIMEFRAMES.get('current', '15m'))
                                        
                                        await self.telegram_bot.send_photo(
                                            chat_id=PUMP_CHAT_ID,
                                            photo=chart_buf,
                                            caption=msg,
                                            parse_mode='HTML',
                                            reply_markup=keyboard
                                        )
                                        logger.info(f"✅ Отправлен памп-сигнал с графиком: {pair}")
                                    else:
                                        await self.telegram_bot.send_message(
                                            chat_id=PUMP_CHAT_ID,
                                            text=msg,
                                            parse_mode='HTML',
                                            reply_markup=keyboard
                                        )
                                        logger.info(f"✅ Отправлен памп-сигнал (без графика): {pair}")
                                        
                                except Exception as e:
                                    logger.error(f"❌ Ошибка отправки сигнала {pair}: {e}")
                                
                                self.cache.set(cache_key, signal)
                                self.last_pump_signals[signal_key] = datetime.now()
                                
                                return signal
            return None
        except Exception as e:
            logger.error(f"Ошибка сканирования {pair}: {e}")
            return None
    
    async def scan_all_pairs(self) -> List[Dict]:
        """
        Оптимизированное сканирование всех пар с гибкими настройками режима
        """
        logger.info("🚀 ЗАПУСК БЫСТРОГО ПАМП-СКАНЕРА (ГИБРИДНЫЙ)")
        
        try:
            all_pairs = await self.fetcher.fetch_all_pairs()
            if not all_pairs:
                return []
            
            # ✅ Загружаем настройки
            from config import SMART_REPEAT_SETTINGS, SCAN_MODE
            
            smart_repeat = SMART_REPEAT_SETTINGS
            
            # ✅ Словарь для отслеживания последних сигналов по монетам
            last_signals = {}  # coin: {'time': datetime, 'change': float, 'direction': str}
            
            # ===== ВЫБОР РЕЖИМА СКАНИРОВАНИЯ =====
            scan_pairs = []
            mode = SCAN_MODE.get('mode', 'all')
            
            # РЕЖИМ 1: ТОП ПО ОБЪЕМУ
            if mode == 'top_volume':
                logger.info("📊 Режим: топ-объем")
                pairs_with_volume = []
                
                # Берем первые 300 пар для скорости
                for pair in all_pairs[:300]:
                    try:
                        ticker = await self.fetcher.fetch_ticker(pair)
                        volume = ticker.get('volume_24h', 0)
                        if volume >= SCAN_MODE['top_volume']['min_volume']:
                            pairs_with_volume.append((pair, volume))
                    except:
                        continue
                
                pairs_with_volume.sort(key=lambda x: x[1], reverse=True)
                scan_pairs = [p[0] for p in pairs_with_volume[:SCAN_MODE['top_volume']['count']]]
                logger.info(f"  📊 Отобрано {len(scan_pairs)} пар по объему")
            
            # РЕЖИМ 2: ЩИТКОИНЫ
            elif mode == 'shitcoin':
                logger.info("📊 Режим: щиткоины")
                shitcoins = await self._get_volatile_shitcoins(all_pairs)
                scan_pairs = shitcoins[:SCAN_MODE['shitcoin']['count']]
                
                # Добавляем мейджоры если нужно
                if SCAN_MODE['shitcoin']['include_majors']:
                    majors = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'BNB/USDT:USDT', 
                            'SOL/USDT:USDT', 'XRP/USDT:USDT']
                    scan_pairs = majors[:SCAN_MODE['shitcoin']['majors_count']] + scan_pairs
                
                logger.info(f"  📊 Отобрано {len(scan_pairs)} щиткоинов")
            
            # РЕЖИМ 3: ГИБРИДНЫЙ
            elif mode == 'hybrid':
                logger.info("📊 Режим: гибридный")
                
                # Топ по объему
                pairs_with_volume = []
                for pair in all_pairs[:300]:
                    try:
                        ticker = await self.fetcher.fetch_ticker(pair)
                        volume = ticker.get('volume_24h', 0)
                        pairs_with_volume.append((pair, volume))
                    except:
                        continue
                pairs_with_volume.sort(key=lambda x: x[1], reverse=True)
                top_volume = [p[0] for p in pairs_with_volume[:SCAN_MODE['hybrid']['top_volume_count']]]
                
                # Щиткоины
                shitcoins = await self._get_volatile_shitcoins(all_pairs)
                shitcoins = shitcoins[:SCAN_MODE['hybrid']['shitcoin_count']]
                
                # Объединяем
                scan_pairs = list(set(top_volume + shitcoins))
                logger.info(f"  📊 Отобрано {len(scan_pairs)} пар (топ-объем: {len(top_volume)}, щиткоины: {len(shitcoins)})")
            
            # РЕЖИМ 4: ВСЕ ПАРЫ
            else:
                logger.info("📊 Режим: все пары")
                scan_pairs = all_pairs[:self.max_pairs]
            
            # Перемешиваем если нужно
            if SCAN_MODE.get('randomize', True):
                random.shuffle(scan_pairs)
            
            # Запускаем WebSocket мониторинг для быстрых сигналов
            if self.websocket_available:
                await self.start_websocket_monitoring(all_pairs)  # отправляем все пары для WebSocket
            
            logger.info(f"📊 Памп-сканер: анализирую {len(scan_pairs)} пар (WebSocket: {self.websocket_available})")
            
            pump_signals = []
            
            # Разбиваем на батчи для параллельной обработки
            batches = [scan_pairs[i:i+self.batch_size] for i in range(0, len(scan_pairs), self.batch_size)]
            
            for batch_num, batch in enumerate(batches):
                logger.info(f"🔄 Обработка батча {batch_num + 1}/{len(batches)} ({len(batch)} пар)")
                
                # Параллельная обработка батча
                tasks = [self.scan_pair(pair) for pair in batch]
                batch_results = await asyncio.gather(*tasks)
                
                # Собираем результаты с умной фильтрацией
                for signal in batch_results:
                    if not signal:
                        continue
                    
                    coin = signal['symbol'].split('/')[0]
                    current_change = abs(signal['pump_dump'][0]['change_percent'])
                    current_direction = 'LONG' if signal['pump_dump'][0]['change_percent'] > 0 else 'SHORT'
                    
                    # ===== УМНАЯ ЛОГИКА ПОВТОРОВ =====
                    if smart_repeat['enabled'] and coin in last_signals:
                        last = last_signals[coin]
                        time_diff = (datetime.now() - last['time']).total_seconds() / 60  # в минутах
                        
                        # Базовая проверка cooldown
                        if time_diff < smart_repeat['cooldown_minutes']:
                            # Проверяем, разрешены ли повторы при усилении
                            if smart_repeat['allow_stronger_moves']:
                                # Вычисляем порог усиления
                                required_strength = last['change'] * smart_repeat['strength_multiplier']
                                
                                # Проверяем, усилилось ли движение
                                if current_change > required_strength:
                                    # Проверяем минимальное время до повтора
                                    if time_diff >= smart_repeat['min_time_for_repeat']:
                                        logger.info(f"⚡ УСИЛЕНИЕ {coin}: {last['change']:.1f}% → {current_change:.1f}% (разрешен повтор)")
                                    else:
                                        logger.info(f"⏳ {coin} усилился, но слишком рано ({time_diff:.0f} мин < {smart_repeat['min_time_for_repeat']} мин)")
                                        continue
                                else:
                                    logger.info(f"⏭️ {coin} повтор: нужно > {required_strength:.1f}%, есть {current_change:.1f}%")
                                    continue
                            else:
                                logger.info(f"⏭️ {coin} повтор: cooldown {time_diff:.0f} мин")
                                continue
                        else:
                            logger.info(f"📌 {coin} повтор после {time_diff:.0f} мин (cooldown истек)")
                    
                    # ===== ФИЛЬТР ПО ТИПУ СИГНАЛА (PUMP/DUMP) =====
                    from config import PUMP_DUMP_FILTER

                    if PUMP_DUMP_FILTER.get('enabled', False):
                        filter_type = PUMP_DUMP_FILTER.get('type', 'both')
                        change = signal['pump_dump'][0]['change_percent']
                        
                        if filter_type == 'pump_only' and change < 0:
                            logger.info(f"⏭️ {signal['symbol']} пропущен (только пампы, а это дамп)")
                            continue
                        if filter_type == 'dump_only' and change > 0:
                            logger.info(f"⏭️ {signal['symbol']} пропущен (только дампы, а это памп)")
                            continue

                    # ✅ Сохраняем сигнал в историю
                    last_signals[coin] = {
                        'time': datetime.now(),
                        'change': current_change,
                        'direction': current_direction,
                        'symbol': signal['symbol']
                    }
                    
                    pump_signals.append(signal)
                    logger.info(f"✅ Памп-сигнал (REST): {signal['symbol']} {signal['pump_dump'][0]['change_percent']:+.1f}%")
                
                # Пауза между батчами
                if batch_num < len(batches) - 1:
                    await asyncio.sleep(self.delay_between_batches)
            
            # Сортируем по силе движения
            pump_signals.sort(key=lambda x: abs(x['pump_dump'][0]['change_percent']), reverse=True)
            logger.info(f"🎯 Памп-сканер: найдено {len(pump_signals)} сигналов (WebSocket активен)")
            return pump_signals
            
        except Exception as e:
            logger.error(f"❌ Ошибка памп-сканера: {e}")
            return []
    
    def _get_power_text(self, strength: float) -> str:
        """Определение текста силы сигнала"""
        if strength >= 90:
            return "🔥🔥🔥 ОЧЕНЬ СИЛЬНЫЙ"
        elif strength >= 75:
            return "🔥🔥 СИЛЬНЫЙ"
        elif strength >= 60:
            return "🔥 СРЕДНИЙ"
        else:
            return "⚡ СЛАБЫЙ"
    
    def _timeframe_to_minutes(self, tf: str) -> int:
        """Конвертация таймфрейма в минуты"""
        return {'1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60}.get(tf, 15)
    
    def _format_compact(self, num: float) -> str:
        """Форматирование больших чисел"""
        if num is None:
            return "N/A"
        if num > 1_000_000_000:
            return f"{num/1_000_000_000:.1f}B"
        elif num > 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num > 1_000:
            return f"{num/1_000:.1f}K"
        else:
            return f"{num:.0f}"
    
    def format_tf_name(self, tf: str) -> str:   # ← 4 пробела отступ
        """Преобразование названия таймфрейма в читаемый формат"""
        tf_map = SIGNAL_FORMAT_SETTINGS.get('tf_names', {
            'current': '15м',
            'hourly': '1ч',
            'four_hourly': '4ч',
            'daily': '1д',
            'weekly': '1н',
            'monthly': '1м'
        })
        return tf_map.get(tf, tf)
    
    def format_pump_message(self, signal: Dict, contract_info: Dict = None) -> Tuple[str, InlineKeyboardMarkup]:
        """
        Форматирование памп-сигнала для отправки с ПРАВИЛЬНЫМ направлением
        """
        
        logger.info(f"  📊 format_pump_message START: направление до изменений = {signal.get('direction')}")

        coin = signal['symbol'].split('/')[0].replace('USDT', '')
        
        # Получаем данные о пампа
        pump_data = signal.get('pump_dump', [{}])[0]
        pump_change = pump_data.get('change_percent', 0)
        pump_time = pump_data.get('time_window', 0)
        
        # ===== ПРАВИЛЬНАЯ ЛОГИКА НАПРАВЛЕНИЯ =====
        # PUMP + пробой - LONG 📈 (пробой после пампа)
        # PUMP без пробоя - SHORT 📉 (коррекция)
        # DUMP + пробой - SHORT 📉 (пробой после дампа)
        # DUMP без пробоя - LONG 📈 (отскок)

        # Проверяем, есть ли пробой уровня
        has_breakout = False
        if 'reasons' in signal:
            for reason in signal['reasons']:
                if 'Пробой' in reason:
                    has_breakout = True
                    break

        logger.info(f"  📊 format_pump_message: has_breakout = {has_breakout}, pump_change = {pump_change}")
        
        # ===== ПРАВИЛЬНАЯ ЛОГИКА НАПРАВЛЕНИЯ =====
        
        # Логируем исходное направление
        logger.info(f"  📊 format_pump_message START: исходное направление = {signal.get('direction')}")
        logger.info(f"  📊 format_pump_message: has_breakout = {has_breakout}, pump_change = {pump_change}")

        if pump_change > 0:  # PUMP
            if has_breakout:
                old_dir = signal['direction']
                signal_emoji = "🚀"
                signal_text = f"PUMP +{pump_change:.1f}%"
                signal['direction'] = 'LONG 📈 (пробой после пампа)'
                signal['signal_type'] = 'PUMP_BREAKOUT'
                logger.info(f"  📊 format_pump_message: СМЕНА! {old_dir} → {signal['direction']} (PUMP + пробой)")
                # Добавляем причину (если еще нет)
                if 'reasons' in signal:
                    has_pump_reason = any('Пробой уровня' in r or 'Коррекция' in r for r in signal['reasons'])
                    if not has_pump_reason:
                        signal['reasons'].insert(0, f"Пробой уровня после пампа +{pump_change:.1f}%")
            else:
                old_dir = signal['direction']
                signal_emoji = "🚨" if pump_change > 3.0 else "🚀"
                signal_text = f"PUMP +{pump_change:.1f}%"
                signal['direction'] = 'SHORT 📉 (коррекция)'
                signal['signal_type'] = 'PUMP'
                logger.info(f"  📊 format_pump_message: СМЕНА! {old_dir} → {signal['direction']} (PUMP без пробоя)")
                if 'reasons' in signal:
                    has_pump_reason = any('Пробой уровня' in r or 'Коррекция' in r for r in signal['reasons'])
                    if not has_pump_reason:
                        signal['reasons'].insert(0, f"Коррекция после пампа +{pump_change:.1f}%")

        else:  # DUMP
            # Получаем bearish_score из сигнала (нужно передать из generate_signal)
            bearish_score = signal.get('bearish_score', 0)
            logger.info(f"  📊 format_pump_message: bearish_score = {bearish_score}")
            
            if has_breakout:
                old_dir = signal['direction']
                signal_emoji = "📉"
                signal_text = f"DUMP {pump_change:.1f}%"
                signal['direction'] = 'SHORT 📉 (пробой после дампа)'
                signal['signal_type'] = 'DUMP_BREAKOUT'
                
                # ===== ПЕРЕСЧИТЫВАЕМ ЦЕЛИ ДЛЯ SHORT =====
                current_price = signal['price']
                atr = signal.get('atr', 0)
                
                if atr > 0:
                    from config import ATR_SETTINGS
                    signal['target_1'] = current_price - atr * ATR_SETTINGS['short_target_1_mult']
                    signal['target_2'] = current_price - atr * ATR_SETTINGS['short_target_2_mult']
                    signal['stop_loss'] = current_price + atr * ATR_SETTINGS['short_stop_loss_mult']
                    logger.info(f"  📊 Пересчитаны цели для SHORT: t1={signal['target_1']:.6f}, t2={signal['target_2']:.6f}, sl={signal['stop_loss']:.6f}")
                
                logger.info(f"  📊 format_pump_message: СМЕНА! {old_dir} → {signal['direction']} (DUMP + пробой)")
                
                if 'reasons' in signal and not any('Отскок' in r for r in signal['reasons']):
                    signal['reasons'].insert(0, f"Пробой уровня после дампа {pump_change:.1f}%")
            else:
                # Без пробоя - проверяем силу медвежьих сигналов
                if bearish_score >= 50:
                    old_dir = signal['direction']
                    signal_emoji = "📉"
                    signal_text = f"DUMP {pump_change:.1f}%"
                    signal['direction'] = 'SHORT 📉 (продолжение)'
                    signal['signal_type'] = 'DUMP'
                    logger.info(f"  📊 format_pump_message: СМЕНА! {old_dir} → {signal['direction']} (DUMP, bearish_score={bearish_score})")
                    if 'reasons' in signal and not any('Отскок' in r for r in signal['reasons']):
                        signal['reasons'].insert(0, f"Продолжение дампа {pump_change:.1f}%")
                else:
                    old_dir = signal['direction']
                    signal_emoji = "📊" if pump_change < -1.5 else "📉"
                    signal_text = f"DUMP {pump_change:.1f}%"
                    signal['direction'] = 'LONG 📈 (отскок)'
                    signal['signal_type'] = 'DUMP'
                    logger.info(f"  📊 format_pump_message: СМЕНА! {old_dir} → {signal['direction']} (DUMP, bearish_score={bearish_score})")
                    if 'reasons' in signal and not any('Отскок' in r for r in signal['reasons']):
                        signal['reasons'].insert(0, f"Отскок после дампа {pump_change:.1f}%")
        
        logger.info(f"  📊 format_pump_message END: финальное направление = {signal['direction']}")
        
        # Определяем силу сигнала по модулю движения
        signal_power = self._get_power_text(abs(pump_change))
        signal['signal_power'] = signal_power
        
        line1 = f"{signal_emoji} <code>{coin}</code> {signal_text} {signal_power}"
        
        # Параметры контракта
        if contract_info:
            max_lev = contract_info.get('max_leverage')
            if max_lev is None or max_lev > 200:
                max_lev = 100
            
            min_amt = contract_info.get('min_amount')
            if min_amt is None or min_amt > 1000:
                min_amt = 5.0
            
            max_amt = contract_info.get('max_amount')
            if max_amt is None or max_amt > 10_000_000:
                max_amt = 2_000_000
            
            line2 = f"📌 {max_lev}x / {min_amt:.0f}$ / {self._format_compact(max_amt)}"
            
            # Объем 24ч
            if signal.get('volume_24h') is not None and signal['volume_24h'] > 0:
                volume = signal['volume_24h']
                if volume > 1_000_000:
                    line2 += f" / {volume/1_000_000:.1f}M"
                elif volume > 1_000:
                    line2 += f" / {volume/1_000:.1f}K"
                else:
                    line2 += f" / {volume:.0f}"
            
            # Фандинг
            funding_rate = signal.get('funding_rate')
            if funding_rate is not None:
                funding = funding_rate * 100
                funding_emoji = "🟢" if funding > 0 else "🔴" if funding < 0 else "⚪"
                line2 += f" / {funding_emoji} {funding:.3f}%"
        else:
            line2 = f"📌 100x / 5$ / 2.0M"
            
            if signal.get('volume_24h') is not None and signal['volume_24h'] > 0:
                volume = signal['volume_24h']
                if volume > 1_000_000:
                    line2 += f" / {volume/1_000_000:.1f}M"
                elif volume > 1_000:
                    line2 += f" / {volume/1_000:.1f}K"
                else:
                    line2 += f" / {volume:.0f}"
            
            funding_rate = signal.get('funding_rate')
            if funding_rate is not None:
                funding = funding_rate * 100
                funding_emoji = "🟢" if funding > 0 else "🔴" if funding < 0 else "⚪"
                line2 += f" / {funding_emoji} {funding:.3f}%"
        
        exchange_link = REF_LINKS.get(signal['exchange'], '#')
        line3 = f"💲 Trade: <a href='{exchange_link}'>{signal['exchange']}</a>"
        
        line4 = ""
        line5 = f"📊 Направление: {signal['direction']}"
        line6 = f"🕓 Таймфрейм: {TIMEFRAMES.get('current', '15m')}"
        
        # Форматирование цены
        if signal['price'] < 0.00001:
            price_formatted = f"{signal['price']:.8f}"
        elif signal['price'] < 0.0001:
            price_formatted = f"{signal['price']:.7f}"
        elif signal['price'] < 0.001:
            price_formatted = f"{signal['price']:.6f}"
        elif signal['price'] < 0.01:
            price_formatted = f"{signal['price']:.5f}"
        elif signal['price'] < 0.1:
            price_formatted = f"{signal['price']:.4f}"
        elif signal['price'] < 1:
            price_formatted = f"{signal['price']:.3f}"
        else:
            price_formatted = f"{signal['price']:.2f}"
        
        price_formatted = price_formatted.rstrip('0').rstrip('.') if '.' in price_formatted else price_formatted
        line7 = f"💰 Цена текущая: {price_formatted}"

        line8 = ""
        if pump_data:
            start_price = pump_data.get('start_price', signal['price'] / (1 + pump_change/100))
            if start_price < 0.001:
                start_formatted = f"{start_price:.8f}".rstrip('0').rstrip('.')
            else:
                start_formatted = f"{start_price:.4f}"
            
            # ✅ ПРАВИЛЬНО: проверяем направление движения
            if pump_change > 0:
                line8 = f"📈 Рост: {start_formatted} → {price_formatted} за {pump_time:.0f}с"
            else:
                line8 = f"📉 Падение: {start_formatted} → {price_formatted} за {pump_time:.0f}с"
        
        # Форматирование целей
        if signal.get('target_1') and signal.get('target_2') and signal.get('stop_loss'):
            def format_target(price):
                if price < 0.00001:
                    return f"{price:.8f}".rstrip('0').rstrip('.')
                elif price < 0.0001:
                    return f"{price:.7f}".rstrip('0').rstrip('.')
                elif price < 0.001:
                    return f"{price:.6f}".rstrip('0').rstrip('.')
                elif price < 0.01:
                    return f"{price:.5f}".rstrip('0').rstrip('.')
                elif price < 0.1:
                    return f"{price:.4f}".rstrip('0').rstrip('.')
                elif price < 1:
                    return f"{price:.3f}".rstrip('0').rstrip('.')
                else:
                    return f"{price:.2f}"
            
            t1 = format_target(signal['target_1'])
            t2 = format_target(signal['target_2'])
            sl = format_target(signal['stop_loss'])
            line9 = f"🎯 Цели: {t1} | {t2} | SL {sl}"
        else:
            line9 = "🎯 Цели: N/A | N/A | SL N/A"
        
        line10 = ""
        line11 = "💡 Причины:"
        
        # Очистка причин от эмодзи
        clean_reasons = []
        for reason in signal['reasons'][:6]:
            clean_reason = reason
            clean_reason = clean_reason.replace("📊 ", "")
            clean_reason = clean_reason.replace("✅ ", "")
            clean_reason = clean_reason.replace("🔄 ", "")
            clean_reason = clean_reason.replace("💰 ", "")
            clean_reason = clean_reason.replace("📈 ", "")
            clean_reason = clean_reason.replace("📉 ", "")
            clean_reason = clean_reason.replace("⚡️ ", "")
            clean_reason = clean_reason.replace("🔥 ", "")
            clean_reason = clean_reason.replace("🟢 ", "")
            clean_reason = clean_reason.replace("🔴 ", "")
            clean_reason = clean_reason.replace("⚪️ ", "")
            clean_reason = clean_reason.replace("⚪ ", "")
            clean_reason = clean_reason.replace("📦 ", "")
            clean_reason = clean_reason.replace("📐 ", "")
            clean_reason = clean_reason.replace("⚠️ ", "")
            clean_reason = clean_reason.strip()
            clean_reasons.append(clean_reason)
        
        reasons_lines = [f"   {r}" for r in clean_reasons]
        
        # Собираем сообщение
        lines = [line1, line2, line3, line4, line5, line6, line7]
        if line8:
            lines.append(line8)
        lines.extend([line9, line10, line11])
        lines.extend(reasons_lines)
        
        message = "\n".join(lines)
        
        # Кнопки
        keyboard = []
        row1 = []
        if DISPLAY_SETTINGS['buttons']['copy']:
            row1.append(InlineKeyboardButton(f"📋 Копировать {coin}", callback_data=f"copy_{coin}"))
        if DISPLAY_SETTINGS['buttons']['trade']:
            row1.append(InlineKeyboardButton(f"🚀 Торговать на {signal['exchange']}", url=REF_LINKS.get(signal['exchange'], '#')))
        if row1:
            keyboard.append(row1)
        
        row2 = []
        if DISPLAY_SETTINGS['buttons']['refresh']:
            row2.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{coin}"))
        if DISPLAY_SETTINGS['buttons']['details']:
            row2.append(InlineKeyboardButton("📊 Детали", callback_data=f"details_{coin}"))
        if row2:
            keyboard.append(row2)
        
        return message, InlineKeyboardMarkup(keyboard) if keyboard else None

    def _get_power_text(self, strength: float) -> str:
        """Определение текста силы сигнала для ПАМП-ДВИЖЕНИЙ (0-100%+)"""
        if strength >= 20.0:
            return "🔥🔥🔥🔥 ЭКСТРЕМАЛЬНЫЙ"
        elif strength >= 12.0:
            return "🔥🔥🔥 ОЧЕНЬ СИЛЬНЫЙ"
        elif strength >= 8.0:
            return "🔥🔥 СИЛЬНЫЙ"
        elif strength >= 5.0:
            return "🔥 СРЕДНИЙ"
        elif strength >= 3.0:
            return "📊 СРЕДНИЙ"
        elif strength >= 1.5:
            return "⚡ СЛАБЫЙ"
        else:
            return "👀 НАБЛЮДЕНИЕ"

# ============== ОСНОВНОЙ КЛАСС БОТА ==============

class MultiExchangeScannerBot:
    def __init__(self):
        self.fetchers = {}
        self.analyzer = MultiTimeframeAnalyzer()
        self.chart_generator = ChartGenerator()
        self.telegram_bot = Bot(token=TELEGRAM_TOKEN)
        self.last_signals = {}
        self.breakout_tracker = BreakoutTracker()
        self.fakeout_detector = FakeoutDetector()
        
        self.divergence = DivergenceAnalyzer() if FEATURES['advanced']['divergence'] else None
        self.imbalance = ImbalanceAnalyzer(IMBALANCE_SETTINGS) if FEATURES['advanced']['imbalance'] else None
        self.liquidity = LiquidityAnalyzer(LIQUIDITY_SETTINGS) if FEATURES['advanced']['liquidity'] else None
        
        # Инициализация дополнительных анализаторов
        if FEATURES['advanced']['fibonacci']:
            from config import FIBONACCI_SETTINGS
            self.fibonacci = FibonacciAnalyzer(FIBONACCI_SETTINGS)
            self.analyzer.set_fibonacci(self.fibonacci)
            logger.info("✅ Анализатор Фибоначчи инициализирован")
        
        # Volume Profile (импорт вынесен ДО условия)
        from config import VOLUME_PROFILE_SETTINGS
        if FEATURES['advanced']['volume_profile'] and VOLUME_PROFILE_SETTINGS.get('enabled', False):
            self.volume_profile = VolumeProfileAnalyzer(VOLUME_PROFILE_SETTINGS)
            self.analyzer.set_volume_profile(self.volume_profile)
            logger.info("✅ Volume Profile анализатор инициализирован")
        
        # Инициализация анализатора накопления
        if FEATURES['advanced']['accumulation']:
            from config import ACCUMULATION_SETTINGS
            self.accumulation = AccumulationAnalyzer(ACCUMULATION_SETTINGS)
            self.analyzer.set_accumulation(self.accumulation)
            logger.info("✅ Анализатор накопления инициализирован")
        
        # Инициализация бирж
        from config import EXCHANGES
        
        for exchange_id, config in EXCHANGES.items():
            if config.get('enabled', False):
                if exchange_id == 'bingx':
                    self.fetchers['BingX'] = BingxFetcher()
                else:
                    self.fetchers[exchange_id.capitalize()] = MultiExchangeFetcher(
                        exchange_id,
                        config.get('api_key'),
                        config.get('api_secret')
                    )
        
        # Инициализация статистики
        if STATS_SETTINGS['enabled'] and STATS_SETTINGS['stats_chat_id']:
            self.stats = SignalStatistics(self.telegram_bot, STATS_SETTINGS['stats_chat_id'])
            logger.info("✅ Система статистики инициализирована")
            
            # Запускаем фоновые задачи
            asyncio.create_task(self.stats_updater_loop())
            asyncio.create_task(self.daily_report_loop())
    
    def extract_coin(self, symbol: str) -> str:
        if '/USDT' in symbol:
            return symbol.split('/')[0]
        return symbol.replace('USDT', '')
    
    def format_compact(self, num: float) -> str:
        if num is None:
            return "N/A"
        if num > 1_000_000_000:
            return f"{num/1_000_000_000:.1f}B"
        elif num > 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num > 1_000:
            return f"{num/1_000:.1f}K"
        else:
            return f"{num:.0f}"
    
    # ============== НОВЫЕ ФУНКЦИИ ДЛЯ ПОНЯТНЫХ ОПИСАНИЙ ==============
    
    def get_volume_description(self, volume_ratio: float) -> str:
        """
        Преобразует числовое значение объема в понятное текстовое описание
        """
        if volume_ratio >= 5.0:
            return f"🔥 АНОМАЛЬНЫЙ объем (x{volume_ratio:.1f})"
        elif volume_ratio >= 3.0:
            return f"⚡ ОЧЕНЬ СИЛЬНЫЙ объем (x{volume_ratio:.1f})"
        elif volume_ratio >= 2.0:
            return f"✅ СИЛЬНЫЙ объем (x{volume_ratio:.1f})"
        elif volume_ratio >= 1.5:
            return f"📊 ПОВЫШЕННЫЙ объем (x{volume_ratio:.1f})"
        else:
            return f"📉 обычный объем (x{volume_ratio:.1f})"
    
    def get_vwap_description(self, price: float, vwap: float) -> str:
        """
        Преобразует положение цены относительно VWAP в понятное описание
        """
        if vwap is None or vwap == 0:
            return ""
        
        diff_percent = (price - vwap) / vwap * 100
        
        if price > vwap:
            if diff_percent > 5:
                return f"🔥 Цена значительно ВЫШЕ справедливой (VWAP +{diff_percent:.1f}%)"
            elif diff_percent > 2:
                return f"⚡ Цена ВЫШЕ справедливой (VWAP +{diff_percent:.1f}%)"
            else:
                return f"✅ Цена чуть ВЫШЕ справедливой (VWAP +{diff_percent:.1f}%)"
        else:
            if diff_percent < -5:
                return f"🔥 Цена значительно НИЖЕ справедливой (VWAP {diff_percent:.1f}%)"
            elif diff_percent < -2:
                return f"⚡ Цена НИЖЕ справедливой (VWAP {diff_percent:.1f}%)"
            else:
                return f"📉 Цена чуть НИЖЕ справедливой (VWAP {diff_percent:.1f}%)"
    
    def get_rsi_description(self, rsi: float) -> str:
        """
        Описание состояния RSI
        """
        if rsi >= 80:
            return f"🔥 RSI перекуплен ({rsi:.1f}) - сильный сигнал на продажу"
        elif rsi >= 70:
            return f"⚡ RSI перекуплен ({rsi:.1f}) - возможна коррекция"
        elif rsi <= 20:
            return f"🔥 RSI перепродан ({rsi:.1f}) - сильный сигнал на покупку"
        elif rsi <= 30:
            return f"⚡ RSI перепродан ({rsi:.1f}) - возможен отскок"
        elif 40 <= rsi <= 60:
            return f"📊 RSI нейтральный ({rsi:.1f})"
        else:
            return f"📉 RSI {rsi:.1f}"
    
    def get_funding_description(self, funding_rate: float) -> str:
        """
        Описание ставки фондирования
        """
        if funding_rate is None:
            return ""
        
        funding_pct = funding_rate * 100
        
        if funding_pct > 0.05:
            return f"🔥 Очень высокий позитивный фандинг ({funding_pct:.3f}%) - шортисты переплачивают"
        elif funding_pct > 0.01:
            return f"⚡ Высокий позитивный фандинг ({funding_pct:.3f}%) - рынок перегрет"
        elif funding_pct > 0.001:
            return f"✅ Позитивный фандинг ({funding_pct:.3f}%)"
        elif funding_pct < -0.05:
            return f"🔥 Очень высокий негативный фандинг ({funding_pct:.3f}%) - лонгисты переплачивают"
        elif funding_pct < -0.01:
            return f"⚡ Высокий негативный фандинг ({funding_pct:.3f}%) - рынок перегрет"
        elif funding_pct < -0.001:
            return f"📉 Негативный фандинг ({funding_pct:.3f}%)"
        else:
            return f"⚪ Фандинг нейтральный ({funding_pct:.3f}%)"
    
    # ============== ОСНОВНОЙ МЕТОД ФОРМАТИРОВАНИЯ ==============
    
    def format_message(self, signal: Dict, contract_info: Dict = None, pump_percent: float = None, df: pd.DataFrame = None) -> Tuple[str, InlineKeyboardMarkup]:
        """Форматирование сигнала с новым форматом"""
        
        # Определяем эмодзи
        if signal.get('signal_type') in ['PUMP', 'DUMP'] or pump_percent:
            main_emoji = '🚀' if signal.get('signal_type') == 'PUMP' else '📉'
            coin = self.extract_coin(signal['symbol'])
            if signal.get('pump_dump'):
                pump_text = f" {signal['pump_dump'][0]['change_percent']:+.1f}%"
            else:
                pump_text = f" {pump_percent:+.1f}%" if pump_percent else ""
        elif signal.get('signal_type') == 'accumulation':
            main_emoji = '📦'
            coin = self.extract_coin(signal['symbol'])
            pump_text = " НАКОПЛЕНИЕ"
        else:
            if 'LONG' in signal['direction']:
                main_emoji = '🟢'
            elif 'SHORT' in signal['direction']:
                main_emoji = '🔴'
            else:
                main_emoji = '⚪'
            coin = self.extract_coin(signal['symbol'])
            pump_text = ""
        
        # Строка 1: название и сила
        line1 = f"{main_emoji} <code>{coin}</code>{pump_text} {signal['signal_power']}"
        
        # Строка 2: параметры контракта
        if contract_info:
            max_lev = contract_info.get('max_leverage', 100)
            if max_lev > 200:
                max_lev = 100
            min_amt = contract_info.get('min_amount', 5.0)
            if min_amt > 1000:
                min_amt = 5.0
            max_amt = contract_info.get('max_amount', 2_000_000)
            if max_amt > 10_000_000:
                max_amt = 2_000_000
            
            line2 = f"📌 {max_lev}x / {min_amt:.0f}$ / {self.format_compact(max_amt)}"
            
            if signal.get('volume_24h') and signal['volume_24h'] > 0:
                volume = signal['volume_24h']
                if volume > 1_000_000:
                    line2 += f" / {volume/1_000_000:.1f}M"
                elif volume > 1_000:
                    line2 += f" / {volume/1_000:.1f}K"
                else:
                    line2 += f" / {volume:.0f}"
            
            funding_rate = signal.get('funding_rate')
            if funding_rate is not None:
                funding = funding_rate * 100
                funding_emoji = "🟢" if funding > 0 else "🔴" if funding < 0 else "⚪"
                line2 += f" / {funding_emoji} {funding:.3f}%"
        else:
            line2 = f"📌 100x / 5$ / 2.0M"
            if signal.get('volume_24h') and signal['volume_24h'] > 0:
                volume = signal['volume_24h']
                if volume > 1_000_000:
                    line2 += f" / {volume/1_000_000:.1f}M"
                elif volume > 1_000:
                    line2 += f" / {volume/1_000:.1f}K"
                else:
                    line2 += f" / {volume:.0f}"
            
            funding_rate = signal.get('funding_rate')
            if funding_rate is not None:
                funding = funding_rate * 100
                funding_emoji = "🟢" if funding > 0 else "🔴" if funding < 0 else "⚪"
                line2 += f" / {funding_emoji} {funding:.3f}%"
        
        # Строка 3: биржи (3 штуки)
        bingx_link = REF_LINKS.get('BingX', '#')
        bybit_link = REF_LINKS.get('Bybit', '#')
        mexc_link = REF_LINKS.get('MEXC', '#')
        line3 = f"💲 Trade: <a href='{bingx_link}'>BingX</a> | <a href='{bybit_link}'>Bybit</a> | <a href='{mexc_link}'>MEXC</a>"
        
        # Пустая строка после бирж
        lines = [line1, line2, line3, ""]
        
        # Направление
        lines.append(f"📊 Направление: {signal['direction']}")
        
        # Таймфрейм (переводим)
        current_tf = TIMEFRAMES.get('current', '15m')
        tf_display = '15м'
        lines.append(f"🕓 Таймфрейм: {tf_display}")
        
        # Цена
        price = signal['price']
        if price < 0.00001:
            price_formatted = f"{price:.8f}".rstrip('0').rstrip('.')
        elif price < 0.0001:
            price_formatted = f"{price:.7f}".rstrip('0').rstrip('.')
        elif price < 0.001:
            price_formatted = f"{price:.6f}".rstrip('0').rstrip('.')
        elif price < 0.01:
            price_formatted = f"{price:.5f}".rstrip('0').rstrip('.')
        elif price < 0.1:
            price_formatted = f"{price:.4f}".rstrip('0').rstrip('.')
        elif price < 1:
            price_formatted = f"{price:.3f}".rstrip('0').rstrip('.')
        else:
            price_formatted = f"{price:.2f}".rstrip('0').rstrip('.')
        
        lines.append(f"💰 Цена текущая: {price_formatted}")
        
        # Зоны доп.входа
        entry_zones = signal.get('entry_zones', [])
        if entry_zones:
            lines.append("🟣 Зоны доп.входа:")
            for zone in entry_zones:
                lines.append(f"     ▪️ {zone}")
        
        # Потенциал для накопления
        potential_line = ""
        if signal.get('signal_type') == 'accumulation' and signal.get('accumulation', {}).get('potential'):
            potential = signal['accumulation']['potential']
            if potential.get('has_potential'):
                direction_emoji = "📈" if potential['target_pct'] > 0 else "📉"
                if potential['target_price'] < 0.001:
                    target_str = f"{potential['target_price']:.6f}".rstrip('0').rstrip('.')
                else:
                    target_str = f"{potential['target_price']:.4f}".rstrip('0').rstrip('.')
                potential_line = f"{direction_emoji} Потенциал: {potential['target_pct']:+.2f}% до {target_str} ({potential['target_level']})"
                lines.append(potential_line)
        
        # Рост для пампов
        pump_line = ""
        if pump_percent and signal.get('pump_dump') and len(signal['pump_dump']) > 0:
            pump_data = signal['pump_dump'][0]
            start_price = pump_data.get('start_price', signal['price'] / (1 + pump_percent/100))
            if start_price < 0.001:
                start_formatted = f"{start_price:.8f}".rstrip('0').rstrip('.')
            else:
                start_formatted = f"{start_price:.4f}"
            pump_line = f"📈 Рост: {start_formatted} → {price_formatted} за {pump_data.get('time_window', 0):.0f}с"
            lines.append(pump_line)
        
        # Цели
        if signal.get('target_1') and signal.get('target_2') and signal.get('stop_loss'):
            def format_target(p):
                if p < 0.00001:
                    return f"{p:.8f}".rstrip('0').rstrip('.')
                elif p < 0.0001:
                    return f"{p:.7f}".rstrip('0').rstrip('.')
                elif p < 0.001:
                    return f"{p:.6f}".rstrip('0').rstrip('.')
                elif p < 0.01:
                    return f"{p:.5f}".rstrip('0').rstrip('.')
                elif p < 0.1:
                    return f"{p:.4f}".rstrip('0').rstrip('.')
                elif p < 1:
                    return f"{p:.3f}".rstrip('0').rstrip('.')
                else:
                    return f"{p:.2f}".rstrip('0').rstrip('.')
            
            t1 = format_target(signal['target_1'])
            t2 = format_target(signal['target_2'])
            sl = format_target(signal['stop_loss'])
            lines.append(f"🎯 Цели: {t1} | {t2} | SL {sl}")
        
        # Пустая строка перед причинами
        lines.append("")
        
        # Причины
        lines.append("💡 Причины:")
        
        # Очистка и группировка причин
        clean_reasons = []
        for reason in signal.get('reasons', [])[:10]:
            clean_reason = reason
            # Убираем эмодзи
            for emoji in ["📊 ", "✅ ", "🔄 ", "💰 ", "📈 ", "📉 ", "⚡️ ", "🔥 ", "🟢 ", "🔴 ", "⚪️ ", "⚪ ", "📦 ", "📐 ", "⚠️ ", "🎯 "]:
                clean_reason = clean_reason.replace(emoji, "")
            # Заменяем VWAP на понятное
            clean_reason = clean_reason.replace("Цена выше VWAP", "Цена выше справедливой стоимости (VWAP)")
            clean_reason = clean_reason.replace("Цена ниже VWAP", "Цена ниже справедливой стоимости (VWAP)")
            clean_reasons.append(f"     {clean_reason}")
        
        lines.extend(clean_reasons)
        
        message = "\n".join(lines)
        
        # Кнопки
        keyboard = []
        row1 = []
        if DISPLAY_SETTINGS['buttons']['copy']:
            row1.append(InlineKeyboardButton(f"📋 Копировать {coin}", callback_data=f"copy_{coin}"))
        if DISPLAY_SETTINGS['buttons']['trade']:
            row1.append(InlineKeyboardButton(f"🚀 Торговать на BingX", url=REF_LINKS.get('BingX', '#')))
        if row1:
            keyboard.append(row1)
        
        row2 = []
        if DISPLAY_SETTINGS['buttons']['refresh']:
            row2.append(InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh_{coin}"))
        if DISPLAY_SETTINGS['buttons']['details']:
            row2.append(InlineKeyboardButton("📊 Детали", callback_data=f"details_{coin}"))
        if row2:
            keyboard.append(row2)
        
        return message, InlineKeyboardMarkup(keyboard) if keyboard else None
    
        logger.info(f"  📊 format_message: направление до изменений = {signal.get('direction')}")

    # ... остальные методы (scan_exchange, scan_all, fast_pump_scan, send_signal и т.д.) остаются без изменений ...
    
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
                    logger.info(f"🔄 [{i+1}/{scan_count}] Анализирую {pair}")
                    
                    dataframes = {}
                    for tf_name, tf_value in TIMEFRAMES.items():
                        limit = 200 if tf_name == 'current' else 100
                        df = await fetcher.fetch_ohlcv(pair, tf_value, limit)
                        if df is not None and not df.empty:
                            df = self.analyzer.calculate_indicators(df)
                            dataframes[tf_name] = df
                            logger.info(f"  ✅ Загружены данные для {tf_name}: {len(df)} свечей")
                        else:
                            logger.warning(f"  ⚠️ Нет данных для {tf_name}")
                    
                    if not dataframes:
                        logger.warning(f"  ⚠️ Нет данных для {pair}, пропускаю")
                        continue
                    
                    funding = await fetcher.fetch_funding_rate(pair)
                    ticker = await fetcher.fetch_ticker(pair)
                    
                    metadata = {
                        'funding_rate': funding,
                        'volume_24h': ticker.get('volume_24h'),
                        'price_change_24h': ticker.get('percentage')
                    }
                    
                    logger.info(f"  📊 Метаданные: funding={funding}, volume={ticker.get('volume_24h')}")
                    
                    try:
                        signal = self.analyzer.generate_signal(dataframes, metadata, pair, name)
                    except Exception as e:
                        logger.error(f"❌ Исключение в generate_signal для {pair}: {e}")
                        import traceback
                        traceback.print_exc()
                        continue
                    
                    if signal is None:
                        logger.info(f"  ❌ generate_signal вернул None для {pair}")
                        continue
                    
                    logger.info(f"  ✅ Сгенерирован сигнал: {signal['direction']} (уверенность {signal['confidence']}%)")
                    
                    if 'NEUTRAL' in signal['direction']:
                        logger.info(f"  ⏭️ NEUTRAL сигнал пропущен")
                        continue
                    
                    if signal['confidence'] < MIN_CONFIDENCE:
                        logger.info(f"  ⏭️ Низкая уверенность: {signal['confidence']}% < {MIN_CONFIDENCE}%")
                        continue
                    
                    signals.append(signal)
                    logger.info(f"  ✅ ДОБАВЛЕН сигнал: {pair} - {signal['direction']} ({signal['confidence']}%)")
                    
                    if (i + 1) % 10 == 0:
                        logger.info(f"📊 Прогресс {name}: {i + 1}/{scan_count}")
                    
                    await asyncio.sleep(0.2)
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка анализа {pair}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Ошибка сканирования {name}: {e}")
        
        logger.info(f"🎯 {name}: найдено {len(signals)} сигналов")
        return signals
    
    async def scan_all(self) -> List[Dict]:
        logger.info("="*50)
        logger.info("🚀 НАЧАЛО ОСНОВНОГО СКАНИРОВАНИЯ")
        logger.info("="*50)
        
        all_signals = []
        for name, fetcher in self.fetchers.items():
            signals = await self.scan_exchange(name, fetcher)
            all_signals.extend(signals)
        
        all_signals.sort(key=lambda x: x['signal_strength'], reverse=True)
        logger.info(f"🎯 ВСЕГО СИГНАЛОВ: {len(all_signals)}")
        
        return all_signals[:15]
    
    async def fast_pump_scan(self) -> List[Dict]:
        if not FEATURES['advanced']['pump_dump']:
            return []
        
        pump_signals = []
        for name, fetcher in self.fetchers.items():
            scanner = FastPumpScanner(
                fetcher, 
                PUMP_SCAN_SETTINGS, 
                self.analyzer,
                self.telegram_bot,
                self.chart_generator
            )
            signals = await scanner.scan_all_pairs()
            
            for signal in signals:
                contract_info = await fetcher.fetch_contract_info(signal['symbol'])
                if 'funding_rate' not in signal:
                    signal['funding_rate'] = await fetcher.fetch_funding_rate(signal['symbol'])
                msg, keyboard = scanner.format_pump_message(signal, contract_info)
                pump_signals.append({
                    'signal': signal,
                    'message': msg,
                    'keyboard': keyboard
                })
        
        pump_signals.sort(key=lambda x: abs(x['signal']['pump_dump'][0]['change_percent']), reverse=True)
        return pump_signals
    
    async def send_signal(self, signal: Dict, pump_only: bool = False):
        if pump_only and not signal.get('pump_dump'):
            return
        
        if signal['confidence'] < MIN_CONFIDENCE:
            return
        
        coin = self.extract_coin(signal['symbol'])
        
        self.last_signals[coin] = {
            'symbol': signal['symbol'],
            'signal': signal,
            'time': datetime.now()
        }
        
        contract_info = None
        df = None
        for fetcher in self.fetchers.values():
            if fetcher.name == signal['exchange']:
                contract_info = await fetcher.fetch_contract_info(signal['symbol'])
                df = await fetcher.fetch_ohlcv(signal['symbol'], TIMEFRAMES.get('current', '15m'), limit=200)
                break
        
        pump_percent = None
        if signal.get('pump_dump') and len(signal['pump_dump']) > 0:
            pump_percent = signal['pump_dump'][0].get('change_percent')
        
        msg, keyboard = self.format_message(signal, contract_info, pump_percent)
        
        if signal.get('signal_type') == 'accumulation':
            chat_id = ACCUMULATION_CHAT_ID
            signal_type = 'accumulation'
        elif signal.get('pump_dump'):
            chat_id = PUMP_CHAT_ID
            signal_type = 'pump'
        else:
            chat_id = TELEGRAM_CHAT_ID
            signal_type = 'regular'
        
        try:
            if df is not None and not df.empty:
                df = self.analyzer.calculate_indicators(df)
                chart_buf = self.chart_generator.create_chart(df, signal, coin, TIMEFRAMES.get('current', '15m'))
                
                await self.telegram_bot.send_photo(
                    chat_id=chat_id,
                    photo=chart_buf,
                    caption=msg,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                logger.info(f"✅ Отправлен {signal_type} сигнал с графиком: {signal['symbol']}")
            else:
                await self.telegram_bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                logger.info(f"✅ Отправлен {signal_type} сигнал: {signal['symbol']}")
            
            if hasattr(self, 'stats'):
                self.stats.add_signal(signal, signal_type)
                
        except Exception as e:
            logger.error(f"❌ Ошибка отправки: {e}")
    
    async def send_pump_signal(self, pump_data: Dict):
        signal = pump_data['signal']
        coin = self.extract_coin(signal['symbol'])
        
        self.last_signals[coin] = {
            'symbol': signal['symbol'],
            'signal': signal,
            'time': datetime.now()
        }
        
        df = None
        for fetcher in self.fetchers.values():
            if fetcher.name == signal['exchange']:
                df = await fetcher.fetch_ohlcv(signal['symbol'], TIMEFRAMES.get('current', '15m'), limit=200)
                break
        
        try:
            if df is not None and not df.empty:
                df = self.analyzer.calculate_indicators(df)
                chart_buf = self.chart_generator.create_chart(df, signal, coin, TIMEFRAMES.get('current', '15m'))
                
                await self.telegram_bot.send_photo(
                    chat_id=PUMP_CHAT_ID,
                    photo=chart_buf,
                    caption=pump_data['message'],
                    parse_mode='HTML',
                    reply_markup=pump_data['keyboard']
                )
                logger.info(f"✅ Отправлен памп-сигнал с графиком: {signal['symbol']}")
            else:
                await self.telegram_bot.send_message(
                    chat_id=PUMP_CHAT_ID,
                    text=pump_data['message'],
                    parse_mode='HTML',
                    reply_markup=pump_data['keyboard']
                )
                logger.info(f"✅ Отправлен памп-сигнал: {signal['symbol']}")
            
            if hasattr(self, 'stats'):
                self.stats.add_signal(signal, 'pump')
                
        except Exception as e:
            logger.error(f"❌ Ошибка отправки пампа: {e}")
    
    async def send_accumulation_signal(self, signal: Dict):
        coin = self.extract_coin(signal['symbol'])
        
        self.last_signals[coin] = {
            'symbol': signal['symbol'],
            'signal': signal,
            'time': datetime.now()
        }
        
        contract_info = None
        df = None
        for fetcher in self.fetchers.values():
            if fetcher.name == signal['exchange']:
                contract_info = await fetcher.fetch_contract_info(signal['symbol'])
                df = await fetcher.fetch_ohlcv(signal['symbol'], TIMEFRAMES.get('current', '15m'), limit=200)
                break
        
        msg, keyboard = self.format_message(signal, contract_info)
        
        try:
            if df is not None and not df.empty:
                df = self.analyzer.calculate_indicators(df)
                chart_buf = self.chart_generator.create_chart(df, signal, coin, TIMEFRAMES.get('current', '15m'))
                
                await self.telegram_bot.send_photo(
                    chat_id=ACCUMULATION_CHAT_ID,
                    photo=chart_buf,
                    caption=msg,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                logger.info(f"✅ Отправлен сигнал накопления с графиком: {signal['symbol']}")
            else:
                await self.telegram_bot.send_message(
                    chat_id=ACCUMULATION_CHAT_ID,
                    text=msg,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                logger.info(f"✅ Отправлен сигнал накопления: {signal['symbol']}")
            
            if hasattr(self, 'stats'):
                self.stats.add_signal(signal, 'accumulation')
                
        except Exception as e:
            logger.error(f"❌ Ошибка отправки сигнала накопления: {e}")
    
    async def get_detailed_analysis(self, fetcher, symbol: str, coin: str, signal_time: str = None) -> Tuple[str, InlineKeyboardMarkup]:
        try:
            lines = []
            lines.append(f"📊 *ДЕТАЛЬНЫЙ АНАЛИЗ {coin}*")
            if signal_time:
                lines.append(f"⏱️ Время сигнала: `{signal_time}`")
            lines.append(f"⏱️ Текущее время: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n")
            
            contract_info = await fetcher.fetch_contract_info(symbol)
            lines.append("⚡️ *ПАРАМЕТРЫ КОНТРАКТА:*")
            lines.append(f"└ Макс. плечо: `{contract_info.get('max_leverage', 100)}x`")
            lines.append(f"└ Мин. вход: `{contract_info.get('min_amount', 5):.2f} USDT`")
            lines.append(f"└ Макс. вход: `{self.format_compact(contract_info.get('max_amount', 2_000_000))} USDT`")
            
            if coin in self.last_signals:
                signal = self.last_signals[coin]['signal']
                lines.append("\n📊 *ТЕХНИЧЕСКИЙ АНАЛИЗ:*")
                for reason in signal['reasons']:
                    clean_reason = reason.replace("📊 ", "").replace("✅ ", "").replace("🔄 ", "")
                    lines.append(f"└ {clean_reason}")
                
                if 'fibonacci' in signal:
                    lines.append("\n📐 *ФИБОНАЧЧИ:*")
                    for tf, levels in signal['fibonacci']['levels'].items():
                        lines.append(f"└ {tf.upper()}: {len(levels)} уровней")
                
                if 'volume_profile' in signal:
                    lines.append("\n📊 *VOLUME PROFILE:*")
                    for tf, vp in signal['volume_profile']['levels'].items():
                        lines.append(f"└ {tf.upper()}: POC={vp['poc']:.2f}")
                
                if 'accumulation' in signal:
                    lines.append("\n📦 *НАКОПЛЕНИЕ:*")
                    acc = signal['accumulation']
                    for sig in acc.get('signals', [])[:3]:
                        lines.append(f"└ {sig}")
                    if acc.get('potential', {}).get('has_potential'):
                        pot = acc['potential']
                        lines.append(f"└ Потенциал: {pot['target_pct']:+.2f}% до {pot['target_level']}")
            
            detailed = "\n".join(lines)
            
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔝 Вернуться к сигналу", callback_data=f"back_{coin}")
            ]])
            
            return detailed, keyboard
            
        except Exception as e:
            logger.error(f"Ошибка детального анализа {symbol}: {e}")
            return f"❌ Ошибка анализа: {e}", None
    
    async def stats_updater_loop(self):
        while True:
            await asyncio.sleep(STATS_SETTINGS['update_interval'])
            
            if not hasattr(self, 'stats'):
                continue
                
            for signal_id, signal_data in self.stats.db['signals'].items():
                if signal_data['status'] != 'pending':
                    continue
                
                for fetcher in self.fetchers.values():
                    ticker = await fetcher.fetch_ticker(signal_data['symbol'])
                    if ticker and ticker.get('last'):
                        self.stats.update_signal(signal_id, ticker['last'])
                        break
    
    async def daily_report_loop(self):
        while True:
            now = datetime.now()
            target_time = datetime.strptime(STATS_SETTINGS['daily_report_time'], '%H:%M').time()
            target = datetime.combine(now.date(), target_time)
            
            if now > target:
                target += timedelta(days=1)
            
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            
            if hasattr(self, 'stats'):
                await self.stats.send_daily_report()
    
    async def run(self):
        logger.info("🤖 Мульти-биржевой бот запущен")
        logger.info(f"📊 Основной анализ: каждые {UPDATE_INTERVAL//60} мин")
        logger.info(f"🚀 Памп-сканер: каждые {PUMP_SCAN_INTERVAL} сек")
        
        last_full_scan = 0
        
        try:
            while True:
                current_time = time.time()
                
                pump_signals = await self.fast_pump_scan()
                if pump_signals:
                    for pump in pump_signals:
                        await self.send_pump_signal(pump)
                        await asyncio.sleep(3)
                
                if current_time - last_full_scan >= UPDATE_INTERVAL:
                    signals = await self.scan_all()
                    if signals:
                        for signal in signals:
                            if signal.get('signal_type') == 'accumulation':
                                await self.send_accumulation_signal(signal)
                            else:
                                await self.send_signal(signal)
                            await asyncio.sleep(3)
                    last_full_scan = current_time
                
                await asyncio.sleep(PUMP_SCAN_INTERVAL)
                
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
        self.breakout_tracker = BreakoutTracker() # Трекер пробоев
    def register(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("scan", self.scan))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("help", self.help))
        self.app.add_handler(CommandHandler("stats", self.stats_command))
        self.app.add_handler(CommandHandler("groups", self.groups_command))
        self.app.add_handler(CallbackQueryHandler(self.button))
        self.app.add_handler(CallbackQueryHandler(self.stats_button_handler, pattern="^stats_"))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 *Мульти-биржевой сканер*\n\n"
            "📊 *Доступные группы:*\n"
            "• Основная группа - обычные сигналы (LONG/SHORT)\n"
            "• Памп-группа - PUMP/DUMP сигналы\n"
            "• Накопление - ранние сигналы до импульса\n"
            "• Статистика - отчеты и метрики\n\n"
            "📋 *Команды:*\n"
            "/scan - Ручное сканирование\n"
            "/status - Статус бота\n"
            "/stats - Статистика сигналов\n"
            "/groups - Информация о группах\n"
            "/help - Помощь",
            parse_mode='Markdown'
        )
    
    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = await update.message.reply_text("🔍 Сканирую...")
        signals = await self.bot.scan_all()
        if signals:
            await msg.edit_text(f"✅ Найдено {len(signals)} сигналов")
            for signal in signals:
                if signal.get('signal_type') == 'accumulation':
                    await self.bot.send_accumulation_signal(signal)
                else:
                    await self.bot.send_signal(signal)
                await asyncio.sleep(3)
        else:
            await msg.edit_text("❌ Сигналов не найдено")
    
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = "*📡 Статус:*\n\n"
        text += f"✅ BingX Futures: активен\n"
        text += f"📊 Групп: 4 (осн., памп, накопление, статистика)\n"
        text += f"📈 Последних сигналов: {len(self.bot.last_signals)}"
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def groups_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = "*📊 ГРУППЫ СИГНАЛОВ*\n\n"
        text += "🔹 *Основная группа* - обычные LONG/SHORT сигналы\n"
        text += "   Технический анализ, тренды, уровни\n\n"
        text += "🔹 *Памп-группа* - PUMP/DUMP сигналы\n"
        text += "   Движения >3%, импульсы и развороты\n\n"
        text += "🔹 *Накопление* - ранние сигналы\n"
        text += "   Дивергенции, аномальный объем, накопление\n\n"
        text += "🔹 *Статистика* - отчеты и метрики\n"
        text += "   Ежедневные отчеты, статистика по команде /stats"
        
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "*Помощь*\n\n"
            "📊 *Анализ:* RSI, MACD, EMA, VWAP\n"
            "🔥 *Дополнительно:* Дивергенции, имбалансы, фракталы\n"
            "📐 *Фибоначчи:* Коррекции и расширения\n"
            "📦 *Накопление:* Ранние сигналы до импульса\n"
            "🚀 *Памп-сканер:* каждые 30 сек\n\n"
            "📋 *Команды:*\n"
            "/scan - ручное сканирование\n"
            "/status - состояние бота\n"
            "/stats - статистика сигналов\n"
            "/groups - информация о группах",
            parse_mode='Markdown'
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_chat.id) != STATS_SETTINGS['stats_chat_id']:
            await update.message.reply_text("❌ Эта команда доступна только в группе статистики")
            return
        
        if not hasattr(self.bot, 'stats'):
            await update.message.reply_text("❌ Статистика не инициализирована")
            return
        
        text = update.message.text
        parts = text.split()
        
        days = 7
        signal_type = 'all'
        coin = None
        
        if len(parts) > 1:
            for part in parts[1:]:
                if part.isdigit():
                    days = int(part)
                elif part.lower() in ['pump', 'pumps']:
                    signal_type = 'pump'
                elif part.lower() in ['regular', 'обычные']:
                    signal_type = 'regular'
                elif part.lower() in ['accumulation', 'накопление']:
                    signal_type = 'accumulation'
                elif part.upper() in [p.split('/')[0] for p in PAIRS_TO_SCAN]:
                    coin = part.upper()
        
        stats = self.bot.stats.get_statistics(
            days=days, 
            signal_type=signal_type,
            coin=coin
        )
        
        msg = self.bot.stats.format_stats_message(stats, days, signal_type, coin)
        
        keyboard = [
            [InlineKeyboardButton("📊 Общая", callback_data="stats_7"),
             InlineKeyboardButton("🚀 Пампы", callback_data="stats_pump_7")],
            [InlineKeyboardButton("📦 Накопление", callback_data="stats_accum_7"),
             InlineKeyboardButton("📈 По монетам", callback_data="stats_coins")],
            [InlineKeyboardButton("❓ Помощь", callback_data="stats_help")]
        ]
        
        await update.message.reply_text(
            msg, 
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def stats_button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "stats_7":
            stats = self.bot.stats.get_statistics(days=7)
            msg = self.bot.stats.format_stats_message(stats, 7)
        elif data == "stats_pump_7":
            stats = self.bot.stats.get_statistics(days=7, signal_type='pump')
            msg = self.bot.stats.format_stats_message(stats, 7, signal_type='pump')
        elif data == "stats_regular_7":
            stats = self.bot.stats.get_statistics(days=7, signal_type='regular')
            msg = self.bot.stats.format_stats_message(stats, 7, signal_type='regular')
        elif data == "stats_accum_7":
            stats = self.bot.stats.get_statistics(days=7, signal_type='accumulation')
            msg = self.bot.stats.format_stats_message(stats, 7, signal_type='accumulation')
        elif data == "stats_coins":
            coins = set()
            for signal in self.bot.stats.db['signals'].values():
                coins.add(signal['coin'])
            
            msg = "📈 *Выберите монету:*\n\n"
            keyboard = []
            row = []
            
            for i, coin in enumerate(sorted(coins)[:12]):
                row.append(InlineKeyboardButton(coin, callback_data=f"stats_coin_{coin}"))
                if len(row) == 3:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="stats_back")])
            
            await query.edit_message_text(
                msg,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        elif data.startswith("stats_coin_"):
            coin = data.replace("stats_coin_", "")
            stats = self.bot.stats.get_statistics(days=7, coin=coin)
            msg = self.bot.stats.format_stats_message(stats, 7, coin=coin)
        elif data == "stats_help":
            msg = """
📚 *ПОМОЩЬ ПО СТАТИСТИКЕ*

Вы можете нажимать кнопки или вводить команды:

🔹 *Простые команды:*
/stats - статистика за 7 дней
/stats 30 - статистика за 30 дней
/stats 1 - статистика за сегодня

🔹 *По типу сигналов:*
/stats pump - только пампы
/stats regular - только обычные
/stats accumulation - только накопление

🔹 *По монетам:*
/stats BTC - по Bitcoin
/stats ETH 14 - по Ethereum за 14 дней

🔹 *Примеры:*
/stats 7 pump - пампы за неделю
/stats 30 accumulation - накопление за месяц
/stats 14 BTC - по BTC за 14 дней

📌 *Совет:* Просто нажимайте кнопки! 👆
"""
        elif data == "stats_back":
            stats = self.bot.stats.get_statistics(days=7)
            msg = self.bot.stats.format_stats_message(stats, 7)
        else:
            return
        
        keyboard = [
            [InlineKeyboardButton("📊 Общая", callback_data="stats_7"),
             InlineKeyboardButton("🚀 Пампы", callback_data="stats_pump_7")],
            [InlineKeyboardButton("📦 Накопление", callback_data="stats_accum_7"),
             InlineKeyboardButton("📈 По монетам", callback_data="stats_coins")],
            [InlineKeyboardButton("❓ Помощь", callback_data="stats_help")]
        ]
        
        await query.edit_message_text(
            msg,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        logger.info(f"🖱️ Нажата кнопка: {data}")
        
        if data.startswith("copy_"):
            coin = data.replace("copy_", "")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<code>{coin}</code>",
                parse_mode='HTML'
            )
            await query.answer(f"✅ {coin} скопирован")
            return
        
        elif data.startswith("refresh_"):
            coin = data.replace("refresh_", "")
            await query.edit_message_text(f"🔄 Обновляю сигнал по {coin}...")
            
            if coin in self.bot.last_signals:
                signal_data = self.bot.last_signals[coin]
                signal = signal_data['signal']
                
                contract_info, df = None, None
                for fetcher in self.bot.fetchers.values():
                    if fetcher.name == signal['exchange']:
                        contract_info = await fetcher.fetch_contract_info(signal['symbol'])
                        df = await fetcher.fetch_ohlcv(signal['symbol'], TIMEFRAMES.get('current', '15m'), limit=200)
                        break
                
                pump_percent = None
                if signal.get('pump_dump') and len(signal['pump_dump']) > 0:
                    pump_percent = signal['pump_dump'][0].get('change_percent')
                
                msg, keyboard = self.bot.format_message(signal, contract_info, pump_percent)
                
                if df is not None and not df.empty:
                    df = self.bot.analyzer.calculate_indicators(df)
                    chart_buf = self.bot.chart_generator.create_chart(df, signal, coin, TIMEFRAMES.get('current', '15m'))
                    await query.message.delete()
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=chart_buf,
                        caption=msg,
                        parse_mode='HTML',
                        reply_markup=keyboard
                    )
                else:
                    await query.edit_message_text(text=msg, parse_mode='HTML', reply_markup=keyboard)
                await query.answer("🔄 Сигнал обновлен")
            else:
                await query.edit_message_text(f"❌ Нет данных для {coin}")
            return
        
        elif data.startswith("details_"):
            coin = data.replace("details_", "")
            if coin in self.bot.last_signals:
                signal_data = self.bot.last_signals[coin]
                signal = signal_data['signal']
                signal_time = signal_data['time'].strftime('%Y-%m-%d %H:%M:%S')
                
                for fetcher in self.bot.fetchers.values():
                    if fetcher.name == signal['exchange']:
                        detailed, keyboard = await self.bot.get_detailed_analysis(
                            fetcher, signal['symbol'], coin, signal_time
                        )
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=detailed,
                            parse_mode='Markdown',
                            reply_markup=keyboard
                        )
                        await query.answer("📊 Детали загружены")
                        return
            await query.answer(f"❌ Нет данных для {coin}")
            return
        
        elif data.startswith("back_"):
            coin = data.replace("back_", "")
            if coin in self.bot.last_signals:
                signal = self.bot.last_signals[coin]['signal']
                contract_info = None
                for fetcher in self.bot.fetchers.values():
                    if fetcher.name == signal['exchange']:
                        contract_info = await fetcher.fetch_contract_info(signal['symbol'])
                        break
                pump_percent = None
                if signal.get('pump_dump') and len(signal['pump_dump']) > 0:
                    pump_percent = signal['pump_dump'][0].get('change_percent')
                msg, keyboard = self.bot.format_message(signal, contract_info, pump_percent)
                await query.edit_message_text(
                    text=msg,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                await query.answer("↩️ Возврат к сигналу")
            return
    
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
