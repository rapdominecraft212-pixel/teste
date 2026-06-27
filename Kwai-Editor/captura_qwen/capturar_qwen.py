"""
Capturador completo de estrutura do Qwen AI.

Abre o Chrome com o chrome_profile do projeto (mantém login Qwen),
injeta um observer que captura TUDO que acontece no DOM:
- Mutações (nós adicionados/removidos/texto alterado)
- Cliques, inputs, scrolls
- Requisições fetch/XHR (com request E response bodies)
- Mensagens do console
- Snapshots de HTML em momentos críticos
- Screenshots periódicos

Quando o usuário fecha o navegador, tudo é compactado num ZIP.
"""
import asyncio
import json
import sys
import zipfile
import atexit
from datetime import datetime
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERRO: Playwright nao instalado.")
    print("Rode: pip install playwright && python -m playwright install chromium")
    sys.exit(1)


# ============================================================
# CONFIGURAÇÕES
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent


def encontrar_chrome_profile():
    """
    Procura o chrome_profile em vários locais possíveis:
    - No mesmo diretório do script
    - Em diretórios ancestrais (subindo até 5 níveis)
    - Em siblings de diretórios ancestrais

    Isso torna o script robusto independente de onde o zip foi extraído.
    """
    candidatos = []

    # 1. Mesmo diretório do script
    candidatos.append(SCRIPT_DIR / "Playwright" / "chrome_profile")

    # 2. Subindo até 5 níveis de diretórios ancestrais
    ancestral = SCRIPT_DIR
    for i in range(5):
        ancestral = ancestral.parent
        if ancestral == ancestral.parent:  # chegou na raiz
            break
        candidatos.append(ancestral / "Playwright" / "chrome_profile")

    # 3. Procura em toda a árvore a partir do desktop (mais agressivo)
    # Só faz isso se os candidatos acima falharem
    for c in candidatos:
        if c.exists():
            return c

    # 4. Busca agressiva: procura por chrome_profile em todo o caminho do script
    # e em pastas relacionadas
    print("  Procura rapida falhou. Fazendo busca agressiva...")
    busca_inicio = Path.home() / "Desktop"
    if not busca_inicio.exists():
        busca_inicio = Path.home()

    # Procura por pastas "video-editor*" no Desktop
    for video_editor_dir in busca_inicio.glob("video-editor*"):
        if video_editor_dir.is_dir():
            candidato = video_editor_dir / "Playwright" / "chrome_profile"
            if candidato.exists():
                return candidato

    # 5. Última tentativa: procura em tudo que tem "video-editor" no nome
    # dentro do Desktop (até 3 níveis de profundidade)
    for path in busca_inicio.rglob("chrome_profile"):
        if path.is_dir() and "video-editor" in str(path).lower():
            # Verifica se o parent tem estrutura do projeto
            if (path.parent.parent / "Playwright" / "qwen_reply_async.py").exists() or \
               (path.parent.parent / "capturar_qwen.py").exists():
                return path

    return None


PROFILE_DIR = encontrar_chrome_profile()

if PROFILE_DIR is None:
    # Não encontrou — usa um perfil temporário e avisa o usuário
    PROFILE_DIR = SCRIPT_DIR / "Playwright" / "chrome_profile"
    print(f"\n  AVISO: chrome_profile nao encontrado automaticamente.")
    print(f"  Procurado em:")
    print(f"    - {SCRIPT_DIR / 'Playwright' / 'chrome_profile'}")
    print(f"    - Diretorios ancestrais")
    print(f"    - Desktop/video-editor*")
    print(f"\n  O Chrome vai abrir SEM perfil salvo.")
    print(f"  Voce precisara fazer login no Qwen manualmente.\n")

LOG_DIR = SCRIPT_DIR / "captura_logs"
SNAP_DIR = LOG_DIR / "snapshots"
SHOT_DIR = LOG_DIR / "screenshots"
LOG_FILE = LOG_DIR / "events.jsonl"
SUMMARY_FILE = LOG_DIR / "summary.txt"

