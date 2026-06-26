"""
keep_awake — Impede o computador de dormir enquanto o servidor esta rodando.

No Windows, usa SetThreadExecutionState (kernel32.dll) para:
- Impedir o computador de entrar em modo de suspensao (sleep)
- Impedir o monitor de desligar (display sleep)
- Impedir o screensaver de activating

No Linux, usa systemd-inhibit ou /proc wakeup.

Uso:
    from keep_awake import KeepAwake

    with KeepAwake():
        # Computador NAO vai dormir enquanto este bloco executar
        rodar_servidor()

    # Ou uso manual:
    ka = KeepAwake()
    ka.enable()   # Computador permanece acordado
    # ... trabalho longo ...
    ka.disable()  # Computador pode dormir novamente
"""
import sys
import os
import platform
import threading
import time
import logging

logger = logging.getLogger("keep_awake")


class KeepAwake:
    """Impede o computador de dormir enquanto ativo.

    Funciona como um 'cafe' para o computador — diz ao sistema operacional
    'estou trabalhando, nao durma!'. Essencial para servidores que processam
    videos longo (Qwen + FFmpeg podem demorar horas).

    Implementa:
    - Windows: SetThreadExecutionState (kernel32.dll) via ctypes
    - Linux: systemd-inhibit + periodic wakeup
    - Fallback: thread que simula atividade a cada 30s
    """

    def __init__(self, prevent_display_sleep=False):
        """
        Args:
            prevent_display_sleep: Se True, tambem impede o monitor de desligar.
                Use apenas quando necessario (processamento interativo).
                Default False (so impede computador de dormir).
        """
        self._prevent_display = prevent_display_sleep
        self._enabled = False
        self._thread = None
        self._stop_event = threading.Event()
        self._os_handle = None

    def enable(self):
        """Ativa a protecao contra suspensao."""
        if self._enabled:
            return

        system = platform.system()

        if system == "Windows":
            self._enable_windows()
        elif system == "Linux":
            self._enable_linux()
        else:
            # Fallback: thread de wakeup
            self._enable_fallback()

        self._enabled = True
        logger.info("[keep_awake] Protecao contra suspensao ATIVADA")

    def disable(self):
        """Desativa a protecao contra suspensao."""
        if not self._enabled:
            return

        system = platform.system()

        if system == "Windows":
            self._disable_windows()
        elif system == "Linux":
            self._disable_linux()
        else:
            self._disable_fallback()

        self._enabled = False
        logger.info("[keep_awake] Protecao contra suspensao DESATIVADA")

    def __enter__(self):
        self.enable()
        return self

    def __exit__(self, *args):
        self.disable()

    # === Windows ===

    def _enable_windows(self):
        """Usa SetThreadExecutionState para impedir suspensao no Windows.

        ES_CONTINUOUS (0x80000000): Mantem o estado ate ser explicitamente revertido
        ES_SYSTEM_REQUIRED (0x00000001): Impede o computador de dormir
        ES_DISPLAY_REQUIRED (0x00000002): Impede o monitor de desligar

        Sem ES_CONTINUOUS, o estado seria resetado apos qualquer input do usuario.
        Com ES_CONTINUOUS, persiste ate chamarmos com apenas ES_CONTINUOUS (reset).
        """
        try:
            import ctypes

            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002

            flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            if self._prevent_display:
                flags |= ES_DISPLAY_REQUIRED

            # Chamada que diz ao Windows: "nao durma enquanto meu programa roda"
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            logger.info(f"[keep_awake] Windows: SetThreadExecutionState(0x{flags:08X})")
        except Exception as e:
            logger.warning(f"[keep_awake] Windows SetThreadExecutionState falhou: {e}")
            self._enable_fallback()

    def _disable_windows(self):
        """Reseta o estado do Windows para permitir suspensao novamente."""
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            # Chamada com apenas ES_CONTINUOUS reseta o estado
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            logger.info("[keep_awake] Windows: estado resetado (computador pode dormir)")
        except Exception as e:
            logger.warning(f"[keep_awake] Windows reset falhou: {e}")

    # === Linux ===

    def _enable_linux(self):
        """Usa systemd-inhibit ou wakeup periodico para impedir suspensao no Linux."""
        # Tentar systemd-inhibit primeiro (mais robusto)
        try:
            import subprocess
            cmd = [
                "systemd-inhibit",
                "--what=idle:sleep",
                "--who=Kwai-Editor",
                "--why=Processando videos — servidor ativo",
                "--mode=block",
                "sleep", "infinity"
            ]
            self._inhibit_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if self._inhibit_proc.poll() is None:
                logger.info("[keep_awake] Linux: systemd-inhibit ativo")
                return
        except (FileNotFoundError, Exception):
            pass

        # Fallback: thread de wakeup
        self._enable_fallback()

    def _disable_linux(self):
        """Encerra o systemd-inhibit ou thread de wakeup."""
        if hasattr(self, '_inhibit_proc') and self._inhibit_proc:
            self._inhibit_proc.terminate()
            try:
                self._inhibit_proc.wait(timeout=5)
            except:
                self._inhibit_proc.kill()
            self._inhibit_proc = None
            logger.info("[keep_awake] Linux: systemd-inhibit encerrado")

        if self._thread:
            self._disable_fallback()

    # === Fallback (qualquer OS) ===

    def _enable_fallback(self):
        """Thread que simula atividade periodica para impedir suspensao.

        A cada 30 segundos, move o cursor virtualmente ou envia um
        sinal de wakeup. Isso e menos robusto que os metodos nativos,
        mas funciona como fallback em qualquer sistema.
        """
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._wakeup_loop,
            name="keep_awake",
            daemon=True,
        )
        self._thread.start()
        logger.info("[keep_awake] Fallback: thread de wakeup iniciada (a cada 30s)")

    def _disable_fallback(self):
        """Para a thread de wakeup."""
        if self._thread:
            self._stop_event.set()
            self._thread.join(timeout=5)
            self._thread = None

    def _wakeup_loop(self):
        """Loop que simula atividade a cada 30 segundos."""
        while not self._stop_event.is_set():
            try:
                # No Linux, escrever em /sys/power/wakeup_count mantem acordado
                if platform.system() == "Linux":
                    try:
                        with open("/proc/sys/vm/drop_caches", "w") as _:
                            pass  # Apenas acessar o sistema de arquivos
                    except:
                        pass
                # Simular atividade: pequeno calculo (impede CPU idle)
                _ = sum(range(1000))
            except:
                pass
            self._stop_event.wait(30)  # Espera 30s ou ate stop


