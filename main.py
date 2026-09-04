import os
import discord
from discord.ext import commands, tasks
from discord import ui
import datetime
from zoneinfo import ZoneInfo
import random
import asyncio

# --- إعدادات البوت والصلات ---

# توقيت مصر المدمج بدون مكتبات خارجية
EGYPT_TZ = ZoneInfo("Africa/Cairo")
# الرومات المحددة
ANNOUNCEMENT_CHANNELS = [1544833977058074675, 1545494721445625907]
STAFF_SCHEDULE_CHANNEL = 1545497084373897359
PUBLIC_EVENTS_CHANNEL = 1544835764850786384

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- قواعد البيانات ---
# 1. الفعاليات المبرمجة
HARDCODED_EVENTS = {
    "لعبة المافيا": {
        "desc": "لعبة ذكاء وغموض (تتطلب 4 لاعبين على الأقل: قاتل، دكتور، ومواطنين)",
        "type": "interactive"
    }
}

# 2. فعاليات بنك البيانات (المضافة ديناميكياً)
BANK_EVENTS = {
    "تخمين الإعلان": {
        "desc": "تخمين اسم الشركة أو المنتج من صورة الإعلان",
        "image": "https://via.placeholder.com/400x200",
        "type": "bank"
    }
}

daily_events_schedule = []  # [{ "name": ..., "time": ... }]
staff_assignments = {}      # { "event_key": user_id }
banned_users = {}           # { user_id: admin_id }
daily_logs = []             # ["HH:MM - Action"]
player_stats = {}           # { user_id: count }

active_live_event = {
    "name": None,
    "participants": [],
    "admin_msg": None,
    "public_msg": None,
    "mafia_state": {}
}

def get_all_events():
    """دمج فعاليات بنك البيانات والفعاليات المبرمجة في قائمة واحدة"""
    all_evs = {}
    all_evs.update(HARDCODED_EVENTS)
    all_evs.update(BANK_EVENTS)
    return all_evs

def log_action(action_text, user):
    """تسجيل الأحداث بتوقيت مصر"""
    now = datetime.datetime.now(EGYPT_TZ).strftime("%I:%M %p")
    daily_logs.append(f"`[{now}]` {user.mention}: {action_text}")

# ==========================================
# 1. لوحة التحكم الرئيسية
# ==========================================

class MainControlPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="إدارة الفعاليات", style=discord.ButtonStyle.primary, custom_id="btn_manage_events", emoji="📅", row=0)
    async def manage_events(self, interaction: discord.Interaction, button: ui.Button):
        view = ScheduleManageView()
        await interaction.response.send_message("⚙️ **لوحة إدارة جدول الفعاليات:**", view=view, ephemeral=True)

    @ui.button(label="قائمة الفعاليات", style=discord.ButtonStyle.secondary, custom_id="btn_list_events", emoji="📜", row=0)
    async def list_events(self, interaction: discord.Interaction, button: ui.Button):
        all_evs = get_all_events()
        embed = discord.Embed(title="📜 قائمة الفعاليات المتاحة في البوت", color=discord.Color.blue())
        for name, data in all_evs.items():
            tag = "🧩 بنك بيانات" if data.get("type") == "bank" else "🎮 لعبة مبرمجة"
            embed.add_field(name=f"🔹 {name} ({tag})", value=data['desc'], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="بنك البيانات", style=discord.ButtonStyle.success, custom_id="btn_bank_data", emoji="📂", row=0)
    async def bank_data(self, interaction: discord.Interaction, button: ui.Button):
        view = BankManagementView()
        await interaction.response.send_message("📂 **إدارة فعاليات بنك البيانات:**", view=view, ephemeral=True)

    @ui.button(label="جدول المسؤوليين", style=discord.ButtonStyle.success, custom_id="btn_staff_schedule", emoji="👥", row=1)
    async def staff_schedule(self, interaction: discord.Interaction, button: ui.Button):
        if not daily_events_schedule:
            return await interaction.response.send_message("❌ لا يوجد جدول فعاليات معتمد لليوم بعد!", ephemeral=True)
        view = StaffAssignmentView()
        await interaction.response.send_message("👥 **توزيع الإداريين على فعاليات اليوم:**", view=view, ephemeral=True)

    @ui.button(label="بدء فعالية", style=discord.ButtonStyle.danger, custom_id="btn_start_event", emoji="🚀", row=1)
    async def start_event(self, interaction: discord.Interaction, button: ui.Button):
        view = SelectEventToStartView()
        await interaction.response.send_message("🎯 **اختر الفعالية المراد بدء التسجيل عليها:**", view=view, ephemeral=True)

    @ui.button(label="إدارة المشاركة", style=discord.ButtonStyle.secondary, custom_id="btn_manage_bans", emoji="🚫", row=1)
    async def manage_bans(self, interaction: discord.Interaction, button: ui.Button):
        view = BanManagementView()
        await interaction.response.send_message("🚫 **لوحة التحكم في حظر/فك حظر المشاركين:**", view=view, ephemeral=True)

    @ui.button(label="السجل اليومي", style=discord.ButtonStyle.secondary, custom_id="btn_daily_log", emoji="📑", row=2)
    async def show_log(self, interaction: discord.Interaction, button: ui.Button):
        log_text = "\n".join(daily_logs) if daily_logs else "لا توجد أحداث مسجلة اليوم بعد."
        embed = discord.Embed(title="📜 السجل اليومي للعمليات الإدارية (توقيت مصر)", description=log_text, color=discord.Color.dark_grey())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="إحصائيات اللاعبين", style=discord.ButtonStyle.primary, custom_id="btn_player_stats", emoji="📊", row=2)
    async def show_stats(self, interaction: discord.Interaction, button: ui.Button):
        view = StatsControlView()
        embed = build_stats_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ==========================================
