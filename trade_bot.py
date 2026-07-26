#!/usr/bin/env python3
"""
Telegram бот для автоматического принятия выгодных обменов на mangabuff.ru
ОПТИМИЗИРОВАННАЯ ВЕРСИЯ с рабочим логином
"""

import os
import sys
import json
import re
import time
import threading
import html
from pathlib import Path
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ Установите beautifulsoup4: pip install beautifulsoup4")
    sys.exit(1)

try:
    from curl_cffi.requests import Session as CffiSession
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False
    print("[WARN] curl_cffi не установлен, используется requests. Возможны проблемы с Cloudflare.")

try:
    import telebot
    from telebot import types
except ImportError:
    print("❌ Установите pyTelegramBotAPI: pip install pyTelegramBotAPI")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("❌ Установите python-dotenv: pip install python-dotenv")
    sys.exit(1)

# ==================== КЛАСС АВТОРИЗАЦИИ (РАБОЧИЙ ЛОГИН) ====================
class MangaBuffAuth:
    BASE_URL = "https://mangabuff.ru"

    def __init__(self, proxy: dict = None, impersonate: str = "chrome131"):
        self.impersonate = impersonate
        self._setup_session(proxy)

    def _setup_session(self, proxy):
        if USE_CURL_CFFI:
            self.session = CffiSession(impersonate=self.impersonate)
        else:
            self.session = requests.Session()
        if proxy:
            self.session.proxies.update(proxy)

        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.109 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Sec-Ch-Ua': '"Google Chrome";v="131", "Not_A Brand";v="8"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
        })

    def _get_csrf_from_cookies(self) -> str:
        """Получение CSRF токена из кук - как в оригинале"""
        xsrf = self.session.cookies.get('XSRF-TOKEN')
        if xsrf:
            return unquote(xsrf)
        for cookie in self.session.cookies:
            name = cookie.name if hasattr(cookie, 'name') else cookie
            if name.upper() == 'XSRF-TOKEN':
                value = cookie.value if hasattr(cookie, 'value') else self.session.cookies[name]
                return unquote(value)
        return ''

    def login(self, email: str, password: str):
        """ТОЧНО КАК В ОРИГИНАЛЕ - рабочий метод"""
        # 1. Получаем страницу логина
        resp = self.session.get(f'{self.BASE_URL}/login')
        if resp.status_code != 200:
            return False, f'GET login failed: HTTP {resp.status_code}'

        # 2. Получаем CSRF токен
        csrf = self._get_csrf_from_cookies()
        if not csrf:
            return False, 'CSRF token not found'

        # Небольшая задержка как в оригинале
        time.sleep(1)

        # 3. Отправляем логин
        login_data = {'email': email, 'password': password, 'remember': 'on'}
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-XSRF-TOKEN': csrf,  # КЛЮЧЕВОЙ ЗАГОЛОВОК
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': f'{self.BASE_URL}/login',
            'Origin': self.BASE_URL,
        }
        resp = self.session.post(
            f'{self.BASE_URL}/login', 
            data=login_data, 
            headers=headers, 
            allow_redirects=False  # Как в оригинале
        )

        # 4. Проверяем авторизацию
        check = self.session.get(f'{self.BASE_URL}/')
        if check.status_code != 200:
            return False, 'Auth check failed'

        # 5. Ищем user_id
        html_text = check.text
        match = re.search(r'data-userid="(\d+)"', html_text)
        if not match:
            match = re.search(r'/users/(\d+)', html_text)
        
        if match:
            user_id = match.group(1)
            cookies = []
            for name, value in self.session.cookies.items():
                cookies.append({'name': name, 'value': value, 'domain': 'mangabuff.ru'})
            return True, {'user_id': user_id, 'cookies': cookies}
        else:
            return False, 'User ID not found after login'

    def load_cookies(self, cookies_list: list):
        """Загрузка сохранённых кук"""
        for c in cookies_list:
            name = c.get('name')
            value = c.get('value')
            domain = c.get('domain', 'mangabuff.ru')
            if name and value:
                self.session.cookies.set(name, value, domain=domain)

    def is_authenticated(self) -> bool:
        """Проверка авторизации"""
        try:
            resp = self.session.get(f'{self.BASE_URL}/')
            if resp.status_code != 200:
                return False
            html_text = resp.text
            if re.search(r'data-userid="\d+"', html_text):
                return True
            if 'header__user' in html_text or '/logout' in html_text:
                return True
            return False
        except:
            return False

    def get_user_id(self) -> str:
        """Получение ID пользователя"""
        resp = self.session.get(f'{self.BASE_URL}/')
        if resp.status_code != 200:
            return None
        match = re.search(r'data-userid="(\d+)"', resp.text)
        if not match:
            match = re.search(r'/users/(\d+)', resp.text)
        return match.group(1) if match else None

