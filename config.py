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

# ============== НАСТРОЙКИ БИРЖ ==============
EXCHANGES = {
    'bingx': {
        'enabled': True,
        'api_key': os.getenv('BINGX_API_KEY', ''),
        'api_secret': os.getenv('BINGX_SECRET_KEY', ''),
        'type': 'swap'
    },
    'bybit': {
        'enabled': False,
        'api_key': '',
        'api_secret': '',
        'type': 'linear'
    },
    'mexc': {
        'enabled': False,
        'api_key': '',
        'api_secret': '',
        'type': 'future'
    }
}

# ============== НАСТРОЙКИ ПРОКСИ ==============

PROXY_SETTINGS = {
    'enabled': False,           # включить прокси принудительно
    'auto_detect': False,        # автоматически включать при ошибках
    'auto_detect_exchanges': ['bybit', 'mexc'],  # для каких бирж авто-детект
    'http': 'http://your-proxy:port',
    'https': 'https://your-proxy:port',
    'auth': None,               # 'username:password' если нужна авторизация
}

# ============== НАСТРОЙКИ ПАМП-СКАНЕРА ==============

PUMP_SCAN_SETTINGS = {
    'enabled': True,
    'threshold': 4.0,                               # Порог % движения для REST API (было 3.5, 2.0) 3.0
    'instant_threshold': 4.0,                       # Порог % движения для WebSocket (мейджоры) (было 2.0, 1.2) 2.5
    'shitcoin_instant_threshold': 6.0,              # Порог % движения для WebSocket (щиткоины) (было 1.5, 0.8) 2.0
    'timeframes': ['1m', '3m', '5m', '15m', '30m'], # Было ['1m', '3m', '5m']
    'min_volume_usdt': 1000,
    'max_pairs_to_scan': 600,                       # Было 600
    'include_low_liquidity': True,
    'send_top_pumps': 999,
    'cooldown_minutes': 15,                         # Было 5
    'batch_size': 50,                               # Размер батча для параллельного сканирования (меньше = быстрее, но больше нагрузка) было 100
    'delay_between_batches': 0.3,                   # Задержка между батчами в секундах, было 0.1
        # В FastPumpScanner.__init__
        # self.batch_size = PUMP_SCAN_SETTINGS.get('batch_size', 100)
        # self.delay_between_batches = PUMP_SCAN_SETTINGS.get('delay_between_batches', 0.1)
    
    # Новые настройки для WebSocket
    'websocket_top_pairs': 200,                   # Сколько пар в WebSocket
    'shitcoin_volume_threshold': 2000_000,        # Объем < 0.5M$ = щиткоин
    'websocket_reconnect_delay': 5,               # Задержка перед переподключением
}

# ============== ФИЛЬТР ПАМП-ДАМП СИГНАЛОВ ==============

PUMP_DUMP_FILTER = {
    'enabled': True,           # включить фильтр
    'type': 'both',            # 'both', 'pump_only', 'dump_only'
    # 'both' - все сигналы
    # 'pump_only' - только пампы (рост)
    # 'dump_only' - только дампы (падение)
}

# ============== НАСТРОЙКИ МЕХАНИЗМОВ ПАМП-СКАНЕРА ==============

