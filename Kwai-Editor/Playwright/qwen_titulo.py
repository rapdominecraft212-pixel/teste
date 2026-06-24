import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Playwright.qwen_reply import QwenReply

PROMPT_TITULO = (
    "Voce e um agente de Inteligencia Artificial integrado a um sistema automatizado "
    "de postagem para TikTok e Kwai. Sua funcao e atuar como especialista em retencao "
    "de atencao, copywriting e marketing de videos curtos.\n"
    "INPUT:\n"
    "Voce recebera em anexo um arquivo de video.\n"
    "SUA TAREFA:\n"
    "Analise o conteudo do video e crie o texto ideal para o "
    "\"Titulo\" (o pop-up de gancho/hook que prende a atencao nos primeiros segundos).\n"
    "DIRETRIZES CONCEITUAIS DO TITULO:\n"
    " * Funcao de Narrativa e Conflito: O titulo nao e a capa. Ele nao serve apenas "
    "para identificar \"quem\" esta no video. O titulo deve resumir o assunto exato, "
    "a mensagem central, a \"fofoca\" ou o conflito da historia que esta sendo contada.\n"
    " * Praticas de Redes Sociais: O titulo deve atuar como um gancho irresistivel. "
    "Ele precisa abrir um loop de curiosidade na mente do espectador. Use frases "
    "provocativas, que gerem urgencia, duvida ou forte interesse emocional. Mantenha "
    "o texto curto e impactante (facil de ler rapidamente na tela).\n"
    " * Nao use emojis, emoticons ou caracteres especiais.\n"
    "REGRA CRITICA DE OUTPUT (FORMATO ESTRITO):\n"
    "Esta e uma requisicao direta de sistema. Qualquer palavra gerada fora do "
    "formato exigido quebrara o codigo da automacao que ira ler a sua resposta.\n"
    "Sua resposta deve conter UNICAMENTE o texto puro do titulo — sem formatacao, "
    "sem codigo, sem explicacoes, sem saudacoes, sem numeracao, sem caracteres "
    "adicionais. Nenhum espaco, quebra de linha ou caractere alem do proprio texto.\n"
    "Voce esta terminantemente proibida de:\n"
    "- Fornecer saudacoes, explicacoes do raciocinio, confirmacoes ou texto conversacional.\n"
    "- Usar formatacao markdown (negrito, italico, code blocks ```, etc).\n"
    "- Incluir numeracao, listas ou bullets.\n"
    "- Incluir quebras de linha ou espacos extras.\n"
    "- Escrever QUALQUER coisa alem do texto puro do titulo.\n\n"
    "Sua resposta final deve conter APENAS o texto do titulo, nada mais."
)


def analisar(video_path):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Arquivo nao encontrado: {video_path}")

    with QwenReply(headless=True) as qr:
        return analisar_com_instancia(qr, video_path)


def analisar_com_instancia(qr, video_path):
    qr.ask(PROMPT_TITULO, arquivo=video_path, timeout=300)
    return qr.ultima_resposta()


if __name__ == "__main__":
    upload_dir = Path(__file__).resolve().parent.parent / "data" / "upload"
    videos = sorted(upload_dir.glob("**/*.mp4"))
    if not videos:
        print("Nenhum video encontrado em data/upload/")
        sys.exit(1)
    resultado = analisar(str(videos[0]))
    print(f"Titulo: {resultado}")
