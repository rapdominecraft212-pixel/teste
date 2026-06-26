import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from log_utils import log

DB_PATH = Path("jobs.sqlite3")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                input_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                output_path TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_states (
                chat_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'idle',
                wait_until_all_ready INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )

        # Migracao: adicionar coluna retry_count
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN retry_count INTEGER DEFAULT 0")
            log.info("[DB] Coluna 'retry_count' adicionada à tabela jobs")
        except Exception:
            pass

        # Migracao: remover colunas legadas (delivered, sent_at)
        for col in ("delivered", "sent_at"):
            try:
                conn.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
                log.info(f"[DB] Coluna '{col}' removida da tabela jobs")
            except Exception:
                pass

        # Migracao Fase 2 (pipeline paralelo): adicionar preparation_data
        # Armazena JSON com o codigo de barras do chassis (prep_data retornado
        # por preparar_video). Usado para transferir dados entre estagio A
        # (prepare) e estagio B (render) sem perder informacao do job.
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN preparation_data TEXT")
            log.info("[DB] Coluna 'preparation_data' adicionada à tabela jobs")
        except Exception:
            pass

        conn.commit()


def get_user_state(chat_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT state FROM user_states WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()
        return row["state"] if row else "idle"


def set_user_state(chat_id, state):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_states (chat_id, state, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state = excluded.state,
                updated_at = excluded.updated_at
            """,
            (chat_id, state, now)
        )
        conn.commit()


def set_wait_until_all_ready(chat_id, value):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_states (chat_id, state, wait_until_all_ready, updated_at)
            VALUES (?, 'idle', ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                wait_until_all_ready = excluded.wait_until_all_ready,
                updated_at = excluded.updated_at
            """,
            (chat_id, 1 if value else 0, now)
        )
        conn.commit()


def get_wait_until_all_ready(chat_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT wait_until_all_ready FROM user_states WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()
        return bool(row["wait_until_all_ready"]) if row else False


def create_job(job_id, chat_id, input_url):
    """Cria job ja como 'queued' (worker pode pega-lo imediatamente).
    Use para casos onde o gate 'Concluido' nao se aplica (e.g. testes, API direta)."""
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, chat_id, input_url, status, created_at, updated_at)
            VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (job_id, chat_id, input_url, now, now)
        )
        conn.commit()


def create_pending_job(job_id, chat_id, input_url):
    """Cria job como 'pending' — worker NAO ve jobs pending.
    Use quando o usuario esta colando links no modo de coleta; o job so
    vira 'queued' (e portanto visivel ao worker) apos o usuario clicar
    'Concluido', via flush_pending_jobs(chat_id)."""
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, chat_id, input_url, status, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (job_id, chat_id, input_url, now, now)
        )
        conn.commit()
    log.info(f"[DB] job {job_id[:12]} -> pending (chat={chat_id})")


def flush_pending_jobs(chat_id):
    """Transita todos os jobs 'pending' do chat para 'queued'.
    Deve ser chamado quando o usuario clica em 'Concluido' no modo de coleta.
    Retorna o numero de jobs afetados."""
    now = utc_now()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'queued', updated_at = ? "
            "WHERE chat_id = ? AND status = 'pending'",
            (now, chat_id)
        )
        affected = cur.rowcount
        conn.commit()
    if affected > 0:
        log.info(f"[DB] chat {chat_id}: {affected} job(s) pending -> queued (Concluido)")
    return affected


def count_pending(chat_id):
    """Conta jobs 'pending' do chat (ainda nao liberados para o worker)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM jobs WHERE chat_id = ? AND status = 'pending'",
            (chat_id,)
        ).fetchone()
        return row["c"] if row else 0


def discard_pending_jobs(chat_id):
    """Remove (DELETE) todos os jobs 'pending' do chat.
    Usado quando o usuario clica 'Cancelar' no modo de coleta — descarta
    os links que ele ainda nao confirmou com 'Concluido'.
    Retorna o numero de jobs removidos."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM jobs WHERE chat_id = ? AND status = 'pending'",
            (chat_id,)
        )
        affected = cur.rowcount
        conn.commit()
    if affected > 0:
        log.info(f"[DB] chat {chat_id}: {affected} job(s) pending descartados (Cancelar)")
    return affected


