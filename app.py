import os
import re
import time
import json
import datetime
import pytz
import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from openai import OpenAI

# タイムゾーン設定（日本時間）
JST = pytz.timezone('Asia/Tokyo')
CSV_FILE = "trade_history.csv"
CONFIG_FILE = "config.json"
POSITION_FILE = "position.json"

# ページ設定
st.set_page_config(page_title="FX AI 仮想自動売買モニター", layout="wide")

# OpenAI API Key設定
api_key = os.environ.get("OPENAI_API_KEY", "")
if not api_key and "OPENAI_API_KEY" in st.secrets:
    api_key = st.secrets["OPENAI_API_KEY"]

# ---------------------------------------------------------
# 💾 設定・データファイルの読み書き関数
# ---------------------------------------------------------
def load_config():
    default_config = {
        "symbol": "USDJPY=X",
        "min_pips": 10.0,
        "lot_size": 1.0,
        "refresh_rate": 1
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                default_config.update(config)
        except Exception:
            pass
    return default_config

def save_config(config_data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def load_position():
    if os.path.exists(POSITION_FILE):
        try:
            with open(POSITION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_position(pos_data):
    try:
        if pos_data is None:
            if os.path.exists(POSITION_FILE):
                os.remove(POSITION_FILE)
        else:
            with open(POSITION_FILE, "w", encoding="utf-8") as f:
                json.dump(pos_data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def load_history_from_csv():
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE)
            history = df.to_dict('records')
            total_pips = sum(float(str(row["獲得pips"]).replace("+", "")) for row in history if "獲得pips" in row)
            return history, total_pips
        except Exception:
            return [], 0.0
    return [], 0.0

# 保存されている設定の読み込み
saved_config = load_config()

# ---------------------------------------------------------
# ⚙️ サイドバー（設定入力）
# ---------------------------------------------------------
st.sidebar.title("⚙️ 仮想トレード設定")

symbol = st.sidebar.text_input("通貨ペア", saved_config["symbol"])
min_pips = st.sidebar.number_input("最小ボラティリティ (pips)", value=float(saved_config["min_pips"]), step=1.0)
lot_size = st.sidebar.number_input("取引数量 (万通貨)", value=float(saved_config["lot_size"]), step=0.1)
refresh_rate = st.sidebar.slider("更新間隔 (秒)", min_value=1, max_value=15, value=int(saved_config["refresh_rate"]))

# 設定値が変更されたら自動保存
current_config = {
    "symbol": symbol,
    "min_pips": min_pips,
    "lot_size": lot_size,
    "refresh_rate": refresh_rate
}
if current_config != saved_config:
    save_config(current_config)

# リセット機能
if st.sidebar.button("🗑️ 取引履歴＆設定をリセット"):
    for file_path in [CSV_FILE, CONFIG_FILE, POSITION_FILE]:
        if os.path.exists(file_path):
            os.remove(file_path)
    st.session_state.trade_history = []
    st.session_state.total_pnl_pips = 0.0
    st.session_state.position = None
    st.sidebar.success("データと設定をすべてリセットしました！")
    st.rerun()

if not api_key:
    user_api_key = st.sidebar.text_input("OpenAI API Key", type="password")
    if user_api_key:
        api_key = user_api_key

client = OpenAI(api_key=api_key) if api_key else None

# ---------------------------------------------------------
# 📊 データ取得 & 分析ロジック
# ---------------------------------------------------------
@st.cache_data(ttl=1)
def load_data(sym):
    ticker = yf.Ticker(sym)
    df_htf = ticker.history(period="7d", interval="1h")
    df_ltf = ticker.history(period="1d", interval="5m")
    
    if not df_htf.empty:
        df_htf.index = df_htf.index.tz_convert(JST)
    if not df_ltf.empty:
        df_ltf.index = df_ltf.index.tz_convert(JST)
        
    return df_htf, df_ltf

def analyze(df_htf, df_ltf, threshold_pips):
    df_htf['sma20'] = df_htf['Close'].rolling(window=20).mean()
    htf_trend = "UP" if df_htf['Close'].iloc[-1] > df_htf['sma20'].iloc[-1] else "DOWN"

    df_ltf['sma20'] = df_ltf['Close'].rolling(window=20).mean()
    df_ltf['high_max'] = df_ltf['High'].rolling(10).max()
    df_ltf['low_min'] = df_ltf['Low'].rolling(10).min()

    current_close = df_ltf['Close'].iloc[-1]
    prev_high = df_ltf['high_max'].iloc[-2]
    prev_low = df_ltf['low_min'].iloc[-2]

    pips_range = (prev_high - prev_low) * 100

    if pips_range < threshold_pips:
        return "HOLD", f"ボラティリティ不足 ({pips_range:.1f} pips < 閾値{threshold_pips:.1f} pips)", pips_range, prev_high, prev_low

    if htf_trend == "UP" and current_close > prev_high:
        return "BUY", "H1上昇トレンド + M5高値ブレイク", pips_range, prev_high, prev_low
    elif htf_trend == "DOWN" and current_close < prev_low:
        return "SELL", "H1下降トレンド + M5安値ブレイク", pips_range, prev_high, prev_low

    return "HOLD", "静観（ブレイク条件未達成）", pips_range, prev_high, prev_low

def query_ai(signal, price, df_ltf, reason):
    if not client:
        tp = price + 0.15 if signal == "BUY" else price - 0.15
        sl = price - 0.10 if signal == "BUY" else price + 0.10
        return "APIキー未設定のためデフォルト値(TP:+15pips/SL:-10pips)を使用", tp, sl

    recent = df_ltf.tail(5)[['Open', 'High', 'Low', 'Close']].to_string()
    prompt = f"""
    FXスキャルピングの利確(TP)と損切(SL)を計算してください。
    通貨ペア: USD/JPY, シグナル: {signal}, 現在値: {price:.3f}
    直近5分足データ:
    {recent}

    【出力形式】
    TP: <数値>
    SL: <数値>
    理由: <簡潔に>
    """
    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    text = res.choices[0].message.content
    
    tp_val, sl_val = None, None
    tp_match = re.search(r"TP:\s*([0-9]+\.?[0-9]*)", text)
    sl_match = re.search(r"SL:\s*([0-9]+\.?[0-9]*)", text)
    if tp_match: tp_val = float(tp_match.group(1))
    if sl_match: sl_val = float(sl_match.group(1))

    return text, tp_val, sl_val

# --- 状態復元 ---
if "position" not in st.session_state:
    st.session_state.position = load_position()

if "trade_history" not in st.session_state or "total_pnl_pips" not in st.session_state:
    history, total_pips = load_history_from_csv()
    st.session_state.trade_history = history
    st.session_state.total_pnl_pips = total_pips

# データ取得
df_htf, df_ltf = load_data(symbol)
signal, reason, pips_range, prev_high, prev_low = analyze(df_htf, df_ltf, min_pips)
current_price = df_ltf['Close'].iloc[-1]
now_jst_str = datetime.datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')

# ---------------------------------------------------------
# 🤖 仮想トレード実行エンジン (自動エントリー＆決済)
# ---------------------------------------------------------

# 1. 決済判定
if st.session_state.position is not None:
    pos = st.session_state.position
    pnl_pips = 0.0
    closed = False
    close_reason = ""

    if pos["side"] == "BUY":
        if current_price >= pos["tp"]:
            closed, close_reason = True, "🎯 利確 (TP到達)"
            pnl_pips = (pos["tp"] - pos["entry_price"]) * 100
        elif current_price <= pos["sl"]:
            closed, close_reason = True, "🛑 損切 (SL到達)"
            pnl_pips = (pos["sl"] - pos["entry_price"]) * 100
    elif pos["side"] == "SELL":
        if current_price <= pos["tp"]:
            closed, close_reason = True, "🎯 利確 (TP到達)"
            pnl_pips = (pos["entry_price"] - pos["tp"]) * 100
        elif current_price >= pos["sl"]:
            closed, close_reason = True, "🛑 損切 (SL到達)"
            pnl_pips = (pos["entry_price"] - pos["sl"]) * 100

    if closed:
        pnl_jpy = pnl_pips * 100 * lot_size
        st.session_state.total_pnl_pips += pnl_pips
        
        new_record = {
            "エントリー日時(JST)": pos["entry_time"],
            "決済日時(JST)": now_jst_str,
            "売買": pos["side"],
            "数量(万)": lot_size,
            "新規価格": f"{pos['entry_price']:.3f}",
            "決済価格": f"{current_price:.3f}",
            "結果": close_reason,
            "獲得pips": f"{pnl_pips:+.1f}",
            "損益金額(円)": f"{pnl_jpy:+,.0f}円"
        }
        
        st.session_state.trade_history.insert(0, new_record)
        
        # CSVへ保存
        df_to_save = pd.DataFrame(st.session_state.trade_history)
        df_to_save.to_csv(CSV_FILE, index=False)

        # ポジションクリア＆ファイル更新
        st.session_state.position = None
        save_position(None)

# 2. 新規エントリー判定
elif st.session_state.position is None and signal in ["BUY", "SELL"]:
    _, tp_val, sl_val = query_ai(signal, current_price, df_ltf, reason)
    if tp_val and sl_val:
        new_pos = {
            "side": signal,
            "entry_price": current_price,
            "tp": tp_val,
            "sl": sl_val,
            "entry_time": now_jst_str
        }
        st.session_state.position = new_pos
        save_position(new_pos) # ポジション情報の永続化保存

# ---------------------------------------------------------
# 📊 UI表示部
# ---------------------------------------------------------
st.title("🤖 FX AI 仮想自動売買モニター")

col1, col2, col3, col4 = st.columns(4)
col1.metric("現在価格", f"{current_price:.3f}")
col2.metric("売買判定", signal)
col3.metric("ボラティリティ / 閾値", f"{pips_range:.1f} / {min_pips:.1f} pips")
col4.metric("累計獲得 pips", f"{st.session_state.total_pnl_pips:+.1f} pips")

st.caption(f"最終更新時間 (JST): {now_jst_str}")
st.markdown("---")

# ---------------------------------------------------------
# ⚡ 大きく強調した「含み損益（評価損益）」専用パネル
# ---------------------------------------------------------
if st.session_state.position:
    pos = st.session_state.position
    # 現在の含み損益(pips & 円)の計算
    if pos["side"] == "BUY":
        unrealized_pips = (current_price - pos["entry_price"]) * 100
    else:
        unrealized_pips = (pos["entry_price"] - current_price) * 100
    unrealized_jpy = unrealized_pips * 100 * lot_size

    # 利益/損失に応じたカラー・アイコン設定
    pnl_color = "#10b981" if unrealized_pips >= 0 else "#ef4444"  # 緑 または 赤
    bg_color = "rgba(16, 185, 129, 0.1)" if unrealized_pips >= 0 else "rgba(239, 68, 68, 0.1)"
    status_icon = "📈 含み益" if unrealized_pips >= 0 else "📉 含み損"

    # HTML/CSSを使った大きめの別枠カード表示
    st.markdown(
        f"""
        <div style="
            background-color: {bg_color};
            border: 2px solid {pnl_color};
            border-radius: 12px;
            padding: 18px 25px;
            margin-bottom: 20px;
        ">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span style="color: {pnl_color}; font-weight: bold; font-size: 1.1rem;">
                    {status_icon}（リアルタイム評価損益）
                </span>
                <span style="color: #94a3b8; font-size: 0.9rem;">
                    エントリー日時: <b>{pos['entry_time']}</b>
                </span>
            </div>
            <div style="display: flex; gap: 30px; align-items: baseline;">
                <div>
                    <span style="font-size: 0.9rem; color: #cbd5e1;">損益 pips:</span><br>
                    <span style="font-size: 2.2rem; font-weight: 800; color: {pnl_color};">
                        {unrealized_pips:+.1f} <span style="font-size: 1.2rem;">pips</span>
                    </span>
                </div>
                <div>
                    <span style="font-size: 0.9rem; color: #cbd5e1;">評価損益額:</span><br>
                    <span style="font-size: 2.2rem; font-weight: 800; color: {pnl_color};">
                        {unrealized_jpy:+,.0f} <span style="font-size: 1.2rem;">円</span>
                    </span>
                </div>
            </div>
            <div style="margin-top: 10px; font-size: 0.95rem; color: #e2e8f0; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 8px;">
                保有ポジション: <b>{pos['side']}</b> @ <code>{pos['entry_price']:.3f}</code> ｜ 
                <b>TP (利確)</b>: <code>{pos['tp']:.3f}</code> ｜ 
                <b>SL (損切)</b>: <code>{pos['sl']:.3f}</code>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )
else:
    if signal == "BUY":
        st.success(f"🟢 **【買いシグナル発令】** {reason}")
    elif signal == "SELL":
        st.error(f"🔴 **【売りシグナル発令】** {reason}")
    else:
        st.info(f"⚪ **【様子見】** {reason}")

# ---------------------------------------------------------
# 📈 テクニカル分析チャート
# ---------------------------------------------------------
st.subheader("📈 5分足テクニカル分析チャート (JST)")

fig = go.Figure()
df_plot = df_ltf.tail(60)

fig.add_trace(go.Candlestick(
    x=df_plot.index, open=df_plot['Open'], high=df_plot['High'],
    low=df_plot['Low'], close=df_plot['Close'], name="USD/JPY 5分足"
))

fig.add_trace(go.Scatter(
    x=df_plot.index, y=df_plot['sma20'], mode='lines', name='20 SMA',
    line=dict(color='#FFD700', width=1.5)
))

fig.add_hline(y=prev_high, line_dash="dot", line_color="#FF4B4B", line_width=1,
              annotation_text=f"直近高値(上抜け買い): {prev_high:.3f}", annotation_position="top right")

fig.add_hline(y=prev_low, line_dash="dot", line_color="#0080FF", line_width=1,
              annotation_text=f"直近安値(下抜け売り): {prev_low:.3f}", annotation_position="bottom right")

fig.add_hline(y=current_price, line_dash="solid", line_color="cyan", line_width=1.5,
              annotation_text=f"現在値: {current_price:.3f}", annotation_position="top left")

# --- ポジション保有時：チャート上への表示（マーカー & 水平線） ---
if st.session_state.position:
    pos = st.session_state.position
    entry_time_dt = pd.to_datetime(pos["entry_time"]).tz_localize(JST)

    # 1. チャート上へのエントリーポイント（マーカー表示）
    marker_symbol = "triangle-up" if pos["side"] == "BUY" else "triangle-down"
    marker_color = "#0080FF" if pos["side"] == "BUY" else "#FF4B4B"
    marker_name = f"エントリー ({pos['side']})"

    fig.add_trace(go.Scatter(
        x=[entry_time_dt],
        y=[pos["entry_price"]],
        mode="markers+text",
        marker=dict(symbol=marker_symbol, size=14, color=marker_color),
        text=[f"  {pos['side']} @ {pos['entry_price']:.3f}"],
        textposition="top right" if pos["side"] == "BUY" else "bottom right",
        name=marker_name
    ))

    # 2. 保有価格・TP・SLラインの描画
    fig.add_hline(y=pos["entry_price"], line_dash="solid", line_color="white", line_width=2,
                  annotation_text=f"保有位置: {pos['entry_price']:.3f}", annotation_position="bottom right")
    fig.add_hline(y=pos["tp"], line_dash="dash", line_color="#00FF00", line_width=2,
                  annotation_text=f"🎯 TP (利確): {pos['tp']:.3f}", annotation_position="bottom left")
    fig.add_hline(y=pos["sl"], line_dash="dash", line_color="#FF0055", line_width=2,
                  annotation_text=f"🛑 SL (損切): {pos['sl']:.3f}", annotation_position="bottom left")

fig.update_layout(
    xaxis_rangeslider_visible=False, template="plotly_dark", height=500,
    margin=dict(l=20, r=20, t=30, b=20),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------
# 📋 取引履歴の一覧表
# ---------------------------------------------------------
st.subheader("📋 仮想トレード取引履歴一覧 (完全永続化)")

if st.session_state.trade_history:
    df_history = pd.DataFrame(st.session_state.trade_history)
    st.dataframe(df_history, use_container_width=True)
else:
    st.caption("※まだ取引履歴はありません。シグナルが発生して売買が決済されると自動で保存・一覧に追加されます。")

time.sleep(refresh_rate)
st.rerun()