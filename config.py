#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from dotenv import load_dotenv

load_dotenv()

# ============== НАСТРОЙКИ БОТА ==============

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')               # Обычные сигналы
PUMP_CHAT_ID = os.getenv('PUMP_CHAT_ID', '')                   # Памп-сигналы
STATS_CHAT_ID = os.getenv('STATS_CHAT_ID', '')                 # Статистика
ACCUMULATION_CHAT_ID = os.getenv('ACCUMULATION_CHAT_ID', '')   # Накопление

UPDATE_INTERVAL = int(os.getenv('UPDATE_INTERVAL', 900))       # 15 минут для основного анализа
PUMP_SCAN_INTERVAL = int(os.getenv('PUMP_SCAN_INTERVAL', 30))  # 30 секунд для памп-сканера
MIN_CONFIDENCE = int(os.getenv('MIN_CONFIDENCE', 55))
TIMEFRAME = os.getenv('TIMEFRAME', '15m')
PAIRS_TO_SCAN = int(os.getenv('PAIRS_TO_SCAN', 50))

# Реферальные ссылки
REF_LINKS = {
    'BingX': 'https://bingxdao.com/invite/ZTR83C/',
    'Bybit': os.getenv('BYBIT_REF_LINK', 'https://www.bybit.com/invite?ref=7GNJDR6'),
    'MEXC': os.getenv('MEXC_REF_LINK', 'https://promote.mexc.com/r/DPJr2UJJDC')
}

# ============== НАСТРОЙКИ ПАМП-СКАНЕРА ==============

PUMP_SCAN_SETTINGS = {
    'enabled': True,
    'threshold': 3.5,                            # % движения для REST API
    'instant_threshold': 2.0,                    # ⚡ СНИЖЕН ДО 1% для WebSocket (было 2.0)
    'shitcoin_instant_threshold': 1.5,           # Для щиткоинов еще ниже
    'timeframes': ['1m', '3m', '5m', '15m', '30m'],            # Было ['1m', '3m', '5m']
    'min_volume_usdt': 1000,
    'max_pairs_to_scan': 600,                    # Было 600
    'include_low_liquidity': True,
    'send_top_pumps': 999,
    'cooldown_minutes': 15,                       # Было 5
    'batch_size': 50,                            # Размер батча для параллельного сканирования (меньше = быстрее, но больше нагрузка) было 100
    'delay_between_batches': 0.3,                # Задержка между батчами в секундах, было 0.1
        # В FastPumpScanner.__init__
        # self.batch_size = PUMP_SCAN_SETTINGS.get('batch_size', 100)
        # self.delay_between_batches = PUMP_SCAN_SETTINGS.get('delay_between_batches', 0.1)
    
    # Новые настройки для WebSocket
    'websocket_top_pairs': 200,                   # Сколько пар в WebSocket
    'shitcoin_volume_threshold': 1500_000,        # Объем < 0.5M$ = щиткоин
    'websocket_reconnect_delay': 5,               # Задержка перед переподключением
}

# ============== НАСТРОЙКИ WEBSOCKET АНАЛИЗА ==============

WEBSOCKET_ANALYSIS_SETTINGS = {
    'enabled': True,
    
    # Окна анализа (в секундах)
    'time_windows': [3, 5, 10, 30, 60],  # проверяем рост за 3с, 5с, 10с, 30с, 60с
    
    # Пороги для разных типов монет
    'thresholds': {
        'major': {
            '3s': 1.0,    # 1% за 3 секунды
            '5s': 1.5,    # 1.5% за 5 секунд
            '10s': 2.0,   # 2% за 10 секунд
            '30s': 3.0,   # 3% за 30 секунд
            '60s': 4.0,   # 4% за 60 секунд
        },
        'shitcoin': {
            '3s': 0.8,    # 0.8% за 3 секунды
            '5s': 1.0,    # 1% за 5 секунд
            '10s': 1.5,   # 1.5% за 10 секунд
            '30s': 2.0,   # 2% за 30 секунд
            '60s': 2.5,   # 2.5% за 60 секунд
        }
    },
    
    # Минимальный объем для учета
    'min_volume_usdt': {
        'major': 10_000_000,  # 10M$ для мейджоров
        'shitcoin': 200_000,    # 200K$ для щиткоинов
    },
    
    # Максимальное количество сигналов в минуту (защита от спама)
    'max_signals_per_minute': 5,
    
    # История цен для анализа (сколько значений хранить)
    'price_history_size': 100,  # ← это нужно для maxlen
}

