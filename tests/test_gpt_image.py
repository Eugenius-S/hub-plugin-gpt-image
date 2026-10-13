"""Скрипт плагина gpt-image на подставном codex: файлы в папке, аргументы, не затирает старое, причина сбоя.

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

# подставной codex: пишет args в файл и кладёт PNG в папку -C, сколько попросили (n из промпта)
FAKE = """#!/usr/bin/env python3
import json, os, re, sys, time
a = sys.argv[1:]
call = {"args": a, "stdin": sys.stdin.read(), "home": os.environ.get("CODEX_HOME")}
with open(os.environ["FAKE_LOG"], "w") as f:
    json.dump(call, f)
with open(os.environ["FAKE_LOG"] + ".all", "a") as f:   # все запуски, по строке
    f.write(json.dumps(call) + "\\n")
seen = 1                                   # номер запуска — атомарно, запуски идут разом
while True:
    try:
        os.close(os.open(os.environ["FAKE_LOG"] + f".n{seen}", os.O_CREAT | os.O_EXCL))
        break
    except FileExistsError:
        seen += 1
if os.environ.get("FAKE_SLEEP"):
    time.sleep(float(os.environ["FAKE_SLEEP"]))
# первые N запусков упираются в лимит подписки (или все начиная с N-го)
if seen <= int(os.environ.get("FAKE_LIMIT_FIRST", 0)) or seen >= int(os.environ.get("FAKE_LIMIT_FROM", 10 ** 6)):
    sys.stderr.write("ERROR: You've hit your usage limit. Try again later.\\n")
    sys.exit(1)
if os.environ.get("FAKE_REFRESH"):        # codex обновил вход: записал auth.json заново (ссылка стала файлом)
    h = os.environ["CODEX_HOME"]
    os.remove(os.path.join(h, "auth.json"))
    open(os.path.join(h, "auth.json"), "w").write("fresh")
cd = a[a.index("-C") + 1]
prompt = a[-1]
names = re.findall(r"`([\\w.-]+\\.png)`", prompt)
if os.environ.get("FAKE_SAY"):            # codex дошёл до конца, но картинку не сделал — сказал почему
    open(a[a.index("-o") + 1], "w").write(os.environ["FAKE_SAY"])
    sys.exit(0)
if os.environ.get("FAKE_FAIL"):
    sys.stderr.write("[2026-10-07T12:00:00] codex\\nthinking...\\n" + os.environ["FAKE_FAIL"] + "\\ntokens used\\n1 234\\n")
    sys.exit(1)
for n in names:
    open(os.path.join(cd, n), "wb").write(b"\\x89PNG\\r\\n\\x1a\\n")
