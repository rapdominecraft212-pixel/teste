# Especificação de layout e fluxo — Bot Telegram de edição automática

Este documento transforma a ideia de layout do bot em uma especificação técnica clara para implementação.

---

## 1. Objetivo

O bot deve permitir que o usuário:

1. Envie vários links em sequência.
2. O sistema crie uma tarefa de edição para cada link.
3. O usuário possa sair do modo de envio quando quiser.
4. O bot processe os vídeos em segundo plano.
5. O usuário possa consultar vídeos editados/prontos.
6. O bot envie múltiplos arquivos prontos pelo Telegram.

O Telegram passa a ser tanto a entrada quanto a saída do sistema:

```text
Usuário envia links pelo Telegram
        ↓
Computador processa os vídeos
        ↓
Bot devolve os arquivos editados no mesmo chat
```

---

## 2. Menu principal

Ao abrir o bot ou enviar `/start`, o usuário deve ver:

```text
Olá! Eu sou o KwaiEditor.

Escolha uma opção:

[Enviar link]
[Vídeos editados]
```

Sugestão visual com emojis:

```text
🎬 O que você quer fazer?

[📤 Enviar link]
[📥 Vídeos editados]
```

Tipo de teclado recomendado:

```text
ReplyKeyboardMarkup
```

Motivo:

- os botões ficam disponíveis embaixo do chat;
- é simples para o usuário;
- não exige abrir site;
- funciona bem em celular.

---

## 3. Botão: Enviar link

Quando o usuário clicar em:

```text
📤 Enviar link
```

O bot deve entrar no modo de coleta de links.

Mensagem:

```text
Cole o texto com o link.
Nós criaremos o resto.

Você pode enviar vários links, um por mensagem.
Quando terminar, digite: sair
```

Ou versão mais clara:

```text
📤 Envie seus links agora.

Cole um link por mensagem.
Você pode mandar vários links seguidos.

Quando terminar, digite: sair
```

Estado interno do usuário:

```text
collecting_links
```

---

## 4. Usuário envia um link

Enquanto o usuário estiver em `collecting_links`, cada mensagem deve ser analisada.

Se for link válido:

```text
Link recebido ✅
Criamos uma nova tarefa de edição.

Pode mandar outro link ou digitar sair.
```

A cada link, o sistema cria uma nova tarefa:

```text
job_id
chat_id
input_url
status = queued
created_at
```

---

## 5. Usuário envia vários links

Exemplo de conversa:

```text
Bot:
Envie seus links agora. Quando terminar, digite sair.

Usuário:
https://kwai.com/video1

Bot:
Link recebido ✅
Pode mandar outro link ou digitar sair.

Usuário:
https://kwai.com/video2

Bot:
Link recebido ✅
Pode mandar outro link ou digitar sair.

Usuário:
https://kwai.com/video3

Bot:
Link recebido ✅
Pode mandar outro link ou digitar sair.
```

---

## 6. Usuário digita “sair”

Quando o usuário digitar:

```text
sair
```

O bot deve sair do modo `collecting_links` e voltar ao menu principal.

Mensagem:

```text
Tudo certo ✅
Seus links foram recebidos.

Agora vou editar os vídeos em segundo plano.
Quando quiser consultar, toque em “Vídeos editados”.
```

Voltar estado para:

```text
idle
```

Mostrar novamente o menu:

```text
[📤 Enviar link]
[📥 Vídeos editados]
```

---

## 7. Botão: Vídeos editados

Quando o usuário clicar em:

```text
📥 Vídeos editados
```

O bot deve consultar o banco e contar:

```text
ready_count       vídeos prontos e ainda não enviados
processing_count  vídeos ainda processando
sent_count        vídeos já enviados
failed_count      vídeos com erro
```

A resposta muda dependendo do estado.

---

## 8. Caso A — Existem vídeos prontos e vídeos processando

Condição:

```python
ready_count > 0 and processing_count > 0
```

Mensagem:

```text
Você tem {ready_count} vídeo(s) pronto(s).
Ainda existem {processing_count} vídeo(s) sendo editado(s).

Deseja receber os prontos agora ou prefere esperar todos terminarem?
```

