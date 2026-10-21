# Measured results (bench.py unless noted)

Decode = tokens/s while writing (short chat). Prompt = tokens/s while reading a prompt of that size.

## p102: 4× P102-100 10 GB (PCIe 1.1 x4), Ryzen on B450, 46 GB DDR4-3200, NVMe, Ubuntu, CUDA 12.9

Strata 0.1.43 + all patches, IQ3_XXS, context 262K, 4 cards (as in the video):

| | story | code | prompt 3K | prompt 12K |
|---|---|---|---|---|
| IQ3_XXS (original) | 38.5–39.1 | 59.9–60.7 | 194–204 | 371–372 |
| IQ3_XXS SC117 abliterated, spec-min-p 0.5 | 36.0 | 54.5 | 196 | 369 |
| IQ3_XXS SC117 abliterated, spec-min-p 0.70 | 38.7 | 54.7 | – | – |

Fewer cards (same config, only `"gpu"` changed; 2026-10-21):

| cards | story | code | prompt 3K | prompt 12K | experts on cards | RAM free |
|---|---|---|---|---|---|---|
| 4 | 39.1 | 59.9 | 194 | 371 | 12,104 | 7.3 GB |
| 3 | 36.2 (−7%) | 53.6 (−11%) | 186 | 335 (−10%) | 8,819 | 4.6 GB |
| 2 | 29.8 (−24%) | 46.0 (−23%) | 165 | 266 (−28%) | 5,363 | 3.5 GB, swap 1.5 GB |
| 1 | 11.5 (−71%) | 18.0 (−70%) | 150 | 187 (−50%) | 2,034 | 3.2 GB, model barely fits |

(original IQ3_XXS; the SC117 file gave the same shape, 5–10% slower in decode)

What the engine patches gave on p102 (long prompts, layer split): own CPU pool per stage (POOL_OWN=3) 9K prompt
32 → 28 s, 20K prompt 50 → 44 s; short prompts and decode unchanged.

## Main PC: RTX 3080 Ti 12 GB + RTX 3060 12 GB, 64 GB RAM, Windows 11, CUDA 13.0

SC117 IQ3_S, context 262K, stock 0.1.43 → patched + calibrated config:

| | before | after |
|---|---|---|
| prompt 3K | 347 | 692 |
| prompt 12K | 770 | 1,229 |
| story | 62.2 | 59.3 |
| code | 76.9 | 77.3 |
| first token after a 1K prompt | 7.5 s | 2.3 s |

## Humanity's Last Exam (100 random questions, seed 1, 16K-token cap incl. thinking)

IQ3_S 17/100 (70 ran out of tokens while thinking). On the 62 questions both finished: IQ3_S 9, IQ3_XXS 4.
Hand-judged where the answer format was loose; ±4% noise at this size.
