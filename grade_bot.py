import logging
import os
import re
import sqlite3 # <--- Используем встроенную, простую базу данных
import datetime
from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    ReplyKeyboardRemove,
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackQueryHandler,
)

# --- 1. ТҰРАҚТЫЛАР ЖӘНЕ КОНФИГУРАЦИЯ ---

# --- ВАЖНО: Настройка Секретов ---
TOKEN = os.environ.get('BOT_TOKEN')

# --- ВАЖНО: Путь к файлу Базы Данных ---
# Это специальный путь к "Диску", который мы создали в Шаге 2
# Render "приклеит" наш диск к папке /var/data
DB_PATH = "/var/data/grades.db" 

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

GRADE, SUBJECT = range(2) 
REGISTER_FIO = 2 
SELECT_MONTH = 3 

AVAILABLE_SUBJECTS = [
    "Математика", "Қазақ тілі", "Физика", 
    "Тарих", "Информатика", "Дене шынықтыру"
]

MONTH_NAMES = {
    "09": "Қыркүйек", "10": "Қазан", "11": "Қараша", "12": "Желтоқсан",
    "01": "Қаңтар", "02": "Ақпан", "03": "Наурыз", "04": "Сәуір", "05": "Мамыр",
}
CURRENT_YEAR = str(datetime.datetime.now().year)
SUBJECT_REGEX = '^(' + '|'.join(map(re.escape, AVAILABLE_SUBJECTS)) + ')$'

# --- 2. UI КЛАВИАТУРАЛАРЫ ---
UNREGISTERED_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("✅ Тіркелу")]], resize_keyboard=True
)
REGISTERED_KEYBOARD = ReplyKeyboardMarkup(
    [["📝 Баға енгізу"], ["📊 Бағаларды көру"]], resize_keyboard=True
)

# --- 3. SQLite БАЗА ДАННЫХ (Реальный Код) ---

def init_db():
    """Создает таблицы в базе данных, если их еще нет."""
    try:
        # Убедимся, что папка /var/data существует (на всякий случай)
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        
        # Создаем таблицу пользователей
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                fio TEXT NOT NULL,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Создаем таблицу оценок
        cur.execute('''
            CREATE TABLE IF NOT EXISTS grades (
                grade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id BIGINT REFERENCES users(user_id),
                subject TEXT NOT NULL,
                grade INTEGER NOT NULL,
                created_at DATE NOT NULL DEFAULT CURRENT_DATE
            )
        ''')
        con.commit()
    except sqlite3.Error as e:
        logging.error(f"Database initialization error: {e}")
    finally:
        if con:
            con.close()

def check_user_registration(user_id: int) -> bool:
    """Проверяет, есть ли user_id в таблице users."""
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        result = cur.fetchone()
        return result is not None
    except sqlite3.Error as e:
        logging.error(f"DB check_user_registration error: {e}")
        return False
    finally:
        if con:
            con.close()

def register_user_in_db(user_id: int, fio: str) -> bool:
    """Сохраняет нового пользователя в таблицу users."""
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("INSERT INTO users (user_id, fio) VALUES (?, ?)", (user_id, fio))
        con.commit()
        return True
    except sqlite3.Error as e:
        logging.error(f"DB register_user_in_db error: {e}")
        return False
    finally:
        if con:
            con.close()

def save_grade_to_db(user_id: int, subject: str, grade: int) -> bool:
    """Сохраняет оценку в таблицу grades."""
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute(
            "INSERT INTO grades (user_id, subject, grade) VALUES (?, ?, ?)",
            (user_id, subject, grade)
        )
        con.commit()
        return True
    except sqlite3.Error as e:
        logging.error(f"DB save_grade_to_db error: {e}")
        return False
    finally:
        if con:
            con.close()

def fetch_grades_from_db(user_id: int, month: str = 'All') -> dict[str, list[int]]:
    """Получает оценки пользователя, фильтруя по месяцу (YYYY-MM)."""
    grades_by_subject = {}
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        
        query = "SELECT subject, grade FROM grades WHERE user_id = ?"
        params = [user_id]
        
        if month != 'All':
            # Используем 'strftime' (это как TO_CHAR в SQLite)
            query += " AND strftime('%Y-%m', created_at) = ?"
            params.append(month)
        
        cur.execute(query, tuple(params))
        all_grades = cur.fetchall()
        
        for subject, grade in all_grades:
            if subject not in grades_by_subject:
                grades_by_subject[subject] = []
            grades_by_subject[subject].append(grade)
            
    except sqlite3.Error as e:
        logging.error(f"DB fetch_grades_from_db error: {e}")
    
    return grades_by_subject

# --- 4. Есептеу Логикасы (Өзгеріссіз) ---
def convert_to_5_point(average_grade: float) -> str:
    if average_grade >= 90: return "5 (Үздік)"
    elif average_grade >= 70: return "4 (Жақсы)"
    elif average_grade >= 50: return "3 (Қанағаттанарлық)"
    else: return "2 (Қанағаттанарлықсыз)"

