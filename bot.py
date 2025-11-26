import logging
import sqlite3
import os
import json
import asyncio
import time
import warnings
import threading
import statistics
import io
import html
import re
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

# --- Standard Time & Date Libraries ---
from datetime import datetime, timedelta, timezone
import jdatetime

# --- Networking & Cryptography ---
import requests
import paramiko
from cryptography.fernet import Fernet

# --- Plotting (Matplotlib - Thread Safe Fix) ---
import matplotlib
matplotlib.use('Agg')  # Set backend to non-interactive
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

# --- Telegram Libraries ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.error import BadRequest, TelegramError, Conflict, NetworkError
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler, JobQueue
)

# ==============================================================================
# ⚙️ CONFIGURATION & CONSTANTS
# ==============================================================================
CONFIG_FILE = 'sonar_config.json'

try:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            TOKEN = config.get('bot_token', 'Not_Set')
            try:
                SUPER_ADMIN_ID = int(config.get('admin_id', 0))
            except:
                SUPER_ADMIN_ID = 0
    else:
        TOKEN = 'TOKEN_NOT_SET'
        SUPER_ADMIN_ID = 0
        print(f"⚠️ Config file ({CONFIG_FILE}) not found. Please run install.sh")
except Exception as e:
    print(f"❌ Error loading config: {e}")
    TOKEN = 'ERROR'
    SUPER_ADMIN_ID = 0

DEFAULT_INTERVAL = 40
DOWN_RETRY_LIMIT = 3
DB_NAME = 'sonar_ultra_pro.db'
KEY_FILE = 'secret.key'
# --- Subscription Configuration (تنظیمات اشتراک و پرداخت) ---
SUBSCRIPTION_PLANS = {
    'bronze': {
        'name': 'برنزی 🥉',
        'limit': 5,
        'days': 30,
        'price': 100000,
        'desc': 'مناسب برای استفاده شخصی'
    },
    'silver': {
        'name': 'نقره‌ای 🥈',
        'limit': 10,
        'days': 30,
        'price': 180000,
        'desc': 'مناسب برای تیم‌های کوچک'
    },
    'gold': {
        'name': 'طلایی 🥇',
        'limit': 15,
        'days': 30,
        'price': 240000,
        'desc': 'حرفه‌ای و بدون محدودیت'
    }
}

# اطلاعات پرداخت (اطلاعات خود را جایگزین کنید)
PAYMENT_INFO = {
    'card': {
        'number': '6037-9979-0000-0000',
        'name': 'نام صاحب حساب'
    },
    'tron': {
        'address': 'TRC20_WALLET_ADDRESS_HERE',
        'network': 'TRC20'
    }
}
# --- Global Cache & State Trackers ---
SERVER_FAILURE_COUNTS = {}
LAST_REPORT_CACHE = {}
CPU_ALERT_TRACKER = {}
DAILY_REPORT_USAGE = {}
UPTIME_MILESTONE_TRACKER = set()
CPU_ALERT_TRACKER = {}
DAILY_REPORT_USAGE = {}
SSH_SESSION_CACHE = {}

# --- Conversation States ---
(
    GET_NAME, GET_IP, GET_PORT, GET_USER, GET_PASS, SELECT_GROUP,
    GET_GROUP_NAME, GET_CHANNEL_FORWARD, GET_MANUAL_HOST,
    ADD_ADMIN_ID, ADD_ADMIN_DAYS, ADMIN_SEARCH_USER,
    ADMIN_SET_LIMIT, ADMIN_RESTORE_DB, ADMIN_RESTORE_KEY, ADMIN_SET_TIME_MANUAL,
    GET_CUSTOM_INTERVAL,
    GET_EXPIRY,
    GET_CHANNEL_TYPE,
    EDIT_SERVER_EXPIRY,
    GET_REMOTE_COMMAND,  
    GET_CPU_LIMIT, GET_RAM_LIMIT, GET_DISK_LIMIT,
    GET_BROADCAST_MSG,
    GET_REBOOT_TIME,
    ADD_PAY_TYPE, ADD_PAY_NET, ADD_PAY_ADDR, ADD_PAY_HOLDER,
    GET_RECEIPT
) = range(31)

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")


# ==============================================================================
# 📅 DATE & TIME UTILS
# ==============================================================================
def get_tehran_datetime():
    return datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)

def get_jalali_str():
    tehran_now = get_tehran_datetime()
    j_date = jdatetime.datetime.fromgregorian(datetime=tehran_now)
    months = {
        1: 'فروردین', 2: 'اردیبهشت', 3: 'خرداد', 4: 'تیر', 5: 'مرداد', 
        6: 'شهریور', 7: 'مهر', 8: 'آبان', 9: 'آذر', 10: 'دی', 11: 'بهمن', 12: 'اسفند'
    }
    return f"{j_date.day} {months[j_date.month]} {j_date.year} | {j_date.hour:02d}:{j_date.minute:02d}"


# ==============================================================================
# 🔐 SECURITY & DATABASE
# ==============================================================================
class Security:
    def __init__(self):
        if not os.path.exists(KEY_FILE):
            with open(KEY_FILE, 'wb') as f:
                f.write(Fernet.generate_key())
        with open(KEY_FILE, 'rb') as f:
            self.key = f.read()
        self.cipher = Fernet(self.key)

    def encrypt(self, txt):
        return self.cipher.encrypt(txt.encode()).decode()

    def decrypt(self, txt):
        try:
            return self.cipher.decrypt(txt.encode()).decode()
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return "" 


