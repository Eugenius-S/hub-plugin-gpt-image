# GPT Image - an Agents Hub plugin

Picture generation through Codex CLI on your ChatGPT subscription: a prompt, up to 16 references and a folder - the
pictures land right in the project. No API key and no separate payment; it works in the background and on a failure
shows the reason (limit, moderation, network). **Risk:** prompts and references go to OpenAI.

This is a set of skills for hub sessions: after installing, a `gpt-image@...` tool appears in the catalog and the hub
hands it to sessions. A session runs the script in the background:

```
python3 plugins/gpt-image/bin/gpt-image --out <folder> [-i ref.png ...] [--n 1-4] [--name name] [--size 1024x1024] "<prompt>"
```

The result is JSON `{"files": [...]}`; files `<name>-1.png`, `<name>-2.png`..., taken names are never overwritten.

Several pictures go in parallel, 3 at once (`--jobs 1-4`): `--n 4` - variants of one prompt, `--batch file.json` - a
batch with own prompts, references and names (`[{"prompt" | "prompt_file", "name", "images", "size", "n"}]`).
Hit the ChatGPT limit - the failed ones are retried one by one, up to 3 times after 2 min (with `--jobs 1` too), the line
"GPT limit: retry i/3 in 2 min" goes to stderr; still failing - into `"failed"` with the error text.

Protection from duplicates: one generation per `--out` folder - a second run into the same folder while the first is
alive exits at once with the error "a generation is already running (pid N since HH:MM)" (the lock is the file
`.gpt-image.lock`, the lock of a dead run is removed). One picture takes at most 7 min, otherwise it goes to `"failed"`.
Every finished one is a line "done i/N: file" in stderr.

GPT is one for all sessions: every run takes a slot (4 by default, `GPT_IMAGE_SLOTS`), hitting the subscription limit
pauses everyone. No free slot - the run waits its turn instead of hammering the limit. `gpt-image --status` prints
"GPT free: k of N slots" or "GPT busy until HH:MM:SS: by whom": if free - the session sends the next frame at once,
not idling.

The models are the lightest (luna class) ones, in order: `gpt-6-luna`, `gpt-5.6-luna`, reasoning effort `low`
(the picture is drawn by the image tool, not by thinking). If the ChatGPT login does not accept a model ("model is not
supported"), the script remembers that on this machine for 12 h (`GPT_IMAGE_UNSUPPORTED_TTL`, s) - the next pictures do
not spend a Codex run on it - and takes the next one; none accepted - an error, not a heavier model. Every run prints
its model and effort to stderr; `gpt-image --which-model` shows the choice without a run. It never falls back to the model from `~/.codex/config.toml`: that
is usually a heavy one and eats the ChatGPT limit. `GPT_IMAGE_MODEL` - your own model or a comma-separated list, taken as
is, in that order; `GPT_IMAGE_EFFORT` - your own effort. The prompt
reaches Codex through stdin: on Windows a multi-line prompt arrives whole.

Thin Codex run: besides the picture Codex adds its own system prompt, sub-agent, sleep and question tools, the owner's
skills, project docs and web search to every request (a text run "Reply with OK" took 19105 input tokens). The script
swaps the system prompt for a one-line instruction file and turns the extras off (`agents`, `web_search`, skills,
`features.multi_agent|apps|plugins|hooks|goals|memories|...`) - the same text run takes 4611 tokens. The image
generation tool and the shell it is called through stay. `GPT_IMAGE_LEAN=0` brings the full wrapper back.

Language: the script's messages (progress lines, errors, `--status`) are English by default and Russian when the hub
language is Russian - taken from the hub (`core.i18n`) when the hub code is on `PYTHONPATH`, otherwise from the
`HUB_LANG` environment variable.

## Requirements

- Codex CLI signed in with ChatGPT: `codex login` (Codex from ChatGPT.app works too), the `image_generation` feature
  enabled (`codex features list`).
- Codex picks up the proxy from the environment (`HTTPS_PROXY`) itself.

## Install

Settings -> Plugins -> Available -> gpt-image.

Tests: `python3 -m unittest discover -s tests`. Code - MIT, the icon - the OpenAI logo (Simple Icons, CC0).

Icon — the ChatGPT (OpenAI) logo.
