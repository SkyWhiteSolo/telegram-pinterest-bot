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


class PinterestSession:
    """Класс для работы с Pinterest с фильтрацией рекламы и проверкой размеров"""
    
    def __init__(self):
        self.session = None
        self.cookies = None
        self.is_authenticated = False
        self.seen_images = {}
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
    
    def is_ad_pin(self, img_tag, alt_text: str, src: str) -> bool:
        """
        Определяет, является ли пин рекламным
        """
        # Ключевые слова, указывающие на рекламу
        ad_keywords = [
            'ad', 'sponsored', 'промо', 'реклама', 'promo', 
            'shop', 'buy', 'купить', 'магазин', 'store',
            'sale', 'скидка', 'discount', 'заказать',
            'price', 'цена', '₽', '$', 'руб', 'рублей',
            'limited', 'offer', 'code', 'промокод'
        ]
        
        # Проверяем alt текст
        alt_lower = alt_text.lower()
        if any(word in alt_lower for word in ad_keywords):
            logger.info(f"Реклама обнаружена по alt: {alt_text[:50]}")
            return True
        
        # Проверяем URL на признаки рекламы
        src_lower = src.lower()
        ad_url_patterns = [
            'adsystem', 'adserver', 'doubleclick', 
            'googleadservices', 'amazon-adsystem',
            'analytics', 'tracking', 'pixel'
        ]
        if any(pattern in src_lower for pattern in ad_url_patterns):
            logger.info(f"Реклама обнаружена по URL: {src[:50]}")
            return True
        
        # Проверяем наличие промо-атрибутов в теге
        if img_tag.get('data-sponsored') == 'true':
            logger.info("Реклама обнаружена по атрибуту data-sponsored")
            return True
        
        # Проверяем на наличие цены в alt
        price_patterns = [r'\d+\s?₽', r'\d+\s?руб', r'\$\d+', r'€\d+']
        for pattern in price_patterns:
            if re.search(pattern, alt_text, re.IGNORECASE):
                logger.info(f"Реклама обнаружена по цене: {alt_text[:50]}")
                return True
        
        return False
    
    def check_image_format(self, width: int, height: int, category: str) -> bool:
        """
        Проверяет, соответствует ли изображение требуемому формату
        """
        if width == 0 or height == 0:
            # Если размер неизвестен, проверяем по категории
            return True
        
        if category == "avatars":
            # Для аватарок нужно соотношение близкое к 1:1 (квадрат)
            ratio = width / height if height > 0 else 0
            is_square = 0.8 <= ratio <= 1.2  # Допуск 20%
            if not is_square:
                logger.info(f"Не квадратное: {width}x{height}")
            return is_square
        
        elif category == "wallpapers_pc":
            # Для обоев ПК нужно горизонтальное (ширина > высоты)
            if width < 1280 or height < 720:  # Минимальный размер
                logger.info(f"Слишком маленькое для ПК: {width}x{height}")
                return False
            is_landscape = width > height * 1.3  # Соотношение примерно 16:9
            if not is_landscape:
                logger.info(f"Не горизонтальное для ПК: {width}x{height}")
            return is_landscape
        
        elif category == "wallpapers_phone":
            # Для обоев телефона нужно вертикальное (высота > ширины)
            if width < 720 or height < 1280:  # Минимальный размер
                logger.info(f"Слишком маленькое для телефона: {width}x{height}")
                return False
            is_portrait = height > width * 1.3  # Соотношение примерно 9:16
            if not is_portrait:
                logger.info(f"Не вертикальное для телефона: {width}x{height}")
            return is_portrait
        
        return True
    
    async def check_image_dimensions(self, image_url: str) -> Tuple[int, int]:
        """
        Проверяет реальные размеры изображения по URL
        """
        try:
            # Пытаемся получить заголовки
            async with aiohttp.ClientSession() as session:
                async with session.head(image_url, allow_redirects=True) as response:
                    if response.status == 200:
                        # Пробуем определить размер из URL Pinterest
                        size_match = re.search(r'/(\d+)x/', image_url)
                        if size_match:
                            width = int(size_match.group(1))
                            # Pinterest часто использует 736x как базовый размер
                            if '736x' in image_url:
                                return (736, 736)
                            elif '564x' in image_url:
                                return (564, 564)
                            elif '236x' in image_url:
                                return (236, 236)
                        
                        # Пробуем получить размер из заголовка Content-Length
                        content_length = response.headers.get('Content-Length')
                        if content_length:
                            # Примерная оценка: для JPEG 736x736 ~ 100-200 KB
                            size_kb = int(content_length) / 1024
                            if size_kb > 100:  # Больше 100 KB
                                return (736, 736)
        except Exception as e:
            logger.error(f"Ошибка проверки размеров: {e}")
        
        return (0, 0)
    
    async def get_filtered_images(self, category: str, count: int = 10, user_id: str = None) -> List[str]:
        """
        Получает изображения с фильтрацией рекламы и проверкой формата
        """
        images = []
        attempts = 0
        max_attempts = 50
        ad_skipped = 0
        format_skipped = 0
        
        # Поисковые запросы без рекламных слов
        search_queries = {
            "avatars": [
                "avatar art", "character portrait", "anime face", 
                "profile picture aesthetic", "icon art",
                "square avatar", "1:1 portrait",
                "cool avatar", "anime pfp"
            ],
            "wallpapers_pc": [
                "landscape art", "nature scene", "digital art landscape",
                "scenery background", "aesthetic desktop",
                "4k wallpaper", "wide wallpaper",
                "mountain landscape", "cityscape"
            ],
            "wallpapers_phone": [
                "vertical art", "portrait scene", "aesthetic vertical",
                "nature vertical", "digital art vertical",
                "mobile wallpaper", "phone background",
                "vertical landscape", "portrait wallpaper"
            ]
        }
        
        if category not in search_queries:
            return []
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers, cookies=self.cookies) as session:
                for query in search_queries[category]:
                    if len(images) >= count or attempts >= max_attempts:
                        break
                    
                    url = f'https://ru.pinterest.com/search/pins/?q={query.replace(" ", "%20")}'
                    logger.info(f"Поиск: {url}")
                    
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            soup = BeautifulSoup(html, 'html.parser')
                            
                            # Ищем все изображения
                            img_tags = soup.find_all('img', {'src': True, 'alt': True})
                            
                            for img in img_tags:
                                if len(images) >= count:
                                    break
                                
                                src = img.get('src', '')
                                alt = img.get('alt', '').lower()
                                
                                # Фильтр 1: Проверяем на рекламу
                                if self.is_ad_pin(img, alt, src):
                                    ad_skipped += 1
                                    continue
                                
                                # Фильтр 2: Проверяем что это Pinterest изображение
                                if 'pinimg.com' in src and '236x' in src:
                                    # Конвертируем в высокое разрешение
                                    high_res = src.replace('236x', 'originals')
                                    if 'originals' not in high_res:
                                        high_res = src.replace('236x', '736x')
                                    
                                    # Проверяем размеры
                                    width, height = await self.check_image_dimensions(high_res)
                                    
                                    if self.check_image_format(width, height, category):
                                        # Проверяем на дубликаты
                                        if user_id and high_res in self.seen_images.get(user_id, {}).get(category, set()):
                                            continue
                                        
                                        images.append(high_res)
                                        
                                        # Сохраняем в историю
                                        if user_id:
                                            if user_id not in self.seen_images:
                                                self.seen_images[user_id] = {}
                                            if category not in self.seen_images[user_id]:
                                                self.seen_images[user_id][category] = set()
                                            self.seen_images[user_id][category].add(high_res)
                                        
                                        logger.info(f"✅ Найдено подходящее изображение: {category}")
                                    else:
                                        format_skipped += 1
                                    
                                    attempts += 1
                        else:
                            logger.error(f"Ошибка запроса: {resp.status}")
        
        except Exception as e:
            logger.error(f"Ошибка получения изображений: {e}")
        
        logger.info(f"Категория {category}: найдено {len(images)} изображений, "
                   f"пропущено рекламы: {ad_skipped}, не подошло по формату: {format_skipped}")
        
        # Если не нашли ни одного, возвращаем заглушки
        if not images:
            logger.info(f"Использую заглушки для {category}")
            return self.get_fallback_images(category, count)
        
        return images[:count]
    
    def get_fallback_images(self, category: str, count: int) -> List[str]:
        """Заглушки с правильными пропорциями (без рекламы)"""
        images = []
        
        if category == "avatars":
            # Квадратные аватарки
            for i in range(count):
                images.append(f"https://api.dicebear.com/7.x/avataaars/svg?seed={random.randint(1, 10000)}")
        elif category == "wallpapers_pc":
            # Горизонтальные обои 16:9
            for i in range(count):
                images.append(f"https://picsum.photos/1920/1080?random={random.randint(1, 10000)}")
        elif category == "wallpapers_phone":
            # Вертикальные обои 9:16
            for i in range(count):
                images.append(f"https://picsum.photos/1080/1920?random={random.randint(1, 10000)}")
        else:
            for i in range(count):
                images.append(f"https://picsum.photos/800/600?random={random.randint(1, 10000)}")
        
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
            "🚫 **Реклама автоматически фильтруется!**"
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
            f"**Pinterest:** {auth_status}\n"
            f"**Фильтрация:** ✅ Без рекламы, ✅ По формату\n\n"
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
                'menu_avatars': ('avatars', 'АВАТАРОК'),
                'menu_wallpapers_pc': ('wallpapers_pc', 'ОБОЕВ ДЛЯ ПК'),
                'menu_wallpapers_phone': ('wallpapers_phone', 'ОБОЕВ ДЛЯ ТЕЛЕФОНА')
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
                    "Или продолжайте с общими изображениями (с фильтрацией):",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                # Если авторизован - ПОЛУЧАЕМ ПЕРСОНАЛЬНЫЕ РЕКОМЕНДАЦИИ!
                await self.fetch_filtered_images(update, context, category, ru_name)
        
        elif query.data.startswith('continue_noauth_'):
            category = query.data.replace('continue_noauth_', '')
            ru_name = {
                'avatars': 'аватарок',
                'wallpapers_pc': 'обоев для ПК',
                'wallpapers_phone': 'обоев для телефона'
            }.get(category, 'изображений')
            
            await self.fetch_filtered_images(update, context, category, ru_name)
        
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
    
    async def fetch_filtered_images(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                    category: str, ru_name: str):
        """Получение отфильтрованных изображений (без рекламы и по формату)"""
        query = update.callback_query
        user_id = str(update.effective_user.id)
        
        await query.edit_message_text(
            f"🔄 Ищу {ru_name}...\n"
            f"📸 Отфильтровываю рекламу\n"
            f"📐 Проверяю формат",
            parse_mode='Markdown'
        )
        
        # Получаем изображения с фильтрацией
        images = await self.pinterest.get_filtered_images(category, count=12, user_id=user_id)
        
        if not images:
            await query.edit_message_text(
                "❌ Не удалось найти изображения.\n"
                "Использую заглушки...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Еще", callback_data=f'menu_{category}')
                ]])
            )
            # Используем заглушки
            images = self.pinterest.get_fallback_images(category, 6)
        
        # Отправляем изображения
        sent_count = 0
        for i, img_url in enumerate(images[:6]):
            try:
                # Определяем описание формата
                format_desc = {
                    'avatars': 'квадратное',
                    'wallpapers_pc': 'горизонтальное 16:9',
                    'wallpapers_phone': 'вертикальное 9:16'
                }.get(category, '')
                
                caption = f"🎨 {ru_name} #{i+1}\n📐 {format_desc}"
                await query.message.reply_photo(
                    photo=img_url,
                    caption=caption
                )
                sent_count += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")
        
        # Кнопка для новых изображений
        keyboard = [
            [InlineKeyboardButton("🔄 Еще", callback_data=f'menu_{category}')],
            [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            f"✅ Найдено {len(images)} изображений!\n"
            f"📸 Все без рекламы\n"
            f"📐 В правильном формате\n"
            f"Отправлено: {sent_count}",
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
                        "🚫 Реклама будет автоматически отфильтрована!"
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
        print("🚫 Реклама автоматически фильтруется!")
        
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
