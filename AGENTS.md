# Strata on cheap multi-GPU rigs — the "p102" kit (Strata 0.1.43 + local patches)

You are an AI agent helping a person reproduce the setup from the video: Strata (https://github.com/Niko1221/Strata)
running Qwen3.8-Flash-Next on 4× P102-100 (10 GB mining cards, PCIe 1.1 x4 each) plus a second PC with
RTX 3080 Ti + RTX 3060. This folder holds the exact patches, configs and test tools. Work through the steps below,
adapting paths and card lists to the person's machine. Ask before anything that downloads big files, installs
drivers/toolkits, or exposes a server to the network.

## 0. What the person needs

- NVIDIA cards. Pascal (P102-100, P104, GTX 10xx, P40) needs the CUDA **12** build; RTX 20+ can use CUDA 13.
- RAM + VRAM together ≥ the model file. IQ3_XXS (~47 GB) needs about 48 GB RAM with 2–4 × 10 GB cards (see
  results/). With 32 GB RAM pick a smaller quant (IQ2_XS, Q2_0).
- An SSD (NVMe best) for the model files and the conversation spill folder; ~80 GB free per model.
- Linux (tested Ubuntu, driver 580 branch, CUDA 12.9) or Windows 11 (driver 581.94, CUDA 13.0, VS 2022 Build Tools).
- Do **not** update the GPU driver as a side effect: CUDA toolkit installers can replace it. Ask first, and check
  `nvidia-smi` afterwards. Some mixed-generation rigs only work with one specific driver.

## 1. Get Strata 0.1.43

```sh
git clone https://github.com/Niko1221/Strata.git && cd Strata && git checkout v0.1.43
./setup.sh            # Linux (Windows: setup.bat) - let it download the model and build once, unpatched
```
Let the stock setup finish and answer one chat first: that proves drivers, toolkit and model files are fine before
any patch is involved. Note which model/quant setup chose and the config it wrote (`strata-<tag>.json`).

## 2. Apply the patches (order matters)

All patch scripts take the Strata folder as their argument and refuse to run twice.

```sh
K=/path/to/this/kit
python $K/patches/engine/apply-spill-patch.py .
git apply --ignore-whitespace $K/patches/engine/strata-multihelp-0.1.43.patch
for p in log splice perflog pplog divergence enginelog stageprog runid; do
  python $K/patches/server/apply-$p-patch.py . || break
done
```
They apply cleanly to v0.1.43 only (checked). On another version they will fail — see "Newer Strata" below.

What each one does:

| Patch | Part | What it does | Switch |
|---|---|---|---|
| spill | engine | parked conversations move from RAM to files on an SSD when RAM runs short, instead of being dropped | `STRATA_CONVERSATION_SPILL_DIR=<ssd folder>` (off without it) |
| multihelp | engine | on a layer split, idle cards help read the prompt (every chunk, not just one); each stage gets its own small CPU pool while another stage holds the shared one; branch-skip and extra checkpoints when parking; per-card progress lines | `STRATA_PREFILL_HELPERS=1`, `STRATA_PREFILL_POOL_OWN=3`, `STRATA_PARK_BRANCH_SKIP=1`, `STRATA_PARK_CHECKPOINTS=3`, `STRATA_STAGE_PROGRESS=1` |
| log | server | progress lines with time of day and the last-3-seconds speed | always |
| splice | server | an answer sent back unchanged reuses the model's own tokens (no re-read); maintenance switch: while `~/strata-maintenance` exists only local requests are served | always / file |
| perflog | server | one JSON line per request in `~/strata-perf/requests-YYYY-MM.jsonl` | always |
| pplog | server | one line per request: how the prompt was read, how much came from the cache | always |
| divergence | server | says where a request differs from the cached conversation (why it was re-read) | always |
| enginelog | server | engine log lines get timestamps | always |
| stageprog | server | progress line shows each card's place while reading a prompt | needs `STRATA_STAGE_PROGRESS=1` |
| runid | server | every engine start gets a run id; details (binaries, config, env, driver, GPUs) in `~/strata-perf/runs.db` | always |

The server patches are optional quality-of-life/measurement; the speed comes from the engine patches and the
config. Opt-in engine switches do nothing unless set in the config's `"env"`.

## 3. Build the patched engine

```sh
.venv/bin/python $K/tools/build-engine.py . --arch 61 --toolkit 12 --jobs 4     # P102 / Pascal, Linux
.venv\Scripts\python.exe %K%\tools\build-engine.py . --arch 86 --toolkit 13      # RTX 30, Windows
```
The engine lands in `engine-cuda12/` (toolkit 12) or `engine/`. Keep the stock one: copy the result to its own
folder (e.g. `engine-patched/`) and point the config's `"exe"` at it, so you can switch back by editing one line.
With little free RAM (a model already running) use `--jobs 2`: each CUDA compile job can take 2 GB.

## 4. Config

`configs/` has the three configs from the video (API keys replaced by `CHANGE-ME…`, home folders by `/home/YOU`).
Do not copy them blindly: start from the config the person's own setup wrote and carry over these parts:

- `"env"`: the switches above plus `STRATA_PREFILL_CPU_SHARE=auto`, `STRATA_PREFILL_CPU_SHARE_MAX=8192`
  (the CPU computes part of each prompt chunk up to 8,192-token chunks), and on Linux
  `MALLOC_MMAP_THRESHOLD_=4194304`.
- `"args"` worth copying for multi-card rigs: `--prefill 4096`, `--layer-split auto` (or `"layer_split": "auto"`),
  `--kv int8`, `--resident-experts`, `--prompt-cache 16 --prompt-cache-every 4096`,
  `--conversation-cache-mib` sized to spare RAM, `--pcie-frac 0.00` on PCIe 1.1 x4 cards (measured best on p102).
- `--spec-min-p`: 0.70 was calibrated for the plain IQ3_XXS on p102; the abliterated SC117 file ran ~7% faster in
  prose at 0.70 than at 0.5, code unchanged (noisy). Try both.
- `"gpu": [0, 1, 2, 3]` picks the cards; `CUDA_DEVICE_ORDER=PCI_BUS_ID` keeps numbering equal to `nvidia-smi`.
- Security: if `"host"` is `0.0.0.0` (other machines can reach it), `"api_key"` must be set to a long random value.
  Never publish a server without a key. Generate one: `python -c "import secrets;print(secrets.token_urlsafe(32))"`
  and also save it to `~/.strata-api-key` for the test tools.

## 5. Check it

```sh
STRATA_URL=http://127.0.0.1:22221 python $K/tools/bench.py "my first run"     # results in ~/bench-results.jsonl
STRATA_URL=http://127.0.0.1:22221 python $K/tools/qualtest.py mine              # facts in long filler + arithmetic
```
bench.py: decode (story ×2, code ×2) and prompt reading at 3K / 12K / 1K tokens. Compare with `results/`.
Run each test twice; run-to-run spread is about ±5–10% on decode. `tools/maint.sh "<reason>" <command>` runs a test
with the maintenance switch on so other users get a clear "under maintenance" answer meanwhile.

## 6. Hardware lessons from this rig

- P102-100 is fixed at PCIe 1.1 x4 (1 GB/s). That is fine: Strata keeps hot experts on the cards and the rest in
  page-locked RAM. More cards = more experts on cards (4 cards 11.5K, 2 cards 5.1K, 1 card 1.9K).
- An extra display card on the chipset lanes can steal the chipset's 256 MB prefetchable window, and then a P102 on
  the chipset fails with `NVRM: BAR1 is 0M` — no display card there, or try kernel `pci=realloc`.
- A bifurcation adapter (x16 → x4x4x4x4) needs the BIOS lane setting (MSI B450: PCI_E1 Lanes Configuration); a
  loose adapter makes all its cards and its NVMe vanish together.
- Power: 4 × P102 decoding drew ~1 kW for hours with spikes near 1.45 kW; a 1200 W PSU died. 1800 W since.
- RAM: with 46 GB, 2 cards left 3.4 GB free and started swapping a little; plan RAM before cutting cards.

## Newer Strata (0.2.x)

Do not apply these patches to 0.2.x blindly. Checked against 0.2.1.4: the server patches need two small anchor
fixes, the spill patch two (conversation cache gained per-entry serials and borrowing), and the multihelp patch fails
6 of 39 hunks (the core of POOL_OWN and of HELPERS among them; 4 belong to an unused opt-in). 0.2.x also brings its
own Pascal (sm_61) and RTX 30 (sm_86) speedups, so measure a clean 0.2.x first and port only what still pays.