# ==================== ФУНКЦИИ ПАРСИНГА ОБМЕНОВ (ОПТИМИЗИРОВАННЫЕ) ====================
def get_trades_fast(auth: MangaBuffAuth):
    """Быстрое получение списка обменов"""
    url = f"{auth.BASE_URL}/trades"
    try:
        response = auth.session.get(url, timeout=10)
        if response.status_code != 200:
            return []
        
        soup = BeautifulSoup(response.text, 'lxml' if 'lxml' in sys.modules else 'html.parser')
        trades = []
        
        trade_items = soup.find_all('a', class_=lambda c: c and 'trade__list-item' in c.split())
        
        for item in trade_items:
            href = item.get('href')
            if not href or '/trades/' not in href:
                continue
            trade_id = href.split('/')[-1]
            trade_url = f"{auth.BASE_URL}{href}"
            
            # Быстрая проверка на новый обмен
            info_div = item.find('div', class_='trade__list-info')
            if info_div:
                header_div = info_div.find('div', class_='trade__list-header')
                is_new = bool(header_div and header_div.find('span', class_='trade__list-dot--new'))
            else:
                is_new = False
            
            trades.append({
                'trade_id': trade_id,
                'is_new': is_new,
                'url': trade_url
            })
        
        return trades
    except Exception as e:
        print(f"[GET_TRADES] Ошибка: {e}")
        return []

def get_trade_details_fast(auth: MangaBuffAuth, trade_id: str):
    """Быстрое получение деталей обмена"""
    url = f"{auth.BASE_URL}/trades/{trade_id}"
    try:
        response = auth.session.get(url, timeout=10)
        if response.status_code != 200:
            return None
        
        soup = BeautifulSoup(response.text, 'lxml' if 'lxml' in sys.modules else 'html.parser')
        
        sender_elem = soup.find('a', class_='trade__header-name')
        if not sender_elem:
            return None
        sender_name = sender_elem.text.strip()
        sender_id = sender_elem.get('href', '').split('/')[-1]
        
        offered_cards = []
        creator_div = soup.find('div', class_='trade__main-items trade__main-items--creator')
        if creator_div:
            for link in creator_div.find_all('a', class_='trade__main-item'):
                card_url = f"{auth.BASE_URL}{link.get('href')}"
                card_id = card_url.split('/')[-2] if '/cards/' in card_url else ''
                offered_cards.append({'card_id': card_id, 'url': card_url})

        required_cards = []
        receiver_div = soup.find('div', class_='trade__main-items trade__main-items--receiver')
        if receiver_div:
            for link in receiver_div.find_all('a', class_='trade__main-item'):
                card_url = f"{auth.BASE_URL}{link.get('href')}"
                card_id = card_url.split('/')[-2] if '/cards/' in card_url else ''
                required_cards.append({'card_id': card_id, 'url': card_url})

        return {
            'trade_id': trade_id,
            'sender_id': sender_id,
            'sender_name': sender_name,
            'offered_cards': offered_cards,
            'required_cards': required_cards,
            'url': f"{auth.BASE_URL}/trades/{trade_id}"
        }
    except Exception as e:
        print(f"[GET_DETAILS] Ошибка для {trade_id}: {e}")
        return None

def accept_trade_fast(auth: MangaBuffAuth, trade_id: str, max_retries: int = 2):
    """Быстрое принятие обмена с минимальными задержками"""
    csrf = auth._get_csrf_from_cookies()
    if not csrf:
        return False, "CSRF token not found"
    
    headers = {
        'X-XSRF-TOKEN': csrf,
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': f"{auth.BASE_URL}/trades/{trade_id}",
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    }
    
    endpoints = [
        f"{auth.BASE_URL}/trades/accept",
        f"{auth.BASE_URL}/trades/accept/{trade_id}",
        f"{auth.BASE_URL}/trades/{trade_id}/accept",
    ]
    
    for attempt in range(max_retries):
        for endpoint in endpoints:
            try:
                resp = auth.session.post(
                    endpoint, 
                    headers=headers, 
                    data={'trade_id': trade_id}, 
                    timeout=10
                )
                if resp.status_code < 400:
                    try:
                        data = resp.json()
                        if data.get('error'):
                            continue
                    except:
                        pass
                    return True, "Обмен успешно принят!"
            except Exception as e:
                continue
        
        if attempt < max_retries - 1:
            time.sleep(0.5)
    
    return False, "Не удалось принять обмен"

# ==================== НАСТРОЙКИ БОТА ====================
BOT_TOKEN = os.getenv("TRADE_BOT_TOKEN") or os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ Не найден TRADE_BOT_TOKEN или BOT_TOKEN в .env файле")
    sys.exit(1)

