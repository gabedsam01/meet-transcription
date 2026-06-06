# Provedor de Transcrição AssemblyAI (Speech-to-Text)

Este documento explica como configurar, usar e solucionar problemas com o provedor de transcrição **AssemblyAI** no Meet Transcription, com foco em diarização real (identificação de locutores) para reuniões.

---

## 1. Por que usar AssemblyAI

O **AssemblyAI** é uma das principais plataformas de inteligência artificial para voz e transcrição do mercado. Ele se destaca especialmente por:
*   **Diarização de locutores altamente precisa:** Excelente capacidade de discernir quem está falando em reuniões ou podcasts (separando em Speaker A, Speaker B, etc.).
*   **Modelos modernos:** Acesso aos modelos `universal-3-pro` e `universal-2`, treinados em milhões de horas de dados de fala reais.
*   **Processamento assíncrono robusto:** Adequado para processar arquivos de áudio longos por meio de polling seguro.

---

## 2. Como obter a API Key

Para utilizar a transcrição da AssemblyAI:
1.  Cadastre-se ou faça login no [AssemblyAI Dashboard](https://www.assemblyai.com/).
2.  Copie a sua **API Key** exibida na página inicial do painel (geralmente sob o cabeçalho "Your API Key").
3.  No Meet Transcription, acesse a aba **Models** no menu superior.
4.  Localize a seção do **AssemblyAI**, preencha a API Key e defina as configurações desejadas. A chave será criptografada ao ser salva.

---

## 3. Diarização e Configurações Personalizadas

O AssemblyAI oferece controle sobre a identificação de locutores turn-by-turn (utterances). No painel de credenciais do Meet Transcription, você pode ajustar:

*   **Speaker Labels (checkbox):** Quando ativo (`true`), a API separa o texto em turnos de fala por locutor. O Meet Transcription normaliza o formato original da API (`A`, `B`, etc.) exibindo-o na interface como `Speaker A`, `Speaker B`, mantendo o rótulo original no banco de dados para permitir futuras renomeações.
*   **Número esperado de locutores (speakers_expected - opcional):** Se você souber previamente quantas pessoas participaram da reunião, informar esse valor ajuda o algoritmo de diarização a ser ainda mais assertivo ao dividir as vozes.

---

## 4. Custos e Limites

*   **Custos:** O AssemblyAI é um provedor pago. Os valores variam conforme o modelo de transcrição selecionado (`universal-3-pro` ou `universal-2`) e recursos ativados (como diarização). Orientamos o usuário a conferir o dashboard ou a tabela de preços atual da AssemblyAI para obter os valores exatos por minuto.
*   **Limites de tamanho:** O limite de upload do provedor é configurado por padrão em **99 MB** (através da variável `AUDIO_MAX_FILE_API_MB` ou equivalente).
*   **Compressão automática:** Se o arquivo de mídia original for maior que 99 MB, o worker aplicará automaticamente o pipeline de pré-processamento (extraindo áudio mono 16 kHz em FLAC ou MP3) para reduzir o peso antes de realizar o envio, evitando estourar os limites da API sem necessidade.

---

## 5. Comparativo: AssemblyAI vs Outros Provedores

| Característica | Deepgram | AssemblyAI | Gemini | Groq / OpenRouter | Local (CPU) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Velocidade** | Rápida | Média (polling) | Média | Ultrarrápida | Lenta / Depende do hardware |
| **Diarização** | Real (Excelente) | **Real (Muito Forte para reuniões)** | Pseudo (via Prompt) | Nenhuma | Nenhuma (no MVP local) |
| **Timestamps** | Sim (palavra/locutor) | **Sim (palavra/locutor)** | Não | Segmento | Segmento |
| **Custo** | Pago por minuto | **Pago por minuto** | Pago/Gratuito (Flash) | Muito barato / Free Tier | Gratuito (CPU local) |
| **Ideal Para** | Reuniões gerais | **Reuniões estruturadas com foco em locutores** | Análises multimodais de alta latência | Transcrição rápida e de baixo custo | Privacidade máxima e offline |

---

## 6. Solução de Problemas (Troubleshooting)

### Erro HTTP 401 / 403 (Autenticação Inválida)
*   **Causa:** API Key incorreta ou expirada.
*   **Ação:** Verifique e atualize sua API Key na aba **Models**.

### Erro HTTP 429 (Rate Limit)
*   **Causa:** Muitas requisições simultâneas efetuadas na mesma conta.
*   **Ação:** O sistema agenda automaticamente uma nova tentativa (retry) respeitando as orientações de tempo recomendadas pela API.

### Erro de Timeout no Polling
*   **Causa:** Transcrições muito demoradas que excedem o tempo limite de espera (default: 30 minutos).
*   **Ação:** O worker registra o status do job como falho com mensagem amigável e permite a redemanda de transcrição.
