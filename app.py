# ---------------------------------------------------------
# ⚡ 大きく強調した「含み損益（評価損益）」専用パネル (視認性改善版)
# ---------------------------------------------------------
if st.session_state.position:
    pos = st.session_state.position
    # 現在の含み損益(pips & 円)の計算
    if pos["side"] == "BUY":
        unrealized_pips = (current_price - pos["entry_price"]) * 100
    else:
        unrealized_pips = (pos["entry_price"] - current_price) * 100
    unrealized_jpy = unrealized_pips * 100 * lot_size

    # 利益/損失に応じたカラー・アイコン設定（明るい背景でもしっかり映える濃いめの色）
    pnl_color = "#059669" if unrealized_pips >= 0 else "#dc2626"  # 濃い緑 または 濃い赤
    bg_color = "rgba(16, 185, 129, 0.08)" if unrealized_pips >= 0 else "rgba(239, 68, 68, 0.08)"
    status_icon = "📈 含み益" if unrealized_pips >= 0 else "📉 含み損"

    # HTML/CSSを使った大きめの別枠カード表示（文字色を濃くして視認性を劇的向上）
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
            <div style="font-size: 1.0rem; color: #0f172a; border-top: 1px solid rgba(0,0,0,0.12); padding-top: 10px; font-weight: 500;">
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