CHECK_INTERVAL = 10  # Оптимизированный интервал
SESSIONS_FILE = Path(__file__).parent / "tg_sessions.json"
PROCESSED_TRADES_FILE = Path(__file__).parent / "processed_trades.json"

sessions = {}
processed_trades = set()
monitoring_active = False
monitoring_thread = None
executor = ThreadPoolExecutor(max_workers=5)

def load_sessions():
    global sessions
    if SESSIONS_FILE.exists():
        try:
            sessions = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        except:
            sessions = {}

def save_sessions():
    SESSIONS_FILE.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")

def load_processed_trades():
    global processed_trades
    if PROCESSED_TRADES_FILE.exists():
        try:
            data = json.loads(PROCESSED_TRADES_FILE.read_text(encoding="utf-8"))
            processed_trades = set(data.get("trades", []))
        except:
            processed_trades = set()

def save_processed_trades():
    data = {"trades": list(processed_trades)}
    PROCESSED_TRADES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

load_sessions()
load_processed_trades()

bot = telebot.TeleBot(BOT_TOKEN)

def get_auth_for_user(chat_id: int) -> MangaBuffAuth:
    auth = MangaBuffAuth()
    if str(chat_id) in sessions:
        cookies = sessions[str(chat_id)].get('cookies', [])
        if cookies:
            auth.load_cookies(cookies)
    return auth

def save_user_session(chat_id: int, user_id: str, cookies: list):
    sessions[str(chat_id)] = {'user_id': user_id, 'cookies': cookies}
    save_sessions()

def clear_user_session(chat_id: int):
    if str(chat_id) in sessions:
        del sessions[str(chat_id)]
        save_sessions()

def get_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(
        types.KeyboardButton("🔁 Мониторинг обменов"),
        types.KeyboardButton("📊 Статус"),
    )
    return markup

# ==================== МОНИТОРИНГ (ОПТИМИЗИРОВАННЫЙ) ====================
def process_trade(chat_id, auth, trade):
    """Обработка одного обмена - выполняется в отдельном потоке"""
    try:
        if trade['trade_id'] in processed_trades:
            return None
        
        processed_trades.add(trade['trade_id'])
        save_processed_trades()
        
        details = get_trade_details_fast(auth, trade['trade_id'])
        if not details:
            return None
        
        offered_count = len(details['offered_cards'])
        required_count = len(details['required_cards'])
        
        accept = (required_count == 1 and offered_count >= 2)
        
        if accept:
            success, msg = accept_trade_fast(auth, trade['trade_id'], max_retries=2)
            if success:
                result_msg = "✅ **Обмен автоматически ПРИНЯТ!** 🚀"
            else:
                result_msg = f"❌ **Не удалось принять**: {msg}"
        else:
            if required_count != 1:
                reason = f"вы отдаёте {required_count} карт (нужно ровно 1)"
            elif offered_count < 2:
                reason = f"вам предлагают только {offered_count} карт (нужно 2 и более)"
            else:
                reason = "неподходящие условия"
            result_msg = f"⏩ **Пропущен** ({offered_count}:{required_count}) – {reason}"
        
        message = f"🔄 **Новое предложение обмена**\n\n"
        message += f"👤 *Отправитель:* {html.escape(details['sender_name'])}\n"
        message += f"🔗 [Ссылка]({details['url']})\n\n"
        message += f"📦 *Предлагают:* {offered_count} карт\n"
        for card in details['offered_cards'][:5]:
            message += f"  • [Карта]({card['url']})\n"
        if offered_count > 5:
            message += f"  • ... и ещё {offered_count - 5} карт\n"
        message += f"\n📤 *Вы отдаёте:* {required_count} карт\n"
        for card in details['required_cards'][:5]:
            message += f"  • [Карта]({card['url']})\n"
        if required_count > 5:
            message += f"  • ... и ещё {required_count - 5} карт\n"
        message += f"\n{result_msg}"
        
        try:
            bot.send_message(chat_id, message, parse_mode='Markdown', disable_web_page_preview=True)
        except Exception as e:
            print(f"Ошибка отправки: {e}")
            
        return details
        
    except Exception as e:
        print(f"[TRADE-PROCESS] Ошибка: {e}")
        return None

