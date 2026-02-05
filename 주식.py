import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import google.generativeai as genai
import urllib.parse
import random
import email.utils
from datetime import datetime, timedelta

# ==========================================
# 🔑 [필수] Gemini API 키 설정
# ==========================================
try:
    GOOG_API_KEY = st.secrets["GOOG_API_KEY"]
except:
    GOOG_API_KEY = "여기에_키를_넣으세요"

# 1. 페이지 설정
st.set_page_config(page_title="주식 테마 분석기", layout="wide")
st.title("🤖 AI 주식 투자 전략가 (7-Day Context Ver.)")

# 세션 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_code" not in st.session_state:
    st.session_state.last_code = None
if "current_news_data" not in st.session_state:
    st.session_state.current_news_data = [] 
if "current_market_fact" not in st.session_state:
    st.session_state.current_market_fact = ""

# --- 안전 필터 해제 (전역 변수) ---
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]

# --- [모델 목록] ---
@st.cache_data(ttl=3600)
def get_available_gemini_models(api_key):
    try:
        genai.configure(api_key=api_key)
        return [m.name.replace("models/", "") for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
    except: return ["gemini-1.5-flash"]

# --- [핵심] Google News RSS 수집기 (7일 + 최신순 정렬) ---
def fetch_google_news_rss(keyword, limit=30):
    news_data = []
    try:
        # [수정] when:7d (최근 7일)로 기간 확장
        search_query = f"{keyword} when:7d"
        encoded_kw = urllib.parse.quote(search_query)
        
        # [유지] scoring=n (최신순 정렬)은 필수
        url = f"https://news.google.com/rss/search?q={encoded_kw}&hl=ko&gl=KR&ceid=KR:ko&scoring=n&t={int(time.time())}"
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5)
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.content, 'xml')
            items = soup.find_all('item')
            
            for item in items:
                title = item.title.text
                link = item.link.text
                pub_date = item.pubDate.text
                raw_desc = item.description.text
                clean_desc = BeautifulSoup(raw_desc, "html.parser").get_text(separator=" ", strip=True)
                
                # [수정] Python 레벨 필터링도 7일(days=7)로 변경
                dt_object = None
                try:
                    dt_object = email.utils.parsedate_to_datetime(pub_date)
                    now = datetime.now(dt_object.tzinfo)
                    if dt_object < (now - timedelta(days=7)): 
                        continue
                except:
                    pass

                source = "News"
                if "-" in title:
                    parts = title.rsplit("-", 1)
                    if len(parts) > 1:
                        source = parts[1].strip()
                        title = parts[0].strip()
                    
                news_data.append({
                    "source": source, "title": title, "link": link,
                    "summary": clean_desc, "date": pub_date, "date_obj": dt_object
                })
                
            # 최신순 정렬 보장 (7일치 데이터 중에서도 최신이 위로)
            news_data.sort(key=lambda x: x['date_obj'] if x['date_obj'] else datetime.min.replace(tzinfo=None), reverse=True)
            news_data = news_data[:limit]

    except Exception as e:
        print(f"RSS Error: {e}")
    return news_data

# --- [데이터 수집 함수들 (10분 갱신)] ---
@st.cache_data(ttl=600)
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
        
        for idx, theme in enumerate(theme_links):
            try:
                res_t = requests.get(theme['link'], headers={'User-Agent': 'Mozilla/5.0'})
                soup_t = BeautifulSoup(res_t.content.decode('cp949', 'ignore'), 'html.parser')
                
                inner_rank = 1
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
                        
                        rank_display = f"👑 {inner_rank}위" if inner_rank == 1 else f"{inner_rank}위"
                        
                        all_theme_stocks.append({
                            "code": code, "종목명": stock_name, "테마명": theme['name'],
                            "테마순위": f"{idx+1}위", "테마순위_int": idx+1,
                            "테마내순위": rank_display,
                            "현재가(등락률)": price_str
                        })
                        inner_rank += 1
            except: pass
    except: pass
    return pd.DataFrame(all_theme_stocks)

