import os
import time
import json
import datetime
import pytz
import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go

# =========================================================
# 1. 基本設定・定数定義
# =========================================================
JST = pytz.timezone('Asia/Tokyo')
CSV_FILE = "trade_history.csv"
CONFIG_FILE = "config.json"
POSITION_FILE = "position.json"

st.set_page_config(page_title="FX 仮想自動売買モニター V4.2", layout="wide")

# =========================================================
# 2. 設定およびポジションデータのファイル管理関数
# =========================================================
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

saved_config = load_config()

# =========================================================
# 3. サイドバー設定メニュー
# =========================================================
st.sidebar.title("⚙️ 仮想トレード設定 (V4.2)")

symbol = st.sidebar.text_input("通貨ペア", saved_config["symbol"])
min_pips = st.sidebar.number_input("最小ボラティリティ (pips)", value=float(saved_config["min_pips"]), step=1.0)
lot_size = st.sidebar.number_input("取引数量 (万通貨)", value=float(saved_config["lot_size"]), step=0.1)
refresh_rate = st.sidebar.slider("更新間隔 (秒)", min_value=1, max_value=15, value=int(saved_config["refresh_rate"]))

current_config = {
    "symbol": symbol,
    "min_pips": min_pips,
    "lot_size": lot_size,
    "refresh_rate": refresh_rate
}
if current_config != saved_config:
    save_config(current_config)

if st.sidebar.button("🗑️ 取引履歴＆設定をリセット"):
    for file_path in [CSV_FILE, CONFIG_FILE, POSITION_FILE]:
        if os.path.exists(file_path):
            os.remove(file_path)
    st.session_state.trade_history = []
    st.session_state.total_pnl_pips = 0.0
    st.session_state.position = None
    st.sidebar.success("データと設定をすべてリセットしました！")
    st.rerun()

# =========================================================
# 4. データ取得 & テクニカル分析
# =========================================================
@st.cache_data(ttl=1)
def load_data(sym):
    ticker = yf.Ticker(sym)
    df_daily = ticker.history(period="1mo", interval="1d")
    df_htf = ticker.history(period="7d", interval="1h")
    df_ltf = ticker.history(period="1d", interval="5m")
    
    if not df_daily.empty:
        df_daily.index = df_daily.index.tz_convert(JST)
    if not df_htf.empty:
        df_htf.index = df_htf.index.tz_convert(JST)
    if not df_ltf.empty:
        df_ltf.index = df_ltf.index.tz_convert(JST)
        
    return df_daily, df_htf, df_ltf

def analyze(df_daily, df_htf, df_ltf, threshold_pips):
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

