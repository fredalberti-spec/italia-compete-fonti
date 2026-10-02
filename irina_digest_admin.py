"""Explicit service-shell commands. No web endpoint, new credentials or scheduled tests."""
import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from irina_inbox import Inbox, owner_phones, WABA_ID, PHONE_ID
from irina_processor import WhatsApp, Archive, request, json_request, NoRedirect
from irina_publications import template_json
from irina_digest import BASE,TEMPLATE,BODY,validate_pdf,upload_pdf,approved_template
from irina_digest_costs import Budget,CostBlocked

TEST_FILE=BASE+'/Test/la-settimana-in-prospettiva-mockup-v6.pdf'
TEST_HASH='b8ac2688e02b7b58f75dcc42a7cb3dc40753b62c2f8f02d4ed2e5e9a8e0fc01e'
TEST_KEY='mockup-'+TEST_HASH[:16]
LABEL='CONTROLUCE — PROVA MOCK-UP; non rassegna verificata; contenuti illustrativi'
SUBMISSION='digest_template_submission_v1'
ACTIVATION='digest_delivery_active_v1'


def context():
    import dropbox
    inbox=Inbox(Path(os.environ['IRINA_DATA_DIR']))
    owners=owner_phones(os.environ['IRINA_OWNER_PHONES'])
    primary=os.environ['IRINA_PRIMARY_PHONE']
    if primary not in owners:raise ValueError('PrimaryNotOwner')
    wa=WhatsApp(os.environ['IRINA_WHATSAPP_TOKEN'],owners)
    writer=Archive(os.environ['IRINA_DROPBOX_REFRESH_TOKEN'],os.environ['DROPBOX_APP_KEY']).client.with_path_root(dropbox.common.PathRoot.namespace_id('2166447024'))
    path=Path(os.environ['IRINA_DATA_DIR'])/'notes_dropbox.json'
    refresh=json.loads(path.read_text())['refresh_token'] if path.exists() else os.environ['IRINA_DROPBOX_REFRESH_TOKEN']
    reader=dropbox.Dropbox(oauth2_refresh_token=refresh,app_key=os.environ['DROPBOX_APP_KEY'],timeout=45).with_path_root(dropbox.common.PathRoot.namespace_id('2166447024'))
    return SimpleNamespace(inbox=inbox,whatsapp=wa,primary=primary,writer=writer,reader=reader)


def mockup(p):
    meta=p.reader.files_get_metadata(TEST_FILE)
    if not 0<meta.size<=10*1024*1024:raise ValueError('InvalidMockupSize')
    _,r=p.reader.files_download(TEST_FILE)
    return validate_pdf(r.content,TEST_HASH)


def sample_handle(wa,data):
    base='https://graph.facebook.com/v23.0/'
    session=template_json(base+'app/uploads?file_length='+str(len(data))+'&file_type=application%2Fpdf&file_name=PROVA-MOCKUP.pdf',wa.token,{})
    sid=session.get('id','')
    if not re.fullmatch(r'upload:[A-Za-z0-9_:=?&.%-]+',sid):raise ValueError('InvalidUploadSession')
    req=urllib.request.Request(base+sid,data=data,headers={'Authorization':'OAuth '+wa.token,'file_offset':'0','Content-Type':'application/pdf'})
    with urllib.request.build_opener(NoRedirect).open(req,timeout=90) as r:result=json.loads(r.read(65536))
    if not result.get('h'):raise ValueError('MissingSampleHandle')
    return result['h']


def template(p):
    url='https://graph.facebook.com/v23.0/'+WABA_ID+'/message_templates'
    found=template_json(url+'?name='+TEMPLATE+'&fields=name,status,language,category,components&limit=100',p.whatsapp.token)
    match=next((t for t in found.get('data',[]) if t.get('name')==TEMPLATE and t.get('language')=='it'),None)
    if match:return {k:match.get(k) for k in ('name','status','language','category')}
    with p.inbox.db() as db:
        if db.execute('SELECT 1 FROM settings WHERE key=?',(SUBMISSION,)).fetchone():
            return {'status':'SUBMISSION_UNCERTAIN','name':TEMPLATE}
    handle=sample_handle(p.whatsapp,mockup(p))
    payload={'name':TEMPLATE,'language':'it','category':'MARKETING','components':[
        {'type':'HEADER','format':'DOCUMENT','example':{'header_handle':[handle]}},
        {'type':'BODY','text':BODY,'example':{'body_text':[[LABEL]]}}]}
    with p.inbox.db() as db:
        changed=db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(SUBMISSION,'submission_started')).rowcount
    if not changed:return {'status':'SUBMISSION_UNCERTAIN','name':TEMPLATE}
    try:
        result=template_json(url,p.whatsapp.token,payload)
    except Exception:
        # Marker intentionally remains. Reconcile exact-name lookup, never blind repeat.
        raise
    report={'name':TEMPLATE,'status':result.get('status','PENDING'),'category':result.get('category','MARKETING')}
    with p.inbox.db() as db:db.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(report),SUBMISSION))
    return report


