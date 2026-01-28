import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import google.generativeai as genai
from duckduckgo_search import DDGS

# ==========================================
# 🔑 [필수] Gemini API 키 설정
# ==========================================
try:
    GOOG_API_KEY = st.secrets["GOOG_API_KEY"]
except:
    GOOG_API_KEY = "여기에_키를_넣으세요"

# 1. 페이지 설정
st.set_page_config(page_title="주식 테마 분석기 (AI Ver.)", layout="wide")
st.title("🤖 AI 주식 투자 전략가 (Complete Ver.)")

# 세션 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_code" not in st.session_state:
    st.session_state.last_code = None

# --- [유틸리티] ---
@st.cache_data
def get_available_gemini_models(api_key):
    try:
        genai.configure(api_key=api_key)
        models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                name = m.name.replace("models/", "")
                display_name = name
                if "1.5-flash" in name and "latest" not in name:
                     display_name = f"✅ {name}"
                else: display_name = f"🧪 {name}"
                models.append(display_name)
        models.sort(key=lambda x: "✅" not in x)
        return models
    except: return ["gemini-1.5-flash"]

def extract_code(link):
    match = re.search(r'code=([a-zA-Z0-9]+)', link)
    if match: return match.group(1)
    return None

def clean_text(text):
    if not text: return "-"
    return re.sub(r'[^가-힣0-9a-zA-Z.]', '', text)

# --- [검색 함수] ---
def search_news_robust(keyword):
    search_context = ""
    total_chars = 0
    try:
        results = list(DDGS().news(keywords=keyword, region='kr-kr', max_results=5))
        if not results:
            results = list(DDGS().text(keywords=keyword, region='kr-kr', max_results=5))
        for i, res in enumerate(results):
            title = res.get('title', '-')
            body = res.get('body', res.get('snippet', '-'))
            entry = f"[{i+1}] {title}: {body}\n"
            search_context += entry
            total_chars += len(entry)
    except: 
        search_context = "외부 검색 데이터 없음"
    return search_context, total_chars

# --- [데이터 수집] Tab 1용 (기존 코드 그대로) ---
@st.cache_data
def get_naver_themes():
    url = "https://finance.naver.com/sise/theme.naver"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers)
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        data = []
        for row in soup.select("#contentarea_left > table.type_1 > tr"):
            cols = row.select("td")
            if len(cols) >= 4:
                data.append({"테마명": cols[0].text.strip(), "링크": "https://finance.naver.com" + cols[0].find('a')['href']})
        return pd.DataFrame(data).head(20)
    except: return pd.DataFrame()

def get_theme_details(theme_link):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(theme_link, headers=headers)
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
                stocks.append({'name': name, 'code': code, 'price_str': f"{price} ({rate})", 'link': theme_link})
        return stocks
    except: return []

@st.cache_data
def get_market_rankings():
    market_map = {}
    vol_leaders = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 1. 상승률 상위 (300위까지 확대)
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}", headers=headers)
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:300]: 
                market_map[item.text.strip()] = "KOSPI" if s==0 else "KOSDAQ"
        except: pass
    # 2. 거래량 상위 (200위까지 확대)
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_quant_high.naver?sosok={s}", headers=headers)
            soup = BeautifulSoup(res.text, 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:200]: 
                vol_leaders.append(item.text.strip())
        except: pass
    return market_map, vol_leaders

def get_stock_fundamentals(code):
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        cap = soup.select_one("#_market_sum").text.strip()
        return {"시가총액": f"{cap}억"}
    except: return {"시가총액": "-"}

def get_latest_news(code):
    try:
        url = f"https://finance.naver.com/item/news_news.naver?code={code}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        news_list = []
        for a in soup.select(".title > a")[:20]:
            news_list.append({"제목": a.text.strip(), "링크": "https://finance.naver.com"+a['href']})
        return news_list
    except: return []

