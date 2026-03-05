import requests
import random
import subprocess
import tempfile
import threading
import hashlib
import re
import time
from flask import Flask, render_template_string, request, jsonify, Response, stream_with_context, send_from_directory, abort, redirect

app = Flask(__name__)
import os
from urllib.parse import quote, urlparse, urljoin

# --- YENİ SUNUCU:---
BASE_URL = os.getenv("IPTV_BASE_URL", "http://xbluex5k.xyz:8080").rstrip("/")
USER = os.getenv("IPTV_USER", "asan8442")
PASS = os.getenv("IPTV_PASS", "6748442")

# TAPINAKÇI'dan aldığımız 'Turbo Session' ayarı
turbo_session = requests.Session()
turbo_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Connection': 'keep-alive'
})


TRANSCODE_ROOT = os.path.join(tempfile.gettempdir(), 'iptv_transcode')
os.makedirs(TRANSCODE_ROOT, exist_ok=True)
TRANSCODE_JOBS = {}
TRANSCODE_LOCK = threading.Lock()


def _is_ffmpeg_available():
    try:
        p = subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return p.returncode == 0
    except Exception:
        return False


def _is_allowed_media_url(target):
    try:
        p = urlparse(target)
        if p.scheme not in ('http', 'https'):
            return False
        # By default allow external CDN/media hosts; set IPTV_STRICT_HOST=1 to lock to BASE_URL host.
        strict = os.getenv('IPTV_STRICT_HOST', '0') == '1'
        if strict:
            base_host = urlparse(BASE_URL).hostname
            if base_host and p.hostname != base_host:
                return False
        return True
    except Exception:
        return False


def _transcode_job_id(target_url):
    return hashlib.sha1(target_url.encode('utf-8')).hexdigest()[:16]


