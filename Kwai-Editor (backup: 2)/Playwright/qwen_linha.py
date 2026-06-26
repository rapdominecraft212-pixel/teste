import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Playwright.qwen_reply import QwenReply

PROMPT_LINHA = (
    "REGRA CRITICA DE OUTPUT (FORMATO ESTRITO):\n"
    "Esta e uma requisicao direta de sistema. Qualquer palavra gerada fora do "
    "padrao exigido quebrara o codigo da automacao que ira ler a sua resposta.\n"
    "Voce esta terminantemente proibida de:\n"
    "- Fornecer saudacoes, explicacoes, raciocinio, confirmacoes ou texto conversacional.\n"
    "- Escrever QUALQUER coisa alem do formato especificado abaixo.\n\n"
    "TAREFA:\n"
    "Analise a imagem anexada e determine a faixa exata pertencente ao filme, "
    "excluindo toda a area de edicao.\n"
    "Seja preciso: inclua o maximo de conteudo possivel, "
    "excluindo apenas as fileiras que contem artefatos visuais de edicao.\n\n"
    "Criterio:\n"
    "- Qualquer linha/frame que faca parte da fronteira entre edicao e filme "
    "deve ser classificada como edicao.\n"
    "- A linha/frame inicial do filme e o primeiro ponto completamente fora da edicao.\n"
    "- A linha/frame final do filme e o ultimo ponto completamente fora da edicao.\n\n"
    "Entregue EXATAMENTE este formato e nada mais:\n\n"
    "```\n"
    "Linha_inicial = [linha]\n"
    "Linha_final = [linha]\n"
    "```\n\n"
    "Exemplo:\n"
    "```\n"
    "Linha_inicial = 3\n"
    "Linha_final = 78\n"
    "```"
)


def _extrair_linhas(texto):
    # Tenta extrair de dentro de code block ```...```
    cb = re.search(r"```\s*\n?(.*?)\n?```", texto, re.DOTALL)
    if cb:
        texto = cb.group(1)
    match = re.search(r"Linha_inicial\s*=\s*(\d+)[\s\S]*?Linha_final\s*=\s*(\d+)", texto.strip(), re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))
    lines = [l.strip() for l in texto.split('\n') if l.strip()]
    for line in reversed(lines):
        parts = line.replace('=', ' ').split()
        nums = [int(p) for p in parts if p.isdigit()]
        if len(nums) >= 2:
            return nums[0], nums[-1]
    raise ValueError(f"Nao foi possivel interpretar a resposta do Qwen: {texto[:500]}")


def analisar(grid_path, cell_h):
    if not os.path.exists(grid_path):
        raise FileNotFoundError(f"Grid nao encontrado: {grid_path}")

    with QwenReply(headless=True) as qr:
        return analisar_com_instancia(qr, grid_path, cell_h)


def analisar_com_instancia(qr, grid_path, cell_h):
    qr.ask(PROMPT_LINHA, arquivo=grid_path, timeout=180)
    texto = qr.ultima_resposta()

    row_start, row_end = _extrair_linhas(texto)
    y_start = int((row_start - 1) * cell_h)
    y_end = int(row_end * cell_h)
    return y_start, y_end
