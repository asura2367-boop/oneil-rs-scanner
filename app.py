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

st.markdown("""<style>
.block-container{padding-top:1.2rem;padding-bottom:2rem}
h1{font-size:1.65rem!important;margin-bottom:.1rem!important}
h2,h3{font-size:1.15rem!important;margin-top:.7rem!important}
[data-testid="stMetricValue"]{font-size:1.35rem!important}
[data-testid="stVerticalBlockBorderWrapper"]{padding:0!important}
</style>""", unsafe_allow_html=True)
st.title('📈 오닐 RS 스캐너')
st.caption('한국투자증권 일봉 · 저장 데이터 · 실시간 아님')
status, status_source = load_status()
if status:
    total = int(status.get('universe_total',0))
    collected = int(status.get('collected_symbols',0))
    pct = min(100.,100*collected/total) if total else 0
    st.markdown(f'**전체시장 수집** · {collected:,} / {total:,}종목 ({pct:.2f}%)')
    st.progress(pct/100)
    st.caption(f"기준 {status.get('as_of','미확인')} · 최근 성공 {status.get('last_run_success','?')} / 실패 {status.get('last_run_failures','?')} · {status_source}")
else:
    st.warning('수집 진행률 조회 실패 — 기존 저장 일봉으로만 분석합니다.')
with st.sidebar:
    st.header('추천 설정')
    threshold = st.slider('부분 표본 RS 최소값',70,99,90)
    only_good = st.checkbox('상승채널 양호만 표시',False)
    vol_check = st.checkbox('거래량 1.2배 이상만 표시',False)
    top_n = st.slider('표시 종목 수',5,30,10,5)
    if st.button('저장 데이터 새로고침'):
        load_csv.clear(); load_status.clear(); st.rerun()
st.caption('※ RS는 **수집된 종목끼리 비교한 시험 등급**이며 전체시장 RS가 아닙니다. 외국인·기관 수급은 미연동입니다.')
df, source = load_csv('kis_daily_universe.csv')
if df is None:
    st.error('일봉 CSV 조회 실패: data/kis_daily_universe.csv')
    st.stop()
try:
    ranked, histories = rank_candidates(df)
except Exception as e:
    st.error(f'데이터 분석 오류: {e}')
    st.stop()
if ranked.empty:
    st.warning('253거래일 이상 확보된 종목이 없습니다.')
    st.stop()
latest_date = pd.to_datetime(ranked['date']).max().date()
st.caption(f'일봉 최신일 {latest_date} · RS 계산 가능 {len(ranked):,}종목 · {source}')
filtered = ranked[ranked['sample_rs'] >= threshold].copy()
if only_good: filtered = filtered[filtered['channel_good']]
if vol_check: filtered = filtered[filtered['volume_ratio'] >= 1.2]
st.subheader('추천 후보 TOP 10')
if filtered.empty:
    st.warning('조건을 충족하는 종목이 없습니다. 설정을 조정하세요.')
else:
    for i, (_,r) in enumerate(filtered.head(top_n).iterrows(),1):
        ratio = r['volume_ratio']
        pos = r['channel_position']
        good = bool(r['channel_good'])
        if pd.isna(ratio) or pd.isna(pos):
            verdict = '데이터 확인'
        elif pos < 0 or pos > 1:
            verdict = '주의 · 채널 이탈'
        elif good and ratio >= 1.2:
            verdict = '관심 · 채널/거래량 양호'
        elif good:
            verdict = '관찰 · 거래량 확인'
        else:
            verdict = '관찰 · 채널 위치 확인'
        volume_text = f'{ratio:.2f}배' if pd.notna(ratio) else '미확인'
        channel_text = f'{pos*100:.0f}%' if pd.notna(pos) else '미확인'
        with st.container(border=True):
            a,b=st.columns([4,1],vertical_alignment='center')
            a.markdown(f'**{i}. {r["name"]}**  ·  `{r["ticker"]}`')
            b.markdown(f'**RS {int(r["sample_rs"])}**')
            st.caption(f'{verdict}  |  거래량 {volume_text}  |  채널 {channel_text}  |  수급 미확인')
st.caption('판정은 참고용 기술적 분류이며 매수 지시가 아닙니다. RS 우선 정렬, 거래량·채널은 동점 보조 조건입니다.')
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
