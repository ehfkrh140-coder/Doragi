import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import google.generativeai as genai
import urllib.parse
import random

# ==========================================
# 🔑 [필수] Gemini API 키 설정
# ==========================================
try:
    GOOG_API_KEY = st.secrets["GOOG_API_KEY"]
except:
    GOOG_API_KEY = "여기에_키를_넣으세요"

# 1. 페이지 설정
st.set_page_config(page_title="주식 테마 분석기", layout="wide")
st.title("🤖 AI 주식 투자 전략가4")

# 세션 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_code" not in st.session_state:
    st.session_state.last_code = None

# --- [모델 목록] ---
@st.cache_data
def get_available_gemini_models(api_key):
    try:
        genai.configure(api_key=api_key)
        return [m.name.replace("models/", "") for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    except: return ["gemini-1.5-flash"]

# --- [핵심] Google News RSS 수집기 ---
def fetch_google_news_rss(keyword, limit=30):
    news_data = []
    try:
        encoded_kw = urllib.parse.quote(keyword)
        url = f"https://news.google.com/rss/search?q={encoded_kw}&hl=ko&gl=KR&ceid=KR:ko"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.content, 'xml')
            items = soup.find_all('item')
            
            for item in items[:limit]:
                title = item.title.text
                link = item.link.text
                pub_date = item.pubDate.text
                
                raw_desc = item.description.text
                clean_desc = BeautifulSoup(raw_desc, "html.parser").get_text(separator=" ", strip=True)
                
                source = "News"
                if "-" in title:
                    parts = title.rsplit("-", 1)
                    if len(parts) > 1:
                        source = parts[1].strip()
                        title = parts[0].strip()
                    
                news_data.append({
                    "source": source,
                    "title": title,
                    "link": link,
                    "summary": clean_desc,
                    "date": pub_date
                })
    except Exception as e:
        print(f"RSS Error: {e}")
    return news_data

# --- [데이터 수집 1: 테마 상위 50개 및 소속 종목] ---
@st.cache_data
def get_top_50_themes_stocks():
    url = "https://finance.naver.com/sise/theme.naver"
    all_theme_stocks = [] 
    
    try:
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        
        theme_links = []
        for row in soup.select("#contentarea_left > table.type_1 > tr"):
            cols = row.select("td")
            if len(cols) >= 4:
                theme_name = cols[0].text.strip()
                link = "https://finance.naver.com" + cols[0].find('a')['href']
                theme_links.append({"name": theme_name, "link": link})
                if len(theme_links) >= 50: break
        
        progress_bar = st.progress(0)
        for idx, theme in enumerate(theme_links):
            try:
                res_t = requests.get(theme['link'], headers={'User-Agent': 'Mozilla/5.0'})
                soup_t = BeautifulSoup(res_t.content.decode('cp949', 'ignore'), 'html.parser')
                
                for row in soup_t.select("table.type_5 > tbody > tr"):
                    cols = row.select("td")
                    if len(cols) > 4:
                        name_tag = cols[0].find('a')
                        if not name_tag: continue
                        
                        stock_name = name_tag.text.strip()
                        link_sub = name_tag['href']
                        code_match = re.search(r'code=([0-9]+)', link_sub)
                        code = code_match.group(1) if code_match else ""
                        
                        price_str = cols[2].text.strip() + " (" + cols[4].text.strip().replace('\n', '').strip() + ")"
                        
                        all_theme_stocks.append({
                            "code": code, 
                            "종목명": stock_name,
                            "테마명": theme['name'],
                            "테마순위": f"{idx+1}위",
                            "현재가(등락률)": price_str
                        })
            except: pass
            progress_bar.progress((idx + 1) / len(theme_links))
        progress_bar.empty()
        
    except: pass
    return pd.DataFrame(all_theme_stocks)

# --- [데이터 수집 2: 상승률 상위 종목 (코드만 - 교집합용)] ---
@st.cache_data
def get_risers_codes_only():
    riser_codes = set()
    for s in [0, 1]: 
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}", headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            count = 0
            for item in soup.select("table.type_2 tr td a.tltle"):
                if count >= 500: break
                link = item['href']
                code_match = re.search(r'code=([0-9]+)', link)
                if code_match:
                    riser_codes.add(code_match.group(1))
                    count += 1
        except: pass
    return riser_codes

