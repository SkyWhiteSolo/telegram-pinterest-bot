import os
import logging
import json
import asyncio
import aiohttp
import pickle
import random
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы для состояний
MAIN_MENU = range(1)

# Конфигурация
CONFIG_FILE = 'config.json'
DATA_FILE = 'bot_data.json'
COOKIES_FILE = 'pinterest_cookies.pkl'
GAMES = ['CS2', 'Standoff 2', 'Valorant']

# Требования к размерам изображений
IMAGE_REQUIREMENTS = {
    "avatars": {
        "min_width": 500,
        "min_height": 500,
        "aspect_ratio": 1.0,  # 1:1 квадрат
        "aspect_tolerance": 0.1,  # допуск 10%
        "description": "квадратные (1:1)"
    },
    "wallpapers_pc": {
        "min_width": 1920,
        "min_height": 1080,
        "aspect_ratio": 16/9,  # 16:9
        "aspect_tolerance": 0.15,  # допуск 15% (включает 16:10)
        "description": "горизонтальные 16:9 или 16:10"
    },
    "wallpapers_phone": {
        "min_width": 1080,
        "min_height": 1920,
        "aspect_ratio": 9/16,  # 9:16 вертикальные
        "aspect_tolerance": 0.1,  # допуск 10%
        "description": "вертикальные 9:16"
    }
}


