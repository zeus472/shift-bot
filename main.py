import os
import datetime
import asyncio
import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput, UserSelect, Select

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# تخزين البيانات الموقعة
shifts = {}  # {shift_id: {"name": str, "time": str, "mode": str, "staff": []}}
shift_counter = 1

class AddShiftModal(Modal, title="إضافة مناوبة جديدة"):
    shift_name = TextInput(label="اسم المناوبة", placeholder="مثال: مناوبة الجدعان", required=True)
    shift_time = TextInput(label="الوقت (صيغة 24 ساعة)", placeholder="مثال: 22:10", required=True, max_length=5)
    shift_mode = TextInput(label="النمط / الملاحظات", placeholder="مثال: 10 دقائق", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        global shift_counter
        shift_id = str(shift_counter)
        shifts[shift_id] = {
            "name": self.shift_name.value,
            "time": self.shift_time.value,
            "mode": self.shift_mode.value or "قياسي",
            "staff": []
        }
        shift_counter += 1
        
        embed = discord.Embed(
            title="✅ تم إنشاء المناوبة بنجاح",
            description=f"**الاسم:** {self.shift_name.value}\n**الوقت:** {self.shift_time.value}\n**ID:** `{shift_id}`",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class SelectShiftForStaffView(View):
    def __init__(self):
        super().__init__(timeout=None)
        options = []
        for s_id, data in shifts.items():
            options.append(discord.SelectOption(label=f"{data['name']} ({data['time']})", value=s_id, description=f"ID: {s_id}"))
        
        if options:
            select = Select(placeholder="اختر المناوبة لتسكين الإداري...", options=options, custom_id="select_shift_staff")
            select.callback = self.shift_selected
            self.add_item(select)

    async def shift_selected(self, interaction: discord.Interaction):
        selected_shift_id = interaction.data['values'][0]
        view = AssignUserView(selected_shift_id)
        await interaction.response.send_message(f"اختر الإداري للمناوبة `{shifts[selected_shift_id]['name']}`:", view=view, ephemeral=True)

class AssignUserView(View):
    def __init__(self, shift_id):
        super().__init__(timeout=None)
        self.shift_id = shift_id
        user_select = UserSelect(placeholder="ابحث واختر الإداري...", custom_id="user_select_assign")
        user_select.callback = self.user_selected
        self.add_item(user_select)

    async def user_selected(self, interaction: discord.Interaction):
        user_id = interaction.data['values'][0]
        member = interaction.guild.get_member(int(user_id))
        
        if member:
            if member.id not in shifts[self.shift_id]["staff"]:
                shifts[self.shift_id]["staff"].append(member.id)
                await interaction.response.send_message(f"✅ تم إضافة الإداري {member.mention} للمناوبة `{shifts[self.shift_id]['name']}` بنجاح!", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ الإداري {member.mention} مضاف بالفعل لهذه المناوبة.", ephemeral=True)

class MainControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="إضافة مناوبة", style=discord.ButtonStyle.green, emoji="➕", custom_id="btn_add_shift")
    async def add_shift_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AddShiftModal())

    @discord.ui.button(label="تسكين إداري في مناوبة", style=discord.ButtonStyle.primary, emoji="👤", custom_id="btn_assign_staff")
    async def assign_staff_btn(self, interaction: discord.Interaction, button: Button):
        if not shifts:
            await interaction.response.send_message("❌ لا توجد مناوبات مضافة حالياً. قم بإضافة مناوبة أولاً.", ephemeral=True)
            return
        await interaction.response.send_message("اختر المناوبة المراد تسكين الإداري بها:", view=SelectShiftForStaffView(), ephemeral=True)

    @discord.ui.button(label="عرض المناوبات الحالية", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="btn_list_shifts")
    async def list_shifts_btn(self, interaction: discord.Interaction, button: Button):
        if not shifts:
            await interaction.response.send_message("📋 لا توجد مناوبات حالية.", ephemeral=True)
            return
        
        embed = discord.Embed(title="📋 قائمة المناوبات المسجلة", color=discord.Color.blue())
        for s_id, data in shifts.items():
            staff_mentions = ", ".join([f"<@{uid}>" for uid in data["staff"]]) if data["staff"] else "لا يوجد إداريين مسجلين"
            embed.add_field(
                name=f"🔹 {data['name']} (ID: {s_id})",
                value=f"⏰ **الوقت:** {data['time']}\n👤 **الإداريين:** {staff_mentions}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.command(name="setup")
async def setup_cmd(ctx):
    embed = discord.Embed(
        title="⚙️ لوحة التحكم في إدارة المناوبات",
        description="إدارة كاملة للمناوبات والتسكين عبر الأزرار والقوائم المنسدلة بدون الحاجة لأوامر.",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed, view=MainControlView())

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

bot.run(os.getenv("DISCORD_TOKEN"))
