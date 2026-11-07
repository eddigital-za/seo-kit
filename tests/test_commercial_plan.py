import unittest
from app.commercial_plan import build_commercial_plan, intent, expected_target, load_plan

class CommercialPlanTest(unittest.TestCase):
    def test_intent_and_unmapped_routes(self):
        self.assertEqual(intent('motorbike transport johannesburg'), 'commercial')
        self.assertEqual(intent('motorbike delivery'), 'ambiguous')
        self.assertEqual(intent('bicycle transport'), 'outside_pilot')
        self.assertIsNone(expected_target('motorcycle transport bloemfontein to cape town', load_plan())['expected_page'])

    def test_tracking_variants_and_missing_previous_are_not_added(self):
        rows = [dict(query='motorbike transport', page='https://biketransport.co.za/'+suffix, impressions=n, clicks=0, ctr=0, position=8) for suffix,n in [('',100),('?utm=gbp',80)]]
        result=build_commercial_plan({'analytics':{'rows':rows}})
        self.assertEqual(len(result['top_opportunities']),1)
        lead=result['top_opportunities'][0]
        self.assertEqual(lead['impressions'],100)
        self.assertIsNone(lead['previous_impressions'])
        self.assertEqual(len(result['markets']),5)
        self.assertTrue(any(not r['observations'] for r in result['keyword_to_page_map']))

    def test_previous_comparison_uses_exact_url(self):
        row=dict(query='motorbike transport',page='https://biketransport.co.za/',impressions=100,clicks=1,ctr=.01,position=8)
        result=build_commercial_plan({'analytics':{'rows':[row]},'previous_analytics':{'rows':[{**row,'impressions':70,'clicks':0}]}})
        self.assertEqual(result['top_opportunities'][0]['impression_change'],30)
