"""
QwenSession — Captura e restaura sessão mínima do Qwen (cookies + localStorage + IndexedDB).

DESIGN:
    Em vez de salvar perfil Chrome completo (50-300MB por conta, com cache inútil),
    capturamos apenas o que mantém a sessão logada:
    - Cookies (especialmente os persistentes com `expires`)
    - LocalStorage (tokens JWT, sessão)
    - SessionStorage (estado temporário)
    - IndexedDB (algumas SPAs guardam sessão aqui)

TAMANHO:
    ~20-150KB por conta (vs 50-300MB do perfil completo)
    7 contas = ~140KB-1MB total
    ZIP = ~300KB (portável via Google Drive/email)

PERSISTÊNCIA:
    Sessão dura o mesmo tempo que duraria no Chrome normal (7-30 dias para Qwen).
    Servidor Qwen vê exatamente o mesmo cliente — não tem como distinguir de Chrome real.

USO:
    # Capturar (depois de login manual em Chrome visível)
    sessao = await extrair_sessao(ctx, page)
    salvar_sessao("Playwright/sessions/conta_1.json", sessao)

    # Restaurar (em novo Playwright context)
    sessao = carregar_sessao("Playwright/sessions/conta_1.json")
    await restaurar_sessao(ctx, page, sessao)
"""
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional


# Diretório padrão para sessões salvas
SESSIONS_DIR = Path(__file__).resolve().parent / "sessions"