# Cria pastas
for d in (LOG_DIR, SNAP_DIR, SHOT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Abre arquivo de log (mode append pra não perder nada se reiniciar)
log_fp = open(LOG_FILE, "w", encoding="utf-8", buffering=1)  # line-buffered

# Contadores
event_count = 0
snapshot_count = 0
stats = {
    "mutations": 0,
    "clicks": 0,
    "inputs": 0,
    "fetch_requests": 0,
    "fetch_responses": 0,
    "xhr_requests": 0,
    "xhr_responses": 0,
    "console_messages": 0,
    "snapshots": 0,
    "screenshots": 0,
    "errors": 0,
}


# ============================================================
# LOGGING
# ============================================================
def log_event(event_type, data):
    """Escreve um evento no log JSONL."""
    global event_count
    event_count += 1
    event = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "type": event_type,
        **data,
    }
    try:
        line = json.dumps(event, ensure_ascii=False, default=str)
        log_fp.write(line + "\n")
    except Exception as e:
        log_fp.write(json.dumps({
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "type": "log_error",
            "error": str(e),
            "original_type": event_type,
        }) + "\n")


def increment_stat(key):
    stats[key] = stats.get(key, 0) + 1


# ============================================================
# OBSERVER JAVASCRIPT (injetado antes da página carregar)
# ============================================================
INIT_SCRIPT = r"""
(() => {
    if (window.__qwenCaptureLoaded) return;
    window.__qwenCaptureLoaded = true;
    window.__pendingEvents = [];

    function emit(type, data) {
        const evt = { type, data, ts: new Date().toISOString() };
        window.__pendingEvents.push(evt);
        // Também chama a função exposta pelo Playwright (se disponível)
        if (typeof window.__logEvent === 'function') {
            try { window.__logEvent(type, data); } catch(e) {}
        }
    }

    // --- Helper: seletor CSS de um elemento ---
    function getSelector(el) {
        if (!el || el.nodeType !== 1) return 'unknown';
        let s = (el.tagName || '').toLowerCase();
        if (el.id) s += '#' + el.id;
        if (el.className && typeof el.className === 'string') {
            const cls = el.className.trim().split(/\s+/).filter(Boolean).join('.');
            if (cls) s += '.' + cls;
        }
        return s.substring(0, 300);
    }

    // --- Helper: HTML truncado ---
    function safeHtml(el, maxLen) {
        if (!el) return null;
        try {
            const html = el.outerHTML || '';
            return html.length > maxLen ? html.substring(0, maxLen) + '...[TRUNCATED]' : html;
        } catch(e) { return null; }
    }

    function safeText(el, maxLen) {
        if (!el) return null;
        try {
            const txt = el.innerText || el.textContent || '';
            return txt.length > maxLen ? txt.substring(0, maxLen) + '...[TRUNCATED]' : txt;
        } catch(e) { return null; }
    }

    // ============================================================
    // 1. HOOK DE FETCH
    // ============================================================
    const originalFetch = window.fetch;
    window.fetch = async function(...args) {
        const [resource, config] = args;
        const method = (config && config.method) || 'GET';
        const url = typeof resource === 'string' ? resource : (resource && resource.url) || '';

        // Loga request
        let reqBody = null;
        if (config && config.body) {
            try {
                reqBody = typeof config.body === 'string'
                    ? config.body.substring(0, 10000)
                    : '[non-string body]';
            } catch(e) { reqBody = '[unreadable]'; }
        }

        emit('fetch_request', { method, url, body: reqBody });

        let response;
        try {
            response = await originalFetch.apply(this, args);
        } catch(err) {
            emit('fetch_error', { method, url, error: String(err).substring(0, 500) });
            throw err;
        }

        // Loga response (clona pra não consumir o body)
        try {
            const clone = response.clone();
            const text = await clone.text();
            emit('fetch_response', {
                method, url,
                status: response.status,
                body: text.substring(0, 50000)
            });
        } catch(e) {
            emit('fetch_response_error', { method, url, error: String(e).substring(0, 500) });
        }

        return response;
    };

    // ============================================================
    // 2. HOOK DE XMLHttpRequest
    // ============================================================
    const XHRopen = XMLHttpRequest.prototype.open;
    const XHRsend = XMLHttpRequest.prototype.send;

    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        this.__method = method;
        this.__url = url;
        return XHRopen.apply(this, [method, url, ...rest]);
    };

    XMLHttpRequest.prototype.send = function(body) {
        emit('xhr_request', {
            method: this.__method,
            url: this.__url,
            body: body ? String(body).substring(0, 10000) : null
        });

        this.addEventListener('load', function() {
            emit('xhr_response', {
                method: this.__method,
                url: this.__url,
                status: this.status,
                body: (this.responseText || '').substring(0, 50000)
            });
        });

        this.addEventListener('error', function() {
            emit('xhr_error', {
                method: this.__method,
                url: this.__url,
                status: this.status
            });
        });

        return XHRsend.apply(this, [body]);
    };

    // ============================================================
    // 3. MUTATION OBSERVER
    // ============================================================
    let mutationBuffer = [];
    let flushTimer = null;

    function flushMutations() {
        if (mutationBuffer.length === 0) return;
        emit('mutations_batch', { mutations: mutationBuffer.splice(0, mutationBuffer.length) });
    }

    function scheduleFlush() {
        if (flushTimer) return;
        flushTimer = setTimeout(() => {
            flushTimer = null;
            flushMutations();
        }, 100);  // Agrupa mutações a cada 100ms
    }

    const observer = new MutationObserver((mutations) => {
        for (const mut of mutations) {
            const target = getSelector(mut.target);

            if (mut.type === 'childList') {
                // Nós adicionados
                for (const node of mut.addedNodes) {
                    if (node.nodeType === 1) {  // Element
                        // Filtra: só loga se tiver classe/id ou for relevante
                        const isRelevant = node.className || node.id ||
                                           ['DIV', 'SPAN', 'P', 'PRE', 'CODE', 'BUTTON',
                            'svg', 'IMG', 'SECTION', 'ARTICLE'].includes(node.tagName);

                        if (isRelevant) {
                            mutationBuffer.push({
                                kind: 'added',
                                target: target,
                                tag: node.tagName,
                                class: typeof node.className === 'string' ? node.className.substring(0, 200) : '',
                                id: node.id || '',
                                html: safeHtml(node, 3000),
                                text: safeText(node, 500)
                            });
                            scheduleFlush();
                        }
                    }
                }
                // Nós removidos (só info básica)
                for (const node of mut.removedNodes) {
                    if (node.nodeType === 1) {
                        mutationBuffer.push({
                            kind: 'removed',
                            target: target,
                            tag: node.tagName,
                            class: typeof node.className === 'string' ? node.className.substring(0, 200) : ''
                        });
                        scheduleFlush();
                    }
                }
            } else if (mut.type === 'characterData') {
                const text = mut.target.textContent;
                if (text && text.trim().length > 0) {
                    mutationBuffer.push({
                        kind: 'text_changed',
                        target: target,
                        text: text.substring(0, 1000)
                    });
                    scheduleFlush();
                }
            } else if (mut.type === 'attributes') {
                // Só loga mudanças de classe, style, data-* (ignora outros atributos)
                const attrName = mut.attributeName;
                if (['class', 'style', 'data-role', 'data-state', 'aria-expanded',
                     'hidden', 'disabled'].includes(attrName)) {
                    mutationBuffer.push({
                        kind: 'attr_changed',
                        target: target,
                        attr: attrName,
                        old_value: mut.oldValue ? mut.oldValue.substring(0, 200) : null,
                        new_value: mut.target.getAttribute(attrName)
                            ? mut.target.getAttribute(attrName).substring(0, 200) : null
                    });
                    scheduleFlush();
                }
            }
        }
    });

    function startObserving() {
        observer.observe(document.body || document.documentElement, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeOldValue: true,
            characterData: true,
            characterDataOldValue: false
        });
        emit('observer_started', { url: window.location.href });
    }

    if (document.body) {
        startObserving();
    } else {
        document.addEventListener('DOMContentLoaded', startObserving);
    }

    // ============================================================
    // 4. CLIQUES
    // ============================================================
    document.addEventListener('click', (e) => {
        emit('click', {
            target: getSelector(e.target),
            text: safeText(e.target, 200),
            x: e.clientX,
            y: e.clientY,
            timestamp: e.timeStamp
        });
    }, true);

    // ============================================================
    // 5. INPUTS (textarea, input)
    // ============================================================
    let lastInputValue = '';
    let inputDebounce = null;

    document.addEventListener('input', (e) => {
        const t = e.target;
        if (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT') {
            const value = t.value || '';
            // Debounce: só loga 500ms após parar de digitar
            if (inputDebounce) clearTimeout(inputDebounce);
            inputDebounce = setTimeout(() => {
                emit('input', {
                    target: getSelector(t),
                    value: value.substring(0, 2000),
                    value_length: value.length
                });
                inputDebounce = null;
            }, 500);
        }
    }, true);

    // ============================================================
    // 6. CONSOLE
    // ============================================================
    const origLog = console.log;
    const origErr = console.error;
    const origWarn = console.warn;

    console.log = function(...args) {
        emit('console_log', { msg: args.map(a => {
            try { return typeof a === 'string' ? a : JSON.stringify(a); }
            catch(e) { return String(a); }
        }).join(' ').substring(0, 2000) });
        origLog.apply(console, args);
    };
    console.error = function(...args) {
        emit('console_error', { msg: args.map(a => {
            try { return typeof a === 'string' ? a : JSON.stringify(a); }
            catch(e) { return String(a); }
        }).join(' ').substring(0, 2000) });
        origErr.apply(console, args);
    };
    console.warn = function(...args) {
        emit('console_warn', { msg: args.map(a => {
            try { return typeof a === 'string' ? a : JSON.stringify(a); }
            catch(e) { return String(a); }
        }).join(' ').substring(0, 2000) });
        origWarn.apply(console, args);
    };

    // ============================================================
    // 7. EVENTOS ESPECÍFICOS DO QWEN
    // ============================================================

    // Detecta quando o botão de stop aparece/desaparece (resposta começou/terminou)
    let lastStopVisible = false;
    setInterval(() => {
        const stopBtn = document.querySelector('.stop-button');
        const isVisible = !!stopBtn;
        if (isVisible !== lastStopVisible) {
            lastStopVisible = isVisible;
            emit('stop_button_state', { visible: isVisible });

            // Se acabou de desaparecer (resposta terminou), captura detalhes
            if (!isVisible) {
                // Captura TODAS as mensagens do assistente
                const msgs = document.querySelectorAll('.qwen-chat-message-assistant');
                const last = msgs[msgs.length - 1];
                if (last) {
                    emit('response_complete_detailed', {
                        total_assistant_messages: msgs.length,
                        last_message_html: safeHtml(last, 50000),
                        last_message_text: safeText(last, 10000),
                        response_content_html: safeHtml(last.querySelector('.response-message-content'), 50000),
                        response_content_text: safeText(last.querySelector('.response-message-content'), 10000),
                        // Procura por blocos de thinking
                        thinking_blocks: Array.from(last.querySelectorAll('[class*="think"], [class*="reason"], details, summary')).map(el => ({
                            tag: el.tagName,
                            class: typeof el.className === 'string' ? el.className.substring(0, 200) : '',
                            html: safeHtml(el, 5000),
                            text: safeText(el, 2000),
                            is_open: el.open !== undefined ? el.open : null
                        })),
                        // Procura por code blocks
                        code_blocks: Array.from(last.querySelectorAll('pre, code, .code-block')).map(el => ({
                            tag: el.tagName,
                            class: typeof el.className === 'string' ? el.className.substring(0, 200) : '',
                            text: safeText(el, 5000)
                        }))
                    });
                }
            }
        }
    }, 300);

    // Detecta mudanças no seletor de modo (.mode-select-open)
    let lastModeText = '';
    setInterval(() => {
        const modeEl = document.querySelector('.mode-select-open');
        if (modeEl) {
            const txt = modeEl.innerText || '';
            if (txt !== lastModeText) {
                lastModeText = txt;
                emit('mode_select_changed', { text: txt.substring(0, 200) });
            }
        }
    }, 500);

    emit('init_script_loaded', { url: window.location.href });
})();
"""