@st.cache_data(ttl=600)
def get_risers_data_with_market():
    riser_map = {}
    for s, market_name in [(0, "KOSPI"), (1, "KOSDAQ")]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}", headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle"):
                link = item['href']
                code_match = re.search(r'code=([0-9]+)', link)
                if code_match:
                    code = code_match.group(1)
                    riser_map[code] = market_name
        except: pass
    return riser_map

@st.cache_data(ttl=600)
def get_top_gainers_df(limit=150):
    kospi_gainers = []
    kosdaq_gainers = []
    for market_code, result_list in [(0, kospi_gainers), (1, kosdaq_gainers)]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={market_code}", headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            rows = soup.select("table.type_2 tr")
            count = 0
            for row in rows:
                cols = row.select("td")
                if len(cols) < 5: continue
                name_tag = cols[1].find('a')
                if not name_tag: continue
                result_list.append({
                    "종목명": name_tag.text.strip(),
                    "현재가": cols[2].text.strip(),
                    "등락률": cols[4].text.strip().replace('\n', '').strip()
                })
                count += 1
                if count >= limit: break
        except: pass
    return pd.DataFrame(kospi_gainers), pd.DataFrame(kosdaq_gainers)

@st.cache_data(ttl=600)
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

@st.cache_data(ttl=600)
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

# --- [AI 응답 함수 1: 개별 종목] ---
def get_gemini_response_stock_deep(messages, model_name, stock_name, theme, market_data_str, news_data):
    genai.configure(api_key=GOOG_API_KEY)
    
    combined_news_context = ""
    if news_data:
        for i, item in enumerate(news_data):
            combined_news_context += f"[{i+1}. {item['source']}] {item['title']} ({item['date']})\n> 요약: {item['summary']}\n\n"
    else:
        combined_news_context = "(수집된 뉴스가 없습니다. 시장 데이터 위주로 분석하세요.)"
        
    search_res = f"""
    \n[현재 분석 중인 종목 데이터]
    1. 📊 시장 팩트: {market_data_str}
    2. 📰 뉴스 데이터 (총 {len(news_data)}건):
    {combined_news_context}
    """
    
    sys_instructions = """
    [Role]
    당신은 냉철한 판단력을 가진 세계최고 주식 애널리스트 겸 분석가 입니다.
    제공된 [정량 데이터]와 [뉴스 데이터]를 기반으로 사용자의 질문에 답변하십시오.
    
    [Instruction]
    - 질문이 "분석해줘" 같은 요청이면 아래 포맷으로 리포트를 작성하세요.
    - 긴말하지 말고 핵심만 명확하게 전달하세요.
    
    [Report Format]
    ### 1. 🎯 AI 투자 매력도 점수 (100점 만점)
    * **점수:** OOO점
    * **한줄 평:** (상승 동력 및 리스크 요약)
    
    ### 2. 🚀 핵심 상승 동력 (Fact Base)
    * 뉴스에서 확인된 실체 있는 호재 3가지를 요약.
    
    ### 3. ⚠️ 리스크 및 수급 점검
    * 과열 여부, 대주주 매도, 테마 대장주 여부 등 판단.
    
    ### 4. 💡 실전 매매 전략
    * **포지션:** [적극 매수 / 눌림목 매수 / 관망 / 매도]
    * **전략:** 구체적인 진입/대응 가이드.
    """
    
    modified_msgs = []
    for msg in messages[:-1]:
        modified_msgs.append({"role": "user" if msg['role']=="user" else "model", "parts": [msg['content']]})
    
    last_content = messages[-1]['content'] + search_res + "\n\n" + sys_instructions
    modified_msgs.append({"role": "user", "parts": [last_content]})
    
    model = genai.GenerativeModel(f"models/{model_name}")
    try:
        response = model.generate_content(modified_msgs, stream=True, safety_settings=safety_settings)
        for chunk in response:
            try:
                if chunk.text: yield chunk.text
            except ValueError: pass
    except Exception as e:
        yield f"⚠️ 응답 중 오류: {str(e)}"

