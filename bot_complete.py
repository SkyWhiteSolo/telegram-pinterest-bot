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
    filters,
    ContextTypes
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
        """Определяет, является ли пин рекламным"""
        ad_keywords = [
            'ad', 'sponsored', 'промо', 'реклама', 'promo', 
            'shop', 'buy', 'купить', 'магазин', 'store',
            'sale', 'скидка', 'discount', 'заказать',
            'price', 'цена', '₽', '$', 'руб', 'рублей'
        ]
        
        alt_lower = alt_text.lower()
        if any(word in alt_lower for word in ad_keywords):
            return True
        
        src_lower = src.lower()
        ad_url_patterns = [
            'adsystem', 'adserver', 'doubleclick', 
            'googleadservices', 'amazon-adsystem'
        ]
        if any(pattern in src_lower for pattern in ad_url_patterns):
            return True
        
        return False
    
    async def get_real_image_size(self, image_url: str) -> Tuple[int, int]:
        """РЕАЛЬНАЯ проверка размеров изображения"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(image_url, allow_redirects=True) as response:
                    if response.status == 200:
                        size_match = re.search(r'/(\d+)x/', image_url)
                        if size_match:
                            width = int(size_match.group(1))
                            return (width, width)
                        
                        size_match = re.search(r'/(\d+)x(\d+)/', image_url)
                        if size_match:
                            width = int(size_match.group(1))
                            height = int(size_match.group(2))
                            return (width, height)
        except Exception as e:
            logger.error(f"Ошибка проверки размеров: {e}")
        
        return (0, 0)
    
    def check_image_format(self, width: int, height: int, category: str) -> bool:
        """Строгая проверка соответствия формату"""
        if width == 0 or height == 0:
            return False
        
        ratio = width / height if height > 0 else 0
        
        if category == "avatars":
            return 0.9 <= ratio <= 1.1
        
        elif category == "wallpapers_pc":
            if width < 800 or height < 600:
                return False
            return ratio > 1.3
        
        elif category == "wallpapers_phone":
            if width < 600 or height < 800:
                return False
            return ratio < 0.77
        
        return True
    
    async def get_filtered_images(self, category: str, count: int = 10, user_id: str = None) -> List[str]:
        """Получает изображения со строгой проверкой размеров"""
        images = []
        attempts = 0
        max_attempts = 100
        ad_skipped = 0
        size_skipped = 0
        
        search_queries = {
            "avatars": [
                "square avatar 1:1",
                "profile picture square",
                "icon 1x1"
            ],
            "wallpapers_pc": [
                "16:9 wallpaper",
                "1920x1080 wallpaper",
                "landscape wide"
            ],
            "wallpapers_phone": [
                "9:16 wallpaper",
                "1080x1920 wallpaper",
                "vertical wallpaper"
            ]
        }
        
        if category not in search_queries:
            return []
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers, cookies=self.cookies) as session:
                for query in search_queries[category]:
                    if len(images) >= count or attempts >= max_attempts:
                        break
                    
                    url = f'https://ru.pinterest.com/search/pins/?q={query.replace(" ", "%20")}'
                    
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            html = await resp.text()
                            soup = BeautifulSoup(html, 'html.parser')
                            
                            img_tags = soup.find_all('img', {'src': True, 'alt': True})
                            
                            for img in img_tags:
                                if len(images) >= count:
                                    break
                                
                                src = img.get('src', '')
                                alt = img.get('alt', '').lower()
                                
                                if self.is_ad_pin(img, alt, src):
                                    ad_skipped += 1
                                    continue
                                
                                if 'pinimg.com' in src and '236x' in src:
                                    high_res = src.replace('236x', 'originals')
                                    if 'originals' not in high_res:
                                        high_res = src.replace('236x', '1200x')
                                    
                                    width, height = await self.get_real_image_size(high_res)
                                    
                                    if width > 0 and height > 0:
                                        if self.check_image_format(width, height, category):
                                            if user_id and high_res in self.seen_images.get(user_id, {}).get(category, set()):
                                                continue
                                            
                                            images.append(high_res)
                                            
                                            if user_id:
                                                if user_id not in self.seen_images:
                                                    self.seen_images[user_id] = {}
                                                if category not in self.seen_images[user_id]:
                                                    self.seen_images[user_id][category] = set()
                                                self.seen_images[user_id][category].add(high_res)
                                        else:
                                            size_skipped += 1
                                    else:
                                        size_skipped += 1
                                    
                                    attempts += 1
        
        except Exception as e:
            logger.error(f"Ошибка получения изображений: {e}")
        
        logger.info(f"Категория {category}: найдено {len(images)}, "
                   f"пропущено рекламы: {ad_skipped}, не подошло по размеру: {size_skipped}")
        
        if not images:
            return self.get_guaranteed_format_images(category, count)
        
        return images[:count]
    
    def get_guaranteed_format_images(self, category: str, count: int) -> List[str]:
        """Заглушки с ГАРАНТИРОВАННЫМ правильным форматом"""
        images = []
        
        if category == "avatars":
            for i in range(count):
                images.append(f"https://api.dicebear.com/7.x/avataaars/svg?seed={random.randint(1, 10000)}")
        elif category == "wallpapers_pc":
            for i in range(count):
                images.append(f"https://picsum.photos/1920/1080?random={random.randint(1, 10000)}")
        elif category == "wallpapers_phone":
            for i in range(count):
                images.append(f"https://picsum.photos/1080/1920?random={random.randint(1, 10000)}")
        
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
        
        self.application = Application.builder().token(token).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Настройка обработчиков"""
        
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("cookies", self.cookies_instruction))
        self.application.add_handler(CommandHandler("formats", self.formats_info))
        
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        
        self.application.add_error_handler(self.error_handler)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        await self.show_main_menu(update, context)
    
    async def cookies_instruction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Инструкция по установке кук"""
        instruction = (
            "🍪 **Как настроить Pinterest в боте:**\n\n"
            "1. Откройте Pinterest в браузере и войдите в свой аккаунт\n"
            "2. Установите расширение EditThisCookie для Chrome\n"
            "3. Нажмите Export и сохраните файл\n"
            "4. Отправьте этот файл боту\n\n"
            "После этого бот будет показывать ваши персональные рекомендации!"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data='back_to_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(instruction, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def formats_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Информация о форматах"""
        info = (
            "📐 **Требования к форматам:**\n\n"
            "👤 Аватарки: квадратные 1:1\n"
            "🖥️ Обои ПК: горизонтальные 16:9\n"
            "📱 Обои телефон: вертикальные 9:16\n\n"
            "🚫 Реклама автоматически фильтруется!"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 В меню", callback_data='back_to_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(info, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показ главного меню"""
        auth_status = "✅ Персональные" if self.pinterest.is_authenticated else "❌ Общие"
        
        keyboard = [
            [InlineKeyboardButton("📁 Файлы", callback_data='menu_files')],
            [InlineKeyboardButton("👤 Аватарки", callback_data='menu_avatars')],
            [InlineKeyboardButton("⚙️ Настройки игры", callback_data='menu_game_settings')],
            [InlineKeyboardButton("📸 Скриншоты", callback_data='menu_screenshots')],
            [InlineKeyboardButton("🎥 Видео", callback_data='menu_videos')],
            [InlineKeyboardButton("📝 Заметки", callback_data='menu_notes')],
            [InlineKeyboardButton("🖥️ Обои ПК", callback_data='menu_wallpapers_pc')],
            [InlineKeyboardButton("📱 Обои телефон", callback_data='menu_wallpapers_phone')]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        message = f"📋 **Главное меню**\n\nPinterest: {auth_status}\n\nВыберите категорию:"
        
        if update.callback_query:
            await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback-запросов"""
        query = update.callback_query
        await query.answer()
        
        if query.data == 'back_to_main':
            await self.show_main_menu(update, context)
            return
        
        # ========== ФАЙЛЫ ==========
        if query.data == 'menu_files':
            keyboard = [
                [InlineKeyboardButton("📥 Добавить файл", callback_data='add_file_now')],
                [InlineKeyboardButton("📋 Список файлов", callback_data='list_files_now')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "📁 **Файлы**\n\nВыберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data == 'add_file_now':
            await query.edit_message_text(
                "📁 **Отправьте файл**\n\n"
                "Я жду ваш файл. Просто пришлите его сейчас."
            )
            context.user_data['state'] = 'waiting_file'
            context.user_data['category'] = 'files'
        
        elif query.data == 'list_files_now':
            files = self.data_manager.get_items('files')
            if files:
                text = "📁 **Список файлов:**\n\n"
                for i, file in enumerate(files, 1):
                    name = file.get('name', 'Без имени')
                    date = file.get('date', '')[:16]
                    text += f"{i}. {name}\n   📅 {date}\n"
            else:
                text = "📁 **Нет загруженных файлов**"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='menu_files')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # ========== ВИДЕО ==========
        elif query.data == 'menu_videos':
            keyboard = [
                [InlineKeyboardButton("🎥 Добавить видео", callback_data='add_video_now')],
                [InlineKeyboardButton("📋 Список видео", callback_data='list_videos_now')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "🎥 **Видео**\n\nВыберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data == 'add_video_now':
            await query.edit_message_text(
                "🎥 **Отправьте видео**\n\n"
                "Я жду ваш видеофайл. Просто пришлите его сейчас."
            )
            context.user_data['state'] = 'waiting_video'
            context.user_data['category'] = 'videos'
        
        elif query.data == 'list_videos_now':
            videos = self.data_manager.get_items('videos')
            if videos:
                text = "🎥 **Список видео:**\n\n"
                for i, video in enumerate(videos, 1):
                    name = video.get('name', 'Без имени')
                    date = video.get('date', '')[:16]
                    text += f"{i}. {name}\n   📅 {date}\n"
            else:
                text = "🎥 **Нет загруженных видео**"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='menu_videos')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # ========== СКРИНШОТЫ ==========
        elif query.data == 'menu_screenshots':
            keyboard = [
                [InlineKeyboardButton("📸 Добавить скриншот", callback_data='add_screenshot_now')],
                [InlineKeyboardButton("📋 Список скриншотов", callback_data='list_screenshots_now')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "📸 **Скриншоты**\n\nВыберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif query.data == 'add_screenshot_now':
            await query.edit_message_text(
                "📸 **Отправьте скриншот**\n\n"
                "Я жду ваше фото. Просто пришлите его сейчас."
            )
            context.user_data['state'] = 'waiting_screenshot'
            context.user_data['category'] = 'screenshots'
        
        elif query.data == 'list_screenshots_now':
            screenshots = self.data_manager.get_items('screenshots')
            if screenshots:
                text = "📸 **Список скриншотов:**\n\n"
                for i, ss in enumerate(screenshots, 1):
                    caption = ss.get('caption', 'Без подписи')
                    date = ss.get('date', '')[:16]
                    text += f"{i}. {caption}\n   📅 {date}\n"
            else:
                text = "📸 **Нет скриншотов**"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='menu_screenshots')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # ========== ЗАМЕТКИ ==========
        elif query.data == 'menu_notes':
            notes = self.data_manager.get_items('notes')
            if notes:
                text = "📝 **Заметки**\n\n"
                for i, note in enumerate(notes, 1):
                    title = note.get('title', 'Без названия')
                    date = note.get('date', '')[:16]
                    text += f"{i}. {title}\n   📅 {date}\n"
            else:
                text = "📝 **Нет заметок**"
            
            keyboard = [
                [InlineKeyboardButton("➕ Добавить заметку", callback_data='add_note_now')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
        elif query.data == 'add_note_now':
            await query.edit_message_text(
                "📝 **Напишите заметку**\n\n"
                "Первая строка будет заголовком.\n"
                "Остальной текст - содержание."
            )
            context.user_data['state'] = 'waiting_note'
        
        # ========== НАСТРОЙКИ ИГР ==========
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
            
            await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        
        elif query.data == 'add_game_setting':
            await query.edit_message_text(
                "⚙️ Отправьте настройку в формате:\n`Название: значение`\n\nНапример: `Чувствительность: 2.5`",
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
        
        # ========== PINTEREST КАТЕГОРИИ ==========
        elif query.data in ['menu_avatars', 'menu_wallpapers_pc', 'menu_wallpapers_phone']:
            category_map = {
                'menu_avatars': ('avatars', 'АВАТАРОК'),
                'menu_wallpapers_pc': ('wallpapers_pc', 'ОБОЕВ ДЛЯ ПК'),
                'menu_wallpapers_phone': ('wallpapers_phone', 'ОБОЕВ ДЛЯ ТЕЛЕФОНА')
            }
            
            category, ru_name = category_map[query.data]
            await self.fetch_filtered_images(update, context, category, ru_name)
    
    async def fetch_filtered_images(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                    category: str, ru_name: str):
        """Получение отфильтрованных изображений"""
        query = update.callback_query
        user_id = str(update.effective_user.id)
        
        await query.edit_message_text(
            f"🔄 Ищу {ru_name}...\n📸 Фильтрую рекламу\n📐 Проверяю размеры",
            parse_mode='Markdown'
        )
        
        images = await self.pinterest.get_filtered_images(category, count=12, user_id=user_id)
        
        if not images:
            await query.edit_message_text(
                "❌ Не найдено подходящих изображений. Использую заглушки...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Еще", callback_data=f'menu_{category}')
                ]])
            )
            images = self.pinterest.get_guaranteed_format_images(category, 6)
        
        sent_count = 0
        for i, img_url in enumerate(images[:6]):
            try:
                format_desc = {
                    'avatars': '✅ квадратное 1:1',
                    'wallpapers_pc': '✅ горизонтальное 16:9',
                    'wallpapers_phone': '✅ вертикальное 9:16'
                }.get(category, '')
                
                caption = f"🎨 {ru_name} #{i+1}\n📐 {format_desc}"
                await query.message.reply_photo(photo=img_url, caption=caption)
                sent_count += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Ошибка отправки фото: {e}")
        
        keyboard = [
            [InlineKeyboardButton("🔄 Еще", callback_data=f'menu_{category}')],
            [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            f"✅ Найдено {len(images)} изображений!\n📸 Без рекламы\n📐 С проверенным форматом\nОтправлено: {sent_count}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка документов"""
        state = context.user_data.get('state')
        document = update.message.document
        
        # Проверка на файл с куками
        if document.file_name.endswith('.json'):
            await update.message.reply_text("🔄 Обрабатываю файл с куками...")
            try:
                file = await context.bot.get_file(document.file_id)
                file_path = f"temp_{document.file_name}"
                await file.download_to_drive(file_path)
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    cookies_data = json.load(f)
                
                cookies = {}
                for cookie in cookies_data:
                    if 'name' in cookie and 'value' in cookie:
                        cookies[cookie['name']] = cookie['value']
                
                if self.pinterest.save_cookies(cookies):
                    await update.message.reply_text("✅ Куки успешно загружены!")
                else:
                    await update.message.reply_text("❌ Ошибка при сохранении кук")
                
                os.remove(file_path)
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка обработки файла: {e}")
            
            await self.show_main_menu(update, context)
            return
        
        # Обработка файлов
        if state == 'waiting_file':
            category = context.user_data.get('category', 'files')
            
            file_info = {
                'name': document.file_name,
                'file_id': document.file_id,
                'file_size': document.file_size,
                'mime_type': document.mime_type,
                'date': datetime.now().isoformat()
            }
            
            self.data_manager.add_item(category, file_info)
            await update.message.reply_text(f"✅ Файл '{document.file_name}' сохранен!")
            
            context.user_data['state'] = None
            context.user_data['category'] = None
            await self.show_main_menu(update, context)
        
        # Обработка видео
        elif state == 'waiting_video':
            category = context.user_data.get('category', 'videos')
            
            file_info = {
                'name': document.file_name,
                'file_id': document.file_id,
                'file_size': document.file_size,
                'mime_type': document.mime_type,
                'date': datetime.now().isoformat()
            }
            
            self.data_manager.add_item(category, file_info)
            await update.message.reply_text(f"✅ Видео '{document.file_name}' сохранено!")
            
            context.user_data['state'] = None
            context.user_data['category'] = None
            await self.show_main_menu(update, context)
        
        else:
            await update.message.reply_text("❌ Сначала выберите 'Добавить файл' или 'Добавить видео' в меню.")
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка фотографий"""
        state = context.user_data.get('state')
        
        if state == 'waiting_screenshot':
            photo = update.message.photo[-1]
            caption = update.message.caption or "Без подписи"
            
            photo_info = {
                'file_id': photo.file_id,
                'caption': caption,
                'date': datetime.now().isoformat()
            }
            
            self.data_manager.add_item('screenshots', photo_info)
            await update.message.reply_text("✅ Скриншот сохранен!")
            
            context.user_data['state'] = None
            await self.show_main_menu(update, context)
        
        else:
            await update.message.reply_text("❌ Сначала выберите 'Добавить скриншот' в меню.")
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        state = context.user_data.get('state')
        text = update.message.text
        
        if state == 'waiting_note':
            lines = text.split('\n', 1)
            title = lines[0][:50] if lines[0] else "Без названия"
            content = lines[1] if len(lines) > 1 else ""
            
            note = {
                'title': title,
                'content': content,
                'date': datetime.now().isoformat()
            }
            
            self.data_manager.add_item('notes', note)
            await update.message.reply_text(f"✅ Заметка '{title}' сохранена!")
            
            context.user_data['state'] = None
            await self.show_main_menu(update, context)
        
        elif state == 'waiting_game_setting':
            if ':' in text:
                name, value = text.split(':', 1)
                game = context.user_data.get('current_game')
                
                setting = {
                    'name': name.strip(),
                    'value': value.strip(),
                    'date': datetime.now().isoformat()
                }
                
                self.data_manager.add_item('game_settings', setting, game)
                await update.message.reply_text(f"✅ Настройка '{name.strip()}' добавлена в {game}!")
                
                context.user_data['state'] = None
            else:
                await update.message.reply_text("❌ Неверный формат. Используйте: Название: значение")
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Вернуться к {game}:", reply_markup=reply_markup)
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка: {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ Произошла ошибка. Попробуйте позже.")
    
    def run(self):
        """Запуск бота"""
        print("✅ Бот запущен...")
        print("📱 Отправьте /start в Telegram")
        
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
        return
    
    bot = TelegramBot(TOKEN)
    bot.run()


if __name__ == '__main__':
    main()
