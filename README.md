## Fox Telegram Summarizer Bot

Telegram bot for automatic summarization of group messages using OpenAI and speech-to-text for voice messages via WhisperX. The bot collects messages from specified channels, groups them by topics, and creates concise summaries.

### Features

- **Message collection**: automatically collects all text messages from specified channels or groups
- **Voice message recognition**: transcribes audio via **WhisperX**
- **Relevance filtering**: filters messages using OpenAI before summarization (optional)
- **Topic-based summaries**: groups messages by topics and produces 1–2 sentence summaries per topic
- **Structured Output**: uses Pydantic models with OpenAI's structured output for reliable topic extraction
- **Participant display**: optionally shows list of participants for each topic with profile links
- **Source links**: generates links back to original messages
- **Link summarization**: automatically processes links in messages (including YouTube subtitles)
- **Daily automatic summarization**: scheduled daily summaries via cron or task scheduler
- **Template system**: customizable summary formatting using Jinja2 templates
- **Prompt customization**: all prompts are editable via text files in the `prompts/` directory with fallback support
- **Per-chat configuration**: individual settings for each chat/channel via `config.yaml`
- **Timezone support**: proper timezone handling for daily summaries in Docker containers
- **LLM logging**: comprehensive logging of all LLM requests/responses for debugging

---

## Bot Setup in BotFather

Before you can use the bot, you need to create it and get a token from Telegram's BotFather.

### 1. Create a new bot

1. Open Telegram and search for `@BotFather`
2. Start a conversation with BotFather
3. Send the command `/newbot`
4. Follow the instructions:
   - Choose a name for your bot (e.g., "My Summarizer Bot")
   - Choose a username for your bot (must end with `bot`, e.g., `my_summarizer_bot`)

### 2. Get your bot token

After creating the bot, BotFather will send you a message with your bot token. It looks like this:

```
1234567890:AAExampleBotToken1234567890
```

**Important:** Keep this token secret! Never commit it to version control or share it publicly.

### 3. Set bot description (optional)

You can optionally set a description for your bot:

```
/setdescription
```

Then select your bot and provide a description, for example:
```
Telegram bot for automatic summarization of channel messages using OpenAI.
```

### 4. Configure bot commands (optional)

The bot uses standard commands (`/summarize`, `/clear`) that work automatically. However, you can register them in BotFather for better user experience:

```
/setcommands
```

Select your bot and add the following commands:
```
summarize - Create a summary of messages for the current chat
clear - Clear stored messages for the current chat
```

### 5. Add the bot to your channels

After creating the bot, you need to:

1. Add the bot to your target channels as an **administrator**
2. Grant the bot permission to **read messages**
3. The bot will automatically start collecting messages from configured channels

**Note:** The bot must be added as an administrator with message read permissions to work properly.

---

## Installation (without Docker)

1. **Clone the repository**

```bash
git clone https://github.com/<your-username>/fox_tg_summarizer_bot.git
cd fox_tg_summarizer_bot
```

2. **Create and activate a virtual environment (recommended)**

```bash
python -m venv venv
source venv/bin/activate      # Linux / macOS
# or
venv\Scripts\activate         # Windows
```

3. **Install system dependencies**

WhisperX requires `ffmpeg` to be installed on your system:

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

**Windows:**
Download from https://ffmpeg.org/download.html and add to PATH

4. **Install Python dependencies**

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

4. **Create `.env` file**

Create a `.env` file in the project root and fill in the required variables:

```env
# Telegram
BOT_TOKEN=1234567890:AAExampleBotToken
TRASH_CHAT_ID=

# Channels to monitor (IDs and/or usernames, comma-separated)
CHANNEL_ID=-1001234567890,-1009876543210
CHANNEL_USERNAME=@my_channel,@another_channel

# OpenAI
OPENAI_API_KEY=sk-openai-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
OPENAI_MIN_DELAY_S=0.5
OPENAI_MAX_RETRIES=5
OPENAI_BACKOFF_BASE_S=1.0
OPENAI_BACKOFF_MAX_S=30.0


# SQLite database
SQLITE_DB_PATH=data/messages.db
```

**Environment variables description**

