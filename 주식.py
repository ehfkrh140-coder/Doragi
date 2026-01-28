import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import google.generativeai as genai
from duckduckgo_search import DDGS

# ==========================================
# 🔑 [기본 설정] API 키 및 페이지
# ==========================================
try:
    GOOG_API_KEY = st.secrets["GOOG_API_KEY"]
except:
    GOOG_API_KEY = "여기에_키를_넣으세요" # 로컬 테스트용

st.set_page_config(page_title="AI 주식 투자 비서", layout="wide")
st.title("🤖 AI 주식 투자 전략가 (Market & Stock)")

# --- [유틸리티] 모델 목록 가져오기 ---
@st.cache_data
def get_available_models(api_key):
    try:
        genai.configure(api_key=api_key)
        return [m.name.replace("models/", "") for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    except: return ["gemini-1.5-flash"]

# --- [검색 함수] DuckDuckGo + 네이버 뉴스 ---
def robust_search(keyword):
    search_context = ""
    try:
        # 뉴스 검색 시도
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

# --- [데이터 수집 1] 기존: 테마, 거래량, 급등주 ---
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

def get_theme_stocks(link):
    try:
        res = requests.get(link, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'cp949'
        soup = BeautifulSoup(res.text, 'html.parser')
        stocks = []
        for row in soup.select("table.type_5 > tbody > tr"):
            cols = row.select("td")
            if len(cols) > 4:
                name = cols[0].text.strip()
                code = re.search(r'code=([0-9]+)', cols[0].find('a')['href']).group(1)
                price = cols[2].text.strip()
                rate = cols[4].text.strip()
                stocks.append({'종목명': name, '코드': code, '현재가': price, '등락률': rate})
        return stocks
    except: return []

@st.cache_data
def get_market_rankings():
    # 거래량 상위 & 상승률 상위 가져오기 (범위 확대: 200위/300위)
    vol_stocks, rise_stocks = set(), set()
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 1. 거래량 상위 (코스피/코스닥)
    for s in [0, 1]:
        try:
            url = f"https://finance.naver.com/sise/sise_quant_high.naver?sosok={s}"
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.text, 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:200]: # 200위까지
                vol_stocks.add(item.text.strip())
        except: pass
        
    # 2. 상승률 상위
    for s in [0, 1]:
        try:
            url = f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}"
            res = requests.get(url, headers=headers) # 인코딩 이슈 자동 처리
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:300]: # 300위까지
                rise_stocks.add(item.text.strip())
        except: pass
            
    return vol_stocks, rise_stocks

# --- [데이터 수집 2] 신규: 시가총액 상위 150위 ---
@st.cache_data
def get_market_cap_top150():
    stocks = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 코스피(0) 기준 1~3페이지 (페이지당 50개)
    for page in range(1, 4):
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page={page}"
        try:
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            rows = soup.select("table.type_2 tbody tr")
            for row in rows:
                cols = row.select("td")
                if len(cols) < 10: continue # 빈 줄 제외
                try:
                    rank = cols[0].text.strip()
                    name = cols[1].text.strip()
                    code = re.search(r'code=([0-9]+)', cols[1].find('a')['href']).group(1)
                    price = cols[2].text.strip()
                    rate = cols[4].text.strip().replace("\n", "").strip()
                    mkt_cap = cols[6].text.strip() # 시가총액
                    
                    # 상승/하락 기호 정리
                    if "상승" in rate: rate = "+" + rate.replace("상승", "").strip()
                    elif "하락" in rate: rate = "-" + rate.replace("하락", "").strip()
                    
                    stocks.append({
                        "순위": rank, "종목명": name, "코드": code, 
                        "현재가": price, "등락률": rate, "시가총액(억)": mkt_cap
                    })
                except: pass
        except: pass
    return pd.DataFrame(stocks)

