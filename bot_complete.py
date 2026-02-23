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
DATA_FILE = 'bot_data.json'
COOKIES_FILE = 'pinterest_cookies.pkl'
GAMES = ['CS2', 'Standoff 2', 'Valorant']


class PinterestSession:
    """Класс для работы с Pinterest через куки (личные рекомендации)"""
    
    def __init__(self):
        self.cookies = None
        self.is_authenticated = False
        self.seen_images = {}  # Для отслеживания уже показанных изображений
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
        """Сохранение кук"""
        try:
            with open(COOKIES_FILE, 'wb') as f:
                pickle.dump(cookies, f)
            self.cookies = cookies
            self.is_authenticated = True
            logger.info("✅ Куки сохранены")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения кук: {e}")
            return False
    
    def is_ad_pin(self, img_tag, alt_text: str, src: str) -> bool:
        """Проверка на рекламу"""
        ad_keywords = [
            'ad', 'sponsored', 'промо', 'реклама', 'promo',
            'shop', 'buy', 'купить', 'магазин', 'store',
            'sale', 'скидка', 'discount', 'price', 'цена'
        ]
        
        alt_lower = alt_text.lower()
        if any(word in alt_lower for word in ad_keywords):
            logger.info(f"Реклама: {alt_text[:50]}")
            return True
        
        if img_tag.get('data-sponsored') == 'true':
            return True
        
        return False
    
    async def get_image_size(self, url: str) -> Tuple[int, int]:
        """Определение размера изображения по URL"""
        # Пробуем найти размер в URL
        size_match = re.search(r'/(\d+)x/', url)
        if size_match:
            width = int(size_match.group(1))
            return (width, width)
        
        size_match = re.search(r'/(\d+)x(\d+)/', url)
        if size_match:
            width = int(size_match.group(1))
            height = int(size_match.group(2))
            return (width, height)
        
        return (0, 0)
    
    def check_format(self, width: int, height: int, category: str) -> bool:
        """Проверка соответствия формату"""
        if width == 0 or height == 0:
            return True  # Если размер неизвестен, пропускаем
        
        ratio = width / height if height > 0 else 0
        
        if category == "avatars":
            # Квадрат 1:1 (с допуском)
            return 0.8 <= ratio <= 1.2
        
        elif category == "wallpapers_pc":
            # Горизонтальные (ширина > высоты)
            return ratio > 1.3 and width >= 800
        
        elif category == "wallpapers_phone":
            # Вертикальные (высота > ширины)
            return ratio < 0.8 and height >= 800
        
        return True
    
    async def get_my_feed(self, category: str, limit: int = 10, user_id: str = None) -> List[str]:
        """
        Парсинг личной ленты Pinterest (ТВОИ РЕКОМЕНДАЦИИ)
        """
        if not self.is_authenticated:
            logger.warning("Нет авторизации")
            return self.get_fallback_images(category, limit)
        
        images = []
        found = 0
        attempts = 0
        max_attempts = 50
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers, cookies=self.cookies) as session:
                # Главная страница = личная лента
                url = 'https://ru.pinterest.com/'
                logger.info("Загружаю твою личную ленту...")
                
                async with session.get(url) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Ищем все картинки
                        for img in soup.find_all('img', {'src': True, 'alt': True}):
                            if len(images) >= limit or attempts >= max_attempts:
                                break
                            
                            src = img.get('src', '')
                            alt = img.get('alt', '').lower()
                            
                            # Проверка на рекламу
                            if self.is_ad_pin(img, alt, src):
                                attempts += 1
                                continue
                            
                            # Только Pinterest картинки
                            if 'pinimg.com' in src and '236x' in src:
                                high_res = src.replace('236x', '736x')
                                
                                # Проверка на дубликаты
                                if user_id and high_res in self.seen_images.get(user_id, {}).get(category, set()):
                                    attempts += 1
                                    continue
                                
                                # Проверка формата
                                w, h = await self.get_image_size(high_res)
                                if self.check_format(w, h, category):
                                    images.append(high_res)
                                    
                                    # Запоминаем, что показали
                                    if user_id:
                                        if user_id not in self.seen_images:
                                            self.seen_images[user_id] = {}
                                        if category not in self.seen_images[user_id]:
                                            self.seen_images[user_id][category] = set()
                                        self.seen_images[user_id][category].add(high_res)
                                    
                                    found += 1
                                    logger.info(f"✅ Найдено подходящее: {w}x{h}")
                                
                                attempts += 1
                        
                        logger.info(f"Всего найдено: {len(images)}")
        
        except Exception as e:
            logger.error(f"Ошибка загрузки ленты: {e}")
        
        # Если ничего не нашли - заглушки
        if not images:
            logger.info("Использую заглушки")
            return self.get_fallback_images(category, limit)
        
        return images[:limit]
    
    def get_fallback_images(self, category: str, count: int) -> List[str]:
        """Заглушки с правильными форматами"""
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
    """Управление данными"""
    
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
            'game_settings': {'CS2': [], 'Standoff 2': [], 'Valorant': []},
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
    def __init__(self, token: str):
        self.token = token
        self.data_manager = DataManager(DATA_FILE)
        self.pinterest = PinterestSession()
        
        self.application = Application.builder().token(token).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CallbackQueryHandler(self.callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.document))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.photo))
        self.application.add_error_handler(self.error)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        auth = "✅" if self.pinterest.is_authenticated else "❌"
        keyboard = [
            [InlineKeyboardButton(f"📁 Файлы", callback_data='menu_files')],
            [InlineKeyboardButton(f"👤 Аватарки {auth}", callback_data='menu_avatars')],
            [InlineKeyboardButton("⚙️ Настройки игр", callback_data='menu_games')],
            [InlineKeyboardButton("📸 Скриншоты", callback_data='menu_screens')],
            [InlineKeyboardButton("🎥 Видео", callback_data='menu_videos')],
            [InlineKeyboardButton("📝 Заметки", callback_data='menu_notes')],
            [InlineKeyboardButton(f"🖥️ Обои ПК {auth}", callback_data='menu_pc')],
            [InlineKeyboardButton(f"📱 Обои телефон {auth}", callback_data='menu_phone')],
            [InlineKeyboardButton("🍪 Загрузить куки", callback_data='load_cookies')]
        ]
        await update.message.reply_text("Меню:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        logger.info(f"Callback: {query.data}")
        
        if query.data == 'back':
            await self.start(update, context)
            return
        
        if query.data == 'load_cookies':
            await query.edit_message_text(
                "🍪 Отправь JSON файл с куками от Pinterest",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад", callback_data='back')
                ]])
            )
            context.user_data['state'] = 'waiting_cookies'
            return
        
        # ===== АВАТАРКИ (ТВОИ ЛИЧНЫЕ) =====
        if query.data == 'menu_avatars':
            await query.edit_message_text("🔄 Загружаю твои личные рекомендации...")
            
            images = await self.pinterest.get_my_feed('avatars', 10, str(update.effective_user.id))
            
            sent = 0
            for url in images[:6]:
                try:
                    await query.message.reply_photo(photo=url, caption="✨ Твоя рекомендация")
                    sent += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"Ошибка: {e}")
            
            await query.message.reply_text(
                f"✅ Найдено: {len(images)}, отправлено: {sent}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Еще", callback_data='menu_avatars'),
                    InlineKeyboardButton("🔙 Назад", callback_data='back')
                ]])
            )
            return
        
        # ===== ОБОИ ПК =====
        if query.data == 'menu_pc':
            await query.edit_message_text("🔄 Загружаю твои личные рекомендации...")
            
            images = await self.pinterest.get_my_feed('wallpapers_pc', 8, str(update.effective_user.id))
            
            sent = 0
            for url in images[:4]:
                try:
                    await query.message.reply_photo(photo=url, caption="🖥️ Твоя рекомендация")
                    sent += 1
                    await asyncio.sleep(0.5)
                except:
                    pass
            
            await query.message.reply_text(
                f"✅ Найдено: {len(images)}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Еще", callback_data='menu_pc'),
                    InlineKeyboardButton("🔙 Назад", callback_data='back')
                ]])
            )
            return
        
        # ===== ОБОИ ТЕЛЕФОН =====
        if query.data == 'menu_phone':
            await query.edit_message_text("🔄 Загружаю твои личные рекомендации...")
            
            images = await self.pinterest.get_my_feed('wallpapers_phone', 8, str(update.effective_user.id))
            
            sent = 0
            for url in images[:4]:
                try:
                    await query.message.reply_photo(photo=url, caption="📱 Твоя рекомендация")
                    sent += 1
                    await asyncio.sleep(0.5)
                except:
                    pass
            
            await query.message.reply_text(
                f"✅ Найдено: {len(images)}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Еще", callback_data='menu_phone'),
                    InlineKeyboardButton("🔙 Назад", callback_data='back')
                ]])
            )
            return
        
        # ===== ФАЙЛЫ =====
        if query.data == 'menu_files':
            await query.edit_message_text(
                "📁 Отправь файл",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад", callback_data='back')
                ]])
            )
            context.user_data['state'] = 'waiting_file'
            return
        
        # ===== СКРИНШОТЫ =====
        if query.data == 'menu_screens':
            await query.edit_message_text(
                "📸 Отправь скриншот",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад", callback_data='back')
                ]])
            )
            context.user_data['state'] = 'waiting_screenshot'
            return
        
        # ===== ВИДЕО =====
        if query.data == 'menu_videos':
            await query.edit_message_text(
                "🎥 Отправь видео",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад", callback_data='back')
                ]])
            )
            context.user_data['state'] = 'waiting_video'
            return
        
        # ===== ЗАМЕТКИ =====
        if query.data == 'menu_notes':
            await query.edit_message_text(
                "📝 Напиши заметку\nПервая строка - заголовок",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад", callback_data='back')
                ]])
            )
            context.user_data['state'] = 'waiting_note'
            return
        
        # ===== НАСТРОЙКИ ИГР =====
        if query.data == 'menu_games':
            keyboard = []
            for game in GAMES:
                keyboard.append([InlineKeyboardButton(game, callback_data=f'game_{game}')])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back')])
            await query.edit_message_text("Выбери игру:", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        if query.data.startswith('game_'):
            game = query.data.replace('game_', '')
            settings = self.data_manager.get_items('game_settings', game)
            
            text = f"⚙️ {game}\n\n"
            if settings:
                for i, s in enumerate(settings, 1):
                    text += f"{i}. {s['name']}: {s['value']}\n"
            else:
                text += "Нет настроек"
            
            keyboard = [
                [InlineKeyboardButton("➕ Добавить", callback_data=f'add_{game}')],
                [InlineKeyboardButton("🗑️ Удалить", callback_data=f'del_{game}')],
                [InlineKeyboardButton("🔙 Назад", callback_data='menu_games')]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data['current_game'] = game
            return
        
        if query.data.startswith('add_'):
            game = query.data.replace('add_', '')
            await query.edit_message_text("Формат: Название: значение")
            context.user_data['state'] = 'waiting_setting'
            return
        
        if query.data.startswith('del_'):
            game = query.data.replace('del_', '')
            settings = self.data_manager.get_items('game_settings', game)
            
            if not settings:
                await query.edit_message_text("❌ Нет настроек")
                return
            
            keyboard = []
            for i, s in enumerate(settings):
                keyboard.append([InlineKeyboardButton(
                    f"❌ {s['name']}", callback_data=f'delete_{game}_{i}'
                )])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')])
            
            await query.edit_message_text("Что удалить?", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        if query.data.startswith('delete_'):
            parts = query.data.split('_')
            game = parts[1]
            idx = int(parts[2])
            
            if self.data_manager.delete_item('game_settings', idx, game):
                await query.edit_message_text("✅ Удалено")
            else:
                await query.edit_message_text("❌ Ошибка")
            
            # Возврат в меню игры
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')]]
            await query.message.reply_text("Вернуться", reply_markup=InlineKeyboardMarkup(keyboard))
            return
    
    async def document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = context.user_data.get('state')
        doc = update.message.document
        
        # Загрузка кук
        if state == 'waiting_cookies' and doc.file_name.endswith('.json'):
            await update.message.reply_text("🔄 Загружаю куки...")
            try:
                file = await context.bot.get_file(doc.file_id)
                path = f"temp_{doc.file_name}"
                await file.download_to_drive(path)
                
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                cookies = {}
                for item in data:
                    if 'name' in item and 'value' in item:
                        cookies[item['name']] = item['value']
                
                if self.pinterest.save_cookies(cookies):
                    await update.message.reply_text("✅ Куки загружены! Теперь будут ТВОИ рекомендации")
                else:
                    await update.message.reply_text("❌ Ошибка")
                
                os.remove(path)
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {e}")
            
            context.user_data['state'] = None
            await self.start(update, context)
            return
        
        # Файлы
        if state == 'waiting_file':
            info = {
                'name': doc.file_name,
                'file_id': doc.file_id,
                'date': datetime.now().isoformat()
            }
            self.data_manager.add_item('files', info)
            await update.message.reply_text("✅ Файл сохранен")
            context.user_data['state'] = None
            await self.start(update, context)
            return
        
        # Видео
        if state == 'waiting_video':
            info = {
                'name': doc.file_name,
                'file_id': doc.file_id,
                'date': datetime.now().isoformat()
            }
            self.data_manager.add_item('videos', info)
            await update.message.reply_text("✅ Видео сохранено")
            context.user_data['state'] = None
            await self.start(update, context)
            return
        
        await update.message.reply_text("Сначала выбери действие в меню")
    
    async def photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = context.user_data.get('state')
        
        if state == 'waiting_screenshot':
            photo = update.message.photo[-1]
            info = {
                'file_id': photo.file_id,
                'caption': update.message.caption or '',
                'date': datetime.now().isoformat()
            }
            self.data_manager.add_item('screenshots', info)
            await update.message.reply_text("✅ Скриншот сохранен")
            context.user_data['state'] = None
            await self.start(update, context)
        else:
            await update.message.reply_text("Сначала выбери 'Скриншоты'")
    
    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        state = context.user_data.get('state')
        text = update.message.text
        
        if state == 'waiting_note':
            lines = text.split('\n', 1)
            title = lines[0][:50]
            content = lines[1] if len(lines) > 1 else ''
            
            note = {'title': title, 'content': content, 'date': datetime.now().isoformat()}
            self.data_manager.add_item('notes', note)
            await update.message.reply_text(f"✅ Заметка '{title}' сохранена")
            context.user_data['state'] = None
            await self.start(update, context)
            return
        
        if state == 'waiting_setting':
            game = context.user_data.get('current_game')
            if ':' in text:
                name, val = text.split(':', 1)
                setting = {'name': name.strip(), 'value': val.strip(), 'date': datetime.now().isoformat()}
                self.data_manager.add_item('game_settings', setting, game)
                await update.message.reply_text("✅ Добавлено")
            else:
                await update.message.reply_text("❌ Формат: Название: значение")
            
            context.user_data['state'] = None
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')]]
            await update.message.reply_text("Вернуться", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        await update.message.reply_text("Используй /start")
    
    async def error(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Ошибка: {context.error}")
    
    def run(self):
        print("✅ Бот запущен")
        print("📱 Отправь /start в Telegram")
        print("🍪 Загрузи куки через меню")
        self.application.run_polling()


def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        print("❌ Нет токена")
        return
    TelegramBot(token).run()


if __name__ == '__main__':
    main()
