# ASF EU Regulatory Work — August 2026

Owner unless noted: Justin Mclean (VP Legal / VP Incubator / Director as marked).

## READ THIS FIRST — the standing constraint on every item below

**Every item on this list produces a DRAFT FILE for human review. Nothing here
sends, posts, publishes, files, or transmits anything.**

No item may: send mail to any list or person; post to a wiki, mailing list, issue tracker
or website; commit or push; or contact any external service.
If a step seems to require sending, the deliverable is the drafted text saved
to `drafts/`, and the send is a human action taken afterwards, by hand, outside this tool.

**Do fetch primary sources.** Fetch primary sources and place in `sources/` before the
relevant item runs. Allways use the source and never to substitute a summary, a recollection,
or a plausible reconstruction of what the document probably says.

**Accuracy bar.** A draft that cites a document not present in `sources/` is a
failed draft, not a draft with a caveat. Mark every unverified claim inline as
`[UNVERIFIED — check against primary source]` so a reviewer can find it.

Background: the EU AI Act became generally applicable 2 Aug 2026 (high-risk
regime deferred to Dec 2027 / Aug 2028 by the Digital Omnibus; Article 50
transparency **not** deferred).

---

## Incubator — AI disclosure questions on new proposals

These are disclosure questions, not acceptance criteria. Nothing here gates a
proposal.

- [ ] Draft the podling proposal template AI additions @id=incubator-ai-checks @priority=2 @due=2026-08-21 @capability=write_fs
      - [ ] Draft `drafts/podling-proposal-ai-questions.md` containing the two questions as they would appear in the cwiki template: whether the project distributes trained model weights (as release artefacts, convenience binaries, or via package repositories); and whether the project's software, as distributed, generates content (text, image, audio, video) or interacts directly with users
      - [ ] Add one short explanatory paragraph: the first question feeds a pending Legal Affairs discussion on model-weights guidance, with OpenNLP as the existing precedent; the second matters because EU AI Act Article 50 transparency obligations apply now, were not deferred by the Digital Omnibus, and the open-source exemption in Article 2(12) does not cover Article 50 cases
      - [ ] Draft `drafts/general-incubator-ai-questions-note.md` for general@incubator explaining the addition and leaving a placeholder for the legal-discuss thread link
      - [ ] State explicitly in that note that these are disclosure questions and not acceptance criteria, to pre-empt objections
      - [ ] Note that editing the cwiki template and posting to general@ are human steps

## Agents on ASF infrastructure

Frame as a mechanism gap in existing Infra automation policy — not as new AI
policy. Keep this separate from the AI Act project-distribution thread
(legal-discuss).

- [ ] Draft the agents-on-ASF-infrastructure thread @id=agents-thread @due=2026-08-31 @capability=write_fs
      - [ ] Draft `drafts/agents-on-infrastructure-post.md` opening with existing policy: only committers get write access, and the GitHub Actions policy constrains what workflows can run
      - [ ] Set out the two mechanisms that slip past both: GitHub App installations such as Dosu, which receive their own permission grants outside the committer model; and agents run under a committer's own credentials, where the credential holder is human but the judgement being exercised is not
      - [ ] Include the verified evidence base: Airflow runs Dosu for labelling only, adopted by lazy consensus, with the community explicitly declining auto-answers, and has adopted the Magpie framework with an AGENTS.md distinguishing agent drafts from maintainer-reviewed output; Superset runs Dosu autonomously as first responder on Issues and Discussions at 66% of first responses, wired in by Preset, a vendor; Groovy's AGENTS.md documents Assisted-by / Generated-by attribution conventions crediting the ASF AI working group; Impala committers use Generated-by tags by convention
      - [ ] Explain the Article 50 connection: live since 2 Aug, it gives the disclosure practice a binding counterpart for unreviewed AI-generated text published to inform the public, with obvious-bot content and human-reviewed content in safe harbours — so uniform attribution practice is also compliance posture
      - [ ] State the two asks: visibility into which GitHub Apps hold which permissions on the apache org, which Infra holds from install approvals; and generalising the attribution convention as ComDev and Infra work, with the Magpie and Groovy conventions as a starting point
      - [ ] Explicitly disclaim new bureaucracy: per-project autonomy over whether to use agents stays, and the foundation layer is access visibility and attribution only
      - [ ] Write `drafts/agents-thread-VENUE-DECISION.md` laying out the venue options — board@, or a cross-post seeding Infra and ComDev — with the case for each, and noting that RAI lists are excluded. **The venue choice is the human's, and posting is a human step**