open(a[a.index("-o") + 1], "w").write("ok")
"""


class Script(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gpt-image-")
        self.codex = os.path.join(self.tmp, "codex")
        with open(self.codex, "w") as f:
            f.write(FAKE)
        os.chmod(self.codex, os.stat(self.codex).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, "log.json")
        self.out = os.path.join(self.tmp, "out")
        self.ref = os.path.join(self.tmp, "ref.png")
        open(self.ref, "wb").write(b"\x89PNG")
        self.home = os.path.join(self.tmp, "codex-home")   # вход владельца в Codex и его общие правила
        os.makedirs(self.home)
        open(os.path.join(self.home, "auth.json"), "w").write("login")
        open(os.path.join(self.home, "AGENTS.md"), "w").write("Foundation: require skills")

    def run_script(self, *args, fail=False, say=None, env=None):
        env = {**os.environ, "GPT_IMAGE_CODEX": self.codex, "FAKE_LOG": self.log, "CODEX_HOME": self.home,
               "GPT_IMAGE_HOME": os.path.join(self.tmp, "iso"), "GPT_IMAGE_STAGGER": "0", "GPT_IMAGE_PAUSE": "0",
               **(env or {}),
               **({"FAKE_FAIL": fail if isinstance(fail, str) else "ERROR: boom"} if fail else {}),
               **({"FAKE_SAY": say} if say else {})}
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env, timeout=30)

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
        self.assertEqual(a[a.index("-C") + 1], self.out)
        self.assertEqual(a[a.index("-i") + 1], os.path.abspath(self.ref))   # референс — картинкой в codex
        self.assertIn("glossy red apple on white", a[-1])                    # промпт — как дала сессия
        self.assertEqual(a[-2], "--")                                        # -i не съедает промпт
        self.assertEqual(call["stdin"], "")                                  # stdin закрыт: codex не ждёт EOF
        self.assertEqual(a[a.index("-m") + 1], "gpt-6-luna")                 # модель задана, не на выбор codex
        self.assertIn("model_reasoning_effort=medium", a)

    @unittest.skipIf(os.name == "nt", "ссылки на Windows — с правами администратора; там codex идёт со своим домом")
    def test_codex_runs_without_the_owners_agents_md_but_with_his_login(self):
        """09.10: codex отказывался сохранять картинку — общий ~/.codex/AGENTS.md требовал скиллы Foundation. Генерация
        идёт в своём CODEX_HOME: вход — ссылкой на auth.json владельца, правил нет. Обновил вход — уходит владельцу."""
        r = self.run_script("--out", self.out, "apple", env={"FAKE_REFRESH": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        home = json.load(open(self.log))["home"]
        self.assertNotEqual(os.path.realpath(home), os.path.realpath(self.home))
        self.assertFalse(os.path.exists(os.path.join(home, "AGENTS.md")))
        self.assertTrue(os.path.islink(os.path.join(home, "auth.json")))
        self.assertEqual(open(os.path.join(self.home, "auth.json")).read(), "fresh")
        r = self.run_script("--out", self.out, "apple")
        self.assertEqual(open(os.path.join(home, "auth.json")).read(), "fresh")

    def fresh(self):
        for f in os.listdir(self.tmp):
            if f.startswith("log.json."):
                os.remove(os.path.join(self.tmp, f))

    def calls(self):
        with open(self.log + ".all") as f:
            return [json.loads(x) for x in f]

    def test_several_images_are_made_at_once(self):
        # владелец 13.10 (q93848): кадр 1–3.5 мин, пачка из 10 шла по одному 25 мин — генерить параллельно
        t = time.time()
        r = self.run_script("--out", self.out, "--n", "3", "--name", "f", "x", env={"FAKE_SLEEP": "1.5"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.time() - t, 3.5)                               # три по 1.5 с — разом, не 4.5 с
        self.assertEqual(len(self.calls()), 3)                               # запуск codex — картинка
        self.assertEqual(len(json.loads(r.stdout.strip().splitlines()[-1])["files"]), 3)
        t = time.time()
        self.run_script("--out", self.out, "--n", "2", "--jobs", "1", "x", env={"FAKE_SLEEP": "1"})
        self.assertGreaterEqual(time.time() - t, 2)                          # --jobs 1 — по одной

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
        self.assertEqual([os.path.basename(p) for p in files], ["f01-1.png", "f02-1.png"])   # порядок — как в списке
        by = {c["args"][-1].split("`")[1]: c["args"] for c in self.calls()}
        self.assertIn("rocket on pad", by["f01-1.png"][-1])
        self.assertIn("-i", by["f01-1.png"])
        self.assertIn("rocket lifts off", by["f02-1.png"][-1])                  # prompt_file — от папки списка
        self.assertNotIn("-i", by["f02-1.png"])
        self.assertIn("Size: 1536x1024", by["f02-1.png"][-1])

    def test_on_the_subscription_limit_the_rest_go_one_by_one(self):
        # два из трёх упёрлись в лимит — повтор по одной, все три готовы
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
        self.assertEqual(len(self.calls()), 4)                               # три разом и одна проба, не ещё две
        self.fresh()
        r = self.run_script("--out", self.out, "--n", "3", "--name", "p", "x", env={"FAKE_LIMIT_FROM": "2"})
        res = json.loads(r.stdout.strip().splitlines()[-1])                 # часть вышла — итог с недоделанным
        self.assertEqual(len(res["files"]), 1)
        self.assertEqual(len(res["failed"]), 2)                              # какие — как разошлись запуски
        self.assertIn("usage limit", res["failed"][1]["reason"])
        self.assertEqual(len(self.calls()), 4)

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
        self.assertIn("не получилось", r.stderr)

    def test_the_reason_from_codex_is_shown(self):
        # 07.10: у дизайнера три запуска подряд — «картинок не получилось» без причины
        r = self.run_script("--out", self.out, "x", fail="ERROR: You've hit your usage limit. Try again at 13:40.")
        self.assertIn("usage limit", r.stderr)
        self.assertNotIn("tokens used", r.stderr)
        r = self.run_script("--out", self.out, "x", say="I can't create that image: it violates the content policy.")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("content policy", r.stderr)

    def test_missing_reference_fails_fast(self):
        r = self.run_script("--out", self.out, "-i", os.path.join(self.tmp, "nope.png"), "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.log))   # codex не звали


if __name__ == "__main__":
    unittest.main()
