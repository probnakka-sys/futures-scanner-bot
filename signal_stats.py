#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import asyncio
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

# ============== НАСТРОЙКИ СТАТИСТИКИ ==============

STATS_SETTINGS = {
    'enabled': True,
    'stats_chat_id': os.getenv('STATS_CHAT_ID', ''),
    'daily_report_time': '20:00',
    'update_interval': 300,  # 5 минут
    'history_days': 90,       # храним 90 дней
    'db_file': 'signals_database.json'
}

class SignalStatistics:
    """Система статистики сигналов с поддержкой кнопок и команд"""
    
    def __init__(self, bot, stats_chat_id: str):
        self.bot = bot
        self.stats_chat_id = stats_chat_id
        self.db_file = STATS_SETTINGS['db_file']
        self.load_database()
    
    def load_database(self):
        """Загрузка базы сигналов"""
        try:
            with open(self.db_file, 'r', encoding='utf-8') as f:
                self.db = json.load(f)
        except FileNotFoundError:
            self.db = {
                'signals': {},
                'statistics': {
                    'total_signals': 0,
                    'by_type': {'regular': 0, 'pump': 0},
                    'by_power': {},
                    'by_pair': {},
                    'daily_stats': {}
                }
            }
            self.save_database()
    
    def save_database(self):
        """Сохранение базы сигналов"""
        try:
            with open(self.db_file, 'w', encoding='utf-8') as f:
                json.dump(self.db, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка сохранения базы: {e}")
    
    def add_signal(self, signal: Dict, signal_type: str = 'regular') -> str:
        """Добавление нового сигнала в базу"""
        signal_id = f"{signal['symbol']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Очищаем старые сигналы
        self.cleanup_old_signals()
        
        # Определяем тип сигнала
        if signal.get('pump_dump'):
            signal_type = 'pump'
        
        self.db['signals'][signal_id] = {
            'id': signal_id,
            'type': signal_type,
            'symbol': signal['symbol'],
            'coin': signal['symbol'].split('/')[0],
            'direction': signal['direction'],
            'entry_price': signal['price'],
            'target_1': signal.get('target_1'),
            'target_2': signal.get('target_2'),
            'stop_loss': signal.get('stop_loss'),
            'signal_power': signal['signal_power'],
            'signal_strength': signal.get('signal_strength', 0),
            'reasons': signal['reasons'][:3],
            'status': 'pending',
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'max_price': signal['price'],
            'min_price': signal['price'],
            'final_result': None,
            'profit_percent': 0
        }
        
        # Обновляем статистику
        self.db['statistics']['total_signals'] += 1
        self.db['statistics']['by_type'][signal_type] += 1
        
        self.save_database()
        return signal_id
    
    def update_signal(self, signal_id: str, current_price: float):
        """Обновление статуса сигнала по текущей цене"""
        if signal_id not in self.db['signals']:
            return
        
        signal = self.db['signals'][signal_id]
        if signal['status'] != 'pending':
            return
        
        # Обновляем максимум/минимум
        signal['max_price'] = max(signal['max_price'], current_price)
        signal['min_price'] = min(signal['min_price'], current_price)
        
        old_status = signal['status']
        profit_pct = 0
        
        # Проверяем достижение целей
        if 'LONG' in signal['direction'] or 'Разворот LONG' in signal['direction']:
            if current_price >= signal['target_2']:
                signal['status'] = 'victory'
                signal['final_result'] = 'target_2_hit'
                profit_pct = ((signal['target_2'] - signal['entry_price']) / signal['entry_price']) * 100
            elif current_price >= signal['target_1']:
                signal['status'] = 'profit'
                signal['final_result'] = 'target_1_hit'
                profit_pct = ((signal['target_1'] - signal['entry_price']) / signal['entry_price']) * 100
            elif current_price <= signal['stop_loss']:
                signal['status'] = 'loss'
                signal['final_result'] = 'stop_loss'
                profit_pct = ((signal['stop_loss'] - signal['entry_price']) / signal['entry_price']) * 100
        else:  # SHORT или Разворот SHORT
            if current_price <= signal['target_2']:
                signal['status'] = 'victory'
                signal['final_result'] = 'target_2_hit'
                profit_pct = ((signal['entry_price'] - signal['target_2']) / signal['entry_price']) * 100
            elif current_price <= signal['target_1']:
                signal['status'] = 'profit'
                signal['final_result'] = 'target_1_hit'
                profit_pct = ((signal['entry_price'] - signal['target_1']) / signal['entry_price']) * 100
            elif current_price >= signal['stop_loss']:
                signal['status'] = 'loss'
                signal['final_result'] = 'stop_loss'
                profit_pct = ((signal['entry_price'] - signal['stop_loss']) / signal['entry_price']) * 100
        
        signal['profit_percent'] = round(profit_pct, 2)
        signal['updated_at'] = datetime.now().isoformat()
        
        # Если статус изменился, отправляем уведомление
        if old_status != signal['status'] and signal['status'] in ['victory', 'profit', 'loss']:
            asyncio.create_task(self.send_result_notification(signal_id))
        
        self.save_database()
    
    def cleanup_old_signals(self):
        """Удаление старых сигналов"""
        cutoff = datetime.now() - timedelta(days=STATS_SETTINGS['history_days'])
        to_delete = []
        
        for signal_id, signal in self.db['signals'].items():
            created = datetime.fromisoformat(signal['created_at'])
            if created < cutoff:
                to_delete.append(signal_id)
        
        for signal_id in to_delete:
            del self.db['signals'][signal_id]
    
    def get_statistics(self, days: int = 7, signal_type: str = 'all', 
                      coin: str = None, power_filter: str = None) -> Dict:
        """Получение статистики за период с фильтрами"""
        cutoff = datetime.now() - timedelta(days=days)
        
        stats = {
            'total': 0,
            'victory': 0,     # цель 2
            'profit': 0,      # цель 1
            'loss': 0,        # стоп
            'pending': 0,
            'by_type': {'regular': 0, 'pump': 0},
            'by_power': defaultdict(lambda: {'total': 0, 'victory': 0, 'profit': 0, 'loss': 0}),
            'by_pair': defaultdict(lambda: {'total': 0, 'success': 0, 'profit_sum': 0}),
            'total_profit': 0,
            'avg_profit': 0
        }
        
        for signal_id, signal in self.db['signals'].items():
            created = datetime.fromisoformat(signal['created_at'])
            if created < cutoff:
                continue
            
            # Фильтр по типу
            if signal_type != 'all' and signal['type'] != signal_type:
                continue
            
            # Фильтр по монете
            if coin and signal['coin'] != coin:
                continue
            
            # Фильтр по силе
            if power_filter and signal['signal_power'] != power_filter:
                continue
            
            stats['total'] += 1
            stats['by_type'][signal['type']] += 1
            
            # Статистика по статусам
            if signal['status'] in ['victory', 'profit', 'loss']:
                stats[signal['status']] += 1
                stats['total_profit'] += signal.get('profit_percent', 0)
            elif signal['status'] == 'pending':
                stats['pending'] += 1
            
            # Статистика по силе сигнала
            power = signal['signal_power']
            stats['by_power'][power]['total'] += 1
            if signal['status'] in ['victory', 'profit']:
                stats['by_power'][power]['victory'] += 1
                if signal['status'] == 'victory':
                    stats['by_power'][power]['profit'] += 1
            elif signal['status'] == 'loss':
                stats['by_power'][power]['loss'] += 1
            
            # Статистика по парам
            pair = signal['coin']
            stats['by_pair'][pair]['total'] += 1
            if signal['status'] in ['victory', 'profit']:
                stats['by_pair'][pair]['success'] += 1
                stats['by_pair'][pair]['profit_sum'] += signal.get('profit_percent', 0)
        
        if stats['total'] > 0:
            successful = stats['victory'] + stats['profit']
            stats['win_rate'] = round((successful / stats['total']) * 100, 1)
            stats['avg_profit'] = round(stats['total_profit'] / stats['total'], 2)
        
        return stats
    
    def format_stats_message(self, stats: Dict, days: int, 
                            signal_type: str = 'all', coin: str = None) -> str:
        """Форматирование статистики в читаемое сообщение"""
        
        # Заголовок
        if coin:
            title = f"📊 СТАТИСТИКА ПО {coin} ЗА {days} ДНЕЙ"
        elif signal_type == 'regular':
            title = f"📊 ОБЫЧНЫЕ СИГНАЛЫ ЗА {days} ДНЕЙ"
        elif signal_type == 'pump':
            title = f"🚀 ПАМП-СИГНАЛЫ ЗА {days} ДНЕЙ"
        else:
            title = f"📊 ОБЩАЯ СТАТИСТИКА ЗА {days} ДНЕЙ"
        
        if stats['total'] == 0:
            return f"{title}\n\n❌ Нет данных за этот период"
        
        msg = f"{title}\n\n"
        msg += f"📈 Всего сигналов: {stats['total']}\n"
        msg += f"🏆 Достигли цели 2: {stats['victory']}\n"
        msg += f"💰 Достигли цели 1: {stats['profit']}\n"
        msg += f"❌ Сработал стоп: {stats['loss']}\n"
        msg += f"🔄 В процессе: {stats['pending']}\n"
        msg += f"📊 Win Rate: {stats['win_rate']}%\n"
        msg += f"💵 Средняя прибыль: {stats['avg_profit']}%\n\n"
        
        # Статистика по типам если общая
        if signal_type == 'all':
            msg += f"*По типам сигналов:*\n"
            msg += f"📊 Обычные: {stats['by_type']['regular']}\n"
            msg += f"🚀 Пампы: {stats['by_type']['pump']}\n\n"
        
        # Статистика по силе
        if stats['by_power']:
            msg += f"*По силе сигнала:*\n"
            for power, data in stats['by_power'].items():
                if data['total'] > 0:
                    win = data['victory'] + data['profit']
                    rate = (win / data['total']) * 100
                    msg += f"{power}: {win}/{data['total']} ({rate:.1f}%)\n"
            msg += "\n"
        
        # Лучшие монеты
        if not coin and stats['by_pair']:
            top_pairs = sorted(stats['by_pair'].items(), 
                              key=lambda x: x[1]['success']/x[1]['total'] if x[1]['total']>0 else 0, 
                              reverse=True)[:3]
            msg += f"*Лучшие монеты:*\n"
            for pair, data in top_pairs:
                if data['total'] > 0:
                    rate = (data['success'] / data['total']) * 100
                    msg += f"• {pair}: {data['success']}/{data['total']} ({rate:.1f}%)\n"
        
        return msg
    
    async def send_daily_report(self):
        """Ежедневный отчет в группу статистики"""
        stats = self.get_statistics(days=1)
        
        if stats['total'] == 0:
            return
        
        msg = self.format_stats_message(stats, 1)
        msg += f"\n\n📌 /stats - общая статистика"
        msg += f"\n📌 /stats pump - только пампы"
        msg += f"\n📌 /stats regular - только обычные"
        
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        keyboard = [
            [InlineKeyboardButton("📊 Общая", callback_data="stats_7"),
             InlineKeyboardButton("🚀 Пампы", callback_data="stats_pump_7")],
            [InlineKeyboardButton("📈 По монетам", callback_data="stats_coins"),
             InlineKeyboardButton("❓ Помощь", callback_data="stats_help")]
        ]
        
        await self.bot.send_message(
            chat_id=self.stats_chat_id,
            text=msg,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def send_result_notification(self, signal_id: str):
        """Уведомление о результате сигнала"""
        signal = self.db['signals'][signal_id]
        
        emoji = {
            'victory': '🏆',
            'profit': '💰',
            'loss': '❌'
        }.get(signal['status'], '🔄')
        
        msg = f"{emoji} *Сигнал завершен*\n\n"
        msg += f"Монета: `{signal['coin']}`\n"
        msg += f"Тип: {'🚀 Памп' if signal['type'] == 'pump' else '📊 Обычный'}\n"
        msg += f"Направление: {signal['direction']}\n"
        msg += f"Вход: {signal['entry_price']}\n"
        msg += f"Результат: {signal['final_result']}\n"
        msg += f"Прибыль: {signal['profit_percent']:+.2f}%\n"
        
        await self.bot.send_message(
            chat_id=self.stats_chat_id,
            text=msg,
            parse_mode='Markdown'
        )