def retry_failed_jobs(chat_id):
    """Requeue todos os jobs 'failed' do chat para 'queued' (retentativa).
    Reseta o error_message e retry_count.
    Retorna o numero de jobs reenfileirados."""
    now = utc_now()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'queued', error_message = NULL, retry_count = 0, updated_at = ? "
            "WHERE chat_id = ? AND status = 'failed'",
            (now, chat_id)
        )
        affected = cur.rowcount
        conn.commit()
    if affected > 0:
        log.info(f"[DB] chat {chat_id}: {affected} job(s) failed -> queued (/reprocess)")
    return affected


def count_by_status(chat_id):
    """Retorna dict com contagens por status para um chat."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as c FROM jobs WHERE chat_id = ? GROUP BY status",
            (chat_id,)
        ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    return counts


def get_job(job_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,)
        ).fetchone()
        return dict(row) if row else None


def get_user_jobs(chat_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE chat_id = ? ORDER BY created_at DESC",
            (chat_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def count_ready(chat_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM jobs WHERE chat_id = ? AND status = 'ready'",
            (chat_id,)
        ).fetchone()
        return row["c"] if row else 0


def count_processing(chat_id):
    """Conta jobs visiveis ao usuario como 'em andamento'.
    Inclui todos os estados ativos: pending, queued, processing (legado),
    preparing (Qwen rodando), ready_to_render (aguardando render),
    rendering (render em andamento)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM jobs WHERE chat_id = ? AND status IN "
            "('pending', 'queued', 'processing', 'preparing', 'ready_to_render', 'rendering')",
            (chat_id,)
        ).fetchone()
        return row["c"] if row else 0


def count_failed(chat_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM jobs WHERE chat_id = ? AND status = 'failed'",
            (chat_id,)
        ).fetchone()
        return row["c"] if row else 0


def get_next_queued_job():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY chat_id, created_at ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def set_job_processing(job_id):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'processing', updated_at = ? WHERE job_id = ?",
            (now, job_id)
        )
        conn.commit()
    log.info(f"[DB] job {job_id[:12]} -> processing")


# === Fase 2 (pipeline paralelo): novos estados e funções ===
# Estados novos no ciclo de vida do job:
#   queued -> preparing -> ready_to_render -> rendering -> ready
#                                                              -> failed (em qualquer estágio)
#
# Estados legados mantidos para compatibilidade:
#   queued, processing, ready, failed (worker antigo usa estes)
#
# Invariante 1 (vinculação job_id <-> prep_data):
#   - set_job_ready_to_render(job_id, prep_data) salva prep_data como JSON
#   - get_preparation_data(job_id) retorna o dict ou None
#   - Se preparation_data faltar campos, job falha (não fica preso)
#
# Invariante 2 (recovery nunca deixa peça presa):
#   - preparing -> volta para queued (prep_data descartado, refaz Qwen)
#   - ready_to_render -> permanece (prep_data preservado, pula Qwen)
#   - rendering -> volta para ready_to_render (prep_data preservado, refaz render)

def set_job_preparing(job_id):
    """Marca job como 'preparing' (estágio A em andamento — Qwen rodando)."""
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'preparing', updated_at = ? WHERE job_id = ?",
            (now, job_id)
        )
        conn.commit()
    log.info(f"[DB] job {job_id[:12]} -> preparing")


def set_job_ready_to_render(job_id, prep_data: dict):
    """Marca job como 'ready_to_render' e salva prep_data (código de barras).
    Estágio A terminou, estágio B pode pegar esse job."""
    import json
    now = utc_now()
    prep_json = json.dumps(prep_data)
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'ready_to_render', preparation_data = ?, updated_at = ? WHERE job_id = ?",
            (prep_json, now, job_id)
        )
        conn.commit()
    log.info(f"[DB] job {job_id[:12]} -> ready_to_render (prep_data {len(prep_json)} bytes)")


def set_job_rendering(job_id):
    """Marca job como 'rendering' (estágio B em andamento — corte + render)."""
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'rendering', updated_at = ? WHERE job_id = ?",
            (now, job_id)
        )
        conn.commit()
    log.info(f"[DB] job {job_id[:12]} -> rendering")


def get_preparation_data(job_id) -> dict | None:
    """Recupera prep_data (código de barras) de um job.
    Retorna dict ou None se não houver."""
    import json
    with get_conn() as conn:
        row = conn.execute(
            "SELECT preparation_data FROM jobs WHERE job_id = ?",
            (job_id,)
        ).fetchone()
        if not row or not row["preparation_data"]:
            return None
        try:
            return json.loads(row["preparation_data"])
        except json.JSONDecodeError as e:
            log.error(f"[DB] job {job_id[:12]} preparation_data corrompido: {e}")
            return None