def generate_grades_report(user_grades_by_subject: dict[str, list[int]], month_label: str) -> str:
    if month_label != 'All':
        month_name = MONTH_NAMES.get(month_label.split('-')[1], month_label)
        title = f"📊 **{month_name} айы бойынша есеп:**"
    else:
        title = "📊 **Барлық айлар бойынша ЖАЛПЫ есеп:**"
    response_text = title
    total_sum = 0
    total_count = 0
    for subject, grades_list in user_grades_by_subject.items():
        if not grades_list: continue
        subject_sum = sum(grades_list)
        subject_count = len(grades_list)
        subject_avg = subject_sum / subject_count
        subject_5_point = convert_to_5_point(subject_avg)
        response_text += f"\n\n**{subject}:**"
        response_text += f"\n  Бағалары: {', '.join(map(str, grades_list))}"
        response_text += f"\n  Орташа баға (100): **{subject_avg:.2f}**"
        response_text += f"\n  5-балдың жүйе: **{subject_5_point}**"
        total_sum += subject_sum
        total_count += subject_count
    if total_count > 0:
        overall_avg = total_sum / total_count
        overall_5_point = convert_to_5_point(overall_avg)
        response_text += "\n\n---"
        response_text += f"\n**ЖАЛПЫ ОРТАША БАҒА:**"
        response_text += f"\n  Орташа баға (100): **{overall_avg:.2f}**"
        response_text += f"\n  5-балдың жүйе: **{overall_5_point}**"
    return response_text

# --- 5. КОМАНДАЛАР ЖӘНЕ ХЭНДЛЕРЛЕР (Handlers) ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    if check_user_registration(user_id):
        response_text = (f"Сәлем, {user.first_name}! 👋 Сіз тіркелгенсіз.\n"
                         "Негізгі әрекеттерді төмендегі кнопкалар арқылы орындаңыз.")
        reply_markup = REGISTERED_KEYBOARD
    else:
        response_text = (f"Сәлем, {user.first_name}! 👋\n"
                         "Мен сіздің бағаларыңызды есептеуге арналған ботпын.\n\n"
                         "⚠️ Жалғастыру үшін, **Тіркелу** кнопкасын басыңыз.")
        reply_markup = UNREGISTERED_KEYBOARD
    await update.message.reply_html(response_text, reply_markup=reply_markup)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start_command(update, context)
async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if check_user_registration(user_id):
        await update.message.reply_text("✅ Сіз бұрын тіркелгенсіз!", reply_markup=REGISTERED_KEYBOARD)
        return ConversationHandler.END
    await update.message.reply_text(
        "📝 **Тіркелу:** Өтінемін, толық аты-жөніңізді (Ф.И.О.) енгізіңіз. Мысалы: **Ахметов Әли Аманұлы**",
        reply_markup=ReplyKeyboardRemove()
    )
    return REGISTER_FIO
async def get_fio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    fio = update.message.text.strip()
    user_id = update.effective_user.id
    if not re.fullmatch(r"^[А-Яа-яЁёІіҢңҒғҮүҰұҚқӨөӘә\s\-]{5,100}$", fio) or len(fio.split()) < 2:
        await update.message.reply_text(
            "❌ **Қате!** Аты-жөніңізді дұрыс енгізіңіз (тек кириллица, кемінде 2 сөз).\n"
            "Мысалы: **Ахметов Әли**"
        )
        return REGISTER_FIO
    if register_user_in_db(user_id, fio):
        await update.message.reply_text(
            f"🎉 **Сәтті тіркелдіңіз!**\nТіркелген аты-жөніңіз: **{fio}**",
            reply_markup=REGISTERED_KEYBOARD
        )
    else:
        await update.message.reply_text("❌ **Тіркеу қатесі.** Деректер қорымен байланыста проблема туындады.")
    return ConversationHandler.END
async def add_grade_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not check_user_registration(update.effective_user.id):
        await update.message.reply_text("⚠️ **Алдымен тіркелу қажет!** /register командасын қолданыңыз.")
        return ConversationHandler.END
    keyboard_buttons = []
    for i in range(0, len(AVAILABLE_SUBJECTS), 2):
        row = [KeyboardButton(s) for s in AVAILABLE_SUBJECTS[i:i+2]]
        keyboard_buttons.append(row)
    reply_markup = ReplyKeyboardMarkup(
        keyboard_buttons, resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        "Қандай **сабақтан** баға енгізесіз? Төмендегі кнопкалардан **тек таңдаңыз**:",
        reply_markup=reply_markup,
    )
    return SUBJECT
