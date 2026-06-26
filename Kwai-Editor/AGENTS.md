# opencode Project Summary

## Goal
Kwai video editing bot — Telegram is the remote control (joystick), computer is the processing server.
Videos stay on the computer in `data/editado/` — no Telegram upload (avoids quality loss).

## Done
- **Telegram is now remote-control only** — removed `send_ready_videos()`, all "Receber agora" buttons,
  callback handlers for receiving videos. Bot only accepts links and shows job status.
- **Removed sender.py from main flow** — no more video upload via Telegram API.
  `MAX_DIRECT_UPLOAD_MB` removed from `.env`.
- **Removed `cleanup_sent_jobs()`, `set_job_sent()`, `get_ready_unsent_jobs()`** from db.py —
  `sent` status no longer used. Jobs persist as `queued` / `processing` / `ready` / `failed`.
- **`handle_check_videos()` simplified** — no inline keyboards; tells user folder path
  `data/editado/{chat_id}/` instead of offering to send.
- **Video encoding changed to CRF 18** — removed fixed `BITRATE="1500k"` cap.
  Now uses `-crf 18` (visually lossless) via ffmpeg_params for maximum quality.
- Fixed high latency (10s+) by replacing `requests.post/get` with `requests.Session()` in listener.py and sender.py
- Added session-based DB cleanup in db.py using atomic sentinel file (`.session_id`, `O_CREAT | O_EXCL`)
- Created `scripts/launcher.py` with Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`)
- Updated `iniciar_servidores.bat`, `parar_servidores.bat`, `scripts/parar.bat` for PID-based kill
- Redesigned bot messages with button-only navigation (Concluido/Cancelar)
- Removed `kwai-video.com` from `KWAI_HOST_HINTS` — blocks invalid domains early
- Added timeout (120s) to `run_command()` in kwai_downloader.py
- Added URL probe validation in `listener.py:handle_collecting_link()` before creating jobs — bad URLs are rejected with clear error message before creating a job
- Added URL probe validation in `worker.py:process_job()` as defense-in-depth before download
- Added `error_message` display in `handle_check_videos()` — user now sees why a job failed
- **Removed `_init_session()` / `.session_id` sentinel** — jobs now persist across server restarts
- **Worker verifies file exists** before `set_job_ready()` — prevents "phantom ready" jobs
- **`final_path` is always absolute** via `Path.resolve()` in both `worker.py` and `pipeline/simple.py`
- **Tests fixed** — `test_quick_check.py` and `test_model_cycling.py` now have correct Python paths
- Full lifecycle test: 3 procs (launcher, listener, worker), 0 orphans, DB persists after shutdown, `.session_id` eliminated

## Key Architecture
- **Telegram is joystick only** — no video upload. User sends links, checks status, collects from `data/editado/` on computer.
- **Max quality**: CRF 18 encoding instead of fixed bitrate — no quality loss from aggressive compression.
- **URL validation chain**: `find_urls_in_text()` → `is_probably_kwai_url()` (host hints) → `probe_url()` (yt-dlp metadata) → download
- **Validation happens twice**: listener (instant feedback to user) + worker (defense before processing)
- **Only `kwai.com`, `m.kwai.com`, `k.kwai.com`, `kuaishou.com`** are accepted as valid Kwai domains
- **2-hour timeout**: `run_command()` with `timeout=120` prevents worker hangs
- **Error transparency**: `error_message` column in DB is shown to user via `handle_check_videos()`
- **Persistence**: No data loss on restart. Jobs survive in `queued`/`processing`/`ready`/`failed` states.

## BUG-04: Mensagens cortadas no terminal de execução
- **Camada 1 — truncamento inteligente**: substituídos cortes cegos (`[:35]`, `[:60]`, `[:120]`, `[:200]`, `[:300]`) por `truncar_erro()` (mensagens curtas) e `truncar_stderr()` (stderr ffmpeg) em `bot/log_utils.py`
- **`terminal_bot.py`**: tabela mostra preview (primeira linha, max 50 chars); abaixo, seção "ERROS COMPLETOS" com erro completo via `truncar_erro()`
- **`db.py`**: log de falha agora mostra `error_message` completo (sem `[:120]`)
- **`cortar_video.py`** / **`cortar_resolusao.py`**: stderr do ffmpeg usa `truncar_stderr()` (cabeça 500 + cauda 500 chars, meio omitido)
- **Camada 2 — `traceback.print_exc()`**: adicionado em `worker.py:91` (URL inválida) e `worker.py:113` (download falhou). Já existia em `worker.py:159` (erro na edição)

## Remaining
- Test full user flow end-to-end: send link → process → collect from data/editado/ (requires sending a real Kwai link to the bot)
