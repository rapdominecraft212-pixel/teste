# Tutorial técnico: entregar arquivos do computador para o celular usando Telegram Bot

Este documento descreve, do zero, como criar um fluxo em que um programa no computador termina de gerar/editar um vídeo e envia automaticamente o arquivo final para o celular do usuário via Telegram.

O objetivo é servir como **manual de implementação para você ou para um agente de inteligência artificial** construir a integração passo a passo.

---

## 1. Objetivo do sistema

Fluxo desejado:

```text
Usuário envia um link no site/celular
        ↓
Sistema cria uma tarefa de edição
        ↓
Computador/worker processa o vídeo em segundo plano
        ↓
Computador gera um arquivo final, exemplo: video_editado.mp4
        ↓
Programa Python envia esse arquivo para o usuário pelo Telegram
        ↓
Usuário recebe no celular sem precisar ficar esperando no site
```

A experiência ideal para o usuário é:

```text
Site:
Recebemos seu link. Pode fechar esta página.

Telegram:
Tudo certo. Vou te enviar o vídeo aqui quando estiver pronto.

Depois:
Seu vídeo está pronto.
[video_editado.mp4]
```

---

## 2. Por que Telegram é uma boa escolha aqui

Telegram Bot é mais simples que WhatsApp para este caso porque:

- criar um bot é rápido usando o `@BotFather`;
- não precisa conta empresarial;
- não precisa aprovação de templates;
- não precisa WhatsApp Business API;
- não precisa automatizar navegador;
- a API HTTP é direta;
- Python consegue enviar mensagens e arquivos com poucas linhas;
- o usuário recebe o arquivo em um app que já funciona muito bem em celular.

---

## 3. Conceitos importantes antes de implementar

### 3.1. Bot não envia mensagem para alguém que nunca iniciou conversa

Um bot do Telegram não consegue simplesmente enviar mensagem para qualquer pessoa aleatória. O usuário precisa primeiro abrir o bot e clicar em **Start** ou enviar `/start`.

Depois disso, o bot passa a conhecer o `chat_id` daquela conversa e consegue enviar mensagens/arquivos para ela.

---

### 3.2. `chat_id`

O `chat_id` é o identificador da conversa entre o bot e o usuário.

Exemplo simplificado de update recebido pelo bot:

```json
{
  "message": {
    "chat": {
      "id": 123456789,
      "type": "private"
    },
    "text": "/start abc123"
  }
}
```

Neste caso:

```text
chat_id = 123456789
```

Esse número é o destino para onde o programa Python deve enviar o arquivo.

---

### 3.3. Token do bot

Quando você cria um bot no `@BotFather`, recebe um token parecido com:

```text
1234567890:AAExampleExampleExampleExample
```

Esse token é basicamente a senha do bot.

Regras:

- não publique o token;
- não coloque em repositório público;
- use variável de ambiente ou arquivo `.env`;
- se vazar, gere outro token no `@BotFather`.

---

### 3.4. Deep link do Telegram

Para ligar uma tarefa do site a uma pessoa no Telegram, usa-se um link especial:

```text
https://t.me/NOME_DO_BOT?start=ID_DA_TAREFA
```

Exemplo:

```text
https://t.me/meueditorvideos_bot?start=abc123
```

Quando o usuário toca nesse link, o Telegram abre o bot e envia ao bot:

```text
/start abc123
```

Assim o sistema consegue saber:

```text
tarefa abc123 pertence ao chat_id 123456789
```

Importante: o payload do `start` deve ser curto. Use um identificador seguro, sem espaços, com caracteres seguros como letras, números, `_` e `-`. Uma boa prática é usar algo como:

```python
import secrets
job_id = secrets.token_urlsafe(16)
```

Isso gera algo parecido com:

```text
zR4j9TQmEXAMPLE_ab12
```

---

## 4. Limite de tamanho de arquivo

Este ponto é essencial.

Na API normal do Telegram Bot, o envio direto por upload multipart costuma ter limite de aproximadamente:

```text
50 MB para arquivos em geral
```

Então o fluxo inicial recomendado é:

```python
if arquivo <= 50 MB:
    enviar direto pelo Telegram
else:
    enviar link de download pelo Telegram
```

