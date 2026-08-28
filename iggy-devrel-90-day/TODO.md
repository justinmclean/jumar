# Apache Iggy DevRel first 90 days

Source plan:
  /Users/justinmclean/.codex/attachments/6b641d73-3f4d-457a-a91c-047e4a22b705/pasted-text.txt
Engagement window:
  2026-08-31 to 2026-11-29
Project workspace:
  /Users/justinmclean/iggy
DevRel analysis workspace:
  /Users/justinmclean/iggy-work

Autonomous operating rule: do not post to GitHub, push branches, open PRs, or
make operator-facing governance decisions. Prepare drafts, local analysis,
patches, scripts, reports, and review notes for Justin to approve or publish.

Jumar output rule: every unfinished item below must leave a local artefact under
this folder, using the exact path named in the subtask. Prefer one or two
subtasks per item. Each artefact must contain a short `Status: draft` or
`Status: report` line, an `Evidence:` section naming the local files or public
URLs used, and enough concrete labels from the subtask for a simple file-content
check to prove the work exists. Do not rely on fragile exact prose checks.

Run a pre-start check so the first week begins with current, reproducible data.
The existing DevRel baseline inventory and analysis artefacts are in
/Users/justinmclean/iggy-work; start there before looking anywhere else.
- [x] Refresh the DevRel baseline inventory @id=baseline-inventory @priority=1 @not-before=2026-08-27 @due=2026-09-02 @capability=read_fs @capability=write_fs @capability=run_commands @capability=network
      - [ ] Inspect /Users/justinmclean/iggy-work/iggy_analysis.py, figures-baseline-2026-08-26.json, figures.json, iggy-figures-report.txt, gfi.json, issues.json, discussions.json, and the PR audit JSON files
      - [ ] Record the baseline measures from /Users/justinmclean/iggy-work/iggy-devrel-90-day-plan.md and figures-baseline-2026-08-26.json in a machine-readable local file
      - [ ] If GitHub access is available, refresh the current good-first-issue, issue label, PR, and discussion snapshots using the existing scripts in /Users/justinmclean/iggy-work
      - [ ] Write a concise baseline note with commands run, data dates, and gaps

The plan calls for an early first-fortnight checkpoint before too many hours are
spent. Prepare material only; Justin chooses what to send.
- [ ] Draft the first-fortnight priority checkpoint @id=fortnight-checkpoint @priority=2 @not-before=2026-09-07 @due=2026-09-11 @depends=baseline-inventory @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/fortnight-checkpoint.md with Status: draft, Evidence:, Week-one priorities:, Tradeoffs:, Decisions needed:, and Async update: sections
      - [ ] Ensure reports/fortnight-checkpoint.md mentions contribution handling, on-ramp issues, docs, and AI-developer assets

This recurring item supports contribution handling without requiring commit
rights. It must produce local notes only: do not post, comment, label, close,
assign, or push anything. Use existing local snapshots in /Users/justinmclean/iggy-work
first; refresh from GitHub only if the configured network access works quickly.
- [ ] Prepare daily Iggy contribution triage notes @id=daily-triage @priority=3 @max-subtasks=2 @every=weekday @not-before=2026-08-27 @capability=read_fs @capability=write_fs @capability=run_commands @capability=network
      - [ ] Create recurring/daily-triage/latest.md with exactly these headings: Status: report, Evidence:, Snapshot date:, New or updated:, Newcomer items:, Unanswered:, Duplicates:, Stale branches:, Missing labels:, Suggested replies:, Suggested labels:, Review focus:, Manual actions only:
      - [ ] Append one dated entry to recurring/daily-triage/log.md with exactly these labels: Date:, Source:, First-response observations:, Unlabelled-issue observations:, Next check:

Introduce Justin to the Apache Iggy community in the project's public channels.
Draft only; Justin sends the final messages.
- [x] Draft Iggy mailing list and Discord introductions @id=community-introductions @priority=4 @not-before=2026-08-27 @due=2026-09-02 @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create project_context/channels.md containing exactly mailinglist://@iggy.org and create project_context/channels_notes.md mentioning dev@iggy.apache.org and https://discord.gg/apache-iggy
      - [ ] Create drafts/mailing_list_intro.md and drafts/discord_intro.txt, then copy both files into review_staging/

