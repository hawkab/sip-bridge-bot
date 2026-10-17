"""Selective term correction. No network, model loading, or production side effects.

The first-pass transcript is immutable. A donor can replace at most two words
inside one existing row, with exact character edits and an audit trail.
"""
from copy import deepcopy
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from functools import lru_cache
import math
import re

SR = 16000
WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+(?:[-+#][A-Za-zА-Яа-яЁё0-9]+)*")
STOP = set('а и но да нет не ни в во на к ко от до из за с со по под над о об у для при это этот эта эти то вот там тут я мы вы он она они мне вам нас вас себя свой как что кто где когда если или же бы был была были будет есть уже ещё еще ну угу ага алло хорошо понятно спасибо пожалуйста'.split())
TERMS = {
    'NVIDIA': ('энвидиа', 'нвидиа', 'энвидия', 'нвидия'),
    'Spark NVIDIA': ('спарк энвидиа', 'спарк нвидиа'),
    'Ethernet': ('эзернет', 'изернет', 'этернет'),
    'Wi-Fi': ('вайфай', 'вай фай'),
    'Bluetooth': ('блютуз', 'блютус'),
    'Docker': ('докер', 'доккер'),
    'Kubernetes': ('кубернетес', 'кубернетис'),
    'Telegram': ('телеграм', 'телеграмм'),
    'WhatsApp': ('ватсап', 'ватсапп', 'вотсап'),
    'YouTube': ('ютуб', 'ютюб'),
    'Zoom': ('зум',), 'Google': ('гугл',),
    'Windows': ('виндовс', 'виндоус'), 'Linux': ('линукс',),
    'Python': ('пайтон', 'питон'), 'JavaScript': ('джаваскрипт',),
    'GitHub': ('гитхаб',), 'GitLab': ('гитлаб',),
    'GigaAM': ('гигаам', 'гига ам'), 'Parakeet': ('паракит', 'паракет'),
    'Whisper': ('виспер', 'уиспер'), 'API': ('апи', 'эйпиай'),
    'SIP': ('сип',), 'SMS': ('смс', 'эсэмэс'), 'USB': ('юсб', 'юэсби'),
    'HDMI': ('ашдиэмай', 'хдми'), 'VPN': ('впн', 'випиэн'),
    'OpenAI': ('опенай', 'опен эйай'), 'ChatGPT': ('чатгпт', 'чатджипити'),
}


@dataclass(frozen=True)
class Policy:
    require_term_hint: bool = True
    low_word_probability: float = .65
    weak_word_probability: float = .80
    min_context_seconds: float = 6
    max_window_seconds: float = 20
    budget_fraction: float = .15
    min_budget_seconds: float = 12
    max_budget_seconds: float = 60
    max_windows: int = 8
    min_phonetic_similarity: float = .4
    min_donor_probability: float = .90
    splice_guard_seconds: float = .25
    min_anchor_words: int = 1
    min_total_anchor_words: int = 3


def norm(text):
    return re.sub(r'[^a-zа-я0-9]', '', text.lower().replace('ё','е'))


@lru_cache(maxsize=8192)
def phonetic(text):
    s = text.lower()
    for a,b in [('tion','шн'),('ch','ч'),('sh','ш'),('th','т'),('ph','ф'),('oo','у'),('ee','и'),('ck','к')]:
        s = s.replace(a,b)
    s = re.sub(r'c(?=[eiy])','с',s)
    s = s.translate(str.maketrans(dict(zip('abcdefghijklmnopqrstuvwxyz',
        ['а','б','к','д','е','ф','г','х','и','дж','к','л','м','н','о','п','к','р','с','т','у','в','в','кс','и','з']))))
    s = s.translate(str.maketrans({'ё':'е','э':'е','й':'и','ы':'и','ь':'','ъ':''}))
    return re.sub(r'(.)\1+',r'\1',norm(s))


@lru_cache(maxsize=16384)
def similarity(a,b):
    a,b = phonetic(a),phonetic(b)
    if not a or not b:
        return 0.
    prev = list(range(len(b)+1))
    for i,x in enumerate(a,1):
        cur=[i]
        for j,y in enumerate(b,1):
            cur.append(min(cur[-1]+1,prev[j]+1,prev[j-1]+(x!=y)))
        prev=cur
    return 1-prev[-1]/max(len(a),len(b))


@lru_cache(maxsize=16384)
def term_similarity(text, canonical):
    return max(similarity(text, alias) for alias in (canonical,*TERMS[canonical]))


def confidence_words(interval):
    tokens, probs = interval.get('tokens',[]),interval.get('logprobs',[])
    if len(tokens)!=len(probs) or not tokens or any(not math.isfinite(p) or p>0 for p in probs):
        return []
    words=[]
    for token,p in zip(tokens,probs):
        if not token:
            continue
        if not words or token[0].isspace():
            words.append({'text':token.strip(),'probs':[]})
        else:
            words[-1]['text']+=token
        # Punctuation probability is not evidence of a misrecognized term.
        if WORD.search(token):
            words[-1]['probs'].append(p)
    return [dict(text=w['text'], probability=math.exp(sum(w['probs'])/len(w['probs'])))
            for w in words if w['probs'] and len(norm(w['text']))>=3 and norm(w['text']) not in STOP
            and not any(c.isdigit() for c in w['text'])]


def suspicion(interval, policy):
    words=confidence_words(interval)
    weak=[w for w in words if w['probability']<policy.weak_word_probability]
    low=[w for w in words if w['probability']<policy.low_word_probability]
    reasons=[]
    if low or len(weak)>=2:
        reasons.append('low_confidence')
    pieces=[m.group() for m in WORD.finditer(interval['text'])]
    matches=[]
    for i in range(len(pieces)):
        for count in (1,2):
            part=pieces[i:i+count]
            if len(part)!=count or any(norm(w) in STOP or any(c.isdigit() for c in w) for w in part):
                continue
            phrase=' '.join(part)
            for canonical in TERMS:
                if norm(phrase)!=norm(canonical) and term_similarity(phrase,canonical)>=.85:
                    matches.append({'phrase':phrase,'term':canonical})
    if matches:
        reasons.append('term_like')
    score=(2 if matches else 0)+(1-min((w['probability'] for w in weak),default=1))
    return dict(reasons=reasons,score=score,weak_words=weak,term_matches=matches)


def select_windows(intervals, policy=Policy()):
    speech=sum((r['end']-r['start'])/SR for r in intervals)
    budget=min(policy.max_budget_seconds,max(policy.min_budget_seconds,speech*policy.budget_fraction)) if speech else 0
    groups=[]
    for channel in sorted({r['channel'] for r in intervals}):
        current=[]
        for interval in sorted((r for r in intervals if r['channel']==channel),key=lambda r:r['start']):
            if current and (interval['end']-current[0]['start'])/SR>policy.max_window_seconds:
                groups.append(current);current=[]
            current.append(interval)
        if current:
            groups.append(current)
    considered=[]
    for group in groups:
        flags=[dict(start=r['start'],end=r['end'],**suspicion(r,policy)) for r in group]
        flags=[f for f in flags if f['reasons']]
        if not flags:
            continue
        seconds=sum((r['end']-r['start'])/SR for r in group)
        considered.append(dict(channel=group[0]['channel'],start=group[0]['start'],end=group[-1]['end'],
            pieces=[{'start':r['start'],'end':r['end']} for r in group],seconds=seconds,
            score=max(f['score'] for f in flags),flags=flags,
            eligible=seconds>=policy.min_context_seconds and (group[-1]['end']-group[0]['start'])/SR<=policy.max_window_seconds+.6))
    has_term_hint=any('term_like' in flag['reasons'] for group in considered for flag in group['flags'])
    chosen=[];spent=0.
    for w in sorted(considered,key=lambda w:(-w['score'],w['channel'],w['start'])):
        if policy.require_term_hint and not has_term_hint:
            break
        if w['eligible'] and spent+w['seconds']<=budget+1e-9 and len(chosen)<policy.max_windows:
            chosen.append(w);spent+=w['seconds']
    return sorted(chosen,key=lambda w:(w['channel'],w['start'])),dict(
        budget_seconds=budget,selected_seconds=spent,speech_seconds=speech,
        considered_windows=len(considered),selected_windows=len(chosen),considered=considered,has_term_hint=has_term_hint,
        policy=asdict(policy))


def pack_window(audio, window):
    import numpy as np
    parts=[];boundaries=[];cursor=0
    for p in window['pieces']:
        assert 0<=p['start']<p['end']<=len(audio)
        if parts:
            boundaries.append(cursor/SR)
        parts.append(audio[p['start']:p['end']]);cursor+=p['end']-p['start']
    return np.ascontiguousarray(np.concatenate(parts),dtype=np.float32),boundaries


def donor_words(result):
    """Map native confidence to words; no donor timestamp enters final output."""
    words=[]
    for w in result.get('words',[]):
        probs=[t['p'] for t in result.get('tokens',[])[w['first_token']:w['first_token']+w['n_tokens']]]
        probability=math.exp(sum(math.log(max(p,1e-12)) for p in probs)/len(probs)) if probs and all(math.isfinite(p) and 0<p<=1 for p in probs) else None
        for m in WORD.finditer(w['text']):
            words.append(dict(text=m.group(),start=w['t0_ms']/1000,end=w['t1_ms']/1000,
                              probability=probability))
    return words


def propose_edits(rows, window, donor, boundaries, policy=Policy()):
    base=[]
    for index,row in enumerate(rows):
        if row['channel']!=window['channel'] or row['start']<window['start']/SR-.05 or row['end']>window['end']/SR+.05:
            continue
        base.extend(dict(text=m.group(),row=index,start=m.start(),end=m.end()) for m in WORD.finditer(row['text']))
    other=donor_words(donor)
    ops=SequenceMatcher(None,[norm(w['text']) for w in base],[norm(w['text']) for w in other],autojunk=False).get_opcodes()
    proposals=[]
    canonical_by_norm={norm(t):t for t in TERMS}
    suspects={norm(w['text']) for flag in window.get('flags',[]) for w in flag['weak_words']}
    suspects.update(norm(m['phrase']) for flag in window.get('flags',[]) for m in flag['term_matches'])
    for index,(tag,a,b,c,d) in enumerate(ops):
        if tag=='equal':
            continue
        before=' '.join(w['text'] for w in base[a:b]);after=' '.join(w['text'] for w in other[c:d])
        reason=None;canonical=canonical_by_norm.get(norm(after))
        if tag!='replace' or not(1<=b-a<=2 and 1<=d-c<=2): reason='not_local_replacement'
        elif len({w['row'] for w in base[a:b]})!=1: reason='crosses_row'
        elif any(norm(w['text']) in STOP for w in base[a:b]): reason='protected_word'
        elif norm(before) not in suspects and not any(norm(w['text']) in suspects for w in base[a:b]): reason='not_suspicious_target'
        elif re.findall(r'\d+',before)!=re.findall(r'\d+',after): reason='numbers_changed'
        elif not canonical or not re.fullmatch(r'[A-Za-z0-9 +#-]+',after): reason='not_known_latin_term'
        elif term_similarity(before,canonical)<policy.min_phonetic_similarity: reason='phonetic_mismatch'
        elif any(w['probability'] is None or w['probability']<policy.min_donor_probability for w in other[c:d]): reason='donor_uncertain'
        elif index==0 or index+1==len(ops) or ops[index-1][0]!='equal' or ops[index+1][0]!='equal' or min(ops[index-1][2]-ops[index-1][1],ops[index+1][2]-ops[index+1][1])<policy.min_anchor_words or (ops[index-1][2]-ops[index-1][1]+ops[index+1][2]-ops[index+1][1])<policy.min_total_anchor_words: reason='insufficient_anchors'
        elif any(min(abs(w['start']-cut),abs(w['end']-cut))<policy.splice_guard_seconds or w['start']<cut<w['end'] for cut in boundaries for w in other[c:d]): reason='splice_ambiguity'
        elif any(w['start']<0 or w['end']>window['seconds']+.08 for w in other[c:d]): reason='invalid_donor_time'
        proposals.append(dict(before=before,donor=after,replacement=canonical,
            accepted=reason is None,reason=reason or 'known_term_with_anchors',
            row=base[a]['row'] if b>a else None,
            start_char=base[a]['start'] if b>a else None,
            end_char=base[b-1]['end'] if b>a else None,
            channel=window['channel'],window_start=window['start']/SR,
            phonetic_similarity=term_similarity(before,canonical) if canonical else None))
    return proposals


def apply_edits(payload, proposals):
    result=deepcopy(payload);accepted=[]
    by_row={}
    for p in proposals:
        if p['accepted']:
            by_row.setdefault(p['row'],[]).append(p)
    for index,changes in by_row.items():
        row=result['conversation'][index];original=row['text'];last=len(original)
        for p in sorted(changes,key=lambda p:p['start_char'],reverse=True):
            start,end=p['start_char'],p['end_char']
            if not(0<=start<end<=last<=len(original)) or row['channel']!=p['channel']:
                continue
            if ' '.join(m.group() for m in WORD.finditer(original[start:end]))!=p['before']:
                continue
            # Never replace punctuation between two source words.
            if re.search(r'[^A-Za-zА-Яа-яЁё0-9\s+#-]',original[start:end]):
                continue
            row['text']=row['text'][:start]+p['replacement']+row['text'][end:]
            last=start;accepted.append(dict(p,base_text=original,base_start=row['start'],base_end=row['end']))
    assert len(result['conversation'])==len(payload['conversation'])
    for old,new in zip(payload['conversation'],result['conversation']):
        assert {k:v for k,v in old.items() if k!='text'}=={k:v for k,v in new.items() if k!='text'}
        assert re.findall(r'\d+',old['text'])==re.findall(r'\d+',new['text'])
    return result,accepted


def correct(payload, windows, recognize_window, policy=Policy()):
    """Keep the complete first pass even when a donor window fails."""
    proposals=[];attempts=[]
    for window in windows:
        try:
            donor,boundaries=recognize_window(window)
            changes=propose_edits(payload['conversation'],window,donor,boundaries,policy)
            proposals.extend(changes)
            attempts.append(dict(window=window,donor=donor,boundaries=boundaries,proposals=changes,error=None))
        except Exception as exc:
            attempts.append(dict(window=window,error=f'{type(exc).__name__}: {exc}'))
    result,accepted=apply_edits(payload,proposals)
    return result,dict(accepted=accepted,attempts=attempts,proposals=proposals)
