"""Grouped picker compatibility, independent membership and legacy migration."""
import json
import pytest
from test_exitlab_storage import db
from test_exitlab_m5 import seed, client_for
from exitlab.assignments import setup_options, journal_rows, applies, saved_rule
from exitlab.schema import migrate
from exitlab.rules import Rule


def selection(group, name):
    return {'group': group, 'name': name}


def test_private_union_counts_case_zero_names_and_eligibility(db):
    seed(db, [(100, 100.5, 99.5, 100)])
    db.execute("UPDATE trades SET setup='A'")
    db.execute("UPDATE trade_analysis SET strategy='VWAP Support'")
    db.execute("CREATE TABLE library_items(kind TEXT,name TEXT)")
    db.executemany("INSERT INTO library_items VALUES(?,?)", [('strategy','VWAP Support'),('strategy','Unused'),('strategy','vwap support'),('source','Ignore')])
    db.execute("DROP TABLE IF EXISTS custom_setups")
    db.execute("CREATE TABLE custom_setups(name TEXT,active INTEGER)")
    db.executemany("INSERT INTO custom_setups VALUES(?,?)", [('Unused',1),('Fresh',1),('Retired',0)])
    db.commit()
    with client_for(db) as client:
        result = client.get('/api/exitlab/setups?account_id=1')
        assert result.status_code == 200
        strategies, auto = result.json()
        assert strategies['items'] == [
            {'name':'VWAP Support','trades':1,'eligible':1},
            {'name':'Fresh','trades':0,'eligible':0},
            {'name':'Unused','trades':0,'eligible':0},
            {'name':'vwap support','trades':0,'eligible':0}]
        assert auto['items'] == [{'name':'A','trades':1,'eligible':1}]
        other = client.get('/api/exitlab/setups?account_id=2').json()
        assert all(item['trades'] == 0 for item in other[0]['items'])
        assert other[1]['items'] == []
        db.execute('UPDATE trade_analysis SET stop_loss=NULL')
        assert setup_options(db,1)[0]['items'][0]['eligible'] == 0
        db.execute('UPDATE trade_analysis SET stop_loss=99')
        db.execute('DELETE FROM market_bars')
        assert setup_options(db,1)[1]['items'][0]['eligible'] == 0


@pytest.mark.parametrize('schema', ['no_strategy', 'no_analysis'])
def test_public_schema_without_optional_tables_or_strategy(db, schema):
    seed(db, [(100, 100.5, 99.5, 100)])
    db.execute("UPDATE trades SET setup='B'")
    db.execute('DROP TABLE IF EXISTS custom_setups')
    db.execute('DROP TABLE IF EXISTS library_items')
    if schema == 'no_strategy':
        db.execute('ALTER TABLE trade_analysis DROP COLUMN strategy')
    else:
        db.execute('DROP TABLE trade_analysis')
    db.commit()
    with client_for(db) as client:
        result = client.get('/api/exitlab/setups?account_id=1')
        assert result.status_code == 200, result.text
        assert result.json() == [
            {'group':'strategy','label':'Strategies','items':[]},
            {'group':'auto','label':'Auto setups','items':[{'name':'B','trades':1,'eligible':int(schema == 'no_strategy')}]}]
        assert journal_rows(db,1)[0]['strategy'] is None


def test_either_field_matches_and_display_prefers_strategy(db):
    seed(db, [(100, 100.5, 99.5, 100)])
    db.execute("UPDATE trades SET setup='A'")
    db.execute("UPDATE trade_analysis SET strategy='VWAP Cross'")
    db.commit()
    with client_for(db) as client:
        for rid, assigned in ((1,[selection('strategy','VWAP Cross')]), (2,[selection('auto','A')]),
                              (3,[selection('strategy','A')]), (4,[selection('auto','VWAP Cross')])):
            response = client.patch(f'/api/exitlab/rules/{rid}',json={'applies_to_setups':assigned})
            assert response.status_code == 200, response.text
        replay = client.get('/api/exitlab/trades/1/simple').json()
        assert [r['rule_id'] for r in replay['rules']] == [1,2]
        assert replay['rows'][0]['setup_name'] == 'VWAP Cross'
        history = client.post('/api/exitlab/history',json={'account_id':1}).json()
        assert [r['eligible'] for r in history['rules']] == [1,1,0,0]
        db.execute("UPDATE trades SET setup='B'")
        db.commit()
        client.post('/api/exitlab/history',json={'account_id':1})
        db.execute("UPDATE trade_analysis SET strategy=NULL")
        db.commit()
        assert client.get('/api/exitlab/trades/1/simple').json()['rows'][0]['setup_name'] == 'Leader Long (B)'


def test_migration_idempotent_rules_and_policy_preservation(db):
    seed(db, [(100, 100.5, 99.5, 100)])
    db.execute("UPDATE trades SET setup='A'")
    legacy = json.dumps(['Unused Strategy','A','A'])
    db.execute('UPDATE exit_rules SET applies_to_setups=? WHERE id=1',(legacy,))
    before = dict(db.execute('SELECT * FROM exit_rules WHERE id=1').fetchone())
    db.commit()
    migrate(db)
    migrate(db)
    expected = [selection('auto','A'),selection('strategy','Unused Strategy')]
    after = dict(db.execute('SELECT * FROM exit_rules WHERE id=1').fetchone())
    assert json.loads(after['applies_to_setups']) == expected
    assert {k:v for k,v in before.items() if k != 'applies_to_setups'} == {k:v for k,v in after.items() if k != 'applies_to_setups'}
    assert applies(saved_rule(after), {'setup':'A','strategy':None})
    assert applies(saved_rule(after), {'setup':'B','strategy':'Unused Strategy'})
    assert not applies(saved_rule(after), {'setup':'Unused Strategy','strategy':'A'})
    assert applies(Rule(name='All'), {})


@pytest.mark.parametrize('value', [[], ['A'], [{'group':'bad','name':'A'}], [{'group':'auto','name':' '}], [{'group':'auto','name':'A','extra':True}]])
def test_invalid_assignments_rejected(db, value):
    with client_for(db) as client:
        assert client.patch('/api/exitlab/rules/1',json={'applies_to_setups':value}).status_code == 422
