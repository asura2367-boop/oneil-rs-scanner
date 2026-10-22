import json
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title='오닐 RS 스캐너', page_icon='📈', layout='centered')
ROOT = Path(__file__).resolve().parent
RAW = 'https://raw.githubusercontent.com/asura2367-boop/oneil-rs-scanner/main/data/'

@st.cache_data(ttl=900, show_spinner=False)
def load_csv(filename):
    # Fetch committed updates without requiring Streamlit redeployment.
    url = RAW + filename
    try:
        d = pd.read_csv(url, dtype={'ticker': str}, encoding='utf-8-sig')
        return d, 'GitHub 최신 파일'
    except Exception:
        local = ROOT / 'data' / filename
        if local.exists():
            return pd.read_csv(local, dtype={'ticker': str}, encoding='utf-8-sig'), '배포 시점 파일(최신 아닐 수 있음)'
        return None, '조회 실패'

@st.cache_data(ttl=900, show_spinner=False)
def load_status():
    import requests
    try:
        r = requests.get(RAW + 'collection_status.json', timeout=12)
        r.raise_for_status()
        return r.json(), 'GitHub 최신 파일'
    except Exception:
        local = ROOT / 'data' / 'collection_status.json'
        if local.exists():
            return json.loads(local.read_text(encoding='utf-8-sig')), '배포 시점 파일'
        return None, '조회 실패'

