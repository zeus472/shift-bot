import os
import datetime
import asyncio
import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput, UserSelect, Select

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- آيديهات الرومات والرولات المحددة ---
SHIFT_CHANNEL_ID   = 1542004969983574066  # روم المناوبات
EXCUSE_CHANNEL_ID  = 1544032822098927877  # روم طلبات الاعتذار
WARNING_CHANNEL_ID = 1543078048864403577  # روم التحذيرات
COMMUNITY_MGR_ROLE = 1541599646810374234  # رول الكوميونتي مانجر

# --- قواعد البيانات الإدارية ---
shifts = {}         # {shift_id: {"name": str, "time": str, "mode": str, "staff": [], "confirmed": [], "excuses": {}, "active_msg_id": int}}
staff_warnings = {} # {user_id: int} (عدد التحذيرات)
staff_vacations = {}# {user_id: {"reason": str, "days": int, "end_date": str}}
staff_excuse_count = {} # {user_id: int} (عدد الاعتذارات الأسبوعية)
max_weekly_excuses = 3
shift_counter = 1

# ==================== Modals (النماذج) ====================

class AddShiftModal(Modal, title="إضافة / تعديل مناوبة"):
    shift_name = TextInput(label="اسم المناوبة", placeholder="مثال: مناوبة 10 / مناوبة الجدعان", required=True)
    shift_time = TextInput(label="وقت المناوبة (HH:MM صيغة 24 ساعة)", placeholder="مثال: 18:00", required=True, max_length=5)
    confirm_mode = TextInput(label="نظام تأكيد الحضور", placeholder="اكتب 'قبل 10 دقائق' أو 'في الموعد لمدة 10 دقائق'", required=True, default="قبل 10 دقائق")

    async def on_submit(self, interaction: discord.Interaction):
        global shift_counter
        s_id = str(shift_counter)
        shifts[s_id] = {
            "name": self.shift_name.value,
            "time": self.shift_time.value,
            "mode": self.confirm_mode.value,
            "staff": [],
            "confirmed": [],
            "excuses": {},
            "active_msg_id": None
        }
        shift_counter += 1
        await interaction.response.send_message(f"✅ تم إضافة مناوبة **{self.shift_name.value}** (ID: `{s_id}`) بنجاح!", ephemeral=True)

class ExcuseReasonModal(Modal, title="تقديم طلب اعتذار عن المناوبة"):
    reason = TextInput(label="سبب الاعتذار", style=discord.TextStyle.paragraph, placeholder="اكتب سبب اعتذارك هنا...", required=True)

    def __init__(self, shift_id):
        super().__init__()
        self.shift_id = shift_id

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        current_excuses = staff_excuse_count.get(user_id, 0)

        if current_excuses >= max_weekly_excuses:
            await interaction.response.send_message(f"❌ لقد تجاوزت الحد الأقصى للاعتذارات المسموحة هذا الأسبوع ({max_weekly_excuses}).", ephemeral=True)
            return

        shift = shifts[self.shift_id]
        shift["excuses"][user_id] = {"status": "جاري مراجعة الطلب من قبل المسؤلين", "reason": self.reason.value}
        staff_excuse_count[user_id] = current_excuses + 1

        # إرسال طلب الاعتذار في روم الاعتذارات
        excuse_channel = bot.get_channel(EXCUSE_CHANNEL_ID)
        if excuse_channel:
            embed = discord.Embed(title="📩 طلب اعتذار جديد", color=discord.Color.gold())
            embed.add_field(name="👤 الإداري:", value=interaction.user.mention, inline=True)
            embed.add_field(name="🔹 المناوبة:", value=shift["name"], inline=True)
            embed.add_field(name="📝 السبب:", value=self.reason.value, inline=False)
            
            view = ExcuseReviewView(self.shift_id, user_id)
            await excuse_channel.send(embed=embed, view=view)

        # تحديث الرسالة الرئيسية للمناوبة
        await update_shift_embed(self.shift_id)
        await interaction.response.send_message("✅ تم تقديم طلب الاعتذار بنجاح، وهو قيد المراجعة الآن.", ephemeral=True)

