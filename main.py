import os
import datetime
import asyncio
import pytz
import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput, UserSelect, Select

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- ضبط المنطقة الزمنية (مصر) ---
EGYPT_TZ = pytz.timezone("Africa/Cairo")

# --- آيديهات الرومات والرولات المحددة ---
SHIFT_CHANNEL_ID   = 1542004969983574066  # روم المناوبات
EXCUSE_CHANNEL_ID  = 1544032822098927877  # روم طلبات الاعتذار
WARNING_CHANNEL_ID = 1543078048864403577  # روم التحذيرات

# رولات الإدارة المسموح لها باستخدام اللوحة
ALLOWED_ROLES = [
    1541599513372921856,  # Owner
    1544120441717923950,  # CEO
    1541599646810374234   # Community Manager
]

# --- قواعد البيانات الإدارية ---
shifts = {}         # {shift_id: {"name": str, "time": str, "staff": [], "confirmed": [], "excuses": {}, "active_msg_id": int, "closed": bool}}
staff_warnings = {} # {user_id: int}
staff_vacations = {}# {user_id: {"reason": str, "days": str, "start": str}}
staff_excuse_count = {} # {user_id: int}
max_weekly_excuses = 3
shift_counter = 1

def is_admin(interaction: discord.Interaction) -> bool:
    """التحقق من أن المستخدم يملك إحدى رولات الإدارة العليا"""
    user_role_ids = [role.id for role in interaction.user.roles]
    return any(role_id in user_role_ids for role_id in ALLOWED_ROLES)

# ==================== Modals (النماذج المنبثقة) ====================

class AddShiftModal(Modal, title="إضافة / تعديل مناوبة"):
    shift_name = TextInput(label="اسم المناوبة", placeholder="مثال: مناوبة 10", required=True)
    shift_time = TextInput(label="وقت المناوبة (24 ساعة HH:MM)", placeholder="مثال: 22:38", required=True, max_length=5)

    def __init__(self, shift_id=None):
        super().__init__()
        self.shift_id = shift_id
        if shift_id and shift_id in shifts:
            self.shift_name.default = shifts[shift_id]["name"]
            self.shift_time.default = shifts[shift_id]["time"]

    async def on_submit(self, interaction: discord.Interaction):
        global shift_counter
        if self.shift_id:
            shifts[self.shift_id]["name"] = self.shift_name.value
            shifts[self.shift_id]["time"] = self.shift_time.value
            await interaction.response.send_message(f"✅ تم تعديل المناوبة بنجاح!", ephemeral=True)
        else:
            s_id = str(shift_counter)
            shifts[s_id] = {
                "name": self.shift_name.value,
                "time": self.shift_time.value,
                "staff": [],
                "confirmed": [],
                "excuses": {},
                "active_msg_id": None,
                "closed": False
            }
            shift_counter += 1
            await interaction.response.send_message(f"✅ تم إضافة مناوبة **{self.shift_name.value}** (ID: `{s_id}`) بوقت `{self.shift_time.value}` بنجاح!", ephemeral=True)

class ExcuseReasonModal(Modal, title="تقديم طلب اعتذار عن المناوبة"):
    reason = TextInput(label="سبب الاعتذار", style=discord.TextStyle.paragraph, placeholder="اكتب سبب اعتذارك...", required=True)

    def __init__(self, shift_id):
        super().__init__()
        self.shift_id = shift_id

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        current_excuses = staff_excuse_count.get(user_id, 0)

        if current_excuses >= max_weekly_excuses:
            await interaction.response.send_message(f"❌ تجاوزت الحد الأقصى للاعتذارات المسموحة هذا الأسبوع ({max_weekly_excuses}).", ephemeral=True)
            return

        shift = shifts[self.shift_id]
        shift["excuses"][user_id] = {"status": "جاري مراجعة الطلب من قبل المسؤلين", "reason": self.reason.value}
        staff_excuse_count[user_id] = current_excuses + 1

        excuse_channel = bot.get_channel(EXCUSE_CHANNEL_ID)
        if excuse_channel:
            embed = discord.Embed(title="📩 طلب اعتذار جديد", color=discord.Color.gold())
            embed.add_field(name="👤 الإداري:", value=interaction.user.mention, inline=True)
            embed.add_field(name="🔹 المناوبة:", value=shift["name"], inline=True)
            embed.add_field(name="📝 السبب:", value=self.reason.value, inline=False)
            
            view = ExcuseReviewView(self.shift_id, user_id)
            await excuse_channel.send(embed=embed, view=view)

        await update_shift_embed(self.shift_id)
        await interaction.response.send_message("✅ تم تقديم طلب الاعتذار وهو قيد المراجعة.", ephemeral=True)

