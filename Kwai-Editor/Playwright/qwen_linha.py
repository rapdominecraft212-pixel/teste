import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


def _extrair_linhas(texto, total_linhas=None):
    """Extrai Linha_inicial e Linha_final da resposta do Qwen.

    Args:
        texto: resposta bruta do Qwen
        total_linhas: numero total de linhas do grid (fallback para Linha_final)

    Se o Qwen gerar apenas Linha_inicial, usa total_linhas como Linha_final.
    Se nao encontrar nenhum valor, levanta ValueError.
    """
    # Tenta extrair de dentro de code block ```...```
    cb = re.search(r"```\s*\n?(.*?)\n?```", texto, re.DOTALL)
    if cb:
        texto = cb.group(1)

    # Match completo: ambas as linhas
    match = re.search(r"Linha_inicial\s*=\s*(\d+)[\s\S]*?Linha_final\s*=\s*(\d+)", texto.strip(), re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))

    # Match parcial: apenas Linha_inicial (resposta truncada pelo Qwen)
    match_inicio = re.search(r"Linha_inicial\s*=\s*(\d+)", texto.strip(), re.IGNORECASE)
    if match_inicio:
        inicio = int(match_inicio.group(1))
        if total_linhas and total_linhas > inicio:
            return inicio, total_linhas
        # Sem total_linhas, assumir que o video vai ate o fim (inicio + margem)
        return inicio, inicio + 50

    # Fallback: procurar dois numeros em qualquer formato
    lines = [l.strip() for l in texto.split('\n') if l.strip()]
    for line in reversed(lines):
        parts = line.replace('=', ' ').split()
        nums = [int(p) for p in parts if p.isdigit()]
        if len(nums) >= 2:
            return nums[0], nums[-1]

    # Ultimo recurso: qualquer numero na resposta
    all_nums = re.findall(r'\b(\d+)\b', texto)
    if len(all_nums) >= 1:
        inicio = int(all_nums[0])
        if total_linhas and total_linhas > inicio:
            return inicio, total_linhas
        return inicio, inicio + 50

    raise ValueError(f"Nao foi possivel interpretar a resposta do Qwen: {texto[:500]}")


# NOTA: As funções `analisar()` e `analisar_com_instancia()` que usavam QwenReply (sync)
# foram REMOVIDAS — QwenReply era legado e foi deletado. Para análise standalone,
# use pipeline/simple.py:main() que usa AccountPool + QwenReplyAsync.
