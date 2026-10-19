---
description: Save a finding to agent-memory as a new entry with valid sections
argument-hint: <what to save>
allowed-tools: Bash(memory:*)
---

Save this to agent-memory: $ARGUMENTS

1. Search first: `memory search "<key terms>" --limit 5`. If an entry already covers it, propose `memory update` on that entry instead of a duplicate and ask before writing.
2. Choose a short kebab-case name, a one-line description, a category (`atlas` for durable knowledge, `efforts` for work in progress, `calendar` for dated events, `moc` for maps of content), a confidence (`established`, `working` or `exploratory`) and a few tags.
3. Write the body as `##` sections. The first non-empty line under each heading must be a plain prose sentence describing the section, not a list item, table, code fence or heading. Search indexes section bodies, so a body without sections is unsearchable.
4. Create the entry, piping the body on stdin:

   ```bash
   memory new <name> -d "<description>" -c <category> --confidence <level> -t "<tags>" -b - --no-git <<'BODY'
   ## <Heading>
   <Prose sentence describing this section.>
   ...
   BODY
   ```

   Keep `--no-git` unless the user asked to commit and sync; then drop it, or run `memory sync` afterwards.
5. Run `memory validate <path>` on the new file and fix anything it reports.

Report the path written and its sections.