The newcomer on-ramp is a high-leverage workstream in the plan. This task should
produce concrete issue edits and retire/keep recommendations for human review.
- [ ] Audit and repair good-first-issue inventory @id=good-first-issue-audit @priority=4 @not-before=2026-08-27 @due=2026-09-06 @depends=baseline-inventory @capability=read_fs @capability=write_fs @capability=run_commands @capability=network
      - [ ] Create reports/good-first-issue-audit.md with Status: report, Evidence:, Open good-first-issue inventory:, Classification:, Rewrites:, Assignment-expiry proposal:, and Five new scoped newcomer tasks: sections
      - [ ] Create data/good-first-issue-audit.json with keys generated_at, source_files, open_good_first_issues, classifications, rewrites, dormant_claims, and new_task_candidates

The handoff notes an untested hypothesis that the CONTRIBUTING.md Close Policy
may have affected newcomer outcomes from June onward. Test the history and draft
findings, without changing policy directly.
- [ ] Investigate CONTRIBUTING.md Close Policy impact @id=close-policy-impact @priority=5 @not-before=2026-09-03 @due=2026-09-12 @depends=baseline-inventory @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/close-policy-impact.md with Status: report, Evidence:, Policy change date:, Before/after comparison:, Sampled PRs:, Confounders:, Findings:, and Suggested next questions: sections
      - [ ] Create data/close-policy-impact.json with keys policy_change_date, before_period, after_period, sampled_prs, metrics, confounders, and conclusion

The baseline points to merge authority and close-loop coverage as the throughput
constraint. Prepare a recommendation note for project discussion; do not contact
committers or change governance.
- [ ] Draft merge-throughput and committer enablement recommendations @id=merge-throughput-recommendations @priority=6 @not-before=2026-09-10 @due=2026-09-20 @depends=baseline-inventory @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/merge-throughput-recommendations.md with Status: draft, Evidence:, Merge distribution:, Reviewer pool:, Time to land:, Low-risk recommendations:, Committer enablement checklist:, Rotation proposal:, and Discussion-ready note: sections
      - [ ] Ensure reports/merge-throughput-recommendations.md says draft only and does not recommend changing governance without PMC discussion

Keep open carry-over work from earlier docs and governance passes visible so it
does not vanish behind newer activity. Track status only; do not push, comment,
or close anything automatically.
- [ ] Track open Iggy carry-over PRs and follow-ups @id=open-carryover-tracker @priority=7 @every=mon @not-before=2026-08-27 @capability=read_fs @capability=write_fs @capability=run_commands @capability=network
      - [ ] Create recurring/open-carryover/latest.md with Status: report, Evidence:, Website follow-ups:, Getting-started follow-ups:, Security follow-ups:, Documentation follow-ups:, Drift over one week:, Blockers:, and Next suggested actions: sections
      - [ ] Append a dated summary to recurring/open-carryover/log.md

Benchmark claims are already useful public material, but they need a reader's
guide so people understand what was measured, what was not, and how to reproduce
or interpret the numbers.
- [ ] Draft "how to read Iggy benchmarks" explainer @id=benchmark-reading-guide @priority=8 @not-before=2026-08-27 @due=2026-10-08 @depends=baseline-inventory @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/benchmark-reading-guide.md with Status: draft, Evidence:, Existing benchmark assets:, Workload:, Hardware:, Transport:, Batching:, Latency percentiles:, Throughput:, Caveats:, Reproducible facts:, Interpretation:, and Missing metadata: sections
      - [ ] Ensure reports/benchmark-reading-guide.md links or names the benchmark docs, scripts, dashboards, posts, and any LinkedIn claims reviewed

Before adding more docs, map the current documentation information architecture
from a user's point of view. This is an audit and recommendation task, not a site
rewrite.
- [ ] Audit Iggy documentation information architecture @id=docs-information-architecture-audit @priority=9 @not-before=2026-09-24 @due=2026-10-10 @depends=getting-started-config-verify @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/docs-information-architecture-audit.md with Status: report, Evidence:, Current map:, Install:, Concepts:, SDKs:, Server configuration:, Examples:, Connectors:, MCP:, Contribution guidance:, Duplicated content:, Stale content:, Missing content:, Hard-to-discover paths:, Recommended navigation:, Ownership map:, and Small independent fixes: sections
      - [ ] Create data/docs-information-architecture-map.json with keys generated_at, sources, current_paths, gaps, recommendations, and small_fixes

Real user stories are high-value DevRel content, but the 90-day scope should only
create the pipeline: candidates, questions, and draft outlines. Do not contact
users automatically.
- [ ] Build Iggy user story pipeline @id=user-story-pipeline @priority=10 @not-before=2026-08-27 @due=2026-10-20 @depends=baseline-inventory @capability=read_fs @capability=write_fs @capability=run_commands @capability=network
      - [ ] Create reports/user-story-pipeline.md with Status: draft, Evidence:, Public users:, Integrations:, Mentions:, Story candidates:, Outreach questions:, and Case-study outline: sections
      - [ ] Create data/user-story-candidates.json with keys generated_at, candidates, evidence_links, outreach_questions, and case_study_outline