def _start_transcode_job(target_url):
    job_id = _transcode_job_id(target_url)
    job_dir = os.path.join(TRANSCODE_ROOT, job_id)
    playlist = os.path.join(job_dir, 'index.m3u8')

    with TRANSCODE_LOCK:
        job = TRANSCODE_JOBS.get(job_id)
        if job and job.get('proc') and job['proc'].poll() is None:
            return job_id, playlist

        os.makedirs(job_dir, exist_ok=True)
        # prune stale outputs from previous runs
        for name in os.listdir(job_dir):
            try:
                os.remove(os.path.join(job_dir, name))
            except Exception:
                pass

        seg_pattern = os.path.join(job_dir, 'seg_%05d.ts')
        cmd = [
            'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'warning', '-y',
            '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '5',
            '-i', target_url,
            '-map', '0:v:0', '-map', '0:a:0?',
            '-c:v', 'copy', '-c:a', 'aac', '-ac', '2', '-b:a', '128k',
            '-f', 'hls',
            '-hls_time', '4', '-hls_list_size', '8',
            '-hls_flags', 'delete_segments+append_list+omit_endlist',
            '-hls_segment_filename', seg_pattern,
            playlist,
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        TRANSCODE_JOBS[job_id] = {
            'url': target_url,
            'dir': job_dir,
            'playlist': playlist,
            'proc': proc,
            'started': time.time(),
        }

    return job_id, playlist

def get_data(action, extra=""):
    url = f"{BASE_URL}/player_api.php?username={USER}&password={PASS}&action={action}{extra}"
    try:
        r = turbo_session.get(url, timeout=10) # S??reyi biraz uzatt??k
        r.raise_for_status()
        data = r.json()

        # HATA AYIKLAMA: Terminalde ne geldi??ini g??r
        if not data:
            print(f"UYARI: {action} i??in sunucudan bo?? veri geldi.")
        else:
            print(f"BA??ARI: {action} y??klendi. ????e say??s??: {len(data)}")

        return data
    except (requests.RequestException, ValueError) as e:
        print(f"BA??LANTI HATASI: {e}")
        return []


def _newest_key(item):
    # Try several fields that may indicate recency; return int timestamp if possible
    for k in ('added', 'created', 'date', 'timestamp'):
        v = item.get(k)
        if v:
            try:
                return int(v)
            except:
                try:
                    # sometimes ISO-like strings -> fallback to string
                    return v
                except:
                    pass
    # fallback to numeric id fields
    for k in ('stream_id', 'series_id', 'id'):
        v = item.get(k)
        if v:
            try:
                return int(v)
            except:
                return v
    # last resort: name (string)
    return item.get('name','')



def _append_audio_track(tracks, name='', url=''):
    n = str(name or '').strip()
    u = str(url or '').strip()
    if not n and not u:
        return
    if not n:
        n = 'Ses Parcasi'
    key = (n.lower(), u)
    seen = {(x.get('name','').lower(), x.get('url','')) for x in tracks}
    if key not in seen:
        tracks.append({'name': n, 'url': u})


def _scan_audio_payload(payload, tracks):
    if isinstance(payload, dict):
        name = payload.get('name') or payload.get('lang') or payload.get('language') or payload.get('label')
        url = payload.get('url') or payload.get('file') or payload.get('link')
        if name or url:
            _append_audio_track(tracks, name, url)

        for k, v in payload.items():
            kl = str(k).lower()
            if 'audio' in kl or 'lang' in kl or 'dublaj' in kl:
                if isinstance(v, str):
                    parts = [x.strip() for x in v.split(',') if x.strip()]
                    if parts:
                        for x in parts:
                            _append_audio_track(tracks, x, '')
                    else:
                        _append_audio_track(tracks, v, '')
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            _scan_audio_payload(item, tracks)
                        else:
                            _append_audio_track(tracks, str(item), '')
                elif isinstance(v, dict):
                    _scan_audio_payload(v, tracks)

            if isinstance(v, (dict, list)):
                _scan_audio_payload(v, tracks)

    elif isinstance(payload, list):
        for item in payload:
            _scan_audio_payload(item, tracks)


def extract_audio_tracks(mode, item_id='', episode_id=''):
    tracks = []
    _id = str(episode_id or item_id or '').strip()
    if not _id:
        return tracks

    try:
        # 1) Detail endpoints (best source when available)
        if mode == 'movies':
            details = get_data('get_vod_info', f'&vod_id={_id}')
            _scan_audio_payload(details, tracks)
        elif mode == 'series' and item_id:
            details = get_data('get_series_info', f'&series_id={item_id}')
            _scan_audio_payload(details, tracks)

        # 2) Fallback stream list scan
        if mode == 'movies':
            pool = get_data('get_vod_streams') or []
        elif mode == 'live':
            pool = get_data('get_live_streams') or []
        else:
            pool = []

        for it in (pool if isinstance(pool, list) else []):
            sid = str(it.get('stream_id') or it.get('series_id') or it.get('id') or '')
            if sid == _id:
                _scan_audio_payload(it, tracks)
                break
    except Exception as e:
        print('AUDIO TRACK EXTRACT ERROR', e)

    return tracks
        


def _collect_urls_from_payload(payload, out):
    if isinstance(payload, dict):
        for v in payload.values():
            _collect_urls_from_payload(v, out)
        return
    if isinstance(payload, list):
        for x in payload:
            _collect_urls_from_payload(x, out)
        return
    if isinstance(payload, str):
        s = payload.strip()
        if s.startswith('http://') or s.startswith('https://'):
            out.add(s)
            return
        for m in re.findall(r"https?://[^\s\"'<>]+", s):
            out.add(m)


def _build_proxy_candidates(target, mode='', item_id='', series_id=''):
    candidates = []

    def add(u):
        u = (u or '').strip()
        if not u:
            return
        if u not in candidates and _is_allowed_media_url(u):
            candidates.append(u)

    add(target)

    # Host mirror fallback: same path on alternate hosts if configured.
    alt_hosts = [x.strip().rstrip('/') for x in os.getenv('IPTV_ALT_BASES', '').split(',') if x.strip()]
    try:
        pu = urlparse(target)
        if pu.path:
            for ah in alt_hosts:
                add(ah + pu.path)
    except Exception:
        pass

    # API-based URL extraction fallback.
    found = set()
    try:
        if mode == 'movies' and item_id:
            data = get_data('get_vod_info', f'&vod_id={item_id}')
            _collect_urls_from_payload(data, found)
        elif mode == 'series' and series_id:
            data = get_data('get_series_info', f'&series_id={series_id}')
            _collect_urls_from_payload(data, found)
        elif mode == 'live' and item_id:
            pool = get_data('get_live_streams') or []
            for it in (pool if isinstance(pool, list) else []):
                sid = str(it.get('stream_id') or it.get('id') or '')
                if sid == str(item_id):
                    _collect_urls_from_payload(it, found)
                    break
    except Exception as e:
        print('CANDIDATE URL EXTRACT ERROR', e)

    for u in found:
        add(u)

    return candidates[:8]


# ---------- HTML & CSS & JS ----------
LANDING_TEMPLATE = """
<!doctype html>
<html lang="tr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>FIRATFLIX - Keşfet</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        :root{--accent:#e50914;--bg:#080808;--card:#0f0f0f}
        body{background:var(--bg);color:#fff;font-family:Segoe UI, sans-serif;margin:0}
        .nav{height:70px;display:flex;align-items:center;justify-content:space-between;padding:0 36px;background:linear-gradient(180deg, rgba(0,0,0,0.6), transparent)}
        .nav .logo{font-weight:900;color:var(--accent);font-size:28px;text-decoration:none}
        .hero{height:72vh;display:flex;align-items:flex-end;padding:40px;background-size:cover;background-position:center;position:relative;transition:background-image 0.8s ease-in-out}
        .hero::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg, rgba(0,0,0,0.0), rgba(0,0,0,0.9));}
        .hero-inner{position:relative;z-index:2;max-width:1100px}
        .hero h1{font-size:48px;letter-spacing:1px;margin:0 0 12px;text-transform:uppercase}
        .hero p{color:#d2d2d2;max-width:700px}
        .hero .cta{margin-top:18px}
        .hero-controls{margin-top:12px;display:flex;gap:8px;align-items:center}
        .icon-btn-hero{background:rgba(0,0,0,0.45);border:1px solid rgba(255,255,255,0.06);padding:8px;border-radius:6px;color:#fff;cursor:pointer}
        .btn{background:var(--accent);color:#fff;padding:12px 18px;border-radius:6px;text-decoration:none;font-weight:700;margin-right:10px}
        .btn.alt{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.06)}
        .search-bar{margin:18px 0;display:flex;gap:8px}
        .search-bar input{flex:1;padding:12px;border-radius:8px;border:none;background:rgba(255,255,255,0.04);color:#fff}

        .section{padding:26px 36px}
        .section h3{margin:0 0 12px;color:var(--accent);text-transform:uppercase}
        .row{display:flex;gap:12px;overflow:auto;padding-bottom:8px}
        .card{min-width:180px;background:var(--card);border-radius:8px;overflow:hidden;cursor:pointer;flex:0 0 auto}
        .card img{width:100%;height:270px;object-fit:cover;display:block}
        .card .t{padding:8px;font-size:13px;font-weight:700}

        /* simple scrollbar hide */
        .row::-webkit-scrollbar{height:8px}
        .row::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.06);border-radius:8px}

        @media (max-width:900px){.hero h1{font-size:32px}.card img{height:220px}}
    </style>
        <script>
        // ----- TV UYUMLULUK EKLENTİLERİ -----
        let currentModal = null;

        function setFocusToFirstFocusable(modalElement) {
            if (!modalElement) return;
            const focusable = modalElement.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
            if (focusable.length) {
                focusable[0].focus();
            } else {
                modalElement.setAttribute('tabindex', '-1');
                modalElement.focus();
            }
        }

        function trapFocus(event) {
            if (!currentModal) return;
            const key = event.key;
            if (key === 'Escape' || key === 'Back' || key === 'GoBack') {
                event.preventDefault();
                if (currentModal.id === 'landingInfoModal') closeLandingInfo();
                else if (currentModal.id === 'searchModal') closeSearchModal();
            }
        }

        document.addEventListener('keydown', trapFocus);
        // ------------------------------------

        // hero carousel: cycle through random 10 featured covers (from hero_items)
        (function(){
            const items = {{ hero_items | tojson }};
            let idx = 0;
            const hero = document.getElementById('heroCarousel');
            const title = document.getElementById('heroTitle');
            const desc = document.getElementById('heroDesc');
            const play = document.getElementById('heroPlay');
            function show(i){
                const it = items[i];
                if(!it) return;
                hero.style.backgroundImage = `url('${it.img}')`;
                title.innerText = (it.title||'KEŞFEDİN').toUpperCase();
                desc.innerText = (it.desc||'').length>220 ? it.desc.substring(0,220)+'...' : (it.desc||'');
                play.onclick = function(){ playFeatured(it.id, it.title, it.url, it.mode || 'movies'); };
            }
            if(items && items.length){ show(0); setInterval(()=>{ if(!window._heroPaused) { idx = (idx+1)%items.length; show(idx); } }, 4000); }
        })();

        // Search modal behavior (debounced, min 3 chars)
        let _searchTimer = null;
        function openSearchModal(){ 
            document.getElementById('searchModal').style.display='flex'; 
            document.getElementById('searchBox').focus();
            currentModal = document.getElementById('searchModal');
            setFocusToFirstFocusable(currentModal);
        }
        function closeSearchModal(){ 
            document.getElementById('searchModal').style.display='none'; 
            document.getElementById('searchResults').innerHTML='';
            if (currentModal === document.getElementById('searchModal')) currentModal = null;
        }
        function doSearch(force){
            const q = document.getElementById('searchBox').value.trim();
            if(!force){
                if(_searchTimer) clearTimeout(_searchTimer);
                _searchTimer = setTimeout(()=>{ if(q.length>=3) doSearch(true); }, 350);
                return;
            }
            if(q.length<3){ document.getElementById('searchResults').innerHTML='<div style="color:#ddd">Lütfen en az 3 karakter girin.</div>'; return; }
            fetch('/search?q='+encodeURIComponent(q)).then(r=>r.json()).then(data=>{
                const out = document.getElementById('searchResults'); out.innerHTML='';
                if(!data || !data.results || data.results.length===0){ out.innerHTML='<div style="color:#ddd">Sonuç bulunamadı.</div>'; return; }
                data.results.forEach(it => {
                    const div = document.createElement('div'); div.className='card';
                    div.style.cursor='pointer';
                    div.innerHTML = `<img src="${it.img}" loading="lazy"><div class="t">${it.title}</div>`;
                    div.onclick = () => { playFeatured(it.id, it.title, it.url, it.type || 'movies'); };
                    out.appendChild(div);
                });
            }).catch(e=>{ document.getElementById('searchResults').innerHTML='<div style="color:#ddd">Arama hatası</div>'; });
        }
        document.addEventListener('keydown', e => { if(e.key==='Escape'){ closeSearchModal(); } });

        function playFeatured(id, title, url, mode){
    // SECURITY: do NOT pass upstream URL (contains USER/PASS) to the browser.
    const m = mode || 'movies';
    const u = '/player?id=' + encodeURIComponent(id || '') +
              '&title=' + encodeURIComponent(title || '') +
              '&mode=' + encodeURIComponent(m);
    window.location.href = u;
}
// favorite helpers for landing

        function toggleFavLocal(id, title, img){
            const favs = JSON.parse(localStorage.getItem('f_favs')||'[]');
            const idx = favs.findIndex(f=>f.id===id);
            if(idx>-1){ favs.splice(idx,1); } else { favs.push({id,title,img}); }
            localStorage.setItem('f_favs', JSON.stringify(favs));
            alert(idx>-1? 'Favoriden çıkarıldı' : 'Favorilere eklendi');
        }
        // show small info modal on landing
        function openLandingInfo(id,title,desc,img){
            let m = document.getElementById('landingInfoModal');
            document.getElementById('landingInfoTitle').innerText = title.toUpperCase();
            document.getElementById('landingInfoImg').src = img;
            document.getElementById('landingInfoDesc').innerText = desc || 'Açıklama yok.';
            m.style.display = 'flex';
            currentModal = document.getElementById('landingInfoModal');
            setFocusToFirstFocusable(currentModal);
        }
        function closeLandingInfo(){ 
            document.getElementById('landingInfoModal').style.display='none';
            if (currentModal === document.getElementById('landingInfoModal')) currentModal = null;
        }
    </script>
</head>
<body>
    <div class="nav"><div class="logo">FIRATFLIX</div><div></div></div>

    <!-- Left icon sidebar -->
    <div id="leftIcons" style="position:fixed;left:12px;top:40%;display:flex;flex-direction:column;gap:12px;z-index:13000">
        <button title="Filmler" onclick="location.href='/browse?m=movies'" style="width:48px;height:48px;border-radius:10px;background:rgba(0,0,0,0.6);border:1px solid rgba(255,255,255,0.04);color:#fff;cursor:pointer;font-size:18px">🎬</button>
        <button title="Diziler" onclick="location.href='/browse?m=series'" style="width:48px;height:48px;border-radius:10px;background:rgba(0,0,0,0.6);border:1px solid rgba(255,255,255,0.04);color:#fff;cursor:pointer;font-size:18px">📺</button>
        <button title="Canlı TV" onclick="location.href='/browse?m=live'" style="width:48px;height:48px;border-radius:10px;background:rgba(0,0,0,0.6);border:1px solid rgba(255,255,255,0.04);color:#fff;cursor:pointer;font-size:18px">📡</button>
        <button title="Arama" onclick="openSearchModal()" style="width:48px;height:48px;border-radius:10px;background:var(--accent);border:none;color:#fff;cursor:pointer;font-size:18px">🔎</button>
    </div>

    {% set hero = hero_items[0] if hero_items|length>0 else None %}
    <div class="hero" id="heroCarousel" onmouseenter="window._heroPaused=true" onmouseleave="window._heroPaused=false">
        <div class="hero-inner">
            <h1 id="heroTitle">KEŞFEDİN</h1>
            <p id="heroDesc">FIRATFLIX ile en iyi içerikleri keşfedin.</p>
            <div class="cta">
                <a class="btn" id="heroPlay" onclick="">» İZLE</a>
                <a class="btn alt" href="/browse?m=movies">KATALOĞA GİT</a>
            </div>
            <div class="hero-controls">
                <button class="icon-btn-hero" id="heroInfoBtn" onclick="openLandingInfo('','','')">ℹ BİLGİ</button>
                <button class="icon-btn-hero" id="heroFavBtn" onclick="toggleFavLocal('','','')">❤ FAVORİ</button>
            </div>
            <div class="search-bar">
                <input id="heroSearch" placeholder="Film, dizi veya oyuncu ara..." onkeyup="(event.key==='Enter') && (location.href='/browse?m=movies&search='+encodeURIComponent(this.value))">
            </div>
        </div>
    </div>
    <!-- Landing info modal -->
    <div id="landingInfoModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);align-items:center;justify-content:center;z-index:12000">
        <div style="width:90%;max-width:800px;background:#0b0b0b;padding:18px;border-radius:8px;display:flex;gap:12px;">
            <img id="landingInfoImg" src="data:image/svg+xml;utf8,<svg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%20400%20600'><rect%20width='100%25'%20height='100%25'%20fill='%23111'/><text%20x='50%25'%20y='50%25'%20fill='%23fff'%20font-family='Segoe%20UI,%20Arial'%20font-size='24'%20dominant-baseline='middle'%20text-anchor='middle'>Resim%20Yok</text></svg>" style="width:180px;border-radius:6px;object-fit:cover">
            <div style="flex:1;color:#ddd">
                <h2 id="landingInfoTitle" style="color:var(--accent);text-transform:uppercase;margin:0 0 8px"></h2>
                <p id="landingInfoDesc" style="max-height:300px;overflow:auto;margin-bottom:12px"></p>
                <div style="text-align:right"><button class="ep-btn" onclick="closeLandingInfo()">KAPAT</button></div>
            </div>
        </div>
    </div>
        <!-- Search Modal -->
        <div id="searchModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.85);align-items:center;justify-content:center;z-index:13001">
            <div style="width:95%;max-width:900px;background:#0b0b0b;padding:18px;border-radius:8px;color:#fff">
                <div style="display:flex;gap:12px;align-items:center">
                    <input id="searchBox" placeholder="En az 3 karakter girin..." style="flex:1;padding:12px;border-radius:6px;border:1px solid #222;background:#111;color:#fff;font-size:16px">
                    <button class="ep-btn" onclick="doSearch(true)">ARA</button>
                    <button class="ep-btn" onclick="closeSearchModal()">KAPAT</button>
                </div>
                <div id="searchResults" style="margin-top:12px;max-height:60vh;overflow:auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px"></div>
            </div>
        </div>
    <script>
        // hero carousel: cycle through random 10 featured covers (from hero_items)
        (function(){
            const items = {{ hero_items | tojson }};
            let idx = 0;
            const hero = document.getElementById('heroCarousel');
            const title = document.getElementById('heroTitle');
            const desc = document.getElementById('heroDesc');
            const play = document.getElementById('heroPlay');
            function show(i){
                const it = items[i];
                if(!it) return;
                hero.style.backgroundImage = `url('${it.img}')`;
                title.innerText = (it.title||'KEŞFEDİN').toUpperCase();
                desc.innerText = (it.desc||'').length>220 ? it.desc.substring(0,220)+'...' : (it.desc||'');
                play.onclick = function(){ playFeatured(it.id, it.title, it.url, it.mode || 'movies'); };
            }
            if(items && items.length){ show(0); setInterval(()=>{ if(!window._heroPaused) { idx = (idx+1)%items.length; show(idx); } }, 4000); }
        })();
    </script>

    <script>
        // Search modal behavior (debounced, min 3 chars)
        let _searchTimer = null;
        function openSearchModal(){ document.getElementById('searchModal').style.display='flex'; document.getElementById('searchBox').focus(); }
        function closeSearchModal(){ document.getElementById('searchModal').style.display='none'; document.getElementById('searchResults').innerHTML=''; }
        function doSearch(force){
            const q = document.getElementById('searchBox').value.trim();
            if(!force){
                if(_searchTimer) clearTimeout(_searchTimer);
                _searchTimer = setTimeout(()=>{ if(q.length>=3) doSearch(true); }, 350);
                return;
            }
            if(q.length<3){ document.getElementById('searchResults').innerHTML='<div style="color:#ddd">Lütfen en az 3 karakter girin.</div>'; return; }
            fetch('/search?q='+encodeURIComponent(q)).then(r=>r.json()).then(data=>{
                const out = document.getElementById('searchResults'); out.innerHTML='';
                if(!data || !data.results || data.results.length===0){ out.innerHTML='<div style="color:#ddd">Sonuç bulunamadı.</div>'; return; }
                data.results.forEach(it => {
                    const div = document.createElement('div'); div.className='card';
                    div.style.cursor='pointer';
                    div.innerHTML = `<img src="${it.img}" loading="lazy"><div class="t">${it.title}</div>`;
                    div.onclick = () => { playFeatured(it.id, it.title, it.url, it.type || 'movies'); };
                    out.appendChild(div);
                });
            }).catch(e=>{ document.getElementById('searchResults').innerHTML='<div style="color:#ddd">Arama hatası</div>'; });
        }
        document.addEventListener('keydown', e => { if(e.key==='Escape'){ closeSearchModal(); } });
    </script>

    <!-- ÖNE ÇIKANLAR bölümü kaldırıldı; sadece Yeni Eklenenler ve Trendler gösteriliyor -->

    <div class="section">
        <h3>YENİ EKLENENLER</h3>
        <div class="row">
            {% for f in new_additions %}
            <div class="card" onclick='playFeatured({{ f.id|tojson }}, {{ f.title|tojson }}, {{ f.url|tojson }}, {{ f.mode|tojson }})'>
                <img src="{{f.img}}" loading="lazy">
                <div class="t">{{f.title}}</div>
            </div>
            {% endfor %}
        </div>
    </div>

    <div class="section">
        <h3>TRENDLER</h3>
        <div class="row">
            {% for f in trending %}
            <div class="card" onclick='playFeatured({{ f.id|tojson }}, {{ f.title|tojson }}, {{ f.url|tojson }}, {{ f.mode|tojson }})'>
                <img src="{{f.img}}" loading="lazy">
                <div class="t">{{f.title}}</div>
            </div>
            {% endfor %}
        </div>
    </div>

</body>
</html>
"""

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FIRATFLIX Ultimate Pro</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', sans-serif; }
        body { background: #080808; color: #fff; overflow: hidden; }
        
        /* Navbar */
        .top-bar { background: #000; padding: 0 40px; display: flex; align-items: center; justify-content: space-between; height: 70px; border-bottom: 2px solid #e50914; z-index: 1000; position: relative; }
        .logo { font-size: 28px; font-weight: 900; color: #e50914; text-decoration: none; }
        .nav-links a { color: white; text-decoration: none; margin-left: 20px; font-weight: 600; opacity: 0.7; transition: 0.3s; }
        .nav-links a.active { opacity: 1; color: #e50914; }
        .search-input { padding: 8px 15px; border-radius: 20px; border: 1px solid #333; background: #111; color: white; outline: none; width: 250px; }

        /* Ana İçerik */
        .main-container { display: flex; height: calc(100vh - 70px); }
        /* Sidebar will be hidden off-canvas and toggled via a fixed tab */
        .sidebar { width: 260px; background: #000; border-right: 1px solid #1a1a1a; overflow-y: auto; padding: 20px; position:fixed; left:-280px; top:70px; height:calc(100vh - 70px); transition:left 0.28s ease; z-index:1200; }
        body.sidebar-open .sidebar { left: 0; }
        .cat-item { display: block; padding: 12px; color: #888; text-decoration: none; font-size: 14px; border-radius: 6px; margin-bottom: 2px; }
        .cat-item:hover, .active-cat { background: #141414; color: #fff; border-left: 4px solid #e50914; }

        .content { flex: 1; padding: 25px; overflow-y: auto; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 20px; }
        
        .card { background: #111; border-radius: 8px; overflow: hidden; cursor: pointer; transition: 0.3s; position: relative; border: 1px solid #222; }
        .card:hover { transform: scale(1.05); border-color: #e50914; }
        .card img { width: 100%; aspect-ratio: 2/3; object-fit: cover; }
        .card-info { padding: 10px; text-align: center; font-size: 12px; font-weight: bold; }
        
        .fav-btn { position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.7); width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; z-index: 10; }
        .fav-btn.active { color: #e50914; }

        /* MODAL & EPISODES */
        .ep-btn { display: block; width: 100%; padding: 12px; background: #1a1a1a; border: 1px solid #333; color: #fff; text-align: left; margin-bottom: 6px; border-radius: 5px; cursor: pointer; font-size: 13px; }
        .ep-btn:hover { background: #e50914; border-color: #fff; }
        .ep-btn.active { background: #e50914; font-weight: bold; }

        #toast { position: fixed; bottom: 50px; left: 50%; transform: translateX(-50%); background: #e50914; color: white; padding: 10px 20px; border-radius: 20px; z-index: 10001; display: none; }
    </style>
    <style>
        /* Mobile adjustments */
        @media (max-width: 700px) {
            body { overflow: auto; }
            .top-bar { padding: 0 12px; height: 60px; }
            .search-input { width: 140px; }
            .sidebar { left: -100%; position: fixed; }
            body.sidebar-open .sidebar { left: 0; }
            .content { padding: 12px; }
            .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
            .card img { height: 200px; }
            .icon-btn { font-size: 28px; margin: 0 8px; }
            .cat-item { padding: 10px; font-size: 15px; }
            #catToggle { left: 8px; top: auto; bottom: 12px; width:48px; height:48px; }
        }
    </style>
</head>
<body>

    <div id="toast"></div>

    {% if mode in ['movies','series','live'] %}
    <button id="catToggle" onclick="toggleCategories()" title="Kategoriler" style="position:fixed;left:6px;top:50%;transform:translateY(-50%);z-index:13000;width:38px;height:80px;border-radius:8px;background:rgba(0,0,0,0.6);border:1px solid rgba(255,255,255,0.06);color:#fff;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:6px;">
        <span style="font-size:18px">☰</span>
        <small style="font-size:10px">KAT</small>
    </button>
    {% endif %}

    <div class="top-bar">
        <a href="/" class="logo">FIRATFLIX</a>
        <div class="nav-links">
            <a href="/browse?m=live" class="{{ 'active' if mode=='live' }}">CANLI TV</a>
            <a href="/browse?m=movies" class="{{ 'active' if mode=='movies' }}">FİLMLER</a>
            <a href="/browse?m=series" class="{{ 'active' if mode=='series' }}">DİZİLER</a>
            <a href="#" onclick="showFavs()" style="color:#e50914">FAVORİLER</a>
        </div>
        <input type="text" id="searchInput" class="search-input" placeholder="Ara..." onkeyup="filter()">
    </div>

    <div class="main-container">
        <div class="sidebar">
            {% for c in categories %}
            <a href="/browse?m={{mode}}&c={{c.category_id}}" class="cat-item {{ 'active-cat' if cat_id==c.category_id|string }}">{{c.category_name}}</a>
            {% endfor %}
        </div>
        <div class="content">
            <div class="grid" id="mainGrid">
                {% for i in items %}
                <div class="card" data-name="{{ i.name.lower() }}" data-id="{{i.id}}" data-url="{{i.final_url}}" data-mode="{{mode}}" data-icon="{{i.stream_icon}}" data-desc="{{ i.desc|e }}" data-rating="{{ i.rating }}" data-year="{{ i.year }}" data-genre="{{ i.genre|e }}" data-actors="{{ i.actors|e }}" data-imdb="{{ i.imdb }}" data-platform="{{ i.platform|e }}">
                    <div class="fav-btn" onclick="toggleFav(event, '{{i.id}}', '{{i.name|replace("'", "") }}', '{{i.stream_icon}}', '{{mode}}', '{{i.final_url}}')"><i class="fas fa-heart"></i></div>
                    <div onclick="openModal(this)">
                        <img src="{{ i.stream_icon }}" loading="lazy" onerror="this.src='data:image/svg+xml;utf8,<svg%20xmlns=%27http://www.w3.org/2000/svg%27%20viewBox=%270%200%20400%20600%27><rect%20width=%27100%25%27%20height=%27100%25%27%20fill=%27%23111%27/><text%20x=%2750%25%27%20y=%2750%25%27%20fill=%27%23fff%27%20font-family=%27Segoe%20UI,%20Arial%27%20font-size=%2724%27%20dominant-baseline=%27middle%27%20text-anchor=%27middle%27>Resim%20Yok</text></svg>'">
                        <div class="card-info">{{ i.name }}</div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>

    <!-- Bilgi Özeti Modal -->
    <div id="infoModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.7); align-items:center; justify-content:center; z-index:10002;">
        <div style="width:90%; max-width:900px; background:#0b0b0b; border:1px solid #222; border-radius:8px; overflow:hidden; display:flex; gap:20px; padding:20px;">
            <img id="modal-img" src="data:image/svg+xml;utf8,<svg%20xmlns=%27http://www.w3.org/2000/svg%27%20viewBox=%270%200%20400%20600%27><rect%20width=%27100%25%27%20height=%27100%25%27%20fill=%27%23111%27/><text%20x=%2750%25%27%20y=%2750%25%27%20fill=%27%23fff%27%20font-family=%27Segoe%20UI,%20Arial%27%20font-size=%2724%27%20dominant-baseline=%27middle%27%20text-anchor=%27middle%27>Resim%20Yok</text></svg>" style="width:260px; height:auto; object-fit:cover; border-radius:6px;">
            <div style="flex:1; display:flex; flex-direction:column;">
                <h2 id="modal-title" style="color:#e50914; margin-bottom:8px; text-transform:uppercase; font-size:22px; letter-spacing:0.6px;"></h2>
                <div id="modal-meta" style="color:#bbb; margin-bottom:6px; text-transform:uppercase; font-weight:600;"></div>
                <p id="modal-desc" style="color:#ddd; margin-bottom:12px; max-height:180px; overflow:auto;">Açıklama yok.</p>
                <div id="modal-extra" style="color:#ccc; margin-top:8px; font-size:13px; text-transform:uppercase;"></div>
                <div style="margin-top:auto; display:flex; gap:12px;">
                    <button id="modal-watch-btn" class="ep-btn active" style="background:#e50914; text-transform:uppercase;" onclick="watchFromModal()">» İZLE «</button>
                    <button class="ep-btn" onclick="closeModal()">✖ İPTAL</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Sezon Seçim Modal (Diziler için) -->
    <div id="seasonsModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.7); align-items:center; justify-content:center; z-index:10003;">
        <div style="width:90%; max-width:700px; background:#0b0b0b; border:1px solid #222; border-radius:8px; overflow:hidden; padding:20px;">
            <h3 style="color:#e50914; margin-bottom:10px; text-transform:uppercase;">✦ SEZON SEÇİN ✦</h3>
            <div id="seasonsList" style="display:flex; flex-wrap:wrap; gap:8px; max-height:60vh; overflow:auto;"></div>
            <div style="margin-top:12px; text-align:right;"><button class="ep-btn" onclick="closeSeasonsModal()">Kapat</button></div>
        </div>
    </div>

    <!-- Bölüm Seçim Modal (Sezon içi bölümler) -->
    <div id="episodesModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.7); align-items:center; justify-content:center; z-index:10004;">
        <div style="width:90%; max-width:700px; background:#0b0b0b; border:1px solid #222; border-radius:8px; overflow:hidden; padding:20px;">
            <h3 id="episodesTitle" style="color:#e50914; margin-bottom:10px; text-transform:uppercase;">✦ BÖLÜMLER ✦</h3>
            <div id="episodesList" style="display:flex; flex-direction:column; gap:8px; max-height:60vh; overflow:auto;"></div>
            <div style="margin-top:12px; text-align:right;"><button class="ep-btn" onclick="closeEpisodesModal()">Kapat</button></div>
        </div>
    </div>

      <script>
        // ----- TV UYUMLULUK EKLENTİLERİ -----
        let currentModal = null;

        function setFocusToFirstFocusable(modalElement) {
            if (!modalElement) return;
            const focusable = modalElement.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
            if (focusable.length) {
                focusable[0].focus();
            } else {
                modalElement.setAttribute('tabindex', '-1');
                modalElement.focus();
            }
        }

        function trapFocus(event) {
            if (!currentModal) return;
            const key = event.key;
            if (key === 'Escape' || key === 'Back' || key === 'GoBack') {
                event.preventDefault();
                if (currentModal.id === 'infoModal') closeModal();
                else if (currentModal.id === 'seasonsModal') closeSeasonsModal();
                else if (currentModal.id === 'episodesModal') closeEpisodesModal();
                else if (currentModal.id === 'searchModal') closeSearchModal();
            }
        }

        document.addEventListener('keydown', trapFocus);
        // ------------------------------------

        let currentId = "";

        // İLERLEME VE FAVORİ
        function checkProgress(id) {
            try{
                let prog = JSON.parse(localStorage.getItem('f_prog') || '{}');
                if(prog[id] > 5) localStorage.setItem('f_resume_id', id);
            }catch(e){}
        }

        let favs = JSON.parse(localStorage.getItem('f_favs') || '[]');
        function toggleFav(e, id, name, icon, mode, url) {
            e.stopPropagation();
            let idx = favs.findIndex(f => f.id === id);
            if(idx > -1) favs.splice(idx, 1);
            else favs.push({id, name, icon, mode, url});
            localStorage.setItem('f_favs', JSON.stringify(favs));
            location.reload(); 
        }

        // MODAL İŞLEMLERİ
        function openModal(el) {
            const card = el.closest('.card');
            const id = card.getAttribute('data-id');
            const name = card.getAttribute('data-name');
            const url = card.getAttribute('data-url');
            const mode = card.getAttribute('data-mode');
            const icon = card.getAttribute('data-icon') || "data:image/svg+xml;utf8,<svg%20xmlns=%27http://www.w3.org/2000/svg%27%20viewBox=%270%200%20400%20600%27><rect%20width=%27100%25%27%20height=%27100%25%27%20fill=%27%23111%27/><text%20x=%2750%25%27%20y=%2750%25%27%20fill=%27%23fff%27%20font-family=%27Segoe%20UI,%20Arial%27%20font-size=%2724%27%20dominant-baseline=%27middle%27%20text-anchor=%27middle%27>Resim%20Yok</text></svg>";
            document.getElementById('modal-title').innerText = name.toUpperCase();
            document.getElementById('modal-img').src = icon;
            document.getElementById('modal-desc').innerText = card.getAttribute('data-desc') || 'Açıklama mevcut değil.';
            
            const genre = card.getAttribute('data-genre') || '';
            let year = card.getAttribute('data-year') || '';
            const rating = card.getAttribute('data-rating') || '';
            const imdb = card.getAttribute('data-imdb') || '';
            
            let metaParts = [];
            if(genre) metaParts.push(genre);
            if(year) metaParts.push(year);
            if(imdb || rating) metaParts.push('IMDb: ' + (imdb || rating));
            document.getElementById('modal-meta').innerText = metaParts.join(' • ');
            
            const btn = document.getElementById('modal-watch-btn');
            btn.dataset.id = id; btn.dataset.mode = mode; btn.dataset.url = url; btn.dataset.name = name;
            document.getElementById('infoModal').style.display = 'flex';
            currentModal = document.getElementById('infoModal');
            setFocusToFirstFocusable(currentModal);
        }

        function closeModal() { 
            document.getElementById('infoModal').style.display = 'none';
            if (currentModal === document.getElementById('infoModal')) currentModal = null;
        }

        async function watchFromModal() {
            const btn = document.getElementById('modal-watch-btn');
            const id = btn.dataset.id; const mode = btn.dataset.mode; const url = btn.dataset.url; const name = btn.dataset.name;
            closeModal();
            if(mode === 'series') {
                const res = await fetch('/get_series_details/' + id);
                const data = await res.json();
                openSeasonsModal(data, id, name);
            } else {
                window.location.href = '/player?id=' + encodeURIComponent(id || '') + '&title=' + encodeURIComponent(name || '') + '&mode=' + encodeURIComponent(mode || 'movies');
            }
        }

        function openSeasonsModal(seriesData, seriesId, seriesName) {
            const list = document.getElementById('seasonsList');
            list.innerHTML = '';
            if(!seriesData || !seriesData.seasons || seriesData.seasons.length === 0) {
                list.innerHTML = '<div style="color:#ddd">Sezon bulunamadı.</div>';
            } else {
                seriesData.seasons.forEach(s => {
                    const b = document.createElement('button');
                    b.className = 'ep-btn';
                    b.innerText = 'Sezon ' + s.season_num + ' (' + (s.episodes.length||0) + ' bölüm)';
                    b.onclick = () => openEpisodesModal(s, seriesName, seriesId);
                    list.appendChild(b);
                });
            }
            document.getElementById('seasonsModal').style.display = 'flex';
            currentModal = document.getElementById('seasonsModal');
            setFocusToFirstFocusable(currentModal);
        }

        function closeSeasonsModal() { 
            document.getElementById('seasonsModal').style.display = 'none';
            if (currentModal === document.getElementById('seasonsModal')) currentModal = null;
        }

        function openEpisodesModal(season, seriesName, seriesId) {
            const list = document.getElementById('episodesList');
            list.innerHTML = '';
            document.getElementById('episodesTitle').innerText = 'Bölümler - Sezon ' + season.season_num;
            season.episodes.forEach(e => {
                const b = document.createElement('button');
                b.className = 'ep-btn';
                b.innerText = 'Bölüm ' + (e.num || '') + ' - ' + (e.title || 'Bölüm');
                b.onclick = () => {
                    window.location.href = '/player?id=' + encodeURIComponent(e.id || '') + '&series_id=' + encodeURIComponent(seriesId || '') + '&title=' + encodeURIComponent(seriesName + ' - S' + season.season_num + 'E' + (e.num || '')) + '&mode=series';
                };
                list.appendChild(b);
            });
            document.getElementById('episodesModal').style.display = 'flex';
            currentModal = document.getElementById('episodesModal');
            setFocusToFirstFocusable(currentModal);
        }

        function closeEpisodesModal() { 
            document.getElementById('episodesModal').style.display = 'none';
            if (currentModal === document.getElementById('episodesModal')) currentModal = null;
        }

        function filter() {
            let val = document.getElementById('searchInput').value.toLowerCase();
            document.querySelectorAll('.card').forEach(c => c.style.display = c.getAttribute('data-name').includes(val) ? "block" : "none");
        }

        function toggleCategories(){
            document.body.classList.toggle('sidebar-open');
            const btn = document.getElementById('catToggle');
            btn.innerHTML = document.body.classList.contains('sidebar-open') ? '<span style="font-size:18px">✖</span><small style="font-size:10px">KAPAT</small>' : '<span style="font-size:18px">☰</span><small style="font-size:10px">KAT</small>';
        }

        document.addEventListener('DOMContentLoaded', function(){
            const aid = localStorage.getItem('autoplay_id');
            if(aid){
                const card = document.querySelector('.card[data-id="'+aid+'"]');
                if(card) openModal(card);
                localStorage.removeItem('autoplay_id');
            }
        });
    </script>
</body>
</html>
"""

@app.route('/browse')
def home():
    mode = request.args.get('m', 'movies')
    cat_id = request.args.get('c', '')
    if mode == 'live': categories = get_data("get_live_categories"); action = "get_live_streams"; path = "live"
    elif mode == 'series': categories = get_data("get_series_categories"); action = "get_series"; path = "series"
    else: categories = get_data("get_vod_categories"); action = "get_vod_streams"; path = "movie"

    raw_data = get_data(action, f"&category_id={cat_id}" if cat_id else "")
    # sort newest-first when possible
    if isinstance(raw_data, list) and raw_data:
        try:
            raw_data = sorted(raw_data, key=_newest_key, reverse=True)
        except Exception:
            pass
    items = []
    if isinstance(raw_data, list):
        for i in raw_data[:150]:
            sid = str(i.get('stream_id') or i.get('series_id') or i.get('id') or '')
            ext = i.get('container_extension', 'mp4') if mode != 'live' else 'ts'
            url = f"{BASE_URL}/{path}/{USER}/{PASS}/{sid}.{ext}" if mode != 'series' else ""
            # extra fields (best-effort from API response)
            desc = i.get('description') or i.get('info') or i.get('plot') or i.get('detail') or ''
            rating = i.get('rating') or i.get('rating_5based') or i.get('imdb_rating') or ''
            year = i.get('year') or i.get('created') or i.get('added') or ''
            genre = i.get('genre') or i.get('category_name') or ''
            # try to extract actors (can be list or comma string)
            actors_raw = i.get('actors') or i.get('cast') or i.get('stars') or i.get('actors_list') or ''
            if isinstance(actors_raw, list):
                actors = ', '.join(actors_raw[:3])
            else:
                # take first 2 names if comma-separated
                actors = ', '.join([a.strip() for a in str(actors_raw).split(',')[:3]]) if actors_raw else ''
            platform = i.get('platform') or i.get('service') or i.get('source') or ''
            items.append({
                'id': sid,
                'name': i.get('name', 'İsimsiz'),
                'stream_icon': i.get('stream_icon') or i.get('cover') or '',
                'final_url': url,
                'desc': desc,
                'rating': rating,
                'year': year,
                'genre': genre,
                'actors': actors,
                'imdb': rating,
                'platform': platform
            })
    return render_template_string(HTML_TEMPLATE, categories=categories, items=items, mode=mode, cat_id=cat_id)


@app.route('/')
def index():
    # get some featured VOD items to populate landing carousels
    raw = get_data('get_vod_streams')
    featured, hero_items, trending, new_additions = [], [], [], []

    if isinstance(raw, list) and raw:
        try:
            raw_sorted = sorted(raw, key=_newest_key, reverse=True)
        except Exception:
            raw_sorted = raw

        # hero: random 10 from top pool
        pool = raw_sorted[:100]
        if pool:
            for i in random.sample(pool, min(10, len(pool))):
                sid = str(i.get('stream_id') or i.get('series_id') or i.get('id') or '')
                ext = i.get('container_extension', 'mp4')
                stream_url = f"{BASE_URL}/movie/{USER}/{PASS}/{sid}.{ext}"
                hero_items.append({
                    'id': sid,
                    'title': i.get('name', '??simsiz'),
                    'img': i.get('stream_icon') or i.get('cover') or "",
                    'desc': i.get('description') or i.get('plot') or '',
                    'url': stream_url,
                    'mode': 'movies'
                })

        # Lists: New additions and general pool
        for i in raw_sorted[:12]:
            sid = str(i.get('stream_id') or i.get('series_id') or i.get('id') or '')
            ext = i.get('container_extension', 'mp4')
            stream_url = f"{BASE_URL}/movie/{USER}/{PASS}/{sid}.{ext}"
            item = {
                'id': sid,
                'title': i.get('name', '??simsiz'),
                'img': i.get('stream_icon') or i.get('cover') or "",
                'desc': i.get('description') or i.get('plot') or '',
                'url': stream_url,
                'mode': 'movies'
            }
            featured.append(item)
            new_additions.append(item)

        # Trending by rating
        trending_pool = sorted([x for x in raw_sorted if x.get('rating')], key=lambda z: float(z.get('rating') or 0), reverse=True)
        for i in (trending_pool[:12] if trending_pool else raw_sorted[12:24]):
            sid = str(i.get('stream_id') or i.get('series_id') or i.get('id') or '')
            ext = i.get('container_extension', 'mp4')
            stream_url = f"{BASE_URL}/movie/{USER}/{PASS}/{sid}.{ext}"
            trending.append({
                'id': sid,
                'title': i.get('name', '??simsiz'),
                'img': i.get('stream_icon') or i.get('cover') or "",
                'url': stream_url,
                'mode': 'movies'
            })

    return render_template_string(LANDING_TEMPLATE, featured=featured, hero_items=hero_items, trending=trending, new_additions=new_additions)


@app.route('/get_series_details/<sid>')
def get_series_details(sid):
    data = get_data("get_series_info", f"&series_id={sid}")
    res = {"seasons": []}
    if not data or "episodes" not in data: return jsonify(res)
    season_keys = sorted(
        data["episodes"].keys(),
        key=lambda x: (0, int(str(x))) if str(x).isdigit() else (1, str(x))
    )
    for s_num in season_keys:
        season = {"season_num": s_num, "episodes": []}
        # sort episodes newest-first if episode_num exists
        eps = data["episodes"][s_num]
        try:
            eps_sorted = sorted(eps, key=lambda e: int(e.get('episode_num') or e.get('episode_id') or 0), reverse=True)
        except Exception:
            eps_sorted = list(eps)
        for ep in eps_sorted:
            eid = ep.get('id') or ep.get('episode_id')
            season["episodes"].append({"id": str(eid), "num": ep.get('episode_num'), "title": ep.get('title'), "url": f"{BASE_URL}/series/{USER}/{PASS}/{eid}.{ep.get('container_extension', 'mp4')}"})
        res["seasons"].append(season)
    return jsonify(res)


@app.route('/search')
def search():
    q = request.args.get('q','').strip().lower()
    res = {'results': []}
    if not q or len(q) < 1:
        return jsonify(res)
    # search VOD, series and live (best-effort, limited scan)
    try:
        vod = get_data('get_vod_streams') or []
        series = get_data('get_series') or []
        live = get_data('get_live_streams') or []
        combined = []
        # limit scans to avoid huge payloads
        for v in (vod[:200] if isinstance(vod, list) else []):
            name = (v.get('name') or '').lower()
            if q in name:
                sid = str(v.get('stream_id') or v.get('series_id') or v.get('id') or '')
                img = v.get('stream_icon') or v.get('cover') or ''
                ext = v.get('container_extension','mp4')
                url = f"{BASE_URL}/movie/{USER}/{PASS}/{sid}.{ext}"
                combined.append({'id':sid,'title':v.get('name'),'img':img,'type':'movie','url':url})
        for s in (series[:200] if isinstance(series, list) else []):
            name = (s.get('name') or '').lower()
            if q in name:
                sid = str(s.get('series_id') or s.get('id') or '')
                img = s.get('stream_icon') or s.get('cover') or ''
                combined.append({'id':sid,'title':s.get('name'),'img':img,'type':'series','url':''})
        for l in (live[:200] if isinstance(live, list) else []):
            name = (l.get('name') or '').lower()
            if q in name:
                sid = str(l.get('stream_id') or '')
                img = l.get('stream_icon') or ''
                combined.append({'id':sid,'title':l.get('name'),'img':img,'type':'live','url':f"{BASE_URL}/live/{USER}/{PASS}/{sid}.ts"})
        # take top 48 results
        res['results'] = combined[:48]
    except Exception as e:
        print('SEARCH ERROR', e)
    return jsonify(res)


PLAYER_TEMPLATE = """
<!doctype html>
<html lang="tr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{{ title or 'Player' }}</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>
        :root{
            --bg:#050505;
            --panel:#0d0d0dcc;
            --line:#2a2a2a;
            --text:#f4f4f4;
            --muted:#b9b9b9;
            --brand:#e50914;
            --brand2:#ff2b3a;
            --focus:#6fd8ff;
        }
        html,body{height:100%;margin:0;background:var(--bg);color:var(--text);font-family:Segoe UI,Arial,sans-serif;overflow:hidden}
        .screen{position:fixed;inset:0;background:#000}
        #video{width:100%;height:100%;background:#000;object-fit:contain}

        .top{position:fixed;left:0;right:0;top:0;padding:18px 24px;background:linear-gradient(180deg,rgba(0,0,0,.78),rgba(0,0,0,0));z-index:20;pointer-events:none}
        .brand{display:inline-block;font-weight:900;letter-spacing:1px;color:var(--brand);font-size:26px;margin-right:14px}
        .title{display:inline-block;font-weight:700;font-size:26px;color:#fff;max-width:78vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;vertical-align:middle}

        .hud{position:fixed;left:24px;right:24px;bottom:24px;background:var(--panel);border:1px solid var(--line);z-index:30;padding:14px 16px 12px;backdrop-filter:blur(4px)}
        .progress-wrap{display:flex;align-items:center;gap:12px}
        .time{min-width:96px;text-align:center;font-size:20px;color:#fff}
        .progress{height:12px;background:#2f2f2f;position:relative;flex:1;cursor:pointer;border-radius:2px}
        .progress>.buf{position:absolute;left:0;top:0;bottom:0;width:0;background:#666;border-radius:2px}
        .progress>.bar{position:absolute;left:0;top:0;bottom:0;width:0;background:linear-gradient(90deg,var(--brand),var(--brand2));border-radius:2px}

        .controls{margin-top:12px;display:flex;gap:10px;align-items:center;justify-content:space-between}
        .btns{display:flex;gap:10px;align-items:center}
        .btn{height:48px;min-width:64px;padding:0 14px;border:1px solid #2d2d2d;background:#0a0a0a;color:#fff;font-size:17px;font-weight:700;cursor:pointer}
        .btn:hover{border-color:#4a4a4a}
        .btn:focus{outline:2px solid var(--focus);outline-offset:1px}
        .btn.primary{background:var(--brand);border-color:var(--brand)}
        .status{color:var(--muted);font-size:16px;min-width:280px;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}


        #bigPlay{position:fixed;inset:0;display:none;align-items:center;justify-content:center;z-index:35}
        #bigPlay button{width:112px;height:112px;border-radius:56px;border:2px solid #ffffff55;background:#000000a8;color:#fff;font-size:40px;cursor:pointer}

        #err{position:fixed;inset:0;display:none;align-items:center;justify-content:center;background:#0000009a;z-index:60}
        #err .box{max-width:760px;background:#111;border:1px solid var(--brand);padding:18px}
        #err h3{margin:0 0 10px 0;color:var(--brand)}
        #err p{margin:0 0 12px 0;color:#ddd}
        #err .act{display:flex;gap:10px}

        .hide-ui .top,.hide-ui .hud{opacity:0;pointer-events:none;transition:opacity .25s ease}
    </style>
</head>
<body>
    <div class="screen"><video id="video" playsinline></video></div>

    <div class="top">
        <span class="brand">FIRATFLIX</span>
        <span class="title">{{ title or '' }}</span>
    </div>

    <div class="hud" id="hud">
        <div class="progress-wrap">
            <div class="time" id="cur">00:00</div>
            <div class="progress" id="progress"><div class="buf" id="buf"></div><div class="bar" id="bar"></div></div>
            <div class="time" id="dur">00:00</div>
        </div>
        <div class="controls">
            <div class="btns">
                <button class="btn primary" id="playBtn">Play</button>
                <button class="btn" id="backBtn">-10s</button>
                <button class="btn" id="fwdBtn">+10s</button>
                <button class="btn" id="muteBtn">Mute</button>
                <button class="btn" id="pipBtn">PiP</button>
                <button class="btn" id="fsBtn">FS</button>
            </div>
            <div class="status" id="status">Hazir</div>
        </div>
    </div>

    <div id="bigPlay"><button id="bigPlayBtn">></button></div>

    <div id="err">
        <div class="box">
            <h3>Oynatma Hatasi</h3>
            <p id="errMsg">Bilinmeyen hata.</p>
            <div class="act">
                <button class="btn" id="retryBtn">Tekrar Dene</button>
                <button class="btn" id="copyBtn">URL Kopyala</button>
            </div>
        </div>
    </div>

    <script>
        const playUrl = {{ playback_url|tojson if playback_url is defined else url|tojson }};
        const rawUrl = playUrl;
        const title = {{ title|tojson }};
        const itemId = {{ request.args.get('id','')|tojson }};

        const video = document.getElementById('video');
        video.preload = 'auto';
        const playBtn = document.getElementById('playBtn');
        const backBtn = document.getElementById('backBtn');
        const fwdBtn = document.getElementById('fwdBtn');
        const muteBtn = document.getElementById('muteBtn');
        const pipBtn = document.getElementById('pipBtn');
        const fsBtn = document.getElementById('fsBtn');
        const progress = document.getElementById('progress');
        const bar = document.getElementById('bar');
        const buf = document.getElementById('buf');
        const cur = document.getElementById('cur');
        const dur = document.getElementById('dur');
        const status = document.getElementById('status');
        const hud = document.getElementById('hud');
        const bigPlay = document.getElementById('bigPlay');
        const bigPlayBtn = document.getElementById('bigPlayBtn');
        const err = document.getElementById('err');
        const errMsg = document.getElementById('errMsg');
        const retryBtn = document.getElementById('retryBtn');
        const copyBtn = document.getElementById('copyBtn');

        const ua = navigator.userAgent || '';
        const isChrome = ua.includes('Chrome/') && !ua.includes('OPR/') && !ua.includes('Edg/');

        let hls = null;
        let uiTimer = null;
        let playbackStarted = false;
        let playPromise = null;
        let triedRetry = false;
        let usingProxy = false;

        function fmt(s){
            if(!s || isNaN(s)) return '00:00';
            const h = Math.floor(s/3600);
            const m = Math.floor((s%3600)/60);
            const x = Math.floor(s%60);
            if(h>0) return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(x).padStart(2,'0');
            return String(m).padStart(2,'0')+':'+String(x).padStart(2,'0');
        }

        function setStatus(msg){ status.textContent = msg || ''; }

        function showErr(msg){
            errMsg.textContent = msg || 'Bilinmeyen hata';
            err.style.display = 'flex';
            setStatus('Hata');
        }

        function hideErr(){ err.style.display = 'none'; }

        function saveProgress(){
            try{
                const key = itemId || rawUrl;
                const o = JSON.parse(localStorage.getItem('f_prog') || '{}');
                o[key] = Math.floor(video.currentTime || 0);
                localStorage.setItem('f_prog', JSON.stringify(o));
            }catch(e){}
        }

        function restoreProgress(){
            try{
                const key = itemId || rawUrl;
                const o = JSON.parse(localStorage.getItem('f_prog') || '{}');
                const t = o[key];
                if(t && t > 8) video.currentTime = t;
            }catch(e){}
        }

        function updatePlayLabel(){
            playBtn.textContent = video.paused ? 'Play' : 'Pause';
            muteBtn.textContent = video.muted ? 'Unmute' : 'Mute';
        }

        function hideUiLater(){
            clearTimeout(uiTimer);
            document.body.classList.remove('hide-ui');
            if(!video.paused){
                uiTimer = setTimeout(()=>document.body.classList.add('hide-ui'), 4500);
            }
        }

        function bindWake(){
            ['mousemove','click','keydown','touchstart'].forEach(evt=>{
                document.addEventListener(evt, hideUiLater, true);
            });
        }

        function attachMedia(){
    if(!playUrl){ showErr('Oynatilacak URL yok'); return; }

    const isM3u8 = String(playUrl).includes('.m3u8');
    // Always use proxy playback URL on HTTPS to avoid Mixed Content.
    usingProxy = true;

    if(isM3u8 && window.Hls && Hls.isSupported()){
        hls = new Hls();
        hls.loadSource(playUrl);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, ()=>{
            restoreProgress();
            startPlay();
            setStatus('HLS (proxy) baglandi');
        });
        hls.on(Hls.Events.ERROR, (_, data)=>{
            if(data && data.fatal){
                showErr('HLS hata: ' + (data.details || 'bilinmeyen'));
            }
        });
    }else{
        video.src = playUrl;
        video.load();
        video.addEventListener('loadedmetadata', ()=>{
            restoreProgress();
            startPlay();
            setStatus('Kaynak hazir (proxy)');
        }, {once:true});
    }
}


function unlockAudioOnce
(){
            var done = false;
            function go(){
                if(done) return;
                done = true;
                try{
                    video.muted = false;
                    if(!video.volume || video.volume < 0.1) video.volume = 1.0;
                    var p = video.play();
                    if(p && p.then) p.catch(function(){});
                }catch(e){}
                document.removeEventListener('click', go, true);
                document.removeEventListener('keydown', go, true);
                document.removeEventListener('touchstart', go, true);
            }
            document.addEventListener('click', go, true);
            document.addEventListener('keydown', go, true);
            document.addEventListener('touchstart', go, true);
        }


        function safePlay(){
            if(playPromise) return playPromise;
            try{
                const p = video.play();
                if(p && p.then){
                    playPromise = p.then(()=>{ hideErr(); hideUiLater(); updatePlayLabel(); return true; }, (e)=>{ throw e; });
                    playPromise.then(()=>{ playPromise = null; }, ()=>{ playPromise = null; });
                    return playPromise;
                }
            }catch(e){
                return Promise.reject(e);
            }
            return Promise.resolve();
        }

        function startPlay(){
            safePlay().catch(()=>{ bigPlay.style.display='flex'; setStatus('Play tusuna basin'); });
        }

        bigPlayBtn.onclick = ()=>{
            bigPlay.style.display = 'none';
            safePlay().catch(()=>{});
            updatePlayLabel();
        };

        playBtn.onclick = ()=>{
            if(video.paused){
                try{ video.muted = false; if(!video.volume || video.volume < 0.1) video.volume = 1.0; }catch(e){}
                safePlay().catch(e=>showErr('Oynatma baslatilamadi: ' + (e && e.message ? e.message : e)));
            }else{
                if(playPromise){
                    playPromise.then(()=>video.pause()).catch(()=>video.pause());
                }else{
                    video.pause();
                }
            }
            updatePlayLabel();
        };

        backBtn.onclick = ()=>{ video.currentTime = Math.max(0, (video.currentTime||0) - 10); };
        fwdBtn.onclick = ()=>{ video.currentTime = Math.min(video.duration || 0, (video.currentTime||0) + 10); };

        muteBtn.onclick = ()=>{
            video.muted = !video.muted;
            if(!video.muted && video.volume < 0.1) video.volume = 1.0;
            updatePlayLabel();
        };

        pipBtn.onclick = async ()=>{
            try{
                if(document.pictureInPictureElement) await document.exitPictureInPicture();
                else if(video.requestPictureInPicture) await video.requestPictureInPicture();
            }catch(e){}
        };

        fsBtn.onclick = ()=>{
            if(document.fullscreenElement) document.exitFullscreen();
            else document.documentElement.requestFullscreen();
        };

        progress.onclick = (e)=>{
            const r = progress.getBoundingClientRect();
            const pct = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
            if(video.duration) video.currentTime = pct * video.duration;
        };

        retryBtn.onclick = ()=>{ location.reload(); };
        copyBtn.onclick = ()=>{
            const txt = rawUrl || playUrl || '';
            try{ navigator.clipboard.writeText(txt); setStatus('URL kopyalandi'); }
            catch(e){ prompt('URL:', txt); }
        };

        video.addEventListener('timeupdate', ()=>{
            if(video.duration){
                const pct = (video.currentTime / video.duration) * 100;
                bar.style.width = pct + '%';
                cur.textContent = fmt(video.currentTime);
                dur.textContent = fmt(video.duration);
            }
        });

        video.addEventListener('progress', ()=>{
            try{
                if(video.duration && video.buffered && video.buffered.length){
                    const end = video.buffered.end(video.buffered.length - 1);
                    buf.style.width = Math.min(100, (end / video.duration) * 100) + '%';
                }
            }catch(e){}
        });

        video.addEventListener('playing', ()=>{ setStatus('Oynuyor'); updatePlayLabel(); hideUiLater(); });
        video.addEventListener('pause', ()=>{ setStatus('Duraklatildi'); updatePlayLabel(); document.body.classList.remove('hide-ui'); });
        video.addEventListener('error', function(){
    var c = video.error && video.error.code ? video.error.code : '';
    if(!triedRetry){
        triedRetry = true;
        setStatus('Tekrar deneniyor...');
        try{
            var base = playUrl;
            var withBust = base + (base.indexOf('?') >= 0 ? '&' : '?') + '_r=' + Date.now();
            if(hls){
                try{ hls.destroy(); }catch(e){}
                hls = null;
            }
            video.src = withBust;
            video.load();
            startPlay();
            return;
        }catch(e){}
    }
    showErr('Tarayici medya hatasi' + (c ? (': ' + c) : ''));
});


        setInterval(()=>{ if(!video.paused) saveProgress(); }, 5000);

        document.addEventListener('keydown', (e)=>{
            if(e.code === 'Space' || e.code === 'Enter'){ e.preventDefault(); playBtn.click(); }
            else if(e.code === 'ArrowLeft'){ e.preventDefault(); backBtn.click(); }
            else if(e.code === 'ArrowRight'){ e.preventDefault(); fwdBtn.click(); }
            else if(e.code === 'ArrowUp'){ e.preventDefault(); video.volume = Math.min(1, (video.volume||1) + 0.1); video.muted = false; updatePlayLabel(); }
            else if(e.code === 'ArrowDown'){ e.preventDefault(); video.volume = Math.max(0, (video.volume||1) - 0.1); if(video.volume===0) video.muted=true; updatePlayLabel(); }
            else if(e.key === 'm' || e.key === 'M'){ muteBtn.click(); }
            else if(e.key === 'f' || e.key === 'F'){ fsBtn.click(); }
        });

        bindWake();
        unlockAudioOnce();
        attachMedia();
        updatePlayLabel();
    </script>
</body>
</html>
"""

@app.route('/proxy')
def proxy_stream():
    target = (request.args.get('url') or '').strip()
    if not target:
        return 'Eksik URL', 400

    parsed = urlparse(target)
    if parsed.scheme not in ('http', 'https'):
        return 'Gecersiz URL semasi', 400
    if not _is_allowed_media_url(target):
        return 'Erisim engellendi', 403

    mode = (request.args.get('mode') or '').strip()
    item_id = (request.args.get('id') or '').strip()
    series_id = (request.args.get('series_id') or '').strip()
    candidate_urls = _build_proxy_candidates(target, mode=mode, item_id=item_id, series_id=series_id)

    base_headers = {
        'User-Agent': request.headers.get('User-Agent', turbo_session.headers.get('User-Agent', 'Mozilla/5.0')),
        'Accept': request.headers.get('Accept', '*/*'),
        'Connection': 'close',
        'Accept-Encoding': 'identity',
        'Referer': BASE_URL + '/',
        'Origin': BASE_URL,
    }
    range_header = request.headers.get('Range')
    if range_header:
        base_headers['Range'] = range_header

    passthrough = [
        'Content-Type', 'Content-Range', 'Content-Length',
        'Accept-Ranges', 'Cache-Control', 'ETag', 'Last-Modified'
    ]

    if range_header:
        last_err = None
        for u in candidate_urls:
            for _ in range(2):
                upstream = None
                try:
                    upstream = turbo_session.get(
                        u,
                        headers=base_headers,
                        stream=False,
                        timeout=(10, 120),
                        allow_redirects=True
                    )
                    if upstream.status_code >= 500:
                        last_err = f'status={upstream.status_code} url={u}'
                        continue
                    data = upstream.content
                    response_headers = {}
                    for h in passthrough:
                        v = upstream.headers.get(h)
                        if v:
                            response_headers[h] = v
                    response_headers['Content-Length'] = str(len(data))
                    if 'Accept-Ranges' not in response_headers:
                        response_headers['Accept-Ranges'] = 'bytes'
                    return Response(data, status=upstream.status_code, headers=response_headers)
                except requests.RequestException as e:
                    last_err = e
                finally:
                    if upstream is not None:
                        upstream.close()
        if candidate_urls:
            return redirect(candidate_urls[0], code=307)
        return f'Upstream range okuma hatasi: {last_err}', 502

    last_err = None
    for u in candidate_urls:
        try:
            upstream = turbo_session.get(
                u,
                headers=base_headers,
                stream=True,
                timeout=(10, 300),
                allow_redirects=True
            )
            if upstream.status_code >= 500:
                upstream.close()
                last_err = f'status={upstream.status_code} url={u}'
                continue

            response_headers = {}
            for h in passthrough:
                v = upstream.headers.get(h)
                if v:
                    response_headers[h] = v
            response_headers.pop('Content-Length', None)

            def generate():
                try:
                    for chunk in upstream.raw.stream(128 * 1024, decode_content=False):
                        if chunk:
                            yield chunk
                except requests.RequestException:
                    return
                finally:
                    upstream.close()

            return Response(
                stream_with_context(generate()),
                status=upstream.status_code,
                headers=response_headers,
                direct_passthrough=True,
            )
        except requests.RequestException as e:
            last_err = e

    if candidate_urls:
        return redirect(candidate_urls[0], code=307)
    return f'Upstream baglanti hatasi: {last_err}', 502


@app.route('/proxy_m3u8')
def proxy_m3u8():
    target = (request.args.get('url') or '').strip()
    if not target:
        return 'Eksik URL', 400

    parsed = urlparse(target)
    if parsed.scheme not in ('http', 'https'):
        return 'Gecersiz URL semasi', 400
    if not _is_allowed_media_url(target):
        return 'Erisim engellendi', 403

    try:
        r = turbo_session.get(target, timeout=30, allow_redirects=True)
        body = r.text
    except requests.RequestException as e:
        return f'Manifest alinamadi: {e}', 502
    if r.status_code >= 400:
        return Response(body, status=r.status_code, mimetype='text/plain')

    out = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            out.append(line)
            continue

        abs_url = urljoin(target, s)
        proxied = '/proxy?url=' + quote(abs_url, safe='')
        out.append(proxied)

    return Response('\n'.join(out), mimetype='application/vnd.apple.mpegurl')


@app.route('/transcode_start')
def transcode_start():
    target = (request.args.get('url') or '').strip()
    if not target:
        return jsonify({'ok': False, 'error': 'Eksik URL'}), 400
    if not _is_allowed_media_url(target):
        return jsonify({'ok': False, 'error': 'URL engellendi'}), 403
    if not _is_ffmpeg_available():
        return jsonify({'ok': False, 'error': 'ffmpeg kurulu degil'}), 200

    job_id, playlist = _start_transcode_job(target)
    # wait briefly for first playlist write
    for _ in range(30):
        if os.path.exists(playlist) and os.path.getsize(playlist) > 0:
            break
        time.sleep(0.2)

    return jsonify({'ok': True, 'job': job_id, 'playlist': f'/transcode/{job_id}/index.m3u8'})


@app.route('/transcode/<job_id>/<path:fname>')
def transcode_file(job_id, fname):
    if '..' in fname or fname.startswith('/'):
        abort(400)
    job = TRANSCODE_JOBS.get(job_id)
    if not job:
        abort(404)
    path = os.path.join(job['dir'], fname)
    if not os.path.exists(path):
        proc = job.get('proc')
        if proc and proc.poll() is None:
            return ('Hazirlaniyor', 202)
        abort(404)
    return send_from_directory(job['dir'], fname)


@app.route('/player')
def player():
    # SECURITY: Never expose upstream URL (contains credentials) to the browser.
    mode = request.args.get('mode','movies')
    title = request.args.get('title','')
    _id = request.args.get('id') or request.args.get('episode') or ''
    series_id = request.args.get('series_id') or ''
    url = ''

    # Backward compatibility: if old links pass ?url=..., accept it server-side BUT do not render it to client.
    old_url = (request.args.get('url') or '').strip()
    if old_url and _is_allowed_media_url(old_url):
        url = old_url

    if not url:
        if not _id:
            return "Oynatılacak ID eksik", 400
        # Build upstream URL on server side
        if mode == 'live':
            url = f"{BASE_URL}/live/{USER}/{PASS}/{_id}.ts"
        elif mode == 'series':
            # episode id is in _id
            url = f"{BASE_URL}/series/{USER}/{PASS}/{_id}.mp4"
        else:
            url = f"{BASE_URL}/movie/{USER}/{PASS}/{_id}.mp4"

    # Always proxy playback URL
    if '.m3u8' in url:
        playback_url = '/proxy_m3u8?url=' + quote(url, safe='')
    else:
        playback_url = '/proxy?url=' + quote(url, safe='') + '&mode=' + quote(mode, safe='') + '&id=' + quote(_id, safe='') + '&series_id=' + quote(series_id, safe='')

    # Render template without leaking upstream URL
    return render_template_string(PLAYER_TEMPLATE, url=playback_url, playback_url=playback_url, title=title, audio_tracks=[], can_transcode=_is_ffmpeg_available())


@app.route('/favicon.ico')
def favicon():
    svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><rect width='16' height='16' fill='#e50914'/><text x='8' y='11' font-size='10' fill='#fff' text-anchor='middle' font-family='Segoe UI,Arial'>F</text></svg>"""
    return Response(svg, mimetype='image/svg+xml')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, threaded=True)