def rank_candidates(df):
    needed = {'ticker', 'name', 'date', 'close', 'volume'}
    if not needed.issubset(df.columns):
        raise ValueError(f'CSV에 필요한 열이 없습니다: {sorted(needed - set(df.columns))}')
    d = df.copy()
    d['ticker'] = d['ticker'].astype(str).str.zfill(6)
    d['date'] = pd.to_datetime(d['date'], errors='coerce')
    for c in ('close', 'volume'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d = d.dropna(subset=['date','close']).query('close > 0')
    d = d.sort_values(['ticker','date']).drop_duplicates(['ticker','date'],keep='last')
    rows = []
    histories = {}
    for ticker, h in d.groupby('ticker', sort=False):
        h = h.sort_values('date').copy()
        # Approximately 12 calendar months of trading sessions, require 240 sessions.
        if len(h) < 240: continue
        p = h['close'].to_numpy(dtype=float)
        # 63/126/189/252 trading-day returns, with last available date as anchor.
        if len(p) < 253: continue
        returns = [(p[-1] / p[-1-n] - 1) * 100 for n in (63,126,189,252)]
        score = float(np.dot(returns, [.4,.2,.2,.2]))
        recent = h['volume'].iloc[-1]
        base = h['volume'].iloc[-21:-1].mean()
        vr = float(recent/base) if pd.notna(base) and base > 0 else np.nan
        y = np.log(p[-60:]); x = np.arange(60)
        slope, intercept = np.polyfit(x,y,1)
        mid = intercept + slope*x
        width = max(2*np.std(y-mid),1e-8)
        position = float((y[-1]-mid[-1]+width)/(2*width))
        channel_good = bool(slope>0 and 0 <= position <= .65)
        rows.append({'ticker':ticker,'name':str(h['name'].iloc[-1]),'date':h['date'].iloc[-1],
                     'weighted_return':score,'volume_ratio':vr,'channel_position':position,
                     'channel_good':channel_good,'r3':returns[0],'r6':returns[1],
                     'r9':returns[2],'r12':returns[3]})
        histories[ticker] = h
    if not rows: return pd.DataFrame(), histories
    result = pd.DataFrame(rows)
    result['sample_rs'] = np.ceil(result['weighted_return'].rank(method='average',pct=True)*99).clip(1,99).astype(int)
    result = result.sort_values(['sample_rs','channel_good','volume_ratio','weighted_return'],ascending=[False,False,False,False])
    return result, histories

st.title('📈 오닐 RS 종목추천')
st.caption('한국투자증권 일봉 기반 · 저장된 데이터 · 실시간 아님')
status, status_source = load_status()
if status:
    total = int(status.get('universe_total',0))
    collected = int(status.get('collected_symbols',0))
    pct = min(100.,100*collected/total) if total else 0
    st.subheader('전체시장 수집 진행률')
    c1,c2,c3=st.columns(3)
    c1.metric('전체 종목',f'{total:,}')
    c2.metric('수집 완료',f'{collected:,}')
    c3.metric('진행률',f'{pct:.2f}%')
    st.progress(pct/100)
    st.caption(f"기준일 {status.get('as_of','미확인')} · 최근 성공 {status.get('last_run_success','?')}종목 · 최근 실패 {status.get('last_run_failures','?')}종목 · {status_source}")
else:
    st.warning('수집 진행률을 불러오지 못했습니다. GitHub의 collection_status.json을 확인하세요.')

with st.sidebar:
    st.header('추천 설정')
    threshold = st.slider('부분 표본 RS 최소값',70,99,90)
    only_good = st.checkbox('상승채널 양호 종목만',False)
    vol_check = st.checkbox('거래량 20일 평균 이상만',False)
    top_n = st.slider('표시 종목 수',5,30,10,5)
    if st.button('데이터 새로 불러오기'):
        load_csv.clear(); load_status.clear(); st.rerun()

st.info('현재 RS는 **수집된 종목만 비교한 시험용 상대강도**입니다. 전체 2,675종목의 공식 RS 또는 전체시장 RS 90으로 해석하면 안 됩니다. 외국인·기관 수급은 아직 반영되지 않았습니다.')
df, source = load_csv('kis_daily_universe.csv')
if df is None:
    st.error('전체시장 일봉 CSV를 불러오지 못했습니다. GitHub data/kis_daily_universe.csv를 확인하세요.')
    st.stop()
try:
    ranked, histories = rank_candidates(df)
except Exception as e:
    st.error(f'데이터 분석 오류: {e}')
    st.stop()
if ranked.empty:
    st.warning('253거래일 이상의 일봉이 확보된 종목이 없어 RS를 계산할 수 없습니다.')
    st.stop()

st.caption(f'데이터 출처: {source} · RS 계산 가능 종목 {len(ranked):,}개 · 전체 수집 완료 전 임시 순위')
filtered = ranked[ranked['sample_rs'] >= threshold].copy()
if only_good: filtered=filtered[filtered['channel_good']]
if vol_check: filtered=filtered[filtered['volume_ratio']>=1]
st.subheader('📱 부분 표본 추천 TOP 10')
if filtered.empty:
    st.warning('현재 조건을 만족하는 종목이 없습니다. 사이드바에서 조건을 완화하세요.')
else:
    for _,r in filtered.head(top_n).iterrows():
        with st.container(border=True):
            a,b=st.columns([3,1])
            a.markdown(f"**{r['name']} · {r['ticker']}**")
            b.metric('시험 RS',int(r['sample_rs']))
            vr=f"{r['volume_ratio']:.2f}배" if pd.notna(r['volume_ratio']) else '미확인'
            st.caption(f"가중수익률 {r['weighted_return']:+.1f}% · 거래량 {vr} · 채널 위치 {r['channel_position']*100:.0f}% · 채널 양호 {'예' if r['channel_good'] else '아니오'}")

st.subheader('종목별 상세 차트')
choices = {f"{r['name']} ({r['ticker']})":r['ticker'] for _,r in ranked.iterrows()}
selected = st.selectbox('종목 선택', list(choices))
if selected:
    h = histories[choices[selected]]
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=h['date'],y=h['close'],name='종가',mode='lines'))
    for n in (20,60):
        fig.add_trace(go.Scatter(x=h['date'],y=h['close'].rolling(n).mean(),name=f'{n}일선'))
    h60=h.tail(60)
    y=np.log(h60['close'].to_numpy(float)); x=np.arange(len(y))
    slope, intercept=np.polyfit(x,y,1); mid=intercept+slope*x
    width=max(2*np.std(y-mid),1e-8)
    for offset,label in ((width,'채널 상단'),(0,'채널 중심'),(-width,'채널 하단')):
        fig.add_trace(go.Scatter(x=h60['date'],y=np.exp(mid+offset),name=label,line={'dash':'dot'}))
    fig.update_layout(height=420,margin=dict(l=5,r=5,t=15,b=5),legend=dict(orientation='h',y=-0.2))
    st.plotly_chart(fig,use_container_width=True)
    row=ranked.loc[ranked['ticker']==choices[selected]].iloc[0]
    st.caption(f"3개월 {row['r3']:+.1f}% · 6개월 {row['r6']:+.1f}% · 9개월 {row['r9']:+.1f}% · 12개월 {row['r12']:+.1f}%")
st.caption('RS 근사식: 3/6/9/12개월 수익률 40/20/20/20 가중치. 기간은 각각 63/126/189/252거래일로 근사합니다. 투자 판단 참고용이며 수익을 보장하지 않습니다.')
