import os
import discord
from discord.ext import commands, tasks
from discord import ui
import sqlite3
import datetime
import random
import string
import asyncio
from dotenv import load_dotenv

# تحميل متغيرات البيئة
load_dotenv()

# ==================== الإعدادات والثوابت ====================
ROLE_MEMBER_ID = 1541620051033985085
CHANNEL_LEVELUP_ID = 1544834419544821780
ROLE_STORE_TEAM_ID = 1547655214507622481
CHANNEL_LOG_ID = 1547668485340012575
CHANNEL_RATINGS_ID = 1547726880134664273
CHANNEL_WHEEL_LOG_ID = 1547732226358120579

ROLE_VIP_ID = 1541619810230730762
ROLE_LUCKY_STAR_ID = 1547731648982945792

CURRENCY_NAME = "BX COINS 🪙"

# تتبع أوقات دخول الرومات الصوتية
voice_tracking = {}

# ==================== إعداد قاعدة البيانات ====================
conn = sqlite3.connect("bot_database.db")
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    messages INTEGER DEFAULT 0,
    voice_minutes INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    coins INTEGER DEFAULT 0,
    last_free_spin TEXT,
    extra_free_spins INTEGER DEFAULT 0
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
    original_price INTEGER DEFAULT 0,
    duration_minutes INTEGER DEFAULT 0,
    allowed_users TEXT DEFAULT 'ALL',
    status TEXT DEFAULT 'available',
    allow_coupons INTEGER DEFAULT 1,
    allowed_coupon_perc INTEGER DEFAULT 0
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS temp_roles (
    user_id INTEGER,
    role_id INTEGER,
    expire_time TEXT
)''')

cursor.execute('''CREATE TABLE IF NOT EXISTS user_coupons (
    code TEXT PRIMARY KEY,
    user_id INTEGER,
    discount INTEGER,
    is_used INTEGER DEFAULT 0
)''')

conn.commit()

cols_to_add = [
    ("users", "last_free_spin TEXT"),
    ("users", "extra_free_spins INTEGER DEFAULT 0"),
    ("products", "original_price INTEGER DEFAULT 0"),
    ("products", "allowed_users TEXT DEFAULT 'ALL'"),
    ("products", "status TEXT DEFAULT 'available'"),
    ("products", "allow_coupons INTEGER DEFAULT 1"),
    ("products", "allowed_coupon_perc INTEGER DEFAULT 0")
]
for table, col in cols_to_add:
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass
conn.commit()

# ==================== البوت والإنتنتس ====================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== الدوال المساعدة ====================
async def log_event(guild, title, description, color=0x3498DB):
    if not guild: return
    log_channel = guild.get_channel(CHANNEL_LOG_ID)
    if log_channel:
        embed = discord.Embed(
            title=f"📝 │ {title}",
            description=f"{description}\n\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
            color=color,
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_footer(text="نظام السجلات الشامل • Brevix Logs", icon_url=guild.icon.url if guild.icon else None)
        await log_channel.send(embed=embed)

async def log_wheel_event(guild, user, spin_type, cost_text, prize_name, coins_left):
    if not guild: return
    wheel_log_chan = guild.get_channel(CHANNEL_WHEEL_LOG_ID)
    if wheel_log_chan:
        embed = discord.Embed(
            title="🎰 │ سجل عمليات عجلة الحظ",
            description=(
                f"👤 **العضو:** {user.mention} (`{user.id}`)\n"
                f"🌀 **نوع اللفة:** `{spin_type}`\n"
                f"💳 **التكلفة:** `{cost_text}`\n"
                f"🎉 **الجائزة المكسوبة:** **{prize_name}**\n"
                f"💰 **رصيد الكوينز المتبقي:** `{coins_left}` {CURRENCY_NAME}\n"
                f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
            ),
            color=0x9B59B6,
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="حماية ومراقبة عجلة الحظ", icon_url=guild.icon.url if guild.icon else None)
        await wheel_log_chan.send(embed=embed)

def get_user_data(user_id):
    cursor.execute("SELECT messages, voice_minutes, level, coins, last_free_spin, extra_free_spins FROM users WHERE user_id = ?", (user_id,))
    data = cursor.fetchone()
    if not data:
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return (0, 0, 1, 0, None, 0)
    return data

async def check_level_up(member, channel=None):
    if not any(r.id == ROLE_MEMBER_ID for r in member.roles):
        return
    msgs, v_mins, current_lvl, coins, _, _ = get_user_data(member.id)
    next_lvl = current_lvl + 1
    if next_lvl > 100: return

    cursor.execute("SELECT req_messages, req_voice_mins, reward_coins FROM level_reqs WHERE level = ?", (next_lvl,))
    req = cursor.fetchone()
    if not req: return

    req_msgs, req_vmins, reward = req
    msgs_condition = (req_msgs > 0 and msgs >= req_msgs)
    vmins_condition = (req_vmins > 0 and v_mins >= req_vmins)

    if msgs_condition or vmins_condition:
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
                    f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
                ),
                color=0xF1C40F
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await lvl_channel.send(content=member.mention, embed=embed)

        reason = "الرسائل النصية" if msgs_condition else "الدقائق الصوتية"
        await log_event(member.guild, "ترقية مستوى تلقائية", f"اللاعب {member.mention} وصل إلى **Level {next_lvl}** بفضل ({reason}) وحصل على `{reward}` كوينز.")
        await check_level_up(member, channel)

# ==================== المهارات والمهام الخلفية ====================
@tasks.loop(minutes=1)
async def check_temp_roles():
    now = datetime.datetime.utcnow().isoformat()
    cursor.execute("SELECT user_id, role_id FROM temp_roles WHERE expire_time <= ?", (now,))
    expired = cursor.fetchall()
    
    for uid, rid in expired:
        for guild in bot.guilds:
            member = guild.get_member(uid)
            role = guild.get_role(rid)
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="انتهاء مدة الرتبة المؤقتة")
                    await log_event(guild, "انتهاء رتبة مؤقتة", f"تم سحب الرتبة **{role.name}** من {member.mention} لانتهاء الوقت.")
                except Exception:
                    pass
        cursor.execute("DELETE FROM temp_roles WHERE user_id = ? AND role_id = ?", (uid, rid))
    conn.commit()

# ==================== الأزرار والنوافذ التفاعلية ====================

class TicketControlsView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="إغلاق التذكرة 🔒", style=discord.ButtonStyle.danger, custom_id="btn_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("جاري إغلاق التذكرة خلال ثوانٍ...", ephemeral=True)
        await asyncio.sleep(3)
        await interaction.channel.delete()

class TransferModal(ui.Modal, title="💸 تحويل BX COINS"):
    target_id = ui.TextInput(label="آي دي العضو المستلم", placeholder="مثال: 123456789", required=True)
    amount = ui.TextInput(label="المبلغ المراد تحويله", placeholder="مثال: 500", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target = int(self.target_id.value)
            amt = int(self.amount.value)
            if amt <= 0: raise ValueError
        except ValueError:
            embed = discord.Embed(title="❌ خطأ", description="يرجى إدخال بيانات وأرقام صحيحة.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        _, _, _, sender_coins, _, _ = get_user_data(interaction.user.id)
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

class UserPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="استعلام عن الرصيد 💰", style=discord.ButtonStyle.primary, custom_id="btn_balance")
    async def balance(self, interaction: discord.Interaction, button: ui.Button):
        _, _, _, coins, _, _ = get_user_data(interaction.user.id)
        embed = discord.Embed(
            title="💳 │ محفظتك المالية",
            description=f"مرحباً {interaction.user.mention}\n\n◈ **رصيدك الحالي:** `{coins}` {CURRENCY_NAME}",
            color=0x2ECC71
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="المستوى والتفاعل 📊", style=discord.ButtonStyle.secondary, custom_id="btn_stats")
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        msgs, v_mins, lvl, _, _, _ = get_user_data(interaction.user.id)
        embed = discord.Embed(
            title="📊 │ إحصائيات التفاعل والمستوى",
            description=f"أهلاً بك {interaction.user.mention}، إليك تفاصيل نشاطك:\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
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

    @ui.button(label="🎰 عجلة الحظ", style=discord.ButtonStyle.danger, custom_id="btn_wheel")
    async def spin_wheel(self, interaction: discord.Interaction, button: ui.Button):
        user_id = interaction.user.id
        _, _, _, coins, last_spin, extra_spins = get_user_data(user_id)

        now = datetime.datetime.utcnow()
        cost_text = "مجانية"
        spin_type = ""

        if extra_spins > 0:
            cursor.execute("UPDATE users SET extra_free_spins = extra_free_spins - 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            spin_type = "لفة مجانية إضافية 🎁"
            cost_text = "0 كوينز (إضافية)"
        elif not last_spin or (now - datetime.datetime.fromisoformat(last_spin)).total_seconds() >= 86400:
            cursor.execute("UPDATE users SET last_free_spin = ? WHERE user_id = ?", (now.isoformat(), user_id))
            conn.commit()
            spin_type = "لفة مجانية يومية 🌟"
            cost_text = "0 كوينز (يومية)"
        else:
            if coins < 100:
                embed_err = discord.Embed(
                    title="❌ لا يمكن اللف",
                    description="استنفذت لفتك المجانية اليومية! تكلفة اللفة الإضافية هي `100` BX COINS ورصيدك غير كافٍ.",
                    color=0xE74C3C
                )
                return await interaction.response.send_message(embed=embed_err, ephemeral=True)
            
            cursor.execute("UPDATE users SET coins = coins - 100 WHERE user_id = ?", (user_id,))
            conn.commit()
            coins -= 100
            spin_type = "لفة مدفوعة 🪙"
            cost_text = "100 BX COINS"

        prizes = [
            {"type": "coupon", "val": 5, "name": "كوبون خصم 5%", "weight": 20},
            {"type": "coupon", "val": 10, "name": "كوبون خصم 10%", "weight": 15},
            {"type": "coupon", "val": 25, "name": "كوبون خصم 25%", "weight": 3},
            {"type": "coupon", "val": 50, "name": "كوبون خصم 50%", "weight": 1},
            {"type": "coupon", "val": 100, "name": "كوبون خصم 100%", "weight": 0.20},
            {"type": "coins_rand", "val": [10, 20, 30], "name": "عملات BX COINS (10-30)", "weight": 80},
            {"type": "coins", "val": 50, "name": "50 BX COINS 🪙", "weight": 30},
            {"type": "coins", "val": 70, "name": "70 BX COINS 🪙", "weight": 20},
            {"type": "coins", "val": 100, "name": "100 BX COINS 🪙", "weight": 3},
            {"type": "coins", "val": 1000, "name": "1000 BX COINS 🪙", "weight": 0.05},
            {"type": "extra_spin", "val": 1, "name": "لفة مجانية إضافية 🔄", "weight": 20},
            {"type": "role", "val": ROLE_VIP_ID, "name": "رتبة VIP 👑", "weight": 1},
            {"type": "role", "val": ROLE_LUCKY_STAR_ID, "name": "رتبة Lucky Star ⭐", "weight": 10},
        ]

        weights = [p["weight"] for p in prizes]
        won_prize = random.choices(prizes, weights=weights, k=1)[0]
        prize_display = ""

        if won_prize["type"] == "coupon":
            cpn_code = "CPN-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            cursor.execute("INSERT INTO user_coupons VALUES (?, ?, ?, 0)", (cpn_code, user_id, won_prize["val"]))
            conn.commit()
            prize_display = f"كوبون خصم `{won_prize['val']}%` (كود الخصم: `{cpn_code}`)"

        elif won_prize["type"] == "coins_rand":
            amt = random.choice(won_prize["val"])
            cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amt, user_id))
            conn.commit()
            coins += amt
            prize_display = f"`{amt}` {CURRENCY_NAME}"

        elif won_prize["type"] == "coins":
            amt = won_prize["val"]
            cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amt, user_id))
            conn.commit()
            coins += amt
            prize_display = f"`{amt}` {CURRENCY_NAME}"

        elif won_prize["type"] == "extra_spin":
            cursor.execute("UPDATE users SET extra_free_spins = extra_free_spins + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            prize_display = "لفة مجانية إضافية جديدة 🔄"

        elif won_prize["type"] == "role":
            role = interaction.guild.get_role(won_prize["val"])
            if role:
                if role not in interaction.user.roles:
                    await interaction.user.add_roles(role)
                    prize_display = f"رتبة **{role.name}**"
                else:
                    cursor.execute("UPDATE users SET coins = coins + 150 WHERE user_id = ?", (user_id,))
                    conn.commit()
                    coins += 150
                    prize_display = f"رتبة **{role.name}** (تم تعويضك بـ 150 كوينز لامتلاكك إياها)"

        embed_result = discord.Embed(
            title="🎰 │ نتائج عجلة الحظ",
            description=(
                f"أهلاً بك {interaction.user.mention}!\n\n"
                f"🌀 **نوع اللفة:** `{spin_type}`\n"
                f"🎉 **الجائزة المكسوبة:** **{prize_display}**\n\n"
                f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                f"تم إضافة الجائزة إلى حسابك تلقائياً!"
            ),
            color=0x9B59B6
        )
        embed_result.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed_result, ephemeral=True)

        await log_wheel_event(interaction.guild, interaction.user, spin_type, cost_text, prize_display, coins)

class RateProductModal(ui.Modal, title="⭐ تقييم منتجات المتجر"):
    prod_info = ui.TextInput(label="اسم أو كود المنتج", placeholder="مثال: رتبة VIP أو 123456", required=True)
    rating = ui.TextInput(label="التقييم من 1 إلى 5", placeholder="اكتب رقم من 1 إلى 5", required=True)
    review = ui.TextInput(label="رأيك وسبب التقييم", placeholder="اكتب ملاحظاتك وتقييمك هنا...", style=discord.TextStyle.paragraph, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            stars_num = int(self.rating.value)
            if not (1 <= stars_num <= 5): raise ValueError
        except ValueError:
            embed = discord.Embed(title="❌ خطأ", description="يرجى كتابة رقم تقييم صحيح بين 1 و 5.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        stars_str = "⭐" * stars_num
        ratings_channel = interaction.guild.get_channel(CHANNEL_RATINGS_ID)

        if ratings_channel:
            embed_review = discord.Embed(
                title="🌟 تقييم جديد للمتجر",
                description=(
                    f"**صاحب التقييم:** {interaction.user.mention}\n"
                    f"**المنتج:** `{self.prod_info.value}`\n"
                    f"**التقييم:** {stars_str} ({stars_num}/5)\n"
                    f"**الرأي والتفاصيل:**\n```{self.review.value}```"
                ),
                color=0x2ECC71
            )
            await ratings_channel.send(embed=embed_review)
        
        await interaction.response.send_message("تم إرسال تقييمك بنجاح، شكراً لك!", ephemeral=True)

class BuyModal(ui.Modal, title="💳 شراء منتج من المتجر"):
    code = ui.TextInput(label="كود المنتج (6 أرقام)", placeholder="مثال: 123456", required=True)
    coupon = ui.TextInput(label="كود الخصم (اختياري)", placeholder="ادخل كود الكوبون إن وجد", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        p_code = self.code.value.strip()
        c_code = self.coupon.value.strip() if self.coupon.value else None

        cursor.execute("SELECT name, item_type, role_id, price, duration_minutes, allowed_users, status, allow_coupons, allowed_coupon_perc FROM products WHERE code = ?", (p_code,))
        prod = cursor.fetchone()

        if not prod:
            embed = discord.Embed(title="❌ خطأ", description="كود المنتج المدخل غير صحيح أو غير موجود.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        p_name, p_type, r_id, price, dur, allowed, status, allow_cpn, req_cpn_perc = prod

        if status != "available":
            embed = discord.Embed(title="❌ غير متوفر", description="عذراً، هذا المنتج غير متوفر للشراء حالياً.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if allowed != "ALL":
            allowed_ids = [uid.strip() for uid in allowed.split(",")]
            if str(interaction.user.id) not in allowed_ids:
                embed = discord.Embed(title="❌ غير مصرح", description="عفواً، هذا المنتج مخصص لأشخاص محددين فقط.", color=0xE74C3C)
                return await interaction.response.send_message(embed=embed, ephemeral=True)

        if p_type == "role":
            role = interaction.guild.get_role(r_id)
            if role and role in interaction.user.roles:
                embed = discord.Embed(
                    title="❌ تمتلك الرتبة بالفعل",
                    description=f"أنت تمتلك رتبة **{role.name}** بالفعل على حسابك.",
                    color=0xE74C3C
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)

        discount_percent = 0
        if c_code:
            if allow_cpn == 0:
                embed = discord.Embed(title="❌ غير مسموح بالخصم", description="عذراً، هذا المنتج غير قابل لتطبيق أي كوبونات خصم.", color=0xE74C3C)
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            cursor.execute("SELECT discount FROM user_coupons WHERE code = ? AND user_id = ? AND is_used = 0", (c_code, interaction.user.id))
            cpn_data = cursor.fetchone()
            
            if not cpn_data:
                embed = discord.Embed(title="❌ كوبون غير صالح", description="كود الخصم المدخل غير صحيح أو مستخدم سابقاً.", color=0xE74C3C)
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            cpn_disc = cpn_data[0]
            if req_cpn_perc > 0 and cpn_disc != req_cpn_perc:
                embed = discord.Embed(
                    title="❌ كوبون غير مطابق",
                    description=f"هذا المنتج يتطلب حصراً كوبون خصم بمقدار `{req_cpn_perc}%`.",
                    color=0xE74C3C
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)

            discount_percent = cpn_disc

        final_price = int(price * (100 - discount_percent) / 100)
        _, _, _, coins, _, _ = get_user_data(interaction.user.id)

        if coins < final_price:
            embed = discord.Embed(title="❌ رصيد غير كافٍ", description=f"سعر المنتج بعد الخصم: `{final_price}` كوينز. رصيدك غير كافٍ.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        cursor.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (final_price, interaction.user.id))
        if c_code:
            cursor.execute("DELETE FROM user_coupons WHERE code = ?", (c_code,))
        conn.commit()

        if p_type == "role":
            role = interaction.guild.get_role(r_id)
            if role:
                await interaction.user.add_roles(role)
                if dur > 0:
                    exp = (datetime.datetime.utcnow() + datetime.timedelta(minutes=dur)).isoformat()
                    cursor.execute("INSERT INTO temp_roles VALUES (?, ?, ?)", (interaction.user.id, r_id, exp))
                    conn.commit()
                embed = discord.Embed(
                    title="🎉 │ عملية شراء ناجحة",
                    description=f"تم شراء وإعطاء رتبة **{role.name}** بنجاح!\nالمبلغ المخصوم: `{final_price}` {CURRENCY_NAME}",
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
                    f"📦 **المنتج:** `{p_name}`\n"
                    f"🔑 **الكود:** `{p_code}`\n"
                    f"💰 **المخصوم:** `{final_price}` {CURRENCY_NAME}\n"
                    f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                    f"سيقوم طاقم الدعم بمساعدتك قريباً."
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

        await log_event(interaction.guild, "عملية شراء ناجحة", f"قام {interaction.user.mention} بشراء **{p_name}** بسعر `{final_price}` كوينز.")

class StorePanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="عرض المنتجات 🛍️", style=discord.ButtonStyle.primary, custom_id="btn_list_products")
    async def list_products(self, interaction: discord.Interaction, button: ui.Button):
        cursor.execute("SELECT code, name, item_type, price, original_price, duration_minutes, allowed_users, status, allow_coupons, allowed_coupon_perc FROM products")
        prods = cursor.fetchall()
        if not prods:
            embed = discord.Embed(title="🛒 │ المتجر فارغ", description="لا توجد منتجات متاحة للشراء حالياً.", color=0xE74C3C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        embed = discord.Embed(
            title="🛒 │ قائمة منتجات المتجر المتاحة",
            description="استخدم كود المنتج عند عملية الشراء:\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
            color=0xF1C40F
        )
        for code, name, itype, price, orig_price, dur, allowed, status, allow_cpn, cpn_perc in prods:
            dur_str = f"`{dur}` دقيقة" if dur > 0 else "`دائم`"
            p_str = f"~~{orig_price}~~ **{price}** {CURRENCY_NAME} 🔥" if orig_price > price else f"`{price}` {CURRENCY_NAME}"

            status_map = {"available": "✅ متوفر", "unavailable": "❌ غير متوفر", "coming_soon": "⏳ قريباً"}
            status_str = status_map.get(status, "✅ متوفر")
            allowed_str = "عام للجميع 🌐" if allowed == 'ALL' else "خاص 🔒"

            cpn_info = "مسموح 🎫" if allow_cpn == 1 else "ممنوع ❌"
            if allow_cpn == 1 and cpn_perc > 0:
                cpn_info = f"كوبون `{cpn_perc}%` حصراً 🎯"

            embed.add_field(
                name=f"📦 {name} │ الكود: [{code}]",
                value=(
                    f"◈ **الحالة:** {status_str} | **السعر:** {p_str}\n"
                    f"◈ **الصلاحية:** {dur_str} | **الكوبونات:** {cpn_info}\n"
                    f"◈ **المتاح لهم:** {allowed_str}\n"
                    f"──────────────────"
                ),
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="شراء منتج 💳", style=discord.ButtonStyle.success, custom_id="btn_buy_product")
    async def buy_product(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(BuyModal())

    @ui.button(label="⭐ قيم منتجاتنا", style=discord.ButtonStyle.secondary, custom_id="btn_rate_product")
    async def rate_product(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(RateProductModal())

# ==================== لوحة الإدارة والأوامر ====================

class AdminAddCoinsModal(ui.Modal, title="➕ إضافة / خصم كوينز"):
    target_id = ui.TextInput(label="آي دي العضو", placeholder="مثال: 123456789", required=True)
    amount = ui.TextInput(label="المبلغ (سالب للخصم، موجب للإضافة)", placeholder="مثال: 500 أو -200", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target = int(self.target_id.value)
            amt = int(self.amount.value)
        except ValueError:
            return await interaction.response.send_message("❌ أدخل أرقاماً صحيحة.", ephemeral=True)

        get_user_data(target)
        cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amt, target))
        conn.commit()

        await interaction.response.send_message(f"✅ تم تعديل رصيد <@{target}> بمقدار `{amt}` كوينز بنجاح.", ephemeral=True)
        await log_event(interaction.guild, "تعديل رصيد إداري", f"قام الأدمن {interaction.user.mention} بتعديل رصيد <@{target}> بمقدار `{amt}` كوينز.")

class AdminAddProductModal(ui.Modal, title="📦 إضافة منتج جديد"):
    code = ui.TextInput(label="كود المنتج الفريد", placeholder="مثال: 101010", required=True)
    name = ui.TextInput(label="اسم المنتج", placeholder="مثال: رتبة VIP شهري", required=True)
    item_type = ui.TextInput(label="النوع (role / ticket)", placeholder="اكتب role أو ticket", required=True)
    price = ui.TextInput(label="السعر الأساسي", placeholder="مثال: 1000", required=True)
    role_id = ui.TextInput(label="آي دي الرتبة (إذا كان نوعه role)", placeholder="اتركه 0 إذا كان ticket", default="0", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            p_code = self.code.value.strip()
            p_name = self.name.value.strip()
            p_type = self.item_type.value.strip().lower()
            p_price = int(self.price.value)
            r_id = int(self.role_id.value) if self.role_id.value else 0
        except ValueError:
            return await interaction.response.send_message("❌ بيانات السعر أو الآي دي غير صحيحة.", ephemeral=True)

        cursor.execute("INSERT OR REPLACE INTO products (code, name, item_type, price, role_id) VALUES (?, ?, ?, ?, ?)",
                       (p_code, p_name, p_type, p_price, r_id))
        conn.commit()

        await interaction.response.send_message(f"✅ تم إضافة/تعديل المنتج `{p_name}` بكود [{p_code}] بنجاح.", ephemeral=True)

class AdminPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="إدارة الكوينز 🪙", style=discord.ButtonStyle.primary, custom_id="admin_btn_coins")
    async def manage_coins(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AdminAddCoinsModal())

    @ui.button(label="إضافة منتج 📦", style=discord.ButtonStyle.success, custom_id="admin_btn_add_prod")
    async def add_product(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AdminAddProductModal())

# ==================== الأحداث والتسجيل ====================

@bot.event
async def on_ready():
    bot.add_view(UserPanelView())
    bot.add_view(StorePanelView())
    bot.add_view(TicketControlsView())
    bot.add_view(AdminPanelView())
    
    if not check_temp_roles.is_running():
        check_temp_roles.start()

    print(f"✅ تم تشغيل البوت بنجاح باسم: {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    user_id = message.author.id
    get_user_data(user_id)

    cursor.execute("UPDATE users SET messages = messages + 1 WHERE user_id = ?", (user_id,))
    conn.commit()

    await check_level_up(message.author, message.channel)
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot: return

    user_id = member.id
    now = datetime.datetime.utcnow()

    if before.channel is None and after.channel is not None:
        voice_tracking[user_id] = now
    elif before.channel is not None and after.channel is None:
        join_time = voice_tracking.pop(user_id, None)
        if join_time:
            mins = int((now - join_time).total_seconds() / 60)
            if mins > 0:
                get_user_data(user_id)
                cursor.execute("UPDATE users SET voice_minutes = voice_minutes + ? WHERE user_id = ?", (mins, user_id))
                conn.commit()
                await check_level_up(member)

# ==================== أوامر إرسال اللوحات ====================

@bot.command()
async def setup_store(ctx):
    embed = discord.Embed(
        title="🛒 | متجر Brevix الرسمي",
        description="أهلاً بك في المتجر! اضغط على الأزرار أدناه للاستعراض والشراء.",
        color=0xF1C40F
    )
    await ctx.send(embed=embed, view=StorePanelView())

@bot.command()
async def setup_user(ctx):
    embed = discord.Embed(
        title="👤 | لوحة خدمات الأعضاء",
        description="استخدم الأزرار أدناه للتحكم بملفك الشخصي وعجلة الحظ.",
        color=0x3498DB
    )
    await ctx.send(embed=embed, view=UserPanelView())

@bot.command()
async def setup_admin(ctx):
    embed = discord.Embed(
        title="⚙️ | لوحة التحكم الإدارية",
        description="استخدم الأزرار أدناه لإدارة رصيد الأعضاء وإضافة المنتجات.",
        color=0xE74C3C
    )
    await ctx.send(embed=embed, view=AdminPanelView())

@bot.command()
async def ping(ctx):
    await ctx.send("Pong! 🏓 البوت شغال وبيستجيب للأوامر.")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ **خطأ:** تحتاج صلاحية Administrator لتنفيذ هذا الأمر.")
    else:
        await ctx.send(f"❌ **حدث خطأ:** `{error}`")

@bot.command()
async def test(ctx):
    await ctx.send("الأمر شغال تمام!")

bot.run(os.getenv("DISCORD_TOKEN"))
