import requests
import streamlit as st

st.set_page_config(page_title='오닐 RS - KIS 연결 테스트', page_icon='📈', layout='centered')
st.title('📈 오닐 RS · 한국투자증권 연결 테스트')
st.caption('삼성전자(005930) 현재가 조회 · 실전투자 API · 주문 기능 없음')

if 'KIS_APP_KEY' not in st.secrets or 'KIS_APP_SECRET' not in st.secrets:
    st.error('Streamlit Secrets에 KIS_APP_KEY와 KIS_APP_SECRET을 등록해 주세요.')
    st.code('KIS_APP_KEY = "발급받은 APP Key"\nKIS_APP_SECRET = "발급받은 APP Secret"', language='toml')
    st.stop()

BASE = 'https://openapi.koreainvestment.com:9443'

@st.cache_data(ttl=60*60*20, show_spinner=False)
def get_token(key, secret):
    response = requests.post(BASE + '/oauth2/tokenP', json={
        'grant_type': 'client_credentials', 'appkey': key, 'appsecret': secret
    }, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if 'access_token' not in payload:
        raise RuntimeError(f"토큰 발급 실패: {payload.get('msg1', payload.get('msg_cd', '응답 확인 필요'))}")
    return payload['access_token']

def current_price(token, key, secret):
    response = requests.get(
        BASE + '/uapi/domestic-stock/v1/quotations/inquire-price',
        headers={
            'content-type': 'application/json',
            'authorization': 'Bearer ' + token,
            'appkey': key, 'appsecret': secret,
            'tr_id': 'FHKST01010100',
        },
        params={'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': '005930'},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('rt_cd') != '0':
        raise RuntimeError(f"시세 조회 실패: {payload.get('msg1', payload.get('msg_cd', '응답 확인 필요'))}")
    return payload.get('output') or {}

if st.button('🔎 삼성전자 시세 조회 테스트', type='primary', use_container_width=True):
    try:
        with st.spinner('한국투자증권 API 연결 중...'):
            key = st.secrets['KIS_APP_KEY']
            secret = st.secrets['KIS_APP_SECRET']
            token = get_token(key, secret)
            quote = current_price(token, key, secret)
        price = quote.get('stck_prpr')
        if not price:
            st.warning('연결됐지만 현재가 항목이 비어 있습니다.')
        else:
            st.success('삼성전자 시세 조회 성공!')
            st.metric('삼성전자 (005930) 현재가', f'{int(price):,}원')
            st.caption('API 응답 시세이며 실시간 체결 스트리밍은 아닙니다.')
    except requests.exceptions.RequestException as exc:
        st.error(f'네트워크/API 연결 오류: {type(exc).__name__}. 서버 로그에서 상세 내용을 확인하세요.')
    except Exception as exc:
        st.error(str(exc))

st.info('이 버전은 연결 테스트 전용입니다. RS 전체시장 추천 기능은 API 연결 확인 후 추가합니다.')