- **BOT_TOKEN** – Telegram bot token from `@BotFather`
- **TRASH_CHAT_ID** – optional chat ID (private chat/group with the bot) used for safe message existence checks
- **CHANNEL_ID** – one or more numeric channel IDs separated by comma (e.g. `-1001234567890,-1009876543210`)
- **CHANNEL_USERNAME** – one or more channel usernames separated by comma (e.g. `@my_channel,@another_channel`)
- **OPENAI_API_KEY** – your OpenAI API key
- **OPENAI_BASE_URL** – base URL for the OpenAI API (default: `https://api.openai.com/v1`)
- **OPENAI_MODEL** – model name for summarization (default: `gpt-4o-mini`)
- **OPENAI_MIN_DELAY_S / OPENAI_MAX_RETRIES / OPENAI_BACKOFF_BASE_S / OPENAI_BACKOFF_MAX_S** – rate limiting and retry/backoff settings for OpenAI requests
- **SQLITE_DB_PATH** – path to the SQLite database file (by default `data/messages.db`)

5. **Run database directory creation (if needed)**

Make sure the `data/` directory exists so SQLite can create `messages.db`:

```bash
mkdir -p data
```

6. **Run the bot**

```bash
python bot.py
```

---

## Running with Docker

This project includes a `Dockerfile` and `docker-compose.yml` for easy deployment.

### 1. Create `.env`

Create a `.env` file in the project root with the same variables as described above in the **Installation (without Docker)** section. This file will be mounted into the container via `env_file` in `docker-compose.yml`.

### 2. Start with Docker Compose

```bash
docker-compose up --build -d
```

This will:

- Build the image from the local `Dockerfile`
- Start a container named `fox_tg_summarizer_bot`
- Mount the local `./data` directory into `/app/data` inside the container so the SQLite database (`data/messages.db`) is persisted

### 3. Logs and management

- View logs:

```bash
docker-compose logs -f
```

- Stop the bot:

```bash
docker-compose down
```

---

## How to get `CHANNEL_ID`

1. Add your bot to the target channel as an administrator (with permission to read messages)  
2. Forward any message from that channel to `@userinfobot`  
3. Copy the chat ID from the response (it will start with `-100`) and use it in `CHANNEL_ID`

---

## Usage

### Basic Usage

1. Make sure the bot is running (locally or in Docker)
2. Add the bot to the desired channels as an administrator with message read permissions
3. The bot will automatically start collecting messages from the configured channels
4. To create a summary, send the command in the chat with the bot:

```text
/summarize
```

5. To clear stored messages for the current chat:

```text
/clear
```

### Daily Automatic Summarization

To enable automatic daily summaries:

1. Set `daily_enabled: true` in `config.yaml` for your chat
2. Configure `daily_time` (e.g., `"23:00"`) in your timezone
3. Set up a cron job or task scheduler to run `daily_summary.py`:

**Example cron (runs every day at 00:00 UTC):**
```bash
0 0 * * * cd /path/to/fox_tg_summarizer_bot && /path/to/venv/bin/python daily_summary.py
```

**Example using Python schedule (included in the bot):**
The bot includes a scheduler that can run daily summaries automatically. See `daily_summary.py` for details.

**Note:** The bot automatically converts `daily_time` from your specified timezone to UTC, ensuring correct operation in Docker containers regardless of container timezone.

### Link Summarization

The bot can automatically process links in messages:

- **Regular links**: Downloads content and creates a brief summary stored in the message text
- **YouTube links**: Extracts subtitles and creates summaries

Enable link processing in `config.yaml`:
- `links_summarize: true` – processes all links
- `links_summarize_show: true` – sends summary as separate message to chat
- `youtube_summarize: true` – processes only YouTube links
- `youtube_summarize_show: true` – sends YouTube summary to chat

**Limitations:**
- Maximum 3 links per message
- Maximum 16000 characters of content per link

---

## Configuration

### Environment Variables (`.env`)

See the **Installation** section above for required environment variables.

### Per-chat configuration (`config.yaml`)

You can customize summarization behavior per chat (by `chat_id` or `username`) using `config.yaml`. 

**Getting started:**
1. Copy `config.yaml.example` to `config.yaml`
2. Customize settings for your channels (see `config.yaml.example` for detailed explanations)

**Basic example:**

