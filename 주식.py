import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import google.generativeai as genai
from duckduckgo_search import DDGS

# ==========================================
# 🔑 기본 설정
# ==========================================
try:
    GOOG_API_KEY = st.secrets["GOOG_API_KEY"]
except:
    GOOG_API_KEY = "여기에_키를_넣으세요"

st.set_page_config(page_title="AI 주식 투자 비서", layout="wide")
st.title("🤖 AI 주식 투자 전략가 (Pro Ver.)")

# 세션 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_code" not in st.session_state:
    st.session_state.last_code = None

# --- [유틸리티] ---
@st.cache_data
def get_available_models(api_key):
    try:
        genai.configure(api_key=api_key)
        return [m.name.replace("models/", "") for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    except: return ["gemini-1.5-flash"]

def robust_search(keyword):
    search_context = ""
    try:
        results = list(DDGS().news(keywords=keyword, region='kr-kr', max_results=5))
        if not results:
            results = list(DDGS().text(keywords=keyword, region='kr-kr', max_results=5))
        for i, res in enumerate(results):
            title = res.get('title', '-')
            body = res.get('body', res.get('snippet', '-'))
            search_context += f"[{i+1}] {title}: {body}\n"
    except: 
        search_context = "외부 검색 데이터 없음 (네이버 데이터로 분석 대체)"
    return search_context

# --- [데이터 수집] Tab 1용 ---
@st.cache_data
def get_naver_themes():
    url = "https://finance.naver.com/sise/theme.naver"
    try:
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        data = []
        for row in soup.select("#contentarea_left > table.type_1 > tr"):
            cols = row.select("td")
            if len(cols) >= 4:
                data.append({"테마명": cols[0].text.strip(), "링크": "https://finance.naver.com" + cols[0].find('a')['href']})
        return pd.DataFrame(data).head(20)
    except: return pd.DataFrame()

def get_theme_stocks_detail(link):
    try:
        res = requests.get(link, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        stocks = []
        for row in soup.select("table.type_5 > tbody > tr"):
            cols = row.select("td")
            if len(cols) > 4:
                name = cols[0].text.strip()
                code_match = re.search(r'code=([0-9]+)', cols[0].find('a')['href'])
                code = code_match.group(1) if code_match else ""
                price = cols[2].text.strip()
                rate = cols[4].text.strip().replace('\n', '').strip()
                stocks.append({'종목명': name, '종목코드': code, '현재가(등락률)': f"{price} ({rate})", '링크': link})
        return stocks
    except: return []

@st.cache_data
def get_market_rankings():
    # 필터 범위: 거래량 200위, 상승률 300위
    vol_stocks, rise_stocks = set(), set()
    headers = {'User-Agent': 'Mozilla/5.0'}
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_quant_high.naver?sosok={s}", headers=headers)
            soup = BeautifulSoup(res.text, 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:200]: vol_stocks.add(item.text.strip())
        except: pass
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}", headers=headers)
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:300]: rise_stocks.add(item.text.strip())
        except: pass
    return vol_stocks, rise_stocks

def get_latest_news_simple(code):
    try:
        url = f"https://finance.naver.com/item/news_news.naver?code={code}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        news_list = []
        for a in soup.select(".title > a")[:10]:
            news_list.append(f"- {a.text.strip()}")
        return "\n".join(news_list)
    except: return "뉴스 데이터 없음"

def get_stock_fundamentals(code):
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        cap = soup.select_one("#_market_sum").text.strip()
        return f"{cap}억"
    except: return "-"

# --- [데이터 수집] Tab 2용 ---
@st.cache_data
def get_market_cap_top150():
    stocks = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    for page in range(1, 4):
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page={page}", headers=headers)
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            for row in soup.select("table.type_2 tbody tr"):
                cols = row.select("td")
                if len(cols) < 10: continue
                stocks.append({
                    "순위": cols[0].text.strip(), "종목명": cols[1].text.strip(),
                    "현재가": cols[2].text.strip(), "등락률": cols[4].text.strip().replace("\n", "").strip(),
                    "시가총액": cols[6].text.strip()
                })
        except: pass
    return pd.DataFrame(stocks)

