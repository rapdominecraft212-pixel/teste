import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Playwright.qwen_reply import QwenReply

PROMPT_CAPA_TITULO = (
    "Voce e um agente de Inteligencia Artificial integrado a um sistema automatizado "
    "de postagem para TikTok e Kwai. Sua funcao e atuar como especialista em retencao "
    "de atencao, copywriting e marketing de videos curtos.\n"

    "INPUT:\n"
    "Voce recebera em anexo um arquivo de video.\n"

    "SUA TAREFA:\n"
    "Analise o conteudo do video e crie DOIS textos: a Capa e o Titulo.\n\n"

    "=== FASE 1: OBSERVE (o que esta no video) ===\n"
    "Antes de escrever qualquer texto, observe o video com atencao:\n"
    " * Quem aparece? (pessoa comum, criador, grupo de pessoas)\n"
    " * Qual a expressao ou emocao predominante no inicio? (surpresa, raiva, alegria, duvida, choque)\n"
    " * O que esta acontecendo? (acao em andamento, conversa, demonstracao, revelacao, transformacao)\n"
    " * O que e inesperado ou foge do padrao? (objeto fora de lugar, situacao contraditoria, resultado surpreendente)\n"
    " * Qual o cenario? (ambiente intimido/casual vs ambiente publico/esperado)\n"
    " * Ha texto ou informacao visual na tela? (titulo, legenda, numero, resultado)\n"
    " * Qual o tom geral? (humor, drama, controversia, curiosidade, provocacao)\n\n"

    "=== FASE 2: INFER (o que o espectador sente) ===\n"
    "Com base na observacao, identifique:\n"
    " * Qual e o GATILHO EMOCIONAL mais forte? (curiosidade, FOMO, surpresa, controversia, desafio, identificacao)\n"
    " * Qual LACUNA DE CURIOSIDADE pode ser aberta? (oque o espectador NAO sabe mas PRECISA saber)\n"
    " * Que INTERRUPCAO DE PADRAO existe? (oque quebra a previsao do feed e faz o polegar parar)\n"
    " * Qual GRUPO DE IDENTIDADE se sentiria falado? (quem diria \"isso e pra mim\")\n"
    " * Qual PROMESSA ESPECIFICA o video entrega? (resultado concreto, informacao oculta, revelacao)\n\n"

    "=== FASE 3: CREATE (gerar capa e titulo) ===\n"

    "DIRETRIZES DA CAPA (pop-up inicial / thumbnail):\n"
    " * Funcao de Vitrine: A capa NAO e o titulo. Nao resuma a historia.\n"
    "   Serve exclusivamente para FISGAR o espectador e criar identificacao visual instantanea.\n"
    " * Deve ser o GANCHO VISUAL — a interrupcao de padrao que faz parar de rolar o feed.\n"
    " * Extremamente curta: 2 a 5 palavras. Use palavras fortes, gatilhos ou provocacao.\n"
    " * Priorize: misterio, provocacao, resultado surpreendente, ou identidade.\n"
    " * A capa e o PRIMEIRO contato — precisa causar impacto em menos de 1 segundo.\n\n"

    "DIRETRIZES DO TITULO (pop-up de gancho / hook):\n"
    " * Funcao de Narrativa e Conflito: O titulo NAO e a capa. Nao repita a capa.\n"
    "   Deve aprofundar a curiosidade, resumir o conflito, a fofoca ou a revelacao central.\n"
    " * Deve atuar como um GANCHO IRRESISTIVEL — abrir um loop de curiosidade que so fecha no final.\n"
    " * Use especificidade: numeros concretos, prazos, nomes, resultados mensuraveis.\n"
    " * Evite vagueza: \"dicas\", \"truques\", \"ideias\" sao proibidos. Substitua por resultados concretos.\n"
    " * O titulo e a PROMESSA — o video deve cumprir o que o titulo promete.\n\n"

    "REGRAS DE COERENCIA:\n"
    " * Capa e Titulo DEVEM ser coerentes entre si mas NAO repetitivos.\n"
    " * A capa chama atencao (interrupcao visual); o titulo aprofunda a curiosidade (lacuna de informacao).\n"
    " * Se a capa e provocativa, o titulo explica o porquê.\n"
    " * Se a capa e misteriosa, o titulo promete a revelacao.\n"
    " * Se a capa e um resultado, o titulo mostra o caminho.\n"
    " * Nao use emojis, emoticons ou caracteres especiais em nenhum dos dois.\n\n"

    "REGRA CRITICA DE OUTPUT (FORMATO ESTRITO):\n"
    "Esta e uma requisicao direta de sistema. Qualquer palavra gerada fora do "
    "formato exigido quebrara o codigo da automacao que ira ler a sua resposta.\n"
    "Voce esta terminantemente proibida de:\n"
    "- Fornecer saudacoes, explicacoes do raciocinio, confirmacoes ou texto conversacional.\n"
    "- Usar formatacao markdown (negrito, italico, code blocks ```, etc).\n"
    "- Incluir numeracao, listas ou bullets.\n"
    "- Escrever QUALQUER coisa alem do formato exigido abaixo.\n\n"

    "Sua resposta deve conter EXATAMENTE este formato e nada mais:\n\n"

    "Capa(\"texto da capa aqui\")\n"
    "Titulo(\"texto do titulo aqui\")\n\n"

    "Exemplos:\n"
    "Capa(\"O segredo da acerola\")\n"
    "Titulo(\"A fruta que esconde algo que todas as mulheres amam\")\n\n"

    "Capa(\"Voce esta fazendo errado\")\n"
    "Titulo(\"O erro que 90% dos criadores cometem sem perceber\")\n\n"

    "Capa(\"Isso mudou tudo\")\n"
    "Titulo(\"Testei 47 ganchos e so 3 realmente funcionaram\")\n"
)