class Database:
    def __init__(self):
        self.db_name = DB_NAME
        self.init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_name, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA journal_mode=WAL;')
            yield conn
        except sqlite3.Error as e:
            logger.error(f"Database Error: {e}")
        finally:
            conn.close()

    def init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, full_name TEXT, added_date TEXT, expiry_date TEXT,
                server_limit INTEGER DEFAULT 2, is_banned INTEGER DEFAULT 0, plan_type INTEGER DEFAULT 0
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, name TEXT, UNIQUE(owner_id, name)
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, group_id INTEGER, name TEXT, 
                ip TEXT, port INTEGER, username TEXT, password TEXT, expiry_date TEXT, 
                last_status TEXT DEFAULT 'Unknown', is_active INTEGER DEFAULT 1, UNIQUE(owner_id, name)
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
                owner_id INTEGER, key TEXT, value TEXT, PRIMARY KEY(owner_id, key)
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, chat_id TEXT, name TEXT, 
                usage_type TEXT DEFAULT "all"
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS server_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER, cpu REAL, ram REAL, 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            conn.commit()
            self.migrate()

    def migrate(self):
        with self.get_connection() as conn:
            try: conn.execute("ALTER TABLE servers ADD COLUMN expiry_date TEXT")
            except: pass
            try: conn.execute("ALTER TABLE channels ADD COLUMN usage_type TEXT DEFAULT 'all'")
            except: pass
            try: conn.execute("ALTER TABLE users ADD COLUMN plan_type INTEGER DEFAULT 0")
            except: pass
            try: conn.execute("ALTER TABLE users ADD COLUMN wallet_balance INTEGER DEFAULT 0")
            except: pass
            try: conn.execute("ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0")
            except: pass
            try: conn.execute("ALTER TABLE users ADD COLUMN invited_by INTEGER DEFAULT 0")
            except: pass
            
            # --- جدول جدید پرداخت‌ها ---
            conn.execute('''CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                plan_type TEXT,
                amount INTEGER,
                method TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS temp_bonuses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                bonus_limit INTEGER,
                created_at TEXT,
                expires_at TEXT
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,        -- 'card' or 'crypto'
                network TEXT,     -- Bank Name or Network (TRC20/TON)
                address TEXT,     -- Card Number or Wallet Address
                holder_name TEXT, -- Owner Name
                is_active INTEGER DEFAULT 1
            )''')
            conn.commit()
    # --- Payment Methods ---
    def create_payment(self, user_id, plan_type, amount, method):
        now = get_tehran_datetime().strftime('%Y-%m-%d %H:%M:%S')
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO payments (user_id, plan_type, amount, method, created_at) VALUES (?, ?, ?, ?, ?)',
                (user_id, plan_type, amount, method, now)
            )
            conn.commit()
            return cursor.lastrowid

    def approve_payment(self, payment_id):
        with self.get_connection() as conn:
            # 1. گرفتن اطلاعات پرداخت
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM payments WHERE id = ?', (payment_id,))
            pay = cursor.fetchone()
            
            if not pay or pay['status'] == 'approved': return False
            
            # 2. آپدیت وضعیت پرداخت
            conn.execute("UPDATE payments SET status = 'approved' WHERE id = ?", (payment_id,))
            
            # 3. اعمال تغییرات روی کاربر
            plan = SUBSCRIPTION_PLANS.get(pay['plan_type'])
            if plan:
                # محاسبه تاریخ انقضا
                cursor.execute('SELECT * FROM users WHERE user_id = ?', (pay['user_id'],))
                user = cursor.fetchone()
                
                try:
                    current_exp = datetime.strptime(user['expiry_date'], '%Y-%m-%d %H:%M:%S')
                    if current_exp < datetime.now(): current_exp = datetime.now()
                except:
                    current_exp = datetime.now()
                
                new_exp = (current_exp + timedelta(days=plan['days'])).strftime('%Y-%m-%d %H:%M:%S')
                
                # تعیین کد پلن (1=Bronze, 2=Silver, 3=Gold)
                p_type_code = 1 if pay['plan_type'] == 'bronze' else 2 if pay['plan_type'] == 'silver' else 3
                
                conn.execute('''
                    UPDATE users 
                    SET server_limit = ?, expiry_date = ?, plan_type = ? 
                    WHERE user_id = ?
                ''', (plan['limit'], new_exp, p_type_code, pay['user_id']))
                
            conn.commit()
            return pay['user_id'], plan['name']
    def apply_referral_reward(self, inviter_id):
        """اعمال جایزه: +1 سرور (موقت ۱۰ روزه) و +10 روز اعتبار"""
        user = self.get_user(inviter_id)
        if not user: return False, 0, ""
        
        # 1. افزایش لیمیت کاربر
        new_limit = user['server_limit'] + 1
        
        # 2. افزایش تاریخ انقضای اکانت (+10 روز)
        try:
            current_exp = datetime.strptime(user['expiry_date'], '%Y-%m-%d %H:%M:%S')
            if current_exp < datetime.now(): current_exp = datetime.now()
            new_exp = (current_exp + timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
        except:
            new_exp = (datetime.now() + timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')

        # 3. محاسبه زمان انقضای پاداش سرور (۱۰ روز بعد از الان)
        bonus_expiry = (datetime.now() + timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with self.get_connection() as conn:
            # آپدیت کاربر
            conn.execute('''
                UPDATE users 
                SET server_limit = ?, expiry_date = ?, referral_count = referral_count + 1 
                WHERE user_id = ?
            ''', (new_limit, new_exp, inviter_id))
            
            # ثبت پاداش موقت در جدول جدید
            conn.execute('''
                INSERT INTO temp_bonuses (user_id, bonus_limit, created_at, expires_at)
                VALUES (?, 1, ?, ?)
            ''', (inviter_id, now_str, bonus_expiry))
            
            conn.commit()
            
        return True, new_limit, new_exp

    def update_wallet(self, user_id, amount):
        """افزایش یا کاهش موجودی (amount می‌تواند منفی باشد)"""
        with self.get_connection() as conn:
            conn.execute('UPDATE users SET wallet_balance = wallet_balance + ? WHERE user_id = ?', (amount, user_id))
            conn.commit()        
    def toggle_user_plan(self, user_id):
        user = self.get_user(user_id)
        if not user: return 0 
        new_plan = 1 if user['plan_type'] == 0 else 0
        new_limit = 10 if new_plan == 1 else 2
        with self.get_connection() as conn:
            conn.execute('UPDATE users SET plan_type = ?, server_limit = ? WHERE user_id = ?', (new_plan, new_limit, user_id))
            conn.commit()
        return new_plan
    
    def add_or_update_user(self, user_id, full_name=None, invited_by=0):
        exist = self.get_user(user_id)
        now_str = get_tehran_datetime().strftime('%Y-%m-%d %H:%M:%S')
        default_limit = 2
        default_days = 60
        
        with self.get_connection() as conn:
            if exist:
                if full_name:
                    conn.execute('UPDATE users SET full_name = ? WHERE user_id = ?', (full_name, user_id))
            else:
                expiry = (get_tehran_datetime() + timedelta(days=default_days)).strftime('%Y-%m-%d %H:%M:%S')
                conn.execute('''
                    INSERT INTO users (user_id, full_name, added_date, expiry_date, server_limit, invited_by, wallet_balance, referral_count) 
                    VALUES (?, ?, ?, ?, ?, ?, 0, 0)
                ''', (user_id, full_name, now_str, expiry, default_limit, invited_by))
            conn.commit()
            
    def update_user_limit(self, user_id, limit):
        with self.get_connection() as conn:
            conn.execute('UPDATE users SET server_limit = ? WHERE user_id = ?', (limit, user_id))
            conn.commit()

    def toggle_ban_user(self, user_id):
        user = self.get_user(user_id)
        if not user: return 0
        new_state = 0 if user['is_banned'] else 1
        with self.get_connection() as conn:
            conn.execute('UPDATE users SET is_banned = ? WHERE user_id = ?', (new_state, user_id))
            conn.commit()
        return new_state

    def get_user(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            return cursor.fetchone()

    def get_all_users_paginated(self, page=1, per_page=5):
        offset = (page - 1) * per_page
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users LIMIT ? OFFSET ?', (per_page, offset))
            users = cursor.fetchall()
            cursor.execute('SELECT COUNT(*) FROM users')
            total = cursor.fetchone()[0]
            return users, total

    def get_all_users(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users')
            return cursor.fetchall()

    def remove_user(self, user_id):
        with self.get_connection() as conn:
            for t in ['users', 'servers', 'groups', 'channels']:
                col = 'user_id' if t == 'users' else 'owner_id'
                conn.execute(f'DELETE FROM {t} WHERE {col} = ?', (user_id,))
            conn.commit()

    def check_access(self, user_id):
        if user_id == SUPER_ADMIN_ID: return True, "Super Admin"
        user = self.get_user(user_id)
        if not user: return False, "کاربر یافت نشد"
        if user['is_banned']: return False, "حساب شما مسدود شده است ⛔️"
        try:
            expiry_dt = datetime.strptime(user['expiry_date'], '%Y-%m-%d %H:%M:%S')
            now_tehran_naive = get_tehran_datetime().replace(tzinfo=None)
            if now_tehran_naive > expiry_dt: return False, "اشتراک شما منقضی شده است 📅"
            return True, (expiry_dt - now_tehran_naive).days
        except: return False, "خطا در تاریخ"

    # --- Group Methods ---
    def add_group(self, owner_id, name):
        with self.get_connection() as conn:
            conn.execute('INSERT INTO groups (owner_id, name) VALUES (?,?)', (owner_id, name))
            conn.commit()

    def get_user_groups(self, owner_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM groups WHERE owner_id = ?', (owner_id,))
            return cursor.fetchall()

    def delete_group(self, group_id, owner_id):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM groups WHERE id = ? AND owner_id = ?', (group_id, owner_id))
            conn.execute('UPDATE servers SET group_id = NULL WHERE group_id = ? AND owner_id = ?', (group_id, owner_id)) 
            conn.commit()

    # --- Server Methods ---
    def add_server(self, owner_id, group_id, data):
        g_id = group_id if group_id != 0 else None
        user = self.get_user(owner_id)
        current_servers_list = self.get_all_user_servers(owner_id)
        current_count = len(current_servers_list)

        if user and owner_id != SUPER_ADMIN_ID:
            if current_count >= user['server_limit']:
                raise Exception("Server Limit Reached")
        
        with self.get_connection() as conn:
            # --- تغییر جدید: شروع تایمر ۳۰ روزه با اولین سرور ---
            if current_count == 0 and user['plan_type'] == 0:
                new_expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
                conn.execute('UPDATE users SET expiry_date = ? WHERE user_id = ?', (new_expiry, owner_id))
            # -----------------------------------------------------

            conn.execute(
                'INSERT INTO servers (owner_id, group_id, name, ip, port, username, password, expiry_date) VALUES (?,?,?,?,?,?,?,?)',
                (owner_id, g_id, data['name'], data['ip'], data['port'], data['username'], data['password'], data.get('expiry_date'))
            )
            conn.commit()

    def get_all_user_servers(self, owner_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM servers WHERE owner_id = ?', (owner_id,))
            return cursor.fetchall()

    def get_servers_by_group(self, owner_id, group_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            sql = 'SELECT * FROM servers WHERE owner_id = ? AND group_id IS NULL' if group_id == 0 else 'SELECT * FROM servers WHERE owner_id = ? AND group_id = ?'
            cursor.execute(sql, (owner_id,) if group_id == 0 else (owner_id, group_id))
            return cursor.fetchall()

    def get_server_by_id(self, s_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM servers WHERE id = ?', (s_id,))
            return cursor.fetchone()

    def delete_server(self, s_id, owner_id):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM servers WHERE id = ? AND owner_id = ?', (s_id, owner_id))
            conn.commit()

    def update_status(self, s_id, status):
        with self.get_connection() as conn:
            conn.execute('UPDATE servers SET last_status = ? WHERE id = ?', (status, s_id))
            conn.commit()

    def update_server_expiry(self, s_id, new_date):
        with self.get_connection() as conn:
            conn.execute('UPDATE servers SET expiry_date = ? WHERE id = ?', (new_date, s_id))
            conn.commit()
    
    def toggle_server_active(self, s_id, current_state):
        new_state = 0 if current_state else 1
        with self.get_connection() as conn:
            conn.execute('UPDATE servers SET is_active = ? WHERE id = ?', (new_state, s_id))
            conn.commit()
        return new_state

    # --- Stats & Charts ---
    def add_server_stat(self, server_id, cpu, ram):
        with self.get_connection() as conn:
            conn.execute('INSERT INTO server_stats (server_id, cpu, ram) VALUES (?, ?, ?)', (server_id, cpu, ram))
            conn.execute("DELETE FROM server_stats WHERE created_at < datetime('now', '-1 day')")
            conn.commit()

    def get_server_stats(self, server_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT cpu, ram, strftime('%H:%M', created_at, '+3 hours', '+30 minutes') as time_str 
                FROM server_stats 
                WHERE server_id = ? 
                ORDER BY created_at ASC
            ''', (server_id,))
            return cursor.fetchall()

    # --- Channel & Settings Methods ---
    def add_channel(self, owner_id, chat_id, name, usage_type='all'):
        with self.get_connection() as conn:
            conn.execute('INSERT INTO channels (owner_id, chat_id, name, usage_type) VALUES (?,?,?,?)', (owner_id, chat_id, name, usage_type))
            conn.commit()

    def get_user_channels(self, owner_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM channels WHERE owner_id = ?', (owner_id,))
            return cursor.fetchall()

    def delete_channel(self, c_id, owner_id):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM channels WHERE id = ? AND owner_id = ?', (c_id, owner_id))
            conn.commit()

    def set_setting(self, owner_id, key, value):
        with self.get_connection() as conn:
            conn.execute('REPLACE INTO settings (owner_id, key, value) VALUES (?, ?, ?)', (owner_id, key, str(value)))
            conn.commit()

    def get_setting(self, owner_id, key):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM settings WHERE owner_id = ? AND key = ?', (owner_id, key,))
            res = cursor.fetchone()
            return res['value'] if res else None
    # --- Payment Settings Management ---
    def add_payment_method(self, p_type, network, address, holder):
        with self.get_connection() as conn:
            conn.execute(
                'INSERT INTO payment_methods (type, network, address, holder_name) VALUES (?, ?, ?, ?)',
                (p_type, network, address, holder)
            )
            conn.commit()

    def get_payment_methods(self, p_type=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if p_type:
                cursor.execute('SELECT * FROM payment_methods WHERE type = ? AND is_active = 1', (p_type,))
            else:
                cursor.execute('SELECT * FROM payment_methods')
            return cursor.fetchall()

    def delete_payment_method(self, p_id):
        with self.get_connection() as conn:
            conn.execute('DELETE FROM payment_methods WHERE id = ?', (p_id,))
            conn.commit()
    # --- پایان whitelist_bot_ip ---
# Initializing Global Objects
db = Database()
sec = Security()


# ==============================================================================
# 🧠 SERVER MONITOR CORE
# ==============================================================================
class ServerMonitor:
    @staticmethod
    def get_ssh_client(ip, port, user, password):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, port=port, username=user, password=password, timeout=10)
        return client
    @staticmethod
    def get_bot_public_ip():
        """آی‌پی سرور خود ربات را می‌گیرد"""
        try:
            return requests.get("https://api.ipify.org", timeout=5).text.strip()
        except:
            return None

    @staticmethod
    def whitelist_bot_ip(target_ip, port, user, password, bot_ip):
        """آی‌پی ربات را در سرور مقصد وایت‌لیست می‌کند"""
        if not bot_ip: return False, "Bot IP not found"
        
        cmds = [
            f"fail2ban-client set sshd addignoreip {bot_ip} || true",  # اگر fail2ban نصب باشد
            f"ufw allow from {bot_ip} || true",                      # اگر ufw فعال باشد
            f"iptables -I INPUT -s {bot_ip} -j ACCEPT || true"       # جهت اطمینان در iptables
        ]
        full_cmd = " && ".join(cmds)
        
        return ServerMonitor.run_remote_command(target_ip, port, user, password, full_cmd, timeout=20)
    @staticmethod
    def format_full_global_results(data):
        if not isinstance(data, dict): return "❌ خطا در داده‌های دریافتی"
        flags = {
            'us': '🇺🇸', 'fr': '🇫🇷', 'de': '🇩🇪', 'nl': '🇳🇱', 'uk': '🇬🇧', 'ru': '🇷🇺',
            'ca': '🇨🇦', 'tr': '🇹🇷', 'ua': '🇺🇦', 'ir': '🇮🇷', 'ae': '🇦🇪', 'in': '🇮🇳',
            'cn': '🇨🇳', 'jp': '🇯🇵', 'kr': '🇰🇷', 'br': '🇧🇷', 'it': '🇮🇹', 'es': '🇪🇸',
            'au': '🇦🇺', 'sg': '🇸🇬', 'hk': '🇭🇰', 'ch': '🇨🇭', 'se': '🇸🇪', 'fi': '🇫🇮'
        }
        lines = []
        for node, result in data.items():
            if not result or not result[0]: continue 
            country_code = node[:2].lower()
            flag = flags.get(country_code, '🌍')
            rtts = [p[1] * 1000 for p in result[0] if p[0] == "OK"]
            if rtts:
                avg = int(sum(rtts) / len(rtts))
                status = "🟢" if avg < 100 else "🟡" if avg < 200 else "🔴"
                lines.append(f"{flag} `{node.ljust(12)}` : {status} **{avg}ms**")
            else:
                lines.append(f"{flag} `{node.ljust(12)}` : ❌ Timeout")
        if not lines: return "⚠️ نتیجه‌ای دریافت نشد."
        lines.sort(key=lambda x: 0 if '🇮🇷' in x else 1)
        return "\n".join(lines)

    @staticmethod
    def get_datacenter_info(ip):
        try:
            url = f"https://api.iplocation.net/?ip={ip}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('response_code') == '200':
                    return True, data
                else:
                    return False, data.get('response_message', 'API Error')
            else:
                return False, f"HTTP Error: {response.status_code}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def format_iran_ping_stats(check_host_data):
        if not isinstance(check_host_data, dict): 
            return "\n   ❌ خطا در دریافت پینگ ایران"
        node_map = {
            'ir1': 'Tehran (MCI)', 'ir-thr': 'Tehran (Datacenter)',
            'ir3': 'Karaj (Asiatech)', 'ir-krj': 'Karaj (Asiatech)',
            'ir4': 'Shiraz (ParsOnline)', 'ir-shz': 'Shiraz (ParsOnline)',
            'ir5': 'Mashhad (Ferdowsi)', 'ir-mhd': 'Mashhad (Ferdowsi)',
            'ir6': 'Esfahan (Mokhaberat)', 'ir-ifn': 'Esfahan (Mokhaberat)',
            'ir2': 'Tabriz (Shatel)', 'ir-tbz': 'Tabriz (IT)'
        }
        lines = []
        for node, result in check_host_data.items():
            node_key = node.split('.')[0].lower()
            if 'ir' not in node_key: continue
            city_name = node_map.get(node_key, 'Iran (Unknown)')
            if not result or not result[0]:
                lines.append(f"🔴 {city_name}: Timeout")
                continue
            rtts = [p[1] * 1000 for p in result[0] if p[0] == "OK"]
            if rtts:
                avg_ping = sum(rtts) / len(rtts)
                status_icon = "🟢" if avg_ping < 100 else "🟡" if avg_ping < 200 else "🔴"
                lines.append(f"{status_icon} {city_name}: {avg_ping:.0f} ms")
            else:
                lines.append(f"🔴 {city_name}: Packet Loss")
        if not lines: return "\n   ⚠️ هیچ نود فعالی در ایران یافت نشد."
        return "\n" + "\n".join([f"   {line}" for line in lines])

    @staticmethod
    def make_bar(percentage, length=10):
        if not isinstance(percentage, (int, float)):
            percentage = 0
        blocks = "▏▎▍▌▋▊▉█"
        if percentage < 0: percentage = 0
        if percentage > 100: percentage = 100
        full_blocks = int((percentage / 100) * length)
        remainder = (percentage / 100) * length - full_blocks
        idx = int(remainder * len(blocks))
        
        if idx >= len(blocks): idx = len(blocks) - 1
        
        bar = "█" * full_blocks
        if full_blocks < length: bar += blocks[idx] + " " * (length - full_blocks - 1)
        return bar

    @staticmethod
    def check_full_stats(ip, port, user, password):
        client = None
        try:
            client = ServerMonitor.get_ssh_client(ip, port, user, password)
            commands = [
                "grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$4+$5)} END {print usage}'", 
                "free -m | awk 'NR==2{printf \"%.2f\", $3*100/$2 }'", 
                "df -h / | awk 'NR==2{print $5}' | tr -d '%'", 
                "uptime -p", 
                "cat /proc/uptime | awk '{print $1}'", 
                "cat /proc/net/dev | awk 'NR>2 {rx+=$2; tx+=$10} END {print rx+tx}'",
                "who | awk '{print $1 \"_\" $5}'"
            ]
            results = []
            for cmd in commands:
                try:
                    _, stdout, _ = client.exec_command(cmd, timeout=5)
                    out = stdout.read().decode().strip()
                    results.append(out if out else "0")
                except: results.append("0")
            client.close()
            
            try:
                uptime_sec = float(results[4]) if results[4].replace('.','',1).isdigit() else 0
            except ValueError: uptime_sec = 0

            traffic_bytes = int(results[5]) if results[5].isdigit() else 0
            traffic_gb = round(traffic_bytes / (1024**3), 2)
            uptime_str = results[3].replace('up ', '').replace('weeks', 'w').replace('days', 'd').replace('hours', 'h').replace('minutes', 'm')
            
            try: cpu_val = round(float(results[0]), 1)
            except: cpu_val = 0.0
            try: ram_val = round(float(results[1]), 1)
            except: ram_val = 0.0
            try: disk_val = int(results[2])
            except: disk_val = 0
            who_data = results[6].split('\n') if results[6] != "0" else []
            current_sessions = [line.strip().replace('(', '').replace(')', '') for line in who_data if line.strip()]
            return {
                'status': 'Online', 'cpu': cpu_val, 'ram': ram_val, 'disk': disk_val, 
                'uptime_str': uptime_str, 'uptime_sec': uptime_sec, 'traffic_gb': traffic_gb, 
                'ssh_sessions': current_sessions,
                'error': None
            }
        except Exception as e:
            if client: 
                try: client.close()
                except: pass
            return {'status': 'Offline', 'error': str(e)[:50], 'uptime_sec': 0, 'traffic_gb': 0, 'ssh_sessions': []}

    @staticmethod
    def run_remote_command(ip, port, user, password, command, timeout=60):
        client = None
        try:
            client = ServerMonitor.get_ssh_client(ip, port, user, password)
            full_cmd = f"export DEBIAN_FRONTEND=noninteractive; {command}"
            _, stdout, stderr = client.exec_command(full_cmd, timeout=timeout)
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            client.close()
            return True, (out + "\n" + err).strip()
        except Exception as e:
            if client:
                try: client.close()
                except: pass
            return False, str(e)

    @staticmethod
    def install_speedtest(ip, port, user, password):
        cmd = "sudo DEBIAN_FRONTEND=noninteractive apt-get update -y && (sudo DEBIAN_FRONTEND=noninteractive apt-get install -y speedtest-cli || (sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip && pip3 install --upgrade speedtest-cli))"
        return ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=300)

    @staticmethod
    def run_speedtest(ip, port, user, password):
        return ServerMonitor.run_remote_command(ip, port, user, password, "speedtest-cli --simple", timeout=90)

    @staticmethod
    def clear_cache(ip, port, user, password):
        return ServerMonitor.run_remote_command(ip, port, user, password, "sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'", timeout=30)

    # --- تابع جدید پاکسازی دیسک ---
    @staticmethod
    def clean_disk_space(ip, port, user, password):
        try:
            client = ServerMonitor.get_ssh_client(ip, port, user, password)
            
            # 1. محاسبه فضای مصرفی قبل از پاکسازی
            _, stdout, _ = client.exec_command("df / --output=used | tail -n 1")
            start_used = int(stdout.read().decode().strip())

            # 2. اجرای دستورات پاکسازی
            commands = (
                "sudo DEBIAN_FRONTEND=noninteractive apt-get autoremove -y && "
                "sudo DEBIAN_FRONTEND=noninteractive apt-get clean && "
                "sudo journalctl --vacuum-time=3d && " 
                "sudo rm -rf /var/log/*.gz /var/tmp/* /tmp/*"
            )
            
            # اجرا و صبر برای اتمام
            chan = client.get_transport().open_session()
            chan.exec_command(commands)
            chan.recv_exit_status() # این خط صبر می‌کند تا دستور تمام شود
            
            # 3. محاسبه فضای مصرفی بعد از پاکسازی
            _, stdout, _ = client.exec_command("df / --output=used | tail -n 1")
            end_used = int(stdout.read().decode().strip())
            
            client.close()
            
            # محاسبه مقدار آزاد شده
            freed_kb = start_used - end_used
            if freed_kb < 0: freed_kb = 0
            freed_mb = freed_kb / 1024
            
            return True, freed_mb
        except Exception as e:
            return False, str(e)

    @staticmethod
    def set_dns(ip, port, user, password, dns_type):
        dns_map = {
            "google": "nameserver 8.8.8.8\nnameserver 8.8.4.4", 
            "cloudflare": "nameserver 1.1.1.1\nnameserver 1.0.0.1", 
            "shecan": "nameserver 178.22.122.100\nnameserver 185.51.200.2"
        }
        if dns_type not in dns_map: return False, "Invalid DNS"
        return ServerMonitor.run_remote_command(ip, port, user, password, f"echo '{dns_map[dns_type]}' | sudo tee /etc/resolv.conf", timeout=30)

    @staticmethod
    def full_system_update(ip, port, user, password):
        cmd = (
            "sudo DEBIAN_FRONTEND=noninteractive apt-get update -y && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y -o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold' && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get autoremove -y && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get clean"
        )
        return ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=900)

    @staticmethod
    def repo_update(ip, port, user, password):
        cmd = (
            "sudo DEBIAN_FRONTEND=noninteractive apt-get update -y && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y"
        )
        return ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=300)

    @staticmethod
    def check_host_api(target):
        try:
            headers = {'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'}
            url = f"https://check-host.net/check-ping?host={target}&max_nodes=50"
            req = requests.get(url, headers=headers, timeout=10)
            if req.status_code != 200: return False, f"API Error: {req.status_code}"
            request_id = req.json().get('request_id')
            result_url = f"https://check-host.net/check-result/{request_id}"
            poll_data = {}
            for _ in range(8):
                time.sleep(2.5)
                res_req = requests.get(result_url, headers=headers, timeout=10)
                poll_data = res_req.json()
                if isinstance(poll_data, dict):
                    completed = sum(1 for k, v in poll_data.items() if v)
                    if completed >= 10: break
            return True, poll_data
        except Exception as e: return False, str(e)

    @staticmethod
    def format_check_host_results(data):
        if not isinstance(data, dict): return "❌ داده نامعتبر"
        ir_city_map = {
            'ir1': 'Tehran', 'ir-thr': 'Tehran', 'ir-teh': 'Tehran', 
            'ir3': 'Karaj', 'ir-krj': 'Karaj', 'ir4': 'Shiraz', 'ir-shz': 'Shiraz', 
            'ir5': 'Mashhad', 'ir-mhd': 'Mashhad', 'ir6': 'Esfahan', 'ir-ifn': 'Esfahan', 
            'ir2': 'Tabriz', 'ir-tbz': 'Tabriz'
        }
        rows = []
        has_iran = False
        for node, result in data.items():
            if not result or not isinstance(result, list) or len(result) == 0 or not result[0]: continue
            try:
                if node[:2].lower() != 'ir': continue
                has_iran = True
                node_clean = node.split('.')[0].lower()
                city_name = "Tehran"
                for key, val in ir_city_map.items():
                    if key in node_clean:
                        city_name = val
                        break
                location_display = f"🇮🇷 Iran, {city_name}"
                packets = result[0]
                total_packets = len(packets)
                ok_packets = 0
                rtts = []
                for p in packets:
                    if p[0] == "OK":
                        ok_packets += 1
                        rtts.append(p[1] * 1000)
                packet_stat = f"{ok_packets}/{total_packets}"
                if rtts:
                    ping_stat = f"{min(rtts):.0f} / {statistics.mean(rtts):.0f} / {max(rtts):.0f}"
                else: ping_stat = "Timeout"
                line = f"`{location_display.ljust(17)}`|`{packet_stat}`| `{ping_stat}`"
                rows.append(line)
            except Exception as e: continue
        if not has_iran: return "⚠️ هیچ سرور فعالی از ایران یافت نشد."
        return "🌍 **Check-Host (Iran Only)**\n`Location         | Pkts| Latency (m/a/x)`\n" + "─"*48 + "\n" + "\n".join(rows)


def generate_plot(server_name, stats):
    if not stats:
        return None
    try:
        fig = Figure(figsize=(10, 5))
        ax = fig.add_subplot(111)
        
        times = [s['time_str'] for s in stats]
        cpus = [s['cpu'] for s in stats]
        rams = [s['ram'] for s in stats]
        
        ax.plot(times, cpus, label='CPU (%)', color='red', linewidth=2)
        ax.plot(times, rams, label='RAM (%)', color='blue', linewidth=2)
        
        ax.set_title(f"Server Monitor: {server_name} (Last 24h)")
        ax.set_xlabel('Time')
        ax.set_ylabel('Usage %')
        ax.set_ylim(0, 100)
        ax.legend()
        ax.grid(True, linestyle='--', alpha=0.6)
        
        if len(times) > 10:
            step = max(1, len(times)//8)
            ax.set_xticks(range(0, len(times), step))
            ax.set_xticklabels(times[::step], rotation=45)
        
        fig.tight_layout()
        buf = io.BytesIO()
        FigureCanvasAgg(fig).print_png(buf)
        buf.seek(0)
        return buf
    except Exception as e:
        logger.error(f"Plot error: {e}")
        return None


# ==============================================================================
# 🎮 UI HELPERS & GENERAL HANDLERS
# ==============================================================================
def get_cancel_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 انصراف", callback_data='cancel_flow')]])

async def safe_edit_message(update: Update, text, reply_markup=None, parse_mode='Markdown'):
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif update.message:
            await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest: pass
    except Exception as e: logger.error(f"Edit Error: {e}")


async def run_background_ssh_task(context: ContextTypes.DEFAULT_TYPE, chat_id, func, *args):
    loop = asyncio.get_running_loop()
    try:
        ok, output = await loop.run_in_executor(None, func, *args)
        clean_out = html.escape(str(output))
        if len(clean_out) > 3500: 
            clean_out = clean_out[:3500] + "\n... (Output Truncated)"
        
        status_icon = "✅ عملیات با موفقیت انجام شد." if ok else "❌ عملیات با خطا مواجه شد."
        msg_text = (
            f"{status_icon}\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"<pre>{clean_out}</pre>"
        )
        await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode='HTML')
        
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ خطای غیرمنتظره در عملیات پس‌زمینه:\n{e}")    

async def cancel_handler_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: 
        try: await update.callback_query.answer()
        except: pass
    await safe_edit_message(update, "🚫 **عملیات لغو شد.**")
    await asyncio.sleep(1)
    await start(update, context)
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(context.error, Conflict):
        logger.critical("⚠️ Conflict detected: Another instance is running. Shutting down.")
        os._exit(1) 
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ خطای داخلی سیستم. لطفاً دوباره تلاش کنید.")
        except: pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    username = update.effective_user.username or "ندارد"
    context.user_data.clear()
    loop = asyncio.get_running_loop()

    # --- بررسی دعوت ---
    args = context.args  # پارامترهای لینک (مثلا /start 12345)
    inviter_id = 0
    
    # چک می‌کنیم کاربر قبلاً عضو نبوده باشد
    existing_user = await loop.run_in_executor(None, db.get_user, user_id)
    is_new_user = False if existing_user else True

    if is_new_user and args and args[0].isdigit():
        possible_inviter = int(args[0])
        # کاربر نمی‌تواند خودش را دعوت کند
        if possible_inviter != user_id:
            # چک می‌کنیم معرف وجود دارد؟
            inviter_exists = await loop.run_in_executor(None, db.get_user, possible_inviter)
            if inviter_exists:
                inviter_id = possible_inviter

    # ثبت نام کاربر (با آیدی معرف اگر وجود داشت)
    await loop.run_in_executor(None, db.add_or_update_user, user_id, full_name, inviter_id)
    
    # --- سیستم جایزه دهی ---
    if is_new_user:
        # 1. اطلاع به ادمین کل
        try:
            admin_msg = f"🔔 **کاربر جدید!**\n👤 {full_name}\n🆔 `{user_id}`\n🔗 دعوت شده توسط: `{inviter_id if inviter_id else 'لینک مستقیم'}`"
            await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=admin_msg, parse_mode='Markdown')
        except: pass

        # 2. اگر معرف داشت، جایزه را اعمال کن
        if inviter_id != 0:
            ok, new_lim, new_exp = await loop.run_in_executor(None, db.apply_referral_reward, inviter_id)
            if ok:
                try:
                    # پیام تبریک به معرف
                    await context.bot.send_message(
                        chat_id=inviter_id,
                        text=(
                            f"🎉 **تبریک! یک زیرمجموعه جدید جذب کردید.**\n\n"
                            f"👤 کاربر: {full_name}\n"
                            f"🎁 **پاداش شما:**\n"
                            f"➕ 1 عدد به ظرفیت سرور (مجموع: {new_lim})\n"
                            f"➕ 10 روز به اعتبار اشتراک (تاریخ جدید: {new_exp})"
                        )
                    )
                except: pass

        # 3. پیام خوش‌آمدگویی
        await update.message.reply_text(
            f"🎉 **سلام {full_name} عزیز، خوش اومدی!** \n\n"
            "✅ حساب شما ایجاد شد:\n"
            "🔹 **اعتبار اولیه:** 60 روز\n"
            "🔹 **ظرفیت سرور:** 2 عدد\n\n"
            "می‌تونی با دعوت دوستانت، این محدودیت‌ها رو رایگان افزایش بدی! 🚀",
            parse_mode='Markdown'
        )

    # --- ادامه کد استارت مثل قبل ---
    has_access, msg = await loop.run_in_executor(None, db.check_access, user_id)
    if not has_access:
        await update.effective_message.reply_text(f"⛔️ دسترسی مسدود است: {msg}")
        return
    
    remaining = f"{msg} روز" if isinstance(msg, int) else "♾ نامحدود"
    
    # منوی اصلی با دکمه‌های جدید کیف پول و دعوت
    kb = [
        [InlineKeyboardButton("👤 حساب کاربری", callback_data='user_profile'), InlineKeyboardButton("💰 کیف پول & خرید", callback_data='wallet_menu')],
        [InlineKeyboardButton("🤝 دعوت از دوستان (رایگان)", callback_data='referral_menu')], 
        [InlineKeyboardButton("📂 گروه‌بندی", callback_data='groups_menu'), InlineKeyboardButton("➕ سرور جدید", callback_data='add_server')],
        [InlineKeyboardButton("📋 لیست سرورها", callback_data='list_groups_for_servers'), InlineKeyboardButton("📊 داشبورد شبکه", callback_data='status_dashboard')],
        [InlineKeyboardButton("🌍 تنظیمات همگانی", callback_data='global_ops_menu'), InlineKeyboardButton("⚙️ تنظیمات", callback_data='settings_menu')]
    ]
    if user_id == SUPER_ADMIN_ID: 
        kb.insert(0, [InlineKeyboardButton("🤖 مدیریت ربات", callback_data='admin_panel_main')])

    txt = (
        f"👋 **درود {full_name} عزیز**\n"
        f"🦇 **Sonar Radar Ultra Pro**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📅 اعتبار: `{remaining}`\n"
        f"🔰 گزینه مورد نظر را انتخاب کنید:"
    )
    
    if update.callback_query:
        await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return ConversationHandler.END
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE): await start(update, context)
async def user_profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: 
        try: await update.callback_query.answer()
        except: pass
    uid = update.effective_user.id
    user = db.get_user(uid)
    
    if not user:
        await safe_edit_message(update, "❌ کاربر یافت نشد.")
        return

    try:
        join_date = datetime.strptime(user['added_date'], '%Y-%m-%d %H:%M:%S')
        j_join = jdatetime.date.fromgregorian(date=join_date.date())
        join_str = f"{j_join.day} {jdatetime.date.j_months_fa[j_join.month-1]} {j_join.year}"
    except:
        join_str = "نامشخص"

    access, time_left = db.check_access(uid)
    if uid == SUPER_ADMIN_ID:
        sub_type = "👑 مدیریت کل (God Mode)"
        expiry_str = "♾ نامحدود"
    else:
        sub_type = "💎 پریمیوم (VIP)" if user['server_limit'] > 10 else "👤 عادی (Normal)"
        expiry_str = f"{time_left} روز مانده" if isinstance(time_left, int) else "نامحدود"

    servers = db.get_all_user_servers(uid)
    srv_count = len(servers)
    active_srv = sum(1 for s in servers if s['is_active'])

    txt = (
        f"👤 **پروفایل کاربری شما**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🏷 **نام:** `{user['full_name']}`\n"
        f"🆔 **آیدی عددی:** `{user['user_id']}`\n"
        f"📅 **تاریخ عضویت:** `{join_str}`\n\n"
        
        f"💳 **نوع اشتراک:** {sub_type}\n"
        f"⏳ **اعتبار باقی‌مانده:** `{expiry_str}`\n"
        f"🔢 **سقف مجاز سرور:** `{user['server_limit']} عدد`\n\n"
        
        f"🖥 **وضعیت سرورها:**\n"
        f"   ├ 🟢 فعال: `{active_srv}`\n"
        f"   └ ⚪️ کل ثبت شده: `{srv_count}`"
    )

    kb = [
        [InlineKeyboardButton("🔑 دریافت توکن پنل وب (Web Token)", callback_data='gen_web_token')],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data='main_menu')]
    ]
    
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def web_token_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.answer("🚧 پنل تحت وب در حال توسعه است.\nبه زودی این قابلیت فعال می‌شود!", show_alert=True)
    except: pass


# ==============================================================================
# 👑 ADMIN PANEL HANDLERS
# ==============================================================================
async def admin_panel_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID: return
    
    users_count = len(db.get_all_users())
    with db.get_connection() as conn:
        total_servers = len(conn.execute('SELECT id FROM servers').fetchall())
    
    kb = [
        [InlineKeyboardButton("👥 مدیریت کاربران", callback_data='admin_users_page_1')],
        [InlineKeyboardButton("➕ افزودن دستی کاربر", callback_data='add_new_admin')],
        [InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data='admin_broadcast_start')],
        [InlineKeyboardButton("🔎 جستجوی کاربر", callback_data='admin_search_start'), InlineKeyboardButton("📄 لیست متنی", callback_data='admin_users_text')],
        [InlineKeyboardButton("📥 دریافت بکاپ", callback_data='admin_backup_get'), InlineKeyboardButton("📤 بازنشانی بکاپ", callback_data='admin_backup_restore_start')],
        [InlineKeyboardButton("🔑 دریافت کلید (Backup Key)", callback_data='admin_key_backup_get'), InlineKeyboardButton("🗝 بازیابی کلید (Restore Key)", callback_data='admin_key_restore_start')
        ],
        [InlineKeyboardButton("💳 تنظیمات پرداخت و ولت", callback_data='admin_pay_settings')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='main_menu')]
    ]
    
    txt = (
        f"🤖 **پنل مدیریت ربات**\n\n"
        f"📊 **آمار کلی:**\n"
        f"👤 کل کاربران: `{users_count}`\n"
        f"🖥 کل سرورهای ثبت شده: `{total_servers}`"
    )
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = int(update.callback_query.data.split('_')[-1])
    users, total_count = db.get_all_users_paginated(page, 5)
    total_pages = (total_count + 4) // 5
    
    txt = f"👥 **لیست کاربران (صفحه {page} از {total_pages})**\nتعداد کل: `{total_count}`\n➖➖➖➖➖➖"
    
    kb = []
    for u in users:
        status = "🔴" if u['is_banned'] else "🟢"
        name = u['full_name'] if u['full_name'] else "Unknown"
        kb.append([InlineKeyboardButton(f"{status} {name} | {u['user_id']}", callback_data=f"admin_u_manage_{u['user_id']}")])
    
    nav_btns = []
    if page > 1: nav_btns.append(InlineKeyboardButton("◀️ قبلی", callback_data=f'admin_users_page_{page-1}'))
    if page < total_pages: nav_btns.append(InlineKeyboardButton("بعدی ▶️", callback_data=f'admin_users_page_{page+1}'))
    
    if nav_btns: kb.append(nav_btns)
    kb.append([InlineKeyboardButton("🔙 بازگشت به مدیریت", callback_data='admin_panel_main')])
    
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def admin_user_manage(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id=None):
    if not user_id and update.callback_query:
        data = update.callback_query.data
        if "manage_" in data:
            try:
                user_id = int(data.split('_')[-1])
            except: pass
    
    if not user_id:
        await safe_edit_message(update, "❌ خطای سیستمی: آیدی کاربر پیدا نشد.")
        return

    user = db.get_user(user_id)
    if not user:
        await safe_edit_message(update, "❌ کاربر در دیتابیس یافت نشد.")
        return

    plan_txt = "💎 پریمیوم (VIP)" if user['plan_type'] == 1 else "👤 عادی (Normal)"
    plan_action = "تبدیل به عادی ⬇️" if user['plan_type'] == 1 else "ارتقا به پریمیوم 💎"
    ban_status = "🔴 مسدود" if user['is_banned'] else "🟢 فعال"
    
    txt = (
        f"👤 **مدیریت کاربر:** `{user['full_name']}`\n"
        f"🆔 آیدی: `{user['user_id']}`\n"
        f"💳 **نوع اشتراک:** {plan_txt}\n"
        f"📆 انقضا: `{user['expiry_date']}`\n"
        f"📡 وضعیت: {ban_status}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📊 سرورها: `{len(db.get_all_user_servers(user_id))}` / `{user['server_limit']}`"
    )
    
    kb = [
        [InlineKeyboardButton("➕ تمدید (30 روز)", callback_data=f'admin_u_addtime_{user_id}'), InlineKeyboardButton("📅 تنظیم زمان دستی", callback_data=f'admin_u_settime_{user_id}')],
        [InlineKeyboardButton(plan_action, callback_data=f'admin_u_toggleplan_{user_id}')], 
        [InlineKeyboardButton("🔢 تغییر لیمیت سرور", callback_data=f'admin_u_limit_{user_id}')],
        [InlineKeyboardButton("مسدود/رفع مسدود", callback_data=f'admin_u_ban_{user_id}'), InlineKeyboardButton("🗑 حذف", callback_data=f'admin_u_del_{user_id}')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='admin_users_page_1')]
    ]
    
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def admin_user_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    action = data.split('_')[2]
    target_id = int(data.split('_')[3])
    
    if action == 'ban':
        new_state = db.toggle_ban_user(target_id)
        msg = "کاربر مسدود شد." if new_state else "کاربر فعال شد."
        try: await update.callback_query.answer(msg)
        except: pass
        await admin_user_manage(update, context, user_id=target_id)
        
    elif action == 'del':
        db.remove_user(target_id)
        try: await update.callback_query.answer("کاربر حذف شد.")
        except: pass
        await admin_users_list(update, context)
        
    elif action == 'addtime':
        db.add_or_update_user(target_id, days=30)
        try: await update.callback_query.answer("30 روز تمدید شد.")
        except: pass
        await admin_user_manage(update, context, user_id=target_id)

    elif action == 'limit':
        context.user_data['target_uid'] = target_id
        await safe_edit_message(update, "🔢 **تعداد جدید محدودیت سرور را وارد کنید:**", reply_markup=get_cancel_markup())
        return ADMIN_SET_LIMIT
        
    elif action == 'settime':
        context.user_data['target_uid'] = target_id
        await safe_edit_message(update, "📅 **تعداد روز اعتبار را وارد کنید (مثلا 60):**", reply_markup=get_cancel_markup())
        return ADMIN_SET_TIME_MANUAL
    elif action == 'toggleplan':
        new_plan = db.toggle_user_plan(target_id)
        msg = "✅ کاربر به پریمیوم ارتقا یافت (لیمیت: 10)" if new_plan == 1 else "⬇️ کاربر به عادی تغییر یافت (لیمیت: 2)"
        try: await update.callback_query.answer(msg, show_alert=True)
        except: pass
        await admin_user_manage(update, context, user_id=target_id)

async def admin_set_limit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lim = int(update.message.text)
        target_id = context.user_data.get('target_uid')
        db.update_user_limit(target_id, lim)
        await update.message.reply_text(f"✅ محدودیت سرور به {lim} تغییر یافت.")
        await admin_user_manage(update, context, user_id=target_id)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ لطفاً فقط عدد انگلیسی وارد کنید.")
        return ADMIN_SET_LIMIT

async def admin_set_days_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text)
        target_id = context.user_data.get('target_uid')
        db.add_or_update_user(target_id, days=days)
        await update.message.reply_text(f"✅ اعتبار کاربر {days} روز تمدید شد.")
        await admin_user_manage(update, context, user_id=target_id)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ لطفاً فقط عدد انگلیسی وارد کنید.")
        return ADMIN_SET_TIME_MANUAL

async def admin_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "🔎 **آیدی عددی کاربر را ارسال کنید:**", reply_markup=get_cancel_markup())
    return ADMIN_SEARCH_USER

async def admin_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(update.message.text)
        user = db.get_user(tid)
        if user:
            await admin_user_manage(update, context, user_id=tid)
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ کاربر یافت نشد. مجدد تلاش کنید یا انصراف دهید.")
            return ADMIN_SEARCH_USER
    except:
        await update.message.reply_text("❌ فرمت نامعتبر.")
        return ADMIN_SEARCH_USER

async def admin_users_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_users()
    txt = "📋 **لیست کل کاربران:**\n\n"
    for u in users:
        txt += f"🆔 {u['user_id']} | 👤 {u['full_name']} | 📅 Exp: {u['expiry_date']}\n"
    
    if len(txt) > 4000:
        with open("users_list.txt", "w", encoding='utf-8') as f: f.write(txt)
        try: await update.callback_query.message.reply_document(document=open("users_list.txt", "rb"), caption="لیست کاربران")
        except: pass
        os.remove("users_list.txt")
    else:
        await update.callback_query.message.reply_text(txt)

# --- Backup & Restore ---
async def admin_backup_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.answer("در حال ارسال فایل...")
    except: pass
    with db.get_connection() as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL);")
    await update.callback_query.message.reply_document(document=open(DB_NAME, 'rb'), caption=f"📦 Backup: {get_jalali_str()}")

async def admin_backup_restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "⚠️ **هشدار:** با آپلود فایل جدید، دیتابیس فعلی حذف و جایگزین می‌شود.\n\n📂 **فایل .db خود را ارسال کنید:**", reply_markup=get_cancel_markup())
    return ADMIN_RESTORE_DB

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(
        update, 
        "📢 **لطفاً پیام خود را ارسال کنید:**\n\n"
        "می‌توانید متن، عکس، ویدیو یا پیام فوروارد شده بفرستید.\n"
        "این پیام برای **تمام کاربران** ربات ارسال خواهد شد.",
        reply_markup=get_cancel_markup()
    )
    return GET_BROADCAST_MSG

async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_users()
    total = len(users)
    success = 0
    blocked = 0
    
    status_msg = await update.message.reply_text(f"⏳ در حال ارسال به {total} کاربر...")
    
    for user in users:
        try:
            await update.message.copy(chat_id=user['user_id'])
            success += 1
        except Exception:
            blocked += 1
        
        if success % 20 == 0:
            await asyncio.sleep(1)

    await status_msg.edit_text(
        f"✅ **پیام همگانی ارسال شد.**\n\n"
        f"👥 کل کاربران: `{total}`\n"
        f"✅ موفق: `{success}`\n"
        f"🚫 ناموفق (بلاک/حذف): `{blocked}`"
    )
    
    await admin_panel_main(update, context)
    return ConversationHandler.END

async def admin_backup_restore_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith('.db'):
        await update.message.reply_text("❌ فرمت فایل باید .db باشد.")
        return ADMIN_RESTORE_DB
    
    temp_name = "temp_restore.db"
    f = await doc.get_file()
    await f.download_to_drive(temp_name)
    
    try:
        if os.path.exists(DB_NAME):
            os.remove(DB_NAME)
        os.rename(temp_name, DB_NAME)
        
        # Re-initialize to ensure tables exist if backup was old
        db.init_db()
        
        await update.message.reply_text("✅ دیتابیس با موفقیت بازنشانی شد.")
        await start(update, context)
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در بازنشانی: {e}")
    
    return ConversationHandler.END
# --- SECRET KEY HANDLERS ---

# --- SECRET KEY HANDLERS ---
async def admin_key_backup_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(KEY_FILE):
        try: await update.callback_query.answer("❌ فایل کلید یافت نشد!", show_alert=True)
        except: pass
        return
    await update.callback_query.message.reply_document(
        document=open(KEY_FILE, 'rb'), 
        caption="🔑 **فایل کلید امنیتی (Secret Key)**\n⚠️ این فایل را برای روز مبادا نگه دارید."
    )

async def admin_key_restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "🗝 **لطفاً فایل secret.key را ارسال کنید:**", reply_markup=get_cancel_markup())
    return ADMIN_RESTORE_KEY

async def admin_key_restore_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    f = await update.message.document.get_file()
    await f.download_to_drive("temp_key.key")
    if os.path.exists(KEY_FILE): os.remove(KEY_FILE)
    os.rename("temp_key.key", KEY_FILE)
    global sec; sec = Security() # Reload Key
    await update.message.reply_text("✅ **کلید امنیتی بازیابی شد!**")
    await start(update, context)
    return ConversationHandler.END
# --- Add New User Handlers ---
async def add_new_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.answer()
    except: pass
    await safe_edit_message(update, "👤 **شناسه عددی (User ID) کاربر را وارد کنید:**", reply_markup=get_cancel_markup())
    return ADD_ADMIN_ID

async def get_new_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_uid'] = int(update.message.text)
        await update.message.reply_text("📅 **تعداد روز اعتبار:**", reply_markup=get_cancel_markup())
        return ADD_ADMIN_DAYS
    except: 
        await update.message.reply_text("❌ فقط عدد وارد کنید.")
        return ADD_ADMIN_ID

async def get_new_user_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db.add_or_update_user(context.user_data['new_uid'], full_name="User (Manual)", days=int(update.message.text))
        await update.message.reply_text("✅ کاربر افزوده شد.")
        await start(update, context)
        return ConversationHandler.END
    except: 
        await update.message.reply_text("❌ فقط عدد وارد کنید.")
        return ADD_ADMIN_DAYS
# ==============================================================================
# 💳 PAYMENT SETTINGS (ADMIN)
# ==============================================================================

async def admin_payment_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی مدیریت روش‌های پرداخت"""
    methods = db.get_payment_methods()
    
    txt = "💳 **مدیریت روش‌های پرداخت**\n\nلیست روش‌های فعال:\n"
    if not methods:
        txt += "❌ هیچ روش پرداختی تعریف نشده است."
    
    kb = []
    for m in methods:
        icon = "🏦" if m['type'] == 'card' else "💎"
        kb.append([InlineKeyboardButton(f"🗑 حذف {icon} {m['network']}", callback_data=f'del_pay_method_{m["id"]}')])
    
    kb.append([InlineKeyboardButton("➕ افزودن کارت بانکی", callback_data='add_pay_method_card')])
    kb.append([InlineKeyboardButton("➕ افزودن ولت کریپتو", callback_data='add_pay_method_crypto')])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data='admin_panel_main')])
    
    if update.callback_query:
        await safe_edit_message(update, txt + "\n\n👇 برای حذف روی دکمه‌ها بزنید.", reply_markup=InlineKeyboardMarkup(kb))

async def delete_payment_method_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p_id = int(update.callback_query.data.split('_')[3])
    db.delete_payment_method(p_id)
    await update.callback_query.answer("🗑 حذف شد.")
    await admin_payment_settings(update, context)

# --- Add New Method Flow ---
async def add_pay_method_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p_type = update.callback_query.data.split('_')[3] # card or crypto
    context.user_data['new_pay_type'] = p_type
    
    if p_type == 'card':
        msg = "🏦 **نام بانک را وارد کنید:**\n(مثال: بانک ملت)"
    else:
        msg = "💎 **نام ارز و شبکه را وارد کنید:**\n(مثال: USDT - TRC20 یا TON)"
        
    await safe_edit_message(update, msg, reply_markup=get_cancel_markup())
    return ADD_PAY_NET

async def get_pay_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_pay_net'] = update.message.text
    p_type = context.user_data['new_pay_type']
    
    if p_type == 'card':
        msg = "🔢 **شماره کارت را وارد کنید:**"
    else:
        msg = "🔗 **آدرس ولت (Wallet Address) را ارسال کنید:**"
        
    await update.message.reply_text(msg, reply_markup=get_cancel_markup())
    return ADD_PAY_ADDR

async def get_pay_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_pay_addr'] = update.message.text
    
    if context.user_data['new_pay_type'] == 'card':
        msg = "👤 **نام صاحب حساب را وارد کنید:**"
    else:
        # برای کریپتو معمولا صاحب حساب لازم نیست، اما برای یکدستی دیتابیس چیزی میگیریم
        msg = "📝 **توضیحات کوتاه یا نام ولت:**\n(مثال: ولت اصلی)"
        
    await update.message.reply_text(msg, reply_markup=get_cancel_markup())
    return ADD_PAY_HOLDER

async def get_pay_holder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    holder = update.message.text
    data = context.user_data
    
    db.add_payment_method(data['new_pay_type'], data['new_pay_net'], data['new_pay_addr'], holder)
    
    await update.message.reply_text("✅ **روش پرداخت با موفقیت اضافه شد.**")
    # بازگشت به منوی پرداخت
    class FakeUpdate:
        def __init__(self, u): self.callback_query = u
    
    # اینجا یک تریک میزنیم که برگردیم به منو، اما چون مسیج هندلر هستیم باید دستی انجام بدیم
    # ساده تر: لینک به پنل ادمین
    kb = [[InlineKeyboardButton("بازگشت به مدیریت پرداخت", callback_data='admin_pay_settings')]]
    await update.message.reply_text("جهت مشاهده لیست، دکمه زیر را بزنید:", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ==============================================================================
# 🛠 SERVER & GROUP MANAGEMENT
# ==============================================================================
async def groups_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = db.get_user_groups(update.effective_user.id)
    kb = [[InlineKeyboardButton(f"🗑 {g['name']}", callback_data=f'delgroup_{g["id"]}')] for g in groups]
    kb.append([InlineKeyboardButton("➕ گروه جدید", callback_data='add_group')])
    kb.append([InlineKeyboardButton("🔙", callback_data='main_menu')])
    await safe_edit_message(update, "📂 Groups:", reply_markup=InlineKeyboardMarkup(kb))

async def add_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "📝 Name:", reply_markup=get_cancel_markup())
    return GET_GROUP_NAME

async def get_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.add_group(update.effective_user.id, update.message.text)
    await start(update, context)
    return ConversationHandler.END

async def delete_group_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.delete_group(int(update.callback_query.data.split('_')[1]), update.effective_user.id)
    await groups_menu(update, context)

async def add_server_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db.get_user(update.effective_user.id)
    srv_count = len(db.get_all_user_servers(update.effective_user.id))
    if update.effective_user.id != SUPER_ADMIN_ID and srv_count >= user['server_limit']:
        await update.effective_message.reply_text("⛔️ **شما به سقف مجاز افزودن سرور رسیده‌اید.**")
        return ConversationHandler.END
    await safe_edit_message(update, "🏷 **نام سرور را وارد کنید:**", reply_markup=get_cancel_markup())
    return GET_NAME
# --- تعریف State جدید برای انتخاب روش ---
# --- تعریف Stateهای جدید برای افزودن سرور ---
SELECT_ADD_METHOD, GET_LINEAR_DATA = range(100, 102)

async def add_server_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی انتخاب روش افزودن سرور"""
    user = db.get_user(update.effective_user.id)
    srv_count = len(db.get_all_user_servers(update.effective_user.id))
    
    # چک کردن محدودیت کاربر
    if update.effective_user.id != SUPER_ADMIN_ID and srv_count >= user['server_limit']:
        await safe_edit_message(update, "⛔️ **شما به سقف مجاز افزودن سرور رسیده‌اید.**")
        return ConversationHandler.END

    kb = [
        [InlineKeyboardButton("🧙‍♂️ مرحله به مرحله (ویزارد)", callback_data='add_method_step')],
        [InlineKeyboardButton("⚡️ افزودن سریع (خطی/چندگانه)", callback_data='add_method_linear')],
        [InlineKeyboardButton("🔙 انصراف", callback_data='cancel_flow')]
    ]
    
    txt = (
        "➕ **افزودن سرور جدید**\n\n"
        "لطفاً روش مورد نظر خود را انتخاب کنید:\n\n"
        "1️⃣ **مرحله به مرحله:** ربات سوال می‌پرسد و شما پاسخ می‌دهید.\n"
        "2️⃣ **سریع (خطی):** تمام اطلاعات را در یک پیام می‌فرستید (مناسب برای افزودن همزمان چند سرور)."
    )
    
    if update.callback_query:
        await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        
    return SELECT_ADD_METHOD

async def add_server_step_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع روش قدیمی (مرحله به مرحله)"""
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("🏷 **نام سرور را وارد کنید:**", reply_markup=get_cancel_markup())
    return GET_NAME

async def add_server_linear_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع روش خطی (فرمت جدید)"""
    await update.callback_query.answer()
    txt = (
        "⚡️ **افزودن سریع سرورها**\n\n"
        "لطفاً مشخصات سرورها را به صورت **5 خطی** ارسال کنید.\n"
        "هر سرور باید دقیقاً در 5 خط زیر هم باشد:\n"
        "1. نام سرور\n"
        "2. آی‌پی\n"
        "3. پورت\n"
        "4. یوزرنیم\n"
        "5. پسورد\n\n"
        "⚠️ **نکته:** اگر چند سرور دارید، بلافاصله بعد از پسورد اولی، اطلاعات سرور دوم را شروع کنید.\n\n"
        "💡 **مثال:**\n"
        "`Server A`\n`192.168.1.1`\n`22`\n`root`\n`Pass123`\n"
        "`Server B`\n`45.33.22.11`\n`2244`\n`admin`\n`Secr3t`\n\n"
        "👇 اطلاعات را ارسال کنید:"
    )
    await update.callback_query.message.reply_text(txt, reply_markup=get_cancel_markup(), parse_mode='Markdown')
    return GET_LINEAR_DATA

async def process_linear_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش متن خطی با فرمت ۵ خطی (نسخه اصلاح شده و بدون باگ)"""
    text = update.message.text
    # حذف خطوط خالی اضافی
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    uid = update.effective_user.id
    user = db.get_user(uid)
    limit = user['server_limit']
    current_count = len(db.get_all_user_servers(uid))
    
    success = 0
    failed = 0
    report = []
    
    # دریافت IP ربات
    try:
        bot_ip = await asyncio.get_running_loop().run_in_executor(None, ServerMonitor.get_bot_public_ip)
    except:
        bot_ip = None

    msg = await update.message.reply_text("⏳ **در حال پردازش و تست اتصال...**")

    # بررسی اینکه تعداد خطوط مضربی از ۵ باشد
    if len(lines) % 5 != 0:
        await msg.edit_text(
            f"❌ **فرمت ارسال اشتباه است!**\n\n"
            f"تعداد خطوط باید مضربی از ۵ باشد (نام، آی‌پی، پورت، یوزر، پسورد).\n"
            f"شما {len(lines)} خط فرستادید.\n\n"
            "لطفاً اصلاح کنید و مجدد ارسال نمایید."
        )
        return GET_LINEAR_DATA

    loop = asyncio.get_running_loop()

    # پردازش ۵ خط به ۵ خط
    for i in range(0, len(lines), 5):
        if uid != SUPER_ADMIN_ID and (current_count + success) >= limit:
            report.append(f"⛔️ محدودیت پر شد! (سرور {lines[i]} نادیده گرفته شد)")
            failed += 1
            continue

        name = lines[i]
        ip = lines[i+1]
        port_str = lines[i+2]
        username = lines[i+3]
        password = lines[i+4]
        
        if not port_str.isdigit():
            report.append(f"⚠️ پورت نامعتبر برای {name}: `{port_str}`")
            failed += 1
            continue
            
        port = int(port_str)
        
        # تست اتصال
        res = await loop.run_in_executor(
            None, ServerMonitor.check_full_stats, ip, port, username, password
        )
        
        if res['status'] == 'Online':
            try:
                data = {
                    'name': name, 'ip': ip, 'port': port, 
                    'username': username, 'password': sec.encrypt(password),
                    'expiry_date': None
                }
                
                db.add_server(uid, 0, data)
                
                # ✅ اصلاح بخش وایت‌لیست (رفع ارور Future pending)
                if bot_ip:
                    async def do_whitelist_bg():
                        await loop.run_in_executor(None, ServerMonitor.whitelist_bot_ip, ip, port, username, password, bot_ip)
                    # تسک را بدون await اجرا می‌کنیم تا سرعت کم نشود و ارور ندهد
                    asyncio.create_task(do_whitelist_bg())
                
                report.append(f"✅ **{name}**: افزوده شد.")
                success += 1
            except Exception as e:
                # اگر واقعاً دیتابیس ارور داد (مثلا نام تکراری)
                report.append(f"❌ خطا در ذخیره {name}: {e}")
                failed += 1
        else:
            report.append(f"🔴 عدم اتصال {name}: `{res['error']}`")
            failed += 1

    final_txt = (
        f"📊 **نتیجه عملیات:**\n"
        f"✅ موفق: `{success}` | ❌ ناموفق: `{failed}`\n"
        f"➖➖➖➖➖➖➖➖\n" + 
        "\n".join(report)
    )
    
    await msg.edit_text(final_txt, parse_mode='Markdown')
    await asyncio.sleep(3)
    await start(update, context)
    return ConversationHandler.END
async def get_srv_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['srv'] = {'name': update.message.text}
    await update.message.reply_text("🌐 **آدرس IP سرور را وارد کنید:**", reply_markup=get_cancel_markup(), parse_mode='Markdown')
    return GET_IP

async def get_srv_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['srv']['ip'] = update.message.text
    await update.message.reply_text("🔌 **پورت SSH را وارد کنید:**", reply_markup=get_cancel_markup(), parse_mode='Markdown')
    return GET_PORT

async def get_srv_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data['srv']['port'] = int(update.message.text)
    except: 
        await update.message.reply_text("❌ فقط عدد وارد کنید.")
        return GET_PORT
    await update.message.reply_text("👤 **نام کاربری (Username) را وارد کنید:**", reply_markup=get_cancel_markup(), parse_mode='Markdown')
    return GET_USER

async def get_srv_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['srv']['username'] = update.message.text
    await update.message.reply_text("🔑 **رمز عبور (Password) را وارد کنید:**", reply_markup=get_cancel_markup(), parse_mode='Markdown')
    return GET_PASS

async def get_srv_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['srv']['password'] = sec.encrypt(update.message.text)
    await update.message.reply_text(
        "📅 **مهلت انقضای سرور چند روز دیگر است؟**\n\n"
        "🔢 عدد وارد کنید (مثلاً `30` برای یک ماه)\n"
        "یا عدد `0` را وارد کنید اگر نامحدود است.",
        reply_markup=get_cancel_markup(), parse_mode='Markdown'
    )
    return GET_EXPIRY

async def get_srv_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text)
        if days > 0:
            expiry_dt = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
            context.user_data['srv']['expiry_date'] = expiry_dt
            msg = f"✅ تاریخ انقضا تنظیم شد: {days} روز دیگر."
        else:
            context.user_data['srv']['expiry_date'] = None
            msg = "♾ سرور به عنوان نامحدود ثبت شد."
    except:
        await update.message.reply_text("❌ لطفاً فقط عدد وارد کنید (مثلا 30).")
        return GET_EXPIRY

    await update.message.reply_text(f"{msg}\n\n📂 **حالا سرور در کدام پوشه ذخیره شود؟**", reply_markup=InlineKeyboardMarkup(await get_group_keyboard(update.effective_user.id)), parse_mode='Markdown')
    return SELECT_GROUP

async def get_group_keyboard(uid):
    groups = db.get_user_groups(uid)
    kb = [[InlineKeyboardButton(f"📁 {g['name']}", callback_data=str(g['id']))] for g in groups]
    kb.append([InlineKeyboardButton("فایل اصلی (بدون گروه)", callback_data="0")])
    kb.append([InlineKeyboardButton("🔙 انصراف", callback_data="cancel_flow")])
    return kb

async def select_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query.data == 'cancel_flow': return await cancel_handler_func(update, context)
    await safe_edit_message(update, "⚡️ **در حال تست اتصال به سرور... (لطفاً صبر کنید)**")
    data = context.user_data['srv']
    res = await asyncio.get_running_loop().run_in_executor(None, ServerMonitor.check_full_stats, data['ip'], data['port'], data['username'], sec.decrypt(data['password']))
    if res['status'] == 'Online':
        try:
            db.add_server(update.effective_user.id, int(update.callback_query.data), data)
            try:
                bot_ip = ServerMonitor.get_bot_public_ip()
                if bot_ip:
                    asyncio.create_task(asyncio.get_running_loop().run_in_executor(
                        None, 
                        ServerMonitor.whitelist_bot_ip, 
                        data['ip'], data['port'], data['username'], sec.decrypt(data['password']), bot_ip
                    ))
            except Exception as e:
                logger.error(f"Whitelist Error on Add: {e}")
            await update.callback_query.message.reply_text("✅ **اتصال موفق! سرور ذخیره شد.**", parse_mode='Markdown')
        except Exception as e: await update.callback_query.message.reply_text(f"❌ خطا: {e}")
    else:
        await update.callback_query.message.reply_text(f"❌ **عدم اتصال به سرور!**\n\n⚠️ خطا: `{res['error']}`", parse_mode='Markdown')
    await start(update, context)
    return ConversationHandler.END

async def list_groups_for_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.answer()
    except: pass
    groups = db.get_user_groups(update.effective_user.id)
    kb = [[InlineKeyboardButton("🔗 همه سرورها (یکجا)", callback_data='list_all')]] + [[InlineKeyboardButton(f"📁 {g['name']}", callback_data=f'listsrv_{g["id"]}')] for g in groups]
    kb.append([InlineKeyboardButton("📄 سرورهای بدون گروه", callback_data='listsrv_0')])
    kb.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data='main_menu')])
    await safe_edit_message(update, "🗂 **پوشه مورد نظر را انتخاب کنید:**", reply_markup=InlineKeyboardMarkup(kb))

async def show_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.answer()
    except: pass
    uid, data = update.effective_user.id, update.callback_query.data
    servers = db.get_all_user_servers(uid) if data == 'list_all' else db.get_servers_by_group(uid, int(data.split('_')[1]))
    if not servers: 
        try: await update.callback_query.answer("⚠️ این پوشه خالی است!", show_alert=True)
        except: pass
        return
    kb = []
    for s in servers:
        status_icon = "🟢" if s['last_status'] == 'Online' else "🔴"
        kb.append([InlineKeyboardButton(f"{status_icon} {s['name']}  |  {s['ip']}", callback_data=f'detail_{s["id"]}')])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data='list_groups_for_servers')])
    await safe_edit_message(update, "🖥 **لیست سرورها:**", reply_markup=InlineKeyboardMarkup(kb))


# ==============================================================================
# 📊 MONITORING & SERVER ACTIONS
# ==============================================================================
async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await status_dashboard(update, context)

async def status_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        try: await update.callback_query.answer()
        except: pass
        await safe_edit_message(update, "🔄 **در حال دریافت اطلاعات سرورها... (لطفاً صبر کنید)**")
    else:
        await update.message.reply_text("🔄 **در حال دریافت اطلاعات سرورها...**")

    user_id = update.effective_user.id
    servers = db.get_all_user_servers(user_id)
    if not servers:
        msg = "📂 **سروری یافت نشد!**\nابتدا یک سرور اضافه کنید."
        if update.callback_query: 
            await safe_edit_message(update, msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='main_menu')]]))
        else:
             await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data='main_menu')]]))
        return
    
    loop = asyncio.get_running_loop()
    tasks = []
    for s in servers:
        if s['is_active']:
            tasks.append(loop.run_in_executor(None, ServerMonitor.check_full_stats, s['ip'], s['port'], s['username'], sec.decrypt(s['password'])))
        else:
            async def fake(): return {'status': 'Disabled', 'uptime_sec': -1, 'traffic_gb': 0}
            tasks.append(fake())
    
    results = await asyncio.gather(*tasks)
    txt = f"📊 **داشبورد وضعیت شبکه** 🦇\n📆 `{get_jalali_str()}`\n➖➖➖➖➖➖➖➖➖➖\n\n"
    active_count = sum(1 for r in results if isinstance(r, dict) and r['status'] == 'Online')
    txt += f"🟢 **سرورهای آنلاین:** `{active_count}`\n🔴 **آفلاین/خاموش:** `{len(servers) - active_count}`\n\n"
    
    for i, res in enumerate(results):
        final_res = res if isinstance(res, dict) else await res
        srv_name = servers[i]['name']
        if final_res['status'] == 'Disabled': txt += f"⚪️ **{srv_name}** ⇽ 💤 (خاموش)\n"
        elif final_res['status'] == 'Offline': txt += f"🔴 **{srv_name}** ⇽ ⛔️ **OFFLINE**\n"
        else:
            txt += (f"🟢 **{srv_name}**\n"
                f"   ├ ⏱ `{final_res['uptime_str']}`\n"
                f"   ├ 📡 Traf: `{final_res['traffic_gb']} GB`\n"
                f"   └ 💻 CPU: `{final_res['cpu']}%`  RAM: `{final_res['ram']}%`\n\n")
    
    kb = [[InlineKeyboardButton("⚡️ مدیریت سرورها", callback_data='manage_servers_list')], [InlineKeyboardButton("🔄 بروزرسانی", callback_data='status_dashboard')], [InlineKeyboardButton("🔙 منوی اصلی", callback_data='main_menu')]]
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def server_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, custom_sid=None):
    if update.callback_query:
        try: await update.callback_query.answer()
        except: pass

    if custom_sid:
        sid = custom_sid
    elif update.callback_query:
        sid = update.callback_query.data.split('_')[1]
    else:
        return

    srv = db.get_server_by_id(sid)
    if not srv: return
    
    await safe_edit_message(update, f"⚡️ **در حال پردازش اطلاعات سرور {srv['name']}...**")
    
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    is_premium = True if user['plan_type'] == 1 or user_id == SUPER_ADMIN_ID else False
    
    # دکمه پاکسازی دیسک جایگزین ترمینال شد
    btn_clean = InlineKeyboardButton("🧹 پاکسازی دیسک", callback_data=f'act_cleandisk_{sid}')
    
    if is_premium:
        btn_script = InlineKeyboardButton("🛠 اسکریپت", callback_data=f'act_installscript_{sid}')
    else:
        btn_script = InlineKeyboardButton("🔒 اسکریپت", callback_data=f'act_installscript_{sid}')

    res = await asyncio.get_running_loop().run_in_executor(
        None, ServerMonitor.check_full_stats, srv['ip'], srv['port'], srv['username'], sec.decrypt(srv['password'])
    )
    
    expiry_display = "♾ **نامحدود (همیشگی)**"
    status_expiry = "✅"
    
    if srv['expiry_date']:
        try:
            exp_date_obj = datetime.strptime(srv['expiry_date'], '%Y-%m-%d')
            today = datetime.now().date()
            days_left = (exp_date_obj.date() - today).days
            j_date = jdatetime.date.fromgregorian(date=exp_date_obj)
            persian_months = {1: 'فروردین', 2: 'اردیبهشت', 3: 'خرداد', 4: 'تیر', 5: 'مرداد', 6: 'شهریور', 7: 'مهر', 8: 'آبان', 9: 'آذر', 10: 'دی', 11: 'بهمن', 12: 'اسفند'}
            expiry_display = f"{j_date.day} {persian_months[j_date.month]} {j_date.year}"
            
            if days_left < 0:
                expiry_display += f"\n   🚩 **( {abs(days_left)} روز گذشته - منقضی شده 🔴 )**"
                status_expiry = "🔴"
            elif days_left == 0:
                expiry_display += "\n   ⚠️ **( امروز منقضی می‌شود! )**"
                status_expiry = "🟠"
            elif days_left <= 3:
                expiry_display += f"\n   ⚠️ **( تنها {days_left} روز باقی مانده )**"
                status_expiry = "🟡"
            else:
                expiry_display += f"\n   ⏳ **( {days_left} روز باقی مانده )**"
                status_expiry = "🟢"
        except:
            expiry_display = f"{srv['expiry_date']} (خطا در محاسبه)"

    uptime_display = "⚠️ نامعلوم"
    if res.get('uptime_sec', 0) > 0:
        total_seconds = int(res['uptime_sec'])
        total_hours = total_seconds // 3600
        remaining_minutes = (total_seconds % 3600) // 60
        equiv_days = total_seconds // 86400
        uptime_display = (
            f"🕰 **{total_hours}** ساعت **{remaining_minutes}** دقیقه\n"
            f"   ╰ (معادل **{equiv_days}** روز فعالیت 🔥)"
        )

    kb = [
        [
            InlineKeyboardButton("📊 نمودار", callback_data=f'act_chart_{sid}'),
            InlineKeyboardButton("🔄 تازه‌سازی", callback_data=f'detail_{sid}')
        ],
        [
            InlineKeyboardButton("🌍 بررسی وضعیت جهانی", callback_data=f'act_checkhost_{sid}_{srv["ip"]}'),
            InlineKeyboardButton("🏢 دیتاسنتر", callback_data=f'act_datacenter_{sid}')
        ],
        [
            InlineKeyboardButton("📝 گزارش جامع جهانی", callback_data=f'act_fullreport_{sid}')
        ],
        [
            InlineKeyboardButton("🚀 تست سرعت", callback_data=f'act_speedtest_{sid}'),
            InlineKeyboardButton("🧹 پاکسازی RAM", callback_data=f'act_clearcache_{sid}')
        ],
        [
            InlineKeyboardButton("⚙️ DNS", callback_data=f'act_dns_{sid}'),
            InlineKeyboardButton("📥 نصب Speedtest", callback_data=f'act_installspeed_{sid}')
        ],
        [
            InlineKeyboardButton("📦 بروزرسانی Repo", callback_data=f'act_repoupdate_{sid}'),
            InlineKeyboardButton("💎 ارتقاء کامل", callback_data=f'act_fullupdate_{sid}')
        ],
        [
            InlineKeyboardButton("📅 ویرایش انقضا", callback_data=f'act_editexpiry_{sid}'),
            InlineKeyboardButton("⚠️ راه‌اندازی مجدد", callback_data=f'act_reboot_{sid}')
        ],
        [btn_clean, btn_script],
        [InlineKeyboardButton("❌ حذف سرور", callback_data=f'act_del_{sid}')],
        [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data='list_groups_for_servers')]
    ]

    if res['status'] == 'Online':
        db.update_status(sid, "Online")
        cpu_emoji = "🟢" if res['cpu'] < 50 else "🟡" if res['cpu'] < 80 else "🔴"
        ram_emoji = "🟢" if res['ram'] < 50 else "🟡" if res['ram'] < 80 else "🔴"
        disk_emoji = "💿" if res['disk'] < 80 else "⚠️"

        txt = (
            f"🟢 **{srv['name']}** `[آنلاین]`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎫 **اشتراک:** {status_expiry}\n"
            f"📅 `{expiry_display}`\n\n"
            f"🔌 **زمان فعال بودن:**\n"
            f"{uptime_display}\n\n"
            f"🌐 **IP:** `{srv['ip']}`\n"
            f"📡 **ترافیک:** `{res['traffic_gb']} GB`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 **منابع:**\n\n"
            f"{cpu_emoji} **CPU:** `{res['cpu']}%`\n"
            f"`{ServerMonitor.make_bar(res['cpu'], length=15)}`\n\n"
            f"{ram_emoji} **RAM:** `{res['ram']}%`\n"
            f"`{ServerMonitor.make_bar(res['ram'], length=15)}`\n\n"
            f"{disk_emoji} **Disk:** `{res['disk']}%`\n"
            f"`{ServerMonitor.make_bar(res['disk'], length=15)}`"
        )
    else:
        db.update_status(sid, "Offline")
        txt = (
            f"🔴 **{srv['name']}** `[آفلاین]`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ **سرور در دسترس نیست!**\n\n"
            f"🔍 **عیب‌یابی:**\n"
            f"1. آیا سرور خاموش است؟\n"
            f"2. آیا IP ربات مسدود شده؟\n"
            f"3. آیا پورت SSH تغییر کرده است؟\n\n"
            f"📅 **انقضا:**\n`{expiry_display}`\n\n"
            f"❌ **خطا:**\n`{res['error']}`"
        )
        
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def server_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    parts = data.split('_')
    act, sid = parts[1], parts[2]
    
    srv = db.get_server_by_id(sid)
    if not srv:
        try: await update.callback_query.answer("❌ سرور یافت نشد!", show_alert=True)
        except: pass
        return

    uid = update.effective_user.id
    user = db.get_user(uid)
    is_premium = True if user['plan_type'] == 1 or uid == SUPER_ADMIN_ID else False
    
    LOCKED_FEATURES = ['installscript'] 

    if act in LOCKED_FEATURES and not is_premium:
        try: await update.callback_query.answer("🔒 این قابلیت مخصوص کاربران پریمیوم است!", show_alert=True)
        except: pass
        return

    if srv['password']:
        real_pass = sec.decrypt(srv['password'])
    else:
        real_pass = ""
        
    loop = asyncio.get_running_loop()
    
    if act == 'del':
        db.delete_server(sid, update.effective_user.id)
        try: await update.callback_query.answer("✅ سرور با موفقیت حذف شد.")
        except: pass
        await list_groups_for_servers(update, context)

    elif act == 'reboot':
        try: await update.callback_query.answer("⚠️ دستور ریبوت ارسال شد.")
        except: pass
        asyncio.create_task(run_background_ssh_task(
            context, update.effective_chat.id, 
            ServerMonitor.run_remote_command, srv['ip'], srv['port'], srv['username'], real_pass, "reboot"
        ))

    elif act == 'editexpiry':
        await edit_expiry_start(update, context)

    elif act == 'fullreport':
        wait_msg = await update.callback_query.message.reply_text(
            "⏳ **در حال آنالیز جامع وضعیت سرور...**\n\n"
            "1️⃣ استعلام دیتاسنتر...\n"
            "2️⃣ پینگ جهانی (۱۰ ثانیه زمان می‌برد)..."
        )
        task_dc = loop.run_in_executor(None, ServerMonitor.get_datacenter_info, srv['ip'])
        task_ch = loop.run_in_executor(None, ServerMonitor.check_host_api, srv['ip'])
        
        (dc_ok, dc_data), (ch_ok, ch_data) = await asyncio.gather(task_dc, task_ch)
        
        if dc_ok:
            infra_txt = (
                f"🏢 **زیرساخت (Infrastructure):**\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"🏳️ **کشور:** {dc_data['country_name']} ({dc_data['country_code2']})\n"
                f"🏢 **دیتاسنتر:** `{dc_data['isp']}`\n"
                f"🔢 **آی‌پی:** `{dc_data['ip_number']}`\n"
            )
        else:
            infra_txt = f"❌ خطا در دریافت اطلاعات دیتاسنتر: {dc_data}\n"

        if ch_ok:
            ping_txt = ServerMonitor.format_full_global_results(ch_data)
        else:
            ping_txt = f"❌ خطا در Check-Host API: {ch_data}"
            
        final_report = (
            f"📊 **گزارش جامع سرور: {srv['name']}**\n"
            f"📅 {get_jalali_str()}\n\n"
            f"{infra_txt}\n"
            f"🌍 **وضعیت پینگ جهانی:**\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"{ping_txt}"
        )
        await wait_msg.delete()
        await update.callback_query.message.reply_text(final_report, parse_mode='Markdown')

    elif act == 'chart':
        await update.callback_query.message.reply_text("📊 **در حال ترسیم نمودار...**")
        stats = await loop.run_in_executor(None, db.get_server_stats, sid)
        if not stats:
            await update.callback_query.message.reply_text("❌ داده‌ای برای رسم نمودار موجود نیست.")
            return
        photo = await loop.run_in_executor(None, generate_plot, srv['name'], stats)
        if photo:
            await update.callback_query.message.reply_photo(photo=photo, caption=f"📊 مصرف منابع: **{srv['name']}**")
        else:
            await update.callback_query.message.reply_text("❌ خطا در تولید تصویر نمودار.")

    elif act == 'datacenter':
        await update.callback_query.message.reply_text("🔍 **در حال استعلام...**")
        ok, data = await loop.run_in_executor(None, ServerMonitor.get_datacenter_info, srv['ip'])
        if ok:
            txt = (
                f"🏢 **مشخصات دیتاسنتر:**\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"🖥 **آی‌پی:** `{data['ip']}`\n"
                f"🌍 **کشور:** {data['country_name']} ({data['country_code2']})\n"
                f"🏢 **کمپانی:** `{data['isp']}`\n"
                f"✅ **وضعیت:** {data['response_message']}"
            )
            await update.callback_query.message.reply_text(txt, parse_mode='Markdown')
        else:
            await update.callback_query.message.reply_text(f"❌ خطا: `{data}`", parse_mode='Markdown')

    elif act == 'checkhost':
        await update.callback_query.message.reply_text("🌍 **در حال دریافت گزارش Check-Host...**")
        ok, data = await loop.run_in_executor(None, ServerMonitor.check_host_api, parts[3])
        report = ServerMonitor.format_check_host_results(data) if ok else f"❌ خطا: {data}"
        await update.callback_query.message.reply_text(report, parse_mode='Markdown')

    elif act == 'speedtest':
        await update.callback_query.message.reply_text("🚀 **تست سرعت آغاز شد...**\n(نتیجه پس از پایان ارسال می‌شود، می‌توانید به کارهای دیگر برسید)")
        asyncio.create_task(run_background_ssh_task(
            context, update.effective_chat.id, 
            ServerMonitor.run_speedtest, srv['ip'], srv['port'], srv['username'], real_pass
        ))
        
    elif act == 'installspeed':
        await update.callback_query.message.reply_text("📥 **نصب ابزار Speedtest در پس‌زمینه آغاز شد...**")
        asyncio.create_task(run_background_ssh_task(
            context, update.effective_chat.id, 
            ServerMonitor.install_speedtest, srv['ip'], srv['port'], srv['username'], real_pass
        ))
        
    elif act == 'repoupdate':
        await update.callback_query.message.reply_text("📦 **آپدیت مخازن در حال انجام است...**\n(لطفاً صبور باشید، نتیجه ارسال می‌شود)")
        asyncio.create_task(run_background_ssh_task(
            context, update.effective_chat.id, 
            ServerMonitor.repo_update, srv['ip'], srv['port'], srv['username'], real_pass
        ))
        
    elif act == 'fullupdate':
        await update.callback_query.message.reply_text("💎 **آپدیت کامل سیستم آغاز شد!**\n⚠️ این عملیات ممکن است ۱۰ تا ۲۰ دقیقه زمان ببرد.\nنتیجه پس از پایان ارسال خواهد شد.")
        asyncio.create_task(run_background_ssh_task(
            context, update.effective_chat.id, 
            ServerMonitor.full_system_update, srv['ip'], srv['port'], srv['username'], real_pass
        ))

    elif act == 'clearcache':
        try: await update.callback_query.answer("🧹 کش رم پاکسازی شد.")
        except: pass
        await loop.run_in_executor(None, ServerMonitor.clear_cache, srv['ip'], srv['port'], srv['username'], real_pass)
        await server_detail(update, context)
    
    elif act == 'cleandisk':
        await update.callback_query.message.reply_text(
            "🧹 **پاکسازی دیسک آغاز شد...**\n"
            "این عملیات شامل حذف:\n"
            "- پکیج‌های بلااستفاده (Autoremove)\n"
            "- کش پکیج‌ها (Apt Clean)\n"
            "- لاگ‌های قدیمی (Journalctl)\n"
            "- فایل‌های موقت (Tmp)\n\n"
            "⏳ لطفاً صبر کنید..."
        )
        ok, result = await loop.run_in_executor(None, ServerMonitor.clean_disk_space, srv['ip'], srv['port'], srv['username'], real_pass)
        if ok:
            await update.callback_query.message.reply_text(f"✅ **پاکسازی با موفقیت انجام شد.**\n💾 فضای آزاد شده: `{result:.2f} MB`", parse_mode='Markdown')
        else:
            await update.callback_query.message.reply_text(f"❌ خطا در پاکسازی:\n{result}")
        await server_detail(update, context)
        
    elif act == 'dns':
         kb = [
             [InlineKeyboardButton("Cloudflare (1.1.1.1)", callback_data=f'setdns_cloudflare_{sid}'), 
              InlineKeyboardButton("Google (8.8.8.8)", callback_data=f'setdns_google_{sid}')],
             [InlineKeyboardButton("Shecan (Iran)", callback_data=f'setdns_shecan_{sid}'), 
              InlineKeyboardButton("🔙 بازگشت", callback_data=f'detail_{sid}')]
         ]
         await safe_edit_message(update, "⚙️ **تنظیم DNS سرور:**\nلطفاً پرووایدر مورد نظر را انتخاب کنید.", reply_markup=InlineKeyboardMarkup(kb))
    
    elif act == 'locked_terminal':
       try: await update.callback_query.answer("🔒 ترمینال مخصوص کاربران پریمیوم است.\nبرای دسترسی ارتقا دهید.", show_alert=True)
       except: pass

    elif act == 'installscript':
        try: await update.callback_query.answer("🚧 این بخش در حال توسعه است!", show_alert=True)
        except: pass

async def send_global_full_report_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    
    channels = db.get_user_channels(uid)
    if not channels:
        try: await query.answer("❌ ابتدا کانالی برای ارسال گزارش ثبت کنید!", show_alert=True)
        except BadRequest: pass 
        return

    user = db.get_user(uid)
    is_premium = True if user['plan_type'] == 1 or uid == SUPER_ADMIN_ID else False
    limit = 20 if is_premium else 3
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    user_usage = DAILY_REPORT_USAGE.get(uid, {'date': today_str, 'count': 0})
    
    if user_usage['date'] != today_str:
        user_usage = {'date': today_str, 'count': 0}
    
    if user_usage['count'] >= limit:
        try: await query.answer(f"⛔️ سقف مجاز روزانه شما ({limit} بار) پر شده است.\nبرای افزایش به پریمیوم ارتقا دهید.", show_alert=True)
        except BadRequest: pass
        return

    try:
        await query.answer("✅ در حال پردازش و ارسال به کانال...", show_alert=True)
    except BadRequest: pass

    loading_msg = await query.message.reply_text("⏳ **در حال آنالیز تک‌تک سرورها و ارسال به کانال...**\nلطفاً صبر کنید.")
    
    servers = db.get_all_user_servers(uid)
    active_servers = [s for s in servers if s['is_active']]
    
    if not active_servers:
        await loading_msg.edit_text("❌ هیچ سرور فعالی ندارید.")
        return

    user_usage['count'] += 1
    DAILY_REPORT_USAGE[uid] = user_usage
    
    loop = asyncio.get_running_loop()
    sent_count = 0

    header = f"📣 **گزارش وضعیت فوری شبکه**\n📅 زمان: `{get_jalali_str()}`\n👤 کاربر: {user['full_name']}\n➖➖➖➖➖➖➖➖➖➖"
    for ch in channels:
        try: await context.bot.send_message(ch['chat_id'], header, parse_mode='Markdown')
        except: pass

    for srv in active_servers:
        try:
            task_ssh = loop.run_in_executor(None, ServerMonitor.check_full_stats, srv['ip'], srv['port'], srv['username'], sec.decrypt(srv['password']))
            task_dc = loop.run_in_executor(None, ServerMonitor.get_datacenter_info, srv['ip'])
            
            ssh_res, (dc_ok, dc_data) = await asyncio.gather(task_ssh, task_dc)
            
            if ssh_res['status'] == 'Online':
                cpu_bar = ServerMonitor.make_bar(ssh_res['cpu'], length=10)
                ram_bar = ServerMonitor.make_bar(ssh_res['ram'], length=10)
                
                country = "Unknown"
                if dc_ok:
                    country = f"{dc_data['country_name']} ({dc_data['country_code2']})"

                msg = (
                    f"🖥 **{srv['name']}** 🟢 آنلاین\n"
                    f"➖➖➖➖➖➖➖➖➖➖\n"
                    f"🏢 **دیتاسنتر:** `{country}`\n"
                    f"🌐 **آی‌پی:** `{srv['ip']}`\n\n"
                    f"🧠 **CPU:** `{cpu_bar}` {ssh_res['cpu']}%\n"
                    f"💾 **RAM:** `{ram_bar}` {ssh_res['ram']}%\n"
                    f"💿 **DISK:** `{ssh_res['disk']}%`\n"
                    f"⏱ **آپتایم:** `{ssh_res['uptime_str']}`\n"
                    f"📡 **ترافیک:** `{ssh_res['traffic_gb']} GB`"
                )
            else:
                msg = (
                    f"🖥 **{srv['name']}** 🔴 **آفلاین**\n"
                    f"➖➖➖➖➖➖➖➖➖➖\n"
                    f"⚠️ عدم دسترسی به سرور!\n"
                    f"❌ خطا: `{ssh_res['error']}`"
                )

            for ch in channels:
                try:
                    await context.bot.send_message(ch['chat_id'], msg, parse_mode='Markdown')
                except Exception as e:
                    logger.error(f"Send Error: {e}")
            
            sent_count += 1
            await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Report Error {srv['name']}: {e}")

    await loading_msg.edit_text(f"✅ **گزارش کامل {sent_count} سرور به کانال‌ها ارسال شد.**\n🔢 مصرف امروز شما: {user_usage['count']} / {limit}")


async def set_dns_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = update.callback_query.data.split('_')[2]
    srv = db.get_server_by_id(sid)
    await update.callback_query.message.reply_text("⚙️ **Applying DNS...**")
    ok, out = await asyncio.get_running_loop().run_in_executor(None, ServerMonitor.set_dns, srv['ip'], srv['port'], srv['username'], sec.decrypt(srv['password']), update.callback_query.data.split('_')[1])
    await update.callback_query.message.reply_text("✅ Done" if ok else f"❌ {out}")

async def send_instant_channel_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    channels = db.get_user_channels(user_id)
    if not channels:
        try: await query.answer("❌ ابتدا یک کانال ثبت کنید!", show_alert=True)
        except: pass
        return

    loading_msg = await query.message.reply_text("⏳ **در حال جمع‌آوری و مرتب‌سازی اطلاعات...**")
    servers = db.get_all_user_servers(user_id)
    active_servers = [s for s in servers if s['is_active']]
    
    if not active_servers:
        await loading_msg.edit_text("❌ هیچ سرور فعالی ندارید.")
        return

    loop = asyncio.get_running_loop()
    tasks = []
    for srv in active_servers:
        ssh_task = loop.run_in_executor(None, ServerMonitor.check_full_stats, srv['ip'], srv['port'], srv['username'], sec.decrypt(srv['password']))
        ping_task = loop.run_in_executor(None, ServerMonitor.check_host_api, srv['ip'])
        tasks.append(asyncio.gather(ssh_task, ping_task))

    results = await asyncio.gather(*tasks)
    processed_data = []
    for i, (ssh_res, (ping_ok, ping_data)) in enumerate(results):
        server_info = active_servers[i]
        uptime_seconds = ssh_res.get('uptime_sec', -1) if ssh_res['status'] == 'Online' else -1
        processed_data.append({
            'server': server_info,
            'ssh': ssh_res,
            'ping': (ping_ok, ping_data),
            'uptime_sort_key': uptime_seconds
        })

    processed_data.sort(key=lambda x: x['uptime_sort_key'], reverse=True)

    current_time = get_tehran_datetime().strftime("%H:%M:%S")
    report_lines = []
    
    header = (
        f"📡 **گزارش لحظه‌ای وضعیت سرورها**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📅 زمان گزارش: `{current_time}`\n"
        f"📊 چیدمان: بر اساس بیشترین آپتایم 🔼\n\n"
    )

    for item in processed_data:
        srv = item['server']
        ssh_res = item['ssh']
        ping_ok, ping_data = item['ping']
        
        if ssh_res['status'] == 'Online':
            cpu_bar = ServerMonitor.make_bar(ssh_res['cpu'], length=10)
            ram_bar = ServerMonitor.make_bar(ssh_res['ram'], length=10)
            iran_ping_txt = ServerMonitor.format_iran_ping_stats(ping_data) if ping_ok else "\n   ❌ خطا در Check-Host API"

            srv_block = (
                f"🖥 **{srv['name']}** 🟢 آنلاین\n"
                f"   - ⏱ Uptime: `{ssh_res['uptime_str']}`\n"
                f"   - 🧠 CPU: `{cpu_bar}` {ssh_res['cpu']}%\n"
                f"   - 💾 RAM: `{ram_bar}` {ssh_res['ram']}%\n"
                f"   - 💿 Disk: `{ssh_res['disk']}%`\n"
                f"   - 🇮🇷 **Ping Status ✅:**"
                f"{iran_ping_txt}\n"
            )
        else:
            srv_block = (
                f"🖥 **{srv['name']}** 🔴 **آفلاین**\n"
                f"   ❌ خطا: {ssh_res['error']}\n"
            )
        report_lines.append(srv_block)

    final_report = header + "\n".join(report_lines)
    sent_count = 0
    for ch in channels:
        try:
            await context.bot.send_message(chat_id=ch['chat_id'], text=final_report, parse_mode='Markdown')
            sent_count += 1
        except Exception as e:
            logger.error(f"Error sending to channel {ch['chat_id']}: {e}")

    await loading_msg.delete()
    if sent_count > 0:
        await query.message.reply_text(f"✅ گزارش مرتب‌شده به {sent_count} کانال ارسال شد.")
    else:
        await query.message.reply_text("❌ ارسال ناموفق بود.")

async def manage_servers_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.callback_query.answer()
    except: pass
    servers = db.get_all_user_servers(update.effective_user.id)
    kb = [[InlineKeyboardButton(f"{'🟢' if s['is_active'] else '🔴'} | {s['name']}", callback_data=f'toggle_active_{s["id"]}')] for s in servers]
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data='status_dashboard')])
    await safe_edit_message(update, "🛠 **مدیریت مانیتورینگ:**\nبا کلیک روی هر سرور، مانیتورینگ آن را روشن/خاموش کنید.", reply_markup=InlineKeyboardMarkup(kb))

async def toggle_server_active_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = int(update.callback_query.data.split('_')[2])
    srv = db.get_server_by_id(sid)
    db.toggle_server_active(sid, srv['is_active'])
    try: await update.callback_query.answer(f"وضعیت {srv['name']} تغییر کرد.")
    except: pass
    await manage_servers_list(update, context)

# --- New Missing Functions Added Here ---

async def manual_ping_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "🔎 **لطفاً آدرس IP یا دامنه مورد نظر را ارسال کنید:**", reply_markup=get_cancel_markup())
    return GET_MANUAL_HOST

async def perform_manual_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    host = update.message.text
    msg = await update.message.reply_text("🌍 **در حال استعلام از Check-Host...**")
    loop = asyncio.get_running_loop()
    ok, data = await loop.run_in_executor(None, ServerMonitor.check_host_api, host)
    
    report = ServerMonitor.format_check_host_results(data) if ok else f"❌ خطا: {data}"
    await context.bot.send_message(chat_id=msg.chat_id, text=report, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منوی اصلی", callback_data='main_menu')]]))
    return ConversationHandler.END

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await settings_menu(update, context)

# ==============================================================================
# ⚙️ ORGANIZED SETTINGS MENUS
# ==============================================================================

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی اصلی تنظیمات (دسته‌بندی شده)"""
    uid = update.effective_user.id
    if update.callback_query: 
        try: await update.callback_query.answer()
        except: pass
    
    txt = (
        "⚙️ **مرکز تنظیمات پیشرفته**\n\n"
        "برای دسترسی راحت‌تر، تنظیمات به بخش‌های زیر تقسیم شده‌اند.\n"
        "لطفاً بخش مورد نظر را انتخاب کنید:"
    )
    
    kb = [
        [
            InlineKeyboardButton("🤖 خودکارسازی و زمان‌بندی", callback_data='menu_automation'),
            InlineKeyboardButton("📟 مانیتورینگ و هشدارها", callback_data='menu_monitoring')
        ],
        [
            InlineKeyboardButton("📢 مدیریت کانال‌های ارسال", callback_data='channels_menu')
        ],
        [
            InlineKeyboardButton("📡 دریافت گزارش لحظه‌ای (تست)", callback_data='send_instant_report')
        ],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data='main_menu')]
    ]
    
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def automation_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زیرمنوی خودکارسازی (Tasks & Cronjobs)"""
    if update.callback_query: await update.callback_query.answer()
    
    uid = update.effective_user.id
    
    # دریافت وضعیت‌های فعلی برای نمایش در دکمه
    cron_val = db.get_setting(uid, 'report_interval') or '0'
    cron_status = "❌ خاموش" if cron_val == '0' else f"✅ هر {int(int(cron_val)/60)} دقیقه"
    
    up_val = db.get_setting(uid, 'auto_update_hours') or '0'
    up_status = "❌ خاموش" if up_val == '0' else f"✅ هر {up_val} ساعت"
    
    reb_val = db.get_setting(uid, 'auto_reboot_config')
    reb_status = "✅ فعال" if reb_val and reb_val != 'OFF' else "❌ خاموش"

    txt = (
        "🤖 **تنظیمات خودکارسازی (Automation)**\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "در این بخش می‌توانید وظایف تکرار شونده ربات را مدیریت کنید.\n\n"
        f"📊 **گزارش خودکار:** {cron_status}\n"
        f"🔄 **آپدیت خودکار:** {up_status}\n"
        f"⚠️ **ریبوت خودکار:** {reb_status}"
    )
    
    kb = [
        [InlineKeyboardButton("⏰ تنظیم زمان‌بندی گزارش (Cron)", callback_data='settings_cron')],
        [InlineKeyboardButton("🔄 تنظیم آپدیت خودکار مخازن", callback_data='auto_up_menu')],
        [InlineKeyboardButton("⚠️ تنظیم ریبوت خودکار سرورها", callback_data='auto_reboot_menu')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='settings_menu')]
    ]
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def monitoring_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زیرمنوی تنظیمات نظارتی (Alerts & Thresholds)"""
    if update.callback_query: await update.callback_query.answer()
    
    uid = update.effective_user.id
    
    # وضعیت هشدار قطعی
    down_alert = db.get_setting(uid, 'down_alert_enabled') or '1'
    alert_icon = "🔔 روشن" if down_alert == '1' else "🔕 خاموش"
    toggle_val = "0" if down_alert == "1" else "1"
    
    # وضعیت منابع
    cpu_limit = db.get_setting(uid, 'cpu_threshold') or '80'
    ram_limit = db.get_setting(uid, 'ram_threshold') or '80'

    txt = (
        "📟 **تنظیمات مانیتورینگ و هشدار**\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "حساسیت ربات نسبت به وضعیت سرورها را اینجا تنظیم کنید.\n\n"
        f"🚨 **هشدار قطعی:** {alert_icon}\n"
        f"🧠 **حد هشدار CPU:** `{cpu_limit}%`\n"
        f"💾 **حد هشدار RAM:** `{ram_limit}%`"
    )
    
    kb = [
        [InlineKeyboardButton(f"🚨 هشدار قطعی: {alert_icon}", callback_data=f'toggle_downalert_{toggle_val}')],
        [InlineKeyboardButton("🎚 تغییر آستانه مصرف منابع (Limits)", callback_data='settings_thresholds')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='settings_menu')]
    ]
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chans = db.get_user_channels(uid)
    
    type_map = {'all': '✅ همه', 'down': '🚨 قطعی', 'report': '📊 گزارش', 'expiry': '⏳ انقضا', 'resource': '🔥 منابع'}
    
    kb = [[InlineKeyboardButton(f"🗑 {c['name']} ({type_map.get(c['usage_type'],'all')})", callback_data=f'delchan_{c["id"]}')] for c in chans]
    kb.append([InlineKeyboardButton("➕ افزودن کانال", callback_data='add_channel')])
    kb.append([InlineKeyboardButton("🔙 بازگشت به تنظیمات", callback_data='settings_menu')])
    await safe_edit_message(update, "📢 **مدیریت کانال‌ها:**", reply_markup=InlineKeyboardMarkup(kb))

async def settings_cron_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    current_val = db.get_setting(uid, 'report_interval') or '0'
    def get_label(text, value): return f"✅ {text}" if str(value) == str(current_val) else text
    kb = [
        [InlineKeyboardButton(get_label("30m", 1800), callback_data='setcron_1800'), InlineKeyboardButton(get_label("60m", 3600), callback_data='setcron_3600')],
        [InlineKeyboardButton(get_label("12h", 43200), callback_data='setcron_43200'), InlineKeyboardButton(get_label("❌ Off", 0), callback_data='setcron_0')],
        [InlineKeyboardButton("✍️ زمان دلخواه", callback_data='setcron_custom'), InlineKeyboardButton("🔙 بازگشت", callback_data='settings_menu')]
    ]
    await safe_edit_message(update, "⏰ **بازه گزارش خودکار:**", reply_markup=InlineKeyboardMarkup(kb))

async def set_cron_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_setting(update.effective_user.id, 'report_interval', int(update.callback_query.data.split('_')[1]))
    try: await update.callback_query.answer("ذخیره شد.")
    except: pass
    await settings_cron_menu(update, context)
    

async def resource_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی تنظیم آستانه مصرف منابع"""
    uid = update.effective_user.id
    if update.callback_query: 
        try: await update.callback_query.answer()
        except: pass
    
    cpu_limit = db.get_setting(uid, 'cpu_threshold') or '80'
    ram_limit = db.get_setting(uid, 'ram_threshold') or '80'
    disk_limit = db.get_setting(uid, 'disk_threshold') or '90'
    
    txt = (
        "🎚 **تنظیم آستانه حساسیت (Thresholds)**\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "اگر مصرف منابع سرور از مقادیر زیر بیشتر شود، ربات هشدار می‌دهد.\n\n"
        f"🧠 **حداکثر CPU مجاز:** `{cpu_limit}%`\n"
        f"💾 **حداکثر RAM مجاز:** `{ram_limit}%`\n"
        f"💿 **حداکثر DISK مجاز:** `{disk_limit}%`"
    )

    # تعریف لیست دکمه‌ها (اینجا kb تعریف می‌شود)
    kb = [
        [InlineKeyboardButton(f"تغییر حد CPU ({cpu_limit}%)", callback_data='set_cpu_limit')],
        [InlineKeyboardButton(f"تغییر حد RAM ({ram_limit}%)", callback_data='set_ram_limit')],
        [InlineKeyboardButton(f"تغییر حد Disk ({disk_limit}%)", callback_data='set_disk_limit')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='menu_monitoring')]
    ]
    
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))
async def toggle_down_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_setting(update.effective_user.id, 'down_alert_enabled', update.callback_query.data.split('_')[2])
    await monitoring_settings_menu(update, context)

async def ask_cpu_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "🧠 **حداکثر درصد مجاز CPU (0-100):**", reply_markup=get_cancel_markup())
    return GET_CPU_LIMIT

async def save_cpu_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if 1 <= val <= 100:
            db.set_setting(update.effective_user.id, 'cpu_threshold', val)
            await update.message.reply_text(f"✅ ذخیره شد: {val}%")
            await resource_settings_menu(update, context)
            return ConversationHandler.END
    except: pass
    await update.message.reply_text("❌ عدد نامعتبر.")
    return GET_CPU_LIMIT

async def ask_ram_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "💾 **حداکثر درصد مجاز RAM (0-100):**", reply_markup=get_cancel_markup())
    return GET_RAM_LIMIT

async def save_ram_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if 1 <= val <= 100:
            db.set_setting(update.effective_user.id, 'ram_threshold', val)
            await update.message.reply_text(f"✅ ذخیره شد: {val}%")
            await resource_settings_menu(update, context)
            return ConversationHandler.END
    except: pass
    await update.message.reply_text("❌ عدد نامعتبر.")
    return GET_RAM_LIMIT

async def ask_disk_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "💿 **حداکثر درصد مجاز Disk (0-100):**", reply_markup=get_cancel_markup())
    return GET_DISK_LIMIT

async def save_disk_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if 1 <= val <= 100:
            db.set_setting(update.effective_user.id, 'disk_threshold', val)
            await update.message.reply_text(f"✅ ذخیره شد: {val}%")
            await resource_settings_menu(update, context)
            return ConversationHandler.END
    except: pass
    await update.message.reply_text("❌ عدد نامعتبر.")
    return GET_DISK_LIMIT

async def ask_custom_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "✍️ **بازه زمانی (دقیقه) را وارد کنید:**", reply_markup=get_cancel_markup())
    return GET_CUSTOM_INTERVAL

async def set_custom_interval_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        minutes = int(update.message.text)
        if 10 <= minutes <= 1440:
            db.set_setting(update.effective_user.id, 'report_interval', minutes * 60)
            await update.message.reply_text(f"✅ تنظیم شد: هر {minutes} دقیقه.")
            await settings_cron_menu(update, context)
            return ConversationHandler.END
    except: pass
    await update.message.reply_text("❌ عدد نامعتبر (بین 10 تا 1440).")
    return GET_CUSTOM_INTERVAL

async def add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(
        update, 
        "📝 **افزودن کانال جدید**\n\n"
        "لطفاً **آیدی عددی کانال** را ارسال کنید.\n"
        "مثال: `-100123456789`\n\n"
        "⚠️ **نکته:** ابتدا ربات را در کانال **ادمین** کنید.", 
        reply_markup=get_cancel_markup()
    )
    return GET_CHANNEL_FORWARD

async def get_channel_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.message
        text = getattr(msg, 'text', '').strip()
        
        # اعتبارسنجی: آیدی باید با -100 شروع شود یا @ داشته باشد
        if not text or (not text.startswith('-100') and not text.startswith('@')):
            await msg.reply_text(
                "❌ **فرمت نامعتبر!**\n\n"
                "لطفاً فقط **آیدی عددی** (شروع با -100) یا **یوزرنیم** (شروع با @) بفرستید.\n"
                "مثال صحیح: `-100123456789`"
            )
            return GET_CHANNEL_FORWARD

        c_id = text
        c_name = "Channel (Manual)"
        
        # تلاش برای گرفتن اسم کانال جهت اطمینان
        try:
            chat = await context.bot.get_chat(c_id)
            c_name = chat.title
            c_id = str(chat.id) # تبدیل نهایی به آیدی عددی
        except Exception as e:
            # اگر ربات ادمین نباشد یا آیدی غلط باشد
            await msg.reply_text(
                f"❌ **ربات نتوانست کانال را پیدا کند!**\n\n"
                f"1️⃣ مطمئن شوید آیدی `{text}` صحیح است.\n"
                f"2️⃣ مطمئن شوید ربات در کانال **ادمین** است.\n"
                f"خطا: {e}"
            )
            return GET_CHANNEL_FORWARD

        context.user_data['new_chan'] = {'id': c_id, 'name': c_name}
        
        kb = [
            [InlineKeyboardButton("🔥 فقط فشار منابع (CPU/RAM)", callback_data='type_resource')],
            [InlineKeyboardButton("🚨 فقط هشدار قطعی", callback_data='type_down'), InlineKeyboardButton("⏳ فقط انقضا", callback_data='type_expiry')],
            [InlineKeyboardButton("📊 فقط گزارشات", callback_data='type_report'), InlineKeyboardButton("✅ همه موارد", callback_data='type_all')]
        ]
        
        await msg.reply_text(
            f"✅ کانال **{c_name}** شناسایی شد.\n🆔 آیدی: `{c_id}`\n\n🛠 **این کانال برای دریافت چه نوع پیام‌هایی استفاده شود؟**", 
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return GET_CHANNEL_TYPE

    except Exception as e:
        logger.error(f"Channel Add Error: {e}")
        await msg.reply_text("❌ خطای غیرمنتظره. دوباره تلاش کنید.")
        return GET_CHANNEL_FORWARD

async def set_channel_type_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    usage = query.data.split('_')[1]
    cdata = context.user_data['new_chan']
    db.add_channel(update.effective_user.id, cdata['id'], cdata['name'], usage)
    await query.message.reply_text(f"✅ کانال {cdata['name']} ثبت شد.")
    await channels_menu(update, context)
    return ConversationHandler.END

async def delete_channel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.delete_channel(int(update.callback_query.data.split('_')[1]), update.effective_user.id)
    await channels_menu(update, context)

async def edit_expiry_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    sid = query.data.split('_')[2]
    context.user_data['edit_expiry_sid'] = sid
    srv = db.get_server_by_id(sid)
    txt = (
        f"📅 **تغییر زمان انقضای سرور: {srv['name']}**\n\n"
        f"🔢 لطفاً **تعداد روزهای باقی‌مانده** را به عدد وارد کنید.\n"
        f"مثلاً اگر عدد `30` را بفرستید، انقضا روی ۳۰ روز دیگر تنظیم می‌شود.\n\n"
        f"♾ برای **نامحدود** کردن، عدد `0` را بفرستید."
    )
    await safe_edit_message(update, txt, reply_markup=get_cancel_markup())
    return EDIT_SERVER_EXPIRY

async def edit_expiry_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text)
        sid = context.user_data.get('edit_expiry_sid')
        if days > 0:
            new_date = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
            msg = f"✅ تاریخ انقضا با موفقیت روی **{days} روز دیگر** تنظیم شد."
        else:
            new_date = None
            msg = "✅ سرور با موفقیت **نامحدود (Lifetime)** شد."
        db.update_server_expiry(sid, new_date)
        await update.message.reply_text(msg)
        await server_detail(update, context, custom_sid=sid)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ لطفاً فقط عدد انگلیسی وارد کنید.")
        return EDIT_SERVER_EXPIRY

async def ask_terminal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    sid = query.data.split('_')[2]
    srv = db.get_server_by_id(sid)
    context.user_data['term_sid'] = sid 
    
    kb = [[InlineKeyboardButton("🔙 خروج و بازگشت به پنل", callback_data='exit_terminal')]]
    
    txt = (
        f"📟 **ترمینال تعاملی: {srv['name']}**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🟢 **اتصال برقرار شد.**\n"
        f"هر دستوری بنویسی اجرا میشه. برای خروج دکمه پایین رو بزن.\n\n"
        f"root@{srv['ip']}:~# _"
    )
    
    await query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return GET_REMOTE_COMMAND

async def run_terminal_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text
    if cmd.lower() in ['exit', 'quit']:
        return await close_terminal_session(update, context)

    sid = context.user_data.get('term_sid')
    srv = db.get_server_by_id(sid)
    
    wait_msg = await update.message.reply_text(f"⚙️ `{cmd}` ...")
    
    real_pass = sec.decrypt(srv['password'])
    ok, output = await asyncio.get_running_loop().run_in_executor(None, ServerMonitor.run_remote_command, srv['ip'], srv['port'], srv['username'], real_pass, cmd)
    
    if not output: output = "[No Output]"
    if len(output) > 3000: output = output[:3000] + "\n..."
    safe_output = html.escape(output)
    status = "✅" if ok else "❌"
    
    terminal_view = (
        f"<code>root@{srv['ip']}:~# {cmd}</code>\n"
        f"{status}\n"
        f"<pre language='bash'>{safe_output}</pre>"
    )
    
    kb = [[InlineKeyboardButton("🔙 خروج از ترمینال", callback_data='exit_terminal')]]
    await wait_msg.delete()
    try:
        await update.message.reply_text(terminal_view, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(kb))
    except:
        await update.message.reply_text(f"⚠️ Raw Output:\n{output}", reply_markup=InlineKeyboardMarkup(kb))
    
    return GET_REMOTE_COMMAND

async def close_terminal_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: 
        try: await update.callback_query.answer()
        except: pass
    sid = context.user_data.get('term_sid')
    await server_detail(update, context, custom_sid=sid)
    return ConversationHandler.END

# ==============================================================================
# ⏳ SCHEDULED JOBS
# ==============================================================================
async def check_bonus_expiry_job(context: ContextTypes.DEFAULT_TYPE):
    """بررسی و حذف پاداش‌های منقضی شده"""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # گرفتن پاداش‌های منقضی شده
    with db.get_connection() as conn:
        expired_bonuses = conn.execute("SELECT * FROM temp_bonuses WHERE expires_at < ?", (now_str,)).fetchall()
        
        for bonus in expired_bonuses:
            uid = bonus['user_id']
            amount = bonus['bonus_limit']
            
            # گرفتن کاربر برای کاهش لیمیت
            user = conn.execute("SELECT server_limit FROM users WHERE user_id = ?", (uid,)).fetchone()
            if user:
                current_limit = user['server_limit']
                new_limit = max(0, current_limit - amount) # جلوگیری از منفی شدن
                
                # کاهش لیمیت
                conn.execute("UPDATE users SET server_limit = ? WHERE user_id = ?", (new_limit, uid))
                
                # اطلاع رسانی به کاربر
                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text=f"⚠️ **پایان مهلت پاداش دعوت**\n\nیکی از پاداش‌های ۱۰ روزه شما منقضی شد و ۱ عدد از ظرفیت سرور شما کسر گردید.\nظرفیت فعلی: {new_limit}"
                    )
                except: pass
            
            # حذف از جدول پاداش‌ها
            conn.execute("DELETE FROM temp_bonuses WHERE id = ?", (bonus['id'],))
        
        conn.commit()
async def check_expiry_job(context: ContextTypes.DEFAULT_TYPE):
    users = db.get_all_users()
    today = datetime.now().date()
    for user in users:
        uid = user['user_id']
        servers = db.get_all_user_servers(uid)
        user_channels = db.get_user_channels(uid)
        target_channels = [c for c in user_channels if c.get('usage_type', 'all') in ['expiry', 'all']]
        
        for srv in servers:
            if not srv['expiry_date']: continue
            try:
                exp_date = datetime.strptime(srv['expiry_date'], '%Y-%m-%d').date()
                days_left = (exp_date - today).days
                msg = None
                if days_left == 3:
                    msg = f"⚠️ **هشدار انقضا (۳ روز مانده)**\n\n🖥 سرور: `{srv['name']}`\n📅 اتمام: `{srv['expiry_date']}`\nلطفاً جهت تمدید اقدام کنید."
                elif days_left == 0:
                    msg = f"🚨 **هشدار نهایی (امروز تمام می‌شود)**\n\n🖥 سرور: `{srv['name']}`\nدارای انقضای امروز است!"
                
                if msg:
                    try: await context.bot.send_message(uid, msg, parse_mode='Markdown')
                    except: pass
                    for ch in target_channels:
                        try: await context.bot.send_message(ch['chat_id'], msg, parse_mode='Markdown')
                        except: pass
            except ValueError as e:
                logger.error(f"Date format error for server {srv['id']}: {e}")
            except Exception as e:
                logger.error(f"Expiry Check Error: {e}")

async def global_monitor_job(context: ContextTypes.DEFAULT_TYPE):
    # --- اصلاح شده: اجرای سبک‌تر و جلوگیری از هنگ کردن ---
    loop = asyncio.get_running_loop()
    users_list = await loop.run_in_executor(None, db.get_all_users)
    all_users = set([u['user_id'] for u in users_list] + [SUPER_ADMIN_ID])
    
    # محدودیت: فقط ۱۰ سرور همزمان چک شوند
    semaphore = asyncio.Semaphore(10) 

    async def protected_process(uid):
        async with semaphore:
            servers = await loop.run_in_executor(None, db.get_all_user_servers, uid)
            if not servers: return

            def get_user_settings():
                return {
                    'report_interval': db.get_setting(uid, 'report_interval'),
                    'cpu': int(db.get_setting(uid, 'cpu_threshold') or 80),
                    'ram': int(db.get_setting(uid, 'ram_threshold') or 80),
                    'disk': int(db.get_setting(uid, 'disk_threshold') or 90),
                    'down_alert': db.get_setting(uid, 'down_alert_enabled') == '1'
                }
            settings = await loop.run_in_executor(None, get_user_settings)
            
            await process_single_user(context, uid, servers, settings, loop)

    all_tasks = []
    for uid in all_users:
        all_tasks.append(protected_process(uid))

    if all_tasks:
        await asyncio.gather(*all_tasks)

async def process_single_user(context, uid, servers, settings, loop):
    tasks = []
    for s in servers:
        if s['is_active']:
            tasks.append(loop.run_in_executor(None, ServerMonitor.check_full_stats, s['ip'], s['port'], s['username'], sec.decrypt(s['password'])))
        else:
            async def fake(): return {'status': 'Disabled'}
            tasks.append(fake())
            
    results = await asyncio.gather(*tasks)
    
    # --- شروع ساخت گزارش ---
    header = f"📅 **گزارش خودکار ({get_jalali_str()})**\n➖➖➖➖➖➖\n"
    report_lines = []
    
    for i, res in enumerate(results):
        s_info = servers[i]
        r = res if isinstance(res, dict) else await res
        
        # لاجیک ذخیره آمار و تبریک آپتایم (بدون تغییر)
        if r.get('status') == 'Online':
            db.add_server_stat(s_info['id'], r.get('cpu', 0), r.get('ram', 0))
            
            # ... (کد تبریک آپتایم که قبلا داشتید اینجا محفوظ است فرض کنید هست) ...
            # ... (کد هشدار ورود SSH که قبلا دادم اینجا محفوظ است) ...

            # لاجیک هشدار منابع (Resource Alert)
            alert_msgs = []
            if r['cpu'] >= settings['cpu']: alert_msgs.append(f"🧠 **CPU:** `{r['cpu']}%`")
            if r['ram'] >= settings['ram']: alert_msgs.append(f"💾 **RAM:** `{r['ram']}%`")
            if r['disk'] >= settings['disk']: alert_msgs.append(f"💿 **Disk:** `{r['disk']}%`")
            
            if alert_msgs:
                last_alert = CPU_ALERT_TRACKER.get((uid, s_info['id']), 0)
                if time.time() - last_alert > 3600:
                    full_warning = (f"⚠️ **هشدار مصرف منابع**\n🖥 سرور: `{s_info['name']}`\n" + "\n".join(alert_msgs))
                    # ارسال هشدار منابع به کاربر/کانال...
                    try: await context.bot.send_message(uid, full_warning, parse_mode='Markdown')
                    except: pass
                    CPU_ALERT_TRACKER[(uid, s_info['id'])] = time.time()

        # آیکون وضعیت برای گزارش کلی
        icon = "✅" if r.get('status') == 'Online' else "❌"
        status_txt = f"{r.get('cpu')}% CPU" if r.get('status') == 'Online' else "OFF"
        report_lines.append(f"{icon} **{s_info['name']}** ⇽ `{status_txt}`")
        
        # بررسی قطعی هوشمند (Smart Down Check)
        if settings['down_alert'] and s_info['is_active']:
             await check_server_down_logic(context, uid, s_info, r)

    # --- ارسال گزارش زمان‌بندی شده (با رفع باگ طولانی بودن پیام) ---
    report_int = settings['report_interval']
    if report_int and int(report_int) > 0:
        last_run = LAST_REPORT_CACHE.get(uid, 0)
        if time.time() - last_run > int(report_int):
            
            # تقسیم پیام به بخش‌های کوچک‌تر (Chunking)
            final_msg = header + "\n".join(report_lines)
            
            if len(final_msg) > 4000:
                # اگر پیام طولانی بود، خرد کن
                chunks = [report_lines[i:i + 20] for i in range(0, len(report_lines), 20)]
                try: await context.bot.send_message(uid, header, parse_mode='Markdown')
                except: pass
                
                for chunk in chunks:
                    chunk_text = "\n".join(chunk)
                    try: await context.bot.send_message(uid, chunk_text, parse_mode='Markdown')
                    except: pass
            else:
                # اگر کوتاه بود یکجا بفرست
                try: await context.bot.send_message(uid, final_msg, parse_mode='Markdown')
                except: pass
                
            LAST_REPORT_CACHE[uid] = time.time()

async def check_server_down_logic(context, uid, s, res):
    k = (uid, s['id'])
    fails = SERVER_FAILURE_COUNTS.get(k, 0)
    
    if res['status'] == 'Offline':
        # 🛑 قبل از اینکه بگیم سرور قطعه، از Check-Host می‌پرسیم
        is_really_down = True
        extra_note = ""

        # فقط اگر بار اوله که متوجه قطعی میشیم چک کنیم (که اسپم API نشه)
        if fails == 0: 
            try:
                # استفاده از تابع موجود در کلاس ServerMonitor
                loop = asyncio.get_running_loop()
                chk_ok, chk_data = await loop.run_in_executor(None, ServerMonitor.check_host_api, s['ip'])
                
                if chk_ok and isinstance(chk_data, dict):
                    # بررسی می‌کنیم آیا حداقل ۳ تا نود تونستن پینگ کنن؟
                    ok_nodes = 0
                    for node, result in chk_data.items():
                        if result and result[0] and result[0][0] == "OK":
                            ok_nodes += 1
                    
                    if ok_nodes >= 3:
                        is_really_down = False
                        extra_note = "\n🛡 **نکته:** سرور از دید جهانی **آنلاین** است. احتمالاً آی‌پی ربات مسدود شده."
            except:
                pass # اگر چک هاست ارور داد، فرض رو بر قطعی واقعی میذاریم

        if is_really_down:
            fails += 1
            SERVER_FAILURE_COUNTS[k] = fails
            
            # اگر به حد نصاب رسید هشدار بده
            if fails == DOWN_RETRY_LIMIT:
                alrt = (
                    f"🚨 **هشدار قطع اتصال (CRITICAL)**\n"
                    f"🖥 سرور: `{s['name']}`\n"
                    f"➖➖➖➖➖➖➖➖➖➖\n"
                    f"❌ وضعیت: **عدم دسترسی کامل**\n"
                    f"🔍 خطا: `{res.get('error', 'Time out')}`"
                    f"{extra_note}"
                )
                
                # ارسال به کانال‌های کاربر
                user_channels = db.get_user_channels(uid)
                sent = False
                for c in user_channels:
                    if c['usage_type'] in ['down', 'all']:
                        try: 
                            await context.bot.send_message(c['chat_id'], alrt, parse_mode='Markdown')
                            sent = True
                        except: pass
                
                # ارسال به خود کاربر اگر کانالی نداشت
                if not sent:
                    try: await context.bot.send_message(uid, alrt, parse_mode='Markdown')
                    except: pass
                
                db.update_status(s['id'], "Offline")
        else:
            # اگر واقعا داون نبود ولی ربات وصل نمیشد، کانتر رو صفر نگه دار یا ریست کن
            SERVER_FAILURE_COUNTS[k] = 0

    else:
        # اگر سرور آنلاین شد (Recovery)
        if fails > 0 or s['last_status'] == 'Offline':
            SERVER_FAILURE_COUNTS[k] = 0
            if s['last_status'] == 'Offline':
                rec_msg = (
                    f"✅ **اتصال برقرار شد (RECOVERY)**\n"
                    f"🖥 سرور: `{s['name']}`\n"
                    f"➖➖➖➖➖➖➖➖➖➖\n"
                    f"♻️ سرور مجدداً در دسترس قرار گرفت."
                )
                
                user_channels = db.get_user_channels(uid)
                sent = False
                for c in user_channels:
                    if c['usage_type'] in ['down', 'all']:
                        try: 
                            await context.bot.send_message(c['chat_id'], rec_msg, parse_mode='Markdown')
                            sent = True
                        except: pass
                if not sent:
                    try: await context.bot.send_message(uid, rec_msg, parse_mode='Markdown')
                    except: pass
                db.update_status(s['id'], "Online")
# ==============================================================================
# 🌍 GLOBAL OPERATIONS (NEW FEATURES)
# ==============================================================================

async def global_ops_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش منوی عملیات همگانی"""
    kb = [
        [InlineKeyboardButton("🔄 آپدیت مخازن (همه سرورها)", callback_data='glob_act_update')],
        [InlineKeyboardButton("🧹 پاکسازی RAM (همه سرورها)", callback_data='glob_act_ram')],
        [InlineKeyboardButton("🗑 پاکسازی دیسک (همه سرورها)", callback_data='glob_act_disk')],
        [InlineKeyboardButton("🛠 سرویس کامل (Full Service)", callback_data='glob_act_full')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='main_menu')]
    ]
    
    txt = (
        "🌍 **تنظیمات همگانی سرورها**\n\n"
        "در این بخش می‌تونی یک دستور رو همزمان روی **تمام سرورهای فعال** اجرا کنی.\n"
        "⚠️ نکته: عملیات ممکن است بسته به تعداد سرورها کمی طول بکشد."
    )
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def global_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت درخواست‌های همگانی"""
    query = update.callback_query
    action = query.data.split('_')[2] # update, ram, disk, full
    uid = update.effective_user.id
    servers = db.get_all_user_servers(uid)
    active_servers = [s for s in servers if s['is_active']]
    
    if not active_servers:
        await query.answer("❌ هیچ سرور فعالی نداری!", show_alert=True)
        return

    await query.message.reply_text(
        f"⏳ **عملیات در حال اجرا روی {len(active_servers)} سرور...**\n"
        "لطفاً منتظر بمانید، نتیجه نهایی ارسال خواهد شد."
    )

    asyncio.create_task(run_global_commands_background(context, uid, active_servers, action))

async def run_global_commands_background(context, chat_id, servers, action):
    """تابع اجرایی که روی سرورها لوپ می‌زند"""
    results = []
    success_count = 0
    fail_count = 0
    
    msg_header = ""
    cmd = ""
    
    if action == 'update':
        msg_header = "🔄 **گزارش آپدیت همگانی**"
        cmd = "sudo DEBIAN_FRONTEND=noninteractive apt-get update -y && sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y"
    elif action == 'ram':
        msg_header = "🧹 **گزارش پاکسازی RAM**"
        cmd = "sudo sync; sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'"
    elif action == 'disk':
        msg_header = "🗑 **گزارش پاکسازی دیسک**"
        cmd = (
            "sudo apt-get autoremove -y && "
            "sudo apt-get autoclean -y && "
            "sudo journalctl --vacuum-size=50M && "
            "sudo rm -rf /tmp/*"
        )
    elif action == 'full':
        msg_header = "🛠 **گزارش سرویس کامل (Full Service)**"

        cmd = (
             "sudo DEBIAN_FRONTEND=noninteractive apt-get update -y && "
             "sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y && "
             "sudo sync; sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches' && "
             "sudo apt-get autoremove -y && sudo apt-get autoclean -y"
        )

    for srv in servers:
        try:
            ok, output = await asyncio.get_running_loop().run_in_executor(
                None, ServerMonitor.run_remote_command, 
                srv['ip'], srv['port'], srv['username'], sec.decrypt(srv['password']), 
                cmd, 600 
            )
            
            if ok:
                success_count += 1
                results.append(f"✅ **{srv['name']}:** انجام شد.")
            else:
                fail_count += 1
                results.append(f"❌ **{srv['name']}:** خطا\n`{str(output)[:50]}`") 
                
        except Exception as e:
            fail_count += 1
            results.append(f"❌ **{srv['name']}:** خطای اتصال")

    final_report = (
        f"{msg_header}\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📊 کل سرورها: {len(servers)}\n"
        f"✅ موفق: {success_count} | ❌ ناموفق: {fail_count}\n\n"
        + "\n".join(results)
    )
    
    if len(final_report) > 4000:
        final_report = final_report[:4000] + "\n...(ادامه بریده شد)"
        
    await context.bot.send_message(chat_id=chat_id, text=final_report, parse_mode='Markdown')
# ==============================================================================
# ⏱ AUTO SCHEDULE HANDLERS (CRONJOBS)
# ==============================================================================

async def auto_update_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی تنظیم زمان‌بندی آپدیت خودکار"""
    if update.callback_query: await update.callback_query.answer()

    uid = update.effective_user.id
    curr = db.get_setting(uid, 'auto_update_hours') or '0'
    
    def st(val): return "✅" if str(val) == str(curr) else ""

    txt = (
        "🔄 **تنظیم آپدیت خودکار مخازن (APT Update)**\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "ربات می‌تواند به صورت دوره‌ای دستور `apt-get update && upgrade` را روی تمام سرورهای فعال اجرا کند.\n\n"
        "👇 بازه زمانی مورد نظر را انتخاب کنید:"
    )

    # تعریف دکمه‌ها
    kb = [
        [InlineKeyboardButton(f"{st(6)} هر ۶ ساعت", callback_data='set_autoup_6'), InlineKeyboardButton(f"{st(12)} هر ۱۲ ساعت", callback_data='set_autoup_12')],
        [InlineKeyboardButton(f"{st(24)} هر ۲۴ ساعت", callback_data='set_autoup_24'), InlineKeyboardButton(f"{st(48)} هر ۴۸ ساعت", callback_data='set_autoup_48')],
        [InlineKeyboardButton(f"{st(0)} ❌ غیرفعال", callback_data='set_autoup_0')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='menu_automation')]
    ]
    
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))
    
async def auto_reboot_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی اصلی وضعیت ریبوت خودکار"""
    if update.callback_query: await update.callback_query.answer()

    uid = update.effective_user.id
    curr_setting = db.get_setting(uid, 'auto_reboot_config')
    
    status_txt = "❌ غیرفعال"
    if curr_setting and curr_setting != 'OFF':
        try:
            days, time_str = curr_setting.split('|')
            days = int(days)
            freq_map = {1: "هر روز", 2: "هر ۲ روز", 7: "هفتگی", 14: "هر ۲ هفته", 30: "ماهانه"}
            freq_txt = freq_map.get(days, f"هر {days} روز")
            status_txt = f"✅ {freq_txt} - ساعت {time_str}"
        except:
            status_txt = "⚠️ نامعتبر"

    txt = (
        "⚠️ **تنظیم ریبوت خودکار سرورها**\n"
        "➖➖➖➖➖➖➖➖➖➖\n"
        "🔴 **هشدار:** ریبوت شدن سرور باعث قطع موقت اتصال کاربران می‌شود.\n"
        "در این بخش می‌توانید تعیین کنید تمام سرورها سر ساعت مشخصی ریبوت شوند.\n\n"
        f"وضعیت فعلی: `{status_txt}`"
    )
    
    # تعریف دکمه‌ها
    kb = [
        [InlineKeyboardButton("⚙️ تنظیم زمان‌بندی جدید", callback_data='start_set_reboot')],
        [InlineKeyboardButton("❌ غیرفعال‌سازی", callback_data='disable_reboot')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='menu_automation')]
    ]
    
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def ask_reboot_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پرسیدن ساعت از کاربر"""
    try: await update.callback_query.answer()
    except: pass
    
    txt = (
        "🕰 **تنظیم ساعت ریبوت**\n\n"
        "لطفاً ساعتی که می‌خواهید ریبوت انجام شود را به صورت عدد وارد کنید.\n"
        "🔢 بازه مجاز: `0` تا `23`\n\n"
        "مثال: برای ۴ صبح عدد `4` و برای ۲ بعدازظهر عدد `14` را ارسال کنید."
    )
    await safe_edit_message(update, txt, reply_markup=get_cancel_markup())
    return GET_REBOOT_TIME

async def receive_reboot_time_and_show_freq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت ساعت و نمایش دکمه‌های فرکانس"""
    try:
        hour = int(update.message.text)
        if not (0 <= hour <= 23): raise ValueError()
        
        time_str = f"{hour:02d}:00"
        context.user_data['temp_reboot_time'] = time_str 
        
        txt = (
            f"✅ ساعت انتخاب شده: `{time_str}`\n\n"
            "📅 **حالا بازه زمانی تکرار را انتخاب کنید:**"
        )
        
        kb = [
            [InlineKeyboardButton(f"هر روز ساعت {time_str}", callback_data=f'savereb_1_{time_str}')],
            [InlineKeyboardButton(f"هر ۲ روز ساعت {time_str}", callback_data=f'savereb_2_{time_str}')],
            [InlineKeyboardButton(f"هفته‌ای یکبار (۷ روز)", callback_data=f'savereb_7_{time_str}')],
            [InlineKeyboardButton(f"هر ۲ هفته یکبار", callback_data=f'savereb_14_{time_str}')],
            [InlineKeyboardButton(f"ماهانه (۳۰ روز)", callback_data=f'savereb_30_{time_str}')],
            [InlineKeyboardButton("🔙 انصراف", callback_data='cancel_flow')]
        ]
        
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return ConversationHandler.END 
        
    except ValueError:
        await update.message.reply_text("❌ عدد نامعتبر! لطفاً عددی بین 0 تا 23 وارد کنید.")
        return GET_REBOOT_TIME

async def save_auto_reboot_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره نهایی تنظیمات ریبوت"""
    query = update.callback_query
    data = query.data
    uid = update.effective_user.id
    
    if data == 'disable_reboot':
        db.set_setting(uid, 'auto_reboot_config', 'OFF')
        await query.answer("✅ ریبوت خودکار غیرفعال شد.", show_alert=True)
        await auto_reboot_menu(update, context)
        return

    parts = data.split('_')
    days = parts[1]
    time_str = parts[2]
    
    config_str = f"{days}|{time_str}" 
    db.set_setting(uid, 'auto_reboot_config', config_str)
    db.set_setting(uid, 'last_reboot_date', '2000-01-01') 
    
    await query.answer(f"✅ تنظیم شد: هر {days} روز ساعت {time_str}")
    await auto_reboot_menu(update, context)
async def startup_whitelist_job(context: ContextTypes.DEFAULT_TYPE):
    """این تابع یک بار اول کار اجرا می‌شود تا آی‌پی ربات را در همه سرورها وایت کند"""
    loop = asyncio.get_running_loop()
    
    bot_ip = await loop.run_in_executor(None, ServerMonitor.get_bot_public_ip)
    if not bot_ip:
        logger.error("❌ Could not fetch Bot IP for Whitelisting.")
        return

    logger.info(f"🛡 Starting Global IP Whitelist (Bot IP: {bot_ip})...")
    
    with db.get_connection() as conn:
        servers = conn.execute("SELECT * FROM servers").fetchall()

    count = 0
    for srv in servers:
        try:
            real_pass = sec.decrypt(srv['password'])
            await loop.run_in_executor(
                None, 
                ServerMonitor.whitelist_bot_ip, 
                srv['ip'], srv['port'], srv['username'], real_pass, bot_ip
            )
            count += 1
        except Exception as e:
            logger.error(f"Failed to whitelist on {srv['name']}: {e}")
            
    logger.info(f"✅ Whitelist process finished for {count} servers.")
# --- تابع اجرایی جاب (Job) ---
async def auto_scheduler_job(context: ContextTypes.DEFAULT_TYPE):
    """این تابع هر دقیقه اجرا می‌شود و چک می‌کند آیا وقت عملیات رسیده؟"""
    loop = asyncio.get_running_loop()
    users = await loop.run_in_executor(None, db.get_all_users)
    now = time.time()
    
    # زمان فعلی ایران
    tehran_now = get_tehran_datetime()
    current_hhmm = tehran_now.strftime("%H:%M")
    today_date_str = tehran_now.strftime("%Y-%m-%d")
    today_date_obj = datetime.strptime(today_date_str, "%Y-%m-%d").date()

    for user in users:
        uid = user['user_id']
        
        # 1. چک کردن آپدیت خودکار (بدون تغییر)
        up_interval = db.get_setting(uid, 'auto_update_hours')
        if up_interval and up_interval != '0':
            last_run = int(db.get_setting(uid, 'last_auto_update_run') or 0)
            interval_sec = int(up_interval) * 3600
            if now - last_run > interval_sec:
                servers = db.get_all_user_servers(uid)
                active = [s for s in servers if s['is_active']]
                if active:
                    try: await context.bot.send_message(uid, f"🔄 **شروع آپدیت خودکار ({up_interval} ساعته)...**")
                    except: pass
                    asyncio.create_task(run_global_commands_background(context, uid, active, 'update'))
                db.set_setting(uid, 'last_auto_update_run', int(now))

        # 2. چک کردن ریبوت خودکار (لاجیک جدید)
        # فرمت کانفیگ: "DAYS|HH:MM"
        reb_config = db.get_setting(uid, 'auto_reboot_config')
        
        if reb_config and reb_config != 'OFF' and '|' in reb_config:
            try:
                interval_days_str, target_time = reb_config.split('|')
                interval_days = int(interval_days_str)
                
                # اگر ساعت فعلی با ساعت تنظیم شده یکی بود
                if current_hhmm == target_time:
                    last_reb_str = db.get_setting(uid, 'last_reboot_date') or '2000-01-01'
                    last_reb_date = datetime.strptime(last_reb_str, "%Y-%m-%d").date()
                    
                    # محاسبه فاصله روزها
                    days_diff = (today_date_obj - last_reb_date).days
                    
                    # اگر تعداد روزهای گذشته >= فاصله تنظیم شده باشد
                    if days_diff >= interval_days:
                        servers = db.get_all_user_servers(uid)
                        active = [s for s in servers if s['is_active']]
                        if active:
                            try: await context.bot.send_message(uid, f"⚠️ **شروع ریبوت خودکار (هر {interval_days} روز - {target_time})...**")
                            except: pass
                            for s in active:
                                asyncio.create_task(
                                    run_background_ssh_task(
                                        context, uid, 
                                        ServerMonitor.run_remote_command, s['ip'], s['port'], s['username'], sec.decrypt(s['password']), "reboot"
                                    )
                                )
                        # بروزرسانی تاریخ آخرین اجرا به امروز
                        db.set_setting(uid, 'last_reboot_date', today_date_str)
            except Exception as e:
                logger.error(f"Auto Reboot Error for {uid}: {e}")
async def auto_backup_send_job(context: ContextTypes.DEFAULT_TYPE):
    """ارسال خودکار بکاپ هر یک ساعت"""
    chat_id = SUPER_ADMIN_ID
    if not chat_id: return

    # 1. اطمینان از ذخیره شدن تمام داده‌ها روی دیسک
    try:
        with db.get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(FULL);")
    except Exception as e:
        logger.error(f"Backup Checkpoint Error: {e}")

    # 2. آماده‌سازی فایل و ارسال
    timestamp = get_tehran_datetime().strftime("%Y-%m-%d_%H-%M")
    caption = (
        f"📦 **بکاپ خودکار ساعتی**\n"
        f"📅 زمان: `{get_jalali_str()}`\n"
        f"🤖 دیتابیس ربات"
    )

    try:
        with open(DB_NAME, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=f"backup_{timestamp}.db",
                caption=caption,
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"Auto Backup Send Failed: {e}")
async def save_auto_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ذخیره تنظیمات آپدیت خودکار"""
    query = update.callback_query
    uid = update.effective_user.id
    hours = query.data.split('_')[2]
    
    db.set_setting(uid, 'auto_update_hours', hours)
    
    if hours == '0':
        msg = "❌ آپدیت خودکار غیرفعال شد."
    else:
        msg = f"✅ آپدیت خودکار تنظیم شد: هر {hours} ساعت."
        
    try: await query.answer(msg, show_alert=True)
    except: pass
    
    await auto_update_menu(update, context)
# ==============================================================================
# 💰 WALLET & PAYMENT SYSTEM
# ==============================================================================

async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی اصلی کیف پول و خرید اشتراک"""
    if update.callback_query: await update.callback_query.answer()
    
    uid = update.effective_user.id
    user = db.get_user(uid)
    
    # تعیین نوع اشتراک فعلی
    plan_names = {0: 'پایه (رایگان)', 1: 'برنزی 🥉', 2: 'نقره‌ای 🥈', 3: 'طلایی 🥇'}
    current_plan = plan_names.get(user['plan_type'], 'نامشخص')
    
    txt = (
        f"💎 **فروشگاه و کیف پول سونار**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"👤 وضعیت فعلی شما:\n"
        f"🏷 اشتراک: **{current_plan}**\n"
        f"🖥 لیمیت سرور: `{user['server_limit']} عدد`\n"
        f"📅 انقضا: `{user['expiry_date']}`\n\n"
        f"🛍 **لیست اشتراک‌های قابل خرید:**\n\n"
        
        f"🥉 **اشتراک برنزی**\n"
        f"├ 🖥 5 سرور\n"
        f"├ ⏳ 30 روزه\n"
        f"└ 💰 {SUBSCRIPTION_PLANS['bronze']['price']:,} تومان\n\n"
        
        f"🥈 **اشتراک نقره‌ای**\n"
        f"├ 🖥 10 سرور\n"
        f"├ ⏳ 30 روزه\n"
        f"└ 💰 {SUBSCRIPTION_PLANS['silver']['price']:,} تومان\n\n"
        
        f"🥇 **اشتراک طلایی**\n"
        f"├ 🖥 15 سرور\n"
        f"├ ⏳ 30 روزه\n"
        f"└ 💰 {SUBSCRIPTION_PLANS['gold']['price']:,} تومان\n"
    )
    
    kb = [
        [InlineKeyboardButton("🥉 خرید برنزی", callback_data='buy_plan_bronze')],
        [InlineKeyboardButton("🥈 خرید نقره‌ای", callback_data='buy_plan_silver')],
        [InlineKeyboardButton("🥇 خرید طلایی", callback_data='buy_plan_gold')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='main_menu')]
    ]
    
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def select_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انتخاب روش پرداخت"""
    query = update.callback_query
    plan_key = query.data.split('_')[2]  # buy_plan_bronze -> bronze
    plan = SUBSCRIPTION_PLANS[plan_key]
    
    context.user_data['selected_plan'] = plan_key
    
    txt = (
        f"🛍 **تایید فاکتور خرید**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"📦 سرویس: {plan['name']}\n"
        f"💰 مبلغ قابل پرداخت: `{plan['price']:,} تومان`\n\n"
        f"💳 **لطفاً روش پرداخت را انتخاب کنید:**"
    )
    
    kb = [
        [InlineKeyboardButton("💳 کارت به کارت (Toman)", callback_data='pay_method_card')],
        [InlineKeyboardButton("💎 ارز دیجیتال (TRX/USDT)", callback_data='pay_method_tron')],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='wallet_menu')]
    ]
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def show_payment_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش اطلاعات پرداخت (داینامیک از دیتابیس)"""
    query = update.callback_query
    method_type = query.data.split('_')[2] # card or tron (که ما در دیتابیس card/crypto داریم)
    
    # مپ کردن دکمه‌های قدیمی به تایپ‌های دیتابیس
    db_type = 'card' if method_type == 'card' else 'crypto'
    
    plan_key = context.user_data.get('selected_plan')
    if not plan_key:
        await wallet_menu(update, context)
        return

    plan = SUBSCRIPTION_PLANS[plan_key]
    user_id = update.effective_user.id
    
    # دریافت روش‌های فعال از دیتابیس
    methods = db.get_payment_methods(db_type)
    
    if not methods:
        await safe_edit_message(update, "❌ متاسفانه در حال حاضر هیچ روش پرداختی برای این گزینه فعال نیست.\nلطفاً با پشتیبانی تماس بگیرید.")
        return

    # ثبت سفارش اولیه
    pay_id = db.create_payment(user_id, plan_key, plan['price'], method_type)
    
    details_txt = ""
    if db_type == 'card':
        details_txt = f"💳 **شماره کارت‌های فعال:**\n\n"
        for m in methods:
            details_txt += (
                f"🏦 **{m['network']}**\n"
                f"👤 {m['holder_name']}\n"
                f"🔢 `{m['address']}`\n"
                f"──────────────\n"
            )
        amount_txt = f"💰 مبلغ قابل پرداخت: `{plan['price']:,} تومان`"
        
    else: # Crypto
        details_txt = f"💎 **آدرس‌های واریز (Crypto):**\n\n"
        for m in methods:
            details_txt += (
                f"🪙 **شبکه: {m['network']}**\n"
                f"🔗 آدرس:\n`{m['address']}`\n"
                f"(روی آدرس بزنید کپی می‌شود)\n"
                f"──────────────\n"
            )
        # اینجا مبلغ تومانی است. اگر بخواهید تتری باشد باید نرخ تبدیل داشته باشید
        # فعلاً همان تومانی را نمایش می‌دهیم
        amount_txt = f"💰 مبلغ معادل تومن: `{plan['price']:,} تومان`\n⚠️ لطفاً معادل تتری/ارزی را محاسبه و واریز کنید."

    txt = (
        f"{details_txt}"
        f"{amount_txt}\n\n"
        f"📝 **دستورالعمل:**\n"
        f"۱. مبلغ را به یکی از روش‌های بالا واریز کنید.\n"
        f"۲. اسکرین‌شات تراکنش را آماده کنید.\n"
        f"۳. دکمه **'✅ پرداخت کردم'** را بزنید."
    )
    
    kb = [
        [InlineKeyboardButton("✅ پرداخت کردم (ارسال رسید)", callback_data=f'confirm_pay_{pay_id}')],
        [InlineKeyboardButton("🔙 انصراف", callback_data='wallet_menu')]
    ]
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def ask_for_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۱: درخواست ارسال رسید از کاربر"""
    query = update.callback_query
    # فرمت دیتا: confirm_pay_ID
    pay_id = query.data.split('_')[2]
    
    # ذخیره آیدی پرداخت در حافظه موقت برای مرحله بعد
    context.user_data['current_pay_id'] = pay_id
    
    txt = (
        "📸 **لطفاً تصویر رسید پرداخت را ارسال کنید.**\n\n"
        "می‌توانید عکس بگیرید یا فایل (Screenshot) بفرستید.\n"
        "برای انصراف دکمه زیر را بزنید."
    )
    
    await safe_edit_message(update, txt, reply_markup=get_cancel_markup())
    return GET_RECEIPT

async def process_receipt_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مرحله ۲: دریافت عکس، ذخیره و ارسال برای ادمین"""
    pay_id = context.user_data.get('current_pay_id')
    if not pay_id:
        await update.message.reply_text("❌ خطای نشست. لطفاً دوباره تلاش کنید.")
        return ConversationHandler.END

    user = update.effective_user
    
    # پیدا کردن اطلاعات پرداخت از دیتابیس
    with db.get_connection() as conn:
        pay_info = conn.execute("SELECT * FROM payments WHERE id=?", (pay_id,)).fetchone()
    
    if not pay_info:
        await update.message.reply_text("❌ تراکنش یافت نشد.")
        return ConversationHandler.END

    # تشخیص نوع فایل ارسالی (عکس فشرده یا فایل)
    if update.message.photo:
        # همیشه باکیفیت‌ترین عکس (آخرین در لیست) را برمی‌داریم
        file_id = update.message.photo[-1].file_id
        is_document = False
    elif update.message.document:
        file_id = update.message.document.file_id
        is_document = True
    else:
        await update.message.reply_text("❌ لطفاً فقط **عکس** یا **فایل تصویری** ارسال کنید.")
        return GET_RECEIPT

    # پیام تشکر به کاربر
    await update.message.reply_text(
        "✅ **رسید شما دریافت شد!**\n\n"
        "مدیران سیستم پس از بررسی صحت پرداخت، اشتراک شما را فعال خواهند کرد.\n"
        "این فرآیند معمولاً کمتر از ۱ ساعت زمان می‌برد.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data='main_menu')]])
    )

    # --- ارسال به ادمین ---
    plan = SUBSCRIPTION_PLANS.get(pay_info['plan_type'])
    plan_name = plan['name'] if plan else "Unknown"
    
    admin_caption = (
        f"💰 **درخواست پرداخت جدید (همراه با رسید)**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"👤 کاربر: {user.full_name} (`{user.id}`)\n"
        f"📦 سرویس: {plan_name}\n"
        f"💵 مبلغ: {pay_info['amount']:,}\n"
        f"💳 روش: {pay_info['method']}\n"
        f"🔢 شناسه پرداخت: `{pay_id}`\n\n"
        f"⚠️ لطفاً رسید را چک کنید و تصمیم بگیرید."
    )
    
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید و فعال‌سازی", callback_data=f'admin_approve_pay_{pay_id}')],
        [InlineKeyboardButton("❌ رد کردن (فیک)", callback_data=f'admin_reject_pay_{pay_id}')]
    ])

    try:
        if is_document:
            await context.bot.send_document(chat_id=SUPER_ADMIN_ID, document=file_id, caption=admin_caption, reply_markup=admin_kb, parse_mode='Markdown')
        else:
            await context.bot.send_photo(chat_id=SUPER_ADMIN_ID, photo=file_id, caption=admin_caption, reply_markup=admin_kb, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to send receipt to admin: {e}")
        # اگر ارسال عکس شکست خورد، متنی بفرست
        await context.bot.send_message(chat_id=SUPER_ADMIN_ID, text=admin_caption + "\n\n❌ (عکس رسید ارسال نشد، خطا در تلگرام)", reply_markup=admin_kb)

    return ConversationHandler.END

async def admin_approve_payment_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تایید نهایی توسط ادمین"""
    query = update.callback_query
    pay_id = query.data.split('_')[3]
    
    res = db.approve_payment(pay_id)
    
    if res:
        user_id, plan_name = res
        await safe_edit_message(update, f"✅ پرداخت #{pay_id} تایید شد.\nسرویس {plan_name} برای کاربر فعال گردید.")
        try:
            await context.bot.send_message(chat_id=user_id, text=f"🎉 **تبریک! پرداخت شما تایید شد.**\n\n✅ اشتراک **{plan_name}** فعال شد.")
        except: pass
    else:
        await safe_edit_message(update, "❌ خطا: این پرداخت قبلاً تایید شده است.")

async def admin_reject_payment_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pay_id = update.callback_query.data.split('_')[3]
    await safe_edit_message(update, f"❌ پرداخت #{pay_id} رد شد.")

async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی سیستم دعوت پیشرفته"""
    if update.callback_query: await update.callback_query.answer()
    
    uid = update.effective_user.id
    user = db.get_user(uid)
    bot_username = context.bot.username
    
    invite_link = f"https://t.me/{bot_username}?start={uid}"
    ref_count = user['referral_count'] if user['referral_count'] else 0
    
    txt = (
        f"💎 **کمپین بزرگ دعوت دوستان**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"دوستات رو دعوت کن، سرور رایگان بگیر! 🎁\n\n"
        f"🔰 **قوانین و پاداش‌ها:**\n"
        f"به ازای هر نفری که با لینک شما عضو شود:\n\n"
        f"1️⃣ **+10 روز** به اعتبار کل اکانتت اضافه میشه ⏳\n"
        f"2️⃣ **+1 عدد** ظرفیت سرور هدیه می‌گیری 🖥\n"
        f"   ╰ *(نکته: ظرفیت هدیه ۱۰ روزه است و بعد از آن منقضی می‌شود)*\n\n"
        f"📊 **عملکرد شما:**\n"
        f"👥 تعداد زیرمجموعه: `{ref_count} نفر`\n"
        f"📅 اعتبار فعلی شما: `{user['expiry_date']}`\n\n"
        f"🔗 **لینک اختصاصی شما (لمس کنید):**\n"
        f"`{invite_link}`"
    )
    
    kb = [
        [InlineKeyboardButton("📲 اشتراک‌گذاری سریع", url=f"https://t.me/share/url?url={invite_link}&text=ربات%20مدیریت%20سرور%20حرفه%20ای%20سونار")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data='main_menu')]
    ]
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))
def main():
    print("🚀 SONAR ULTRA PRO RUNNING...")
    
    # تنظیمات اپلیکیشن با تایم‌اوت‌های افزایش یافته برای پایداری در شبکه
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .connect_timeout(60.0)  # 60 ثانیه انتظار برای اتصال
        .read_timeout(60.0)     # 60 ثانیه انتظار برای خواندن
        .write_timeout(60.0)    # 60 ثانیه انتظار برای نوشتن
        .build()
    )
    app.add_error_handler(error_handler)

    # فیلتر متن برای هندلرها (متن باشد اما دستور نباشد)
    text_filter = filters.TEXT & ~filters.COMMAND

    # ==========================================================================
    # 1. CONVERSATION HANDLER (مدیریت مکالمات چند مرحله‌ای)
    # ==========================================================================
    conv_handler = ConversationHandler(
        allow_reentry=True, 
        entry_points=[
            # --- Admin Panel Actions ---
            CallbackQueryHandler(add_new_user_start, pattern='^add_new_admin$'), 
            CallbackQueryHandler(admin_user_actions, pattern='^admin_u_limit_'),
            CallbackQueryHandler(admin_user_actions, pattern='^admin_u_settime_'),
            CallbackQueryHandler(admin_search_start, pattern='^admin_search_start$'),
            CallbackQueryHandler(admin_backup_restore_start, pattern='^admin_backup_restore_start$'),
            CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$'),
            
            # --- Payment Management (Admin) ---
            CallbackQueryHandler(admin_payment_settings, pattern='^admin_pay_settings$'),
            CallbackQueryHandler(add_pay_method_start, pattern='^add_pay_method_'),
            CallbackQueryHandler(ask_for_receipt, pattern='^confirm_pay_'),

            # --- Group & Server Management ---
            CallbackQueryHandler(add_group_start, pattern='^add_group$'),
            CallbackQueryHandler(add_server_start_menu, pattern='^add_server$'),
            
            # --- Tools & Settings ---
            CallbackQueryHandler(manual_ping_start, pattern='^manual_ping_start$'),
            CallbackQueryHandler(add_channel_start, pattern='^add_channel$'),
            CallbackQueryHandler(ask_custom_interval, pattern='^setcron_custom$'),
            CallbackQueryHandler(edit_expiry_start, pattern='^act_editexpiry_'),
            CallbackQueryHandler(ask_terminal_command, pattern='^cmd_terminal_'),
            
            # --- Resource Limits ---
            CallbackQueryHandler(resource_settings_menu, pattern='^settings_thresholds$'),
            CallbackQueryHandler(ask_cpu_limit, pattern='^set_cpu_limit$'),
            CallbackQueryHandler(ask_ram_limit, pattern='^set_ram_limit$'),
            CallbackQueryHandler(ask_disk_limit, pattern='^set_disk_limit$'),
            
            # --- User & Reports ---
            CallbackQueryHandler(user_profile_menu, pattern='^user_profile$'),
            CallbackQueryHandler(web_token_action, pattern='^gen_web_token$'),
            CallbackQueryHandler(send_global_full_report_action, pattern='^act_global_full_report$'),
            
            # --- Auto Reboot ---
            CallbackQueryHandler(ask_reboot_time, pattern='^start_set_reboot$'),
            CallbackQueryHandler(auto_reboot_menu, pattern='^auto_reboot_menu$'),
            CallbackQueryHandler(save_auto_reboot_final, pattern='^disable_reboot$'),
            CallbackQueryHandler(save_auto_reboot_final, pattern='^savereb_'),

            # --- Placeholders ---
            CallbackQueryHandler(lambda u,c: u.callback_query.answer("🔜 به‌زودی!", show_alert=True), pattern='^dev_feature$')
        ],
        states={
            # --- Add Server States ---
            SELECT_ADD_METHOD: [
                CallbackQueryHandler(add_server_step_start, pattern='^add_method_step$'),
                CallbackQueryHandler(add_server_linear_start, pattern='^add_method_linear$')
            ],
            GET_LINEAR_DATA: [MessageHandler(text_filter, process_linear_data)],
            
            # --- Admin States ---
            ADD_ADMIN_ID: [MessageHandler(text_filter, get_new_user_id)],
            ADD_ADMIN_DAYS: [MessageHandler(text_filter, get_new_user_days)],
            ADMIN_SET_LIMIT: [MessageHandler(text_filter, admin_set_limit_handler)],
            ADMIN_SET_TIME_MANUAL: [MessageHandler(text_filter, admin_set_days_handler)],
            ADMIN_SEARCH_USER: [MessageHandler(text_filter, admin_search_handler)],
            ADMIN_RESTORE_DB: [MessageHandler(filters.Document.ALL, admin_backup_restore_handler)],
            GET_BROADCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_broadcast_send)],

            # --- Payment Add States (NEW) ---
            ADD_PAY_NET: [MessageHandler(text_filter, get_pay_network)],
            ADD_PAY_ADDR: [MessageHandler(text_filter, get_pay_address)],
            ADD_PAY_HOLDER: [MessageHandler(text_filter, get_pay_holder)],
            

            # --- General Server States ---
            GET_GROUP_NAME: [MessageHandler(text_filter, get_group_name)],
            GET_NAME: [MessageHandler(text_filter, get_srv_name)],
            GET_IP: [MessageHandler(text_filter, get_srv_ip)],
            GET_PORT: [MessageHandler(text_filter, get_srv_port)],
            GET_USER: [MessageHandler(text_filter, get_srv_user)],
            GET_PASS: [MessageHandler(text_filter, get_srv_pass)],
            GET_EXPIRY: [MessageHandler(text_filter, get_srv_expiry)],
            SELECT_GROUP: [CallbackQueryHandler(select_group)],
            
            # --- Tools States ---
            GET_MANUAL_HOST: [MessageHandler(text_filter, perform_manual_ping)],
            GET_CHANNEL_FORWARD: [MessageHandler(filters.ALL & ~filters.COMMAND, get_channel_forward)],
            GET_CUSTOM_INTERVAL: [MessageHandler(text_filter, set_custom_interval_action)],
            GET_CHANNEL_TYPE: [CallbackQueryHandler(set_channel_type_action, pattern='^type_')],
            EDIT_SERVER_EXPIRY: [MessageHandler(text_filter, edit_expiry_save)],
            GET_REMOTE_COMMAND: [
                MessageHandler(text_filter, run_terminal_action),
                CallbackQueryHandler(close_terminal_session, pattern='^exit_terminal$')
            ],
            
            # --- Resource Limit States ---
            GET_CPU_LIMIT: [MessageHandler(text_filter, save_cpu_limit)],
            GET_RAM_LIMIT: [MessageHandler(text_filter, save_ram_limit)],
            GET_DISK_LIMIT: [MessageHandler(text_filter, save_disk_limit)],

            # --- Auto Reboot State ---
            GET_REBOOT_TIME: [MessageHandler(text_filter, receive_reboot_time_and_show_freq)],
            GET_RECEIPT: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, process_receipt_upload)
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel_handler_func),
            CallbackQueryHandler(cancel_handler_func, pattern='^cancel_flow$'),
            CommandHandler('start', start)
        ]
    )
    app.add_handler(conv_handler)

    # ==========================================================================
    # 2. SECRET KEY MANAGEMENT (بازگردانی کلید امنیتی)
    # ==========================================================================
    key_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_key_restore_start, pattern='^admin_key_restore_start$')],
        states={
            ADMIN_RESTORE_KEY: [MessageHandler(filters.Document.ALL, admin_key_restore_handler)]
        },
        fallbacks=[CallbackQueryHandler(cancel_handler_func, pattern='^cancel_flow$')]
    )
    app.add_handler(key_conv_handler)
    app.add_handler(CallbackQueryHandler(admin_key_backup_get, pattern='^admin_key_backup_get$'))

    # ==========================================================================
    # 3. COMMAND HANDLERS (دستورات متنی)
    # ==========================================================================
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('dashboard', dashboard_command))
    app.add_handler(CommandHandler('setting', settings_command))
    
    # ==========================================================================
    # 4. CALLBACK HANDLERS (دکمه‌های شیشه‌ای)
    # ==========================================================================
    
    # --- Main Menu ---
    app.add_handler(CallbackQueryHandler(main_menu, pattern='^main_menu$'))
    
    # --- Admin Panel ---
    app.add_handler(CallbackQueryHandler(admin_panel_main, pattern='^admin_panel_main$'))
    app.add_handler(CallbackQueryHandler(admin_users_list, pattern='^admin_users_page_'))
    app.add_handler(CallbackQueryHandler(admin_user_manage, pattern='^admin_u_manage_'))
    app.add_handler(CallbackQueryHandler(admin_user_actions, pattern='^admin_u_'))
    app.add_handler(CallbackQueryHandler(admin_users_text, pattern='^admin_users_text$'))
    app.add_handler(CallbackQueryHandler(admin_backup_get, pattern='^admin_backup_get$'))
    
    # --- Payment Deletion (Admin) ---
    app.add_handler(CallbackQueryHandler(delete_payment_method_action, pattern='^del_pay_method_'))

    # --- Server & Group Actions ---
    app.add_handler(CallbackQueryHandler(groups_menu, pattern='^groups_menu$'))
    app.add_handler(CallbackQueryHandler(delete_group_action, pattern='^delgroup_'))
    app.add_handler(CallbackQueryHandler(list_groups_for_servers, pattern='^list_groups_for_servers$'))
    app.add_handler(CallbackQueryHandler(show_servers, pattern='^(listsrv_|list_all)'))
    app.add_handler(CallbackQueryHandler(server_detail, pattern='^detail_'))
    app.add_handler(CallbackQueryHandler(server_actions, pattern='^act_'))
    app.add_handler(CallbackQueryHandler(manage_servers_list, pattern='^manage_servers_list$'))
    app.add_handler(CallbackQueryHandler(toggle_server_active_action, pattern='^toggle_active_'))

    # --- Wallet, Payment & Referral ---
    app.add_handler(CallbackQueryHandler(wallet_menu, pattern='^wallet_menu$'))
    app.add_handler(CallbackQueryHandler(referral_menu, pattern='^referral_menu$'))
    app.add_handler(CallbackQueryHandler(select_payment_method, pattern='^buy_plan_'))
    app.add_handler(CallbackQueryHandler(show_payment_details, pattern='^pay_method_'))
    
    # --- Admin Payment Approval ---
    app.add_handler(CallbackQueryHandler(admin_approve_payment_action, pattern='^admin_approve_pay_'))
    app.add_handler(CallbackQueryHandler(admin_reject_payment_action, pattern='^admin_reject_pay_'))
    
    # --- Global Operations ---
    app.add_handler(CallbackQueryHandler(global_ops_menu, pattern='^global_ops_menu$'))
    app.add_handler(CallbackQueryHandler(global_action_handler, pattern='^glob_act_'))
    
    # --- Settings & Utilities ---
    app.add_handler(CallbackQueryHandler(set_dns_action, pattern='^setdns_'))
    app.add_handler(CallbackQueryHandler(channels_menu, pattern='^channels_menu$'))
    app.add_handler(CallbackQueryHandler(delete_channel_action, pattern='^delchan_'))
    app.add_handler(CallbackQueryHandler(settings_menu, pattern='^settings_menu$'))
    app.add_handler(CallbackQueryHandler(automation_settings_menu, pattern='^menu_automation$'))
    app.add_handler(CallbackQueryHandler(monitoring_settings_menu, pattern='^menu_monitoring$'))
    app.add_handler(CallbackQueryHandler(status_dashboard, pattern='^status_dashboard$'))
    app.add_handler(CallbackQueryHandler(settings_cron_menu, pattern='^settings_cron$'))
    app.add_handler(CallbackQueryHandler(set_cron_action, pattern='^setcron_'))
    app.add_handler(CallbackQueryHandler(toggle_down_alert, pattern='^toggle_downalert_'))
    app.add_handler(CallbackQueryHandler(send_instant_channel_report, pattern='^send_instant_report$'))
    
    
    # --- Auto Schedule Settings ---
    app.add_handler(CallbackQueryHandler(auto_update_menu, pattern='^auto_up_menu$'))
    app.add_handler(CallbackQueryHandler(save_auto_schedule, pattern='^set_autoup_'))
    app.add_handler(CallbackQueryHandler(save_auto_reboot_final, pattern='^(savereb_|disable_reboot)'))
    
    # ==========================================================================
    # 5. JOB QUEUE (وظایف زمان‌بندی شده)
    # ==========================================================================
    if app.job_queue:
        # بررسی انقضا سرورها (هر روز ساعت 8:30 صبح)
        app.job_queue.run_daily(check_expiry_job, time=dt.time(hour=8, minute=30, second=0))
        # مانیتورینگ اصلی (هر 40 ثانیه)
        app.job_queue.run_repeating(global_monitor_job, interval=DEFAULT_INTERVAL, first=10)
        # جاب اسکژولر برای آپدیت و ریبوت خودکار (هر دقیقه)
        app.job_queue.run_repeating(auto_scheduler_job, interval=60, first=20)
        # وایت‌لیست کردن آی‌پی ربات در شروع (یکبار)
        app.job_queue.run_once(startup_whitelist_job, when=10)
        # 👇👇 (بکاپ ساعتی هر 1 ساعت) 👇👇
        app.job_queue.run_repeating(auto_backup_send_job, interval=3600, first=300)
        # بررسی انقضای پاداش رفرال (هر 12 ساعت)
        app.job_queue.run_repeating(check_bonus_expiry_job, interval=43200, first=60)
    else:
        logger.error("JobQueue not available. Install python-telegram-bot[job-queue]")
    
    # اجرای ربات
    app.run_polling(drop_pending_updates=True, close_loop=False)

if __name__ == '__main__':
    main()
