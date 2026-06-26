# Worklog — Kwai-Editor

---
Task ID: 1
Agent: Main Agent
Task: Investigar e corrigir conflito de Chrome entre Jobs consecutivos

Work Log:
- Analisou ciclo de vida do Chrome em worker_prepare → preparar_video() → QwenReplyAsync
- Descobriu CAUSA RAIZ: _limpar() usava self._perfil.lower() no pkill -f, mas Chrome roda com path original (Kwai-Editor ≠ kwai-editor). pkill -f é case-sensitive no Linux, então nunca encontrava o processo.
- Chrome do Job anterior continuava vivo → perfil travado → Job 2 abria Chrome em estado degradado → .mode-select-open não funcionava
- Corrigiu _limpar() em qwen_reply_async.py: pkill -f -i (case-insensitive)
- Adicionou _esperar_chrome_morto(): polling com pgrep -f -i, timeout 8s
- Modificou close() para verificar se Chrome morreu de verdade, forçar pkill se não
- Adicionou pausa de 2s entre jobs em worker_prepare como margem de segurança
- Aplicou mesma correção em qwen_reply.py (versão sync)
- Commit + push: 217bfbb

Stage Summary:
- Bug de case sensitivity era a causa raiz do conflito Chrome
- 3 camadas de defesa: pkill -i, verificação após close, pausa entre jobs
- Arquivos modificados: Playwright/qwen_reply_async.py, Playwright/qwen_reply.py, bot/worker.py
