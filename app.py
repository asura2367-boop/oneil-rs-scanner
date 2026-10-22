from datetime import date, timedelta
from io import BytesIO
import zipfile
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='오닐 RS · 1년 일봉 검증', page_icon='📈', layout='centered')
st.title('📈 오닐 RS · 1년 일봉 검증')
st.caption('한국투자증권 실전투자 API · 조회 전용 · 매매 주문 기능 없음')
st.info('삼성전자·SK하이닉스·현대차 3종목의 1년 일봉을 분할 조회합니다. 전체시장 RS 등급은 아직 산출하지 않습니다.')
BASE = 'https://openapi.koreainvestment.com:9443'
STOCKS = {'삼성전자':'005930','SK하이닉스':'000660','현대차':'005380'}

@st.cache_data(ttl=20*3600, show_spinner=False)
def access_token(key, secret):
    r=requests.post(BASE+'/oauth2/tokenP', json={'grant_type':'client_credentials','appkey':key,'appsecret':secret}, timeout=20)
    r.raise_for_status()
    p=r.json()
    if not p.get('access_token'): raise RuntimeError('토큰 발급 실패: '+str(p.get('msg1','응답 확인 필요')))
    return p['access_token']

@st.cache_data(ttl=6*3600, show_spinner=False)
def daily_chunk(code, start, end, key, secret):
    token=access_token(key, secret)
    r=requests.get(BASE+'/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
        headers={'content-type':'application/json','authorization':'Bearer '+token,
                 'appkey':key,'appsecret':secret,'tr_id':'FHKST03010100'},
        params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,
                'FID_INPUT_DATE_1':start,'FID_INPUT_DATE_2':end,
                'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'1'},timeout=25)
    r.raise_for_status()
    p=r.json()
    if p.get('rt_cd')!='0': raise RuntimeError('KIS 조회 실패: '+str(p.get('msg1',p.get('msg_cd','응답 오류'))))
    d=pd.DataFrame(p.get('output2') or [])
    cols={'stck_bsop_date':'날짜','stck_clpr':'종가','acml_vol':'거래량'}
    if d.empty or not set(cols).issubset(d.columns): raise RuntimeError('일봉 데이터 없음')
    d=d[list(cols)].rename(columns=cols)
    d['날짜']=pd.to_datetime(d['날짜'],format='%Y%m%d',errors='coerce')
    for c in ['종가','거래량']: d[c]=pd.to_numeric(d[c],errors='coerce')
    return d.dropna().drop_duplicates('날짜').sort_values('날짜')

def year_history(code, key, secret, progress=None):
    end=date.today()
    begin=end-timedelta(days=410)
    parts=[]
    cursor=begin
    # 각 요청 구간을 80달력일로 제한하여 일봉 응답 상한을 피합니다.
    while cursor<=end:
        last=min(cursor+timedelta(days=79),end)
        part=daily_chunk(code,cursor.strftime('%Y%m%d'),last.strftime('%Y%m%d'),key,secret)
        parts.append(part)
        cursor=last+timedelta(days=1)
    d=pd.concat(parts,ignore_index=True).drop_duplicates('날짜').sort_values('날짜')
    d=d.reset_index(drop=True)  # 12개월(252거래일) 수익률 계산을 위해 여유분 유지
    if len(d)<200: raise RuntimeError(f'수집된 일봉이 {len(d)}개뿐입니다. 최소 200개 이상 필요합니다.')
    return d

def summary(d):
    # 거래일 기준 대략 3/6/9/12개월: 63/126/189/252거래일
    close=d['종가'].astype(float)
    values={}
    for label,n in [('3개월',63),('6개월',126),('9개월',189),('12개월',252)]:
        values[label]=round((close.iloc[-1]/close.iloc[-n-1]-1)*100,2) if len(close)>n else None
    return values

if not all(x in st.secrets for x in ['KIS_APP_KEY','KIS_APP_SECRET']):
    st.error('Streamlit Secrets에 KIS_APP_KEY, KIS_APP_SECRET이 필요합니다.')
    st.stop()

if st.button('🔎 3종목 1년치 일봉 수집',type='primary',use_container_width=True):
    key,secret=st.secrets['KIS_APP_KEY'],st.secrets['KIS_APP_SECRET']
    results={}
    for name,code in STOCKS.items():
        try:
            with st.spinner(f'{name} 과거 주가 조회 중...'):
                d=year_history(code,key,secret)
            results[name]=d
            st.success(f'{name} ({code}): {len(d)}개 일봉 · {d.날짜.min():%Y-%m-%d} ~ {d.날짜.max():%Y-%m-%d}')
            st.metric(name+' 최근 종가',f'{int(d.종가.iloc[-1]):,}원')
            st.dataframe(pd.DataFrame([summary(d)]),hide_index=True,use_container_width=True)
        except Exception as exc:
            st.error(f'{name} 조회 실패: {str(exc)[:250]}')
    if results:
        name=st.selectbox('차트 종목',list(results))
        st.line_chart(results[name].set_index('날짜')['종가'])
        b=BytesIO()
        with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
            for name,d in results.items():
                z.writestr(STOCKS[name]+'.csv',d.to_csv(index=False).encode('utf-8-sig'))
        st.download_button('수집한 일봉 CSV 다운로드',b.getvalue(),file_name='kis_1year_3stocks.zip',mime='application/zip')
        st.caption('수집 성공한 종목만 포함됩니다. 이 테스트는 서버에 자동 저장하거나 매일 갱신하지 않습니다.')
