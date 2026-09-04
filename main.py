import os
import datetime
import asyncio
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput, UserSelect, Select, ChannelSelect

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- ضبط المنطقة الزمنية (مصر) ---
EGYPT_TZ = ZoneInfo("Africa/Cairo")

# --- آيديهات الرومات والرولات المحددة ---
SHIFT_CHANNEL_ID   = 1542004969983574066  # روم المناوبات
EXCUSE_CHANNEL_ID  = 1544032822098927877  # روم طلبات الاعتذار
WARNING_CHANNEL_ID = 1543078048864403577  # روم التحذيرات

# رابط صورة لوحة التحكم
PANEL_IMAGE_URL = "https://cdn.discordapp.com/attachments/1544707827497697280/1545427200025821234/1788198741395.png?ex=6a9c1abd&is=6a9ac93d&hm=94e010bc303ae2a250195b32216aaedd7284f9c120e9345bc8214cf480b3e88b&"

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
bot_audit_logs = []  # قائمة لتخزين السجلات: [{"timestamp": datetime, "text": str}]

def is_admin(interaction: discord.Interaction) -> bool:
    """التحقق من أن المستخدم يملك إحدى رولات الإدارة العليا"""
    user_role_ids = [role.id for role in interaction.user.roles]
    return any(role_id in user_role_ids for role_id in ALLOWED_ROLES)

def log_action(action_text: str, executor: discord.User):
    """تسجيل العمليات في سجل البوت مع التاريخ والوقت"""
    now = datetime.datetime.now(EGYPT_TZ)
    now_str = now.strftime("%Y-%m-%d %H:%M")
    log_entry = {
        "timestamp": now,
        "text": f"[{now_str}] 👤 {executor.mention}: {action_text}"
    }
    bot_audit_logs.append(log_entry)

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
            log_action(f"تعديل مناوبة `{self.shift_name.value}`", interaction.user)
            await interaction.response.send_message("✅ تم تعديل المناوبة بنجاح!", ephemeral=True)
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
            log_action(f"إضافة مناوبة جديدة `{self.shift_name.value}` (ID: {s_id})", interaction.user)
            await interaction.response.send_message(f"✅ تم إضافة مناوبة **{self.shift_name.value}** (ID: `{s_id}`) بوقت `{self.shift_time.value}` بنجاح!", ephemeral=True)