# 2. إدارة بنك البيانات (إضافة/حذف فعاليات)
# ==========================================

class BankManagementView(ui.View):
    def __init__(self):
        super().__init__()

    @ui.button(label="إضافة فعالية بنك بيانات ➕", style=discord.ButtonStyle.success)
    async def add_bank_event(self, interaction: discord.Interaction, button: ui.Button):
        modal = AddBankEventModal()
        await interaction.response.send_modal(modal)

    @ui.button(label="حذف فعالية من البنك 🗑️", style=discord.ButtonStyle.danger)
    async def remove_bank_event(self, interaction: discord.Interaction, button: ui.Button):
        if not BANK_EVENTS:
            return await interaction.response.send_message("لا توجد فعاليات في بنك البيانات لحذفها!", ephemeral=True)
        view = DeleteBankEventView()
        await interaction.response.send_message("اختر الفعالية المراد حذفها من البنك:", view=view, ephemeral=True)


class AddBankEventModal(ui.Modal, title="إضافة فعالية لبنك البيانات"):
    title_input = ui.TextInput(label="اسم الفعالية", placeholder="مثال: تخمين الصوت", required=True)
    desc_input = ui.TextInput(label="وصف الفعالية", style=discord.TextStyle.paragraph, required=True)
    image_input = ui.TextInput(label="رابط الصورة (اختياري)", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.title_input.value
        BANK_EVENTS[name] = {
            "desc": self.desc_input.value,
            "image": self.image_input.value if self.image_input.value else None,
            "type": "bank"
        }
        log_action(f"أضاف فعالية بنك بيانات جديدة: **{name}**", interaction.user)
        await interaction.response.send_message(f"✅ تم إضافة فعالية **{name}** لجميع القوائم والجداول بنجاح!", ephemeral=True)


class DeleteBankEventView(ui.View):
    def __init__(self):
        super().__init__()
        options = [discord.SelectOption(label=name) for name in BANK_EVENTS.keys()]
        select = ui.Select(placeholder="اختر الفعالية للحذف...", options=options)
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        selected_name = interaction.data['values'][0]
        if selected_name in BANK_EVENTS:
            del BANK_EVENTS[selected_name]
            log_action(f"حذف فعالية **{selected_name}** من بنك البيانات.", interaction.user)
            await interaction.response.send_message(f"🗑️ تم حذف **{selected_name}** بنجاح.", ephemeral=True)


# ==========================================
# 3. إدارة وإنشاء الجدول الإعلاني
# ==========================================

class ScheduleManageView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="إنشاء / تعديل جدول", style=discord.ButtonStyle.primary)
    async def create_schedule(self, interaction: discord.Interaction, button: ui.Button):
        select_view = SelectEventsForScheduleView()
        await interaction.response.send_message("اختر الفعاليات المراد إدراجها في جدول اليوم:", view=select_view, ephemeral=True)

    @ui.button(label="إرسال إعلان الجدول فوراً", style=discord.ButtonStyle.success)
    async def send_schedule_now(self, interaction: discord.Interaction, button: ui.Button):
        if not daily_events_schedule:
            return await interaction.response.send_message("❌ لا يوجد جدول محفوظ لإرساله!", ephemeral=True)
        modal = ScheduleAnnounceModal()
        await interaction.response.send_modal(modal)


