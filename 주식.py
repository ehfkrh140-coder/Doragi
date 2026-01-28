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
st.title("🤖 AI 주식 투자 전략가 (V 7.2 Optimization)")

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
                    source = title.split("-")[-1].strip()
                    
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

# --- [데이터 수집 함수들] ---

# [수정 1] 테마 수집 범위를 상위 20개 -> 50개로 확대
@st.cache_data
def get_naver_themes():
    url = "https://finance.naver.com/sise/theme.naver"
    try:
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        data = []
        # 테마 테이블 파싱
        for row in soup.select("#contentarea_left > table.type_1 > tr"):
            cols = row.select("td")
            if len(cols) >= 4:
                data.append({
                    "테마명": cols[0].text.strip(), 
                    "링크": "https://finance.naver.com" + cols[0].find('a')['href']
                })
        # [핵심] 상위 50개 테마 반환
        return pd.DataFrame(data).head(50)
    except: return pd.DataFrame()

def get_theme_details(theme_link):
    try:
        res = requests.get(theme_link, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        stocks = []
        for row in soup.select("table.type_5 > tbody > tr"):
            cols = row.select("td")
            if len(cols) > 4:
                name = cols[0].text.strip()
                link = "https://finance.naver.com" + cols[0].find('a')['href']
                code_match = re.search(r'code=([0-9]+)', link)
                code = code_match.group(1) if code_match else ""
                price = cols[2].text.strip()
                rate = cols[4].text.strip().replace('\n', '').strip()
                stocks.append({'name': name, 'code': code, 'price_str': f"{price} ({rate})", 'link': theme_link})
        return stocks
    except: return []

@st.cache_data
def get_all_theme_stocks():
    df_themes = get_naver_themes()
    all_stocks = []
    # 50개 테마를 순회하므로 시간이 조금 더 걸릴 수 있음 (진행바 표시 추천하지만 일단 유지)
    for index, row in df_themes.iterrows():
        stocks_info = get_theme_details(row['링크'])
        stocks_info.sort(key=lambda x: float(x['price_str'].split('(')[1].replace('%)','').replace('+','').replace('-','-').replace(',','')) if '(' in x['price_str'] else 0, reverse=True)
        for rank, stock in enumerate(stocks_info, 1):
             all_stocks.append({
                 "테마순위": f"{rank}위", "종목명": stock['name'], "종목코드": stock['code'], 
                 "테마명": row['테마명'], "현재가(등락률)": stock['price_str']
             })
    return pd.DataFrame(all_stocks)

# [수정 2] 상승률 상위 500위까지 수집
@st.cache_data
def get_top_risers_info():
    market_map = {}
    for s in [0, 1]: # 코스피, 코스닥
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}", headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            # [:500] 으로 제한 (기존 전체 -> 500개로 명확히 지정)
            for item in soup.select("table.type_2 tr td a.tltle")[:500]: 
                market_map[item.text.strip()] = "KOSPI" if s==0 else "KOSDAQ"
        except: pass
    return market_map

# [수정 3] 거래량 상위 500위까지 수집
@st.cache_data
def get_volume_leaders():
    tickers = []
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_quant_high.naver?sosok={s}", headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(res.text, 'html.parser')
            # 200 -> 500으로 확대
            for item in soup.select("table.type_2 tr td a.tltle")[:500]: 
                tickers.append(item.text.strip())
        except: pass
    return tickers

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

# --- [AI 응답 함수] ---
def get_gemini_response_mass_analysis(messages, model_name, stock_name, theme, market_data_str, news_data):
    genai.configure(api_key=GOOG_API_KEY)
    
    current_query = messages[-1]['content']
    search_res = ""
    
    if "당신은" in current_query:
        combined_news_context = ""
        # AI에게는 수집된 모든 뉴스 요약문 제공 (50개)
        for i, item in enumerate(news_data):
            combined_news_context += f"[{i+1}. {item['source']}] {item['title']} ({item['date']})\n> 요약: {item['summary']}\n\n"
            
        search_res = f"""
        \n[시스템 데이터 주입]
        1. 📊 시장 데이터 (Hard Fact):
        {market_data_str}
        
        2. 📰 뉴스 대량 요약 데이터 (Soft Fact - 총 {len(news_data)}건):
        {combined_news_context}
        """
    
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
        yield f"⚠️ API 오류: {str(e)}\n\n(API 키 한도가 초과되었거나 네트워크 문제입니다.)"