PUMP_MECHANISM_SETTINGS = {
    'mode': 'new_only',  # 'new_only', 'old_only', 'both'
    
    # 'new_only' - только новый механизм (WEBSOCKET_ANALYSIS_SETTINGS)
    # 'old_only' - только старый механизм (instant_threshold)
    # 'both' - оба механизма (новый находит, старый фильтрует)
    
    # Для режима 'both' можно настроить дополнительную фильтрацию
    'both_settings': {
        'require_both': False,  # True: нужны оба механизма, False: любой
    }
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

# ============== НАСТРОЙКИ СКАНИРОВАНИЯ ПАР ==============

SCAN_MODE = {
    'mode': 'all',  # 'all', 'top_volume', 'shitcoin', 'hybrid'
        # Как переключать режимы:
        # Только щиткоины (сейчас):         'mode': 'shitcoin'      
        # Только топ-100 по объему:         'mode': 'top_volume'
        #                                   'top_volume': {'enabled': True}
        # Гибрид (50 топ + 150 щиткоинов):  'mode': 'hybrid'
        #                                   'hybrid': {'enabled': True}
        # Все пары (600):                   'mode': 'whatever
    'randomize': True,  # перемешивать ли список

    # Для режима top_volume
    'top_volume': {
        'enabled': False,
        'count': 100,  # топ-100 по объему
        'min_volume': 0  # минимальный объем
    },
    
    # Для режима shitcoin
    'shitcoin': {
        'enabled': True,            # Режим щиткоинов включен
        'max_volume': 2_000_000,    # объем < 1.5M$
        'count': 600,               # сколько щиткоинов сканировать
        'include_majors': True,     # включать ли мейджоры (BTC, ETH...)
        'majors_count': 5           # сколько мейджоров добавить
    },
    
    # Для гибридного режима
    'hybrid': {
        'enabled': False,
        'top_volume_count': 50,  # топ-50 по объему
        'shitcoin_count': 150,   # + 150 щиткоинов
    },    
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
# ✅ ДОБАВИТЬ: увеличенные настройки для идеального сетапа (100% согласованность)
    'ideal_long_target_1_mult': 4.0,   # 4x ATR
    'ideal_long_target_2_mult': 8.0,   # 8x ATR
    'ideal_long_stop_loss_mult': 2.5,   # чуть шире стоп
    'ideal_short_target_1_mult': 4.0,
    'ideal_short_target_2_mult': 8.0,
    'ideal_short_stop_loss_mult': 2.5,
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
        'volume_profile': True,      # Отключено до исправления
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
    'ema_periods': [9, 14, 21, 28, 50, 100, 200],  # ← добавили EMA 14, 28, 100
    # EMA 200 оставляем опционально
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
    'min_signals': 1,               # ✅ ИЗМЕНЕНО: минимум 1 сигнал (было 2)
    'lookback_period': 50,          # Период для анализа
}

# ============== НАСТРОЙКИ АНАЛИЗА ОБЪЕМОВ ==============

VOLUME_ANALYSIS_SETTINGS = {
    'enabled': True,  # вкл/выкл анализ объемов
    
    # Детектор аномальных свечей
    'spike_detector': {
        'enabled': True,
        'threshold': 2.5,        # объем x2.5 от среднего = аномалия
        'lookback': 20,           # период для среднего
        'weight': 15              # вес в уверенности
    },
    
    # Дисбаланс buy/sell
    'imbalance': {
        'enabled': True,
        'threshold': 0.3,         # >0.3 = бычий, <-0.3 = медвежий
        'weight': 15
    },
    
    # Volume Profile (уже есть, просто включаем)
    'volume_profile': {
        'enabled': True,
        'timeframes': ['daily', 'weekly', 'monthly']
    },
    
    # Дисперсия объема
    'volume_dispersion': {
        'enabled': True,
        'hours': 2,               # за сколько часов анализировать
        'high_threshold': 2.0,    # >2x = высокая дисперсия
        'low_threshold': 0.7,     # <0.7x = низкая дисперсия
        'weight': 10
    }
}

# ============== НАСТРОЙКИ АНАЛИЗА ДИСПЕРСИИ ==============

DISPERSION_ANALYSIS_SETTINGS = {
    'enabled': True,  # вкл/выкл анализ дисперсии
    
    # Периоды анализа
    'timeframes': {
        'short': 1,      # 1 час
        'medium': 2,     # 2 часа
        'long': 4        # 4 часа
    },
    
    # Пороги для интерпретации
    'thresholds': {
        'high': 5.0,     # >5% = высокая дисперсия
        'medium': 2.0,   # 2-5% = средняя дисперсия
        'low': 1.5       # <1.5% = низкая дисперсия
    },
    
    # Влияние на уверенность
    'weights': {
        'high': 15,      # бонус за высокую дисперсию
        'low': 10,       # бонус за низкую дисперсию (накопление)
        'medium': 5
    },
    
    # Зоны дисперсии для графика
    'show_zones_on_chart': True,  # показывать зоны на графике
    'max_zones': 3                 # максимум зон на графике
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
    'divergence': 30,
    'vwap': 12,
    'patterns': 15,
    'pump_dump': 25,
    'fibonacci': 20,
    'btc_correlation': 8,
    'fvg': 35,
    'imbalance': 35,
    'liquidity': 30,
    'order_blocks': 25,
    'fractals': 15,
    'smart_money': 35,
    'volume_profile': 30,
    'accumulation': 35,
}

# ============== НАСТРОЙКИ АНАЛИЗА СИЛЫ УРОВНЕЙ ==============

LEVEL_STRENGTH_SETTINGS = {
    'enabled': True,  # вкл/выкл анализ силы уровней
    
    # Веса для разных факторов
    'weights': {
        'convergence': {
            '4_tf': 40,
            '3_tf': 30,
            '2_tf': 20,
            '1_tf': 0
        },
        'touches': {
            '7_plus': 25,
            '5_plus': 20,
            '3_plus': 12,
            'default': 0
        },
        'volume': {
            'extreme': 25,    # >3x
            'high': 18,       # >2x
            'medium': 10,     # >1.5x
            'default': 0
        },
        'rsi': {
            'extreme_overbought': 15,   # >85
            'overbought': 10,           # >75
            'extreme_oversold': 15,     # <15
            'oversold': 10,             # <25
            'default': 0
        },
        'trend': {
            'aligned': 20,      # тренд в сторону уровня
            'default': 0
        },
        'distance': {
            'very_close': 15,   # <1%
            'close': 10,        # <2%
            'default': 0
        }
    },
    
    # Пороги вероятности разворота
    'probability_thresholds': {
        'strong_reversal': 70,      # >70% = сильный разворот
        'likely_reversal': 50,      # >50% = вероятный разворот
        'observation': 30,          # >30% = наблюдение
        'breakout': 30              # <30% = возможен пробой
    },
    
    # Допуск для совпадения уровней (в процентах)
    'confluence_tolerance': 0.5,    # 0.5% = уровни считаются совпадающими
    
    # Максимальное расстояние для учета уровня (в %)
    'max_distance': 20,             # уровни дальше 20% не учитываем
    
    # Количество отображаемых сильных уровней
    'max_levels_to_show': 3,
    
    # Автоматическая смена направления при сильном уровне
    'auto_override_direction': True,  # менять направление если уровень сильный

    # Агрессивный (много сигналов):    
    # 'probability_thresholds': {
    #     'strong_reversal': 50,   # снижены пороги
    #     'likely_reversal': 40,
    # }

    # Консервативный (только сильные сигналы):    
    # 'probability_thresholds': {
    #     'strong_reversal': 85,   # повышенные пороги
    #     'likely_reversal': 70,
    # }

    # Без автоматической смены направления:    
    # 'auto_override_direction': False  # только предупреждения, без смены
}

# ============== НАСТРОЙКИ АНАЛИЗА УРОВНЕЙ И ПРОБОЕВ ==============

LEVEL_ANALYSIS_SETTINGS = {
    'enabled': True,  # вкл/выкл анализ уровней
    
    # Какие уровни анализировать
    'level_types': {
        'horizontal': True,      # горизонтальные уровни
        'trendline': True,       # трендовые линии
        'fvg': True,             # FVG зоны
        'ema': True,             # EMA уровни (50, 200)
        'fibonacci': True,       # уровни Фибоначчи
        'volume_profile': True,  # Volume Profile (если включено)
    },
    
    # Настройки сбора уровней
    'collection': {
        'max_levels_per_tf': 10,           # макс уровней с одного ТФ
        'max_total_levels': 50,             # макс всего уровней
        'min_touches': 3,                    # мин касаний для значимости
        'touch_tolerance': 0.3,               # допуск касания в %
    },
    
    # Настройки пробоя
    #'breakout': {
    #    'required_candles': 3,                # свечей для подтверждения
    #    'min_breakout_percent': 1.0,          # мин размер пробоя (%) меньше 1% игнорируем
    #    'confirmation_percent': 1.0,          # закрепление после пробоя (%) (было 0.5)
    #    'retrace_threshold': 70,              # % возврата для ложного пробоя
    #    'volume_confirmation': 2.0,           # объем x2 для подтверждения        
    #    'confirmation_mode': 'any_two',       # Вариант 3: комбинированный 'any_two', 'all', 'any_one'
    #},
    
    # Веса для разных типов уровней
    'weights': {
        'horizontal': {
            'base': 30,
            'per_touch': 5,                    # +5 за каждое касание
            'tf_multiplier': {                  # множитель по ТФ
                'monthly': 4.0,
                'weekly': 3.5,
                'daily': 3.0,
                'four_hourly': 2.5,
                'hourly': 2.0,
                'current': 1.0
            }
        },
        'trendline': {
            'base': 35,
            'per_touch': 8,
            'tf_multiplier': {
                'monthly': 4.0,
                'weekly': 3.5,
                'daily': 3.0,
                'four_hourly': 2.5,
                'hourly': 2.0,
                'current': 1.0
            }
        },
        'fvg': {
            'base': 40,
            'size_multiplier': 2,               # множитель от размера FVG
            'tf_multiplier': {
                'monthly': 4.0,
                'weekly': 3.5,
                'daily': 3.0,
                'four_hourly': 2.5,
                'hourly': 2.0,
                'current': 1.0
            }
        },
        'ema': {
            'ema_200': 50,
            'ema_50': 35,
            'tf_multiplier': {
                'monthly': 4.0,
                'weekly': 3.5,
                'daily': 3.0,
                'four_hourly': 2.5,
                'hourly': 2.0,
                'current': 1.0
            }
        },
        'fibonacci': {
            'base': 30,
            'level_multiplier': {                # особо важные уровни
                '0.618': 1.5,
                '0.786': 1.3,
                '0.382': 1.0
            },
            'tf_multiplier': {
                'monthly': 4.0,
                'weekly': 3.5,
                'daily': 3.0,
                'four_hourly': 2.5,
                'hourly': 2.0,
                'current': 1.0
            }
        }
    },
    
    # Настройки сигналов
    'signals': {
        'approach': {
            'enabled': True,
            'thresholds': {                      # расстояния для предупреждения
                'strong': 1.5,                    # для сильных уровней
                'medium': 1.0,
                'weak': 0.5
            },
            'min_confidence': 20,                  # мин уверенность для сигнала
            'message': "⚠️ {strength} уровень на {tf}: {distance:.1f}% до пробоя ({touches} касаний)"
        },
        
        'breakout_first': {
            'enabled': True,
            'min_confidence': 30,
            'message': "⚡ ПЕРВЫЙ СИГНАЛ: пробой {direction} на {tf} ({touches} касаний)"
        },
        
        'breakout_confirmed': {
            'enabled': True,
            'min_move_percent': 1.0,               # мин движение для подтверждения
            'min_confidence': 40,
            'message': "✅ ПРОБОЙ {direction} на {tf} ПОДТВЕРЖДЕН! +{move:.1f}% ({touches} касаний)"
        },
        
        'fakeout': {
            'enabled': True,
            'min_retrace': 60,                      # мин % возврата для ложного пробоя
            'min_confidence': 60,
            'message': "🚨 ЛОЖНЫЙ ПРОБОЙ {direction} на {tf}! Возврат на {retrace:.0f}%"
        }
    },
    
    # Приоритеты таймфреймов
    'tf_priority': ['monthly', 'weekly', 'daily', 'four_hourly', 'hourly', 'current'],
    
    # Черный список (игнорировать определенные уровни)
    'blacklist': {
        'symbols': [],  # ['BTC', 'ETH'] - не анализировать определенные монеты
        'levels': []     # игнорировать определенные цены
    }
}

# ============== НАСТРОЙКИ ПОДТВЕРЖДЕНИЯ ПРОБОЕВ ==============

BREAKOUT_CONFIRMATION_SETTINGS = {
    'enabled': True,
    'required_candles': 2,          # минимум 2 свечи
    'required_percent': 0.8,        # закрепление на 0.8%
    'volume_confirmation': 1.5,     # объем x1.5 для подтверждения
    'confirmation_mode': 'any_two',  # 'any_two', 'all', 'any_one'
    
    # Пояснения:
    # 'any_two' - нужно 2 из 3 условий (рекомендуется)
    # 'all' - нужны все 3 условия (очень строго)
    # 'any_one' - достаточно одного условия (быстро, но могут быть ложные)
}

# ============== НАСТРОЙКИ СНАЙПЕРСКИХ ТОЧЕК ВХОДА ==============

SNIPER_ENTRY_SETTINGS = {
    'enabled': True,  # вкл/выкл анализ снайперских точек
    
    # Для LONG (покупка на поддержке)
    'long': {
        'distance_threshold': 0.5,   # расстояние до уровня в %
        'min_strength': 50,           # минимальная сила уровня
        'confirmation_volume': 1.5,   # нужен ли объем x1.5
        'confirmation_rsi': 30        # RSI должен быть < 30
    },
    
    # Для SHORT (продажа на сопротивлении)
    'short': {
        'distance_threshold': 0.5,   # расстояние до уровня в %
        'min_strength': 50,           # минимальная сила уровня
        'confirmation_volume': 1.5,   # нужен ли объем x1.5
        'confirmation_rsi': 70        # RSI должен быть > 70
    },
    
    # Настройки лимитного ордера
    'order': {
        'price_offset': 0.1,          # отступ от уровня в %
        'stop_loss_offset': 0.5,      # стоп-лосс за уровнем в %
        'take_profit': 3.0            # тейк-профит в % от входа
    }
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

# ============== НАСТРОЙКИ ДЕТЕКТОРА ЛОЖНЫХ ПРОБОЕВ ==============

FAKEOUT_SETTINGS = {
    'enabled': True,
    'breakout_distance': 2.0,      # игнорировать микродвижения <2%
    'retrace_threshold': 70,       # возврат на 70% = ложный
    'confirmation_candles': 2,     # сколько свечей ждать для подтверждения
}

# ============== ВЕСА ДЛЯ МУЛЬТИТАЙМФРЕЙМА ==============

TIMEFRAME_WEIGHTS = {
    '1m': 0.5,
    '3m': 0.6,
    '5m': 0.7,
    '15m': 1.0,
    '30m': 1.2,
    '1h': 1.5,
    '4h': 2.0,
    '1d': 2.5,
    '1w': 3.0,
    '1M': 3.5,
}

# ============== НАСТРОЙКИ АНАЛИЗА КАСАНИЙ EMA ==============

EMA_TOUCH_SETTINGS = {
    'enabled': True,
    'periods': [9, 14, 21, 28, 50, 100],     # какие EMA анализировать
    'distance_threshold': 0.5,                # % от цены для определения касания
    'max_signals': 3,                         # максимум сигналов в причинах
    'weights': {                              # веса для разных таймфреймов
        'monthly': 30,
        'weekly': 25,
        'daily': 20,
        'four_hourly': 15,
        'hourly': 10,
        'current': 5,
        '30m': 4,
        '5m': 3,
        '3m': 2,
        '1m': 1
    }
}

# ============== МЛАДШИЕ ТАЙМФРЕЙМЫ ==============

MINOR_TIMEFRAMES = {
    '1m': '1m',
    '3m': '3m',
    '5m': '5m',
    '15m': '15m',      
    '30m': '30m',
}

# В TIMEFRAMES добавьте все
TIMEFRAMES = {
    '1m': MINOR_TIMEFRAMES['1m'],
    '3m': MINOR_TIMEFRAMES['3m'],
    '5m': MINOR_TIMEFRAMES['5m'],
    'current': FEATURES['timeframes']['current'],
    '30m': MINOR_TIMEFRAMES['30m'],
    'hourly': '1h',
    'four_hourly': '4h',
    'daily': '1d',
    'weekly': '1w',
    'monthly': '1M',
}

# ============== НАСТРОЙКИ МЛАДШИХ ТАЙМФРЕЙМОВ ==============

MINOR_TF_SETTINGS = {
    'enabled': True,  # вкл/выкл анализ младших ТФ (1м, 3м, 5м, 30м)
    
    # Какие таймфреймы анализировать
    'timeframes': {
        '1m': {'enabled': True, 'weight': 0.5, 'purpose': 'confirmation'},      # только подтверждение
        '3m': {'enabled': True, 'weight': 0.6, 'purpose': 'confirmation'},
        '5m': {'enabled': True, 'weight': 0.7, 'purpose': 'confirmation'},
        '30m': {'enabled': True, 'weight': 1.2, 'purpose': 'analysis'},          # и анализ
    },
    
    # Для чего использовать
    'purposes': {
        'confirmation': {      # только для подтверждения
            'add_to_reasons': True,
            'affect_confidence': True,
            'change_direction': False,   # НЕ меняют направление!
            'max_weight': 10
        },
        'analysis': {          # для полного анализа
            'add_to_reasons': True,
            'affect_confidence': True,
            'change_direction': True,    # могут менять направление
            'max_weight': 20
        }
    }
}

# ============== НАСТРОЙКИ ФИБОНАЧЧИ (РАСШИРЕННЫЕ) ==============

FIBONACCI_ADVANCED_SETTINGS = {
    'enabled': True,
    'retracement_levels': [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 0.86, 1.0],
    'extension_levels': [1.272, 1.618, 2.0, 2.618],
    'lookback_candles': 3,
    'min_distance_pct': 0.5,
    'weight_multiplier': 1.5,
    'history_ttl_days': 30,
    'convergence_tf_min': 2,
    'convergence_tf_list': ['15m', '30m', '1h', '4h', '1d', '1w'],
    'show_approach_count': True,
    'approach_threshold': 0.5,
}

# ============== НАСТРОЙКИ ФОРМАТИРОВАНИЯ СИГНАЛОВ ==============

SIGNAL_FORMAT_SETTINGS = {
    'empty_lines': True,
    'group_trends': True,
    'tf_names': {
        'current': '15м',
        'hourly': '1ч',
        'four_hourly': '4ч',
        'daily': '1д',
        'weekly': '1н',
        'monthly': '1м'
    }
}

# ============== НАСТРОЙКИ ИСТОРИИ ФИБОНАЧЧИ ==============

FIB_HISTORY_SETTINGS = {
    'enabled': True,
    'max_approaches': 10,
    'ttl_seconds': 2592000,
}

# ============== НАСТРОЙКИ СОГЛАСОВАННОСТИ ТАЙМФРЕЙМОВ ==============

TF_ALIGNMENT_SETTINGS = {
    'enabled': True,
    
    # Режимы: 'strict', 'normal', 'loose', 'info', 'off'
    'regular_mode': 'info',        # для обычных LONG/SHORT
    'accumulation_mode': 'normal', # для накопления
    'pump_mode': 'info',           # для памп-дамп
    
    # === НАСТРОЙКИ ТФ ДЛЯ КАЖДОГО ТИПА ===
    'regular_tfs': {
        'major': ['weekly', 'daily', 'four_hourly', 'hourly'],  # 4 ТФ
        'minor': ['30m', 'current'],                            # 2 ТФ
        'ultra_minor': [],                                       # не используем
    },
    'accumulation_tfs': {
        'major': ['weekly', 'daily', 'four_hourly', 'hourly'],  # 4 ТФ
        'minor': ['30m', 'current'],                            # 2 ТФ
        'ultra_minor': [],
    },
    'pump_tfs': {
        'major': ['weekly', 'daily', 'four_hourly', 'hourly'],  # 4 ТФ (для определения тренд/коррекция)
        'minor': ['30m', 'current'],                            # 2 ТФ
        'ultra_minor': ['5m', '3m', '1m'],                      # 3 ТФ (для подтверждения импульса)
    },
    
    # Пороги для режимов (в процентах от доступных ТФ)
    'thresholds': {
        'strict': 100,
        'normal': 66,
        'loose': 33,
        'info': 0,
        'off': -1,
    },
    
    # Показывать процент согласованности в сигнале
    'show_percentage': True,
    
    # Влияние на уверенность
    'bonus_perfect': 20,
    'bonus_high': 10,
    'penalty_low': -5,
    'penalty_very_low': -15,
    
    # Режим INFO: отправлять все сигналы
    'send_all_in_info_mode': True,
}

# ============== НАСТРОЙКИ ОЦЕНКИ СИЛЫ ТРЕНДА ==============

TREND_STRENGTH_SETTINGS = {
    'enabled': True,                     # Вкл/выкл расширенную оценку
    
    # Используемые индикаторы
    'use_ema_50': True,                  # Использовать EMA 50 для силы тренда
    'use_volume': True,                  # Использовать объем для подтверждения
    'use_rsi': True,                     # Использовать RSI для оценки перекупленности
    
    # Веса для разных факторов (влияние на уверенность)
    'weights': {
        'strong_trend': 15,              # Сильный тренд (EMA 9 > 21 > 50)
        'weak_trend': 5,                 # Слабый тренд (EMA 9 > 21, но 21 < 50)
        'volume_confirmation': 10,       # Объем подтверждает тренд
        'rsi_extreme': 10,               # RSI в экстремальной зоне (<30 или >70)
    },
    
    # Пороги
    'volume_ratio_threshold': 1.5,       # Отношение объема к среднему для подтверждения
    'rsi_oversold': 30,                  # RSI перепродан
    'rsi_overbought': 70,                # RSI перекуплен
}

# ============== НАСТРОЙКИ ДИНАМИЧЕСКИХ ЦЕЛЕЙ И СТОПОВ ==============

DYNAMIC_TARGET_SETTINGS = {
    'enabled': True,                         # Вкл/выкл динамические цели
    
    # Для идеального сетапа (все ТФ согласованы)
    'perfect_setup': {
        'target_1_mult': 4.0,                # Цель 1: 4x ATR
        'target_2_mult': 8.0,                # Цель 2: 8x ATR
        'stop_mult': 2.5,                    # Стоп: 2.5x ATR
    },
    
    # Базовые настройки (обычный сигнал)
    'default': {
        'target_1_mult': 2.5,                # Цель 1: 2.5x ATR
        'target_2_mult': 5.0,                # Цель 2: 5x ATR
        'stop_mult': 1.8,                    # Стоп: 1.8x ATR
    },
    
    # Для сильного тренда (недельный тренд + EMA 200)
    'strong_trend': {
        'enabled': True,                     # Вкл/выкл расширенный стоп для сильного тренда
        'target_1_mult': 4.0,                # Цель 1: 4x ATR
        'target_2_mult': 8.0,                # Цель 2: 8x ATR
        'stop_mult': 3.5,                    # Стоп: 3.5x ATR (шире)
    },
    
    # Использовать ATR старшего ТФ
    'use_higher_tf_atr': True,               # Для стопа использовать ATR часового/дневного ТФ
    'higher_tf': 'hourly',                   # Какой ТФ использовать: 'hourly' или 'daily'
}

# ============== НАСТРОЙКИ ЗОН ДОП.ВХОДА ==============

ENTRY_ZONES_SETTINGS = {
    'enabled': True,
    
    # Для обычных сигналов
    'regular': {
        'zone_1_tf': 'current',      # 15м
        'zone_2_tf': 'hourly',       # 1ч
        'zone_3_tf': None,           # опционально
    },
    
    # Для памп-дамп сигналов
    'pump': {
        'zone_1_tf': 'current',      # 15м
        'zone_2_tf': '5m',           # 5м (быстрые уровни)
        'zone_3_tf': '1m',           # 1м (сверхбыстрые)
    },
    
    # Для накопления
    'accumulation': {
        'zone_1_tf': 'hourly',       # 1ч
        'zone_2_tf': 'four_hourly',  # 4ч
        'zone_3_tf': 'daily',        # 1д
    },
    
    # Периоды для поиска уровней (количество свечей)
    'lookback': {
        'zone_1': 20,                # 20 свечей
        'zone_2': 50,                # 50 свечей
        'zone_3': 100,               # 100 свечей
    },
}

# ============== НАСТРОЙКИ ДЛЯ РАЗНЫХ ТИПОВ СИГНАЛОВ ==============

SIGNAL_TYPE_SETTINGS = {
    # Обычные сигналы (LONG/SHORT)
    'regular': {
        'leverage': 50,                      # Плечо по умолчанию
        'stop_multiplier': 1.8,              # Стоп
        'target_1_multiplier': 2.5,          # Цель 1
        'target_2_multiplier': 5.0,          # Цель 2
        'use_higher_tf_atr': False,          # Использовать ATR старшего ТФ
        'higher_tf': 'hourly',
    },
    
    # Памп-дамп сигналы
    'pump': {
        'leverage': 50,
        'stop_multiplier': 2.0,              # Стоп чуть шире для пампа
        'target_1_multiplier': 2.5,
        'target_2_multiplier': 5.0,
        'use_higher_tf_atr': False,
        'higher_tf': 'hourly',
    },
    
    # Сигналы накопления
    'accumulation': {
        'leverage': 15,
        'stop_multiplier': 3.5,
        'target_1_multiplier': 5.0,
        'target_2_multiplier': 10.0,
        'min_potential_pct': 5.0,
        'use_higher_tf_atr': True,
        'higher_tf': 'hourly',
    },
}

# ============== НАСТРОЙКИ ДЛЯ СИГНАЛОВ НАКОПЛЕНИЯ ==============

ACCUMULATION_SIGNAL_SETTINGS = {
    'enabled': True,
    'max_leverage': 15,                  # Сниженное плечо (было 50-100)
    'min_leverage': 10,                  
    'target_1_multiplier': 5.0,          # Цель 1: 5x ATR
    'target_2_multiplier': 10.0,         # Цель 2: 10x ATR
    'stop_multiplier': 3.5,              # Увеличенный стоп (3.5x ATR)
    'show_leverage_warning': True,       # Показывать предупреждение о плече
    'min_potential_pct': 5.0,            # Минимальный потенциал для сигнала
    'use_higher_tf_atr': True,           # Использовать ATR старшего ТФ
    'higher_tf': 'hourly',               # ТФ для ATR
}

# ============== НАСТРОЙКИ ДЕТЕКТОРА ВЫБИВА СТОПОВ ==============
STOP_HUNT_SETTINGS = {
    'enabled': True,
    'min_breakout_pct': 1.0,           # Минимальный пробой в %
    'max_retrace_time': 180,            # Максимальное время возврата (сек)
    'retrace_threshold_pct': 70,        # Возврат на 70% от пробоя
    'min_timeframe': '5m',              # Минимальный ТФ для поиска уровней
    'max_timeframe': '1h',              # Максимальный ТФ для поиска уровней
    'lookback_bars': 100,               # Количество свечей для анализа
    'strength_bonus': 25,               # Бонус к уверенности при выявлении стоп-ханта
    # Стоп-хaнт (Stop Hunt) — это когда цена пробивает уровень, где обычно ставят стоп-лоссы (локальные максимумы/минимумы), 
    # а затем быстро возвращается обратно. Это сигнал, что крупные игроки собрали ликвидность и разворачивают цену.
}

# ============== НАСТРОЙКИ ВХОДА ПОСЛЕ СТОП-ХАНТА ==============
POST_STOP_HUNT_SETTINGS = {
    'enabled': True,
    'require_confirmation': True,           # Требовать подтверждение от младших ТФ
    'min_confidence_bonus': 15,             # Бонус к уверенности
    'signal_type': 'stop_hunt_reversal',    # Тип сигнала
}

# ============== НАСТРОЙКИ ЗОН ЛИКВИДНОСТИ ==============
LIQUIDITY_ZONES_SETTINGS = {
    'enabled': True,
    'timeframes': ['15m', '1h', '4h', '1d'],     # Какие ТФ анализировать
    'lookback_bars': 100,                         # Глубина поиска
    'min_touches': 2,                             # Минимум касаний для значимости
    'zone_width_pct': 0.3,                        # Ширина зоны в %
    'max_zones': 5,                               # Максимум зон для отображения
    'strength_weights': {
        'touches': 15,                            # Вес за каждое касание
        'volume': 10,                             # Вес за объем
        'timeframe': 20,                          # Вес за старший ТФ
    }
}

# ============== НАСТРОЙКИ СТРАТЕГИЙ ==============
STRATEGY_SETTINGS = {
    # Активная стратегия (по умолчанию)
    'active': {
        'enabled': True,
        'name': 'Активная',
        
        # FVG настройки
        'fvg': {
            'require_close_pct': 0,           # Не требовать закрытие (0-100) # Меняйте: 0, 30, 50, 70
            'size_weight': True,               # Учитывать размер FVG # True/False
            'timeframe_weight': True,          # Учитывать ТФ FVG # True/False
            'volume_confirmation': False,      # Не требовать объем
            'liquidity_check': False,          # Не проверять ликвидность
            'direction_filter': True,          # LONG=FVG снизу, SHORT=FVG сверху
        },
        
        # Вход
        'require_breakout_confirmation': False,  # Вход на касании
        'min_confluence_levels': 1,              # Минимум уровней в конфлюенции # Меняйте: 1, 2, 3
        'risk_level': 'medium',
    },
    
    # Консервативная стратегия
    'conservative': {
        'enabled': False,
        'name': 'Консервативная',
        
        'fvg': {
            'require_close_pct': 70,           # Требовать закрытие на 70%
            'size_weight': True,               
            'timeframe_weight': True,          
            'volume_confirmation': True,       
            'liquidity_check': True,           
            'direction_filter': True,
        },
        
        'require_breakout_confirmation': True,   # Только после пробоя
        'min_confluence_levels': 2,              # Минимум 2 уровня
        'risk_level': 'low',
    },
    
    # Агрессивная стратегия
    'aggressive': {
        'enabled': False,
        'name': 'Агрессивная',
        
        'fvg': {
            'require_close_pct': 0,         
            'size_weight': False,             
            'timeframe_weight': False,        
            'volume_confirmation': False,     
            'liquidity_check': False,         
            'direction_filter': False,        
        },
        
        'require_breakout_confirmation': False,
        'min_confluence_levels': 0,
        'risk_level': 'high',
    },
    
    # Выбранная стратегия
    'selected': 'active',  # active, conservative, aggressive
}

# Типы стратегий. Как понять, что лучше:
# Запустите активную — посмотрите на сигналы
# Увеличьте require_close_pct до 30 — стало меньше сигналов, но точнее?
# Увеличьте min_confluence_levels до 2 — сигналы только с 2+ уровнями
# Сравните результаты — какой набор настроек даёт лучшие сигналы?
# Совет для тестирования:
# День	 Настройки	                            Цель
# Пн-Вт	 Активная (по умолчанию)	            База
# Ср-Чт	 require_close_pct = 30	                Проверить FVG
# Пт-Сб	 min_confluence_levels = 2	            Проверить конфлюенцию
# Вс	 require_breakout_confirmation = True   Проверить пробой
