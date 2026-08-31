import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import datetime
import asyncio

# --- إعدادات البوت والـ Intents ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- الثوابت والمُعرّفات (IDs) ---
SHIFT_CHANNEL_ID = 1542004969983574066
EXCUSE_CHANNEL_ID = 1544032822098927877
WARNING_CHANNEL_ID = 1543078048864403577
COMMUNITY_MANAGER_ROLE_ID = 1541599646810374234

# --- إدارة قاعدة البيانات ---
def init_db():
    conn = sqlite3.connect("shifts_bot.db")
    cursor = conn.cursor()
    
    # جدول المناوبات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            time TEXT,
            mode TEXT -- "BEFORE_10" أو "AT_TIME_10"
        )
    ''')
    
    # جدول تسكينات الإداريين في المناوبات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shift_staff (
            shift_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (shift_id, user_id)
        )
    ''')
    
    # جدول الإجازات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vacations (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            end_date TEXT, -- YYYY-MM-DD أو "OPEN"
            cooldown_until TEXT
        )
    ''')
    
    # جدول التحذيرات والاعتذارات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS staff_stats (
            user_id INTEGER PRIMARY KEY,
            warnings_count INTEGER DEFAULT 0,
            excuses_count INTEGER DEFAULT 0
        )
    ''')
    
    # جدول إعدادات النظام
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # القيم الافتراضية للإعدادات
    cursor.execute('INSERT OR IGNORE INTO system_config VALUES ("max_excuses", "3")')
    cursor.execute('INSERT OR IGNORE INTO system_config VALUES ("cooldown_days", "7")')
    
    conn.commit()
    conn.close()

init_db()

# --- دمج واجهة المناوبة والتفاعل (Shift View & Modal) ---

