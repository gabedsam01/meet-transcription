# Provedor de Transcrição Groq Cloud (Speech-to-Text)

Este documento explica como configurar, usar e solucionar problemas com o provedor de transcrição **Groq Cloud** no Meet Transcription.

---

## 1. Como obter a API Key

Para utilizar a transcrição ultrarrápida da Groq Cloud:
1. Cadastre-se ou faça login no [Groq Console](https://console.groq.com/).
2. Vá até a seção **API Keys** no menu lateral.
3. Clique em **Create API Key**, dê um nome para a chave (ex: `meet-transcription`) e copie o token gerado.
4. No Meet Transcription, acesse a aba **Models** no menu superior, selecione o Groq nas credenciais e cole a API Key obtida. Ela será salva de forma criptografada.

---

## 2. Modelos Disponíveis

O provedor Groq suporta os seguintes modelos otimizados para Whisper:
*   `whisper-large-v3-turbo` (Padrão, mais rápido)
*   `whisper-large-v3` (Mais preciso em alguns idiomas complexos)

---

## 3. Limites (Free Tier vs Dev Tier) e Pré-processamento

O tamanho máximo de arquivo aceito pela API da Groq varia conforme a sua conta:
*   **Free Tier (Limite padrão):** 25 MB.
*   **Dev Tier (Limite avançado):** 100 MB.

### Configuração no `.env`

Você pode configurar o limite máximo global e o comportamento de teste via variáveis de ambiente no arquivo `.env`:

```env
# Define o limite padrão em MB para o Groq (padrão: 25)
GROQ_MAX_UPLOAD_MB=25

# Se definido como true, permite uploads de até 100 MB diretamente sem forçar chunking abaixo de 25 MB
GROQ_USE_DEV_LIMIT=false

# Opcionais do formato de resposta (verbose_json ativa timestamps finos de segmentos/palavras)
GROQ_RESPONSE_FORMAT=verbose_json
GROQ_TIMESTAMP_GRANULARITIES=segment,word
```

### Compressão e Divisão Automática (Chunking)

Se o arquivo de áudio exceder o limite ativo (`GROQ_MAX_UPLOAD_MB` ou 100 MB se `GROQ_USE_DEV_LIMIT=true`), o worker executará automaticamente o pipeline de áudio:
1. Extração do áudio original.
2. Compressão em MP3 com bitrate reduzido (formato preferencial para economia de banda com Groq).
3. Caso ainda exceda o limite após compressão, o áudio será particionado em pedaços (chunks) menores que o limite do free tier (~24 MB).
4. Os pedaços são enviados paralelamente/sequencialmente à Groq e os textos resultantes são costurados (`stiching`) preservando os offsets corretos de tempo.

---

## 4. Comparativo: Groq vs Outros Provedores

| Característica | Deepgram | Gemini | OpenRouter | Groq |
| :--- | :--- | :--- | :--- | :--- |
| **Velocidade** | Rápida | Média | Depende do modelo | **Ultrarrápida** |
| **Diarização** | Real / Excelente | Pseudo (via Prompt) | Depende do modelo (ruim) | **Nenhuma** |
| **Timestamps** | Sim (palavra/locutor) | Não | Segmento (se suportado) | **Segmento e Palavra** |
| **Custo** | Pago por minuto | Pago/Gratuito (Flash) | Pago (por milhão de tokens) | **Gratuito / Muito barato** |
| **Ideal Para** | Reuniões com vários participantes onde locutores importam | Análise multimodal avançada sem pressa | Variedade de modelos experimentais | **Transcrição rápida de reuniões pequenas/médias de baixo custo** |

---

## 5. Ausência de Diarização Real

*   A Groq Cloud utiliza a engine Whisper crua. **Não há suporte para diarização real (separação de locutores)** na API de áudio da Groq.
*   O resultado final identificará os tempos dos segmentos (`timestamps`) mas todos os locutores serão mapeados como `null`. Caso precise de diarização, utilize a **Deepgram** ou utilize o pipeline de diarização local complementar do sistema (se ativo).

---

## 6. Solução de Problemas (Troubleshooting)

### Erro HTTP 429 (Rate Limit)
*   **Causa:** Você excedeu o limite de requisições por minuto (RPM) ou de tokens por minuto (TPM) da Groq.
*   **Comportamento:** O sistema captura o erro `provider_rate_limited`, lê o cabeçalho `retry-after` enviado pela Groq e agenda a tentativa de nova transcrição com backoff exponencial respeitando esse tempo mínimo.
*   **Ação:** Verifique os limites de sua conta no Groq Console ou considere fazer o upgrade para reduzir a ocorrência de limites.

### Erro HTTP 413 (File Too Large)
*   **Causa:** O arquivo enviado foi maior que o limite configurado pela Groq para a sua chave de API.
*   **Ação:** Certifique-se de que a variável `AUDIO_PREPROCESSING_ENABLED=true` está ativa no `.env` para que o compressor de áudio do worker possa reduzir o tamanho do arquivo antes de enviar à API.

### Erro HTTP 401 / 403 (Unauthorized / Forbidden)
*   **Causa:** Chave de API inválida, expirada ou sem permissão de acesso.
*   **Ação:** Remova e configure novamente a API Key na aba **Models** do Meet Transcription.