# --- [AI 분석 함수] ---
def get_ai_response(messages, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    response = model.generate_content(messages, stream=True)
    for chunk in response: yield chunk.text

def analyze_market_trend(df_top, search_text, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    top_20 = df_top.head(20).to_string(index=False)
    prompt = f"""
    당신은 수석 애널리스트입니다. 오늘 코스피 시총 상위 150위 흐름과 뉴스를 분석해 시황을 브리핑하세요.
    [상위 20위 데이터]: {top_20}
    [뉴스 검색]: {search_text}
    분석결과는 '오늘의 증시 요약', '주도 섹터', '투자 전략' 순으로 작성하세요.
    """
    response = model.generate_content(prompt, stream=True)
    for chunk in response: yield chunk.text

# ==========================================
# 🖥️ 메인 화면 구성
# ==========================================

with st.sidebar:
    st.header("⚙️ 설정")
    if GOOG_API_KEY.startswith("AIza"):
        models = get_available_models(GOOG_API_KEY)
        selected_model = st.selectbox("AI 모델", models, index=0)
    else:
        st.error("API 키 확인 필요")
        selected_model = "gemini-1.5-flash"

tab1, tab2 = st.tabs(["🎯 급등주 발굴", "📊 시황 분석"])

# --- [Tab 1] 급등주 발굴 ---
with tab1:
    st.subheader("1️⃣ 교집합 분석 결과 (테마별 정렬)")
    
    with st.spinner("데이터 분석 중..."):
        vol_set, rise_set = get_market_rankings()
        df_themes = get_naver_themes()
        final_candidates = []
        
        for idx, row in df_themes.iterrows():
            theme_stocks = get_theme_stocks_detail(row['링크'])
            for s in theme_stocks:
                if (s['종목명'] in vol_set) and (s['종목명'] in rise_set):
                    s['테마명'] = row['테마명']
                    final_candidates.append(s)
        
        if final_candidates:
            df_final = pd.DataFrame(final_candidates).drop_duplicates('종목명')
            # [요청사항] 테마별로 정렬
            df_final = df_final.sort_values(by='테마명')
            
            display_columns = ['테마명', '종목명', '현재가(등락률)']
            column_config = {
                "테마명": st.column_config.TextColumn("관련 테마", width="large"),
                "종목명": st.column_config.TextColumn("종목명", width="medium"),
                "현재가(등락률)": st.column_config.TextColumn("현재가 (등락률)", width="medium"),
            }
            
            event = st.dataframe(
                df_final[display_columns], 
                use_container_width=True, 
                hide_index=True, 
                column_config=column_config, 
                on_select="rerun", 
                selection_mode="single-row"
            )
            
            # [선택 시 즉각 반응]
            if len(event.selection.rows) > 0:
                selected_index = event.selection.rows[0]
                selected_data = df_final.iloc[selected_index]
                s_name = selected_data['종목명']
                s_code = selected_data['종목코드']
                s_theme = selected_data['테마명']
                
                # 세션 리셋
                if st.session_state.last_code != s_code:
                    st.session_state.messages = []
                    st.session_state.last_code = s_code
                
                st.divider()
                st.subheader(f"2️⃣ [{s_name}] 상세 정보")
                
                # 1. 정보 및 차트 (즉시 표시)
                with st.spinner("정보 가져오는 중..."):
                    m_cap = get_stock_fundamentals(s_code)
                    # [요청사항] 시가총액/테마 즉시 표기
                    st.info(f"🏷️ 테마: **{s_theme}** | 💰 시가총액: **{m_cap}**")
                    
                    # [요청사항] 일봉/주봉 탭
                    c_tab1, c_tab2 = st.tabs(["📅 일봉 차트", "📆 주봉 차트"])
                    with c_tab1: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/day/{s_code}.png", use_container_width=True)
                    with c_tab2: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/week/{s_code}.png", use_container_width=True)
                
                # 2. AI 분석 및 질문 (하단 배치)
                st.markdown("---")
                st.subheader("💬 AI 투자 전략가")
                
                # 초기 분석 버튼 (비용 절약을 위해 버튼 유지하되, 누르면 채팅 시작)
                if not st.session_state.messages:
                    if st.button("⚡ 호재 중심 심층 분석 실행 (Click)", type="primary"):
                        news_txt = get_latest_news_simple(s_code)
                        search_res = robust_search(f"{s_name} {s_theme} 호재 전망")
                        
                        system_prompt = f"""
                        당신은 공격적인 투자 전략가입니다. {s_name}({s_theme})을 호재 위주로 분석하세요.
                        [뉴스]: {news_txt}
                        [검색]: {search_res}
                        반드시 '🚀 핵심 호재 3가지', '📈 테마 전망', '💡 매매 전략' 순서로 브리핑하세요.
                        """
                        st.session_state.messages.append({"role": "user", "content": system_prompt})
                        with st.chat_message("assistant"):
                            res_text = st.write_stream(get_ai_response([{"role": "user", "parts": [system_prompt]}], selected_model))
                        st.session_state.messages.append({"role": "assistant", "content": res_text})
                
                # [요청사항] 대화 기능 유지
                for msg in st.session_state.messages:
                    if msg['role'] == 'user' and "당신은" in msg['content']: continue
                    with st.chat_message(msg['role']): st.markdown(msg['content'])
                
                if prompt := st.chat_input(f"{s_name}에 대해 궁금한 점을 물어보세요..."):
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    with st.chat_message("user"): st.markdown(prompt)
                    with st.chat_message("assistant"):
                        history = [{"role": m["role"], "parts": [m["content"]]} for m in st.session_state.messages]
                        res_text = st.write_stream(get_ai_response(history, selected_model))
                    st.session_state.messages.append({"role": "assistant", "content": res_text})

        else:
            st.warning("조건을 만족하는 종목이 없습니다.")

# --- [Tab 2] 시황 분석 ---
with tab2:
    st.header("📊 시장 전체 흐름 (시총 Top 150)")
    if st.button("데이터 가져오기", key="btn_market"):
        st.session_state.df_market = get_market_cap_top150()
    
    if "df_market" in st.session_state and st.session_state.df_market is not None:
        st.dataframe(st.session_state.df_market, height=400)
        if st.button("📢 AI 시황 브리핑"):
            with st.spinner("시장 분석 중..."):
                search_data = robust_search("오늘 주식 시황 특징주")
                st.write_stream(analyze_market_trend(st.session_state.df_market, search_data, selected_model))