Existe uma opção avançada chamada **Telegram Bot API Server local**, que permite subir arquivos bem maiores, até cerca de 2000 MB, mas ela exige configuração extra no computador/servidor.

Recomendação prática:

1. Primeiro implemente a versão simples com limite de 50 MB.
2. Depois, se necessário, implemente:
   - servidor local do Telegram Bot API; ou
   - upload para storage e envio de link.

---

## 5. Arquitetura recomendada

### 5.1. Versão simples para começar

```text
[Site]
  ↓ cria tarefa com job_id
[Banco/arquivo de tarefas]
  ↓
[Bot Listener Python]
  - escuta /start job_id
  - salva chat_id na tarefa
  ↓
[Worker Python de edição]
  - processa vídeo
  - gera arquivo final
  - consulta chat_id da tarefa
  - envia arquivo pelo Telegram
```

---

### 5.2. Estados possíveis de uma tarefa

Uma tarefa pode ter estes estados:

```text
created              tarefa criada
waiting_telegram     esperando usuário abrir o bot
telegram_connected   chat_id salvo
processing           vídeo sendo processado
finished             vídeo gerado
sent                 arquivo enviado pelo Telegram
failed               erro
```

---

## 6. Passo 1 — Criar o bot no Telegram

No celular ou computador:

1. Abra o Telegram.
2. Pesquise por:

```text
@BotFather
```

3. Abra a conversa com o BotFather oficial.
4. Envie:

```text
/newbot
```

5. Escolha o nome de exibição do bot.

Exemplo:

```text
Editor Automático de Vídeos
```

6. Escolha o username do bot. Ele precisa terminar com `bot`.

Exemplo:

```text
meueditorvideos_bot
```

7. O BotFather vai devolver um token.

Guarde:

```text
TELEGRAM_BOT_TOKEN=seu_token_aqui
TELEGRAM_BOT_USERNAME=meueditorvideos_bot
```

---

## 7. Passo 2 — Criar projeto Python básico

Crie uma pasta para a integração:

```bash
mkdir telegram_delivery
cd telegram_delivery
```

Crie um ambiente virtual, se quiser:

```bash
python -m venv .venv
```

Ative o ambiente virtual.

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Windows CMD:

```cmd
.venv\Scripts\activate.bat
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Instale dependências:

```bash
pip install requests python-dotenv
```

Crie `requirements.txt`:

```txt
requests
python-dotenv
```

---

## 8. Passo 3 — Criar `.env`

Crie um arquivo chamado `.env`:

```env
TELEGRAM_BOT_TOKEN=COLE_AQUI_O_TOKEN_DO_BOT
TELEGRAM_BOT_USERNAME=COLE_AQUI_O_USERNAME_DO_BOT_SEM_ARROBA
TELEGRAM_API_BASE_URL=https://api.telegram.org
```

Exemplo:

```env
TELEGRAM_BOT_TOKEN=1234567890:AAExampleExampleExample
TELEGRAM_BOT_USERNAME=meueditorvideos_bot
TELEGRAM_API_BASE_URL=https://api.telegram.org
```

Crie também `.gitignore`:

```gitignore
.env
.venv/
__pycache__/
*.pyc
```

---

## 9. Passo 4 — Testar se o token funciona

Crie `test_get_me.py`:

```python
import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")

url = f"{BASE_URL}/bot{TOKEN}/getMe"
response = requests.get(url, timeout=30)

print(response.status_code)
print(response.text)
```

Execute:

```bash
python test_get_me.py
```

Resposta esperada:

```json
{
  "ok": true,
  "result": {
    "id": 1234567890,
    "is_bot": true,
    "first_name": "Editor Automático de Vídeos",
    "username": "meueditorvideos_bot"
  }
}
```

Se `ok` for `true`, o token está funcionando.

---

## 10. Passo 5 — Abrir o bot no celular

No celular, abra:

```text
https://t.me/SEU_BOT_USERNAME
```

Exemplo:

```text
https://t.me/meueditorvideos_bot
```

Clique em:

```text
Start
```

ou envie:

```text
/start
```

---

## 11. Passo 6 — Descobrir o `chat_id`

Crie `get_chat_id.py`:

```python
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")