class ExcuseModal(discord.ui.Modal, title="تقديم طلب اعتذار عن المناوبة"):
    reason = discord.ui.TextInput(
        label="سبب الاعتذار",
        style=discord.TextStyle.paragraph,
        placeholder="اكتب سبب عدم قدرتك على حضور المناوبة هنا...",
        required=True
    )

    def __init__(self, shift_id, message_id):
        super().__init__()
        self.shift_id = shift_id
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        
        conn = sqlite3.connect("shifts_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT excuses_count FROM staff_stats WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        current_excuses = res[0] if res else 0
        
        cursor.execute("SELECT value FROM system_config WHERE key = 'max_excuses'")
        max_excuses = int(cursor.fetchone()[0])
        
        if current_excuses >= max_excuses:
            conn.close()
            await interaction.response.send_message(f"❌ لقد تجاوزت الحد الأقصى للاعتذارات المسموح بها هذا الأسبوع ({max_excuses}).", ephemeral=True)
            return

        # تسجيل الاعتذار وتحديث العداد
        cursor.execute("INSERT OR REPLACE INTO staff_stats (user_id, warnings_count, excuses_count) VALUES (?, COALESCE((SELECT warnings_count FROM staff_stats WHERE user_id = ?), 0), ?)", (user_id, user_id, current_excuses + 1))
        conn.commit()
        conn.close()

        # إرسال طلب الاعتذار لروم الإعتذارات
        excuse_channel = interaction.guild.get_channel(EXCUSE_CHANNEL_ID)
        if excuse_channel:
            embed = discord.Embed(title="📩 طلب اعتذار جديد", color=discord.Color.gold())
            embed.add_field(name="الإداري", value=interaction.user.mention, inline=False)
            embed.add_field(name="السبب", value=self.reason.value, inline=False)
            embed.add_field(name="الحالة", value="جاري مراجعة الطلب من قبل المسؤلين", inline=False)
            
            view = ExcuseApprovalView(user_id=user_id, shift_message_id=self.message_id)
            await excuse_channel.send(embed=embed, view=view)

        # تحديث قائمة الاعتذارات في الرسالة الخاصة بالمناوبة
        await interaction.response.send_message("✅ تم تقديم طلب الاعتذار وهو قيد المراجعة.", ephemeral=True)

class ExcuseApprovalView(discord.ui.View):
    def __init__(self, user_id, shift_message_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.shift_message_id = shift_message_id

    @discord.ui.button(label="قبول", style=discord.ButtonStyle.success, custom_id="accept_excuse")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_status(interaction, "مقبول", discord.Color.green())

    @discord.ui.button(label="رفض", style=discord.ButtonStyle.danger, custom_id="reject_excuse")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_status(interaction, "مرفوض", discord.Color.red())

    async def update_status(self, interaction: discord.Interaction, status: str, color: discord.Color):
        for child in self.children:
            child.disabled = True
            
        embed = interaction.message.embeds[0]
        embed.color = color
        embed.set_field_at(2, name="الحالة", value=f"تم التحديث: **{status}** بواسطة {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)

class ShiftInteractionView(discord.ui.View):
    def __init__(self, assigned_staff_ids, shift_id):
        super().__init__(timeout=None)
        self.assigned_staff_ids = assigned_staff_ids
        self.shift_id = shift_id
        self.confirmed_users = set()
        self.excused_users = {}  # user_id: status

    @discord.ui.button(label="تأكيد الحضور", style=discord.ButtonStyle.success, custom_id="confirm_attendance")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.assigned_staff_ids:
            await interaction.response.send_message("❌ أنت لست من الإداريين المسجلين في هذه المناوبة!", ephemeral=True)
            return
        
        if interaction.user.id in self.excused_users and self.excused_users[interaction.user.id] == "مقبول":
            await interaction.response.send_message("❌ تم قبول اعتذارك بالفعل ولا يمكنك تأكيد الحضور الآن.", ephemeral=True)
            return

        self.confirmed_users.add(interaction.user.id)
        await self.update_embed(interaction)
        await interaction.response.send_message("✅ تم تأكيد حضورك بنجاح!", ephemeral=True)

    @discord.ui.button(label="تقديم اعتذار", style=discord.ButtonStyle.danger, custom_id="submit_excuse")
    async def excuse(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.assigned_staff_ids:
            await interaction.response.send_message("❌ أنت لست من الإداريين المسجلين في هذه المناوبة!", ephemeral=True)
            return
            
        modal = ExcuseModal(shift_id=self.shift_id, message_id=interaction.message.id)
        await interaction.response.send_modal(modal)

    async def update_embed(self, interaction: discord.Interaction):
        embed = interaction.message.embeds[0]
        
        # قائمة الحضور
        confirmed_text = "\n".join([f"<@{uid}>" for uid in self.confirmed_users]) or "لا يوجد حتى الآن"
        
        # تعديل الحقول
        embed.set_field_at(1, name="إداريين أكدوا الحضور", value=confirmed_text, inline=False)
        await interaction.message.edit(embed=embed)

# --- لوحة التحكم الإدارية (Dashboard) ---

class AdminDashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="إدارة البوت", style=discord.ButtonStyle.primary, custom_id="admin_manage_btn")
    async def open_management(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MainControlPanelButtons()
        await interaction.response.send_message("⚙️ **لوحة التحكم الرئيسية للإدارة:**", view=view, ephemeral=True)

class MainControlPanelButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="إضافة / تعديل مناوبة", style=discord.ButtonStyle.secondary, custom_id="btn_shifts")
    async def shifts_manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = sqlite3.connect("shifts_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, time FROM shifts")
        shifts = cursor.fetchall()
        conn.close()

        msg = "**📋 قائمة المناوبات الحالية:**\n"
        for s in shifts:
            msg += f"• **ID:** `{s[0]}` | **الاسم:** {s[1]} | **الوقت:** {s[2]}\n"
        if not shifts:
            msg += "لا توجد مناوبات مضافة حالياً.\n"

        msg += "\nاستخدم الأوامر التالية للتعديل:\n`/add_shift` لإضافة مناوبة جديدة.\n`/delete_shift` لحذف مناوبة."
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="توظيف إداري", style=discord.ButtonStyle.secondary, custom_id="btn_assign")
    async def assign_staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("📌 لإضافة إداري لمناوبة استخدم الأمر: `/assign_staff`\n📌 ولإلغاء التسكين استخدم الأمر: `/unassign_staff`", ephemeral=True)

    @discord.ui.button(label="إرسال في عطلة", style=discord.ButtonStyle.secondary, custom_id="btn_vacation")
    async def manage_vacation(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🌴 لمنح إجازة لإداري استخدم الأمر: `/grant_vacation`\n🌴 لإلغاء إجازة إداري استخدم: `/revoke_vacation`", ephemeral=True)

    @discord.ui.button(label="سجل الإداريين", style=discord.ButtonStyle.secondary, custom_id="btn_logs")
    async def view_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = sqlite3.connect("shifts_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, warnings_count FROM staff_stats WHERE warnings_count > 0")
        warns = cursor.fetchall()
        
        cursor.execute("SELECT user_id, reason, end_date FROM vacations")
        vacs = cursor.fetchall()
        conn.close()

        msg = "**⚠️ الإداريون المسجل عليهم تحذيرات:**\n"
        for w in warns:
            msg += f"• <@{w[0]}>: {w[1]} تحذير(ات)\n"
        if not warns:
            msg += "لا يوجد إداريين عليهم تحذيرات نشطة.\n"

        msg += "\n**🌴 الإداريون الحاليون في إجازة:**\n"
        for v in vacs:
            msg += f"• <@{v[0]}> - السبب: {v[1]} (تنتهي في: {v[2]})\n"
        if not vacs:
            msg += "لا يوجد إداريين في إجازة حالياً.\n"

        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="إدارة", style=discord.ButtonStyle.danger, custom_id="btn_system_config")
    async def system_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⚙️ لإدارة إعدادات النظام (تصفير التحذيرات، تعديل الحدود) استخدم الأوامر الإدارية `/reset_warnings` أو `/set_max_excuses`.", ephemeral=True)

# --- أحداث البوت والأوامر البرمجية (App Commands) ---

@bot.event
async def on_ready():
    print(f"✅ تم تشغيل البوت بنجاح باسم: {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🌐 تم مزامنة {len(synced)} أمر من أوامر Slash Commands.")
    except Exception as e:
        print(f"❌ خطأ أثناء مزامنة الأوامر: {e}")

# أمر إنشاء لوحة التحكم الإدارية
@bot.tree.command(name="setup_dashboard", description="إنشاء لوحة تحكم إدارية للبوت")
@app_commands.checks.has_permissions(administrator=True)
async def setup_dashboard(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎛️ لوحة تحكم إدارة البوت والمناوبات",
        description="اضغط على الزر أدناه للوصول إلى خيارات إدارة المناوبات، الإداريين، والتحذيرات.",
        color=discord.Color.blue()
    )
    view = AdminDashboardView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ تم إرسال لوحة التحكم بنجاح.", ephemeral=True)

# أمر إضافة مناوبة جديدة
@bot.tree.command(name="add_shift", description="إضافة مناوبة جديدة إلى النظام")
@app_commands.describe(name="اسم المناوبة", time_str="وقت المناوبة بتنسيق HH:MM (مثال 18:00)", mode="توقيت التنبيه")
@app_commands.choices(mode=[
    app_commands.Choice(name="قبل الوقت بـ 10 دقائق", value="BEFORE_10"),
    app_commands.Choice(name="في الوقت المحدد ولمدة 10 دقائق", value="AT_TIME_10")
])
async def add_shift(interaction: discord.Interaction, name: str, time_str: str, mode: app_commands.Choice[str]):
    conn = sqlite3.connect("shifts_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO shifts (name, time, mode) VALUES (?, ?, ?)", (name, time_str, mode.value))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ تم إضافة مناوبة **{name}** في وقت `{time_str}` بنجاح.", ephemeral=True)

# أمر تسكين إداري في مناوبة
@bot.tree.command(name="assign_staff", description="تسكين إداري في مناوبة محددة")
async def assign_staff(interaction: discord.Interaction, shift_id: int, member: discord.Member):
    conn = sqlite3.connect("shifts_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO shift_staff VALUES (?, ?)", (shift_id, member.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ تم تسكين {member.mention} في المناوبة رقم `{shift_id}`.", ephemeral=True)

# أمر إعطاء إجازة لإداري
@bot.tree.command(name="grant_vacation", description="إرسال إداري في عطلة")
@app_commands.choices(reason=[
    app_commands.Choice(name="شخصي", value="شخصي"),
    app_commands.Choice(name="مكافأة", value="مكافأة")
])
async def grant_vacation(interaction: discord.Interaction, member: discord.Member, days: int, reason: app_commands.Choice[str]):
    end_date = "OPEN" if days <= 0 else (datetime.date.today() + datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    
    conn = sqlite3.connect("shifts_bot.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO vacations (user_id, reason, end_date) VALUES (?, ?, ?)", (member.id, reason.value, end_date))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🌴 تم إرسال {member.mention} في إجازة لمدة `{days if days > 0 else 'مفتوحة'}` يوم بسبب: {reason.value}.", ephemeral=True)

# أمر يدوي لبدء إرسال رسالة التنبيه الخاصة بالمناوبة (للتجربة والاختبار)
@bot.tree.command(name="trigger_shift", description="تشغيل رسالة مناوبة بشكل يدوي")
async def trigger_shift(interaction: discord.Interaction, shift_id: int):
    conn = sqlite3.connect("shifts_bot.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM shifts WHERE id = ?", (shift_id,))
    shift_res = cursor.fetchone()
    if not shift_res:
        conn.close()
        await interaction.response.send_message("❌ لم يتم العثور على مناوبة بهذا ID.", ephemeral=True)
        return
        
    shift_name = shift_res[0]

    # جلب الإداريين المسجلين
    cursor.execute("SELECT user_id FROM shift_staff WHERE shift_id = ?", (shift_id,))
    staff_rows = cursor.fetchall()
    staff_ids = [row[0] for row in staff_rows]

    # جلب الإداريين المجازين
    cursor.execute("SELECT user_id FROM vacations")
    vacation_ids = {row[0] for row in cursor.fetchall()}
    conn.close()

    assigned_mentions = []
    vacation_mentions = []

    for sid in staff_ids:
        if sid in vacation_ids:
            vacation_mentions.append(f"<@{sid}>")
        else:
            assigned_mentions.append(f"<@{sid}>")

    shift_channel = interaction.guild.get_channel(SHIFT_CHANNEL_ID)
    if not shift_channel:
        await interaction.response.send_message("❌ تعذر الوصول لروم المناوبات 지정.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📢 تم بدء مناوبتكم ({shift_name}) - يرجي تأكيد الحضور",
        color=discord.Color.blue()
    )
    embed.add_field(name="الإداريون المسجلون في هذه المناوبة", value="\n".join(assigned_mentions) if assigned_mentions else "لا يوجد", inline=False)
    embed.add_field(name="إداريين أكدوا الحضور", value="لا يوجد حتى الآن", inline=False)
    embed.add_field(name="إداريين قدموا اعتذار", value="لا يوجد حتى الآن", inline=False)
    embed.add_field(name="مجازين", value="\n".join(vacation_mentions) if vacation_mentions else "لا يوجد إداريين مجازين", inline=False)

    view = ShiftInteractionView(assigned_staff_ids=staff_ids, shift_id=shift_id)
    
    mentions_text = " ".join(assigned_mentions)
    await shift_channel.send(content=f"🔔 تنبيه مناوبة: {mentions_text}", embed=embed, view=view)
    await interaction.response.send_message("✅ تم إرسال رسالة المناوبة بنجاح إلى روم المناوبات.", ephemeral=True)

# تشغيل البوت
bot.run(os.getenv("DISCORD_TOKEN"))
