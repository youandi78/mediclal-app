import os
from datetime import datetime, timedelta
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

# データベースのモデル（保存する情報の形）
class StudyLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic = db.Column(db.String(200))
    question_data = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    next_review_date = db.Column(db.Date)

# Gemini設定
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    generation_config={
        "temperature": 0.7,
    }
)

PROMPT_TEMPLATE = """あなたは医師国家試験の作問者です。
以下の学習内容を基に、機序理解を問う問題を作成してください。

【学習内容】
{text}

【条件】
・単なる暗記問題にしない。病態の機序（なぜそうなるか）を問う。臨床推論を含める。
・5択。誤答選択肢にも誤りの理由を持たせる。
・形式は必ず以下を守ってください：
【問題】
【選択肢】A. B. C. D. E.
【正解】
【解説】
【国試での重要度】★1〜5
【この病態を説明せよ】(機序の核心を突く記述問題)
"""

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

@app.route('/')
def index():
    today = datetime.now().date()
    # 復習が必要な問題を取得
    reviews = StudyLog.query.filter(StudyLog.next_review_date <= today).all()
    today_count = len(reviews)

    # 週末（土日）判定と週次サマリーはそのまま
    is_weekend = datetime.now().weekday() >= 5
    week_summary = []
    if is_weekend:
        last_week = datetime.now() - timedelta(days=7)
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
        # 問題生成
        problem = model.generate_content(PROMPT_TEMPLATE.format(text=t))

        # 個別保存
        new_log = StudyLog(
            topic=t,
            question_data=problem.text,
            next_review_date=(datetime.now() + timedelta(days=1)).date()
        )
        db.session.add(new_log)

    db.session.commit()
    return redirect(url_for('index'))

with app.app_context():
    db.create_all()