url = f"{BASE_URL}/bot{TOKEN}/getUpdates"
response = requests.get(url, timeout=30)
data = response.json()

print(json.dumps(data, indent=2, ensure_ascii=False))
```

Execute:

```bash
python get_chat_id.py
```

Procure no JSON:

```json
"chat": {
  "id": 123456789,
  "first_name": "Seu Nome",
  "type": "private"
}
```

Esse `id` é o `chat_id`.

---

## 12. Passo 7 — Enviar uma mensagem de teste

Crie `send_message_test.py`:

```python
import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
CHAT_ID = "COLE_AQUI_O_CHAT_ID"

url = f"{BASE_URL}/bot{TOKEN}/sendMessage"

response = requests.post(
    url,
    data={
        "chat_id": CHAT_ID,
        "text": "Olá! O bot está funcionando."
    },
    timeout=30
)

print(response.status_code)
print(response.text)
```

Execute:

```bash
python send_message_test.py
```

Se tudo estiver certo, o celular recebe:

```text
Olá! O bot está funcionando.
```

---

## 13. Passo 8 — Enviar um arquivo de teste

Coloque um arquivo pequeno na pasta, por exemplo:

```text
teste.mp4
```

Crie `send_file_test.py`:

```python
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
CHAT_ID = "COLE_AQUI_O_CHAT_ID"
FILE_PATH = Path("teste.mp4")

url = f"{BASE_URL}/bot{TOKEN}/sendDocument"

with FILE_PATH.open("rb") as file_obj:
    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "caption": "Arquivo de teste enviado pelo computador."
        },
        files={
            "document": (FILE_PATH.name, file_obj)
        },
        timeout=(10, 600)
    )

print(response.status_code)
print(response.text)
```

Execute:

```bash
python send_file_test.py
```

Se funcionar, você já provou:

```text
computador → Telegram Bot → celular
```

---

## 14. `sendDocument` ou `sendVideo`?

### Opção recomendada: `sendDocument`

Use quando você quer entregar o arquivo final exatamente como arquivo.

Vantagens:

- preserva melhor o arquivo;
- o usuário pode baixar como documento;
- é mais adequado para arquivos finais de edição.

Endpoint:

```text
POST https://api.telegram.org/bot<TOKEN>/sendDocument
```

---

### Opção alternativa: `sendVideo`

Use quando você quer que apareça como vídeo reproduzível diretamente na conversa.

Endpoint:

```text
POST https://api.telegram.org/bot<TOKEN>/sendVideo
```

Para o seu caso, comece com `sendDocument`.

---

## 15. Implementação recomendada com múltiplas tarefas e múltiplos usuários

A partir daqui está a estrutura mais adequada para integrar com o seu site e o seu programa de edição.

Vamos usar SQLite porque:

- é simples;
- não precisa servidor separado;
- funciona bem para MVP;
- evita problemas de concorrência melhores que JSON puro.

---

## 16. Estrutura de arquivos recomendada

```text
telegram_delivery/
  .env
  .gitignore
  requirements.txt
  db.py
  bot_listener.py
  telegram_sender.py
  create_job_example.py
  deliver_job_example.py
  jobs.sqlite3
```

---

## 17. Banco de dados `db.py`

Crie `db.py`:

```python
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path("jobs.sqlite3")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                input_url TEXT,
                status TEXT NOT NULL,
                telegram_chat_id INTEGER,
                output_path TEXT,
                delivery_status TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def create_job(job_id, input_url):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, input_url, status, delivery_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, input_url, "waiting_telegram", "not_sent", now, now)
        )
        conn.commit()


def attach_telegram_chat(job_id, chat_id):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET telegram_chat_id = ?, status = CASE
                WHEN status = 'waiting_telegram' THEN 'telegram_connected'
                ELSE status
            END,
            updated_at = ?
            WHERE job_id = ?
            """,
            (chat_id, now, job_id)
        )
        conn.commit()


def get_job(job_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,)
        ).fetchone()
        return dict(row) if row else None


def set_job_processing(job_id):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
            ("processing", now, job_id)
        )
        conn.commit()


def set_job_finished(job_id, output_path):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, output_path = ?, updated_at = ?
            WHERE job_id = ?
            """,
            ("finished", output_path, now, job_id)
        )
        conn.commit()


