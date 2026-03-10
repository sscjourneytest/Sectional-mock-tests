import re
import json
import io
import html
from telegram import Update, Document, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler
)

# ================= CONFIG =================
# ⚠️ REPLACE THIS WITH YOUR NEW BOT TOKEN
TOKEN = "8621127325:AAHDJdevDsLPUA5dJYq4AWyZ7xjrgaQiq4Y"

# ================= SESSION =================
user_sessions = {}

def reset_session(uid):
    user_sessions[uid] = {
        "step": "SECTION_CHOICE",
        "quiz_title": None,
        "quiz_id": None,
        "correct_score": None,
        "negative_score": None,
        "timer_min": None,
        "raw_text": "",
        "mode": None,
        "section_type": None,
        "sections_config": [] # List of dicts: {name, start, end, time}
    }

# ================= RECONSTRUCTION REGEX (COPIED EXACTLY) =================
OPTION_D_END = re.compile(r'^\s*(?:\(?d\)?[\.)])\s*')
NEW_QUESTION_START = re.compile(r'^\s*Q\.\s*\d+', re.I)
HI_MARK = '"Hi":'

# ================= MCQ SPLITTER (COPIED EXACTLY) =================
def split_mcqs(text):
    lines = text.splitlines()
    mcqs = []
    current = []

    q_start = re.compile(r'^\s*Q\.\s*\d+', re.I)

    for line in lines:
        if q_start.match(line.strip()):
            if current:
                mcqs.append("\n".join(current).strip())
                current = []
        current.append(line)

    if current:
        mcqs.append("\n".join(current).strip())

    return [m for m in mcqs if m.strip()]

# ================= HTML ESCAPE (COPIED EXACTLY) =================
def esc(txt):
    return (
        txt.replace("&", "&amp;")
           .replace("<", "&lt;")
           .replace(">", "&gt;")
           .replace("&lt;br&gt;", "<br>")
    )

def parse_mcq(mcq, idx, session, current_quiz_id, c_score, n_score):
    lines = mcq.splitlines()

    q_en = []
    q_hi = []
    opts = {}
    answer = None
    sol_en = []
    sol_hi = []

    q_start = re.compile(r'^\s*Q\.\s*\d+', re.I)
    qnum_clean = re.compile(r'^\s*Q\.\s*\d+\s*', re.I)

    opt_pat = re.compile(r'^\s*(?:\(([a-d])\)|([a-d])\))\s*(.*)')
    ans_pat = re.compile(r'Answer:\s*\(?([a-d])\)?')
    exp_pat = re.compile(r'^\s*Explanation\s*:\s*(.*)', re.I)

    current_option = None
    in_explanation = False
    current_lang = "en"

    for line in lines:
        raw = line.rstrip()
        stripped = raw.strip()

        if stripped.startswith(HI_MARK):
            current_lang = "hi"
            content = stripped.replace(HI_MARK, "").strip()
            if content:
                if current_option:
                    opts[current_option]["hi"] += ("<br>" if opts[current_option]["hi"] else "") + content
                elif in_explanation:
                    sol_hi.append(content)
                else:
                    q_hi.append(content)
            continue

        if q_start.match(stripped):
            q_en.append(qnum_clean.sub("", raw))
            continue

        m_ans = ans_pat.match(stripped)
        if m_ans:
            answer = m_ans.group(1).lower()
            current_option = None
            in_explanation = False
            current_lang = "en"
            continue

        m_exp = exp_pat.match(stripped)
        if m_exp:
            in_explanation = True
            current_lang = "en"
            sol_en.append(m_exp.group(1))
            current_option = None
            continue

        if in_explanation:
            if stripped:
                (sol_hi if current_lang == "hi" else sol_en).append(stripped)
            continue

        m_opt = opt_pat.match(stripped)
        if m_opt:
            key = (m_opt.group(1) or m_opt.group(2)).lower()
            current_option = key
            current_lang = "en"
            opts[key] = {"en": m_opt.group(3).strip(), "hi": ""}
            continue

        if current_option and stripped:
            opts[current_option][current_lang] += "<br>" + stripped
            continue

        (q_hi if current_lang == "hi" else q_en).append(raw)

    if len(opts) != 4 or answer not in opts:
        raise ValueError("Invalid MCQ format")

    return {
        "answer": str("abcd".index(answer) + 1),
        "correct_score": str(c_score),
        "deleted": "0",
        "difficulty_level": "0",
        "id": str(50000 + idx),
        "negative_score": str(n_score),
        "option_1": {"en": esc(opts["a"]["en"]), "hi": esc(opts["a"]["hi"])},
        "option_2": {"en": esc(opts["b"]["en"]), "hi": esc(opts["b"]["hi"])},
        "option_3": {"en": esc(opts["c"]["en"]), "hi": esc(opts["c"]["hi"])},
        "option_4": {"en": esc(opts["d"]["en"]), "hi": esc(opts["d"]["hi"])},
        "option_5": "",
        "option_image_1": "", "option_image_2": "", "option_image_3": "", "option_image_4": "", "option_image_5": "",
        "question": {"en": esc("<br>".join(q_en)), "hi": esc("<br>".join(q_hi))},
        "question_image": "",
        "quiz_id": current_quiz_id,
        "solution_heading": "", "solution_image": "",
        "solution_text": {"en": esc("<br>".join(sol_en)), "hi": esc("<br>".join(sol_hi))},
        "solution_video": "",
        "sortingparam": "0.00"
    }

