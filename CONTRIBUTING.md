# Contributing to UrbanEcho

## Shared repository

Use one common repository named `hackathon-urbanecho-urbanecho`. The team name and confirmed GitHub accounts are in the [README](README.md#team-and-repository). Keep the final reviewed submission on `main`; work in short feature branches and open pull requests into `main` so teammates can review changes. Push useful changes regularly during the hackathon, rather than waiting for the final submission.

Before work starts, the repository owner should give every teammate the required contributor access, including remote teammates. Before freeze, confirm that the judging and organizing team can read the repository. Public visibility is not a substitute for confirming teammate write access; a private repository needs explicit reviewer access. Do not claim access is complete until those permissions have been checked.

## Run your own local installation

Clone the shared repository and follow [Deployment and first run](README.md#deployment-and-first-run). Each teammate generates their own `.env` with `python3 scripts/setup-env.py`; existing `.env` files are retained rather than overwritten. Docker volumes hold that installation's database and original audio. Git does not transfer those volumes.

Use `python3 scripts/verify-simulator.py` for the repeatable three-location demonstration. It adds labelled simulated records to the selected local installation. Follow the [demonstration walkthrough](docs/step7/DEMO-WALKTHROUGH.md), and keep simulated calibration separate from physical accuracy claims.

## Check a change

From the repository root, with Docker running and Node.js available:

```sh
./scripts/test-compose.sh
node --test tests/test_demo.mjs tests/test_application.mjs
```

For persistence or container-lifecycle changes, also run:

```sh
./scripts/test-persistence.sh
```

The Docker scripts use disposable databases and their own test volumes. Do not redirect integration tests to a database containing saved work: the test fixtures clear their application tables. Record what was actually tested and any remaining limitations in the pull request. Consult [Automated verification](README.md#automated-verification) for prerequisites and details.

## Keep the shared source safe to use

- Use isolated development or simulated inputs; do not connect this hackathon project to production databases or customer data.
- Keep real `.env` files, device credentials, Wi-Fi settings, TLS private keys, recordings and database backups out of Git. Share the example templates, then configure each installation privately.
- Preserve `.gitignore` exclusions, including firmware `privateconfig.h`, `config.h` and `device_config.h`. Review the staged file list before committing; do not force-add private files.
- Do not add internal company documents or screenshots containing sensitive information to the repository or other external services.
- Preserve original audio, historical location assignments and live-alert behavior when changing processing. Label demonstrations clearly and document measurement or calibration changes.

## Before the final submission

1. Merge the intended final version into `main` and confirm it is pushed to the shared GitHub repository.
2. Confirm every teammate's access and the organizers'/judges' read access.
3. Complete the README's confirmed team name, every contributor and their contribution; do not invent missing details.
4. Verify a fresh checkout can follow the local setup and repeatable demo instructions without anyone else's private files.
5. Include the overview, key features, business case, local deployment instructions and dated verification evidence. Clearly retain pending physical testing and calibration as limitations.
6. Review the submitted file list for credentials, captured audio, database exports and customer or sensitive company information.

GitHub publication shares the project source and documentation. It does not deploy a live cloud application; the documented installation remains local unless a separate deployment is completed and verified.
