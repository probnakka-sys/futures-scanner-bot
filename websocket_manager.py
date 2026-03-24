#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import websockets
from typing import Dict, List, Optional, Callable
from datetime import datetime
import random

logger = logging.getLogger(__name__)

class BingXWebSocketManager:
    """
    Менеджер WebSocket соединений для BingX
    Поддерживает несколько потоков данных и автоматическое переподключение
    """
    
    def __init__(self, api_key: str = None, secret_key: str = None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.ws_url = "wss://open-api-ws.bingx.com/market"  # WebSocket URL BingX
        self.connections = {}
        self.callbacks = {}
        self.prices = {}  # {symbol: {'price': last_price, 'timestamp': time, 'change_1m': []}}
        self.reconnect_delay = 5
        self.max_reconnect_attempts = 10
        self.running = True
        
        # ✅ Загружаем настройки из конфига
        from config import WEBSOCKET_ANALYSIS_SETTINGS
        self.settings = WEBSOCKET_ANALYSIS_SETTINGS
        
        # История цен для разных временных окон
        self.price_history = {}  # {symbol: deque(maxlen=settings['price_history_size'])}
        
        # Счетчики для защиты от спама
        self.signal_counters = {}  # {symbol: {'count': 0, 'minute': timestamp}}
    
    async def connect_ticker_stream(self, symbols: List[str], callback: Callable):
        """
        Подключение к потоку тикеров для списка символов
        """
        stream_name = f"ticker_{'_'.join(symbols)}"
        
        # Формируем запрос на подписку
        subscribe_msg = {
            "id": random.randint(1, 10000),
            "reqType": "sub",
            "dataType": f"{','.join([s.replace('/', '').replace(':USDT', '').upper() + '@ticker' for s in symbols])}"
        }
        
        # Запускаем WebSocket в отдельной задаче
        asyncio.create_task(self._run_websocket(stream_name, subscribe_msg, callback))
        logger.info(f"✅ WebSocket подключен для {len(symbols)} символов")
    
    async def _run_websocket(self, stream_name: str, subscribe_msg: Dict, callback: Callable):
        """
        Запуск WebSocket с автоматическим переподключением
        """
        attempts = 0
        
        while self.running and attempts < self.max_reconnect_attempts:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    logger.info(f"🔌 WebSocket {stream_name} подключен")
                    
                    # Отправляем запрос на подписку
                    await ws.send(json.dumps(subscribe_msg))
                    
                    # Получаем подтверждение
                    response = await ws.recv()
                    logger.info(f"📡 WebSocket {stream_name} подписка подтверждена")
                    
                    # Основной цикл получения сообщений
                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=60)
                            await self._handle_message(message, callback, stream_name)
                        except asyncio.TimeoutError:
                            # Отправляем ping для поддержания соединения
                            await ws.send(json.dumps({"ping": int(datetime.now().timestamp() * 1000)}))
                            continue
                        except websockets.exceptions.ConnectionClosed:
                            logger.warning(f"⚠️ WebSocket {stream_name} соединение закрыто")
                            break
                    
                    # Если вышли по ошибке, сбрасываем счетчик попыток
                    attempts = 0
                    
            except Exception as e:
                attempts += 1
                logger.error(f"❌ WebSocket {stream_name} ошибка: {e}, попытка {attempts}/{self.max_reconnect_attempts}")
                await asyncio.sleep(self.reconnect_delay * attempts)
        
        logger.error(f"❌ WebSocket {stream_name} не удалось подключиться после {self.max_reconnect_attempts} попыток")
    
    async def _handle_message(self, message: str, callback: Callable, stream_name: str):
        """
        Обработка входящего сообщения
        """
        try:
            data = json.loads(message)
            
            # Проверяем, что это обновление цены
            if 'dataType' in data and 'ticker' in data['dataType']:
                await self._process_ticker(data, callback)
            elif 'ping' in data:
                # Отвечаем на ping
                return
            else:
                logger.debug(f"Получено сообщение: {data}")
                
        except json.JSONDecodeError:
            logger.error(f"Ошибка декодирования JSON: {message}")
    
    async def _process_ticker(self, data: Dict, callback: Callable):
        """
        Обработка тикерных данных
        """
        try:
            symbol_data = data.get('data', {})
            bingx_symbol = data.get('dataType', '').replace('@ticker', '')
            
            # Конвертируем символ обратно в наш формат
            symbol = f"{bingx_symbol[:3]}/{bingx_symbol[3:]}:USDT".upper()
            
            current_price = float(symbol_data.get('c', 0))  # последняя цена
            volume = float(symbol_data.get('v', 0))  # объем
            price_change = float(symbol_data.get('P', 0))  # изменение за 24ч
            
            # Сохраняем цену
            self.prices[symbol] = {
                'price': current_price,
                'timestamp': datetime.now(),
                'volume': volume,
                'price_change_24h': price_change
            }
            
            # Сохраняем историю для обнаружения быстрых движений
            if symbol not in self.price_history:
                self.price_history[symbol] = []
            
            self.price_history[symbol].append({
                'price': current_price,
                'time': datetime.now()
            })
            
            # Оставляем только последние 10 значений
            if len(self.price_history[symbol]) > 10:
                self.price_history[symbol].pop(0)
            
            # Проверяем на быстрое движение
            instant_signal = await self._check_instant_movement(symbol)
            if instant_signal:
                await callback('instant', symbol, current_price, instant_signal)
            
        except Exception as e:
            logger.error(f"Ошибка обработки тикера: {e}")
    
    async def _check_instant_movement(self, symbol: str) -> Optional[Dict]:
        """
        Проверка на движение за разные временные окна
        """
        if symbol not in self.price_history or len(self.price_history[symbol]) < 10:
            return None
        
        history = self.price_history[symbol]
        
        # Определяем тип монеты (мейджор или щиткоин)
        is_shitcoin = await self._is_shitcoin(symbol)
        coin_type = 'shitcoin' if is_shitcoin else 'major'
        
        # Проверяем разные временные окна
        for window in self.settings['time_windows']:
            # Находим цену, которая была window секунд назад
            target_time = datetime.now() - timedelta(seconds=window)
            oldest_price = None
            
            for record in reversed(history):
                if record['time'] <= target_time:
                    oldest_price = record['price']
                    break
            
            if not oldest_price:
                continue
            
            # Считаем изменение
            current_price = history[-1]['price']
            change_percent = (current_price - oldest_price) / oldest_price * 100
            time_diff = (history[-1]['time'] - oldest_price['time']).total_seconds()
            
            # Получаем порог для этого окна
            threshold_key = f"{window}s"
            threshold = self.settings['thresholds'][coin_type].get(threshold_key, 2.0)
            
            # Проверяем, не превысили ли лимит сигналов
            if self._check_rate_limit(symbol):
                continue
            
            # Если движение больше порога
            if abs(change_percent) >= threshold:
                return {
                    'change_percent': change_percent,
                    'time_window': round(time_diff, 1),
                    'start_price': oldest_price['price'],
                    'end_price': current_price,
                    'is_shitcoin': is_shitcoin,
                    'threshold_used': threshold,
                    'window_used': window
                }
        
        return None

    def _check_rate_limit(self, symbol: str) -> bool:
        """Проверка лимита сигналов"""
        current_minute = datetime.now().strftime('%Y%m%d%H%M')
        
        if symbol not in self.signal_counters:
            self.signal_counters[symbol] = {'count': 1, 'minute': current_minute}
            return False
        
        counter = self.signal_counters[symbol]
        
        if counter['minute'] != current_minute:
            # Новая минута
            counter['count'] = 1
            counter['minute'] = current_minute
            return False
        
        if counter['count'] >= self.settings['max_signals_per_minute']:
            return True
        
        counter['count'] += 1
        return False

    async def _is_shitcoin(self, symbol: str) -> bool:
        """Определение щиткоина по объему"""
        try:
            # Здесь нужно получить объем 24ч
            # Можно использовать fetcher, если он доступен
            if hasattr(self, 'fetcher'):
                ticker = await self.fetcher.fetch_ticker(symbol)
                volume = ticker.get('volume_24h', 0)
                return volume < self.settings['min_volume_usdt']['shitcoin']
        except:
            pass
        return False
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Получить последнюю цену"""
        if symbol in self.prices:
            return self.prices[symbol]['price']
        return None
    
    def stop(self):
        """Остановка всех соединений"""
        self.running = False
        logger.info("🛑 WebSocket менеджер остановлен")
