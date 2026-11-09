# Proxy Rotator v2 — SB Admin 2 Dashboard

| | |
| --- | --- |
| **Status** | `review` |
| **Created** | 2026-11-07 |
| **Last touched** | 2026-11-07 |

## Purpose

Proxy rotation dashboard **versi 2** dengan tampilan **SB Admin 2** (Bootstrap 3 admin theme ala Start Bootstrap) untuk rotating proxy pool (scraping, multi-account, privacy).

**Problem it solves:** gratis proxy mati cepat. Rotator auto-fetch → validasi → rotasi via proxy hidup, plus **session connect** — ketika proxy dipilih dan koneksi sukses, semua traffic server (dan hasil rotasi) exit lewat proxy tersebut, sehingga lokasi komputer kita = lokasi IP proxy yang terhubung.

**Improvements vs v1:**
- Dashboard kini SB Admin 2 (sidebar + topbar + panels + stat cards + Bootstrap 3 table).
- Fitur **Connect / Disconnect session** dengan eksekusi test melalui proxy yang dipilih.
- Hasil rotasi menampilkan exit IP + latency + country.

## How to Run

```bash
# install deps
/tmp/proxy_venv/bin/pip install fastapi uvicorn httpx==0.28.1 aiosqlite python-multipart jinja2

# run server
cd /home/don-ai/Documents/Dev.Team/projects/proxy-rotator-v2
/tmp/proxy_venv/bin/python src/server.py

# dashboard: http://localhost:8000
# api docs:  http://localhost:8000/docs
```

## Deploy

- **Render**: pakai `render.yaml` di root project (runtime: docker, port 8000).
- **Railway**: pakai Dockerfile di root project.
- **VPS**: build image atau `pip install -r requirements.txt` + `uvicorn src.main:app --host 0.0.0.0 --port 8000`.

## Structure

```
proxy-rotator-v2/
├── requirements.txt
├── Dockerfile
├── render.yaml
├── tests/
│   └── test_api.py       # 24 tests (API, session, rotator, validation) — no network
└── src/
    ├── main.py            # FastAPI app + API (stats/proxies/fetch/validate/test/rotate/session/logs)
    ├── database.py        # SQLite
    ├── proxy_manager.py   # CRUD, validate, rotate, session, test_proxy, request_with_proxy
    ├── proxy_fetcher.py   # auto-fetch from free proxy sources
    ├── local_proxy.py     # local forward proxy 127.0.0.1:8888 → connected upstream proxy
    ├── server.py          # uvicorn launcher
    ├── templates/
    │   └── index.html     # SB Admin 2 dashboard
    └── static/
        ├── css/app.css    # custom overrides
        ├── js/app.js      # dashboard logic
        └── vendor/        # SB Admin 2 assets (bootstrap, metisMenu, sb-admin-2, font-awesome, jquery)
```

## API (v2)

| Method | Endpoint | Deskripsi |
| --- | --- | --- |
| GET | `/api/stats` | total / alive / dead / sources / top countries / avg latency |
| GET | `/api/proxies` | list proxies (filter: alive_only, source, country, protocol, search, sort, order) |
| POST | `/api/proxies/fetch` | fetch proxy baru dari source |
| POST | `/api/proxies` | bulk add manual `{ip, port, protocol, country, source}` |
| POST | `/api/proxies/validate` | bulk validate alive + latency |
| GET | `/api/proxies/{id}/test` | test satu proxy → exit IP + latency |
| DELETE | `/api/proxies/{id}` | delete proxy |
| DELETE | `/api/proxies/clear-dead` | clear dead / high fail |
| DELETE | `/api/proxies/clear-all` | clear semua |
| POST | `/api/session/connect` | connect session via proxy (`{proxy_id}`) → test → set current |
| POST | `/api/session/disconnect` | lepas session proxy |
| POST | `/api/session/auto-rotate` | `{enabled: true/false}` → toggle auto-rotation sesi |
| GET | `/api/session/rotate` | manual rotate: pindah session ke proxy alive berikutnya |
| GET | `/api/session` | status session (termasuk `auto_rotate`) |
| GET | `/api/rotate/test?url=...&method=...` | request via rotator (session proxy jika aktif) |
| GET | `/api/logs?limit=...` | activity log |
| GET | `/api/local-proxy` | info local forward proxy browser (`127.0.0.1:8888`) + upstream aktif |
| GET | `/api/system-proxy` | status proxy browser system-wide (GNOME gsettings) |
| POST | `/api/system-proxy` | `{enabled: true/false}` → set/lepas proxy browser ke `127.0.0.1:8888` |
| GET | `/proof` | halaman bukti lokasi: query geo-IP lewat proxy yang terhubung (IP/city/country/ISP) |

