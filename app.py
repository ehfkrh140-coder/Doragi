import streamlit as st
import os
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
    GOOG_API_KEY = "여기에_발급받은_API_키를_붙여넣으세요" # 로컬 테스트용

# 1. 페이지 설정
st.set_page_config(page_title="주식 테마 분석기 (AI Ver.)", layout="wide")
st.title("🤖 AI 주식 투자 전략가 (Hojae Focus + Data View)")

# --- [모델 목록] ---
@st.cache_data
def get_available_gemini_models(api_key):
    try:
        genai.configure(api_key=api_key)
        models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                name = m.name.replace("models/", "")
                display_name = name
                if "1.5-flash" in name and "latest" not in name and "8b" not in name:
                     display_name = f"✅ {name} (추천:무한체력)"
                elif "2.0" in name or "exp" in name:
                     display_name = f"🧪 {name} (최신/체력약함)"
                models.append(display_name)
        models.sort(key=lambda x: "✅" not in x)
        return models
    except:
        return ["✅ gemini-1.5-flash (기본)", "gemini-pro"]

# --- [뉴스 본문 읽기] ---
def fetch_url_content(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=3)
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        content = " ".join([p.text.strip() for p in paragraphs])
        if len(content) < 50: return None
        # [데이터량 조절] 너무 길면 2000자에서 자름
        return content[:2000] + "..." if len(content) > 2000 else content
    except: return None

# --- [뉴스 검색] 데이터량 확인 기능 추가 ---
def search_news_strict(keyword):
    search_context = ""
    total_chars = 0 # 글자수 세기
    
    try:
        results = list(DDGS().news(keywords=keyword, region='kr-kr', safesearch='off', max_results=7))
        if results:
            for i, res in enumerate(results):
                title = res.get('title', '-')
                link = res.get('url', res.get('href', ''))
                source = res.get('source', 'News') 
                date = res.get('date', '')
                
                full_body = None
                tag = "📄 [요약]"
                content_to_use = res.get('body', res.get('snippet', ''))

                # 상위 3개 본문 읽기
                if i < 3 and link: 
                    full_body = fetch_url_content(link)
                
                if full_body:
                    content_to_use = f"Analyzed Full Text: {full_body}"
                    tag = "📖 [본문 완독]"
                
                entry = f"[{i+1}] [{source}] {title} ({date}) {tag}\n내용: {content_to_use}\n\n"
                search_context += entry
                total_chars += len(entry) # 글자수 누적
        else:
            search_context = "관련된 뉴스 기사가 없습니다."
        
        return search_context, total_chars # 텍스트와 글자수 반환
    except Exception as e:
        return f"뉴스 검색 중 오류: {e}", 0

# --- [사이드바] ---
with st.sidebar:
    st.header("🔍 컨트롤 패널")
    if st.button("데이터 새로고침 🔄"):
        st.cache_data.clear()
        if "messages" in st.session_state:
            st.session_state.messages = []
    st.markdown("---")
    
    if GOOG_API_KEY.startswith("AIza"):
        model_options = get_available_gemini_models(GOOG_API_KEY)
        selected_display = st.selectbox("사용할 모델:", model_options, index=0)
        selected_real_name = selected_display.split(" ")[1] if " " in selected_display else selected_display
    else:
        st.error("API 키 필요")
        selected_real_name = "gemini-1.5-flash"
    
    use_grounding = st.checkbox("🌍 심층 뉴스 검색(Deep Search)", value=True)
    st.info(f"선택됨: `{selected_real_name}`")

# --- 유틸리티 ---
def extract_code(link):
    match = re.search(r'code=([a-zA-Z0-9]+)', link)
    if match: return match.group(1)
    return None

def clean_text(text):
    if not text: return "-"
    return re.sub(r'[^가-힣0-9a-zA-Z.]', '', text)

