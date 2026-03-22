---
name: 'Debug Workflow'
description: 'Instructions for debugging and fixing bugs'
---
# debug instructions
**DO NOT TRY TO COMMIT CHANGES TO GITHUB**
**DO NOT WRITE SPAM FILES INTO THE REPO**
**DO NOT USE SUDO**
input information: 
input 1: target bug 
input 2: suspected reasons (optional)
input 3: important scripts (optional)


**read through this entire file and follow the instructions carefully**. 

When ask to debug, always, first read the following files @.github/instructions/repo_info (REFER AS KEY MD FILES):
1. codebase_overview.md
2. scripts_overview.md
3. update_logs.md
4. known_issues.md
Understand the structure of the repo, functions inside each script, previous update, and previous bug fix attempts. **KEEP THESE IN THE MEMORY**. 


#CREATE ONE TODO FOR EACH OF THE FOLLOWING STEPS
Then, the main agent should, **CREATE ONE TODO FOR EACH STEP**:
0. the main agent creates a subagent (code agent), pass the input information to the subagent. The subagent should also read through the key md files. The subagent checks if the bug has previously been addressed or fixed based on the key md files. If exists, the subagent follows the codebase diagram from codebase_overview.md and goes through all the scripts that associated with the previous fix attempts. Then, combining the current bug information, the subagent infers why the bug is not fixed, and report back to the main agent.
1. the main agent should create three subagents and **let them work parallel** (code subagent, focus mode; code subagent, broad mode; code subagent, free mode), pass the input information to the three subagents. The three subagents should also read through the key md files. Then:
a. the code subagent in focus mode should focus on the important scripts and suspected reasons, read through those scripts, and check the potential reasons for the bug from the perspective of those scripts and suspected reasons, and report back to the main agent.
b. the code subagent in broad mode should follow the pipeline diagram from the md file, read through all scripts from up stream of the diagram to the down stream of the diagram, and check the potential reasons for the bug from a broader perspective, and report back to the main agent.
c. the code subagent in free mode should decide what files to read, what scripts to check, following its own logic, and check the potential reasons for the bug from a completely free perspective, and report back to the main agent.
2. the main agent should read through all three reports from step 1, understand each of them, exam all the pointed out potential reasons, combines the insights of each report, reject the redundant or incorrect parts of each report, and draft precise and 100% correct report to adress the potential reasons for the bug (ref as [bug info])
3. the main agent creates a subagent (devil's advocate), pass the [bug info] and the original bug description to the subagent. The subagent should read through the key md files, then critically challenge the [bug info] — looking for overlooked root causes, misattributed blame, or incorrect assumptions. The subagent reports any flaws back to the main agent. The main agent incorporates valid criticisms and updates [bug info] accordingly.
4. the main agent creates a subagent (plan agent), pass the input information and the [bug info] to the subagent. The subagent should also read through the key md files. Then based on the bug information and the repo structure based on the key md files, read all scripts that could associated with the bug. Then, the subagent should draft a plan that can fix the bug while maintaining the entire codebase functions the same, not introducing any new bugs, and would not repeate any known issues/bugs in known_issues.md. then the subagent feeds the plan back to the main agent. 
5. the main agent creates a subagent (plan agent, senior staff engineering role), pass the plan from the other subagent and [bug info] to this subagent. The subagent should additionally read through the key md file and all scripts in this repo to get a comprehensive understand of the entire repo. If the plan involves any repo outside this repo, go to that repo, if there are codebase_overview.md and scripts_overview.md, read through them too. Then the subagent reviews the plan based on the information it has from a senior staff engineer perspective, assess the plan's correctness and feasibility, making sure that the plan can 100% fix the bug without breaking the current codebase. feed the review back to the main agent.
6. the main agent reviews the plan from step 4 and the review from step 5. If the plan or the review involves any other repos, go to those repos, read their codebase_overview.md and scripts_overview.md if exist, and keep those in the memory. Finally, combine all those information and draft a final plan that is feasible, stable, and 100% correct. 
7. the main agent creates a subagent (code agent), pass the plan and bug information to the subagent. The subagent should also read through the key md files. Then based on the bug information, the plan, and the repo structure based on the key md files, read all scripts that could associated with the bug and the plan. Then implement the plan and fix the bug accordingly. feed a implementation report (just what has been changed, no explanation why it would fix bug) to the main agent. 
8. the main agent creates a subagent (code agent, senior staff engineering role), pass the plan, bug information, and implementation report from the other subagent to this subagent. The subagent should additionally read through the key md file and all the code changes in the repo. Then the subagent reviews the code changes and the implementation from a senior staff engineer perspective, assess the code implementation correctness, making sure that the bug is 100% fixed without breaking the current codebase. feed the review back to the main agent.
9. the main agent creates a subagent (code agent), pass the plan, input information, the bug, and implementation report from the other subagent to this subagent. The subagent should additionally read through the key md file and all the code changes in the repo. Then the subagent run through the entire repo pipeline, test: a. if the entire repo still performs correctly; b. if the new implemented bug fix fixed the bug as expected with 0 error. Then report the running results to the main agent. 
10. the main agent should read through the code changes, the implementation report, the running results, and the review, finalize all the changes accordingly, and make sure the bug is 100% addressed and fixed. 
11. the main agent should summarize the bug fix in the following format:
{=============================BUG FIX===============================}
{BUG Name (very high level description of the bug) and Bug Id (assign a number in order, i.e., plus 1 to the last bug id)}
{Bug description (one or two sentences of description of what the bug is)}
{Repo involved (what local repos are involved)}
{Implementation ( what has been changed to fix the bug)}
{Fixed (whether the bug has been fixed)}
12. write the summary to update_logs.md. do not add additional contents, just the bug fix report from previous step. If the bug is a recurring issue that has been attempted and failed to fix multiple times, also write to known_issues.md in the following format:
{Problem Title}
a. What was not fixed: (a brief explanation of what remains broken)
b. Last attempt summary: (a brief summary of the last fix attempt)
c. Why the last fix failed: (a brief analysis of why the previous fix failed, including what mistakes the coding agent made)
d. Current fix: (a brief description of the current fix being applied)



