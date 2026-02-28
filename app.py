import os
import json
from datetime import datetime, timedelta, timezone
import google.generativeai as genai
from flask import Flask, render_template, request, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")
# Render上で設定するデータベースのURL
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
# ★ 日本時間（JST）を定義
JST = timezone(timedelta(hours=9))

# データベースのモデル（保存する情報の形）
class StudyLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    topic = db.Column(db.String(200))
    question_data = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    next_review_date = db.Column(db.Date)

    repetition = db.Column(db.Integer, default=0)
    interval = db.Column(db.Integer, default=0)
    ease_factor = db.Column(db.Float, default=2.5)

# Gemini設定
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config={
        "temperature": 0.7,
    }
)

PROMPT_TEMPLATE = """
以下のテーマから医師国家試験レベルの機序理解5択問題を1問作成せよ。

テーマ：
{text}

【最重要指示】
「imagery_hint」には、病態の核心を直感的に理解するための「言語化されたイメージ」を必ず含めること。
例：
- COPDなら「弾力を失って膨らみっぱなしの、空気が抜けない風船」
- ネフローゼなら「穴が大きくなりすぎて、豆（蛋白）が漏れ出すコーヒーフィルター」
- 大動脈解離なら「高圧洗浄機のホースの内側が剥がれて、水の通り道が二つになった状態」

出力は必ずJSONのみ。説明文や前置きは一切不要。

{
  "question": "問題文",
  "choices": [
    "選択肢1",
    "選択肢2",
    "選択肢3",
    "選択肢4",
    "選択肢5"
  ],
  "answer_index": 0,
  "explanations": [
    "選択肢1の解説",
    "選択肢2の解説",
    "選択肢3の解説",
    "選択肢4の解説",
    "選択肢5の解説"
  ],
  "core_mechanism": "病態機序の核心説明"
}
"""
def sm2_update(log, quality):
    if quality < 3:
        log.repetition = 0
        log.interval = 1
    else:
        if log.repetition == 0:
            log.interval = 1
        elif log.repetition == 1:
            log.interval = 6
        else:
            log.interval = round(log.interval * log.ease_factor)

        log.repetition += 1

    log.ease_factor += (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if log.ease_factor < 1.3:
        log.ease_factor = 1.3

    log.next_review_date = datetime.now(JST).date() + timedelta(days=log.interval)

# パスワード保護
@app.before_request
def check_auth():
    if 'logged_in' not in session and request.endpoint not in ['login', 'static']:
        return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == os.getenv("APP_PASSWORD", "med123"):
            session['logged_in'] = True
            return redirect(url_for('index'))
    return '<h1>Password:</h1><form method="post"><input type="password" name="password"><button>Login</button></form>'

@app.route('/quiz/<int:q_id>')
def quiz(q_id):
    log = StudyLog.query.get_or_404(q_id)
    data = json.loads(log.question_data)
    return render_template('quiz.html', log=log, q=data)
    
@app.route('/answer/<int:q_id>', methods=['POST'])
def answer(q_id):
    log = StudyLog.query.get_or_404(q_id)
    data = json.loads(log.question_data)

    selected = int(request.form.get('selected'))
    correct = selected == data["answer_index"]

    quality = 5 if correct else 2

    sm2_update(log, quality)
    db.session.commit()

    return render_template(
        "result.html",
        correct=correct,
        explanations=data["explanations"],
        correct_index=data["answer_index"]
    )
    
@app.route('/')
def index():
    today = datetime.now(JST).date()
    # 復習が必要な問題を取得
    reviews = StudyLog.query.filter(StudyLog.next_review_date <= today).all()
    today_count = len(reviews)

    # 週末（土日）判定と週次サマリーはそのまま
    is_weekend = datetime.now(JST).weekday() >= 5
    week_summary = []
    if is_weekend:
        last_week = datetime.now(JST) - timedelta(days=7)
        week_summary = StudyLog.query.filter(StudyLog.created_at >= last_week).all()

    # 連続学習日数（簡易的にsessionで管理）
    last_login_date = session.get('last_login_date')
    streak = session.get('streak', 0)
    if last_login_date == str(today - timedelta(days=1)):
        streak += 1
    else:
        streak = 1
    session['last_login_date'] = str(today)
    session['streak'] = streak

    return render_template(
        'index.html',
        reviews=reviews,
        weekend=is_weekend,
        summary=week_summary,
        today=today,
        today_count=today_count,
        streak=streak
    )
@app.route('/generate', methods=['POST'])
def generate():
    topic = request.form.get('topic')
    if not topic: 
        return redirect(url_for('index'))

    # ← ここでカンマ・改行で分割
    topics = [t.strip() for t in topic.replace("\n", ",").split(",") if t.strip()]

    for t in topics:
        problem = model.generate_content(PROMPT_TEMPLATE.format(text=t))
    data = json.loads(problem.text)

    new_log = StudyLog(
        topic=t,
        question_data=json.dumps(data),
        next_review_date=datetime.now(JST).date()
    )

    db.session.add(new_log)

    db.session.commit()
    return redirect(url_for('index'))

with app.app_context():
    db.create_all()
