## Tests

```bash
cd projects/proxy-rotator-v2
/tmp/proxy_venv/bin/python -m unittest discover -s tests -v
```

32 tests, tanpa network (DB sementara + ASGI in-process, `request_with_proxy` di-mock):

- dashboard & static assets, halaman `/proof`, info local proxy
- stats, filter alive/country/source/search, sort, delete, clear-dead, clear-all
- session connect sukses (exit IP + country), connect gagal, connect ID tak dikenal, disconnect
- auto-rotate toggle + manual `/api/session/rotate`
- rotator: pool alive, round-robin, session proxy prioritas, clear
- logs, validasi bulk (mock)
- helper local forward proxy (CONNECT detection, rewrite request absolute-form)

## Browser Lewat Proxy

Beda dengan VPN: proxy hanya mengubah traffic yang **di-set memakai proxy**. Aplikasi ini sudah
lewat proxy, tapi browser tetap memakai koneksi ISP sampai diarahkan.

Cara paling gampang: di dashboard, panel **Current Session** ada tombol
**Aktifkan di Browser** / **Matikan**. Tombol itu men-set system proxy (GNOME/gsettings)
ke `127.0.0.1:8888` atau melepasnya — jadi browser yang mengikuti system proxy langsung
keluar lewat proxy yang sedang di-connect.

Kalau mau manual:

1. Jalankan server (local forward proxy otomatis listen di `127.0.0.1:8888`).
2. Connect ke salah satu proxy di dashboard.
3. Set proxy browser ke `127.0.0.1:8888` (HTTP **dan** HTTPS).
   - Firefox: Settings → Network Settings → Manual proxy → HTTP Proxy `127.0.0.1`, Port `8888`, centang *Also use for HTTPS*.
   - Chrome: `google-chrome --proxy-server="http://127.0.0.1:8888"`.
   - GNOME (system-wide):
     ```bash
     gsettings set org.gnome.system.proxy mode 'manual'
     gsettings set org.gnome.system.proxy.http host '127.0.0.1'
     gsettings set org.gnome.system.proxy.http port 8888
     gsettings set org.gnome.system.proxy.https host '127.0.0.1'
     gsettings set org.gnome.system.proxy.https port 8888
     # balikkan: gsettings set org.gnome.system.proxy mode 'none'
     ```
4. Ganti proxy di dashboard → lokasi browser ikut berubah (tanpa ubah setting browser lagi).
5. Cek `https://ipwho.is` / `https://ipinfo.io` di browser → harus muncul lokasi proxy.

Env opsional: `LOCAL_PROXY_HOST` (default `127.0.0.1`), `LOCAL_PROXY_PORT` (default `8888`).

## Done

- [x] Backend API works (fetch / validate / rotate / test / session)
- [x] SB Admin 2 dashboard served
- [x] Tests (30, no network)
- [x] Halaman `/proof` untuk cek lokasi lewat proxy
- [x] Local forward proxy browser (`127.0.0.1:8888`) yang mengikuti proxy terhubung
- [x] Auto-rotate session (toggle + background loop `AUTO_ROTATE_INTERVAL`, default 60s)
- [ ] Mark project `done` (menunggu review)

## Not Done

- Tidak ada item tersisa; menunggu review user.

## Key Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-11-07 | SB Admin 2 (Start Bootstrap) via vendored static assets | Tampilan dashboard ala SB Admin 2 tanpa build step npm |
| 2026-11-07 | Session proxy via `Rotator.current` | Connect → test → semua request exit lewat proxy itu |
| 2026-11-07 | Single-file SQLite + FastAPI + vanilla JS | Konsisten dengan v1, tanpa build step baru |

## Notes

- Asset SB Admin 2 diunduh dari jsdelivr ke `src/static/vendor/` supaya dashboard jalan tanpa CDN eksternal.
- Auto-fetch sources: free-proxy-list.net, sslproxies.org, proxylist.run, spys.ru, openproxy.space.
- Rotate via `GET /api/rotate/test?url=...` → round-robin / session proxy.
- `/proof` menampilkan lokasi keluar **aplikasi** lewat proxy terhubung. Browser pengguna tidak otomatis memakai proxy; untuk mengubah lokasi browser, set HTTP proxy browser ke `IP:port` proxy tersebut.
