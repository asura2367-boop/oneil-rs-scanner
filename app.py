from datetime import date, timedelta
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pykrx import stock

st.set_page_config(page_title='오닐 RS 스캐너', layout='centered', initial_sidebar_state='collapsed')
st.title('📈 오닐 RS 종목추천 스캐너')
st.caption('코스피·코스닥 | 일봉 기준 · 실시간 아님 · IBD 공식 RS가 아닌 근사치')

@st.cache_data(ttl=3600)
def universe(day):
    rows=[]
    for market in ['KOSPI','KOSDAQ']:
        tickers=stock.get_market_ticker_list(day,market=market)
        rows += [(str(t).zfill(6),stock.get_market_ticker_name(t),market) for t in tickers]
    if not rows: raise RuntimeError('코스피·코스닥 종목 목록을 조회하지 못했습니다. 거래일과 데이터 제공 상태를 확인하세요.')
    return pd.DataFrame(rows,columns=['ticker','종목명','시장']).drop_duplicates('ticker').set_index('ticker')

@st.cache_data(ttl=3600)
def returns(start,end):
    d=stock.get_market_price_change_by_ticker(start,end,market='ALL')
    if d.empty: raise RuntimeError('기간별 가격 데이터 없음')
    s=pd.to_numeric(d['등락률'],errors='coerce')/100
    s.index=s.index.astype(str).str.zfill(6)
    return s

@st.cache_data(ttl=3600)
def flows(start,end,who):
    d=stock.get_market_net_purchases_of_equities_by_ticker(start,end,'ALL',who)
    if d.empty: raise RuntimeError(f'{who} 수급 데이터 없음')
    s=pd.to_numeric(d['순매수거래대금'],errors='coerce')/1e8
    s.index=s.index.astype(str).str.zfill(6)
    return s

@st.cache_data(ttl=3600)
def history(ticker,start,end):
    d=stock.get_market_ohlcv_by_date(start,end,ticker,adjusted=True)
    if d.empty: return d
    for c in ['시가','고가','저가','종가','거래량']:
        d[c]=pd.to_numeric(d[c],errors='coerce')
    return d.sort_index().dropna(subset=['종가'])

def channel(d):
    d=d.tail(60)
    if len(d)<40 or (d['종가']<=0).any(): return None
    x=np.arange(len(d)); y=np.log(d['종가'].to_numpy(float))
    slope,intercept=np.polyfit(x,y,1)
    mid=intercept+slope*x; width=max(2*np.std(y-mid),1e-6)
    return {'slope':slope,'position':(y[-1]-mid[-1]+width)/(2*width),'dates':d.index,'upper':np.exp(mid+width),'mid':np.exp(mid),'lower':np.exp(mid-width)}

with st.sidebar:
    threshold=st.slider('최소 RS',70,99,90)
    prioritize=st.checkbox('외국인·기관 수급 우선',True)
    volume_on=st.checkbox('거래량 증가 확인',True)
    channel_on=st.checkbox('상승채널 위치 분석',True)
    limit=st.slider('차트 후보 표시 수',10,100,30,10)
    day=st.date_input('기준일',date.today()-timedelta(days=1),max_value=date.today())
    run=st.button('🔎 실제 데이터 분석',type='primary')

st.info('RS = 3·6·9·12개월 수익률 가중치 40/20/20/20 → 전체시장 백분위(1~99). KRX 원천 데이터 연결이 실패하면 결과를 만들어내지 않습니다.')
if not run: st.stop()
end=day.strftime('%Y%m%d')
try:
    with st.spinner('전체시장 RS 계산 중...'):
        u=universe(end)
        periods=[63,126,189,252]
        r=pd.concat([returns((day-timedelta(days=round(n*365/252)+8)).strftime('%Y%m%d'),end) for n in periods],axis=1)
        r.columns=['3개월','6개월','9개월','12개월']
        r=r.join(u,how='inner').dropna()
        if len(r)<100: raise RuntimeError('RS 계산에 필요한 종목 데이터 부족')
        r['가중수익률']=r[['3개월','6개월','9개월','12개월']].dot([.4,.2,.2,.2])
        r['RS']=np.ceil(r['가중수익률'].rank(pct=True)*99).clip(1,99).astype(int)
        selected=r[r.RS>=threshold].copy()
        if selected.empty: raise RuntimeError('설정한 RS 기준에 맞는 종목이 없습니다.')
        start=(day-timedelta(days=12)).strftime('%Y%m%d')
        selected['외국인_억원']=flows(start,end,'외국인').reindex(selected.index)
        selected['기관_억원']=flows(start,end,'기관합계').reindex(selected.index)
        selected['수급점수']=(selected['외국인_억원']>0).astype(int)+(selected['기관_억원']>0).astype(int)
        start_hist=(day-timedelta(days=150)).strftime('%Y%m%d')
        metrics=[]; progress=st.progress(0)
        for i,ticker in enumerate(selected.index):
            try:
                h=history(ticker,start_hist,end)
                c=channel(h)
                base=h['거래량'].iloc[-21:-1].mean() if len(h)>=21 else np.nan
                ratio=h['거래량'].iloc[-1]/base if base>0 else np.nan
                pos=c['position'] if c else np.nan
                good=bool(c and c['slope']>0 and 0<=pos<=.65)
            except Exception: ratio,pos,good=np.nan,np.nan,False
            metrics.append((ticker,ratio,pos,good))
            progress.progress((i+1)/len(selected))
        progress.empty()
        m=pd.DataFrame(metrics,columns=['ticker','거래량배율','채널위치','채널양호']).set_index('ticker')
        selected=selected.join(m)
        selected['보조점수']=0
        if prioritize: selected['보조점수']+=selected['수급점수']*2
        if volume_on: selected['보조점수']+=(selected['거래량배율']>=1.2).astype(int)
        if channel_on: selected['보조점수']+=selected['채널양호'].astype(int)
        selected=selected.sort_values(['RS','보조점수','가중수익률'],ascending=False)
