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
        recent_volumes = pd.to_numeric(h['volume'].tail(20), errors='coerce')
        zero_days_20 = int((recent_volumes == 0).sum())
        invalid_days_20 = int((recent_volumes.isna() | (recent_volumes < 0)).sum())
        # Never substitute historical volume for a zero/missing latest observation.
        volume_issue = ('최신 거래량 0' if pd.notna(recent) and recent == 0 else
                        '최신 거래량 누락/음수' if pd.isna(recent) or recent < 0 else
                        '최근 20일 거래량 0 반복' if zero_days_20 >= 3 else
                        '최근 20일 거래량 누락/음수' if invalid_days_20 else '')
        vr = (float(recent/base) if not volume_issue and pd.notna(base) and base > 0
              else np.nan)
        if not volume_issue and (pd.isna(base) or base <= 0):
            volume_issue = '20일 평균 거래량 계산 불가'
        y = np.log(p[-60:]); x = np.arange(60)
        slope, intercept = np.polyfit(x,y,1)
        mid = intercept + slope*x
        width = max(2*np.std(y-mid),1e-8)
        position = float((y[-1]-mid[-1]+width)/(2*width))
        channel_good = bool(slope>0 and 0 <= position <= .65)
        rows.append({'ticker':ticker,'name':str(h['name'].iloc[-1]),'date':h['date'].iloc[-1],
                     'weighted_return':score,'volume_ratio':vr,'volume_issue':volume_issue,
                     'zero_days_20':zero_days_20,'channel_position':position,
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
st.title('📈 오닐 RS 스캐너 · 모의투자')
st.caption('한국투자증권 일봉 · 저장 데이터 · 실시간 아님')

# Read-only paper portfolio panel. No brokerage order endpoints are used.
@st.cache_data(ttl=300, show_spinner=False)
def load_paper_state():
    import requests
    url = RAW + 'paper/state.json'
    try:
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        obj = response.json()
        if not isinstance(obj, dict):
            raise ValueError('state.json 형식 오류')
        return obj, 'GitHub 최신 파일'
    except Exception:
        local = ROOT / 'data' / 'paper' / 'state.json'
        if local.exists():
            try:
                return json.loads(local.read_text(encoding='utf-8-sig')), '배포 시점 파일 (최신 아닐 수 있음)'
            except Exception:
                pass
        return None, '조회 실패'

def paper_number(value, default=0.0):
    try:
        n = float(value)
        return n if np.isfinite(n) else default
    except (TypeError, ValueError):
        return default

def paper_positions(state):
    raw = state.get('positions', {})
    if isinstance(raw, dict):
        return [(str(code).zfill(6), pos) for code, pos in raw.items() if isinstance(pos, dict)]
    if isinstance(raw, list):
        return [(str(pos.get('ticker', pos.get('code', ''))).zfill(6), pos)
                for pos in raw if isinstance(pos, dict)]
    return []

def show_paper_account():
    with st.expander('💰 모의투자 계좌 · 조회 전용', expanded=True):
        st.caption('실제 주문 없음 · GitHub에 저장된 가상 계좌를 표시합니다.')
        state, state_source = load_paper_state()
        if state is None:
            st.info('모의 계좌 파일을 불러오지 못했습니다. data/paper/state.json 저장 여부를 확인하세요.')
            return
        initial = paper_number(state.get('initial_cash'), 10_000_000)
        cash = paper_number(state.get('cash'), initial)
        positions = paper_positions(state)
        st.caption(f"모드: {state.get('mode', '미확인')} · 마지막 처리: {state.get('last_processed', '미확인')} · {state_source}")
        # Historical equity is shown only when recorded; no unverified real-time prices.
        equity, eq_source = load_csv('paper/equity.csv')
        trades, tr_source = load_csv('paper/trades.csv')
        total = None
        date_col = next((c for c in ('date','session','as_of') if equity is not None and c in equity.columns), None)
        equity_col = next((c for c in ('equity','total_equity','total_assets','portfolio_value','nav') if equity is not None and c in equity.columns), None)
        if equity is not None and not equity.empty and equity_col:
            history = equity.copy()
            history[equity_col] = pd.to_numeric(history[equity_col], errors='coerce')
            if date_col:
                history[date_col] = pd.to_datetime(history[date_col], errors='coerce')
                history = history.sort_values(date_col)
            history = history.dropna(subset=[equity_col])
            if not history.empty:
                total = float(history[equity_col].iloc[-1])
        if total is None and not positions:
            total = cash
        a, b = st.columns(2)
        a.metric('가상 현금', f'{cash:,.0f}원')
        b.metric('기록된 총자산', f'{total:,.0f}원' if total is not None else '미확인')
        c, d = st.columns(2)
        c.metric('보유 종목', f'{len(positions)}개')
        d.metric('누적 수익률', f'{(total / initial - 1) * 100:+.2f}%' if total is not None and initial > 0 else '미확인')
        st.caption(f'초기 가상자금 {initial:,.0f}원 · 대기 주문 {len(state.get("pending", [])) if isinstance(state.get("pending"), list) else "미확인"}건')
        if positions:
            pos_rows = []
            for code, pos in positions:
                pos_rows.append({'종목코드':code,
                                 '종목명':pos.get('name', ''),
                                 '보유 수량':pos.get('qty', pos.get('shares', pos.get('quantity', '미확인'))),
                                 '매수가':pos.get('entry_price', pos.get('avg_price', pos.get('buy_price', '미확인'))),
                                 '매수일':pos.get('entry_date', pos.get('buy_date', '미확인'))})
            st.markdown('**가상 보유 종목**')
            st.dataframe(pd.DataFrame(pos_rows), hide_index=True, use_container_width=True)
            st.caption('현재가·평가손익은 기록된 총자산과 다를 수 있으므로 미검증 시 표시하지 않습니다.')
        else:
            st.info('현재 가상 보유 종목이 없습니다. 매수 조건을 충족할 때까지 현금을 유지합니다.')
        if equity is not None and not equity.empty:
            st.markdown('**가상 자산 변화**')
            if date_col and equity_col:
                chart = equity.copy()
                chart[date_col] = pd.to_datetime(chart[date_col], errors='coerce')
                chart[equity_col] = pd.to_numeric(chart[equity_col], errors='coerce')
                chart = chart.dropna(subset=[date_col,equity_col]).sort_values(date_col)
                if not chart.empty:
                    st.line_chart(chart.set_index(date_col)[equity_col])
                else:
                    st.caption('차트로 표시할 수 있는 자산 기록이 없습니다.')
            else:
                st.caption('자산 기록은 있지만 날짜·총자산 열 이름을 확인해야 합니다.')
            with st.expander('가상 자산 원본 기록'):
                st.dataframe(equity.tail(30), hide_index=True, use_container_width=True)
        if trades is not None and not trades.empty:
            st.markdown('**모의 매매 내역**')
            st.dataframe(trades.tail(30).iloc[::-1], hide_index=True, use_container_width=True)
        else:
            st.caption('아직 기록된 모의 매수·매도가 없습니다.')
        st.caption(f'자산 데이터: {eq_source} · 매매 내역: {tr_source} · 최대 5분 캐시. 이 화면은 거래를 실행하지 않습니다.')

show_paper_account()

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
        load_csv.clear(); load_status.clear(); load_paper_state.clear(); st.rerun()
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

# V11: Compare against observed market sessions, not calendar days.
# The dataset's union of dates is a proxy for the trading calendar; holidays
# are excluded if no instrument in the collected sample traded on that day.
def session_lag(date_value, market_sessions):
    if pd.isna(date_value) or len(market_sessions) == 0:
        return None
    day = pd.Timestamp(date_value).normalize()
    return int(len(market_sessions) - market_sessions.searchsorted(day, side='right'))

price_dates = pd.to_datetime(df['date'], errors='coerce').dropna().dt.normalize()
market_sessions = pd.DatetimeIndex(sorted(price_dates.unique()))
price_last = (df.assign(ticker=df['ticker'].astype(str).str.strip().str.zfill(6),
                        date=pd.to_datetime(df['date'], errors='coerce'))
                .dropna(subset=['date']).groupby('ticker')['date'].max())
price_lags = {code: session_lag(day, market_sessions) for code, day in price_last.items()}
flow_last = flow.groupby('ticker')['date'].max() if not flow.empty else pd.Series(dtype='datetime64[ns]')
flow_lags = {code: session_lag(day, market_sessions) for code, day in flow_last.items()}

def flow_status(code):
    lag = flow_lags.get(code)
    f = flow_summary.get(code)
    if f is None or f['f5'] is None or f['i5'] is None:
        return '미확인'
    if lag is None or lag < 0:
        return '날짜 확인 필요'
    if lag == 0:
        return '당일 기준'
    if lag == 1:
        return '전 거래일 기준'
    if lag == 2:
        return '2거래일 지연'
    return '3거래일 이상 지연'


ranked['price_lag'] = ranked['ticker'].map(price_lags)
ranked_all = ranked.copy()
# A lag of 3 means three observed market sessions occurred after the stock's last bar.
ranked = ranked[ranked['price_lag'].notna() & (ranked['price_lag'] < 3)].copy()

with st.expander('🕒 데이터 최신성 관리', expanded=True):
    st.caption(f'기준 거래일: {market_sessions.max().date()} · 저장된 시장 거래일을 기준으로 계산 (공식 휴장일 캘린더 아님)')
    a,b,c = st.columns(3)
    a.metric('일봉 최신', sum(v == 0 for v in price_lags.values()))
    b.metric('수급 당일', sum(flow_status(code) == '당일 기준' for code in flow_summary))
    c.metric('일봉 3일+ 지연', sum(v is not None and v >= 3 for v in price_lags.values()))
    st.caption(f'일봉 1~2거래일 지연: {sum(v is not None and 1 <= v < 3 for v in price_lags.values()):,}종목 · 수급 전 거래일: {sum(flow_status(code) == "전 거래일 기준" for code in flow_summary):,}종목 · 수급 2거래일 지연: {sum(flow_status(code) == "2거래일 지연" for code in flow_summary):,}종목 · 수급 3거래일+ 지연: {sum(flow_status(code) == "3거래일 이상 지연" for code in flow_summary):,}종목')
    st.caption(f'RS 계산 가능 {len(ranked_all):,}종목 중 일봉 지연으로 추천 제외 {len(ranked_all)-len(ranked):,}종목')
    delayed = ranked_all[ranked_all['price_lag'] >= 3]
    if not delayed.empty:
        st.dataframe(delayed[['ticker','name','date','price_lag']].rename(columns={'ticker':'종목코드','name':'종목명','date':'일봉 최신일','price_lag':'지연 거래일'}).head(30), hide_index=True, use_container_width=True)
    st.caption('전 거래일 수급은 추천 분석에 사용합니다. 2거래일 지연 수급은 관찰용이며 매수 관심에서는 제외합니다. 3거래일 이상 지연은 추천 수급 평가에 사용하지 않습니다. 실제 거래정지·휴장 여부는 별도 확인이 필요합니다.')
if ranked.empty:
    st.warning('최신성 조건을 충족하는 RS 종목이 없습니다. 데이터 수집 상태를 확인하세요.')
    st.stop()


# V7: unified collection overview; all counts are computed from loaded files.
with st.expander('📊 일봉·수급 통합 수집 현황', expanded=True):
    daily_codes = set(df['ticker'].astype(str).str.strip().str.zfill(6))
    flow_codes = set(flow['ticker'].astype(str).str.strip().str.zfill(6)) if not flow.empty else set()
    common_codes = daily_codes & flow_codes
    universe_total = int(status.get('universe_total', 0)) if status else 0
    a, b, c = st.columns(3)
    a.metric('일봉 종목', f'{len(daily_codes):,}')
    b.metric('수급 종목', f'{len(flow_codes):,}')
    c.metric('둘 다 확보', f'{len(common_codes):,}')
    if universe_total:
        st.progress(min(1.0, len(daily_codes) / universe_total))
        st.caption(f'일봉 확보 {len(daily_codes):,}/{universe_total:,}종목 ({len(daily_codes)/universe_total*100:.2f}%) · 수급 확보 {len(flow_codes):,}/{universe_total:,}종목 ({len(flow_codes)/universe_total*100:.2f}%)')
    st.caption(f"일봉 최신일 {pd.to_datetime(df['date'], errors='coerce').max().date()} · 수급 최신일 {flow['date'].max().date() if not flow.empty else '미확인'}")
    st.caption(f'일봉 출처: {source} · 수급 출처: {flow_source} · 수집 상태: {status_source}')
    st.caption('수집 완료는 파일에 1개 이상 유효한 날짜가 있는 종목 수입니다. RS 계산 가능(253거래일 이상)과 다릅니다.')
    rs_codes = set(ranked.loc[ranked['sample_rs'] >= threshold, 'ticker'])
    missing_flow = ranked[(ranked['sample_rs'] >= threshold) & (~ranked['ticker'].isin(flow_codes))]
    st.write(f'RS {threshold} 이상 {len(rs_codes):,}종목 · 수급 파일에 없는 종목 {len(missing_flow):,}개')
    if not missing_flow.empty:
        st.dataframe(missing_flow[['ticker', 'name', 'sample_rs']].rename(columns={'ticker':'종목코드','name':'종목명','sample_rs':'시험 RS'}), hide_index=True, use_container_width=True)

# RS >= threshold is the eligibility gate; within eligible stocks, prefer
# recent verified foreign/institution accumulation before technical tie-breakers.
latest_by_ticker = ranked.set_index('ticker')['date'].to_dict()
def flow_rank(code):
    f = flow_summary.get(code)
    if not f or f['f5'] is None or f['i5'] is None:
        return (-1, -1, -1)
    age = flow_lags.get(code)
    if age is None or age < 0 or age >= 2:
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
        st.caption('추천 수급 우선순위에는 당일 또는 전 거래일의 5거래일 수급만 사용합니다. 2거래일 지연 자료는 관찰용으로만 표시합니다. 전체시장 RS가 아닌 부분 표본 RS입니다.')

filtered = ranked[ranked['sample_rs'] >= threshold].copy()
if only_good: filtered = filtered[filtered['channel_good']]
if vol_check: filtered = filtered[filtered['volume_ratio'] >= 1.2]

def flow_details(r):
    f = flow_summary.get(r['ticker'])
    if not f or f['f5'] is None or f['i5'] is None:
        return None
    age = flow_lags.get(r['ticker'])
    if age is None or not (0 <= age < 3):
        return None
    return f

def candidate_group(r):
    f = flow_details(r)
    ratio, pos = r['volume_ratio'], r['channel_position']
    if r['volume_issue']:
        return '주의', '거래량 확인 필요 · ' + str(r['volume_issue'])
    if pd.notna(pos) and (pos < 0 or pos > 1):
        return '주의', '상승채널 이탈'
    if f is None:
        return '관찰', '수급 미확인 또는 오래됨'
    if flow_lags.get(r['ticker']) == 2:
        return '관찰', '수급 2거래일 지연 · 매수 관심 제외'
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
        st.caption(f'{group} · {reason} | 거래량 {ratio:.2f}배' if pd.notna(ratio) else f'{group} · {reason} | 거래량 평가 제외')
        if r['volume_issue']:
            st.warning(f'거래량 확인 필요: {r["volume_issue"]} · 최근 20일 거래량 0인 날 {int(r["zero_days_20"])}일. 실제 거래정지 여부는 별도 확인 필요')
        st.caption(f'채널 위치 {pos*100:.0f}%' if pd.notna(pos) else '채널 위치 미확인')
        f = flow_details(r)
        if f is None:
            st.caption(f'수급 {flow_status(r["ticker"])} · 분석 가능한 최신 수급 없음')
        else:
            st.caption(f"수급 {flow_status(r['ticker'])} ({f['date'].date()}) · 외국인 5일 {f['f5']:+,.0f}주 · 기관 5일 {f['i5']:+,.0f}주")
            if f['f20'] is not None and f['i20'] is not None:
                st.caption(f"20일 외국인 {f['f20']:+,.0f}주 · 기관 {f['i20']:+,.0f}주")
            else:
                st.caption('20일 수급 데이터 부족')

with st.expander('🔎 거래량 데이터 품질 점검', expanded=False):
    bad = ranked[ranked['volume_issue'].astype(str).ne('')]
    st.write(f'거래량 확인 필요: {len(bad):,}종목 / RS 계산 가능 {len(ranked):,}종목')
    if not bad.empty:
        st.dataframe(bad[['ticker','name','date','volume_issue','zero_days_20']].rename(
            columns={'ticker':'종목코드','name':'종목명','date':'일봉 최신일',
                     'volume_issue':'문제','zero_days_20':'최근 20일 거래량 0일 수'}),
            hide_index=True, use_container_width=True)
    st.caption('거래량 0이 반드시 거래정지를 뜻하지는 않습니다. 데이터 제공 오류와 실제 거래정지는 별도 확인해야 합니다.')

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

with st.expander('주의 · 채널 이탈/거래량 확인 필요', expanded=False):
    caution = filtered[filtered.apply(lambda r: candidate_group(r)[0] == '주의', axis=1)]
    if caution.empty:
        st.caption('주의 후보 없음')
    else:
        for i, (_, r) in enumerate(caution.head(top_n).iterrows(), 1):
            show_candidate(r, i)
st.caption('일봉이 3거래일 이상 지연된 종목은 추천에서 제외하고, 수급 2거래일 지연 종목은 매수 관심에서 제외합니다. 위 분류는 투자 참고용 기술적 필터입니다. 전체시장 수집 완료 전에는 부분 표본 시험 RS이며 매수 지시가 아닙니다.')
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
