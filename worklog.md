---
Task ID: 1
Agent: Main
Task: Mapear SEQUENCIAL vs PARALELO no pipeline e paralelizar o restante

Work Log:
- Analisou todos os arquivos do pipeline (worker.py, simple.py, video_popup_linear.py, cortar_video.py, db.py)
- Mapeou o que é SEQUENCIAL vs PARALELO:
  - PARALELO: Qwen (3 jobs × 2 contas = 6 contas), Download, Validação URL
  - SEQUENCIAL: Render (1 thread para TODOS os jobs) ← GARGALO PRINCIPAL
- Implementou N render threads em worker.py (era 1, agora N = num_prep)
- Paralelizou BG pre-render + Popup pre-render dentro de criar_video() com ThreadPoolExecutor
- Adicionou controle de FFmpeg threads por render (FFMPEG_THREADS_PER_RENDER) para evitar contenção
- Adicionou acquire_ready_to_render_job() atômica no db.py (BEGIN IMMEDIATE) para thread-safety
- Validou que render paralelo funciona: 2 vídeos criados no mesmo segundo (Teste 0 e Teste 1, 17:24:41)

Stage Summary:
- Render agora roda N threads em paralelo (antes era 1 thread sequencial)
- BG + Popup pre-render rodam em paralelo dentro de cada job (antes era sequencial)
- FFmpeg threads ajustados automaticamente: cores / num_render
- Race condition corrigida com acquire_ready_to_render_job() atômica
- Speedup esperado: de ~260s para ~160s para 3 jobs (render paralelo elimina fila)
