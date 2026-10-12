"""Deterministic synthetic enterprise data, not GDPval original amounts or task answers."""
import json, random, hashlib
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parent
SEED=20261012025
rng=random.Random(SEED)
records=[]
def add(domain, rid, obj, program=None, supplier=None):
    records.append(dict(domain=domain,record_id=rid,program_id=program,supplier_id=supplier,payload=obj))
def business(domain, rid, p, supplier, obj):
    obj=dict(document_id=rid,program_id=p,supplier_id=supplier,**obj)
    if domain != 'documents': add(domain,rid,obj,p,supplier)
    add('documents',rid,dict(document_id=rid,document_type=domain,source_version=obj.get('revision',1),source_system='Synthetic automotive sourcing ERP',record=obj),p,supplier)
for i in range(1,19):
    s=f'SUP-{i:03}'
    add('suppliers',s,dict(supplier_id=s,name=f'{["Aurora","Boreal","Cedar","Delta","Elm","Falcon"][i%6]} Lighting {i:02}',country=['CN','MY','DE'][i%3],status='active',qualification_register=f'QREG-{i:03}'),supplier=s)
for n in range(1,151):
    p=f'PRG-{n:03}'
    sup=[f'SUP-{((n*3+j)%18)+1:03}' for j in range(3)]
    mix=[0.7,0.65,0.8,0.6][n%4]
    basevol=rng.randrange(24000,50001,1000)
    launch='2027-01-01'
    replacement=n%7 in (5,6) and n%17!=0
    add('programs',p,dict(program_id=p,name=f'Platform {n:03} headlamp sourcing',component='headlamp',base_currency='CNY',quantity_uom='vehicle_set',pieces_per_vehicle_set=2,variants=['base','premium'],fiscal_periods=[2027,2028,2029,2030],as_of_date='2026-10-12',launch_date=launch,base_variant_share=str(mix),supplier_ids=sup,forecast_document_ids=[f'F-{n:03}-{y}-{v}-r{r}' for y in range(2027,2031) for v in ['base','premium'] for r in [1,2]],finance_policy_document_id=f'POL-{n:03}',fx_document_id=f'FX-{n:03}',engineering_required_date=launch))
    policy=dict(currency='CNY',uom='vehicle_set',effective_period={'from':'2026-01-01','through':'2030-12-31'},approval_status='approved',revision=1,annual_discount_rate='0.10',first_discounted_fiscal_period=2028,valuation_fiscal_period=2027,operating_payment_date_by_period=[dict(fiscal_period=y,due_date=f'{y}-12-31') for y in range(2027,2031)],tooling_charge_quantity_uom='vehicle_set',tooling_recovery_quantity=100000,tooling_quantity_scope='all program variants',rd_payment_due='at award',rd_allocation_share={'base':'0.50','premium':'0.50'},tier_quantity_scope='supplier award across program variants',intra_period_variant_allocation='approved fiscal-period variant mix',replacement_quantity_opening_balance=0)
    business('documents',f'POL-{n:03}',p,None,policy)
    business('documents',f'FX-{n:03}',p,None,dict(currency='CNY',uom='CNY per one foreign currency unit',effective_period={'from':'2026-10-01','through':'2030-12-31'},approval_status='approved',revision=1,rates=[dict(fiscal_period=y,CNY='1.0000',USD=f'{7.1+(y-2027)*0.06+n%5*.02:.4f}',EUR=f'{7.75+(y-2027)*0.05+n%4*.02:.4f}') for y in range(2027,2031)],upfront_rates={'CNY':'1.0000','USD':f'{7.1+n%5*.02:.4f}','EUR':f'{7.75+n%4*.02:.4f}'}))
    for y in range(2027,2031):
        total=basevol+(y-2027)*rng.randrange(3000,9001,1000)
        for rev in [1,2]:
            t=total if rev==2 else total-3000
            b=round(t*mix)
            for v,q in [('base',b),('premium',t-b)]:
                business('forecasts',f'F-{n:03}-{y}-{v}-r{rev}',p,None,dict(revision=rev,replaces_document_id=f'F-{n:03}-{y}-{v}-r1' if rev==2 else None,approval_status=('draft' if n%4==0 else 'approved') if rev==2 else ('approved' if n%4 in (0,2) else 'superseded'),approved_at=('2026-10-08' if n%4!=0 else None) if rev==2 else '2026-08-01',fiscal_period=y,variant=v,quantity=q,uom='vehicle_set',currency='CNY',quantity_scope='full_fiscal_year',effective_period={'from':f'{y}-01-01','through':f'{y}-12-31'}))
    for j,s in enumerate(sup):
        currency=['CNY','USD','EUR'][(n+j)%3]
        fx={'CNY':1,'USD':7.15,'EUR':7.8}[currency]
        uom='headlamp_piece' if (n+j)%2 else 'vehicle_set'
        unitfactor=2 if uom=='headlamp_piece' else 1
        tool=rng.randrange(1700000,4200001,100000)
        rd=rng.randrange(600000,1500001,100000)
        amended_state=['draft','approved','approved','revoked','approved'][(n+j)%5]
        future_amendment=(n+j)%5==2
        for rev in [1,2]:
            for v in ['base','premium']:
                local=rng.randrange(180,351)+(50 if v=='premium' else 0)
                qprice=round(local/fx/unitfactor,4)
                business('quotes',f'Q-{n:03}-{j}-{v}-r{rev}',p,s,dict(revision=rev,replaces_document_id=f'Q-{n:03}-{j}-{v}-r1' if rev==2 else None,issued_at='2026-10-05' if rev==2 else '2026-07-15',approval_status=amended_state if rev==2 else ('approved' if amended_state!='approved' or future_amendment else 'superseded'),valid_through='2026-12-15',variant=v,currency=currency,uom=uom,pieces_per_vehicle_set=2,effective_period={'from':'2028-01-01' if rev==2 and future_amendment else '2027-01-01','through':'2030-12-31'},tier_quantity_uom='vehicle_set',tier_application='graduated' if j==2 else 'flat',price_tiers=([dict(cumulative_from_inclusive=0,cumulative_to_exclusive=100000+n%3*20000,unit_price=f'{qprice:.4f}'),dict(cumulative_from_inclusive=100000+n%3*20000,cumulative_to_exclusive=None,unit_price=f'{qprice*(.94-j*.01):.4f}')] if j==2 else [dict(cumulative_from_inclusive=0,cumulative_to_exclusive=None,unit_price=f'{qprice:.4f}')]),period_price_multipliers=[dict(fiscal_period=y,multiplier=f'{1+(y-2027)*([.025,-.015,.01][j]):.4f}') for y in range(2027,2031)],tooling_total_quote_currency=f'{tool/fx:.2f}',rd_total_quote_currency=f'{rd/fx:.2f}',upfront_cost_scope='program',tooling_charge_id=f'TOOL-{n:03}-{j}',rd_charge_id=f'RD-{n:03}-{j}',tooling_in_piece_price=False,rd_in_piece_price=False,tooling_charge_basis='vehicle_set',freight_included=True,payment_terms='net 30; upfront engineering at award'))
        for rev in [1,2]:
            lead=[5,8,17][j]+n%3
            cap=basevol+[[-8000,12000,26000],[9000,16000,26000],[-9000,8000,18000]][j][n%3]
            business('engineering',f'E-{n:03}-{j}-r{rev}',p,s,dict(revision=rev,approval_status='approved' if rev==2 else 'superseded',assessed_at='2026-10-09' if rev==2 else '2026-08-15',currency='CNY',uom='vehicle_set',effective_period={'from':'2026-10-09','through':'2030-12-31'},qualification_status='qualified' if j<2 or n%4 else 'conditional',required_actions=[] if j<2 else ['complete photometry validation'],requalification_weeks=lead,tooling_transfer_weeks=2,annual_capacity=[dict(fiscal_period=y,quantity=max(10000,cap+(y-2027)*[4000,6000,7000][j])) for y in range(2027,2031)],capacity_basis='available allocation after other awarded business',technical_coverage=['base','premium']))
    if not replacement: continue
    old=sup[2]
    business('contracts',f'C-{n:03}-nomination',p,old,dict(revision=1,approval_status='executed',currency='CNY',uom='program',effective_period={'from':'2026-06-01','through':'2026-10-01'},status='cancelled' if n%5 else 'superseded',cancelled_at='2026-10-01',cancellation_reason='supplier failed launch milestone' if n%5 else 'planned engineering redesign',tooling_total=f'{1800000+n%9*150000:.2f}',rd_total=f'{600000+n%7*100000:.2f}',upfront_payment_fraction='0.30',cash_refund_fraction_of_settled_upfront='0.30',refund_due_date='2026-12-01',tooling_title='customer' if n%3 else 'supplier',tooling_transfer_credit_cny='200000.00' if n%3 else '0.00',credit_condition='credit reduces replacement tooling total; no cash refund',replacement_contract_document_id=f'C-{n:03}-change'))
    business('contracts',f'C-{n:03}-change',p,None,dict(revision=2,approval_status='approved',currency='CNY',uom='program',effective_period={'from':'2026-10-02','through':'2030-12-31'},status='replacement_authorization',replaces_contract_id=f'C-{n:03}-nomination',authorized_supplier_ids=sup[:2] if n%2 else sup,nomination_not_yet_awarded=True,change_control='Finance and Engineering approval required before external award',launch_date=launch,qualification_deadline=launch,transition_cost_cny='180000.00',transition_cost_timing='upfront at t=0'))
    for kind,amount in [('tooling',f'{(1800000+n%9*150000)*.30:.2f}'),('rd',f'{(600000+n%7*100000)*.30:.2f}')]:
        business('payments',f'PAY-{n:03}-{kind}',p,old,dict(revision=1,approval_status='posted',currency='CNY',uom='invoice',effective_period={'from':'2026-06-01','through':'2026-10-12'},contract_document_id=f'C-{n:03}-nomination',invoice_id=f'INV-{n:03}-{kind}',cost_category=kind,invoice_total=amount,amount_paid=amount,paid_at='2026-06-12',payment_status='settled',refund_received='0.00' if n%5 else f'{float(amount)*.15:.2f}',refund_status='not_received' if n%5 else 'partial'))
counts=dict(sorted(Counter(r['domain'] for r in records).items()))
blob=json.dumps(dict(schema_version=1,seed=SEED,records=records),ensure_ascii=False,indent=2)+'\n'
(ROOT/'seed.json').write_text(blob,encoding='utf-8')
(ROOT/'counts.json').write_text(json.dumps(dict(seed=SEED,counts=counts,seed_sha256=hashlib.sha256(blob.encode()).hexdigest()),indent=2)+'\n',encoding='utf-8')
print(json.dumps(counts))