## AGENTS.md — legal recommendations across AI regimes

A project wears **two different hats**, and conflating them is the mistake to
avoid.

**As a distributor of source code** it is usually neither provider nor deployer
of an AI system, and Art 2(12)'s open-source exemption does a lot of work.
AGENTS.md contributes little here beyond provenance evidence.

**As a deployer of AI on its own infrastructure** — a bot answering issues,
agent-drafted release notes, AI-written documentation — the project **is a
deployer**, and Article 50(4) binds deployers directly. That is a live
obligation, in force since 2 Aug 2026 and explicitly not deferred by the
Digital Omnibus.

Art 50(4), verbatim: *"Deployers of an AI system that generates or manipulates
text which is published with the purpose of informing the public on matters of
public interest shall disclose that the text has been artificially generated or
manipulated."* The exemption: *"This obligation shall not apply where… the
AI-generated content has undergone a process of human review or editorial
control and where a natural or legal person holds editorial responsibility for
the publication of the content."*

That exemption is the crux, and it is what AGENTS.md is for. A convention
distinguishing agent *drafts* from *maintainer-reviewed output* — the Magpie
pattern — does not merely evidence compliance; it **constitutes** the
human-review-and-editorial-responsibility exemption that removes the
obligation. AGENTS.md is the instrument telling agents how to act so the
exemption holds. Ignored in practice, the project is a deployer publishing
unreviewed AI text with no disclosure.

Note which paragraphs bind whom: 50(1) (interacting with humans, with an
"unless obvious" safe harbour) and 50(2) (machine-readable marking of synthetic
output) bind **providers** — the bot vendor, not the ASF. 50(3) and 50(4) bind
**deployers** — the project. Do not attribute provider obligations to projects.

The genuinely open question, which the draft should frame rather than pretend
to settle: what counts as *"published with the purpose of informing the public
on matters of public interest"*? A triage label almost certainly does not.
Release notes, security advisories and user-facing documentation plausibly do.
An issue-tracker reply is arguable. That boundary determines how much of 50(4)
reaches ASF practice, and it is a Legal Affairs judgement.

