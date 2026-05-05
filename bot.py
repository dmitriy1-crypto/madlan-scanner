import os
import json
import logging
import time
import requests
from datetime import datetime, timezone, timedelta

# ---------- НАСТРОЙКИ ----------
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '').strip()
CHAT_ID = os.environ.get('CHAT_ID', '').strip()

# ---------- ФИЛЬТРЫ ПОИСКА ----------
AREA = '5'           # Хайфа
MAX_PRICE = 1_500_000
MIN_ROOMS = 3
MAX_ROOMS = 5

# Часы активности по израильскому времени (UTC+3)
START_HOUR = 7
END_HOUR = 22

SENT_IDS_FILE = 'sent_ids.json'
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

def is_active_hours():
    """Проверяет, что сейчас время от START_HOUR до END_HOUR по израильскому времени."""
    israel_tz = timezone(timedelta(hours=3))
    now = datetime.now(israel_tz)
    return START_HOUR <= now.hour < END_HOUR

def tg_send_message(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.info('Telegram не настроен – пропускаю отправку')
        return
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {
        'chat_id': CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True
    }
    try:
        requests.post(url, json=payload, timeout=10)
        logger.info('Сообщение отправлено в Telegram')
    except Exception as e:
        logger.error('Ошибка отправки в Telegram: %s', e)

def fetch_madlan_listings():
    url = 'https://www.madlan.co.il/for-sale/%D7%97%D7%99%D7%A4%D7%94-%D7%99%D7%A9%D7%A8%D7%90%D7%9C'
    params = {
        'area': AREA,
        'priceTo': MAX_PRICE,
        'roomsFrom': MIN_ROOMS,
        'roomsTo': MAX_ROOMS,
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Referer': 'https://www.madlan.co.il/',
    }
    logger.info('Отправляю GET-запрос к %s', url)
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()

        start_marker = 'window.__SSR_HYDRATED_CONTEXT__='
        start = resp.text.find(start_marker)
        if start == -1:
            logger.error('Не найден JSON')
            return []
        start += len(start_marker)
        end = resp.text.find('</script>', start)
        if end == -1:
            logger.error('Не найден конец JSON')
            return []
        json_str = resp.text[start:end].strip()

        json_str = json_str.replace(':undefined', ':null')
        json_str = json_str.replace(': undefined', ': null')

        data = json.loads(json_str)

        redux = data.get('reduxInitialState', {})
        domain = redux.get('domainData', {})
        search_list = domain.get('searchList', {})
        search_data = search_list.get('data', {})
        poi_data = search_data.get('searchPoiV2', {})
        items = poi_data.get('poi', [])

        listings = [it for it in items if it.get('type') == 'bulletin']
        logger.info('Получено %d частных объявлений', len(listings))
        return listings
    except Exception as e:
        logger.error('Ошибка при запросе к Madlan: %s', e)
        return []

def load_sent_ids():
    try:
        with open(SENT_IDS_FILE, 'r') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_sent_ids(ids_set):
    with open(SENT_IDS_FILE, 'w') as f:
        json.dump(list(ids_set), f)

def format_price(price):
    """Форматирует цену с разделителями разрядов: 1 500 000"""
    if isinstance(price, (int, float)):
        return f'{price:,.0f}'.replace(',', ' ')
    return str(price)

def build_message(item):
    listing_id = item.get('id', '')
    address = item.get('address', 'Адрес не указан')
    full_url = f'https://www.madlan.co.il/listings/{listing_id}'
    url_html = f'<a href="{full_url}">Посмотреть</a>'
    price = format_price(item.get('price', 0))
    rooms = item.get('beds', '—')
    area = item.get('area', '—')
    floor = item.get('floor', '—')

    msg = f'{address}\n'
    msg += f'Цена: {price} ₪\n'
    msg += f'Комнат: {rooms} | Площадь: {area} м² | Этаж: {floor}\n'
    msg += url_html
    return msg, listing_id

def main():
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.error('TELEGRAM_TOKEN или CHAT_ID не заданы. Установите переменные окружения!')
        return

    # Проверка времени активности
   # if not is_active_hours():
    #    logger.info('Сейчас неактивное время (по Израилю), завершаю работу')
     #   return

    # Тестовое сообщение только один раз при старте (можно убрать после отладки)
    # tg_send_message('Запуск агента Madlan. Начинаю поиск...')

    sent_ids = load_sent_ids()
    items = fetch_madlan_listings()

    # Фильтрация
    filtered = []
    for item in items:
        price = item.get('price')
        beds = item.get('beds')
        if price is None or price == 0:
            continue
        if price > MAX_PRICE:
            logger.info('Пропущено (цена %s > %s): %s', price, MAX_PRICE, item.get('address'))
            continue
        if beds is None:
            continue
        if beds < MIN_ROOMS or beds > MAX_ROOMS:
            logger.info('Пропущено (комнаты %s вне %s-%s): %s', beds, MIN_ROOMS, MAX_ROOMS, item.get('address'))
            continue
        filtered.append(item)

    logger.info('После фильтрации осталось %d объявлений', len(filtered))

    new_found = 0
    for item in filtered:
        msg, lid = build_message(item)
        if not lid or lid in sent_ids:
            continue
        tg_send_message(msg)
        sent_ids.add(lid)
        new_found += 1
        time.sleep(1.2)

    if new_found > 0:
        save_sent_ids(sent_ids)
        logger.info('Отправлено %d новых объявлений', new_found)
    else:
        logger.info('Новых объявлений нет')

if __name__ == '__main__':
    main()