def get_session_path(account_id: str) -> Path:
    """Retorna path do arquivo de sessão para uma conta."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR / f"{account_id}.json"


def sessao_existe(account_id: str) -> bool:
    """Verifica se há sessão salva para a conta."""
    return get_session_path(account_id).exists()


def carregar_sessao(account_id: str) -> Optional[dict]:
    """Carrega sessão de arquivo JSON. Retorna None se não existir."""
    path = get_session_path(account_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [session] Erro ao carregar {path}: {e}", flush=True)
        return None


def salvar_sessao(account_id: str, sessao: dict) -> Path:
    """Salva sessão como JSON compacto. Retorna path do arquivo."""
    path = get_session_path(account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sessao, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    size_kb = path.stat().st_size / 1024
    print(f"  [session] Sessão salva: {path.name} ({size_kb:.1f}KB)", flush=True)
    return path


def listar_sessoes() -> list[dict]:
    """Lista todas as sessões salvas com metadados."""
    if not SESSIONS_DIR.exists():
        return []
    result = []
    for path in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append({
                "account_id": path.stem,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "saved_at": data.get("saved_at", "?"),
                "url": data.get("url", "?"),
                "cookies_count": len(data.get("cookies", [])),
                "local_storage_keys": len(data.get("local_storage", {})),
                "indexeddb_dbs": len(data.get("indexeddb", {})),
            })
        except Exception as e:
            result.append({
                "account_id": path.stem,
                "path": str(path),
                "error": str(e),
            })
    return result


def remover_sessao(account_id: str) -> bool:
    """Remove arquivo de sessão. Retorna True se removido."""
    path = get_session_path(account_id)
    if path.exists():
        path.unlink()
        return True
    return False


# === Captura ===

async def extrair_sessao(ctx, page) -> dict:
    """Extrai sessão mínima de um Playwright BrowserContext + Page.

    Deve ser chamado DEPOIS que o usuário fez login manualmente (textarea visível).
    Captura:
    - Cookies (todos, via ctx.cookies())
    - LocalStorage (via JS eval)
    - SessionStorage (via JS eval)
    - IndexedDB (via JS eval — mais complexo)

    Args:
        ctx: Playwright BrowserContext (já autenticado)
        page: Playwright Page (na URL do Qwen)

    Returns:
        dict com todos os dados de sessão, pronto para json.dumps
    """
    print(f"  [session] Extraindo sessão de {page.url}...", flush=True)

    # 1. Cookies — API nativa do Playwright (estruturado, limpo)
    cookies = await ctx.cookies()
    print(f"  [session] Cookies: {len(cookies)} capturados", flush=True)

    # 2. LocalStorage
    local_storage = await page.evaluate("""
        () => {
            const result = {};
            try {
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    result[key] = localStorage.getItem(key);
                }
            } catch (e) {
                console.error('localStorage erro:', e);
            }
            return result;
        }
    """)
    print(f"  [session] LocalStorage: {len(local_storage)} chaves", flush=True)

    # 3. SessionStorage
    session_storage = await page.evaluate("""
        () => {
            const result = {};
            try {
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    result[key] = sessionStorage.getItem(key);
                }
            } catch (e) {
                console.error('sessionStorage erro:', e);
            }
            return result;
        }
    """)
    print(f"  [session] SessionStorage: {len(session_storage)} chaves", flush=True)

    # 4. IndexedDB — mais complexo (precisa enumerar databases + object stores)
    # Pode falhar silenciosamente se não houver DBs; tratamos como opcional
    indexeddb = await page.evaluate("""
        async () => {
            const result = {};
            try {
                if (!indexedDB.databases) {
                    // Alguns navegadores não suportam indexedDB.databases()
                    // Fallback: tentar nomes conhecidos do Qwen
                    return result;
                }
                const dbs = await indexedDB.databases();
                for (const dbInfo of dbs) {
                    const dbName = dbInfo.name;
                    if (!dbName) continue;
                    try {
                        const db = await new Promise((resolve, reject) => {
                            const req = indexedDB.open(dbName, dbInfo.version);
                            req.onsuccess = () => resolve(req.result);
                            req.onerror = () => reject(req.error);
                        });
                        const stores = {};
                        for (const storeName of db.objectStoreNames) {
                            try {
                                const tx = db.transaction(storeName, 'readonly');
                                const store = tx.objectStore(storeName);
                                const allReq = store.getAll();
                                const items = await new Promise((resolve, reject) => {
                                    allReq.onsuccess = () => resolve(allReq.result);
                                    allReq.onerror = () => reject(allReq.error);
                                });
                                // Filtrar items serializáveis (ignorar blobs grandes)
                                stores[storeName] = items
                                    .map(item => {
                                        try {
                                            JSON.stringify(item);
                                            return item;
                                        } catch {
                                            return `[non-serializable: ${typeof item}]`;
                                        }
                                    })
                                    .slice(0, 100);  // limite de 100 items por store
                            } catch (e) {
                                stores[storeName] = `[error: ${e.message}]`;
                            }
                        }
                        db.close();
                        result[dbName] = stores;
                    } catch (e) {
                        result[dbName] = `[error opening: ${e.message}]`;
                    }
                }
            } catch (e) {
                result['_error'] = e.message;
            }
            return result;
        }
    """)
    print(f"  [session] IndexedDB: {len(indexeddb)} databases", flush=True)

    sessao = {
        "saved_at": datetime.now().isoformat(),
        "url": page.url,
        "user_agent": await page.evaluate("navigator.userAgent"),
        "cookies": cookies,
        "local_storage": local_storage,
        "session_storage": session_storage,
        "indexeddb": indexeddb,
    }

    return sessao


# === Restauração ===

async def restaurar_sessao(ctx, page, sessao: dict) -> bool:
    """Restaura sessão em um Playwright BrowserContext + Page.

    Deve ser chamado DEPOIS de page.goto() para a URL do Qwen.
    Após restaurar, é necessário page.reload() para que os cookies/storage
    tenham efeito na página.

    Args:
        ctx: Playwright BrowserContext (vazio, recém-criado)
        page: Playwright Page (já navegou para https://chat.qwen.ai/)
        sessao: dict retornado por extrair_sessao()

    Returns:
        True se restauração OK, False se houve erro.
    """
    try:
        # 1. Cookies — via ctx.add_cookies()
        cookies = sessao.get("cookies", [])
        if cookies:
            await ctx.add_cookies(cookies)
            print(f"  [session] Cookies restaurados: {len(cookies)}", flush=True)

        # 2. LocalStorage + SessionStorage via JS eval
        local_storage = sessao.get("local_storage", {})
        session_storage = sessao.get("session_storage", {})

        if local_storage or session_storage:
            await page.evaluate("""
                (data) => {
                    try {
                        for (const [key, value] of Object.entries(data.local_storage || {})) {
                            localStorage.setItem(key, value);
                        }
                        for (const [key, value] of Object.entries(data.session_storage || {})) {
                            sessionStorage.setItem(key, value);
                        }
                    } catch (e) {
                        console.error('Storage restore erro:', e);
                    }
                }
            """, {"local_storage": local_storage, "session_storage": session_storage})
            print(f"  [session] Storage restaurado: "
                  f"local={len(local_storage)} session={len(session_storage)}",
                  flush=True)

        # 3. IndexedDB (opcional — se presente na sessão)
        indexeddb = sessao.get("indexeddb", {})
        if indexeddb and not indexeddb.get("_error"):
            for db_name, stores in indexeddb.items():
                if db_name == "_error" or not isinstance(stores, dict):
                    continue
                try:
                    await page.evaluate("""
                        async (data) => {
                            const dbName = data.db_name;
                            const stores = data.stores;
                            // Abrir DB sem versão específica — cria se não existe
                            const db = await new Promise((resolve, reject) => {
                                const req = indexedDB.open(dbName);
                                req.onsuccess = () => resolve(req.result);
                                req.onerror = () => reject(req.error);
                            });
                            for (const [storeName, items] of Object.entries(stores)) {
                                if (!Array.isArray(items)) continue;
                                try {
                                    // Verificar se store existe; se não, pular
                                    if (!db.objectStoreNames.contains(storeName)) continue;
                                    const tx = db.transaction(storeName, 'readwrite');
                                    const store = tx.objectStore(storeName);
                                    for (const item of items) {
                                        try {
                                            store.put(item);
                                        } catch {}
                                    }
                                } catch (e) {}
                            }
                            db.close();
                        }
                    """, {"db_name": db_name, "stores": stores})
                except Exception as e:
                    print(f"  [session] IndexedDB {db_name} erro: {e}", flush=True)
            print(f"  [session] IndexedDB restaurado: {len(indexeddb)} databases",
                  flush=True)

        return True
    except Exception as e:
        print(f"  [session] Erro ao restaurar sessão: {e}", flush=True)
        return False


# === Verificação ===

async def sessao_eh_valida(page, timeout_sec: float = 5.0) -> bool:
    """Verifica se sessão restaurada é válida (textarea visível = logado).

    Deve ser chamado DEPOIS de restaurar_sessao() + page.reload().
    """
    try:
        textarea = await page.wait_for_selector("textarea", timeout=int(timeout_sec * 1000))
        return textarea is not None and await textarea.is_visible()
    except Exception:
        return False


async def bem_vindo_modal_visivel(page) -> bool:
    """Detecta Welcome modal (sessão expirou)."""
    try:
        overlay = await page.query_selector(".qwen-modal-overlay")
        if not overlay:
            return False
        return await overlay.is_visible()
    except Exception:
        return False