def set_job_sent(job_id):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, delivery_status = ?, updated_at = ?
            WHERE job_id = ?
            """,
            ("sent", "sent", now, job_id)
        )
        conn.commit()


def set_job_failed(job_id, error_message):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, delivery_status = ?, error_message = ?, updated_at = ?
            WHERE job_id = ?
            """,
            ("failed", "failed", error_message, now, job_id)
        )
        conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Banco inicializado em: {DB_PATH.resolve()}")
```

Inicialize:

```bash
python db.py
```

---

## 18. Criar uma tarefa e gerar link do Telegram

Crie `create_job_example.py`:

```python
import os
import secrets
from dotenv import load_dotenv
from db import init_db, create_job

load_dotenv()

BOT_USERNAME = os.environ["TELEGRAM_BOT_USERNAME"]


def new_job_id():
    # Gera um ID seguro para deep link, curto e compatível com Telegram.
    return secrets.token_urlsafe(16)


def create_video_job(input_url):
    init_db()

    job_id = new_job_id()
    create_job(job_id, input_url)

    telegram_link = f"https://t.me/{BOT_USERNAME}?start={job_id}"

    return job_id, telegram_link


if __name__ == "__main__":
    job_id, link = create_video_job("https://exemplo.com/video")

    print("Tarefa criada:", job_id)
    print("Link para o usuário abrir no Telegram:")
    print(link)
```

Execute:

```bash
python create_job_example.py
```

Saída esperada:

```text
Tarefa criada: abc123...
Link para o usuário abrir no Telegram:
https://t.me/meueditorvideos_bot?start=abc123...
```

No site, depois que o usuário enviar o link do vídeo, você deve mostrar um botão:

```html
<a href="https://t.me/meueditorvideos_bot?start=abc123">Receber no Telegram</a>
```

---

## 19. Bot listener para capturar `/start job_id`

Este script fica rodando no computador/servidor. Ele escuta mensagens enviadas ao bot.

Crie `bot_listener.py`:

```python
import os
import time
import requests
from dotenv import load_dotenv
from db import init_db, get_job, attach_telegram_chat

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")


def api_url(method):
    return f"{BASE_URL}/bot{TOKEN}/{method}"


def send_message(chat_id, text):
    response = requests.post(
        api_url("sendMessage"),
        data={"chat_id": chat_id, "text": text},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def handle_start(chat_id, text):
    parts = text.split(maxsplit=1)

    if len(parts) == 1:
        send_message(
            chat_id,
            "Olá. Para receber um vídeo, envie o link pelo site e depois volte por meio do botão 'Receber no Telegram'."
        )
        return

    job_id = parts[1].strip()

    job = get_job(job_id)

    if not job:
        send_message(
            chat_id,
            "Não encontrei essa tarefa. Volte ao site e gere um novo link de recebimento."
        )
        return

    attach_telegram_chat(job_id, chat_id)

    send_message(
        chat_id,
        "Tudo certo. Vou te enviar o vídeo aqui quando ele estiver pronto. Pode fechar o site."
    )


def process_update(update):
    message = update.get("message")
    if not message:
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "")

    if not chat_id:
        return

    if text.startswith("/start"):
        handle_start(chat_id, text)
    else:
        send_message(
            chat_id,
            "Recebi sua mensagem. Para usar o sistema, envie o link do vídeo pelo site."
        )


def main():
    init_db()

    offset = None
    print("Bot listener iniciado. Aguardando mensagens...")

    while True:
        try:
            params = {
                "timeout": 30,
                "allowed_updates": '["message"]'
            }

            if offset is not None:
                params["offset"] = offset

            response = requests.get(
                api_url("getUpdates"),
                params=params,
                timeout=40
            )
            response.raise_for_status()
            data = response.json()

            for update in data.get("result", []):
                process_update(update)
                offset = update["update_id"] + 1

        except Exception as exc:
            print("Erro no bot_listener:", repr(exc))
            time.sleep(5)