class VacationModal(Modal, title="إرسال إداري في عطلة"):
    user_id_input = TextInput(label="آيدي الإداري", placeholder="أدخل ID الإداري", required=True)
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
            await interaction.response.send_message(f"🏖️ تم منح الإداري <@{target_id}> إجازة ({days_str}) بنجاح!", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ يرجي كتابة آيدي صحيح بالأرقام.", ephemeral=True)

# ==================== Views (الأزرار والواجهات) ====================

class ShiftNotificationView(View):
    def __init__(self, shift_id, disabled=False):
        super().__init__(timeout=None)
        self.shift_id = shift_id
        if disabled:
            for item in self.children:
                item.disabled = True

    @discord.ui.button(label="تأكيد الحضور", style=discord.ButtonStyle.green, emoji="✅", custom_id="btn_confirm")
    async def confirm_attendance(self, interaction: discord.Interaction, button: Button):
        shift = shifts.get(self.shift_id)
        if not shift or shift.get("closed"):
            await interaction.response.send_message("❌ انتهت مهلة تأكيد الحضور لهذه المناوبة.", ephemeral=True)
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
        if not shift or shift.get("closed"):
            await interaction.response.send_message("❌ انتهت مهلة تقديم الاعتذار لهذه المناوبة.", ephemeral=True)
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
        if not is_admin(interaction):
            await interaction.response.send_message("❌ لا تملك صلاحية للموافقة على الاعتذارات.", ephemeral=True)
            return
        shift = shifts.get(self.shift_id)
        if shift and self.staff_id in shift["excuses"]:
            shift["excuses"][self.staff_id]["status"] = "مقبول"
            await update_shift_embed(self.shift_id)
            await interaction.response.send_message("✅ تم قبول طلب الاعتذار.", ephemeral=True)
            self.stop()

    @discord.ui.button(label="رفض الطلب", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject_excuse(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ لا تملك صلاحية لرفض الاعتذارات.", ephemeral=True)
            return
        shift = shifts.get(self.shift_id)
        if shift and self.staff_id in shift["excuses"]:
            shift["excuses"][self.staff_id]["status"] = "مرفوض"
            await update_shift_embed(self.shift_id)
            await interaction.response.send_message("❌ تم رفض طلب الاعتذار.", ephemeral=True)
            self.stop()

# ==================== لوحة التحكم الـ 5 أزرار المحدثة ====================

class AdminBotControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    # 1. إضافة / تعديل مناوبة
    @discord.ui.button(label="إضافة / تعديل مناوبة", style=discord.ButtonStyle.primary, emoji="➕", row=0)
    async def btn_manage_shifts(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ هذه اللوحة مخصصة للإدارة العليا فقط.", ephemeral=True)
            return
        
        view = View()
        view.add_item(Button(label="إضافة مناوبة جديدة", style=discord.ButtonStyle.green, custom_id="add_new_shift"))
        
        if shifts:
            options = [discord.SelectOption(label=f"{d['name']} ({d['time']})", value=s_id) for s_id, d in shifts.items()]
            select = Select(placeholder="اختر مناوبة لتعديلها أو حذفها...", options=options)
            
            async def select_callback(inter):
                s_id = select.values[0]
                sub_view = View()
                
                btn_edit = Button(label="تعديل الاسم/الوقت", style=discord.ButtonStyle.primary)
                async def edit_cb(i):
                    await i.response.send_modal(AddShiftModal(s_id))
                btn_edit.callback = edit_cb
                
                btn_del = Button(label="حذف المناوبة", style=discord.ButtonStyle.danger)
                async def del_cb(i):
                    del shifts[s_id]
                    await i.response.send_message("🗑️ تم حذف المناوبة بنجاح.", ephemeral=True)
                btn_del.callback = del_cb
                
                sub_view.add_item(btn_edit)
                sub_view.add_item(btn_del)
                await inter.response.send_message(f"التحكم بالمناوبة: **{shifts[s_id]['name']}**", view=sub_view, ephemeral=True)

            select.callback = select_callback
            view.add_item(select)

        async def add_cb(i):
            await i.response.send_modal(AddShiftModal())
        view.children[0].callback = add_cb

        await interaction.response.send_message("⚙️ **إدارة المناوبات:**", view=view, ephemeral=True)

    # 2. توظيف إداري
    @discord.ui.button(label="توظيف إداري", style=discord.ButtonStyle.success, emoji="📝", row=0)
    async def btn_assign_staff(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ هذه اللوحة مخصصة للإدارة العليا فقط.", ephemeral=True)
            return
        if not shifts:
            await interaction.response.send_message("❌ لا توجد مناوبات مضافة حالياً.", ephemeral=True)
            return
        await interaction.response.send_message("اختر المناوبة لتسكين أو إلغاء تسكين الإداري:", view=SelectShiftStaffView(), ephemeral=True)

    # 3. إرسال في عطلة
    @discord.ui.button(label="إرسال في عطلة", style=discord.ButtonStyle.secondary, emoji="🏖️", row=0)
    async def btn_vacation(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ هذه اللوحة مخصصة للإدارة العليا فقط.", ephemeral=True)
            return
        
        view = View()
        btn_add_vac = Button(label="إعطاء إجازة", style=discord.ButtonStyle.green)
        async def add_v_cb(i):
            await i.response.send_modal(VacationModal())
        btn_add_vac.callback = add_v_cb

        btn_cancel_vac = Button(label="إلغاء إجازة إداري", style=discord.ButtonStyle.danger)
        async def cancel_v_cb(i):
            if not staff_vacations:
                await i.response.send_message("لا يوجد إداريين في عطلة حالياً.", ephemeral=True)
                return
            v_options = [discord.SelectOption(label=f"إداري ID: {uid}", value=str(uid)) for uid in staff_vacations.keys()]
            v_select = Select(placeholder="اختر الإداري لإلغاء إجازته...", options=v_options)
            async def v_sel_cb(inter):
                target = int(v_select.values[0])
                del staff_vacations[target]
                await inter.response.send_message("✅ تم إلغاء الإجازة بنجاح.", ephemeral=True)
            v_select.callback = v_sel_cb
            v_view = View()
            v_view.add_item(v_select)
            await i.response.send_message("اختر الإداري:", view=v_view, ephemeral=True)
        btn_cancel_vac.callback = cancel_v_cb

        view.add_item(btn_add_vac)
        view.add_item(btn_cancel_vac)
        await interaction.response.send_message("🏖️ **إدارة العطلات للإداريين:**", view=view, ephemeral=True)

    # 4. سجل الإداريين
    @discord.ui.button(label="سجل الإداريين", style=discord.ButtonStyle.secondary, emoji="📁", row=1)
    async def btn_staff_log(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ هذه اللوحة مخصصة للإدارة العليا فقط.", ephemeral=True)
            return

        embed = discord.Embed(title="📁 سجل التحذيرات والإجازات للإداريين", color=discord.Color.blue())
        warn_text = "\n".join([f"• <@{uid}>: {count} تحذيرات" for uid, count in staff_warnings.items() if count > 0]) or "لا يوجد تحذيرات نشطة"
        vac_text = "\n".join([f"• <@{uid}>: إجازة {data['days']} (السبب: {data['reason']})" for uid, data in staff_vacations.items()]) or "لا يوجد إداريين في عطلة"

        embed.add_field(name="⚠️ سجل التحذيرات النشطة:", value=warn_text, inline=False)
        embed.add_field(name="🏖️ الإداريين المجازين حالياً:", value=vac_text, inline=False)

        view = View()
        btn_reset_all = Button(label="تصفير جميع التحذيرات", style=discord.ButtonStyle.danger)
        async def r_all_cb(i):
            staff_warnings.clear()
            await i.response.send_message("✅ تم تصفير جميع التحذيرات لكل الإداريين.", ephemeral=True)
        btn_reset_all.callback = r_all_cb
        view.add_item(btn_reset_all)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # 5. إدارة
    @discord.ui.button(label="إدارة", style=discord.ButtonStyle.danger, emoji="⚙️", row=1)
    async def btn_settings(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ هذه اللوحة مخصصة للإدارة العليا فقط.", ephemeral=True)
            return

        embed = discord.Embed(title="⚙️ إعدادات النظام والإعتذارات", description=f"الحد الأقصى للاعتذارات الأسبوعية: **{max_weekly_excuses}**", color=discord.Color.dark_red())
        view = View()
        btn_reset_excuses = Button(label="تصفير اعتذارات الجميع", style=discord.ButtonStyle.primary)
        async def r_ex_cb(i):
            staff_excuse_count.clear()
            await i.response.send_message("✅ تم تصفير سجل الاعتذارات الأسبوعية لجميع الإداريين.", ephemeral=True)
        btn_reset_excuses.callback = r_ex_cb
        view.add_item(btn_reset_excuses)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class SelectShiftStaffView(View):
    def __init__(self):
        super().__init__(timeout=None)
        options = [discord.SelectOption(label=f"{d['name']} ({d['time']})", value=s_id) for s_id, d in shifts.items()]
        if options:
            select = Select(placeholder="اختر المناوبة لتعديل الإداريين فيها...", options=options)
            select.callback = self.on_select
            self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        s_id = interaction.data['values'][0]
        
        v = View()
        btn_add = Button(label="تسكين إداري", style=discord.ButtonStyle.green)
        async def add_cb(i):
            await i.response.send_message(f"اختر الإداري لتسكينه في `{shifts[s_id]['name']}`:", view=UserAssignSelectView(s_id, mode="add"), ephemeral=True)
        btn_add.callback = add_cb

        btn_rem = Button(label="إلغاء تسكين إداري", style=discord.ButtonStyle.danger)
        async def rem_cb(i):
            await i.response.send_message(f"اختر الإداري لإلغاء تسكينه من `{shifts[s_id]['name']}`:", view=UserAssignSelectView(s_id, mode="remove"), ephemeral=True)
        btn_rem.callback = rem_cb

        v.add_item(btn_add)
        v.add_item(btn_rem)
        await interaction.response.send_message(f"إدارة إداريين مناوبة **{shifts[s_id]['name']}**:", view=v, ephemeral=True)

class UserAssignSelectView(View):
    def __init__(self, shift_id, mode="add"):
        super().__init__(timeout=None)
        self.shift_id = shift_id
        self.mode = mode
        u_select = UserSelect(placeholder="اختر الإداري...")
        u_select.callback = self.user_selected
        self.add_item(u_select)

    async def user_selected(self, interaction: discord.Interaction):
        uid = int(interaction.data['values'][0])
        shift = shifts[self.shift_id]
        if self.mode == "add":
            if uid not in shift["staff"]:
                shift["staff"].append(uid)
                await interaction.response.send_message(f"✅ تم تسكين <@{uid}> في مناوبة `{shift['name']}` بنجاح!", ephemeral=True)
            else:
                await interaction.response.send_message(f"ℹ️ الإداري مضاف بالفعل في هذه المناوبة.", ephemeral=True)
        else:
            if uid in shift["staff"]:
                shift["staff"].remove(uid)
                await interaction.response.send_message(f"🗑️ تم إلغاء تسكين <@{uid}> من مناوبة `{shift['name']}` بنجاح!", ephemeral=True)
            else:
                await interaction.response.send_message(f"ℹ️ الإداري غير موجود في هذه المناوبة.", ephemeral=True)

# ==================== المساعدات والدوال التلقائية والتوقيت ====================

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

    view = ShiftNotificationView(shift_id, disabled=shift.get("closed", False))
    await msg.edit(embed=embed, view=view)

@tasks.loop(seconds=20)
async def shift_scheduler():
    # استخدام التوقيت المحلي لمصر (Africa/Cairo)
    now_egypt = datetime.datetime.now(EGYPT_TZ)
    now_str = now_egypt.strftime("%H:%M")
    
    channel = bot.get_channel(SHIFT_CHANNEL_ID)
    if not channel:
        return

    for s_id, data in list(shifts.items()):
        if data["time"] == now_str and not data.get("active_msg_id"):
            data["closed"] = False
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

            # تشغيل مؤشر الانتظار لمدة 10 دقائق بالضبط لقفل الأزرار وإنزال التحذيرات
            asyncio.create_task(handle_shift_timeout(s_id))

async def handle_shift_timeout(shift_id):
    # الانتظار لمدة 10 دقائق (600 ثانية)
    await asyncio.sleep(600)
    
    shift = shifts.get(shift_id)
    if not shift:
        return

    # قفل الأزرار في المناوبة
    shift["closed"] = True
    await update_shift_embed(shift_id)

    # معالجة التحذيرات للإداريين المقصرين
    warning_channel = bot.get_channel(WARNING_CHANNEL_ID)

    for uid in shift["staff"]:
        if uid in staff_vacations:
            continue
        
        if uid in shift["confirmed"]:
            continue

        ex = shift["excuses"].get(uid)
        if ex and (ex["status"] == "مقبول" or ex["status"] == "جاري مراجعة الطلب من قبل المسؤلية"):
            continue

        # تسجيل وتطبيق التحذير
        staff_warnings[uid] = staff_warnings.get(uid, 0) + 1
        count = staff_warnings[uid]

        if warning_channel:
            if count >= 3:
                await warning_channel.send(content=f"⚠️ <@&1541599646810374234> تنبيه عاجل! الإداري <@{uid}> وصل إلى **{count}** تحذيرات بسبب التغيب عن مناوبة `{shift['name']}`.")
            else:
                await warning_channel.send(content=f"⚠️ الإداري <@{uid}> حصل على تحذير ({count}/3) بسبب التغيب عن مناوبة `{shift['name']}`.")

class MainSetupView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="إدارة البوت", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def open_control_panel(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ عفواً، هذه اللوحة مخصصة فقط لـ (Owner, CEO, Community Manager).", ephemeral=True)
            return
        await interaction.response.send_message("⚙️ **لوحة التحكم الرئيسية لإدارة البوت والمناوبات:**", view=AdminBotControlView(), ephemeral=True)

@bot.command(name="setup")
async def setup_command(ctx):
    user_role_ids = [role.id for role in ctx.author.roles]
    if not any(role_id in user_role_ids for role_id in ALLOWED_ROLES):
        await ctx.send("❌ عفواً، هذا الأمر مخصص فقط للإدارة العليا.")
        return

    embed = discord.Embed(
        title="🤖 لوحة تحكم إدارة المناوبات",
        description="اضغط على زر **إدارة البوت** أدناه للتحكم في كافة المناوبات والوظائف والإجازات بالأزرار.",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed, view=MainSetupView())

@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}")
    if not shift_scheduler.is_running():
        shift_scheduler.start()

bot.run(os.getenv("DISCORD_TOKEN"))
