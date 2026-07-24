import os
import re
import time
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

# ページ設定
st.set_page_config(page_title="FX AI 仮想自動売買モニター", layout="wide")

# OpenAI API Key設定 (Secrets または サイドバーから)
api_key = os.environ.get("OPENAI_API_KEY", "")
if not api_key and "OPENAI_API_KEY" in st.secrets:
    api_key = st.secrets["OPENAI_API_KEY"]

st.sidebar.title("⚙️ 仮想トレード設定")
symbol = st.sidebar.text_input("通貨ペア", "USDJPY=X")
min_pips = st.sidebar.number_input("最小ボラティリティ (pips)", value=10.0, step=1.0)
lot_size = st.sidebar.number_input("取引数量 (万通貨)", value=1.0, step=0.1)
refresh_rate = st.sidebar.slider("更新間隔 (秒)", min_value=1, max_value=15, value=1)

# CSVリセット機能
if st.sidebar.button("🗑️ 取引履歴をリセット"):
    if os.path.exists(CSV_FILE):
        os.remove(CSV_FILE)
    st.session_state.trade_history = []
    st.session_state.total_pnl_pips = 0.0
    st.session_state.position = None
    st.sidebar.success("取引履歴をリセットしました！")
    st.rerun()

if not api_key:
    user_api_key = st.sidebar.text_input("OpenAI API Key", type="password")
    if user_api_key:
        api_key = user_api_key

client = OpenAI(api_key=api_key) if api_key else None

# CSVから履歴を読み込む関数
def load_history_from_csv():
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE)
            history = df.to_dict('records')
            # 累計pipsの再計算
            total_pips = sum(float(str(row["獲得pips"]).replace("+", "")) for row in history if "獲得pips" in row)
            return history, total_pips
        except Exception:
            return [], 0.0
    return [], 0.0

# データ取得 (日本時間に変換)
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

# ロジック判定
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

# AI判定
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

# --- 状態管理（初期化＆CSV読み込み） ---
if "position" not in st.session_state:
    st.session_state.position = None

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
        
        # CSVファイルに永続化保存
        df_to_save = pd.DataFrame(st.session_state.trade_history)
        df_to_save.to_csv(CSV_FILE, index=False)

        st.session_state.position = None

# 2. 新規エントリー判定
elif st.session_state.position is None and signal in ["BUY", "SELL"]:
    _, tp_val, sl_val = query_ai(signal, current_price, df_ltf, reason)
    if tp_val and sl_val:
        st.session_state.position = {
            "side": signal,
            "entry_price": current_price,
            "tp": tp_val,
            "sl": sl_val,
            "entry_time": now_jst_str
        }

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

if signal == "BUY":
    st.success(f"🟢 **【買いシグナル発令】** {reason}")
elif signal == "SELL":
    st.error(f"🔴 **【売りシグナル発令】** {reason}")
else:
    st.info(f"⚪ **【様子見】** {reason}")

if st.session_state.position:
    pos = st.session_state.position
    st.warning(f"⚡ **【仮想ポジション保有中】** {pos['side']} @ `{pos['entry_price']:.3f}` | **TP (利確)**: `{pos['tp']:.3f}` | **SL (損切)**: `{pos['sl']:.3f}`")

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

if st.session_state.position:
    pos = st.session_state.position
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
st.subheader("📋 仮想トレード取引履歴一覧 (自動保存対応)")

if st.session_state.trade_history:
    df_history = pd.DataFrame(st.session_state.trade_history)
    st.dataframe(df_history, use_container_width=True)
else:
    st.caption("※まだ取引履歴はありません。シグナルが発生して売買が決済されると自動でCSVに保存・一覧に追加されます。")

time.sleep(refresh_rate)
st.rerun()