# ============================================================
# SNAPSHOTS E SCREENSHOTS
# ============================================================
async def take_snapshot(page, label=""):
    """Tira snapshot do HTML completo + screenshot."""
    global snapshot_count
    snapshot_count += 1
    num = f"{snapshot_count:03d}"
    label_safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in label)[:50]

    try:
        html = await page.content()
        snap_path = SNAP_DIR / f"{num}_{label_safe}.html"
        snap_path.write_text(html, encoding="utf-8")
        increment_stat("snapshots")
        log_event("snapshot", {
            "label": label,
            "file": snap_path.name,
            "size": len(html),
            "url": page.url
        })
    except Exception as e:
        log_event("snapshot_error", {"label": label, "error": str(e)[:500]})
        increment_stat("errors")

    try:
        ss_path = SHOT_DIR / f"{num}_{label_safe}.png"
        await page.screenshot(path=str(ss_path), full_page=False)
        increment_stat("screenshots")
    except Exception as e:
        log_event("screenshot_error", {"label": label, "error": str(e)[:500]})


async def take_periodic_screenshot(page):
    """Screenshot a cada 3 segundos para contexto visual."""
    counter = 0
    while True:
        await asyncio.sleep(3)
        counter += 1
        try:
            ss_path = SHOT_DIR / f"periodic_{counter:04d}.png"
            await page.screenshot(path=str(ss_path), full_page=False)
            increment_stat("screenshots")
        except Exception:
            pass