class VacationModal(Modal, title="إرسال إداري في عطلة"):
    user_id_input = TextInput(label="آيدي الإداري", placeholder="أدخل ID الإداري هنا", required=True)
    days_input = TextInput(label="مدة الإجازة (1-7 أيام أو 0 لمفتوحة)", placeholder="مثال: 3", required=True, default="3")
    reason_type = TextInput(label="سبب العطلة (شخصي / مكافأة)", placeholder="شخصي أو مكافأة", required=True, default="شخصي")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_id = int(self.user_id_input.value)
            days = int(self.days_input.value)
            days_str = "مفتوحة" if days == 0 else f"{days} أيام"
            
            staff_vacations[target_id] = {
                "reason": self.reason_type.value,
                "days": days_str,
                "start": datetime.date.today().strftime("%Y-%m-%d")
            }
            await interaction.response.send_message(f"🏖️ تم منح الإداري <@{target_id}> إجازة لمدة ({days_str}) بنجاح!", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ يرجي كتابة آيدي الصحيح بالأرقام.", ephemeral=True)

# ==================== Views (الأزرار والواجهات) ====================

class ShiftNotificationView(View):
    def __init__(self, shift_id):
        super().__init__(timeout=None)
        self.shift_id = shift_id

    @discord.ui.button(label="تأكيد الحضور", style=discord.ButtonStyle.green, emoji="✅", custom_id="btn_confirm")
    async def confirm_attendance(self, interaction: discord.Interaction, button: Button):
        shift = shifts.get(self.shift_id)
        if not shift:
            return
        
        if interaction.user.id not in shift["staff"]:
            await interaction.response.send_message("❌ أنت لست مسجلاً في هذه المناوبة.", ephemeral=True)
            return

        if interaction.user.id not in shift["confirmed"]:
            shift["confirmed"].append(interaction.user.id)
            if interaction.user.id in shift["excuses"]:
                del shift["excuses"][interaction.user.id]
            await update_shift_embed(self.shift_id)
            await interaction.response.send_message("✅ تم تأكيد حضورك في المناوبة بنجاح!", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ لقد أكدت حضورك بالفعل.", ephemeral=True)

    @discord.ui.button(label="تقديم اعتذار", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="btn_excuse")
    async def request_excuse(self, interaction: discord.Interaction, button: Button):
        shift = shifts.get(self.shift_id)
        if not shift:
            return
            
        if interaction.user.id not in shift["staff"]:
            await interaction.response.send_message("❌ أنت لست مسجلاً في هذه المناوبة.", ephemeral=True)
            return

        await interaction.response.send_modal(ExcuseReasonModal(self.shift_id))

class ExcuseReviewView(View):
    def __init__(self, shift_id, staff_id):
        super().__init__(timeout=None)
        self.shift_id = shift_id
        self.staff_id = staff_id

    @discord.ui.button(label="قبول الطلب", style=discord.ButtonStyle.green, emoji="✅")
    async def accept_excuse(self, interaction: discord.Interaction, button: Button):
        shift = shifts.get(self.shift_id)
        if shift and self.staff_id in shift["excuses"]:
            shift["excuses"][self.staff_id]["status"] = "مقبول"
            await update_shift_embed(self.shift_id)
            await interaction.response.send_message("✅ تم قبول طلب الاعتذار.", ephemeral=True)
            self.stop()

    @discord.ui.button(label="رفض الطلب", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject_excuse(self, interaction: discord.Interaction, button: Button):
        shift = shifts.get(self.shift_id)
        if shift and self.staff_id in shift["excuses"]:
            shift["excuses"][self.staff_id]["status"] = "مرفوض"
            await update_shift_embed(self.shift_id)
            await interaction.response.send_message("❌ تم رفض طلب الاعتذار ويمكن للإداري إعادة تأكيد الحضور.", ephemeral=True)
            self.stop()

# ==================== لوحة التحكم الـ 5 أزرار ====================

class AdminBotControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    # 1. إضافة / تعديل مناوبة
    @discord.ui.button(label="إضافة / تعديل مناوبة", style=discord.ButtonStyle.primary, emoji="⚙️", row=0)
    async def btn_manage_shifts(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddShiftModal())

    # 2. توظيف إداري
    @discord.ui.button(label="توظيف إداري", style=discord.ButtonStyle.success, emoji="📝", row=0)
    async def btn_assign_staff(self, interaction: discord.Interaction, button: Button):
        if not shifts:
            await interaction.response.send_message("❌ لا توجد مناوبات مضافة حالياً.", ephemeral=True)
            return
        await interaction.response.send_message("اختر المناوبة لتسكين الإداري بها:", view=SelectShiftStaffView(), ephemeral=True)

    # 3. إرسال في عطلة
    @discord.ui.button(label="إرسال في عطلة", style=discord.ButtonStyle.secondary, emoji="🏖️", row=0)
    async def btn_vacation(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(VacationModal())

    # 4. سجل الإداريين
    @discord.ui.button(label="سجل الإداريين", style=discord.ButtonStyle.secondary, emoji="📁", row=1)
    async def btn_staff_log(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(title="📁 سجل التحذيرات والإجازات للإداريين", color=discord.Color.blue())
        
        warn_text = "\n".join([f"<@{uid}>: {count} تحذيرات" for uid, count in staff_warnings.items()]) or "لا يوجد تحذيرات نشطة"
        vac_text = "\n".join([f"<@{uid}>: إجازة {data['days']} (السبب: {data['reason']})" for uid, data in staff_vacations.items()]) or "لا يوجد إداريين في عطلة"

        embed.add_field(name="⚠️ سجل التحذيرات:", value=warn_text, inline=False)
        embed.add_field(name="🏖️ الإداريين المجازين:", value=vac_text, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 5. إدارة
    @discord.ui.button(label="إدارة", style=discord.ButtonStyle.danger, emoji="⚙️", row=1)
    async def btn_settings(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(title="⚙️ إعدادات النظام والإعتذارات", description=f"الحد الأقصى للاعتذارات الأسبوعية: **{max_weekly_excuses}**", color=discord.Color.dark_red())
        await interaction.response.send_message(embed=embed, ephemeral=True)

class SelectShiftStaffView(View):
    def __init__(self):
        super().__init__(timeout=None)
        options = [discord.SelectOption(label=f"{d['name']} ({d['time']})", value=s_id) for s_id, d in shifts.items()]
        if options:
            select = Select(placeholder="اختر المناوبة...", options=options)
            select.callback = self.on_select
            self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        s_id = interaction.data['values'][0]
        await interaction.response.send_message(f"ابحث واختر الإداري لتسكينه في مناوبة `{shifts[s_id]['name']}`:", view=UserAssignSelectView(s_id), ephemeral=True)

class UserAssignSelectView(View):
    def __init__(self, shift_id):
        super().__init__(timeout=None)
        self.shift_id = shift_id
        u_select = UserSelect(placeholder="اختر الإداري من القائمة...")
        u_select.callback = self.user_selected
        self.add_item(u_select)

    async def user_selected(self, interaction: discord.Interaction):
        uid = int(interaction.data['values'][0])
        shift = shifts[self.shift_id]
        if uid not in shift["staff"]:
            shift["staff"].append(uid)
            await interaction.response.send_message(f"✅ تم تسكين <@{uid}> في مناوبة `{shift['name']}` بنجاح!", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ الإداري مضاف بالفعل في هذه المناوبة.", ephemeral=True)

# ==================== المساعدات والدوال التلقائية ====================

async def update_shift_embed(shift_id):
    shift = shifts.get(shift_id)
    if not shift or not shift.get("active_msg_id"):
        return

    channel = bot.get_channel(SHIFT_CHANNEL_ID)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(shift["active_msg_id"])
    except:
        return

    staff_mentions = "\n".join([f"• <@{uid}>" for uid in shift["staff"]]) or "لا يوجد"
    confirmed_mentions = "\n".join([f"• <@{uid}>" for uid in shift["confirmed"]]) or "لا يوجد"
    
    excuses_list = []
    for uid, ex_data in shift["excuses"].items():
        excuses_list.append(f"• <@{uid}> - [{ex_data['status']}]")
    excuses_mentions = "\n".join(excuses_list) or "لا يوجد"

    vacation_mentions = "\n".join([f"• <@{uid}>" for uid in shift["staff"] if uid in staff_vacations]) or "لا يوجد"

    embed = discord.Embed(
        title=f"⏰ تم بدء مناوبتكم يرجى تأكيد الحضور - ({shift['name']})",
        color=discord.Color.blue()
    )
    embed.add_field(name="📋 الإداريون المسجلون في هذه المناوبة:", value=staff_mentions, inline=False)
    embed.add_field(name="✅ إداريين أكدوا الحضور:", value=confirmed_mentions, inline=False)
    embed.add_field(name="✉️ إداريين قدموا اعتذار:", value=excuses_mentions, inline=False)
    embed.add_field(name="🏖️ مجازين:", value=vacation_mentions, inline=False)

    await msg.edit(embed=embed, view=ShiftNotificationView(shift_id))

@tasks.loop(seconds=20)
async def shift_scheduler():
    now_str = datetime.datetime.now().strftime("%H:%M")
    channel = bot.get_channel(SHIFT_CHANNEL_ID)
    if not channel:
        return

    for s_id, data in list(shifts.items()):
        if data["time"] == now_str and not data.get("active_msg_id"):
            mentions = " ".join([f"<@{uid}>" for uid in data["staff"]])
            
            embed = discord.Embed(
                title=f"⏰ تم بدء مناوبتكم يرجى تأكيد الحضور - ({data['name']})",
                color=discord.Color.blue()
            )
            embed.add_field(name="📋 الإداريون المسجلون في هذه المناوبة:", value="\n".join([f"• <@{uid}>" for uid in data["staff"]]) or "لا يوجد", inline=False)
            embed.add_field(name="✅ إداريين أكدوا الحضور:", value="لا يوجد", inline=False)
            embed.add_field(name="✉️ إداريين قدموا اعتذار:", value="لا يوجد", inline=False)
            embed.add_field(name="🏖️ مجازين:", value="\n".join([f"• <@{uid}>" for uid in data["staff"] if uid in staff_vacations]) or "لا يوجد", inline=False)

            msg = await channel.send(content=f"🔔 {mentions}", embed=embed, view=ShiftNotificationView(s_id))
            data["active_msg_id"] = msg.id

            # انتظار 10 دقائق لتوقيع التحذيرات
            await asyncio.sleep(600)
            await process_shift_warnings(s_id)

async def process_shift_warnings(shift_id):
    shift = shifts.get(shift_id)
    if not shift:
        return
    
    warning_channel = bot.get_channel(WARNING_CHANNEL_ID)

    for uid in shift["staff"]:
        # لو الإداري مجاز -> مفيش تحذير
        if uid in staff_vacations:
            continue
        
        # لو أكد الحضور -> مفيش تحذير
        if uid in shift["confirmed"]:
            continue

        # فحص الاعتذارات
        ex = shift["excuses"].get(uid)
        if ex:
            if ex["status"] == "مقبول" or ex["status"] == "جاري مراجعة الطلب من قبل المسؤلين":
                continue # مفيش تحذير

        # ينزل عليه تحذير تلقائي
        staff_warnings[uid] = staff_warnings.get(uid, 0) + 1
        count = staff_warnings[uid]

        if warning_channel:
            if count >= 3:
                await warning_channel.send(content=f"⚠️ <@&{COMMUNITY_MGR_ROLE}> تنبيه عاجل! الإداري <@{uid}> وصل للتحذير رقم **3** بسبب التغيب عن مناوبة `{shift['name']}`.")
            else:
                await warning_channel.send(content=f"⚠️ الإداري <@{uid}> حصل على تحذير ({count}/3) بسبب التغيب عن مناوبة `{shift['name']}`.")

class MainSetupView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="إدارة البوت", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def open_control_panel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("⚙️ **لوحة التحكم الرئيسية لإدارة البوت والمناوبات:**", view=AdminBotControlView(), ephemeral=True)

@bot.command(name="setup")
async def setup_command(ctx):
    embed = discord.Embed(
        title="🤖 لوحة تحكم إدارة المناوبات",
        description="اضغط على زر **إدارة البوت** للتحكم في كافة المناوبات والوظائف والإجازات بالأزرار.",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed, view=MainSetupView())

@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}")
    if not shift_scheduler.is_running():
        shift_scheduler.start()

bot.run(os.getenv("DISCORD_TOKEN"))
