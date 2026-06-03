# Google Meet Drive Deepgram Transcriber

Worker Python containerizado para monitorar uma pasta compartilhada do Google Drive, baixar gravações MP4 do Google Meet, enviar o vídeo diretamente para a Deepgram e subir um TXT com a transcrição para outra pasta do Drive.

O MVP roda como um único container, sem UI, sem banco de dados, sem fila, sem N8N, sem FFmpeg e sem OAuth por navegador.

## Fluxo

1. A reunião é gravada no Google Meet.
2. O Google processa e salva o MP4 no Drive do organizador.
3. Um membro da equipe move ou copia o MP4 para a pasta compartilhada de entrada.
4. O worker detecta o vídeo por polling.
5. O MP4 é baixado temporariamente no container.
6. O MP4 é enviado diretamente para a Deepgram via HTTP.
7. Um TXT legível é gerado.
8. O TXT é enviado para a pasta compartilhada de saída.
9. Arquivos temporários locais são removidos.
10. O ID do vídeo é registrado em `data/processed_files.json`.

## Pastas Google Drive

Pasta de entrada:

```txt
1zS39e71BqRinq2HXpdK81dU7bTgIGdoW
```

Pasta de saída:

```txt
1W7Sq-VoNNnAiUes1wZEO9MK_eIt58Uxs
```

As duas pastas precisam ser compartilhadas com o e-mail da Service Account.

## Criar Credenciais Google

1. Acesse `https://console.cloud.google.com/`.
2. Crie ou selecione um projeto.
3. Ative a API `Google Drive API` em `APIs & Services`.
4. Vá em `IAM & Admin` > `Service Accounts`.
5. Crie uma Service Account.
6. Abra a Service Account criada e vá em `Keys`.
7. Crie uma chave JSON.
8. Salve o arquivo como `service-account.json`.
9. Coloque o arquivo em `./secrets/service-account.json`.
10. Compartilhe a pasta de entrada e a pasta de saída com o e-mail da Service Account.

Não torne os arquivos públicos. O worker usa a Google Drive API para baixar o binário e não envia links públicos para a Deepgram.

## Configuração

Copie o exemplo de ambiente:

```bash
cp .env.example .env
mkdir -p secrets data tmp
```

Coloque o JSON da Service Account em:

```txt
./secrets/service-account.json
```

Edite `.env` e preencha:

```env
DEEPGRAM_API_KEY=sua_chave_deepgram
```

As demais variáveis já vêm com os IDs das pastas aprovadas:

```env
GOOGLE_AUTH_MODE=service_account
GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/service-account.json
SOURCE_DRIVE_FOLDER_ID=1zS39e71BqRinq2HXpdK81dU7bTgIGdoW
DESTINATION_DRIVE_FOLDER_ID=1W7Sq-VoNNnAiUes1wZEO9MK_eIt58Uxs
POLL_INTERVAL_SECONDS=300
TMP_DIR=/app/tmp
STATE_FILE=/app/data/processed_files.json
DEEPGRAM_MODEL=nova-3
DEEPGRAM_LANGUAGE=pt-BR
DEEPGRAM_SMART_FORMAT=true
DEEPGRAM_PUNCTUATE=true
DEEPGRAM_DIARIZE=true
DEEPGRAM_UTTERANCES=true
```

## Rodar Com Docker Compose

Build da imagem:

```bash
docker compose build
```

Rodar uma vez:

```bash
docker compose run --rm meet-transcriber python -m app.main --once
```

Rodar continuamente:

```bash
docker compose up -d
```

Ver logs:

```bash
docker logs -f meet-drive-deepgram
```

## Reprocessar Um Arquivo

Para forçar o reprocessamento de um arquivo específico do Google Drive:

```bash
docker compose run --rm meet-transcriber python -m app.main --once --reprocess GOOGLE_DRIVE_FILE_ID
```

O estado anterior só é sobrescrito se a nova transcrição for enviada com sucesso.

## Estado Persistente

O arquivo `data/processed_files.json` é criado em runtime e fica persistido pelo volume Docker.

Exemplo:

```json
{
  "google_drive_file_id": {
    "name": "arquivo.mp4",
    "processed_at": "2026-06-03T10:45:00+00:00",
    "transcript_drive_file_id": "id_do_txt_no_drive"
  }
}
```

Se um vídeo já estiver nesse JSON, ele não será processado novamente, exceto usando `--reprocess`.

## Desenvolvimento Local

Instale dependências:

```bash
python -m pip install -r requirements.txt
```

Rodar testes:

```bash
python -m pytest -v
```

Validar imports/compilação:

```bash
python -m compileall app
```

Validar Compose:

```bash
docker compose config
```

Esse comando exige que `.env` já exista. Use `cp .env.example .env` antes da validação.

## Operação Em VPS

1. Clone o repositório na VPS.
2. Crie `.env` a partir de `.env.example`.
3. Crie `secrets/`, `data/` e `tmp/`.
4. Coloque `service-account.json` em `secrets/`.
5. Confirme que as pastas do Drive foram compartilhadas com a Service Account.
6. Execute `docker compose build`.
7. Execute `docker compose up -d`.
8. Acompanhe com `docker logs -f meet-drive-deepgram`.

## Segurança

Nunca commitar:

```txt
.env
service-account.json
token.json
tmp/
data/processed_files.json
```

O diretório `secrets/` é montado como volume somente leitura no container.

## Saída TXT

Quando a Deepgram retorna utterances, o TXT usa timestamps e speakers:

```txt
TRANSCRIÇÃO DA REUNIÃO

Arquivo original: nome-do-video.mp4
Data de processamento: 2026-06-03 10:45
ID Google Drive: abc123

==================================================

[00:00:01] Speaker 0:
Texto da fala...

==================================================

Fim da transcrição.
```

Se utterances não vierem na resposta, o worker salva o texto corrido retornado pela Deepgram.
