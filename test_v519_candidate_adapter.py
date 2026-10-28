import unittest,datetime as dt
from zoneinfo import ZoneInfo
from v519_candidate_adapter import select_candidates
class CandidateTests(unittest.TestCase):
 def test_select(self):
  now=dt.datetime(2026,10,27,12,15,tzinfo=ZoneInfo("Asia/Seoul"))
  report={"candidate_input_valid":True,"candidate_source":"V4B_CSV_UNVERIFIED",
          "mode":"READ_ONLY_CANDIDATE_AND_ACCOUNT_EVIDENCE",
          "generated_at_kst":"2026-10-27T12:12:36+09:00","candidate_count":2,
          "candidates":[{"ticker":"085910","rs":98,"flow_category":"INSTITUTION_ONLY"},
                        {"ticker":"083470","rs":85,"flow_category":"FOREIGN_ONLY"}]}
  self.assertEqual([x["ticker"] for x in select_candidates(report,now=now)],["085910"])
  with self.assertRaisesRegex(ValueError,"STALE"):
   select_candidates(report,now=now+dt.timedelta(days=2))
if __name__=="__main__": unittest.main()
