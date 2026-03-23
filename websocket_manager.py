#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import websockets
import gzip
from typing import Dict, List, Optional, Callable
from datetime import datetime, timedelta  # ✅ ДОБАВЛЕН timedelta
import random

logger = logging.getLogger(__name__)

class BingXWebSocketManager:
    """
    Менеджер WebSocket соединений для BingX
    Поддерживает несколько потоков данных и автоматическое переподключение
    """
    
    def __init__(self, api_key: str = None, secret_key: str = None, telegram_bot=None):
        self.api_key = api_key
        self.secret_key = secret_key
        self.telegram_bot = telegram_bot  # ✅ ДОБАВЛЕН telegram_bot
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
        
        logger.info("✅ BingX WebSocket Manager инициализирован")
    
    async def connect_ticker_stream(self, symbols: List[str], callback: Callable):
        """
        Подключение к потоку тикеров для списка символов
        """
        stream_name = f"ticker_{len(symbols)}"
        
        # Формируем запрос на подписку
        subscribe_msg = {
            "id": random.randint(1, 10000),
            "reqType": "sub",
            "dataType": f"{','.join([s.replace('/', '').replace(':USDT', '').upper() + '@ticker' for s in symbols[:50]])}"  # ограничиваем до 50 для безопасности
        }
        
        # Запускаем WebSocket в отдельной задаче
        asyncio.create_task(self._run_websocket(stream_name, subscribe_msg, symbols, callback))
        logger.info(f"✅ WebSocket подключен для {len(symbols)} символов")
    
    async def _run_websocket(self, stream_name: str, subscribe_msg: Dict, symbols: List[str], callback: Callable):
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
                    try:
                        response = await ws.recv()
                        logger.info(f"📡 WebSocket {stream_name} подписка подтверждена: {response}")
                    except asyncio.TimeoutError:
                        logger.warning(f"⚠️ WebSocket {stream_name} нет подтверждения")
                    
                    # Основной цикл получения сообщений
                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=60)
                            await self._handle_message(message, symbols, callback)
                        except asyncio.TimeoutError:
                            # Отправляем ping для поддержания соединения
                            try:
                                await ws.send(json.dumps({"ping": int(datetime.now().timestamp() * 1000)}))
                            except:
                                pass
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
    
    async def _handle_message(self, message: str, symbols: List[str], callback: Callable):
        """
        Обработка входящего сообщения
        """
        try:
            # ✅ РАСПАКОВКА GZIP
            if isinstance(message, bytes):
                try:
                    # Пробуем распаковать gzip
                    decompressed = gzip.decompress(message)
                    message = decompressed.decode('utf-8')
                except:
                    # Если не gzip, просто декодируем
                    message = message.decode('utf-8', errors='ignore')
            
            data = json.loads(message)
            
            # Проверяем, что это обновление цены
            if 'data' in data and 'c' in data.get('data', {}):
                await self._process_ticker(data, symbols, callback)
            elif 'ping' in data:
                return
            else:
                logger.debug(f"Получено сообщение: {data}")
                
        except json.JSONDecodeError:
            logger.error(f"Ошибка декодирования JSON: {message[:200]}")
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")
    
    async def _process_ticker(self, data: Dict, symbols: List[str], callback: Callable):
        """
        Обработка тикерных данных
        """
        try:
            symbol_data = data.get('data', {})
            bingx_symbol = data.get('dataType', '').replace('@ticker', '')
            
            if not bingx_symbol:
                return
            
            # Конвертируем символ обратно в наш формат
            symbol = self._convert_symbol(bingx_symbol)
            
            # Проверяем, что символ в нашем списке
            if symbol not in symbols:
                return
            
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
            
            # Оставляем только последние значения
            max_history = self.settings.get('price_history_size', 100)
            if len(self.price_history[symbol]) > max_history:
                self.price_history[symbol] = self.price_history[symbol][-max_history:]
            
            # Проверяем на быстрое движение
            instant_signal = await self._check_instant_movement(symbol)
            if instant_signal:
                logger.info(f"⚡ Обнаружено движение {symbol}: {instant_signal['change_percent']:.1f}% за {instant_signal['time_window']:.1f} сек")
                await callback('instant', symbol, current_price, instant_signal)
            
        except Exception as e:
            logger.error(f"Ошибка обработки тикера: {e}")
    
    def _convert_symbol(self, bingx_symbol: str) -> str:
        """
        Конвертация символа BingX в формат CCXT
        BingX формат: "BTCUSDT" или "1000PEPEUSDT"
        """
        if bingx_symbol.endswith('USDT'):
            coin = bingx_symbol[:-4]  # убираем USDT
            # Для 1000PEPE, 1000SHIB и т.д.
            if coin.startswith('1000'):
                coin = coin[4:]  # убираем 1000
            return f"{coin}/USDT:USDT"
        return bingx_symbol
    
    async def _check_instant_movement(self, symbol: str) -> Optional[Dict]:
        """
        Проверка на движение за разные временные окна
        """
        if symbol not in self.price_history or len(self.price_history[symbol]) < 5:
            return None
        
        history = self.price_history[symbol]
        
        # Определяем тип монеты (мейджор или щиткоин)
        coin = symbol.split('/')[0].upper()
        # Топ-5 мейджоров
        majors = ['BTC', 'ETH', 'BNB', 'SOL', 'XRP']
        is_shitcoin = coin not in majors
        
        coin_type = 'shitcoin' if is_shitcoin else 'major'
        
        # Проверяем разные временные окна
        for window in self.settings.get('time_windows', [3, 5, 10, 30, 60]):
            # Находим цену, которая была window секунд назад
            target_time = datetime.now() - timedelta(seconds=window)
            oldest_price = None
            oldest_time = None
            
            for record in reversed(history):
                if record['time'] <= target_time:
                    oldest_price = record['price']
                    oldest_time = record['time']
                    break
            
            if not oldest_price:
                continue
            
            # Считаем изменение
            current_price = history[-1]['price']
            change_percent = (current_price - oldest_price) / oldest_price * 100
            time_diff = (history[-1]['time'] - oldest_time).total_seconds()
            
            # Получаем порог для этого окна
            threshold_key = f"{window}s"
            thresholds = self.settings.get('thresholds', {})
            coin_thresholds = thresholds.get(coin_type, {})
            threshold = coin_thresholds.get(threshold_key, 2.0)
            
            # Проверяем, не превысили ли лимит сигналов
            if self._check_rate_limit(symbol):
                continue
            
            # Если движение больше порога
            if abs(change_percent) >= threshold:
                return {
                    'change_percent': change_percent,
                    'time_window': round(time_diff, 1),
                    'start_price': oldest_price,
                    'end_price': current_price,
                    'is_shitcoin': is_shitcoin,
                    'threshold_used': threshold,
                    'window_used': window
                }
        
        return None
    
    def _check_rate_limit(self, symbol: str) -> bool:
        """
        Проверка лимита сигналов
        """
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
        
        max_per_minute = self.settings.get('max_signals_per_minute', 5)
        if counter['count'] >= max_per_minute:
            return True
        
        counter['count'] += 1
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