# =========================================================
# 5. 情勢トレンド・投資家心理ロジック（API非依存エンジン）
# =========================================================
def calculate_market_sentiment(signal_type, price, df_daily, df_htf, df_ltf):
    delta = df_ltf['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs)).iloc[-1]

    daily_sma = df_daily['Close'].rolling(20).mean().iloc[-1]
    bias = ((price - daily_sma) / daily_sma) * 100

    now_hour = datetime.datetime.now(JST).hour
    if 9 <= now_hour < 15:
        market_zone = "東京市場（実需・レンジ傾向強）"
    elif 16 <= now_hour < 21:
        market_zone = "ロンドン市場（トレンド形成期）"
    elif 21 <= now_hour or now_hour < 6:
        market_zone = "NY市場（米ニュース・金利変動集中帯）"
    else:
        market_zone = "オセアニア市場（薄商い・突発変動注意）"

    if signal_type == "BUY":
        tp = price + 0.15
        sl = price - 0.10
        psychology = "【強気買い心理】" if rsi < 70 else "【買われすぎ警戒（過熱）】"
        reason_str = (
            f"時間帯: {market_zone}\n"
            f"RSI(14): {rsi:.1f} → {psychology}\n"
            f"日足20SMA乖離: {bias:+.2f}%\n"
            f"→ 中期上昇心理に素直に従いエントリー。利確(+15pips) / 損切(-10pips)をセット。"
        )
    elif signal_type == "SELL":
        tp = price - 0.15
        sl = price + 0.10
        psychology = "【強気売り心理】" if rsi > 30 else "【売られすぎ警戒（パニック売り）】"
        reason_str = (
            f"時間帯: {market_zone}\n"
            f"RSI(14): {rsi:.1f} → {psychology}\n"
            f"日足20SMA乖離: {bias:+.2f}%\n"
            f"→ 下降モメンタム優勢と判断。利確(+15pips) / 損切(-10pips)をセット。"
        )
    else:
        tp, sl = price, price
        reason_str = (
            f"時間帯: {market_zone}\n"
            f"RSI(14): {rsi:.1f} → 【中立・様子見心理】\n"
            f"日足20SMA乖離: {bias:+.2f}%\n"
            f"→ 現在はブレイク条件未達成のため静観中。ボラティリティや方向性が揃うのを待っています。"
        )

    return reason_str, tp, sl

# =========================================================
# 6. セッション状態初期化 & データ取得
# =========================================================
if "position" not in st.session_state:
    st.session_state.position = load_position()

if "trade_history" not in st.session_state or "total_pnl_pips" not in st.session_state:
    history, total_pips = load_history_from_csv()
    st.session_state.trade_history = history
    st.session_state.total_pnl_pips = total_pips

df_daily, df_htf, df_ltf = load_data(symbol)
signal, reason, pips_range, prev_high, prev_low = analyze(df_daily, df_htf, df_ltf, min_pips)
current_price = df_ltf['Close'].iloc[-1]
now_jst_str = datetime.datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')

# =========================================================
# 7. 自動売買実行エンジン
# =========================================================
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
        
        df_to_save = pd.DataFrame(st.session_state.trade_history)
        df_to_save.to_csv(CSV_FILE, index=False)

        st.session_state.position = None
        save_position(None)

elif st.session_state.position is None and signal in ["BUY", "SELL"]:
    ai_reason, tp_val, sl_val = calculate_market_sentiment(signal, current_price, df_daily, df_htf, df_ltf)
    new_pos = {
        "side": signal,
        "entry_price": current_price,
        "tp": tp_val,
        "sl": sl_val,
        "entry_time": now_jst_str,
        "ai_reason": ai_reason
    }
    st.session_state.position = new_pos
    save_position(new_pos)

# =========================================================
# 8. メインダッシュボードUI表示
# =========================================================
st.title("🤖 FX 仮想自動売買モニター V4.2")

col1, col2, col3, col4 = st.columns(4)
col1.metric("現在価格", f"{current_price:.3f}")
col2.metric("売買判定", signal)
col3.metric("ボラティリティ / 閾値", f"{pips_range:.1f} / {min_pips:.1f} pips")
col4.metric("累計獲得 pips", f"{st.session_state.total_pnl_pips:+.1f} pips")

st.caption(f"最終更新時間 (JST): {now_jst_str}")
st.markdown("---")

# 評価損益・ポジション情報カード ＆ 常時分析表示枠
if st.session_state.position:
    pos = st.session_state.position
    if pos["side"] == "BUY":
        unrealized_pips = (current_price - pos["entry_price"]) * 100
    else:
        unrealized_pips = (pos["entry_price"] - current_price) * 100
    unrealized_jpy = unrealized_pips * 100 * lot_size

    pnl_color = "#059669" if unrealized_pips >= 0 else "#dc2626"
    bg_color = "rgba(16, 185, 129, 0.08)" if unrealized_pips >= 0 else "rgba(239, 68, 68, 0.08)"
    status_icon = "📈 含み益" if unrealized_pips >= 0 else "📉 含み損"

    analysis_text = pos.get('ai_reason', '')
    if not analysis_text:
        analysis_text, _, _ = calculate_market_sentiment(pos["side"], pos["entry_price"], df_daily, df_htf, df_ltf)

    st.markdown(
        f"""
        <div style="
            background-color: {bg_color};
            border: 2px solid {pnl_color};
            border-radius: 12px;
            padding: 20px 25px;
            margin-bottom: 20px;
        ">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <span style="color: {pnl_color}; font-weight: bold; font-size: 1.2rem;">
                    {status_icon}（リアルタイム評価損益）
                </span>
                <span style="color: #334155; font-size: 0.95rem; font-weight: 600;">
                    エントリー日時: <b>{pos['entry_time']}</b>
                </span>
            </div>
            <div style="display: flex; gap: 40px; align-items: baseline; margin-bottom: 12px;">
                <div>
                    <span style="font-size: 0.95rem; color: #475569; font-weight: bold;">損益 pips:</span><br>
                    <span style="font-size: 2.5rem; font-weight: 900; color: {pnl_color};">
                        {unrealized_pips:+.1f} <span style="font-size: 1.2rem;">pips</span>
                    </span>
                </div>
                <div>
                    <span style="font-size: 0.95rem; color: #475569; font-weight: bold;">評価損益額:</span><br>
                    <span style="font-size: 2.5rem; font-weight: 900; color: {pnl_color};">
                        {unrealized_jpy:+,.0f} <span style="font-size: 1.2rem;">円</span>
                    </span>
                </div>
            </div>
            <div style="font-size: 1.0rem; color: #0f172a; border-top: 1px solid rgba(0,0,0,0.12); padding-top: 10px; margin-bottom: 10px; font-weight: 500;">
                保有ポジション: <b>{pos['side']}</b> @ <code>{pos['entry_price']:.3f}</code> ｜ 
                <b>TP (利確)</b>: <code>{pos['tp']:.3f}</code> ｜ 
                <b>SL (損切)</b>: <code>{pos['sl']:.3f}</code>
            </div>
            <div style="font-size: 0.9rem; color: #334155; background: rgba(255,255,255,0.7); padding: 12px; border-radius: 6px; white-space: pre-wrap;">
📊 <b>市場センチメント・時間帯構造分析:</b><br>{analysis_text}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )
else:
    # ポジションがない（様子見中・シグナル待ち）の時でも分析枠を常時表示
    hold_reason, _, _ = calculate_market_sentiment("HOLD", current_price, df_daily, df_htf, df_ltf)
    
    st.markdown(
        f"""
        <div style="
            background-color: rgba(59, 130, 246, 0.06);
            border: 2px solid #3b82f6;
            border-radius: 12px;
            padding: 20px 25px;
            margin-bottom: 20px;
        ">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <span style="color: #2563eb; font-weight: bold; font-size: 1.2rem;">
                    ⚪ 様き見中（シグナル監視・エントリー待機）
                </span>
                <span style="color: #334155; font-size: 0.95rem; font-weight: 600;">
                    判定理由: <b>{reason}</b>
                </span>
            </div>
            <div style="font-size: 0.9rem; color: #334155; background: rgba(255,255,255,0.7); padding: 12px; border-radius: 6px; white-space: pre-wrap;">
📊 <b>市場センチメント・時間帯構造分析:</b><br>{hold_reason}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

# =========================================================
# 9. インタラクティブ・チャート描画
# =========================================================
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
    entry_time_dt = pd.to_datetime(pos["entry_time"]).tz_localize(JST)

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

# =========================================================
# 10. 取引履歴表示
# =========================================================
st.subheader("📋 仮想トレード取引履歴一覧 (完全永続化)")

if st.session_state.trade_history:
    df_history = pd.DataFrame(st.session_state.trade_history)
    st.dataframe(df_history, use_container_width=True)
else:
    st.caption("※まだ取引履歴はありません。シグナルが発生して売買が決済されると自動で保存・一覧に追加されます。")

time.sleep(refresh_rate)
st.rerun()