import os
import json
import time
import re
import shutil
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

# --- НАСТРОЙКИ ---
OUTPUT_DIR = "output_pinkypunk"
os.makedirs(OUTPUT_DIR, exist_ok=True)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# МЕНЯЕМ БАЗОВЫЙ URL НА ЛЕНДИНГ НАДУВАЛ
BASE_URL = "https://pinkypunk.ru/naduv" 

GITHUB_USER = "rmzparazit"
REPO_NAME = "pink"
BRANCH = "main"

# Пути к файлам
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "progress.json")
XML_FILE = os.path.join(OUTPUT_DIR, "landing_naduv_feed.xml")
TEMP_XML_FILE = XML_FILE + ".tmp"

# Настройка логирования
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.info

def get_custom_image_url(vendor_code):
    """
    Возвращает ссылку на кастомное изображение в GitHub, если оно существует.
    """
    if not vendor_code:
        return None
    
    image_path = f"images/{vendor_code}.png"
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_USER}/{REPO_NAME}/{BRANCH}/{image_path}"
    
    try:
        response = requests.head(raw_url, timeout=3)
        if response.status_code == 200:
            log(f"✅ Найдено кастомное изображение для {vendor_code}")
            return raw_url
    except requests.exceptions.RequestException as e:
        log(f"⚠️ Ошибка проверки кастомного изображения для {vendor_code}: {e}")

    return None

def normalize_collection_id(val):
    """Нормализует ID коллекции."""
    if not val:
        return ""
    s_val = str(val)
    if 'e' in s_val.lower() or ('.' in s_val and s_val.replace('.', '').isdigit()):
        try:
            return str(int(float(s_val)))
        except:
            pass
    if isinstance(val, (int, float)):
        return str(int(val))
    return s_val

def load_progress():
    """Загружает прогресс парсинга из файла."""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
        except Exception as e:
            log(f"❌ Ошибка загрузки прогресса: {e}")
    return {"products": []}

def save_progress(products):
    """Сохраняет прогресс парсинга в файл."""
    try:
        unique_products = []
        seen_vendors = set()
        for p in products:
            vendor_code = p.get('vendorCode')
            if vendor_code and vendor_code not in seen_vendors:
                unique_products.append(p)
                seen_vendors.add(vendor_code)
        
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump({"products": unique_products}, f, ensure_ascii=False, indent=4)
        log(f"✅ Прогресс сохранён: {len(unique_products)} товаров")
    except Exception as e:
        log(f"❌ Ошибка сохранения: {e}")

