# Security policy

frost exists to make a script's capabilities visible before it runs, so a
weakness here is not only a bug in a tool. It is a manifest that told somebody
their script was safe when it was not.

## Reporting

Report privately through GitHub's [security advisory
form](https://github.com/keithadler/frost/security/advisories/new). Please do
not open a public issue for anything in the list below.

Include the script, the policy if one was involved, and what frost reported
versus what actually happened. A three-line script that reproduces it is worth
more than a description.

## What counts

The bar is whether frost's own account of a script is wrong, or whether a
boundary it claims to hold does not.

* **An understated manifest.** `--explain` fails to report a capability a
  script actually uses. This is the most serious class here. A manifest may
  overstate; the moment it can understate, every review built on it is
  worthless.
* **A policy that does not refuse what it says it refuses.** A rule matches on
  paper and the script runs anyway, or a subject dodges a rule that names it.
* **A sealed value reaching a place the documentation says it cannot.**
  Printing, tracing, a diagnostic, a telemetry event, a journal.
* **An approval that can be forged**, or a signature that verifies when it
  should not, or verification degrading to acceptance when the cipher is
  absent.
* **Escaping the sandbox** on a platform where frost claims to confine.
* **Executing something the tree does not contain.** A parsed script that runs
  a command nobody can see in it.

## What does not

These are documented limits rather than defects, and
[LANGUAGE.md](LANGUAGE.md) states each one:

* **Anything past `sh -c`.** The shell is a program on PATH; frost reports the
  escape and a policy can refuse it, and beyond that call frost cannot see. A
  report that a shell script hidden inside `run "sh" with "-c", text` is not
  analysed describes the design.
* **A program frost was allowed to run doing something unwanted.** Nothing at
  this layer prevents an allowed program from being bad at its job.
* **`showing output` and intermediate `pipe` stages not being counted** by the
  volume limits. Those bytes never pass through frost.
* **Capabilities of a name built at runtime.** These are reported as unknown
  and refused by allow-list rules rather than guessed at.
* **The keystore standing in for a secret manager.** No rotation, no expiry,
  no audit trail of reads. Use Vault or SSM if you have one.

If you are unsure which side of the line something falls on, report it
privately and say so. Deciding is our job, not yours.

## Handling

You will get an acknowledgement within a week. A confirmed issue in the first
list gets a fix and a release, and the advisory names the reporter unless they
would rather it did not.

frost is a personal project with no paid staff behind it, and there is no
bounty. What there is, is a maintainer who would rather know.
