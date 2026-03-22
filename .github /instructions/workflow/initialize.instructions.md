---
name: 'Initialize Repo'
description: 'Instructions for creating necessary files (codebase_overview, scripts_overview, update_logs, known_issues) to guide the entire agentic coding workflow for a new or existing repo'
---
# create necessary files for guiding the entire agentic coding workflow

#Procedure 1
First go through the entire repo, keep what files exist in this repo in the memory. 

#Procedure 2
Then, check the existence of the following files under instructions folder:
1. codebase_overview.md
2. scripts_overview.md
3. update_logs_auto_generated.md
4. known_issues_auto_generated.md
5. update_logs.md
6. known_issues.md
if any of the above files do not exist, create an empty folder under instructions folder, named repo_info.


#Procedure 3
Create a subagent, read through all the files in the repo, understand them, and create a [file structure] of the repo. The subagent should feed the [file structure] back to the main agent. The main agent validate the [file structure] and make sure [file structure] should include all files and folders in the repo. 


#Procedure 4
create/update the files in step 2 with the **follwoing specifications in the following order**. 
under the repo_info folder. 

##1 codebase_overview.md:
If the file does not exist, create an empty file.
If the file exists, the main agent should read through the file and keep it inside the memory, and set the pipeline to be the diagram pipeline in the file.
Then:
1. **Create THREE subagents and let them work in parallel**:
a. create a subagent (code agent, order mode), follow [file structure] in order, go through all files by folders, understand what each file is, how they work in the repo, and what they do. Then based on the results of reading and understanding all the files, construct a [codebase_overview 1] and return the [codebase_overview 1] to the main agent. 
b. create a subagent (code agent, expand mode), follow [file structure], based on file name, decide what file to go first (usually the main.py or any main scripts of the repo), and start reading and understanding the script. then go through the imported files one by one, for each imported file, read through the file and understand it, then go through the imports of the imported file, and so on. everythime, when it finished reading a file, add that file into [read files]. Once the subagent finishs reading, validate if there are any files that have not been read by comparing [read files] with [file structure]. If there are files that have not been read, repeat the previous steps and read those files until all files have been read. Then based on the results of reading and understanding all the files, construct a [codebase_overview 2] and return the [codebase_overview 2] to the main agent.
c. create a subagent (code agent, free mode), follow [file structure], the agent should decide go through all files in what order and how to understand what each file is, how they work in the repo, and what are their positions in the pipeline. Then based on the results of reading and understanding all the files, construct a [codebase_overview 3] and return the [codebase_overview 3] to the main agent.
2. based on [codebase_overview 1], [codebase_overview 2] and [codebase_overview 3], the main agent should combine the advantages of three codebase_overviews, reject the redundant or incorrect parts of each codebase_overview, and draft a final [codebase_overview]. The main agent should also check the consistency of the final [codebase_overview] with the pipeline diagram in the original codebase_overview.md (if exist), if there are inconsistencies, update the pipeline diagram accordingly.
3. the main agent should update the pipeline based on the final decision from a perspective of a senior staff engineer, making sure the pipeline is correct, stable, and can guide the entire codebase to perform correctly.
4. the main agent should convert the pipeline into a code diagram. In each block in the diagram, the associate scripts should also be mentioned. 
5. Then, create a subagent, pass the pipeline diagram to the subagent, the subagent go through the generated diagram and associate scripts step by step and make sure the correctness and consistency. Then feed the review back to the main agent. 
6. Then based on the diagram and the review, the main agent check the correctness by itself and update the diagram accordingly.
7. Finally, write the diagram into the codebase_overview.md, along with a description of the repo. 


**§2 and §4 are independent and can be done in parallel.**

##2 scripts_overview.md
If the file does not exist, create an empty file.
Then:
1. **create THREE subagents and let them work in parallel**:
a. create a subagent (code, agent, folder mode), pass the [file structure] to the subagent. the subagent should go through all the files in the repo from folder to folder, and:read through files in folders one by one. for reach file, if it is a code script, summarize each module (function, method, class, code blocks) with two sentences: one sentence of function name, parameters, and outputs, one short sentence describes the functionality. organize the summarization by files: give each file a high level summarization, and give out a list of dependencies of that file. then report a [scripts overview 1] to the main agent.
b. create a subagent (code agent, guided mode), ask the agent to read through the scripts_overview.md. then the subagent should read through the codebase_overview.md, understand the pipeline and the codebase structure, then based on that, read through files according to the pipeline (from upstream to downstream). for reach file, if it is a code script, summarize each module (function, method, class, code blocks) with two sentences: one sentence of function name, parameters, and outputs, one short sentence describes the functionality. organize the summarization by files: give each file a high level summarization, and give out a list of dependencies of that file. then report a [scripts overview 2] to the main agent.
c. create a subagent (code agent, file mode), the subagent should go through all the files in the repo, then:read through files one by one. for reach file, if it is a code script, summarize each module (function, method, class, code blocks) with two sentences: one sentence of function name, parameters, and outputs, one short sentence describes the functionality. organize the summarization by files: give each file a high level summarization, and give out a list of dependencies of that file. then report a [scripts overview 3] to the main agent.
2. the main agent reads the report from  step 1 ([scripts overview 1], [scripts overview 2], and [scripts overview 3]) and scripts_overview.md file, understand each of them, combines the advantages of three reports, reject the redundant or incorrect parts of each report, and draft a final scripts overview, and write the final scripts overview into scripts_overview.md.
3. create a subagent (review agent), the subagent first reads the scripts_overview.md, follows the scripts_overview.md go through all the scripts and files one by one, first read the original code/text, then validate the summarization of the scripts_overview.md. Report inconsistency back the main agent.
4. update the scripts_overview.md
  

##3 known_issues_auto_generated.md:
if the file exists, do nothing. 
if the file does not exist, create an empty file.
then:
1. create a subagent (plan agent), go through the codebase_overview.md and scripts_overview.md, point out the weakness of the code architecture and all possible issues. report back to the main agent.
2. create a subagent (code agent), go through the codebase_overview.md and scripts_overview.md, and then go through all the scripts one by one, find any potential issues, code could lead to problems, errors, and bugs. find anything that could affect the code be 100% correct. find anything that prevent code being running 100% correct or function as expected. report back to main agent.
3. the main agent read through **@instructions/workflow/correctness_check.instructions.md** and follow the instructions to check the correctness of the repo based on the information from step 2, and find any potential problems, issues, and weaknesses of the codebase. report back to main agent.
3. the main agent summarize the reviews from step 1, step 2, and step 3, based on the information it has, use its best ability to combine two reviews with only correct and fair part.
4. write the contents to known_issues_auto_generated.md in the format of: 
{Problem Title (very high level summarization)}
{Problem description ( a short description of the problem)}
{Root causes (for example, what code/function is cause the problem )}
{Consequences (what issues can this problem lead to)}. 

##4 update_logs_auto_generated.md
get the git commit history, and create a file with the git commit history ( do not add any interpretations, be faithful to the original contents) logs. 


##5 known_issues.md:
if the file exists, do nothing. 
if the file does not exist, create an empty file.

##6 known_issues.md:
if the file exists, do nothing. 
if the file does not exist, create an empty file. 

##7 past_Q&A.md:
if the file exists, do nothing. 
if the file does not exist, create an empty file. 
