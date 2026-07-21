# Offline Runtime Scaffold

The repository root is a buildable, CPU-only Docker submission scaffold. It
implements the evaluator's exact two-argument boundary:

```text
<input_pdf_dir> <output_predictions_path>
```

WO-1 intentionally contains no extraction, linking, or adjudication logic.
`solution.py` enumerates top-level PDF cases deterministically and initializes a
valid empty JSONL file. The batch runner and canonical row writer are introduced
by the downstream work orders.

## Build from a clean checkout

```bash
docker build -t mib-submission:wo-1 .
```

The image uses an exact Python patch release. Runtime Python dependencies are
listed in `requirements.lock` and installed during the image build with hash
checking enabled. The scaffold currently needs only the Python standard library,
so the lock file contains no packages. Runtime installation or downloads are not
used.

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
  mib-submission:wo-1 /input /output/predictions.jsonl
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
- Scratch files, when introduced by later work orders, must remain below `/tmp`.
- Final predictions are written to the caller-provided output mount.
- The submitted image is designed for the evaluator's 4-vCPU, 8-GiB RAM,
  512-PID, and 2-GiB `/tmp` limits.

## Verify locally

Run the host-side contract tests:

```bash
python3 -m unittest discover -s tests -v
```

When Docker is available, run the image with the full command above. A
successful WO-1 scaffold run exits with status zero and creates an empty
`predictions.jsonl`; downstream work orders add one canonical row per answered
case.
