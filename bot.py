import logging
import sqlite3
import os
import asyncio
import time
import warnings
import threading
import statistics
import io
import html
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

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
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler, JobQueue
)

# ==============================================================================
# ⚙️ CONFIGURATION & CONSTANTS
# ==============================================================================
TOKEN = '8089255042:AAHHqh1zJFHbj6_c5QUTPv_6thKHgNCg2NI'  # ⚠️ TOKEN
SUPER_ADMIN_ID = 585214295                               # ⚠️ ADMIN ID
DEFAULT_INTERVAL = 60
DOWN_RETRY_LIMIT = 3
DB_NAME = 'sonar_ultra_pro.db'
KEY_FILE = 'secret.key'

# --- Global Cache & State Trackers ---
SERVER_FAILURE_COUNTS = {}
LAST_REPORT_CACHE = {}
CPU_ALERT_TRACKER = {}
DAILY_REPORT_USAGE = {}

# --- Conversation States ---
(
    GET_NAME, GET_IP, GET_PORT, GET_USER, GET_PASS, SELECT_GROUP,
    GET_GROUP_NAME, GET_CHANNEL_FORWARD, GET_MANUAL_HOST,
    ADD_ADMIN_ID, ADD_ADMIN_DAYS, ADMIN_SEARCH_USER,
    ADMIN_SET_LIMIT, ADMIN_RESTORE_DB, ADMIN_SET_TIME_MANUAL,
    GET_CUSTOM_INTERVAL,
    GET_EXPIRY,
    GET_CHANNEL_TYPE,
    EDIT_SERVER_EXPIRY,
    GET_REMOTE_COMMAND,  
    GET_CPU_LIMIT, GET_RAM_LIMIT, GET_DISK_LIMIT,
    GET_BROADCAST_MSG
) = range(24)

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s', 
    level=logging.ERROR
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
        except Exception as e: # Fix 3: اضافه کردن لاگ برای خطای رمزگشایی به جای صرفاً نادیده گرفتن
            logger.error(f"Decryption failed for data: {txt[:10]}... Error: {e}") 
            return "" # Handle decryption errors gracefully


