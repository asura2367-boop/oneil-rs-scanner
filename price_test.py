"""Read-only KIS domestic stock quote diagnostic. No trading endpoints."""
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import streamlit as st

BASE = 'https://openapi.koreainvestment.com:9443'
CODE = '005930'
st.set_page_config(page_title='KIS 현재가 테스트', page_icon='🔎')
st.title('🔎 삼성전자 현재가 연결 테스트')
st.caption('005930 · KIS 실전 시세 조회 전용 · 주문 기능 없음')
st.warning('API 응답은 조회 시점의 시세 스냅샷입니다. 거래소 실시간 체결 보장이나 가상 체결 증빙이 아닙니다.')

try:
    key = str(st.secrets.get('KIS_APP_KEY', '')).strip()
    secret = str(st.secrets.get('KIS_APP_SECRET', '')).strip()
except Exception:
    key = secret = ''
if not key or not secret:
    st.error('Streamlit Secrets에 KIS_APP_KEY, KIS_APP_SECRET이 필요합니다.')
    st.stop()

if st.button('삼성전자 현재가 조회 (1회)', type='primary'):
    try:
        with requests.Session() as session:
            auth = session.post(BASE + '/oauth2/tokenP', json={
                'grant_type':'client_credentials', 'appkey':key, 'appsecret':secret
            }, timeout=20)
            if not auth.ok:
                raise RuntimeError(f'토큰 발급 HTTP {auth.status_code} (발급 제한 또는 인증 설정 확인)')
            token = auth.json().get('access_token')
            if not token:
                raise RuntimeError('토큰 발급 응답에 access_token이 없습니다.')
            headers = {
                'authorization':f'Bearer {token}', 'appkey':key, 'appsecret':secret,
                'tr_id':'FHKST01010100', 'content-type':'application/json; charset=utf-8'
            }
            response = session.get(
                BASE + '/uapi/domestic-stock/v1/quotations/inquire-price',
                headers=headers,
                params={'FID_COND_MRKT_DIV_CODE':'J', 'FID_INPUT_ISCD':CODE},
                timeout=25
            )
            received_at = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M:%S KST')
            if not response.ok:
                raise RuntimeError(f'현재가 조회 HTTP {response.status_code}')
            payload = response.json()
            if str(payload.get('rt_cd')) != '0':
                raise RuntimeError(f"KIS API 오류 {payload.get('msg_cd','미확인')}: {payload.get('msg1','')}")
            out = payload.get('output') or {}
            if not isinstance(out, dict) or not out:
                raise RuntimeError('현재가 응답 output이 비어 있거나 형식이 다릅니다.')
            def number(field):
                raw = out.get(field)
                try:
                    return f'{int(raw):,}' if raw not in (None, '') else '미제공'
                except (TypeError, ValueError):
                    return str(raw) if raw is not None else '미제공'
            st.success('조회 성공 · KIS 응답 정상')
            st.metric('현재가 (stck_prpr)', f"{number('stck_prpr')}원")
            st.metric('당일 누적 거래량 (acml_vol)', f"{number('acml_vol')}주")
            st.write(f'서버에서 응답 수신한 시각: **{received_at}**')
            st.caption('수신 시각은 거래소 체결 시각이 아닙니다. 장외 시간에는 마지막 시세가 반환될 수 있습니다.')
            with st.expander('진단용 필드 (민감정보 제외)'):
                st.json({k: out.get(k) for k in ('stck_prpr','acml_vol','stck_oprc','stck_hgpr','stck_lwpr','stck_bsop_date','stck_cntg_hour') if k in out})
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        st.error(str(exc))
        st.info('연속 재시도는 토큰 발급 제한을 유발할 수 있습니다. 오류 메시지를 확인해 주세요.')
