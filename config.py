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

# Реферальные ссылки
REF_LINKS = {
    'BingX': os.getenv('BINGX_REF_LINK', 'https://bingx.com/invite/'),
    'Bybit': os.getenv('BYBIT_REF_LINK', 'https://www.bybit.com'),
    'MEXC': os.getenv('MEXC_REF_LINK', 'https://www.mexc.com')
}

# ============== ПЕРЕКЛЮЧАТЕЛИ ФУНКЦИЙ ==============

FEATURES = {
    'exchanges': {
        'bingx': {'enabled': True},
        'bybit': {'enabled': False},
        'mexc': {'enabled': False},
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
        'fibonacci': True,
        'imbalance': True,
        'liquidity': True,
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
    'show_imbalance': True,
    'show_liquidity': True,
    'show_exchange_link': True,
    
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
    'imbalance': 15,
    'liquidity': 20,
}

# ============== НАСТРОЙКИ ПАМП-ДАМП ==============

PUMP_DUMP_SETTINGS = {
    'enabled': True,
    'threshold': 5.0,
    'time_windows': [5, 15, 30, 60],
    'history_minutes': 120,
}

# ============== НАСТРОЙКИ ИМБАЛАНСОВ ==============

IMBALANCE_SETTINGS = {
    'enabled': True,
    'threshold_buy': 0.3,
    'threshold_sell': -0.3,
    'stack_threshold': 3,
    'lookback_bars': 20,
    'weight_higher_tf': 1.5
}

# ============== НАСТРОЙКИ ЛИКВИДНОСТИ ==============

LIQUIDITY_SETTINGS = {
    'enabled': True,
    'lookback_bars': 100,
    'sweep_retrace_threshold': 1.0,
    'consolidation_threshold': 0.5,
    'zone_distance': 1.0
}

# ============== ТАЙМФРЕЙМЫ ==============

TIMEFRAMES = {
    'current': FEATURES['timeframes']['current'],
    'hourly': '1h' if FEATURES['timeframes']['hourly'] else None,
    'daily': '1d' if FEATURES['timeframes']['daily'] else None,
    'weekly': '1w' if FEATURES['timeframes']['weekly'] else None,
}

TIMEFRAMES = {k: v for k, v in TIMEFRAMES.items() if v is not None}