class Database:
    def __init__(self):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try: # Fix 1: اضافه کردن Try/Except برای اجرای PRAGMA تا در صورت خراب بودن دیتابیس (Malformed DB) کرش نکند.
            self.conn.execute('PRAGMA journal_mode=WAL;')
        except sqlite3.DatabaseError as e:
            logger.error(f"Error setting PRAGMA journal_mode=WAL: {e}")
        self.create_tables()
        self.migrate()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None # Fix 2: اتصال را به صراحت None می‌کنیم تا در هنگام Re-init مطمئن شویم که مرجع قبلی آزاد شده است.

    def create_tables(self):
        with self.lock:
            cursor = self.conn.cursor()
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
            self.conn.commit()

    def migrate(self):
        with self.lock:
            try: self.conn.execute("ALTER TABLE servers ADD COLUMN expiry_date TEXT")
            except: pass
            try: self.conn.execute("ALTER TABLE channels ADD COLUMN usage_type TEXT DEFAULT 'all'")
            except: pass
            try: self.conn.execute("ALTER TABLE users ADD COLUMN plan_type INTEGER DEFAULT 0")
            except: pass
            self.conn.commit()
            
    def toggle_user_plan(self, user_id):
        user = self.get_user(user_id)
        if not user: return 0 
        
        new_plan = 1 if user['plan_type'] == 0 else 0
        new_limit = 50 if new_plan == 1 else 2
        
        with self.lock:
            self.conn.execute('UPDATE users SET plan_type = ?, server_limit = ? WHERE user_id = ?', (new_plan, new_limit, user_id))
            self.conn.commit()
        return new_plan
    
    def add_or_update_user(self, user_id, full_name=None, days=None):
        with self.lock:
            exist = self.get_user(user_id)
            now_str = get_tehran_datetime().strftime('%Y-%m-%d %H:%M:%S')
            if exist:
                if full_name:
                    self.conn.execute('UPDATE users SET full_name = ? WHERE user_id = ?', (full_name, user_id))
                if days is not None:
                    expiry = (get_tehran_datetime() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
                    self.conn.execute('UPDATE users SET expiry_date = ? WHERE user_id = ?', (expiry, user_id))
            else:
                d = days if days else 30
                expiry = (get_tehran_datetime() + timedelta(days=d)).strftime('%Y-%m-%d %H:%M:%S')
                self.conn.execute('INSERT INTO users (user_id, full_name, added_date, expiry_date) VALUES (?, ?, ?, ?)', 
                                  (user_id, full_name, now_str, expiry))
            self.conn.commit()
            
    def update_user_limit(self, user_id, limit):
        with self.lock:
            self.conn.execute('UPDATE users SET server_limit = ? WHERE user_id = ?', (limit, user_id))
            self.conn.commit()

    def toggle_ban_user(self, user_id):
        user = self.get_user(user_id)
        if not user: return 0
        new_state = 0 if user['is_banned'] else 1
        with self.lock:
            self.conn.execute('UPDATE users SET is_banned = ? WHERE user_id = ?', (new_state, user_id))
            self.conn.commit()
        return new_state

    def get_user(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        return cursor.fetchone()

    def get_all_users_paginated(self, page=1, per_page=5):
        offset = (page - 1) * per_page
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users LIMIT ? OFFSET ?', (per_page, offset))
        users = cursor.fetchall()
        cursor.execute('SELECT COUNT(*) FROM users')
        total = cursor.fetchone()[0]
        return users, total

    def get_all_users(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users')
        return cursor.fetchall()

    def remove_user(self, user_id):
        with self.lock:
            for t in ['users', 'servers', 'groups', 'channels']:
                col = 'user_id' if t == 'users' else 'owner_id'
                self.conn.execute(f'DELETE FROM {t} WHERE {col} = ?', (user_id,))
            self.conn.commit()

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
        with self.lock:
            self.conn.execute('INSERT INTO groups (owner_id, name) VALUES (?,?)', (owner_id, name))
            self.conn.commit()

    def get_user_groups(self, owner_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM groups WHERE owner_id = ?', (owner_id,))
        return cursor.fetchall()

    def delete_group(self, group_id, owner_id):
        with self.lock:
            self.conn.execute('DELETE FROM groups WHERE id = ? AND owner_id = ?', (group_id, owner_id))
            # Fix 5: اضافه کردن شرط owner_id برای جلوگیری از به روزرسانی ناخواسته سرورهای کاربران دیگر
            self.conn.execute('UPDATE servers SET group_id = NULL WHERE group_id = ? AND owner_id = ?', (group_id, owner_id)) 
            self.conn.commit()

    # --- Server Methods ---
    def add_server(self, owner_id, group_id, data):
        # Fix 6: انتقال منطق چک کردن محدودیت سرور به داخل قفل (Lock) برای جلوگیری از Race Condition
        g_id = group_id if group_id != 0 else None
        with self.lock:
            user = self.get_user(owner_id)
            current_servers = len(self.get_all_user_servers(owner_id))
            if user and owner_id != SUPER_ADMIN_ID:
                if current_servers >= user['server_limit']:
                    raise Exception("Server Limit Reached")
            
            self.conn.execute(
                'INSERT INTO servers (owner_id, group_id, name, ip, port, username, password, expiry_date) VALUES (?,?,?,?,?,?,?,?)',
                (owner_id, g_id, data['name'], data['ip'], data['port'], data['username'], data['password'], data.get('expiry_date'))
            )
            self.conn.commit()

    def get_all_user_servers(self, owner_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM servers WHERE owner_id = ?', (owner_id,))
        return cursor.fetchall()

    def get_servers_by_group(self, owner_id, group_id):
        cursor = self.conn.cursor()
        sql = 'SELECT * FROM servers WHERE owner_id = ? AND group_id IS NULL' if group_id == 0 else 'SELECT * FROM servers WHERE owner_id = ? AND group_id = ?'
        cursor.execute(sql, (owner_id,) if group_id == 0 else (owner_id, group_id))
        return cursor.fetchall()

    def get_server_by_id(self, s_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM servers WHERE id = ?', (s_id,))
        return cursor.fetchone()

    def delete_server(self, s_id, owner_id):
        with self.lock:
            self.conn.execute('DELETE FROM servers WHERE id = ? AND owner_id = ?', (s_id, owner_id))
            self.conn.commit()

    def update_status(self, s_id, status):
        with self.lock:
            self.conn.execute('UPDATE servers SET last_status = ? WHERE id = ?', (status, s_id))
            self.conn.commit()

    def update_server_expiry(self, s_id, new_date):
        with self.lock:
            self.conn.execute('UPDATE servers SET expiry_date = ? WHERE id = ?', (new_date, s_id))
            self.conn.commit()
    
    def toggle_server_active(self, s_id, current_state):
        new_state = 0 if current_state else 1
        with self.lock:
            self.conn.execute('UPDATE servers SET is_active = ? WHERE id = ?', (new_state, s_id))
            self.conn.commit()
        return new_state

    # --- Stats & Charts ---
    def add_server_stat(self, server_id, cpu, ram):
        with self.lock:
            self.conn.execute('INSERT INTO server_stats (server_id, cpu, ram) VALUES (?, ?, ?)', (server_id, cpu, ram))
            # Keep last 24h stats only
            self.conn.execute("DELETE FROM server_stats WHERE created_at < datetime('now', '-1 day')")
            self.conn.commit()

    def get_server_stats(self, server_id):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT cpu, ram, strftime('%H:%M', created_at, '+3 hours', '+30 minutes') as time_str 
            FROM server_stats 
            WHERE server_id = ? 
            ORDER BY created_at ASC
        ''', (server_id,))
        return cursor.fetchall()

    # --- Channel & Settings Methods ---
    def add_channel(self, owner_id, chat_id, name, usage_type='all'):
        with self.lock:
            self.conn.execute('INSERT INTO channels (owner_id, chat_id, name, usage_type) VALUES (?,?,?,?)', (owner_id, chat_id, name, usage_type))
            self.conn.commit()

    def get_user_channels(self, owner_id):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM channels WHERE owner_id = ?', (owner_id,))
        return cursor.fetchall()

    def delete_channel(self, c_id, owner_id):
        with self.lock:
            self.conn.execute('DELETE FROM channels WHERE id = ? AND owner_id = ?', (c_id, owner_id))
            self.conn.commit()

    def set_setting(self, owner_id, key, value):
        with self.lock:
            self.conn.execute('REPLACE INTO settings (owner_id, key, value) VALUES (?, ?, ?)', (owner_id, key, str(value)))
            self.conn.commit()

    def get_setting(self, owner_id, key):
        cursor = self.conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE owner_id = ? AND key = ?', (owner_id, key,))
        res = cursor.fetchone()
        return res['value'] if res else None

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
        blocks = "▏▎▍▌▋▊▉█"
        if percentage < 0: percentage = 0
        if percentage > 100: percentage = 100
        full_blocks = int((percentage / 100) * length)
        remainder = (percentage / 100) * length - full_blocks
        idx = int(remainder * len(blocks))
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
                "cat /proc/net/dev | awk 'NR>2 {rx+=$2; tx+=$10} END {print rx+tx}'"
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
            
            return {'status': 'Online', 'cpu': cpu_val, 'ram': ram_val, 'disk': disk_val, 'uptime_str': uptime_str, 'uptime_sec': uptime_sec, 'traffic_gb': traffic_gb, 'error': None}
        except Exception as e:
            if client: 
                try: client.close()
                except: pass
            return {'status': 'Offline', 'error': str(e)[:50], 'uptime_sec': 0, 'traffic_gb': 0}

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
        cmd = "sudo apt-get update && (sudo apt-get install -y speedtest-cli || (sudo apt-get install -y python3-pip && pip3 install --upgrade speedtest-cli))"
        return ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=180)

    @staticmethod
    def run_speedtest(ip, port, user, password):
        return ServerMonitor.run_remote_command(ip, port, user, password, "speedtest-cli --simple", timeout=90)

    @staticmethod
    def clear_cache(ip, port, user, password):
        return ServerMonitor.run_remote_command(ip, port, user, password, "sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'", timeout=30)

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
        cmd = "sudo apt update && sudo DEBIAN_FRONTEND=noninteractive apt full-upgrade -y -o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold' && sudo apt autoremove -y && sudo apt clean"
        return ServerMonitor.run_remote_command(ip, port, user, password, cmd, timeout=600)

    @staticmethod
    def repo_update(ip, port, user, password):
        cmd = "sudo apt update && sudo DEBIAN_FRONTEND=noninteractive apt upgrade -y -o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold'"
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
    """
    Thread-safe plot generation using Object-Oriented Matplotlib interface.
    Do NOT use plt.figure() or plt.plot() here!
    """
    if not stats:
        return None
    
    try:
        # Create Figure object directly (Thread Safe)
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
        
        # Save to IO buffer
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
    if update.callback_query: await update.callback_query.answer()
    await safe_edit_message(update, "🚫 **عملیات لغو شد.**")
    await asyncio.sleep(1)
    await start(update, context)
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("❌ خطای داخلی سیستم. لطفاً دوباره تلاش کنید.")
        except: pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    full_name = update.effective_user.full_name
    context.user_data.clear()
    db.add_or_update_user(user_id, full_name=full_name, days=180)
    has_access, msg = db.check_access(user_id)

    if not has_access:
        # Fix 7: استفاده از effective_message برای ارسال پیام دسترسی مسدود، تا هم برای دستورات (Command) و هم برای Callback Query ها کار کند.
        await update.effective_message.reply_text(f"⛔️ **دسترسی مسدود است**\nعلت: {msg}", parse_mode='Markdown')
        return
    
    remaining = f"{msg} روز" if isinstance(msg, int) else "♾ نامحدود"
    
    kb = [
        [InlineKeyboardButton("👤 حساب کاربری من", callback_data='user_profile')],
        [InlineKeyboardButton("📂 گروه‌بندی", callback_data='groups_menu'),
         InlineKeyboardButton("➕ سرور جدید", callback_data='add_server')],
        [InlineKeyboardButton("📋 لیست سرورها", callback_data='list_groups_for_servers'),
         InlineKeyboardButton("📊 داشبورد شبکه", callback_data='status_dashboard')],
        [InlineKeyboardButton("🌍 چـک هـاسـت (Global)", callback_data='manual_ping_start')],
        [InlineKeyboardButton("⚙️ تنظیمات و هشدارها", callback_data='settings_menu')]
    ]
    if user_id == SUPER_ADMIN_ID: 
        kb.insert(0, [InlineKeyboardButton("🤖 مدیریت ربات", callback_data='admin_panel_main')])

    txt = (
        f"👋 **درود {full_name} عزیز**\n"
        f"🚀 **Sonar Radar Ultra Pro**\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"👤 شناسه کاربری: `{user_id}`\n"
        f"📅 اعتبار اشتراک: `{remaining}`\n"
        f"🔰 **گزینه مورد نظر را انتخاب کنید:**"
    )
    
    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return ConversationHandler.END

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE): await start(update, context)
async def user_profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: await update.callback_query.answer()
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
    await update.callback_query.answer("🚧 پنل تحت وب در حال توسعه است.\nبه زودی این قابلیت فعال می‌شود!", show_alert=True)


# ==============================================================================
# 👑 ADMIN PANEL HANDLERS
# ==============================================================================
async def admin_panel_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN_ID: return
    
    users_count = len(db.get_all_users())
    total_servers = len(db.conn.execute('SELECT id FROM servers').fetchall())
    
    kb = [
        [InlineKeyboardButton("👥 مدیریت کاربران", callback_data='admin_users_page_1')],
        [InlineKeyboardButton("➕ افزودن دستی کاربر", callback_data='add_new_admin')],
        [InlineKeyboardButton("📢 ارسال پیام همگانی", callback_data='admin_broadcast_start')],
        [InlineKeyboardButton("🔎 جستجوی کاربر", callback_data='admin_search_start'), InlineKeyboardButton("📄 لیست متنی", callback_data='admin_users_text')],
        [InlineKeyboardButton("📥 دریافت بکاپ", callback_data='admin_backup_get'), InlineKeyboardButton("📤 بازنشانی بکاپ", callback_data='admin_backup_restore_start')],
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
        await update.callback_query.answer(msg)
        await admin_user_manage(update, context, user_id=target_id)
        
    elif action == 'del':
        db.remove_user(target_id)
        await update.callback_query.answer("کاربر حذف شد.")
        await admin_users_list(update, context)
        
    elif action == 'addtime':
        db.add_or_update_user(target_id, days=30)
        await update.callback_query.answer("30 روز تمدید شد.")
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
        msg = "✅ کاربر به پریمیوم ارتقا یافت (لیمیت: 50)" if new_plan == 1 else "⬇️ کاربر به عادی تغییر یافت (لیمیت: 2)"
        await update.callback_query.answer(msg, show_alert=True)
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
        await update.callback_query.message.reply_document(document=open("users_list.txt", "rb"), caption="لیست کاربران")
        os.remove("users_list.txt")
    else:
        await update.callback_query.message.reply_text(txt)

# --- Backup & Restore ---
async def admin_backup_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("در حال ارسال فایل...")
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
    f = await doc.get_file()
    await f.download_to_drive(DB_NAME)
    db.close()
    db.__init__()
    await update.message.reply_text("✅ دیتابیس با موفقیت بازنشانی شد.")
    await start(update, context)
    return ConversationHandler.END

# --- Add New User Handlers ---
async def add_new_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
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
            await update.callback_query.message.reply_text("✅ **اتصال موفق! سرور ذخیره شد.**", parse_mode='Markdown')
        except Exception as e: await update.callback_query.message.reply_text(f"❌ خطا: {e}")
    else:
        await update.callback_query.message.reply_text(f"❌ **عدم اتصال به سرور!**\n\n⚠️ خطا: `{res['error']}`", parse_mode='Markdown')
    await start(update, context)
    return ConversationHandler.END

async def list_groups_for_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    groups = db.get_user_groups(update.effective_user.id)
    kb = [[InlineKeyboardButton("🔗 همه سرورها (یکجا)", callback_data='list_all')]] + [[InlineKeyboardButton(f"📁 {g['name']}", callback_data=f'listsrv_{g["id"]}')] for g in groups]
    kb.append([InlineKeyboardButton("📄 سرورهای بدون گروه", callback_data='listsrv_0')])
    kb.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data='main_menu')])
    await safe_edit_message(update, "🗂 **پوشه مورد نظر را انتخاب کنید:**", reply_markup=InlineKeyboardMarkup(kb))

async def show_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    uid, data = update.effective_user.id, update.callback_query.data
    servers = db.get_all_user_servers(uid) if data == 'list_all' else db.get_servers_by_group(uid, int(data.split('_')[1]))
    if not servers: 
        await update.callback_query.answer("⚠️ این پوشه خالی است!", show_alert=True)
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
        await update.callback_query.answer()
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
    txt = f"📊 **داشبورد وضعیت شبکه**\n📆 `{get_jalali_str()}`\n➖➖➖➖➖➖➖➖➖➖\n\n"
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
        await update.callback_query.answer()

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
    
    if is_premium:
        btn_term = InlineKeyboardButton("📟 ترمینال", callback_data=f'cmd_terminal_{sid}')
        btn_script = InlineKeyboardButton("🛠 اسکریپت", callback_data=f'act_installscript_{sid}')
    else:
        btn_term = InlineKeyboardButton("🔒 ترمینال", callback_data=f'act_locked_terminal_{sid}') 
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
        [btn_term, btn_script],
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
        await update.callback_query.answer("❌ سرور یافت نشد!", show_alert=True)
        return

    uid = update.effective_user.id
    user = db.get_user(uid)
    is_premium = True if user['plan_type'] == 1 or uid == SUPER_ADMIN_ID else False
    
    LOCKED_FEATURES = ['installscript'] 

    if act in LOCKED_FEATURES and not is_premium:
        await update.callback_query.answer("🔒 این قابلیت مخصوص کاربران پریمیوم است!", show_alert=True)
        return

    if srv['password']:
        real_pass = sec.decrypt(srv['password'])
    else:
        real_pass = ""
        
    loop = asyncio.get_running_loop()
    
    if act == 'del':
        db.delete_server(sid, update.effective_user.id)
        await update.callback_query.answer("✅ سرور با موفقیت حذف شد.")
        await list_groups_for_servers(update, context)

    elif act == 'reboot':
        await update.callback_query.answer("⚠️ دستور ریبوت ارسال شد.")
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
        await update.callback_query.answer("🧹 کش رم پاکسازی شد.")
        await loop.run_in_executor(None, ServerMonitor.clear_cache, srv['ip'], srv['port'], srv['username'], real_pass)
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
       await update.callback_query.answer("🔒 ترمینال مخصوص کاربران پریمیوم است.\nبرای دسترسی ارتقا دهید.", show_alert=True)

    elif act == 'installscript':
        await update.callback_query.answer("🚧 این بخش در حال توسعه است!", show_alert=True)

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
        await query.answer("❌ ابتدا یک کانال ثبت کنید!", show_alert=True)
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
    await update.callback_query.answer()
    servers = db.get_all_user_servers(update.effective_user.id)
    kb = [[InlineKeyboardButton(f"{'🟢' if s['is_active'] else '🔴'} | {s['name']}", callback_data=f'toggle_active_{s["id"]}')] for s in servers]
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data='status_dashboard')])
    await safe_edit_message(update, "🛠 **مدیریت مانیتورینگ:**\nبا کلیک روی هر سرور، مانیتورینگ آن را روشن/خاموش کنید.", reply_markup=InlineKeyboardMarkup(kb))

async def toggle_server_active_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = int(update.callback_query.data.split('_')[2])
    srv = db.get_server_by_id(sid)
    db.toggle_server_active(sid, srv['is_active'])
    await update.callback_query.answer(f"وضعیت {srv['name']} تغییر کرد.")
    await manage_servers_list(update, context)


# ==============================================================================
# 📅 EXPIRY & TERMINAL HANDLERS
# ==============================================================================
async def edit_expiry_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
    await query.answer()
    
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
    if update.callback_query: await update.callback_query.answer()
    sid = context.user_data.get('term_sid')
    await server_detail(update, context, custom_sid=sid)
    return ConversationHandler.END

async def manual_ping_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "🔎 **IP/Domain:**", reply_markup=get_cancel_markup())
    return GET_MANUAL_HOST

async def perform_manual_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🌍 Check-Host...")
    ok, data = await asyncio.get_running_loop().run_in_executor(None, ServerMonitor.check_host_api, update.message.text)
    report = ServerMonitor.format_check_host_results(data) if ok else f"❌ {data}"
    await context.bot.send_message(chat_id=msg.chat_id, text=report, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data='main_menu')]]))
    return ConversationHandler.END


# ==============================================================================
# ⚙️ SETTINGS & CONFIG HANDLERS
# ==============================================================================
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await settings_menu(update, context)

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if update.callback_query: await update.callback_query.answer()
    
    down_alert = db.get_setting(uid, 'down_alert_enabled') or '1'
    alert_icon = "🔔 روشن" if down_alert == '1' else "🔕 خاموش"
    
    kb = [
        [InlineKeyboardButton("📢 مدیریت کانال‌های هشدار", callback_data='channels_menu')],
        [InlineKeyboardButton("⏰ بازه گزارش خودکار", callback_data='settings_cron')],
        [InlineKeyboardButton("🎚 تنظیم آستانه هشدار (Resource)", callback_data='settings_thresholds')],
        [InlineKeyboardButton(f"🚨 هشدار قطعی سرور: {alert_icon}", callback_data=f'toggle_downalert_{"0" if down_alert=="1" else "1"}')],
        [
            InlineKeyboardButton("🔄 آپدیت خودکار (Dev)", callback_data='dev_feature'),
            InlineKeyboardButton("⚠️ ریبوت خودکار (Dev)", callback_data='dev_feature')
        ],
        [
            InlineKeyboardButton("📡 دریافت اطلاعات فوری (ارسال به کانال)", callback_data='act_global_full_report')
        ],
        [InlineKeyboardButton("🇬🇧 زبان (Language)", callback_data='dev_feature')],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data='main_menu')]
    ]
    
    txt = (
        "⚙️ **مرکز تنظیمات ربات**\n\n"
        "در اینجا می‌توانید رفتار ربات، زمان‌بندی گزارش‌ها و حساسیت هشدارها را کنترل کنید."
    )
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

async def toggle_down_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_setting(update.effective_user.id, 'down_alert_enabled', update.callback_query.data.split('_')[2])
    await settings_menu(update, context)

async def resource_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if update.callback_query: await update.callback_query.answer()
    
    cpu_limit = db.get_setting(uid, 'cpu_threshold') or '80'
    ram_limit = db.get_setting(uid, 'ram_threshold') or '80'
    disk_limit = db.get_setting(uid, 'disk_threshold') or '90'
    
    kb = [
        [InlineKeyboardButton(f"🧠 هشدار CPU (فعلی: {cpu_limit}%)", callback_data='set_cpu_limit')],
        [InlineKeyboardButton(f"💾 هشدار RAM (فعلی: {ram_limit}%)", callback_data='set_ram_limit')],
        [InlineKeyboardButton(f"💿 هشدار Disk (فعلی: {disk_limit}%)", callback_data='set_disk_limit')],
        [InlineKeyboardButton("🔙 بازگشت به تنظیمات", callback_data='settings_menu')]
    ]
    txt = "🎚 **تنظیم آستانه حساسیت:**\nاگر مصرف منابع از این مقادیر رد شود، هشدار دریافت می‌کنید."
    await safe_edit_message(update, txt, reply_markup=InlineKeyboardMarkup(kb))

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
    await update.callback_query.answer("ذخیره شد.")
    await settings_cron_menu(update, context)

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

async def channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chans = db.get_user_channels(uid)
    
    type_map = {
        'all': '✅ همه', 
        'down': '🚨 قطعی', 
        'report': '📊 گزارش', 
        'expiry': '⏳ انقضا',
        'resource': '🔥 منابع' 
    }
    
    kb = [[InlineKeyboardButton(f"🗑 {c['name']} ({type_map.get(c['usage_type'],'all')})", callback_data=f'delchan_{c["id"]}')] for c in chans]
    kb.append([InlineKeyboardButton("➕ افزودن کانال", callback_data='add_channel')])
    kb.append([InlineKeyboardButton("🔙 بازگشت به تنظیمات", callback_data='settings_menu')])
    await safe_edit_message(update, "📢 **مدیریت کانال‌ها:**", reply_markup=InlineKeyboardMarkup(kb))

async def add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_edit_message(update, "📝 یک پیام از کانال مورد نظر **فوروارد** کنید:", reply_markup=get_cancel_markup())
    return GET_CHANNEL_FORWARD

async def get_channel_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.forward_from_chat and update.message.forward_from_chat.type == 'channel':
        context.user_data['new_chan'] = {'id': str(update.message.forward_from_chat.id), 'name': update.message.forward_from_chat.title}
        
        kb = [
            [InlineKeyboardButton("🔥 فقط فشار منابع (CPU/RAM)", callback_data='type_resource')],
            
            [InlineKeyboardButton("🚨 فقط هشدار قطعی", callback_data='type_down'), InlineKeyboardButton("⏳ فقط انقضا", callback_data='type_expiry')],
            [InlineKeyboardButton("📊 فقط گزارشات", callback_data='type_report'), InlineKeyboardButton("✅ همه موارد", callback_data='type_all')]
        ]
        await update.message.reply_text("🛠 **این کانال برای دریافت چه نوع پیام‌هایی استفاده شود؟**", reply_markup=InlineKeyboardMarkup(kb))
        return GET_CHANNEL_TYPE
    
    await update.message.reply_text("❌ لطفاً یک پیام از کانال **فوروارد** کنید.")
    return GET_CHANNEL_FORWARD

async def set_channel_type_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    usage = query.data.split('_')[1]
    cdata = context.user_data['new_chan']
    db.add_channel(update.effective_user.id, cdata['id'], cdata['name'], usage)
    await query.message.reply_text(f"✅ کانال {cdata['name']} ثبت شد.")
    await channels_menu(update, context)
    return ConversationHandler.END

async def delete_channel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.delete_channel(int(update.callback_query.data.split('_')[1]), update.effective_user.id)
    await channels_menu(update, context)


# ==============================================================================
# ⏳ SCHEDULED JOBS
# ==============================================================================
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
    all_users = set([u['user_id'] for u in db.get_all_users()] + [SUPER_ADMIN_ID])
    loop = asyncio.get_running_loop()
    all_tasks = []
    
    for uid in all_users:
        access, _ = db.check_access(uid)
        if not access: continue
        
        servers = db.get_all_user_servers(uid)
        if not servers: continue

        settings = {
            'report_interval': db.get_setting(uid, 'report_interval'),
            'cpu': int(db.get_setting(uid, 'cpu_threshold') or 80),
            'ram': int(db.get_setting(uid, 'ram_threshold') or 80),
            'disk': int(db.get_setting(uid, 'disk_threshold') or 90),
            'down_alert': db.get_setting(uid, 'down_alert_enabled') == '1'
        }
        all_tasks.append(process_single_user(context, uid, servers, settings, loop))

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
    msg_auto_report = [f"📅 **گزارش خودکار ({get_jalali_str()})**\n"]
    
    for i, res in enumerate(results):
        s_info = servers[i]
        r = res if isinstance(res, dict) else await res
        
        if r.get('status') == 'Online':
            db.add_server_stat(s_info['id'], r.get('cpu', 0), r.get('ram', 0))
            
            alert_msgs = []
            if r['cpu'] >= settings['cpu']: alert_msgs.append(f"🧠 **CPU:** `{r['cpu']}%`")
            if r['ram'] >= settings['ram']: alert_msgs.append(f"💾 **RAM:** `{r['ram']}%`")
            if r['disk'] >= settings['disk']: alert_msgs.append(f"💿 **Disk:** `{r['disk']}%`")
            
            if alert_msgs:
                last_alert = CPU_ALERT_TRACKER.get((uid, s_info['id']), 0)
                if time.time() - last_alert > 3600:
                    full_warning = f"⚠️ **هشدار منابع:** `{s_info['name']}`\n" + "\n".join(alert_msgs)
                    user_channels = db.get_user_channels(uid)
                    target_chans = [ch for ch in user_channels if ch['usage_type'] in ['resource', 'all']]
                    if target_chans:
                        for ch in target_chans:
                            try: await context.bot.send_message(ch['chat_id'], full_warning, parse_mode='Markdown')
                            except: pass
                    else:
                        try: await context.bot.send_message(uid, full_warning, parse_mode='Markdown')
                        except: pass
                    CPU_ALERT_TRACKER[(uid, s_info['id'])] = time.time()

        icon = "✅" if r.get('status') == 'Online' else "❌"
        msg_auto_report.append(f"{icon} **{s_info['name']}**")
        
        if settings['down_alert'] and s_info['is_active']:
             await check_server_down_logic(context, uid, s_info, r)

    report_int = settings['report_interval']
    if report_int and int(report_int) > 0:
        last_run = LAST_REPORT_CACHE.get(uid, 0)
        if time.time() - last_run > int(report_int):
            try: await context.bot.send_message(uid, "\n".join(msg_auto_report), parse_mode='Markdown')
            except: pass
            LAST_REPORT_CACHE[uid] = time.time()

async def check_server_down_logic(context, uid, s, res):
    k = (uid, s['id'])
    fails = SERVER_FAILURE_COUNTS.get(k, 0)
    
    if res['status'] == 'Offline':
        fails += 1
        SERVER_FAILURE_COUNTS[k] = fails
        if fails == DOWN_RETRY_LIMIT:
            alrt = f"🚨 **Down Alert:** `{s['name']}`\n❌ `{res.get('error', 'Unknown')}`"
            user_channels = db.get_user_channels(uid)
            sent = False
            for c in user_channels:
                if c['usage_type'] in ['down', 'all']:
                    try: 
                        await context.bot.send_message(c['chat_id'], alrt, parse_mode='Markdown')
                        sent = True
                    except: pass
            if not sent:
                try: await context.bot.send_message(uid, alrt, parse_mode='Markdown')
                except: pass
            db.update_status(s['id'], "Offline")
    else:
        if fails > 0 or s['last_status'] == 'Offline':
            SERVER_FAILURE_COUNTS[k] = 0
            if s['last_status'] == 'Offline':
                rec_msg = f"✅ **Recovery:** `{s['name']}` is back online!"
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
# 🚀 MAIN EXECUTION
# ==============================================================================
def main():
    print("🚀 SONAR ULTRA PRO RUNNING...")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_error_handler(error_handler)

    text_filter = filters.TEXT & ~filters.COMMAND

    conv_handler = ConversationHandler(
        allow_reentry=True, 
        entry_points=[
            CallbackQueryHandler(add_new_user_start, pattern='^add_new_admin$'), 
            CallbackQueryHandler(admin_user_actions, pattern='^admin_u_limit_'),
            CallbackQueryHandler(admin_user_actions, pattern='^admin_u_settime_'),
            CallbackQueryHandler(admin_search_start, pattern='^admin_search_start$'),
            CallbackQueryHandler(admin_backup_restore_start, pattern='^admin_backup_restore_start$'),
            CallbackQueryHandler(add_group_start, pattern='^add_group$'),
            CallbackQueryHandler(add_server_start, pattern='^add_server$'),
            CallbackQueryHandler(manual_ping_start, pattern='^manual_ping_start$'),
            CallbackQueryHandler(add_channel_start, pattern='^add_channel$'),
            CallbackQueryHandler(ask_custom_interval, pattern='^setcron_custom$'),
            CallbackQueryHandler(edit_expiry_start, pattern='^act_editexpiry_'),
            CallbackQueryHandler(ask_terminal_command, pattern='^cmd_terminal_'),
            CallbackQueryHandler(resource_settings_menu, pattern='^settings_thresholds$'),
            CallbackQueryHandler(ask_cpu_limit, pattern='^set_cpu_limit$'),
            CallbackQueryHandler(ask_ram_limit, pattern='^set_ram_limit$'),
            CallbackQueryHandler(ask_disk_limit, pattern='^set_disk_limit$'),
            CallbackQueryHandler(user_profile_menu, pattern='^user_profile$'),
            CallbackQueryHandler(web_token_action, pattern='^gen_web_token$'),
            CallbackQueryHandler(admin_broadcast_start, pattern='^admin_broadcast_start$'),
            CallbackQueryHandler(send_global_full_report_action, pattern='^act_global_full_report$'),
            CallbackQueryHandler(lambda u,c: u.callback_query.answer("🔜 به‌زودی!", show_alert=True), pattern='^dev_feature$')
        ],
        states={
            ADD_ADMIN_ID: [MessageHandler(text_filter, get_new_user_id)],
            ADD_ADMIN_DAYS: [MessageHandler(text_filter, get_new_user_days)],
            ADMIN_SET_LIMIT: [MessageHandler(text_filter, admin_set_limit_handler)],
            ADMIN_SET_TIME_MANUAL: [MessageHandler(text_filter, admin_set_days_handler)],
            ADMIN_SEARCH_USER: [MessageHandler(text_filter, admin_search_handler)],
            ADMIN_RESTORE_DB: [MessageHandler(filters.Document.ALL, admin_backup_restore_handler)],
            GET_GROUP_NAME: [MessageHandler(text_filter, get_group_name)],
            GET_NAME: [MessageHandler(text_filter, get_srv_name)],
            GET_IP: [MessageHandler(text_filter, get_srv_ip)],
            GET_PORT: [MessageHandler(text_filter, get_srv_port)],
            GET_USER: [MessageHandler(text_filter, get_srv_user)],
            GET_PASS: [MessageHandler(text_filter, get_srv_pass)],
            GET_EXPIRY: [MessageHandler(text_filter, get_srv_expiry)],
            SELECT_GROUP: [CallbackQueryHandler(select_group)],
            GET_MANUAL_HOST: [MessageHandler(text_filter, perform_manual_ping)],
            GET_CHANNEL_FORWARD: [MessageHandler(filters.FORWARDED, get_channel_forward)],
            GET_CUSTOM_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_custom_interval_action)],
            GET_CHANNEL_TYPE: [CallbackQueryHandler(set_channel_type_action, pattern='^type_')],
            EDIT_SERVER_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_expiry_save)],
            GET_REMOTE_COMMAND: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, run_terminal_action),
                CallbackQueryHandler(close_terminal_session, pattern='^exit_terminal$')
            ],
            GET_CPU_LIMIT: [MessageHandler(text_filter, save_cpu_limit)],
            GET_RAM_LIMIT: [MessageHandler(text_filter, save_ram_limit)],
            GET_DISK_LIMIT: [MessageHandler(text_filter, save_disk_limit)],
            GET_BROADCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_broadcast_send)],
        },
        fallbacks=[
            CommandHandler('cancel', cancel_handler_func),
            CallbackQueryHandler(cancel_handler_func, pattern='^cancel_flow$'),
            CommandHandler('start', start)
        ]
    )

    app.add_handler(conv_handler)

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('dashboard', dashboard_command))
    app.add_handler(CommandHandler('setting', settings_command))
    app.add_handler(CallbackQueryHandler(main_menu, pattern='^main_menu$'))
    
    app.add_handler(CallbackQueryHandler(admin_panel_main, pattern='^admin_panel_main$'))
    app.add_handler(CallbackQueryHandler(admin_users_list, pattern='^admin_users_page_'))
    app.add_handler(CallbackQueryHandler(admin_user_manage, pattern='^admin_u_manage_'))
    app.add_handler(CallbackQueryHandler(admin_user_actions, pattern='^admin_u_'))
    app.add_handler(CallbackQueryHandler(admin_users_text, pattern='^admin_users_text$'))
    app.add_handler(CallbackQueryHandler(admin_backup_get, pattern='^admin_backup_get$'))
    
    app.add_handler(CallbackQueryHandler(groups_menu, pattern='^groups_menu$'))
    app.add_handler(CallbackQueryHandler(delete_group_action, pattern='^delgroup_'))
    app.add_handler(CallbackQueryHandler(list_groups_for_servers, pattern='^list_groups_for_servers$'))
    app.add_handler(CallbackQueryHandler(show_servers, pattern='^(listsrv_|list_all)'))
    app.add_handler(CallbackQueryHandler(server_detail, pattern='^detail_'))
    app.add_handler(CallbackQueryHandler(server_actions, pattern='^act_'))
    
    app.add_handler(CallbackQueryHandler(set_dns_action, pattern='^setdns_'))
    app.add_handler(CallbackQueryHandler(channels_menu, pattern='^channels_menu$'))
    app.add_handler(CallbackQueryHandler(delete_channel_action, pattern='^delchan_'))
    app.add_handler(CallbackQueryHandler(settings_menu, pattern='^settings_menu$'))
    app.add_handler(CallbackQueryHandler(status_dashboard, pattern='^status_dashboard$'))
    app.add_handler(CallbackQueryHandler(settings_cron_menu, pattern='^settings_cron$'))
    app.add_handler(CallbackQueryHandler(set_cron_action, pattern='^setcron_'))
    app.add_handler(CallbackQueryHandler(toggle_down_alert, pattern='^toggle_downalert_'))
    app.add_handler(CallbackQueryHandler(manage_servers_list, pattern='^manage_servers_list$'))
    app.add_handler(CallbackQueryHandler(toggle_server_active_action, pattern='^toggle_active_'))
    app.add_handler(CallbackQueryHandler(send_instant_channel_report, pattern='^send_instant_report$'))

    if app.job_queue:
        app.job_queue.run_daily(check_expiry_job, time=dt.time(hour=8, minute=30, second=0))
        app.job_queue.run_repeating(global_monitor_job, interval=DEFAULT_INTERVAL, first=10)
    else:
        logger.error("JobQueue not available. Install python-telegram-bot[job-queue]")
    
    app.run_polling()

if __name__ == '__main__':
    main()
