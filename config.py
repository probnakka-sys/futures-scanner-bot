#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Конфигурационный файл с переключателями функций
Все настройки бота в одном месте
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============== НАСТРОЙКИ БОТА ==============

# --- Telegram ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# --- Основные параметры ---
UPDATE_INTERVAL = int(os.getenv('UPDATE_INTERVAL', 900))  # 15 минут
MIN_CONFIDENCE = int(os.getenv('MIN_CONFIDENCE', 65))      # Мин. уверенность
TIMEFRAME = os.getenv('TIMEFRAME', '15m')                  # Основной таймфрейм
PAIRS_TO_SCAN = int(os.getenv('PAIRS_TO_SCAN', 100))        # Сколько пар сканировать

# --- Реферальные ссылки ---
REF_LINKS = {
    'MEXC': os.getenv('MEXC_REF_LINK', 'https://www.mexc.com'),
    'Bybit': os.getenv('BYBIT_REF_LINK', 'https://www.bybit.com'),
    'BingX': os.getenv('BINGX_REF_LINK', 'https://bingx.com')
}

# ============== ПЕРЕКЛЮЧАТЕЛИ ФУНКЦИЙ ==============

FEATURES = {
    # === БИРЖИ ===
    'exchanges': {
        'mexc': True,      # MEXC основная биржа
        'bybit': False,    # Bybit пока отключена
        'bingx': False,    # BingX пока отключена
    },
    
    # === ИСТОЧНИКИ ДАННЫХ ===
    'data_sources': {
        'http': True,       # HTTP API для исторических данных
        'websocket': False, # WebSocket для реального времени (в разработке)
    },
    
    # === ТАЙМФРЕЙМЫ ===
    'timeframes': {
        'current': TIMEFRAME,
        'hourly': True,     # Часовой тренд
        'daily': True,      # Дневной тренд
        'weekly': True,     # Недельный тренд
    },
    
    # === ТЕХНИЧЕСКИЙ АНАЛИЗ ===
    'indicators': {
        'rsi': True,        # Индекс относительной силы
        'macd': True,       # MACD
        'ema': True,        # Скользящие средние
        'bollinger': True,  # Полосы Боллинджера
        'atr': True,        # ATR для волатильности
        'volume': True,     # Анализ объемов
    },
    
    # === РАСШИРЕННЫЙ АНАЛИЗ (новые функции) ===
    'advanced': {
        'divergence': False,    # Дивергенции RSI/MACD
        'btc_correlation': False, # Корреляция с BTC
        'vwap': False,          # VWAP индикатор
        'patterns': False,      # Свечные паттерны
        'pump_dump': True,     # Памп-дамп анализ
        'fibonacci': False,     # Уровни Фибоначчи
    },
    
    # === ЭКСПЕРИМЕНТАЛЬНЫЕ ===
    'experimental': {
        'cvd': False,           # Cumulative Volume Delta
        'liquidations': False,  # Ликвидации
        'fear_greed': False,    # Индекс страха и жадности
        'orderbook': False,     # Анализ стакана
    },
    
    # === ТЕСТИРОВАНИЕ ===
    'testing': {
        'test_signal': True,    # Тестовый сигнал по BTC/USDT
        'debug_mode': False,    # Режим отладки
    }
}

# ============== НАСТРОЙКИ ИНДИКАТОРОВ ==============

INDICATOR_SETTINGS = {
    'rsi_period': 14,
    'rsi_oversold': 30,
    'rsi_overbought': 70,
    'macd_fast': 12,
    'macd_slow': 26,
    'macd_signal': 9,
    'ema_periods': [9, 21, 50, 200],
    'bollinger_period': 20,
    'bollinger_std': 2,
    'atr_period': 14,
    'volume_sma_period': 20,
}

# ============== ВЕСА ИНДИКАТОРОВ ==============

INDICATOR_WEIGHTS = {
    'rsi': 10,
    'macd': 15,
    'ema_cross': 15,
    'volume': 10,
    'hourly_trend': 8,
    'daily_trend': 15,
    'weekly_trend': 20,
    'trend_alignment': 15,
    'divergence': 20,
    'vwap': 12,
    'patterns': 15,
    'pump_dump': 10,
    'btc_correlation': 8,
}

# ============== НАСТРОЙКИ ПАМП-ДАМП ==============

PUMP_DUMP_SETTINGS = {
    'enabled': True,
    'threshold': 7.0,           # % для определения пампа
    'time_windows': [1, 3, 5, 15],  # минут для анализа
    'history_minutes': 30,       # храним историю цен
}

# ============== ТАЙМФРЕЙМЫ ==============

TIMEFRAMES = {
    'current': FEATURES['timeframes']['current'],
    'hourly': '1h' if FEATURES['timeframes']['hourly'] else None,
    'daily': '1d' if FEATURES['timeframes']['daily'] else None,
    'weekly': '1w' if FEATURES['timeframes']['weekly'] else None,
}

# Фильтруем None
TIMEFRAMES = {k: v for k, v in TIMEFRAMES.items() if v is not None}
