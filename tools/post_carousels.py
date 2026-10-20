#!/usr/bin/python3
"""Post approved carousels to Instagram (carousel, with video slides) and the Facebook Page (multi-photo post).

carousel-plan.csv: date,time,id,version,media,caption,status,media_id,posted_at,fb_status,fb_post_id,fb_posted_at
  media = "01.mp4|02.jpg|03.jpg|…" — the Instagram item per slide (mp4 = moving slide). Every slide also has NN.jpg,
  which is what Facebook gets (Facebook multi-photo posts can't mix in video).
  Files must be reachable at $CAROUSEL_PUBLIC_BASE/<id>/v<version>/<file> (Meta fetches media by URL).
A row posts once its date + time (Europe/London) has passed. status/fb_status "ready" → posted independently.

  python3 post_carousels.py                 dry run: what is due + next 5
  python3 post_carousels.py --go            publish everything due (max 2 carousels per run)
  python3 post_carousels.py --go --now C10  publish that one now (both platforms, whichever are still ready)
  python3 post_carousels.py --ig-only / --fb-only   limit a run to one platform
  python3 post_carousels.py auth            check tokens: IG account, FB Page and publishing quota (read-only)
  python3 post_carousels.py urls C10        check every public URL for a row loads

Env: META_IG_TOKEN (same as post_plan.py). Facebook: if META_IG_TOKEN is a Facebook user token (EAA…) the Page
token is fetched from me/accounts; otherwise set FB_PAGE_ID and FB_PAGE_TOKEN. CAROUSEL_PUBLIC_BASE (https://…).
Skips anything whose caption first line is already on that platform, so re-runs are safe.
"""
import csv, json, os, sys, time, urllib.request, urllib.parse, datetime as dt
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLAN = os.path.join(ROOT, 'carousel-plan.csv')
LOG = os.path.join(ROOT, 'post_carousels.log')
UK = ZoneInfo('Europe/London')
GO = '--go' in sys.argv
NOW_ID = sys.argv[sys.argv.index('--now') + 1] if '--now' in sys.argv else None
DO_IG = '--fb-only' not in sys.argv
DO_FB = '--ig-only' not in sys.argv
MAX_PER_RUN = 2
BASE = os.environ.get('CAROUSEL_PUBLIC_BASE', '').rstrip('/')
COLS = ['date', 'time', 'id', 'version', 'media', 'caption', 'status', 'media_id', 'posted_at', 'fb_status', 'fb_post_id', 'fb_posted_at']


def log(msg):
    line = f"{dt.datetime.now(UK):%Y-%m-%d %H:%M} {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def tok():
    t = os.environ.get('META_IG_TOKEN')
    if not t:
        sys.exit('META_IG_TOKEN not set')
    return t


def call(method, path, params=None, token=None, graph=None):
    token = token or tok()
    graph = graph or ('https://graph.instagram.com/v21.0' if token.startswith('IG') else 'https://graph.facebook.com/v21.0')
    params = dict(params or {}, access_token=token)
    data = urllib.parse.urlencode(params).encode() if method == 'POST' else None
    url = f'{graph}/{path}' + ('' if data else '?' + urllib.parse.urlencode(params))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method), timeout=180) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{method} {path}: {e.code} {e.read().decode()[:400]}')


def ig_account():
    if tok().startswith('IG'):
        me = call('GET', 'me', {'fields': 'user_id,username'})
        return {'id': me['user_id'], 'username': me['username']}
    pages = call('GET', 'me/accounts', {'fields': 'name,instagram_business_account{id,username}'})['data']
    ig = next((p['instagram_business_account'] for p in pages if p.get('instagram_business_account')), None)
    if not ig:
        sys.exit('No IG business account linked to any Page this token can see')
    return ig


