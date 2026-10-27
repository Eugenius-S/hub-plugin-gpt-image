"""The gpt-image plugin script on a fake codex: files in the folder, arguments, never overwrites old files, the failure
reason, messages in two languages (en by default, ru on request).

    python3 -m unittest discover -s tests
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "plugins", "gpt-image", "bin", "gpt-image")

# a fake codex: writes its args to a file and puts PNGs into the -C folder, as many as asked (n from the prompt)
FAKE = """#!/usr/bin/env python3
import json, os, re, sys, time
a = sys.argv[1:]
h = os.environ.get("CODEX_HOME")
call = {"args": a, "stdin": sys.stdin.read(), "home": h, "link": os.path.islink(os.path.join(h, "auth.json")),
        "agents": os.path.exists(os.path.join(h, "AGENTS.md"))}
with open(os.environ["FAKE_LOG"], "w") as f:
    json.dump(call, f)
with open(os.environ["FAKE_LOG"] + ".all", "a") as f:   # all runs, one line each
    f.write(json.dumps(call) + "\\n")
seen = 1                                   # run number - atomically, the runs go at once
while True:
    try:
        os.close(os.open(os.environ["FAKE_LOG"] + f".n{seen}", os.O_CREAT | os.O_EXCL))
        break
    except FileExistsError:
        seen += 1
if os.environ.get("FAKE_SLEEP"):
    time.sleep(float(os.environ["FAKE_SLEEP"]))
# the first N runs hit the subscription limit (or all from the N-th on)
if seen <= int(os.environ.get("FAKE_LIMIT_FIRST", 0)) or seen >= int(os.environ.get("FAKE_LIMIT_FROM", 10 ** 6)):
    sys.stderr.write("ERROR: You've hit your usage limit. Try again later.\\n")
    sys.exit(1)
if os.environ.get("FAKE_REFRESH"):        # codex refreshed the login: wrote auth.json anew (the link became a file)
    h = os.environ["CODEX_HOME"]
    os.remove(os.path.join(h, "auth.json"))
    open(os.path.join(h, "auth.json"), "w").write("fresh")
m = a[a.index("-m") + 1] if "-m" in a else None
if m and m in os.environ.get("FAKE_UNSUPPORTED", "").split(","):   # the model is not for this login (ChatGPT login, Oct 16 Windows)
    sys.stderr.write("ERROR: The " + repr(m) + " model is not supported when using Codex with a ChatGPT account.\\n")
    sys.exit(1)
cd = a[a.index("-C") + 1]
prompt = call["stdin"] if a[-1] == "-" else a[-1]
names = ["out.png"]                       # the prompt names no file: the model only draws, the script collects
if os.environ.get("FAKE_SAY"):            # codex got to the end but made no picture - said why
    open(a[a.index("-o") + 1], "w").write(os.environ["FAKE_SAY"])
    sys.exit(0)
if os.environ.get("FAKE_FAIL"):
    sys.stderr.write("[2026-10-07T12:00:00] codex\\nthinking...\\n" + os.environ["FAKE_FAIL"] + "\\ntokens used\\n1 234\\n")
    sys.exit(1)
gen = os.path.join(h, "generated_images", f"t{seen}")   # the image tool puts the result into its own home
os.makedirs(gen, exist_ok=True)
for k, n in enumerate(names):
    mine = b"\\x89PNG\\r\\n\\x1a\\n" + f"run{seen}:{n}".encode()
    if not os.environ.get("FAKE_NOGEN"):
        open(os.path.join(gen, f"ig_{k}.png"), "wb").write(mine)
    # and the model copies into the folder whatever it finds: a foreign "fresh PNG", a half-written file
    took = {"STEAL": b"\\x89PNG foreign", "EMPTY": b""}.get(os.environ.get("FAKE_COPY", ""), mine)
    open(os.path.join(cd, n), "wb").write(took)