def parse_catalog_page(page):
    """Парсит лендинг, извлекает характеристики и игнорирует внутренние ссылки товаров."""
    log(f"📦 Начинаем парсинг посадочной страницы: {BASE_URL}")
    all_products = []
    
    page.goto(BASE_URL, timeout=60000)
    page.wait_for_timeout(5000)
    
    # Пробуем закрыть попап "Да, мне есть 18"
    try:
        popup_btn = page.locator('text="Да, мне есть 18"')
        if popup_btn.is_visible(timeout=3000):
            log("🔞 Закрываем окно подтверждения возраста...")
            popup_btn.click()
            page.wait_for_timeout(1000)
    except Exception:
        pass

    # Дополнительный короткий скролл для ленивой загрузки изображений
    last_height = page.evaluate("document.body.scrollHeight")
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        page.wait_for_timeout(1000)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
        
    # Ищем карточки товаров
    product_elements = page.query_selector_all('.js-product.t-catalog__card, .js-product.t-store__card')
    log(f"🔍 Найдено {len(product_elements)} карточек товаров на странице.")
    
    for card in product_elements:
        try:
            # Двойная проверка наличия
            sold_out_el = card.query_selector('.js-catalog-prod-sold-out, .js-store-prod-sold-out')
            inv_count = card.get_attribute('data-product-inv')
            
            available = True
            if sold_out_el or inv_count == "0":
                available = False
            
            name_el = card.query_selector('.js-catalog-prod-name, .js-store-prod-name, .js-product-name')
            name = name_el.inner_text().strip() if name_el else ""
            if not name: continue
            
            sku_el = card.query_selector('.js-catalog-prod-sku, .js-store-prod-sku, .js-product-sku')
            vendorCode = sku_el.inner_text().replace('Артикул:', '').strip() if sku_el else ""
            if not vendorCode: continue
            
            # ЖЕСТКАЯ ПРИВЯЗКА К ЛЕНДИНГУ
            # Игнорируем реальные ссылки, чтобы трафик всегда шел на BASE_URL
            link = BASE_URL
            
            # Цены
            price_el = card.query_selector('.js-product-price')
            price = "0"
            if price_el:
                price_attr = price_el.get_attribute('data-product-price-def')
                if price_attr:
                    price = price_attr
                else:
                    price_match = re.search(r'\d+', price_el.inner_text().replace(' ',''))
                    if price_match: price = price_match[0]

            old_price_el = card.query_selector('.js-store-prod-price-old-val')
            old_price = ""
            if old_price_el:
                old_price_match = re.search(r'\d+', old_price_el.inner_text().replace(' ',''))
                if old_price_match: old_price = old_price_match[0]

            img_el = card.query_selector('.js-product-img')
            image = (img_el.get_attribute('data-original') if img_el else "").strip()

            descr_el = card.query_selector('.js-catalog-prod-descr, .js-store-prod-descr')
            description = descr_el.inner_text().strip() if descr_el else ""

            # Формирование характеристик
            properties = []
            properties.append({'name': 'Категория', 'value': 'Надувные пробки'})
            properties.append({'name': 'Бренд', 'value': 'Секспедиция'})
            
            pack_m = card.get_attribute('data-product-pack-m')
            if pack_m and pack_m.isdigit() and int(pack_m) > 0:
                properties.append({'name': 'Вес брутто', 'value': f"{pack_m} г"})
                
            pack_x = card.get_attribute('data-product-pack-x')
            if pack_x and pack_x.isdigit() and int(pack_x) > 0:
                properties.append({'name': 'Длина упаковки', 'value': f"{pack_x} мм"})
                
            pack_y = card.get_attribute('data-product-pack-y')
            if pack_y and pack_y.isdigit() and int(pack_y) > 0:
                properties.append({'name': 'Ширина упаковки', 'value': f"{pack_y} мм"})
                
            pack_z = card.get_attribute('data-product-pack-z')
            if pack_z and pack_z.isdigit() and int(pack_z) > 0:
                properties.append({'name': 'Высота упаковки', 'value': f"{pack_z} мм"})

            all_products.append({
                'name': name, 'vendorCode': vendorCode, 'link': link, 'price': price, 'oldprice': old_price,
                'image': image, 'description': description, 'available': available, 
                'properties': properties, 'additional_images': []
            })
        except Exception as e:
            log(f"⚠️ Ошибка парсинга карточки: {e}")
            continue
            
    return all_products

def clean_text_for_xml(text):
    """Экранирует критические символы для XML."""
    if not text:
        return ""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text

