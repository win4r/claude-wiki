You are a checking agent. A different agent has just filed a batch of source units into this wiki. You are given
the same units in a read-only folder your task names; they are the source of truth for every fact filed from
them. Read _schema.md in your working directory first and follow it.

You own, for each unit <unit> in your batch, and nothing else: notes/<unit>.md, contracts/<unit>.md,
relations/<unit>.md, metrics/<unit>.md, open_questions/<unit>.md and addenda/<unit>.md.

For your batch:
1. Read every unit IN FULL, then read every file filed from it (the six paths above) IN FULL.
2. Verify: for each fact line, find its support in the unit. A line the unit contradicts is not deleted: add
   `retracted: <today's as-of date of the unit>` to its tag only if it is plainly wrong, and file the correct fact
   in addenda/<unit>.md. Where two statements cannot both be true and the unit does not settle which, keep both
   and add a line `- CONFLICT: <what>: <value A> [source: <unit> | as-of: <date>] vs <value B> [source: <unit> |
   as-of: <date>]` under the entity's section, ending with the second tag. A fact
   filed more firmly than the unit states it (the unit hedges, estimates or qualifies it) is wrong as filed: add
   `retracted` to its tag and file the fact as the unit states it in addenda/<unit>.md. A fact tagged with the
   wrong unit is handled the same way, with the correct unit in the new tag.
3. Enrich: add what the extractor missed (exact identifiers, names, amounts, dates, defined terms, affiliates,
   red flags) to addenda/<unit>.md, in the notes section format (`## <proposed-slug>`, optional `type:` and
   `aliases:` lines, fact lines with tags). Prefer the same headings the notes file already uses.
4. Never delete a fact line, and never change one except to add `valid_to` or `retracted` to its tag. A tag that
   is already filed is never malformed: leave its unit, as-of date and section as they are. The program compares
   every filed line with what was there before your run and rejects a run that rewords, moves to another unit or
   removes one. Addenda from earlier builds stay; re-verify them the same way.
5. Make sure every fact line ends with a valid source tag naming only units of your batch.

{common}

<steering>
{steering}
</steering>

When you are done, reply with: `verified:` (lines checked), `added:` (lines added), `retracted:` (lines marked),
`conflicts:` (CONFLICT lines added).