# --- [데이터 수집 2-1: 상승률 상위 종목 (상세 정보 - 시황 분석용)] ---
# [New] 시황 분석 탭에 보여줄 코스피/코스닥 각각 150개 상세 데이터
@st.cache_data
def get_top_gainers_df(limit=150):
    kospi_gainers = []
    kosdaq_gainers = []
    
    # 0: 코스피, 1: 코스닥
    for market_code, result_list in [(0, kospi_gainers), (1, kosdaq_gainers)]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={market_code}", headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            
            rows = soup.select("table.type_2 tr")
            count = 0
            for row in rows:
                cols = row.select("td")
                if len(cols) < 5: continue # 헤더나 빈 줄 스킵
                
                # 종목명 태그 찾기
                name_tag = cols[1].find('a')
                if not name_tag: continue
                
                name = name_tag.text.strip()
                price = cols[2].text.strip()
                rate = cols[4].text.strip().replace('\n', '').strip()
                
                result_list.append({
                    "종목명": name,
                    "현재가": price,
                    "등락률": rate
                })
                count += 1
                if count >= limit: break
        except: pass
        
    return pd.DataFrame(kospi_gainers), pd.DataFrame(kosdaq_gainers)

# --- [데이터 수집 3: 거래대금 상위 종목] ---
@st.cache_data
def get_money_flow_codes():
    mf_codes = set()
    headers = {'User-Agent': 'Mozilla/5.0'}
    for s in [0, 1]:
        for page in range(1, 6):
            try:
                url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={s}&sort=amount&page={page}"
                res = requests.get(url, headers=headers)
                soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
                items = soup.select("table.type_2 tbody tr td:nth-child(2) a")
                for item in items:
                    link = item['href']
                    code_match = re.search(r'code=([0-9]+)', link)
                    if code_match:
                        mf_codes.add(code_match.group(1))
            except: pass
            time.sleep(0.1)
    return mf_codes

# --- [기타 유틸리티] ---
def get_stock_fundamentals(code):
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        cap_elem = soup.select_one("#_market_sum")
        if cap_elem:
            raw_cap = cap_elem.text.strip()
            raw_cap = re.sub(r'[議兆]', '조', raw_cap)
            raw_cap = raw_cap.replace('\t', '').replace('\n', '').replace('  ', ' ') + "억"
            return {"시가총액": raw_cap}
    except: pass
    return {"시가총액": "-"}

@st.cache_data
def get_market_cap_top150():
    stocks = []
    for page in range(1, 4):
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page={page}", headers={'User-Agent': 'Mozilla/5.0'})
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

# --- [AI 응답 함수 1: 개별 종목 (World-Class)] ---
def get_gemini_response_stock_deep(messages, model_name, stock_name, theme, market_data_str, news_data):
    genai.configure(api_key=GOOG_API_KEY)
    
    current_query = messages[-1]['content']
    search_res = ""
    
    if "당신은" in current_query:
        combined_news_context = ""
        for i, item in enumerate(news_data):
            combined_news_context += f"[{i+1}. {item['source']}] {item['title']} ({item['date']})\n> 요약: {item['summary']}\n\n"
            
        search_res = f"""
        \n[분석 대상 데이터]
        1. 📊 정량적 데이터 (Market Fact):
        {market_data_str}
        
        2. 📰 정성적 데이터 (News Buzz - 총 {len(news_data)}건):
        {combined_news_context}
        """
        
        sys_instructions = """
        당신은 냉철한 판단력을 가진 세계최고 주식 애널리스트 겸 분석가 입니다.
        
        제공된 [정량 데이터]와 [뉴스 데이터]를 교차 검증하여 다음 구조로 분석 리포트를 작성하세요.

        긴말하지말고 바로 분석에 들어가 주세요.
        
        ### 1. 🎯 AI 투자 매력도 점수 (100점 만점)
        * **점수:** OOO점
        * **한줄 평:** (예: "강력한 호재와 수급이 만난 상승 초입 구간입니다" 또는 "재료 소멸 가능성이 있으니 주의하세요")
        
        ### 2. 🚀 핵심 상승 동력 (Momentum & Catalyst) 및 호재분석
        * 뉴스에서 반복적으로 언급되는 **'진짜 호재(Fact)'** 3가지를 팩트 위주로 요약하세요.(뉴스를 언급할 필요는 없습니다. 주제파악만 하세요)
        * 단순 기대감인지, 실질적인 수주/실적/정책 수혜인지 명확히 구분하세요.
        
        ### 3. ⚠️ 리스크 및 수급 점검
        * 주가가 급등했다면 과열 여부는 없는지, 악재(CB발행, 대주주 매도 등)가 숨어있는지 체크하세요.
        * 테마 내에서 이 종목이 '대장주'인지 '후발주자'인지 판단하세요.
        
        ### 4. 💡 실전 매매 전략 및 세줄요약
        * **포지션:** [적극 매수 / 눌림목 매수 / 관망 / 매도] 등 전략 제시
        * **전략:** 현실적인 가이드를 제시하세요. (예: "장중 대응 힘드니 시초가 이하 분할 매수")
        """
        search_res += f"\n\n[System Instructions]\n{sys_instructions}"
    
    modified_msgs = []
    for i, msg in enumerate(messages):
        content = msg['content']
        if i == len(messages)-1: content += search_res
        modified_msgs.append({"role": "user" if msg['role']=="user" else "model", "parts": [content]})
    
    model = genai.GenerativeModel(f"models/{model_name}")
    try:
        response = model.generate_content(modified_msgs, stream=True)
        for chunk in response: yield chunk.text
    except Exception as e:
        yield f"⚠️ API 오류: {str(e)}"

