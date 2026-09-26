import json
import unittest
from json_compare import compare_json

class JsonComparisonTests(unittest.TestCase):
    def compare(self,a,b,default='ordered',rules=None):return compare_json(json.dumps(a),json.dumps(b),default,rules)
    def test_nested_unordered_and_duplicates(self):
        a={'x':[{'tags':[1,2],'id':1},{'id':2}]};b={'x':[{'id':2},{'id':1,'tags':[2,1]}]}
        self.assertTrue(self.compare(a,b,'unordered')['equal'])
        self.assertFalse(self.compare(a,b)['equal'])
        result=self.compare([1,1,2],[2,1],'unordered');self.assertEqual(result['counts']['removed'],1)
    def test_per_path_and_keyed_original_paths(self):
        a={'users':[{'id':'a','v':1,'tags':[1,2]},{'id':'b','v':2}]}
        b={'users':[{'id':'b','v':3},{'id':'a','v':1,'tags':[2,1]}]}
        rules=[{'path':'$.users','mode':'keyed','field':'id'},{'path':'$.users[*].tags','mode':'unordered'}]
        result=self.compare(a,b,rules=rules)
        self.assertEqual(result['counts'],{'added':0,'removed':0,'changed':1})
        self.assertEqual(result['differences'][0]['left_path'],'$.users[1].v')
        self.assertEqual(result['differences'][0]['right_path'],'$.users[0].v')
    def test_override_order_and_special_properties(self):
        a={'a.b':[[1,2]]};b={'a.b':[[2,1]]}
        self.assertFalse(self.compare(a,b,'unordered',[{'path':'$["a.b"][*]','mode':'ordered'}])['equal'])
    def test_types_null_missing_and_precision(self):
        self.assertFalse(self.compare(True,1)['equal'])
        self.assertFalse(self.compare({'a':None},{})['equal'])
        self.assertTrue(compare_json('1.0','1')['equal'])
        self.assertFalse(compare_json('9007199254740992','9007199254740993')['equal'])
        self.assertFalse(compare_json('"1"','1')['equal'])
    def test_invalid_and_ambiguous_inputs(self):
        for text in ['{"a":1,"a":2}','NaN','{','['*102+'0'+']'*102]:
            with self.assertRaises(ValueError):compare_json(text,'{}')
        for a in [[{'id':1},{'id':1}],[{'missing':1}],[{'id':None}]]:
            with self.assertRaises(ValueError):self.compare(a,a,rules=[{'path':'$','mode':'keyed','field':'id'}])
        with self.assertRaises(ValueError):self.compare([],[],rules=[{'path':'$[0]','mode':'ordered'}])
    def test_unmatched_rules_and_changed_block(self):
        result=self.compare([{'x':1}],[{'x':2}],'unordered',[{'path':'$.absent','mode':'ordered'}])
        self.assertEqual(result['counts'],{'added':1,'removed':1,'changed':0})
        self.assertEqual(len(result['warnings']),1)

if __name__=='__main__':unittest.main()
