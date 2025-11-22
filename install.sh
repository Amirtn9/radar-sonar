#!/bin/bash

# --- Colors ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# --- Configuration ---
INSTALL_DIR="/opt/radar-sonar"
SERVICE_NAME="sonar-bot"
REPO_URL="https://github.com/Amirtn9/radar-sonar.git"

# --- Root Check ---
if [ "$EUID" -ne 0 ]; then 
  echo -e "${RED}❌ Lotfan ba dastrasi ROOT ejra konid (sudo bash ...)${NC}"
  exit
fi

# --- Ensure Whiptail is installed for GUI ---
if ! command -v whiptail &> /dev/null; then
    echo -e "${YELLOW}📦 Installing GUI dependencies (whiptail)...${NC}"
    apt-get update && apt-get install -y whiptail
fi

# ==============================================================================
# 🔧 FUNCTIONS
# ==============================================================================

# 1. Install / Re-install Function (Core Logic Preserved)
function install_bot() {
    echo -e "${GREEN}🚀 Starting Sonar Radar Ultra Pro Installer...${NC}"

    # نصب پیش‌نیازهای سیستمی
    echo -e "${YELLOW}📦 Installing system dependencies...${NC}"
    apt-get update && apt-get upgrade -y
    apt-get install -y python3 python3-pip python3-venv git

    # کلون کردن یا آپدیت ریپازیتوری
    if [ -d "$INSTALL_DIR" ]; then
        echo -e "${YELLOW}⚠️ Directory exists. Updating repo...${NC}"
        cd "$INSTALL_DIR"
        git pull
    else
        echo -e "${YELLOW}⬇️ Cloning repository...${NC}"
        git clone "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    fi

    # ساخت محیط ایزوله
    echo -e "${YELLOW}🐍 Creating Python Virtual Environment...${NC}"
    python3 -m venv venv
    source venv/bin/activate

    # نصب کتابخانه‌های پایتون
    echo -e "${YELLOW}📥 Installing Python libraries...${NC}"
    pip install --upgrade pip
    pip install -r requirements.txt

    # دریافت کانفیگ اولیه (اگر فایل تازه ساخته شده باشد)
    configure_bot_gui "install_mode"

    # ساخت سرویس Systemd
    echo -e "${YELLOW}🔧 Setting up Systemd Service...${NC}"
    SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

    cat <<EOF > $SERVICE_FILE
[Unit]
Description=Sonar Radar Ultra Pro Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    # فعال‌سازی و استارت ربات
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
    systemctl restart $SERVICE_NAME

    whiptail --msgbox "✅ Installation Completed Successfully!\n🤖 Bot Service is running." 10 50
}

# 2. Configure Token & ID (GUI)
function configure_bot_gui() {
    MODE=$1
    
    if [ ! -f "$INSTALL_DIR/bot.py" ]; then
        whiptail --msgbox "❌ File bot.py peyda nashod! Aval robot ro nasb konid." 10 50
        return
    fi

    # دریافت توکن
    USER_TOKEN=$(whiptail --inputbox "🤖 Enter your Telegram Bot TOKEN:" 10 60 --title "Bot Configuration" 3>&1 1>&2 2>&3)
    exitstatus=$?
    if [ $exitstatus != 0 ]; then return; fi # Cancelled

    # دریافت آیدی عددی
    USER_ADMIN_ID=$(whiptail --inputbox "👤 Enter Super Admin Numeric ID:" 10 60 --title "Bot Configuration" 3>&1 1>&2 2>&3)
    exitstatus=$?
    if [ $exitstatus != 0 ]; then return; fi # Cancelled

    # اعمال تغییرات
    cd "$INSTALL_DIR"
    sed -i "s/TOKEN = .*/TOKEN = '$USER_TOKEN'/" bot.py
    sed -i "s/SUPER_ADMIN_ID = .*/SUPER_ADMIN_ID = $USER_ADMIN_ID/" bot.py

    if [ "$MODE" != "install_mode" ]; then
        systemctl restart $SERVICE_NAME
        whiptail --msgbox "✅ Configuration Updated & Bot Restarted!" 8 45
    fi
}

# 3. Uninstall Function
function uninstall_bot() {
    if (whiptail --title "Uninstall" --yesno "⚠️ Aya motmaen hastid ke mikhahid Robot va tamame file-ha ra HAZF konid?" 10 60); then
        echo -e "${YELLOW}🛑 Stopping service...${NC}"
        systemctl stop $SERVICE_NAME
        systemctl disable $SERVICE_NAME
        rm /etc/systemd/system/$SERVICE_NAME.service
        systemctl daemon-reload
        
        echo -e "${YELLOW}🗑 Removing files...${NC}"
        rm -rf "$INSTALL_DIR"
        
        whiptail --msgbox "🗑 Robot ba movafaghiat va be tor kamel hazf shod." 8 45
    fi
}

# 4. Logs Viewer
function view_logs() {
    clear
    echo -e "${GREEN}📜 Showing Logs (Press Ctrl+C to exit logs)...${NC}"
    journalctl -u $SERVICE_NAME -f
}

# ==============================================================================
# 🖥 MAIN MENU LOOP
# ==============================================================================
while true; do
    CHOICE=$(whiptail --title "🚀 Sonar Radar Ultra Pro Manager" --menu "Lotfan yek gozine entekhab konid:" 18 70 10 \
    "1" "📥 Install / Update Bot (Nasb/Update)" \
    "2" "⚙️ Change Token & Admin ID (Taghir Config)" \
    "3" "📜 View Logs (Moshahede Log)" \
    "4" "🔄 Restart Bot" \
    "5" "🛑 Stop Bot" \
    "6" "🗑 Uninstall (Hazf Kamel)" \
    "7" "❌ Exit" 3>&1 1>&2 2>&3)

    exitstatus=$?
    if [ $exitstatus != 0 ]; then exit; fi

    case $CHOICE in
        1) install_bot ;;
        2) configure_bot_gui "menu_mode" ;;
        3) view_logs ;;
        4) 
            systemctl restart $SERVICE_NAME
            whiptail --msgbox "✅ Bot Restarted." 8 30 
            ;;
        5) 
            systemctl stop $SERVICE_NAME
            whiptail --msgbox "🛑 Bot Stopped." 8 30 
            ;;
        6) uninstall_bot ;;
        7) break ;;
    esac
done
