import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import google.generativeai as genai
import urllib.parse
from datetime import datetime

# ==========================================
# 🔑 [필수] Gemini API 키 설정
# ==========================================
try:
    GOOG_API_KEY = st.secrets["GOOG_API_KEY"]
except:
    GOOG_API_KEY = "여기에_키를_넣으세요"

# 1. 페이지 설정
st.set_page_config(page_title="주식 테마 분석기", layout="wide")
st.title("🤖 AI 주식 투자 전략가 (RSS & Safety Ver.)")

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

# --- [유틸: 요청 헤더] ---
def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

# --- [핵심 1] 구글 뉴스 RSS 수집기 (네이버 차단 우회용) ---
def get_google_news_rss(query, limit=20):
    """
    네이버가 차단될 경우를 대비해 Google News RSS를 사용하여 뉴스를 수집합니다.
    이 방식은 IP 차단에 매우 강하며 데이터를 안정적으로 가져옵니다.
    """
    news_data = []
    try:
        # 구글 뉴스 RSS (한국어, 한국 지역)
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
        
        res = requests.get(url, headers=get_headers(), timeout=5)
        
        if res.status_code == 200:
            # XML 파싱
            soup = BeautifulSoup(res.content, 'xml')
            items = soup.find_all('item')
            
            for item in items[:limit]:
                title = item.title.text
                link = item.link.text
                pub_date = item.pubDate.text if item.pubDate else ""
                
                # 출처 추출 (제목 뒤에 보통 ' - 언론사명' 붙음)
                source = "News"
                if "-" in title:
                    source = title.split("-")[-1].strip()
                
                news_data.append({
                    "source": source,
                    "title": title,
                    "link": link,
                    "pub_date": pub_date
                })
    except Exception as e:
        print(f"RSS Error: {e}")
        
    return news_data

# --- [핵심 2] 본문 읽기 (RSS 링크 추적) ---
def fetch_news_body(url):
    try:
        session = requests.Session()
        # 구글 RSS 링크는 리다이렉트가 발생하므로 따라가야 함
        res = session.get(url, headers=get_headers(), timeout=5, allow_redirects=True)
        
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 불필요 태그 제거
        for tag in soup(["script", "style", "iframe", "header", "footer", "button", "nav"]):
            tag.decompose()
            
        body = ""
        # 일반적인 본문 태그 패턴들
        selectors = [
            "article", ".article_body", "#articleBody", "#dic_area", 
            "#newsEndContents", ".news_view", ".content_view"
        ]
        
        for selector in selectors:
            target = soup.select_one(selector)
            if target:
                body = target.get_text(separator=" ", strip=True)
                break
        
        if not body:
            # 본문 못 찾으면 p 태그 중 긴 것들 위주로 수집
            paragraphs = soup.find_all('p')
            body = " ".join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50])

        if len(body) < 100: return None
        return body[:1500] + "..."
    except: return None

# --- [데이터 수집 함수들 (네이버 금융 테이블 등)] ---
# 테마, 시총 등은 HTML 구조가 단순하여 아직 차단되지 않았을 가능성이 높으므로 유지
# 만약 이것도 차단되면 RSS 데이터만으로 분석해야 함.

@st.cache_data
def get_naver_themes():
    url = "https://finance.naver.com/sise/theme.naver"
    try:
        res = requests.get(url, headers=get_headers())
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        data = []
        for row in soup.select("#contentarea_left > table.type_1 > tr"):
            cols = row.select("td")
            if len(cols) >= 4:
                data.append({"테마명": cols[0].text.strip(), "링크": "https://finance.naver.com" + cols[0].find('a')['href']})
        return pd.DataFrame(data).head(20)
    except: return pd.DataFrame()

def get_theme_details(theme_link):
    try:
        res = requests.get(theme_link, headers=get_headers())
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
    for index, row in df_themes.iterrows():
        stocks_info = get_theme_details(row['링크'])
        stocks_info.sort(key=lambda x: float(x['price_str'].split('(')[1].replace('%)','').replace('+','').replace('-','-').replace(',','')) if '(' in x['price_str'] else 0, reverse=True)
        for rank, stock in enumerate(stocks_info, 1):
             all_stocks.append({
                 "테마순위": f"{rank}위", "종목명": stock['name'], "종목코드": stock['code'], 
                 "테마명": row['테마명'], "현재가(등락률)": stock['price_str']
             })
    return pd.DataFrame(all_stocks)

@st.cache_data
def get_top_risers_info():
    market_map = {}
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}", headers=get_headers())
            soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:300]: 
                market_map[item.text.strip()] = "KOSPI" if s==0 else "KOSDAQ"
        except: pass
    return market_map

