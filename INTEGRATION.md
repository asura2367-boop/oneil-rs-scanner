# V5.19 일괄 안전 점검 추가

기존 Paper 파일은 변경하지 않습니다. 아래 두 파일을 저장소에 업로드합니다.
- `oneil_v519_execution_safety.py` → 저장소 루트
- `tests/test_v519_execution_safety.py` → tests 폴더

기존 `.github/workflows/oneil_live_v5_19.yml`의 `Merge same-run evidence and hard DENY` 단계 바로 앞에 추가:

```yaml
      - name: Test consolidated execution safety
        run: python -m unittest discover -s tests -p 'test_v519_execution_safety.py' -v
      - name: Audit order-state recovery with hard deny
        run: python oneil_v519_execution_safety.py --candidate v5_11_review.json --account v5_13_review.json --quote v5_18_report.json --risk data/live/v4_8_audit.json --output v5_19_execution_safety.json
```

아티팩트 업로드 `path`에 `v5_19_execution_safety.json`을 추가합니다. 기존 실계좌/호가 조회 옵션은 변경하지 않습니다.

**제약:** 이는 주문 상태 전이와 증빙 검증의 오프라인 테스트입니다. 실제 주문 접수, 취소, 체결 복구, 일일 실손익 산출은 아직 미구현입니다. `LIVE_ENABLED=false`, `KIS_ALLOW_ORDER=false` 유지.
