"""무료 KRX 종목 목록 연결 진단. 기존 주가 CSV는 수정하지 않습니다."""
from pathlib import Path
import sys
import pandas as pd
import FinanceDataReader as fdr

OUT = Path(__file__).resolve().parent / 'data' / 'universe_test.csv'

def main():
    frames=[]
    for market in ('KOSPI','KOSDAQ'):
        print(f'{market}: 종목 목록 조회 중',flush=True)
        try:
            df=fdr.StockListing(market)
            if df is None or df.empty:
                raise ValueError('빈 응답')
            code_col=next((c for c in ('Code','Symbol','Ticker') if c in df.columns),None)
            name_col=next((c for c in ('Name','Company','종목명') if c in df.columns),None)
            if not code_col or not name_col:
                raise ValueError(f'지원되지 않는 열: {list(df.columns)}')
            part=pd.DataFrame({'ticker':df[code_col].astype(str).str.zfill(6),
                               'name':df[name_col].astype(str),'market':market})
            part=part[part.ticker.str.fullmatch(r'\d{6}') & part.name.str.strip().ne('')]
            frames.append(part)
            print(f'{market}: {len(part)}종목',flush=True)
        except Exception as exc:
            print(f'{market} 실패: {type(exc).__name__}: {exc}',flush=True)
    if len(frames)!=2:
        raise RuntimeError('코스피·코스닥 모두 수집되지 않아 기존 데이터는 변경하지 않습니다.')
    result=pd.concat(frames,ignore_index=True).drop_duplicates('ticker').sort_values(['market','ticker'])
    if len(result)<1000:
        raise RuntimeError(f'종목 수 비정상: {len(result)}개. 저장 중단')
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix('.tmp')
    result.to_csv(tmp,index=False,encoding='utf-8-sig')
    tmp.replace(OUT)
    print(f'완료: {len(result)}종목, {OUT}',flush=True)

if __name__=='__main__':
    main()
