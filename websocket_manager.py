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
        
        # История цен для обнаружения быстрых движений
        self.price_history = {}  # {symbol: deque(maxlen=20)}
    
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
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
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
            instant_signal = self._check_instant_movement(symbol)
            if instant_signal:
                await callback('instant', symbol, current_price, instant_signal)
            
        except Exception as e:
            logger.error(f"Ошибка обработки тикера: {e}")
    
    def _check_instant_movement(self, symbol: str) -> Optional[Dict]:
        """
        Проверка на мгновенное движение (1-2 секунды)
        """
        if symbol not in self.price_history or len(self.price_history[symbol]) < 3:
            return None
        
        history = self.price_history[symbol]
        
        # Берем самую старую и самую новую цену
        oldest = history[0]
        newest = history[-1]
        
        time_diff = (newest['time'] - oldest['time']).total_seconds()
        
        # Если прошло меньше 5 секунд
        if time_diff < 5:
            change_percent = (newest['price'] - oldest['price']) / oldest['price'] * 100
            
            # Если движение больше порога (например 2%)
            if abs(change_percent) >= 2.0:
                return {
                    'change_percent': change_percent,
                    'time_window': round(time_diff, 1),
                    'start_price': oldest['price'],
                    'end_price': newest['price']
                }
        
        return None
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Получить последнюю цену"""
        if symbol in self.prices:
            return self.prices[symbol]['price']
        return None
    
    def stop(self):
        """Остановка всех соединений"""
        self.running = False
        logger.info("🛑 WebSocket менеджер остановлен")
