#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from dotenv import load_dotenv

load_dotenv()

# ============== НАСТРОЙКИ БОТА ==============

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
UPDATE_INTERVAL = int(os.getenv('UPDATE_INTERVAL', 900))  # 15 минут для основного анализа
PUMP_SCAN_INTERVAL = int(os.getenv('PUMP_SCAN_INTERVAL', 30))  # 30 секунд для памп-сканера
MIN_CONFIDENCE = int(os.getenv('MIN_CONFIDENCE', 55))
TIMEFRAME = os.getenv('TIMEFRAME', '15m')
PAIRS_TO_SCAN = int(os.getenv('PAIRS_TO_SCAN', 50))

# Реферальные ссылки
REF_LINKS = {
    'BingX': 'https://bingxdao.com/invite/ZTR83C/',
    'Bybit': os.getenv('BYBIT_REF_LINK', 'https://www.bybit.com'),
    'MEXC': os.getenv('MEXC_REF_LINK', 'https://www.mexc.com')
}

# ============== НАСТРОЙКИ ПАМП-СКАНЕРА ==============

PUMP_SCAN_SETTINGS = {
    'enabled': True,
    'threshold': 4.0,                          # % движения для сигнала
    'instant_threshold': 2.5,                   # % за 1-2 минуты для мгновенного сигнала
    'timeframes': ['1m', '3m', '5m', '15m'],    # Быстрые ТФ
    'min_volume_usdt': 5000,                    # Мин. объем
    'max_pairs_to_scan': 500,
    'include_low_liquidity': True,
    'send_top_pumps': 999,
    'cooldown_minutes': 15,
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
        'monthly': True,  # Добавили месячный
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
        'fibonacci': True,      # Включили Фибоначчи
        'imbalance': True,
        'liquidity': True,
        'order_blocks': True,
        'fractals': True,
        'smart_money': True,
        'volume_profile': True,  # Добавили Volume Profile
    },
    
    'testing': {
        'test_signal': False,
        'debug_mode': False,
    }
}

# ============== НАСТРОЙКИ ОТОБРАЖЕНИЯ ==============

DISPLAY_SETTINGS = {
    'show_price_source': False,
    'show_funding': True,
    'show_volume': True,
    'show_divergence': True,
    'show_patterns': True,
    'show_pump_dump': True,
    'show_vwap': True,
    'show_alignment': True,
    'show_imbalance': True,
    'show_liquidity': True,
    'show_order_blocks': True,
    'show_fractals': True,
    'show_fibonacci': True,
    'show_volume_profile': True,
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

# ============== НАСТРОЙКИ ФИБОНАЧЧИ ==============

FIBONACCI_SETTINGS = {
    'retracement_levels': [0.236, 0.382, 0.5, 0.618, 0.786, 0.86],
    'extension_levels': [0.18, 0.27, 0.618],
    'lookback_candles': 3,
    'min_distance_pct': 0.5,
    'weight_multiplier': 1.5,
}

# ============== НАСТРОЙКИ VOLUME PROFILE ==============

VOLUME_PROFILE_SETTINGS = {
    'enabled': True,
    'lookback_bars': 100,
    'value_area_pct': 70,
    'min_hvn_strength': 2.0,
    'confluence_distance': 0.5,
    'timeframes': ['daily', 'weekly', 'monthly'],
}

INDICATOR_WEIGHTS = {
    'rsi': 10,
    'macd': 15,
    'ema_cross': 15,
    'volume': 10,
    'hourly_trend': 15,
    'daily_trend': 25,
    'weekly_trend': 35,
    'trend_alignment': 20,
    'divergence': 20,
    'vwap': 12,
    'patterns': 15,
    'pump_dump': 25,
    'fibonacci': 20,
    'btc_correlation': 8,
    'imbalance': 15,
    'liquidity': 20,
    'order_blocks': 18,
    'fractals': 12,
    'smart_money': 25,
    'volume_profile': 25,
}

# ============== НАСТРОЙКИ СТАТИСТИКИ ==============

STATS_SETTINGS = {
    'enabled': True,
    'stats_chat_id': os.getenv('STATS_CHAT_ID', ''),  # ID группы для статистики
    'daily_report_time': '20:00',  # Время ежедневного отчета (20:00)
    'update_interval': 300,  # 5 минут обновление статусов
    'history_days': 90,       # храним статистику 90 дней
    'db_file': 'signals_database.json'
}

# ============== ОСТАЛЬНЫЕ НАСТРОЙКИ ==============

PUMP_DUMP_SETTINGS = {
    'enabled': True,
    'threshold': 5.0,
    'time_windows': [5, 15, 30, 60],
    'history_minutes': 120,
}

IMBALANCE_SETTINGS = {
    'enabled': True,
    'threshold_buy': 0.3,
    'threshold_sell': -0.3,
    'stack_threshold': 3,
    'lookback_bars': 20,
    'weight_higher_tf': 1.5
}

LIQUIDITY_SETTINGS = {
    'enabled': True,
    'lookback_bars': 100,
    'sweep_retrace_threshold': 1.0,
    'consolidation_threshold': 0.5,
    'zone_distance': 1.0
}

SMC_SETTINGS = {
    'enabled': True,
    'order_block_lookback': 50,
    'fair_value_gap_threshold': 0.5,
    'liquidity_sweep_retrace': 0.5,
    'bos_choch_threshold': 1.0,
    'min_order_block_strength': 30,
}

FRACTAL_SETTINGS = {
    'enabled': True,
    'window': 5,
    'strength_multiplier': 1.5,
    'confirmation_bars': 2,
}

# ============== ТАЙМФРЕЙМЫ ==============

TIMEFRAMES = {
    'current': FEATURES['timeframes']['current'],
    'hourly': '1h' if FEATURES['timeframes']['hourly'] else None,
    'daily': '1d' if FEATURES['timeframes']['daily'] else None,
    'weekly': '1w' if FEATURES['timeframes']['weekly'] else None,
    'monthly': '1M' if FEATURES['timeframes']['monthly'] else None,
}

TIMEFRAMES = {k: v for k, v in TIMEFRAMES.items() if v is not None}
