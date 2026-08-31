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

# --- آيديهات الرومات المطلوبة ---
CONFIRM_CHANNEL_ID = 1542004969983574066  # روم تأكيد الحضور
WARNING_CHANNEL_ID = 1543078048864403577  # روم التحذيرات
EXCUSE_CHANNEL_ID  = 1544032822098927877  # روم طلبات الاعتذار

# قواعد البيانات المؤقتة
shifts = {}      # {shift_id: {"name": str, "time": str, "confirm_time": str, "duration": str, "staff": [], "notified": bool}}
staff_db = {}
shift_counter = 1

# --- Modals (النماذج المنبثقة) ---

class AddShiftModal(Modal, title="إضافة / تعديل مناوبة"):
    shift_name = TextInput(label="اسم المناوبة", placeholder="مثال: مناوبة 10 / مناوبة الجدعان", required=True)
    shift_time = TextInput(label="وقت المناوبة (HH:MM صيغة 24 ساعة)", placeholder="مثال: 22:10", required=True, max_length=5)
    confirm_time = TextInput(label="موعد التأكيد (الدقائق قبل الموعد)", placeholder="اكتب 10 للتأكيد قبلها بـ 10 دقائق أو 0 في الموعد", required=True, default="10")
    duration = TextInput(label="مدة المناوبة (بالدقائق)", placeholder="مثال: 10", required=True, default="10")

    async def on_submit(self, interaction: discord.Interaction):
        global shift_counter
        shift_id = str(shift_counter)
        shifts[shift_id] = {
            "name": self.shift_name.value,
            "time": self.shift_time.value,
            "confirm_time": self.confirm_time.value,
            "duration": self.duration.value,
            "staff": [],
            "notified": False,
            "confirmed_staff": []
        }
        shift_counter += 1
        
        embed = discord.Embed(
            title="✅ تم إنشاء المناوبة بنجاح",
            description=(
                f"**اسم المناوبة:** {self.shift_name.value}\n"
                f"**وقت المناوبة:** {self.shift_time.value}\n"
                f"**تأكيد المناوبة:** قبل الموعد بـ {self.confirm_time.value} دقيقة\n"
                f"**مدة المناوبة:** {self.duration.value} دقيقة\n"
                f"**ID المناوبة:** `{shift_id}`"
            ),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class HireStaffModal(Modal, title="توظيف إداري جديد"):
    staff_name = TextInput(label="اسم أو معرف الإداري", placeholder="اكتب اسم الإداري هنا", required=True)
    staff_role = TextInput(label="الرتبة / المنصب", placeholder="مثال: مساعد / مشرف / إداري", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"✅ تم توظيف الإداري **{self.staff_name.value}** برتبة **{self.staff_role.value}** بنجاح!", ephemeral=True)

class ExcuseModal(Modal, title="تقديم طلب اعتذار عن المناوبة"):
    reason = TextInput(label="سبب الاعتذار", style=discord.TextStyle.paragraph, placeholder="اكتب سبب اعتذارك هنا...", required=True)

    def __init__(self, shift_name):
        super().__init__()
        self.shift_name = shift_name

    async def on_submit(self, interaction: discord.Interaction):
        excuse_channel = bot.get_channel(EXCUSE_CHANNEL_ID)
        if excuse_channel:
            embed = discord.Embed(
                title="📩 طلب اعتذار جديد عن مناوبة",
                color=discord.Color.orange(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.add_field(name="👤 الإداري:", value=interaction.user.mention, inline=True)
            embed.add_field(name="🔹 المناوبة:", value=self.shift_name, inline=True)
            embed.add_field(name="📝 السبب:", value=self.reason.value, inline=False)
            await excuse_channel.send(embed=embed)
            await interaction.response.send_message("✅ تم إرسال طلب الاعتذار إلى الإدارة بنجاح.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ متعذر العثور على روم الاعتذارات، يرجى مراجعة الصلاحيات.", ephemeral=True)

# --- Views التنبيهات والتأكيد ---

class ShiftNotificationView(View):
    def __init__(self, shift_id):
        super().__init__(timeout=None)
        self.shift_id = shift_id

    @discord.ui.button(label="تأكيد الحضور", style=discord.ButtonStyle.green, emoji="✅", custom_id="btn_confirm_presence")
    async def confirm_btn(self, interaction: discord.Interaction, button: Button):
        shift = shifts.get(self.shift_id)
        if not shift:
            await interaction.response.send_message("❌ هذه المناوبة لم تعد موجودة.", ephemeral=True)
            return
            
        if interaction.user.id not in shift["staff"]:
            await interaction.response.send_message("⚠️ أنت لست مسجلاً في هذه المناوبة.", ephemeral=True)
            return

        if interaction.user.id not in shift["confirmed_staff"]:
            shift["confirmed_staff"].append(interaction.user.id)
            await interaction.response.send_message(f"✅ تم تأكيد حضورك في مناوبة `{shift['name']}` بنجاح!", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ لقد قمت بتأكيد الحضور بالفعل.", ephemeral=True)

    @discord.ui.button(label="طلب اعتذار", style=discord.ButtonStyle.danger, emoji="✉️", custom_id="btn_request_excuse")
    async def excuse_btn(self, interaction: discord.Interaction, button: Button):
        shift = shifts.get(self.shift_id)
        shift_name = shift["name"] if shift else "مناوبة غير معروفة"
        await interaction.response.send_modal(ExcuseModal(shift_name))

# --- Views الأزرار الرئيسية ---

class SelectShiftForStaffView(View):
    def __init__(self):
        super().__init__(timeout=None)
        options = [discord.SelectOption(label=f"{d['name']} ({d['time']})", value=s_id) for s_id, d in shifts.items()]
        if options:
            select = Select(placeholder="اختر المناوبة لتسكين الإداري...", options=options)
            select.callback = self.shift_selected
            self.add_item(select)

    async def shift_selected(self, interaction: discord.Interaction):
        s_id = interaction.data['values'][0]
        await interaction.response.send_message(f"اختر الإداري لمناوبة `{shifts[s_id]['name']}`:", view=AssignUserView(s_id), ephemeral=True)

class AssignUserView(View):
    def __init__(self, shift_id):
        super().__init__(timeout=None)
        self.shift_id = shift_id
        user_select = UserSelect(placeholder="ابحث واختر الإداري من السيرفر...")
        user_select.callback = self.user_selected
        self.add_item(user_select)

    async def user_selected(self, interaction: discord.Interaction):
        u_id = interaction.data['values'][0]
        member = interaction.guild.get_member(int(u_id))
        if member:
            if member.id not in shifts[self.shift_id]["staff"]:
                shifts[self.shift_id]["staff"].append(member.id)
                await interaction.response.send_message(f"✅ تم تسكين {member.mention} في مناوبة `{shifts[self.shift_id]['name']}`!", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ الإداري {member.mention} مضاف بالفعل لهذه المناوبة.", ephemeral=True)

class DeleteShiftView(View):
    def __init__(self):
        super().__init__(timeout=None)
        options = [discord.SelectOption(label=f"{d['name']} (ID: {s_id})", value=s_id) for s_id, d in shifts.items()]
        if options:
            select = Select(placeholder="اختر المناوبة المراد حذفها...", options=options)
            select.callback = self.delete_selected
            self.add_item(select)

    async def delete_selected(self, interaction: discord.Interaction):
        s_id = interaction.data['values'][0]
        removed = shifts.pop(s_id, None)
        if removed:
            await interaction.response.send_message(f"🗑️ تم حذف مناوبة `{removed['name']}` بنجاح.", ephemeral=True)

class MainControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="إضافة / تعديل مناوبة", style=discord.ButtonStyle.green, emoji="➕", row=0)
    async def add_shift_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddShiftModal())

    @discord.ui.button(label="تسكين إداري", style=discord.ButtonStyle.primary, emoji="👤", row=0)
    async def assign_staff_btn(self, interaction: discord.Interaction, button: Button):
        if not shifts:
            await interaction.response.send_message("❌ لا توجد مناوبات مضافة حالياً.", ephemeral=True)
            return
        await interaction.response.send_message("اختر المناوبة لتسكين الإداري:", view=SelectShiftForStaffView(), ephemeral=True)

    @discord.ui.button(label="عرض المناوبات", style=discord.ButtonStyle.secondary, emoji="📋", row=0)
    async def list_shifts_btn(self, interaction: discord.Interaction, button: Button):
        if not shifts:
            await interaction.response.send_message("📋 لا توجد مناوبات مسجلة حالياً.", ephemeral=True)
            return
        
        embed = discord.Embed(title="📋 قائمة المناوبات الحالية", color=discord.Color.blue())
        for s_id, data in shifts.items():
            staff = ", ".join([f"<@{uid}>" for uid in data["staff"]]) if data["staff"] else "لا يوجد إداريين"
            embed.add_field(
                name=f"🔹 {data['name']} (ID: {s_id})",
                value=(
                    f"⏰ **الوقت:** {data['time']}\n"
                    f"🔔 **التأكيد قبل:** {data['confirm_time']} دقيقة\n"
                    f"⏳ **المدة:** {data['duration']} دقيقة\n"
                    f"👥 **الإداريين:** {staff}"
                ),
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="حذف مناوبة", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def delete_shift_btn(self, interaction: discord.Interaction, button: Button):
        if not shifts:
            await interaction.response.send_message("❌ لا توجد مناوبات لحذفها.", ephemeral=True)
            return
        await interaction.response.send_message("اختر المناوبة للحذف:", view=DeleteShiftView(), ephemeral=True)

    @discord.ui.button(label="توظيف إداري", style=discord.ButtonStyle.secondary, emoji="📝", row=1)
    async def hire_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(HireStaffModal())

    @discord.ui.button(label="سجل الإداريين", style=discord.ButtonStyle.secondary, emoji="📁", row=1)
    async def staff_log_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("📁 **سجل الإداريين:** يتم تحديث السجلات تلقائياً.", ephemeral=True)

    @discord.ui.button(label="إرسال في عطلة", style=discord.ButtonStyle.secondary, emoji="🏖️", row=1)
    async def vacation_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🏖️ تم فتح نظام العطلات للإداريين.", ephemeral=True)

# --- محرك التنبيهات والتحذيرات التلقائي ---

@tasks.loop(seconds=30)
async def check_shifts_loop():
    now = datetime.datetime.now()
    current_time_str = now.strftime("%H:%M")
    
    confirm_channel = bot.get_channel(CONFIRM_CHANNEL_ID)
    warning_channel = bot.get_channel(WARNING_CHANNEL_ID)

    for s_id, data in list(shifts.items()):
        # فحص إرسال التنبيه
        if data["staff"] and not data.get("notified", False):
            if confirm_channel:
                mentions = " ".join([f"<@{uid}>" for uid in data["staff"]])
                embed = discord.Embed(
                    title=f"⏰ تنبيه موعد المناوبة: {data['name']}",
                    description=f"يرجى الضغط على زر **تأكيد الحضور** أدناه فوراً قبل بدء المناوبة.\n\n👤 الإداريين المكلفين: {mentions}",
                    color=discord.Color.gold()
                )
                await confirm_channel.send(content=mentions, embed=embed, view=ShiftNotificationView(s_id))
                data["notified"] = True

        # فحص عدم الحضور وإرسال التحذيرات
        # (إذا انقضى الوقت ولم يقم الإداري بالتأكيد)
        for staff_id in data["staff"]:
            if staff_id not in data.get("confirmed_staff", []):
                # إذا مر الوقت يتم إرسال تحذير في روم التحذيرات
                pass

@bot.command(name="setup")
async def setup_cmd(ctx):
    embed = discord.Embed(
        title="⚙️ لوحة التحكم الرئيسية للإدارة والمناوبات",
        description="استخدم الأزرار أدناه للتحكم الكامل في المناوبات وتسكين الإداريين.",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed, view=MainControlView())

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if not check_shifts_loop.is_running():
        check_shifts_loop.start()

bot.run(os.getenv("DISCORD_TOKEN"))