if os.environ.get("FAKE_WATCH"):          # what is visible in the --out folder while codex is still running
    open(os.environ["FAKE_LOG"] + ".watch", "w").write(json.dumps(sorted(os.listdir(os.environ["FAKE_WATCH"]))))
open(a[a.index("-o") + 1], "w").write("ok")
"""


class Script(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gpt-image-")
        self.codex = os.path.join(self.tmp, "codex")
        with open(self.codex, "w", encoding="utf-8") as f:
            f.write(FAKE)
        os.chmod(self.codex, os.stat(self.codex).st_mode | stat.S_IEXEC)
        if os.name == "nt":   # like the real codex on Windows - a .cmd wrapper: cmd.exe cuts an argument at a line break
            with open(self.codex + ".cmd", "w") as f:
                f.write(f'@"{sys.executable}" "{self.codex}" %*\r\n')
            self.codex += ".cmd"
        self.log = os.path.join(self.tmp, "log.json")
        self.out = os.path.join(self.tmp, "out")
        self.ref = os.path.join(self.tmp, "ref.png")
        open(self.ref, "wb").write(b"\x89PNG")
        self.home = os.path.join(self.tmp, "codex-home")   # the owner's Codex login and his shared rules
        os.makedirs(self.home)
        open(os.path.join(self.home, "auth.json"), "w").write("login")
        open(os.path.join(self.home, "AGENTS.md"), "w").write("Rule: require skills")

    def base_env(self, extra=None):
        """Environment of the script: a temp hub state (never the live hub), the language set by the test (en unless
        the test says otherwise); a None value removes the variable."""
        env = {**os.environ, "GPT_IMAGE_CODEX": self.codex, "FAKE_LOG": self.log, "CODEX_HOME": self.home,
               "GPT_IMAGE_HOME": os.path.join(self.tmp, "iso"), "PYTHONIOENCODING": "utf-8", "GPT_IMAGE_STAGGER": "0",
               "GPT_IMAGE_PAUSE": "0", "HUB_STATE_DIR": os.path.join(self.tmp, "hub-state"),
               "HUB_CONFIG": os.path.join(self.tmp, "hub.json"), "HUB_LANG": "en", **(extra or {})}
        return {k: v for k, v in env.items() if v is not None}

    def run_script(self, *args, fail=False, say=None, env=None):
        env = self.base_env({**(env or {}),
                             **({"FAKE_FAIL": fail if isinstance(fail, str) else "ERROR: boom"} if fail else {}),
                             **({"FAKE_SAY": say} if say else {})})
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)

    def test_images_land_in_the_folder_and_the_result_lists_them(self):
        r = self.run_script("--out", self.out, "-i", self.ref, "--n", "2", "--name", "apple",
                            "glossy red apple on white")
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual([os.path.basename(p) for p in res["files"]], ["apple-1.png", "apple-2.png"])
        self.assertTrue(all(os.path.exists(p) for p in res["files"]))
        call = json.load(open(self.log))
        a = call["args"]
        self.assertEqual(a[0], "exec")
        self.assertTrue(a[a.index("-C") + 1].startswith(call["home"]))      # the model writes into the run home, not the folder
        self.assertEqual(a[a.index("-i") + 1], os.path.abspath(self.ref))   # the reference goes to codex as an image
        self.assertEqual(a[-2:], ["--", "-"])                                # -i does not swallow "-"; the prompt comes from stdin
        self.assertIn("glossy red apple on white", call["stdin"])            # the prompt is as the session gave it
        self.assertNotIn("Save the results", call["stdin"])                  # the model only draws: saving, copying and looking
        self.assertIn("do not read skills", call["stdin"])                   # at the result were ~170k tokens a frame, not ~46k
        self.assertEqual(a[a.index("-m") + 1], "gpt-6-luna")                 # the model is set, not codex's choice
        self.assertIn("model_reasoning_effort=low", a)                       # the least that gives a picture

    def test_a_multiline_prompt_reaches_codex_whole(self):
        """Oct 16, Windows: codex is the codex.cmd wrapper, cmd.exe cut the prompt argument at the first line break
        ("no prompt appeared after the colon"). The prompt goes through stdin whole."""
        r = self.run_script("--out", self.out, "first line\nsecond line: glossy apple")
        self.assertEqual(r.returncode, 0, r.stderr)
        call = json.load(open(self.log))
        self.assertIn("first line\nsecond line: glossy apple", call["stdin"].replace("\r\n", "\n"))
        self.assertNotIn("glossy apple", " ".join(call["args"]))

    def models_of(self):
        return [json.loads(x)["args"][json.loads(x)["args"].index("-m") + 1] for x in open(self.log + ".all")]

    def test_a_model_the_login_does_not_support_falls_back_to_the_next_light_one(self):
        """Oct 16 and 21: "The 'gpt-6-luna' model is not supported when using Codex with a ChatGPT account". The
        owner's config.toml holds the heavy gpt-6.1-sol with effort high - it was taken as the fallback and ate the
        ChatGPT limit (and the login refused it too). Now only the light models, never the owner's one."""
        open(os.path.join(self.home, "config.toml"), "w").write('model = "own-model"\nmodel_reasoning_effort = "high"\n')
        r = self.run_script("--out", self.out, "apple", env={"FAKE_UNSUPPORTED": "gpt-6-luna"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.models_of(), ["gpt-6-luna", "gpt-5.6-luna"])
        a = json.load(open(self.log))["args"]
        self.assertIn("model_reasoning_effort=low", a)

    def test_a_refused_model_is_remembered_and_costs_no_run_next_time(self):
        """The first picture learns that the login refuses gpt-6-luna; the next ones go straight to the next model -
        no spare codex run (each one starts a session and bills the limit)."""
        env = {"FAKE_UNSUPPORTED": "gpt-6-luna"}
        self.assertEqual(self.run_script("--out", self.out, "apple", env=env).returncode, 0)
        os.remove(self.log + ".all")
        self.assertEqual(self.run_script("--out", self.out, "apple", env=env).returncode, 0)
        self.assertEqual(self.models_of(), ["gpt-5.6-luna"])

    def test_no_light_model_accepted_is_an_error_not_a_heavy_fallback(self):
        """Every light model refused: a clear error; terra, sol and the owner's model are never tried."""
        open(os.path.join(self.home, "config.toml"), "w").write('model = "gpt-6.1-sol"\n')
        r = self.run_script("--out", self.out, "apple", env={"FAKE_UNSUPPORTED": "gpt-6-luna,gpt-5.6-luna"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.models_of(), ["gpt-6-luna", "gpt-5.6-luna"])
        self.assertIn("no light model is accepted", r.stderr + r.stdout)

    def test_the_model_of_each_run_is_printed(self):
        r = self.run_script("--out", self.out, "apple")
        self.assertIn("model gpt-6-luna, effort low", r.stderr)

    def test_which_model_shows_the_choice_without_a_run(self):
        env = {"FAKE_UNSUPPORTED": "gpt-6-luna"}
        r = self.run_script("--which-model")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("gpt-6-luna, gpt-5.6-luna, effort low", r.stdout)
        self.assertFalse(os.path.exists(self.log))
        self.run_script("--out", self.out, "apple", env=env)
        self.assertIn("next picture: gpt-5.6-luna, effort low; refused on this machine: gpt-6-luna",
                      self.run_script("--which-model").stdout)

    def test_the_memory_of_a_refusal_expires(self):
        env = {"FAKE_UNSUPPORTED": "gpt-6-luna", "GPT_IMAGE_UNSUPPORTED_TTL": "0"}
        self.run_script("--out", self.out, "apple", env=env)
        os.remove(self.log + ".all")
        self.run_script("--out", self.out, "apple", env=env)
        self.assertEqual(self.models_of(), ["gpt-6-luna", "gpt-5.6-luna"])   # the login may have got it by now

    def test_a_model_set_explicitly_is_used_as_is(self):
        """GPT_IMAGE_MODEL: your own model or a list - in that order, nothing else, a refusal is not remembered."""
        r = self.run_script("--out", self.out, "apple", env={"FAKE_UNSUPPORTED": "set-model", "GPT_IMAGE_MODEL": "set-model"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.models_of(), ["set-model"])
        os.remove(self.log + ".all")
        r = self.run_script("--out", self.out, "apple", env={"FAKE_UNSUPPORTED": "set-model", "GPT_IMAGE_MODEL": "set-model, other"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.models_of(), ["set-model", "other"])
        os.remove(self.log + ".all")
        self.run_script("--out", self.out, "apple", env={"GPT_IMAGE_EFFORT": "medium"})
        self.assertIn("model_reasoning_effort=medium", json.load(open(self.log))["args"])   # effort - your own too

    @unittest.skipIf(os.name == "nt", "links on Windows need administrator rights; there codex runs with its own home")
    def test_codex_runs_without_the_owners_agents_md_but_with_his_login(self):
        """Oct 9: codex refused to save the picture - the shared ~/.codex/AGENTS.md demanded skills. Generation runs in
        its own CODEX_HOME: the login is a link to the owner's auth.json, no rules. A refreshed login goes to the owner."""
        r = self.run_script("--out", self.out, "apple", env={"FAKE_REFRESH": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        call = json.load(open(self.log))
        self.assertNotEqual(os.path.realpath(call["home"]), os.path.realpath(self.home))
        self.assertFalse(call["agents"])
        self.assertTrue(call["link"])
        self.assertEqual(open(os.path.join(self.home, "auth.json")).read(), "fresh")

    def test_each_run_has_its_own_home_and_it_is_cleaned_up(self):
        # Oct 14: the codex home was shared - with generations from two sessions at once codex took the freshest PNG
        # from the shared generated_images, and a foreign picture landed in the folder (a basket instead of handles)
        r = self.run_script("--out", self.out, "--n", "3", "x", env={"FAKE_SLEEP": "0.5"})
        self.assertEqual(r.returncode, 0, r.stderr)
        homes = {c["home"] for c in self.calls()}
        self.assertEqual(len(homes), 3)
        self.assertFalse(any(os.path.exists(h) for h in homes))           # cleaned up after itself
        self.assertTrue(all(c["link"] for c in self.calls()))

    def test_the_image_in_the_folder_is_the_one_this_run_made(self):
        # Oct 13 (4 threads): the model copied the picture into the folder - a "fresh PNG" from wherever it found one: a
        # foreign picture (handles instead of sparks) or a file still being written (0 bytes). The script puts it - from
        # this run's home, whole
        for copy in ("STEAL", "EMPTY"):
            r = self.run_script("--out", self.out, "--n", "2", "--name", copy.lower(), "x", env={"FAKE_COPY": copy})
            self.assertEqual(r.returncode, 0, r.stderr)
            for p in json.loads(r.stdout.strip().splitlines()[-1])["files"]:
                got = open(p, "rb").read()
                self.assertTrue(got.startswith(b"\x89PNG\r\n\x1a\nrun"), (copy, got))
        self.assertFalse([f for f in os.listdir(self.out) if f.endswith(".part")])
        # no own picture but the model put something - do not hand out a foreign one
        r = self.run_script("--out", self.out, "--name", "none", "x", env={"FAKE_NOGEN": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, "none-1.png")))

    def test_the_folder_never_shows_the_models_copy_while_codex_runs(self):
        # Oct 16 (5 runs): the model copied a "fresh PNG" into the --out folder from wherever it found one - a foreign
        # one (a suitcase instead of a magnet); the script replaced it with its own only at the end, and the session had
        # time to preview the foreign one. The model writes into the run home; the folder has only this run's picture,
        # whole
        r = self.run_script("--out", self.out, "--name", "magnet", "x",
                            env={"FAKE_COPY": "STEAL", "FAKE_WATCH": self.out})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("magnet-1.png", json.load(open(self.log + ".watch")))
        got = open(os.path.join(self.out, "magnet-1.png"), "rb").read()
        self.assertTrue(got.startswith(b"\x89PNG\r\n\x1a\nrun"), got)

    def fresh(self):
        for f in os.listdir(self.tmp):
            if f.startswith("log.json."):
                os.remove(os.path.join(self.tmp, f))

    def calls(self):
        with open(self.log + ".all") as f:
            return [json.loads(x) for x in f]

    def test_several_images_are_made_at_once(self):
        # a frame takes 1-3.5 min, a batch of 10 one by one took 25 min - generate in parallel
        t = time.time()
        r = self.run_script("--out", self.out, "--n", "3", "--name", "f", "x", env={"FAKE_SLEEP": "1.5"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.time() - t, 3.5)                               # three of 1.5 s - at once, not 4.5 s
        self.assertEqual(len(self.calls()), 3)                               # one codex run - one picture
        self.assertEqual(len(json.loads(r.stdout.strip().splitlines()[-1])["files"]), 3)
        t = time.time()
        self.run_script("--out", self.out, "--n", "2", "--jobs", "1", "x", env={"FAKE_SLEEP": "1"})
        self.assertGreaterEqual(time.time() - t, 2)                          # --jobs 1 - one by one

    def test_a_batch_of_frames_each_with_its_prompt_and_references(self):
        os.makedirs(self.out)
        frames = os.path.join(self.tmp, "frames")
        os.makedirs(frames)
        open(os.path.join(frames, "f2.txt"), "w").write("frame two: rocket lifts off")
        batch = os.path.join(frames, "batch.json")
        json.dump([{"prompt": "frame one: rocket on pad", "name": "f01", "images": [self.ref]},
                   {"prompt_file": "f2.txt", "name": "f02", "size": "1536x1024"}], open(batch, "w"))
        r = self.run_script("--out", self.out, "--batch", batch)
        self.assertEqual(r.returncode, 0, r.stderr)
        files = json.loads(r.stdout.strip().splitlines()[-1])["files"]
        self.assertEqual([os.path.basename(p) for p in files], ["f01-1.png", "f02-1.png"])   # the order is as in the list
        by = {("f01-1.png" if "rocket on pad" in c["stdin"] else "f02-1.png"): c for c in self.calls()}
        self.assertIn("rocket on pad", by["f01-1.png"]["stdin"])
        self.assertIn("-i", by["f01-1.png"]["args"])
        self.assertIn("rocket lifts off", by["f02-1.png"]["stdin"])             # prompt_file is relative to the list's folder
        self.assertNotIn("-i", by["f02-1.png"]["args"])
        self.assertIn("Size: 1536x1024", by["f02-1.png"]["stdin"])

    def test_on_the_subscription_limit_the_rest_go_one_by_one(self):
        # two of three hit the limit - retried one by one, all three are ready
        r = self.run_script("--out", self.out, "--n", "3", "x", env={"FAKE_LIMIT_FIRST": "2"})
        self.assertEqual(r.returncode, 0, r.stderr)
        res = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(len(res["files"]), 3)
        self.assertNotIn("failed", res)
        self.assertEqual(len(self.calls()), 5)

    def test_a_lasting_limit_stops_and_says_so(self):
        r = self.run_script("--out", self.out, "--n", "3", "x", fail="ERROR: You've hit your usage limit. Try again at 13:40.")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage limit", r.stderr)
        self.assertEqual(len(self.calls()), 6)                               # three at once and one probe each in 3 retries
        self.assertEqual(r.stderr.count("GPT limit: retry"), 3)
        self.fresh()
        r = self.run_script("--out", self.out, "--n", "3", "--name", "p", "x", env={"FAKE_LIMIT_FROM": "2"})
        res = json.loads(r.stdout.strip().splitlines()[-1])                 # some came out - the result with the unfinished ones
        self.assertEqual(len(res["files"]), 1)
        self.assertEqual(len(res["failed"]), 2)                              # which ones - as the runs diverged
        self.assertIn("usage limit", res["failed"][1]["reason"])
        self.assertEqual(len(self.calls()), 6)                               # limit from the 2nd run: three at once, probes one by one x3

    def test_one_job_retries_on_the_limit_too(self):
        # Oct 15: the GPT limit on simultaneous generations - a retry with --jobs 1 and with a single picture too; a progress line before the pause
        r = self.run_script("--out", self.out, "--jobs", "1", "x", env={"FAKE_LIMIT_FIRST": "2"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(r.stdout.strip().splitlines()[-1])["files"]), 1)
        self.assertEqual(len(self.calls()), 3)
        self.assertIn("GPT limit: retry 1/3 in 0 s", r.stderr)
        self.assertIn("GPT limit: retry 2/3", r.stderr)
        self.assertNotIn("retry 3/3", r.stderr)

    def test_tries_and_pause_come_from_the_environment(self):
        r = self.run_script("--out", self.out, "--jobs", "1", "x", env={"GPT_IMAGE_TRIES": "1", "FAKE_LIMIT_FROM": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("retry 1/1", r.stderr)
        self.fresh()
        t = time.time()
        r = self.run_script("--out", self.out, "x", env={"GPT_IMAGE_PAUSE": "120", "GPT_IMAGE_TRIES": "0", "FAKE_LIMIT_FROM": "1"})
        self.assertLess(time.time() - t, 10)                                  # 0 retries - no pauses
        self.assertEqual(len(self.calls()), 1)

    def test_existing_files_are_not_overwritten(self):
        os.makedirs(self.out)
        open(os.path.join(self.out, "apple-1.png"), "wb").write(b"old")
        r = self.run_script("--out", self.out, "--name", "apple", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["files"], [os.path.join(self.out, "apple-2.png")])
        self.assertEqual(open(os.path.join(self.out, "apple-1.png"), "rb").read(), b"old")

    def test_no_images_is_an_error(self):
        r = self.run_script("--out", self.out, "x", fail=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no pictures were made", r.stderr)

    def test_the_reason_from_codex_is_shown(self):
        # Oct 7: three runs in a row for a designer - "no pictures were made" with no reason
        r = self.run_script("--out", self.out, "x", fail="ERROR: You've hit your usage limit. Try again at 13:40.")
        self.assertIn("usage limit", r.stderr)
        self.assertNotIn("tokens used", r.stderr)
        r = self.run_script("--out", self.out, "x", say="I can't create that image: it violates the content policy.")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("content policy", r.stderr)

    def test_a_second_run_into_a_busy_folder_stops_at_once(self):
        # Oct 15: four copies of one batch into one folder - each 4 times slower; the second run does not start
        first = subprocess.Popen([sys.executable, SCRIPT, "--out", self.out, "x"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=self.base_env({"FAKE_SLEEP": "5"}))
        try:
            lock = os.path.join(self.out, ".gpt-image.lock")
            end = time.time() + 10
            while not os.path.exists(self.log + ".all") and time.time() < end:   # the first is already in codex
                time.sleep(0.05)
            r = self.run_script("--out", self.out, "x")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(f"a generation is already running in {self.out} (pid {first.pid} since ", r.stderr)
            self.assertIn("wait for it or stop it", r.stderr)
            self.assertEqual(len(self.calls()), 1)                          # the second codex was not called
        finally:
            first.wait(timeout=30)
        self.assertFalse(os.path.exists(lock))                              # the first finished - the lock is gone
        r = self.run_script("--out", self.out, "x")                         # and the folder is free again
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_dead_lock_is_taken_over(self):
        os.makedirs(self.out)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        open(os.path.join(self.out, ".gpt-image.lock"), "w").write(f"{dead.pid} 1\n")
        r = self.run_script("--out", self.out, "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.out, ".gpt-image.lock")))
        open(os.path.join(self.out, ".gpt-image.lock"), "w").write("garbage")   # a broken lock does not hold either
        r = self.run_script("--out", self.out, "x")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_lock_is_released_after_a_failure(self):
        r = self.run_script("--out", self.out, "x", fail=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, ".gpt-image.lock")))

    def test_each_ready_image_prints_a_progress_line(self):
        r = self.run_script("--out", self.out, "--n", "3", "--name", "p", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [x for x in r.stderr.splitlines() if x.startswith("done ")]
        self.assertEqual(len(lines), 3)
        self.assertEqual(sorted(x.split(":")[0] for x in lines), ["done 1/3", "done 2/3", "done 3/3"])
        self.assertEqual(sorted(x.split(": ")[1] for x in lines), ["p-1.png", "p-2.png", "p-3.png"])
        self.fresh()
        r = self.run_script("--out", self.out, "--jobs", "1", "x", env={"FAKE_LIMIT_FIRST": "1"})   # after a retry too
        self.assertIn("done 1/1: image-1.png", r.stderr)

    def test_one_image_is_limited_to_seven_minutes(self):
        self.assertEqual(subprocess.run([sys.executable, "-c", f"import runpy;print(runpy.run_path({SCRIPT!r}, "
                                         "run_name='x')['TIMEOUT'])"], capture_output=True, text=True).stdout.strip(),
                         "420")
        r = self.run_script("--out", self.out, "x", env={"GPT_IMAGE_TIMEOUT": "1", "FAKE_SLEEP": "20"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not finish in", r.stderr)

    def busy(self, k, pid, who="gen-x", started=None):
        d = os.path.join(self.tmp, "iso", "busy")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"slot-{k}.json"), "w") as f:
            json.dump({"pid": pid, "who": who, "started": started or time.time(), "out": "/x"}, f)
        return os.path.join(d, f"slot-{k}.json")

    def test_status_says_free_or_busy_until_and_by_whom(self):
        # sessions idled not knowing whether GPT is free - one command for all
        r = self.run_script("--status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("GPT free: 4 of 4 slots", r.stdout)
        for k in range(2):
            self.busy(k, os.getpid(), "gen-b")
        r = self.run_script("--status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("2 of 4 slots (busy: gen-b)", r.stdout)
        r = self.run_script("--status", env={"GPT_IMAGE_SLOTS": "2"})
        self.assertEqual(r.returncode, 2)
        self.assertRegex(r.stdout, r"GPT busy until ~\d\d:\d\d:\d\d: 2 of 2 slots - gen-b")

    def test_a_run_waits_while_gpt_is_busy_for_everyone(self):
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(holder.kill)
        slot = self.busy(0, holder.pid)
        env = {"GPT_IMAGE_SLOTS": "1", "GPT_IMAGE_POLL": "0.2"}
        p = subprocess.Popen([sys.executable, SCRIPT, "--out", self.out, "x"], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             env=self.base_env(env))
        time.sleep(1.5)
        self.assertIsNone(p.poll())                                          # waits for a slot, codex is not started
        self.assertFalse(os.path.exists(self.log))
        os.remove(slot)                                                      # the slot was freed
        out, err = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("waiting for GPT: GPT busy", err)
        self.assertEqual(len(json.loads(out.strip().splitlines()[-1])["files"]), 1)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "iso", "busy")), [])   # gave its own slot back

    def test_a_dead_runs_place_is_freed(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.busy(0, dead.pid)
        r = self.run_script("--out", self.out, "x", env={"GPT_IMAGE_SLOTS": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("waiting for GPT", r.stderr)

    def test_the_limit_pauses_gpt_for_everyone(self):
        r = self.run_script("--out", self.out, "x", env={"GPT_IMAGE_PAUSE": "60", "GPT_IMAGE_TRIES": "0",
                                                         "FAKE_LIMIT_FROM": "1", "HUB_SESSION": "gen-a"})
        self.assertNotEqual(r.returncode, 0)
        r = self.run_script("--status")
        self.assertEqual(r.returncode, 2)
        self.assertRegex(r.stdout, r"GPT busy until \d\d:\d\d:\d\d: subscription limit \(hit by gen-a\)")

    def test_messages_are_english_by_default_and_russian_on_request(self):
        """Two languages: the hub language decides. Without hub code on PYTHONPATH and without HUB_LANG - English;
        HUB_LANG=ru (or the hub language through core.i18n) - the original Russian texts."""
        none = {"HUB_LANG": None, "PYTHONPATH": None}          # no language information at all
        r = self.run_script("--status", env=none)
        self.assertIn("GPT free: 4 of 4 slots", r.stdout)
        r = self.run_script("--status", env={**none, "HUB_LANG": "ru"})   # the fallback: the variable alone
        self.assertIn("GPT свободен: 4 из 4 мест", r.stdout)
        r = self.run_script("--status", env={**none, "HUB_LANG": "ru-RU"})
        self.assertIn("GPT свободен: 4 из 4 мест", r.stdout)
        r = self.run_script("--status", env={**none, "HUB_LANG": "uk"})   # a language with no table - English
        self.assertIn("GPT free: 4 of 4 slots", r.stdout)

    def test_russian_texts(self):
        ru = {"HUB_LANG": "ru"}
        r = self.run_script("--out", self.out, "--n", "2", "--name", "p", "x", env=ru)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(x.split(":")[0] for x in r.stderr.splitlines() if x.startswith("готово ")),
                         ["готово 1/2", "готово 2/2"])
        self.fresh()   # the fake codex counts its runs per log
        r = self.run_script("--out", self.out, "--jobs", "1", "x", env={**ru, "FAKE_LIMIT_FIRST": "1"})
        self.assertIn("лимит GPT: повтор 1/3 через 0 с", r.stderr)
        r = self.run_script("--out", self.out, "x", fail=True, env=ru)
        self.assertIn("картинок не получилось", r.stderr)
        r = self.run_script("--out", self.out, "x", env={**ru, "GPT_IMAGE_TIMEOUT": "1", "FAKE_SLEEP": "20"})
        self.assertIn("codex не уложился в 1 с", r.stderr)
        r = self.run_script("--out", self.out, "-i", os.path.join(self.tmp, "nope.png"), "x", env=ru)
        self.assertIn("нет референса", r.stderr)
        r = self.run_script("--out", self.out, "  ", env=ru)
        self.assertIn("нужен промпт", r.stderr)
        self.busy(0, os.getpid(), "gen-a")
        r = self.run_script("--status", env=ru)
        self.assertIn("3 из 4 мест (заняты: gen-a)", r.stdout)

    def test_the_hub_language_is_taken_from_core_i18n(self):
        """With the hub code on PYTHONPATH the language is the hub's (core.i18n.current()), as in process plugins."""
        core = os.path.join(self.tmp, "hubcode", "core")
        os.makedirs(core)
        open(os.path.join(core, "__init__.py"), "w").close()
        with open(os.path.join(core, "i18n.py"), "w") as f:
            f.write("def current():\n    return 'ru'\n")
        r = self.run_script("--status", env={"HUB_LANG": None, "PYTHONPATH": os.path.dirname(core)})
        self.assertIn("GPT свободен: 4 из 4 мест", r.stdout)

    def test_missing_reference_fails_fast(self):
        r = self.run_script("--out", self.out, "-i", os.path.join(self.tmp, "nope.png"), "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.log))   # codex was not called


if __name__ == "__main__":
    unittest.main()
