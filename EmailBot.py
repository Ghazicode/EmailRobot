import telebot
import imaplib
import email
from email.header import decode_header
import time
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, MenuButtonCommands
import threading
import logging
from bs4 import BeautifulSoup
import re

# تنظیمات لاگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('email_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# تنظیمات اتصال با قابلیت تلاش مجدد
session = requests.Session()
retry = Retry(
    total=5,
    backoff_factor=0.5,
    status_forcelist=[500, 502, 503, 504]
)
adapter = HTTPAdapter(max_retries=retry)
session.mount('http://', adapter)
session.mount('https://', adapter)

# تنظیمات ربات
TELEGRAM_TOKEN = "Bot token"
EMAIL = "your email"
PASSWORD = "App password"
IMAP_SERVER = 'imap.gmail.com'
ALLOWED_USERS = [1234567891]  # چت آیدی شما (عدد)
ADMIN_CHAT_ID = 1234567891    # چت آیدی مدیر (عدد)

# ایجاد ربات
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
bot.session = session

# تنظیم منوی دستورات و منوی کنار چت
def setup_menus():
    bot.set_my_commands([
        BotCommand('start', 'شروع کار با ربات'),
        BotCommand('unread', 'نمایش ایمیل‌های خوانده نشده'),
        BotCommand('stats', 'نمایش آمار ایمیل‌ها'),
        BotCommand('help', 'راهنمای استفاده از ربات'),
        BotCommand('setup', 'تنظیمات اولیه ربات')
    ])
    try:
        bot.set_chat_menu_button(menu_button=MenuButtonCommands(type='commands'))
    except Exception as e:
        logger.error(f"خطا در تنظیم منوی چت: {str(e)}")

# دیتابیس ساده
email_db = {
    'emails': {},
    'users': ALLOWED_USERS,
    'config': {
        'email': EMAIL,
        'imap_server': IMAP_SERVER
    }
}

def clean_html(raw_html):
    """پاکسازی HTML و تبدیل به متن ساده"""
    if not raw_html:
        return "بدون متن"
    
    # حذف تگ‌های اسکریپت و استایل
    cleanr = re.compile('<script.*?</script>|<style.*?</style>', re.DOTALL)
    cleantext = re.sub(cleanr, '', raw_html)
    
    # استفاده از BeautifulSoup برای استخراج متن
    soup = BeautifulSoup(cleantext, "html.parser")
    text = soup.get_text(separator="\n")
    
    # حذف فضاهای خالی اضافی
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = text.strip()
    
    return text if text else "بدون متن"

def get_email_body(msg):
    """استخراج محتوای ایمیل با پشتیبانی از HTML"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            
            # اولویت با متن ساده است
            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    body = part.get_payload(decode=True).decode()
                    break
                except:
                    continue
            
            # اگر متن ساده نبود، از HTML استفاده می‌کنیم
            elif content_type == "text/html" and "attachment" not in content_disposition:
                try:
                    html_content = part.get_payload(decode=True).decode()
                    body = clean_html(html_content)
                    break
                except:
                    continue
    else:
        try:
            if msg.get_content_type() == "text/html":
                html_content = msg.get_payload(decode=True).decode()
                body = clean_html(html_content)
            else:
                body = msg.get_payload(decode=True).decode()
        except:
            body = "متن ایمیل قابل نمایش نیست"
    
    return body if body else "بدون متن"

def connect_imap():
    """اتصال به سرور IMAP با مدیریت خطا"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER, timeout=30)
            mail.login(EMAIL, PASSWORD)
            mail.select('inbox')
            return mail
        except Exception as e:
            logger.error(f"اتصال IMAP شکست خورد (تلاش {attempt + 1}): {str(e)}")
            if attempt == max_retries - 1:
                raise
            time.sleep(5)

