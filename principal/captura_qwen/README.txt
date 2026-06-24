============================================================
  CAPTURA DE ESTRUTURA DO QWEN AI
============================================================

O QUE ISSO FAZ
--------------
Este script abre o Chrome com o site do Qwen AI e captura TUDO
que acontece no navegador enquanto voce interage com o site:

  - Todas as mudancas no DOM (nodos adicionados/removidos/texto)
  - Todos os cliques e inputs
  - Todas as requisições de rede (fetch/XHR) com corpos de request E response
  - Mensagens do console
  - Snapshots do HTML completo em momentos criticos
  - Screenshots periodicos

O objetivo é entender exatamente como o Qwen 3.7-Plus estrutura
a resposta no DOM, para corrigir o bug onde a resposta final
esta vindo com texto estranho (ex: "1\nO virus infectou...").


COMO USAR
--------
1. Este zip deve ser extraido DENTRO da pasta video-editor/
   (a pasta que tem Playwright/, bot/, src/, etc.)

   Estrutura esperada:
     video-editor/
       ├── Playwright/
       │   └── chrome_profile/      <- seu login Qwen esta aqui
       ├── bot/
       ├── src/
       ├── capturar.bat             <- extraido deste zip
       ├── capturar_qwen.py         <- extraido deste zip
       └── README.txt               <- este arquivo

2. De um duplo-clique em capturar.bat

3. O Chrome vai abrir com o site https://chat.qwen.ai/
   (se o chrome_profile tiver sessao valida, voce ja estara logado)

4. Faca o processo completo:
   a. Clique no botao de selecao de modo (.mode-select-open)
   b. Escolha "Upload attachment"
   c. Selecione um video (qualquer um, pode ser curto)
   d. Digite o prompt (pode usar o prompt de capa do qwen_capa.py)
   e. Clique em enviar
   f. AGUARDE a resposta completar totalmente
      (o botao "stop" deve aparecer e depois sumir)

5. Quando a resposta estiver completa, FECHE O NAVEGADOR (X)

6. O script vai:
   - Salvar todos os eventos em captura_logs/events.jsonl
   - Salvar snapshots de HTML em captura_logs/snapshots/
   - Salvar screenshots em captura_logs/screenshots/
   - Criar um sumario em captura_logs/summary.txt
   - Compactar tudo num unico ZIP

7. Pegue o arquivo:
     captura_logs/captura_qwen_YYYYMMDD_HHMMSS.zip

   e envie de volta para analise.


O QUE ESTAMOS PROCURANDO
------------------------
Especificamente, queremos ver:

1. O HTML da ultima mensagem do assistente (.qwen-chat-message-assistant)
   no momento exato em que a resposta termina (stop-button desaparece).

2. A estrutura interna de .response-message-content:
   - Tem algum bloco de "pensamento" (thinking)?
   - Onde fica o numero "1" que aparece na resposta?
   - Qual classe CSS isola so a resposta final?

3. As respostas das APIs do Qwen (fetch_response):
   - O "1" vem da API ou e renderizado pelo frontend?
   - Como o Qwen estruturou a resposta no JSON?


REQUISITOS
----------
- Python 3.10+ instalado (https://python.org)
- Playwright instalado (o .bat instala automaticamente se faltar)
- Google Chrome instalado (recomendado) OU Chromium (fallback)


PROBLEMAS CONHECIDOS
--------------------
- Se o Chrome ja estiver aberto com o mesmo perfil, pode dar erro.
  Feche todas as instancias do Chrome antes de rodar.

- Se a sessao do Qwen expirou, voce precisara fazer login manualmente.
  O script vai esperar — faca login normalmente e continue o processo.

- O script pode gerar logs grandes (varias dezenas de MB) se a resposta
  for longa. Isso e normal e necessario para a analise.


DENTRO DO ZIP FINAL
-------------------
O arquivo captura_qwen_*.zip que voce vai enviar de volta contem:

  events.jsonl       <- log estruturado (uma linha = um evento)
  summary.txt        <- resumo legivel
  snapshots/
    001_initial_load.html
    002_response_started.html
    003_response_complete.html     <- CRITICO
    004_response_final_2s.html     <- CRITICO
    005_response_final_7s.html
    ...
  screenshots/
    001_*.png
    periodic_0001.png
    ...

Eso é tudo que precisamos para entender exatamente o que esta
acontecendo e aplicar a correção definitiva.
