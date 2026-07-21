# Offline Runtime

The repository root is a buildable, CPU-only Docker submission. It
implements the evaluator's exact two-argument boundary:

```text
<input_pdf_dir> <output_predictions_path>
```

The runtime renders every PDF page to visible pixels, runs bounded Tesseract OCR,
links evidence to the active case and applicant, resolves fields through the
published six-level precedence hierarchy, and applies deterministic policy.
Missing, contested, illegible, or untrusted-only decision evidence is routed to
`NEEDS_REVIEW`; `APPROVED` is emitted only after the stricter approval bar is
cleared. The canonical writer emits exactly the twelve submission fields,
rejects duplicate case IDs, and writes atomically.

## Build from a clean checkout

```bash
docker build -t mib-submission .
```

The image uses an exact Python patch release. Runtime Python dependencies are
listed in `requirements.lock` and installed during the image build with hash
checking enabled. Linux x86_64 and ARM64 wheel hashes are pinned for the PDFium,
Pillow, and NumPy rendering stack. Runtime installation or downloads are not
used. Tesseract 5 and its English/OSD data are installed at image-build time
from version-pinned Debian packages and run only against rendered page pixels.

## Run with the scoring constraints

Create a dedicated output directory before starting the container:

```bash
mkdir -p /tmp/mib-output
docker run --rm \
  --network none \
  --cpus 4 \
  --memory 8g \
  --pids-limit 512 \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=2g \
  --mount type=bind,src="$PWD/data/train",dst=/input,readonly \
  --mount type=bind,src="/tmp/mib-output",dst=/output \
  mib-submission /input /output/predictions.jsonl
```

The entrypoint rejects missing or extra arguments. It reads only the supplied
input directory and writes only the exact supplied output path. The image runs
as an unprivileged user, sets `/tmp` as its temporary and home directory, and
does not require a writable container root.

## Offline and resource guarantees

- No runtime package installation, model download, API key, network call, or
  external service is used.
- The image is CPU-only. Common numeric and tokenization thread pools are capped
  at four threads, and the application worker limit can never exceed four.
- Any scratch files remain below `/tmp`.
- Final predictions are written to the caller-provided output mount.
- The submitted image is designed for the evaluator's 4-vCPU, 8-GiB RAM,
  512-PID, and 2-GiB `/tmp` limits.

## Verify locally

Run the host-side contract tests:

```bash
python3 -m unittest discover -s tests -v
```

When Docker is available, run the image with the full command above. A
successful run exits with status zero and creates canonical policy predictions
for independently processed PDFs. A technically unreadable case is isolated
rather than aborting the batch. An empty input directory produces an empty
`predictions.jsonl`.