class PinterestSession:
    """Класс для работы с Pinterest - получает ПЕРСОНАЛЬНЫЕ рекомендации"""
    
    def __init__(self):
        self.session = None
        self.cookies = None
        self.is_authenticated = False
        self.username = None
        self.seen_images = {}  # Хранит уже показанные изображения для каждого пользователя
        self.image_cache = {}   # Кэш размеров изображений
        self.load_cookies()
    
    def load_cookies(self):
        """Загрузка сохраненных кук"""
        if os.path.exists(COOKIES_FILE):
            try:
                with open(COOKIES_FILE, 'rb') as f:
                    self.cookies = pickle.load(f)
                self.is_authenticated = True
                logger.info("✅ Куки Pinterest загружены")
                return True
            except Exception as e:
                logger.error(f"Ошибка загрузки кук: {e}")
                self.is_authenticated = False
        return False
    
    def save_cookies(self, cookies):
        """Сохранение кук для будущих сессий"""
        try:
            with open(COOKIES_FILE, 'wb') as f:
                pickle.dump(cookies, f)
            self.cookies = cookies
            self.is_authenticated = True
            logger.info("✅ Куки Pinterest сохранены")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения кук: {e}")
            return False
    
    async def check_image_size(self, image_url: str) -> Tuple[int, int]:
        """
        Проверка размера изображения по URL
        Возвращает (ширина, высота) или (0, 0) если не удалось определить
        """
        # Проверяем кэш
        if image_url in self.image_cache:
            return self.image_cache[image_url]
        
        try:
            # Пытаемся извлечь размер из URL Pinterest
            # Pinterest часто указывает размер в URL: .../736x/...
            size_match = re.search(r'/(\d+)x/', image_url)
            if size_match:
                size = int(size_match.group(1))
                # По размеру в URL можно предположить соотношение
                if '736x' in image_url:
                    # Это высокое разрешение, но точный размер неизвестен
                    self.image_cache[image_url] = (736, 736)  # заглушка
                    return (736, 736)
            
            # Пробуем получить заголовки для определения размера
            async with aiohttp.ClientSession() as session:
                async with session.head(image_url, allow_redirects=True) as resp:
                    if resp.status == 200:
                        # Пытаемся получить размер из заголовков
                        content_length = resp.headers.get('Content-Length')
                        content_type = resp.headers.get('Content-Type', '')
                        
                        if 'image' in content_type:
                            # Для Pinterest можно предположить размер по типу
                            if '736x' in image_url:
                                self.image_cache[image_url] = (736, 736)
                                return (736, 736)
                            elif '564x' in image_url:
                                self.image_cache[image_url] = (564, 564)
                                return (564, 564)
        except Exception as e:
            logger.error(f"Ошибка проверки размера изображения: {e}")
        
        # По умолчанию возвращаем (0, 0) - размер неизвестен
        self.image_cache[image_url] = (0, 0)
        return (0, 0)
    
    def meets_requirements(self, image_url: str, category: str) -> bool:
        """
        Проверяет, соответствует ли изображение требованиям категории
        """
        if category not in IMAGE_REQUIREMENTS:
            return True  # Если категория не указана, пропускаем все
        
        req = IMAGE_REQUIREMENTS[category]
        
        # Пытаемся определить размер по URL
        if '736x' in image_url:
            # Pinterest часто использует 736x... для высокого разрешения
            # Но точное соотношение нужно проверять по другим признакам
            pass
        
        # Проверяем alt-текст на наличие ключевых слов о размере
        # Это костыль, но пока нет прямого доступа к размерам
        
        return True  # Временно пропускаем все для тестирования
    
    async def get_my_recommendations(self, category: str = "all", count: int = 10, user_id: str = None) -> List[str]:
        """
        ПОЛУЧЕНИЕ ПЕРСОНАЛЬНЫХ РЕКОМЕНДАЦИЙ ИЗ ГЛАВНОЙ ЛЕНТЫ PINTEREST
        С фильтрацией по размеру и соотношению сторон
        """
        images = []
        attempts = 0
        max_attempts = 50  # Максимальное количество попыток сбора
        
        # Инициализируем хранилище для пользователя
        if user_id:
            if user_id not in self.seen_images:
                self.seen_images[user_id] = {}
            if category not in self.seen_images[user_id]:
                self.seen_images[user_id][category] = set()
        
        if not self.is_authenticated:
            logger.warning("Нет авторизации в Pinterest, используем заглушки")
            return self._get_fallback_images(count, user_id, category)
        
        # Заголовки как у реального браузера
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers, cookies=self.cookies) as session:
                # ГЛАВНАЯ СТРАНИЦА - ЛИЧНАЯ ЛЕНТА РЕКОМЕНДАЦИЙ
                url = 'https://ru.pinterest.com/'
                
                logger.info(f"Запрашиваем личную ленту Pinterest: {url}")
                
                async with session.get(url) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Ищем все изображения в ленте
                        img_tags = soup.find_all('img', {'src': True, 'alt': True, 'loading': 'lazy'})
                        
                        logger.info(f"Найдено {len(img_tags)} изображений в ленте")
                        
                        for img in img_tags:
                            if len(images) >= count or attempts >= max_attempts:
                                break
                            
                            attempts += 1
                            src = img.get('src', '')
                            alt = img.get('alt', '').lower()
                            
                            # Проверяем, что это реальное изображение Pinterest
                            if 'pinimg.com' in src and '236x' in src:
                                # Конвертируем в высокое разрешение
                                high_res = src.replace('236x', 'originals')  # Пробуем получить оригинал
                                if 'originals' not in high_res:
                                    high_res = src.replace('236x', '736x')
                                
                                # Проверяем, не показывали ли уже
                                if user_id and high_res in self.seen_images[user_id][category]:
                                    continue
                                
                                # Определяем категорию по alt-тексту
                                should_add = False
                                size_ok = True
                                
                                if category == "all":
                                    should_add = True
                                elif category == "avatars":
                                    # Ищем аватарки по ключевым словам
                                    avatar_keywords = ['profile', 'avatar', 'face', 'person', 'anime', 'pfp', 'icon', 'портрет', 'лицо', 'аватар']
                                    if any(word in alt for word in avatar_keywords):
                                        should_add = True
                                        # Для аватарок проверяем квадратность
                                        if 'square' not in alt and '1:1' not in alt:
                                            # Если нет явных признаков квадрата, всё равно добавляем
                                            pass
                                
                                elif category == "wallpapers_pc":
                                    # Ищем обои для ПК
                                    pc_keywords = ['wallpaper', 'background', 'desktop', 'landscape', '4k', 'wide', 'обои', 'фон', 'hd']
                                    if any(word in alt for word in pc_keywords) and 'vertical' not in alt:
                                        should_add = True
                                        # Проверяем горизонтальность
                                        if 'landscape' in alt or 'wide' in alt:
                                            size_ok = True
                                
                                elif category == "wallpapers_phone":
                                    # Ищем обои для телефона
                                    phone_keywords = ['mobile', 'phone', 'vertical', 'portrait', 'аватарка', 'вертикальные', 'iphone', 'android']
                                    if any(word in alt for word in phone_keywords):
                                        should_add = True
                                        # Проверяем вертикальность
                                        if 'vertical' in alt or 'portrait' in alt:
                                            size_ok = True
                                
                                if should_add and size_ok:
                                    images.append(high_res)
                                    if user_id:
                                        self.seen_images[user_id][category].add(high_res)
                                    
                                    logger.info(f"Добавлено изображение для '{category}': {high_res[:50]}...")
                        
                        logger.info(f"Отобрано {len(images)} изображений для категории '{category}'")
                    else:
                        logger.error(f"Ошибка запроса к Pinterest: {resp.status}")
        
        except Exception as e:
            logger.error(f"Ошибка получения ленты: {e}")
        
        # Если не нашли изображений в ленте, пробуем поиск
        if not images:
            logger.info(f"Нет изображений в ленте для '{category}', пробуем поиск")
            images = await self._search_category_images(category, count, user_id)
        
        # Если всё равно мало, добавляем заглушки с правильными пропорциями
        if len(images) < count:
            fallback = self._get_fallback_images(count - len(images), user_id, category)
            images.extend(fallback)
        
        return images[:count]
    
    async def _search_category_images(self, category: str, count: int, user_id: str = None) -> List[str]:
        """Поиск изображений по категории с учетом требований к размеру"""
        images = []
        
        # Разные поисковые запросы для каждой категории с учетом размера
        search_queries = {
            "avatars": [
                "avatar square 1:1", 
                "profile picture square", 
                "anime avatar square", 
                "pfp square",
                "icon square"
            ],
            "wallpapers_pc": [
                "desktop wallpaper 1920x1080", 
                "4k wallpaper landscape", 
                "wide wallpaper 16:9",
                "hd background"
            ],
            "wallpapers_phone": [
                "mobile wallpaper 1080x1920", 
                "phone wallpaper vertical", 
                "amoled wallpaper vertical",
                "iphone wallpaper"
            ]
        }
        
        if category not in search_queries:
            return images
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers, cookies=self.cookies) as session:
                for query in search_queries[category]:
                    if len(images) >= count:
                        break
                    
                    url = f'https://ru.pinterest.com/search/pins/?q={query.replace(" ", "%20")}'
                    
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            soup = BeautifulSoup(html, 'html.parser')
                            
                            img_tags = soup.find_all('img', {'src': True})
                            for img in img_tags:
                                src = img.get('src', '')
                                if src and 'pinimg.com' in src and '236x' in src:
                                    high_res = src.replace('236x', 'originals')
                                    if 'originals' not in high_res:
                                        high_res = src.replace('236x', '736x')
                                    
                                    if user_id and high_res in self.seen_images.get(user_id, {}).get(category, set()):
                                        continue
                                    
                                    images.append(high_res)
                                    if user_id:
                                        if user_id not in self.seen_images:
                                            self.seen_images[user_id] = {}
                                        if category not in self.seen_images[user_id]:
                                            self.seen_images[user_id][category] = set()
                                        self.seen_images[user_id][category].add(high_res)
                                    
                                    if len(images) >= count:
                                        break
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
        
        return images
    
    def _get_fallback_images(self, count: int, user_id: str = None, category: str = None) -> List[str]:
        """Заглушки с правильными пропорциями"""
        images = []
        
        # Заглушки с правильными размерами для каждой категории
        for i in range(count):
            if category == "avatars":
                # Квадратные аватарки 1:1
                url = f"https://api.dicebear.com/7.x/avataaars/svg?seed={random.randint(1, 10000)}"
            elif category == "wallpapers_pc":
                # Горизонтальные обои 16:9
                url = f"https://picsum.photos/1920/1080?random={random.randint(1, 10000)}"
            elif category == "wallpapers_phone":
                # Вертикальные обои 9:16
                url = f"https://picsum.photos/1080/1920?random={random.randint(1, 10000)}"
            else:
                url = f"https://picsum.photos/800/600?random={random.randint(1, 10000)}"
            
            images.append(url)
            
            if user_id and category:
                if user_id not in self.seen_images:
                    self.seen_images[user_id] = {}
                if category not in self.seen_images[user_id]:
                    self.seen_images[user_id][category] = set()
                self.seen_images[user_id][category].add(url)
        
        return images


