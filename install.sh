#!/bin/bash

# ==============================================================================
# 🚀 SONAR RADAR ULTRA PRO - INSTALLER & MANAGER
# ==============================================================================

# --- Configuration ---
INSTALL_DIR="/opt/radar-sonar"
SERVICE_NAME="sonar-bot"
REPO_URL="https://github.com/Amirtn9/radar-sonar.git"
RAW_URL="https://raw.githubusercontent.com/Amirtn9/radar-sonar/main"

# --- Colors & Styling ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# --- ASCII Header ---
function show_header() {
    clear
    echo -e "${CYAN}"
    echo "   _____  ____  _   _          _____ "
    echo "  / ____|/ __ \| \ | |   /\   |  __ \\"
    echo " | (___ | |  | |  \| |  /  \  | |__) |"
    echo "  \___ \| |  | | . ' | / /\ \ |  _  / "
    echo "  ____) | |__| | |\  |/ ____ \| | \ \ "
    echo " |_____/ \____/|_| \_/_/    \_\_|  \_\\"
    echo "                                      "
    echo "      🚀 RADAR ULTRA PRO MANAGER      "
    echo -e "${NC}"
    echo -e "${BLUE}==========================================${NC}"
    sleep 0.5
}

# --- Root Check ---
if [ "$EUID" -ne 0 ]; then 
  echo -e "${RED}❌ Error: Please run as root (sudo bash install.sh)${NC}"
  exit 1
fi

# --- Dependencies Check (Whiptail) ---
if ! command -v whiptail &> /dev/null; then
    echo -e "${YELLOW}📦 Installing GUI tools (whiptail)...${NC}"
    apt-get update -y > /dev/null 2>&1
    apt-get install -y whiptail > /dev/null 2>&1
fi

# ==============================================================================
# 🔧 CORE FUNCTIONS
# ==============================================================================

# 1. Install / Re-Install (Fresh Setup)
function install_bot() {
    show_header
    
    if systemctl is-active --quiet $SERVICE_NAME; then
        systemctl stop $SERVICE_NAME
    fi

    {
        echo 10; echo "XXX\nUpdating system packages...\nXXX"; apt-get update -y > /dev/null 2>&1
        echo 30; echo "XXX\nInstalling Python, Git & Pip...\nXXX"; apt-get install -y python3 python3-pip python3-venv git curl > /dev/null 2>&1
        
        echo 50; echo "XXX\nPreparing directory...\nXXX"
        if [ -f "$INSTALL_DIR/sonar_ultra_pro.db" ]; then cp "$INSTALL_DIR/sonar_ultra_pro.db" /tmp/sonar_backup.db; fi
        rm -rf "$INSTALL_DIR"; mkdir -p "$INSTALL_DIR"
        
        echo 60; echo "XXX\nDownloading source code...\nXXX"
        if ! git clone "$REPO_URL" "$INSTALL_DIR" > /dev/null 2>&1; then
            curl -s -o "$INSTALL_DIR/bot.py" "$RAW_URL/bot.py"
            curl -s -o "$INSTALL_DIR/requirements.txt" "$RAW_URL/requirements.txt"
        fi

        if [ -f "/tmp/sonar_backup.db" ]; then mv /tmp/sonar_backup.db "$INSTALL_DIR/sonar_ultra_pro.db"; fi

        echo 80; echo "XXX\nCreating Virtual Environment...\nXXX"
        python3 -m venv "$INSTALL_DIR/venv"
        source "$INSTALL_DIR/venv/bin/activate"
        pip install --upgrade pip > /dev/null 2>&1
        
        echo 90; echo "XXX\nInstalling Python Libraries...\nXXX"
        pip install -r "$INSTALL_DIR/requirements.txt" > /dev/null 2>&1
        
        echo 100
    } | whiptail --title "Installation" --gauge "Installing Sonar Radar..." 8 60 0

    if [ ! -f "$INSTALL_DIR/bot.py" ]; then whiptail --msgbox "❌ Error: bot.py download failed." 8 45; return; fi

    configure_bot_gui "install"

    cat <<EOF > /etc/systemd/system/$SERVICE_NAME.service
[Unit]
Description=Sonar Radar Ultra Pro Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable $SERVICE_NAME > /dev/null 2>&1
    systemctl restart $SERVICE_NAME

    whiptail --msgbox "✅ Installation Complete!\n\n🤖 Bot is now running." 8 45
}

