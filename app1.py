import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# --- YENİ SUNUCU:---
BASE_URL = "http://xbluex5k.xyz:8080"
USER = "asan8442"
PASS = "6748442"

# TAPINAKÇI'dan aldığımız 'Turbo Session' ayarı
turbo_session = requests.Session()
turbo_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Connection': 'keep-alive'
})

def get_data(action, extra=""):
    url = f"{BASE_URL}/player_api.php?username={USER}&password={PASS}&action={action}{extra}"
    try:
        r = turbo_session.get(url, timeout=10) # Süreyi biraz uzattık
        data = r.json()
        
        # HATA AYIKLAMA: Terminalde ne geldiğini gör
        if not data:
            print(f"UYARI: {action} için sunucudan boş veri geldi.")
        else:
            print(f"BAŞARI: {action} yüklendi. Öğe sayısı: {len(data)}")
            
        return data
    except Exception as e:
        print(f"BAĞLANTI HATASI: {e}")
        return []
        

# ---------- HTML & CSS & JS ----------
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
        .sidebar { width: 260px; background: #000; border-right: 1px solid #1a1a1a; overflow-y: auto; padding: 20px; }
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

        /* PLAYER & SOL MENÜ */
        #player { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: #000; display: none; z-index: 9999; }
        video { width: 100%; height: 100%; }
        
        .p-ui { position: absolute; inset: 0; background: linear-gradient(to top, rgba(0,0,0,0.8) 0%, transparent 20%, transparent 80%, rgba(0,0,0,0.8) 100%); transition: 0.4s; z-index: 10; }
        .v-hidden { opacity: 0; cursor: none; }

        /* Sol Bölümler Menüsü (Tetikleyici Alan Dahil) */
        .left-trigger { position: absolute; left: 0; top: 0; width: 50px; height: 100%; z-index: 100; }
        .side-menu { position: absolute; top: 0; left: -350px; height: 100%; width: 340px; background: rgba(0,0,0,0.95); transition: 0.4s; z-index: 110; padding: 80px 20px; border-right: 3px solid #e50914; overflow-y: auto; }
        .left-trigger:hover + .side-menu, .side-menu:hover { left: 0; }
        
        .ep-btn { display: block; width: 100%; padding: 12px; background: #1a1a1a; border: 1px solid #333; color: #fff; text-align: left; margin-bottom: 6px; border-radius: 5px; cursor: pointer; font-size: 13px; }
        .ep-btn:hover { background: #e50914; border-color: #fff; }
        .ep-btn.active { background: #e50914; font-weight: bold; }

        /* Alt Kontroller */
        .p-bottom { position: absolute; bottom: 0; width: 100%; padding: 20px 40px; z-index: 20; }
        .prog-cont { width: 100%; height: 6px; background: rgba(255,255,255,0.2); border-radius: 3px; cursor: pointer; margin-bottom: 15px; }
        .prog-bar { height: 100%; background: #e50914; width: 0%; border-radius: 3px; }
        .ctrl-row { display: flex; align-items: center; justify-content: space-between; }
        .icon-btn { background: none; border: none; color: white; font-size: 24px; cursor: pointer; margin: 0 15px; }
        
        #toast { position: fixed; bottom: 50px; left: 50%; transform: translateX(-50%); background: #e50914; color: white; padding: 10px 20px; border-radius: 20px; z-index: 10001; display: none; }
    </style>
</head>
<body>

    <div id="toast"></div>

    <div class="top-bar">
        <a href="/" class="logo">FIRATFLIX</a>
        <div class="nav-links">
            <a href="/?m=live" class="{{ 'active' if mode=='live' }}">CANLI TV</a>
            <a href="/?m=movies" class="{{ 'active' if mode=='movies' }}">FİLMLER</a>
            <a href="/?m=series" class="{{ 'active' if mode=='series' }}">DİZİLER</a>
            <a href="#" onclick="showFavs()" style="color:#e50914">FAVORİLER</a>
        </div>
        <input type="text" id="searchInput" class="search-input" placeholder="Ara..." onkeyup="filter()">
    </div>

    <div class="main-container">
        <div class="sidebar">
            {% for c in categories %}
            <a href="/?m={{mode}}&c={{c.category_id}}" class="cat-item {{ 'active-cat' if cat_id==c.category_id|string }}">{{c.category_name}}</a>
            {% endfor %}
        </div>
        <div class="content">
            <div class="grid" id="mainGrid">
                {% for i in items %}
                <div class="card" data-name="{{ i.name.lower() }}" data-id="{{i.id}}">
                    <div class="fav-btn" onclick="toggleFav(event, '{{i.id}}', '{{i.name|replace(\"'\", \"\") }}', '{{i.stream_icon}}', '{{mode}}', '{{i.final_url}}')"><i class="fas fa-heart"></i></div>
                    <div onclick="handleClick('{{i.id}}', '{{i.name|replace(\"'\", \"\") }}', '{{mode}}', '{{i.final_url}}')">
                        <img src="{{ i.stream_icon }}" loading="lazy" onerror="this.src='https://via.placeholder.com/200x300/111/fff?text=Resim+Yok'">
                        <div class="card-info">{{ i.name }}</div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>

    <div id="player">
        <video id="v-obj" crossorigin="anonymous"></video>
        
        <div class="left-trigger"></div>
        <div class="side-menu" id="l-menu">
            <h3 style="color:#e50914; margin-bottom:20px; border-bottom:1px solid #333; padding-bottom:10px;">BÖLÜMLER</h3>
            <div id="ep-list"></div>
        </div>

        <div class="p-ui" id="p-ui">
            <div style="padding: 30px; display: flex; align-items: center;">
                <button class="icon-btn" onclick="closePlayer()"><i class="fas fa-arrow-left"></i></button>
                <h2 id="p-title" style="margin-left: 20px;"></h2>
            </div>
            
            <div class="p-bottom">
                <div class="prog-cont" onclick="seek(event)"><div class="prog-bar" id="p-bar"></div></div>
                <div class="ctrl-row">
                    <div>
                        <button class="icon-btn" onclick="skip(-10)"><i class="fas fa-undo"></i></button>
                        <button class="icon-btn" onclick="togglePlay()" id="playBtn"><i class="fas fa-pause"></i></button>
                        <button class="icon-btn" onclick="skip(10)"><i class="fas fa-redo"></i></button>
                        <span id="cur-t">00:00</span> / <span id="tot-t">00:00</span>
                    </div>
                    <div>
                        <span id="p-tag" style="color:#e50914; margin-right:20px; font-weight:bold;"></span>
                        <button class="icon-btn" onclick="video.requestPictureInPicture()"><i class="fas fa-clone"></i></button>
                        <button class="icon-btn" onclick="toggleFS()"><i class="fas fa-expand"></i></button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let hls = new Hls();
        const video = document.getElementById('v-obj');
        let currentId = "";

        // ANA İŞLEM
        async function handleClick(id, name, mode, url) {
            currentId = id;
            if(mode === 'series') {
                const res = await fetch('/get_series_details/' + id);
                const data = await res.json();
                loadEpisodes(data, name);
                document.getElementById('l-menu').style.display = 'block';
            } else {
                document.getElementById('l-menu').style.display = 'none';
                playVideo(url, name, mode.toUpperCase());
                checkProgress(id);
            }
        }

        function loadEpisodes(data, seriesName) {
            const list = document.getElementById('ep-list');
            list.innerHTML = "";
            data.seasons.forEach(s => {
                s.episodes.forEach(e => {
                    let b = document.createElement('button');
                    b.className = "ep-btn";
                    b.innerText = `S${s.season_num} E${e.num} - ${e.title || 'Bölüm'}`;
                    b.onclick = () => {
                        currentId = e.id;
                        playVideo(e.url, seriesName, `S${s.season_num} E${e.num}`);
                        checkProgress(e.id);
                    };
                    list.appendChild(b);
                });
            });
            // İlk bölümü otomatik başlat
            if(data.seasons.length > 0) {
                const first = data.seasons[0].episodes[0];
                currentId = first.id;
                playVideo(first.url, seriesName, "S1 E1");
            }
        }

        function playVideo(url, name, tag) {
            document.getElementById('player').style.display = 'block';
            document.getElementById('p-title').innerText = name;
            document.getElementById('p-tag').innerText = tag;
            if (Hls.isSupported() && (url.includes('m3u8') || url.includes('ts'))) {
                hls.destroy(); hls = new Hls(); hls.loadSource(url); hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, () => video.play());
            } else { video.src = url; video.play(); }
        }

        // İLERLEME VE FAVORİ
        function checkProgress(id) {
            let prog = JSON.parse(localStorage.getItem('f_prog') || '{}');
            if(prog[id] > 5) {
                if(confirm("Kaldığınız yerden devam edilsin mi?")) video.currentTime = prog[id];
            }
        }
        setInterval(() => {
            if(!video.paused && currentId) {
                let prog = JSON.parse(localStorage.getItem('f_prog') || '{}');
                prog[currentId] = video.currentTime;
                localStorage.setItem('f_prog', JSON.stringify(prog));
            }
        }, 5000);

        let favs = JSON.parse(localStorage.getItem('f_favs') || '[]');
        function toggleFav(e, id, name, icon, mode, url) {
            e.stopPropagation();
            let idx = favs.findIndex(f => f.id === id);
            if(idx > -1) favs.splice(idx, 1);
            else favs.push({id, name, icon, mode, url});
            localStorage.setItem('f_favs', JSON.stringify(favs));
            location.reload(); 
        }

        // KONTROLLER
        function togglePlay() { video.paused ? video.play() : video.pause(); }
        function skip(s) { video.currentTime += s; }
        function seek(e) {
            const r = e.currentTarget.getBoundingClientRect();
            video.currentTime = ((e.clientX - r.left) / r.width) * video.duration;
        }
        function toggleFS() { document.fullscreenElement ? document.exitFullscreen() : document.getElementById('player').requestFullscreen(); }
        function closePlayer() { video.pause(); document.getElementById('player').style.display='none'; }

        video.ontimeupdate = () => {
            document.getElementById('p-bar').style.width = (video.currentTime / video.duration * 100) + "%";
            document.getElementById('cur-t').innerText = format(video.currentTime);
            if(video.duration) document.getElementById('tot-t').innerText = format(video.duration);
        };
        function format(s) { let m=Math.floor(s/60); let sec=Math.floor(s%60); return (m<10?"0"+m:m)+":"+(sec<10?"0"+sec:sec); }

        window.onkeydown = (e) => {
            if(document.getElementById('player').style.display==='block') {
                if(e.code==='Space') togglePlay();
                if(e.code==='ArrowRight') skip(10);
                if(e.code==='ArrowLeft') skip(-10);
            }
        };

        function filter() {
            let val = document.getElementById('searchInput').value.toLowerCase();
            document.querySelectorAll('.card').forEach(c => c.style.display = c.getAttribute('data-name').includes(val) ? "block" : "none");
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    mode = request.args.get('m', 'movies')
    cat_id = request.args.get('c', '')
    if mode == 'live': categories = get_data("get_live_categories"); action = "get_live_streams"; path = "live"
    elif mode == 'series': categories = get_data("get_series_categories"); action = "get_series"; path = "series"
    else: categories = get_data("get_vod_categories"); action = "get_vod_streams"; path = "movie"

    raw_data = get_data(action, f"&category_id={cat_id}" if cat_id else "")
    items = []
    if isinstance(raw_data, list):
        for i in raw_data[:150]:
            sid = str(i.get('series_id') or i.get('stream_id'))
            ext = i.get('container_extension', 'mp4') if mode != 'live' else 'ts'
            url = f"{BASE_URL}/{path}/{USER}/{PASS}/{sid}.{ext}" if mode != 'series' else ""
            items.append({'id': sid, 'name': i.get('name', 'İsimsiz'), 'stream_icon': i.get('stream_icon') or i.get('cover') or '', 'final_url': url})
    return render_template_string(HTML_TEMPLATE, categories=categories, items=items, mode=mode, cat_id=cat_id)

@app.route('/get_series_details/<sid>')
def get_series_details(sid):
    data = get_data("get_series_info", f"&series_id={sid}")
    res = {"seasons": []}
    if not data or "episodes" not in data: return jsonify(res)
    for s_num in sorted(data["episodes"].keys(), key=int):
        season = {"season_num": s_num, "episodes": []}
        for ep in data["episodes"][s_num]:
            eid = ep.get('id') or ep.get('episode_id')
            season["episodes"].append({"id": str(eid), "num": ep.get('episode_num'), "title": ep.get('title'), "url": f"{BASE_URL}/series/{USER}/{PASS}/{eid}.{ep.get('container_extension', 'mp4')}"})
        res["seasons"].append(season)
    return jsonify(res)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, threaded=True)