Botões inline:

```text
[Quero agora]
[Aceito esperar]
```

Tipo de botão recomendado:

```text
InlineKeyboardMarkup
```

Callback data:

```text
receive_ready_now
wait_all
```

---

## 9. Se clicar em “Quero agora”

O bot deve:

1. Responder o callback.
2. Enviar uma mensagem informando que começará o envio.
3. Enviar todos os arquivos prontos ainda não enviados.
4. Marcar cada tarefa como enviada após sucesso.

Mensagem:

```text
Perfeito. Vou enviar agora os {ready_count} vídeo(s) que já estão prontos.
```

Depois envia:

```text
[arquivo1.mp4]
[arquivo2.mp4]
[arquivo3.mp4]
```

Ao final:

```text
Envio concluído ✅
Os vídeos que ainda estão editando serão enviados quando você solicitar novamente ou quando todos ficarem prontos.
```

---

## 10. Se clicar em “Aceito esperar”

O bot deve marcar preferência do usuário/tarefas:

```text
wait_until_all_ready = true
```

Mensagem:

```text
Combinado ✅
Vou esperar todos os vídeos terminarem.
Quando estiverem prontos, aviso você aqui.
```

Quando todos ficarem prontos, o bot pode enviar:

```text
Todos os seus vídeos terminaram ✅
Você tem {ready_count} vídeo(s) pronto(s).

Quer receber agora?
```

Botões:

```text
[Sim, quero receber]
[Não, prefiro depois]
```

---

## 11. Caso B — Todos os vídeos estão prontos

Condição:

```python
ready_count > 0 and processing_count == 0
```

Mensagem:

```text
Você tem {ready_count} vídeo(s) pronto(s).
Quer receber agora?
```

Botões inline:

```text
[Sim, quero receber]
[Não, prefiro depois]
```

Callback data:

```text
receive_all_ready
not_now
```

---

## 12. Se clicar em “Sim, quero receber”

O bot envia todos os arquivos prontos ainda não enviados.

Mensagem inicial:

```text
Certo. Vou enviar seus vídeos agora.
```

Depois:

```text
[arquivo1.mp4]
[arquivo2.mp4]
...
```

Mensagem final:

```text
Pronto ✅
Todos os vídeos disponíveis foram enviados.
```

---

## 13. Se clicar em “Não, prefiro depois”

Mensagem:

```text
Tudo bem.
Seus vídeos continuarão salvos.
Quando quiser, toque em “Vídeos editados”.
```

Nenhum arquivo é enviado.

---

## 14. Caso C — Não há vídeos prontos, mas existem vídeos processando

Condição:

```python
ready_count == 0 and processing_count > 0
```

Mensagem:

```text
Ainda não há vídeos prontos.

Existem {processing_count} vídeo(s) sendo editado(s).
Assim que terminarem, você poderá recebê-los por aqui.
```

Opcionalmente:

```text
Quer que eu avise quando todos terminarem?
```

Botões:

```text
[Sim, avisar]
[Não precisa]
```

---

## 15. Caso D — Não há vídeos nem tarefas

Condição:

```python
ready_count == 0 and processing_count == 0 and sent_count == 0
```

Mensagem:

```text
Você ainda não tem vídeos por aqui.

Toque em “Enviar link” para começar.
```

---

## 16. Caso E — Existem erros

Se `failed_count > 0`, incluir na mensagem:

```text
⚠️ {failed_count} vídeo(s) tiveram erro durante a edição.
```

No futuro pode haver botão:

```text
[Ver erros]
[Tentar novamente]
```

---

## 17. Estados do usuário

Tabela `user_states` sugerida:

```text
chat_id INTEGER PRIMARY KEY
state TEXT
wait_until_all_ready BOOLEAN
updated_at TEXT
```

Estados possíveis:

```text
idle
collecting_links
```

---

## 18. Estados das tarefas

Tabela `jobs` sugerida:

```text
job_id TEXT PRIMARY KEY
chat_id INTEGER NOT NULL
input_url TEXT NOT NULL
status TEXT NOT NULL
output_path TEXT
delivered INTEGER DEFAULT 0
created_at TEXT
updated_at TEXT
sent_at TEXT
error_message TEXT
```