def _extrair_capa_titulo(texto):
    """Extrai capa e titulo da resposta do Qwen no formato Capa("...") Titulo("...")."""
    # Tentar formato exato: Capa("...") Titulo("...")
    match = re.search(
        r'Capa\(["\'](.+?)["\']\)\s*Titulo\(["\'](.+?)["\']\)',
        texto.strip(), re.IGNORECASE | re.DOTALL
    )
    if match:
        return match.group(1).strip(), match.group(2).strip()

    # Fallback: tentar com aspas diferentes ou ordem invertida
    match2 = re.search(
        r'Capa\((.+?)\)\s*Titulo\((.+?)\)',
        texto.strip(), re.IGNORECASE | re.DOTALL
    )
    if match2:
        capa = match2.group(1).strip().strip('"').strip("'")
        titulo = match2.group(2).strip().strip('"').strip("'")
        return capa, titulo

    # Fallback 2: tentar formato alternativo com dois-pontos
    match3 = re.search(
        r'Capa\s*[:=]\s*["\']?(.+?)["\']?\s*\n\s*Titulo\s*[:=]\s*["\']?(.+?)["\']?\s*$',
        texto.strip(), re.IGNORECASE | re.MULTILINE
    )
    if match3:
        return match3.group(1).strip(), match3.group(2).strip()

    # Fallback 3: duas linhas, primeira = capa, segunda = titulo
    lines = [l.strip() for l in texto.strip().split('\n') if l.strip()]
    if len(lines) >= 2:
        capa = lines[0].strip('"').strip("'").strip()
        titulo = lines[1].strip('"').strip("'").strip()
        return capa, titulo

    raise ValueError(f"Nao foi possivel extrair capa e titulo da resposta do Qwen: {texto[:500]}")


def analisar(video_path):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Arquivo nao encontrado: {video_path}")

    with QwenReply(headless=True) as qr:
        return analisar_com_instancia(qr, video_path)


def analisar_com_instancia(qr, video_path):
    qr.ask(PROMPT_CAPA_TITULO, arquivo=video_path, timeout=300)
    texto = qr.ultima_resposta()
    return _extrair_capa_titulo(texto)


if __name__ == "__main__":
    upload_dir = Path(__file__).resolve().parent.parent / "data" / "upload"
    videos = sorted(upload_dir.glob("**/*.mp4"))
    if not videos:
        print("Nenhum video encontrado em data/upload/")
        sys.exit(1)
    capa, titulo = analisar(str(videos[0]))
    print(f"Capa: {capa}")
    print(f"Titulo: {titulo}")
