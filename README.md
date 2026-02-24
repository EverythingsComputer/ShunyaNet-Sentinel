<img width="1642" height="946" alt="ShunyaNetSentinelAppGraphicV1" src="https://github.com/user-attachments/assets/f1348720-4d36-4bfa-b8d6-ea92bdfd0a9f" />


# ShunyaNet Sentinel

ShunyaNet Sentinel is a lightweight, cyberpunk-themed program that ingests RSS feeds (e.g., breaking news, social media), sends them to an LLM for analysis, and delivers alerts and summary reports directly to the GUI and Slack at regular intervals.

The project is built to utilize an LLM hosted locally on the same machine or network (e.g., with Tailscale) using LMStudio or **Ollama (02/23 Update)**. It will also work with an **OpenAI API key (02/23 Update)**. 

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

See Tips, Tricks, and Known Issues at the end for important info!

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

-   Sends RSS feed items to local/api LLM endpoint
-   Prompt-driven classification, summarization, filtering
-   Works with any OpenAI-compatible `/v1/chat/completions` endpoint, designed for LMStudio

## Alerting / Signal Generation

-   Slack webhook support (for webhook setup info, go here: https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/) 
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

LLM hosted via: 
- LM Studio-hosted (recommended)
- Ollama 
- OpenAI api key
- (a few more to come soon)

------------------------------------------------------------------------

# Installation

## 1. Clone Repository (...Or Download the Zip) & Navigate to the Folder

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

    python3 ShunyaNet_Sentinel.py

Windows:

    python ShunyaNet_Sentinel.py

------------------------------------------------------------------------

# Quick Start (with LMStudio) (UPDATED 02/23)

1.  Click "Load Prompt File" and load `default_prompt.txt`
2.  Click "Load Data Source File" and Load `Default_test` RSS list
3.  Open "Additional Settings" and enter:
        *  LLM_PROVIDER = lmstudio
        *  LLM_BASE_URL = <YOUR LMSTUDIO SERVER IP, e.g.: http://x.x.x.x:port/v1>
        *  LLM_MODEL = <I recommend you leave this blank. Just follow Step 4>
        *  API_KEY = <leave blank, unless you set one for your server>
4. Load model in LM Studio. Turn on "Serve on Local Network" in Server Settings. Tips: 
        * In the "Load Model"/model selection window, turn on "Manually choose model load parameters"
        * In your model's load parameters window, Set Max Concurrent Predictions to 1 (for now)
        * In the inference tab, turn off or minimize thinking, if that setting is available.
        * A model of moderate size that works well: gpt-oss-20b. Thinking set to "low"
11. Click the cat! (...or just hit Fetch / Send)

------------------------------------------------------------------------

# Full Instructions & Config (UPDATED 02/23)

1. **Enter topics of interest**, or load one of the default lists provided. Up to 10 topics may be added to each list.

3. **Click “Load Prompt File” to load a prompt file.** A default prompt is provided (`default_prompt.txt`).
   1. You’re encouraged to tweak and revise this prompt - it may substantially improve the quality of reporting. There is A LOT of room for customization here.
   2. There is a 'default_prompt-always-reply.txt' included. This prompt requires the LLM choose one RSS feed item to report back on, even if no topics are triggered. You can use this to debug/test the LLM's analysis.

4. **Click “Load Data Source File” to load an RSS list.** Two default lists are provided. A short “Default_test” list and a longer “Default_long” list, which focuses on world-wide news and breaking news.
   1. Tailoring your own lists to your region or topics of interest will significantly affect the output of information. An example region-focused list that I used for a recent trip is provided ('India_regional_example-v1.txt').
   2. Reddit and blue-sky can be easily converted into RSS feeds. Programs, such as RSSBridge, can also generate RSS feeds from websites that don’t have one.
   2. The “Default_test” list is a short list of a variety of RSS feeds. The purpose is to keep the first RSS pull quick and short, so that you can diagnose whether all the pieces are working the way they should.

5. **In "Additional Settings", set the following fields.** These will save and persist if you end and restart the program. The default settings will work with most configurations - but you must still enter field #1 yourself:

