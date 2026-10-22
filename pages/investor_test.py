"""Read-only KIS investor data diagnostic page. No orders and no credential logging."""
import streamlit as st
import requests
import pandas as pd

st.title('🔎 외국인·기관 수급 연결 테스트')
st.caption('삼성전자 005930 · KRX · 장 마감 후 제공되는 투자자별 일별 데이터')
st.info('기존 RS 추천 앱과 자동 수집 코드는 변경하지 않습니다. 이 페이지는 연결 테스트 전용입니다.')

try:
    key = str(st.secrets.get('KIS_APP_KEY', '')).strip()
    secret = str(st.secrets.get('KIS_APP_SECRET', '')).strip()
except Exception:
    key, secret = '', ''
if not key or not secret:
    st.error('Streamlit Secrets에 KIS_APP_KEY와 KIS_APP_SECRET이 필요합니다.')
    st.stop()

@st.cache_data(ttl=3600, show_spinner=False)
def get_investor(_unused: str, appkey: str, appsecret: str):
    # This cached function holds secrets in server memory; never show/cache tokens in files.
    base = 'https://openapi.koreainvestment.com:9443'
    session = requests.Session()
    try:
        auth = session.post(base + '/oauth2/tokenP', json={
            'grant_type': 'client_credentials', 'appkey': appkey, 'appsecret': appsecret,
        }, timeout=20)
        if not auth.ok:
            raise RuntimeError(f'인증 실패 HTTP {auth.status_code} (토큰 발급 제한일 수 있음)')
        token = auth.json().get('access_token')
        if not token:
            raise RuntimeError('인증 응답에 access_token 없음')
        headers = {
            'authorization': f'Bearer {token}', 'appkey': appkey,
            'appsecret': appsecret, 'tr_id': 'FHKST01010900',
            'content-type': 'application/json; charset=utf-8',
        }
        res = session.get(base + '/uapi/domestic-stock/v1/quotations/inquire-investor',
            headers=headers, params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':'005930'}, timeout=25)
        if not res.ok:
            raise RuntimeError(f'수급 조회 HTTP {res.status_code}')
        payload = res.json()
        if str(payload.get('rt_cd')) != '0':
            raise RuntimeError(f"API 오류 {payload.get('msg_cd', '')}: {payload.get('msg1', '원인 미확인')}")
        output = payload.get('output') or []
        if isinstance(output, dict):
            output = [output]
        if not output:
            raise RuntimeError('수급 데이터가 비어 있습니다.')
        return output
    finally:
        session.close()

if st.button('삼성전자 수급 조회 테스트', type='primary'):
    with st.spinner('조회 중...'):
        try:
            rows = get_investor('005930', key, secret)
            df = pd.DataFrame(rows)
            st.success(f'조회 성공: {len(df)}개 행, {len(df.columns)}개 필드')
            st.caption('원본 필드명과 값 확인용. 단위·매수/매도 방향 검증 전이므로 순매수 금액으로 해석하지 않습니다.')
            st.dataframe(df.head(10), use_container_width=True)
            st.code(', '.join(df.columns.tolist()), language='text')
            st.warning('당일 투자자별 확정 데이터는 장 종료 후 제공됩니다. 장중 실시간 수급으로 해석하지 마세요.')
        except Exception as exc:
            st.error(str(exc))
            st.caption('반복 실행하면 토큰 발급 제한이 발생할 수 있습니다. 오류 시 즉시 재시도하지 마세요.')
