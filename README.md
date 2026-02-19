<img width="1642" height="946" alt="ShunyaNetSentinelAppGraphicV1" src="https://github.com/user-attachments/assets/f1348720-4d36-4bfa-b8d6-ea92bdfd0a9f" />


# ShunyaNet Sentinel

ShunyaNet Sentinel is a lightweight, cyberpunk-themed program that ingests RSS feeds (e.g., breaking news, social media), sends them to an LLM for analysis, and delivers alerts and summary reports directly to the GUI and Slack at regular intervals.

The project is built to utilize an LLM hosted using LMStudio, which can
be served:

-   On the same machine
-   On the same local network
-   Over the internet using your solution of choice (e.g., Tailscale)

For alerting, the program utilizes Slack Webhooks, enabling push
notifications to be sent to a mobile device.

The ShunyaNet Sentinel is compatible with the latest versions of Linux, MacOS, and Windows*

The quality of reporting and analysis is influenced by the prompt, context size, RSS feeds and LLM chosen. It's recommended you turn off thinking features. Models that seem to have performed well and generally follow instructions, include:
- GPT OSS 20b (thinking set to LOW in LMStudio)
- GPT OSS 120b (prob. overkill)
- Hermes 4 70b
- Gemma 3 27b it abliterated (mlabonne's version)
- Qwen3 32b and/or VL 30b (i forget which...)

*Strongly suggest macOS or Linux. See Known Issues below.

------------------------------------------------------------------------

# High-Level Workflow

1.  User enters topics of interest
2.  User provides a list of RSS feeds
3.  Sentinel pulls RSS feeds at user-configured intervals of time
4.  RSS content is sent to the user-provided LLM server
5.  The LLM reviews feeds and reports back to Sentinel on relevant topics of interest
6.  If Slack webhook is configured, alerts are forwarded and notifications can be accessed on ios/android (often with live links to referenced RSS content)
7.  Optional bulk analysis identifies trends over time

------------------------------------------------------------------------

# Core Capabilities

## RSS Feed Monitoring

-   Periodic polling of RSS feeds
-   Deduplication and timestamp tracking
-   Handles slow or malformed feeds (...more work to be done here)

## LLM-Based Analysis (Optional)

-   Sends RSS feed items to local LLM endpoint
-   Prompt-driven classification, summarization, filtering
-   Works with any OpenAI-compatible `/v1/chat/completions` endpoint, designed for LMStudio

## Alerting / Signal Generation

-   Slack webhook support
-   Structured output suitable for automation
-   Designed for integration into larger workflows

## Local & Self-Hosted

-   Other than polling RSS feeds, or sending replies to slack (optional), all information remains on hardware you control

------------------------------------------------------------------------

# Requirements

-   Python 3.10+
-   Dependencies listed in `requirements.txt`:
    -   PySide6==6.10.0
    -   feedparser==6.0.12
    -   requests==2.31.0
    -   python-dateutil==2.9.0.post0

Additional: 
- LM Studio-hosted LLM (recommended) - Or any
OpenAI-compatible `/v1/chat/completions` endpoint

------------------------------------------------------------------------

# Installation

## 1. Clone Repository

    git clone https://github.com/EverythingsComputer/ShunyaNet-Sentinel.git
    cd ShunyaNet-Sentinel

## 2. (Strongly Recommended) Create Virtual Environment

macOS / Linux:

    python3 -m venv venv
    source venv/bin/activate

Windows (PowerShell):

    python -m venv venv
    venv\Scripts\Activate.ps1

## 3. Install Requirements

    pip install -r requirements.txt

## 4. Run

macOS / Linux:

    python3 shunyanet_sentinel.py

Windows:

    python shunyanet_sentinel.py

------------------------------------------------------------------------

# Quick Start

1.  Click "Load Prompt File" and load `default_prompt.txt`
2.  Click "Load Data Source File" and Load `Default_test` RSS list
3.  Open "Additional Settings" and enter LLM URL into LMSTUDIO_URL (e.g., `http://x.x.x.x:<port>/v1/chat/completions`)
4.  Load model in LM Studio. Turn on "Serve on Local Network" in Server Settings
5.  Click the cat! (...or just hit Fetch / Send)


------------------------------------------------------------------------

# Full Instructions & Config

1. **Enter topics of interest**, or load one of the default lists provided. Up to 10 topics may be added to each list.

3. **Click “Load Prompt File” to load a prompt file.** A default prompt is provided (`default_prompt.txt`).
   1. You’re encouraged to tweak and revise this prompt - it may substantially improve the quality of reporting. There is A LOT of room for customization here.
   2. There is a 'default_prompt-always-reply.txt' included. This prompt requires the LLM choose one news item to report back on, even if no topics are triggered. You can use this to debug/test the LLM's analysis.

4. **Click “Load Data Source File” to load an RSS list.** Two default lists are provided. A short “Default_test” list and a longer “Default_long” list, which focuses on world-wide news and breaking news.
   1. Tailoring your own lists to your region or topics of interest will significantly affect the output of information. An example region-focused list that I used for a recent trip is provided ('India_regional_example-v1.txt').
   2. Reddit and blue-sky can be easily converted into RSS feeds. Programs, such as RSSBridge, can also generate RSS feeds from websites that don’t have one.
   2. The “Default_test” list is a short list of a variety of RSS feeds. The purpose is to keep the first RSS pull quick and short, so that you can diagnose whether all the pieces are working the way they should.

5. **In settings, set the following fields.** These will save and persist if you end and restart the program. The default settings will work with most configurations - but you must still enter field #1 yourself:

| Setting | Description | Default | Example / Notes |
|----------|------------|----------|------------------|
| **LLM_URL** | URL to your LM Studio (or compatible) server. `/v1` **must** be included at the end. | — | Local/LAN/Tailscale HTTP: `http://x.x.x.x:<port>/v1/chat/completions` <br> Tailscale HTTPS: `https://ca***a.tail2a*****.ts.net/v1/chat/completions` |
| **SLACK_WEBHOOK_URL** | Optional Slack webhook URL for sending alerts to Slack. | Optional | `https://hooks.slack.com/services/...` |
| **MAX_TOKENS** | Maximum tokens sent to the LLM per RSS pull. Rule of thumb: **1 token ≈ 4 characters**. If exceeding model context size, enable chunked mode. | 4000 | Increase carefully depending on your LLM's context window. |
| **MAX_TOKENS_BULK** | Maximum tokens used for bulk processing reports. When bulk processing is enabled, RSS feeds are saved and sent together with a special trend-analysis prompt. | 4000 | Likely needs to be increased for meaningful bulk reports. May stress VRAM and context limits. Recommended to disable bulk mode initially. |
| **FETCH_INTERVAL** | Time in seconds between RSS pulls and LLM analysis. | 600 (seconds, i.e. 10 min) | Do **not** set lower than total processing time or backlog may occur. |
| **ITEMS_PER_FEED** | Maximum number of RSS entries pulled per feed per cycle. Previously pulled items are ignored. | 50 | Higher values create a larger first pull. Most RSS feeds do not produce much more than 20 new items every 10 minutes, some much less. |
| **USE_CHUNKED_MODE** | Enables automatic splitting of RSS content if it exceeds token allowance. `1 = On`, `0 = Off`. | 1 | Prevents context overflow but may duplicate event reporting across chunks. |
| **CHUNK_SIZE** | Size of each chunk in **characters** (not tokens). | 8000 | Approximate conversion: **4 characters ≈ 1 token**. I REPEAT: THIS IS IN **CHARACTERS**. Should it be in tokens? Probably! But it's not.|
| **WRITE_TO_FILE** | Optional. Writes all pulled RSS content to a rolling file for external benchmarking, prompt testing, or model comparison. Does **not** affect core Sentinel functionality. `1 = On`, `0 = Off`. | 0 | Useful for offline LLM testing and evaluation. |
| **ANALYSIS_WINDOW** | Time interval used for each bulk processing report. | 3600 (seconds, i.e. 1h) | Used only when Bulk Processing is enabled. |
| **BULK_PROCESSING** | Enables periodic bulk RSS trend reports. `1 = On`, `0 = Off`. | 0 | Sends accumulated RSS feeds to the LLM for a single trend analysis report. May increase processing load significantly. |


6. **(In LM-Studio) load your model** of choice and be sure to set its context window to comfortably exceed the value you enter in the TOKENs field of ShunyaNet Sentinel (and bulk processing tokens, if that features is active).
      1. Although this is designed/tested with LMStudio in mind, it should work with any OpenAI-compatible /v1/chat/completions endpoint.

7. **Done! - Now click the cat!** (...or hit "Fetch / Send", or just wait the number of seconds you set in FETCH_INTERVAL)

**NOTE:** I recommend you keep it simple for the first run. Use the default settings & make sure it works. Then, tweak context & RSS feeds. Then, adjust the prompt. I’d be curious to see folks’ improved prompts….

------------------------------------------------------------------------

# Known Issues

-   Poor configuration choices can overload your LLM or hardware
-   Some RSS feeds may time out and or pull very slowly (i'm noticing this with fema.gov and NRc.gov. hmm...). If certain URLs slow down your pull, remove them from the list. I will try to improve handling of this in a future update.
-   Sentinel spontaneously quits on Windows 11 after a few hours. No idea why. No errors thrown. Doesn’t have this problem on Mac or Linux. If you must use Windows: Until I fix this, I recommend you set up a virtual box with Linux/ubuntu (or suggest a better solution, if you have one!). Note, this program will run fine on a raspberry pi!
-   Error message thrown when closed on MacOS, maybe also Linux. It doesn’t seem to affect how the program operates or its stability, though. I fixed this once, but it came back. I will fix it again …at some point.
-   Some "thinking" models produce malformed replies or get stuck in loops. I recommend turning thinking off first. 

------------------------------------------------------------------------

# Status

Alpha

------------------------------------------------------------------------

# License

This project is licensed under the **Apache 2.0**.

Attribution: ShynyaNet / EverythingsComputer

**No Warranty:** The software is provided "as is", without warranty of any kind, express or implied.

### Dependencies

This project uses the following third-party libraries. Please review their licenses when using, distributiong, and/or altering this project:

- **PySide6** — LGPL  
- **feedparser** — BSD  
- **requests** — Apache 2.0  
- **python-dateutil** — BSD

------------------------------------------------------------------------

# Name Origin

My cat.