def monitoring_loop(chat_id):
    global monitoring_active
    print(f"[MONITOR] Запуск для чата {chat_id}")
    auth = get_auth_for_user(chat_id)
    
    if not auth.is_authenticated():
        bot.send_message(chat_id, "❌ Вы не авторизованы. Используйте /login")
        monitoring_active = False
        return

    bot.send_message(chat_id, f"🚀 Мониторинг запущен! Проверка каждые {CHECK_INTERVAL} сек.\nПринимаются обмены 1→2+ (вы отдаёте 1, получаете 2 и более)")

    while monitoring_active:
        try:
            start_time = time.time()
            trades = get_trades_fast(auth)
            
            if trades:
                new_trades = [t for t in trades if t['trade_id'] not in processed_trades]
                
                if new_trades:
                    print(f"[MONITOR] Найдено {len(new_trades)} новых обменов")
                    futures = []
                    for trade in new_trades:
                        future = executor.submit(process_trade, chat_id, auth, trade)
                        futures.append(future)
                    
                    for future in as_completed(futures):
                        try:
                            future.result(timeout=5)
                        except Exception as e:
                            print(f"[MONITOR] Ошибка в потоке: {e}")
            
            elapsed = time.time() - start_time
            sleep_time = max(1, CHECK_INTERVAL - elapsed)
            
            for _ in range(int(sleep_time)):
                if not monitoring_active:
                    break
                time.sleep(1)
                
        except Exception as e:
            print(f"[MONITOR] Ошибка: {e}")
            time.sleep(2)

    bot.send_message(chat_id, "🔕 Мониторинг остановлен.")

# ==================== КОМАНДЫ БОТА ====================
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "🤖 Бот для автоматического обмена картами на mangabuff.ru\n\n"
        "⚡ **ОПТИМИЗИРОВАННАЯ ВЕРСИЯ**\n\n"
        "Команды:\n"
        "/login email password – войти в аккаунт\n"
        "/logout – выйти\n"
        "/status – проверить авторизацию\n"
        "/monitor_start – запустить мониторинг (автопринятие 1→2+)\n"
        "/monitor_stop – остановить мониторинг\n\n"
        "Используйте кнопки для управления.",
        reply_markup=get_keyboard()
    )

@bot.message_handler(commands=['login'])
def cmd_login(message):
    chat_id = message.chat.id
    args = message.text.split()
    if len(args) < 3:
        bot.send_message(chat_id, "❌ Использование: /login email password")
        return
    email = args[1]
    password = args[2]

    bot.send_message(chat_id, "⏳ Выполняю вход...")
    auth = MangaBuffAuth()
    success, result = auth.login(email, password)

    if success:
        user_id = result['user_id']
        save_user_session(chat_id, user_id, result['cookies'])
        bot.send_message(chat_id, f"✅ Успешный вход!\nВаш user_id: {user_id}\nСессия сохранена.")
    else:
        bot.send_message(chat_id, f"❌ Ошибка входа: {result}")

@bot.message_handler(commands=['logout'])
def cmd_logout(message):
    chat_id = message.chat.id
    clear_user_session(chat_id)
    bot.send_message(chat_id, "👋 Вы вышли. Сессия очищена.")

@bot.message_handler(commands=['status'])
def cmd_status(message):
    chat_id = message.chat.id
    auth = get_auth_for_user(chat_id)
    if auth.is_authenticated():
        user_id = auth.get_user_id()
        bot.send_message(chat_id, f"🟢 Вы авторизованы\nUser ID: {user_id}")
    else:
        bot.send_message(chat_id, "🔴 Вы не авторизованы. Используйте /login")

@bot.message_handler(commands=['monitor_start'])
def cmd_monitor_start(message):
    global monitoring_active, monitoring_thread
    chat_id = message.chat.id
    if monitoring_active:
        bot.send_message(chat_id, "⚠️ Мониторинг уже запущен.")
        return
    auth = get_auth_for_user(chat_id)
    if not auth.is_authenticated():
        bot.send_message(chat_id, "❌ Вы не авторизованы. Используйте /login")
        return
    monitoring_active = True
    monitoring_thread = threading.Thread(target=monitoring_loop, args=(chat_id,), daemon=True)
    monitoring_thread.start()
    bot.send_message(chat_id, "✅ Мониторинг обменов запущен.")

@bot.message_handler(commands=['monitor_stop'])
def cmd_monitor_stop(message):
    global monitoring_active
    if not monitoring_active:
        bot.send_message(message.chat.id, "ℹ️ Мониторинг не запущен.")
        return
    monitoring_active = False
    bot.send_message(message.chat.id, "⏹ Мониторинг остановлен.")

@bot.message_handler(func=lambda m: m.text in ["🔁 Мониторинг обменов", "📊 Статус"])
def handle_buttons(message):
    text = message.text
    chat_id = message.chat.id
    if text == "🔁 Мониторинг обменов":
        if monitoring_active:
            bot.send_message(chat_id, "⚠️ Мониторинг уже запущен. Используйте /monitor_stop для остановки.")
        else:
            cmd_monitor_start(message)
    elif text == "📊 Статус":
        cmd_status(message)

def run_bot():
    while True:
        try:
            print("✅ Торговый бот запущен. Нажмите Ctrl+C для остановки.")
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception as e:
            print(f"❌ Ошибка соединения: {e}. Переподключение через 5 секунд...")
            time.sleep(5)

if __name__ == '__main__':
    run_bot()