# 2. Update Only (Fast Update)
function update_bot() {
    if [ ! -d "$INSTALL_DIR" ]; then
        whiptail --msgbox "❌ Bot is not installed! Use 'Install' option first." 8 45
        return
    fi

    systemctl stop $SERVICE_NAME
    
    {
        echo 20; echo "XXX\nPulling changes from GitHub...\nXXX"
        cd "$INSTALL_DIR" || exit
        
        if [ -d ".git" ]; then
            git fetch --all > /dev/null 2>&1
            git reset --hard origin/main > /dev/null 2>&1
            git pull > /dev/null 2>&1
        else
            curl -s -o "$INSTALL_DIR/bot.py" "$RAW_URL/bot.py"
            curl -s -o "$INSTALL_DIR/requirements.txt" "$RAW_URL/requirements.txt"
        fi
        
        echo 60; echo "XXX\nUpdating dependencies...\nXXX"
        if [ -d "venv" ]; then
            source "venv/bin/activate"
            pip install -r requirements.txt > /dev/null 2>&1
        fi
        
        echo 90; echo "XXX\nRestarting Service...\nXXX"
        systemctl restart $SERVICE_NAME
        echo 100
    } | whiptail --title "Update" --gauge "Updating Sonar Radar..." 8 60 0
    
    whiptail --msgbox "✅ Bot Updated Successfully!\n(Your database and config were preserved)" 8 50
}

# 3. Configuration GUI
function configure_bot_gui() {
    MODE=$1
    CONFIG_FILE="$INSTALL_DIR/bot.py"

    if [ ! -f "$CONFIG_FILE" ]; then whiptail --msgbox "❌ bot.py not found. Install first." 8 45; return; fi

    TOKEN=$(whiptail --inputbox "🤖 Enter Telegram Bot TOKEN:" 10 60 --title "Bot Configuration" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then return; fi

    ADMIN_ID=$(whiptail --inputbox "👤 Enter Super Admin Numeric ID:" 10 60 --title "Bot Configuration" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then return; fi

    sed -i "s/TOKEN = .*/TOKEN = '$TOKEN'/" "$CONFIG_FILE"
    sed -i "s/SUPER_ADMIN_ID = .*/SUPER_ADMIN_ID = $ADMIN_ID/" "$CONFIG_FILE"

    if [ "$MODE" != "install" ]; then
        if (whiptail --title "Restart" --yesno "Config saved. Restart bot now?" 8 45); then
            systemctl restart $SERVICE_NAME
            whiptail --msgbox "✅ Bot Restarted." 8 30
        fi
    fi
}

# 4. Uninstall Logic
function uninstall_bot() {
    if (whiptail --title "⚠️ DELETE BOT" --yesno "Are you sure you want to DELETE everything?" 10 60); then
        systemctl stop $SERVICE_NAME
        systemctl disable $SERVICE_NAME > /dev/null 2>&1
        rm -f /etc/systemd/system/$SERVICE_NAME.service
        systemctl daemon-reload
        rm -rf "$INSTALL_DIR"
        whiptail --msgbox "🗑️ Uninstalled successfully." 8 40
    fi
}

# 5. View Logs
function view_logs() {
    clear
    echo -e "${GREEN}📜 Showing Logs (Ctrl+C to exit)...${NC}"
    journalctl -u $SERVICE_NAME -f -n 50
}

# 6. Check Status
function check_status() {
    if systemctl is-active --quiet $SERVICE_NAME; then
        whiptail --msgbox "✅ Status: RUNNING 🟢" 8 30
    else
        whiptail --msgbox "❌ Status: STOPPED 🔴" 8 30
    fi
}

# ==============================================================================
# 🖥 MAIN MENU
# ==============================================================================
while true; do
    show_header
    
    # MENU DEFINITION - CHECK THIS PART CAREFULLY
    OPTION=$(whiptail --title "🚀 Sonar Radar Manager" --menu "Select an option:" 20 70 11 \
    "1" "📥 Install / Re-install (Reset DB if needed)" \
    "2" "🔄 Update Source Code (Keep DB)" \
    "3" "⚙️ Configure Token & Admin ID" \
    "4" "⏯️ Restart Bot" \
    "5" "🛑 Stop Bot" \
    "6" "📜 View Live Logs" \
    "7" "📊 Check Service Status" \
    "8" "🗑️ Uninstall Completely" \
    "9" "❌ Exit" 3>&1 1>&2 2>&3)

    exitstatus=$?
    if [ $exitstatus != 0 ]; then exit; fi

    case $OPTION in
        1) install_bot ;;
        2) update_bot ;;
        3) configure_bot_gui "menu" ;;
        4) systemctl restart $SERVICE_NAME; whiptail --msgbox "✅ Restarted." 8 30 ;;
        5) systemctl stop $SERVICE_NAME; whiptail --msgbox "🛑 Stopped." 8 30 ;;
        6) view_logs ;;
        7) check_status ;;
        8) uninstall_bot ;;
        9) clear; exit ;;
    esac
done
