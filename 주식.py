import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import google.generativeai as genai
from duckduckgo_search import DDGS
from urllib.parse import urlparse

# ==========================================
# 🔑 [필수] Gemini API 키 설정
# ==========================================
try:
    GOOG_API_KEY = st.secrets["GOOG_API_KEY"]
except:
    GOOG_API_KEY = "여기에_키를_넣으세요"

# 1. 페이지 설정
st.set_page_config(page_title="AI 주식 투자 비서", layout="wide")
st.title("🤖 AI 주식 투자 전략가 (Final Fix Ver.)")

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

# --- [뉴스 검색 함수] ---
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
        # [핵심 수정] 네이버 차단 방지용 헤더 강화
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://finance.naver.com/'
        }
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
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        data = []
        for row in soup.select("#contentarea_left > table.type_1 > tr"):
            cols = row.select("td")
            if len(cols) >= 4:
                data.append({"테마명": cols[0].text.strip(), "링크": "https://finance.naver.com" + cols[0].find('a')['href']})
        return pd.DataFrame(data).head(20)
    except: return pd.DataFrame()

def get_theme_details(theme_link):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    try:
        res = requests.get(theme_link, headers=headers)
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        stocks = []
        for row in soup.select("table.type_5 > tbody > tr"):
            cols = row.select("td")
            if len(cols) > 4:
                name_tag = cols[0].find('a')
                if not name_tag: continue
                name = name_tag.text.strip()
                link = "https://finance.naver.com" + name_tag['href']
                
                # [검증] 종목코드 추출 정규식
                code_match = re.search(r'code=([0-9]+)', link)
                code = code_match.group(1) if code_match else ""
                
                price = cols[2].text.strip()
                rate = cols[4].text.strip().replace('\n', '').strip()
                # 화면에 코드도 같이 표시해서 맞는지 확인
                display_name = f"{name} ({code})" 
                stocks.append({'name': name, 'display_name': display_name, 'code': code, 'price_str': f"{price} ({rate})", 'link': theme_link})
        return stocks
    except: return []

@st.cache_data
def get_all_theme_stocks():
    df_themes = get_naver_themes()
    all_stocks = []
    for index, row in df_themes.iterrows():
        stocks_info = get_theme_details(row['링크'])
        stocks_info.sort(key=lambda x: float(x['price_str'].split('(')[1].replace('%)','').replace('+','').replace('-','-').replace(',','')) if '(' in x['price_str'] else 0, reverse=True)
        for rank, stock in enumerate(stocks_info, 1):
             all_stocks.append({
                 "테마순위": f"{rank}위", 
                 "종목명": stock['name'],       # 원본 이름 (매칭용)
                 "종목표기": stock['display_name'], # 화면 표시용 (코드 포함)
                 "종목코드": stock['code'], 
                 "테마명": row['테마명'], 
                 "현재가(등락률)": stock['price_str']
             })
    return pd.DataFrame(all_stocks)

@st.cache_data
def get_top_risers_info():
    market_map = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}", headers=headers)
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:300]: 
                market_map[item.text.strip()] = "KOSPI" if s==0 else "KOSDAQ"
        except: pass
    return market_map

@st.cache_data
def get_volume_leaders():
    tickers = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_quant_high.naver?sosok={s}", headers=headers)
            soup = BeautifulSoup(res.text, 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:200]: 
                tickers.append(item.text.strip())
        except: pass
    return tickers

def get_stock_fundamentals(code):
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Referer': f'https://finance.naver.com/item/main.naver?code={code}'
        }
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        cap_elem = soup.select_one("#_market_sum")
        if cap_elem:
            raw_cap = cap_elem.text.strip()
            raw_cap = raw_cap.replace('\t', '').replace('\n', '') + "억"
            return {"시가총액": raw_cap}
    except: pass
    return {"시가총액": "-"}

# [핵심 수정] 뉴스 크롤링 기능 강력 보완
def get_latest_news(code):
    try:
        url = f"https://finance.naver.com/item/news_news.naver?code={code}"
        # [수정] 차단 방지를 위한 완벽한 헤더
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': f'https://finance.naver.com/item/main.naver?code={code}'
        }
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        
        news_list = []
        # [수정] 네이버 뉴스 구조 변경에 대응하는 다중 선택자
        articles = soup.select(".title > a")
        if not articles:
            articles = soup.select("a.tit")
        if not articles:
            articles = soup.select("td.title > a")
            
        for a in articles[:15]:
            title = a.text.strip()
            link = a['href']
            if link.startswith('/'): 
                link = "https://finance.naver.com" + link
            news_list.append({"제목": title, "링크": link})
            
        return news_list
    except: return []