# --- [AI 응답 함수] ---
def get_gemini_response_robust(messages, model_name, use_search, stock_name, theme):
    genai.configure(api_key=GOOG_API_KEY)
    
    current_query = messages[-1]['content']
    search_res = ""
    if use_search:
        q = f"{stock_name} {theme} 호재 전망" if "당신은" in current_query else f"{stock_name} {current_query}"
        data, count = search_news_robust(q)
        search_res = f"\n[검색 데이터]:\n{data}\n"
    
    modified_msgs = []
    for i, msg in enumerate(messages):
        content = msg['content']
        if i == len(messages)-1: content += search_res
        modified_msgs.append({"role": "user" if msg['role']=="user" else "model", "parts": [content]})
    
    model = genai.GenerativeModel(f"models/{model_name}")
    response = model.generate_content(modified_msgs, stream=True)
    for chunk in response: yield chunk.text

def analyze_market_trend_ai(df, search_text, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    top_20 = df.head(20).to_string(index=False)
    prompt = f"""
    당신은 수석 애널리스트입니다. 오늘 코스피 시총 상위 150위 흐름과 뉴스를 분석해 시황을 브리핑하세요.
    [상위 20위 데이터]: {top_20}
    [뉴스 검색]: {search_text}
    분석결과는 '오늘의 증시 요약', '주도 섹터', '투자 전략' 순으로 작성하세요.
    """
    response = model.generate_content(prompt, stream=True)
    for chunk in response: yield chunk.text

# --- [데이터 수집] Tab 2용 (신규 기능) ---
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


# ==========================================
# 🖥️ 메인 화면 구성
# ==========================================

# 사이드바
with st.sidebar:
    st.header("🔍 설정")
    if GOOG_API_KEY.startswith("AIza"):
        models = get_available_gemini_models(GOOG_API_KEY)
        selected_real_name = st.selectbox("모델 선택", models, index=0).split(" ")[1] if " " in models[0] else models[0]
    else:
        st.error("API 키 필요")
        selected_real_name = "gemini-1.5-flash"
    use_grounding = st.checkbox("🌍 심층 검색 사용", value=True)

# 탭 구성 (기존 기능 + 신규 기능)
tab1, tab2 = st.tabs(["🎯 급등주 발굴 (기존)", "📊 시황 분석 (신규)"])

# --- [Tab 1] 기존 코드 로직 복원 ---
with tab1:
    st.subheader("1️⃣ 교집합 분석 결과 (핵심 주도주)")
    try:
        with st.spinner('시장 데이터를 분석하고 있습니다...'):
            market_map, vol_leaders = get_market_rankings()
            df_themes = get_naver_themes()
            
            final_candidates = []
            for index, row in df_themes.iterrows():
                stocks_info = get_theme_details(row['링크'])
                for s in stocks_info:
                    if (s['name'] in market_map) and (s['name'] in vol_leaders):
                        final_candidates.append({
                            "테마순위": f"{index+1}위",
                            "시장구분": market_map[s['name']],
                            "종목명": s['name'], "종목코드": s['code'],
                            "현재가(등락률)": s['price_str'],
                            "테마명": row['테마명']
                        })
        
        if final_candidates:
            df_final = pd.DataFrame(final_candidates).drop_duplicates(['종목명'])
            
            # [기존 UI 복구] 클릭 가능한 데이터프레임
            display_cols = ['테마순위', '시장구분', '종목명', '현재가(등락률)', '테마명']
            event = st.dataframe(
                df_final[display_cols], use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row"
            )
            
            st.divider()
            
            # [기존 UI 복구] 선택 시 하단에 상세 정보 표시
            if len(event.selection.rows) > 0:
                sel_idx = event.selection.rows[0]
                sel_data = df_final.iloc[sel_idx]
                s_name = sel_data['종목명']
                code = sel_data['종목코드']
                s_theme = sel_data['테마명']
                price_info = sel_data['현재가(등락률)']
                
                # 세션 리셋
                if st.session_state.last_code != code:
                    st.session_state.messages = []
                    st.session_state.last_code = code

                with st.spinner(f'{s_name} 정보 수집 중...'):
                    fund = get_stock_fundamentals(code)
                    news_list = get_latest_news(code)
                
                st.subheader(f"2️⃣ [{s_name}] 상세 분석")
                st.info(f"💰 시가총액: **{fund['시가총액']}** | 🏆 테마: **{s_theme}**")
                
                # AI 대화창 (Expander 구조 유지)
                with st.expander("💬 AI 투자 전략가와 대화하기 (Click)", expanded=True):
                    if not st.session_state.messages:
                        if st.button(f"⚡ '{s_name}' 심층 분석 시작"):
                            news_ctx = "\n".join([f"- {n['제목']}" for n in news_list])
                            sys_prompt = f"""
                            당신은 공격적인 투자 전략가입니다. {s_name}({s_theme})을 호재 위주로 분석하세요.
                            [뉴스]: {news_ctx}
                            반드시 '🚀 핵심 호재 3가지', '📈 테마 전망', '💡 매매 전략' 순서로 브리핑하세요.
                            """
                            st.session_state.messages.append({"role": "user", "content": sys_prompt})
                            with st.chat_message("assistant"):
                                res_txt = st.write_stream(get_gemini_response_robust(st.session_state.messages, selected_real_name, use_grounding, s_name, s_theme))
                            st.session_state.messages.append({"role": "assistant", "content": res_txt})

                    for msg in st.session_state.messages:
                        if msg['role'] == 'user' and "당신은" in msg['content']: continue
                        with st.chat_message(msg['role']): st.markdown(msg['content'])

                    if prompt := st.chat_input(f"{s_name}에 대해 질문하세요..."):
                        st.session_state.messages.append({"role": "user", "content": prompt})
                        with st.chat_message("user"): st.markdown(prompt)
                        with st.chat_message("assistant"):
                            res_txt = st.write_stream(get_gemini_response_robust(st.session_state.messages, selected_real_name, use_grounding, s_name, s_theme))
                        st.session_state.messages.append({"role": "assistant", "content": res_txt})

                col1, col2 = st.columns([1, 1])
                with col1:
                    t1, t2, t3 = st.tabs(["📅 일봉", "📆 주봉", "📋 테마 전체"])
                    with t1: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/day/{code}.png", use_container_width=True)
                    with t2: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/week/{code}.png", use_container_width=True)
                    with t3:
                        theme_stocks = get_all_theme_stocks()
                        cur_theme_list = theme_stocks[theme_stocks['테마명']==s_theme]
                        st.dataframe(cur_theme_list[['테마순위','종목명','현재가(등락률)']], hide_index=True)
                with col2:
                    st.markdown("##### 📰 최신 뉴스")
                    for i, n in enumerate(news_list):
                        st.markdown(f"{i+1}. [{n['제목']}]({n['링크']})")

        else: st.warning("현재 조건(거래량 200위 & 상승률 300위)을 동시에 만족하는 종목이 없습니다.")
    except Exception as e: st.error(f"오류: {e}")

# --- [Tab 2] 시황 분석 (신규 추가) ---
with tab2:
    st.header("📊 시장 전체 흐름 (시총 Top 150)")
    if st.button("데이터 가져오기", key="btn_market"):
        st.session_state.df_market = get_market_cap_top150()
    
    if "df_market" in st.session_state and st.session_state.df_market is not None:
        st.dataframe(st.session_state.df_market, height=400)
        if st.button("📢 AI 시황 브리핑"):
            with st.spinner("시장 분석 중..."):
                search_data, _ = search_news_robust("오늘 주식 시황 특징주")
                st.write_stream(analyze_market_trend_ai(st.session_state.df_market, search_data, selected_real_name))
