# No comments, no code documentation

## Why

1. **An agent's model of thinking is not human.** It writes a lot of filler;
   understanding that text costs me more than reading the code. Fixing it means
   constantly stating and policing rules.
2. **A comment is almost always an attempt to cut a corner.** Leave something
   unfinished, slip in a quick hack, shift responsibility elsewhere.
3. **Comments go stale next to the code.** They have to be maintained along
   with the code they already duplicate. And they load the next agent with
   needless bias. A clean context is better: the agent will work it out itself.
4. **They are useless.** Better to read the code — it does not lie.
5. **A comment is the appearance of protection, not protection.**

## What instead

- **Design.** Anything that begs for an explanation is expressed in code: a
  private method with a telling name, a different design pattern, named
  constants, linters.
- **A clear API.** KDoc papers over a non-obvious API — so fix the API instead
  of writing KDoc.
- **Tests and gates on the way out.** Code where something breaks easily and
  invisibly must not get through. That is caught by tests and gates, not by a
  warning in a comment.
- **Commit messages.** The reason for a change lives there; those are not
  comments, and this rule does not touch them.

## Exception

An inline comment is allowed only as a reference to an already filed ticket
about a problem outside this code. A problem in the current implementation gets
solved, not tagged with a comment. The agent does not file tickets — in a
critical situation it stops and offers to have me file one.