| Setting | Description | Default | Example / Notes |
|----------|---------------------------|----------|------------------|
| **LLM_PROVIDER** | SEE NEXT STEP | — | — |
| **LLM_BASE_URL** | SEE NEXT STEP | — | — |
| **LLM_MODEL** | SEE NEXT STEP | — | — |
| **LLM_API_KEY** | SEE NEXT STEP | — | — |
| **SLACK_WEBHOOK_URL** | Optional Slack webhook URL for sending alerts to Slack. | Optional | `https://hooks.slack.com/services/...` |
| **MAX_INPUT_TOKENS** | Maximum tokens sent to the LLM per RSS pull. Rule of thumb: **1 token ≈ 4 characters**. If exceeding model context size, enable chunked mode. | 4000 | Increase carefully depending on your LLM's context window. |
| **MAX_OUTPUT_TOKENS** | Maximum tokens the LLM will send back in its reply. (note: this is the max/cap, not the target!) | 4000 | Recommend you do not change. 4000 is probaly too much breathing room, to be honest |
| **MAX_INPUT_TOKENS_BULK** | Maximum tokens length of RSS feeds sent to the LLM for bulk processing reports. When bulk processing is enabled, RSS feeds are saved and sent together with a special trend-analysis prompt (hard-coded prompt, for now). Chunks are never used for this, so dont exceed your context limit | 4000 | Likely needs to be increased for meaningful bulk reports. May stress VRAM and context limits. Recommended you disable bulk mode initially. |
| **MAX_OUTPUT_TOKENS_BULK** | Maximum tokens length of the bulk processing report itself. | 4000 | May need to be increased for meaningful bulk reports. May stress VRAM and context limits. Recommended to disable bulk mode initially. |
| **FETCH_INTERVAL** | Time in seconds between RSS pulls and LLM analysis. | 600 (seconds, i.e. 10 min) | Do **not** set lower than total processing time or backlog may occur. |
| **ITEMS_PER_FEED** | Maximum number of RSS entries pulled per feed per cycle. Previously pulled items are ignored. | 50 | Higher values create a larger first pull. Most RSS feeds do not produce much more than 20 new items every 10 minutes, some much less. |
| **USE_CHUNKED_MODE** | Enables automatic splitting of RSS content if it exceeds token allowance. `1 = On`, `0 = Off`. | 1 | Prevents context overflow but may duplicate event reporting across chunks. |
| **CHUNK_SIZE** | Size of each chunk in **characters** (not tokens). | 8000 | Approximate conversion: **4 characters ≈ 1 token**. I REPEAT: THIS IS IN **CHARACTERS**. Should it be in tokens? Probably! But it's not.|
| **WRITE_TO_FILE** | Optional. Writes all pulled RSS content to a rolling file for external benchmarking, prompt testing, or model comparison. Does **not** affect core Sentinel functionality. `1 = On`, `0 = Off`. | 0 | Useful for offline LLM testing and evaluation. |
| **ANALYSIS_WINDOW** | Time interval used for each bulk processing report. | 3600 (seconds, i.e. 1h) | Used only when Bulk Processing is enabled. |
| **BULK_ANALYSIS** | Enables periodic bulk RSS trend reports. `1 = On`, `0 = Off`. Very experimental relative to the routine reporting. The special prompt is hard-coded, for now.| 0 | Sends accumulated RSS feeds to the LLM for a single trend analysis report. May increase processing load significantly. |

6. **Load your model (e.g., in LMStudio, Ollama) or acquire your API Key (e.g., for OpenAI)** of choice and be sure to set its context window to comfortably exceed the value you enter in the TOKENS_INPUT fields of ShunyaNet Sentinel. The program is currently designed to be compatible with LMStudio, Ollama, and OpenAI (via API Key).
Then, go to "Additional Settings" in ShunyaNet Sentinel and fill in the following fields as appropriate for your LLM solution:

##### LMSTUDIO
- `LLM_PROVIDER = lmstudio`
- `LLM_BASE_URL = <your LMStudio server URL, e.g.: http://localhost:1234/v1>`
- `LLM_MODEL = <OPTIONAL. I recommend leaving this blank and loading your model in LMStudio directly. If you must, then the format is like: lmstudio-community/mistral-7b-instruct>`
- `LLM_API_KEY = <Leave this blank, unless you use an authentical key>`

##### OLLAMA
- `LLM_PROVIDER = ollama`
- `LLM_BASE_URL = <Your LMStudio server URL, e.g.: http://localhost:11434/v1>`
- `LLM_MODEL = llama3`
- `LLM_API_KEY = = <leave this empty/blank>`

##### OPENAI (via API KEY)
- `LLM_PROVIDER = openai`
- `LLM_BASE_URL = (doesn't matter: this field is ignored)`
- `LLM_MODEL = gpt-4o-mini`
- `LLM_API_KEY = sk-xxxx`

7. **Done! - Now click the cat!** (...or hit "Fetch / Send", or just wait the number of seconds you set in FETCH_INTERVAL)

**NOTE:** I recommend you keep it simple for the first run. Use the default settings & make sure it works. Then, tweak context & RSS feeds. Then, adjust the prompt. I’d be curious to see folks’ improved prompts….

------------------------------------------------------------------------

# Tips, Tricks, and Known Issues

Issues/Warnings: 
-   Sentinel spontaneously quits on Windows 11 after a few hours. No idea why. No errors thrown. Doesn’t have this problem on Mac or Linux - and maybe your system will be different! If you encounter this issue and must use Windows, then I recommend you set up a virtual box with Linux/ubuntu (or suggest a better solution, if you have one!). Note, this program will run fine on a raspberry pi!
-   Poor configuration choices can overload your LLM or hardware. Make sure your LLM can reliably process and reply faster than your RSS pull interval.
-   LMStudio recently implemented the ability ("parallel") for an LLM to accept and process more than one prompt at once. Turn this off until you have tested it with this app, or else you can get stuck with time-out errors and a backlog of ever-continuous prompt processing.  
-   An error message is sometimes thrown when the app is closed on MacOS, maybe also Linux. It doesn’t seem to affect how the program operates or its stability, though. I fixed this once, but it came back. I will fix it again …at some point.
-   I had to remove a few urls from the default list because they were slow (e.g., FEMA's...) or seemingly broken now (e.g., NRC's...).

Suggestions:
-   The first fetch / send is sort-of a stress test: it pulls the maximum feed volume for your settings and thus is likely to contain stale information. However, this is a good way to test whether everything is working, to understand your longest prompt processing time, and to get a sample of how your prompt, rss list, and topic list will perform.
-   Some "thinking" models produce malformed replies or get stuck in loops. I recommend turning thinking off first. Thinking set to "low" works fine for GPT OSS.
-   There are all sorts of tricks to broadcast feeds that don’t have RSS by default (e.g., look into RSSBridge). Also, some social sites can be converted into RSS feeds automatically (e.g., adding .rss to a reddit URL, or /RSS to a bluesky profile URL.)
-   Some sites, reddit specifically, will rate-limit your RSS pulls. To minimize this issue, I strongly recommend you *randomize the order of your RSS url list* so that you do not hit the same site too fast.

------------------------------------------------------------------------

# Status

Alpha

------------------------------------------------------------------------

# License

This project is licensed under the **Apache 2.0**.

Attribution: ShunyaNet / EverythingsComputer

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