# --- [AI 응답 함수] 프롬프트 강력 수정 ---
def get_gemini_response_hojae(messages, model_name, use_search, stock_name, theme):
    genai.configure(api_key=GOOG_API_KEY)
    
    # 1. 검색 실행 (데이터량 시각화 포함)
    current_query = messages[-1]['content']
    is_system_prompt = "당신은" in current_query
    
    search_result_text = ""
    data_log = ""
    
    if use_search:
        if is_system_prompt:
            search_query = f"{stock_name} {theme} 주가 전망 호재 특징주"
        else:
            search_query = f"{stock_name} {current_query}"
        
        with st.spinner(f"📰 '{search_query}' 관련 호재를 채굴 중..."):
            search_data, char_count = search_news_strict(search_query)
            
            # [시각화] 데이터량 보여주기
            with st.expander(f"📊 AI가 읽은 데이터량: 총 {char_count:,}자 (클릭해서 원문 보기)"):
                st.info(f"뉴스 7건 (상위 3건 본문 포함)을 분석합니다. 이는 원고지 약 {char_count // 200}장 분량입니다.")
                st.text(search_data)
                
        search_result_text = f"\n\n[📰 검색된 최신 뉴스 데이터]:\n{search_data}\n"

    # 2. 메시지 구성
    modified_messages = []
    for i, msg in enumerate(messages):
        content = msg['content']
        if i == len(messages) - 1 and use_search:
            content += search_result_text
        role = "user" if msg["role"] == "user" else "model"
        modified_messages.append({"role": role, "parts": [content]})

    # 3. 모델 호출 (자동 복구)
    target_models = [model_name, "gemini-1.5-flash"]
    if "1.5-flash" in model_name: target_models = ["gemini-1.5-flash"]

    for m_name in target_models:
        try:
            full_name = f"models/{m_name}" if "models/" not in m_name else m_name
            model = genai.GenerativeModel(full_name)
            response = model.generate_content(modified_messages, stream=True)
            for chunk in response:
                yield chunk.text
            break
        except Exception as e:
            if "Quota" in str(e) or "429" in str(e):
                if m_name != target_models[-1]:
                    yield f"\n\n⚠️ **[{m_name}] 용량 초과! 튼튼한 1.5-flash로 전환합니다...**\n\n"
                    time.sleep(1)
                    continue
                else: yield "🚨 모든 모델 할당량 초과."
            else: yield f"오류: {e}"

# --- 데이터 수집 함수들 (생략 없이 포함) ---
@st.cache_data
def get_naver_themes():
    url = "https://finance.naver.com/sise/theme.naver"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers)
        response.encoding = 'cp949' 
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.select("#contentarea_left > table.type_1 > tr")
        data = []
        for row in rows:
            cols = row.select("td")
            if len(cols) >= 4:
                theme_name = cols[0].text.strip()
                link = "https://finance.naver.com" + cols[0].find('a')['href']
                data.append({"테마명": theme_name, "링크": link})
        return pd.DataFrame(data).head(20)
    except: return pd.DataFrame()

def get_theme_details(theme_link):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(theme_link, headers=headers)
        response.encoding = 'cp949'
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.select("table.type_5 > tbody > tr")
        stock_info = []
        for row in rows:
            cols = row.select("td")
            if len(cols) > 4: 
                name_tag = cols[0].find('a')
                if not name_tag: continue
                name = name_tag.text.strip()
                link = "https://finance.naver.com" + name_tag['href']
                code = extract_code(link)
                price = cols[2].text.strip()
                diff_rate_str = cols[4].text.strip().replace('\n', '').strip()
                try: diff_rate_val = float(diff_rate_str.replace('%', '').replace('+', ''))
                except: diff_rate_val = -999.0
                formatted_price = f"{price} ({diff_rate_str})"
                stock_info.append({'name': name, 'code': code, 'price_str': formatted_price, 'diff_rate_val': diff_rate_val, 'link': link})
        return stock_info
    except: return []

@st.cache_data
def get_all_theme_stocks():
    df_themes = get_naver_themes()
    all_stocks = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(df_themes)
    for index, row in df_themes.iterrows():
        theme_name = row['테마명']
        theme_link = row['링크']
        status_text.text(f"데이터 수집 중... ({index+1}/{total}): {theme_name}")
        progress_bar.progress((index + 1) / total)
        stocks_info = get_theme_details(theme_link)
        stocks_info.sort(key=lambda x: x['diff_rate_val'], reverse=True)
        for rank, stock in enumerate(stocks_info, 1):
            rank_display = f"👑 1위" if rank == 1 else f"{rank}위"
            all_stocks.append({"종목명": stock['name'], "종목코드": stock['code'], "테마명": theme_name, "현재가(등락률)": stock['price_str'], "테마순위": rank_display, "링크": stock['link']})
        time.sleep(0.05)
    status_text.text("테마 데이터 수집 완료!")
    progress_bar.empty()
    return pd.DataFrame(all_stocks)

