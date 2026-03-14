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
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import RetryAfter, TimedOut
from telegram import Update
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
    DISPLAY_SETTINGS
)

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

# ============== ГЕНЕРАТОР ГРАФИКОВ ==============

class ChartGenerator:
    """Генератор графиков для сигналов"""
    
    def __init__(self):
        self.figsize = (12, 6)
        self.dpi = 100
        self.style = 'dark_background'
        
    def create_chart(self, df: pd.DataFrame, signal: Dict, coin: str) -> BytesIO:
        """
        Создание графика с ценой, индикаторами и целями
        """
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
        
        direction_emoji = "🟢" if "LONG" in signal['direction'] else "🔴" if "SHORT" in signal['direction'] else "⚪"
        ax1.set_title(f'{direction_emoji} {coin} - {signal["direction"]} (уверенность {signal["confidence"]}%)', 
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
    """
    
    def __init__(self, settings: Dict = None):
        self.settings = settings or FIBONACCI_SETTINGS
        self.retracement_levels = self.settings.get('retracement_levels', 
                                                    [0.236, 0.382, 0.5, 0.618, 0.786, 0.86])
        self.extension_levels = self.settings.get('extension_levels', 
                                                  [0.18, 0.27, 0.618])
        self.lookback_candles = self.settings.get('lookback_candles', 3)
        self.min_distance = self.settings.get('min_distance_pct', 0.5)
    
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
                reactions.append({
                    'level': key,
                    'price': level_price,
                    'strength': strength,
                    'description': f"{level_data['description']} ({direction})"
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
                    result['signals'].append(f"📐 {timeframe}: {r['description']}")
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
                    result['signals'].append(f"📐 {timeframe}: {r['description']}")
                    result['strength'] = max(result['strength'], r['strength'])
                    result['levels'] = levels
        
        return result
    
    def analyze_multi_timeframe(self, dataframes: Dict[str, pd.DataFrame]) -> Dict:
        """Мультитаймфреймовый анализ Фибоначчи"""
        result = {'has_confluence': False, 'signals': [], 'strength': 0, 'levels': {}}
        
        for tf_name, df in dataframes.items():
            if df is None or df.empty:
                continue
            
            tf_result = self.analyze(df, tf_name)
            if tf_result['has_signal']:
                weight = 1.0
                if tf_name == 'monthly':
                    weight = 3.0
                elif tf_name == 'weekly':
                    weight = 2.5
                elif tf_name == 'daily':
                    weight = 2.0
                elif tf_name == 'hourly':
                    weight = 1.5
                
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
        try:
            markets = await self.exchange.load_markets()
            market = markets.get(symbol, {})
            limits = market.get('limits', {})
            
            max_leverage = 100
            min_amount = 5.0
            max_amount = 2_000_000
            
            if limits.get('leverage'):
                max_leverage = limits['leverage'].get('max', 100)
            if limits.get('amount'):
                min_amount = limits['amount'].get('min', 5.0)
                max_amount = limits['amount'].get('max', 2_000_000)
            
            return {
                'max_leverage': max_leverage,
                'min_amount': min_amount,
                'max_amount': max_amount,
                'has_data': True
            }
        except Exception as e:
            logger.debug(f"Нет данных контракта для {symbol}, использую стандартные")
            return {
                'max_leverage': 100,
                'min_amount': 5.0,
                'max_amount': 2_000_000,
                'has_data': False
            }
    
    async def close(self):
        await self.exchange.close()

# ============== МУЛЬТИТАЙМФРЕЙМ АНАЛИЗАТОР ==============

class MultiTimeframeAnalyzer:
    def __init__(self):
        self.divergence = DivergenceAnalyzer() if FEATURES['advanced']['divergence'] else None
        self.smc = SmartMoneyAnalyzer(SMC_SETTINGS) if FEATURES['advanced']['smart_money'] else None
        self.fractal = FractalAnalyzer(FRACTAL_SETTINGS) if FEATURES['advanced']['fractals'] else None
    
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
            reasons.append(f"{signal}")
            if "НЕДЕЛЬНЫЙ" in signal:
                confidence += INDICATOR_WEIGHTS['weekly_trend']
            elif "Дневной" in signal:
                confidence += INDICATOR_WEIGHTS['daily_trend']
        
        if alignment['trend_alignment'] > 70:
            reasons.append(f"Тренды согласованы ({alignment['trend_alignment']:.0f}%)")
            confidence += INDICATOR_WEIGHTS['trend_alignment']
        
            # Анализ Фибоначчи
        if hasattr(self, 'fibonacci') and self.fibonacci and FEATURES['advanced']['fibonacci']:
            fib_analysis = self.fibonacci.analyze_multi_timeframe(dataframes)
            if fib_analysis['has_confluence']:
                for signal in fib_analysis['signals']:
                    reasons.append(signal)
                confidence += fib_analysis['strength'] / 5
                # Сохраняем в результат (будет добавлен позже)
                fib_result = fib_analysis

        # Анализ Volume Profile
        if hasattr(self, 'volume_profile') and self.volume_profile and FEATURES['advanced']['volume_profile']:
            vp_analysis = self.volume_profile.analyze_multi_timeframe(dataframes)
            if vp_analysis['has_confluence']:
                for signal in vp_analysis['signals']:
                    reasons.append(signal)
                confidence += vp_analysis['strength'] / 5
                # Сохраняем в результат (будет добавлен позже)
                vp_result = vp_analysis

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
        
        bullish_keywords = ['перепродан', 'Бычье', 'восходящий', 'негативный фандинг', 'выше VWAP']
        bearish_keywords = ['перекуплен', 'Медвежье', 'нисходящий', 'позитивный фандинг', 'ниже VWAP']
        bullish = sum(1 for r in reasons if any(k in r for k in bullish_keywords))
        bearish = sum(1 for r in reasons if any(k in r for k in bearish_keywords))
        
        if bullish > bearish and confidence >= MIN_CONFIDENCE:
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
            return None
        
        atr = last['atr'] if pd.notna(last['atr']) else (last['high'] - last['low']) * 0.3
        current_price = last['close']
        targets = {}
        
        if 'LONG' in direction:
            targets['target_1'] = round(current_price + atr * 1.5, 2)
            targets['target_2'] = round(current_price + atr * 3.0, 2)
            targets['stop_loss'] = round(current_price - atr * 1.0, 2)
        else:
            targets['target_1'] = round(current_price - atr * 1.5, 2)
            targets['target_2'] = round(current_price - atr * 3.0, 2)
            targets['stop_loss'] = round(current_price + atr * 1.0, 2)
        
        return {
            'symbol': symbol,
            'exchange': exchange,
            'price': current_price,
            'direction': direction,
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
         # Добавляем результаты анализов если они есть
        if 'fib_result' in locals():
            result['fibonacci'] = fib_result
        if 'vp_result' in locals():
            result['volume_profile'] = vp_result
        
        return result

# ============== БЫСТРЫЙ ПАМП-СКАНЕР ==============

class FastPumpScanner:
    def __init__(self, fetcher: BaseExchangeFetcher, settings: Dict = None, analyzer=None):
        self.fetcher = fetcher
        self.settings = settings or PUMP_SCAN_SETTINGS
        self.analyzer = analyzer
        self.threshold = self.settings.get('threshold', 5.0)
        self.timeframes = self.settings.get('timeframes', ['15m'])
        self.max_pairs = self.settings.get('max_pairs_to_scan', 300)
        self.last_pump_signals = {}
    
    async def scan_all_pairs(self) -> List[Dict]:
        logger.info("🚀 ЗАПУСК БЫСТРОГО ПАМП-СКАНЕРА")
        
        try:
            all_pairs = await self.fetcher.fetch_all_pairs()
            if not all_pairs:
                return []
            
            scan_pairs = all_pairs[:self.max_pairs]
            random.shuffle(scan_pairs)
            logger.info(f"📊 Памп-сканер: анализирую {len(scan_pairs)} пар")
            
            pump_signals = []
            
            for pair in scan_pairs:
                try:
                    for tf in self.timeframes:
                        limit = 20
                        df = await self.fetcher.fetch_ohlcv(pair, tf, limit=limit)
                        
                        if df is None or len(df) < 10:
                            continue
                        
                        if tf == '30m':
                            bars_ago = 1
                            minutes = 30
                        elif tf == '15m':
                            bars_ago = 2
                            minutes = 30
                        elif tf == '5m':
                            bars_ago = 6
                            minutes = 30
                        else:
                            bars_ago = 1
                            minutes = self._timeframe_to_minutes(tf)
                        
                        start_price = df['close'].iloc[-bars_ago-1]
                        current_price = df['close'].iloc[-1]
                        change_percent = (current_price - start_price) / start_price * 100
                        
                        if abs(change_percent) >= self.threshold:
                            signal_key = f"{pair}_{tf}"
                            last_time = self.last_pump_signals.get(signal_key)
                            
                            if last_time and (datetime.now() - last_time).total_seconds() < 1800:
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
                                else:
                                    signal = None
                            else:
                                signal = None
                            
                            if signal and 'NEUTRAL' not in signal['direction']:
                                signal['pump_dump'] = [{
                                    'change_percent': change_percent,
                                    'time_window': minutes,
                                    'start_price': start_price,
                                    'end_price': current_price
                                }]
                                signal['signal_type'] = "PUMP" if change_percent > 0 else "DUMP"
                                
                                pump_signals.append(signal)
                                self.last_pump_signals[signal_key] = datetime.now()
                                logger.info(f"✅ Памп-сигнал: {pair} {change_percent:+.1f}% за {minutes} мин")
                                break
                    
                except Exception as e:
                    continue
            
            pump_signals.sort(key=lambda x: abs(x['pump_dump'][0]['change_percent']), reverse=True)
            logger.info(f"🎯 Памп-сканер: найдено {len(pump_signals)} сигналов")
            return pump_signals
            
        except Exception as e:
            logger.error(f"❌ Ошибка памп-сканера: {e}")
            return []
    
    def _get_power_text(self, strength: float) -> str:
        if strength >= 90:
            return "🔥🔥🔥 ОЧЕНЬ СИЛЬНЫЙ"
        elif strength >= 75:
            return "🔥🔥 СИЛЬНЫЙ"
        elif strength >= 60:
            return "🔥 СРЕДНИЙ"
        else:
            return "⚡ СЛАБЫЙ"
    
    def _timeframe_to_minutes(self, tf: str) -> int:
        return {'5m': 5, '15m': 15, '30m': 30, '1h': 60}.get(tf, 15)
    
    def _format_compact(self, num: float) -> str:
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
        coin = signal['symbol'].split('/')[0].replace('USDT', '')
        
        line1 = f"🚀 {coin} {signal['signal_type']} {signal['pump_dump'][0]['change_percent']:+.1f}% {signal['signal_power']}"
        
        if contract_info:
            max_lev = contract_info.get('max_leverage', 100)
            min_amt = contract_info.get('min_amount', 5.0)
            max_amt = contract_info.get('max_amount', 2_000_000)
            
            volume_str = ""
            if signal.get('volume_24h') and signal['volume_24h'] is not None and signal['volume_24h'] > 0:
                vol = signal['volume_24h']
                if vol > 1_000_000:
                    volume_str = f" / {vol/1_000_000:.1f}M"
                elif vol > 1_000:
                    volume_str = f" / {vol/1_000:.1f}K"
                else:
                    volume_str = f" / {vol:.0f}"
            else:
                volume_str = " / N/A"
            
            line2 = f"📌 {max_lev}x / {min_amt:.0f}$ / {self._format_compact(max_amt)}{volume_str} / ⚪ 0.000%"
        else:
            line2 = "📌 100x / 5$ / 2.0M / N/A / ⚪ 0.000%"
        
        exchange_link = REF_LINKS.get(signal['exchange'], '#')
        line3 = f"💲 Trade: <a href='{exchange_link}'>{signal['exchange']}</a>"
        
        line4 = ""
        line5 = f"📊 Направление: {signal['direction']}"
        line6 = f"🕓 Таймфрейм: {signal.get('timeframe', '15m')}"
        
        if signal['price'] < 0.001:
            price_formatted = f"{signal['price']:.8f}".rstrip('0').rstrip('.')
            start_formatted = f"{signal['pump_dump'][0]['start_price']:.8f}".rstrip('0').rstrip('.')
        else:
            price_formatted = f"{signal['price']:.4f}"
            start_formatted = f"{signal['pump_dump'][0]['start_price']:.4f}"
        
        line7 = f"💰 Цена текущая: {price_formatted}"
        line8 = f"📈 Рост: {start_formatted} → {price_formatted}"
        
        if signal.get('target_1') and signal.get('target_2') and signal.get('stop_loss'):
            t1 = f"{signal['target_1']:.8f}".rstrip('0').rstrip('.') if signal['target_1'] < 0.001 else f"{signal['target_1']:.4f}"
            t2 = f"{signal['target_2']:.8f}".rstrip('0').rstrip('.') if signal['target_2'] < 0.001 else f"{signal['target_2']:.4f}"
            sl = f"{signal['stop_loss']:.8f}".rstrip('0').rstrip('.') if signal['stop_loss'] < 0.001 else f"{signal['stop_loss']:.4f}"
            line9 = f"🎯 Цели: {t1} | {t2} | SL {sl}"
        else:
            line9 = "🎯 Цели: N/A | N/A | SL N/A"
        
        line10 = ""
        line11 = "💡 Причины:"
        
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
        
        if FEATURES['advanced']['fibonacci']:
            from config import FIBONACCI_SETTINGS
            self.fibonacci = FibonacciAnalyzer(FIBONACCI_SETTINGS)
            logger.info("✅ Анализатор Фибоначчи инициализирован")

        if FEATURES['advanced']['volume_profile']:
            from config import VOLUME_PROFILE_SETTINGS
            self.volume_profile = VolumeProfileAnalyzer(VOLUME_PROFILE_SETTINGS)
            logger.info("✅ Volume Profile анализатор инициализирован")

        if FEATURES['exchanges'].get('bingx', {}).get('enabled', False):
            self.fetchers['BingX'] = BingxFetcher()
    
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
        if pump_percent:
            main_emoji = '🚀'
            coin = self.extract_coin(signal['symbol'])
            pump_text = f" {signal['pump_dump'][0]['change_percent']:+.1f}%"
        else:
            if 'LONG' in signal['direction']:
                main_emoji = '🟢'
            elif 'SHORT' in signal['direction']:
                main_emoji = '🔴'
            else:
                main_emoji = '⚪'
            coin = self.extract_coin(signal['symbol'])
            pump_text = ""
        
        line1 = f"{main_emoji} {coin}{pump_text} {signal['signal_power']}"
        
        if contract_info:
            max_lev = contract_info.get('max_leverage', 100)
            min_amt = contract_info.get('min_amount', 5.0)
            max_amt = contract_info.get('max_amount', 2_000_000)
            
            line2 = f"📌 {max_lev}x / {min_amt:.0f}$ / {self.format_compact(max_amt)}"
            
            if signal.get('volume_24h') and signal['volume_24h'] is not None and signal['volume_24h'] > 0:
                volume = signal['volume_24h']
                if volume > 1_000_000:
                    line2 += f" / {volume/1_000_000:.1f}M"
                elif volume > 1_000:
                    line2 += f" / {volume/1_000:.1f}K"
                else:
                    line2 += f" / {volume:.0f}"
            else:
                line2 += f" / N/A"
            
            funding_rate = signal.get('funding_rate', 0)
            if funding_rate is None:
                funding_rate = 0.0
            funding = funding_rate * 100
            funding_emoji = "🟢" if funding > 0 else "🔴" if funding < 0 else "⚪"
            line2 += f" / {funding_emoji} {funding:.3f}%"
        else:
            line2 = f"📌 100x / 5$ / 2.0M / N/A / ⚪ 0.000%"
        
        exchange_link = REF_LINKS.get(signal['exchange'], '#')
        line3 = f"💲 Trade: <a href='{exchange_link}'>{signal['exchange']}</a>"
        
        line4 = ""
        line5 = f"📊 Направление: {signal['direction']}"
        line6 = f"🕓 Таймфрейм: {TIMEFRAMES.get('current', '15m')}"
        
        if signal['price'] < 0.001:
            price_formatted = f"{signal['price']:.8f}".rstrip('0').rstrip('.')
        else:
            price_formatted = f"{signal['price']:.2f}"
        line7 = f"💰 Цена текущая: {price_formatted}"
        
        line8 = ""
        if pump_percent and signal.get('pump_dump') and len(signal['pump_dump']) > 0:
            pump_data = signal['pump_dump'][0]
            start_price = pump_data.get('start_price', signal['price'] / (1 + pump_percent/100))
            if start_price < 0.001:
                start_formatted = f"{start_price:.8f}".rstrip('0').rstrip('.')
            else:
                start_formatted = f"{start_price:.2f}"
            line8 = f"📈 Рост: {start_formatted} → {price_formatted}"
        
        if signal.get('target_1') and signal.get('target_2') and signal.get('stop_loss'):
            line9 = f"🎯 Цели: {signal['target_1']} | {signal['target_2']} | SL {signal['stop_loss']}"
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
            clean_reason = clean_reason.strip()
            clean_reasons.append(clean_reason)
        
        reasons_lines = [f"     {r}" for r in clean_reasons]
        
        lines = [line1, line2, line3, line4, line5, line6, line7]
        if line8:
            lines.append(line8)
        lines.extend([line9, line10, line11])
        lines.extend(reasons_lines)
        
        message = "\n".join(lines)
        
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
                return []
            
            scan_count = min(PAIRS_TO_SCAN, len(pairs))
            
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
                        'price_change_24h': ticker.get('percentage')
                    }
                    
                    signal = self.analyzer.generate_signal(dataframes, metadata, pair, name)
                    
                    if not signal:
                        continue
                    
                    if 'NEUTRAL' in signal['direction']:
                        continue
                    
                    if signal['confidence'] >= MIN_CONFIDENCE:
                        signals.append(signal)
                        logger.info(f"✅ {name} {pair} - {signal['direction']} ({signal['confidence']}%)")
                    
                    if (i + 1) % 10 == 0:
                        logger.info(f"📊 Прогресс {name}: {i + 1}/{scan_count}")
                    
                    await asyncio.sleep(0.2)
                    
                except Exception as e:
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Ошибка сканирования {name}: {e}")
        
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
        
        try:
            if df is not None and not df.empty:
                df = self.analyzer.calculate_indicators(df)
                chart_buf = self.chart_generator.create_chart(df, signal, coin)
                
                await self.telegram_bot.send_photo(
                    chat_id=TELEGRAM_CHAT_ID,
                    photo=chart_buf,
                    caption=msg,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                logger.info(f"✅ Отправлен сигнал с графиком: {signal['symbol']}")
            else:
                await self.telegram_bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=msg,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )
                logger.info(f"✅ Отправлен сигнал: {signal['symbol']}")
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
                chart_buf = self.chart_generator.create_chart(df, signal, coin)
                
                await self.telegram_bot.send_photo(
                    chat_id=TELEGRAM_CHAT_ID,
                    photo=chart_buf,
                    caption=pump_data['message'],
                    parse_mode='HTML',
                    reply_markup=pump_data['keyboard']
                )
                logger.info(f"✅ Отправлен памп-сигнал с графиком: {signal['symbol']}")
            else:
                await self.telegram_bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=pump_data['message'],
                    parse_mode='HTML',
                    reply_markup=pump_data['keyboard']
                )
                logger.info(f"✅ Отправлен памп-сигнал: {signal['symbol']}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки пампа: {e}")
    
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
            
            detailed = "\n".join(lines)
            
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔝 Вернуться к сигналу", callback_data=f"back_{coin}")
            ]])
            
            return detailed, keyboard
            
        except Exception as e:
            logger.error(f"Ошибка детального анализа {symbol}: {e}")
            return f"❌ Ошибка анализа: {e}", None
    
    async def run(self):
        logger.info("🤖 Мульти-биржевой бот запущен")
        logger.info(f"📊 Основной анализ: каждые {UPDATE_INTERVAL//60} мин")
        logger.info(f"🚀 Памп-сканер: каждые {PUMP_SCAN_INTERVAL//60} мин")
        
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
                        for i, signal in enumerate(signals):
                            await self.send_signal(signal)
                            if i < len(signals) - 1:
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
        self.app.add_handler(CallbackQueryHandler(self.button))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 *Мульти-биржевой сканер*\n\n"
            "Активные биржи: BingX\n"
            "Команды:\n"
            "/scan - Ручное сканирование\n"
            "/status - Статус\n"
            "/help - Помощь",
            parse_mode='Markdown'
        )
    
    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = await update.message.reply_text("🔍 Сканирую...")
        signals = await self.bot.scan_all()
        if signals:
            await msg.edit_text(f"✅ Найдено {len(signals)} сигналов")
            for signal in signals:
                await self.bot.send_signal(signal)
                await asyncio.sleep(3)
        else:
            await msg.edit_text("❌ Сигналов не найдено")
    
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = "*📡 Статус:*\n\n✅ BingX Futures: активен"
        await update.message.reply_text(text, parse_mode='Markdown')
    
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "*Помощь*\n\n"
            "📊 Анализ: RSI, MACD, EMA, VWAP\n"
            "🔥 Дивергенции, имбалансы, фракталы\n"
            "📐 Фибоначчи (коррекции и расширения)\n"
            "📊 Volume Profile (POC, HVN, Value Area)\n"
            "🚀 Памп-сканер каждые 30 сек\n\n"
            "Команды:\n"
            "/scan - ручное сканирование\n"
            "/status - состояние бота",
            parse_mode='Markdown'
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
                
                pump_percent = signal.get('pump_dump', [{}])[0].get('change_percent') if signal.get('pump_dump') else None
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
                            parse_mode='HTML',
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
                pump_percent = signal.get('pump_dump', [{}])[0].get('change_percent') if signal.get('pump_dump') else None
                msg, keyboard = self.bot.format_message(signal, contract_info, pump_percent)
                await query.edit_message_text(text=msg, parse_mode='HTML', reply_markup=keyboard)
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