```yaml
defaults:
  timezone: "Europe/Moscow"  # or "+03:00"
  topics:
    only_top: false
    min_messages: 5
    max_topics: 0  # 0 = unlimited
    show_users: false
    user_list_length: 10
  summarize:
    command_enabled: true
    daily_enabled: false
    day_start_time: "06:00"
    daily_time: "23:00"
    max_output_tokens: 0  # 0 = unlimited (recommended if responses are truncated)
  links_summarize: false
  links_summarize_show: false
  youtube_summarize: false
  youtube_summarize_show: false

chats:
  "-1001234567890":
    topics:
      only_top: false
      min_messages: 3
      max_topics: 10
    summarize:
      daily_enabled: true
      daily_time: "10:00"
    links_summarize: true
    links_summarize_show: true
```

**Configuration parameters:**

**Timezone (`timezone`):**
- Supports IANA format (`"Europe/Moscow"`) or UTC offset (`"+03:00"`)
- Used for converting `daily_time` and `day_start_time` to UTC
- Can be set globally in `defaults` or overridden per chat

**Topics (`topics`):**
- `only_top` – if `true`, only topics with at least `min_messages` messages are included
- `min_messages` – minimum number of messages in a topic to be included
- `max_topics` – maximum number of topics in summary (0 = unlimited)
- `show_users` – if `true`, shows list of participants for each topic with profile links
- `user_list_length` – maximum number of participants to display (0 = unlimited)

**Summarization (`summarize`):**
- `command_enabled` – enable/disable `/summarize` command
- `daily_enabled` – enable automatic daily summarization
- `day_start_time` – start of day for calculating message window (format: `"HH:MM"`)
- `daily_time` – time to send daily summary (format: `"HH:MM"`, in specified timezone)
- `summarize_template` – path to custom Jinja2 template (relative to project root)
- `no_clear_after_summarize` – if `true`, messages are not archived after summarization
- `max_output_tokens` – maximum number of tokens for LLM response during summarization
  - `0` or omitted – unlimited (recommended if responses are being truncated)
  - Positive number – maximum tokens in response
  - If response doesn't fit within `max_output_tokens`, summarization may not be output or may be truncated
  - Default in code: 65535 for structured output, 32000 for fallback
  - Example: `max_output_tokens: 0` to disable limit

**Voice recognition:**
- `voice_recognition_enabled` – enable/disable voice message recognition via WhisperX
  - `true` – bot will automatically transcribe voice messages, audio files, and video notes and save transcribed text to DB
  - `false` – recognition disabled (default)
  - Requires `ffmpeg` installed on the system and `whisperx` library
  - Example: `voice_recognition_enabled: true` to enable recognition

**Link summarization:**
- `links_summarize` – if `true`, downloads and summarizes link content for storage in DB
- `links_summarize_show` – if `true`, sends link summary as separate message to chat
- `youtube_summarize` – if `true`, processes only YouTube links (uses subtitles)
- `youtube_summarize_show` – if `true`, sends YouTube summary as separate message

**Chat identification:**

Keys in `chats` can be:
- String `chat_id` (e.g. `"-1001234567890"`)
- Channel/group `username` without `@` and in lowercase (e.g. `"cords_community"`)

See `config.yaml.example` for detailed explanations and examples.

---

## Project structure

```text
fox_tg_summarizer_bot/
├── bot.py                     # Entry point: bot startup
├── config.py                  # Configuration loaded from environment variables
├── config.yaml                # Per-chat summarization configuration
├── config.yaml.example        # Example configuration with detailed comments
├── daily_summary.py           # Script for daily automatic summarization
├── handlers/
│   └── messages.py            # Message handlers and commands
├── services/
│   ├── db.py                  # SQLite database layer
│   ├── openai_service.py      # OpenAI API integration with Structured Output
│   ├── whisper_service.py     # WhisperX integration for speech recognition
│   ├── summarizer.py          # Summarization logic
│   └── link_summarizer.py     # Link content summarization
├── utils/
│   ├── prompt_loader.py       # Prompt loading utilities
│   ├── formatter.py           # Output formatting with Jinja2 templates
│   └── chat_config.py         # Chat configuration and timezone handling
├── prompts/                   # Text files with prompts (editable)
│   ├── relevance_prompt.txt
│   ├── relevance_prompt_fallback.txt
│   ├── summarization_prompt.txt
│   ├── summarization_prompt_fallback.txt
│   ├── structured_system_prompt.txt
│   ├── structured_user_prompt.txt
│   └── fallback_user_prompt.txt
├── templates/                 # Jinja2 templates for summary formatting
│   └── summarize_default.txt  # Default summary template
├── data/                      # SQLite database and related files
├── logs/                      # LLM request/response logs
│   ├── llm_chat.log
│   └── llm_structured.log
├── docker-compose.yml         # Docker Compose configuration
├── Dockerfile                 # Docker image definition
├── requirements.txt           # Python dependencies
├── test_summarizer.py         # Example tests for summarization
└── README.md                  # Project documentation
```