class ExcuseReasonModal(Modal, title="تقديم طلب اعتذار عن المناوبة"):
    reason = TextInput(label="سبب الاعتذار", style=discord.TextStyle.paragraph, placeholder="اكتب سبب اعتذارك...", required=True)

    def __init__(self, shift_id):
        super().__init__()
        self.shift_id = shift_id

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        shift = shifts[self.shift_id]

        shift["excuses"][user_id] = {"status": "قيد المراجعة", "reason": self.reason.value}
        staff_excuse_count[user_id] = staff_excuse_count.get(user_id, 0) + 1

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
            log_action(f"منح إجازة للإداري <@{target_id}> لمدة ({days_str})", interaction.user)
            await interaction.response.send_message(f"🏖️ تم منح الإداري <@{target_id}> إجازة ({days_str}) بنجاح!", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ يرجي كتابة آيدي صحيح بالأرقام.", ephemeral=True)

class SetMaxExcusesModal(Modal, title="تعديل الحد الأقصى للاعتذارات"):
    max_count = TextInput(label="العدد الجديد للاعتذارات الأسبوعية", placeholder="مثال: 3", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        global max_weekly_excuses
        try:
            val = int(self.max_count.value)
            max_weekly_excuses = val
            log_action(f"تعديل حد الاعتذارات الأسبوعية إلى {val}", interaction.user)
            await interaction.response.send_message(f"✅ تم تغيير الحد الأقصى للاعتذارات الأسبوعية إلى **{val}** بنجاح!", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ يرجى كتابة رقم صحيح.", ephemeral=True)

class RemoveSpecificWarningModal(Modal, title="خصم تحذيرات من إداري"):
    count_to_remove = TextInput(label="عدد التحذيرات المراد إزالتها", placeholder="مثال: 1", required=True)

    def __init__(self, target_id):
        super().__init__()
        self.target_id = target_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rem_count = int(self.count_to_remove.value)
            current = staff_warnings.get(self.target_id, 0)
            new_count = max(0, current - rem_count)
            staff_warnings[self.target_id] = new_count
            
            log_action(f"إزالة {rem_count} تحذير من الإداري <@{self.target_id}> (المتبقي: {new_count})", interaction.user)
            await interaction.response.send_message(f"✅ تم خصم {rem_count} تحذير من <@{self.target_id}>. التحذيرات المتبقية: **{new_count}**", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ يرجى كتابة رقم صحيح.", ephemeral=True)

# نماذج إرسال الرسائل (نص عادي + Embed احترافي)
class SendNormalMessageModal(Modal, title="إرسال رسالة نصية"):
    message_content = TextInput(label="محتوى الرسالة (يدعم المنشن والروابط)", style=discord.TextStyle.paragraph, placeholder="اكتب رسالتك هنا...", required=True)

    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        target_channel = bot.get_channel(self.channel_id)
        if target_channel:
            await target_channel.send(content=self.message_content.value)
            log_action(f"إرسال رسالة نصية في الروم <#{self.channel_id}>", interaction.user)
            await interaction.response.send_message("✅ تم إرسال الرسالة بنجاح!", ephemeral=True)

class SendEmbedMessageModal(Modal, title="إرسال رسالة Embed (شكل احترافي)"):
    embed_title = TextInput(label="عنوان الرسالة (Title)", placeholder="مثال: قوانين سيرفر الديسكورد", required=False)
    embed_description = TextInput(label="نص الرسالة / القوانين", style=discord.TextStyle.paragraph, placeholder="اكتب تفاصيل الرسالة هنا...", required=True)
    thumb_url = TextInput(label="رابط صورة اللوجو الصغير (Thumbnail)", placeholder="رابط صورة مباشر (اختياري)", required=False)
    img_url = TextInput(label="رابط صورة كبيرة أسفل الرسالة", placeholder="رابط صورة مباشر (اختياري)", required=False)

    def __init__(self, channel_id):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        target_channel = bot.get_channel(self.channel_id)
        if target_channel:
            embed = discord.Embed(
                title=self.embed_title.value if self.embed_title.value else None,
                description=self.embed_description.value,
                color=discord.Color.dark_red()
            )
            if self.thumb_url.value:
                embed.set_thumbnail(url=self.thumb_url.value)
            if self.img_url.value:
                embed.set_image(url=self.img_url.value)

            await target_channel.send(embed=embed)
            log_action(f"إرسال رسالة Embed احترافية في الروم <#{self.channel_id}>", interaction.user)
            await interaction.response.send_message("✅ تم إرسال رسالة الـ Embed بنجاح!", ephemeral=True)

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
        user_id = interaction.user.id

        if not shift or shift.get("closed"):
            await interaction.response.send_message("❌ انتهت مهلة تأكيد الحضور لهذه المناوبة.", ephemeral=True)
            return
        
        if user_id not in shift["staff"]:
            await interaction.response.send_message("❌ أنت لست مسجلاً في هذه المناوبة.", ephemeral=True)
            return

        if user_id in shift["confirmed"]:
            await interaction.response.send_message("ℹ️ تم تأكيد حضورك بالفعل ولا يمكنك تغيير حالتك.", ephemeral=True)
            return

        ex = shift["excuses"].get(user_id)
        if ex and ex["status"] in ["قيد المراجعة", "مقبول"]:
            await interaction.response.send_message("❌ لديك طلب اعتذار قائم أو مقبول، لا يمكنك تأكيد الحضور إلا في حالة رفض الاعتذار.", ephemeral=True)
            return

        shift["confirmed"].append(user_id)
        if user_id in shift["excuses"]:
            del shift["excuses"][user_id]

        await update_shift_embed(self.shift_id)
        await interaction.response.send_message("✅ تم تأكيد حضورك في المناوبة بنجاح! ولن تتمكن من تعديل حالتك.", ephemeral=True)

    @discord.ui.button(label="تقديم اعتذار", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="btn_excuse")
    async def request_excuse(self, interaction: discord.Interaction, button: Button):
        shift = shifts.get(self.shift_id)
        user_id = interaction.user.id

        if not shift or shift.get("closed"):
            await interaction.response.send_message("❌ انتهت مهلة تقديم الاعتذار لهذه المناوبة.", ephemeral=True)
            return
            
        if user_id not in shift["staff"]:
            await interaction.response.send_message("❌ أنت لست مسجلاً في هذه المناوبة.", ephemeral=True)
            return

        if user_id in shift["confirmed"]:
            await interaction.response.send_message("❌ لقد قمت بتأكيد حضورك بالفعل ولا يمكنك تقديم اعتذار الآن.", ephemeral=True)
            return

        ex = shift["excuses"].get(user_id)
        if ex and ex["status"] in ["قيد المراجعة", "مقبول"]:
            await interaction.response.send_message("ℹ️ لديك طلب اعتذار قيد المراجعة أو مقبول بالفعل.", ephemeral=True)
            return

        current_excuses = staff_excuse_count.get(user_id, 0)
        if current_excuses >= max_weekly_excuses:
            await interaction.response.send_message(f"❌ لقد استنفذت الحد الأقصى للاعتذارات الأسبوعية المسموحة لك ({max_weekly_excuses}). لن يتم قبول اعتذارك.", ephemeral=True)
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
            log_action(f"قبول اعتذار الإداري <@{self.staff_id}> في مناوبة `{shift['name']}`", interaction.user)
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
            log_action(f"رفض اعتذار الإداري <@{self.staff_id}> في مناوبة `{shift['name']}`", interaction.user)
            await update_shift_embed(self.shift_id)
            await interaction.response.send_message("❌ تم رفض طلب الاعتذار (يمكن للإداري الآن تأكيد الحضور).", ephemeral=True)
            self.stop()

# ==================== لوحة التحكم الرئيسية والخيارات ====================

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
                    s_name = shifts[s_id]['name']
                    del shifts[s_id]
                    log_action(f"حذف المناوبة `{s_name}`", i.user)
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
                log_action(f"إلغاء إجازة الإداري <@{target}>", inter.user)
                await inter.response.send_message("✅ تم إلغاء الإجازة بنجاح.", ephemeral=True)
            v_select.callback = v_sel_cb
            v_view = View()
            v_view.add_item(v_select)
            await i.response.send_message("اختر الإداري:", view=v_view, ephemeral=True)
        btn_cancel_vac.callback = cancel_v_cb

        view.add_item(btn_add_vac)
        view.add_item(btn_cancel_vac)
        await interaction.response.send_message("🏖️ **إدارة العطلات للإداريين:**", view=view, ephemeral=True)

    # 4. سجل الإداريين (مع تصفير إداري واحد أو خصم تحذيرات محددة)
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

        # زر تصفير جميع التحذيرات
        btn_reset_all = Button(label="تصفير تحذيرات الجميع", style=discord.ButtonStyle.danger, emoji="⚠️")
        async def r_all_cb(i):
            staff_warnings.clear()
            log_action("تصفير جميع تحذيرات الإداريين", i.user)
            await i.response.send_message("✅ تم تصفير جميع التحذيرات لكل الإداريين.", ephemeral=True)
        btn_reset_all.callback = r_all_cb

        # زر التحكم بتحذيرات إداري محدد
        btn_manage_user_warn = Button(label="تعديل تحذيرات إداري محدد", style=discord.ButtonStyle.primary, emoji="👤")
        async def m_usr_w_cb(i):
            active_warned = [uid for uid, c in staff_warnings.items() if c > 0]
            if not active_warned:
                await i.response.send_message("لا يوجد إداريين لديهم تحذيرات حالياً.", ephemeral=True)
                return
            
            w_options = [discord.SelectOption(label=f"إداري ID: {uid} (التحذيرات: {staff_warnings[uid]})", value=str(uid)) for uid in active_warned]
            w_select = Select(placeholder="اختر الإداري...", options=w_options)
            
            async def w_sel_cb(inter):
                target_uid = int(w_select.values[0])
                
                sub_v = View()
                # تصفير الكل لهذا اللاعب
                btn_clear_single = Button(label="تصفير تحذيراته بالكامل", style=discord.ButtonStyle.danger)
                async def clr_s_cb(inter_i):
                    staff_warnings[target_uid] = 0
                    log_action(f"تصفير تحذيرات الإداري <@{target_uid}> بالكامل", inter_i.user)
                    await inter_i.response.send_message(f"✅ تم تصفير كافة تحذيرات الإداري <@{target_uid}>.", ephemeral=True)
                btn_clear_single.callback = clr_s_cb

                # خصم عدد محدد من التحذيرات
                btn_rem_specific = Button(label="خصم عدد محدد من التحذيرات", style=discord.ButtonStyle.secondary)
                async def rem_s_cb(inter_i):
                    await inter_i.response.send_modal(RemoveSpecificWarningModal(target_uid))
                btn_rem_specific.callback = rem_s_cb

                sub_v.add_item(btn_clear_single)
                sub_v.add_item(btn_rem_specific)
                await inter.response.send_message(f"التحكم بتحذيرات الإداري <@{target_uid}> (الحالية: {staff_warnings[target_uid]}):", view=sub_v, ephemeral=True)

            w_select.callback = w_sel_cb
            w_view = View()
            w_view.add_item(w_select)
            await i.response.send_message("اختر الإداري للتحكم بتحذيراته:", view=w_view, ephemeral=True)

        btn_manage_user_warn.callback = m_usr_w_cb

        view.add_item(btn_reset_all)
        view.add_item(btn_manage_user_warn)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # 5. الإعدادات
    @discord.ui.button(label="الإعدادات", style=discord.ButtonStyle.danger, emoji="⚙️", row=1)
    async def btn_settings(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ هذه اللوحة مخصصة للإدارة العليا فقط.", ephemeral=True)
            return

        maxed_users = [f"• <@{uid}> ({count}/{max_weekly_excuses})" for uid, count in staff_excuse_count.items() if count >= max_weekly_excuses]
        maxed_text = "\n".join(maxed_users) or "لا يوجد إداريين استنفذوا اعتذاراتهم"

        embed = discord.Embed(title="⚙️ إعدادات النظام والإعتذارات", color=discord.Color.dark_red())
        embed.add_field(name="📌 حد الاعتذارات الأسبوعي:", value=f"**{max_weekly_excuses}** اعتذارات", inline=False)
        embed.add_field(name="🚫 إداريين استنفذوا رصيد الاعتذارات:", value=maxed_text, inline=False)

        view = View()

        btn_set_max = Button(label="تعديل الحد الأقصى", style=discord.ButtonStyle.primary, emoji="✏️")
        async def set_max_cb(i):
            await i.response.send_modal(SetMaxExcusesModal())
        btn_set_max.callback = set_max_cb

        btn_reset_all_ex = Button(label="تصفير اعتذارات الجميع", style=discord.ButtonStyle.danger, emoji="🔄")
        async def r_ex_cb(i):
            staff_excuse_count.clear()
            log_action("تصفير سجل الاعتذارات الأسبوعية للجميع", i.user)
            await i.response.send_message("✅ تم تصفير سجل الاعتذارات الأسبوعية لجميع الإداريين.", ephemeral=True)
        btn_reset_all_ex.callback = r_ex_cb

        btn_reset_user_ex = Button(label="تصفير اعتذارات إداري محدد", style=discord.ButtonStyle.secondary, emoji="👤")
        async def r_usr_cb(i):
            u_options = [discord.SelectOption(label=f"إداري ID: {uid} ({cnt})", value=str(uid)) for uid, cnt in staff_excuse_count.items() if cnt > 0]
            if not u_options:
                await i.response.send_message("لا يوجد إداريين لديهم اعتذارات مسجلة.", ephemeral=True)
                return
            u_select = Select(placeholder="اختر الإداري لتصفير اعتذاراته...", options=u_options)
            async def u_sel_cb(inter):
                target = int(u_select.values[0])
                staff_excuse_count[target] = 0
                log_action(f"تصفير رصيد اعتذارات الإداري <@{target}>", inter.user)
                await inter.response.send_message(f"✅ تم تصفير اعتذارات الإداري <@{target}> بنجاح.", ephemeral=True)
            u_select.callback = u_sel_cb
            u_view = View()
            u_view.add_item(u_select)
            await i.response.send_message("اختر الإداري:", view=u_view, ephemeral=True)
        btn_reset_user_ex.callback = r_usr_cb

        view.add_item(btn_set_max)
        view.add_item(btn_reset_all_ex)
        view.add_item(btn_reset_user_ex)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # 6. سجل البوت (Audit Log)
    @discord.ui.button(label="سجل البوت", style=discord.ButtonStyle.secondary, emoji="📜", row=2)
    async def btn_audit_log(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ هذه اللوحة مخصصة للإدارة العليا فقط.", ephemeral=True)
            return

        logs_list = [entry["text"] for entry in bot_audit_logs[-15:]]
        log_text = "\n".join(logs_list) or "لا توجد عمليات مسجلة حديثاً في البوت."
        embed = discord.Embed(title="📜 سجل عمليات البوت الإدارية (تلقائي المسح كل 7 أيام)", description=log_text, color=discord.Color.dark_gray())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 7. إرسال رسالة (عادية أو Embed)
    @discord.ui.button(label="إرسال رسالة", style=discord.ButtonStyle.primary, emoji="📢", row=2)
    async def btn_send_msg(self, interaction: discord.Interaction, button: Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ هذه اللوحة مخصصة للإدارة العليا فقط.", ephemeral=True)
            return

        c_select = ChannelSelect(placeholder="اختر الروم لإرسال الرسالة فيها...", channel_types=[discord.ChannelType.text])
        
        async def c_sel_cb(inter):
            target_chan_id = c_select.values[0].id
            
            # خيارات نوع الرسالة
            type_view = View()
            btn_norm = Button(label="رسالة نصية عادية", style=discord.ButtonStyle.primary)
            async def norm_cb(i_n):
                await i_n.response.send_modal(SendNormalMessageModal(target_chan_id))
            btn_norm.callback = norm_cb

            btn_emb = Button(label="رسالة Embed (مثل الصورة)", style=discord.ButtonStyle.success)
            async def emb_cb(i_e):
                await i_e.response.send_modal(SendEmbedMessageModal(target_chan_id))
            btn_emb.callback = emb_cb

            type_view.add_item(btn_norm)
            type_view.add_item(btn_emb)

            await inter.response.send_message("اختر نوع الرسالة التي تريد إرسالها:", view=type_view, ephemeral=True)

        c_select.callback = c_sel_cb
        v = View()
        v.add_item(c_select)
        await interaction.response.send_message("اختر الروم المطلوب الإرسال فيها:", view=v, ephemeral=True)

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
                log_action(f"تسكين الإداري <@{uid}> في مناوبة `{shift['name']}`", interaction.user)
                await interaction.response.send_message(f"✅ تم تسكين <@{uid}> في مناوبة `{shift['name']}` بنجاح!", ephemeral=True)
            else:
                await interaction.response.send_message("ℹ️ الإداري مضاف بالفعل في هذه المناوبة.", ephemeral=True)
        else:
            if uid in shift["staff"]:
                shift["staff"].remove(uid)
                log_action(f"إلغاء تسكين الإداري <@{uid}> من مناوبة `{shift['name']}`", interaction.user)
                await interaction.response.send_message(f"🗑️ تم إلغاء تسكين <@{uid}> من مناوبة `{shift['name']}` بنجاح!", ephemeral=True)
            else:
                await interaction.response.send_message("ℹ️ الإداري غير موجود في هذه المناوبة.", ephemeral=True)

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
    now_egypt = datetime.datetime.now(EGYPT_TZ)
    now_str = now_egypt.strftime("%H:%M")
    
    channel = bot.get_channel(SHIFT_CHANNEL_ID)
    if not channel:
        return

    for s_id, data in list(shifts.items()):
        if data["time"] == now_str and not data.get("active_msg_id"):
            data["closed"] = False
            data["confirmed"] = []
            data["excuses"] = {}
            
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

            asyncio.create_task(handle_shift_timeout(s_id))

async def handle_shift_timeout(shift_id):
    await asyncio.sleep(600)  # 10 دقائق
    
    shift = shifts.get(shift_id)
    if not shift:
        return

    shift["closed"] = True
    await update_shift_embed(shift_id)

    warning_channel = bot.get_channel(WARNING_CHANNEL_ID)

    for uid in shift["staff"]:
        if uid in staff_vacations:
            continue
        
        if uid in shift["confirmed"]:
            continue

        ex = shift["excuses"].get(uid)
        if ex and ex["status"] == "مقبول":
            continue

        staff_warnings[uid] = staff_warnings.get(uid, 0) + 1
        count = staff_warnings[uid]

        if warning_channel:
            if count >= 3:
                await warning_channel.send(content=f"⚠️ <@&1541599646810374234> تنبيه عاجل! الإداري <@{uid}> وصل إلى **{count}** تحذيرات بسبب التغيب عن مناوبة `{shift['name']}`.")
            else:
                await warning_channel.send(content=f"⚠️ الإداري <@{uid}> حصل على تحذير ({count}/3) بسبب التغيب عن مناوبة `{shift['name']}`.")

    await asyncio.sleep(120)
    shift["active_msg_id"] = None
    shift["closed"] = False

# مهمة تنظيف السجلات الأقدم من 7 أيام تلقائياً
@tasks.loop(hours=6)
async def clear_old_audit_logs():
    now = datetime.datetime.now(EGYPT_TZ)
    global bot_audit_logs
    bot_audit_logs = [
        entry for entry in bot_audit_logs 
        if (now - entry["timestamp"]).days < 7
    ]

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
    embed.set_image(url=PANEL_IMAGE_URL)
    await ctx.send(embed=embed, view=MainSetupView())

@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}")
    if not shift_scheduler.is_running():
        shift_scheduler.start()
    if not clear_old_audit_logs.is_running():
        clear_old_audit_logs.start()

bot.run(os.getenv("DISCORD_TOKEN"))
