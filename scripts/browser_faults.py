"""Real-browser network fault injection against an isolated mechanical API.

The server really accepts jobs; Playwright drops selected responses afterwards.
This exercises transport/recovery, never model accuracy or paid requests.
"""
import argparse
from datetime import datetime, timezone
from copy import deepcopy
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
QUEUE_KEY='cqa.pending-evaluations.v1'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    args=parser.parse_args()
    base=args.url.rstrip('/')
    if urlsplit(base).hostname not in {'127.0.0.1','localhost','::1'}:
        raise SystemExit('Only local isolated test servers are allowed')
    checks=[];status='failed'
    with httpx.Client(base_url=base,timeout=15) as client, sync_playwright() as p:
        if client.get('/api/config').raise_for_status().json().get('mode')!='test':
            raise SystemExit('Refusing mutations outside explicit test mode')
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
        page.set_default_timeout(15000)
        errors=[]
        page.on('pageerror',lambda e: errors.append(str(e)))
        page.on('dialog',lambda d: d.accept())
        page.goto(base,wait_until='networkidle')

        def seed(name,count=1):
            dataset=json.loads((ROOT/'fixtures/synthetic_dev.json').read_text(encoding='utf-8'))
            group=name+'-'+uuid4().hex[:8]
            dataset['data']['dataset_group_id']=group
            dataset['data']['records']=dataset['data']['records'][:count]
            dataset['data']['total_records']=count
            for i,row in enumerate(dataset['data']['records']):row['conversation_id']=group+'-'+str(i)
            page.get_by_label('Выбрать JSON с диалогами').set_input_files({'name':'fault.json','mimeType':'application/json','buffer':json.dumps(dataset,ensure_ascii=False).encode()})
            expect(page.locator('.conversation-item')).to_have_count(count)
            expect(page.get_by_role('heading',name=dataset['data']['records'][0]['conversation_id'],exact=True)).to_be_visible()
            return group

        def finished():
            expect(page.locator('.batch-banner')).to_contain_text('Обработка завершена',timeout=30000)
            expect(page.locator('.job-errors')).to_have_count(0)
            expect(page.locator('.queue-recovery')).to_have_count(0)

        def start_run(name='Оценить'):
            # Wait through the fresh-config preflight; a previous completed banner
            # can still be visible until the new queue actually starts dispatching.
            with page.expect_request(lambda request: request.method=='POST' and request.url.endswith('/api/evaluation-jobs')):
                page.get_by_role('button',name=name,exact=True).click()

        def evaluations(group):
            return client.get('/api/evaluations',params={'group_id':group}).raise_for_status().json()

        def force_poll(route):
            # A fast mechanical evaluator can finish before the browser reads 202.
            # Delay only its status visibility, keeping the real accepted job ID.
            response=route.fetch();body=response.json()
            body.update(status='queued',evaluation_id=None)
            route.fulfill(status=202,json=body)

        try:
            group=seed('lost-once')
            accepted=[]; keys=[]
            def lose_once(route):
                response=route.fetch()
                accepted.append(response.json()['job_id'])
                keys.append(route.request.post_data_json['idempotency_key'])
                if len(accepted)==1:route.abort('failed')
                else:route.fulfill(response=response)
            page.route('**/api/evaluation-jobs',lose_once)
            start_run()
            finished()
            assert len(accepted)==2 and len(set(accepted))==len(set(keys))==1
            assert len(evaluations(group))==1
            checks.append({'case':'accepted_POST_response_lost_once','passed':True,'http_posts':2,'unique_jobs':1,'evaluations':1})
            page.unroute('**/api/evaluation-jobs')

            group=seed('poll-503')
            polls=[]
            def transient(route):
                polls.append(route.request.url)
                if len(polls)==1:route.fulfill(status=503,json={'detail':'Synthetic temporary outage'})
                else:route.continue_()
            page.route('**/api/evaluation-jobs/*',transient)
            page.route('**/api/evaluation-jobs',force_poll)
            start_run()
            finished()
            assert len(polls)>=2 and len(evaluations(group))==1
            checks.append({'case':'transient_poll_503_recovers','passed':True,'poll_requests':len(polls),'evaluations':1})
            page.unroute('**/api/evaluation-jobs/*')
            page.unroute('**/api/evaluation-jobs')

            group=seed('lost-all-and-reload',3)
            accepted=[];keys=[]
            def lose_all(route):
                response=route.fetch(); accepted.append(response.json()['job_id'])
                keys.append(route.request.post_data_json['idempotency_key']);route.abort('failed')
            page.route('**/api/evaluation-jobs',lose_all)
            start_run('Оценить выборку (3)')
            expect(page.locator('.queue-recovery')).to_be_visible()
            assert len(accepted)==3 and len(set(accepted))==len(set(keys))==1
            assert len(evaluations(group))==1
            page.unroute('**/api/evaluation-jobs')
            page.reload(wait_until='networkidle')
            expect(page.get_by_role('button',name='Продолжить очередь')).to_be_enabled()
            page.get_by_role('button',name='Продолжить очередь').click()
            finished()
            assert len(evaluations(group))==3
            assert page.evaluate('(key)=>sessionStorage.getItem(key)',QUEUE_KEY) is None
            checks.append({'case':'all_POST_responses_lost_then_reload_resume','passed':True,'lost_posts':3,'first_unique_jobs':1,'final_evaluations':3})

            group=seed('reload-active',3)
            start_run('Оценить выборку (3)')
            page.wait_for_function('(key)=>JSON.parse(sessionStorage.getItem(key))?.items.some(x=>x.job_id)',arg=QUEUE_KEY)
            page.reload(wait_until='networkidle')
            expect(page.locator('.queue-recovery')).to_be_visible()
            page.get_by_role('button',name='Продолжить очередь').click()
            finished()
            assert len(evaluations(group))==3
            checks.append({'case':'reload_during_active_queue_resumes_once','passed':True,'final_evaluations':3})

            group=seed('stop-resume',3)
            start_run('Оценить выборку (3)')
            page.get_by_role('button',name='Остановить очередь').click()
            expect(page.locator('.queue-recovery')).to_be_visible()
            assert 1<=len(evaluations(group))<3
            page.get_by_role('button',name='Продолжить очередь').click()
            finished()
            assert len(evaluations(group))==3
            checks.append({'case':'explicit_stop_then_resume','passed':True,'final_evaluations':3})

            group=seed('malformed-success')
            accepted=[]
            def null_success(route):
                response=route.fetch();accepted.append(response.json()['job_id'])
                route.fulfill(status=200,body='null',content_type='application/json')
            page.route('**/api/evaluation-jobs',null_success)
            start_run()
            expect(page.locator('.queue-recovery')).to_be_visible()
            assert len(set(accepted))==1
            page.unroute('**/api/evaluation-jobs')
            page.get_by_role('button',name='Продолжить очередь').click()
            finished()
            assert len(evaluations(group))==1
            checks.append({'case':'accepted_POST_null_body_preserves_recovery_key','passed':True,'evaluations':1})

            group=seed('malformed-evaluation')
            page.route('**/api/evaluations/*',lambda route: route.fulfill(status=200,json={'evaluation_id':'truncated'}))
            start_run()
            expect(page.locator('.queue-recovery')).to_be_visible()
            page.unroute('**/api/evaluations/*')
            page.get_by_role('button',name='Продолжить очередь').click()
            finished()
            assert len(evaluations(group))==1
            checks.append({'case':'malformed_completed_evaluation_preserves_job','passed':True,'evaluations':1})

            group=seed('long-cooldown')
            polls=[]
            def cooldown(route):
                polls.append(route.request.url)
                route.fulfill(status=429,headers={'Retry-After':'60'},body='<html>Synthetic rate limit</html>',content_type='text/html')
            page.route('**/api/evaluation-jobs/*',cooldown)
            page.route('**/api/evaluation-jobs',force_poll)
            start_run()
            expect(page.locator('.queue-recovery')).to_be_visible()
            assert len(polls)==1,'Must not retry before a long Retry-After'
            page.unroute('**/api/evaluation-jobs/*')
            page.unroute('**/api/evaluation-jobs')
            page.get_by_role('button',name='Продолжить очередь').click()
            finished()
            checks.append({'case':'long_Retry_After_pauses_without_early_retry','passed':True})

            group=seed('null-config')
            page.route('**/api/config',lambda route: route.fulfill(status=200,body='null',content_type='application/json'))
            page.get_by_role('button',name='Оценить',exact=True).click()
            expect(page.locator('.notice.error')).to_be_visible()
            assert not evaluations(group)
            page.unroute('**/api/config')
            start_run();finished()
            assert len(evaluations(group))==1
            checks.append({'case':'null_config_does_not_lock_future_runs','passed':True})

            group=seed('wrong-job')
            def wrong_job(route):
                response=route.fetch();body=response.json();body['job_id']='another-valid-job-id'
                route.fulfill(status=200,json=body)
            page.route('**/api/evaluation-jobs',force_poll)
            page.route('**/api/evaluation-jobs/*',wrong_job)
            start_run()
            expect(page.locator('.queue-recovery')).to_be_visible()
            page.unroute('**/api/evaluation-jobs');page.unroute('**/api/evaluation-jobs/*')
            page.get_by_role('button',name='Продолжить очередь').click();finished()
            assert len(evaluations(group))==1
            checks.append({'case':'wrong_poll_job_id_preserves_original_job','passed':True})

            group=seed('wrong-checklist')
            def wrong_rules(route):
                response=route.fetch();body=response.json();body['checklist']['version']+=1
                route.fulfill(status=200,json=body)
            page.route('**/api/evaluations/*',wrong_rules)
            start_run();expect(page.locator('.queue-recovery')).to_be_visible()
            page.unroute('**/api/evaluations/*')
            page.get_by_role('button',name='Продолжить очередь').click();finished()
            assert len(evaluations(group))==1
            checks.append({'case':'wrong_checklist_version_preserves_recovery','passed':True})

            held_history=[]
            def hold_history(route):
                response=route.fetch();held_history.append((route,response))
            page.route('**/api/evaluations?*',hold_history)
            group=seed('delayed-history')
            start_run('Оценить выборку (1)');finished()
            assert held_history
            for route,response in held_history:route.fulfill(response=response)
            page.unroute('**/api/evaluations?*')
            expect(page.get_by_role('button',name='Оценить',exact=True)).to_be_enabled()
            stored=evaluations(group);assert len(stored)==1
            expect(page.get_by_role('combobox',name='История',exact=True)).to_have_value(stored[0]['evaluation_id'])
            checks.append({'case':'delayed_old_history_keeps_fresh_result','passed':True})

            group=seed('late-old-snapshot')
            dataset=json.loads((ROOT/'fixtures/synthetic_dev.json').read_text(encoding='utf-8'))
            dataset['data']['dataset_group_id']=group
            dataset['data']['records']=dataset['data']['records'][:1]
            dataset['data']['total_records']=1
            cid=group+'-0';dataset['data']['records'][0]['conversation_id']=cid
            newer=deepcopy(dataset)
            newer['data']['records'][0]['transcript'][1]['text']+=' CURRENT SOURCE REVISION'
            def direct_evaluate(source):
                client.post('/api/datasets/import',json={'dataset':source}).raise_for_status()
                job=client.post('/api/evaluation-jobs',json={'group_id':group,'conversation_id':cid,'checklist_id':'synthetic-checklist','checklist_version':1,'idempotency_key':str(uuid4())}).raise_for_status().json()
                until=time.monotonic()+10
                while job['status'] in ('queued','running') and time.monotonic()<until:
                    time.sleep(.1);job=client.get('/api/evaluation-jobs/'+job['job_id']).raise_for_status().json()
                assert job['status']=='completed'
                return job['evaluation_id']
            current_id=direct_evaluate(newer)
            stale_id=direct_evaluate(dataset)
            client.post('/api/datasets/import',json={'dataset':newer}).raise_for_status()
            page.reload(wait_until='networkidle')
            page.locator('.conversation-item').filter(has_text=cid).click()
            expect(page.get_by_role('combobox',name='История',exact=True)).to_have_value(current_id)
            assert current_id!=stale_id
            checks.append({'case':'current_source_selected_over_late_old_evaluation','passed':True})

            malicious='<img src=x onerror="window.__fault_markup_executed=true">'
            newer['data']['records'][0]['transcript'][0]['text']=malicious
            client.post('/api/datasets/import',json={'dataset':newer}).raise_for_status()
            page.reload(wait_until='networkidle')
            page.locator('.conversation-item').filter(has_text=cid).click()
            expect(page.locator('.transcript-content').get_by_text(malicious,exact=True)).to_be_visible()
            assert page.evaluate('window.__fault_markup_executed') is None
            checks.append({'case':'transcript_HTML_is_inert_text','passed':True})

            page.evaluate('(key)=>sessionStorage.setItem(key,"{broken")',QUEUE_KEY)
            page.reload(wait_until='networkidle')
            expect(page.locator('.workspace-toolbar')).to_be_visible()
            expect(page.locator('.queue-recovery')).to_have_count(0)
            checks.append({'case':'corrupt_saved_queue_does_not_crash_UI','passed':True})
            assert not errors,errors
            page.screenshot(path=str(ROOT/'scratch/fault-browser-desktop.png'),full_page=True)
            status='passed'
        finally:
            report={'status':status,'created_at':datetime.now(timezone.utc).isoformat(),'mode':'real_browser_and_HTTP_mechanical_evaluator','paid_calls':0,'checks':checks,'page_errors':errors}
            (ROOT/'scratch').mkdir(exist_ok=True)
            (ROOT/'scratch/browser-faults-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            browser.close()
    print(json.dumps(report))


if __name__=='__main__':main()