---

## Prompt customization

You can customize prompts in the `prompts/` folder. All prompts support fallback chains: if the main file is missing, the bot uses the fallback file, and if that's missing, it uses a built-in prompt.

**Main prompts:**
- `relevance_prompt.txt` – prompt for relevance filtering of messages
- `summarization_prompt.txt` – prompt for generating summaries

**Fallback prompts:**
- `relevance_prompt_fallback.txt` – fallback for relevance prompt
- `summarization_prompt_fallback.txt` – fallback for summarization prompt

**Structured Output prompts:**
- `structured_system_prompt.txt` – system prompt for structured output (defines Pydantic models)
- `structured_user_prompt.txt` – user prompt for structured output (with `{messages_text}` placeholder)
- `fallback_user_prompt.txt` – fallback prompt for regular output (with `{messages_text}` placeholder)

**Custom prompts per chat:**

You can specify custom prompts in `config.yaml`:
```yaml
chats:
  "-1001234567890":
    topics:
      prompt: "prompts/custom_summarization_prompt.txt"
      structured_system_prompt: "prompts/custom_structured_system_prompt.txt"
```

Changes to these files will affect how the bot groups and summarizes topics. Prompts are loaded asynchronously on first use.

## Template system

The bot uses Jinja2 templates to format summaries. You can customize the output format by creating your own template.

**Default template:** `templates/summarize_default.txt`

**Custom template example:**

Create `templates/my_template.txt`:
```html
<b>{{ header }}</b>

{% for topic in topics %}
• {% if topic.link %}<a href="{{ topic.link }}">{{ topic.topic }}</a>{% else %}{{ topic.topic }}{% endif %}{% if topic.message_count > 1 %} ({{ topic.message_count }}){% endif %}. {{ topic.description }}{% if topic.participants %}
<i>{{ topic.participants | join(', ') }}</i>{% endif %}

{% endfor %}
{{ footer }}
```

**Available template variables:**
- `{{ header }}` – summary header (default: "Сегодня обсуждали:")
- `{{ topics }}` – list of topics (loop with `{% for topic in topics %}`)
  - `topic.topic` – topic title
  - `topic.link` – link to first message (may be empty)
  - `topic.description` – topic description
  - `topic.message_count` – number of messages in topic
  - `topic.participants` – formatted list of participants (if `show_users=true`)
- `{{ footer }}` – summary footer (default: "#summarize")

**Using custom template:**

Specify in `config.yaml`:
```yaml
chats:
  "-1001234567890":
    summarize:
      summarize_template: "templates/my_template.txt"
```

If the custom template is not found, the bot falls back to `templates/summarize_default.txt`, and if that's missing, uses a built-in fallback template.

## Structured Output

The bot uses OpenAI's Structured Output feature with Pydantic models for reliable topic extraction:

- **Primary method**: `responses.parse` API (preferred)
- **Fallback method**: `beta.chat.completions.parse` API
- **Final fallback**: Regular chat completions with text parsing

**Benefits:**
- Reliable topic extraction with validated data structures
- Participant list with user IDs/usernames and message counts
- Automatic sorting by activity (message count per topic)
- Token optimization (returns only first message ID per topic)

**Pydantic models:**
- `TopicItem` – topic with description, message IDs (limited to 1), and participants list
- `TopicsResponse` – response containing list of topics

All structured output requests are logged to `logs/llm_structured.log` for debugging.

---

## Logging

The bot provides comprehensive logging for debugging and monitoring:

**LLM request/response logs:**
- `logs/llm_chat.log` – regular chat completions
- `logs/llm_structured.log` – structured output requests

Both logs include:
- Request parameters (model, messages, temperature, etc.)
- Response content and metadata
- Token usage (prompt, completion, total)
- Response length and finish reason
- Timestamps

**Daily summary runs:**
- `logs/last_daily_runs.json` – tracks last run time per chat to prevent duplicate runs