def fb_page():
    if os.environ.get('FB_PAGE_ID') and os.environ.get('FB_PAGE_TOKEN'):
        return {'id': os.environ['FB_PAGE_ID'], 'token': os.environ['FB_PAGE_TOKEN'], 'name': os.environ['FB_PAGE_ID']}
    if tok().startswith('IG'):
        return None
    pages = call('GET', 'me/accounts', {'fields': 'id,name,access_token,instagram_business_account'})['data']
    p = next((p for p in pages if p.get('instagram_business_account')), pages[0] if pages else None)
    return p and {'id': p['id'], 'token': p['access_token'], 'name': p['name']}


def fbcall(page, method, path, params=None):
    return call(method, path, params, token=page['token'], graph='https://graph.facebook.com/v21.0')


def load():
    if not os.path.exists(PLAN):
        return []
    rows = list(csv.DictReader(open(PLAN, newline='')))
    for r in rows:
        for c in COLS:
            r.setdefault(c, '')
    return rows


def save(rows):
    tmp = PLAN + '.tmp'
    with open(tmp, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, PLAN)


def slot(r):
    return dt.datetime.fromisoformat(f"{r['date']}T{r['time']}").replace(tzinfo=UK)


def url(r, f):
    if not BASE:
        sys.exit('CAROUSEL_PUBLIC_BASE not set')
    return f"{BASE}/{r['id']}/v{r['version']}/{f}"


def items(r):
    return r['media'].split('|')


def wait_ready(cid, tries=100, every=4):
    for _ in range(tries):
        st = call('GET', cid, {'fields': 'status_code'})
        if st.get('status_code') in ('FINISHED', 'ERROR', 'EXPIRED'):
            return st.get('status_code')
        time.sleep(every)
    return 'TIMEOUT'


def post_ig(ig, r):
    kids = []
    for f in items(r):
        if f.endswith('.mp4'):
            c = call('POST', f"{ig['id']}/media", {'media_type': 'VIDEO', 'video_url': url(r, f), 'is_carousel_item': 'true'})
        else:
            c = call('POST', f"{ig['id']}/media", {'image_url': url(r, f), 'is_carousel_item': 'true'})
        if wait_ready(c['id']) != 'FINISHED':
            raise RuntimeError(f'slide container failed: {f}')
        kids.append(c['id'])
    car = call('POST', f"{ig['id']}/media", {'media_type': 'CAROUSEL', 'children': ','.join(kids), 'caption': r['caption']})
    if wait_ready(car['id']) != 'FINISHED':
        raise RuntimeError('carousel container did not finish')
    return call('POST', f"{ig['id']}/media_publish", {'creation_id': car['id']})['id']


def post_fb(page, r):
    ids = []
    for f in items(r):
        still = f[:2] + '.jpg'
        ids.append(fbcall(page, 'POST', f"{page['id']}/photos", {'url': url(r, still), 'published': 'false'})['id'])
    params = {'message': r['caption']}
    for i, pid in enumerate(ids):
        params[f'attached_media[{i}]'] = json.dumps({'media_fbid': pid})
    return fbcall(page, 'POST', f"{page['id']}/feed", params)['id']


def first(s):
    return (s or '').strip().split('\n')[0][:80]


