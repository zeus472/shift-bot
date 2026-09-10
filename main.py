import os
import discord
from discord.ext import commands, tasks
from discord import ui
import sqlite3
import datetime
import random
import string
import asyncio

# ==================== الإعدادات الثابتة ====================
ROLE_MEMBER_ID = 1541620051033985085
CHANNEL_LEVELUP_ID = 1544834419544821780
ROLE_STORE_TEAM_ID = 1547655214507622481
CHANNEL_LOG_ID = 1547668485340012575
CURRENCY_NAME = "BX COINS 🪙"

# ==================== إعداد قاعدة البيانات ====================
conn = sqlite3.connect("bot_database.db")
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    messages INTEGER DEFAULT 0,
    voice_minutes INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    coins INTEGER DEFAULT 0
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS level_reqs (
    level INTEGER PRIMARY KEY,
    req_messages INTEGER DEFAULT 0,
    req_voice_mins INTEGER DEFAULT 0,
    reward_coins INTEGER DEFAULT 0
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS products (
    code TEXT PRIMARY KEY,
    name TEXT,
    item_type TEXT,
    role_id INTEGER,
    price INTEGER,
    duration_minutes INTEGER
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS temp_roles (
    user_id INTEGER,
    role_id INTEGER,
    expire_time TEXT
)''')
conn.commit()

# ==================== البوت والإنتنتس ====================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

async def log_event(guild, title, description, color=0x3498DB):
    log_channel = guild.get_channel(CHANNEL_LOG_ID)
    if log_channel:
        embed = discord.Embed(
            title=f"📝 │ {title}",
            description=f"{description}\n\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
            color=color,
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_footer(text="نظام السجلات الآلي • Brevix Logs", icon_url=guild.icon.url if guild.icon else None)
        await log_channel.send(embed=embed)

def get_user_data(user_id):
    cursor.execute("SELECT messages, voice_minutes, level, coins FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    if not data:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return (0, 0, 1, 0)
    return data

async def check_level_up(member, channel=None):
    if not any(r.id == ROLE_MEMBER_ID for r in member.roles):
        return
    msgs, v_mins, current_lvl, coins = get_user_data(member.id)
    next_lvl = current_lvl + 1
    
    if next_lvl > 100:
        return

    cursor.execute("SELECT req_messages, req_voice_mins, reward_coins FROM level_reqs WHERE level = ?", (next_lvl,))
    req = cursor.fetchone()
    if not req:
        return

    req_msgs, req_vmins, reward = req
    if msgs >= req_msgs and v_mins >= req_vmins:
        new_coins = coins + reward
        cursor.execute("UPDATE users SET level = ?, coins = ? WHERE user_id = ?", (next_lvl, new_coins, member.id))
        conn.commit()

        lvl_channel = member.guild.get_channel(CHANNEL_LEVELUP_ID)
        if lvl_channel:
            embed = discord.Embed(
                title="🎉 ✦ ترقية مستوى جديدة! ✦ 🎉",
                description=(
                    f"تهانينا الحارة لـ {member.mention} على هذا الإنجاز!\n\n"
                    f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                    f"📈 **المستوى الجديد:** `Level {next_lvl}`\n"
                    f"🎁 **المكافأة:** `{reward}` {CURRENCY_NAME}\n"
                    f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                    f"استمر في التفاعل والمشاركة للوصول إلى المستويات التالية! 🔥"
                ),
                color=0xF1C40F
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="نظام المستويات الآلي", icon_url=member.guild.icon.url if member.guild.icon else None)
            await lvl_channel.send(content=member.mention, embed=embed)

        await log_event(member.guild, "ترقية مستوى", f"اللاعب {member.mention} وصل إلى **Level {next_lvl}** وحصل على مكافأة `{reward}` كوينز.")
        await check_level_up(member, channel)

# ==================== اللوحات والعناصر التفاعلية ====================

# --- لوحة المستخدم ---
class UserPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="استعلام عن الرصيد 💰", style=discord.ButtonStyle.primary, custom_id="btn_balance")
    async def balance(self, interaction: discord.Interaction, button: ui.Button):
        _, _, _, coins = get_user_data(interaction.user.id)
        embed = discord.Embed(
            title="💳 │ محفظتك المالية",
            description=f"مرحباً {interaction.user.mention}\n\n◈ **رصيدك الحالي:** `{coins}` {CURRENCY_NAME}",
            color=0x2ECC71
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="المستوى والتفاعل 📊", style=discord.ButtonStyle.secondary, custom_id="btn_stats")
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        msgs, v_mins, lvl, _ = get_user_data(interaction.user.id)
        embed = discord.Embed(
            title="📊 │ إحصائيات التفاعل والمستوى",
            description=f"أهلاً بك {interaction.user.mention}، إليك تفاصيل نشاطك داخل السيرفر:\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
            color=0x3498DB
        )
        embed.add_field(name="🏆 المستوى الحالي", value=f"`Level {lvl}`", inline=True)
        embed.add_field(name="💬 الرسائل النصية", value=f"`{msgs}` رسالة", inline=True)
        embed.add_field(name="🎙️ الدقائق الصوتية", value=f"`{v_mins}` دقيقة", inline=True)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="تحويل عملات 💸", style=discord.ButtonStyle.success, custom_id="btn_transfer")
    async def transfer(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(TransferModal())

class TransferModal(ui.Modal, title="💸 تحويل BX COINS"):
    target_id = ui.TextInput(label="آي دي العضو المستلم", placeholder="مثال: 123456789", required=True)
    amount = ui.TextInput(label="المبلغ المراد تحويله", placeholder="مثال: 500", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target = int(self.target_id.value)
            amt = int(self.amount.value)
            if amt <= 0:
                raise ValueError
        except ValueError:
            embed = discord.Embed(title="❌ خطأ", description="يرجى إدخال بيانات وأرقام صحيحة.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        _, _, _, sender_coins = get_user_data(interaction.user.id)
        if sender_coins < amt:
            embed = discord.Embed(title="❌ رصيد غير كافٍ", description="لا تمتلك هذا القدر من العملات لإتمام التحويل.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        get_user_data(target)
        cursor.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amt, interaction.user.id))
        cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amt, target))
        conn.commit()

        embed = discord.Embed(
            title="✅ │ عملية تحويل ناجحة",
            description=f"تم تحويل `{amt}` {CURRENCY_NAME} بنجاح إلى <@{target}>.",
            color=0x2ECC71
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_event(interaction.guild, "تحويل عملات", f"قام {interaction.user.mention} بتحويل `{amt}` {CURRENCY_NAME} إلى <@{target}>.")

# --- لوحة المتجر ---
class StorePanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="عرض المنتجات 🛍️", style=discord.ButtonStyle.primary, custom_id="btn_list_products")
    async def list_products(self, interaction: discord.Interaction, button: ui.Button):
        cursor.execute("SELECT code, name, item_type, price, duration_minutes FROM products")
        prods = cursor.fetchall()
        if not prods:
            embed = discord.Embed(title="🛒 │ المتجر فارغ", description="لا توجد منتجات متاحة للشراء حالياً.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        embed = discord.Embed(
            title="🛒 │ قائمة منتجات المتجر المتاحة",
            description="إليك جميع المنتجات المتاحة حالياً، استخدم كود المنتج عند الشراء:\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
            color=0xF1C40F
        )
        for code, name, itype, price, dur in prods:
            dur_str = f"`{dur}` دقيقة" if dur > 0 else "`دائم`"
            p_str = "`مجاني`" if price == 0 else f"`{price}` {CURRENCY_NAME}"
            type_str = "رتبة (Role)" if itype == "role" else "منتج آخر (Ticket)"
            embed.add_field(
                name=f"📦 {name} │ الكود: [{code}]",
                value=f"◈ **النوع:** {type_str}\n◈ **السعر:** {p_str}\n◈ **الصلاحية:** {dur_str}\n──────────────────",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="شراء منتج 💳", style=discord.ButtonStyle.success, custom_id="btn_buy_product")
    async def buy_product(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(BuyModal())

class BuyModal(ui.Modal, title="💳 شراء منتج من المتجر"):
    code = ui.TextInput(label="كود المنتج (6 أرقام)", placeholder="مثال: 123456", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        p_code = self.code.value.strip()
        cursor.execute("SELECT name, item_type, role_id, price, duration_minutes FROM products WHERE code = ?", (p_code,))
        prod = cursor.fetchone()

        if not prod:
            embed = discord.Embed(title="❌ خطأ", description="كود المنتج المدخل غير صحيح أو غير موجود.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        p_name, p_type, r_id, price, dur = prod
        _, _, _, coins = get_user_data(interaction.user.id)

        if coins < price:
            embed = discord.Embed(title="❌ رصيد غير كافٍ", description="لا تمتلك العملات الكافية لشراء هذا المنتج.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        cursor.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (price, interaction.user.id))
        conn.commit()

        if p_type == "role":
            role = interaction.guild.get_role(r_id)
            if role:
                await interaction.user.add_roles(role)
                if dur > 0:
                    exp = datetime.datetime.utcnow() + datetime.timedelta(minutes=dur)
                    cursor.execute("INSERT INTO temp_roles VALUES (?, ?, ?)", (interaction.user.id, r_id, exp.isoformat()))
                    conn.commit()
                embed = discord.Embed(
                    title="🎉 │ عملية شراء ناجحة",
                    description=f"تم شراء وإعطاء رتبة **{role.name}** بنجاح!\nخصم من رصيدك: `{price}` {CURRENCY_NAME}",
                    color=0x2ECC71
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            guild = interaction.guild
            store_role = guild.get_role(ROLE_STORE_TEAM_ID)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                store_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            ticket_chan = await guild.create_text_channel(name=f"ticket-{interaction.user.name}", overwrites=overwrites)
            
            embed_ticket = discord.Embed(
                title="🎫 │ تذكرة طلب منتج جديد",
                description=(
                    f"أهلاً بك {interaction.user.mention} في تذكرة طلبك!\n\n"
                    f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                    f"📦 **المنتج:** `{p_name}`\n"
                    f"🔑 **الكود:** `{p_code}`\n"
                    f"💰 **المبلغ المخصوم:** `{price}` {CURRENCY_NAME}\n"
                    f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                    f"سيقوم <@&{ROLE_STORE_TEAM_ID}> بمساندتك واستلام التذكرة قريباً."
                ),
                color=0x2ECC71
            )
            await ticket_chan.send(embed=embed_ticket, view=TicketControlsView())

            embed_user = discord.Embed(
                title="✅ │ تم فتح تذكرة طلبك",
                description=f"تم خصم المبلغ وفتح تذكرة خاصة لمتابعة استلام المنتج: {ticket_chan.mention}",
                color=0x2ECC71
            )
            await interaction.response.send_message(embed=embed_user, ephemeral=True)

        await log_event(interaction.guild, "عملية شراء", f"قام {interaction.user.mention} بشراء المنتج **{p_name}** بسعر `{price}` كوينز.")

# --- عناصر التحكم داخل التيكت ---
class TicketControlsView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="استلام التذكرة ✋", style=discord.ButtonStyle.primary, custom_id="btn_claim_ticket")
    async def claim(self, interaction: discord.Interaction, button: ui.Button):
        if not any(r.id == ROLE_STORE_TEAM_ID for r in interaction.user.roles):
            embed = discord.Embed(title="❌ غير مصرح", description="هذا الزر مخصص لفريق المتجر فقط.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        embed = discord.Embed(
            title="✋ │ تم استلام التذكرة",
            description=f"قام الإداري {interaction.user.mention} بمسك واستلام هذه التذكرة.",
            color=0x3498DB
        )
        await interaction.response.send_message(embed=embed)

    @ui.button(label="إغلاق التذكرة 🔒", style=discord.ButtonStyle.danger, custom_id="btn_close_ticket")
    async def close(self, interaction: discord.Interaction, button: ui.Button):
        if not any(r.id == ROLE_STORE_TEAM_ID for r in interaction.user.roles):
            embed = discord.Embed(title="❌ غير مصرح", description="هذا الزر مخصص لفريق المتجر فقط.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        embed = discord.Embed(
            title="🔒 │ إغلاق التذكرة",
            description="سيتم حذف وحفظ التذكرة وإغلاق القناة خلال 5 ثوانٍ...",
            color=0xE74C3C
        )
        await interaction.response.send_message(embed=embed)
        await asyncio.sleep(5)
        await interaction.channel.delete()

# --- لوحة الإدارة ---
class AdminPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="إدارة المنتجات 📦", style=discord.ButtonStyle.primary, custom_id="admin_prod")
    async def manage_prod(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddProductModal())

    @ui.button(label="إدارة الكوينز 💰", style=discord.ButtonStyle.success, custom_id="admin_coins")
    async def manage_coins(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ManageCoinsModal())

    @ui.button(label="إدارة المستويات 📈", style=discord.ButtonStyle.secondary, custom_id="admin_lvl")
    async def manage_level(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ManageLevelModal())

    @ui.button(label="متطلبات ومكافأة الترقية ⚙️", style=discord.ButtonStyle.danger, custom_id="admin_reqs")
    async def set_reqs(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(SetLevelReqModal())

class SetLevelReqModal(ui.Modal, title="⚙️ ضبط متطلبات ومكافأة المستوى"):
    target_lvl = ui.TextInput(label="رقم المستوى (1-100)", placeholder="مثال: 5", required=True)
    msgs = ui.TextInput(label="عدد الرسائل المطلوبة", placeholder="مثال: 50", required=True)
    voice_mins = ui.TextInput(label="دقائق الفويس المطلوبة", placeholder="مثال: 120", required=True)
    reward = ui.TextInput(label="مكافأة الوصول للمستوى (BX COINS)", placeholder="مثال: 1000", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            lvl = int(self.target_lvl.value)
            m = int(self.msgs.value)
            v = int(self.voice_mins.value)
            r = int(self.reward.value)
            if not (1 <= lvl <= 100):
                raise ValueError
        except ValueError:
            embed = discord.Embed(title="❌ خطأ", description="يرجى كتابة أرقام وقيم صحيحة.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        cursor.execute("INSERT OR REPLACE INTO level_reqs VALUES (?, ?, ?, ?)", (lvl, m, v, r))
        conn.commit()

        embed = discord.Embed(
            title="✅ │ تم حفظ متطلبات ومكافأة المستوى",
            description=(
                f"تم تحديث الشروط بنجاح لـ **Level {lvl}**:\n\n"
                f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                f"💬 **الرسائل المطلوبة:** `{m}`\n"
                f"🎙️ **دقائق الفويس:** `{v}` دقيقة\n"
                f"🎁 **المكافأة:** `{r}` {CURRENCY_NAME}\n"
                f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
            ),
            color=0x2ECC71
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await log_event(interaction.guild, "تعديل شروط المستوى", f"تم تحديد شروط ومكافأة المستوى **{lvl}** بواسطة {interaction.user.mention}")

class AddProductModal(ui.Modal, title="📦 إضافة منتج جديد للمتجر"):
    p_name = ui.TextInput(label="اسم المنتج", placeholder="مثال: رتبة مميزة", required=True)
    p_type = ui.TextInput(label="النوع (اركب role أم other)", placeholder="role أو other", required=True)
    role_id = ui.TextInput(label="آي دي الرتبة (إذا كان role)", placeholder="اكتب الآي دي هنا أو اتركه فارغاً", required=False)
    price = ui.TextInput(label="السعر (0 للمجاني)", placeholder="مثال: 500", required=True)
    duration = ui.TextInput(label="المدة بالدقائق (0 للدائم)", placeholder="مثال: 1440 (ليوم واحد)", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        code = ''.join(random.choices(string.digits, k=6))
        r_id = int(self.role_id.value) if self.role_id.value else 0
        
        cursor.execute("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)", 
                       (code, self.p_name.value, self.p_type.value.lower(), r_id, int(self.price.value), int(self.duration.value)))
        conn.commit()

        embed = discord.Embed(
            title="✅ │ تم إضافة المنتج بنجاح",
            description=f"تم نشر المنتج **{self.p_name.value}** في المتجر.\n🔑 **الكود المختصر:** `{code}`",
            color=0x2ECC71
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ManageCoinsModal(ui.Modal, title="💰 تعديل رصيد كوينز لاعب"):
    target_id = ui.TextInput(label="آي دي العضو", placeholder="مثال: 123456789", required=True)
    action = ui.TextInput(label="العملية (add أو remove)", placeholder="add أو remove", required=True)
    amount = ui.TextInput(label="المبلغ", placeholder="مثال: 1000", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        uid = int(self.target_id.value)
        amt = int(self.amount.value)
        get_user_data(uid)

        if self.action.value.lower() == "add":
            cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amt, uid))
            act_text = "إضافة"
        else:
            cursor.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amt, uid))
            act_text = "خصم"
        conn.commit()

        embed = discord.Embed(
            title="✅ │ تم تعديل الرصيد",
            description=f"تمت عملية {act_text} بمقدار `{amt}` {CURRENCY_NAME} للحساب <@{uid}>.",
            color=0x2ECC71
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ManageLevelModal(ui.Modal, title="📈 تعديل مستوى لاعب يدوي"):
    target_id = ui.TextInput(label="آي دي العضو", placeholder="مثال: 123456789", required=True)
    new_lvl = ui.TextInput(label="المستوى الجديد (1-100)", placeholder="مثال: 10", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        uid = int(self.target_id.value)
        lvl = int(self.new_lvl.value)
        get_user_data(uid)

        cursor.execute("UPDATE users SET level = ? WHERE user_id = ?", (lvl, uid))
        conn.commit()

        embed = discord.Embed(
            title="✅ │ تم تعديل المستوى",
            description=f"تم تغيير مستوى العضو <@{uid}> يدويّاً إلى **Level {lvl}**.",
            color=0x2ECC71
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ==================== الأوامر والمهام الدوريات ====================

@bot.event
async def on_ready():
    bot.add_view(UserPanelView())
    bot.add_view(StorePanelView())
    bot.add_view(AdminPanelView())
    bot.add_view(TicketControlsView())
    voice_tracker.start()
    temp_role_checker.start()
    print(f"Logged in successfully as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    if any(r.id == ROLE_MEMBER_ID for r in message.author.roles):
        cursor.execute("INSERT INTO users (user_id, messages) VALUES (?, 1) ON CONFLICT(user_id) DO UPDATE SET messages = messages + 1", (message.author.id,))
        conn.commit()
        await check_level_up(message.author, message.channel)

    await bot.process_commands(message)

@tasks.loop(minutes=1)
async def voice_tracker():
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            for member in vc.members:
                if not member.bot and any(r.id == ROLE_MEMBER_ID for r in member.roles):
                    cursor.execute("INSERT INTO users (user_id, voice_minutes) VALUES (?, 1) ON CONFLICT(user_id) DO UPDATE SET voice_minutes = voice_minutes + 1", (member.id,))
                    conn.commit()
                    await check_level_up(member)

@tasks.loop(minutes=1)
async def temp_role_checker():
    now = datetime.datetime.utcnow().isoformat()
    cursor.execute("SELECT user_id, role_id FROM temp_roles WHERE expire_time <= ?", (now,))
    expired = cursor.fetchall()
    
    for uid, rid in expired:
        for guild in bot.guilds:
            member = guild.get_member(uid)
            role = guild.get_role(rid)
            if member and role:
                await member.remove_roles(role)
                await log_event(guild, "انتهاء صلاحية رتبة", f"تم سحب رتبة **{role.name}** تلقائياً من {member.mention} لانتهاء مدتها.")
        cursor.execute("DELETE FROM temp_roles WHERE user_id = ? AND role_id = ?", (uid, rid))
    conn.commit()

# ==================== أوامر إحضار اللوحات المنفصلة ====================

# 1. أمر لوحة الأعضاء
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_user(ctx):
    await ctx.message.delete()
    embed = discord.Embed(
        title="🌐 │ لوحة خدمات الأعضاء والتفاعل",
        description=(
            "مرحباً بكم في لوحة الأعضاء التفاعلية!\n"
            "يمكنك استخدام الأزرار أدناه للاستعلام عن حسابك، متابعة مستواك، أو تحويل الكوينز.\n\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            "💰 **استعلام عن الرصيد:** لمعرفة رصيدك الحقيقي.\n"
            "📊 **المستوى والتفاعل:** لعرض مستواك والرسائل ودقائق الصوتي.\n"
            "💸 **تحويل عملات:** لتحويل BX COINS لأصدقائك."
        ),
        color=0x3498DB
    )
    await ctx.send(embed=embed, view=UserPanelView())

# 2. أمر لوحة المتجر
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_store(ctx):
    await ctx.message.delete()
    embed = discord.Embed(
        title="🛒 │ متجر السيرفر الرسمي (BX Store)",
        description=(
            "أهلاً بكم في متجر السيرفر!\n"
            "استعرض المنتجات المتاحة واشترِ الرولات والخدمات باستخدام العملات.\n\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            "🛍️ **عرض المنتجات:** لاستعراض كافة السلع وأكوادها لـ 6 أرقام.\n"
            "💳 **شراء منتج:** لإدخال كود السلعة وإتمام الشراء فورياً."
        ),
        color=0xF1C40F
    )
    await ctx.send(embed=embed, view=StorePanelView())

# 3. أمر لوحة الإدارة
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_admin(ctx):
    await ctx.message.delete()
    embed = discord.Embed(
        title="⚙️ │ لوحة الإدارة والتحكم",
        description=(
            "اللوحة الخاصة بطاقم إدارة السيرفر والمتجر.\n\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            "📦 **إدارة المنتجات:** لإضافة منتجات ورولات جديدة للبيع.\n"
            "💰 **إدارة الكوينز:** لإضافة أو خصم الكوينز من الأعضاء.\n"
            "📈 **إدارة المستويات:** لترقية أو تخفيض ليفل لاعب يدوياً.\n"
            "⚙️ **متطلبات ومكافأة الترقية:** لتحديد شروط ومكافأة كل مستوى."
        ),
        color=0xE74C3C
    )
    await ctx.send(embed=embed, view=AdminPanelView())

bot.run(os.getenv("DISCORD_TOKEN"))
