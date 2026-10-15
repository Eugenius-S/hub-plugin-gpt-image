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
h = os.environ.get("CODEX_HOME")
call = {"args": a, "stdin": sys.stdin.read(), "home": h, "link": os.path.islink(os.path.join(h, "auth.json")),
        "agents": os.path.exists(os.path.join(h, "AGENTS.md"))}
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
gen = os.path.join(h, "generated_images", f"t{seen}")   # инструмент картинок кладёт результат в свой дом
os.makedirs(gen, exist_ok=True)
for k, n in enumerate(names):
    mine = b"\\x89PNG\\r\\n\\x1a\\n" + f"run{seen}:{n}".encode()
    if not os.environ.get("FAKE_NOGEN"):
        open(os.path.join(gen, f"ig_{k}.png"), "wb").write(mine)
    # а в папку копирует модель — чем найдёт: чужой «свежий PNG», недописанный файл
    took = {"STEAL": b"\\x89PNG foreign", "EMPTY": b""}.get(os.environ.get("FAKE_COPY", ""), mine)
    open(os.path.join(cd, n), "wb").write(took)
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
        call = json.load(open(self.log))
        self.assertNotEqual(os.path.realpath(call["home"]), os.path.realpath(self.home))
        self.assertFalse(call["agents"])
        self.assertTrue(call["link"])
        self.assertEqual(open(os.path.join(self.home, "auth.json")).read(), "fresh")

    def test_each_run_has_its_own_home_and_it_is_cleaned_up(self):
        # 14.10 (generated-styles): дом codex был общий — при генерациях из двух сессий разом codex брал самый свежий
        # PNG из общей generated_images, и в папку ложилась чужая картинка (корзина вместо ручек)
        r = self.run_script("--out", self.out, "--n", "3", "x", env={"FAKE_SLEEP": "0.5"})
        self.assertEqual(r.returncode, 0, r.stderr)
        homes = {c["home"] for c in self.calls()}
        self.assertEqual(len(homes), 3)
        self.assertFalse(any(os.path.exists(h) for h in homes))           # за собой убрал
        self.assertTrue(all(c["link"] for c in self.calls()))

    def test_the_image_in_the_folder_is_the_one_this_run_made(self):
        # 13.10 (gen-y2k, 4 потока): в папку картинку копировала модель — «свежий PNG» где нашла: чужая картинка (ручки
        # вместо искр) или файл, который ещё пишется (0 байт). Кладёт скрипт — из дома этого запуска, целиком
        for copy in ("STEAL", "EMPTY"):
            r = self.run_script("--out", self.out, "--n", "2", "--name", copy.lower(), "x", env={"FAKE_COPY": copy})
            self.assertEqual(r.returncode, 0, r.stderr)
            for p in json.loads(r.stdout.strip().splitlines()[-1])["files"]:
                got = open(p, "rb").read()
                self.assertTrue(got.startswith(b"\x89PNG\r\n\x1a\nrun"), (copy, got))
                self.assertTrue(got.endswith(os.path.basename(p).encode()), (copy, got))
        self.assertFalse([f for f in os.listdir(self.out) if f.endswith(".part")])
        # своей картинки нет, а модель что-то положила — не отдавать чужое
        r = self.run_script("--out", self.out, "--name", "none", "x", env={"FAKE_NOGEN": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, "none-1.png")))

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
        self.assertEqual(len(self.calls()), 6)                               # три разом и по одной пробе в 3 повторах
        self.assertEqual(r.stderr.count("лимит GPT: повтор"), 3)
        self.fresh()
        r = self.run_script("--out", self.out, "--n", "3", "--name", "p", "x", env={"FAKE_LIMIT_FROM": "2"})
        res = json.loads(r.stdout.strip().splitlines()[-1])                 # часть вышла — итог с недоделанным
        self.assertEqual(len(res["files"]), 1)
        self.assertEqual(len(res["failed"]), 2)                              # какие — как разошлись запуски
        self.assertIn("usage limit", res["failed"][1]["reason"])
        self.assertEqual(len(self.calls()), 6)                               # лимит с 2-го запуска: три разом, пробы по одной ×3

    def test_one_job_retries_on_the_limit_too(self):
        # 15.10: лимит GPT на одновременные генерации — повтор и при --jobs 1, и при одной картинке; строка хода перед паузой
        r = self.run_script("--out", self.out, "--jobs", "1", "x", env={"FAKE_LIMIT_FIRST": "2"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(r.stdout.strip().splitlines()[-1])["files"]), 1)
        self.assertEqual(len(self.calls()), 3)
        self.assertIn("лимит GPT: повтор 1/3 через 0 с", r.stderr)
        self.assertIn("лимит GPT: повтор 2/3", r.stderr)
        self.assertNotIn("повтор 3/3", r.stderr)

    def test_tries_and_pause_come_from_the_environment(self):
        r = self.run_script("--out", self.out, "--jobs", "1", "x", env={"GPT_IMAGE_TRIES": "1", "FAKE_LIMIT_FROM": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("повтор 1/1", r.stderr)
        self.fresh()
        t = time.time()
        r = self.run_script("--out", self.out, "x", env={"GPT_IMAGE_PAUSE": "120", "GPT_IMAGE_TRIES": "0", "FAKE_LIMIT_FROM": "1"})
        self.assertLess(time.time() - t, 10)                                  # 0 повторов — без пауз
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
        self.assertIn("не получилось", r.stderr)

    def test_the_reason_from_codex_is_shown(self):
        # 07.10: у дизайнера три запуска подряд — «картинок не получилось» без причины
        r = self.run_script("--out", self.out, "x", fail="ERROR: You've hit your usage limit. Try again at 13:40.")
        self.assertIn("usage limit", r.stderr)
        self.assertNotIn("tokens used", r.stderr)
        r = self.run_script("--out", self.out, "x", say="I can't create that image: it violates the content policy.")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("content policy", r.stderr)

    def test_a_second_run_into_a_busy_folder_stops_at_once(self):
        # 15.10: четыре копии одной пачки в одну папку — каждая в 4 раза дольше; второй запуск не стартует
        first = subprocess.Popen([sys.executable, SCRIPT, "--out", self.out, "x"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env={**os.environ, "GPT_IMAGE_CODEX": self.codex,
                                 "FAKE_LOG": self.log, "CODEX_HOME": self.home, "FAKE_SLEEP": "5",
                                 "GPT_IMAGE_HOME": os.path.join(self.tmp, "iso")})
        try:
            lock = os.path.join(self.out, ".gpt-image.lock")
            end = time.time() + 10
            while not os.path.exists(self.log + ".all") and time.time() < end:   # первый уже в codex
                time.sleep(0.05)
            r = self.run_script("--out", self.out, "x")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(f"уже идёт генерация (pid {first.pid} с ", r.stderr)
            self.assertIn("дождись её или останови", r.stderr)
            self.assertEqual(len(self.calls()), 1)                          # второй codex не звали
        finally:
            first.wait(timeout=30)
        self.assertFalse(os.path.exists(lock))                              # первый закончил — замок снят
        r = self.run_script("--out", self.out, "x")                         # и папка снова свободна
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_dead_lock_is_taken_over(self):
        os.makedirs(self.out)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        open(os.path.join(self.out, ".gpt-image.lock"), "w").write(f"{dead.pid} 1\n")
        r = self.run_script("--out", self.out, "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.out, ".gpt-image.lock")))
        open(os.path.join(self.out, ".gpt-image.lock"), "w").write("мусор")   # битый замок — тоже не держит
        r = self.run_script("--out", self.out, "x")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_lock_is_released_after_a_failure(self):
        r = self.run_script("--out", self.out, "x", fail=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, ".gpt-image.lock")))

    def test_each_ready_image_prints_a_progress_line(self):
        r = self.run_script("--out", self.out, "--n", "3", "--name", "p", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [x for x in r.stderr.splitlines() if x.startswith("готово ")]
        self.assertEqual(len(lines), 3)
        self.assertEqual(sorted(x.split(":")[0] for x in lines), ["готово 1/3", "готово 2/3", "готово 3/3"])
        self.assertEqual(sorted(x.split(": ")[1] for x in lines), ["p-1.png", "p-2.png", "p-3.png"])
        self.fresh()
        r = self.run_script("--out", self.out, "--jobs", "1", "x", env={"FAKE_LIMIT_FIRST": "1"})   # после повтора тоже
        self.assertIn("готово 1/1: image-1.png", r.stderr)

    def test_one_image_is_limited_to_seven_minutes(self):
        self.assertEqual(subprocess.run([sys.executable, "-c", f"import runpy;print(runpy.run_path({SCRIPT!r}, "
                                         "run_name='x')['TIMEOUT'])"], capture_output=True, text=True).stdout.strip(),
                         "420")
        r = self.run_script("--out", self.out, "x", env={"GPT_IMAGE_TIMEOUT": "1", "FAKE_SLEEP": "20"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("не уложился", r.stderr)

    def busy(self, k, pid, who="gen-x", started=None):
        d = os.path.join(self.tmp, "iso", "busy")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"slot-{k}.json"), "w") as f:
            json.dump({"pid": pid, "who": who, "started": started or time.time(), "out": "/x"}, f)
        return os.path.join(d, f"slot-{k}.json")

    def test_status_says_free_or_busy_until_and_by_whom(self):
        # владелец 15.10: сессии простаивали, не зная, свободен ли GPT — одна команда для всех
        r = self.run_script("--status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("GPT свободен: 4 из 4 мест", r.stdout)
        for k in range(2):
            self.busy(k, os.getpid(), "gen-y2k")
        r = self.run_script("--status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("2 из 4 мест (заняты: gen-y2k)", r.stdout)
        r = self.run_script("--status", env={"GPT_IMAGE_SLOTS": "2"})
        self.assertEqual(r.returncode, 2)
        self.assertRegex(r.stdout, r"GPT занят до ~\d\d:\d\d:\d\d: 2 из 2 мест — gen-y2k")

    def test_a_run_waits_while_gpt_is_busy_for_everyone(self):
        holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(holder.kill)
        slot = self.busy(0, holder.pid)
        env = {"GPT_IMAGE_SLOTS": "1", "GPT_IMAGE_POLL": "0.2"}
        p = subprocess.Popen([sys.executable, SCRIPT, "--out", self.out, "x"], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True,
                             env={**os.environ, "GPT_IMAGE_CODEX": self.codex, "FAKE_LOG": self.log,
                                  "CODEX_HOME": self.home, "GPT_IMAGE_HOME": os.path.join(self.tmp, "iso"),
                                  "GPT_IMAGE_STAGGER": "0", "GPT_IMAGE_PAUSE": "0", **env})
        time.sleep(1.5)
        self.assertIsNone(p.poll())                                          # ждёт места, codex не запущен
        self.assertFalse(os.path.exists(self.log))
        os.remove(slot)                                                      # место освободилось
        out, err = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("жду GPT: GPT занят", err)
        self.assertEqual(len(json.loads(out.strip().splitlines()[-1])["files"]), 1)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "iso", "busy")), [])   # своё место отдал

    def test_a_dead_runs_place_is_freed(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.busy(0, dead.pid)
        r = self.run_script("--out", self.out, "x", env={"GPT_IMAGE_SLOTS": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("жду GPT", r.stderr)

    def test_the_limit_pauses_gpt_for_everyone(self):
        r = self.run_script("--out", self.out, "x", env={"GPT_IMAGE_PAUSE": "60", "GPT_IMAGE_TRIES": "0",
                                                         "FAKE_LIMIT_FROM": "1", "HUB_SESSION": "gen-a"})
        self.assertNotEqual(r.returncode, 0)
        r = self.run_script("--status")
        self.assertEqual(r.returncode, 2)
        self.assertRegex(r.stdout, r"GPT занят до \d\d:\d\d:\d\d: лимит подписки \(упёрся gen-a\)")

    def test_missing_reference_fails_fast(self):
        r = self.run_script("--out", self.out, "-i", os.path.join(self.tmp, "nope.png"), "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.log))   # codex не звали


if __name__ == "__main__":
    unittest.main()
