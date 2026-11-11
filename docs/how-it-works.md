# How OpenAudioFix works

The rule behind every module: **change what is broken, and nothing else.** Most tools that clean audio regenerate
the whole signal. OpenAudioFix repairs specific samples, lists every fix, and leaves the rest of the file as it was.

## The chain

Modules run in a fixed order. Each one reports what it changed, and you can listen to the result of every step.

```mermaid
flowchart LR
  A[Your file] --> B[Declip] --> C[Glitch repair] --> D[Background repair] --> E[Keep speech only] --> F[Export]
```

Intermediate results stay in 64-bit float with no gain change. Gain is applied once, at export.

Declip runs before glitch repair for a measured reason. Glitch repair can overshoot full scale when it reconnects a
waveform across a flattened peak, and the overshoot hides that peak from declip. Dropout seams are still detected on
the untouched source, so running declip first costs no detections.

## Glitch repair

**The damage.** When an audio interface misses its deadline, a buffer of samples is lost and the two sides of the
gap are joined directly. You hear a tick or a tiny skip. These seams tend to land on multiples of the interface's
buffer size, which helps tell them apart from real transients.

**The repair.** Filling the gap with interpolated audio is not enough on voiced speech, because the waveform on each
side of the seam is out of phase by the lost time. OpenAudioFix applies a short, phase-aligned micro-warp around the
seam so the periods line up again. Where that does not apply, it falls back to autoregressive interpolation.

Samples outside each repair stay bit-identical to the input.

## Declip

**The damage.** Too much input gain flattens peaks at full scale.

**The repair.** Two methods run on each clipped region, and their results are averaged on the clipped samples:

- A **frame-joint constrained autoregressive solve**, which tends to rebuild peaks slightly low.
- **A-SPADE** (Kitić, Bertin and Gribonval, 2015), a sparsity-based method that tends to overshoot.

Their errors point in opposite directions, so the average beats either one. On clean speech clipped on purpose, in
signal-to-distortion ratio over the clipped samples (dB, higher is better, 11 to 12 cases per column):

| Method | 0.5% clipped | 2% | 6% | 12% |
|---|---:|---:|---:|---:|
| Frame-joint AR | 31.6 | 22.8 | 17.8 | 14.7 |
| A-SPADE | 36.5 | 23.3 | 17.6 | 16.4 |
| **Average (used here)** | 33.5 | **23.5** | **20.0** | **17.1** |

We also looked at learned declippers. At the time of that survey they ran at lower sample rates, changed samples that
were not clipped, or had no public code, so none could keep the rule above.

Rebuilt peaks are often above 0 dBFS. That is correct: they were above it before the clipping. Export offers a
32-bit float file that keeps them as they are, or a file at the original bit depth lowered just enough to fit.

## Background repair

Removes noise and room sound with [MossFormer2](https://github.com/modelscope/ClearerVoice-Studio) on an NVIDIA GPU.
This is the one module that changes every sample, so it is optional and off by default.

## Keep speech only

Removes what is not talking from a podcast track and gives you both halves:

1. A level gate finds each sound, measured after a 100 Hz high-pass so that hum barely counts.
2. Each sound is classified. It is speech if [Silero VAD](https://github.com/snakers4/silero-vad) hears speech in it,
   or if it is voiced (character voices, laughs, singing).
3. Knocks, clicks, short noises and long stretches the model never hears, such as music, are removed.

`_speech.wav` and `_removed.wav` have complementary fades, so the two played together are the original. Nothing is
lost: pull anything you want back from the removed track.

## Exporting without touching the rest

For 16, 24 and 32-bit integer WAV input with no gain change, export patches only the repaired samples into a copy of
your file. Every other byte, including metadata chunks, is unchanged.

## How this is tested

- **Bit-exact checks** on samples outside repairs.
- **Regression references** on synthetic damage, in `tests/regression/`.
- **A real browser** plays real audio in `web/e2e/`.
- **Every bug** gets a test and a line in [tests/REGRESSIONS.md](../tests/REGRESSIONS.md).
