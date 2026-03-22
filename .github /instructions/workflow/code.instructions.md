---
name: 'Code Implementation'
description: 'Instructions for implementing, updating, and adding new functionalities'
---
# add new functions to an existing repo
**DO NOT TRY TO COMMIT CHANGES TO GITHUB**
**DO NOT WRITE SPAM FILES INTO THE REPO**
**DO NOT USE SUDO**
inputs:
input 1: target functionalities
input 2: important files (optional)
input 3: target repo (optional)

**read through this entire file and follow the instructions carefully**. 

When ask to implement new functionalities, always, first read the following files @.github/instructions/repo_info (REFER AS KEY MD FILES):
1. codebase_overview.md
2. scripts_overview.md
3. update_logs.md
4. known_issues.md
Understand them, and keep them inside the memory. 
The main agent should go through all files and scripts inside the repo and get a detailed understanding. 


#CREATE ONE TODO FOR EACH OF THE FOLLOWING STEPS
then, for implementing new functionalities to an existing codebase, **CREATE ONE TODO FOR EACH STEP**:
1. if preferred files are specified, the main agent should read through the preferred files, and understand them. then combine the understood knowledge with the key md files. 
2. the main agent should create three subagents and let them work parallel (plan agent, focus mode; plan agent, broad mode; plan agent, free mode), pass the input information to the three subagents. The Three subagents should be launched paralelly. The three subagents should read through the key md files. Then:
a. the plan agent in focus mode should first process the input information and the key md files, and then analysis what the new functionalities, how to integrate the new functionalities to the existing codebase, and what scripts and files could be associated with the new functionalities. Then, the subagent should read through the highly associated files and scripts.  Then, the subagent should draft a plan that integrates the new functionalities into the existing codebase and draft a diagram that integrates the new functionalities into the codebase diagram, while maintaining the entire codebase performs stable, not introducing any new bugs, and would not repeate any known issues/bugs in known_issues.md. then the subagent feeds the plan and the implementation diagram back to the main agent as [plan 1] and [diagram 1].
b. the code agent in broad mode should follow the pipeline diagram from the md file, read through all scripts from up stream of the diagram to the down stream of the diagram.  then analysis what the new functionalities, how to integrate the new functionalities to the existing codebase, and what scripts and files could be associated with the new functionalities. Then, the subagent should draft a plan that integrates the new functionalities into the existing codebase and draft a diagram that integrates the new functionalities to the codebase diagram, while maintaining the entire codebase performs stable, not introducing any new bugs, and would not repeate any known issues/bugs in known_issues.md. then the subagent feeds the plan and the implementation diagram back to the main agent as [plan 2] and [diagram 2].
c. the code agent in free mode should first process the input information and the key md files, then it should decide what files to read, what scripts to check, following its own logic. Then analysis what the new functionalities, how to integrate the new functionalities to the existing codebase, and what scripts and files could be associated with the new functionalities. Then, the subagent should draft a plan that integrates the new functionalities into the existing codebase while maintaining the entire codebase performs stable. then the subagent feeds the plan and the implementation diagram back to the main agent as [plan 3].
3. the main agent creates a subagent (plan agent, senior staff engineering role), pass all three plans [plan 1], [plan 2], and [plan 3] and the implementation diagram [diagram 1]and [diagram 2] from the other subagents and input information to this subagent. The subagent should additionally read through the key md file and all scripts in this repo to get a comprehensive understand of the entire repo. If the plan involves any repo outside this repo, go to that repo, if there are codebase_overview.md and scripts_overview.md, read through them too. Then the subagent reviews all plans and diagrams based on the information it has from a senior staff engineer perspective, assess the plans' and the diagrams' correctness and feasibility, rejecting redundant or incorrect plans, making sure that the plan and the diagram can 100% achieves the new functionalities without breaking the current codebase. feed the review back to the main agent. 
4. the main agent reviews the plans, and the implementation diagrams from step 2 and the review from step 3. If the plans or the review involves any other repos, go to those repos, read their codebase_overview.md and scripts_overview.md if exist, and keep those in the memory. Finally, combine all those information and draft a final plan that is feasible, stable, and 100% correct. 
5. the main agent creates a subagent (devil's advocate), pass the final plan and the input functionalities to the subagent. The subagent should read through the key md files and all relevant scripts, then critically challenge the plan — looking for overlooked side effects, integration risks, incorrect assumptions about the codebase, or potential regressions. The subagent reports any flaws back to the main agent. The main agent incorporates valid criticisms and updates the final plan accordingly.
6. the main agent creates a subagent (code agent), pass the plan and input information to the subagent. The subagent should also read through the key md files. Then based on the input information, the plan, and the repo structure based on the key md files, read all scripts that could associated with the input, the new functionalities and the plan. Then implement the plan and achieve the new functionalities accordingly. feed a implementation report (just what has been changed, **no explanation** why it would achieves the new functionalities) to the main agent. 
7. the main agent creates a subagent (code agent, senior staff engineering role), pass the plan, input information, new functionalities, and implementation report from the other subagent to this subagent. The subagent should additionally read through the key md file and all the code changes in the repo. Then the subagent reviews the code changes and the implementations from a senior staff engineer perspective, assess the code implementation correctness, making sure that the new functionalities are 100% achieved without breaking the current codebase. feed the review back to the main agent.
8.  the main agent creates a subagent (code agent), pass the plan, input information, new functionalities, and implementation report from the other subagent to this subagent. The subagent should additionally read through the key md file and all the code changes in the repo. Then the subagent run through the entire repo pipeline, test: a. if the entire repo still performs correctly; b. if the new implemented functionalities performs as expected with 0 error. Then report the running results to the main agent. 
9. the main agent should read through the code changes, the implementation report, the review, and the running results, then follow the codebase pipeline specified by the diagram and check the correctness of the implementation for the entire codebase. Then, finalize all the changes accordingly, and make sure the new functionalities are 100% achieved without any bugs introduced.  
10. the main agent should summarize the implementation in the following format, for each new functionality:
{=============================Function Update===============================}
{functionality Name (very high level description of the functionality) and functionality Id (assign a number in order, i.e., plus 1 to the last functionality id)}
{functionality description (one or two sentences of description of what the functionality is)}
{Repo involved (what local repos are involved)}
{Implementation ( what has been implemented to achieve the functionality)}
{Achived (whether the functionality has been achieved)}

11. write the Function Update summary to update_logs.md. do not add additional contents, just the function update report from previous step.

