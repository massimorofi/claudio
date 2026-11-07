import json
RUN='6dae3f0d'
f='/srv/claudio/claudio-backend/data/sessions/1f908a92-15f3-4df5-963a-0d51145dabf6.jsonl'
ev=[]
for line in open(f):
    d=json.loads(line)
    if not (d.get('run_id','').startswith(RUN) or d['type']=='user'): continue
    t=d['type']
    if t=='tool_result':
        out=str(d.get('output','')); sz=len(out)
    elif t=='tool_call': sz=len(json.dumps(d.get('arguments'))); out=None
    elif t in ('assistant','reasoning','user'): sz=len(str(d.get('content',''))); out=None
    else: continue
    ev.append({'ts':d['ts'],'t':t,'sz':sz,'out':out,'name':d.get('name','')})
ev.sort(key=lambda x:x['ts'])
CALL_TS=sorted({e['ts'] for e in ev if e['t']=='tool_call'})
IDX={ts:i for i,ts in enumerate(CALL_TS)}
# step index for every event: events before call i belong to context of call i
cur=-1; seq=[]
for e in ev:
    if e['t']=='tool_call' and e['ts'] in IDX: cur=IDX[e['ts']]
    seq.append((e,cur))     # cur = step in which this event was produced

def est(cap=None, evict=None, drop_reasoning=False, digest=120, fixed=4000, ct=3.9, nsteps=None):
    """Sum of context chars across every model call, applying a retention policy."""
    total=0.0
    calls=len(CALL_TS)+1
    n=nsteps or calls
    for j in range(n):                       # each model call
        # with fewer calls, the same content is produced by proportionally more of the run
        cut = (j*(len(CALL_TS)+1))//n if n != calls else j
        ctx=0
        for e,st in seq:
            if st>=cut: break                # not yet produced
            s=e['sz']
            if drop_reasoning and e['t']=='reasoning': continue
            if e['t']=='tool_result':
                if cap: s=min(s,cap)
                if evict is not None and st < cut-evict: s=min(s,digest)
            ctx+=s
        total+=ctx+fixed*ct
    return total/ct

base=est()
BASE=base
print(f'{"policy":42}{"est input tok":>14}{"saving":>9}')
def show(name,**kw):
    v=est(**kw); print(f'{name:42}{v:14,.0f}{(1-v/BASE)*100:8.0f}%')
print(f'{"baseline (what actually happened)":42}{base:14,.0f}{0:8}%')
show('cap tool output 4000 ch', cap=4000)
show('drop reasoning from replay', drop_reasoning=True)
show('evict results >12 steps old', evict=12)
show('evict results >6 steps old', evict=6)
show('evict 12 + drop reasoning', evict=12, drop_reasoning=True)
show('evict 6 + drop reasoning + cap 4000', evict=6, drop_reasoning=True, cap=4000)
# fewer steps: simulate halving call count by keeping content but 45 calls
show('evict 6 + drop reasoning + 45 calls', evict=6, drop_reasoning=True, nsteps=45)
show('evict 6 + drop reason + cap 4k + 45 calls', evict=6, drop_reasoning=True, cap=4000, nsteps=45)
print()
print(f'\nmodel check: predicted baseline {base:,.0f} tok vs MEASURED 3,118,010 tok  (error {(base/3118010-1)*100:+.1f}%)')