if __name__ == "__main__":
    main()
```

Execute:

```bash
python bot_listener.py
```

Agora teste:

1. Rode `create_job_example.py`.
2. Copie o link gerado.
3. Abra o link no celular.
4. Clique em Start.
5. O bot deve responder:

```text
Tudo certo. Vou te enviar o vídeo aqui quando ele estiver pronto. Pode fechar o site.
```

---

## 20. Enviador de arquivos `telegram_sender.py`

Crie `telegram_sender.py`:

```python
import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org")
MAX_DIRECT_UPLOAD_MB = int(os.getenv("MAX_DIRECT_UPLOAD_MB", "50"))


def api_url(method):
    return f"{BASE_URL}/bot{TOKEN}/{method}"


def file_size_mb(path):
    path = Path(path)
    return path.stat().st_size / (1024 * 1024)


def send_message(chat_id, text):
    response = requests.post(
        api_url("sendMessage"),
        data={"chat_id": chat_id, "text": text},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def send_document(chat_id, file_path, caption=None):
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {file_path}")

    size_mb = file_size_mb(file_path)

    if size_mb > MAX_DIRECT_UPLOAD_MB:
        raise ValueError(
            f"Arquivo tem {size_mb:.2f} MB, maior que o limite configurado de {MAX_DIRECT_UPLOAD_MB} MB."
        )

    if caption is None:
        caption = "Seu arquivo está pronto."

    with file_path.open("rb") as file_obj:
        response = requests.post(
            api_url("sendDocument"),
            data={
                "chat_id": chat_id,
                "caption": caption
            },
            files={
                "document": (file_path.name, file_obj)
            },
            timeout=(10, 600)
        )

    response.raise_for_status()
    return response.json()


def send_video(chat_id, file_path, caption=None):
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {file_path}")

    size_mb = file_size_mb(file_path)

    if size_mb > MAX_DIRECT_UPLOAD_MB:
        raise ValueError(
            f"Arquivo tem {size_mb:.2f} MB, maior que o limite configurado de {MAX_DIRECT_UPLOAD_MB} MB."
        )

    if caption is None:
        caption = "Seu vídeo está pronto."

    with file_path.open("rb") as file_obj:
        response = requests.post(
            api_url("sendVideo"),
            data={
                "chat_id": chat_id,
                "caption": caption
            },
            files={
                "video": (file_path.name, file_obj)
            },
            timeout=(10, 600)
        )

    response.raise_for_status()
    return response.json()
```

---

## 21. Enviar resultado de uma tarefa

Crie `deliver_job_example.py`:

```python
import sys
from db import init_db, get_job, set_job_sent, set_job_failed
from telegram_sender import send_document, send_message


def deliver_job(job_id):
    init_db()

    job = get_job(job_id)

    if not job:
        raise RuntimeError(f"Tarefa não encontrada: {job_id}")

    chat_id = job.get("telegram_chat_id")
    output_path = job.get("output_path")

    if not chat_id:
        raise RuntimeError(f"Tarefa {job_id} ainda não tem telegram_chat_id.")

    if not output_path:
        raise RuntimeError(f"Tarefa {job_id} ainda não tem output_path.")

    try:
        send_message(chat_id, "Seu vídeo ficou pronto. Estou enviando o arquivo agora...")

        send_document(
            chat_id=chat_id,
            file_path=output_path,
            caption="Seu vídeo editado está pronto."
        )

        set_job_sent(job_id)
        print("Arquivo enviado com sucesso.")

    except Exception as exc:
        set_job_failed(job_id, str(exc))
        raise


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python deliver_job_example.py <job_id>")
        raise SystemExit(1)

    deliver_job(sys.argv[1])
```

---

## 22. Simular o final do processamento de vídeo

No seu programa real, quando terminar o vídeo, você deve chamar:

```python
set_job_finished(job_id, caminho_do_arquivo_final)
deliver_job(job_id)
```

Exemplo de simulação:

Crie `simulate_processing.py`:

```python
import sys
from db import init_db, set_job_processing, set_job_finished
from deliver_job_example import deliver_job


def simulate(job_id, output_path):
    init_db()

    set_job_processing(job_id)

    # Aqui no sistema real entraria:
    # baixar vídeo
    # cortar vídeo
    # renderizar arquivo final
    # gerar output_path

    set_job_finished(job_id, output_path)
    deliver_job(job_id)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python simulate_processing.py <job_id> <output_path>")
        raise SystemExit(1)

    simulate(sys.argv[1], sys.argv[2])
```

Teste completo:

```bash
python create_job_example.py
```

Abra o link no celular.

Depois:

```bash
python simulate_processing.py ID_DA_TAREFA caminho/para/video_editado.mp4
```

---

## 23. Como integrar com o site

Quando o usuário enviar um link no site:

1. Gerar `job_id`.
2. Salvar a tarefa no banco.
3. Retornar para a tela um botão do Telegram.

Pseudo-código:

```python
job_id = secrets.token_urlsafe(16)
create_job(job_id, input_url)
telegram_link = f"https://t.me/{BOT_USERNAME}?start={job_id}"
```

HTML:

```html
<p>Recebemos seu vídeo. Pode fechar esta página depois de conectar seu Telegram.</p>
<a href="https://t.me/meueditorvideos_bot?start=abc123">
  Receber resultado no Telegram
</a>
```

Mensagem recomendada:

```text
Seu vídeo foi enviado para edição.
Clique abaixo para conectar seu Telegram e receber o arquivo quando estiver pronto.
```

---

## 24. E se o vídeo terminar antes do usuário conectar o Telegram?

Isso pode acontecer.

Exemplo:

```text
Usuário envia link
↓
Sistema processa rápido
↓
Arquivo fica pronto
↓
Usuário ainda não clicou no Telegram
```

Nesse caso:

- salve `output_path` normalmente;
- deixe a tarefa com status `finished`;
- quando o usuário conectar o Telegram depois, o listener pode verificar se a tarefa já está pronta e enviar.

Melhoria no `handle_start` do `bot_listener.py`:

```python
if job.get("status") == "finished" and job.get("output_path"):
    send_message(chat_id, "Seu vídeo já está pronto. Enviaremos agora.")
    # Chamar deliver_job(job_id) aqui, ou colocar numa fila.
```

Cuidado: para evitar import circular, o ideal em produção é o listener apenas marcar `telegram_connected` e um worker separado cuidar dos envios pendentes.

---

## 25. E se o usuário conectar o Telegram antes do vídeo terminar?

Esse é o fluxo normal.

```text
Usuário conecta Telegram
↓
chat_id é salvo
↓
Worker termina vídeo
↓
Worker envia arquivo para chat_id
```

---

## 26. Como lidar com arquivos maiores que 50 MB

Você tem três caminhos.

---

### 26.1. Caminho A — comprimir o vídeo

Antes de enviar, tentar reduzir o tamanho:

```bash
ffmpeg -i input.mp4 -vcodec libx264 -crf 28 -preset veryfast -acodec aac output_comprimido.mp4
```

Quanto maior o `crf`, menor o arquivo e pior a qualidade.

Valores comuns:

```text
23 = boa qualidade, arquivo maior
28 = qualidade aceitável, arquivo menor
32 = qualidade mais baixa, arquivo bem menor
```

---

### 26.2. Caminho B — enviar link de download

Se o arquivo for grande, suba para algum lugar e envie o link pelo Telegram.

Opções:

- Google Drive;
- Cloudflare R2;
- Supabase Storage;
- Backblaze B2;
- S3;
- servidor próprio;
- pasta pública temporária.

Fluxo:

```text
Arquivo > 50 MB
↓
Upload para storage
↓
Gerar link de download
↓
Bot envia mensagem com link
```

Mensagem:

```text
Seu vídeo está pronto.
O arquivo ficou grande para envio direto pelo Telegram.
Baixe aqui:
https://...
```

---

### 26.3. Caminho C — Telegram Bot API Server local

O Telegram permite rodar um servidor local da Bot API. Com ele, é possível aumentar bastante o limite de upload, chegando a cerca de 2000 MB.

Arquitetura:

```text
Seu Python
↓
Bot API Server local no computador
↓
Telegram
↓
Usuário recebe arquivo grande
```

Na prática, em vez de usar:

```text
https://api.telegram.org
```

você usa algo como:

```text
http://127.0.0.1:8081
```

No `.env`:

```env
TELEGRAM_API_BASE_URL=http://127.0.0.1:8081
MAX_DIRECT_UPLOAD_MB=2000
```

Observações:

- é mais avançado;
- exige obter `api_id` e `api_hash` do Telegram;
- normalmente exige rodar um serviço separado;
- para começar, não é necessário;
- só implemente se realmente precisar enviar arquivos grandes diretamente pelo Telegram.

---

## 27. Rodar em segundo plano

### 27.1. Durante desenvolvimento

Abra dois terminais:

Terminal 1:

```bash
python bot_listener.py
```

Terminal 2:

```bash
python seu_worker_de_video.py
```

---

### 27.2. Em produção simples no Windows

Opções:

- usar Agendador de Tarefas;
- usar NSSM para transformar o script em serviço;
- usar um `.bat` que inicia o listener;
- deixar uma janela de terminal aberta para MVP.

---

### 27.3. Em produção simples no Linux

Criar serviço systemd.

Exemplo conceitual:

```ini
[Unit]
Description=Telegram Bot Listener
After=network.target

[Service]
WorkingDirectory=/caminho/telegram_delivery
ExecStart=/caminho/telegram_delivery/.venv/bin/python bot_listener.py
Restart=always
EnvironmentFile=/caminho/telegram_delivery/.env

[Install]
WantedBy=multi-user.target
```

---

## 28. Polling vs Webhook

### Polling/getUpdates

O script fica perguntando ao Telegram se há mensagens novas.

Vantagens:

- mais simples;
- não precisa domínio;
- não precisa HTTPS;
- funciona bem no computador local.

Desvantagens:

- precisa processo rodando;
- menos elegante para alto volume.

Recomendado para começar.

---

### Webhook

O Telegram chama uma URL sua quando chega mensagem nova.

Vantagens:

- mais profissional;
- melhor para servidor web.

Desvantagens:

- precisa endpoint público HTTPS;
- precisa domínio/túnel;
- mais configuração.

Não recomendo começar por webhook. Comece com `getUpdates`.

---

## 29. Cuidados de segurança

### 29.1. Não exponha o token

Errado:

```python
TOKEN = "123456789:ABC..."
```

Melhor:

```python
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
```

---

### 29.2. Use `job_id` aleatório

Não use IDs previsíveis como:

```text
1
2
3
```

Use:

```python
secrets.token_urlsafe(16)
```

Assim outra pessoa não consegue adivinhar links de tarefas.

---

### 29.3. Verifique se a tarefa existe antes de salvar `chat_id`

Nunca aceite `/start qualquercoisa` sem validar no banco.

---

### 29.4. Evite guardar arquivos para sempre

Defina política de limpeza:

```text
apagar arquivos finais depois de 24h, 7 dias, 30 dias etc.
```

---

## 30. Tratamento de erros recomendado

O envio pode falhar por:

- internet caiu;
- arquivo grande demais;
- token inválido;
- `chat_id` inexistente;
- bot bloqueado pelo usuário;
- timeout;
- arquivo não encontrado.

Recomendações:

1. Salvar erro em `error_message`.
2. Marcar tarefa como `failed` ou `delivery_failed`.
3. Tentar reenviar algumas vezes.
4. Se arquivo for grande, enviar link em vez de arquivo.

---

## 31. Melhorias futuras

Depois do MVP funcionar, melhorar com:

- fila de tarefas com Redis/RQ/Celery;
- storage para arquivos grandes;
- painel para ver tarefas;
- reenvio manual;
- limpeza automática de arquivos antigos;
- logs estruturados;
- webhook em vez de polling;
- Telegram Bot API Server local para arquivos grandes;
- botões inline no Telegram, exemplo: `Baixar`, `Reprocessar`, `Cancelar`.

---

## 32. Checklist para implementação por agente de IA

Entregáveis mínimos:

```text
[ ] Criar bot no @BotFather
[ ] Salvar TELEGRAM_BOT_TOKEN no .env
[ ] Salvar TELEGRAM_BOT_USERNAME no .env
[ ] Criar test_get_me.py
[ ] Confirmar que getMe retorna ok=true
[ ] Abrir bot no celular e clicar Start
[ ] Criar get_chat_id.py
[ ] Confirmar chat_id
[ ] Criar send_message_test.py
[ ] Confirmar mensagem no celular
[ ] Criar send_file_test.py
[ ] Confirmar arquivo no celular
[ ] Criar db.py com SQLite
[ ] Criar create_job_example.py
[ ] Criar bot_listener.py
[ ] Testar deep link: https://t.me/BOT?start=JOB_ID
[ ] Confirmar que chat_id é salvo no banco
[ ] Criar telegram_sender.py
[ ] Criar deliver_job_example.py
[ ] Integrar entrega ao final do programa de edição
[ ] Tratar arquivos maiores que 50 MB
[ ] Definir fallback para link/storage ou API local
```

---

## 33. Critérios de sucesso

A integração estará funcionando quando for possível executar este teste completo:

```text
1. Criar tarefa fake.
2. Gerar link do Telegram com job_id.
3. Abrir link no celular.
4. Bot responder confirmando conexão.
5. Simular arquivo final.
6. Rodar entrega da tarefa.
7. Receber arquivo no Telegram do celular.
8. Banco marcar tarefa como sent.
```

---

## 34. Prompt sugerido para agente de IA implementar

Você pode entregar este prompt para um agente de IA junto com este tutorial:

```text
Você deve implementar uma integração Python para entregar arquivos finais de edição de vídeo via Telegram Bot.

Contexto:
- O usuário envia um link em um site.
- O sistema cria uma tarefa com job_id aleatório.
- O site mostra um botão https://t.me/<bot_username>?start=<job_id>.
- Quando o usuário abre o bot, o bot recebe /start <job_id> e salva o telegram_chat_id naquela tarefa.
- Quando o worker de edição termina o vídeo, ele salva output_path e envia o arquivo para o chat_id via Telegram Bot API.

Requisitos:
- Usar Python.
- Usar requests e python-dotenv.
- Usar SQLite para armazenar jobs.
- Token e username devem vir do .env.
- Implementar polling com getUpdates, não webhook.
- Implementar envio com sendDocument.
- Implementar limite configurável MAX_DIRECT_UPLOAD_MB, padrão 50.
- Se arquivo exceder limite, lançar erro claro ou chamar fallback de link.
- Não hardcodar token.
- Não usar IDs sequenciais para job_id; usar secrets.token_urlsafe(16).
- Criar scripts de teste: getMe, sendMessage, sendDocument.
- Criar funções reutilizáveis para integrar ao worker real.

Arquivos esperados:
- requirements.txt
- .env.example
- db.py
- bot_listener.py
- telegram_sender.py
- create_job_example.py
- deliver_job_example.py
- simulate_processing.py
- README ou instruções de uso.

Critério de aceitação:
- Consigo criar uma tarefa, abrir o deep link no celular, salvar chat_id no SQLite, simular arquivo final e receber esse arquivo no Telegram.
```

---

## 35. Referências técnicas

- Telegram Bot Features / BotFather / criação de bots:  
  https://core.telegram.org/bots/features

- Telegram Bot API:  
  https://core.telegram.org/bots/api

- `sendDocument` e envio de arquivos:  
  https://core.telegram.org/bots/api#senddocument

- `getUpdates` e long polling:  
  https://core.telegram.org/bots/api#getupdates

- Deep linking no Telegram:  
  https://core.telegram.org/bots/features#deep-linking

- Servidor local da Telegram Bot API:  
  https://github.com/tdlib/telegram-bot-api

---

## 36. Resumo final

Para o seu caso, o caminho mais simples é:

```text
1. Criar bot no @BotFather.
2. Pegar token.
3. Usuário abre o bot pelo link gerado pelo site.
4. Bot salva chat_id associado à tarefa.
5. Programa de edição termina o vídeo.
6. Python envia o arquivo usando sendDocument.
7. Se o arquivo for grande demais, enviar link ou configurar Bot API Server local.
```

Comece com a versão simples. Depois que ela funcionar, evolua para arquivos grandes e produção.
