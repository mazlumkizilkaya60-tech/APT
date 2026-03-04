# Render (FREE) Deploy Paketi — Flask

## Bu paket ne yapar?
Render üzerinde `app1.py` Flask uygulamanı **Blueprint** olarak ayağa kaldırır.

## Dosyalar
- app1.py  (kendi dosyanı buraya koymalısın)
- requirements.txt
- render.yaml
- .gitignore

## 1) GitHub'a yükle
1. GitHub’da yeni repo aç.
2. Bu paketin içeriğini repo köküne koy.
3. KENDİ `app1.py` dosyanla bu paketteki placeholder `app1.py` dosyasını değiştir.
4. Commit + push.

## 2) Render’da deploy
1. Render -> New -> **Blueprint**
2. GitHub repo’nu seç
3. Deploy

## 3) Environment Variables (zorunlu)
Render servis ayarlarında Environment bölümüne ekle:
- IPTV_BASE_URL
- IPTV_USER
- IPTV_PASS

## Start Command
render.yaml içinde:
gunicorn -w 2 -k gthread -t 120 -b 0.0.0.0:$PORT app1:app

## Notlar
- Render Free servisler “uyku”ya geçebilir; TV’de ilk açılış gecikebilir.