**Application logs:**
- Standard output/error for general bot operations
- Docker logs via `docker-compose logs -f`

## Summary output examples

**Example with participants (`show_users: true`):**

```
Сегодня обсуждали:

- Планирование сходки (https://t.me/c/2340656226/104590) (30). Обсуждение идей для предстоящей сходки, включая бейджи, форматы джемов и музыкальные инструменты.

(Леонид,Alex Vans,Niko Niko)

- Обсуждение нового проекта (https://t.me/c/2340656226/104591) (15). Планирование архитектуры и выбор технологий.

(Иван Петров,Мария Смирнова)

#summarize
```

**Example without participants (`show_users: false`):**

```
Сегодня обсуждали:

- Планирование сходки (https://t.me/c/2340656226/104590) (30). Обсуждение идей для предстоящей сходки, включая бейджи, форматы джемов и музыкальные инструменты.

- Обсуждение нового проекта (https://t.me/c/2340656226/104591) (15). Планирование архитектуры и выбор технологий.

#summarize
```

**Features:**
- Each topic includes a link to the first message
- Message count in parentheses after the link
- Participants sorted by activity (if enabled)
- Participant names formatted as links if username is available

## Technology stack

**Backend (Python):**
- `aiogram 3.13.1` – Telegram Bot API framework
- `openai 1.54.3` – OpenAI API client with Structured Output support
- `aiosqlite 0.20.0` – async SQLite access
- `httpx 0.27.2` – async HTTP client for external APIs
- `aiofiles 24.1.0` – async file operations
- `pydantic 2.9.2` – data validation and Structured Output
- `PyYAML 6.0.2` – YAML configuration parsing
- `youtube-transcript-api 0.6.2` – YouTube subtitle extraction
- `markdownify 0.13.1` – HTML to Markdown conversion
- `schedule` – task scheduler
- `python-dotenv 1.0.1` – environment variable loading
- `Jinja2 3.1.4` – template engine for summary formatting
- `whisperx` – speech recognition library
- `torch` – PyTorch (required by WhisperX)
- `torchaudio` – PyTorch audio processing (required by WhisperX)

**Database:**
- SQLite with WAL mode for better concurrency

**External APIs:**
- OpenAI API (or any compatible API via `OPENAI_BASE_URL`)

**Speech Recognition:**
- WhisperX — local library for voice transcription

## Notes and production recommendations

- The bot stores messages in an SQLite database under `data/messages.db` (WAL mode enabled for better performance)
- Make sure the bot has permission to read messages in the target channels (add as administrator)
- Voice message transcription uses local WhisperX library (only if `voice_recognition_enabled: true` in `config.yaml`, requires ffmpeg to be installed on the system)
- The bot supports any OpenAI-compatible API via `OPENAI_BASE_URL` (useful for local models or proxies)
- **Timezone handling**: The bot properly handles timezones in Docker containers by converting all times to UTC internally
- **Token optimization**: Only the first message ID per topic is returned to save tokens; full message count is still tracked
- **Output token limits**: Configure `max_output_tokens` in `config.yaml` to control LLM response length (set to `0` to disable limit if responses are being truncated)
- **Rate limiting**: Built-in exponential backoff for 429 errors and throttling to prevent rate limits
- **Message archiving**: Messages are automatically archived after successful summarization (can be disabled with `no_clear_after_summarize: true`)
- For higher reliability and scale, you can replace SQLite with an external database and extend the `services/db.py` layer
- **Participant display**: When `show_users: true`, participants are sorted by activity and formatted with profile links (if username available)

---

## Contributing

Contributions are very welcome! If you want to report a bug, propose a feature, or submit a pull request, please follow these guidelines:

- **Fork** the repository and create a feature branch (e.g. `feature/add-new-command`)
- **Follow the existing style** and structure of the codebase (handlers in `handlers/`, services in `services/`, utilities in `utils/`)
- **Keep changes focused** – try to make each PR about one logical change
- **Update or add tests** in `test_summarizer.py` if you change summarization logic or other critical behavior
- **Document user-facing changes** in the `README.md` or code comments where appropriate
- **Do not commit secrets** – never commit `.env` or any real tokens/keys

For more details, see the dedicated [CONTRIBUTING.md](CONTRIBUTING.md) file.

---

## License

This project is licensed under the **MIT License** – see the `LICENSE` file for details.
