import os
import logging
import json
import asyncio
import aiohttp
import random
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple

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
GAMES = ['CS2', 'Standoff 2', 'Valorant']


class PinterestRSS:
    """Простой класс для работы с Pinterest RSS"""
    
    async def search_images(self, query: str, category: str, count: int = 10) -> List[str]:
        """Поиск изображений через RSS"""
        images = []
        
        # Формируем URL для поиска
        url = f"https://www.pinterest.com/search/pins/rss/?q={query.replace(' ', '+')}"
        logger.info(f"Поиск: {url}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        root = ET.fromstring(text)
                        
                        for item in root.findall('.//item'):
                            if len(images) >= count:
                                break
                            
                            # Получаем описание с картинкой
                            description = item.find('description')
                            if description is not None and description.text:
                                # Ищем URL изображения
                                img_match = re.search(r'<img src="([^"]+)"', description.text)
                                if img_match:
                                    img_url = img_match.group(1)
                                    # Увеличиваем размер
                                    high_res = img_url.replace('236x', '736x')
                                    images.append(high_res)
        except Exception as e:
            logger.error(f"Ошибка RSS: {e}")
        
        # Если ничего не нашли, возвращаем заглушки
        if not images:
            images = self.get_fallback_images(category, count)
        
        return images[:count]
    
    def get_fallback_images(self, category: str, count: int) -> List[str]:
        """Гарантированные изображения правильного формата"""
        images = []
        
        if category == "avatars":
            # DiceBear API - всегда квадратные аватарки
            styles = ['avataaars', 'bottts', 'identicon', 'micah', 'pixel-art']
            for i in range(count):
                style = random.choice(styles)
                images.append(f"https://api.dicebear.com/7.x/{style}/svg?seed={random.randint(1, 10000)}")
        
        elif category == "wallpapers_pc":
            # Picsum - всегда 16:9
            for i in range(count):
                images.append(f"https://picsum.photos/1920/1080?random={random.randint(1, 10000)}")
        
        elif category == "wallpapers_phone":
            # Picsum - всегда 9:16
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
        self.pinterest = PinterestRSS()
        
        self.application = Application.builder().token(token).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Настройка обработчиков"""
        
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        
        self.application.add_error_handler(self.error_handler)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        await self.show_main_menu(update, context)
    
    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показ главного меню"""
        
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
        message = "📋 **Главное меню**\n\nВыберите категорию:"
        
        if update.callback_query:
            await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка callback-запросов"""
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback: {query.data}")
        
        # ========== ВОЗВРАТ В МЕНЮ ==========
        if query.data == 'back_to_main':
            await self.show_main_menu(update, context)
            return
        
        # ========== ФАЙЛЫ ==========
        if query.data == 'menu_files':
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
            return
        
        elif query.data == 'add_file':
            await query.edit_message_text("📁 **Отправьте файл**")
            context.user_data['state'] = 'waiting_file'
            return
        
        elif query.data == 'list_files':
            files = self.data_manager.get_items('files')
            if files:
                text = "📁 **Список файлов:**\n\n"
                for i, file in enumerate(files[-10:], 1):  # Последние 10
                    name = file.get('name', 'Без имени')
                    text += f"{i}. {name}\n"
            else:
                text = "📁 **Нет файлов**"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='menu_files')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            return
        
        # ========== ВИДЕО ==========
        elif query.data == 'menu_videos':
            keyboard = [
                [InlineKeyboardButton("🎥 Добавить видео", callback_data='add_video')],
                [InlineKeyboardButton("📋 Список видео", callback_data='list_videos')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "🎥 **Видео**\n\nВыберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return
        
        elif query.data == 'add_video':
            await query.edit_message_text("🎥 **Отправьте видео**")
            context.user_data['state'] = 'waiting_video'
            return
        
        elif query.data == 'list_videos':
            videos = self.data_manager.get_items('videos')
            if videos:
                text = "🎥 **Список видео:**\n\n"
                for i, video in enumerate(videos[-10:], 1):
                    name = video.get('name', 'Без имени')
                    text += f"{i}. {name}\n"
            else:
                text = "🎥 **Нет видео**"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='menu_videos')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            return
        
        # ========== СКРИНШОТЫ ==========
        elif query.data == 'menu_screenshots':
            keyboard = [
                [InlineKeyboardButton("📸 Добавить скриншот", callback_data='add_screenshot')],
                [InlineKeyboardButton("📋 Список скриншотов", callback_data='list_screenshots')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "📸 **Скриншоты**\n\nВыберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return
        
        elif query.data == 'add_screenshot':
            await query.edit_message_text("📸 **Отправьте скриншот**")
            context.user_data['state'] = 'waiting_screenshot'
            return
        
        elif query.data == 'list_screenshots':
            screenshots = self.data_manager.get_items('screenshots')
            if screenshots:
                text = "📸 **Список скриншотов:**\n\n"
                for i, ss in enumerate(screenshots[-10:], 1):
                    caption = ss.get('caption', 'Без подписи')
                    text += f"{i}. {caption}\n"
            else:
                text = "📸 **Нет скриншотов**"
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data='menu_screenshots')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            return
        
        # ========== ЗАМЕТКИ ==========
        elif query.data == 'menu_notes':
            notes = self.data_manager.get_items('notes')
            if notes:
                text = "📝 **Заметки**\n\n"
                for i, note in enumerate(notes[-10:], 1):
                    title = note.get('title', 'Без названия')
                    text += f"{i}. {title}\n"
            else:
                text = "📝 **Нет заметок**"
            
            keyboard = [
                [InlineKeyboardButton("➕ Добавить заметку", callback_data='add_note')],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            return
        
        elif query.data == 'add_note':
            await query.edit_message_text(
                "📝 **Напишите заметку**\n\n"
                "Первая строка - заголовок"
            )
            context.user_data['state'] = 'waiting_note'
            return
        
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
            return
        
        elif query.data.startswith('game_'):
            game = query.data.replace('game_', '')
            context.user_data['current_game'] = game
            
            settings = self.data_manager.get_items('game_settings', game)
            
            keyboard = [
                [InlineKeyboardButton("➕ Добавить", callback_data='add_game_setting')],
                [InlineKeyboardButton("🗑️ Удалить", callback_data='delete_game_setting')],
                [InlineKeyboardButton("🔙 Назад", callback_data='menu_game_settings')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if settings:
                text = "⚙️ **Настройки**\n\n"
                for i, s in enumerate(settings, 1):
                    text += f"{i}. {s['name']}: {s['value']}\n"
            else:
                text = "⚙️ **Нет настроек**"
            
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            return
        
        elif query.data == 'add_game_setting':
            await query.edit_message_text(
                "⚙️ Формат: Название: значение\n"
                "Пример: Чувствительность: 2.5"
            )
            context.user_data['state'] = 'waiting_game_setting'
            return
        
        elif query.data == 'delete_game_setting':
            game = context.user_data.get('current_game')
            settings = self.data_manager.get_items('game_settings', game)
            
            if not settings:
                await query.edit_message_text("❌ Нет настроек")
                return
            
            keyboard = []
            for i, setting in enumerate(settings):
                keyboard.append([InlineKeyboardButton(
                    f"❌ {setting['name']}",
                    callback_data=f'delete_{i}'
                )])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Выберите для удаления:", reply_markup=reply_markup)
            return
        
        elif query.data.startswith('delete_'):
            index = int(query.data.replace('delete_', ''))
            game = context.user_data.get('current_game')
            
            if self.data_manager.delete_item('game_settings', index, game):
                await query.edit_message_text("✅ Удалено")
            else:
                await query.edit_message_text("❌ Ошибка")
            
            # Возврат в меню игры
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text("Вернуться:", reply_markup=reply_markup)
            return
        
        # ========== PINTEREST КАТЕГОРИИ ==========
        elif query.data in ['menu_avatars', 'menu_wallpapers_pc', 'menu_wallpapers_phone']:
            category_map = {
                'menu_avatars': ('avatars', 'аватарок', 'аниме аватарка'),
                'menu_wallpapers_pc': ('wallpapers_pc', 'обоев для ПК', 'аниме обои пк'),
                'menu_wallpapers_phone': ('wallpapers_phone', 'обоев для телефона', 'аниме обои вертикальные')
            }
            
            category, ru_name, search_query = category_map[query.data]
            await self.fetch_images(update, context, category, ru_name, search_query)
            return
    
    async def fetch_images(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                          category: str, ru_name: str, search_query: str):
        """Получение и отправка изображений"""
        query = update.callback_query
        
        await query.edit_message_text(f"🔄 Ищу {ru_name}...")
        
        # Пробуем найти через Pinterest
        images = await self.pinterest.search_images(search_query, category, count=10)
        
        # Если ничего нет - заглушки
        if not images:
            images = self.pinterest.get_fallback_images(category, 6)
            source = "заглушки"
        else:
            source = "Pinterest"
        
        # Отправляем
        sent = 0
        for img in images[:6]:
            try:
                await query.message.reply_photo(photo=img)
                sent += 1
                await asyncio.sleep(0.5)
            except:
                pass
        
        keyboard = [[InlineKeyboardButton("🔄 Еще", callback_data=f'menu_{category}')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            f"✅ Найдено {len(images)} ({source})\nОтправлено {sent}",
            reply_markup=reply_markup
        )
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка документов"""
        state = context.user_data.get('state')
        
        if state == 'waiting_file':
            doc = update.message.document
            info = {
                'name': doc.file_name,
                'file_id': doc.file_id,
                'date': datetime.now().isoformat()
            }
            self.data_manager.add_item('files', info)
            await update.message.reply_text(f"✅ Файл сохранен")
            
            context.user_data['state'] = None
            await self.show_main_menu(update, context)
        
        elif state == 'waiting_video':
            doc = update.message.document
            info = {
                'name': doc.file_name,
                'file_id': doc.file_id,
                'date': datetime.now().isoformat()
            }
            self.data_manager.add_item('videos', info)
            await update.message.reply_text(f"✅ Видео сохранено")
            
            context.user_data['state'] = None
            await self.show_main_menu(update, context)
        
        else:
            await update.message.reply_text("Сначала выберите действие в меню")
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка фото"""
        state = context.user_data.get('state')
        
        if state == 'waiting_screenshot':
            photo = update.message.photo[-1]
            info = {
                'file_id': photo.file_id,
                'caption': update.message.caption or '',
                'date': datetime.now().isoformat()
            }
            self.data_manager.add_item('screenshots', info)
            await update.message.reply_text(f"✅ Скриншот сохранен")
            
            context.user_data['state'] = None
            await self.show_main_menu(update, context)
        
        else:
            await update.message.reply_text("Сначала выберите 'Добавить скриншот'")
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текста"""
        state = context.user_data.get('state')
        text = update.message.text
        
        if state == 'waiting_note':
            lines = text.split('\n', 1)
            title = lines[0][:50]
            content = lines[1] if len(lines) > 1 else ''
            
            note = {'title': title, 'content': content, 'date': datetime.now().isoformat()}
            self.data_manager.add_item('notes', note)
            await update.message.reply_text(f"✅ Заметка сохранена")
            
            context.user_data['state'] = None
            await self.show_main_menu(update, context)
        
        elif state == 'waiting_game_setting':
            if ':' in text:
                name, val = text.split(':', 1)
                game = context.user_data.get('current_game')
                setting = {'name': name.strip(), 'value': val.strip(), 'date': datetime.now().isoformat()}
                self.data_manager.add_item('game_settings', setting, game)
                await update.message.reply_text(f"✅ Добавлено")
            else:
                await update.message.reply_text("❌ Формат: Название: значение")
            
            context.user_data['state'] = None
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f'game_{game}')]]
            await update.message.reply_text("Вернуться", reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Ошибка: {context.error}")
        if update and update.effective_message:
            await update.effective_message.reply_text("❌ Ошибка")
    
    def run(self):
        print("✅ Бот запущен")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.application.run_polling()
        except RuntimeError:
            asyncio.run(self.application.run_polling())


def main():
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    if not TOKEN:
        print("❌ Нет токена")
        return
    bot = TelegramBot(TOKEN)
    bot.run()


if __name__ == '__main__':
    main()
