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
st.set_page_config(page_title="AI 주식 투자 비서", layout="wide")
st.title("🤖 AI 주식 투자 전략가 (All-in-One Ver.)")

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
        return [m.name.replace("models/", "") for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    except: return ["gemini-1.5-flash"]

def extract_code(link):
    match = re.search(r'code=([a-zA-Z0-9]+)', link)
    if match: return match.group(1)
    return None

# --- [검색 함수] ---
def search_news_robust(keyword, limit=15):
    search_context = ""
    try:
        results = list(DDGS().news(keywords=keyword, region='kr-kr', max_results=limit))
        if len(results) < limit:
            results.extend(list(DDGS().text(keywords=keyword, region='kr-kr', max_results=limit)))
            results = results[:limit]
        for i, res in enumerate(results):
            title = res.get('title', '-')
            body = res.get('body', res.get('snippet', '-'))
            search_context += f"[DDG-{i+1}] {title}: {body}\n"
    except: search_context += "검색 데이터 없음\n"
    return search_context

def get_naver_market_news(limit=15):
    news_context = ""
    try:
        url = "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers)
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        articles = soup.select("dd.articleSubject > a") + soup.select("dt.articleSubject > a")
        summaries = soup.select("dd.articleSummary")
        count = 0
        for art, sum_text in zip(articles, summaries):
            if count >= limit: break
            news_context += f"[네이버시황-{count+1}] {art.text.strip()} // {sum_text.text.strip()[:100]}\n"
            count += 1
    except: pass
    return news_context

# --- [데이터 수집 함수들] ---
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

def get_theme_details(link):
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
                stocks.append({'name': name, 'code': code, 'price_str': f"{price} ({rate})", 'link': link})
        return stocks
    except: return []

@st.cache_data
def get_market_rankings():
    market_map = {}
    vol_leaders = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 상승률 (300위)
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}", headers=headers)
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:300]: 
                market_map[item.text.strip()] = "KOSPI" if s==0 else "KOSDAQ"
        except: pass
    # 거래량 (200위)
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_quant_high.naver?sosok={s}", headers=headers)
            soup = BeautifulSoup(res.text, 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:200]: 
                vol_leaders.append(item.text.strip())
        except: pass
    return market_map, vol_leaders

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

# [복구된 함수] 오류의 원흉이었던 함수 재정의
@st.cache_data
def get_all_theme_stocks():
    df_themes = get_naver_themes()
    all_stocks = []
    for _, row in df_themes.iterrows():
        stocks_info = get_theme_details(row['링크'])
        for rank, stock in enumerate(stocks_info, 1):
             all_stocks.append({
                 "테마순위": f"{rank}위", "종목명": stock['name'], 
                 "현재가(등락률)": stock['price_str'], "테마명": row['테마명']
             })
    return pd.DataFrame(all_stocks)

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

# --- [AI 응답] ---
def get_gemini_response_robust(messages, model_name, use_search, stock_name, theme):
    genai.configure(api_key=GOOG_API_KEY)
    current_query = messages[-1]['content']
    search_res = ""
    if use_search:
        data = search_news_robust(f"{stock_name} {theme} 호재 전망", limit=5)
        search_res = f"\n[검색 데이터]:\n{data}\n"
    modified_msgs = []
    for i, msg in enumerate(messages):
        content = msg['content']
        if i == len(messages)-1: content += search_res
        modified_msgs.append({"role": "user" if msg['role']=="user" else "model", "parts": [content]})
    
    model = genai.GenerativeModel(f"models/{model_name}")
    response = model.generate_content(modified_msgs, stream=True)
    for chunk in response: yield chunk.text