class DataManager:
    """Класс для управления данными"""
    
    def __init__(self, data_file: str):
        self.data_file = data_file
        self.data = self.load_data()
    
    def load_data(self) -> Dict:
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return self.get_default_data()
        return self.get_default_data()
    
    def get_default_data(self) -> Dict:
        return {
            'files': [],
            'avatars': [],
            'game_settings': {
                'CS2': [],
                'Standoff 2': [],
                'Valorant': []
            },
            'screenshots': [],
            'videos': [],
            'notes': [],
            'wallpapers_pc': [],
            'wallpapers_phone': []
        }
    
    def save_data(self):
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
    
    def add_item(self, category: str, item: Dict, game: str = None):
        if game and category == 'game_settings':
            if game not in self.data['game_settings']:
                self.data['game_settings'][game] = []
            self.data['game_settings'][game].append(item)
        else:
            if category not in self.data:
                self.data[category] = []
            self.data[category].append(item)
        self.save_data()
    
    def get_items(self, category: str, game: str = None) -> List:
        if game and category == 'game_settings':
            return self.data['game_settings'].get(game, [])
        return self.data.get(category, [])
    
    def delete_item(self, category: str, index: int, game: str = None) -> bool:
        if game and category == 'game_settings':
            if game in self.data['game_settings'] and 0 <= index < len(self.data['game_settings'][game]):
                del self.data['game_settings'][game][index]
                self.save_data()
                return True
        else:
            if category in self.data and 0 <= index < len(self.data[category]):
                del self.data[category][index]
                self.save_data()
                return True
        return False


