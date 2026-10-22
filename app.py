from datetime import timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title='오닐 RS 추천 TOP 10', page_icon='📈', layout='centered')
st.title('📈 오닐 RS 추천 TOP 10')
st.caption('한국투자증권 일봉 기반 · 30종목 시험용 상대강도 · 투자 권유 아님')
URL = 'https://raw.githubusercontent.com/asura2367-boop/oneil-rs-scanner/main/data/kis_daily_30stocks.csv'
LOCAL = Path(__file__).resolve().parent / 'data' / 'kis_daily_30stocks.csv'

@st.cache_data(ttl=900, show_spinner=False)
def load_data():
    try:
        import requests
        from io import BytesIO
        response = requests.get(URL, timeout=20)
        response.raise_for_status()
        d = pd.read_csv(BytesIO(response.content), dtype={'ticker': str})
    except Exception:
        try:
            import requests
            from io import BytesIO
            response = requests.get(URL, timeout=20)
            response.raise_for_status()
            d = pd.read_csv(BytesIO(response.content), dtype={'ticker': str})
        except Exception as exc:
            if not LOCAL.exists():
                raise RuntimeError(f'GitHub CSV 다운로드 실패: {exc}') from exc
            d = pd.read_csv(LOCAL, dtype={'ticker': str})
    required = {'ticker','name','date','close','volume'}
    if not required.issubset(d.columns):
        raise ValueError('CSV 열 누락: ' + ', '.join(sorted(required - set(d.columns))))
    d['ticker'] = d['ticker'].astype(str).str.zfill(6)
    d['date'] = pd.to_datetime(d['date'], errors='coerce')
    for col in ('close','volume'):
        d[col] = pd.to_numeric(d[col], errors='coerce')
    d = d.dropna(subset=['date','close','ticker']).query('close > 0')
    return d.sort_values(['ticker','date']).drop_duplicates(['ticker','date'], keep='last')

def channel(d):
    xdf = d.tail(60)
    if len(xdf) < 40: return None
    y = np.log(xdf['close'].to_numpy(dtype=float))
    x = np.arange(len(y))
    slope, intercept = np.polyfit(x, y, 1)
    mid = intercept + slope*x
    width = max(2*np.std(y-mid), 1e-6)
    pos = float((y[-1]-mid[-1]+width)/(2*width))
    return {'position':pos,'rising':slope>0,'dates':xdf.date,
            'upper':np.exp(mid+width),'mid':np.exp(mid),'lower':np.exp(mid-width)}

def score(d):
    rows=[]
    for ticker,g in d.groupby('ticker'):
        g=g.sort_values('date')
        end=g.date.iloc[-1]
        last=float(g.close.iloc[-1])
        ret=[]
        ok=True
        for months in (3,6,9,12):
            target=end-pd.DateOffset(months=months)
            prior=g[g.date<=target]
            if prior.empty or (target-prior.date.iloc[-1]).days>10:
                ok=False;break
            ret.append(last/float(prior.close.iloc[-1])-1)
        if not ok:continue
        vbase=g.volume.iloc[-21:-1].mean() if len(g)>=21 else np.nan
        vr=float(g.volume.iloc[-1]/vbase) if pd.notna(vbase) and vbase>0 else np.nan
        ch=channel(g)
        rows.append({'ticker':ticker,'name':str(g.name.iloc[-1]),'date':end,'close':last,
                     'weighted':float(np.dot(ret,[.4,.2,.2,.2])),
                     'volume_ratio':vr,'channel_pos':ch['position'] if ch else np.nan,
                     'channel_good':bool(ch and ch['rising'] and 0<=ch['position']<=.65),
                     'returns':ret})
    out=pd.DataFrame(rows)
    if out.empty:return out
    out['RS_시험용']=np.ceil(out.weighted.rank(pct=True)*99).clip(1,99).astype(int)
    out['보조점수']=(out.volume_ratio>=1.2).astype(int)+out.channel_good.astype(int)
    return out.sort_values(['RS_시험용','보조점수','weighted'],ascending=False)

if st.button('🔄 저장된 데이터 새로 불러오기'):
    load_data.clear()

try:
    with st.spinner('저장된 데이터 불러오는 중...'):
        raw=load_data()
        ranked=score(raw)
except Exception as e:
    st.error(str(e))
    st.stop()

st.info('현재 30종목 표본에서 계산한 **시험용 RS**입니다. 코스피·코스닥 전체시장 RS 90 이상 추천과 다릅니다. 외국인·기관 수급은 데이터가 없어 순위에 반영하지 않습니다.')
st.caption(f'저장된 종목 {raw.ticker.nunique()}개 · RS 계산 가능 {len(ranked)}개 · 최종 데이터 {raw.date.max():%Y-%m-%d}')
if ranked.empty:
    st.warning('12개월 수익률 계산에 충분한 데이터가 없습니다. 수집 기간을 확인해 주세요.')
    st.stop()

min_rs=st.slider('시험용 최소 RS', 1, 99, 90)
filtered=ranked[ranked['RS_시험용']>=min_rs]
st.subheader('🏆 추천 후보 TOP 10')
if filtered.empty: st.warning('현재 기준을 충족하는 종목이 없습니다.')
for _,r in filtered.head(10).iterrows():
    with st.container(border=True):
        a,b=st.columns([3,1])
        a.markdown(f"**{r['name']}** · {r['ticker']}")
        b.metric('시험 RS',int(r['RS_시험용']))
        vr=f"{r['volume_ratio']:.2f}배" if pd.notna(r['volume_ratio']) else '미확인'
        cp=f"{r['channel_pos']*100:.0f}%" if pd.notna(r['channel_pos']) else '미확인'
        st.caption(f"가중 수익률 {r['weighted']*100:+.1f}% · 거래량 {vr} · 채널 위치 {cp} · 상승채널 양호 {'예' if r['channel_good'] else '아니오'}")

st.subheader('📊 종목별 차트')
options={f"{r['name']} ({r['ticker']})":r['ticker'] for _,r in ranked.iterrows()}
chosen=st.selectbox('종목 선택',list(options))
g=raw[raw.ticker==options[chosen]].sort_values('date')
fig=go.Figure()
fig.add_trace(go.Scatter(x=g.date,y=g.close,name='종가',mode='lines'))
for n in (20,60):
    fig.add_trace(go.Scatter(x=g.date,y=g.close.rolling(n).mean(),name=f'{n}일선'))
ch=channel(g)
if ch:
    for key,label in [('upper','채널 상단'),('mid','채널 중심'),('lower','채널 하단')]:
        fig.add_trace(go.Scatter(x=ch['dates'],y=ch[key],name=label,line={'dash':'dot'}))
fig.update_layout(height=410,margin={'l':5,'r':5,'t':20,'b':5},legend={'orientation':'h','y':-0.25})
st.plotly_chart(fig,use_container_width=True)
st.download_button('📥 분석 결과 CSV',ranked.drop(columns=['returns']).to_csv(index=False).encode('utf-8-sig'),file_name='oneil_rs_sample.csv',mime='text/csv')
st.caption('상승채널은 최근 60거래일 로그종가 회귀 기반 추정치입니다. 장중 실시간 시세가 아닌 저장된 일봉을 사용합니다.')
