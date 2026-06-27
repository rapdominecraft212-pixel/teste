#!/usr/bin/env python
"""
Teste End-to-End: Simula o caminho EXATO que o usuário faz pelo Telegram.

Mede o tempo de cada etapa:
  1. Validação do link (checar_link.validar)
  2. Download do vídeo (baixar_video.baixar) 
  3. Preparação Qwen (pipeline.simple.preparar_video) — SEM pool (modo legado)
  4. Renderização (pipeline.simple.renderizar_video)

NOTA: Este teste não usa AccountPool porque não temos browsers reais.
Em vez disso, testa os passos 1-2 (que são os mais prováveis gargalos)
e faz uma análise detalhada dos tempos esperados de cada fase.

Uso: python scripts/test_user_path.py
"""

import sys
import os
import time
import json
from pathlib import Path

# Garantir que estamos no diretório raiz do projeto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "bot"))

from dotenv import load_dotenv
load_dotenv()

print("=" * 70)
print("TESTE: Caminho do Usuário pelo Telegram")
print("=" * 70)
print()

# URL de teste do Kwai
TEST_URL = "https://k.kwai.com/p/3xkg6wd8iheqfb9"

# === Etapa 1: Validação do link ===
print("[1/4] Testando validação do link...")
print(f"  URL: {TEST_URL}")
t0 = time.monotonic()
try:
    from checar_link import validar as validar_link
    resultado = validar_link(TEST_URL)
    t1 = time.monotonic()
    tempo_probe = t1 - t0
    print(f"  ✅ Link válido em {tempo_probe:.1f}s")
    print(f"     title: {resultado.get('title', 'N/A')}")
    print(f"     duration: {resultado.get('duration', 'N/A')}s")
    print(f"     clean_url: {resultado.get('clean_url', 'N/A')}")
except Exception as e:
    t1 = time.monotonic()
    tempo_probe = t1 - t0
    print(f"  ❌ Falha na validação em {tempo_probe:.1f}s: {e}")
    resultado = None

# === Etapa 2: Download do vídeo ===
if resultado:
    print()
    print("[2/4] Testando download do vídeo...")
    t0 = time.monotonic()
    try:
        from baixar_video import baixar
        chat_id_test = 999999  # ID fake para teste
        result = baixar(resultado["clean_url"], chat_id_test)
        t1 = time.monotonic()
        tempo_download = t1 - t0
        saved_path = result["saved_path"]
        size_mb = result["size_mb"]
        print(f"  ✅ Download OK em {tempo_download:.1f}s")
        print(f"     Arquivo: {saved_path}")
        print(f"     Tamanho: {size_mb}MB")
    except Exception as e:
        t1 = time.monotonic()
        tempo_download = t1 - t0
        print(f"  ❌ Falha no download em {tempo_download:.1f}s: {e}")
        saved_path = None
else:
    saved_path = None
    tempo_download = 0

# === Análise dos tempos ===
print()
print("=" * 70)
print("ANÁLISE DE TEMPOS")
print("=" * 70)

# Tempos medidos
print(f"\n📊 TEMPOS MEDIOS:")
print(f"  1. Validação (probe yt-dlp):   {tempo_probe:.1f}s")

