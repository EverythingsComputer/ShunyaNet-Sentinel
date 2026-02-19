# ShunyaNet Sentinel

ShunyaNet Sentinel is a lightweight monitoring and analysis program
designed to ingest RSS feeds at regular intervals and bundle them for
analysis by a Large Language Model (LLM) for summarization or alerting.

The project is built to utilize an LLM hosted using LM Studio, which can
be served:

-   On the same machine\
-   On the same local network\
-   Over the internet using your solution of choice (reverse proxy, VPS,
    Tailscale, etc.)

For alerting, the program utilizes Slack Webhooks, enabling push
notifications to be sent to a mobile device running Slack.

The program is compatible with: 
- Linux\
- macOS\
- Windows\*

\*Strongly suggest macOS or Linux. See Known Issues below.

------------------------------------------------------------------------

# High-Level Workflow

1.  User enters topics of interest\
2.  User provides a list of RSS feeds\
3.  Sentinel pulls RSS feeds at user-configured intervals of time\
4.  RSS content is sent to the user-provided LLM server\
5.  The LLM reviews feeds and reports back to Sentinel on relevant topics of interest\
6.  If Slack webhook is configured, alerts are forwarded\
7.  Optional bulk analysis identifies trends over time

------------------------------------------------------------------------

# Core Capabilities

## RSS Feed Monitoring

-   Periodic polling of RSS feeds\
-   Deduplication and timestamp tracking\
-   Handles slow or malformed feeds

## LLM-Based Analysis (Optional)

-   Sends feed items to local LLM endpoint\
-   Prompt-driven classification, summarization, filtering\
-   Works with any OpenAI-compatible `/v1/chat/completions` endpoint

## Alerting / Signal Generation

-   Slack webhook support\
-   Structured output suitable for automation\
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

Additional: - LM Studio-hosted LLM (recommended) - Or any
OpenAI-compatible `/v1/chat/completions` endpoint

------------------------------------------------------------------------

# Installation

## 1. Clone Repository

    git clone https://github.com/yourusername/ShunyaNet-Sentinel.git
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
4.  Load model in LM Studio. Turn on "Serve on Local Network" in Server Serttings
5.  Click the cat! (...or just hit Fetch / Send)


------------------------------------------------------------------------

# Full Instructions & Config

1. Enter topics of interest, or load one of the default lists provided. Up to 10 topics may be added to each list.
2. Click “Load Prompt File” to load a prompt file. A default prompt is provided (`default_prompt.txt`).
   1. You’re encouraged to tweak and revise this prompt - it may substantially improve the quality of reporting. There is A LOT of room for customization here.
   2. There is a 'default_prompt-always-reply.txt' included. This prompt requires the LLM choose one news item to report back on, even if no topics are triggered. You can use this to debug/test the LLM's analysis.
3. Click “Load Data Source File” to load an RSS list. Two default lists are provided. A short “Default_test” list and a longer “Default_long” list, which focuses on world-wide news and breaking news.
   1. Tailoring your own lists to your region or topics of interest will significantly affect the output of information. An example region-focused list that I used for a recent trip is provided ('India_regional_example-v1.txt').
   2. Reddit and blue-sky can be easily converted into RSS feeds. Programs, such as RSSBridge, can also generate RSS feeds from websites that don’t have one.
   2. The “Default_test” list is a short list of a variety of RSS feeds. The purpose is to keep the first RSS pull quick and short, so that you can diagnose whether all the pieces are working the way they should.