def generate_xml(products):
    """Генерирует XML-фид вручную, делая коллекции точными клонами офферов."""
    log("📝 Генерация XML-фида...")
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    xml_lines = []
    xml_lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    xml_lines.append(f'<yml_catalog date="{current_date}">')
    xml_lines.append('  <shop>')
    
    xml_lines.append('    <name>Секспедиция</name>')
    xml_lines.append('    <company>Секспедиция</company>')
    xml_lines.append(f'    <url>{BASE_URL}</url>')
    xml_lines.append('    <platform>Tilda</platform>')
    xml_lines.append('    <version>1.0</version>')
    
    xml_lines.append('    <currencies>')
    xml_lines.append('      <currency id="RUB" rate="1"/>')
    xml_lines.append('    </currencies>')
    
    xml_lines.append('    <categories>')
    xml_lines.append('      <category id="1">Секс-игрушки</category>')
    xml_lines.append('    </categories>')
    
    # 1. Блок Офферов
    xml_lines.append('    <offers>')
    
    for prod in products:
        vendor_code = prod.get('vendorCode')
        if not vendor_code: continue
        
        is_available = prod.get('available', True)
        avail_str = "true" if is_available else "false"
        
        xml_lines.append(f'      <offer id="{vendor_code}" available="{avail_str}">')
        xml_lines.append(f'        <name>{clean_text_for_xml(prod["name"])}</name>')
        xml_lines.append('        <vendor>Секспедиция</vendor>')
        xml_lines.append(f'        <vendorCode>{vendor_code}</vendorCode>')
        xml_lines.append(f'        <price>{prod["price"]}</price>')
        
        if prod.get("oldprice"):
            xml_lines.append(f'        <oldprice>{prod["oldprice"]}</oldprice>')
            
        xml_lines.append('        <currencyId>RUB</currencyId>')
        xml_lines.append('        <categoryId>1</categoryId>')
        
        custom_image = get_custom_image_url(vendor_code)
        pic_url = custom_image if custom_image else prod.get("image")
        if pic_url:
            xml_lines.append(f'        <picture>{pic_url}</picture>')
            
        # Привязываем коллекцию только если товар в наличии
        if is_available:
            xml_lines.append(f'        <collectionId>{vendor_code}</collectionId>')
             
        xml_lines.append(f'        <url>{clean_text_for_xml(prod["link"])}</url>')
        
        if prod.get("description"):
             xml_lines.append(f'        <description>{clean_text_for_xml(prod["description"])}</description>')
             
        xml_lines.append('        <sales_notes>Официальный сайт Секспедиция.</sales_notes>')
        xml_lines.append(f'        <custom_label_0>{clean_text_for_xml(prod["name"])}</custom_label_0>')
        
        if prod.get("properties"):
            for prop in prod["properties"]:
                p_name = clean_text_for_xml(prop.get("name", ""))
                p_val = clean_text_for_xml(prop.get("value", ""))
                if p_name and p_val:
                    xml_lines.append(f'        <property name="{p_name}">{p_val}</property>')
        
        xml_lines.append('      </offer>')

    xml_lines.append('    </offers>')
    
    # 2. Блок Коллекций (формируются персонально под каждый товар, ТОЛЬКО если в наличии)
    if products:
        xml_lines.append('    <collections>')
        for prod in products:
            is_available = prod.get('available', True)
            if not is_available: 
                continue 
                
            vendor_code = prod.get('vendorCode')
            if not vendor_code: continue
            
            custom_image = get_custom_image_url(vendor_code)
            image_url = custom_image if custom_image else prod.get("image", "")
            
            xml_lines.append(f'      <collection id="{vendor_code}">')
            xml_lines.append(f'        <name>{clean_text_for_xml(prod["name"])}</name>')
            xml_lines.append(f'        <url>{clean_text_for_xml(prod["link"])}</url>')
            
            if image_url:
                xml_lines.append(f'        <picture>{image_url}</picture>')
            if prod.get("description"):
                xml_lines.append(f'        <description>{clean_text_for_xml(prod["description"])}</description>')
            xml_lines.append('      </collection>')
        xml_lines.append('    </collections>')

    xml_lines.append('  </shop>')
    xml_lines.append('</yml_catalog>')

    try:
        with open(TEMP_XML_FILE, "w", encoding="utf-8") as f:
            f.write('\n'.join(xml_lines))
            
        if os.path.exists(XML_FILE):
            shutil.copy2(XML_FILE, XML_FILE + ".backup")
        os.replace(TEMP_XML_FILE, XML_FILE)
        log(f"✅ XML сохранён: {XML_FILE}")
    except Exception as e:
        log(f"❌ Ошибка сохранения XML: {e}")

# --- ОСНОВНОЙ ЗАПУСК ---
if __name__ == "__main__":
    log(f"🚀 Запуск парсера посадочной {BASE_URL}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        
        try:
            current_products = parse_catalog_page(page)
            
            progress = load_progress()
            products_map = {p['vendorCode']: p for p in progress.get("products", [])}
            current_skus = {p['vendorCode'] for p in current_products}
            
            # Помечаем отсутствующие на странице товары как "нет в наличии"
            for vendor_code, old_prod in products_map.items():
                if vendor_code not in current_skus:
                    old_prod['available'] = False
                    
            # Обновляем базу свежими данными
            for prod in current_products:
                products_map[prod['vendorCode']] = prod
            
            final_products = list(products_map.values())
            save_progress(final_products)
            generate_xml(final_products)
            
            log(f"🎉 Готово! Всего товаров в фиде: {len(final_products)}")
            
        except Exception as e:
            log(f"❌ Критическая ошибка: {e}")
        finally:
            browser.close()
            log("✅ Браузер закрыт.")