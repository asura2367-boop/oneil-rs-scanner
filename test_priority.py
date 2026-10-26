import tempfile,json,sys
from pathlib import Path
from datetime import date
import pandas as pd
sys.path.insert(0,str(Path(__file__).parent))
import build_data as b
with tempfile.TemporaryDirectory() as temp:
 p=Path(temp);(p/'paper').mkdir();(p/'v4b').mkdir()
 (p/'paper'/'state.json').write_text(json.dumps({'mode':'paper_only_v1','pending':[{'ticker':'052460','name':'아이크래프트'}], 'positions':{'048910':{'name':'대원미디어'}}}))
 pd.DataFrame([{'ticker':'005930','date':'2026-10-26','name':'삼성전자'}]).to_csv(p/'v4b'/'candidates.csv',index=False)
 u=[('005930','삼성전자'),('052460','아이크래프트'),('048910','대원미디어')]
 assert [x[0] for x in b.prioritized_symbols(p,u,date(2026,10,26))]==['052460','048910','005930']
 d=pd.DataFrame([{'ticker':'052460','date':'2026-10-23','close':5980,'volume':100}, {'ticker':'048910','date':'2026-10-26','close':8500,'volume':50}, {'ticker':'005930','date':'2026-10-26','close':70000,'volume':500}])
 try: b.verify_priority_dates(d,u,date(2026,10,26),True)
 except RuntimeError as e: assert '052460' in str(e)
 else: raise AssertionError('missed missing daily')
 d.loc[d.ticker=='052460','date']='2026-10-26'
 assert b.verify_priority_dates(d,u,date(2026,10,26),True)['052460']=='2026-10-26'
 print('priority order and daily missing guard: PASS')