# --- [AI 응답] ---
def get_gemini_response_robust(messages, model_name, use_search, stock_name, theme):
    genai.configure(api_key=GOOG_API_KEY)
    current_query = messages[-1]['content']
    search_res = ""
    if use_search and "당신은" in current_query:
        # 네이버 뉴스가 비어있을 경우 대비하여 DuckDuckGo도 병행
        data = search_news_robust(f"{stock_name} {theme} 호재 전망", limit=5)
        search_res = f"\n[웹 검색 데이터]:\n{data}\n"
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
    ## 💡 투자 전략
    """
    response = model.generate_content(prompt, stream=True)
    for chunk in response: yield chunk.text

# --- [시총 데이터 함수] ---
@st.cache_data
def get_market_cap_top150():
    stocks = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
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
# 🖥️ 메인 실행
# ==========================================
with st.sidebar:
    st.header("🔍 설정")
    if st.button("🔄 데이터 새로고침 (클릭)"):
        st.cache_data.clear()
        st.rerun() # 새로고침 즉시 반영
    
    if GOOG_API_KEY.startswith("AIza"):
        models = get_available_gemini_models(GOOG_API_KEY)
        model_name = st.selectbox("모델 선택", models, index=0)
        selected_real_name = model_name.split(" ")[1] if " " in model_name else model_name
    else:
        st.error("API 키 필요")
        selected_real_name = "gemini-1.5-flash"
    use_grounding = st.checkbox("🌍 심층 검색 사용", value=True)

# 초기 로딩
with st.status("🚀 시장 데이터를 수집 중입니다... (네이버 금융 & 뉴스)", expanded=True) as status:
    df_market = get_market_cap_top150()
    market_map = get_top_risers_info()
    vol_leaders = get_volume_leaders()
    df_C = get_all_theme_stocks()
    status.update(label="✅ 데이터 수집 완료!", state="complete", expanded=False)

tab1, tab2 = st.tabs(["🎯 급등주 발굴 (Core)", "📊 시황 분석"])

# --- Tab 1 ---
with tab1:
    st.subheader("1️⃣ 교집합 분석 결과")
    
    list_A = list(market_map.keys())
    list_B = vol_leaders
    
    final_candidates = []
    for index, row in df_C.iterrows():
        stock_name = row['종목명']
        if (stock_name in list_A) and (stock_name in list_B):
            market_type = market_map.get(stock_name, "Unknown")
            row_data = row.to_dict()
            row_data['시장구분'] = market_type
            final_candidates.append(row_data)
    
    if final_candidates:
        df_final = pd.DataFrame(final_candidates)
        df_final = df_final.drop_duplicates(['종목명'])
        df_final = df_final.sort_values(by="테마명")
        
        # [수정] 종목표기(코드포함) 컬럼 사용
        display_cols = ['테마순위', '시장구분', '종목표기', '현재가(등락률)', '테마명']
        
        event = st.dataframe(
            df_final[display_cols], 
            use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row"
        )
        
        st.divider()
        
        if len(event.selection.rows) > 0:
            sel_idx = event.selection.rows[0]
            sel_data = df_final.iloc[sel_idx]
            
            s_name = sel_data['종목명']
            code = sel_data['종목코드']
            s_theme = sel_data['테마명']
            
            if st.session_state.last_code != code:
                st.session_state.messages = []
                st.session_state.last_code = code

            with st.spinner(f'🔍 [{s_name}({code})] 뉴스 및 재무 정보 가져오는 중...'):
                fund = get_stock_fundamentals(code)
                news_list = get_latest_news(code) # 여기서 뉴스 수집
            
            st.subheader(f"2️⃣ [{s_name}] 상세 분석")
            st.info(f"💰 시가총액: **{fund['시가총액']}** | 🏆 테마: **{s_theme}**")
            
            with st.expander("💬 AI 투자 전략가와 대화하기 (Click)", expanded=True):
                if not st.session_state.messages:
                    if st.button(f"⚡ '{s_name}' 심층 분석 시작"):
                        news_ctx = "\n".join([f"- {n['제목']}" for n in news_list])
                        if not news_list: news_ctx = "(네이버 뉴스 수집 실패 - DuckDuckGo 검색으로 대체합니다)"
                        
                        sys_prompt = f"""
                        당신은 공격적인 투자 전략가입니다. {s_name}({s_theme})을 호재 위주로 분석하세요.
                        [뉴스 데이터]: {news_ctx}
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
                    cur_theme_list = df_C[df_C['테마명']==s_theme]
                    st.dataframe(cur_theme_list[['테마순위','종목명','현재가(등락률)']], hide_index=True)
            with col2:
                st.markdown("##### 📰 최신 뉴스 (네이버 금융)")
                if news_list:
                    for i, n in enumerate(news_list):
                        st.markdown(f"{i+1}. [{n['제목']}]({n['링크']})")
                else:
                    st.warning("⚠️ 네이버 뉴스를 가져오지 못했습니다. (차단 혹은 데이터 없음)")
                    st.caption("AI 분석 시에는 웹 검색으로 대체 정보를 찾습니다.")
    else:
        st.warning("조건을 만족하는 종목이 없습니다.")

# --- Tab 2 ---
with tab2:
    st.header("📊 시장 전체 흐름 (시총 Top 150)")
    if df_market is not None:
        st.dataframe(df_market, height=400)
        
        st.subheader("🤖 AI 실시간 시황 브리핑")
        if st.button("📢 뉴스 30개 수집 및 분석 시작"):
            with st.spinner("1. DuckDuckGo 검색 중..."):
                ddg_data = search_news_robust("금일 코스피 코스닥 시황 특징주", limit=15)
            with st.spinner("2. 네이버 시황 뉴스 수집 중..."):
                naver_data = get_naver_market_news(limit=15)
            
            combined_news = f"--- [DuckDuckGo] ---\n{ddg_data}\n\n--- [네이버 시황] ---\n{naver_data}"
            with st.expander(f"🔍 뉴스 원문 보기", expanded=True):
                st.text(combined_news)
            with st.spinner("3. AI 분석 중..."):
                st.write_stream(analyze_market_trend_ai(df_market, combined_news, selected_real_name))