The plan identifies documentation snippet regressions as preventable. Prototype
outside the repo first, then leave a patch or design note that can become a PR.
- [ ] Prototype documentation snippet compile checks @id=docs-snippet-ci-prototype @priority=11 @not-before=2026-08-27 @due=2026-09-15 @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/docs-snippet-ci-prototype.md with Status: report, Evidence:, Snippet inventory:, Classification:, Extractor path:, Commands run:, Results:, CI integration note:, and Language-specific gaps: sections
      - [ ] Create tools/docs-snippet-check/README.md describing the prototype extractor and create data/docs-snippet-inventory.json with keys generated_at, sources, snippets, classifications, commands_run, and gaps

C# is called out as the last SDK page family not yet corrected. Work locally and
produce exact suggested edits and verification notes.
- [ ] Verify and draft fixes for C# SDK documentation @id=csharp-docs-sweep @priority=12 @not-before=2026-09-08 @due=2026-09-22 @depends=docs-snippet-ci-prototype @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/csharp-docs-sweep.md with Status: report, Evidence:, C# docs located:, Examples located:, Commands checked:, Snippets checked:, Corrected snippets:, Page edits:, Compiled examples:, Run examples:, and Not verified: sections
      - [ ] Create drafts/csharp-docs-edits.md containing exact suggested Markdown edits for Justin to review

Getting-started and server configuration need verification against a running
server. Keep outputs local and stop short of publishing.
- [ ] Verify getting-started and server configuration docs @id=getting-started-config-verify @priority=13 @not-before=2026-09-15 @due=2026-09-29 @depends=docs-snippet-ci-prototype @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/getting-started-config-verify.md with Status: report, Evidence:, Server start command:, Getting-started steps checked:, Server configuration examples checked:, Commands run:, Results:, Documentation fixes:, and Verification evidence: sections
      - [ ] Create drafts/getting-started-config-edits.md containing exact suggested Markdown edits for Justin to review

The plan includes an independently publishable end-to-end tutorial. Draft it
after the getting-started path has been verified, so every command is grounded in
the current server and SDK behaviour.
- [ ] Draft end-to-end getting-started tutorial @id=end-to-end-tutorial @priority=14 @not-before=2026-09-29 @due=2026-10-18 @depends=getting-started-config-verify @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create drafts/end-to-end-getting-started-tutorial.md with Status: draft, Evidence:, Target SDK:, Scenario:, Setup:, Start server:, Produce messages:, Consume messages:, Cleanup:, Rerun-safe commands:, Verification evidence:, and Notes for Justin: sections
      - [ ] Create reports/end-to-end-tutorial-verification.md listing every command run, exit status, output summary, and any unverified step

The documentation MCP work is intentionally parked for now. When unpaused, keep
it to a design note and smoke-test draft only unless Justin explicitly asks for
code changes.
- [ ] Continue the Iggy documentation MCP work @id=iggy-docs-mcp-prototype @priority=15 @paused=skip-for-now @max-subtasks=2 @not-before=2026-09-22 @due=2026-10-13 @depends=getting-started-config-verify @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/iggy-docs-mcp-prototype.md with exactly these headings: Status: draft, Evidence:, Existing MCP summary:, Smallest useful gap:, Initial documentation resource set:, Patch plan:, Scope:, Maintenance:, Broker-tool fit:, No code changes:
      - [ ] Create drafts/iggy-docs-mcp-smoke-test.md with exactly these headings: Status: draft, Evidence:, Smoke test goal:, User question:, Expected answer shape:, Expected cited local sources:, Pass criteria:

The Apache Incubator training site has AI tutor lessons as copyable prompts:
https://incubator.apache.org/training/lessons.html. Use that pattern for Iggy
after the documentation MCP work is in hand: one or two lesson prompts that teach
concepts interactively, wait for the learner, include exercises, and cite
authoritative Iggy docs.
- [ ] Draft pilot Iggy AI tutor lessons @id=iggy-ai-tutor-lessons @priority=16 @not-before=2026-10-14 @due=2026-11-03 @depends=end-to-end-tutorial @depends=iggy-docs-mcp-prototype @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create drafts/iggy-ai-tutor-lessons.md with Status: draft, Evidence:, Source lesson pattern:, Lesson topic 1:, Lesson topic 2:, Copyable tutor prompts:, Exercises:, Self-check questions:, Authoritative docs caution:, and Manual model-test notes: sections
      - [ ] Create reports/iggy-ai-tutor-lesson-test-notes.md recording each manual prompt run and tightening notes