- [ ] Draft legal recommendations for AGENTS.md across AI regimes @id=agents-md-legal @due=2026-09-30 @capability=write_fs
      - [ ] Draft `drafts/agents-md-legal-SCOPE.md` first, separating the two hats: project-as-distributor (Art 2(12) exemption; AGENTS.md contributes provenance evidence only) and project-as-deployer of AI on its own infrastructure (Art 50(4) binds directly, and the draft/reviewed distinction constitutes the human-review exemption). Get this agreed before anything else
      - [ ] Map, per paragraph, who is bound: 50(1) and 50(2) bind providers (the bot vendor); 50(3) and 50(4) bind deployers (the project). Do not attribute provider obligations to projects
      - [ ] Frame — do not settle — the scope question for 50(4)'s text limb: what is "published with the purpose of informing the public on matters of public interest"? Propose a tiered reading (release notes / advisories / docs, versus issue replies, versus labels) and mark it a Legal Affairs judgement
      - [ ] Build `drafts/agents-md-jurisdiction-matrix.md` as a table with one row per regime and columns: instrument, in force from, who it binds, whether an ASF project distributing source is in scope, what (if anything) an AGENTS.md convention contributes, and **source location**. Every row's claims must trace to a file in `sources/`
      - [ ] **Discovery: enumerate the regimes, do not work from a remembered list.** Sweep the standing trackers — the OECD AI Policy Observatory, the IAPP global AI law tracker, national legislature and gazette sites — and record every AI instrument in force or with a fixed commencement date into `drafts/agents-md-regime-inventory.md`. Any list recalled rather than looked up is incomplete by construction, including the seed list below
      - [ ] Seed list, to be **completed and corrected** by that sweep, not treated as the answer: EU AI Act (Art 50 transparency, Art 2(12) exemption, Digital Omnibus deferrals); US state law (Colorado SB24-205, California SB 942 and AB 2013, Texas TRAIGA) with no comprehensive federal statute; China's generative AI measures and content-labelling rules; South Korea's AI Framework Act; Japan's soft-law approach; Canada (AIDA — confirm current status); Brazil PL 2338; UK's regulator-led approach with no cross-cutting statute. Expect the sweep to add jurisdictions this list omits
      - [ ] Fetch each instrument's primary text into `sources/`, named so the matrix can cite a file and a location within it. An instrument nobody fetched cannot appear in the matrix as a finding
      - [ ] Screen each with the **same three questions**, so the matrix is comparable across regimes: (1) does it impose a duty on a *deployer* (not only a provider)? (2) does that duty attach to *published text or content*, as opposed to high-risk system obligations? (3) does it reach a third-country deployer whose output is read in that jurisdiction — the Art 2(1)(c) pattern, which several regimes share?
      - [ ] **Expect almost every row to be "no distinct obligation", and record those as findings.** A regime screened and excluded, with the reason and the source, is a result — it is what lets the recommendations stay short and defensible. Do not pad the matrix to look thorough
      - [ ] Mark any row `[UNVERIFIED — check against primary source]` unless a file in `sources/` supports it. Effective dates and scope move constantly and several seed entries have already shifted. **A confident date with no source is the failure mode to avoid**
      - [ ] Draft `drafts/agents-md-recommendations.md`: the recommended AGENTS.md clauses themselves, each with the reason it exists and which regime (if any) it serves. Cover at minimum — attribution conventions (Assisted-by / Generated-by, per the Groovy and Impala practice); an explicit statement that agent output is a draft requiring maintainer review before merge, per the Magpie framework; a record of which agent and model produced a change; and a statement on what the project does *not* claim about generated content
      - [ ] Separate the recommendations into two clearly-labelled tiers: clauses justified by a specific legal obligation with a citation, and clauses that are simply good practice. Conflating the two is how a convention file acquires unearned legal authority
      - [ ] Add a section on the question that is actually load-bearing for the ASF and is **not** an AI-transparency question: copyright and licensing of AI-generated contributions — provenance, the contributor's warranty under the ICLA, and how this interacts with the ASF's existing generative tooling guidance. Note explicitly that this is a Legal Affairs matter, not something an AGENTS.md resolves
      - [ ] Draft `drafts/agents-md-template.md`: a concrete example AGENTS.md section a project could adopt, marked clearly as a starting point for projects to adapt, not a mandate. Per-project autonomy is the ASF norm and the draft should say so
      - [ ] Note in the draft that circulation (legal-discuss, ComDev, the AI working group) and any adoption decision are human steps

---

## Notes for the reviewer

**Do not rely on "no EU establishment" as a shield.** Art 2(1)(c) applies the
Regulation to "providers and deployers of AI systems that have their place of
establishment or are located in a third country, where the output produced by
the AI system is used in the Union". A bot whose replies, release notes or
documentation are read by EU users produces output used in the Union, so a
US-registered foundation is within scope as a deployer. Art 2(12)'s open-source
exclusion does not rescue this either: it expressly does not apply to systems
falling under Article 5 or Article 50.

Jurisdictional *scope* and enforcement *practicality* are different questions,
and only the second is genuinely uncertain. Whether an EU authority would
pursue a volunteer foundation is speculative; whether the obligations formally
reach it is not. The operative pressure remains downstream users demanding
documentation, but the draft should not present that as the only exposure.

Item `agents-thread` is dated end-August on the assumption the legal-discuss
thread has run its course by then. If it has not, push the `@due=` out rather
than posting into a live thread.

`agents-md-legal` overlaps `agents-thread` but is deliberately separate: the
thread is about *access and attribution mechanisms on ASF infrastructure*, the
recommendations are about *what a project's own convention file should say*.
Keeping them apart preserves the three-thread separation already being
maintained.

Breadth of sources is not the difficulty here. Most of the jurisdiction matrix
will resolve to "no distinct obligation" or "out of scope", and the operative
material is likely to be small — Art 50(4) and its human-review exemption, plus
whichever analogues elsewhere actually impose a deployer disclosure duty on
published project text. The work is mostly triage down to that short list, which
is a bounded question with a small answer.

The real risk is staleness, not volume: effective dates and scope in this area
move, and a confident wrong date reads exactly like a correct one. That is what
the `[UNVERIFIED]` tagging is for, and why every row needs a source location.