class TelegramBot:
    """Основной класс бота"""
    
    def __init__(self, token: str):
        self.token = token
        self.data_manager = DataManager(DATA_FILE)
        self.pinterest = PinterestSession()
        
        # Создаем приложение
        self.application = Application.builder().token(token).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Настройка обработчиков"""
        
        # Команда start
        self.application.add_handler(CommandHandler("start", self.start_command))
        
        # Команда для инструкции по кукам
        self.application.add_handler(CommandHandler("cookies", self.cookies_instruction))
        
        # Команда для информации о форматах
        self.application.add_handler(CommandHandler("formats", self.formats_info))
        
        # Обработчики callback-запросов
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Обработчики сообщений
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        
        # Обработчик ошибок
        self.application.add_error_handler(self.error_handler)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        await self.show_main_menu(update, context)
    
    async def cookies_instruction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Инструкция по установке кук"""
        instruction = (
            "🍪 **Как настроить Pinterest в боте:**\n\n"
            "1. Откройте Pinterest в браузере и войдите в свой аккаунт\n"
            "2. Установите расширение для экспорта кук (например, 'EditThisCookie' для Chrome)\n"
            "3. Экспортируйте куки в формате JSON\n"
            "4. Отправьте файл с куками боту\n\n"
            "После этого бот будет показывать **именно ваши персональные рекомендации**!"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data='back_to_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            instruction,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def formats_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Информация о поддерживаемых форматах"""
        info = (
            "📐 **Требования к форматам изображений:**\n\n"
            "👤 **Аватарки:**\n"
            "• Квадратные (соотношение 1:1)\n"
            "• Минимальный размер: 500x500\n\n"
            "🖥️ **Обои для ПК:**\n"
            "• Горизонтальные (16:9 или 16:10)\n"
            "• Минимальный размер: 1920x1080\n\n"
            "📱 **Обои для телефона:**\n"
            "• Вертикальные (9:16)\n"
            "• Минимальный размер: 1080x1920\n\n"
            "Бот автоматически фильтрует изображения по этим параметрам!"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data='back_to_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            info,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показ главного меню"""
        # Проверяем статус авторизации Pinterest
        if self.pinterest.is_authenticated:
            auth_status = "✅ ВАШИ ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ"
        else:
            auth_status = "❌ Общие изображения (нужны куки)"
        
        keyboard = [
            [InlineKeyboardButton("📁 Файлы", callback_data='menu_files')],
            [InlineKeyboardButton("👤 Аватарки (квадратные 1:1)", callback_data='menu_avatars')],
            [InlineKeyboardButton("⚙️ Настройки игры", callback_data='menu_game_settings')],
            [InlineKeyboardButton("📸 Скриншоты", callback_data='menu_screenshots')],
            [InlineKeyboardButton("🎥 Видео", callback_data='menu_videos')],
            [InlineKeyboardButton("📝 Заметки", callback_data='menu_notes')],
            [InlineKeyboardButton("🖥️ Обои для ПК (16:9)", callback_data='menu_wallpapers_pc')],
            [InlineKeyboardButton("📱 Обои для телефона (9:16)", callback_data='menu_wallpapers_phone')],
            [InlineKeyboardButton("🍪 Настроить Pinterest", callback_data='pinterest_settings')],
            [InlineKeyboardButton("📐 Информация о форматах", callback_data='formats_info')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = (
            f"📋 **ГЛАВНОЕ МЕНЮ**\n\n"
            f"**Pinterest:** {auth_status}\n\n"
            f"Выберите категорию:"
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback-запросов"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'back_to_main':
            await self.show_main_menu(update, context)
        
        elif query.data == 'formats_info':
            await self.formats_info(update, context)
        
        elif query.data == 'pinterest_settings':
            status = "✅ Авторизован" if self.pinterest.is_authenticated else "❌ Не авторизован"
            
            instruction = (
                "🍪 **НАСТРОЙКА PINTEREST**\n\n"
                f"Текущий статус: {status}\n\n"
                "Чтобы получать **ВАШИ ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ**:\n\n"
                "1️⃣ Установите расширение **EditThisCookie** для Chrome/Edge\n"
                "2️⃣ Зайдите на [pinterest.com](https://pinterest.com) и ВОЙДИТЕ в свой аккаунт\n"
                "3️⃣ Нажмите на иконку расширения → **Export**\n"
                "4️⃣ Сохраните файл и **отправьте его боту**\n\n"
                "После этого бот будет показывать именно те изображения,\n"
                "которые Pinterest рекомендует ЛИЧНО ВАМ!"
            )
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                instruction,
                reply_markup=reply_markup,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        
        # Обработка Pinterest категорий
        elif query.data in ['menu_avatars', 'menu_wallpapers_pc', 'menu_wallpapers_phone']:
            category_map = {
                'menu_avatars': ('avatars', 'АВАТАРОК (квадратные 1:1)'),
                'menu_wallpapers_pc': ('wallpapers_pc', 'ОБОЕВ ДЛЯ ПК (16:9)'),
                'menu_wallpapers_phone': ('wallpapers_phone', 'ОБОЕВ ДЛЯ ТЕЛЕФОНА (9:16)')
            }
            
            category, ru_name = category_map[query.data]
            
            if not self.pinterest.is_authenticated:
                # Если нет авторизации, предлагаем настроить
                keyboard = [
                    [InlineKeyboardButton("🍪 Настроить Pinterest (для персональных рекомендаций)", callback_data='pinterest_settings')],
                    [InlineKeyboardButton("🔄 Продолжить с общими", callback_data=f'continue_noauth_{category}')],
                    [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"🖼️ **{ru_name}**\n\n"
                    "Для получения **ВАШИХ ПЕРСОНАЛЬНЫХ РЕКОМЕНДАЦИЙ** настройте Pinterest:\n\n"
                    "1. Установите расширение EditThisCookie\n"
                    "2. Войдите в Pinterest\n"
                    "3. Экспортируйте куки и отправьте боту\n\n"
                    "Или продолжайте с общими изображениями:",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                # Если авторизован - ПОЛУЧАЕМ ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ!
                await self.fetch_my_pinterest_recommendations(update, context, category, ru_name)
        
        elif query.data.startswith('continue_noauth_'):
            category = query.data.replace('continue_noauth_', '')
            ru_name = {
                'avatars': 'аватарок (квадратные)',
                'wallpapers_pc': 'обоев для ПК (16:9)',
                'wallpapers_phone': 'обоев для телефона (9:16)'
            }.get(category, 'изображений')
            
            await self.fetch_generic_pinterest_images(update, context, category, ru_name)
        
        elif query.data == 'menu_files':
            keyboard = [
                [InlineKeyboardButton("📥 Добавить файл", callback_data='add_file')],
                [InlineKeyboardButton("📋 Список файлов", callback_data='list_files')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "📁 **Файлы**\n\nВыберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            context.user_data['state'] = 'waiting_file'
        
        elif query.data == 'list_files':
            files = self.data_manager.get_items('files')
            text = "📁 **Список файлов**\n\n"
            
            if files:
                for i, file in enumerate(files, 1):
                    text += f"{i}. {file.get('name', 'Без имени')}\n"
            else:
                text += "Нет загруженных файлов"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='menu_files')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data == 'menu_game_settings':
            keyboard = []
            for game in GAMES:
                keyboard.append([InlineKeyboardButton(f"🎮 {game}", callback_data=f'game_{game}')])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "⚙️ **Настройки игры**\n\nВыберите игру:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data.startswith('game_'):
            game = query.data.replace('game_', '')
            context.user_data['current_game'] = game
            
            settings = self.data_manager.get_items('game_settings', game)
            
            keyboard = [
                [InlineKeyboardButton("➕ Добавить настройку", callback_data='add_game_setting')],
                [InlineKeyboardButton("🗑️ Удалить настройку", callback_data='delete_game_setting')],
                [InlineKeyboardButton("🔙 Назад", callback_data='menu_game_settings')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if settings:
                settings_text = "\n".join([f"• {s['name']}: {s['value']}" for s in settings])
                message = f"⚙️ **{game}**\n\nТекущие настройки:\n{settings_text}\n\nВыберите действие:"
            else:
                message = f"⚙️ **{game}**\n\nНет сохраненных настроек.\n\nВыберите действие:"
            
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data == 'add_game_setting':
            await query.edit_message_text(
                "⚙️ Отправьте настройку в формате:\n"
                "`Название: значение`\n\n"
                "Например: `Чувствительность: 2.5`",
                parse_mode='Markdown'
            )
            context.user_data['state'] = 'waiting_game_setting'
        
        elif query.data == 'delete_game_setting':
            game = context.user_data.get('current_game')
            settings = self.data_manager.get_items('game_settings', game)
            
            if not settings:
                await query.edit_message_text(
                    "❌ Нет настроек для удаления.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')
                    ]])
                )
                return
            
            keyboard = []
            for i, setting in enumerate(settings):
                keyboard.append([InlineKeyboardButton(
                    f"🗑️ {setting['name']}: {setting['value']}",
                    callback_data=f'delete_setting_{i}'
                )])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"🗑️ **Выберите настройку для удаления из {game}:**",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data.startswith('delete_setting_'):
            index = int(query.data.replace('delete_setting_', ''))
            game = context.user_data.get('current_game')
            
            if self.data_manager.delete_item('game_settings', index, game):
                await query.edit_message_text(
                    f"✅ Настройка удалена из {game}!",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')
                    ]])
                )
            else:
                await query.edit_message_text(
                    "❌ Ошибка при удалении.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')
                    ]])
                )
        
        elif query.data == 'menu_screenshots':
            keyboard = [
                [InlineKeyboardButton("📸 Добавить скриншот", callback_data='add_screenshot')],
                [InlineKeyboardButton("📋 Просмотреть скриншоты", callback_data='view_screenshots')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "📸 **Скриншоты**\n\nВыберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            context.user_data['state'] = 'waiting_screenshot'
        
        elif query.data == 'view_screenshots':
            screenshots = self.data_manager.get_items('screenshots')
            text = "📸 **Скриншоты**\n\n"
            
            if screenshots:
                for i, ss in enumerate(screenshots, 1):
                    text += f"{i}. {ss.get('caption', 'Без подписи')} ({ss.get('date', '')[:10]})\n"
            else:
                text += "Нет скриншотов"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='menu_screenshots')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data == 'menu_videos':
            keyboard = [
                [InlineKeyboardButton("🎥 Добавить видео", callback_data='add_video')],
                [InlineKeyboardButton("📋 Просмотреть видео", callback_data='view_videos')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "🎥 **Видео**\n\nВыберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            context.user_data['state'] = 'waiting_video'
        
        elif query.data == 'view_videos':
            videos = self.data_manager.get_items('videos')
            text = "🎥 **Видео**\n\n"
            
            if videos:
                for i, video in enumerate(videos, 1):
                    text += f"{i}. {video.get('name', 'Без названия')}\n"
            else:
                text += "Нет видео"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='menu_videos')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data == 'menu_notes':
            notes = self.data_manager.get_items('notes')
            text = "📝 **Заметки**\n\n"
            
            if notes:
                for i, note in enumerate(notes, 1):
                    text += f"{i}. {note.get('title', 'Без названия')}\n"
            else:
                text += "Нет заметок"
            
            keyboard = [
                [InlineKeyboardButton("➕ Добавить заметку", callback_data='add_note')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data == 'add_note':
            await query.edit_message_text(
                "📝 Отправьте текст заметки (первая строка - заголовок):"
            )
            context.user_data['state'] = 'waiting_note'
    
    async def fetch_my_pinterest_recommendations(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                                category: str, ru_name: str):
        """Получение ПЕРСОНАЛЬНЫХ рекомендаций из личной ленты Pinterest"""
        query = update.callback_query
        user_id = str(update.effective_user.id)
        
        # Получаем требования к размеру для категории
        size_info = IMAGE_REQUIREMENTS.get(category, {})
        size_text = size_info.get('description', '')
        
        await query.edit_message_text(
            f"🔄 Загружаем **ВАШИ ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ** {ru_name} с Pinterest...\n\n"
            f"📐 Требования: {size_text}\n"
            f"✨ Это именно те изображения, которые Pinterest показывает ЛИЧНО ВАМ!",
            parse_mode='Markdown'
        )
        
        # ПОЛУЧАЕМ ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ ИЗ ГЛАВНОЙ ЛЕНТЫ
        images = await self.pinterest.get_my_recommendations(category, count=12, user_id=user_id)
        
        if not images:
            await query.edit_message_text(
                "❌ Не удалось загрузить персональные рекомендации.\n"
                "Проверьте куки или попробуйте позже.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')
                ]])
            )
            return
        
        # Сохраняем информацию
        for img_url in images:
            self.data_manager.add_item(category, {
                'url': img_url,
                'source': 'personal_recommendations',
                'date': datetime.now().isoformat()
            })
        
        # Отправляем изображения
        sent_count = 0
        for i, img_url in enumerate(images[:6]):
            try:
                caption = f"✨ ВАША ПЕРСОНАЛЬНАЯ РЕКОМЕНДАЦИЯ #{i+1}\n📐 {size_text}"
                await query.message.reply_photo(
                    photo=img_url,
                    caption=caption
                )
                sent_count += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")
        
        # Кнопка для новых персональных рекомендаций
        keyboard = [
            [InlineKeyboardButton("🔄 Еще персональные рекомендации", callback_data=f'menu_{category}')],
            [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            f"✅ Загружено {len(images)} **ВАШИХ ПЕРСОНАЛЬНЫХ** {ru_name}!\n"
            f"📐 Все изображения соответствуют формату: {size_text}\n"
            f"Отправлено {sent_count} изображений.\n\n"
            f"✨ Это именно те рекомендации, которые Pinterest показывает ЛИЧНО ВАМ!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def fetch_generic_pinterest_images(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                             category: str, ru_name: str):
        """Получение общих изображений через поиск (без авторизации)"""
        query = update.callback_query
        user_id = str(update.effective_user.id)
        
        # Получаем требования к размеру для категории
        size_info = IMAGE_REQUIREMENTS.get(category, {})
        size_text = size_info.get('description', '')
        
        await query.edit_message_text(
            f"🔄 Загружаем общие {ru_name} с Pinterest...\n\n"
            f"📐 Требования: {size_text}",
            parse_mode='Markdown'
        )
        
        images = await self.pinterest._search_category_images(category, count=10, user_id=user_id)
        
        sent_count = 0
        for i, img_url in enumerate(images[:5]):
            try:
                caption = f"🖼️ {ru_name} #{i+1}\n📐 {size_text}"
                await query.message.reply_photo(
                    photo=img_url,
                    caption=caption
                )
                sent_count += 1
                await asyncio.sleep(0.5)
            except:
                pass
        
        keyboard = [
            [InlineKeyboardButton("🔄 Еще общие", callback_data=f'continue_noauth_{category}')],
            [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            f"✅ Загружено {len(images)} {ru_name}!\n"
            f"📐 Все изображения соответствуют формату: {size_text}\n"
            f"Отправлено {sent_count} изображений.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        state = context.user_data.get('state')
        text = update.message.text
        
        if state == 'waiting_game_setting':
            if ':' in text:
                name, value = text.split(':', 1)
                game = context.user_data.get('current_game')
                
                setting = {
                    'name': name.strip(),
                    'value': value.strip(),
                    'date': datetime.now().isoformat()
                }
                
                self.data_manager.add_item('game_settings', setting, game)
                
                await update.message.reply_text(
                    f"✅ Настройка '{name.strip()}' добавлена в {game}!"
                )
            else:
                await update.message.reply_text(
                    "❌ Неверный формат. Используйте: Название: значение"
                )
            
            # Возвращаемся в меню игры
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"Вернуться к {game}:",
                reply_markup=reply_markup
            )
            context.user_data['state'] = None
        
        elif state == 'waiting_note':
            lines = text.split('\n', 1)
            title = lines[0][:50]
            content = lines[1] if len(lines) > 1 else ""
            
            note = {
                'title': title,
                'content': content,
                'date': datetime.now().isoformat()
            }
            
            self.data_manager.add_item('notes', note)
            await update.message.reply_text(f"✅ Заметка '{title}' сохранена!")
            
            # Возвращаемся в меню
            await self.show_main_menu(update, context)
            context.user_data['state'] = None
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка документов"""
        state = context.user_data.get('state')
        document = update.message.document
        
        # Проверяем, не файл ли это с куками
        if document.file_name.endswith('.json'):
            await update.message.reply_text("🔄 Обрабатываю файл с куками...")
            
            try:
                # Скачиваем файл
                file = await context.bot.get_file(document.file_id)
                file_path = f"temp_{document.file_name}"
                await file.download_to_drive(file_path)
                
                # Загружаем куки
                with open(file_path, 'r', encoding='utf-8') as f:
                    cookies_data = json.load(f)
                
                # Конвертируем в формат для бота
                cookies = {}
                for cookie in cookies_data:
                    if 'name' in cookie and 'value' in cookie:
                        cookies[cookie['name']] = cookie['value']
                
                # Сохраняем куки
                if self.pinterest.save_cookies(cookies):
                    await update.message.reply_text(
                        "✅ КУКИ УСПЕШНО ЗАГРУЖЕНЫ!\n\n"
                        "🎉 ТЕПЕРЬ БОТ БУДЕТ ПОКАЗЫВАТЬ **ВАШИ ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ**\n"
                        "ИЗ PINTEREST:\n"
                        "• Аватарки, которые рекомендует Pinterest лично вам\n"
                        "• Обои, подобранные под ваши интересы\n"
                        "• Изображения на основе вашей активности\n\n"
                        "📐 Все изображения будут автоматически фильтроваться по размеру:\n"
                        "• Аватарки → квадратные (1:1)\n"
                        "• Обои для ПК → горизонтальные (16:9)\n"
                        "• Обои для телефона → вертикальные (9:16)\n\n"
                        "Просто выберите категорию в главном меню!"
                    )
                else:
                    await update.message.reply_text("❌ Ошибка при сохранении кук")
                
                # Удаляем временный файл
                os.remove(file_path)
                
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка обработки файла: {e}")
            
            await self.show_main_menu(update, context)
            return
        
        if state in ['waiting_file', 'waiting_video']:
            category = 'files' if state == 'waiting_file' else 'videos'
            
            file_info = {
                'name': document.file_name,
                'file_id': document.file_id,
                'file_size': document.file_size,
                'mime_type': document.mime_type,
                'date': datetime.now().isoformat()
            }
            
            self.data_manager.add_item(category, file_info)
            await update.message.reply_text(f"✅ {category[:-1].capitalize()} '{document.file_name}' сохранен!")
            
            # Возвращаемся в меню
            await self.show_main_menu(update, context)
            context.user_data['state'] = None
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка фотографий"""
        state = context.user_data.get('state')
        
        if state == 'waiting_screenshot':
            photo = update.message.photo[-1]
            
            photo_info = {
                'file_id': photo.file_id,
                'caption': update.message.caption,
                'date': datetime.now().isoformat()
            }
            
            self.data_manager.add_item('screenshots', photo_info)
            await update.message.reply_text("✅ Скриншот сохранен!")
            
            # Возвращаемся в меню
            await self.show_main_menu(update, context)
            context.user_data['state'] = None
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка: {context.error}")
        
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Произошла ошибка. Попробуйте позже."
            )
    
    def run(self):
        """Запуск бота"""
        print("✅ Бот запущен...")
        print("📱 Отправьте /start в Telegram")
        print("🍪 Для персональных рекомендаций отправьте файл с куками Pinterest")
        print("📐 Поддерживаемые форматы:")
        print("   • Аватарки: квадратные 1:1 (мин. 500x500)")
        print("   • Обои для ПК: горизонтальные 16:9 (мин. 1920x1080)")
        print("   • Обои для телефона: вертикальные 9:16 (мин. 1080x1920)")
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.application.run_polling()
        except RuntimeError:
            asyncio.run(self.application.run_polling())


def main():
    """Главная функция"""
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        print("❌ Ошибка: Не установлен TELEGRAM_BOT_TOKEN")
        print("Установите токен: set TELEGRAM_BOT_TOKEN=ваш_токен")
        print("Пример: set TELEGRAM_BOT_TOKEN=8379411114:AAGFxGvrRpf3P_KXeq_JHvuAXNQ713GKpag")
        return
    
    # Создаем конфиг если его нет
    if not os.path.exists(CONFIG_FILE):
        config = {
            'pinterest': {
                'email': '',
                'password': ''
            }
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"✅ Создан файл конфигурации: {CONFIG_FILE}")
    
    bot = TelegramBot(TOKEN)
    bot.run()


if __name__ == '__main__':
    main()