Status possíveis:

```text
queued       tarefa criada
processing   vídeo sendo editado
ready        arquivo final pronto
sent         arquivo enviado
failed       erro
```

---

## 19. Lógica central do bot

Pseudo-código:

```python
if text == "/start":
    set_user_state(chat_id, "idle")
    show_main_menu(chat_id)

elif text == "📤 Enviar link":
    set_user_state(chat_id, "collecting_links")
    ask_for_links(chat_id)

elif text == "📥 Vídeos editados":
    show_edited_videos_status(chat_id)

else:
    state = get_user_state(chat_id)

    if state == "collecting_links":
        if text.lower().strip() == "sair":
            set_user_state(chat_id, "idle")
            finish_link_collection(chat_id)
        elif looks_like_url(text):
            create_job(chat_id, text)
            confirm_link_received(chat_id)
        else:
            ask_valid_link_or_exit(chat_id)
    else:
        show_main_menu(chat_id)
```

---

## 20. Lógica dos botões inline

Pseudo-código:

```python
if callback_data == "receive_ready_now":
    send_ready_videos(chat_id)

elif callback_data == "wait_all":
    set_wait_until_all_ready(chat_id, True)
    confirm_wait(chat_id)

elif callback_data == "receive_all_ready":
    send_ready_videos(chat_id)

elif callback_data == "not_now":
    confirm_not_now(chat_id)
```

Importante:

Sempre responder o callback com `answerCallbackQuery`, mesmo que não mostre pop-up.

---

## 21. Tipos de botão a usar

### Menu principal

Usar:

```text
ReplyKeyboardMarkup
```

Exemplo:

```json
{
  "keyboard": [
    [{"text": "📤 Enviar link"}],
    [{"text": "📥 Vídeos editados"}]
  ],
  "resize_keyboard": true,
  "is_persistent": true
}
```

---

### Decisões dentro de mensagens

Usar:

```text
InlineKeyboardMarkup
```

Exemplo:

```json
{
  "inline_keyboard": [
    [{"text": "Quero agora", "callback_data": "receive_ready_now"}],
    [{"text": "Aceito esperar", "callback_data": "wait_all"}]
  ]
}
```

---

## 22. Observação sobre “baixar”

No Telegram, o bot envia o arquivo no chat. O usuário pode:

- assistir no Telegram;
- baixar manualmente;
- salvar no dispositivo;
- encaminhar;
- manter no histórico da conversa.

Então, tecnicamente, “baixar agora” significa:

```text
receber os arquivos agora no chat
```

---

## 23. Limite de arquivo

Na API normal do Telegram Bot, arquivos enviados diretamente pelo bot têm limite prático de aproximadamente 50 MB por arquivo.

Regra recomendada:

```python
if file_size <= 50MB:
    enviar arquivo pelo Telegram
else:
    enviar link de download ou usar Telegram Bot API Server local
```

---

## 24. Experiência final esperada

```text
Bot:
🎬 O que você quer fazer?

[📤 Enviar link]
[📥 Vídeos editados]

Usuário toca: 📤 Enviar link

Bot:
📤 Envie seus links agora.
Cole um link por mensagem.
Quando terminar, digite sair.

Usuário:
https://kwai.com/video1

Bot:
Link recebido ✅
Pode mandar outro link ou digitar sair.

Usuário:
https://kwai.com/video2

Bot:
Link recebido ✅
Pode mandar outro link ou digitar sair.

Usuário:
sair

Bot:
Tudo certo ✅
Seus links foram recebidos.
Vou editar os vídeos em segundo plano.

Depois, usuário toca: 📥 Vídeos editados

Bot:
Você tem 2 vídeos prontos.
Quer receber agora?

[Sim, quero receber]
[Não, prefiro depois]

Usuário toca: Sim, quero receber

Bot:
Certo. Vou enviar seus vídeos agora.

[video1_editado.mp4]
[video2_editado.mp4]

Bot:
Pronto ✅
Todos os vídeos disponíveis foram enviados.
```