def analyze_market_summary(df, news_data, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    top_30 = df.head(30).to_string(index=False)
    
    combined_text = ""
    for item in news_data:
        combined_text += f"[{item['source']}] {item['title']}\n(요약): {item['summary']}\n\n"
    
    prompt = f"""
    당신은 수석 애널리스트입니다.
    [시총 상위 30위 흐름]: {top_30}
    [최신 뉴스 요약 데이터 ({len(news_data)}건)]: {combined_text}
    
    위 뉴스 요약문들을 종합하여 현재 시장의 분위기, 핵심 거시 경제 요인(금리/환율 등), 주도 섹터를 분석하고 34세 직장인을 위한 전략을 제시하세요.
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
with st.status("🚀 시장 데이터 수집 중... (테마 50개 / 등락·거래량 500위)", expanded=True) as status:
    df_market = get_market_cap_top150()
    market_map = get_top_risers_info() 
    vol_leaders = get_volume_leaders() 
    df_C = get_all_theme_stocks()
    status.update(label="✅ 데이터 준비 완료!", state="complete", expanded=False)

tab1, tab2 = st.tabs(["🎯 급등주 발굴", "📊 시황 분석"])

# --- Tab 1 ---
with tab1:
    st.subheader("1️⃣ 교집합 분석 결과 (Top 500 & Top 50 Themes)")
    
    # 교집합 로직 적용
    list_A = list(market_map.keys()) # 상승률 Top 500
    list_B = vol_leaders # 거래량 Top 500
    final_candidates = []
    
    # 테마(df_C)는 이미 Top 50 테마의 종목들임
    for index, row in df_C.iterrows():
        stock_name = row['종목명']
        # 교집합 검사
        if (stock_name in list_A) and (stock_name in list_B):
            market_type = market_map.get(stock_name, "Unknown")
            row_data = row.to_dict()
            row_data['시장구분'] = market_type
            final_candidates.append(row_data)
            
    if final_candidates:
        df_final = pd.DataFrame(final_candidates)
        df_final = df_final.drop_duplicates(['종목명'])
        df_final = df_final.sort_values(by="테마명")
        
        event = st.dataframe(
            df_final[['테마순위', '시장구분', '종목명', '현재가(등락률)', '테마명']], 
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

            with st.spinner(f"🔍 {s_name} 관련 뉴스 데이터 50건 수집 중..."):
                fund = get_stock_fundamentals(code)
                # 뉴스 수집 (종목 25 + 호재 25 = 50)
                news_1 = fetch_google_news_rss(f"{s_name} 주가", limit=25)
                news_2 = fetch_google_news_rss(f"{s_name} 호재 특징주", limit=25)
                
                # 중복 제거
                all_news = news_1 + news_2
                unique_news = {v['link']: v for v in all_news}.values()
                final_news_list = list(unique_news)
                
                market_data_str = f"종목명: {s_name}\n코드: {code}\n테마: {s_theme}\n시가총액: {fund['시가총액']}\n현재가(등락): {sel_data['현재가(등락률)']}\n시장구분: {sel_data['시장구분']}"
            
            st.subheader(f"2️⃣ [{s_name}] 상세 분석")
            st.info(f"💰 시가총액: **{fund['시가총액']}** | 🏆 테마: **{s_theme}**")
            
            with st.expander("💬 AI 투자 전략가와 대화하기 (Click)", expanded=True):
                if not st.session_state.messages:
                    if st.button(f"⚡ '{s_name}' 심층 분석 시작"):
                        sys_prompt = f"""
                        당신은 월가 출신의 퀀트 및 투자 전략가입니다.
                        제공된 [시장 데이터(30% 비중)]와 [뉴스 요약 데이터(70% 비중)]를 종합하여 분석하십시오.
                        
                        [분석 목표]
                        뉴스 요약문들에서 반복되는 키워드와 팩트를 추출하여 상승/하락의 '진짜 이유'를 찾아내고,
                        34세 직장인 투자자에게 맞는 매매 전략을 제시하십시오.
                        
                        반드시 다음 포맷으로 답변하세요:
                        1. 🚀 핵심 호재/악재 3가지 (팩트 기반)
                        2. 🔍 뉴스 키워드 분석 (언론이 주목하는 포인트)
                        3. 💡 실전 매매 전략 (매수/매도/관망 및 목표가)
                        """
                        st.session_state.messages.append({"role": "user", "content": sys_prompt})
                        with st.chat_message("assistant"):
                            res_txt = st.write_stream(get_gemini_response_mass_analysis(st.session_state.messages, selected_real_name, s_name, s_theme, market_data_str, final_news_list))
                        st.session_state.messages.append({"role": "assistant", "content": res_txt})

                for msg in st.session_state.messages:
                    if msg['role'] == 'user' and "당신은" in msg['content']: continue
                    with st.chat_message(msg['role']): st.markdown(msg['content'])

                if prompt := st.chat_input(f"{s_name} 질문..."):
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    with st.chat_message("user"): st.markdown(prompt)
                    with st.chat_message("assistant"):
                        model = genai.GenerativeModel(f"models/{selected_real_name}")
                        history = [{"role": "user" if m["role"]=="user" else "model", "parts": [m["content"]]} for m in st.session_state.messages]
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
                    cur_theme_list = df_C[df_C['테마명']==s_theme]
                    st.dataframe(cur_theme_list[['테마순위','종목명','현재가(등락률)']], hide_index=True)
            with col2:
                # [UI 반영] 상위 20개 제목만 출력
                st.markdown(f"##### 📰 관련 뉴스 (상위 20건)")
                st.caption(f"※ AI는 총 {len(final_news_list)}건의 데이터를 심층 분석했습니다.")
                
                if final_news_list:
                    for n in final_news_list[:20]: # 20개 슬라이싱
                        st.markdown(f"- [{n['title']}]({n['link']})")
                else:
                    st.warning("뉴스를 찾을 수 없습니다.")

    else:
        st.warning("조건(상승률 500위 & 거래량 500위 & 테마 50위)을 만족하는 종목이 없습니다.")

# --- Tab 2 ---
with tab2:
    st.header("📊 시장 전체 흐름 (시총 Top 150)")
    if df_market is not None:
        st.dataframe(df_market, height=400)
        
        st.subheader("🤖 AI 실시간 시황 브리핑")
        if st.button("📢 시황 뉴스 수집 및 분석 (RSS)"):
            with st.spinner("시황 뉴스 수집 중..."):
                news_1 = fetch_google_news_rss("한국 증시 시황", limit=20)
                news_2 = fetch_google_news_rss("코스피 코스닥 특징주", limit=20)
                
                all_market_news = news_1 + news_2
                unique_market_news = {v['link']: v for v in all_market_news}.values()
                final_market_news = list(unique_market_news)
                
                top_30_str = df_market.head(30).to_string(index=False)
            
            if final_market_news:
                st.success(f"✅ 뉴스 {len(final_market_news)}건 확보! 분석 시작.")
                with st.expander("🔍 수집된 데이터 확인", expanded=False):
                    for n in final_market_news:
                        st.write(f"- {n['title']}: {n['summary']}")
                
                st.write_stream(get_gemini_response_mass_analysis(
                    [{"role": "user", "content": "당신은 수석 애널리스트입니다. 시황을 분석해주세요."}], 
                    selected_real_name, 
                    "KOSPI/KOSDAQ", 
                    "Market", 
                    top_30_str, 
                    final_market_news
                ))
            else:
                st.error("⚠️ 뉴스 수집 실패.")