# ============================================================
# WATCHER DO CICLO DE RESPOSTA
# ============================================================
async def watch_response_cycle(page):
    """Observa o botão stop para detectar início/fim de resposta."""
    stop_visible = False
    while True:
        await asyncio.sleep(0.3)
        try:
            has_stop = await page.evaluate("!!document.querySelector('.stop-button')")
            if has_stop and not stop_visible:
                stop_visible = True
                log_event("RESPONSE_STARTED", {})
                await take_snapshot(page, "response_started")
            elif not has_stop and stop_visible:
                stop_visible = False
                log_event("RESPONSE_COMPLETE", {})
                await take_snapshot(page, "response_complete")
                # Snapshot adicional 2s depois (DOM pode atualizar)
                await asyncio.sleep(2)
                await take_snapshot(page, "response_final_2s")
                # E mais 5s depois
                await asyncio.sleep(5)
                await take_snapshot(page, "response_final_7s")
        except Exception as e:
            log_event("watcher_error", {"error": str(e)[:500]})
            increment_stat("errors")


# ============================================================
# POLL DE EVENTOS DO OBSERVER JS
# ============================================================
async def poll_pending_events(page):
    """Puxa eventos acumulados pelo observer JS (caso expose_function falhe)."""
    while True:
        await asyncio.sleep(0.05)
        try:
            events = await page.evaluate("""() => {
                const evts = window.__pendingEvents || [];
                window.__pendingEvents = [];
                return evts;
            }""")
            for evt in events:
                evt_type = evt.get("type", "unknown")
                evt_data = {k: v for k, v in evt.items() if k not in ("type", "ts")}
                # Atualiza stats
                if evt_type == "mutations_batch":
                    increment_stat("mutations")
                elif evt_type == "click":
                    increment_stat("clicks")
                elif evt_type == "input":
                    increment_stat("inputs")
                elif evt_type == "fetch_request":
                    increment_stat("fetch_requests")
                elif evt_type == "fetch_response":
                    increment_stat("fetch_responses")
                elif evt_type == "xhr_request":
                    increment_stat("xhr_requests")
                elif evt_type == "xhr_response":
                    increment_stat("xhr_responses")
                elif evt_type.startswith("console"):
                    increment_stat("console_messages")
                log_event(evt_type, evt_data)
        except Exception:
            pass


