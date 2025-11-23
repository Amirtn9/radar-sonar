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
    echo -e "${YELLOW}📦 Installing necessary tools (whiptail)...${NC}"
    apt-get update -y
    apt-get install -y whiptail
fi

# ==============================================================================
# 🔧 CORE FUNCTIONS
# ==============================================================================

# 1. Install Bot Logic
function install_bot() {
    show_header
    
    # 1. Stop existing service if running
    if systemctl is-active --quiet $SERVICE_NAME; then
        systemctl stop $SERVICE_NAME
        echo -e "${YELLOW}🛑 Stopped existing service.${NC}"
    fi

    # 2. Install System Dependencies
    {
        echo 10
        echo "XXX\nUpdating package lists...\nXXX"
        apt-get update -y > /dev/null 2>&1
        
        echo 30
        echo "XXX\nInstalling Python, Git & Pip...\nXXX"
        apt-get install -y python3 python3-pip python3-venv git curl > /dev/null 2>&1
        
        echo 50
        echo "XXX\nCleaning old installation...\nXXX"
        # Force remove directory to ensure fresh clone
        rm -rf "$INSTALL_DIR"
        mkdir -p "$INSTALL_DIR"
        
        echo 60
        echo "XXX\nDownloading repository...\nXXX"
        # Try Git Clone first
        if ! git clone "$REPO_URL" "$INSTALL_DIR" > /dev/null 2>&1; then
            # Fallback to CURL if git fails
            echo "XXX\nGit failed, using fallback download...\nXXX"
            curl -s -o "$INSTALL_DIR/bot.py" "$RAW_URL/bot.py"
            curl -s -o "$INSTALL_DIR/requirements.txt" "$RAW_URL/requirements.txt"
        fi

        echo 80
        echo "XXX\nSetting up Python Environment...\nXXX"
        python3 -m venv "$INSTALL_DIR/venv"
        source "$INSTALL_DIR/venv/bin/activate"
        pip install --upgrade pip > /dev/null 2>&1
        pip install -r "$INSTALL_DIR/requirements.txt" > /dev/null 2>&1
        
        echo 100
    } | whiptail --title "Installation Progress" --gauge "Installing Sonar Radar..." 8 60 0

    # 3. Verify Download
    if [ ! -f "$INSTALL_DIR/bot.py" ]; then
        whiptail --msgbox "❌ CRITICAL ERROR:\nFile 'bot.py' was not downloaded.\nCheck your internet connection or GitHub repository URL." 10 60
        return
    fi

    # 4. Configure Bot
    configure_bot_gui "install"

    # 5. Setup Systemd Service
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

    # 6. Enable & Start
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME > /dev/null 2>&1
    systemctl restart $SERVICE_NAME

    whiptail --msgbox "✅ Installation Complete!\n\n🤖 Bot is now running in the background." 10 50
}

# 2. Configuration GUI
function configure_bot_gui() {
    MODE=$1
    CONFIG_FILE="$INSTALL_DIR/bot.py"

    if [ ! -f "$CONFIG_FILE" ]; then
        whiptail --msgbox "❌ bot.py not found! Please run 'Install' option first." 8 45
        return
    fi

    TOKEN=$(whiptail --inputbox "🤖 Enter Telegram Bot TOKEN:" 10 60 --title "Bot Setup" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then return; fi

    ADMIN_ID=$(whiptail --inputbox "👤 Enter Super Admin Numeric ID:" 10 60 --title "Bot Setup" 3>&1 1>&2 2>&3)
    if [ $? -ne 0 ]; then return; fi

    # Update File using sed
    sed -i "s/TOKEN = .*/TOKEN = '$TOKEN'/" "$CONFIG_FILE"
    sed -i "s/SUPER_ADMIN_ID = .*/SUPER_ADMIN_ID = $ADMIN_ID/" "$CONFIG_FILE"

    if [ "$MODE" != "install" ]; then
        if (whiptail --title "Restart Required" --yesno "Configuration saved. Restart bot now?" 8 45); then
            systemctl restart $SERVICE_NAME
            whiptail --msgbox "✅ Bot Restarted with new config." 8 40
        fi
    fi
}

# 3. Uninstall Logic
function uninstall_bot() {
    if (whiptail --title "⚠️ DANGER ZONE" --yesno "Are you sure you want to completely REMOVE Sonar Radar?" 10 60); then
        
        systemctl stop $SERVICE_NAME
        systemctl disable $SERVICE_NAME > /dev/null 2>&1
        rm -f /etc/systemd/system/$SERVICE_NAME.service
        systemctl daemon-reload
        rm -rf "$INSTALL_DIR"
        
        whiptail --msgbox "🗑️ Uninstallation Complete." 8 40
    fi
}

# 4. Logs Viewer
function view_logs() {
    clear
    echo -e "${GREEN}📜 Showing Live Logs (Press Ctrl+C to return)...${NC}"
    echo -e "${YELLOW}-----------------------------------------------------${NC}"
    journalctl -u $SERVICE_NAME -f -n 50
}

# 5. Check Status
function check_status() {
    STATUS=$(systemctl is-active $SERVICE_NAME)
    if [ "$STATUS" == "active" ]; then
        ICON="🟢"
        MSG="Running"
    else
        ICON="🔴"
        MSG="Stopped"
    fi
    whiptail --msgbox "📊 Status: $MSG $ICON" 8 40
}

# ==============================================================================
# 🖥 MAIN MENU
# ==============================================================================
while true; do
    show_header
    
    OPTION=$(whiptail --title "🚀 Sonar Radar Manager" --menu "Choose an option:" 18 70 10 \
    "1" "📥 Install / Re-Install (Force Update)" \
    "2" "⚙️ Configure (Token & Admin ID)" \
    "3" "⏯️ Restart Bot" \
    "4" "🛑 Stop Bot" \
    "5" "📜 Live Logs" \
    "6" "📊 Check Status" \
    "7" "🗑️ Uninstall" \
    "8" "❌ Exit" 3>&1 1>&2 2>&3)

    exitstatus=$?
    if [ $exitstatus != 0 ]; then exit; fi

    case $OPTION in
        1) install_bot ;;
        2) configure_bot_gui "menu" ;;
        3) systemctl restart $SERVICE_NAME; whiptail --msgbox "✅ Restarted." 8 30 ;;
        4) systemctl stop $SERVICE_NAME; whiptail --msgbox "🛑 Stopped." 8 30 ;;
        5) view_logs ;;
        6) check_status ;;
        7) uninstall_bot ;;
        8) clear; exit ;;
    esac
done