# --- [AI 분석 함수] 1. 개별 종목 분석 ---
def analyze_stock_detail(name, theme, news_text, search_text, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    prompt = f"""
    당신은 20년 경력의 펀드매니저입니다. {name} 종목(테마: {theme})을 분석하세요.
    
    [뉴스 데이터]: {news_text}
    [웹 검색 데이터]: {search_text}
    
    분석 포인트:
    1. 🚀 핵심 호재 3가지 (상승 재료)
    2. 📈 차트/수급 관점 (간략히)
    3. 💡 매매 전략 (목표가/손절가 제안 포함)
    """
    response = model.generate_content(prompt, stream=True)
    for chunk in response: yield chunk.text

# --- [AI 분석 함수] 2. 시장(시황) 분석 ---
def analyze_market_trend(df_top, search_text, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    
    # 상위 종목들의 흐름을 텍스트로 요약해서 AI에게 전달
    # (토큰 절약을 위해 상위 20개 + 등락률 큰 순서 일부만 발췌)
    top_20 = df_top.head(20).to_string(index=False)
    
    # 150위 내에서 가장 많이 오른 5개, 떨어진 5개 추출
    try:
        df_sorted = df_top.copy()
        df_sorted['numeric_rate'] = df_sorted['등락률'].str.replace('%','').str.replace('+','').str.replace('-','-').astype(float)
        top_gainers = df_sorted.nlargest(5, 'numeric_rate')[['종목명', '등락률']].to_string(index=False)
        top_losers = df_sorted.nsmallest(5, 'numeric_rate')[['종목명', '등락률']].to_string(index=False)
    except:
        top_gainers = "데이터 추출 실패"
        top_losers = "데이터 추출 실패"

    prompt = f"""
    당신은 거시경제와 주식 시장 전체를 읽는 수석 애널리스트입니다.
    오늘 대한민국 증시의 '시가총액 상위 150위' 흐름과 '뉴스 검색 결과'를 바탕으로 현재 시황을 브리핑하세요.

    [시총 상위 20위 흐름]:
    {top_20}
    
    [150위 내 급등주 Top 5]: {top_gainers}
    [150위 내 급락주 Top 5]: {top_losers}
    
    [주요 시황 뉴스]:
    {search_text}
    
    작성 양식:
    ## 📊 오늘의 증시 요약 (한줄평)
    ## 🌍 메인 주도 테마 및 섹터 분석
    - 오늘 시장을 이끄는 업종은 무엇인가? (반도체, 바이오, 2차전지 등 시총 상위주 움직임 기반)
    - 특징적인 수급 쏠림 현상 분석
    ## 📰 주요 이슈 체크
    - 검색된 뉴스를 바탕으로 시장에 영향을 준 거시 경제 이슈(금리, 환율, 해외 증시 등) 언급
    ## 💡 투자자 대응 전략
    - 현재 장세에서의 포트폴리오 전략 (공격적 매수 vs 관망 등)
    """
    
    response = model.generate_content(prompt, stream=True)
    for chunk in response: yield chunk.text

# ==========================================
# 🖥️ 메인 화면 구성
# ==========================================

# 사이드바 설정
with st.sidebar:
    st.header("⚙️ 설정 패널")
    if GOOG_API_KEY.startswith("AIza"):
        models = get_available_models(GOOG_API_KEY)
        selected_model = st.selectbox("AI 모델", models, index=0)
    else:
        st.error("API 키를 확인하세요!")
        selected_model = "gemini-1.5-flash"
    
    st.info("데이터 범위: 거래량 상위 200위 / 상승률 상위 300위 / 시총 상위 150위")

# 탭 구성
tab1, tab2 = st.tabs(["🎯 급등주/테마 발굴", "📊 시총 상위 & 시황 분석"])

# --- [Tab 1] 기존 기능: 교집합 종목 발굴 ---
with tab1:
    st.header("🎯 교집합 급등주 발굴")
    if st.button("데이터 분석 시작 (Tab 1)", key="btn1"):
        with st.spinner("시장 데이터를 샅샅이 뒤지는 중..."):
            vol_set, rise_set = get_market_rankings()
            df_themes = get_naver_themes()
            
            final_list = []
            
            # 테마별 종목 순회
            progress = st.progress(0)
            for idx, row in df_themes.iterrows():
                t_name = row['테마명']
                stocks = get_theme_stocks(row['링크'])
                
                for s in stocks:
                    # 교집합 조건 체크
                    if (s['종목명'] in vol_set) and (s['종목명'] in rise_set):
                        final_list.append({
                            "테마": t_name, "종목명": s['종목명'], 
                            "현재가": s['현재가'], "등락률": s['등락률'], "코드": s['코드']
                        })
                progress.progress((idx + 1) / len(df_themes))
            
            if final_list:
                df_result = pd.DataFrame(final_list).drop_duplicates('종목명')
                st.success(f"조건 만족 종목 {len(df_result)}개 발견!")
                st.dataframe(df_result)
                
                # 개별 분석 기능
                selected_stock = st.selectbox("심층 분석할 종목 선택", df_result['종목명'].unique())
                if st.button(f"⚡ {selected_stock} AI 분석"):
                    row = df_result[df_result['종목명'] == selected_stock].iloc[0]
                    with st.spinner(f"{selected_stock} 뉴스 및 정보 수집 중..."):
                        search_q = f"{selected_stock} {row['테마']} 주가 전망"
                        search_data = robust_search(search_q)
                        st.write_stream(analyze_stock_detail(selected_stock, row['테마'], "네이버뉴스 데이터", search_data, selected_model))
            else:
                st.warning("조건을 만족하는 종목이 없습니다. (필터링 범위를 300위까지 넓혔으나 발견되지 않음)")

# --- [Tab 2] 신규 기능: 시총 상위 & 시황 분석 ---
with tab2:
    st.header("📊 시장 전체 흐름 (시총 Top 150)")
    
    if "df_market" not in st.session_state:
        st.session_state.df_market = None

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        if st.button("시황 데이터 가져오기", key="btn2"):
            with st.spinner("코스피 시가총액 상위 150개를 가져오는 중..."):
                st.session_state.df_market = get_market_cap_top150()
    
    if st.session_state.df_market is not None:
        df = st.session_state.df_market
        
        # 1. 데이터 표시
        st.dataframe(df, height=300)
        
        # 2. AI 시황 분석 버튼
        st.subheader("🤖 AI 수석 애널리스트의 시장 브리핑")
        if st.button("📢 현재 시장 상황 분석하기"):
            with st.spinner("주요 뉴스 검색 및 수급 분석 중..."):
                # 검색어 설정
                search_keywords = "오늘 주식 시황 주도 테마 특징주"
                search_data = robust_search(search_keywords)
                
                # 분석 실행
                st.write_stream(analyze_market_trend(df, search_data, selected_model))
                
                # 검색된 뉴스 출처 표시
                with st.expander("참고한 뉴스 데이터 보기"):
                    st.text(search_data)
