import unittest
from workspace_settings import resolved_rules
from comparison_rules import equivalent

class RuleScopeTests(unittest.TestCase):
    def test_explicit_empty_rules_disable_legacy_defaults(self):
        defaults={'comparison_rules':[{'column':'amount','tolerance':'0'}]}
        self.assertEqual(resolved_rules(defaults,{'comparison_rules':[]},['id','amount'],['id'],[]),[])
        self.assertEqual(len(resolved_rules(defaults,{},['id','amount'],['id'],[])),1)

    def test_multiple_normalizations_for_multiple_columns(self):
        rules=resolved_rules({}, {'comparison_rules':[{'column':c,'trim':True,'ignore_case':True,'tolerance':'0'} for c in ['a','b']]},['id','a','b'],['id'],[])
        for rule in rules:
            self.assertTrue(equivalent(' NONE ','none',rule))
            self.assertTrue(equivalent(' -11 ','-11.00',rule))
            self.assertFalse(equivalent('11','12',rule))

    def test_key_and_ignored_rules_rejected(self):
        for column in ['id','ignored']:
            with self.assertRaises(ValueError):
                resolved_rules({}, {'comparison_rules':[{'column':column,'trim':True}]},['id','ignored','a'],['id'],['ignored'])
