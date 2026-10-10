# YAMNet assets and UrbanEcho category mapping

YAMNet is Google's pretrained AudioSet sound-event model. It estimates sounds
present in a clip; it does not measure decibels, transcribe speech, identify a
speaker, or establish the physical cause of a sound. Classification does not
participate in threshold or incident evaluation.

## Provenance and redistribution

- Model: the official [Google AudioSet TFLite release](https://storage.googleapis.com/audioset/yamnet.tflite),
  also described in the [Google model card](https://www.kaggle.com/models/google/yamnet/tfLite/classification-tflite/1).
  The classifier image downloads the model at build time, not during uploads.
  SHA-256: `e193da5676a2fdb7a3702c10fba62aa27d76f64e4534467443811c1a2ff1667d`.
- `yamnet_class_map.csv` is unmodified from
  [tensorflow/models commit dfffd623b6be8d1d9744b8e261fbac370d17c46d](https://github.com/tensorflow/models/blob/dfffd623b6be8d1d9744b8e261fbac370d17c46d/research/audioset/yamnet/yamnet_class_map.csv).
  SHA-256: `cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2`.
- `LICENSE.tensorflow-models` preserves that repository's Apache License 2.0.
  Upstream YAMNet source is copyright The TensorFlow Authors. See its
  [README](https://github.com/tensorflow/models/blob/dfffd623b6be8d1d9744b8e261fbac370d17c46d/research/audioset/yamnet/README.md)
  and [official export code](https://github.com/tensorflow/models/blob/dfffd623b6be8d1d9744b8e261fbac370d17c46d/research/audioset/yamnet/export.py).
- `category_mapping.v1.json` is UrbanEcho's own grouping of all 521 official
  labels. It preserves each original class index, AudioSet ID and display name.
  The model, class map and mapping are checked against pinned checksums before
  inference. Version identifiers and checksums live in
  `app/classification_contract.py`, which has no ML dependencies.

## What the eight categories mean

| Category | Included evidence | Important limitation |
| --- | --- | --- |
| Traffic | Road vehicles, road traffic, vehicle movement/accessory sounds | Generic Vehicle/Engine, aircraft, boats and trains are not assumed to be road traffic. |
| Horn | Car, truck, train and foghorn labels | The category alone does not identify the vehicle. |
| Siren | Emergency-vehicle and general/civil-defense sirens | Does not establish that there is an emergency. |
| Construction | Chainsaw and tool sounds, including hammer, drill and jackhammer | A tool sound does not prove construction is taking place; generic engine/hum is excluded. |
| Music | Singing, instruments and music/genre labels | Generic bells are not automatically music. |
| Animal | Animal, bird and insect vocalizations | Estimates a sound, not an animal's location or identity. |
| Voice | Speech, vocal calls, laughter and crowd sounds | No words, speaker identity or personal characteristics are inferred. |
| Other | All remaining official classes or insufficient supported evidence | Includes silence/flat/short input and uncertain estimates, with explicit reasons. |

## Input and score calculation

Accepted originals are intact mono PCM16 or packed PCM24 WAV at 16, 32, 44.1 or
48 kHz, bounded to 600 seconds and 100 MB. The normal backend upload limits may
be lower. The same strict WAV parser used by the sound-level pipeline validates
headers, extents and frames before allocation. Original bytes are only read.
Signed samples are scaled to float32 full scale. Non-16-kHz input is resampled
in memory with SciPy's polyphase low-pass resampler. Resampler overshoot alone
is limited to [-1, 1] for model input; no gain normalization is applied.

The official fixed-input LiteRT model takes 15,600 waveform samples (975 ms)
for one 960-ms feature patch. We use 480-ms hops and, when necessary, add a
final full window aligned to the recording's end. This includes the tail
without padding it with invented silence; a 1-second clip has two overlapping
windows. Each window's 521 scores is averaged across the clip. A category's
score is the maximum averaged member-class score. Top original labels and
scores are retained alongside the category estimate.

The 0.25 selection cutoff is an explicit product heuristic, not a measured
accuracy or calibrated probability. Scores are multilabel outputs: they need
not sum to one and must not be displayed as a percentage chance. Qualifying
horn/siren evidence suppresses a generic traffic primary label. Other supported
categories still compete by score. If an unmapped label scores at least as high
as the selected category, the result is Other/uncertain. If no supported category
reaches the cutoff, it is also Other/uncertain.

Clips shorter than 975 ms, silence and flat input produce `no_usable_audio`
without inference. PCM rail clipping produces a warning and an uncertain
result even if a category has a high model score. This does not detect every
microphone, wiring or acoustic problem. Sound-pressure calibration is not
required to estimate a sound category, and a category estimate does not make
an uncalibrated level accurate. Missing/bad models, invalid WAVs and runtime
failures produce bounded errors; they never modify originals or create alerts.

## Repeatable model verification

Unit tests cover taxonomy, preprocessing and safe failure paths. With the
classifier dependencies installed and a verified model available, run:

```sh
YAMNET_TEST_MODEL_PATH=/opt/urbanecho-models/yamnet.tflite \
  python -m pytest -q tests/test_classification.py
```

The real-model test uses a clearly synthetic sine wave held only in a test
directory. It verifies the official Sine wave label and unchanged original
bytes; it is not evidence of field classification accuracy. Public Google
example audio may be used for additional manual smoke checks, without adding
it to the application database or presenting it as device data.
