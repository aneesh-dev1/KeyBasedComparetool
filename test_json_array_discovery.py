import json
import unittest
from json_compare import discover_arrays, compare_json

class ArrayDiscoveryTests(unittest.TestCase):
    def test_students_reordered_with_selected_key(self):
        students = [dict(id='A1',name='Jim',math=60,physics=66,chemistry=61),dict(id='A2',name='Dwight',math=89,physics=76,chemistry=51),dict(id='A3',name='Kevin',math=79,physics=90,chemistry=78)]
        left = json.dumps(dict(school_name='Dunder Miflin', **{'class':'Year 1'}, students=students))
        right = json.dumps(dict(school_name='Dunder Miflin', **{'class':'Year 1'}, students=[students[0],students[2],students[1]]))
        array = discover_arrays(left,right)['arrays'][0]
        self.assertEqual(array['path'],'$.students')
        self.assertEqual(array['fields'][0],'id')
        self.assertEqual(array['left_items'],3)
        self.assertFalse(compare_json(left,right)['equal'])
        rule = dict(path=array['path'], mode='keyed', field='id')
        self.assertTrue(compare_json(left,right,rules=[rule],include_view=True)['equal'])
        changed=json.loads(right);changed['students'][2]['math']=88
        result=compare_json(left,json.dumps(changed),rules=[rule])
        self.assertEqual(result['counts']['changed'],1)
    def test_nested_arrays_have_independent_keys(self):
        left=json.dumps({'students':[{'id':'A1','subjects':[{'code':'M','score':1},{'code':'P','score':2}]},{'id':'A2','subjects':[{'code':'M','score':3}]}]})
        right=json.dumps({'students':[{'id':'A2','subjects':[{'code':'M','score':3}]},{'id':'A1','subjects':[{'code':'P','score':2},{'code':'M','score':1}]}]})
        arrays={a['path']:a for a in discover_arrays(left,right)['arrays']}
        self.assertIn('code',arrays['$.students[*].subjects']['fields'])
        rules=[dict(path='$.students',mode='keyed',field='id'),dict(path='$.students[*].subjects',mode='keyed',field='code')]
        self.assertTrue(compare_json(left,right,rules=rules)['equal'])
        self.assertFalse(compare_json(left,right,rules=rules[:1])['equal'])
    def test_invalid_keys_not_offered_and_exact_numbers_preserved(self):
        cases=['[{"id":1},{"id":1.0}]','[{"id":null}]','[{"id":1},{}]','[{"id":{}}]','[1,2]']
        for value in cases:
            with self.subTest(value=value):self.assertEqual(discover_arrays(value,value)['arrays'][0]['fields'],[])
        value='[{"id":9007199254740992},{"id":9007199254740993}]'
        self.assertEqual(discover_arrays(value,value)['arrays'][0]['fields'],['id'])
        self.assertEqual(discover_arrays('[{"id":1},{"id":true}]','[]')['arrays'][0]['fields'],['id'])
    def test_every_nested_occurrence_and_both_sides_checked(self):
        left='{"groups":[{"items":[{"id":1}]},{"items":[{"id":1},{"id":1}]}]}'
        right='{"groups":[{"items":[{"id":2}]}]}'
        arrays={a['path']:a for a in discover_arrays(left,right)['arrays']}
        self.assertEqual(arrays['$.groups[*].items']['fields'],[])
        self.assertEqual(discover_arrays('[{"id":1}]','[{}]')['arrays'][0]['fields'],[])
    def test_empty_special_paths_and_discovery_limits(self):
        self.assertEqual(discover_arrays('{}','{}')['arrays'],[])
        value='{"a.b":[{"student.id":"x"}]}'
        array=discover_arrays(value,value)['arrays'][0]
        self.assertEqual(array['path'],'$["a.b"]')
        self.assertEqual(array['fields'],['student.id'])
        self.assertTrue(compare_json(value,value,rules=[dict(path=array['path'],mode='keyed',field='student.id')])['equal'])
        with self.assertRaises(ValueError):discover_arrays('{','{}')
        with self.assertRaises(ValueError):discover_arrays(json.dumps({str(i):[] for i in range(101)}),'{}')