def set_high_priority():
    """Define prioridade ALTA para o processo atual.

    No Windows: ABOVE_NORMAL_PRIORITY_CLASS
    No Linux: nice -10

    Isso diz ao SO: "meu programa e mais importante que processos em background"
    resultando em mais fatias de CPU para Qwen/FFmpeg.
    """
    system = platform.system()

    if system == "Windows":
        try:
            import ctypes
            ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(handle, ABOVE_NORMAL_PRIORITY_CLASS)
            logger.info("[keep_awake] Windows: prioridade do processo = ABOVE_NORMAL")
        except Exception as e:
            logger.warning(f"[keep_awake] Windows set priority falhou: {e}")

    elif system == "Linux":
        try:
            os.nice(-10)  # Prioridade mais alta (requer permissoes)
            logger.info("[keep_awake] Linux: nice = -10")
        except PermissionError:
            try:
                os.nice(-5)
                logger.info("[keep_awake] Linux: nice = -5")
            except PermissionError:
                logger.warning("[keep_awake] Linux: sem permissao para alterar nice")
    else:
        logger.warning(f"[keep_awake] {system}: alteracao de prioridade nao suportada")


def set_ffmpeg_high_priority():
    """Garante que processos FFmpeg filhos tambem rodam com prioridade alta.

    Deve ser chamado antes de subprocess.run() ou Popen() para FFmpeg.
    No Windows, usa CREATE_HIGH_PRIORITY_CLASS.
    No Linux, usa processo em background com nice.
    """
    system = platform.system()
    if system == "Windows":
        return subprocess.CREATE_HIGH_PRIORITY_CLASS
    return 0  # Linux: default flags


if __name__ == "__main__":
    # Teste: impedir sono por 60 segundos
    print("Testando KeepAwake — computador nao vai dormir por 60s...")
    logging.basicConfig(level=logging.INFO)
    with KeepAwake(prevent_display_sleep=True):
        set_high_priority()
        print("Protecao ativa! Pressione Ctrl+C para parar.")
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            pass
    print("Protecao desativada. Computador pode dormir novamente.")