4. In settings, set the following fields. These will save and persist if you end and restart the program. The default settings will work with most configurations - but you must still enter field #1 yourself:
   1. **LLM URL:** Copy your LM-Studio server URL here. Make sure `/v1` is included at the end. Examples:
      1. For local, LAN, or tailscale lan: `http://x.x.x.x:<port>/v1/chat/completions`
      2. For tailscale https, use https: `https://ca***a.tail2a*****.ts.net/v1/chat/completions`
   2. **SLACK_WEBHOOK_URL:** Copy your slack Webhook URL here. This is optional: `https://hooks.slack.com/services/........`
   3. **MAX_TOKENS:** Max tokens worth of characters that will be sent to the LLM for analysis with each pull. As a rule of thumb, 1 token = 4 characters. If you would like to send MORE tokens than your LLM context can accept, then make sure Batch Processing is active (see below). Default is 4000 tokens.
   4. **MAX_TOKENS_BULK:** Max tokens for a bulk processing report. If bulk processing is turned on (see #6), the program will save all RSS feeds and then, at regular intervals (see #7), send them in bulk [TBC: confirm if batch works here] to the LLM with a unique prompt. This prompt will instruct the LLM to look at ALL the RSS feeds, look for trends, and then send back a single report for that period.
      1. Default tokens is 4000, but if you use this feature, you will probably want to increase that. 4000 is generally safe for most LLM configurations, though.
      2. This feature works, although it is more finnicky because you are likely to stress LLM context size limits, VRAM, and processing time. When getting to first use this tool, I recommend turning this feature off.
   5. **FETCH_INTERVAL:** Enter the number of seconds in-between each RSS feed pull / LLM analysis. The default is 600s (10 minutes).
      1. NOTE: setting the interval to a time LESS than the time it takes for the program to pull all RSS feeds, send them to the LLM, and for the LLM to send back a reply will result in a backlog (and potentially other glitches). So don’t do that.
   6. **ITEMS_PER_FEED:** This is the max number of entries that will be pulled for analysis each time the program polls an RSS feed. The default is 50, which is generally higher than you need, and will result in a particularly large first-pull. The program automatically ignores items that were already pulled! At least, I think it does…
   7. **USE_CHUNKED_MODE:** 1 is yes, 0 is no. Default is 1. If the amount of RSS text pulled is greater than your token allowance (MAX_TOKENS, see #3), the program will break it up into chunks equivalent to the number of *characters* you set in CHUNK_SIZE. Important considerations:
      1. This lets you search a huge volume of RSS feeds. However, there is a trade-off. The smaller the chunks, the more likely that your LLM will send report the same “event” more than one time, because there’s a good chance that major events will be featured in more than one chunk of RSS feeds. The larger the chunk, the longer the processing time and the greater the chance your LLM will miss something of interest by prioritizing something that is a better match for the topics it is looking for.
   8. **CHUNK_SIZE:** This is the size in characters – again, CHARACTERS, not tokens – of each chunk. See the description for USE_CHUNKED_MODE for more info.
      1. Should this have been coded in tokens? Yup. But it’s not. So, for now, just do the math. 4 characters ~ 1 token.
   9. **WRITE_TO_FILE:** Optional and does not affect use of Shunyanet Sentinel. If turned on, this will write all RSSs pulled to a rolling RSS file. You can then take that file and, outside of ShunyaNet Sentinel, use it to benchmark different LLMs ability to find the info you are looking for, or test and evaluate different prompts. 1 is on, 0 is off. Default is 0. 
   10. **ANALYSIS_WINDOW:** This is the interval of time for each bulk processing report. See MAX_TOKENS_BULK (#4) for a description of this feature.
   11. **BULK PROCESSING:** 1 is yes, 0 is no. See separate explanation in (#4 above) for this feature and important considerations. Default is 0.
   5. (In LM-Studio) load your model of choice and be sure to set its context window to comfortably exceed the value you enter in the TOKENs field of ShunyaNet Sentinel (and bulk processing tokens, if that features is active).
      1. Although this is designed/tested with LMStudio in mind, it should work with any OpenAI-compatible /v1/chat/completions endpoint.
   6. Done! - Now click “Pull / Fetch,” click the cat, or just wait the number of seconds you set in INTERVAL.

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

Specify license (MIT, Apache 2.0, GPLv3, etc.)

------------------------------------------------------------------------

# Name Origin

My cat.