# ================= COMMANDS =================
async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_session(update.effective_user.id)
    keyboard = [
        [InlineKeyboardButton("Default Sections", callback_data="sec_default")],
        [InlineKeyboardButton("Manual Sections", callback_data="sec_manual")]
    ]
    await update.message.reply_text("🚀 Sectional Quiz Mode\nChoose Section Method:", reply_markup=InlineKeyboardMarkup(keyboard))

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Force delete the key from the dictionary to clear memory
    if uid in user_sessions:
        del user_sessions[uid]

    # Initialize a fresh session
    reset_session(uid)

    await update.message.reply_text("🔄 **Session Cleared.**\nAll previous data has been deleted. Send /quiz to start a fresh session.")

async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    session = user_sessions.get(uid)
    if not session or session["step"] != "MCQS": return

    all_mcqs = split_mcqs(session["raw_text"])

    total_secs = len(session["sections_config"])
    await update.message.reply_text(f"⚙️ <b>Processing {total_secs} sections... Please wait.</b>", parse_mode="HTML")

    for index, sec in enumerate(session["sections_config"], start=1):
        sec_name = sec['name']
        start_idx = sec['start'] - 1
        end_idx = sec['end']

        sec_mcqs = all_mcqs[start_idx:end_idx]
        if not sec_mcqs: continue

        # Format specific IDs and Titles
        current_title = f"{session['quiz_title']} {sec_name}"
        current_id = f"{session['quiz_id']}-{sec_name.replace(' ', '')}"

        parsed_objs = []
        for i, m in enumerate(sec_mcqs, start=1):
            try:
                obj = parse_mcq(m, i, session, current_id, sec['pos'], sec['neg'])
                parsed_objs.append(obj)
            except Exception as e:
                await update.message.reply_text(f"❌ Error in {sec_name} Q{i}: {str(e)}")
                return

        # Prepare JSON with "sections" structure
        final_data = {
            "meta": {
                "title": current_title,
                "id": current_id,
                "total_questions": len(parsed_objs),
                "correct_score": str(sec['pos']),
                "negative_score": str(sec['neg']),
                "timer_minutes": str(sec['time']),
                "timer_seconds": int(sec['time'] * 60)
            },
            "sections": {
                sec_name: parsed_objs
            }
        }

        json_str = json.dumps(final_data, indent=2, ensure_ascii=False)
        file_name = f"{current_id}.json"

        # Caption updated as per requirements
        caption = (
            f"✅ <b>JSON Generated Successfully! ({index}/{total_secs})</b>\n\n"
            f"📌 <b>Quiz Title:</b> {current_title}\n"
            f"🆔 <b>Quiz ID:</b> {current_id}\n"
            f"📊 <b>Total Questions:</b> {len(parsed_objs)}\n"
            f"⏱️ <b>Total Time:</b> {sec['time']} mins\n"
            f"➕ <b>Positive Mark:</b> {sec['pos']}\n"
            f"➖ <b>Negative Mark:</b> {sec['neg']}"
        )

        await update.message.reply_document(
            document=io.BytesIO(json_str.encode("utf-8")),
            filename=file_name,
            caption=caption,
            parse_mode="HTML"
        )

        # 7. Generate Updated Website HTML Snippet for this section
        snippet = (
            f'<div class="quiz" data-type="paid">\n'
            f'    <div class="quiz-left">\n'
            f'      <div class="quiz-title">{current_title} <span class="quiz-badge badge-paid">PAID</span></div>\n'
            f'      <div class="quiz-info">{len(parsed_objs)} Questions • {sec["time"]} Min</div>\n'
            f'    </div>\n'
            f'    <div class="action-area">\n'
            f'        <a href="full_test.html?id={current_id}" class="start-btn">START</a>\n'
            f'    </div>\n'
            f'</div>'
        )

        # ✅ ESCAPE THE HTML CODE SO TELEGRAM SENDS IT AS TEXT
        escaped_code = html.escape(snippet)

        # 8. Send the Snippet Message for this specific section
        await update.message.reply_text(
            f"📋 <b>Website Code Snippet for {sec_name}:</b>\n\n"
            f"<pre><code class='language-html'>{escaped_code}</code></pre>",
            parse_mode="HTML"
        )

    reset_session(uid)
    await update.message.reply_text("🏁 <b>All sections processed successfully!</b>", parse_mode="HTML")