# ============== НАСТРОЙКИ УМНЫХ ПОВТОРОВ ==============

SMART_REPEAT_SETTINGS = {
    'enabled': True,                              # Вкл/выкл умную логику
    'cooldown_minutes': 15,                       # Базовый cooldown
    'allow_stronger_moves': True,                 # Разрешать повторы при усилении
    'strength_multiplier': 1.3,                   # 1.5 = усиление на 50%
    'min_time_for_repeat': 5,                     # Минимум минут до повтора
}

# ============== НАСТРОЙКИ ATR (True Range) ==============

ATR_SETTINGS = {
    'long_target_1_mult': 2.5,
    'long_target_2_mult': 5.0,
    'long_stop_loss_mult': 1.8,
    'short_target_1_mult': 2.5,
    'short_target_2_mult': 5.0,
    'short_stop_loss_mult': 1.8,
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
        'monthly': True,
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
        'order_blocks': True,
        'fractals': True,
        'smart_money': True,
        'volume_profile': False,      # Отключено до исправления
        'accumulation': True,         # Новый анализатор накопления
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
    'show_accumulation': True,         # Отображение накопления
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

# ============== НАСТРОЙКИ НАКОПЛЕНИЯ ==============

ACCUMULATION_SETTINGS = {
    'ad_threshold': 2.0,            # Порог для A/D дивергенции
    'volume_spike_threshold': 2.0,  # Аномальный объем x2
    'range_width_threshold': 5.0,   # Макс. ширина диапазона для консолидации
    'min_signals': 2,               # Минимум сигналов для подтверждения
    'lookback_period': 50,          # Период для анализа
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
    'enabled': False,
    'lookback_bars': 100,
    'value_area_pct': 70,
    'min_hvn_strength': 2.0,
    'confluence_distance': 0.5,
    'timeframes': ['daily', 'weekly', 'monthly'],
}

INDICATOR_WEIGHTS = {
    # Базовые индикаторы
    'rsi': 10,
    'macd': 15,
    
    # ===== EMA ПЕРЕСЕЧЕНИЯ =====
    'ema_cross_current': 15,              # Пересечение 9/21 на текущем ТФ (было просто ema_cross)
    'ema_cross_hourly': 20,                # ✅ НОВОЕ: пересечение 9/21 на часовом
    'ema_cross_daily': 30,                  # ✅ НОВОЕ: пересечение 9/21 на дневном
    'ema_cross_weekly': 40,                  # ✅ НОВОЕ: пересечение 9/21 на недельном (ОЧЕНЬ ВАЖНО!)
    
    # ===== EMA ПОЛОЖЕНИЕ =====
    'ema_position_hourly': 15,              # Положение относительно EMA 50/200 на часовом
    'ema_position_daily': 25,                # Положение относительно EMA 50/200 на дневном
    'ema_position_weekly': 35,                # Положение относительно EMA 50/200 на недельном
    
    # Объем
    'volume': 10,
    
    # Тренды (теперь только как вспомогательные)
    'hourly_trend': 10,        # ⬇️ было 15, стало 10 (заменили более точными EMA)
    'daily_trend': 15,          # ⬇️ было 25, стало 15
    'weekly_trend': 20,          # ⬇️ было 35, стало 20
    
    'trend_alignment': 20,
    'divergence': 20,
    'vwap': 12,
    'patterns': 15,
    'pump_dump': 25,
    'fibonacci': 20,
    'btc_correlation': 8,
    'fvg': 35,
    'imbalance': 20,
    'liquidity': 30,
    'order_blocks': 25,
    'fractals': 15,
    'smart_money': 35,
    'volume_profile': 30,
    'accumulation': 35,
}

# ============== НАСТРОЙКИ СТАТИСТИКИ ==============

STATS_SETTINGS = {
    'enabled': True,
    'stats_chat_id': os.getenv('STATS_CHAT_ID', ''),
    'daily_report_time': '20:00',
    'update_interval': 300,
    'history_days': 90,
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

# ============== НАСТРОЙКИ ПРОИЗВОДИТЕЛЬНОСТИ ==============

PERFORMANCE_SETTINGS = {
    'pump_batch_size': 50,
    'max_concurrent_requests': 10,
    'delay_between_batches': 0.5,
    'cache_ohlcv': True,
    'cache_ttl': 60,
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