def analyze_market_trend_ai(df, news_data, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    top_20 = df.head(20).to_string(index=False)
    prompt = f"""
    당신은 수석 애널리스트입니다. 
    [실시간 시황 뉴스 30건]과 [시총 상위주 흐름]을 종합하여 시장 상황을 브리핑하세요.
    
    [코스피 상위 20위]: {top_20}
    [실시간 뉴스]: {news_data}
    
    ## 📰 증시 핵심 요약
    ## 🌍 섹터별 수급 분석
    ## 💡 34세 직장인을 위한 투자 전략
    """
    response = model.generate_content(prompt, stream=True)
    for chunk in response: yield chunk.text

# ==========================================
# 🖥️ 메인 실행 및 데이터 통합 로딩
# ==========================================

# 1. 사이드바 설정
with st.sidebar:
    st.header("🔍 설정")
    if GOOG_API_KEY.startswith("AIza"):
        models = get_available_gemini_models(GOOG_API_KEY)
        model_name = st.selectbox("모델 선택", models, index=0)
        selected_real_name = model_name.split(" ")[1] if " " in model_name else model_name
    else:
        st.error("API 키 필요")
        selected_real_name = "gemini-1.5-flash"
    use_grounding = st.checkbox("🌍 심층 검색 사용", value=True)

# 2. [핵심] 앱 시작 시 데이터 일괄 수집 (Progress Bar)
with st.status("🚀 전체 시장 데이터 수집 및 분석 중... (잠시만 기다려주세요)", expanded=True) as status:
    st.write("1. 네이버 테마 및 시장 랭킹(거래량/상승률) 수집 중...")
    market_map, vol_leaders = get_market_rankings()
    df_themes = get_naver_themes()
    
    st.write("2. 교집합(급등주) 분석 및 정렬 중...")
    final_candidates = []
    for index, row in df_themes.iterrows():
        stocks_info = get_theme_details(row['링크'])
        for s in stocks_info:
            if (s['name'] in market_map) and (s['name'] in vol_leaders):
                final_candidates.append({
                    "테마순위": f"{index+1}위", "시장구분": market_map[s['name']],
                    "종목명": s['name'], "종목코드": s['code'],
                    "현재가(등락률)": s['price_str'], "테마명": row['테마명']
                })
    df_final = pd.DataFrame(final_candidates)
    if not df_final.empty:
        # [요청] 테마명 정렬
        df_final = df_final.sort_values(by="테마명")
        df_final = df_final.drop_duplicates(['종목명'])

    st.write("3. 시가총액 상위 150위 데이터 수집 중...")
    df_market = get_market_cap_top150()
    
    # [중요] 미리 캐싱 (에러 방지용)
    st.write("4. 테마 전체 상세 데이터 구성 중...")
    _ = get_all_theme_stocks()
    
    status.update(label="✅ 모든 데이터 수집 완료!", state="complete", expanded=False)

# 3. 탭 구성
tab1, tab2 = st.tabs(["🎯 급등주 발굴", "📊 시황 분석"])

# --- Tab 1: 교집합 분석 ---
with tab1:
    st.subheader("1️⃣ 교집합 분석 결과 (테마별 정렬)")
    
    if not df_final.empty:
        event = st.dataframe(
            df_final[['테마명', '종목명', '현재가(등락률)', '시장구분']], 
            use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row"
        )
        st.divider()
        
        # 선택 시 상세 정보 표시
        if len(event.selection.rows) > 0:
            sel_idx = event.selection.rows[0]
            sel_data = df_final.iloc[sel_idx]
            s_name = sel_data['종목명']
            code = sel_data['종목코드']
            s_theme = sel_data['테마명']
            
            # 세션 초기화
            if st.session_state.last_code != code:
                st.session_state.messages = []
                st.session_state.last_code = code

            with st.spinner(f"🔍 {s_name} 상세 정보(뉴스/차트) 로딩 중..."):
                fund = get_stock_fundamentals(code)
                news_list = get_latest_news(code)
            
            st.subheader(f"2️⃣ [{s_name}] 상세 분석")
            st.info(f"💰 시가총액: **{fund['시가총액']}** | 🏆 테마: **{s_theme}**")
            
            with st.expander("💬 AI 투자 전략가와 대화하기", expanded=True):
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

                if prompt := st.chat_input(f"{s_name} 질문..."):
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
                    # [오류 해결] 이제 get_all_theme_stocks가 정의되어 있으므로 정상 작동
                    theme_stocks = get_all_theme_stocks()
                    cur_theme_list = theme_stocks[theme_stocks['테마명']==s_theme]
                    st.dataframe(cur_theme_list[['테마순위','종목명','현재가(등락률)']], hide_index=True)
            with col2:
                st.markdown("##### 📰 최신 뉴스")
                for i, n in enumerate(news_list):
                    st.markdown(f"{i+1}. [{n['제목']}]({n['링크']})")
    else:
        st.warning("조건(거래량 200위 & 상승률 300위 & 테마상위)을 만족하는 종목이 없습니다.")

# --- Tab 2: 시황 분석 ---
with tab2:
    st.header("📊 시장 전체 흐름 (시총 Top 150)")
    if df_market is not None:
        st.dataframe(df_market, height=400)
        
        st.subheader("🤖 AI 실시간 시황 브리핑")
        if st.button("📢 뉴스 30개 수집 및 분석 시작"):
            # 1. DuckDuckGo 15개
            with st.spinner("1. DuckDuckGo: '금일 코스피 코스닥 시황' 검색 중 (15건)..."):
                ddg_data = search_news_robust("금일 코스피 코스닥 시황 특징주", limit=15)
            
            # 2. 네이버 시황 15개
            with st.spinner("2. 네이버 금융: 실시간 시황 뉴스 수집 중 (15건)..."):
                naver_data = get_naver_market_news(limit=15)
            
            combined_news = f"--- [DuckDuckGo] ---\n{ddg_data}\n\n--- [네이버 시황] ---\n{naver_data}"
            
            with st.expander(f"🔍 AI가 읽은 뉴스 원문 보기 (총 30건)", expanded=True):
                st.text(combined_news)
                
            with st.spinner("3. AI 분석 중..."):
                st.write_stream(analyze_market_trend_ai(df_market, combined_news, selected_real_name))
