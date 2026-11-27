# Methodology recording script (about 9 minutes)

Read this while you screen-record. Speak in English. Show the repository, not a stock pitch.
Do not open `.env`, do not show API keys, and do not read investment conclusions.
The video itself is still yours to record. This file is only the script.

Aim for 8 to 10 minutes. A comfortable pace is about 130 words per minute.

## 0:00–0:45 Framework

"This recording explains the research framework, not a buy or sell view.
The course asks for source code, prompts, two or three company reports of at most ten A4 pages, and this short methodology video.
Our pipeline has four stages: Data, Analysis, Writer, and Reviewer.
An orchestrator only sequences those stages. It does not recalculate the figures.
The companies wired for this delivery are Microsoft, Cisco, and Apple."

Show `integration/README.md` and the four folders under `integration/member_a` and `integration/member_b`.

## 0:45–2:30 Data: what is fetched, and what is not

"Data receives a ticker, an as-of date, and a timezone.
The as-of date becomes 23:59:59 in that timezone. Later stages filter on that cutoff.
Python tools, not the model, request market prices and SEC filings.
Yahoo prices are kept only when the exchange close is at or before the cutoff.
SEC 10-K, 10-Q, and, when the feed actually contains them, 8-K filings are labeled as SEC documents.
An 8-K is not relabeled as a Seeking Alpha article or as an earnings-call transcript.
Seeking Alpha is not scraped, and we do not bypass a login.
A transcript or Seeking Alpha article is used only when I place the real file and a manifest under imports, for example imports/MSFT.
If those files are absent, coverage stays not_checked. The run does not invent a document."

Show `imports/README.md`. Do not open a private download if it contains account details.

## 2:30–4:15 Analysis: Python numbers, then the model

"Analysis always computes ratios in Python from the DataBundle.
Growth, margins, and valuation keep their formulas and input ids.
The model is not allowed to invent a new number.
On a live run it may call only allowlisted tools: list the slots Python prepared, search evidence already stored in the bundle, and submit sentences.
A sentence is kept only when it copies the display string Python calculated.
If the model adds another number, or never submits, the stage fails.
It does not silently fall back to a template and call that a model analysis.
The offline command is different. It is marked synthetic_templates. It is a demo of the wiring, not a live model run."

Show `integration/llm_author.py` function names on screen, not a secret.

## 4:15–6:00 Writer: chart, prose, and the ten-page PDF

"Writer turns the accepted sentences into a short report.
When calculated metrics exist, it writes one SVG chart and a caption.
Every display string on that chart is also a numeric mention in the claim manifest, so Reviewer can recheck it.
If nothing has been calculated, the chart list stays empty and the warning says so.
Writer then exports an A4 PDF and records pdf_path and page_count.
Those fields are no longer left null after a successful write.
If the PDF is longer than ten pages, or the declared count disagrees with the file, the stage fails.
Reviewer does not claim the page count was checked when no PDF exists."

Open one offline `report.md` only if you label it on camera as a synthetic demo.

## 6:00–7:30 Reviewer and the rework limit

"Reviewer checks citations, periods, the cutoff, formulas, and the PDF page count.
Its decision is pass, revise, or manual_review.
That decision is separate from whether the program status is success, partial, or failed.
A revise result names the earliest stage to rerun. Dependent stages run again.
After the first pass, at most two rework rounds are allowed.
If the report still needs revision, the orchestrator stops at manual_review.
Partial means something is missing, such as volatility we refuse to publish because the checker cannot recompute a logarithm. It is not a hidden pass."

## 7:30–8:45 How I will produce the course pack

"From the repo root I can run the offline pack to see the file layout:
python -m integration.pack --offline
That writes course_pack/offline_demo and a README that says the numbers are synthetic.
The official reports require a live run on my machine:
copy .env.example to .env, fill the DeepSeek key and the SEC user agent locally, then
python -m integration.pack --live
I do not commit .env.
I then open each PDF, confirm it is ten A4 pages or fewer, and compare the printed numbers with the analysis JSON."

Do not type the key on camera. Show the empty placeholder names in `.env.example` only.

## 8:45–9:30 What this video is not

"I am not giving an investment conclusion.
Accuracy here means the sentence matches a calculated display and a stored source.
Coverage means we record not_checked when a transcript or Seeking Alpha file was not supplied.
The offline demo is not evidence that Yahoo, SEC, or DeepSeek were called.
The recording stops here."
