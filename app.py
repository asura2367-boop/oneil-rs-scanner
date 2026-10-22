from pathlib import Path
from datetime import date,timedelta
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title='오닐 RS 스캐너',layout='centered',initial_sidebar_state='collapsed')
st.title('📈 오닐 RS 종목추천')
st.caption('코스피·코스닥 · 일봉 · IBD 공식 RS가 아닌 근사치 · 투자 판단 참고용')
DATA=Path(__file__).parent/'data'/'rs_latest.csv'
with st.sidebar:
    threshold=st.slider('최소 RS',70,99,90)
    flow_priority=st.checkbox('외국인·기관 수급 우선',True)
    volume=st.checkbox('거래량 증가 확인 (상세 차트)',True)
    channel_on=st.checkbox('상승채널 위치 분석 (상세 차트)',True)
    top=st.slider('표시 종목 수',5,30,10)
    if st.button('🔄 화면 새로고침'): st.rerun()
if not DATA.exists():
    st.warning('아직 RS 분석 데이터가 없습니다. GitHub Actions → Update RS data → Run workflow를 먼저 실행해 주세요. 데이터 수집이 실패하면 결과를 임의 생성하지 않습니다.')
    st.stop()
try:
    df=pd.read_csv(DATA,dtype={'ticker':str},encoding='utf-8-sig')
    needed={'ticker','종목명','시장','RS','가중수익률','기준일','외국인_억원','기관_억원'}
    if not needed.issubset(df): raise ValueError('저장된 데이터 형식 오류')
except Exception as exc:
    st.error(f'저장 데이터 읽기 실패: {exc}')
    st.stop()
if df.empty:
    st.error('저장된 데이터가 비어 있습니다.')
    st.stop()
day=str(df['기준일'].iloc[0])
st.success(f'분석 기준일: {day} · 전체 {len(df):,}개 종목')
try:
    age=(date.today()-date.fromisoformat(day)).days
    if age>4: st.warning(f'저장 데이터가 {age}일 전 자료입니다. 최신 데이터 수집이 실패했을 수 있습니다.')
except ValueError: pass
selected=df[df['RS']>=threshold].copy()
if flow_priority:
    selected['수급점수']=((selected['외국인_억원']>0).astype(int)+(selected['기관_억원']>0).astype(int))
    selected=selected.sort_values(['RS','수급점수','가중수익률'],ascending=False)
else: selected=selected.sort_values(['RS','가중수익률'],ascending=False)
st.metric('RS 기준 충족',len(selected))
st.caption('수급은 약 12달력일 누적이며 미확인 값은 0으로 대체하지 않습니다. 거래량·채널은 전체 순위에 반영되지 않고 선택 종목 상세에서만 확인합니다.')
if selected.empty:
    st.info('해당 조건의 종목이 없습니다.')
    st.stop()
st.subheader('🏅 RS 추천 TOP')
for _,r in selected.head(top).iterrows():
    with st.container(border=True):
        a,b=st.columns([4,1])
        a.markdown(f"**{r['종목명']}** · {r['ticker']} · {r['시장']}")
        b.metric('RS',int(r['RS']))
        def fmt(v): return f'{v:+,.1f}억' if pd.notna(v) else '미확인'
        st.caption(f"외국인 {fmt(r['외국인_억원'])} | 기관 {fmt(r['기관_억원'])}")
st.download_button('전체 후보 CSV',selected.to_csv(index=False).encode('utf-8-sig'),file_name=f'oneil_rs_{day}.csv')
st.subheader('📉 선택 종목 상세')
opts={f"{r['종목명']} ({r['ticker']})":r['ticker'] for _,r in selected.head(100).iterrows()}
choice=st.selectbox('종목',list(opts))
if st.button('선택 종목 차트 조회'):
    ticker=opts[choice]
    try:
        import FinanceDataReader as fdr
        with st.spinner('선택한 종목만 조회 중...'):
            h=fdr.DataReader(ticker,(date.today()-timedelta(days=180)).isoformat())
        if h is None or len(h)<40: raise RuntimeError('차트에 필요한 가격 자료 부족')
        h=h.sort_index().tail(100)
        fig=go.Figure(go.Candlestick(x=h.index,open=h.Open,high=h.High,low=h.Low,close=h.Close,name='일봉'))
        for n in (20,60): fig.add_trace(go.Scatter(x=h.index,y=h.Close.rolling(n).mean(),name=f'{n}일선'))
        if volume:
            base=h.Volume.iloc[-21:-1].mean() if len(h)>=21 else np.nan
            st.write('최근 거래량 / 직전 20일 평균:',f'{h.Volume.iloc[-1]/base:.2f}배' if pd.notna(base) and base>0 else '미확인')
        if channel_on:
            d=h.tail(60)
            x=np.arange(len(d)); y=np.log(d.Close.to_numpy(float))
            slope,intercept=np.polyfit(x,y,1)
            mid=intercept+slope*x
            width=max(2*np.std(y-mid),1e-6)
            pos=(y[-1]-mid[-1]+width)/(2*width)
            for offset,label in ((width,'상단'),(0,'중심'),(-width,'하단')):
                fig.add_trace(go.Scatter(x=d.index,y=np.exp(mid+offset),name='채널 '+label,line=dict(dash='dot')))
            st.write('상승채널:', '상승' if slope>0 else '하락/횡보','· 위치:',f'{pos*100:.0f}% (0%=하단, 100%=상단)')
        fig.update_layout(height=420,xaxis_rangeslider_visible=False,margin=dict(l=0,r=0,t=15,b=0))
        st.plotly_chart(fig,use_container_width=True)
    except Exception as exc:
        st.error(f'차트 데이터 조회 실패: {exc}')
st.caption('데이터 공급처가 차단되면 갱신 및 상세 차트가 실패할 수 있습니다. 저장된 RS 순위는 마지막 정상 수집일 기준입니다.')
