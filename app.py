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
st.caption('※ RS는 수집된 종목끼리 비교한 시험 등급입니다. 외국인·기관 수급은 저장된 종목만 반영하며, 미수집은 미확인입니다.')

@st.cache_data(ttl=900, show_spinner=False)
def load_flow():
    d, source = load_csv('kis_investor_flow.csv')
    if d is None: return pd.DataFrame(), source
    needed = {'ticker','date','foreign_net_qty','institution_net_qty'}
    if not needed.issubset(d.columns): return pd.DataFrame(), '수급 CSV 열 불일치'
    d['ticker'] = d['ticker'].astype(str).str.zfill(6)
    d['date'] = pd.to_datetime(d['date'], errors='coerce')
    for c in ('foreign_net_qty','institution_net_qty'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    return d.dropna(subset=['date']).sort_values('date').drop_duplicates(['ticker','date'],keep='last'), source

flow, flow_source = load_flow()
flow_summary = {}
if not flow.empty:
    for code, h in flow.groupby('ticker'):
        h=h.sort_values('date').dropna(subset=['foreign_net_qty','institution_net_qty'])
        if h.empty: continue
        last5=h.tail(5)
        flow_summary[code] = {
            'n':len(h), 'date':h['date'].max(),
            'f5':float(last5['foreign_net_qty'].sum()) if len(last5)==5 else None,
            'i5':float(last5['institution_net_qty'].sum()) if len(last5)==5 else None,
            'f20':float(h.tail(20)['foreign_net_qty'].sum()) if len(h)>=20 else None,
            'i20':float(h.tail(20)['institution_net_qty'].sum()) if len(h)>=20 else None,
        }
st.caption(f'외국인·기관 수급 저장 종목: {len(flow_summary)}개 · {flow_source}')

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
# RS >= threshold is the eligibility gate; within eligible stocks, prefer
# recent verified foreign/institution accumulation before technical tie-breakers.
latest_by_ticker = ranked.set_index('ticker')['date'].to_dict()
def flow_rank(code):
    f = flow_summary.get(code)
    if not f or f['f5'] is None or f['i5'] is None:
        return (-1, -1, -1)
    age = (pd.Timestamp(latest_by_ticker[code]) - f['date']).days
    if age < 0 or age > 7:
        return (-1, -1, -1)
    f5, i5 = f['f5'], f['i5']
    # Signs, not absolute share counts, are compared across companies.
    both = int(f5 > 0 and i5 > 0)
    positive = int(f5 > 0) + int(i5 > 0)
    long_confirm = (int(f['f20'] > 0) + int(f['i20'] > 0)
                    if f['f20'] is not None and f['i20'] is not None else -1)
    return (both, positive, long_confirm)

ranked[['flow_both','flow_positive','flow_long']] = pd.DataFrame(
    [flow_rank(c) for c in ranked['ticker']], index=ranked.index)
ranked = ranked.sort_values(
    ['flow_both','flow_positive','flow_long','sample_rs','volume_ratio','channel_good','weighted_return'],
    ascending=[False,False,False,False,False,False,False])
# Diagnostic panel: distinguish missing, stale, and valid investor flow.
with st.expander('🔎 수급 데이터 연결 진단', expanded=False):
    st.write(f'수급 CSV 출처: {flow_source}')
    st.write(f'수급 데이터 행: {len(flow):,}개 / 고유 종목: {len(flow_summary):,}개')
    if not flow.empty:
        st.write(f"수급 최신 날짜: {flow['date'].max().date()}")
    st.write(f'RS 계산 가능 종목: {len(ranked):,}개')
    valid_count = int((ranked['flow_both'] >= 0).sum())
    st.write(f'RS 대상 중 최근 수급 5일 확인: {valid_count:,}개')
    candidates = ranked[ranked['sample_rs'] >= threshold]
    valid_candidates = candidates[candidates['flow_both'] >= 0]
    st.write(f'RS 기준 통과: {len(candidates):,}개 / 이 중 수급 확인: {len(valid_candidates):,}개')
    if valid_candidates.empty:
        st.info('현재 RS 기준을 통과한 종목 중 최근 수급이 확인된 종목이 없습니다. 수급 파일의 종목 구성·최신일을 확인하세요.')
    else:
        diag = []
        for _, item in valid_candidates.head(15).iterrows():
            f = flow_summary[item['ticker']]
            diag.append({'종목':item['name'], '코드':item['ticker'], 'RS':int(item['sample_rs']),
                         '수급일':str(f['date'].date()), '외국인 5일(주)':f['f5'], '기관 5일(주)':f['i5']})
        st.dataframe(pd.DataFrame(diag), hide_index=True, use_container_width=True)
    with st.container():
        st.caption('최근 수급은 일봉 최신일 대비 0~7일 차이, 5거래일 자료가 있을 때만 유효로 판정합니다. 전체시장 RS가 아닌 부분 표본 RS입니다.')

filtered = ranked[ranked['sample_rs'] >= threshold].copy()
if only_good: filtered = filtered[filtered['channel_good']]
if vol_check: filtered = filtered[filtered['volume_ratio'] >= 1.2]

def flow_details(r):
    f = flow_summary.get(r['ticker'])
    if not f or f['f5'] is None or f['i5'] is None:
        return None
    age = (pd.Timestamp(r['date']) - f['date']).days
    if not (0 <= age <= 7):
        return None
    return f

def candidate_group(r):
    f = flow_details(r)
    ratio, pos = r['volume_ratio'], r['channel_position']
    if pd.notna(pos) and (pos < 0 or pos > 1):
        return '주의', '상승채널 이탈'
    if f is None:
        return '관찰', '수급 미확인 또는 오래됨'
    if f['f5'] <= 0 or f['i5'] <= 0:
        return '관찰', '외국인·기관 동반 순매수 미충족'
    if not bool(r['channel_good']):
        return '관찰', '상승채널 조건 미충족'
    if pd.isna(ratio) or ratio < 1.2:
        return '관찰', '거래량 1.2배 미만'
    return '매수 관심', 'RS·수급·거래량·상승채널 조건 충족'

def show_candidate(r, i):
    group, reason = candidate_group(r)
    ratio, pos = r['volume_ratio'], r['channel_position']
    with st.container(border=True):
        st.markdown(f'**{i}. {r["name"]}** · `{r["ticker"]}` · **RS {int(r["sample_rs"])}**')
        st.caption(f'{group} · {reason} | 거래량 {ratio:.2f}배' if pd.notna(ratio) else f'{group} · {reason} | 거래량 미확인')
        st.caption(f'채널 위치 {pos*100:.0f}%' if pd.notna(pos) else '채널 위치 미확인')
        f = flow_details(r)
        if f is None:
            st.caption('외국인·기관 수급 미확인/오래됨')
        else:
            st.caption(f"외국인 5일 {f['f5']:+,.0f}주 · 기관 5일 {f['i5']:+,.0f}주")
            if f['f20'] is not None and f['i20'] is not None:
                st.caption(f"20일 외국인 {f['f20']:+,.0f}주 · 기관 {f['i20']:+,.0f}주")
            else:
                st.caption('20일 수급 데이터 부족')

st.subheader('매수 관심 · 조건 충족 후보')
st.caption('부분 표본 RS 90 이상, 최근 5일 외국인·기관 동반 순매수, 거래량 1.2배 이상, 상승채널 양호')
interested = filtered[filtered.apply(lambda r: candidate_group(r)[0] == '매수 관심', axis=1)]
if interested.empty:
    st.info('현재 모든 조건을 충족하는 종목이 없습니다. 기준을 임의로 완화하지 않습니다.')
else:
    for i, (_, r) in enumerate(interested.head(top_n).iterrows(), 1):
        show_candidate(r, i)

st.subheader('관찰 · RS 상위 후보')
watch = filtered[filtered.apply(lambda r: candidate_group(r)[0] == '관찰', axis=1)]
if watch.empty:
    st.caption('관찰 후보 없음')
else:
    for i, (_, r) in enumerate(watch.head(top_n).iterrows(), 1):
        show_candidate(r, i)

with st.expander('주의 · 채널 이탈 종목', expanded=False):
    caution = filtered[filtered.apply(lambda r: candidate_group(r)[0] == '주의', axis=1)]
    if caution.empty:
        st.caption('주의 후보 없음')
    else:
        for i, (_, r) in enumerate(caution.head(top_n).iterrows(), 1):
            show_candidate(r, i)
st.caption('위 분류는 투자 참고용 기술적 필터입니다. 전체시장 수집 완료 전에는 부분 표본 시험 RS이며 매수 지시가 아닙니다.')
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
