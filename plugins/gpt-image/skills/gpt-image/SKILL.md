---
name: gpt-image
description: Generate or edit a picture with the GPT Image model (through Codex CLI on a ChatGPT subscription) and save it to a folder. Use when you need a finished picture, not just a prompt: an illustration from a description, a variation from a reference, an image edit.
---

# GPT Image

GPT Image makes the pictures through Codex CLI: prompt, references and a folder - the files land in the folder.
Pictures and references go to OpenAI on the owner's subscription.

Generation takes minutes - run it **in the background** (Bash with `run_in_background`), the result arrives by itself:

```
python3 "${CLAUDE_PLUGIN_ROOT}/bin/gpt-image" --out <folder> [-i ref1.png -i ref2.png] [--n 1-4] [--name name] \
  [--size 1024x1024] "<prompt>"
```

- A long prompt goes into a file and `--prompt-file <file>`. The prompt reaches the model as is: write it out in full
  yourself (the `image` skill, if you have it, tells how to write GPT Image prompts).
- `-i` - references (up to 16): say in the prompt what to take from each.
- A picture takes 1-3 minutes, so **several pictures - one run, they go in parallel** (3 at once): `--n 4` - variants
  of one prompt; frames, a series, a batch with own prompts - `--batch`, not one run per frame in turn:

  ```
  python3 "${CLAUDE_PLUGIN_ROOT}/bin/gpt-image" --out <folder> --batch <folder>/frames.json
  ```
  `frames.json` - `[{"prompt_file": "f01.txt", "name": "f01", "images": ["ref.png"]}, {"prompt": "...", "name": "f02"}]`
  (also `size`, `n`; paths are relative to the file's folder). A batch over 12 - in parts of 12. `--jobs 1-4` - how many at
  once, 3 by default.
- ChatGPT subscription limit: if hit, the script retries the failed ones one by one, up to 3 times after 2 min (the
  output has lines "GPT limit: retry i/3 in 2 min"); still failing - the result has `failed` with the reason (the time
  the limit lifts is in it too). Do not restart at once: tell the hub in one line.
- One generation per `--out` folder: a second run into the same folder while the first is alive exits at once
  ("a generation is already running in ... (pid N ...)"). Do not rerun "just in case" and do not run the same batch
  from the background and from a script: wait for the first or stop it. A picture takes at most 7 min, otherwise it goes
  to `failed`; progress - lines "done i/N: file" in stderr.
- **GPT is shared by all sessions.** Whether it is free - `python3 "${CLAUDE_PLUGIN_ROOT}/bin/gpt-image" --status`:
  "GPT free: k of N slots" (exit code 0) or "GPT busy until HH:MM:SS: by whom" (exit code 2). If you are waiting for the
  owner (a blank to approve, a choice) and GPT is free - do not idle: start the next frames that need no answer in the
  background at once. If busy - the run waits for a slot itself (line "waiting for GPT: ..."), the limit pauses everyone.
- The script's lines (progress, retries, errors, `--status`) come in the hub language, English or Russian; the
  English wording is quoted here.
- Files - `<name>-1.png`, `<name>-2.png`...; taken numbers are skipped, old files are never overwritten.
- The result is the last output line, JSON `{"files": [...]}`, the ones that failed - `"failed": [{"name", "reason"}]`.
  Error "codex not found" or a login problem - tell the hub in one line.
- Look at every picture you get yourself (Read the file) before handing it over: is it the right object and style from
  the prompt, is it not empty. Not it - regenerate this frame; wrong twice - say it did not work out, do not hand it
  over as done.
- Show the result to the owner: `hub send-file <file> "<what is in it>"`.
