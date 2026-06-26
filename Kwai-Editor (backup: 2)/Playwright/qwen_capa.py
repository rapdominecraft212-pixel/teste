import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Playwright.qwen_reply import QwenReply

PROMPT_CAPA = (
    "Voce e um agente de Inteligencia Artificial integrado a um sistema automatizado "
    "de postagem para TikTok e Kwai. Sua funcao e atuar como especialista em retencao "
    "de atencao e marketing de videos curtos.\n"
    "INPUT:\n"
    "Voce recebera em anexo um arquivo de video.\n"
    "SUA TAREFA:\n"
    "Analise o conteudo do video e crie o texto ideal para a "
    "\"Capa\" (o pop-up inicial ou thumbnail).\n"
    "DIRETRIZES CONCEITUAIS DA CAPA:\n"
    " * Funcao de Vitrine: A capa nao e o titulo do video. Nao escreva um resumo "
    "da historia. Ela serve exclusivamente para fisgar o espectador e promover a "
    "identificacao visual instantanea de \"quem\" ou \"o que\" esta no video.\n"
    " * Praticas de Redes Sociais: Para funcionar no TikTok e Kwai, o texto precisa "
    "ser direto e criar alta curiosidade. Deve ser extremamente curto "
    "(idealmente de 2 a 5 palavras). Use palavras fortes ou gatilhos que facam "
    "o usuario parar de rolar o feed.\n"
    " * Nao use emojis, emoticons ou caracteres especiais.\n"
    "REGRA CRITICA DE OUTPUT (FORMATO ESTRITO):\n"
    "Esta e uma requisicao direta de sistema. Qualquer palavra gerada fora do "
    "formato exigido quebrara o codigo da automacao que ira ler a sua resposta.\n"
    "Sua resposta deve conter UNICAMENTE o texto puro da capa — sem formatacao, "
    "sem codigo, sem explicacoes, sem saudacoes, sem numeracao, sem caracteres "
    "adicionais. Nenhum espaco, quebra de linha ou caractere alem do proprio texto.\n"
    "Voce esta terminantemente proibida de:\n"
    "- Fornecer saudacoes, explicacoes do raciocinio, confirmacoes ou texto conversacional.\n"
    "- Usar formatacao markdown (negrito, italico, code blocks ```, etc).\n"
    "- Incluir numeracao, listas ou bullets.\n"
    "- Incluir quebras de linha ou espacos extras.\n"
    "- Escrever QUALQUER coisa alem do texto puro da capa.\n\n"
    "Sua resposta final deve conter APENAS o texto da capa, nada mais."
)


def analisar(video_path):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Arquivo nao encontrado: {video_path}")

    with QwenReply(headless=True) as qr:
        return analisar_com_instancia(qr, video_path)


def analisar_com_instancia(qr, video_path):
    qr.ask(PROMPT_CAPA, arquivo=video_path, timeout=300)
    return qr.ultima_resposta()


if __name__ == "__main__":
    upload_dir = Path(__file__).resolve().parent.parent / "data" / "upload"
    videos = sorted(upload_dir.glob("**/*.mp4"))
    if not videos:
        print("Nenhum video encontrado em data/upload/")
        sys.exit(1)
    resultado = analisar(str(videos[0]))
    print(f"Capa: {resultado}")