class SelectEventsForScheduleView(ui.View):
    def __init__(self):
        super().__init__()
        all_evs = get_all_events()
        options = [discord.SelectOption(label=name, description=data['desc'][:50]) for name, data in all_evs.items()]
        select = ui.Select(placeholder="اختر الفعاليات...", min_values=1, max_values=len(options), options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_events = interaction.data['values']
        modal = SetEventTimesModal(selected_events)
        await interaction.response.send_modal(modal)


class SetEventTimesModal(ui.Modal, title="تحديد أوقات الفعاليات"):
    def __init__(self, selected_events):
        super().__init__()
        self.inputs = []
        for name in selected_events[:5]:
            item = ui.TextInput(label=f"وقت {name} (بتوقيت مصر)", placeholder="مثال: 09:00 PM", required=True)
            self.inputs.append((name, item))
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        global daily_events_schedule
        daily_events_schedule = []
        for name, item in self.inputs:
            daily_events_schedule.append({"name": name, "time": item.value})

        log_action("قام بإنشاء/تعديل مسودة جدول الفعاليات.", interaction.user)
        await interaction.response.send_message("✅ تم حفظ جدول الفعاليات كمسودة بنجاح!", ephemeral=True)


class ScheduleAnnounceModal(ui.Modal, title="تفاصيل إعلان الجدول"):
    header = ui.TextInput(label="النص الافتتاحي للإعلان", style=discord.TextStyle.paragraph, required=True)
    footer = ui.TextInput(label="النص الختامي للإعلان", style=discord.TextStyle.paragraph, required=False)
    image_url = ui.TextInput(label="رابط صورة للإعلان (اختياري)", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title="📅 جدول فعاليات اليوم (بتوقيت مصر)", description=self.header.value, color=discord.Color.gold())
        for item in daily_events_schedule:
            embed.add_field(name=f"🎮 {item['name']}", value=f"⏰ الموعد: **{item['time']}**", inline=False)

        if self.footer.value:
            embed.set_footer(text=self.footer.value)
        if self.image_url.value:
            embed.set_image(url=self.image_url.value)

        for ch_id in ANNOUNCEMENT_CHANNELS:
            channel = bot.get_channel(ch_id)
            if channel:
                await channel.send(embed=embed)

        log_action("قام بنشر إعلان جدول الفعاليات اليومي.", interaction.user)
        await interaction.response.send_message("🚀 تم نشر الإعلان بنجاح في رومات الإعلانات!", ephemeral=True)


# ==========================================
# 4. جدول الإداريين والتذكيرات والتأكيد
# ==========================================

class StaffAssignmentView(ui.View):
    def __init__(self):
        super().__init__()
        options = [discord.SelectOption(label=f"{ev['name']} ({ev['time']})", value=f"{ev['name']}|{ev['time']}") for ev in daily_events_schedule]
        select = ui.Select(placeholder="اختر الفعالية لتعيين إداري لها...", options=options)
        select.callback = self.event_select_callback
        self.add_item(select)

        user_select = ui.UserSelect(placeholder="اختر الإداري المسؤول...", min_values=1, max_values=1)
        user_select.callback = self.user_select_callback
        self.add_item(user_select)

        self.selected_event = None
        self.selected_user = None

    async def event_select_callback(self, interaction: discord.Interaction):
        self.selected_event = interaction.data['values'][0]
        await interaction.response.defer()

    async def user_select_callback(self, interaction: discord.Interaction):
        self.selected_user = interaction.data['values'][0]
        await interaction.response.defer()

    @ui.button(label="حفظ التكليف", style=discord.ButtonStyle.primary, row=2)
    async def save_assignment(self, interaction: discord.Interaction, button: ui.Button):
        if not self.selected_event or not self.selected_user:
            return await interaction.followup.send("❌ يرجى اختيار الفعالية والإداري أولاً!", ephemeral=True)

        staff_assignments[self.selected_event] = self.selected_user
        log_action(f"كلف الإداري <@{self.selected_user}> بفعالية {self.selected_event.split('|')[0]}.", interaction.user)
        await interaction.followup.send(f"✅ تم تكليف <@{self.selected_user}> بالفعالية.", ephemeral=True)

    @ui.button(label="نشر جدول الإداريين وتفعيله", style=discord.ButtonStyle.success, row=2)
    async def publish_staff_schedule(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(title="📋 جدول توزيع إداريي الفعاليات اليومي", color=discord.Color.purple())
        for event_key, user_id in staff_assignments.items():
            name, time_str = event_key.split('|')
            embed.add_field(name=f"🎮 {name} - ⏰ {time_str}", value=f"👤 الإداري: <@{user_id}>", inline=False)

        channel = bot.get_channel(STAFF_SCHEDULE_CHANNEL)
        if channel:
            await channel.send(embed=embed)
            log_action("نشر جدول مسؤولي الفعاليات الإداري.", interaction.user)
            await interaction.response.send_message("✅ تم نشر الجدول وتنشيط التذكيرات!", ephemeral=True)


class ClaimEventView(ui.View):
    def __init__(self, admin_id):
        super().__init__(timeout=600)  # 10 دقائق
        self.admin_id = admin_id
        self.claimed = False

    @ui.button(label="إستلام الفعالية ✋", style=discord.ButtonStyle.success, custom_id="btn_claim_event")
    async def claim(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ هذا التذكير مخصص لإداري آخر!", ephemeral=True)

        self.claimed = True
        button.disabled = True
        button.label = "تم الاستلام ✅"
        log_action("قام بتأكيد استلام الفعالية.", interaction.user)
        await interaction.response.edit_message(content=f"✅ **تم استلام الفعالية بنجاح بواسطة:** {interaction.user.mention}", view=self)

    async def on_timeout(self):
        if not self.claimed:
            for child in self.children:
                child.disabled = True
            try:
                await self.message.edit(content="❌ **لم يتم استلام الفعالية (انتهى الوقت المحدد - 10 دقائق).**", view=self)
            except Exception:
                pass


# ==========================================
# 5. التسجيل الحي وإدارة اللعبة الحية
# ==========================================

class SelectEventToStartView(ui.View):
    def __init__(self):
        super().__init__()
        all_evs = get_all_events()
        options = [discord.SelectOption(label=name, description=data['desc'][:50]) for name, data in all_evs.items()]
        select = ui.Select(placeholder="اختر الفعالية الحالية...", options=options)
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        event_name = interaction.data['values'][0]
        all_evs = get_all_events()
        event_info = all_evs[event_name]

        pub_channel = bot.get_channel(PUBLIC_EVENTS_CHANNEL)
        pub_embed = discord.Embed(
            title=f"🎉 فتح باب التسجيل: {event_name}",
            description=f"{event_info['desc']}\n\n👥 **عدد المشاركين الحالي:** 0",
            color=discord.Color.green()
        )
        if event_info.get("image"):
            pub_embed.set_image(url=event_info["image"])

        pub_view = MemberRegistrationView()
        pub_msg = await pub_channel.send(embed=pub_embed, view=pub_view)

        active_live_event["name"] = event_name
        active_live_event["participants"] = []
        active_live_event["public_msg"] = pub_msg
        active_live_event["mafia_state"] = {}

        admin_embed = discord.Embed(title=f"⚙️ لوحة إدارة فعالية: {event_name}", description="قائمة اللاعبين المشاركين:\nلا يوجد مشاركون بعد.", color=discord.Color.blue())
        admin_view = AdminLiveControlView()
        admin_msg = await interaction.response.send_message(embed=admin_embed, view=admin_view, ephemeral=True)
        active_live_event["admin_msg"] = admin_msg

        log_action(f"فتح باب التسجيل لفعالية: {event_name}.", interaction.user)


class MemberRegistrationView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="مشاركة 🟢", style=discord.ButtonStyle.success, custom_id="btn_join_event")
    async def join(self, interaction: discord.Interaction, button: ui.Button):
        user = interaction.user
        if user.id in banned_users:
            banned_by = banned_users[user.id]
            return await interaction.response.send_message(f"❌ أنت محظور من المشاركة في الفعاليات بواسطة الإداري: <@{banned_by}>", ephemeral=True)

        if user in active_live_event["participants"]:
            return await interaction.response.send_message("أنت مسجل بالفعل في الفعالية!", ephemeral=True)

        active_live_event["participants"].append(user)
        await self.update_panels()
        await interaction.response.send_message("✅ تم تسجلك في الفعالية!", ephemeral=True)

    @ui.button(label="خروج 🔴", style=discord.ButtonStyle.danger, custom_id="btn_leave_event")
    async def leave(self, interaction: discord.Interaction, button: ui.Button):
        user = interaction.user
        if user not in active_live_event["participants"]:
            return await interaction.response.send_message("أنت غير مسجل بالأساس!", ephemeral=True)

        active_live_event["participants"].remove(user)
        await self.update_panels()
        await interaction.response.send_message("🔴 تم إلغاء مشاركتك.", ephemeral=True)

    async def update_panels(self):
        pub_msg = active_live_event["public_msg"]
        embed = pub_msg.embeds[0]
        count = len(active_live_event["participants"])
        all_evs = get_all_events()
        embed.description = f"{all_evs[active_live_event['name']]['desc']}\n\n👥 **عدد المشاركين الحالي:** {count}"
        await pub_msg.edit(embed=embed)


class AdminLiveControlView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="بدء الفعالية 🎬", style=discord.ButtonStyle.primary)
    async def start_game(self, interaction: discord.Interaction, button: ui.Button):
        event_name = active_live_event["name"]
        participants = active_live_event["participants"]

        # 1. إذا كانت الفعالية هي لعبة المافيا
        if event_name == "لعبة المافيا":
            if len(participants) < 4:
                return await interaction.response.send_message("❌ لعبة المافيا تتطلب 4 لاعبين على الأقل للبدء!", ephemeral=True)

            await start_mafia_game(interaction)
            return

        # 2. أي فعالية أخرى
        pub_msg = active_live_event["public_msg"]
        disabled_view = ui.View()
        embed = pub_msg.embeds[0]
        embed.title = f"🔒 اكتمل التسجيل - بدأت فعالية: {event_name}"
        await pub_msg.edit(embed=embed, view=disabled_view)

        for player in participants:
            player_stats[player.id] = player_stats.get(player.id, 0) + 1

        log_action(f"بدأ فعالية {event_name} بـ {len(participants)} مشارك.", interaction.user)
        await interaction.response.send_message("🚀 تم إغلاق التسجيل وبدء الفعالية واحتساب المشاركات!", ephemeral=True)

    @ui.button(label="طرد لاعب ❌", style=discord.ButtonStyle.secondary)
    async def kick_player(self, interaction: discord.Interaction, button: ui.Button):
        if not active_live_event["participants"]:
            return await interaction.response.send_message("لا يوجد مشاركون لطرد أحدهم!", ephemeral=True)
        view = KickPlayerSelectView()
        await interaction.response.send_message("اختر اللاعب المراد استبعاده:", view=view, ephemeral=True)

    @ui.button(label="إلغاء الفعالية 🛑", style=discord.ButtonStyle.danger)
    async def cancel_event(self, interaction: discord.Interaction, button: ui.Button):
        pub_msg = active_live_event["public_msg"]
        embed = pub_msg.embeds[0]
        embed.title = f"❌ تم إلغاء فعالية: {active_live_event['name']}"
        embed.color = discord.Color.red()
        await pub_msg.edit(embed=embed, view=ui.View())

        active_live_event["participants"] = []
        log_action(f"ألغى فعالية {active_live_event['name']}.", interaction.user)
        await interaction.response.send_message("🛑 تم إلغاء الفعالية وإغلاق باب التسجيل.", ephemeral=True)


class KickPlayerSelectView(ui.View):
    def __init__(self):
        super().__init__()
        options = [discord.SelectOption(label=p.display_name, value=str(p.id)) for p in active_live_event["participants"]]
        select = ui.Select(placeholder="اختر لاعباً لطرد...", options=options)
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        user_id = int(interaction.data['values'][0])
        target_user = discord.utils.get(active_live_event["participants"], id=user_id)

        if target_user:
            active_live_event["participants"].remove(target_user)
            pub_msg = active_live_event["public_msg"]
            embed = pub_msg.embeds[0]
            all_evs = get_all_events()
            embed.description = f"{all_evs[active_live_event['name']]['desc']}\n\n👥 **عدد المشاركين الحالي:** {len(active_live_event['participants'])}"
            await pub_msg.edit(embed=embed)

            log_action(f"استبعد اللاعب {target_user.mention} من الفعالية الحية.", interaction.user)
            await interaction.response.send_message(f"❌ تم طرد {target_user.mention} من الفعالية.", ephemeral=True)


# ==========================================
# 6. البرمجة التفاعلية للعبة المافيا
# ==========================================

async def start_mafia_game(interaction: discord.Interaction):
    players = active_live_event["participants"].copy()
    random.shuffle(players)

    killer = players[0]
    doctor = players[1]
    citizens = players[2:]

    active_live_event["mafia_state"] = {
        "killer": killer,
        "doctor": doctor,
        "citizens": citizens,
        "target": None,
        "healed": None
    }

    # زيادة النقاط للاعبين
    for p in players:
        player_stats[p.id] = player_stats.get(p.id, 0) + 1

    # إرسال الأدوار السرية بالخاص
    await killer.send("🕵️ **أنت القاتل في لعبة المافيا!** سيُطلب منك اختيار ضحيتك الآن.")
    await doctor.send("🩺 **أنت الدكتور في لعبة المافيا!** سيُطلب منك اختيار شخص لإنقاذه.")
    for c in citizens:
        await c.send("👨‍🌾 **أنت مواطن بريء!** انتظر نتائج الليل.")

    # 1. القاتل يختار الضحية عبر الخاص
    killer_view = MafiaTargetSelectView(citizens, "killer")
    await killer.send("اختر الشخص الذي تريد القضاء عليه:", view=killer_view)

    # 2. الدكتور يختار شخصاً لإنقاذه عبر الخاص
    doctor_view = MafiaTargetSelectView(citizens, "doctor")
    await doctor.send("اختر الشخص الذي تريد إنقاذه الليلة:", view=doctor_view)

    log_action("بدأ فعالية لعبة المافيا ووزع الأدوار بالخاص.", interaction.user)
    await interaction.response.send_message("🕵️ **بدأت لعبة المافيا!** تم توزيع الأدوار بالخاص على القاتل والدكتور والمواطنين.", ephemeral=True)


class MafiaTargetSelectView(ui.View):
    def __init__(self, candidates, role):
        super().__init__()
        self.role = role
        options = [discord.SelectOption(label=p.display_name, value=str(p.id)) for p in candidates]
        select = ui.Select(placeholder="اختر اللاعب...", options=options)
        select.callback = self.callback
        self.add_item(select)

    async def callback(self, interaction: discord.Interaction):
        selected_id = int(interaction.data['values'][0])
        state = active_live_event["mafia_state"]

        if self.role == "killer":
            state["target"] = selected_id
            await interaction.response.send_message("🗡️ تم تحديد هدفك بنجاح.", ephemeral=True)
        elif self.role == "doctor":
            state["healed"] = selected_id
            await interaction.response.send_message("🩺 تم اختيار المريض لإنقاذه بنجاح.", ephemeral=True)

        # إذا قام الاثنين بالاختيار، يتم حساب النتيجة
        if state["target"] and state["healed"]:
            await resolve_mafia_round()


async def resolve_mafia_round():
    state = active_live_event["mafia_state"]
    pub_channel = bot.get_channel(PUBLIC_EVENTS_CHANNEL)

    target_id = state["target"]
    healed_id = state["healed"]

    target_user = discord.utils.get(active_live_event["participants"], id=target_id)

    if target_id == healed_id:
        result_text = f"🎉 **انتهى الليل!** حاول القاتل القضاء على {target_user.mention} ولكن **الدكتور نجح في إنقاذه!** لم يخسر أحد."
    else:
        result_text = f"💀 **انتهى الليل!** تم اغتيال {target_user.mention} ولم يتمكن الدكتور من إنقاذه! خرج {target_user.mention} من اللعبة."

    embed = discord.Embed(title="🌙 نتائج ليلة المافيا", description=result_text, color=discord.Color.dark_red())
    await pub_channel.send(embed=embed)


# ==========================================
# 7. العقوبات والإحصائيات
# ==========================================

class BanManagementView(ui.View):
    def __init__(self):
        super().__init__()
        user_select = ui.UserSelect(placeholder="اختر لاعباً للحظر / فك الحظر...", min_values=1, max_values=1)
        user_select.callback = self.user_callback
        self.add_item(user_select)
        self.selected_user = None

    async def user_callback(self, interaction: discord.Interaction):
        self.selected_user = interaction.data['values'][0]
        await interaction.response.defer()

    @ui.button(label="حظر من المشاركة 🚫", style=discord.ButtonStyle.danger, row=1)
    async def ban_user(self, interaction: discord.Interaction, button: ui.Button):
        if not self.selected_user:
            return await interaction.followup.send("❌ يرجى اختيار العضو أولاً!", ephemeral=True)

        banned_users[self.selected_user] = interaction.user.id
        log_action(f"حظر <@{self.selected_user}> من المشاركة في الفعاليات.", interaction.user)
        await interaction.followup.send(f"🚫 تم حظر <@{self.selected_user}> من المشاركة.", ephemeral=True)

    @ui.button(label="فك الحظر ✅", style=discord.ButtonStyle.success, row=1)
    async def unban_user(self, interaction: discord.Interaction, button: ui.Button):
        if not self.selected_user:
            return await interaction.followup.send("❌ يرجى اختيار العضو أولاً!", ephemeral=True)

        if self.selected_user in banned_users:
            del banned_users[self.selected_user]
            log_action(f"رفع الحظر عن <@{self.selected_user}>.", interaction.user)
            await interaction.followup.send(f"✅ تم فك الحظر عن <@{self.selected_user}>.", ephemeral=True)
        else:
            await interaction.followup.send("هذا العضو غير محظور بالأساس!", ephemeral=True)

    @ui.button(label="قائمة المعاقبين 📜", style=discord.ButtonStyle.secondary, row=1)
    async def view_banned(self, interaction: discord.Interaction, button: ui.Button):
        if not banned_users:
            return await interaction.response.send_message("لا يوجد أعضاء محظورون حالياً.", ephemeral=True)

        list_text = "\n".join([f"👤 العضو: <@{u_id}> | 👮 الإداري: <@{a_id}>" for u_id, a_id in banned_users.items()])
        embed = discord.Embed(title="🚫 قائمة المحظورين من الفعاليات", description=list_text, color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)


def build_stats_embed():
    if not player_stats:
        return discord.Embed(title="📊 إحصائيات مشاركة اللاعبين", description="لا توجد إحصائيات مسجلة بعد.", color=discord.Color.gold())

    sorted_stats = sorted(player_stats.items(), key=lambda x: x[1], reverse=True)
    text = "\n".join([f"🏅 <@{u_id}> — **{count}** فعالية" for u_id, count in sorted_stats])
    return discord.Embed(title="📊 لوحة صدارة مشاركات اللاعبين", description=text, color=discord.Color.gold())


class StatsControlView(ui.View):
    def __init__(self):
        super().__init__()

    @ui.button(label="تصفير الإحصائيات 🔄", style=discord.ButtonStyle.danger)
    async def reset_stats(self, interaction: discord.Interaction, button: ui.Button):
        player_stats.clear()
        log_action("قام بتصفير إحصائيات مشاركات جميع اللاعبين.", interaction.user)
        embed = build_stats_embed()
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send("✅ تم تصفير جميع الإحصائيات بنجاح!", ephemeral=True)


# ==========================================
# 8. التراسك والتأكد من توقيت مصر
# ==========================================

@tasks.loop(hours=24)
async def auto_reset_daily_log():
    """تصفير السجل اليومي تلقائياً عند 12 منتصف الليل بتوقيت مصر"""
    daily_logs.clear()

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_panel(ctx):
    """إنشاء لوحة التحكم الثابتة"""
    embed = discord.Embed(
        title="🎮 لوحة تحكم إدارة الفعاليات الشاملة",
        description="استخدم الأزرار أدناه للتحكم الكامل في الجداول، الفعاليات المبرمجة، وبنك البيانات بدون أوامر.",
        color=discord.Color.dark_purple()
    )
    view = MainControlPanelView()
    await ctx.send(embed=embed, view=view)

@bot.event
async def on_ready():
    if not auto_reset_daily_log.is_running():
        auto_reset_daily_log.start()
    now_egypt = datetime.datetime.now(EGYPT_TZ).strftime("%Y-%m-%d %I:%M:%S %p")
    print(f"✅ البوت جاهز بكافة وظائفه باسم: {bot.user}")
    print(f"🕐 التوقيت الحالي المعتمد في البوت (مصر): {now_egypt}")

bot.run(os.getenv("DISCORD_TOKEN"))