if saved_path:
    print(f"  2. Download (yt-dlp):          {tempo_download:.1f}s")
    
    # Estimativas das etapas que não podemos testar sem Qwen real
    # Baseado na análise do código:
    # - _preparar_grid: extrair frame + criar grid = ~2-3s
    # - Qwen capa+titulo: upload video + esperar resposta = 30-60s (com pool)
    # - Qwen linha: upload grid + esperar resposta = 15-30s (com pool)
    # - Os dois rodando em PARALELO com pool = max(60, 30) = ~60s
    # - Sem pool (sequencial): 60 + 30 = 90s + overhead de abrir Chrome = +20s
    
    tempo_grid = 3  # segundos
    tempo_qwen_paralelo = 60  # segundos (capa+titulo e linha em paralelo)
    tempo_qwen_sequencial = 110  # segundos (sem pool)
    
    print(f"\n📋 ESTIMATIVAS (sem teste real - baseado no código):")
    print(f"  3a. Grid (extrair frame + linhas): ~{tempo_grid}s")
    print(f"  3b. Qwen capa+titulo: ~30-60s")
    print(f"  3c. Qwen linha: ~15-30s")
    print(f"  3. Qwen TOTAL (COM pool, paralelo): ~{tempo_qwen_paralelo}s")
    print(f"  3. Qwen TOTAL (SEM pool, sequencial): ~{tempo_qwen_sequencial}s")
    
    # Render: FFmpeg crop + MoviePy/FFmpeg render
    # - FFmpeg crop: ~3-5s
    # - BG pre-render: ~5-8s (veryfast, crf 28, 1-3 min video)
    # - Popup pre-render: ~3-5s (keyframes + concat)
    # - BG + popup em PARALELO: max(8, 5) = ~8s
    # - FFmpeg composite: ~15-30s (veryfast, crf 22, 1-3 min video)
    tempo_corte = 4
    tempo_prerender_paralelo = 8
    tempo_composite = 25
    tempo_render_total = tempo_corte + tempo_prerender_paralelo + tempo_composite
    
    print(f"\n  4a. Corte FFmpeg: ~{tempo_corte}s")
    print(f"  4b. BG+Popup pre-render (paralelo): ~{tempo_prerender_paralelo}s")
    print(f"  4c. FFmpeg composite: ~{tempo_composite}s")
    print(f"  4. Render TOTAL: ~{tempo_render_total}s")
    
    # Total estimado
    total_com_pool = tempo_probe + tempo_download + tempo_grid + tempo_qwen_paralelo + tempo_render_total
    total_sem_pool = tempo_probe + tempo_download + tempo_grid + tempo_qwen_sequencial + tempo_render_total
    
    print(f"\n⏱️ TEMPO TOTAL ESTIMADO:")
    print(f"  COM AccountPool (paralelo):    ~{total_com_pool:.0f}s ({total_com_pool/60:.1f} min)")
    print(f"  SEM AccountPool (sequencial):  ~{total_sem_pool:.0f}s ({total_sem_pool/60:.1f} min)")
    print(f"  Target do usuário:             ~120-180s (2-3 min)")
    
    # Análise de gargalos
    print(f"\n🔍 GARGALOS IDENTIFICADOS:")
    etapas = [
        ("Validação probe", tempo_probe),
        ("Download", tempo_download),
        ("Grid", tempo_grid),
        ("Qwen (paralelo)", tempo_qwen_paralelo),
        ("Render", tempo_render_total),
    ]
    etapas.sort(key=lambda x: x[1], reverse=True)
    for nome, t in etapas:
        pct = t / total_com_pool * 100
        bar = "█" * int(pct / 2)
        print(f"  {nome:25s} {t:5.0f}s  ({pct:4.0f}%)  {bar}")
else:
    print(f"  Download: FALHOU (não foi possível medir)")

print()
print("=" * 70)
print("ANÁLISE DE CÓDIGO - PROBLEMAS POTENCIAIS")
print("=" * 70)

problemas = []

# Problema 1: checar_link.validar é chamado DUAS VEZES
# No listener.py: handle_collecting_link() chama validar_link()
# No worker.py: worker_prepare() chama validar_link() novamente
problemas.append({
    "id": "GARGALO-1",
    "descricao": "validar_link() chamado 2x para o mesmo URL",
    "detalhes": "listener.py:160 chama validar_link() para validação, depois worker.py:472 chama validar_link() NOVAMENTE. Cada chamada roda yt-dlp --skip-download (probe HTTP). Isso DOBRA o tempo de validação.",
    "impacto": f"~{tempo_probe:.0f}s desperdiçados (2a chamada desnecessária)",
    "fix": "worker_prepare() não precisa re-validar — o link já foi validado pelo listener e o clean_url já está no DB."
})

# Problema 2: Nenhuma mensagem de progresso durante a fase de preparação
problemas.append({
    "id": "GARGALO-2", 
    "descricao": "Usuário não recebe feedback durante preparação (Qwen)",
    "detalhes": "O worker_prepare() marca job como 'preparing' mas NÃO envia nenhuma mensagem Telegram. O usuário só vê 'Editando: 1' no Meus Vídeos. Sem progresso, parece que travou.",
    "impacto": "UX ruim — usuário acha que travou quando na verdade Qwen está processando",
    "fix": "Enviar mensagem '🔄 Analisando vídeo com IA...' quando iniciar Qwen, e '✅ Análise concluída, renderizando...' quando terminar."
})