async def get_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    subject = update.message.text
    context.user_data['subject'] = subject 
    await update.message.reply_text(
        f"Жақсы. **{subject}** сабағынан қандай **баға** алдыңыз?\n"
        "Бағаны **0-100** аралығындағы санмен енгізіңіз:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return GRADE
async def get_grade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    grade_text = update.message.text
    try:
        grade = int(grade_text)
        if not (0 <= grade <= 100):
            await update.message.reply_text("❌ Баға **0 мен 100** аралығындағы сан болуы керек. Қайта енгізіңіз:")
            return GRADE
    except ValueError:
        await update.message.reply_text("❌ Бағаны дұрыс санмен енгізіңіз (мысалы, 75 немесе 92).")
        return GRADE
    subject = context.user_data.get('subject')
    telegram_user_id = update.effective_user.id
    if save_grade_to_db(telegram_user_id, subject, grade):
        await update.message.reply_text(
            f"✅ **Баға сәтті сақталды.**\nСабақ: **{subject}**, Баға: **{grade}** (100-балл)",
            reply_markup=REGISTERED_KEYBOARD
        )
    else:
         await update.message.reply_text(
            "❌ **Сақтау қатесі.** Деректер қорымен байланыста проблема туындады.",
            reply_markup=REGISTERED_KEYBOARD 
        )
    context.user_data.clear()
    return ConversationHandler.END
async def show_grades_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not check_user_registration(update.effective_user.id):
        await update.message.reply_text("⚠️ Бағаларды көру үшін, алдымен /register арқылы тіркеліңіз.", reply_markup=UNREGISTERED_KEYBOARD)
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton("📊 Барлық айлар", callback_data='month_All')],]
    month_buttons = []
    for num, name in MONTH_NAMES.items():
        month_buttons.append(InlineKeyboardButton(name, callback_data=f'month_{CURRENT_YEAR}-{num}'))
    for i in range(0, len(month_buttons), 3):
        keyboard.append(month_buttons[i:i+3])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Бағаларды қай ай бойынша көргіңіз келеді?', reply_markup=reply_markup)
    return SELECT_MONTH
async def handle_month_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    month_data = query.data.replace('month_', '') 
    user_id = query.from_user.id
    user_grades_by_subject = fetch_grades_from_db(user_id, month=month_data)
    if not user_grades_by_subject:
        month_name = MONTH_NAMES.get(month_data.split('-')[1], month_data) if month_data != 'All' else 'Барлық айларда'
        await query.edit_message_text(f"❌ {month_name} сіздің бағаларыңыз әлі енгізілмеген.")
        return ConversationHandler.END
    response_text = generate_grades_report(user_grades_by_subject, month_data)
    await query.edit_message_text(response_text)
    return ConversationHandler.END
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('Әрекет тоқтатылды.', reply_markup=REGISTERED_KEYBOARD)
    context.user_data.clear()
    return ConversationHandler.END
async def cancel_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text('Ағымдағы әрекет тоқтатылды. Басты мәзірге ораламыз.')
    await start_command(update, context)
    return ConversationHandler.END

# --- 6. НЕГІЗГІ ФУНКЦИЯ (main) ---
def main() -> None:
    """Ботты іске қосады."""
    if not TOKEN:
        logging.critical("BOT_TOKEN environment variable not found!")
        return
        
    # --- ВАЖНО: Мы запускаем init_db() при старте ---
    # Это создаст файл /var/data/grades.db и таблицы, если их нет
    init_db()

    application = Application.builder().token(TOKEN).build()
    
    register_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("register", register_start), MessageHandler(filters.TEXT & filters.Regex('^✅ Тіркелу$'), register_start)],
        states={REGISTER_FIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_fio)]},
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel_and_start)],
        allow_reentry=True,
    )
    grade_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_grade", add_grade_start), MessageHandler(filters.TEXT & filters.Regex('^📝 Баға енгізу$'), add_grade_start)],
        states={
            SUBJECT: [MessageHandler(filters.TEXT & filters.Regex(SUBJECT_REGEX), get_subject)],
            GRADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_grade)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel_and_start)],
        allow_reentry=True,
    )
    show_grades_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("show_grades", show_grades_start), MessageHandler(filters.TEXT & filters.Regex('^📊 Бағаларды көру$'), show_grades_start)],
        states={SELECT_MONTH: [CallbackQueryHandler(handle_month_selection, pattern='^month_')]},
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel_and_start)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(register_conv_handler) 
    application.add_handler(grade_conv_handler)
    application.add_handler(show_grades_conv_handler) 
    
    async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        reply_markup = UNREGISTERED_KEYBOARD
        if check_user_registration(update.effective_user.id):
            reply_markup = REGISTERED_KEYBOARD
        await update.message.reply_text("Кешіріңіз, мен бұл хабарламаны түсінбедім. /start немесе кнопкаларды қолданыңыз.", reply_markup=reply_markup)
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_command))

    print("🚀 Бот іске қосылды! Тоқтату үшін Ctrl+C басыңыз.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
