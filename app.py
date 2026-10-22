from datetime import date, timedelta
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='오닐 RS · KIS 일봉 연결', page_icon='📈', layout='centered')
st.title('📈 오닐 RS · KIS 일봉 연결 테스트')
st.caption('실전투자 API · 삼성전자 / SK하이닉스 / 현대차 · 주문 기능 없음')
st.info('이번 단계는 일봉 데이터 연결 확인입니다. 전체시장 RS 순위나 종목 추천은 아직 계산하지 않습니다.')

BASE = 'https://openapi.koreainvestment.com:9443'
STOCKS = {'삼성전자': '005930', 'SK하이닉스': '000660', '현대차': '005380'}

@st.cache_data(ttl=20*3600, show_spinner=False)
def token_for(key, secret):
    response = requests.post(BASE+'/oauth2/tokenP', json={'grant_type':'client_credentials','appkey':key,'appsecret':secret}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not payload.get('access_token'):
        raise RuntimeError('토큰 발급 실패: '+str(payload.get('msg1','응답 오류')))
    return payload['access_token']

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_daily(code, start, end, key, secret):
    token = token_for(key, secret)
    response = requests.get(
        BASE+'/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
        headers={'content-type':'application/json','authorization':'Bearer '+token,'appkey':key,'appsecret':secret,'tr_id':'FHKST03010100'},
        params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code,
                'FID_INPUT_DATE_1':start,'FID_INPUT_DATE_2':end,
                'FID_PERIOD_DIV_CODE':'D','FID_ORG_ADJ_PRC':'1'}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if payload.get('rt_cd') != '0':
        raise RuntimeError('KIS 응답 오류: '+str(payload.get('msg1',payload.get('msg_cd','알 수 없음'))))
    rows = payload.get('output2') or []
    data = pd.DataFrame(rows)
    required = ['stck_bsop_date','stck_clpr','acml_vol']
    if data.empty or any(c not in data.columns for c in required):
        raise RuntimeError('일봉 응답이 비어 있거나 필수 항목이 없습니다.')
    data = data[required].copy()
    data.columns = ['날짜','종가','거래량']
    data['날짜'] = pd.to_datetime(data['날짜'],format='%Y%m%d',errors='coerce')
    for col in ['종가','거래량']:
        data[col] = pd.to_numeric(data[col],errors='coerce')
    data = data.dropna().sort_values('날짜').drop_duplicates('날짜')
    if data.empty: raise RuntimeError('유효한 일봉 데이터가 없습니다.')
    return data

if not all(k in st.secrets for k in ('KIS_APP_KEY','KIS_APP_SECRET')):
    st.error('Streamlit Secrets에 KIS_APP_KEY 및 KIS_APP_SECRET을 등록하세요.')
    st.stop()

if st.button('🔎 3종목 일봉 연결 테스트', type='primary', use_container_width=True):
    end = date.today()
    start = end-timedelta(days=45)
    key, secret = st.secrets['KIS_APP_KEY'], st.secrets['KIS_APP_SECRET']
    results = {}
    for name, code in STOCKS.items():
        try:
            with st.spinner(f'{name} 일봉 조회 중...'):
                df = fetch_daily(code,start.strftime('%Y%m%d'),end.strftime('%Y%m%d'),key,secret)
            results[name] = df
            st.success(f'{name} ({code}): {len(df)}개 일봉 조회 성공')
            st.metric(f'{name} 최근 조회 종가', f"{int(df.iloc[-1]['종가']):,}원")
        except Exception as exc:
            st.error(f'{name} 조회 실패: {str(exc)[:250]}')
    if results:
        selected = st.selectbox('차트 종목',list(results))
        df = results[selected].set_index('날짜')
        st.line_chart(df['종가'])
        st.dataframe(df.tail(10).sort_index(ascending=False),use_container_width=True)
        st.caption('최근 약 45달력일 범위 테스트. KIS 일봉 응답은 요청당 데이터 건수 제한이 있어 장기 RS 계산에는 분할 조회가 필요합니다.')
