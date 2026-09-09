# Repository agent

The `Repository agent` Actions workflow provides manually requested PR review
and optional CI failure triage using the
[official Codex action](https://developers.openai.com/codex/github-action).
It reports in the workflow run's **report** job summary. It has read-only GitHub
permissions and does not post comments, commit fixes, merge, or publish releases.

## Setup

1. Merge `.github/workflows/agent.yml` and `.github/codex/review.md` into the
   repository's default branch.
2. Add an Actions repository secret named `OPENAI_API_KEY` under **Settings →
   Secrets and variables → Actions**. Use an API key with available API quota;
   runs consume OpenAI API usage as well as GitHub Actions minutes.
3. Under **Actions → Repository agent → Run workflow**, select the default
   branch and enter a PR number. Optionally enter the numeric run ID from a
   failing CI run's URL (`/actions/runs/<run_id>`).
4. Open the completed run's **report** job summary to read the findings.

The CI run must belong to this repository and match the PR's current head SHA.
Unavailable or expired logs fail evidence collection; omit the run ID to perform
a PR-only review. A missing API key fails the Codex step. The agent uses the
action's default model; set its `model` input to customize it.

## Behavior and limits

Only a manual dispatch starts a run. The action defaults to allowing users with
repository write access. Each run has a 15-minute review timeout; a new review
of the same PR cancels the previous run.

The workflow checks out the default branch and downloads the PR diff as data.
It does not check out or execute PR code. The prompt covers catalogs, shell
behavior, installers, documentation, and dependency compatibility. Failed-job
logs are limited to their final 200,000 characters. PR changes and supplied logs
are sent to OpenAI for analysis; only request reviews of data suitable for that
service. The model uses a read-only sandbox with sudo removed.

Existing CI remains responsible for lint and tests. Agent findings are advisory,
and a green agent run means a report was generated, not that tests passed.
No paid run is triggered by adding these files locally. Validate the first live
run after merging and configuring the secret.