@st.cache_data
def get_volume_leaders():
    tickers = []
    for s in [0, 1]:
        try:
            res = requests.get(f"https://finance.naver.com/sise/sise_quant_high.naver?sosok={s}", headers=get_headers())
            soup = BeautifulSoup(res.text, 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:200]: 
                tickers.append(item.text.strip())
        except: pass
    return tickers

def get_stock_fundamentals(code):
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        res = requests.get(url, headers=get_headers())
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
            res = requests.get(f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page={page}", headers=get_headers())
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

# --- [AI 응답 함수 (안전장치 추가)] ---
def get_gemini_response_safe(messages, model_name, stock_name, theme, news_list):
    genai.configure(api_key=GOOG_API_KEY)
    
    # 시스템 프롬프트가 포함된 경우에만 뉴스 분석
    current_query = messages[-1]['content']
    search_res = ""
    
    if "당신은" in current_query and news_list:
        full_text_data = ""
        read_count = 0
        
        with st.status(f"📰 '{stock_name}' 뉴스 분석 중 (Google RSS)...", expanded=True) as status:
            # 상위 10개 시도
            for item in news_list[:10]:
                body = fetch_news_body(item['link'])
                time.sleep(0.1)
                
                if body:
                    full_text_data += f"[{item['source']}] {item['title']}\n{body}\n\n"
                    read_count += 1
                    st.write(f"✅ 본문 확보: {item['title']}")
                else:
                    # 본문 실패시 제목이라도 사용
                    full_text_data += f"[{item['source']}] {item['title']}\n(본문 읽기 실패)\n\n"
                    st.write(f"⚠️ 제목만 사용: {item['title']}")
            
            status.update(label=f"분석 준비 완료! (본문 {read_count}건)", state="complete", expanded=False)
            
        search_res = f"\n[뉴스 데이터]:\n{full_text_data}\n"
    
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
        # [핵심] API 한도 초과 등 에러 핸들링
        error_msg = str(e)
        if "429" in error_msg or "ResourceExhausted" in error_msg:
            yield "⚠️ **[API 한도 초과]** Gemini API 사용량이 한도에 도달했습니다.\n\n잠시 기다리시거나(1~2분), 다른 Google API 키로 교체해주세요."
        else:
            yield f"⚠️ **[AI 분석 오류]** 문제가 발생했습니다: {error_msg}"

def analyze_market_safe(df, news_list, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    top_30 = df.head(30).to_string(index=False)
    
    full_text_data = ""
    read_count = 0
    
    with st.status("🌍 시황 뉴스 분석 중 (Google RSS)...", expanded=True) as status:
        if not news_list:
             st.error("뉴스 목록이 없습니다.")
        
        for item in news_list[:10]:
            body = fetch_news_body(item['link'])
            time.sleep(0.1)
            if body:
                full_text_data += f"[뉴스] {item['title']}\n{body}\n\n"
                read_count += 1
                st.write(f"✅ 본문 확보: {item['title']}")
            else:
                 full_text_data += f"[뉴스] {item['title']}\n(본문 없음)\n\n"
                 st.write(f"⚠️ 제목만 사용: {item['title']}")
                 
        status.update(label=f"분석 준비 완료! (데이터 {len(news_list)}건)", state="complete", expanded=False)
    
    prompt = f"""
    당신은 수석 애널리스트입니다. 아래 데이터를 바탕으로 시황을 브리핑하세요.
    [시총 상위 30위]: {top_30}
    [뉴스 데이터]: {full_text_data}
    
    1. 거시 경제(금리, 환율) 및 주요 이슈 분석.
    2. 시총 상위주와 뉴스를 연결한 섹터 분석.
    3. 34세 직장인 투자자를 위한 전략.
    """
    
    try:
        response = model.generate_content(prompt, stream=True)
        for chunk in response: yield chunk.text
    except Exception as e:
        if "429" in error_msg or "ResourceExhausted" in error_msg:
            yield "⚠️ **[API 한도 초과]** Gemini API 사용량이 한도에 도달했습니다.\n\n잠시 기다리시거나(1~2분), 다른 Google API 키로 교체해주세요."
        else:
            yield f"⚠️ **[AI 분석 오류]** 문제가 발생했습니다: {str(e)}"

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
with st.status("🚀 시장 데이터 수집 중... (Naver & Google RSS)", expanded=True) as status:
    df_market = get_market_cap_top150()
    market_map = get_top_risers_info()
    vol_leaders = get_volume_leaders()
    df_C = get_all_theme_stocks()
    status.update(label="✅ 데이터 준비 완료!", state="complete", expanded=False)

tab1, tab2 = st.tabs(["🎯 급등주 발굴", "📊 시황 분석"])

# --- Tab 1 ---
with tab1:
    st.subheader("1️⃣ 교집합 분석 결과 (핵심 주도주)")
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

            # [핵심] Google News RSS로 뉴스 수집 (네이버 차단 회피)
            with st.spinner(f"🔍 {s_name} 뉴스 수집 중 (Google RSS)..."):
                fund = get_stock_fundamentals(code)
                # 1. 종목명으로 검색
                news_list = get_google_news_rss(f"{s_name} 주가 특징주", limit=20)
                # 2. 호재 키워드로 추가 검색
                news_list_2 = get_google_news_rss(f"{s_name} 호재", limit=10)
                
                # 중복 제거 및 합치기
                all_news = news_list + news_list_2
                unique_news = []
                seen_links = set()
                for n in all_news:
                    if n['link'] not in seen_links:
                        unique_news.append(n)
                        seen_links.add(n['link'])
            
            st.subheader(f"2️⃣ [{s_name}] 상세 분석")
            st.info(f"💰 시가총액: **{fund['시가총액']}** | 🏆 테마: **{s_theme}**")
            
            with st.expander("💬 AI 투자 전략가와 대화하기 (Click)", expanded=True):
                if not st.session_state.messages:
                    if st.button(f"⚡ '{s_name}' 심층 분석 시작"):
                        news_ctx = "\n".join([f"- {n['title']}" for n in unique_news[:10]])
                        
                        sys_prompt = f"""
                        당신은 공격적인 투자 전략가입니다. {s_name}({s_theme})을 호재 위주로 분석하세요.
                        [참고 뉴스 헤드라인]:
                        {news_ctx}
                        
                        반드시 '🚀 핵심 호재 3가지', '📈 테마 전망', '💡 매매 전략' 순서로 브리핑하세요.
                        """
                        st.session_state.messages.append({"role": "user", "content": sys_prompt})
                        with st.chat_message("assistant"):
                            res_txt = st.write_stream(get_gemini_response_safe(st.session_state.messages, selected_real_name, s_name, s_theme, unique_news))
                        st.session_state.messages.append({"role": "assistant", "content": res_txt})

                for msg in st.session_state.messages:
                    if msg['role'] == 'user' and "당신은" in msg['content']: continue
                    with st.chat_message(msg['role']): st.markdown(msg['content'])

                if prompt := st.chat_input(f"{s_name} 질문..."):
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    with st.chat_message("user"): st.markdown(prompt)
                    with st.chat_message("assistant"):
                        # 일반 대화는 뉴스 없이 진행
                        model = genai.GenerativeModel(f"models/{selected_real_name}")
                        history = []
                        for m in st.session_state.messages:
                            history.append({"role": "user" if m["role"]=="user" else "model", "parts": [m["content"]]})
                        
                        try:
                            res = model.generate_content(history, stream=True)
                            res_txt = st.write_stream(res)
                        except Exception as e:
                            res_txt = f"⚠️ API 오류: {str(e)}"
                            st.error(res_txt)
                            
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
                st.markdown(f"##### 📰 관련 뉴스 (총 {len(unique_news)}건)")
                if unique_news:
                    for n in unique_news: 
                        st.markdown(f"- [{n['title']}]({n['link']}) <span style='color:grey; font-size:0.8em'>({n['source']})</span>", unsafe_allow_html=True)
                else:
                    st.warning("뉴스를 찾을 수 없습니다.")

    else:
        st.warning("조건을 만족하는 종목이 없습니다.")

# --- Tab 2 ---
with tab2:
    st.header("📊 시장 전체 흐름 (시총 Top 150)")
    if df_market is not None:
        st.dataframe(df_market, height=400)
        
        st.subheader("🤖 AI 실시간 시황 브리핑")
        if st.button("📢 시황 뉴스 수집 및 분석 (Google RSS)"):
            with st.spinner("Google News RSS에서 시황 뉴스를 수집 중입니다..."):
                market_news = get_google_news_rss("한국 증시 시황 코스피 코스닥", limit=30)
            
            if market_news:
                st.success(f"✅ 뉴스 {len(market_news)}건 수집 완료! 분석을 시작합니다.")
                with st.expander("🔍 수집된 뉴스 목록 보기", expanded=False):
                    for n in market_news:
                        st.write(f"- {n['title']}")
                st.write_stream(analyze_market_safe(df_market, market_news, selected_real_name))
            else:
                st.error("⚠️ 뉴스 수집에 실패했습니다.")