# --- [AI 응답 함수 2: 시황 분석] ---
def analyze_market_macro_v2(df_cap, df_gainers_kospi, df_gainers_kosdaq, news_data, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    
    str_cap = df_cap.head(50).to_string(index=False)
    str_kospi_gain = df_gainers_kospi.head(50).to_string(index=False)
    str_kosdaq_gain = df_gainers_kosdaq.head(50).to_string(index=False)
    
    combined_news = ""
    for item in news_data:
        combined_news += f"[{item['source']}] {item['title']}\n(요약): {item['summary']}\n\n"
    
    prompt = f"""
    당신은 거시경제와 시장 흐름을 읽는 국내 최고 '마켓스트래티지스트겸 애널리스트 입니다.
    긴말하지말고 바로 분석에 들어가 주세요.
    
    [입력 데이터]
    1. Blue Chips (50위): {str_cap}
    2. Momentum (급등주): {str_kospi_gain} / {str_kosdaq_gain}
    3. News: {combined_news}
    
    [분석 요구사항]
    위 데이터를 종합하여 '대형주(지수)'와 '개별 급등주(테마)'의 괴리를 분석하고,
    오늘 시장의 **'진짜 주도 흐름'**을 명확히 정의해 주세요.
    
    ### 1. 🌍 오늘의 시장 세줄 요약 (Market Color)
    * (예: "지수는 보합이나 2차전지와 AI 로봇 테마가 폭발하는 종목 장세")
    
    ### 2. 💰 자금 흐름 추적 (Money Flow)
    * **대형주:** 반도체, 바이오, 금융 등 시총 상위 섹터의 수급은 어떻습니까?
    * **개별주:** 급등주 리스트에서 공통적으로 보이는 **'오늘의 강세 테마'**는 무엇입니까?
    
    ### 3. 📈 주요 거시 요인 분석
    * 환율, 금리, 미 증시 영향, 정부 정책 등이 오늘 시장에 미친 영향.
    
    ### 4. 💼 투자자 대응 가이드
    * 오늘 같은 장세에서는 **어떤 스타일의 투자**가 유리합니까? (돌파 매매 vs 눌림목 매수 vs 현금 확보)
    """
    
    try:
        response = model.generate_content(prompt, stream=True, safety_settings=safety_settings)
        for chunk in response:
            try:
                if chunk.text: yield chunk.text
            except ValueError: pass
    except Exception as e:
        yield f"⚠️ 분석 중 오류: {str(e)}"

# ==========================================
# 🖥️ 메인 실행
# ==========================================
with st.sidebar:
    st.header("🔍 설정")
    if st.button("🔄 데이터 새로고침"):
        st.cache_data.clear()
        st.session_state.current_news_data = [] 
        st.rerun()
    
    if GOOG_API_KEY.startswith("AIza"):
        models = get_available_gemini_models(GOOG_API_KEY)
        
        # [Flash 모델 기본 설정]
        target_model = "gemini-flash-latest"
        default_index = 0
        if target_model in models:
            default_index = models.index(target_model)
        else:
            # flash 이름이 들어간 모델 자동 선택
            for i, m in enumerate(models):
                if "flash" in m.lower():
                    default_index = i
                    break
            
        model_name = st.selectbox("모델 선택", models, index=default_index)
        selected_real_name = model_name.split(" ")[1] if " " in model_name else model_name
    else:
        st.error("API 키 필요")
        selected_real_name = "gemini-1.5-flash"

# 초기 데이터 로딩
with st.status("🚀 3중 필터(테마/상승/거래대금) 데이터 수집 중...", expanded=True) as status:
    df_themes = get_top_50_themes_stocks() 
    riser_data = get_risers_data_with_market() 
    mf_codes = get_money_flow_codes()
    df_market_cap = get_market_cap_top150()
    df_kospi_gainers, df_kosdaq_gainers = get_top_gainers_df(limit=150)
    status.update(label="✅ 데이터 준비 완료!", state="complete", expanded=False)

tab1, tab2 = st.tabs(["🎯 3중 교집합 발굴", "📊 시황 분석 (Dual-Engine)"])

# --- Tab 1 ---
with tab1:
    st.subheader("1️⃣ 3중 교집합 분석 결과 (The Intersection)")
    st.markdown("""
    **필터링 조건 (AND 조건):**
    1. 🔥 **테마 상위 50위** 내 종목
    2. 📈 **상승률 상위 500위** (코스피+코스닥)
    3. 💰 **거래대금 상위 500위** (코스피+코스닥)
    """)
    
    st.info(f"📊 **데이터 수집 현황**")
    col1, col2, col3 = st.columns(3)
    col1.metric("🔥 테마 종목", f"{len(df_themes)}개")
    col2.metric("📈 상승 종목", f"{len(riser_data)}개")
    col3.metric("💰 거래대금 종목", f"{len(mf_codes)}개")
    
    final_candidates = []
    
    if not df_themes.empty:
        for index, row in df_themes.iterrows():
            code = row['code']
            if (code in riser_data) and (code in mf_codes):
                row_data = row.to_dict()
                row_data['시장'] = riser_data[code] 
                final_candidates.append(row_data)
                
    if final_candidates:
        df_final = pd.DataFrame(final_candidates)
        df_final = df_final.drop_duplicates(['code'])
        df_final = df_final.sort_values(by="테마순위_int")
        
        event = st.dataframe(
            df_final[['테마순위', '테마내순위', '시장', '종목명', '현재가(등락률)', '테마명']], 
            use_container_width=True, 
            hide_index=True, 
            on_select="rerun", 
            selection_mode="single-row",
            column_config={
                "테마순위": st.column_config.TextColumn("테마랭킹", width="small"),
                "테마내순위": st.column_config.TextColumn("테마내등수", width="small"),
                "시장": st.column_config.TextColumn("시장", width="small"),
                "종목명": st.column_config.TextColumn("종목명", width="medium"),
                "현재가(등락률)": st.column_config.TextColumn("현재가", width="medium"),
                "테마명": st.column_config.TextColumn("테마명", width="medium"),
            }
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
                st.session_state.current_news_data = [] 
                
                with st.spinner(f"⚡ {s_name} 실시간 속보 수집 중..."):
                    # [핵심] 검색어 적용: {종목명} 주가 특징주 / {종목명} 속보
                    news_1 = fetch_google_news_rss(f"{s_name} 주가 특징주", limit=20)
                    news_2 = fetch_google_news_rss(f"{s_name} 속보", limit=20)
                    
                    all_news = news_1 + news_2
                    unique_news = {v['link']: v for v in all_news}.values()
                    
                    sorted_news = sorted(list(unique_news), key=lambda x: x['date_obj'] if x['date_obj'] else datetime.min.replace(tzinfo=None), reverse=True)
                    st.session_state.current_news_data = sorted_news
                    
                    market_str = f"종목명: {s_name}\n코드: {code}\n테마: {s_theme}\n시가총액: {get_stock_fundamentals(code)['시가총액']}\n현재가(등락): {sel_data['현재가(등락률)']}"
                    st.session_state.current_market_fact = market_str

            st.subheader(f"2️⃣ [{s_name}] 상세 분석")
            st.info(f"💰 시가총액: **{get_stock_fundamentals(code)['시가총액']}** | 🏆 테마: **{s_theme}**")
            
            with st.expander("💬 AI 투자 전략가와 대화하기 (Click)", expanded=True):
                news_count = len(st.session_state.current_news_data)
                if news_count > 0:
                    st.success(f"✅ **뉴스 {news_count}건 확보됨.**")
                else:
                    st.warning("⚠️ 최근 7일 내 관련 뉴스가 없습니다.")

                for msg in st.session_state.messages:
                    if msg['role'] == 'user' and "당신은" in msg['content']: continue
                    with st.chat_message(msg['role']): st.markdown(msg['content'])

                if not st.session_state.messages:
                    if st.button(f"⚡ '{s_name}' 심층 분석 리포트 생성"):
                        user_msg_content = f"{s_name} 심층 분석해줘."
                        with st.chat_message("user"): st.markdown(user_msg_content)
                        st.session_state.messages.append({"role": "user", "content": user_msg_content})
                        
                        with st.chat_message("assistant"):
                            res_txt = st.write_stream(get_gemini_response_stock_deep(
                                st.session_state.messages, 
                                selected_real_name, 
                                s_name, 
                                s_theme, 
                                st.session_state.current_market_fact, 
                                st.session_state.current_news_data
                            ))
                        st.session_state.messages.append({"role": "assistant", "content": res_txt})

                if prompt := st.chat_input(f"{s_name} 질문..."):
                    with st.chat_message("user"): st.markdown(prompt)
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    with st.chat_message("assistant"):
                        res_txt = st.write_stream(get_gemini_response_stock_deep(
                            st.session_state.messages, 
                            selected_real_name, 
                            s_name, 
                            s_theme, 
                            st.session_state.current_market_fact, 
                            st.session_state.current_news_data
                        ))
                    st.session_state.messages.append({"role": "assistant", "content": res_txt})

            col1, col2 = st.columns([1, 1])
            with col1:
                t1, t2, t3 = st.tabs(["📅 일봉", "📆 주봉", "📋 테마 전체"])
                with t1: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/day/{code}.png", use_container_width=True)
                with t2: st.image(f"https://ssl.pstatic.net/imgfinance/chart/item/candle/week/{code}.png", use_container_width=True)
                with t3:
                    cur_theme_list = df_themes[df_themes['테마명']==s_theme]
                    st.dataframe(cur_theme_list[['테마내순위', '종목명','현재가(등락률)']], hide_index=True)
            with col2:
                final_news_list = st.session_state.current_news_data
                st.markdown(f"##### 📰 관련 뉴스 (상위 20건)")
                if final_news_list:
                    for n in final_news_list[:20]: 
                        st.markdown(f"- [{n['title']}]({n['link']})")
                else:
                    st.warning("수집된 뉴스가 없습니다.")
    else:
        st.warning("조건(테마50위 & 상승500위 & 거래대금500위)을 동시에 만족하는 종목이 현재 없습니다.")

# --- Tab 2 ---
with tab2:
    st.header("📊 시장 입체 분석 (대형주 vs 주도주)")
    sub_t1, sub_t2 = st.tabs(["🏢 시총 상위 150 (지수)", "🚀 급등 상위 150 (모멘텀)"])
    with sub_t1:
        if not df_market_cap.empty: st.dataframe(df_market_cap, height=400, use_container_width=True)
    with sub_t2:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### 코스피 급등 Top 150")
            if not df_kospi_gainers.empty: st.dataframe(df_kospi_gainers, height=400, use_container_width=True)
        with c2:
            st.markdown("#### 코스닥 급등 Top 150")
            if not df_kosdaq_gainers.empty: st.dataframe(df_kosdaq_gainers, height=400, use_container_width=True)
        
    st.divider()
    st.subheader("🤖 AI 실시간 시황 브리핑")
    if st.button("📢 시황 뉴스 수집 및 종합 분석 (RSS)"):
        with st.spinner("실시간 증시 속보 수집 중..."):
            # [핵심] 시황 검색어: 국내 증시 / 한국 증시
            news_1 = fetch_google_news_rss("국내 증시", limit=20)
            news_2 = fetch_google_news_rss("한국 증시", limit=20)
            
            all_market_news = news_1 + news_2
            unique_market_news = {v['link']: v for v in all_market_news}.values()
            
            final_market_news = sorted(list(unique_market_news), key=lambda x: x['date_obj'] if x['date_obj'] else datetime.min.replace(tzinfo=None), reverse=True)
            
        if final_market_news:
            st.success(f"✅ 뉴스 {len(final_market_news)}건 확보! (분석 시작)")
            with st.expander("🔍 수집된 뉴스 데이터 확인", expanded=False):
                for n in final_market_news:
                    st.write(f"- {n['title']}: {n['summary']}")
            st.write_stream(analyze_market_macro_v2(df_market_cap, df_kospi_gainers, df_kosdaq_gainers, final_market_news, selected_real_name))
        else:
            st.error("⚠️ 최근 7일 내 시황 뉴스가 없습니다.")
