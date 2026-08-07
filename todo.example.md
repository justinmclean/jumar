<!-- Copy to todo.md and edit. `todo.md` is git-ignored by default. -->
# My list

Context lines like this one are attached to the item below them and are passed
to the agent as background — they are never treated as work.

- [ ] Add a --json flag to the export script @priority=1 @capability=write_fs
- [ ] Update the README install section @depends=export-json
      - [ ] Rewrite the install steps for the new flag
      - [ ] Check every command in the README actually runs
- [ ] Rotate the backup logs @every=weekday
- [ ] Draft the quarterly summary @not-before=2026-09-01 @due=2026-09-05
- [x] This one is already done and will be skipped

Schedule tokens:
  @not-before=  eligibility gate; the item is deferred until this instant
  @due=         advisory deadline; affects ordering and reporting only
  @every=       recurrence: weekday | 1d | 2w | mon,thu
A completed @every= item stays unchecked with @not-before= advanced to the next
occurrence. An unparseable token blocks that item rather than being guessed at.
