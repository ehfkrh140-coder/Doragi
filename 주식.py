import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import google.generativeai as genai
import urllib.parse

# ==========================================
# 🔑 [필수] Gemini API 키 설정
# ==========================================
try:
    GOOG_API_KEY = st.secrets["GOOG_API_KEY"]
except:
    GOOG_API_KEY = "여기에_키를_넣으세요"

# 1. 페이지 설정
st.set_page_config(page_title="주식 테마 분석기 (AI Ver.)", layout="wide")
st.title("🤖 AI 주식 투자 전략가 (Smart Encoding Ver4.2.)")

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

# --- [핵심 수정] 뉴스 본문 크롤링 (스마트 인코딩 스위칭) ---
def fetch_news_body(url):
    """
    뉴스 링크의 최종 목적지를 확인하여 인코딩을 자동으로 전환하고 본문을 추출
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        # 1. 일단 요청을 보냄
        res = requests.get(url, headers=headers, timeout=3)
        
        # 2. [중요] 리다이렉트된 최종 URL 확인 후 인코딩 결정
        final_url = res.url
        if "news.naver.com" in final_url:
            res.encoding = 'utf-8' # 메인 뉴스는 utf-8
        elif "finance.naver.com" in final_url:
            res.encoding = 'cp949' # 금융 전용 페이지는 cp949
        else:
            res.encoding = 'utf-8' # 기타는 utf-8 시도
            
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 3. 본문 추출 (다양한 선택자 시도)
        body = ""
        
        # Case A: 네이버 메인 뉴스 (가장 흔함)
        if soup.select_one("#dic_area"):
            body = soup.select_one("#dic_area").get_text(strip=True)
            
        # Case B: 네이버 금융 전용 뉴스
        elif soup.select_one("#newsEndContents"):
            content = soup.select_one("#newsEndContents")
            # 불필요한 태그 삭제 (기자 정보, 링크 등)
            for tag in content.select("div, span, em, a"): 
                tag.decompose()
            body = content.get_text(strip=True)
            
        # Case C: 스포츠/연예 등
        elif soup.select_one(".article_body"):
            body = soup.select_one(".article_body").get_text(strip=True)
            
        # Case D: 최후의 수단 (P 태그 긁기)
        else:
            paragraphs = soup.find_all('p')
            if paragraphs:
                body = " ".join([p.get_text(strip=True) for p in paragraphs])
            
        if not body or len(body) < 50: 
            return None
            
        return body[:1500] + "..." # 1500자 제한
        
    except Exception as e:
        # st.error(f"본문 읽기 에러: {e}") # 디버깅용
        return None

# --- [1. 개별 종목 뉴스 리스트 (네이버 금융)] ---
def get_stock_news_list(code, limit=20):
    news_data = []
    try:
        url = f"https://finance.naver.com/item/news_news.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': f'https://finance.naver.com/item/main.naver?code={code}'}
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        
        titles = soup.select(".title > a")
        if not titles: titles = soup.select("a.tit")
        
        for i, t in enumerate(titles):
            if i >= limit: break
            link = "https://finance.naver.com" + t['href']
            news_data.append({"source": "종목뉴스", "title": t.get_text(strip=True), "link": link})
    except: pass
    return news_data

# --- [2. 키워드 검색 뉴스 리스트 (네이버 검색)] ---
def search_naver_news_keyword(keyword, limit=20):
    news_data = []
    try:
        enc_kw = urllib.parse.quote(keyword)
        url = f"https://search.naver.com/search.naver?where=news&query={enc_kw}&sm=tab_opt&sort=1"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        items = soup.select("div.news_wrap.api_ani_send")
        for i, item in enumerate(items):
            if i >= limit: break
            title_tag = item.select_one(".news_tit")
            if title_tag:
                news_data.append({"source": "검색뉴스", "title": title_tag.get_text(strip=True), "link": title_tag['href']})
    except: pass
    return news_data

# --- [3. 시황 뉴스 리스트] ---
def get_market_news_list(limit=30):
    news_data = []
    try:
        url = "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.content.decode('cp949', 'ignore'), 'html.parser')
        
        articles = soup.select("dd.articleSubject > a") + soup.select("dt.articleSubject > a")
        
        for i, art in enumerate(articles):
            if i >= limit: break
            link = "https://finance.naver.com" + art['href']
            news_data.append({"source": "시황속보", "title": art.get_text(strip=True), "link": link})
    except: pass
    return news_data

# --- [데이터 수집 함수들] ---
@st.cache_data
def get_naver_themes():
    url = "https://finance.naver.com/sise/theme.naver"
    headers = {'User-Agent': 'Mozilla/5.0'}
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
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(theme_link, headers=headers)
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
            res = requests.get(f"https://finance.naver.com/sise/sise_rise.naver?sosok={s}", headers={'User-Agent': 'Mozilla/5.0'})
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
            res = requests.get(f"https://finance.naver.com/sise/sise_quant_high.naver?sosok={s}", headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(res.text, 'html.parser')
            for item in soup.select("table.type_2 tr td a.tltle")[:200]: 
                tickers.append(item.text.strip())
        except: pass
    return tickers

def get_stock_fundamentals(code):
    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers)
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

# --- [AI 응답 함수] ---
def get_gemini_response_with_news(messages, model_name, stock_name, theme, news_list_1, news_list_2):
    genai.configure(api_key=GOOG_API_KEY)
    
    current_query = messages[-1]['content']
    search_res = ""
    
    if "당신은" in current_query:
        full_text_data = ""
        read_count = 0
        
        with st.status(f"📰 '{stock_name}' 관련 뉴스 본문을 분석 중...", expanded=True) as status:
            # 1. 종목 뉴스 (5개)
            for item in news_list_1[:5]:
                body = fetch_news_body(item['link'])
                if body:
                    full_text_data += f"[종목뉴스] {item['title']}\n{body}\n\n"
                    read_count += 1
                    st.write(f"✅ 읽음: {item['title']}")
                else:
                    st.write(f"⚠️ 읽기 실패(인코딩/차단): {item['title']}")
            
            # 2. 호재 검색 뉴스 (5개)
            for item in news_list_2[:5]:
                body = fetch_news_body(item['link'])
                if body:
                    full_text_data += f"[호재검색] {item['title']}\n{body}\n\n"
                    read_count += 1
                    st.write(f"✅ 읽음: {item['title']}")
                else:
                    st.write(f"⚠️ 읽기 실패(인코딩/차단): {item['title']}")
            
            status.update(label=f"완료! 총 {read_count}개의 기사 본문을 확보했습니다. (Data 2 생성 완료)", state="complete", expanded=False)
            
        # 프롬프트에 들어갈 Data 2
        search_res = f"\n[Data 2: 뉴스 본문 텍스트 모음 ({read_count}건)]:\n{full_text_data}\n"
    
    modified_msgs = []
    for i, msg in enumerate(messages):
        content = msg['content']
        if i == len(messages)-1: content += search_res
        modified_msgs.append({"role": "user" if msg['role']=="user" else "model", "parts": [content]})
    
    model = genai.GenerativeModel(f"models/{model_name}")
    response = model.generate_content(modified_msgs, stream=True)
    for chunk in response: yield chunk.text

def analyze_market_trend_ai(df, news_list, model_name):
    genai.configure(api_key=GOOG_API_KEY)
    model = genai.GenerativeModel(f"models/{model_name}")
    top_30 = df.head(30).to_string(index=False)
    
    full_text_data = ""
    read_count = 0
    with st.status("🌍 시장 시황 뉴스 본문을 읽고 있습니다...", expanded=True) as status:
        # 상위 10개 본문 읽기
        for item in news_list[:10]:
            body = fetch_news_body(item['link'])
            if body:
                full_text_data += f"[시황뉴스] {item['title']}\n{body}\n\n"
                read_count += 1
                st.write(f"✅ 읽음: {item['title']}")
            else:
                 st.write(f"⚠️ 읽기 실패: {item['title']}")
        status.update(label=f"분석 준비 완료! (본문 {read_count}건 확보)", state="complete", expanded=False)
    
    headlines = "\n".join([f"- {n['title']}" for n in news_list[10:]])
    
    prompt = f"""
    당신은 월가 출신의 수석 애널리스트입니다. 
    다음 데이터를 바탕으로 현재 시장 상황을 깊이 있게 브리핑하세요.

    [Data 1: 코스피 시총 상위 30위 흐름]
    {top_30}
    
    [Data 2: 주요 시황 뉴스 심층 분석 (본문 {read_count}건)]
    {full_text_data}
    
    [Data 3: 추가 뉴스 헤드라인]
    {headlines}
    
    [분석 가이드]:
    1. 'Data 2'의 뉴스 본문에 언급된 금리, 환율, 해외 증시, 정책 등의 핵심 요인을 상세히 설명하십시오.
    2. 시총 상위주의 움직임과 뉴스를 연결하여 오늘 시장의 주도 섹터와 소외 섹터를 명확히 구분하십시오.
    3. 34세 직장인 투자자를 위한 '오늘 당장 취해야 할 포지션(매수/매도/관망)'을 제안하십시오.
    """
    response = model.generate_content(prompt, stream=True)
    for chunk in response: yield chunk.text

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

with st.status("🚀 시장 데이터 수집 중... (네이버 금융)", expanded=True) as status:
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

            with st.spinner(f"🔍 {s_name} 관련 뉴스 수집 중..."):
                fund = get_stock_fundamentals(code)
                news_list_1 = get_stock_news_list(code, limit=20)
                news_list_2 = search_naver_news_keyword(f"{s_name} 호재", limit=20)
            
            st.subheader(f"2️⃣ [{s_name}] 상세 분석")
            st.info(f"💰 시가총액: **{fund['시가총액']}** | 🏆 테마: **{s_theme}**")
            
            with st.expander("💬 AI 투자 전략가와 대화하기 (Click)", expanded=True):
                if not st.session_state.messages:
                    if st.button(f"⚡ '{s_name}' 심층 분석 시작 (본문 10개 읽기)"):
                        all_news = news_list_1 + news_list_2
                        news_ctx = "\n".join([f"- {n['title']}" for n in all_news])
                        
                        sys_prompt = f"""
                        당신은 공격적인 투자 전략가입니다. {s_name}({s_theme})을 호재 위주로 분석하세요.
                        [참고 뉴스 헤드라인]:
                        {news_ctx}
                        
                        (잠시 후 제공될 [Data 2: 뉴스 본문] 내용을 최우선으로 분석하십시오.)
                        반드시 '🚀 핵심 호재 3가지', '📈 테마 전망', '💡 매매 전략' 순서로 브리핑하세요.
                        """
                        st.session_state.messages.append({"role": "user", "content": sys_prompt})
                        with st.chat_message("assistant"):
                            res_txt = st.write_stream(get_gemini_response_with_news(st.session_state.messages, selected_real_name, s_name, s_theme, news_list_1, news_list_2))
                        st.session_state.messages.append({"role": "assistant", "content": res_txt})

                for msg in st.session_state.messages:
                    if msg['role'] == 'user' and "당신은" in msg['content']: continue
                    with st.chat_message(msg['role']): st.markdown(msg['content'])

                if prompt := st.chat_input(f"{s_name} 질문..."):
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    with st.chat_message("user"): st.markdown(prompt)
                    with st.chat_message("assistant"):
                        model = genai.GenerativeModel(f"models/{selected_real_name}")
                        history = []
                        for m in st.session_state.messages:
                            history.append({"role": "user" if m["role"]=="user" else "model", "parts": [m["content"]]})
                        res = model.generate_content(history, stream=True)
                        res_txt = st.write_stream(res)
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
                st.markdown(f"##### 📰 관련 뉴스 (총 {len(news_list_1) + len(news_list_2)}건)")
                st.caption("※ AI가 상위 10개 기사의 본문을 정독하고 분석합니다.")
                if news_list_1:
                    st.markdown("**[종목 뉴스]**")
                    for n in news_list_1: st.markdown(f"- [{n['title']}]({n['link']})")
                if news_list_2:
                    st.markdown("**[호재 검색]**")
                    for n in news_list_2: st.markdown(f"- [{n['title']}]({n['link']})")
    else:
        st.warning("조건을 만족하는 종목이 없습니다.")

# --- Tab 2 ---
with tab2:
    st.header("📊 시장 전체 흐름 (시총 Top 150)")
    if df_market is not None:
        st.dataframe(df_market, height=400)
        
        st.subheader("🤖 AI 실시간 시황 브리핑")
        if st.button("📢 시황 뉴스 30개 수집 및 분석 (본문 10개)"):
            with st.spinner("네이버 금융에서 시황 뉴스 30개를 수집 중입니다..."):
                market_news = get_market_news_list(limit=30)
            
            st.success(f"✅ 뉴스 {len(market_news)}건 수집 완료! 상위 10개 본문을 정독합니다.")
            
            with st.expander("🔍 수집된 뉴스 목록 보기", expanded=False):
                for n in market_news:
                    st.write(f"- {n['title']}")
                
            st.write_stream(analyze_market_trend_ai(df_market, market_news, selected_real_name))