# ================= HANDLERS =================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    session = user_sessions.get(uid)
    if not session: return
    await query.answer()

    if query.data == "sec_default":
        session["sections_config"] = [
            {"name": "REASONING", "start": 1, "end": 25, "time": 20, "pos": 2, "neg": 0.5},
            {"name": "GK", "start": 26, "end": 50, "time": 10, "pos": 2, "neg": 0.5},
            {"name": "MATHS", "start": 51, "end": 75, "time": 25, "pos": 2, "neg": 0.5},
            {"name": "ENGLISH", "start": 76, "end": 100, "time": 15, "pos": 2, "neg": 0.5}
        ]
        session["step"] = "TITLE"
        await query.edit_message_text("✅ Default (2, 0.5) selected.\nSend **Common Quiz Title**.")

    elif query.data == "sec_manual":
        session["step"] = "MANUAL_INPUT"
        await query.edit_message_text("Send sections in format:\nName(start-end)-min-pos-neg\n\nExample:\nREASONING(1-25)-20-2-0.5\nGK(26-50)-10-2-0.5")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    session = user_sessions.get(uid)
    if not session: return
    text = update.message.text.strip()

    if session["step"] == "MANUAL_INPUT":
        # Regex to parse: Name(1-25)-20-2-0.5
        lines = text.splitlines()
        for line in lines:
            m = re.match(r'(.+?)\((\d+)-(\d+)\)-(\d+)-([\d\.]+)-([\d\.]+)', line)
            if m:
                session["sections_config"].append({
                    "name": m.group(1).strip(),
                    "start": int(m.group(2)), "end": int(m.group(3)),
                    "time": int(m.group(4)), "pos": float(m.group(5)), "neg": float(m.group(6))
                })
        session["step"] = "TITLE"
        await update.message.reply_text("✅ Manual Sections Saved.\nSend **Common Quiz Title**.")

    elif session["step"] == "TITLE":
        session["quiz_title"] = text
        session["step"] = "ID"
        await update.message.reply_text("Send **Common Quiz ID**.")

    elif session["step"] == "ID":
        session["quiz_id"] = text.replace(" ", "")
        session["step"] = "MCQS"
        await update.message.reply_text("Now send the SINGLE file containing ALL 100 questions (or as per config).\nSend /done when finished.")

    elif session["step"] == "MCQS":
        session["raw_text"] += "\n" + text

async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    session = user_sessions.get(uid)
    if not session or session["step"] != "MCQS": return
    doc = update.message.document
    file = await doc.get_file()
    content = (await file.download_as_bytearray()).decode("utf-8")
    session["raw_text"] += "\n" + content
    await update.message.reply_text("📄 File added. Send /done to process.")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("quiz", quiz_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd)) # Add this line
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Document.TEXT | filters.Document.MimeType("text/plain"), file_handler))
    print("Sectional Bot Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