def check_emails():
    """بررسی ایمیل‌های جدید"""
    try:
        mail = connect_imap()
        status, messages = mail.search(None, 'UNSEEN')
        
        if status == 'OK' and messages[0]:
            for mail_id in messages[0].split():
                status, msg_data = mail.fetch(mail_id, '(RFC822)')
                
                if status == 'OK':
                    msg = email.message_from_bytes(msg_data[0][1])
                    subject, encoding = decode_header(msg['Subject'])[0]
                    subject = subject.decode(encoding) if isinstance(subject, bytes) else subject
                    
                    from_, encoding = decode_header(msg.get('From'))[0]
                    from_ = from_.decode(encoding) if isinstance(from_, bytes) else from_
                    
                    body = get_email_body(msg)
                    email_key = f"{mail_id.decode()}_{int(time.time())}"
                    
                    email_db['emails'][email_key] = {
                        'mail_id': mail_id,
                        'read': False,
                        'subject': subject,
                        'from': from_,
                        'body': body[:4000],  # محدودیت تلگرام
                        'date': msg.get('Date')
                    }
                    
                    markup = InlineKeyboardMarkup()
                    markup.row(
                        InlineKeyboardButton("✅ خوانده شد", callback_data=f"read_{email_key}"),
                        InlineKeyboardButton("📌 مهم", callback_data=f"important_{email_key}")
                    )
                    markup.row(
                        InlineKeyboardButton("📝 نمایش کامل", callback_data=f"full_{email_key}")
                    )
                    
                    for user_id in ALLOWED_USERS:
                        try:
                            bot.send_message(
                                user_id,
                                f"📧 ایمیل جدید\n\n✉️ از: {from_}\n📌 موضوع: {subject}\n📅 تاریخ: {msg.get('Date')}\n\n📝 متن:\n{body[:500]}...",
                                reply_markup=markup
                            )
                        except Exception as e:
                            logger.error(f"ارسال به کاربر {user_id} شکست خورد: {str(e)}")
        
        mail.close()
        mail.logout()
    except Exception as e:
        logger.error(f"خطا در بررسی ایمیل‌ها: {str(e)}")
        bot.send_message(ADMIN_CHAT_ID, f"⚠️ خطا در بررسی ایمیل‌ها:\n{str(e)}")

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """مدیریت کلیک روی دکمه‌ها"""
    if call.from_user.id not in ALLOWED_USERS:
        bot.answer_callback_query(call.id, "⛔ دسترسی غیرمجاز", show_alert=True)
        return
    
    try:
        action, email_key = call.data.split('_', 1)
        email_data = email_db['emails'].get(email_key)
        
        if not email_data:
            bot.answer_callback_query(call.id, "⚠️ ایمیل منقضی شده", show_alert=True)
            return
        
        mail = connect_imap()
        
        if action == 'read':
            mail.store(email_data['mail_id'], '+FLAGS', '\Seen')
            email_data['read'] = True
            bot.edit_message_text(
                f"✅ خوانده شده\n\n✉️ از: {email_data['from']}\n📌 موضوع: {email_data['subject']}",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None
            )
            bot.answer_callback_query(call.id, "علامت‌گذاری شد")
            
        elif action == 'important':
            mail.store(email_data['mail_id'], '+FLAGS', '\Flagged')
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("✅ خوانده شد", callback_data=f"read_{email_key}"),
                InlineKeyboardButton("📝 نمایش کامل", callback_data=f"full_{email_key}")
            )
            bot.edit_message_text(
                f"🚩 مهم\n\n✉️ از: {email_data['from']}\n📌 موضوع: {email_data['subject']}",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup
            )
            bot.answer_callback_query(call.id, "ایمیل مهم علامت‌گذاری شد")
            
        elif action == 'full':
            clean_body = clean_html(email_data['body'])
            message_text = (
                f"📧 متن کامل\n\n✉️ از: {email_data['from']}\n"
                f"📌 موضوع: {email_data['subject']}\n"
                f"📅 تاریخ: {email_data['date']}\n\n"
                f"📝 متن:\n{clean_body[:4000]}"
            )
            bot.send_message(
                call.message.chat.id,
                message_text
            )
            bot.answer_callback_query(call.id)
            
        mail.close()
        mail.logout()
        
    except Exception as e:
        logger.error(f"خطا در پردازش callback: {str(e)}")
        bot.answer_callback_query(call.id, "⚠️ خطا در پردازش", show_alert=True)

# دستورات ربات
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if message.from_user.id not in ALLOWED_USERS:
        return
        
    bot.reply_to(message, """
🤖 ربات مدیریت ایمیل

🔹 دستورات:
/unread - نمایش ایمیل‌های خوانده نشده
/stats - آمار ایمیل‌ها
/setup - تنظیمات ربات
/help - راهنمای استفاده
""")

@bot.message_handler(commands=['unread'])
def show_unread(message):
    if message.from_user.id not in ALLOWED_USERS:
        return
        
    unread_emails = [e for e in email_db['emails'].values() if not e['read']]
    
    if not unread_emails:
        bot.reply_to(message, "📭 هیچ ایمیل خوانده نشده‌ای وجود ندارد")
        return
    
    bot.reply_to(message, f"📬 ایمیل‌های خوانده نشده ({len(unread_emails)} مورد):")
    
    for email_data in unread_emails[-5:]:  # نمایش ۵ مورد آخر
        email_key = next(k for k, v in email_db['emails'].items() if v == email_data)
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ خوانده شد", callback_data=f"read_{email_key}"),
            InlineKeyboardButton("📝 نمایش کامل", callback_data=f"full_{email_key}")
        )
        
        bot.send_message(
            message.chat.id,
            f"✉️ از: {email_data['from']}\n📌 موضوع: {email_data['subject']}\n📅 تاریخ: {email_data['date']}",
            reply_markup=markup
        )

@bot.message_handler(commands=['stats'])
def show_stats(message):
    if message.from_user.id not in ALLOWED_USERS:
        return
        
    total = len(email_db['emails'])
    unread = len([e for e in email_db['emails'].values() if not e['read']])
    
    bot.reply_to(message, f"""
📊 آمار ایمیل‌ها:

📭 کل ایمیل‌ها: {total}
📬 خوانده نشده: {unread}
📭 خوانده شده: {total - unread}
""")

@bot.message_handler(commands=['setup'])
def setup_bot(message):
    if message.from_user.id != ADMIN_CHAT_ID:
        return
        
    bot.reply_to(message, f"""
⚙️ تنظیمات فعلی:

📧 ایمیل: {email_db['config']['email']}
🖥️ سرور IMAP: {email_db['config']['imap_server']}
👤 کاربران مجاز: {len(email_db['users'])} نفر
""")

# اجرای چک ایمیل در پس‌زمینه
def email_poller():
    while True:
        try:
            check_emails()
            time.sleep(60)
        except Exception as e:
            logger.error(f"خطا در EmailPoller: {str(e)}")
            time.sleep(120)

if __name__ == '__main__':
    logger.info("Starting email bot...")
    setup_menus()  # تنظیم منوها
    threading.Thread(target=email_poller, daemon=True).start()
    bot.infinity_polling()
