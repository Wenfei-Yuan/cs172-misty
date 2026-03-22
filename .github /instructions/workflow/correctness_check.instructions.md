---
name: 'Correctness Check'
description: 'Instructions for examining, testing, and running an existing repo for 100% correctness and consistency'
---
# EXAM THE EXISTING REPO FOR 100% CORRECTNESS AND CONSISTENCY
**DO NOT TRY TO COMMIT CHANGES TO GITHUB**
**DO NOT WRITE SPAM FILES INTO THE REPO**
**DO NOT USE SUDO**
inputs:
input 1: target repo
input 2: target functionalities (optional)
input 3: important files (optional)
if target functionalities are specified, focus more on target functionalities, but still go through the entire repo. 

**read through this entire file and follow the instructions carefully**. 

Before checking correctness, always, first read the following files @.github/instructions/repo_info (REFER AS KEY MD FILES):
1. codebase_overview.md
2. scripts_overview.md
3. update_logs.md
4. known_issues.md
Understand them, and keep them inside the memory. 
The main agent should go through all files and scripts inside the repo and get a detailed understanding. 

#CREATE ONE TODO FOR EACH OF THE FOLLOWING STEPS
then, for checking the 100% correctness and 100% consistency to an existing codebase, **CREATE ONE TODO FOR EACH STEP**:
1. if important files are specified, the main agent should read through the important files, and understand them. then combine the understood knowledge with the key md files.
2. if target functionalities are specified, according to the key md files, the main agent should read through the related scripts, and understand them. then combine the understood knowledge with the key md files.
3. Then, the main agent should decide what are the most relevant codes, scripts, files, and functionalities to the correctness objectives, and create a list of **BRIEF** [important information]. If the goal is to check the correctness of the entire repo, the [important information] should include the pipeline diagram of the repo. If the goal is to check target functionalities, the [important information] should at least contains the pipeline of the up stream and down stream of the target functionalities. UPDATE the [important information]. 
**Perform Step 4, step 5, step 6, and step 7 in parallel**
4. the main agent creates a subagent (code agent, focus mode), pass the [important information] list to the subagent. The subagent should also read through the key md files. 
Then the subagent should list out all important files and functionalities in the repo (refer as [ALL IMPORTANT FILE LIST]). Based on the [important information] list and the repo structure based on key md files, the subagent should add or remove the files in the [ALL IMPORTANT FILE LIST] based on importance of functionalities and  re-order [ALL IMPORTANT FILE LIST] from up stream of the workflow to down stream of workflow. Then, the subagent should read through all the files in [ALL FILE LIST] in order and understand the files and codes while carefully examining the correctness to make sure 100% correctness. Then, the subagent should report any incorrectness accordingly, and report the final assessment back to the main agent (Refer as, [answers 1]). 
5. the main agent creates a subagent (code agent, broad mode), pass the [important information] list to the subagent. The subagent should also read through the key md files. 
Then the subagent should list out all files in the repo (refer as [ALL FILE LIST]). 
Based on the [important information] list and the repo structure based on key md files, the subagent should re-order all the files in the [ALL FILE LIST] based on workflow (from up stream of the pipeline to down stream of the pipeline). Then, the subagent should read through all the files in [ALL FILE LIST] in order and understand the files and codes while carefully examining the correctness to make sure 100% correctness. Then, the subagent should report any incorrectness accordingly, and report the final assessment back to the main agent (Refer as, [answers 2]). 
6. the main agent creates a subagent (code agent, free mode), pass the correctness objectives and [important information] to the subagent. The subagent should also read through the key md files. Based on the correctness objectives and repo information from the key md files, the subagent should decide what files and scripts to read and in what order to read, and thus to check entire repo to make sure every functionality is 100% correct. Then, the subagent should report any incorrectness accordingly, and report the final assessment back to the main agent (Refer as, [answers 3]). 
7. the main agent creates a subagent (code agent, exam mode, from a senior QA and SQA engineer perspective), pass the [important information] list to the subagent. The subagent should also read through the key md files. Then the subagent should list out all runable Python/C/C++/Java scripts in the repo (refer as [ALL SCRIPT FILE LIST]). Based on the [important information] list and the repo structure based on key md files, the subagent should re-order all the script files in the [ALL SCRIPT FILE LIST] based on workflow (from up stream of the pipeline diagram to down stream of the pipeline diagram) to make sure the entire pipeline runs correctly. Then, the subagent should **run** through all the script files in [ALL SCRIPT FILE LIST] in order. If the subagent encounters any errors, or receive any unexpect outputs from the scripts, record it. If any errors prevent the current script from running, the subagent should record the errors, and then go to run next script in the [ALL SCRIPT FILE LIST] in order. Then, the subagent should report any incorrectness accordingly, and report the final assessment back to the main agent (Refer as, [answers 4]). 
8. the main agent should read through all four answers ([answers 1], [answers 2], [answers 3], and [answers 4]), understand each of them, examine all the pointed out correctness issues, combine the insights of each report, reject the redundant or incorrect parts of each report, and draft a precise and 100% correct report to report any incorrectness of the repo in bullet points. 
9. the main agent should summarize the correctness check report in the follow format, for incorrectness:
{=============================Correctness Check: (fill an CC ID here, simply use last CC ID + 1)===============================}
Incorrect: (fill a one sentence summary of the Incorrect here.)
Potential Cause: (fill a brief but precise summary of the Potential Cause in bullet points here.)
Then the main agent should write it to past_Correctness_Check.md (if not exist, create one)
10. further more, based on the correctness check results, the main agent should go check known_issues.md and check if the founded problems are marked as fixed in known_issues.md. If yes, add an addtional line and say "the attempt fix was actually failed. "
