# START HERE — One Command at a Time

Rules for using this guide:

- Run **one** line at a time. Copy it, paste it, press Enter.
- After each step, compare what you see with **"You should see"**.
- If it doesn't match, follow the **"If not"** line before moving on.
- Never paste two commands joined by `&&`. If one fails you won't know which.
- `~` means your home folder, `/Users/carte`.

---

# PART A — Clean up and find your files (10 minutes)

## A1. Open a fresh terminal window

Press `Cmd + N` in Terminal.

**You should see:** a prompt ending in `%`.

## A2. Go to your home folder

```
cd ~
```

**You should see:** the prompt shows `~`.

## A3. Look for every copy of the project

```
ls -d ~/Downloads/sanctions* ~/projects/sanctions*
```

**You should see:** one or more folder paths. Errors about "No such file" for one of the two
locations are fine; it just means nothing is there.

**Write down which paths exist.** You will keep exactly one.

## A4. Check what is inside the Downloads copy

```
ls ~/Downloads/sanctions-delta-watch
```

**You should see:** names like `CLAUDE.md`, `scripts`, `data`, `requirements.txt`.

**If not:** "No such file or directory" means there is no Downloads copy. Skip to A6.

## A5. Check what is inside the projects copy

```
ls ~/projects/sanctions-delta-watch
```

**You should see:** the same kind of names.

**If not:** no projects copy exists. Skip to A6.

## A6. Decide which copy to keep

- Only one copy exists → that's the one. Note its path.
- Both exist → keep the one whose `ls` showed more files (especially `INSTRUCTIONS.md`).
- A folder named `sanctions-delta-watch` appears **inside** another one → see A7.
- Neither exists → skip to PART B, step B1.

## A7. Only if a folder is nested inside itself

Check first:

```
ls ~/projects/sanctions-delta-watch/sanctions-delta-watch
```

**If that lists files**, run these three lines, one at a time:

```
cd ~/projects/sanctions-delta-watch
```
```
mv sanctions-delta-watch/* .
```
```
rmdir sanctions-delta-watch
```

**You should see:** no output. Silence means success.

## A8. Delete the copy you are not keeping

Skip this if you only have one copy. Otherwise, adjust the path to the one you are discarding:

```
rm -rf ~/Downloads/sanctions-delta-watch
```

**You should see:** no output.

---

# PART B — Set up the project folder (15 minutes)

Do B1 and B2 **only if PART A found no copy at all**. Otherwise start at B3.

## B1. Make the projects folder

```
mkdir -p ~/projects
```

**You should see:** no output.

## B2. Unzip the starter package into it

In Finder, double-click `sanctions-delta-watch.zip` in Downloads, then drag the resulting
folder into your `projects` folder. Then continue at B3.

## B3. Go into the project folder

```
cd ~/projects/sanctions-delta-watch
```

**You should see:** the prompt ends with `sanctions-delta-watch %`.

**If not:** the folder isn't there. Go back to A3.

## B4. Confirm you are in the right place

```
pwd
```

**You should see:** `/Users/carte/projects/sanctions-delta-watch`

## B5. Confirm the files are here

```
ls
```

**You should see:** `CLAUDE.md`, `INSTRUCTIONS.md`, `README.md`, `data`, `docs`, `prompts`,
`requirements.txt`, `scripts`, `tests`, `watcher`, `copilot`.

**If `INSTRUCTIONS.md` is missing:** you have the older package. Download the newest zip and
replace the folder, then return to B3.

## B6. Check you have Python

```
python3 --version
```

**You should see:** `Python 3.12.4` or any 3.11+.

**If not:** run `brew install python@3.12`, then try again.

## B7. Delete any broken virtual environment

```
rm -rf .venv
```

**You should see:** no output. This is safe; nothing of yours lives in there.

## B8. Create a fresh virtual environment

```
python3 -m venv .venv
```

**You should see:** no output. It takes a few seconds.

## B9. Activate it

```
source .venv/bin/activate
```

**You should see:** the prompt now starts with `(.venv)`.

**If not:** run `ls .venv/bin` — if that's empty, repeat B8.

> From now on, every new terminal window needs B3 and B9 again. Nothing else.

## B10. Install the dependencies

```
pip install -r requirements.txt
```

**You should see:** several "Downloading" lines, ending in "Successfully installed…".

## B11. Verify the install

```
python -c "import requests, pandas; print('ok')"
```

**You should see:** `ok`

---

# PART C — Stage 0: prove the data source (20 minutes)

## C1. Confirm the script is the new version

```
python scripts/step0_verify_events.py --help
```

**You should see:** a usage block listing `--provider`, `--key`, `--rpc`, `--blocks`, `--probe`.

**If `--probe` is missing:** you have the old script. Download `step0_verify_events.py` again,
then run this (adjust the filename if your browser added a number):

```
cp ~/Downloads/step0_verify_events.py scripts/step0_verify_events.py
```

Then repeat C1.

## C2. Test which public endpoints serve event logs

```
python scripts/step0_verify_events.py --probe
```

**You should see:** a table of six endpoints with `eth_call` and `eth_getLogs` columns.
It takes about 30 seconds.