def get_next_ready_to_render_job():
    """Pega próximo job em 'ready_to_render' (FIFO por created_at).
    Retorna dict com todos os campos (incluindo preparation_data) ou None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'ready_to_render' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def count_ready_to_render():
    """Conta jobs em 'ready_to_render' (para respeitar MAX_READY_TO_RENDER)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM jobs WHERE status = 'ready_to_render'"
        ).fetchone()
        return row["c"] if row else 0


def validate_preparation_data(prep_data: dict) -> tuple[bool, str]:
    """Valida que prep_data tem todos os campos obrigatórios do código de barras.
    Invariante 1: peça nunca se perde — se faltar campo, job falha (não fica preso).
    Retorna (True, '') se válido, (False, motivo) se inválido."""
    if not prep_data:
        return False, "prep_data é None"
    required = ["job_id", "video_path", "chat_id", "texto_capa",
                "texto_titulo", "y1", "y2", "video_size", "video_name"]
    for field in required:
        if field not in prep_data:
            return False, f"campo faltando: {field}"
        if prep_data[field] is None:
            return False, f"campo None: {field}"
    # Validar que video_path existe em disco (Invariante 1)
    from pathlib import Path
    if not Path(prep_data["video_path"]).exists():
        return False, f"video_path não existe: {prep_data['video_path']}"
    return True, ''


def recover_pipeline_jobs():
    """Recovery para os novos estados do pipeline (Fase 2).
    Deve ser chamado no startup do worker, junto com recover_processing_jobs().

    Invariante 2: nenhum job fica preso após restart.
    - preparing -> volta para queued (prep_data descartado, refaz Qwen)
    - ready_to_render -> permanece (prep_data preservado, pula Qwen)
    - rendering -> volta para ready_to_render (prep_data preservado, refaz render)
    """
    now = utc_now()
    with get_conn() as conn:
        # preparing -> queued (preparação foi interrompida, refaz do zero)
        cur1 = conn.execute(
            "UPDATE jobs SET status = 'queued', preparation_data = NULL, "
            "updated_at = ? WHERE status = 'preparing'",
            (now,)
        )
        preparing_count = cur1.rowcount

        # rendering -> ready_to_render (render foi interrompido, mas prep_data está OK)
        cur2 = conn.execute(
            "UPDATE jobs SET status = 'ready_to_render', updated_at = ? WHERE status = 'rendering'",
            (now,)
        )
        rendering_count = cur2.rowcount

        # ready_to_render -> permanece (não precisa fazer nada, mas logamos)
        cur3 = conn.execute(
            "SELECT COUNT(*) as c FROM jobs WHERE status = 'ready_to_render'"
        )
        ready_to_render_count = cur3.fetchone()["c"]

        conn.commit()

    if preparing_count > 0:
        log.info(f"[DB] recovery: {preparing_count} job(s) preparing -> queued (refaz Qwen)")
    if rendering_count > 0:
        log.info(f"[DB] recovery: {rendering_count} job(s) rendering -> ready_to_render (refaz render)")
    if ready_to_render_count > 0:
        log.info(f"[DB] recovery: {ready_to_render_count} job(s) permanecem em ready_to_render")


def recover_processing_jobs():
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET retry_count = retry_count + 1, updated_at = ? WHERE status = 'processing'",
            (now,)
        )
        conn.execute(
            "UPDATE jobs SET status = 'failed', output_path = NULL, "
            "error_message = 'max_retries_exceeded apos ' || retry_count || ' tentativas', updated_at = ? "
            "WHERE status = 'processing' AND retry_count >= 3",
            (now,)
        )
        conn.execute(
            "UPDATE jobs SET status = 'queued', output_path = NULL, "
            "error_message = 'recuperado_apos_reinicio', updated_at = ? "
            "WHERE status = 'processing' AND retry_count < 3",
            (now,)
        )
        conn.commit()


def set_job_ready(job_id, output_path):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'ready', output_path = ?, retry_count = 0, updated_at = ? WHERE job_id = ?",
            (output_path, now, job_id)
        )
        conn.commit()
    size = Path(output_path).stat().st_size if output_path and Path(output_path).exists() else 0
    log.info(f"[DB] job {job_id[:12]} -> ready ({size/1024/1024:.1f}MB)")


def set_job_failed(job_id, error_message):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', error_message = ?, retry_count = 0, updated_at = ? WHERE job_id = ?",
            (error_message, now, job_id)
        )
        conn.commit()
    log.error(f"[DB] job {job_id[:12]} -> failed: {error_message}")


if __name__ == "__main__":
    init_db()
    print(f"Banco inicializado em: {DB_PATH.resolve()}")