# ============================================================
# CONFIGURAR PÁGINA (init script + listeners)
# ============================================================
async def setup_page(page):
    """Configura uma página com observer e listeners."""
    try:
        await page.expose_function("__logEvent", lambda t, d: log_event(t, d))
    except Exception:
        pass  # Já foi exposta

    await page.add_init_script(INIT_SCRIPT)

    # Console messages via Playwright (backup)
    def on_console(msg):
        increment_stat("console_messages")
        log_event("browser_console", {
            "type": msg.type,
            "text": msg.text[:2000]
        })

    def on_pageerror(err):
        increment_stat("errors")
        log_event("page_error", {"message": str(err)[:2000]})

    def on_request(request):
        # Log mínimo de requests (só URL/method) — bodies já vêm pelo hook JS
        url = request.url
        # Filtra só requests do Qwen (ignora analytics, fonts, etc.)
        if "qwen.ai" in url or "aliyun" in url:
            log_event("network_request", {
                "method": request.method,
                "url": url[:500],
                "resource_type": request.resource_type
            })

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("request", on_request)


# ============================================================
# MAIN ASSÍNCRONO
# ============================================================
async def main():
    if not PROFILE_DIR.exists():
        print(f"\n  AVISO: chrome_profile nao encontrado em: {PROFILE_DIR}")
        print(f"  Criando perfil temporario novo...")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  Voce precisara fazer login no Qwen manualmente.\n")

    log_event("script_start", {
        "profile_dir": str(PROFILE_DIR),
        "log_dir": str(LOG_DIR),
        "python_version": sys.version,
    })

    print("=" * 60)
    print("  CAPTURA DE ESTRUTURA DO QWEN AI")
    print("=" * 60)
    print()
    print(f"  Perfil Chrome: {PROFILE_DIR}")
    print(f"  Logs em:       {LOG_DIR}")
    print()
    print("  >>> Chrome vai abrir com o Qwen. <<<")
    print("  Faca o processo completo:")
    print("    1. (se preciso) faca login")
    print("    2. clique no botao de upload (mode-select)")
    print("    3. selecione um video")
    print("    4. digite o prompt")
    print("    5. clique em enviar")
    print("    6. aguarde a resposta completar")
    print("    7. feche o navegador (X)")
    print()
    print("  Logs estao sendo gravados em tempo real.")
    print()

    async with async_playwright() as pw:
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="chrome",  # Usa Chrome real do Windows
                headless=False,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                ],
                viewport=None,  # Usa tamanho da janela
                no_viewport=True,
            )
        except Exception as e:
            print(f"  ERRO ao abrir Chrome: {e}")
            print("  Tentando sem channel=chrome (usando Chromium bundled)...")
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
                viewport=None,
                no_viewport=True,
            )

        # Configura página inicial
        page = context.pages[0] if context.pages else await context.new_page()
        await setup_page(page)

        # Configura futuras páginas abertas (new tabs)
        context.on("page", lambda p: asyncio.create_task(setup_page(p)))

        # Navega para o Qwen
        log_event("navigating", {"url": "https://chat.qwen.ai/"})
        print("  Navegando para https://chat.qwen.ai/ ...")
        try:
            await page.goto("https://chat.qwen.ai/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log_event("navigation_error", {"error": str(e)[:500]})
            print(f"  Erro na navegacao: {e}")

        await asyncio.sleep(3)
        await take_snapshot(page, "initial_load")

        print("  Pagina carregada. Faca o processo e feche o navegador quando terminar.")
        print()

        # Inicia tarefas em paralelo
        tasks = [
            asyncio.create_task(poll_pending_events(page)),
            asyncio.create_task(watch_response_cycle(page)),
            asyncio.create_task(take_periodic_screenshot(page)),
        ]

        # Espera o contexto fechar (usuário fecha o navegador)
        try:
            await context.wait_for_event("close", timeout=0)
        except Exception:
            pass

        # Cancela tarefas
        for t in tasks:
            t.cancel()
            try:
                await t
            except Exception:
                pass

    log_event("script_end", {"total_events": event_count})

    # Escreve sumário
    write_summary()

    # Compacta tudo
    final_zip = create_final_zip()

    print()
    print("=" * 60)
    print("  CAPTURA CONCLUIDA!")
    print("=" * 60)
    print(f"  Eventos capturados: {event_count}")
    print(f"  Snapshots:          {stats['snapshots']}")
    print(f"  Screenshots:        {stats['screenshots']}")
    print()
    print(f"  Arquivo final:")
    print(f"    {final_zip}")
    print()
    print("  Envie esse arquivo ZIP de volta para analise.")
    print()


def write_summary():
    """Escreve sumário legível por humanos."""
    lines = [
        "=" * 60,
        "  SUMARIO DA CAPTURA",
        "=" * 60,
        f"  Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  Total de eventos: {event_count}",
        "",
        "  Estatisticas:",
        f"    - Mutações DOM (batches): {stats['mutations']}",
        f"    - Cliques:                {stats['clicks']}",
        f"    - Inputs:                 {stats['inputs']}",
        f"    - Fetch requests:         {stats['fetch_requests']}",
        f"    - Fetch responses:        {stats['fetch_responses']}",
        f"    - XHR requests:           {stats['xhr_requests']}",
        f"    - XHR responses:          {stats['xhr_responses']}",
        f"    - Console messages:       {stats['console_messages']}",
        f"    - Snapshots HTML:         {stats['snapshots']}",
        f"    - Screenshots:            {stats['screenshots']}",
        f"    - Erros:                  {stats['errors']}",
        "",
        "  Arquivos gerados:",
        f"    - {LOG_FILE.name} (log estruturado JSONL)",
        f"    - {SNAP_DIR.name}/ (snapshots de HTML)",
        f"    - {SHOT_DIR.name}/ (screenshots)",
        "",
        "  Momentos críticos capturados (procure no events.jsonl por):",
        "    - 'RESPONSE_STARTED'    → início da resposta do Qwen",
        "    - 'RESPONSE_COMPLETE'   → resposta terminou",
        "    - 'response_complete_detailed' → HTML + texto da resposta final",
        "    - 'fetch_response'      → respostas das APIs do Qwen",
        "",
    ]

    # Lista snapshots
    snaps = sorted(SNAP_DIR.glob("*.html"))
    if snaps:
        lines.append("  Snapshots HTML capturados:")
        for s in snaps:
            size_kb = s.stat().st_size / 1024
            lines.append(f"    - {s.name}  ({size_kb:.1f} KB)")
        lines.append("")

    SUMMARY_FILE.write_text("\n".join(lines), encoding="utf-8")


def create_final_zip():
    """Compacta tudo num ZIP final."""
    zip_name = f"captura_qwen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path = LOG_DIR / zip_name

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # events.jsonl
        if LOG_FILE.exists():
            zf.write(LOG_FILE, "events.jsonl")

        # summary.txt
        if SUMMARY_FILE.exists():
            zf.write(SUMMARY_FILE, "summary.txt")

        # snapshots/
        for f in SNAP_DIR.glob("*.html"):
            zf.write(f, f"snapshots/{f.name}")

        # screenshots/
        for f in SHOT_DIR.glob("*.png"):
            zf.write(f, f"screenshots/{f.name}")

    return zip_path


# ============================================================
# CLEANUP
# ============================================================
@atexit.register
def cleanup():
    try:
        log_fp.flush()
        log_fp.close()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuario.")
    except Exception as e:
        print(f"\nERRO FATAL: {e}")
        log_event("fatal_error", {"error": str(e)[:2000]})
    finally:
        cleanup()
