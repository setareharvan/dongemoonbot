import telebot
from telebot import apihelper

import config

apihelper.CONNECT_TIMEOUT = 15
apihelper.READ_TIMEOUT = 40
apihelper.RETRY_ON_ERROR = True          # تلاش مجدد روی خطای شبکه
if config.PROXY:
    apihelper.proxy = {"https": config.PROXY}

# threaded=False → پردازش ترتیبی و قابل‌اتکا (وب‌هوک باید قبل از پاسخ 200 تمام شود)
bot = telebot.TeleBot(config.TOKEN, threaded=False, parse_mode=None)