def report(p):
    import dropbox
    with p.inbox.db() as db:
        row=db.execute('SELECT * FROM digest_tests WHERE key=?',(TEST_KEY,)).fetchone()
        result=dict(row) if row else {'key':TEST_KEY,'state':'not_attempted'}
        result['receipts']=[dict(r) for r in db.execute('SELECT status,stamp FROM receipts WHERE id=?',(result.get('message_id'),))]
        cost=db.execute('SELECT year,gross_micros,category FROM digest_costs WHERE key=?',('test:'+TEST_KEY,)).fetchone()
        result['cost_reservation']=dict(cost) if cost else None
    result.update(test_only=True,editorial_verified=False,pdf_sha256=TEST_HASH)
    p.writer.files_upload(json.dumps(result,ensure_ascii=False,indent=2).encode(),BASE+'/Esiti/Test-'+TEST_KEY+'.json',mode=dropbox.files.WriteMode.overwrite,mute=True)
    return result


def test(p):
    with p.inbox.db() as db:
        row=db.execute('SELECT state FROM digest_tests WHERE key=?',(TEST_KEY,)).fetchone()
    if row:return report(p)  # one attempt across restarts, never an edition
    with p.inbox.db() as db:
        previous=db.execute("SELECT 1 FROM digest_tests WHERE state IN ('sending','accepted','uncertain') LIMIT 1").fetchone()
    if previous:raise ValueError('PreviousTestAttemptExists')
    t=approved_template(p.whatsapp)
    if not t:raise ValueError('DocumentTemplateNotApproved')
    budget=Budget(p.inbox,time.time)
    micros,category=budget.quote(t.get('category'),p.primary)
    data=mockup(p)
    mid=upload_pdf(p.whatsapp,data)
    payload={'messaging_product':'whatsapp','to':p.primary,'type':'template',
        'template':{'name':TEMPLATE,'language':{'code':'it'},'components':[
            {'type':'header','parameters':[{'type':'document','document':{'id':mid,'filename':'CONTROLUCE-PROVA-MOCKUP-NON-VERIFICATO.pdf'}}]},
            {'type':'body','parameters':[{'type':'text','text':LABEL}]}]}}
    with p.inbox.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM digest_tests WHERE state IN ('sending','accepted','uncertain') OR key=?",(TEST_KEY,)).fetchone():return {'state':'already_attempted'}
        budget.reserve(db,'test:'+TEST_KEY,micros,category)
        db.execute('INSERT INTO digest_tests(key,state) VALUES (?,?)',(TEST_KEY,'sending'))
    state,mid,error='uncertain',None,None
    try:
        result=json_request('https://graph.facebook.com/v23.0/'+PHONE_ID+'/messages',p.whatsapp.token,payload)
        mid=result.get('messages',[{}])[0].get('id')
        if not mid:raise ValueError('MissingMessageId')
        state='accepted'
    except Exception:error='SendUncertain'
    with p.inbox.db() as db:db.execute('UPDATE digest_tests SET state=?,message_id=?,error=? WHERE key=?',(state,mid,error,TEST_KEY))
    return report(p)


def activate(p):
    r=report(p)
    if not any(x['status']=='delivered' for x in r['receipts']):raise ValueError('TestDeliveryNotVerified')
    t=approved_template(p.whatsapp)
    if not t:raise ValueError('DocumentTemplateNotApproved')
    Budget(p.inbox,time.time).quote(t.get('category'),p.primary)
    with p.inbox.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(ACTIVATION,'true'))
    return {'digest_delivery_enabled':True,'annual_gross_limit_eur':5}


def main():
    global TEST_FILE,TEST_HASH,TEST_KEY
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['template','test','status','activate','rates'])
    parser.add_argument('--test-file');parser.add_argument('--test-sha256')
    args=parser.parse_args();command=args.command
    if args.test_file or args.test_sha256:
        if (not args.test_file or not args.test_sha256
                or not re.fullmatch(re.escape(BASE)+r'/Test/[A-Za-z0-9_.-]+\.pdf',args.test_file)
                or not re.fullmatch('[a-f0-9]{64}',args.test_sha256)):
            parser.error('Both a dedicated Test PDF path and exact SHA-256 are required')
        TEST_FILE,TEST_HASH=args.test_file,args.test_sha256
        TEST_KEY='mockup-'+TEST_HASH[:16]
    try:
        p=context()
        if command=='rates':
            from irina_digest_costs import live_rates
            result={'meta_rates_eur':live_rates()}
        else:result={'template':template,'test':test,'status':report,'activate':activate}[command](p)
        print(json.dumps(result,ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'blocked':str(exc) if isinstance(exc,CostBlocked) else type(exc).__name__}))
        raise SystemExit(1)

if __name__=='__main__':main()
