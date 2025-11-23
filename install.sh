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
