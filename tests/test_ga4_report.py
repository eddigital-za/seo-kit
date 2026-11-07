import unittest
from app.email_report import ga4_referrals, render_email

class GA4ReportTest(unittest.TestCase):
    def payload(self, rows):
        return {'ga4_report':{'metricHeaders':[{'name':x} for x in ['sessions','engagedSessions','keyEvents']], 'rows':rows,'rowCount':len(rows)},'start_date':'2026-10-11','end_date':'2026-11-07'}
    def test_live_totals_and_label(self):
        result=ga4_referrals(self.payload([{'metricValues':[{'value':'7'},{'value':'3'},{'value':'0'}]}]))
        self.assertEqual(result['sessions'],7)
        html=render_email({'ai_referrals':result})['email_html']
        self.assertIn('Fresh GA4 report',html)
        self.assertNotIn('Automatic refresh pending',html)
    def test_empty_valid_report_is_zero_and_invalid_metrics_fail(self):
        self.assertEqual(ga4_referrals(self.payload([]))['sessions'],0)
        payload=self.payload([]);payload['ga4_report']['metricHeaders']=[]
        with self.assertRaises(ValueError): ga4_referrals(payload)