def get_latest_news(code):
    url = f"https://finance.naver.com/item/news_news.naver?code={code}"
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': f'https://finance.naver.com/item/main.naver?code={code}'}
    try:
        response = requests.get(url, headers=headers)
        response.encoding = 'cp949'
        soup = BeautifulSoup(response.text, 'html.parser')
        news_list = []
        articles = soup.select(".title > a")
        if not articles: articles = soup.select("a.tit")
        for article in articles[:20]: 
            title = article.text.strip()
            link = article['href']
            if link.startswith('/'): link = "https://finance.naver.com" + link
            news_list.append({"제목": title, "링크": link})
        return news_list
    except: return []

@st.cache_data
def get_top_risers_info():
    market_map = {} 
    for sosok, market_name in [(0, "KOSPI"), (1, "KOSDAQ")]: 
        url = f"https://finance.naver.com/sise/sise_rise.naver?sosok={sosok}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            response = requests.get(url, headers=headers)
            response.encoding = 'cp949'
            soup = BeautifulSoup(response.text, 'html.parser')
            items = soup.select("table.type_2 tr td a.tltle")
            for item in items[:150]: market_map[item.text.strip()] = market_name
        except: pass
    return market_map

@st.cache_data
def get_volume_leaders():
    tickers = []
    for sosok in [0, 1]:
        url = f"https://finance.naver.com/sise/sise_quant_high.naver?sosok={sosok}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.text, 'html.parser')
            items = soup.select("table.type_2 tr td a.tltle")
            for item in items[:100]: tickers.append(item.text.strip())
        except: pass
    return tickers

def get_stock_fundamentals(code):
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers)
        response.encoding = 'cp949'
        soup = BeautifulSoup(response.text, 'html.parser')
        market_cap_elem = soup.select_one("#_market_sum")
        if market_cap_elem: market_cap = clean_text(market_cap_elem.text.strip()) + "억"
        else: market_cap = "-"
        return {"시가총액": market_cap}
    except: return {"시가총액": "-"}

# --- 메인 화면 ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_code" not in st.session_state:
    st.session_state.last_code = None