def main():
    rows = load()
    if 'auth' in sys.argv:
        ig = ig_account()
        lim = call('GET', f"{ig['id']}/content_publishing_limit", {'fields': 'quota_usage,config'})
        print(f"Instagram OK @{ig['username']} ({ig['id']}) quota: {lim.get('data')}")
        page = fb_page()
        print(f"Facebook Page OK: {page['name']} ({page['id']})" if page else
              'Facebook: no Page token (IG-login token). Set FB_PAGE_ID + FB_PAGE_TOKEN to post to Facebook.')
        return
    if 'urls' in sys.argv:
        r = next(x for x in rows if x['id'] == sys.argv[sys.argv.index('urls') + 1])
        fs = sorted(set(items(r) + [f[:2] + '.jpg' for f in items(r)]))
        for f in fs:
            try:
                with urllib.request.urlopen(urllib.request.Request(url(r, f), method='HEAD'), timeout=20) as h:
                    print(h.status, h.headers.get('Content-Type'), url(r, f))
            except Exception as e:
                print('FAIL', e, url(r, f))
        return
    for r in rows:
        if 'ready' in (r['status'], r['fb_status']):
            assert 2 <= len(items(r)) <= 10, f"{r['id']}: carousels need 2-10 slides"
            assert r['caption'].strip() and len(r['caption']) <= 2200, f"bad caption for {r['id']}"
    now = dt.datetime.now(UK)
    pending = lambda r: (DO_IG and r['status'] == 'ready') or (DO_FB and r['fb_status'] == 'ready')
    if NOW_ID:
        due = [r for r in rows if r['id'] == NOW_ID and pending(r)]
    else:
        due = sorted([r for r in rows if pending(r) and slot(r) <= now], key=slot)[:MAX_PER_RUN]
    if not GO:
        print(f"Instagram: {sum(r['status'] == 'posted' for r in rows)} posted, {sum(r['status'] == 'ready' for r in rows)} ready · "
              f"Facebook: {sum(r['fb_status'] == 'posted' for r in rows)} posted, {sum(r['fb_status'] == 'ready' for r in rows)} ready")
        print('Due now:', ', '.join(r['id'] for r in due) or 'nothing')
        for r in sorted([r for r in rows if pending(r) and slot(r) > now], key=slot)[:5]:
            print(f"  next: {r['date']} {r['time']}  {r['id']:<6} {first(r['caption'])[:60]}")
        print('Dry run. Add --go to publish what is due.')
        return
    if not due:
        return
    ig = ig_account() if DO_IG and any(r['status'] == 'ready' for r in due) else None
    page = fb_page() if DO_FB and any(r['fb_status'] == 'ready' for r in due) else None
    if DO_FB and any(r['fb_status'] == 'ready' for r in due) and not page:
        log('Facebook skipped: no Page token (set FB_PAGE_ID + FB_PAGE_TOKEN)')
    live_ig = {first(m.get('caption')) for m in call('GET', f"{ig['id']}/media", {'fields': 'caption', 'limit': 100}).get('data', [])} if ig else set()
    live_fb = {first(p.get('message')) for p in fbcall(page, 'GET', f"{page['id']}/posts", {'fields': 'message', 'limit': 100}).get('data', [])} if page else set()
    stamp = lambda: f'{dt.datetime.now(UK):%Y-%m-%d %H:%M}'

    def live(r):  # all media reachable? (GitHub Pages can lag a minute after a push)
        for f in set(items(r) + [f[:2] + '.jpg' for f in items(r)]):
            try:
                with urllib.request.urlopen(urllib.request.Request(url(r, f), method='HEAD'), timeout=20) as h:
                    if h.status != 200:
                        return False
            except Exception:
                return False
        return True

    for r in due:
        if not live(r):
            log(f"{r['id']}: media not live yet at {BASE}, will retry next run")
            continue
        if ig and r['status'] == 'ready':
            if first(r['caption']) in live_ig:
                r['status'], r['posted_at'] = 'posted', 'already on feed'
            else:
                log(f"IG posting {r['id']} v{r['version']} ({len(items(r))} slides, slot {r['date']} {r['time']})")
                try:
                    r['media_id'], r['status'], r['posted_at'] = post_ig(ig, r), 'posted', stamp()
                    log(f"IG posted {r['id']} -> {r['media_id']}")
                except RuntimeError as e:
                    r['status'] = 'failed'
                    log(f"IG FAILED {r['id']}: {e} (set status back to ready to retry)")
            save(rows)
        if page and r['fb_status'] == 'ready':
            if first(r['caption']) in live_fb:
                r['fb_status'], r['fb_posted_at'] = 'posted', 'already on page'
            else:
                log(f"FB posting {r['id']} v{r['version']} to {page['name']}")
                try:
                    r['fb_post_id'], r['fb_status'], r['fb_posted_at'] = post_fb(page, r), 'posted', stamp()
                    log(f"FB posted {r['id']} -> {r['fb_post_id']}")
                except RuntimeError as e:
                    r['fb_status'] = 'failed'
                    log(f"FB FAILED {r['id']}: {e} (set fb_status back to ready to retry)")
            save(rows)


main()