The plan calls for LLM-oriented project material on iggy.apache.org. Prepare the
content and validation locally.
- [ ] Draft llms.txt and machine-readable Iggy project intro @id=llms-project-intro @priority=17 @not-before=2026-10-01 @due=2026-10-15 @depends=getting-started-config-verify @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create drafts/llms.txt and drafts/iggy-project-intro.json; llms.txt must include canonical links, scope, and guidance for AI assistants, and the JSON must include purpose, transports, SDKs, connectors, and examples
      - [ ] Create reports/llms-project-intro-validation.md with Status: report, Evidence:, Required fields:, Link checks:, Validation result:, and Remaining gaps: sections

The plan mentions complete runnable applications rather than snippets. Select
examples that prove core workflows and do not overload repository throughput.
- [ ] Design the first runnable Iggy example applications @id=example-app-backlog @priority=18 @not-before=2026-10-08 @due=2026-10-22 @depends=getting-started-config-verify @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/example-app-backlog.md with Status: draft, Evidence:, Existing examples inventory:, Gaps:, Candidate app 1:, Candidate app 2:, Acceptance tests:, Repository placement:, and Implementation plans: sections
      - [ ] Create data/example-app-candidates.json with keys generated_at, existing_examples, gaps, candidate_apps, acceptance_tests, and placement

Turn completed DevRel work into occasional LinkedIn-ready drafts. Keep the pace
low: one polished draft every two weeks, based on work or project activity that
already happened. Do not publish automatically.
- [ ] Draft fortnightly Iggy LinkedIn post @id=linkedin-fortnightly-draft @priority=19 @every=2w @not-before=2026-08-27 @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create recurring/linkedin/latest.md with Status: draft, Evidence:, Reviewed notes:, Angle:, Useful link:, LinkedIn draft:, ASF tone check:, and Manual publish note: sections
      - [ ] Append a dated entry to recurring/linkedin/log.md naming the angle and draft path

This recurring item produces the weekly lightweight update described in the
plan. It should compile the previous week's local notes into a draft only.
- [ ] Draft weekly DevRel update @id=weekly-devrel-update @priority=20 @every=mon @not-before=2026-09-07 @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create recurring/weekly-update/latest.md with Status: draft, Evidence:, Work completed:, Blockers:, Pending reviews:, Decisions needed:, Next week focus:, and Async update: sections
      - [ ] Append a dated entry to recurring/weekly-update/log.md

Monthly reporting travels with the invoice. Do not overfit monthly metrics;
focus on delivered work and decisions, as the plan says.
- [ ] Draft September DevRel invoice report @id=monthly-invoice-report-sep @priority=21 @not-before=2026-09-30 @due=2026-09-30 @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/monthly-invoice-report-2026-09.md with Status: draft, Evidence:, Work delivered:, Decisions made:, Assumptions changed:, Waiting on others:, Small-sample caution:, and Invoice summary: sections

- [ ] Draft October DevRel invoice report @id=monthly-invoice-report-oct @priority=21 @not-before=2026-10-31 @due=2026-10-31 @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/monthly-invoice-report-2026-10.md with Status: draft, Evidence:, Work delivered:, Decisions made:, Assumptions changed:, Waiting on others:, Small-sample caution:, and Invoice summary: sections

- [ ] Draft November DevRel invoice report @id=monthly-invoice-report-nov @priority=21 @not-before=2026-11-29 @due=2026-11-29 @capability=read_fs @capability=write_fs @capability=run_commands
      - [ ] Create reports/monthly-invoice-report-2026-11.md with Status: draft, Evidence:, Work delivered:, Decisions made:, Assumptions changed:, Waiting on others:, Small-sample caution:, and Invoice summary: sections

At the end of the engagement, rerun the analysis against the baseline and draft
the next-period proposal.
- [ ] Rerun 90-day DevRel measurement and draft next-period proposal @id=ninety-day-measurement @priority=22 @not-before=2026-11-23 @due=2026-11-29 @depends=baseline-inventory @capability=read_fs @capability=write_fs @capability=run_commands @capability=network
      - [ ] Create reports/ninety-day-measurement.md with Status: report, Evidence:, Baseline definitions:, Commands run:, Median landing time:, 90th percentile landing time:, Merger distribution:, Distinct mergers:, Good-first-issue inventory:, Unlabelled issue share:, Newcomer response time:, Newcomer PR merge rate:, Contributor count:, Deltas:, Caveats:, and Next-period proposal: sections
      - [ ] Create data/ninety-day-measurement.json with keys generated_at, baseline_sources, current_sources, metrics, deltas, caveats, and next_period_proposal
