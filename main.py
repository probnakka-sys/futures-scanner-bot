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
    PERFORMANCE_SETTINGS
)

# Импорт системы статистики
from signal_stats import SignalStatistics

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

# ============== ГЕНЕРАТОР ГРАФИКОВ ==============

class ChartGenerator:
    """Генератор графиков для сигналов"""
    
    def __init__(self):
        self.figsize = (12, 6)
        self.dpi = 100
        self.style = 'dark_background'
        
    def create_chart(self, df: pd.DataFrame, signal: Dict, coin: str, timeframe: str = '15m') -> BytesIO:
        """Создание графика с ценой, индикаторами и целями"""
        plt.style.use(self.style)
        
        plot_df = df.tail(100).copy()
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=self.figsize, 
                                       gridspec_kw={'height_ratios': [3, 1]})
        
        # ===== ВЕРХНИЙ ГРАФИК =====
        ax1.plot(plot_df.index, plot_df['close'], 
                color='white', linewidth=2, label='Close')
        
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
        
        if 'BBL_20_2.0' in plot_df.columns and 'BBU_20_2.0' in plot_df.columns:
            ax1.fill_between(plot_df.index, 
                            plot_df['BBL_20_2.0'], 
                            plot_df['BBU_20_2.0'],
                            alpha=0.2, color='gray', label='Bollinger Bands')
        
        current_price = signal['price']
        ax1.axhline(y=current_price, color='#00ff00', 
                   linestyle='--', linewidth=1.5, alpha=0.8,
                   label=f'Current: {current_price:.2f}')
        
        if signal.get('target_1'):
            ax1.axhline(y=signal['target_1'], color='#ffaa00', 
                       linestyle='--', linewidth=1.5, alpha=0.8,
                       label=f'Target 1: {signal["target_1"]}')
        if signal.get('target_2'):
            ax1.axhline(y=signal['target_2'], color='#00ff00', 
                       linestyle='--', linewidth=1.5, alpha=0.8,
                       label=f'Target 2: {signal["target_2"]}')
        if signal.get('stop_loss'):
            ax1.axhline(y=signal['stop_loss'], color='#ff0000', 
                       linestyle='--', linewidth=1.5, alpha=0.8,
                       label=f'Stop: {signal["stop_loss"]}')
        
        if signal.get('accumulation'):
            emoji = " "
        elif signal.get('pump_dump'):
            emoji = " "
        elif 'LONG' in signal['direction']:
            emoji = " "
        elif 'SHORT' in signal['direction']:
            emoji = " "
        else:
            emoji = " "
        
        ax1.set_title(f'{emoji} {coin} - {signal["direction"]} (TF: {timeframe}, уверенность {signal["confidence"]}%)', 
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
                    
                    result['signals'].append(f"📐 {tf_ru}: {r['description']}")
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
                    
                    result['signals'].append(f"📐 {tf_ru}: {r['description']}")
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

# ============== МУЛЬТИТАЙМФРЕЙМ АНАЛИЗАТОР ==============

class MultiTimeframeAnalyzer:
    def __init__(self):
        self.divergence = DivergenceAnalyzer() if FEATURES['advanced']['divergence'] else None
        self.smc = SmartMoneyAnalyzer(SMC_SETTINGS) if FEATURES['advanced']['smart_money'] else None
        self.fractal = FractalAnalyzer(FRACTAL_SETTINGS) if FEATURES['advanced']['fractals'] else None
        self.fibonacci = None
        self.volume_profile = None
        self.accumulation = None
        
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
        """
        Генерация торгового сигнала на основе всех индикаторов
        """
        logger.info(f"🔄 generate_signal начал работу для {symbol}")
        
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
            confidence += INDICATOR_WEIGHTS['ema_cross']
        elif last['ema_9'] < last['ema_21'] and prev['ema_9'] >= prev['ema_21']:
            reasons.append("Медвежье пересечение EMA (9/21)")
            confidence += INDICATOR_WEIGHTS['ema_cross']
        
        # ===== ОБЪЕМ =====
        if last['volume_ratio'] > 1.5:
            reasons.append(f"Объем x{last['volume_ratio']:.1f} от нормы")
            confidence += INDICATOR_WEIGHTS['volume']
        
        # ===== VWAP =====
        if FEATURES['advanced']['vwap'] and 'vwap' in df.columns:
            if last['close'] > last['vwap']:
                reasons.append(f"Цена выше VWAP ({last['vwap']:.2f})")
                confidence += 10
            else:
                reasons.append(f"Цена ниже VWAP ({last['vwap']:.2f})")
                confidence += 10
        
        # ===== СИГНАЛЫ ОТ СТАРШИХ ТАЙМФРЕЙМОВ =====
        for signal in alignment['signals']:
            reasons.append(f"{signal}")
            if "НЕДЕЛЬНЫЙ" in signal:
                confidence += INDICATOR_WEIGHTS['weekly_trend']
            elif "Дневной" in signal:
                confidence += INDICATOR_WEIGHTS['daily_trend']
        
        # ===== СОГЛАСОВАННОСТЬ ТРЕНДОВ =====
        if alignment['trend_alignment'] > 70:
            reasons.append(f"Тренды согласованы ({alignment['trend_alignment']:.0f}%)")
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
        
        # ===== VOLUME PROFILE (ВРЕМЕННО ОТКЛЮЧЕН) =====
        vp_analysis = None
        # if self.volume_profile and FEATURES['advanced']['volume_profile']:
        #     vp_analysis = self.volume_profile.analyze_multi_timeframe(dataframes)
        #     if vp_analysis['has_confluence']:
        #         for signal in vp_analysis['signals']:
        #             reasons.append(signal)
        #         confidence += vp_analysis['strength'] / 5
        
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
                
                # Добавляем расчет потенциала
                potential = self.accumulation.calculate_potential(df, dataframes)
                if potential['has_potential']:
                    accumulation_analysis['potential'] = potential
                    for reason in potential['reasons']:
                        reasons.append(reason)
                    logger.info(f"  📈 {symbol} - Потенциал: {potential['target_pct']}%")
                
                if accumulation_analysis.get('direction'):
                    direction = accumulation_analysis['direction'] + ' 📈 (накопление)' if accumulation_analysis['direction'] == 'LONG' else 'SHORT 📉 (накопление)'
                logger.info(f"  ✅ {symbol} - Накопление: найдено {len(accumulation_analysis['signals'])} сигналов")
        
        # ===== ФАНДИНГ =====
        funding = metadata.get('funding_rate')
        if funding is not None and funding != 0:
            funding_pct = funding * 100
            if funding > 0.001:
                reasons.append(f"Позитивный фандинг ({funding_pct:.4f}%)")
                if confidence > 60:
                    direction = 'SHORT 📉'
            elif funding < -0.001:
                reasons.append(f"Негативный фандинг ({funding_pct:.4f}%)")
                if confidence > 60:
                    direction = 'LONG 📈'
        
        # ===== ОПРЕДЕЛЕНИЕ НАПРАВЛЕНИЯ =====
        bullish_keywords = ['перепродан', 'Бычье', 'восходящий', 'негативный фандинг', 'выше VWAP']
        bearish_keywords = ['перекуплен', 'Медвежье', 'нисходящий', 'позитивный фандинг', 'ниже VWAP']
        bullish = sum(1 for r in reasons if any(k in r for k in bullish_keywords))
        bearish = sum(1 for r in reasons if any(k in r for k in bearish_keywords))
        
        if accumulation_analysis and accumulation_analysis.get('direction') and accumulation_analysis.get('has_accumulation'):
            # Для накопления оставляем направление из анализатора
            pass
        elif bullish > bearish and confidence >= MIN_CONFIDENCE:
            if alignment['weekly_trend'] == 'ВОСХОДЯЩИЙ 📈':
                direction = 'Разворот LONG 📈'
                reasons.append("Подтверждение разворота недельным трендом")
            else:
                direction = 'LONG 📈'
        elif bearish > bullish and confidence >= MIN_CONFIDENCE:
            if alignment['weekly_trend'] == 'НИСХОДЯЩИЙ 📉':
                direction = 'Разворот SHORT 📉'
                reasons.append("Подтверждение разворота недельным трендом")
            else:
                direction = 'SHORT 📉'
        
        logger.info(f"  📊 {symbol} - Направление: {direction}, Уверенность: {confidence}")
        
        # ===== РАСЧЕТ СИЛЫ СИГНАЛА =====
        signal_strength = (confidence + alignment['trend_alignment']) / 2
        
        signal_power = "⚡️"
        if signal_strength >= 85:
            signal_power = "🔥🔥🔥 ОЧЕНЬ СИЛЬНЫЙ"
        elif signal_strength >= 70:
            signal_power = "🔥🔥 СИЛЬНЫЙ"
        elif signal_strength >= 55:
            signal_power = "🔥 СРЕДНИЙ"
        elif signal_strength >= 40:
            signal_power = "📊 СЛАБЫЙ"
        else:
            signal_power = "👀 НАБЛЮДЕНИЕ"
        
        if direction == 'NEUTRAL':
            logger.info(f"⏭️ NEUTRAL сигнал для {symbol}")
            return None
        
        # ===== РАСЧЕТ ЦЕЛЕЙ ПО ATR =====
        atr = last['atr'] if pd.notna(last['atr']) else (last['high'] - last['low']) * 0.3
        current_price = last['close']
        targets = {}
        
        if 'LONG' in direction:
            if current_price < 0.0001:
                targets['target_1'] = round(current_price + atr * ATR_SETTINGS['long_target_1_mult'], 8)
                targets['target_2'] = round(current_price + atr * ATR_SETTINGS['long_target_2_mult'], 8)
                targets['stop_loss'] = round(current_price - atr * ATR_SETTINGS['long_stop_loss_mult'], 8)
            elif current_price < 0.001:
                targets['target_1'] = round(current_price + atr * ATR_SETTINGS['long_target_1_mult'], 6)
                targets['target_2'] = round(current_price + atr * ATR_SETTINGS['long_target_2_mult'], 6)
                targets['stop_loss'] = round(current_price - atr * ATR_SETTINGS['long_stop_loss_mult'], 6)
            elif current_price < 0.01:
                targets['target_1'] = round(current_price + atr * ATR_SETTINGS['long_target_1_mult'], 5)
                targets['target_2'] = round(current_price + atr * ATR_SETTINGS['long_target_2_mult'], 5)
                targets['stop_loss'] = round(current_price - atr * ATR_SETTINGS['long_stop_loss_mult'], 5)
            elif current_price < 0.1:
                targets['target_1'] = round(current_price + atr * ATR_SETTINGS['long_target_1_mult'], 4)
                targets['target_2'] = round(current_price + atr * ATR_SETTINGS['long_target_2_mult'], 4)
                targets['stop_loss'] = round(current_price - atr * ATR_SETTINGS['long_stop_loss_mult'], 4)
            elif current_price < 1.0:
                targets['target_1'] = round(current_price + atr * ATR_SETTINGS['long_target_1_mult'], 3)
                targets['target_2'] = round(current_price + atr * ATR_SETTINGS['long_target_2_mult'], 3)
                targets['stop_loss'] = round(current_price - atr * ATR_SETTINGS['long_stop_loss_mult'], 3)
            else:
                targets['target_1'] = round(current_price + atr * ATR_SETTINGS['long_target_1_mult'], 2)
                targets['target_2'] = round(current_price + atr * ATR_SETTINGS['long_target_2_mult'], 2)
                targets['stop_loss'] = round(current_price - atr * ATR_SETTINGS['long_stop_loss_mult'], 2)
        else:
            if current_price < 0.0001:
                targets['target_1'] = round(current_price - atr * ATR_SETTINGS['short_target_1_mult'], 8)
                targets['target_2'] = round(current_price - atr * ATR_SETTINGS['short_target_2_mult'], 8)
                targets['stop_loss'] = round(current_price + atr * ATR_SETTINGS['short_stop_loss_mult'], 8)
            elif current_price < 0.001:
                targets['target_1'] = round(current_price - atr * ATR_SETTINGS['short_target_1_mult'], 6)
                targets['target_2'] = round(current_price - atr * ATR_SETTINGS['short_target_2_mult'], 6)
                targets['stop_loss'] = round(current_price + atr * ATR_SETTINGS['short_stop_loss_mult'], 6)
            elif current_price < 0.01:
                targets['target_1'] = round(current_price - atr * ATR_SETTINGS['short_target_1_mult'], 5)
                targets['target_2'] = round(current_price - atr * ATR_SETTINGS['short_target_2_mult'], 5)
                targets['stop_loss'] = round(current_price + atr * ATR_SETTINGS['short_stop_loss_mult'], 5)
            elif current_price < 0.1:
                targets['target_1'] = round(current_price - atr * ATR_SETTINGS['short_target_1_mult'], 4)
                targets['target_2'] = round(current_price - atr * ATR_SETTINGS['short_target_2_mult'], 4)
                targets['stop_loss'] = round(current_price + atr * ATR_SETTINGS['short_stop_loss_mult'], 4)
            elif current_price < 1.0:
                targets['target_1'] = round(current_price - atr * ATR_SETTINGS['short_target_1_mult'], 3)
                targets['target_2'] = round(current_price - atr * ATR_SETTINGS['short_target_2_mult'], 3)
                targets['stop_loss'] = round(current_price + atr * ATR_SETTINGS['short_stop_loss_mult'], 3)
            else:
                targets['target_1'] = round(current_price - atr * ATR_SETTINGS['short_target_1_mult'], 2)
                targets['target_2'] = round(current_price - atr * ATR_SETTINGS['short_target_2_mult'], 2)
                targets['stop_loss'] = round(current_price + atr * ATR_SETTINGS['short_stop_loss_mult'], 2)
        
        logger.info(f"  📈 {symbol} - ATR: {atr}, Цели: {targets}")
        
        # ===== ФОРМИРОВАНИЕ РЕЗУЛЬТАТА =====
        result = {
            'symbol': symbol,
            'exchange': exchange,
            'price': current_price,
            'direction': direction,
            'signal_type': signal_type,
            'signal_power': signal_power,
            'confidence': round(confidence, 1),
            'signal_strength': round(signal_strength, 1),
            'reasons': reasons[:6],
            'funding_rate': metadata.get('funding_rate', 0),
            'volume_24h': metadata.get('volume_24h', 0),
            'price_change_24h': metadata.get('price_change_24h', 0),
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'alignment': alignment,
            **targets
        }
        
        if fib_analysis:
            result['fibonacci'] = fib_analysis
        if vp_analysis:
            result['volume_profile'] = vp_analysis
        if accumulation_analysis:
            result['accumulation'] = accumulation_analysis
        
        logger.info(f"✅ generate_signal успешно завершен для {symbol}")
        return result

# ============== БЫСТРЫЙ ПАМП-СКАНЕР ==============

class FastPumpScanner:
    def __init__(self, fetcher: BaseExchangeFetcher, settings: Dict = None, analyzer=None):
        self.fetcher = fetcher
        self.settings = settings or PUMP_SCAN_SETTINGS
        self.analyzer = analyzer
        self.threshold = self.settings.get('threshold', 3.0)
        self.instant_threshold = self.settings.get('instant_threshold', 1.0)  # Снижено до 1%
        self.shitcoin_instant_threshold = self.settings.get('shitcoin_instant_threshold', 0.8)  # Для щиткоинов 0.8%
        self.shitcoin_volume_threshold = self.settings.get('shitcoin_volume_threshold', 1_000_000)  # 1M$ = порог щиткоина
        self.timeframes = self.settings.get('timeframes', ['1m', '3m', '5m', '15m', '30m'])
        self.max_pairs = self.settings.get('max_pairs_to_scan', 600)
        self.websocket_top_pairs = self.settings.get('websocket_top_pairs', 100)
        self.last_pump_signals = {}
        self.cache = CacheManager(ttl=30)
        
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
        Обработка мгновенного сигнала от WebSocket с разными порогами
        """
        try:
            # Определяем, щиткоин или нет
            is_shitcoin = False
            try:
                ticker = await self.fetcher.fetch_ticker(symbol)
                volume = ticker.get('volume_24h', 1_000_000)
                if volume < self.shitcoin_volume_threshold:
                    is_shitcoin = True
            except:
                pass
            
            # Выбираем порог срабатывания
            if is_shitcoin:
                threshold = self.shitcoin_instant_threshold
                coin_type = "Щиткоин"
            else:
                threshold = self.instant_threshold
                coin_type = "Мейджор"
            
            # Проверяем по порогу
            if abs(movement['change_percent']) >= threshold:
                logger.info(f"⚡ {coin_type} {symbol}: {movement['change_percent']:+.1f}% за {movement['time_window']:.1f} сек")
                
                # Добавляем в очередь для обработки
                await self.instant_signals_queue.put({
                    'symbol': symbol,
                    'price': price,
                    'movement': movement,
                    'is_shitcoin': is_shitcoin,
                    'time': datetime.now()
                })
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
                                
                                # ✅ ДОБАВЛЯЕМ ОТПРАВКУ В TELEGRAM
                                try:
                                    contract_info = await self.fetcher.fetch_contract_info(pair)
                                    msg, keyboard = self.format_pump_message(signal, contract_info)
                                    
                                    await self.fetcher.telegram_bot.send_message(
                                        chat_id=PUMP_CHAT_ID,
                                        text=msg,
                                        parse_mode='HTML',
                                        reply_markup=keyboard
                                    )
                                    logger.info(f"✅ Отправлен памп-сигнал: {pair}")
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
        Оптимизированное сканирование всех пар с гибридным подходом
        """
        logger.info("🚀 ЗАПУСК БЫСТРОГО ПАМП-СКАНЕРА (ГИБРИДНЫЙ)")
        
        try:
            all_pairs = await self.fetcher.fetch_all_pairs()
            if not all_pairs:
                return []
            
            # Запускаем WebSocket мониторинг для быстрых сигналов
            if self.websocket_available:
                await self.start_websocket_monitoring(all_pairs)
            
            scan_pairs = all_pairs[:self.max_pairs]
            random.shuffle(scan_pairs)
            logger.info(f"📊 Памп-сканер: анализирую {len(scan_pairs)} пар (WebSocket: {self.websocket_available})")
            
            pump_signals = []
            
            # Разбиваем на батчи для параллельной обработки
            batches = [scan_pairs[i:i+self.batch_size] for i in range(0, len(scan_pairs), self.batch_size)]
            
            for batch_num, batch in enumerate(batches):
                logger.info(f"🔄 Обработка батча {batch_num + 1}/{len(batches)} ({len(batch)} пар)")
                
                # Параллельная обработка батча
                tasks = [self.scan_pair(pair) for pair in batch]
                batch_results = await asyncio.gather(*tasks)
                
                # Собираем результаты
                for signal in batch_results:
                    if signal:
                        pump_signals.append(signal)
                        change = signal['pump_dump'][0]['change_percent']
                        logger.info(f"✅ Памп-сигнал (REST): {signal['symbol']} {change:+.1f}%")
                
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
    
    def format_pump_message(self, signal: Dict, contract_info: Dict = None) -> Tuple[str, InlineKeyboardMarkup]:
        """
        Форматирование памп-сигнала для отправки
        """
        coin = signal['symbol'].split('/')[0].replace('USDT', '')
        
        # Определяем эмодзи и тип сигнала
        if signal.get('signal_type') == "PUMP":
            signal_emoji = "🚀"
            signal_text = f"PUMP {signal['pump_dump'][0]['change_percent']:+.1f}%"
        elif signal.get('signal_type') == "DUMP":
            signal_emoji = "📉"
            signal_text = f"DUMP {signal['pump_dump'][0]['change_percent']:+.1f}%"
        else:
            signal_emoji = "📊"
            signal_text = f"{signal['pump_dump'][0]['change_percent']:+.1f}%"
        
        line1 = f"{signal_emoji} <code>{coin}</code> {signal_text} {signal['signal_power']}"
        
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
        line6 = f"🕓 Таймфрейм: {signal.get('timeframe', '15m')}"
        
        # Форматирование цены
        if signal['price'] < 0.001:
            price_formatted = f"{signal['price']:.8f}".rstrip('0').rstrip('.')
            start_formatted = f"{signal['pump_dump'][0]['start_price']:.8f}".rstrip('0').rstrip('.')
        else:
            price_formatted = f"{signal['price']:.4f}"
            start_formatted = f"{signal['pump_dump'][0]['start_price']:.4f}"
        
        line7 = f"💰 Цена текущая: {price_formatted}"
        line8 = f"📈 Рост: {start_formatted} → {price_formatted}"
        
        # Форматирование целей
        if signal.get('target_1') and signal.get('target_2') and signal.get('stop_loss'):
            def format_target(price):
                if price < 0.001:
                    return f"{price:.8f}".rstrip('0').rstrip('.')
                elif price < 1:
                    return f"{price:.4f}".rstrip('0').rstrip('.')
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
        for reason in signal['reasons'][:3]:
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
            clean_reason = clean_reason.strip()
            clean_reasons.append(clean_reason)
        
        reasons_lines = [f"     {r}" for r in clean_reasons]
        
        lines = [line1, line2, line3, line4, line5, line6, line7, line8, line9, line10, line11] + reasons_lines
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

# ============== ОСНОВНОЙ КЛАСС БОТА ==============

class MultiExchangeScannerBot:
    def __init__(self):
        self.fetchers = {}
        self.analyzer = MultiTimeframeAnalyzer()
        self.chart_generator = ChartGenerator()
        self.telegram_bot = Bot(token=TELEGRAM_TOKEN)
        self.last_signals = {}
        
        self.divergence = DivergenceAnalyzer() if FEATURES['advanced']['divergence'] else None
        self.imbalance = ImbalanceAnalyzer(IMBALANCE_SETTINGS) if FEATURES['advanced']['imbalance'] else None
        self.liquidity = LiquidityAnalyzer(LIQUIDITY_SETTINGS) if FEATURES['advanced']['liquidity'] else None
        
        # Инициализация дополнительных анализаторов
        if FEATURES['advanced']['fibonacci']:
            from config import FIBONACCI_SETTINGS
            self.fibonacci = FibonacciAnalyzer(FIBONACCI_SETTINGS)
            self.analyzer.set_fibonacci(self.fibonacci)
            logger.info("✅ Анализатор Фибоначчи инициализирован")
        
        if FEATURES['advanced']['volume_profile'] and VOLUME_PROFILE_SETTINGS.get('enabled', False):
            from config import VOLUME_PROFILE_SETTINGS
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
        if FEATURES['exchanges'].get('bingx', {}).get('enabled', False):
            self.fetchers['BingX'] = BingxFetcher()
        
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
    
    def format_message(self, signal: Dict, contract_info: Dict = None, pump_percent: float = None) -> Tuple[str, InlineKeyboardMarkup]:
        # Определяем эмодзи и тип сигнала
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
        
        line1 = f"{main_emoji} <code>{coin}</code>{pump_text} {signal['signal_power']}"
        
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
            
            line2 = f"📌 {max_lev}x / {min_amt:.0f}$ / {self.format_compact(max_amt)}"
            
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
        
        # Форматирование цены с правильной точностью
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
        
        # Потенциал для сигналов накопления
        line_potential = ""
        if signal.get('signal_type') == 'accumulation' and signal.get('accumulation', {}).get('potential'):
            potential = signal['accumulation']['potential']
            if potential['has_potential']:
                direction_emoji = "📈" if potential['target_pct'] > 0 else "📉"
                # Форматируем цену цели с правильной точностью
                if potential['target_price'] < 0.001:
                    target_price_str = f"{potential['target_price']:.6f}".rstrip('0').rstrip('.')
                elif potential['target_price'] < 1:
                    target_price_str = f"{potential['target_price']:.4f}".rstrip('0').rstrip('.')
                else:
                    target_price_str = f"{potential['target_price']:.2f}"
                
                # Переводим название таймфрейма на русский
                tf_ru = {
                    'monthly': 'месячном',
                    'weekly': 'недельном',
                    'daily': 'дневном',
                    'hourly': 'часовом',
                    'current': 'текущем'
                }.get(potential['timeframe'], potential['timeframe'])
                
                line_potential = f"{direction_emoji} Потенциал: {potential['target_pct']:+.2f}% до {target_price_str} ({potential['target_level']} на {tf_ru})"
        
        line8 = ""
        if pump_percent and signal.get('pump_dump') and len(signal['pump_dump']) > 0:
            pump_data = signal['pump_dump'][0]
            start_price = pump_data.get('start_price', signal['price'] / (1 + pump_percent/100))
            if start_price < 0.001:
                start_formatted = f"{start_price:.8f}".rstrip('0').rstrip('.')
            else:
                start_formatted = f"{start_price:.4f}"
            line8 = f"📈 Рост: {start_formatted} → {price_formatted}"
        
        if signal.get('target_1') and signal.get('target_2') and signal.get('stop_loss'):
            # Форматируем цели с правильной точностью
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
        
        clean_reasons = []
        for reason in signal['reasons'][:4]:
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
            clean_reason = clean_reason.strip()
            clean_reasons.append(clean_reason)
        
        reasons_lines = [f"     {r}" for r in clean_reasons]
        
        # Собираем строки в правильном порядке
        lines = [line1, line2, line3, line4, line5, line6, line7]
        
        # Добавляем потенциал сразу после текущей цены
        if line_potential:
            lines.append(line_potential)
        
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
            scanner = FastPumpScanner(fetcher, PUMP_SCAN_SETTINGS, self.analyzer)
            signals = await scanner.scan_all_pairs()
            
            for signal in signals:
                contract_info = await fetcher.fetch_contract_info(signal['symbol'])
                # Убедимся, что funding_rate не потерялся
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