try:
    with st.spinner('시장 데이터를 분석하고 있습니다...'):
        market_info_map = get_top_risers_info()
        list_A_names = list(market_info_map.keys())
        list_B = get_volume_leaders()
        df_C = get_all_theme_stocks()
        
    st.subheader("1️⃣ 교집합 분석 결과 (핵심 주도주)")
    
    final_candidates = []
    for index, row in df_C.iterrows():
        stock_name = row['종목명']
        if (stock_name in list_A_names) and (stock_name in list_B):
            market_type = market_info_map.get(stock_name, "Unknown")
            row_data = row.to_dict()
            row_data['시장구분'] = market_type
            final_candidates.append(row_data)
    
    if final_candidates:
        df_final = pd.DataFrame(final_candidates)
        df_final = df_final.drop_duplicates(['종목명'])
        
        display_columns = ['테마순위', '시장구분', '종목명', '현재가(등락률)', '테마명']
        column_config = {
            "테마순위": st.column_config.TextColumn("테마 순위", width="small"),
            "시장구분": st.column_config.TextColumn("시장", width="small"),
            "종목명": st.column_config.TextColumn("종목명", width="medium"),
            "현재가(등락률)": st.column_config.TextColumn("현재가 (등락률)", width="medium"),
            "테마명": st.column_config.TextColumn("관련 테마", width="large"),
        }
        
        event = st.dataframe(df_final[display_columns], use_container_width=True, hide_index=True, column_config=column_config, on_select="rerun", selection_mode="single-row")
        st.divider()
        
        if len(event.selection.rows) > 0:
            selected_index = event.selection.rows[0]
            selected_stock_data = df_final.iloc[selected_index]
            selected_name = selected_stock_data['종목명']
            code = selected_stock_data['종목코드']
            
            if st.session_state.last_code != code:
                st.session_state.messages = []
                st.session_state.last_code = code

            rank = selected_stock_data['테마순위']
            selected_theme = selected_stock_data['테마명']
            price_info = selected_stock_data['현재가(등락률)']
            
            with st.spinner(f'{selected_name}의 상세 정보를 가져오는 중...'):
                fund_data = get_stock_fundamentals(code)
                m_cap = fund_data['시가총액']
                news_list = get_latest_news(code)
            
            st.subheader(f"2️⃣ [{selected_name}] 상세 분석 & AI 채팅")
            st.info(f"💰 시가총액: **{m_cap}** | 🏆 테마 내 순위: **{rank}** | 🏷️ 테마: **{selected_theme}**")
            
            with st.expander("💬 AI 투자 전략가와 대화하기 (Click)", expanded=True):
                if not st.session_state.messages:
                    if st.button(f"⚡ '{selected_name}' 심층 분석 시작"):
                        if not GOOG_API_KEY.startswith("AIza"):
                            st.error("🚨 API 키 확인 필요")
                        else:
                            news_context = "\n".join([f"- {n['제목']}" for n in news_list])
                            # [핵심] 프롬프트 수정: 호재와 상승 모멘텀 우선 분석
                            system_prompt = f"""
                            당신은 '저평가 우량주'와 '급등 테마주'를 발굴하는 공격적인 투자 전략가입니다.
                            단순한 사실 나열보다는 **"왜 이 주식이 오를 수밖에 없는가?"**에 집중하여 분석하십시오.
                            
                            [분석 대상]: {selected_name} (테마: {selected_theme}, 시총: {m_cap}, 주가: {price_info})
                            [최신 뉴스 헤드라인]: {news_context}
                            
                            반드시 다음 순서와 관점으로 브리핑하십시오:
                            
                            ### 🚀 1. 핵심 호재 & 상승 모멘텀 (가장 중요)
                            - 제공된 뉴스와 검색 결과를 바탕으로, 주가 상승을 견인할 **가장 강력한 재료 3가지**를 선정하여 설명하십시오.
                            - 단순 뉴스가 아니라, 이것이 왜 '돈이 되는지' 투자자 관점에서 해석하십시오.
                            - (예: 기술 수출, 실적 턴어라운드, M&A, 정부 정책 수혜 등)
                            
                            ### 📈 2. 테마 & 시장 통찰
                            - 이 테마({selected_theme})가 현재 시장에서 왜 주목받고 있는지 설명하십시오.
                            
                            ### 💡 3. 매매 전략 & 리스크 체크
                            - **긍정적 시나리오**를 전제로 목표가나 매수 구간을 넌지시 제시하십시오.
                            - 리스크는 마지막에 짧게 언급하여 주의를 환기시키는 정도로만 작성하십시오.
                            
                            **작성 원칙:**
                            - 말투는 확신에 찬 전문가처럼 하십시오.
                            - 개조식(Bullet points)으로 가독성을 높이십시오.
                            - 호재와 긍정적 전망의 비중을 80%로 두십시오.
                            """
                            st.session_state.messages.append({"role": "user", "content": system_prompt})
                            with st.chat_message("assistant"):
                                response_text = st.write_stream(get_gemini_response_hojae(st.session_state.messages, selected_real_name, use_grounding, selected_name, selected_theme))
                            st.session_state.messages.append({"role": "assistant", "content": response_text})

                for i, message in enumerate(st.session_state.messages):
                    if i == 0: continue
                    with st.chat_message(message["role"]):
                        st.markdown(message["content"])

                if st.session_state.messages:
                    if prompt := st.chat_input(f"{selected_name}에 대해 질문하세요..."):
                        st.session_state.messages.append({"role": "user", "content": prompt})
                        with st.chat_message("user"):
                            st.markdown(prompt)
                        with st.chat_message("assistant"):
                            response_text = st.write_stream(get_gemini_response_hojae(st.session_state.messages, selected_real_name, use_grounding, selected_name, selected_theme))
                        st.session_state.messages.append({"role": "assistant", "content": response_text})

            col1, col2 = st.columns([1, 1])
            with col1:
                tab1, tab2, tab3 = st.tabs(["📅 일봉 차트", "📆 주봉 차트", "📋 테마 전체 보기"])
                with tab1: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/day/{code}.png", use_container_width=True)
                with tab2: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/week/{code}.png", use_container_width=True)
                with tab3:
                    theme_stocks = df_C[df_C['테마명'] == selected_theme]
                    st.dataframe(theme_stocks[['테마순위', '종목명', '현재가(등락률)']], use_container_width=True, hide_index=True)
            with col2:
                st.markdown(f"##### 📰 최신 뉴스 (최근 20건)")
                if news_list:
                    for i, news in enumerate(news_list):
                        st.markdown(f"{i+1}. [{news['제목']}]({news['링크']})")
                else: st.info("최신 뉴스가 없거나 가져오지 못했습니다.")
        else: st.info("👆 위 표에서 종목을 선택하면 AI 분석과 차트를 볼 수 있습니다.")
    else: st.warning("조건을 만족하는 종목이 없습니다.")
except Exception as e: st.error(f"오류가 발생했습니다: {e}")
