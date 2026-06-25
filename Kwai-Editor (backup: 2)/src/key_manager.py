import json
import time
import threading
from pathlib import Path

KEYS_FILE = Path(__file__).parent.parent / "api_keys.json"
INDEX_FILE = Path(__file__).parent.parent / "api_key_index.txt"

# Controle de rate limiting por chave
_key_cooldowns: dict[int, float] = {}  # chave_idx → timestamp quando pode ser usada novamente
_banned_keys: set[int] = set()        # chaves permanentemente banidas (invalidas)
_lock = threading.Lock()              # protege acesso concorrente entre threads


def _load_keys():
    with open(KEYS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data["keys"]


def _read_index():
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_index(idx: int):
    INDEX_FILE.write_text(str(idx), encoding="utf-8")


def _next_available_idx(start_idx: int, keys: list[str]) -> int:
    """Retorna o proximo indice NAO banido a partir de start_idx.
    Se todas estiverem banidas, limpa os bans e retorna start_idx."""
    n = len(keys)
    for offset in range(n):
        idx = (start_idx + offset) % n
        if idx not in _banned_keys:
            return idx
    # Todas banidas — resetar bans como fallback
    _banned_keys.clear()
    return start_idx


def get_api_key() -> str:
    """Retorna a chave atual (sem rotacionar), pulando chaves banidas."""
    with _lock:
        keys = _load_keys()
        idx = _read_index()
        idx = _next_available_idx(idx, keys)
        return keys[idx]


def rotate_api_key() -> str:
    """Rotaciona para a proxima chave e retorna ela.
    
    Chamada quando uma chave da erro (429/401/403).
    Tambem marca a chave atual como 'em cooldown' por 60s.
    Pula chaves permanentemente banidas.
    """
    with _lock:
        keys = _load_keys()
        idx = _read_index()
        
        # Marcar chave atual como em cooldown
        _key_cooldowns[idx] = time.time() + 60  # nao usar essa chave por 60s
        
        next_idx = _next_available_idx((idx + 1) % len(keys), keys)
        _write_index(next_idx)
        return keys[next_idx]


def next_key() -> str:
    """Rotaciona PROATIVAMENTE para a proxima chave disponivel.
    
    Diferente de rotate_api_key() (que e chamada apos falha),
    esta funcao e chamada apos SUCESSO para distribuir a carga
    entre todas as chaves. Pula chaves que estao em cooldown
    ou permanentemente banidas.
    """
    with _lock:
        keys = _load_keys()
        idx = _read_index()
        now = time.time()
        
        # Tentar encontrar uma chave que nao esta em cooldown nem banida
        for _ in range(len(keys)):
            next_idx = (idx + 1) % len(keys)
            if next_idx in _banned_keys:
                idx = next_idx
                continue
            cooldown_until = _key_cooldowns.get(next_idx, 0)
            if now >= cooldown_until:
                # Esta chave esta disponivel
                _write_index(next_idx)
                return keys[next_idx]
            # Chave em cooldown, tentar a proxima
            idx = next_idx
        
        # Todas em cooldown — usar a proxima mesmo assim (melhor que travar)
        next_idx = _next_available_idx((_read_index() + 1) % len(keys), keys)
        _write_index(next_idx)
        return keys[next_idx]


def ban_api_key(permanent: bool = False, duration: int = 86400):
    """Bane a chave atual (impede que seja usada novamente).
    
    Args:
        permanent: Se True, chave nunca mais sera usada (ate reset_index()).
        duration: Se nao permanente, tempo em segundos de banimento (padrao 24h).
    """
    with _lock:
        keys = _load_keys()
        idx = _read_index()
        
        if permanent:
            _banned_keys.add(idx)
            print(f"    [key_manager] chave #{idx} banida PERMANENTEMENTE")
        else:
            _key_cooldowns[idx] = time.time() + duration
            print(f"    [key_manager] chave #{idx} banida por {duration}s ({duration//3600}h)")


def get_key_status() -> list[dict]:
    """Retorna status de todas as chaves (para debug)."""
    with _lock:
        keys = _load_keys()
        now = time.time()
        result = []
        for i, key in enumerate(keys):
            cd = _key_cooldowns.get(i, 0)
            remaining = max(0, cd - now)
            result.append({
                "index": i,
                "key": key[:10] + "..." + key[-4:],
                "cooldown_remaining": round(remaining, 1),
                "available": remaining == 0 and i not in _banned_keys,
                "banned": i in _banned_keys,
            })
        return result


def reset_index():
    with _lock:
        _write_index(0)
        _key_cooldowns.clear()
        _banned_keys.clear()