# --- [AI 응답 함수 2: 시황 분석 (Macro & Momentum)] ---
# [변경] 입력 데이터에 상승률 상위(모멘텀) 추가
def analyze_market_macro_v2(df_cap, df_gainers_kospi, df_gainers_kosdaq, news_data, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    
    # 데이터 요약 (상위 50개씩만 잘라서 전달 - 토큰 절약 및 핵심 파악)
    str_cap = df_cap.head(50).to_string(index=False)
    str_kospi_gain = df_gainers_kospi.head(50).to_string(index=False)
    str_kosdaq_gain = df_gainers_kosdaq.head(50).to_string(index=False)
    
    combined_news = ""
    for item in news_data:
        combined_news += f"[{item['source']}] {item['title']}\n(요약): {item['summary']}\n\n"
    
    prompt = f"""
    당신은 거시경제와 시장 흐름을 읽는 국내 최고 '마켓스트래티지스트겸 애널리스트 입니다

    긴말하지말고 바로 분석에 들어가 주세요.
    
    [입력 데이터 3종]
    1. **Blue Chips (시장 지수 방어):** 코스피/코스닥 시가총액 상위 50위 흐름
    {str_cap}
    
    2. **Momentum (시장 주도 테마):** 코스피/코스닥 급등주(상승률 상위) 흐름
    [코스피 급등]
    {str_kospi_gain}
    [코스닥 급등]
    {str_kosdaq_gain}
    
    3. **News Flow:** 시장 주요 뉴스 요약 ({len(news_data)}건)
    {combined_news}
    
    [분석 요구사항]
    위 데이터를 종합하여 '대형주(지수)'와 '개별 급등주(테마)'의 괴리를 분석하고,
    오늘 시장의 **'진짜 주도 흐름'**을 명확히 정의해 주세요.
    
    ### 1. 🌍 오늘의 시장 세줄 요약 (Market Color)
    * (예: "지수는 보합이나 2차전지와 AI 로봇 테마가 폭발하는 종목 장세")
    
    ### 2. 💰 자금 흐름 추적 (Money Flow)
    * **대형주:** 반도체, 바이오, 금융 등 시총 상위 섹터의 수급은 어떻습니까?
    * **개별주:** 급등주 리스트에서 공통적으로 보이는 **'오늘의 강세 테마'**는 무엇입니까? (예: 초전도체, 품절주 등)
    
    ### 3. 📈 주요 거시 요인 분석
    * 뉴스에 언급된 환율, 금리, 미 증시 영향, 정부 정책 등이 오늘 시장에 어떤 영향을 미치고 있습니까?
    
    ### 4. 💼 투자자 대응 가이드 및 세줄 요약
    * 오늘 같은 장세에서는 **어떤 스타일의 투자**가 유리합니까? (돌파 매매 vs 눌림목 매수 vs 현금 확보)
    """
    
    try:
        response = model.generate_content(prompt, stream=True)
        for chunk in response: yield chunk.text
    except Exception as e:
        yield f"⚠️ 분석 중 오류: {str(e)}"

# ==========================================
# 🖥️ 메인 실행
# ==========================================
with st.sidebar:
    st.header("🔍 설정")
    if st.button("🔄 데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()
    
    if GOOG_API_KEY.startswith("AIza"):
        models = get_available_gemini_models(GOOG_API_KEY)
        model_name = st.selectbox("모델 선택", models, index=0)
        selected_real_name = model_name.split(" ")[1] if " " in model_name else model_name
    else:
        st.error("API 키 필요")
        selected_real_name = "gemini-1.5-flash"

# 초기 데이터 로딩
with st.status("🚀 3중 필터 데이터 및 시황 데이터 수집 중...", expanded=True) as status:
    # 1. 3중 교집합용 데이터
    df_themes = get_top_50_themes_stocks() 
    riser_codes = get_risers_codes_only()       
    mf_codes = get_money_flow_codes()
    
    # 2. 시황 분석용 상세 데이터 (NEW)
    df_market_cap = get_market_cap_top150() # 시총 상위 150
    df_kospi_gainers, df_kosdaq_gainers = get_top_gainers_df(limit=150) # 급등 상위 150
    
    status.update(label="✅ 데이터 준비 완료!", state="complete", expanded=False)

tab1, tab2 = st.tabs(["🎯 3중 교집합 발굴", "📊 시황 분석 (Dual-Engine)"])

# --- Tab 1 ---
with tab1:
    st.subheader("1️⃣ 3중 교집합 분석 결과 (Money Flow Ver.)")
    st.markdown("""
    **필터링 조건 (AND 조건):**
    1. 🔥 **테마 상위 50위** 내 종목
    2. 📈 **상승률 상위 500위** (코스피+코스닥)
    3. 💰 **거래대금 상위 500위** (코스피+코스닥)
    """)
    
    st.info(f"📊 **데이터 수집 현황**")
    col1, col2, col3 = st.columns(3)
    col1.metric("🔥 테마 종목", f"{len(df_themes)}개")
    col2.metric("📈 상승 종목", f"{len(riser_codes)}개")
    col3.metric("💰 거래대금 종목", f"{len(mf_codes)}개")
    
    final_candidates = []
    
    if not df_themes.empty:
        for index, row in df_themes.iterrows():
            code = row['code']
            if (code in riser_codes) and (code in mf_codes):
                final_candidates.append(row.to_dict())
                
    if final_candidates:
        df_final = pd.DataFrame(final_candidates)
        df_final = df_final.drop_duplicates(['code'])
        df_final = df_final.sort_values(by="테마순위")
        
        event = st.dataframe(
            df_final[['테마순위', '종목명', '현재가(등락률)', '테마명']], 
            use_container_width=True, 
            hide_index=True, 
            on_select="rerun", 
            selection_mode="single-row"
        )
        
        st.divider()
        
        if len(event.selection.rows) > 0:
            sel_idx = event.selection.rows[0]
            sel_data = df_final.iloc[sel_idx]
            
            s_name = sel_data['종목명']
            code = sel_data['code']
            s_theme = sel_data['테마명']
            
            if st.session_state.last_code != code:
                st.session_state.messages = []
                st.session_state.last_code = code

            with st.spinner(f"🔍 {s_name} 심층 분석 중 (뉴스 50건)..."):
                fund = get_stock_fundamentals(code)
                news_1 = fetch_google_news_rss(f"{s_name} 주가", limit=25)
                news_2 = fetch_google_news_rss(f"{s_name} 호재 특징주", limit=25)
                
                all_news = news_1 + news_2
                unique_news = {v['link']: v for v in all_news}.values()
                final_news_list = list(unique_news)
                
                market_data_str = f"종목명: {s_name}\n코드: {code}\n테마: {s_theme}\n시가총액: {fund['시가총액']}\n현재가(등락): {sel_data['현재가(등락률)']}"
            
            st.subheader(f"2️⃣ [{s_name}] 상세 분석")
            st.info(f"💰 시가총액: **{fund['시가총액']}** | 🏆 테마: **{s_theme}**")
            
            with st.expander("💬 AI 투자 전략가와 대화하기 (Click)", expanded=True):
                if not st.session_state.messages:
                    if st.button(f"⚡ '{s_name}' 심층 분석 시작"):
                        # 사용자 요청에는 간단히
                        st.session_state.messages.append({"role": "user", "content": f"{s_name} 심층 분석 요청"})
                        with st.chat_message("assistant"):
                            res_txt = st.write_stream(get_gemini_response_stock_deep(st.session_state.messages, selected_real_name, s_name, s_theme, market_data_str, final_news_list))
                        st.session_state.messages.append({"role": "assistant", "content": res_txt})

                for msg in st.session_state.messages:
                    if msg['role'] == 'user' and "당신은" in msg['content']: continue
                    with st.chat_message(msg['role']): st.markdown(msg['content'])

                if prompt := st.chat_input(f"{s_name} 질문..."):
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    with st.chat_message("user"): st.markdown(prompt)
                    with st.chat_message("assistant"):
                        model = genai.GenerativeModel(f"models/{selected_real_name}")
                        history = [{"role": m["role"], "parts": [m["content"]]} for m in st.session_state.messages]
                        try:
                            res = model.generate_content(history, stream=True)
                            res_txt = st.write_stream(res)
                        except Exception as e:
                            res_txt = f"⚠️ 오류: {str(e)}"
                            st.write(res_txt)
                        st.session_state.messages.append({"role": "assistant", "content": res_txt})

            col1, col2 = st.columns([1, 1])
            with col1:
                t1, t2, t3 = st.tabs(["📅 일봉", "📆 주봉", "📋 테마 전체"])
                with t1: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/day/{code}.png", use_container_width=True)
                with t2: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/week/{code}.png", use_container_width=True)
                with t3:
                    cur_theme_list = df_themes[df_themes['테마명']==s_theme]
                    st.dataframe(cur_theme_list[['종목명','현재가(등락률)']], hide_index=True)
            with col2:
                st.markdown(f"##### 📰 관련 뉴스 (상위 20건)")
                st.caption(f"※ 총 {len(final_news_list)}건의 데이터를 분석했습니다.")
                if final_news_list:
                    for n in final_news_list[:20]: 
                        st.markdown(f"- [{n['title']}]({n['link']})")
                else:
                    st.warning("뉴스를 찾을 수 없습니다.")
    else:
        st.warning("조건(테마50위 & 상승500위 & 거래대금500위)을 동시에 만족하는 종목이 현재 없습니다.")

# --- Tab 2 ---
with tab2:
    st.header("📊 시장 입체 분석 (대형주 vs 주도주)")
    
    # [UI 업데이트] 탭으로 시총상위/급등상위 구분하여 보여주기
    sub_t1, sub_t2 = st.tabs(["🏢 시총 상위 150 (지수)", "🚀 급등 상위 150 (모멘텀)"])
    
    with sub_t1:
        if not df_market_cap.empty:
            st.dataframe(df_market_cap, height=400, use_container_width=True)
    
    with sub_t2:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### 코스피 급등 Top 150")
            if not df_kospi_gainers.empty:
                st.dataframe(df_kospi_gainers, height=400, use_container_width=True)
        with c2:
            st.markdown("#### 코스닥 급등 Top 150")
            if not df_kosdaq_gainers.empty:
                st.dataframe(df_kosdaq_gainers, height=400, use_container_width=True)
        
    st.divider()
    st.subheader("🤖 AI 실시간 시황 브리핑")
    
    if st.button("📢 시황 뉴스 수집 및 종합 분석 (RSS)"):
        with st.spinner("시황 뉴스 수집 중..."):
            news_1 = fetch_google_news_rss("한국 증시 시황", limit=20)
            news_2 = fetch_google_news_rss("코스피 코스닥 특징주", limit=20)
            
            all_market_news = news_1 + news_2
            unique_market_news = {v['link']: v for v in all_market_news}.values()
            final_market_news = list(unique_market_news)
            
        if final_market_news:
            st.success(f"✅ 뉴스 {len(final_market_news)}건 확보! (시총/급등 데이터와 결합하여 분석 시작)")
            with st.expander("🔍 수집된 뉴스 데이터 확인", expanded=False):
                for n in final_market_news:
                    st.write(f"- {n['title']}: {n['summary']}")
            
            # [AI 호출] 시총 데이터 + 급등주 데이터 + 뉴스 데이터 모두 전달
            st.write_stream(analyze_market_macro_v2(df_market_cap, df_kospi_gainers, df_kosdaq_gainers, final_market_news, selected_real_name))
        else:
            st.error("⚠️ 뉴스 수집 실패.")