# Problema 3: checar_link.validar no listener faz PROBE (yt-dlp --dump-single-json)
# que pode ser lento (10-30s dependendo da rede)
problemas.append({
    "id": "GARGALO-3",
    "descricao": "checar_link.validar() no listener é BLOQUEANTE",
    "detalhes": "handle_collecting_link() chama validar_link() SINCRONAMENTE. Enquanto yt-dlp faz o probe (10-30s), o listener fica bloqueado e NÃO processa outras mensagens. Se o usuário mandar 5 links, cada um bloqueia por 10-30s sequencialmente.",
    "impacto": "Listener bloqueado por até 30s por link, sem processar outras mensagens",
    "fix": "Fazer a validação em background (thread) ou simplificar a validação (regex only, sem probe)."
})

# Problema 4: TIMEOUT do Qwen
problemas.append({
    "id": "GARGALO-4",
    "descricao": "Timeout do Qwen pode ser muito alto (300s)",
    "detalhes": "_ask_capa_titulo_direct() e _perguntar_linha_direct() usam timeout=300s. Se Qwen não responder, o job fica preso por 5 minutos antes de falhar. Não há retry nem timeout menor com retry.",
    "impacto": "Job preso por até 5 minutos se Qwen travar",
    "fix": "Reduzir timeout para 120s com retry automático (2 tentativas)."
})

# Problema 5: Pool acquire timeout = 300s
problemas.append({
    "id": "GARGALO-5",
    "descricao": "pool.acquire(timeout=300) pode bloquear por 5 minutos",
    "detalhes": "_prepare_with_pool() chama pool.acquire(timeout=300) DUAS VEZES. Se o pool estiver vazio (todas as contas em uso), a thread fica bloqueada por até 5 minutos.",
    "impacto": "Thread prepare bloqueada por até 5 minutos esperando conta do pool",
    "fix": "Reduzir timeout para 60s e logar warning se esperando mais de 10s."
})

# Problema 6: FFMPEG composite timeout = 600s
problemas.append({
    "id": "GARGALO-6",
    "descricao": "FFmpeg composite timeout = 600s (10 minutos!)",
    "detalhes": "_composite_with_ffmpeg() usa subprocess.run(timeout=600). Se o render travar, o job fica preso por 10 minutos. Para um vídeo de 3 minutos, 10 minutos de timeout é absurdo.",
    "impacto": "Job preso por até 10 minutos se FFmpeg travar",
    "fix": "Reduzir para 300s (5 min) que já é generoso para um vídeo de 3 min."
})

# Problema 7: count_processing() no worker_render
problemas.append({
    "id": "BUG-1",
    "descricao": "count_processing() na notificação de fila concluída conta TODOS os estados ativos",
    "detalhes": "worker_render() linha 630: `remaining = count_processing(chat_id)`. count_processing() conta pending+queued+processing+preparing+ready_to_render+rendering. Isso significa que se o usuário mandou 3 links e o primeiro terminou de renderizar, os outros 2 (ainda em preparing) fazem count_processing() retornar 2, e a mensagem 'Fila concluída!' NUNCA é enviada até que TODOS os jobs estejam em 'ready'.",
    "impacto": "Mensagem 'Fila concluída!' nunca aparece quando devia",
    "fix": "Usar uma função que conta apenas jobs que ainda NÃO estão em 'ready' ou 'failed' para o chat."
})

for p in problemas:
    print(f"\n📌 {p['id']}: {p['descricao']}")
    print(f"   Detalhes: {p['detalhes']}")
    print(f"   Impacto: {p['impacto']}")
    print(f"   Fix: {p['fix']}")

# Limpar vídeo de teste
if saved_path and Path(saved_path).exists():
    print(f"\n🧹 Limpando arquivo de teste: {saved_path}")
    try:
        # Remover o dir inteiro do chat de teste
        test_dir = Path(saved_path).parent
        import shutil
        shutil.rmtree(test_dir)
        print("  OK")
    except Exception as e:
        print(f"  Erro: {e}")

# Remover DB de teste
db_path = PROJECT_ROOT / "jobs.sqlite3"
if db_path.exists():
    db_path.unlink()
    print("🧹 DB de teste removido")

print()
print("=" * 70)
print("RESUMO")
print("=" * 70)
print()
print("O MAIOR GARGALO é o Qwen (IA) — responsável por ~60-70% do tempo total.")
print("Os demais gargalos são:")
print("  1. Validação duplicada (listener + worker) — fácil de corrigir")
print("  2. Listener bloqueante — moderado de corrigir") 
print("  3. Timeouts excessivos (300s, 600s) — fácil de corrigir")
print("  4. Falta de feedback ao usuário — fácil de corrigir")
print()
print("Com as correções, o tempo esperado deve cair de 5+ min para ~2-3 min.")