**Write down** any endpoint whose `eth_getLogs` column says `ok`.

## C3A. If one endpoint said `ok`

Use its URL in place of `PUT_URL_HERE`:

```
python scripts/step0_verify_events.py --provider rpc --rpc PUT_URL_HERE --blocks 2000000
```

Skip to C5.

## C3B. If every endpoint said `blocked`

You need a free Etherscan key.

1. Open etherscan.io in your browser.
2. Sign up (email and password; it's free and instant).
3. Sign in, click your profile icon, then **API Keys**.
4. Click **Add**, give it any name, and copy the key.

## C4. Run the scan with the key

Replace `PASTE_KEY_HERE` with the key you copied:

```
python scripts/step0_verify_events.py --provider etherscan --key PASTE_KEY_HERE --blocks 2000000
```

## C5. Read the result

**The first two lines must be:**

```
Doc sanctioned address -> isSanctioned = True
Doc clean address      -> isSanctioned = False
```

That proves you can read the Chainalysis oracle. If those two lines are wrong, stop and send me
the output.

**Then look at the last line:**

| Last line says | Meaning | Your design |
|---|---|---|
| `Events found: 5` (any number above 0) | The oracle announces list changes | **Design A** |
| `Events found: 0` plus "The scan worked but found nothing" | The oracle updates silently | **Design B** |
| A message about refused calls | Access still blocked | Go back to C3B |

Both A and B are fine. Only one build stage differs between them.

## C6. Record your choice

```
open -e CLAUDE.md
```

**You should see:** TextEdit opens the file. On line 3, delete the bracketed text and write
either `Design: A (event-driven)` or `Design: B (snapshot-diff)`. Save with `Cmd + S`, then close.

---

# PART D — Put it on GitHub (15 minutes)

## D1. Check Git is installed

```
git --version
```

**You should see:** `git version 2.x.x`

## D2. Check the GitHub CLI is installed

```
gh --version
```

**You should see:** `gh version 2.x.x`

**If not:** run `brew install gh`, then repeat.

## D3. Log in to GitHub

```
gh auth login
```

**You should see:** a series of questions. Answer: GitHub.com → HTTPS → Yes (authenticate Git) →
Login with a web browser. Copy the code shown, press Enter, paste it in the browser, authorise.

**Ends with:** `✓ Logged in as <your-username>`

## D4. Start tracking the project with Git

```
git init
```

**You should see:** `Initialized empty Git repository…`

## D5. Stage all the files

```
git add .
```

**You should see:** no output.

## D6. Check nothing secret is included

```
git status
```

**You should see:** a list of files. `.venv` must **not** appear. Your Etherscan key must not
appear anywhere.

**If `.venv` appears:** run `echo ".venv/" >> .gitignore`, then `git rm -r --cached .venv`, then
repeat D5.

## D7. Save the snapshot

```
git commit -m "Starter scaffold"
```

**You should see:** a summary such as `27 files changed`.

## D8. Create the GitHub repository and upload

```
gh repo create sanctions-delta-watch --public --source . --push
```

**You should see:** a URL like `https://github.com/<your-username>/sanctions-delta-watch`.

## D9. Turn on write permissions for automation

In your browser, open that repository, then:
Settings → Actions → General → scroll to **Workflow permissions** → select
**Read and write permissions** → Save.

## D10. Turn on the dashboard

Settings → Pages → Source: **Deploy from a branch** → Branch: `main`, Folder: `/docs` → Save.

**You should see:** a message that your site is being published. The URL appears after a minute
or two.

---

# PART E — Build it with Claude Code

Now the stage-by-stage prompts in `INSTRUCTIONS.md`, Part 4, take over. Start Claude Code here:

## E1. Make sure you are in the project folder

```
pwd
```

**You should see:** `/Users/carte/projects/sanctions-delta-watch`

## E2. Start Claude Code

```
claude
```

**If `command not found`:** run `npm install -g @anthropic-ai/claude-code`, then repeat.

## E3. Work one stage at a time

Open `INSTRUCTIONS.md`, go to Stage 2, paste that stage's prompt into Claude Code, and let it
finish. Then run the verification command for that stage. Then:

```
git add .
```
```
git commit -m "Stage 2 done"
```
```
git push
```

Repeat for Stages 3 through 7. Do not skip the verification step; it's how you catch a problem
while it's still small.

---

# If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `no such file or directory` after `cd` | The folder isn't where you think | Run `ls -d ~/projects/* ~/Downloads/*` and look |
| `source: no such file or directory: .venv/bin/activate` | No virtual environment yet | Do B7, B8, B9 |
| `command not found: python` | Environment not activated | Do B3, then B9 |
| `command not found: pip` | Same as above | Do B3, then B9 |
| `403 Forbidden` repeatedly | Endpoint won't serve logs | Do C3B and C4 |
| `Permission denied` | Wrong folder, or a system path | Run `pwd` and check where you are |
| Anything else | — | Copy the last 10 lines and send them to me |

**A habit worth keeping:** when a command fails, read the first error line, not the last. The
first one tells you what actually broke.
