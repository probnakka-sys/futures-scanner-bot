#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from dotenv import load_dotenv

load_dotenv()

# ============== НАСТРОЙКИ БОТА ==============

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
UPDATE_INTERVAL = int(os.getenv('UPDATE_INTERVAL', 300))  # 5 минут
MIN_CONFIDENCE = int(os.getenv('MIN_CONFIDENCE', 65))
TIMEFRAME = os.getenv('TIMEFRAME', '15m')
PAIRS_TO_SCAN = int(os.getenv('PAIRS_TO_SCAN', 50))

REF_LINKS = {
    'MEXC': os.getenv('MEXC_REF_LINK', 'https://www.mexc.com'),
    'Bybit': os.getenv('BYBIT_REF_LINK', 'https://www.bybit.com'),
    'BingX': os.getenv('BINGX_REF_LINK', 'https://bingx.com')
}

# ============== ПЕРЕКЛЮЧАТЕЛИ ФУНКЦИЙ ==============

FEATURES = {
    'exchanges': {
        'bybit': {'enabled': True},   # Включаем Bybit
        'bingx': {'enabled': True},   # Включаем BingX
        'mexc': {'enabled': False},   # MEXC пока отключен
    },
    
    'data_sources': {
        'http': True,
        'websocket': False,
    },
    
    'timeframes': {
        'current': TIMEFRAME,
        'hourly': True,
        'daily': True,
        'weekly': True,
    },
    
    'indicators': {
        'rsi': True,
        'macd': True,
        'ema': True,
        'bollinger': True,
        'atr': True,
        'volume': True,
    },
    
    'advanced': {
        'divergence': True,
        'btc_correlation': False,
        'vwap': True,
        'patterns': True,
        'pump_dump': True,
        'fibonacci': False,
    },
    
    'testing': {
        'test_signal': True,
        'debug_mode': False,
    }
}

# ============== НАСТРОЙКИ ОТОБРАЖЕНИЯ ==============

DISPLAY_SETTINGS = {
    'show_price_source': True,
    'show_funding': True,
    'show_volume': True,
    'show_divergence': True,
    'show_patterns': True,
    'show_pump_dump': True,
    'show_vwap': True,
    'show_alignment': True,
    
    'buttons': {
        'copy': True,
        'trade': True,
        'refresh': True,
        'details': True,
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
    'threshold': 7.0,
    'time_windows': [1, 3, 5, 15],
    'history_minutes': 30,
}

# ============== ТАЙМФРЕЙМЫ ==============

TIMEFRAMES = {
    'current': FEATURES['timeframes']['current'],
    'hourly': '1h' if FEATURES['timeframes']['hourly'] else None,
    'daily': '1d' if FEATURES['timeframes']['daily'] else None,
    'weekly': '1w' if FEATURES['timeframes']['weekly'] else None,
}

TIMEFRAMES = {k: v for k, v in TIMEFRAMES.items() if v is not None}