except Exception as e:
    st.error(f'실제 데이터 조회 실패: {e}')
    st.warning('데이터 제공 방식 변경 또는 서버 접근 제한일 수 있습니다. 기준일 변경으로 해결되지 않는 API 오류는 코드 수정이 필요합니다.')
    st.stop()

st.success(f'{day} 기준 전체 {len(r):,}개 종목 중 RS {threshold}+ : {len(selected):,}개')
a,b,c=st.columns(3)
a.metric('RS 90 이상',int((r.RS>=90).sum()))
b.metric('외국인·기관 동반 순매수',int((selected['수급점수']==2).sum()))
c.metric('상승채널 양호',int(selected['채널양호'].sum()))
st.subheader('📱 추천 TOP 10')
for ticker,row in selected.head(10).iterrows():
    with st.container(border=True):
        left,right=st.columns([3,1])
        left.markdown(f"**{row['종목명']}** · {ticker} · {row['시장']}")
        right.metric('RS',int(row['RS']))
        vr=row['거래량배율']
        vol=f'{vr:.2f}배' if pd.notna(vr) else '미확인'
        pos=row['채널위치']
        position=f'{pos*100:.0f}%' if pd.notna(pos) else '미확인'
        st.caption(f"외국인·기관 순매수: {int(row['수급점수'])}/2 | 거래량: {vol} | 채널: {position}")
st.subheader('전체 후보 순위')
display=selected.head(limit).copy()
display['채널위치_%']=(display['채널위치']*100).round(1)
st.dataframe(display[['종목명','시장','RS','외국인_억원','기관_억원','거래량배율','채널위치_%','채널양호']].round(2),use_container_width=True)
st.download_button('전체 후보 CSV 다운로드',selected.reset_index().to_csv(index=False).encode('utf-8-sig'),file_name=f'rs_{end}.csv')
st.subheader('개별 종목 차트')
options={f'{row["종목명"]} ({ticker})':ticker for ticker,row in display.iterrows()}
ticker=options[st.selectbox('종목 선택',list(options))]
h=history(ticker,(day-timedelta(days=220)).strftime('%Y%m%d'),end)
if not h.empty:
    fig=go.Figure(go.Candlestick(x=h.index,open=h['시가'],high=h['고가'],low=h['저가'],close=h['종가'],name='일봉'))
    for n in [20,60]: fig.add_trace(go.Scatter(x=h.index,y=h['종가'].rolling(n).mean(),name=f'{n}일선'))
    ch=channel(h)
    if ch:
        for key,label in [('upper','채널상단'),('mid','채널중심'),('lower','채널하단')]:
            fig.add_trace(go.Scatter(x=ch['dates'],y=ch[key],name=label,line=dict(dash='dot')))
    fig.update_layout(height=390,xaxis_rangeslider_visible=False,margin=dict(l=5,r=5,t=20,b=5),legend=dict(orientation='h',y=-0.2))
    st.plotly_chart(fig,use_container_width=True)
st.caption('주의: 수익률 기간은 달력일 근사, 수급은 약 12달력일 합산(정확히 5거래일은 아님). 회귀채널은 통계적 추정이며 수익을 보장하지 않습니다. API의 종목·수급 조회 가능 여부는 배포 환경에서 별도 확인이 필요합니다.')
